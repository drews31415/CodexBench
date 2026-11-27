import json
import sys
from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aah.cli import main
from aah.context import dcc
from aah.domain import (Condition, ContractError, RawEvent, SessionMode, TurnResult, canonical_json_bytes,
                        load_record, resolve_condition, sha256_bytes)
from aah.domain.checkpoint import BranchRef, CheckpointRecord
from aah.domain.usage import UsageRecord
from aah.evidence.ledger import EvidenceLedger
from aah.experiments.runner import token_cps
from aah.graphs import service
from aah.reporting.store import RunStore
from aah.runtime.observer import Observer
from aah.workflow import orchestrator
from aah.workflow.budget import Limits
from benchmarks.smoke import live_dev01, s1, s3, s4, s4_checkpoint
from benchmarks.smoke.public_checks import run_approved
from evaluation.smoke import fixture_request


def rows(directory, stream):
    return [json.loads(line) for line in (directory / (stream + ".jsonl")).read_bytes().splitlines()]


def test_five_conditions_keep_policy_calls_and_initial_inputs_separate(tmp_path, monkeypatch):
    active = {}
    inputs, gates, fixed = {}, {}, {}
    check = s3.check
    def same_observation(*args, **kwargs):
        result = check(*args, **kwargs)
        return replace(result, check_result_id="check-" + (result.turn_id or "baseline"))
    monkeypatch.setattr(s3, "check", same_observation)
    for obj, name in ((dcc, "select"), (service, "update")):
        original = getattr(obj, name)
        def observe(*args, original=original, name=name, **kwargs):
            active[name] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(obj, name, observe)
    for condition in Condition:
        active = {"select": 0, "update": 0}
        report, turns = s3.replay("repeat", tmp_path, condition=condition)
        root = tmp_path / report.run_id
        inputs[condition] = turns[0].message.encode()
        assert active == {"select": 2 if condition in ("C", "E") else 0,
                          "update": 1 if condition in ("D", "E") else 0}
        config = json.loads((root / "manifest.json").read_bytes())
        fixed[condition] = (config["fixed_requirements_hash"], config["limits"], config["supplement_cap"])
        if condition == "A":
            assert len(turns) == 1 and not (root / "interventions.jsonl").exists()
            continue
        log = rows(root, "interventions")
        gates[condition] = (log[0]["checkpoint_key"], log[0]["trigger_ids"], log[0]["anchors"])
        assert len(turns) == 2 and log[-1]["event"] == "OUTCOME_OBSERVED"
        if condition == "E":
            assert log[0]["strategy"] == "RELOCALIZE" and "RELOCALIZE" in turns[1].message
            assert log[-1]["adherence"]["status"] == "performed"
            assert rows(root, "context")[1]["policy"] == "DCC"
    assert inputs["B"] == inputs["D"] and inputs["C"] == inputs["E"]
    assert all(value == fixed["A"] for value in fixed.values())
    assert all(value == gates["B"] for value in gates.values())


@pytest.mark.parametrize(("cap", "reserved"), [(170, 1), (15, 0)])
def test_e_fallback_keeps_budget_and_condition(tmp_path, cap, reserved):
    report, _ = s3.replay("repeat", tmp_path, condition=Condition.E, cap=cap)
    root = tmp_path / report.run_id
    assert report.condition == "E" and report.recoveries_reserved == reserved
    assert sum(r["event"] == "FALLBACK" for r in rows(root, "interventions")) == 1
    assert [r["strategy_id"] for r in rows(root, "context") if r["stage"] == "RECOVERY"] == [
        "RELOCALIZE", "GENERIC_RETRY"]


