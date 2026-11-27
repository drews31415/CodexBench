"""Curator-side Main evaluator: judge one frozen Main patch with the kit's private suites.

Runs in its own process from the operator's PowerShell (or as the separate evaluator
process started by the Main schedule). The curator kit stays outside the repository; only
the evaluator manifest hash and ``EvaluationResult`` records leave it. Private suite sources
are sent only to the pinned Docker worker.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from aah.domain import ContractError, EvaluationResult, canonical_json_bytes, load_record, sha256_bytes
from aah.workspace import WorkspaceError, apply_frozen_patch, export_revision
from aah.workspace.snapshot import checked_path, disjoint, read_regular
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import docker_worker
from evaluation import source_suites
from evaluation.curator_check import SUITES, _interface
from evaluation.runner import EvaluationRequest, write_result


PROFILE = "main-curator-docker-v1"
CHECKOUT = Path(__file__).resolve().parents[1]
SUITE_TIMEOUT_SECONDS = 30


class CuratorKit:
    """Pinned view of one extracted curator kit, bound to the public candidate."""

    def __init__(self, kit: Path, *, identity: dict | None = None):
        kit = checked_path(kit)
        disjoint(CHECKOUT, kit)
        spec_data = read_regular(kit / "task.json")[0]
        self.task_id = json.loads(spec_data)["task_id"]
        directory, self.task, spec_hash = read_task(self.task_id)
        if sha256_bytes(spec_data) != spec_hash:
            raise ContractError("kit task specification does not match the public candidate")
        self.base = directory / "base"
        self.revision = export_revision(self.base)
        if export_revision(kit / "baseline") != self.revision:
            raise ContractError("kit baseline does not match the public candidate")
        self.suites = {kind: read_regular(kit / "curator_tests" / name)[0] for kind, name in SUITES.items()}
        self.modules = sorted(p.stem for p in self.base.glob("*.py") if p.is_file() and p.stem.isidentifier())
        self.bundle_hash = sha256_bytes(canonical_json_bytes({k: sha256_bytes(v) for k, v in self.suites.items()}))
        self.environment_hash = sha256_bytes(canonical_json_bytes(source_suites.environment_record(
            identity if identity is not None else docker_worker.identity())))
        self.manifest_hash = sha256_bytes(canonical_json_bytes({
            "profile": PROFILE, "task_id": self.task_id, "public_spec_sha256": spec_hash, "base": self.revision,
            "suite_sha256": self.bundle_hash, "environment_sha256": self.environment_hash,
            "allowed_paths": list(self.task.allowed_change_paths),
        }))

    def judge(self, changed: dict[str, bytes | None], *, transport=None) -> dict:
        """Judge the public base with ``changed`` files overlaid (None deletes a file)."""
        allowed = set(self.task.allowed_change_paths)
        implementation = {name: read_regular(self.base / f"{name}.py")[0] for name in self.modules}
        for path, data in changed.items():
            name = Path(path).stem
            if "/" not in path and path.endswith(".py") and name in implementation:
                implementation[name] = data if data is not None else b""
        try:
            signatures_kept = all(
                all(_interface(implementation[name]).get(fn) == sig
                    for fn, sig in _interface(read_regular(self.base / f"{name}.py")[0]).items())
                for name in self.modules if implementation[name])
        except SyntaxError:
            signatures_kept = False
        outcome = {"functional": "NOT_RUN", "regression": "NOT_RUN", "instruction": "NOT_RUN",
                   "status": None, "reason": None}
        if set(changed) - allowed or not signatures_kept or not all(implementation.values()):
            outcome.update(instruction="FAIL", status="FAIL", reason="INSTRUCTION_VIOLATION")
            return outcome
        outcome["instruction"] = "PASS"
        try:
            statuses = source_suites.run_suites(implementation, self.suites, monotonic() + SUITE_TIMEOUT_SECONDS,
                                                transport=transport)
        except docker_worker.CleanupUnconfirmed:
            outcome.update(status="INFRA_ERROR", reason="DOCKER_CLEANUP_UNCONFIRMED")
            return outcome
        except (subprocess.TimeoutExpired, TimeoutError):
            outcome.update(status="INFRA_ERROR", reason="EVALUATOR_TIMEOUT_REVIEW")
            return outcome
        except (ContractError, OSError, ValueError, subprocess.SubprocessError):
            outcome.update(status="INFRA_ERROR", reason="EVALUATOR_ENV_ERROR")
            return outcome
        functional, regression = statuses["functional"]["status"], statuses["regression"]["status"]
        status = "PASS" if functional == regression == "PASS" else "FAIL"
        outcome.update(functional=functional, regression=regression, status=status,
                       reason=None if status == "PASS" else
                       "FUNCTIONAL_FAILURE" if functional == "FAIL" else "REGRESSION_FAILURE")
        return outcome

    def evaluate(self, request: EvaluationRequest, artifacts: Path, *, transport=None) -> EvaluationResult:
        started = monotonic()
        patch = request.patch
        patch_hash = None

        def result(outcome):
            return EvaluationResult(request.run_id, self.task_id, self.revision, patch_hash, self.manifest_hash,
                                    self.bundle_hash, self.environment_hash, outcome["status"],
                                    outcome["functional"], outcome["regression"], outcome["instruction"],
                                    int((monotonic() - started) * 1000), outcome["reason"], None)

        not_run = {"functional": "NOT_RUN", "regression": "NOT_RUN", "instruction": "NOT_RUN"}
        if (request.task_id, request.base_commit, request.manifest_hash) != (
                self.task_id, self.revision, self.manifest_hash):
            return result({**not_run, "status": "NOT_RUN", "reason": "REQUEST_IDENTITY_MISMATCH"})
        if patch is None or not patch.applicable:
            return result({**not_run, "status": "NOT_EVALUABLE", "reason": "NO_EVALUABLE_PATCH"})
        if (patch.run_id, patch.base_commit) != (request.run_id, self.revision):
            return result({**not_run, "status": "NOT_RUN", "reason": "REQUEST_IDENTITY_MISMATCH"})
        try:
            with TemporaryDirectory(prefix="aah-main-curator-") as temporary:
                replay = Path(temporary) / "replay"
                apply_frozen_patch(self.base, patch, checked_path(artifacts), replay, repo_id=self.task.repo_id)
                patch_hash = patch.patch_hash
                changed = {path: (read_regular(replay / path)[0] if (replay / path).is_file() else None)
                           for path in patch.changed_paths}
        except (WorkspaceError, OSError, ValueError):
            return result({**not_run, "status": "INFRA_ERROR", "reason": "PATCH_REPLAY_ERROR"})
        return result(self.judge(changed, transport=transport))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True, help="the extracted curator kit for one Main task")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--manifest", action="store_true", help="print the evaluator manifest hash only")
    mode.add_argument("--request", type=Path, help="EvaluationRequest JSON written by the Main schedule")
    parser.add_argument("--artifacts", type=Path, help="the run's content-addressed artifact root")
    parser.add_argument("--results", type=Path, help="directory that receives <run_id>.json")
    args = parser.parse_args()
    try:
        kit = CuratorKit(args.kit)
        if args.manifest:
            print("MAIN_CURATOR_MANIFEST", kit.task_id, kit.manifest_hash)
            return 0
        if args.artifacts is None or args.results is None:
            parser.error("--request needs --artifacts and --results")
        request = load_record(EvaluationRequest, read_regular(args.request)[0])
        verdict = kit.evaluate(request, args.artifacts)
        write_result(args.results, verdict)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # Contract messages are fixed diagnostics; never echo private suite content.
        print("MAIN_CURATOR_BLOCKED", str(error) if isinstance(error, ContractError) else type(error).__name__,
              file=sys.stderr)
        return 2
    print("MAIN_CURATOR_VERDICT", verdict.run_id, verdict.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
