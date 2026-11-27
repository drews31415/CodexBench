"""공개 합성 fixture의 제한된 I/O. 독립 평가 데이터나 판정은 참조하지 않는다."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic

from aah.domain import canonical_json_bytes, sha256_bytes

from . import docker_worker, worker


ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "worker.py"
WORKER_SHA256 = "081222099ee02b9e545507bb6f1981e4884771089deb0821d177d361d1a347e0"
TIMEOUT_MS = 5000
WORKER_FLAGS = ("-I", "-S", "-B")
TASK_DIGESTS = {
    "DEV-01": "241d68487bff37b0d89386a660bc57a1612cf6a526cb817f0869ccd5f73d2af2",
    "DEV-02": "d23d93c5425101a5c5849ce60c2a706f2e9db8550b5a7709e5ff8174dd5451da",
}


class UnreviewedSource(ValueError):
    """격리가 확인되지 않은 환경에서 제출 코드의 실행을 요청했다."""


class CheckEnvironmentError(RuntimeError):
    """검사 자산, worker 프로세스 또는 응답을 검증할 수 없다."""


class DeadlineExhausted(RuntimeError):
    """worker를 시작하기 전에 사용 가능한 시간이 소진되었다."""


def task_dir(task_id: str) -> Path:
    if task_id not in worker.REVIEWED:
        raise ValueError("unknown synthetic task")
    return ROOT / task_id


def load_bundle(path: Path, expected_hash: str) -> dict:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    if sha256_bytes(data) != expected_hash:
        raise CheckEnvironmentError("fixture bundle digest mismatch")
    return json.loads(data)


def environment_hash(*, in_docker: bool = False) -> str:
    environment = {
        "python": sys.version,
        "executable_hash": sha256_bytes(Path(sys.executable).read_bytes()),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "flags": WORKER_FLAGS,
        "worker_hash": sha256_bytes(WORKER.read_bytes().replace(b"\r\n", b"\n")),
        "timeout_ms": TIMEOUT_MS,
    }
    if in_docker:
        environment["candidate_docker"] = docker_worker.identity()
    return sha256_bytes(canonical_json_bytes(environment))


def observe(task_id: str, source: bytes, cases: list, *, deadline: float | None = None,
            in_docker: bool = False, dev01_candidate: bool = False) -> list:
    """검사 정의·기대값·판정 파일 경로는 worker에 보내지 않는다."""
    candidate = in_docker and dev01_candidate and task_id == "DEV-01" and worker.is_dev01_candidate(source)
    if not worker.is_reviewed(task_id, source) and not candidate:
        raise UnreviewedSource("unverified submission execution boundary")
    request = canonical_json_bytes({
        "task_id": task_id, "source": source.decode("utf-8"),
        "inputs": [args for _, args, _ in cases],
    })
    child_env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR") if k in os.environ}
    script = WORKER.read_bytes().replace(b"\r\n", b"\n")
    if sha256_bytes(script) != WORKER_SHA256:
        raise CheckEnvironmentError("reviewed worker digest mismatch")
    stop_at = monotonic() + TIMEOUT_MS / 1000
    if deadline is not None:
        stop_at = min(stop_at, deadline)
    if in_docker:
        process = docker_worker.run(script, WORKER_FLAGS, request, stop_at,
                                    worker_args=("--dev01-candidate",) if candidate else ())
    else:
        with tempfile.TemporaryDirectory() as cwd:
            trusted_worker = Path(cwd) / "worker.py"
            trusted_worker.write_bytes(script)
            timeout = stop_at - monotonic()
            if timeout <= 0:
                raise DeadlineExhausted("public check deadline exhausted")
            process = subprocess.run(
                [sys.executable, *WORKER_FLAGS, str(trusted_worker)], cwd=cwd, env=child_env,
                input=request, capture_output=True, timeout=timeout,
            )
    if process.returncode:
        raise CheckEnvironmentError("reviewed worker exited unsuccessfully")
    try:
        outputs = json.loads(process.stdout)
    except (UnicodeError, ValueError) as exc:
        raise CheckEnvironmentError("invalid worker response") from exc
    if not isinstance(outputs, list) or len(outputs) != len(cases):
        raise CheckEnvironmentError("invalid response shape")
    return outputs
