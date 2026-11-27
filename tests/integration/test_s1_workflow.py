"""S1 contract and vertical-slice regressions against public fixtures."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from aah.cli import main
from aah.context.default import render
from aah.diagnosis.detector import detect
from aah.domain import (
    CheckResult, CheckStatus, CleanupResult, Condition, ContractError, RawEvent, SnapshotRef, StopReason,
    TestCaseResult as CaseResult, TriggerId, TurnResult, UnitStatus, UsageMode, resolve_condition,
)
from aah.evidence.ledger import EvidenceLedger
from aah.reporting.store import RunStore
from aah.runtime.observer import Observer
from aah.runtime.usage import UsageAccount
from aah.workflow.budget import Budget, Limits
from aah.workflow.orchestrator import run
from benchmarks.smoke.public_checks import run_approved
from benchmarks.smoke.s1 import CONFIG, SyntheticAdapter, replay
from evaluation.smoke import fixture_request


NOW = "2026-11-26T00:00:00Z"
SNAPSHOT = SnapshotRef("DEV-01", "a" * 64, "base", "snapshots/a.json", NOW, True)


def rows(path: Path):
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def test_synthetic_a_b_report_and_public_boundary(tmp_path, capsys):
    a, native = replay(Condition.A, tmp_path)
    b, retry = replay(Condition.B, tmp_path)
    request = fixture_request("DEV-01")
    fixed = request.fixed.task + "\n\n" + request.fixed.mandatory_text
    assert [turn.message for turn in native] == [fixed]
    assert a.public_check_status == CheckStatus.FAIL and a.recoveries_reserved == 0
    assert not (tmp_path / a.run_id / "interventions.jsonl").exists()
    assert b.public_check_status == CheckStatus.PASS and b.evaluable
    assert b.frozen_patch.changed_paths == ("module.py",)
    assert [turn.kind for turn in retry] == ["INITIAL", "RECOVERY"]
    assert all(turn.message.startswith(fixed + "\n\n") for turn in retry)
    recovery = retry[1].message
    assert "GENERIC_RETRY" in recovery and "DEV-01-P-FUNC" in recovery and "tab-edges" in recovery
    assert retry[1].session_ref == "synthetic-session"
    journal = rows(tmp_path / b.run_id / "interventions.jsonl")
    assert [row["event"] for row in journal] == [
        "DECIDED", "PACKAGE_READY", "RESERVED", "DELIVERED", "OUTCOME_OBSERVED",
    ]
    assert journal[0]["trigger_ids"] == ["T4"]
    phases = rows(tmp_path / b.run_id / "transitions.jsonl")
    assert phases[-1]["to_phase"] == "REPORT"
    assert b.usage_summary["observed_model_tokens"] is None
    assert main(["report", "--run-id", b.run_id, "--results-root", str(tmp_path)]) == 0
    public = json.loads(capsys.readouterr().out)
    assert public["run_id"] == b.run_id and "evaluation_status" not in public


def test_default_initial_byte_parity_between_b_and_d():
    request = fixture_request("DEV-01")
    a, b, d = (render(replace(request, policies=resolve_condition(condition)), SNAPSHOT, cap=2048)
               for condition in (Condition.A, Condition.B, Condition.D))
    assert b.text.encode("utf-8") == d.text.encode("utf-8")
    assert a.supplement == "" and b.text.startswith(a.text + "\n\n")


def test_clock_reserve_turn_dedup_and_checkpoint_are_independent():
    now = [100.0]
    budget = Budget(Limits(10_000, 4, 2, 20, 2_000), lambda: now[0])
    budget.reserve("initial")
    budget.reserve("retry", checkpoint="checkpoint", intervention_id="one")
    with pytest.raises(ContractError):
        budget.reserve("retry", checkpoint="other", intervention_id="two")
    with pytest.raises(ContractError):
        budget.reserve("retry-2", checkpoint="checkpoint", intervention_id="two")
    assert len(budget.turns) == 2 and len(budget.recoveries) == 1
    now[0] = 108.1
    assert budget.work_ms == 0 and budget.state().remaining_ms > 0
    with pytest.raises(ContractError):
        budget.reserve("late")


def observer(tmp_path):
    run_id = str(uuid4())
    store = RunStore(tmp_path / run_id, run_id)
    ledger = EvidenceLedger(store)
    return Observer(store, ledger), ledger, run_id


def outcome(turn_id, refs=(), **options):
    return replace(TurnResult(turn_id, UnitStatus.COMPLETED, "session", None, None, None,
                              False, 0, refs, (), None, True), **options)


def check(turn_id, result_id, status=CheckStatus.FAIL, *, check_id="check", definition="b", fingerprint="d"):
    case = CaseResult("case", status, "AssertionError" if status == CheckStatus.FAIL else None,
                          None, None, fingerprint * 64 if status == CheckStatus.FAIL else None)
    return CheckResult(result_id, check_id, definition * 64, turn_id, SNAPSHOT.snapshot_id, status, "TRUSTED_RUNNER",
                       0, 1, "c" * 64, "fixture", (case,), None, None, ())


def test_observer_keeps_raw_refs_and_never_infers_file_access(tmp_path):
    item, ledger, run_id = observer(tmp_path)
    raw = RawEvent(run_id, "t1", "raw1", 1, NOW, "provider-1", "item.completed", "restricted/item.json", "OK")
    folder = item.store.root / "restricted"
    folder.mkdir()
    (folder / "item.json").write_bytes(
        b'{"type":"item.completed","item":{"type":"command_execution","id":"any","command":"echo secret.py"}}')
    (folder / "bad.json").write_bytes(b'{"type":')
    duplicate = replace(raw, raw_id="raw2", received_seq=2)
    bad = replace(raw, raw_id="raw3", received_seq=3, provider_event_id=None, payload_ref="restricted/bad.json")
    for event in (raw, duplicate, bad, raw):
        item.receive(event, "t1")
    item.collect(outcome("t1", ("raw1", "raw2", "raw3")), SNAPSHOT, SNAPSHOT, None, ())
    view = ledger.view()
    assert [record.kind for record in view.records] == ["COMMAND_RESULT", "UNKNOWN", "COMPLETION"]
    assert view.records[0].raw_refs == ("raw1",) and view.records[0].snapshot_id is None
    assert view.records[1].raw_refs == ("raw3",) and view.records[1].observation_status == "unknown"
    assert view.records[2].payload["declared"] is None
    assert len(rows(item.store.root / "events.raw.jsonl")) == 3
    assert "secret.py" not in (item.store.root / "evidence.jsonl").read_text()


def test_detector_requires_repeat_and_prioritizes_new_regression(tmp_path):
    item, ledger, _ = observer(tmp_path)
    first = check("t1", "first")
    item.collect(outcome("t1"), SNAPSHOT, SNAPSHOT, None, (first, check("t1", "pass", CheckStatus.PASS, check_id="reg")))
    item.collect(outcome("t1"), SNAPSHOT, SNAPSHOT, None, (first,))
    assert detect(ledger.view(), SNAPSHOT, "t1", ("check", "reg")).signals == ()
    item.collect(outcome("t2"), SNAPSHOT, SNAPSHOT, None, (
        check("t2", "second"), check("t2", "regression", check_id="reg"),
    ))
    assert [x.kind for x in detect(ledger.view(), SNAPSHOT, "t2", ("check", "reg")).signals] == [
        TriggerId.T3, TriggerId.T1,
    ]
    item.collect(outcome("t3"), SNAPSHOT, SNAPSHOT, None, (check("t3", "different", definition="e"),))
    assert detect(ledger.view(), SNAPSHOT, "t3", ("check",)).signals == ()


@pytest.mark.parametrize(("searches", "expected"), [(("a",) * 5, False), (("a",) * 5 + ("b",), False),
                                                   (("a",) * 6, True)])
def test_stagnation_uses_full_window_and_exact_ratio(tmp_path, searches, expected):
    item, ledger, run_id = observer(tmp_path)
    item.collect(outcome("t1"), SNAPSHOT, SNAPSHOT, None, (check("t1", "first", CheckStatus.PASS),))
    folder = item.store.root / "restricted"
    folder.mkdir()
    for index, query in enumerate(searches):
        ref = f"restricted/s{index}.json"
        (item.store.root / ref).write_text(json.dumps(
            {"type": "search.observed", "query": query, "scope": ".", "result_digest": "f" * 64,
             "snapshot_id": SNAPSHOT.snapshot_id}), encoding="utf-8")
        item.receive(RawEvent(run_id, "t2", f"s{index}", index, NOW, None, "search.observed", ref, "OK"), "t2")
    item.collect(outcome("t2", tuple(f"s{index}" for index in range(len(searches)))), SNAPSHOT, SNAPSHOT,
                 None, (check("t2", "next", CheckStatus.PASS),))
    signals = detect(ledger.view(), SNAPSHOT, "t2", ("check",)).signals
    assert (TriggerId.T2 in {signal.kind for signal in signals}) is expected


def test_usage_missing_breaks_cumulative_attribution():
    usage = UsageAccount()
    mode = UsageMode.CUMULATIVE
    assert usage.record(mode, "s", {"input_tokens": 10, "cached_input_tokens": 9, "output_tokens": 2},
                        fresh_session=True)["input_tokens"] == 10
    assert usage.record(mode, "s", None, fresh_session=False) is None
    assert usage.record(mode, "s", {"input_tokens": 20, "output_tokens": 4}, fresh_session=False) is None
    assert usage.record(mode, "s", {"input_tokens": 26, "output_tokens": 5},
                        fresh_session=False)["input_tokens"] == 6
    assert usage.summary(4) == {"observed_model_tokens": None, "known_model_tokens_subtotal": 19}


@pytest.mark.parametrize("mode", ["cancel", "timeout", "unconfirmed", "same_failure", "publish"])
def test_workflow_bounded_errors_preserve_evidence(tmp_path, monkeypatch, mode):
    output = tmp_path / "results"
    request = replace(fixture_request("DEV-01"), policies=resolve_condition(Condition.B))
    adapter = SyntheticAdapter(output)
    if mode in ("timeout", "unconfirmed"):
        adapter.run_unit = lambda turn, callback: outcome(
            turn.turn_id, status=UnitStatus.TIMEOUT, process_quiescent=mode == "timeout")
    if mode == "unconfirmed":
        adapter.close_run = lambda run_id: CleanupResult(False, 1, None)
    if mode == "same_failure":
        adapter.continue_unit = adapter.run_unit
    if mode == "publish":
        def broken_publish(self, report):
            raise OSError("simulated publication failure")
        monkeypatch.setattr(RunStore, "publish", broken_publish)

    def execute():
        return run(request, runtime=adapter, config=CONFIG, public_root=request.repository.source_path,
                   work_root=tmp_path / "work", results_root=output, limits=Limits(30_000, 4, 2, 100, 5_000),
                   check=run_approved, supplement_cap=2048, cancelled=lambda: mode == "cancel")

    root = output / request.run_id
    if mode == "publish":
        with pytest.raises(OSError):
            execute()
        assert (root / "incomplete.json").is_file() and (root / "checks.jsonl").is_file()
        return
    report = execute()
    if mode == "cancel":
        assert report.stop_reason == StopReason.CANCELED and report.turns_used == 0 and not adapter.requests
    elif mode == "timeout":
        assert report.stop_reason == StopReason.GLOBAL_LIMIT and report.evaluable
    elif mode == "unconfirmed":
        assert report.stop_reason == StopReason.SAFETY_VIOLATION and report.frozen_patch is None
        assert (tmp_path / "work" / request.run_id).is_dir()
    else:
        assert report.stop_reason == StopReason.NO_SAFE_CONTINUATION and report.recoveries_reserved == 1
        assert "DUPLICATE_CHECKPOINT" in report.warnings
