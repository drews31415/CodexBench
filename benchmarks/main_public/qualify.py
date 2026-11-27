"""Verify repository-authored public baselines and export evaluator handoff metadata."""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from aah.domain import (Condition, ContractError, EvaluationResult, FixedRequirements, FrozenPatch,
                        RepositoryRef, RunRequest, WireRecord, canonical_json_bytes, dump_record, load_record,
                        resolve_condition, sha256_bytes, validate_evaluation_binding)
from aah.domain.wire import Digest, Identifier, RelativePath
from aah.workspace import WorkspaceManager, export_revision
from aah.workspace.patch import store_bytes, verified_bytes
from aah.workspace.snapshot import checked_path, disjoint, read_regular, relative_path
from evaluation.runner import EvaluationRequest


TASK_ROOT = Path(__file__).resolve().parent
TASK_IDS = ("AAH-S02", "AAH-S03", "AAH-D02", "AAH-D04", "AAH-R03", "AAH-R04", "AAH-C02", "AAH-C03")
PUBLIC_SUITES = {"P-FUNC": "test_functional.py", "P-REG": "test_regression.py"}
PUBLIC_RUNNER = """\
import io, json, sys, unittest
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
suite = unittest.defaultTestLoader.discover(str(root / "public_checks"), pattern=sys.argv[1])
result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
print(json.dumps({"tests_run": result.testsRun, "failures": len(result.failures),
                  "errors": len(result.errors), "skipped": len(result.skipped),
                  "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses)}))
"""


@dataclass(frozen=True)
class PublicTask(WireRecord):
    task_id: Identifier
    repo_id: Identifier
    task_family_id: Identifier
    doc_state: Identifier
    task: Identifier
    mandatory_text: Identifier
    allowed_change_paths: tuple[RelativePath, ...]
    approved_check_ids: tuple[Identifier, ...]

    def validate(self):
        paths = self.allowed_change_paths
        if self.doc_state not in {"NORMAL", "NOISY", "STALE"}:
            raise ContractError("unknown document state")
        if (not paths or len(paths) != len(set(paths))
                or any(relative_path(path).startswith("public_checks/") or not path.endswith(".py") for path in paths)):
            raise ContractError("public checks cannot be part of the candidate patch")
        if self.approved_check_ids != tuple(f"{self.task_id}-{kind}" for kind in PUBLIC_SUITES):
            raise ContractError("expected the task's functional and regression check IDs")


def read_task(task_id):
    if task_id not in TASK_IDS:
        raise ContractError("unknown authored Main candidate")
    directory = TASK_ROOT / task_id
    data = read_regular(directory / "task.json")[0]
    spec = load_record(PublicTask, data)
    if spec.task_id != task_id:
        raise ContractError("public task is outside its registered identity")
    return directory, spec, sha256_bytes(data)


def run_public_baseline_suite(workspace, suite_name):
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
    outcome = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", PUBLIC_RUNNER, suite_name], cwd=workspace,
                             env=env, check=True, capture_output=True, timeout=15)
    result = json.loads(outcome.stdout)
    if (result["tests_run"] < 1 or any(result[key] for key in (
        "errors", "skipped", "expected_failures", "unexpected_successes"
    ))):
        raise ContractError(f"public check is incomplete or has an infrastructure error: {suite_name}")
    return result


def verify_candidate(task_id, output_dir):
    directory, spec, spec_hash = read_task(task_id)
    source = directory / "base"
    revision = export_revision(source)
    for path in spec.allowed_change_paths:
        read_regular(source / path)
    checks = {name: {"path": f"public_checks/{file}", "sha256": sha256_bytes(
        read_regular(source / "public_checks" / file)[0])} for name, file in PUBLIC_SUITES.items()}
    task_output = checked_path(Path(output_dir).absolute() / task_id, allow_missing=True)
    disjoint(TASK_ROOT, task_output)
    task_output.mkdir(parents=True, exist_ok=False)
    artifacts = task_output / "artifacts"
    comparisons = []
    fixed = FixedRequirements(spec.task, spec.mandatory_text, sha256_bytes(spec.mandatory_text.encode("utf-8")),
                              "public-copy-only", spec.approved_check_ids)
    with TemporaryDirectory(prefix="aah-main-public-") as temporary:
        manager = WorkspaceManager(source, Path(temporary) / "work", artifacts, quiescence_check=lambda _: True)
        for repeat in range(1, 3):
            request = RunRequest(str(uuid4()), task_id, None, RepositoryRef(spec.repo_id, source, revision), fixed,
                                 resolve_condition(Condition.A), spec_hash, "user")
            handle = manager.prepare(request)
            try:
                before = manager.snapshot(handle)
                results = {kind: run_public_baseline_suite(handle.root, file) for kind, file in PUBLIC_SUITES.items()}
                after = manager.snapshot(handle)
                if not before.complete or before.snapshot_id != after.snapshot_id:
                    raise ContractError(f"public check changed the {task_id} workspace")
                if results["P-FUNC"]["failures"] < 1 or results["P-REG"]["failures"]:
                    raise ContractError(f"{task_id} baseline must show functional FAIL and regression PASS")
                patch = manager.freeze_patch(handle, after)
                if not patch.applicable or patch.changed_paths:
                    raise ContractError(f"{task_id} expected an empty baseline patch")
                comparisons.append({"repeat": repeat, "snapshot_id": after.snapshot_id, "checks": results})
                if repeat == 1:
                    store_bytes(task_output, "baseline-patch.json", dump_record(patch))
            finally:
                manager.cleanup(handle)
    if comparisons[0]["snapshot_id"] != comparisons[1]["snapshot_id"] or comparisons[0]["checks"] != comparisons[1]["checks"]:
        raise ContractError(f"{task_id} public baseline checks changed across repeats")
    handoff = {
        "schema_version": "1.0", "status": "PUBLIC_BASELINE_VERIFIED", "task_id": task_id, "repo_id": spec.repo_id,
        "task_family_id": spec.task_family_id, "doc_state": spec.doc_state, "public_spec_hash": spec_hash,
        "base_revision": revision, "snapshot_hash": comparisons[0]["snapshot_id"], "allowed_change_paths": spec.allowed_change_paths,
        "public_checks": checks, "public_checks_hash": sha256_bytes(canonical_json_bytes(checks)),
        "baseline_patch_ref": "baseline-patch.json", "baseline_artifacts_ref": "artifacts",
        "public_validation": comparisons, "independent_review": "PENDING",
    }
    store_bytes(task_output, "handoff.json", canonical_json_bytes(handoff))
    return handoff