def test_checkpoint_first_recovery_and_corrupt_source(tmp_path):
    class Metered(s4_checkpoint.CheckpointAdapter):
        def probe_capabilities(self, config):
            return replace(super().probe_capabilities(config), usage_mode="DELTA")

        def _finish(self, request, callback):
            outcome = super()._finish(request, callback)
            ref = f"restricted/{request.turn_id}-usage.json"
            amount = 10 if request.kind == "INITIAL" else 4
            (self.results_root / request.run_id / ref).write_bytes(canonical_json_bytes({
                "type": "turn.completed", "usage": {"input_tokens": amount, "output_tokens": 1}}))
            raw_id = "usage-event-" + request.turn_id
            callback(RawEvent(request.run_id, request.turn_id, raw_id, 2, "2026-11-26T00:00:00Z",
                              None, "turn.completed", ref, "PARSED"))
            return replace(outcome, event_refs=(*outcome.event_refs, raw_id))

    root = tmp_path / "checkpoint"
    summary = s4_checkpoint.verify(root, adapter_type=Metered)
    assert summary["source_turns"] == 1
    assert {r["condition"] for r in summary["branches"]} == {"B", "C", "D", "E"}
    assert all(r["evaluation_status"] == "PASS" and r["model_turns"] == 1 for r in summary["branches"])
    assert summary["source_model_tokens"] == 11 and [r["model_tokens"] for r in summary["branches"]] == [5]*4
    runs = root / "runs"
    raw = (runs / summary["source_run_id"] / "checkpoint.json").read_bytes()
    record = load_record(CheckpointRecord, raw)
    assert record.workspace_bundle.changed_paths == ("observed.txt",)
    assert record.initial_usage_refs and not record.session_clone_verified
    for row in summary["branches"]:
        target = runs / row["run_id"]
        assert [t["to_phase"] for t in rows(target, "transitions")][:2] == ["RESTORE_VERIFY", "DECIDE"]
        assert [u["turn_id"] for u in rows(target, "usage")] == ["branch-turn-0001"]
        assert rows(target, "interventions")[0]["checkpoint_key"] == record.source_gate_key
        assert json.loads((target / "manifest.json").read_bytes())["limits"] == summary["remaining_limits"]
    request = replace(fixture_request("DEV-01"), experiment_id=record.experiment_id, mode="research",
                      config_hash=record.config_hash, policies=resolve_condition(Condition.E))
    kwargs = dict(config=replace(s1.CONFIG, session_mode=SessionMode.NEW_SESSION), public_root=request.repository.source_path,
                  work_root=tmp_path / "work", results_root=runs, check=run_approved, supplement_cap=2048,
                  limits=Limits(**asdict(record.remaining_limits)))
    class Unchanged(Metered):
        def continue_unit(self, turn, callback):
            return self._finish(turn, callback)

    valid = BranchRef(record.source_run_id, record.checkpoint_id, sha256_bytes(raw), "unchanged-branch")
    adapter = Unchanged(runs)
    stalled = orchestrator.run(request, runtime=adapter, branch=valid, **kwargs)
    assert [t.kind for t in adapter.requests] == ["RECOVERY"]
    assert stalled.recoveries_reserved == 1 and "DUPLICATE_CHECKPOINT" in stalled.warnings
    ref = BranchRef(record.source_run_id, record.checkpoint_id, "0"*64, "tampered-branch")
    adapter = Metered(runs)
    result = orchestrator.run(replace(request, run_id=str(uuid4())), runtime=adapter, branch=ref, **kwargs)
    assert not adapter.requests and not result.evaluable and result.turns_used == 0


def test_manifest_replay_does_not_launch_or_recount(tmp_path, monkeypatch):
    root = tmp_path / "trial"
    summary = s4.execute(root, "trial")
    assert {r["condition"]: r["evaluation_status"] for r in summary["runs"]} == {
        "A": "FAIL", "B": "PASS", "C": "PASS", "D": "PASS", "E": "PASS"}
    assert all(r["model_tokens"] is None and r["cost_amount"] is None for r in summary["runs"])
    def forbid(*args, **kwargs):
        pytest.fail("a saved attempt was dispatched again")
    monkeypatch.setattr(s4, "run", forbid)
    monkeypatch.setattr(s4, "_evaluate", forbid)
    assert main(["experiment", "--manifest", str(root / "experiment.json")]) == 0
    assert s4.replay_manifest(root / "experiment.json") == summary
    (root / "locked-config.json").write_bytes(b"{}")
    with pytest.raises(ContractError):
        s4.execute(root, "trial")


