"""Snapshot-bound public checks and a separate frozen-patch Pilot evaluator."""

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from aah.domain import (
    ApprovedCheckSpec, CheckResult, CheckStatus, EvaluationResult, EvaluationStatus,
    TestCaseResult, canonical_json_bytes, load_record, sha256_bytes,
)
from aah.workspace import WorkspaceError, apply_frozen_patch, read_snapshot_file
from aah.workspace.checks import PublicCheckRunner
from benchmarks.smoke import docker_worker, driver
from evaluation.runner import EvaluationRequest, write_result

from . import prepare, worker


WORKER = Path(worker.__file__)
PROFILE = "pilot-pure-python-docker-v1"


def worker_bytes():
    return WORKER.read_bytes().replace(b"\r\n", b"\n")


def environment_hash():
    return sha256_bytes(canonical_json_bytes([PROFILE, driver.environment_hash(in_docker=True),
                                              sha256_bytes(worker_bytes())]))


def evaluator_hash(task_id, asset, environment):
    return sha256_bytes(canonical_json_bytes([PROFILE, task_id, asset["base_revision"],
                                              asset["review_suite_hash"], environment,
                                              sha256_bytes(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))]))


def observe(task_id, source, cases, deadline):
    if len(source) > 8_192:
        raise driver.UnreviewedSource("Pilot candidate too large for the reviewed worker")
    request = canonical_json_bytes({"task_id": task_id, "source": source.decode("utf-8"),
                                    "inputs": [args for _, args, _ in cases]})
    process = docker_worker.run(worker_bytes(), driver.WORKER_FLAGS, request, deadline)
    if process.returncode == 2:
        raise driver.UnreviewedSource("Pilot candidate outside the supported Python subset")
    if process.returncode or len(process.stdout) > 256_000:
        raise driver.CheckEnvironmentError("Pilot worker failed")
    outputs = json.loads(process.stdout)
    if (not isinstance(outputs, list) or len(outputs) != len(cases)
            or any(not isinstance(row, list) or len(row) != 2 or type(row[0]) is not bool for row in outputs)):
        raise driver.CheckEnvironmentError("Pilot worker returned malformed output")
    return [success and canonical_json_bytes(actual) == canonical_json_bytes(expected)
            for (success, actual), (_, _, expected) in zip(outputs, cases, strict=True)]


def public_runner(task_id, asset, environment):
    directory = prepare.task_directory(task_id)
    bundle = driver.load_bundle(directory / "public.json", asset["public_checks_hash"])
    specs = [ApprovedCheckSpec(
        check_id=f"{task_id}-{kind}", kind=kind, argv=("python", *driver.WORKER_FLAGS, "pilot-worker"),
        cwd=".", timeout_ms=driver.TIMEOUT_MS, parser_id="pilot-verdict-v1",
        required=True, check_version="1", environment_profile_id=PROFILE,
        trusted_test_ref=directory.relative_to(prepare.CHECKOUT).as_posix() + "/public.json",
        trusted_test_hash=asset["public_checks_hash"],
    ) for kind in ("P-FUNC", "P-REG")]

    def run_public(snapshot, check_id, artifact_root, *, deadline, turn_id):
        def execute(bound, spec, stop_at, active_turn):
            started = monotonic()
            cases, warnings, exit_code = (), (), None
            try:
                if (bound.repo_id, bound.base_commit) != (task_id, asset["base_revision"]):
                    raise WorkspaceError("Pilot snapshot identity mismatch")
                source = read_snapshot_file(artifact_root, bound, "module.py")
                definitions = bundle[spec.kind]
                matches = observe(task_id, source, definitions, stop_at)
                cases = tuple(TestCaseResult(
                    test_id=case[0], status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                    error_type=None if passed else "AssertionError",
                    message=None if passed else f"Expected public behavior differs for {case[0]}",
                    frame=None, fingerprint=None if passed else sha256_bytes(canonical_json_bytes(
                        [spec.check_id, case[0], "OUTPUT_MISMATCH"])),
                ) for case, passed in zip(definitions, matches, strict=True))
                status = CheckStatus.PASS if all(matches) else CheckStatus.FAIL
                exit_code = 0
            except driver.UnreviewedSource:
                status, warnings = CheckStatus.NOT_RUN, ("SUBMISSION_BOUNDARY_UNVERIFIED",)
            except subprocess.TimeoutExpired:
                status, warnings = CheckStatus.TIMEOUT, ("PILOT_WORKER_TIMEOUT",)
            except (driver.CheckEnvironmentError, OSError, ValueError):
                status, warnings = CheckStatus.INFRA_ERROR, ("PUBLIC_CHECK_INPUT_OR_ENVIRONMENT_ERROR",)
            return CheckResult(str(uuid4()), spec.check_id, spec.check_definition_hash, active_turn,
                               bound.snapshot_id, status, "TRUSTED_RUNNER" if exit_code == 0 else "UNVERIFIED_OBSERVATION",
                               exit_code, int((monotonic() - started) * 1000), environment, spec.parser_id,
                               cases, None, None, warnings)

        return PublicCheckRunner(specs, environment_hash=environment, execute=execute).run_approved(
            snapshot, check_id, deadline, turn_id=turn_id)
    return run_public


