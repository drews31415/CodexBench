"""Dev 과제 기준본·공개 합성 수정본의 오프라인 검증 명령."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from aah.domain import (
    CheckStatus, Condition, EvaluationResult, FixedRequirements, RepositoryRef, RunRequest,
    dump_record, load_record, resolve_condition, sha256_bytes,
)
from aah.workspace import WorkspaceManager, export_revision
from benchmarks.smoke import driver
from benchmarks.smoke.driver import TASK_DIGESTS
from benchmarks.smoke.public_checks import run_approved

from .runner import EvaluationRequest, FIXTURES, fixture_manifest_hash


CHECKOUT = Path(__file__).resolve().parents[1]


def fixture_request(task_id: str) -> RunRequest:
    directory = driver.task_dir(task_id)
    spec = driver.load_bundle(directory / "task.json", TASK_DIGESTS[task_id])
    source = directory / "base"
    fixed = FixedRequirements(
        task=spec["task"], mandatory_text=spec["mandatory_text"],
        mandatory_hash=sha256_bytes(spec["mandatory_text"].encode("utf-8")),
        permission_profile_id="public-copy-only", approved_check_ids=spec["approved_check_ids"],
    )
    return RunRequest(
        run_id=str(uuid4()), task_id=task_id, experiment_id=None,
        repository=RepositoryRef(task_id, source, export_revision(source)), fixed=fixed,
        policies=resolve_condition(Condition.A), config_hash=TASK_DIGESTS[task_id], mode="user",
    )


def _evaluate_separately(request_path: Path, artifact_root: Path, results: Path, *, in_docker: bool,
                         dev01_candidate: bool = False) -> None:
    child_env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
    child_env["PYTHONPATH"] = os.pathsep.join(map(str, (CHECKOUT / "src", CHECKOUT)))
    if in_docker:
        binary = shutil.which("docker")
        if binary is None:
            raise FileNotFoundError("Docker CLI is unavailable")
        child_env["PATH"] = str(Path(binary).resolve().parent)
    argv = [sys.executable, "-P", "-S", "-B", "-m", "evaluation.runner",
            "--request", str(request_path), "--artifact-root", str(artifact_root), "--results", str(results)]
    with tempfile.TemporaryDirectory() if in_docker else nullcontext() as home:
        if home is not None:
            child_env.update(DOCKER_CONFIG=home, USERPROFILE=home, HOME=home)
            argv.append("--docker-evaluator")
        if dev01_candidate:
            argv.append("--dev01-candidate")
        subprocess.run(argv, env=child_env, cwd=CHECKOUT, check=True, timeout=60)


def verify(task_id: str, output_dir: Path, *, in_docker: bool = False) -> list[dict]:
    """깨끗한 출력 위치에서 두 상태를 검증하고 실제 관측 결과를 되돌린다."""
    source = driver.task_dir(task_id) / "base"
    directory = output_dir.resolve() / task_id
    if any(directory.is_relative_to(root) for root in (driver.ROOT, FIXTURES)):
        raise ValueError("output directory overlaps read-only fixture inputs")
    directory.mkdir(parents=True, exist_ok=False)
    artifacts = directory / "artifacts"
    public = directory / "public-checks"
    requests = directory / "requests"
    results = directory / "evaluation"
    public.mkdir()
    requests.mkdir()
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        # 이 오프라인 운영자가 workspace에서 여는 자식 프로세스는 없다.
        manager = WorkspaceManager(source, Path(temp) / "work", artifacts, quiescence_check=lambda _: True)
        for variant in ("baseline", "reference"):
            req = fixture_request(task_id)
            handle = manager.prepare(req)
            try:
                if variant == "reference":
                    (handle.root / "module.py").write_bytes((FIXTURES / task_id / "module.py").read_bytes())
                snapshot = manager.snapshot(handle)
                checks = [run_approved(snapshot, check_id, artifacts) for check_id in req.fixed.approved_check_ids]
                for check in checks:
                    with (public / f"{req.run_id}-{check.check_id}.json").open("xb") as f:
                        f.write(dump_record(check))
                patch = manager.freeze_patch(handle, snapshot)
            finally:
                manager.cleanup(handle)
            manifest = fixture_manifest_hash(task_id, patch.base_commit, in_docker=in_docker)
            request = EvaluationRequest(req.run_id, task_id, patch.base_commit, manifest, patch)
            request_path = requests / f"{req.run_id}.json"
            with request_path.open("xb") as f:
                f.write(dump_record(request))
            _evaluate_separately(request_path, artifacts, results, in_docker=in_docker)
            evaluation = load_record(EvaluationResult, (results / f"{req.run_id}.json").read_bytes())
            functional = CheckStatus.FAIL if variant == "baseline" else CheckStatus.PASS
            verified = (
                evaluation.run_id == req.run_id and evaluation.patch_hash == patch.patch_hash
                and [c.status for c in checks] == [functional, CheckStatus.PASS]
                and all(c.source_class == "TRUSTED_RUNNER" for c in checks)
                and evaluation.functional_status == functional
                and evaluation.regression_status == evaluation.instruction_status == CheckStatus.PASS
                and evaluation.status.value == functional.value
            )
            rows.append({
                "task_id": task_id, "variant": variant, "run_id": req.run_id,
                "public": [c.status.value for c in checks], "evaluation": evaluation.status.value,
                "functional": evaluation.functional_status.value, "regression": evaluation.regression_status.value,
                "instruction": evaluation.instruction_status.value, "patch_hash": patch.patch_hash, "verified": verified,
            })
            if in_docker and not verified:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="공개 합성 Dev fixture 기준 상태와 수정 상태 검증")
    parser.add_argument("--task", choices=tuple(TASK_DIGESTS))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--docker-evaluator", action="store_true",
                        help="execute reviewed candidates inside the pinned image for independent evaluation")
    args = parser.parse_args()
    if args.output_dir is None and not args.docker_evaluator:
        parser.error("--output-dir is required for the local fixture evaluator")
    output_dir = args.output_dir or CHECKOUT / "results" / "s0-verification" / f"docker-eval-{uuid4().hex}"
    tasks = (args.task,) if args.task else TASK_DIGESTS
    try:
        rows = []
        for task_id in tasks:
            rows.extend(verify(task_id, output_dir, in_docker=args.docker_evaluator))
            if args.docker_evaluator and not all(row["verified"] for row in rows):
                break
    except FileExistsError:
        print('{"error_code":"SMOKE_OUTPUT_EXISTS"}', file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"error_code": "SMOKE_PREPARATION_ERROR", "error_type": type(error).__name__}),
              file=sys.stderr)
        return 2
    verified = len(rows) == 2 * len(tasks) and all(row["verified"] for row in rows)
    if args.docker_evaluator:
        summary = {"schema_version": "1.0", "profile": "public-synthetic-docker-v1", "verified": verified, "runs": rows}
        # Each invocation owns new task directories. Keep every invocation's summary, even in a shared output root.
        path = output_dir / f"summary-{uuid4().hex}.json"
        with path.open("x", encoding="utf-8") as file:
            json.dump(summary, file, indent=2, allow_nan=False)
        print(f"{'PASS' if verified else 'INCOMPLETE'}  {path.resolve()}")
    else:
        print(json.dumps({"profile": "public-synthetic-only", "runs": rows}, indent=2))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
