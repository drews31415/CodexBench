"""공개 합성 fixture 전용 독립 평가기. 결과 기록은 이 운영자 경계에서 수행한다."""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns
from uuid import UUID

from aah.domain import (
    CheckStatus, EvaluationResult, EvaluationStatus, FrozenPatch, canonical_json_bytes,
    dump_record, load_record, sha256_bytes,
)
from aah.domain.wire import Digest, Identifier, RunId, WireRecord
from aah.workspace import apply_frozen_patch, export_revision
from benchmarks.smoke import docker_worker, driver, worker


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "evaluation"
SUITE_DIGESTS = {
    "DEV-01": "3ced21597705e8cfee0009b505d4b26ebe7e799d5e6de1626ba952d01b88b153",
    "DEV-02": "7b5d2ed58d27df65f2723370c383d966b50536cd41342b798b42aabeb15ecf2e",
}
PARAMETERS = {"DEV-01": ("text",), "DEV-02": ("now", "expires_at")}
NOT_RUN = CheckStatus.NOT_RUN


@dataclass(frozen=True)
class EvaluationRequest(WireRecord):
    run_id: RunId
    task_id: Identifier
    base_commit: Identifier
    manifest_hash: Digest
    patch: FrozenPatch | None


def fixture_manifest_hash(task_id: str, base_commit: str, *, in_docker: bool = False,
                          dev01_candidate: bool = False) -> str:
    profile = "public-synthetic-docker-v1" if in_docker else "public-synthetic-only"
    if dev01_candidate:
        if task_id != "DEV-01" or not in_docker:
            raise ValueError("DEV-01 candidates require the Docker evaluator")
        profile = "dev01-strip-candidate-docker-v1"
    return sha256_bytes(canonical_json_bytes({
        "schema_version": "1.0", "profile": profile, "task_id": task_id,
        "base_commit": base_commit, "allowed_paths": ["module.py"], "parameters": PARAMETERS[task_id],
        "suite_hash": SUITE_DIGESTS[task_id],
        "evaluator_hash": sha256_bytes(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
    }))


def _suite(task_id: str, source: bytes, cases: list, *, in_docker: bool, dev01_candidate: bool) -> CheckStatus:
    outputs = driver.observe(task_id, source, cases, in_docker=in_docker, dev01_candidate=dev01_candidate)
    return CheckStatus.PASS if all(
        type(actual) is type(expected) and actual == expected
        for (_, _, expected), actual in zip(cases, outputs, strict=True)
    ) else CheckStatus.FAIL


def _interface_preserved(task_id: str, source: bytes) -> bool:
    name = worker.REVIEWED[task_id][0]
    return any(
        isinstance(node, ast.FunctionDef) and node.name == name
        and tuple(arg.arg for arg in node.args.args) == PARAMETERS[task_id]
        for node in ast.parse(source).body
    )


def evaluate(request: EvaluationRequest, *, artifact_root: Path, in_docker: bool = False,
             dev01_candidate: bool = False) -> EvaluationResult:
    start = monotonic_ns()
    task_id = request.task_id
    patch = request.patch
    base_commit = patch_hash = manifest_hash = bundle_hash = environment = None
    functional = regression = instruction = NOT_RUN

    def result(status: EvaluationStatus, error: str | None = None) -> EvaluationResult:
        return EvaluationResult(
            run_id=request.run_id, task_id=task_id, base_commit=base_commit, patch_hash=patch_hash,
            private_manifest_hash=manifest_hash, test_bundle_hash=bundle_hash,
            evaluation_environment_hash=environment, status=status, functional_status=functional,
            regression_status=regression, instruction_status=instruction,
            duration_ms=(monotonic_ns() - start) // 1_000_000, error_code=error, restricted_details_ref=None,
        )

    if task_id not in SUITE_DIGESTS:
        return result(EvaluationStatus.NOT_RUN, "UNKNOWN_SYNTHETIC_TASK")
    if patch is None:
        return result(EvaluationStatus.NOT_EVALUABLE, "NO_FROZEN_PATCH")
    base = driver.task_dir(task_id) / "base"
    try:
        base_commit = export_revision(base)
        expected_base = worker.REVIEWED[task_id][1][0]
        if sha256_bytes((base / "module.py").read_bytes().replace(b"\r\n", b"\n")) != expected_base:
            return result(EvaluationStatus.INFRA_ERROR, "BASE_FIXTURE_MISMATCH")
        if (request.base_commit != base_commit or patch.base_commit != base_commit
                or request.run_id != patch.run_id):
            return result(EvaluationStatus.NOT_RUN, "REQUEST_IDENTITY_MISMATCH")
        manifest_hash = fixture_manifest_hash(task_id, base_commit, in_docker=in_docker, dev01_candidate=dev01_candidate)
        if request.manifest_hash != manifest_hash:
            return result(EvaluationStatus.NOT_RUN, "MANIFEST_MISMATCH")
        suites = driver.load_bundle(FIXTURES / task_id / "suites.json", SUITE_DIGESTS[task_id])
        bundle_hash = SUITE_DIGESTS[task_id]
        environment = driver.environment_hash(in_docker=in_docker)
        if not patch.applicable:
            return result(EvaluationStatus.NOT_EVALUABLE, "INCOMPLETE_FROZEN_PATCH")
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "replay"
            apply_frozen_patch(base, patch, artifact_root, destination, repo_id=task_id)
            patch_hash = patch.patch_hash
            module = destination / "module.py"
            outside_scope = any(path != "module.py" for path in patch.changed_paths)
            if outside_scope or not module.is_file():
                instruction = CheckStatus.FAIL
                return result(EvaluationStatus.FAIL, "REQUIRED_PATH_CONSTRAINT")
            source = module.read_bytes()
            if not worker.is_reviewed(task_id, source) and not (dev01_candidate and worker.is_dev01_candidate(source)):
                return result(EvaluationStatus.NOT_RUN, "SUBMISSION_BOUNDARY_UNVERIFIED")
            instruction = CheckStatus.PASS if _interface_preserved(task_id, source) else CheckStatus.FAIL
            functional = _suite(task_id, source, suites["functional"], in_docker=in_docker,
                                dev01_candidate=dev01_candidate)
            regression = _suite(task_id, source, suites["regression"], in_docker=in_docker,
                                dev01_candidate=dev01_candidate)
    except docker_worker.CleanupUnconfirmed as exc:
        print(f"Could not confirm removal of {exc}", file=sys.stderr)
        return result(EvaluationStatus.INFRA_ERROR, "DOCKER_CLEANUP_UNCONFIRMED")
    except (OSError, driver.CheckEnvironmentError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        # 경계·재현 오류만으로 후보가 잘못됐다는 판정을 만들지 않는다.
        return result(EvaluationStatus.INFRA_ERROR, "EVALUATOR_ENV_ERROR")
    return result(EvaluationStatus.PASS if functional == regression == instruction == CheckStatus.PASS
                  else EvaluationStatus.FAIL)


def write_result(directory: Path, record: EvaluationResult) -> Path:
    """완성된 판정만 공개하고 같은 run의 기록을 덮어쓰지 않는다."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{UUID(record.run_id)}.json"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        stage = Path(temporary.name)
        try:
            temporary.write(dump_record(record))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary.close()
            if os.name == "nt":
                # Windows의 rename은 기존 목적지를 교체하지 않는다.
                stage.rename(destination)
            else:
                os.link(stage, destination)
        finally:
            temporary.close()
            stage.unlink(missing_ok=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="공개 합성 Dev 패치 독립 평가")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--docker-evaluator", action="store_true", help="run reviewed candidate code in local Docker")
    parser.add_argument("--dev01-candidate", action="store_true", help="allow the restricted DEV-01 candidate shape")
    args = parser.parse_args()
    if args.dev01_candidate and not args.docker_evaluator:
        parser.error("--dev01-candidate requires --docker-evaluator")
    try:
        destination = args.results.resolve()
        for other in (args.artifact_root.resolve(), driver.ROOT, FIXTURES):
            if destination.is_relative_to(other) or other.is_relative_to(destination):
                raise ValueError("evaluation output overlaps an input root")
        request = load_record(EvaluationRequest, args.request.read_bytes())
        verdict = evaluate(request, artifact_root=args.artifact_root, in_docker=args.docker_evaluator,
                           dev01_candidate=args.dev01_candidate)
        write_result(destination, verdict)
    except FileExistsError:
        print('{"error_code":"EVALUATION_RESULT_EXISTS"}', file=sys.stderr)
        return 2
    except (ValueError, OSError):
        print('{"error_code":"EVALUATION_REQUEST_OR_STORAGE_ERROR"}', file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
