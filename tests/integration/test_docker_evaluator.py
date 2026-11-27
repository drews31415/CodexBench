import json
import subprocess
import sys
from dataclasses import replace
from time import monotonic

import pytest

from aah.domain import EvaluationResult, load_record
from benchmarks.smoke import docker_worker, driver
from evaluation import runner
from evaluation.smoke import verify


def test_docker_evaluator_uses_replayed_code_and_only_case_inputs(tmp_path, monkeypatch):
    task = "DEV-01"
    rows = verify(task, tmp_path)
    root = tmp_path / task
    monkeypatch.setattr(docker_worker, "identity", lambda: {"test_double": "python-worker"})
    delivered = []

    def observed(script, flags, request, deadline, *, worker_args):
        payload = json.loads(request)
        assert set(payload) == {"source", "task_id", "inputs"}
        assert payload["task_id"] == task and deadline > monotonic()
        delivered.append(payload["inputs"])
        return subprocess.run([sys.executable, *flags, "-c", script.decode(), *worker_args], input=request,
                              capture_output=True, check=True)

    monkeypatch.setattr(docker_worker, "run", observed)
    for row in rows:
        request = load_record(runner.EvaluationRequest, (root / "requests" / (row["run_id"] + ".json")).read_bytes())
        local = load_record(EvaluationResult, (root / "evaluation" / (row["run_id"] + ".json")).read_bytes())
        assert runner.evaluate(request, artifact_root=root / "artifacts", in_docker=True).error_code == "MANIFEST_MISMATCH"
        manifest = runner.fixture_manifest_hash(task, request.base_commit, in_docker=True)
        result = runner.evaluate(replace(request, manifest_hash=manifest), artifact_root=root / "artifacts", in_docker=True)
        assert result.status == local.status and result.patch_hash == row["patch_hash"]
        assert result.evaluation_environment_hash != local.evaluation_environment_hash
    suites = json.loads((runner.FIXTURES / task / "suites.json").read_bytes())
    assert delivered == [[args for _, args, _ in suites[key]] for key in ("functional", "regression")] * 2


def test_docker_source_gate_runs_before_dispatch(monkeypatch):
    monkeypatch.setattr(docker_worker, "run", lambda *a: pytest.fail("unexpected candidate dispatch"))
    with pytest.raises(driver.UnreviewedSource):
        driver.observe("DEV-01", b"# not a reviewed fixture", [], in_docker=True)


@pytest.mark.parametrize("leftover", [False, True])
def test_timeout_reconciles_exact_container_before_returning(monkeypatch, leftover):
    cid = b"f" * 64
    seen = []

    def fake(argv, **kwargs):
        seen.append(argv)
        cmd = argv[3]
        if cmd == "run":
            assert "--pull=never" in argv and docker_worker.IMAGE in argv and "--mount" not in argv
            assert argv[argv.index("--network") + 1] == "none" and kwargs["input"] == b"input"
            raise subprocess.TimeoutExpired("synthetic timeout", 1)
        if cmd == "rm":
            assert argv[-2:] == ["-f", cid.decode()]
        if cmd == "container":
            name = seen[0][seen[0].index("--name") + 1]
            assert "name=^/" + name + "$" in argv and "label=aah.s0.evaluation=" + name in argv
        return subprocess.CompletedProcess(argv, 0, cid if cmd == "container" and (len(seen) == 2 or leftover) else b"")

    monkeypatch.setattr(docker_worker.subprocess, "run", fake)
    expected = docker_worker.CleanupUnconfirmed if leftover else subprocess.TimeoutExpired
    with pytest.raises(expected):
        docker_worker.run(b"pass", (), b"input", monotonic() + 2)
    assert [argv[3] for argv in seen] == ["run", "container", "rm", "container"]
