import json
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aah.domain import ContractError, TurnRequest, canonical_json_bytes, resolve_condition, sha256_bytes
from aah.workspace import WorkspaceManager
from benchmarks.pilot import assessment, live, prepare, worker
from evaluation.runner import EvaluationRequest


@pytest.mark.parametrize("task_id", prepare.TASKS)
def test_pilot_worker_baseline_and_reference(task_id, monkeypatch):
    # Exercise the isolated worker protocol with repository-authored sources only.
    def transport(script, flags, request, deadline):
        assert set(json.loads(request)) == {"task_id", "source", "inputs"}
        return subprocess.run([sys.executable, *flags, "-c", script.decode()], input=request, capture_output=True,
                              timeout=10)

    monkeypatch.setattr(assessment.docker_worker, "run", transport)
    draft = prepare.read_pinned_draft()
    source = prepare.task_directory(task_id)
    asset = draft["task_assets"][task_id]
    cases = assessment.driver.load_bundle(source / "public.json", asset["public_checks_hash"])
    baseline = (source / "base/module.py").read_bytes()
    reference = (prepare.FIXTURES / task_id / "module.py").read_bytes()
    assert not all(assessment.observe(task_id, baseline, cases["P-FUNC"], 1))
    assert all(assessment.observe(task_id, baseline, cases["P-REG"], 1))
    assert all(assessment.observe(task_id, reference, cases["P-FUNC"] + cases["P-REG"], 1))


def test_worker_restricts_execution_to_the_task_data_boundary():
    assert worker.candidate_tree("DEV-02", "def is_live(now, expires_at): return expires_at is None or now < expires_at")
    assert worker.candidate_tree("DEV-02", "import math\ndef is_live(now, expires_at): return False") is None
    assert worker.candidate_tree("DEV-02", "def is_live(now, expires_at): return str.upper('x')") is None
    assert worker.candidate_tree("DEV-02", "def is_live(now): return now") is None


def test_public_and_private_case_comparison_checks_nested_value_types(monkeypatch):
    def transport(*_):
        return SimpleNamespace(returncode=0, stdout=b'[[true, {"ready": true}]]')

    monkeypatch.setattr(assessment.docker_worker, "run", transport)
    assert assessment.observe("DEV-04", b"fixture", [["counts", [], {"ready": 1}]], 1) == [False]


def test_pilot_runtime_rejects_a_third_recovery(tmp_path):
    task = prepare.task_request("DEV-02")
    task = replace(task, experiment_id=live.LIVE_ID, policies=resolve_condition("B"))
    manager = WorkspaceManager(task.repository.source_path, tmp_path / "work", tmp_path / "artifacts",
                               quiescence_check=lambda _: True)
    handle = manager.prepare(task)
    session = str(uuid4())
    calls = []

    def dispatch(data, seconds, *, persist, session_id):
        calls.append(session_id)
        return fake_turn(session)

    runtime = live.PilotRuntime(task, handle, tmp_path, live.s4.LIVE_CONFIG, dispatch,
                                live.live_dev01._event_summary, live.live_dev01.EVENT_TYPES, lambda: True)
    runtime.open_run(task, handle)
    def turn(index, kind, intervention=None):
        message = "fixed synthetic prompt"
        return TurnRequest(task.run_id, f"turn-{index:04}", handle, session if intervention else None,
                           kind, message, sha256_bytes(message.encode()), intervention, 500, "manifest.json")
    runtime.run_unit(turn(1, "INITIAL"), lambda _: None)
    for i in (2, 3):
        runtime.continue_unit(turn(i, "RECOVERY", f"intervention-{i}"), lambda _: None)
    with pytest.raises(ContractError, match="approved scope"):
        runtime.continue_unit(turn(4, "RECOVERY", "intervention-4"), lambda _: None)
    assert calls == [None, session, session]


def fake_turn(session):
    events = ({"type": "thread.started", "thread_id": session}, {"type": "turn.started"},
              {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}})
    stdout = b"\n".join(canonical_json_bytes(event) for event in events)
    return SimpleNamespace(status="EXITED", exit_code=0, stdout=stdout, stderr=b"", remaining_process_count=0)


def test_pilot_schedule_passes_all_tasks_and_keeps_attempts_on_resume(tmp_path, monkeypatch):
    root = tmp_path / live.LIVE_ID
    monkeypatch.setattr(live, "LIVE_ROOT", root)
    environment = "6" * 64
    monkeypatch.setattr(assessment, "environment_hash", lambda: environment)
    launches = []

    def native(directory, work_root, request, operator, *, execute, experiment):
        assert not execute
        manager = WorkspaceManager(request.repository.source_path, work_root, directory / "artifacts",
                                   quiescence_check=lambda _: True)
        handle = manager.prepare(request)
        def dispatch(data, seconds, *, persist, session_id):
            operator["launch_attempts"] += 1
            launches.append((request.task_id, request.policies.condition))
            source = prepare.FIXTURES / request.task_id / "module.py"
            (handle.root / "module.py").write_bytes(source.read_bytes())
            return fake_turn(str(uuid4()))
        experiment(manager, handle, dispatch, lambda: True)

    def observe(task_id, submitted, cases, deadline):
        reference = (prepare.FIXTURES / task_id / "module.py").read_bytes()
        if submitted == reference:
            return [True] * len(cases)
        assert submitted == (prepare.task_directory(task_id) / "base/module.py").read_bytes()
        public_functional = json.loads((prepare.task_directory(task_id) / "public.json").read_bytes())["P-FUNC"]
        return [case not in public_functional for case in cases]

    def verdict(directory, run_root, request):
        assert isinstance(request, EvaluationRequest)
        return assessment.evaluate(request, run_root / "artifacts")

    monkeypatch.setattr(live.live_dev01, "_run", native)
    monkeypatch.setattr(assessment, "observe", observe)
    monkeypatch.setattr(live, "independent_verdict", verdict)
    draft = prepare.read_pinned_draft()
    summary = live.execute(draft, environment, live.approval(draft))
    assert len(launches) == len(set(launches)) == 25
    assert all(row["evaluation_status"] == "PASS" and row["model_tokens"] == 6 for row in summary["runs"])
    assert live.execute(draft, environment, live.approval(draft)) == summary
    assert len(launches) == 25
