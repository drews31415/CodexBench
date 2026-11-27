"""승인된 공개 Smoke 검사. 올바른 snapshot의 불변 blob만 worker에 제공한다."""

from __future__ import annotations

import subprocess
from functools import partial
from pathlib import Path
from time import monotonic, monotonic_ns
from uuid import uuid4

from aah.domain import (
    ApprovedCheckSpec, CheckResult, CheckStatus, SnapshotRef, TestCaseResult,
    canonical_json_bytes, sha256_bytes,
)
from aah.workspace import WorkspaceError, read_snapshot_file
from aah.workspace.checks import PublicCheckRunner

from . import driver


PUBLIC_BUNDLES = {
    "DEV-01": "0c5e3780d61b8f9f8131f68d2589317107a8b55dc003d369db89da008e6effa9",
    "DEV-02": "a5ba2cf259ca6fd238081c0f6965ce8dd598f4c8872fade098011d26736c88d0",
}


def run_approved(snapshot: SnapshotRef, check_id: str, artifact_root: Path, *,
                 deadline: float | None = None, turn_id: str | None = None,
                 dev01_candidate: bool = False) -> CheckResult:
    task_id = snapshot.repo_id
    driver.task_dir(task_id)
    if dev01_candidate and task_id != "DEV-01":
        raise ValueError("the DEV-01 candidate profile is task-specific")
    stop_at = deadline if deadline is not None else monotonic() + driver.TIMEOUT_MS / 1000
    profile = "dev01-strip-candidate-docker-v1" if dev01_candidate else "public-synthetic-v1"
    worker_args = ("--dev01-candidate",) if dev01_candidate else ()
    specs = [ApprovedCheckSpec(
        check_id=f"{task_id}-{kind}", kind=kind,
        argv=("python", *driver.WORKER_FLAGS, "benchmarks/smoke/worker.py", *worker_args),
        cwd=".", timeout_ms=driver.TIMEOUT_MS, parser_id="smoke-json-v1",
        required=True, check_version="1", environment_profile_id=profile,
        trusted_test_ref=f"benchmarks/smoke/{task_id}/public.json", trusted_test_hash=PUBLIC_BUNDLES[task_id],
    ) for kind in ("P-FUNC", "P-REG")]
    environment = driver.environment_hash(in_docker=dev01_candidate)
    runner = PublicCheckRunner(specs, environment_hash=environment,
                               execute=partial(_execute, artifact_root=artifact_root, environment=environment,
                                               dev01_candidate=dev01_candidate))
    return runner.run_approved(snapshot, check_id, stop_at, turn_id=turn_id)


def _execute(snapshot: SnapshotRef, spec: ApprovedCheckSpec, deadline: float, turn_id: str | None,
             *, artifact_root: Path, environment: str, dev01_candidate: bool) -> CheckResult:
    task_id = snapshot.repo_id
    start = monotonic_ns()
    cases = []
    warnings = []
    exit_code = None
    trusted = False
    try:
        definitions = driver.load_bundle(driver.task_dir(task_id) / "public.json", spec.trusted_test_hash)[spec.kind]
        source = read_snapshot_file(artifact_root, snapshot, "module.py")
        outputs = driver.observe(task_id, source, definitions, deadline=deadline,
                                 in_docker=dev01_candidate, dev01_candidate=dev01_candidate)
        for (test_id, _, expected), actual in zip(definitions, outputs, strict=True):
            passed = type(actual) is type(expected) and actual == expected
            message = None if passed else f"expected {expected!r}, observed {actual!r}"
            fingerprint = None if passed else sha256_bytes(canonical_json_bytes(
                [spec.check_id, test_id, "AssertionError", message, None]))
            cases.append(TestCaseResult(test_id=test_id, status=CheckStatus.PASS if passed else CheckStatus.FAIL,
                                        error_type=None if passed else "AssertionError",
                                        message=message, frame=None, fingerprint=fingerprint))
        status = CheckStatus.PASS if all(c.status == CheckStatus.PASS for c in cases) else CheckStatus.FAIL
        # 종료 코드 0은 계산 worker의 실제 종료 코드다. assertion은 판정자가 비교한다.
        exit_code = 0
        trusted = True
    except driver.UnreviewedSource:
        status = CheckStatus.NOT_RUN
        warnings.append("SUBMISSION_BOUNDARY_UNVERIFIED")
    except driver.DeadlineExhausted:
        status = CheckStatus.NOT_RUN
        warnings.append("DEADLINE_EXHAUSTED")
    except subprocess.TimeoutExpired:
        status = CheckStatus.TIMEOUT
        warnings.append("REVIEWED_WORKER_TIMEOUT")
    except (driver.CheckEnvironmentError, WorkspaceError, OSError):
        status = CheckStatus.INFRA_ERROR
        warnings.append("PUBLIC_CHECK_INPUT_OR_ENVIRONMENT_ERROR")
    return CheckResult(
        check_result_id=str(uuid4()), check_id=spec.check_id, check_definition_hash=spec.check_definition_hash,
        turn_id=turn_id, snapshot_id=snapshot.snapshot_id, status=status,
        source_class="TRUSTED_RUNNER" if trusted else "UNVERIFIED_OBSERVATION",
        exit_code=exit_code, duration_ms=(monotonic_ns() - start) // 1_000_000,
        environment_hash=environment, parser_id=spec.parser_id, test_cases=tuple(cases),
        stdout_ref=None, stderr_ref=None, warnings=tuple(warnings),
    )