def evaluate(request, artifact_root):
    started = monotonic()
    task_id, patch = request.task_id, request.patch
    asset = prepare.read_pinned_draft()["task_assets"][task_id]
    base = asset["base_revision"]
    environment = environment_hash()
    manifest = evaluator_hash(task_id, asset, environment)
    patch_hash = None
    functional = regression = instruction = CheckStatus.NOT_RUN

    def result(status, reason=None):
        return EvaluationResult(request.run_id, task_id, base, patch_hash, manifest, asset["review_suite_hash"],
                                environment, status, functional, regression, instruction,
                                int((monotonic() - started) * 1000), reason, None)

    if patch is None or not patch.applicable:
        return result(EvaluationStatus.NOT_EVALUABLE, "NO_EVALUABLE_PATCH")
    if ((request.run_id, request.base_commit, request.manifest_hash) != (patch.run_id, base, manifest)
            or patch.base_commit != base):
        return result(EvaluationStatus.NOT_RUN, "REQUEST_IDENTITY_MISMATCH")
    suite = driver.load_bundle(prepare.FIXTURES / task_id / "suites.json", asset["review_suite_hash"])
    cases = suite["functional"] + suite["regression"]
    try:
        with TemporaryDirectory() as temp:
            replay = Path(temp) / "replay"
            apply_frozen_patch(prepare.task_directory(task_id) / "base", patch, artifact_root, replay, repo_id=task_id)
            patch_hash = patch.patch_hash
            if set(patch.changed_paths) - {"module.py"} or not (replay / "module.py").is_file():
                instruction = CheckStatus.FAIL
                return result(EvaluationStatus.FAIL, "REQUIRED_PATH_CONSTRAINT")
            matches = observe(task_id, (replay / "module.py").read_bytes(), cases,
                              monotonic() + driver.TIMEOUT_MS / 1000)
        functional = CheckStatus.PASS if all(matches[:len(suite["functional"])]) else CheckStatus.FAIL
        regression = CheckStatus.PASS if all(matches[len(suite["functional"]):]) else CheckStatus.FAIL
        instruction = CheckStatus.PASS
    except docker_worker.CleanupUnconfirmed:
        return result(EvaluationStatus.INFRA_ERROR, "DOCKER_CLEANUP_UNCONFIRMED")
    except driver.UnreviewedSource:
        return result(EvaluationStatus.NOT_RUN, "SUBMISSION_BOUNDARY_UNVERIFIED")
    except (OSError, ValueError, driver.CheckEnvironmentError, subprocess.SubprocessError):
        return result(EvaluationStatus.INFRA_ERROR, "PILOT_EVALUATOR_ERROR")
    return result(EvaluationStatus.PASS if functional == regression == CheckStatus.PASS else EvaluationStatus.FAIL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    if not args.results.resolve().is_relative_to(prepare.CHECKOUT / "results" / "s5-live"):
        parser.error("Pilot results must stay within the operator output tree")
    verdict = evaluate(load_record(EvaluationRequest, args.request.read_bytes()), args.artifact_root)
    write_result(args.results, verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
