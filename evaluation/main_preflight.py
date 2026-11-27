"""Evaluate the reviewed AAH-S03 synthetic submission on the operator side."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from aah.domain import ContractError, EvaluationResult, canonical_json_bytes, load_record, sha256_bytes
from aah.workspace import apply_frozen_patch, export_revision
from aah.workspace.snapshot import checked_path, disjoint, read_regular
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import docker_worker

from .runner import EvaluationRequest, write_result


TASK = "AAH-S03"
FILE = "csv_rows.py"
PUBLIC_SOURCE_HASH = "eb98dec8e1cba1aab129b8949d594df93d928dc319c48d715f11a548182cb9ea"
MARKER = b"\n# Synthetic Main handoff probe.\n"
FLAGS = ("-I", "-S", "-B")
CASES = {
    "functional": ("id,score\nprobe-a,7\n", [{"id": "probe-a", "score": 7}]),
    "regression": ("id,score\nprobe-b,\n", [{"id": "probe-b", "score": None}]),
}
WORKER = b"""\
import json
import sys

payload = json.load(sys.stdin)
namespace = {"__name__": "csv_rows"}
exec(compile(payload["source"], "csv_rows.py", "exec"), namespace)
results = [namespace["load_rows"](case) for case in payload["inputs"]]
print(json.dumps(results, allow_nan=False))
"""


class SyntheticEvaluator:
    """Bind one public baseline, one reviewed source and an input-only worker protocol."""

    def __init__(self, *, docker: bool):
        directory, self.task, spec_hash = read_task(TASK)
        self.base = directory / "base"
        self.revision = export_revision(self.base)
        public_source = read_regular(self.base / FILE)[0]
        if (self.task.allowed_change_paths != (FILE,)
                or sha256_bytes(public_source.replace(b"\r\n", b"\n")) != PUBLIC_SOURCE_HASH):
            raise ContractError("the reviewed public source has changed")
        self.approved_source = public_source + MARKER
        self.docker = docker
        transport = docker_worker.identity() if docker else {"python": sys.version, "scope": "local-synthetic"}
        self.environment_hash = sha256_bytes(canonical_json_bytes([transport, FLAGS, sha256_bytes(WORKER)]))
        self.bundle_hash = sha256_bytes(canonical_json_bytes(CASES))
        self.manifest_hash = sha256_bytes(canonical_json_bytes({
            "profile": "main-synthetic-preflight-v1", "task": TASK, "base": self.revision,
            "spec": spec_hash, "source": sha256_bytes(self.approved_source),
            "bundle": self.bundle_hash, "environment": self.environment_hash,
        }))

    def evaluate(self, request: EvaluationRequest, artifacts: Path, results: Path) -> EvaluationResult:
        started = monotonic()
        artifacts = checked_path(artifacts)
        results = checked_path(results, allow_missing=True)
        disjoint(artifacts, results)
        disjoint(self.base, results)
        patch = request.patch
        if ((request.task_id, request.base_commit, request.manifest_hash) != (TASK, self.revision, self.manifest_hash)
                or patch is None or not patch.applicable or patch.changed_paths != (FILE,)
                or (patch.run_id, patch.base_commit) != (request.run_id, self.revision)):
            raise ContractError("synthetic evaluation request or patch scope differs from the pinned probe")

        with TemporaryDirectory(prefix="aah-main-evaluation-") as temporary:
            sandbox = Path(temporary)
            replay = sandbox / "replay"
            apply_frozen_patch(self.base, patch, artifacts, replay, repo_id=self.task.repo_id)
            source = read_regular(replay / FILE)[0]
            if source != self.approved_source:
                raise ContractError("only the reviewed synthetic submission can execute")
            inputs, expected = zip(*CASES.values(), strict=True)
            payload = canonical_json_bytes({"source": source.decode("utf-8-sig"), "inputs": inputs})
            if self.docker:
                observed = docker_worker.run(WORKER, FLAGS, payload, monotonic() + 30)
            else:
                env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
                observed = subprocess.run([sys.executable, *FLAGS, "-c", WORKER.decode("utf-8")],
                                          input=payload, capture_output=True, cwd=sandbox, env=env, timeout=10)

        if observed.returncode or len(observed.stdout) > 16_384:
            raise ContractError("synthetic worker failed to return bounded observations")
        outputs = json.loads(observed.stdout)
        if not isinstance(outputs, list) or len(outputs) != len(expected):
            raise ContractError("synthetic worker returned invalid observations")
        functional, regression = (
            "PASS" if canonical_json_bytes(actual) == canonical_json_bytes(wanted) else "FAIL"
            for actual, wanted in zip(outputs, expected, strict=True)
        )
        result = EvaluationResult(request.run_id, TASK, self.revision, patch.patch_hash, self.manifest_hash,
                                  self.bundle_hash, self.environment_hash,
                                  "PASS" if functional == regression == "PASS" else "FAIL",
                                  functional, regression, "PASS", int((monotonic() - started) * 1000), None, None)
        write_result(results, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--docker", action="store_true")
    args = parser.parse_args()
    try:
        request = load_record(EvaluationRequest, read_regular(args.request)[0])
        SyntheticEvaluator(docker=args.docker).evaluate(request, args.artifacts, args.results)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("MAIN_SYNTHETIC_EVALUATION_FAILED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