@pytest.mark.parametrize("mode", ["DELTA", "CUMULATIVE"])
def test_one_usage_receipt_per_terminal_observation(tmp_path, mode):
    rid = str(uuid4())
    store = RunStore(tmp_path / rid, rid)
    obj = {"type": "turn.completed", "usage": {"input_tokens": 15, "output_tokens": 5,
           "cached_input_tokens": 3, "reasoning_output_tokens": 2}}
    observer = Observer(store, EvidenceLedger(store), lambda _: canonical_json_bytes(obj))
    t = TurnResult("turn-1", "COMPLETED", "session", None, None, None, False, 0, ("event",), (), None, True)
    observer.receive(RawEvent(rid, t.turn_id, "event", 1, "2026-11-26T00:00:00Z", None, "turn.completed",
                              "restricted/event.json", "PARSED"), t.turn_id)
    for _ in range(2):
        observer.collect(t, None, None, None, (), usage_mode=mode, fresh_session=True, stage="INITIAL", elapsed_ms=7)
    assert observer.usage.summary(1)["observed_model_tokens"] == 20
    output = (store.root / "usage.jsonl").read_bytes().splitlines()
    assert len(output) == 1
    receipt = load_record(UsageRecord, output[0])
    assert receipt.total_tokens == 20 and receipt.cost_kind == "UNKNOWN" and receipt.cost_amount is None
    with pytest.raises(ContractError, match="conflicting usage"):
        observer.collect(t, None, None, None, (), usage_mode=mode, fresh_session=False, stage="INITIAL")


def test_cps_includes_failed_attempts_and_distinguishes_missing_coverage():
    def row(status, n):
        return {"evaluation_status": status, "model_tokens": n}
    assert token_cps([row("PASS", 10), row("FAIL", 6)])["tokens_per_success"] == 16
    assert token_cps([row("PASS", 10), row("FAIL", None)])["reason"] == "INCOMPLETE_COVERAGE"
    assert token_cps([row("FAIL", 6)])["reason"] == "NO_SUCCESS"
    assert token_cps([row("NOT_RUN", None)])["reason"] == "NO_VALID_RUNS"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows operator entry point")
def test_live_operator_transport_fixture_dispatches_at_most_nine(tmp_path, monkeypatch):
    from aah.runtime import windows_job

    auth = tmp_path / "auth"
    auth.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(auth))
    monkeypatch.setattr(live_dev01, "_find_codex", lambda: tmp_path / "codex.exe")
    monkeypatch.setattr(live_dev01.docker_worker, "identity", lambda: {"engine": "fixture"})
    root = tmp_path / s4.LIVE_ID
    monkeypatch.setattr(s4, "LIVE_ROOT", root)
    rt03 = tmp_path / "probe.json"
    rt03.write_text('{"codex_version":"0.166.0","runtime_checks":{"RT03":{"passed":true}}}')
    monkeypatch.setattr(s4, "LIVE_PROBE", str(rt03))
    manifest_hash = s4.fixture_manifest_hash
    monkeypatch.setattr(s4, "fixture_manifest_hash", lambda task, base, **kw: manifest_hash(task, base))
    monkeypatch.setattr(s4, "run_approved", lambda *a, dev01_candidate, **kw: run_approved(*a, **kw))
    evaluate = s4._evaluate
    monkeypatch.setattr(s4, "_evaluate", lambda root, slot, report, directory, _: evaluate(root, slot, report, directory, False))
    calls, sessions = [], {}
    def job(argv, *, stdin_data, cwd, **kw):
        args = tuple(argv[1:])
        if args == ("--version",):
            out = b"codex-cli 0.166.0"
        elif args == ("login", "status"):
            out = b"Logged in using ChatGPT"
        elif "sandbox" in args:
            out = "\n".join(live_dev01.PROBE_STEPS).encode()
        else:
            assert args[0] == "exec" and stdin_data
            resume = args[1] == "resume"
            if resume:
                assert args[-2] == sessions[cwd]
            else:
                assert cwd not in sessions
                sessions[cwd] = str(uuid4())
            calls.append((cwd, resume))
            events = [{"type": "thread.started", "thread_id": sessions[cwd]}, {"type": "turn.started"},
                      {"type": "turn.completed", "usage": {"input_tokens": 20 if resume else 10,
                                                         "output_tokens": 8 if resume else 4}}]
            out = b"\n".join(canonical_json_bytes(event) for event in events)
        return SimpleNamespace(status="EXITED", exit_code=0, stdout=out, stderr=b"", remaining_process_count=0)
    monkeypatch.setattr(windows_job, "run_windows_job", job)
    assert s4.live() == 0
    assert len(sessions) == 5 and sum(resume for _, resume in calls) == 4
    assert len(calls) == 9 and s4.live() == 0 and len(calls) == 9
    result = json.loads((root / "summary.json").read_bytes())
    assert [r["model_tokens"] for r in result["runs"]] == [28, 28, 14, 28, 28]
    assert all(r["evaluation_status"] == "FAIL" for r in result["runs"])