def baseline_evaluation_request(handoff_dir: Path, evaluator_manifest_hash: Digest):
    """Bind the empty baseline patch to a manifest provisioned by the separate evaluator."""
    handoff = json.loads(read_regular(handoff_dir / "handoff.json")[0])
    patch = load_record(FrozenPatch, read_regular(handoff_dir / "baseline-patch.json")[0])
    directory, task, public_spec_hash = read_task(handoff["task_id"])
    if (handoff["status"] != "PUBLIC_BASELINE_VERIFIED" or not patch.applicable or patch.changed_paths
            or handoff["public_spec_hash"] != public_spec_hash or patch.base_commit != export_revision(directory / "base")
            or (patch.base_commit, patch.snapshot_id) != (handoff["base_revision"], handoff["snapshot_hash"])):
        raise ContractError("public qualification and baseline patch do not agree")
    verified_bytes(handoff_dir / "artifacts", "patches", patch.patch_hash)
    return EvaluationRequest(patch.run_id, task.task_id, patch.base_commit, evaluator_manifest_hash, patch)


def verify_independent_pair(baseline: EvaluationResult, reference: EvaluationResult, request: EvaluationRequest,
                            *, environment_hash, test_bundle_hash):
    """Check only verdict receipts from the curator, with no access to tests or the reference patch."""
    for verdict in (baseline, reference):
        if (verdict.task_id, verdict.base_commit, verdict.private_manifest_hash, verdict.evaluation_environment_hash,
                verdict.test_bundle_hash) != (
                request.task_id, request.base_commit, request.manifest_hash, environment_hash, test_bundle_hash
        ):
            raise ContractError("independent verdicts must use the same approved task, suite and environment")
    if (request.patch is None or not request.patch.applicable or request.patch.changed_paths
            or (request.run_id, request.base_commit) != (request.patch.run_id, request.patch.base_commit)):
        raise ContractError("an identified baseline patch is required")
    validate_evaluation_binding(baseline, request.patch)
    if (reference.run_id == baseline.run_id or reference.patch_hash == baseline.patch_hash
            or (baseline.status, baseline.functional_status, baseline.regression_status, baseline.instruction_status)
            != ("FAIL", "FAIL", "PASS", "PASS")
            or (reference.status, reference.functional_status, reference.regression_status, reference.instruction_status)
            != ("PASS", "PASS", "PASS", "PASS")):
        raise ContractError("independent baseline and reference do not demonstrate the required behavior")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new report directory or request file")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--task", choices=TASK_IDS, help="qualify only this repository-authored baseline")
    modes.add_argument("--handoff-dir", type=Path, help="verified candidate directory for a curator request")
    parser.add_argument("--evaluator-manifest-hash", help="approved manifest hash from the independent evaluator")
    args = parser.parse_args()
    if bool(args.handoff_dir) != bool(args.evaluator_manifest_hash):
        parser.error("--handoff-dir and --evaluator-manifest-hash must be supplied together")
    try:
        if args.handoff_dir:
            request = baseline_evaluation_request(args.handoff_dir, args.evaluator_manifest_hash)
            store_bytes(args.output.absolute().parent, args.output.name, dump_record(request))
            print("MAIN_BASELINE_REQUEST_WRITTEN", args.output)
            return 0
        rows = [verify_candidate(task_id, args.output) for task_id in ((args.task,) if args.task else TASK_IDS)]
        store_bytes(args.output.absolute(), "summary.json",
                    canonical_json_bytes({"schema_version": "1.0", "candidates": rows, "main_approved": 0}))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print("MAIN_PUBLIC_BASELINE_INVALID", str(exc))
        return 2
    print("MAIN_PUBLIC_BASELINES_VERIFIED", args.output / "summary.json", f"candidates={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
