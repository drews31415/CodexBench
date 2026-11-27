"""Graph-only D uses the normal gate, DEFAULT renderer and subsequent-turn observer."""

import json
from dataclasses import replace

import networkx as nx
import pytest

from aah.context import dcc
from aah.domain import (Condition, ContractError, RawEvent, canonical_json_bytes, dump_record, load_record,
                        CheckResult, TestCaseResult as Case)
from aah.graphs import service
from aah.graphs.artifact import GraphArtifact
from aah.workspace import WorkspaceError
from benchmarks.smoke import s3


def rows(root, name):
    return [json.loads(line) for line in (root / (name + ".jsonl")).read_bytes().splitlines()]


@pytest.mark.parametrize(("scenario", "expected", "signal"),
                         [("repeat", "RELOCALIZE", "T1"), ("regression", "REVIEW_REGRESSION", "T3")])
def test_d_strategy_delivery_and_observed_action(tmp_path, monkeypatch, scenario, expected, signal):
    def forbid(*args, **kwargs):
        pytest.fail("D invoked DCC")
    monkeypatch.setattr(dcc, "select", forbid)
    report, turns = s3.replay(scenario, tmp_path)
    assert report.condition == "D" and report.stop_reason == "COMPLETED" and report.public_check_status == "PASS"
    assert [t.kind for t in turns] == ["INITIAL", "RECOVERY"]
    root = tmp_path / report.run_id
    journal = rows(root, "interventions")
    first, package = journal[:2]
    assert first["strategy"] == expected and first["trigger_ids"][0] == signal
    assert first["targets"] == ["labels.py"] and first["evidence_ids"]
    assert package["message_hash"] == turns[1].message_hash and expected in turns[1].message
    context = rows(root, "context")
    assert [c["policy"] for c in context] == ["DEFAULT", "DEFAULT"]
    support = next(item for item in context[1]["decisions"]
                   if item["reason_code"] == "CORE_REQUIRED" and item["kind"] == "relation_evidence")
    assert support["path_refs"][0] in turns[1].message
    info = first["graph"]
    artifact_file = root / "graphs" / (info["graph_id"] + ".json")
    artifact = load_record(GraphArtifact, artifact_file.read_bytes())
    assert dump_record(artifact) == artifact_file.read_bytes() and artifact.graph_id == info["graph_id"]
    with pytest.raises(ContractError):
        replace(artifact, graph_id="invalid")
    trace = json.loads((root / "graphs" / (info["trace_id"] + ".json")).read_bytes())
    paths = {p["path_ref"]: p for p in trace["paths"]}
    path = paths[support["path_refs"][0]]
    assert {"RETURNS", "TESTS_TARGET"}.issubset(path["relations"])
    assert "OBSERVED_AFTER" not in path["relations"]
    assert all(c["observation_status"] == "inferred" for c in trace["candidates"])
    adherence = journal[-1]["adherence"]
    evidence = {r["evidence_id"]: r for r in rows(root, "evidence")}
    assert adherence["status"] == "performed"
    assert all(evidence[e]["turn_id"] == turns[1].turn_id and evidence[e]["payload"]["path"] == "labels.py"
               for e in adherence["evidence_ids"])
    assert len([r for r in evidence.values() if r["kind"] == "INTERVENTION"]) == 1
    assert rows(root, "transitions")[-2]["strategy_id"] == "STOP"


def test_flat_conditions_and_initial_parity(tmp_path, monkeypatch):
    _, turns = s3.replay("repeat", tmp_path)
    def forbid(*args, **kwargs):
        pytest.fail("graph invoked for a flat or native condition")
    monkeypatch.setattr(service, "update", forbid)
    for condition in (Condition.A, Condition.B, Condition.C):
        report, flat = s3.replay("repeat", tmp_path, condition=condition)
        assert not (tmp_path / report.run_id / "graphs").exists()
        if condition == Condition.B:
            assert flat[0].message.encode() == turns[0].message.encode()
        if condition != Condition.A:
            assert "GENERIC_RETRY" in flat[1].message and "path_ref" not in flat[1].message


def test_graph_failure_falls_back_but_integrity_failure_stops(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise nx.NetworkXError("fixture failure")
    monkeypatch.setattr(service, "diagnose", unavailable)
    report, turns = s3.replay("repeat", tmp_path)
    root = tmp_path / report.run_id
    assert report.condition == "D" and "GENERIC_RETRY" in turns[1].message
    assert "GRAPH_UNAVAILABLE" in report.warnings and report.recoveries_reserved == 1
    assert rows(root, "interventions")[0]["graph"]["fallback_reason"] == "NetworkXError"
    def corrupt(*args, **kwargs):
        raise WorkspaceError("bad blob hash")
    monkeypatch.setattr(service.repository, "build", corrupt)
    failed, turns = s3.replay("repeat", tmp_path)
    assert failed.artifact_status == "incomplete" and failed.recoveries_reserved == 0 and len(turns) == 1


def test_typed_core_overflow_repackages_once_or_stops(tmp_path):
    for cap, reserved in ((170, 1), (15, 0)):
        report, turns = s3.replay("repeat", tmp_path, cap=cap)
        root = tmp_path / report.run_id
        assert report.condition == "D" and report.recoveries_reserved == reserved
        context = rows(root, "context")
        recovery = [c for c in context if c["stage"] == "RECOVERY"]
        assert [c["strategy_id"] for c in recovery] == ["RELOCALIZE", "GENERIC_RETRY"]
        assert recovery[0]["status"] == "CORE_OVER_CAP"
        assert sum(r["event"] == "FALLBACK" for r in rows(root, "interventions")) == 1
        if reserved:
            assert recovery[1]["status"] == "OK" and "GENERIC_RETRY" in turns[1].message
        else:
            assert recovery[1]["status"] == "CORE_OVER_CAP" and report.stop_reason == "NO_SAFE_CONTINUATION"


def test_delivery_without_matching_observation_has_unknown_adherence(tmp_path):
    class NoChange(s3.LabelAdapter):
        def continue_unit(self, request, on_event):
            return self._finish(request, on_event)
    report, turns = s3.replay("repeat", tmp_path, adapter_type=NoChange)
    assert report.public_check_status == "FAIL" and len(turns) == 2
    journal = rows(tmp_path / report.run_id, "interventions")
    assessment = journal[-1]["adherence"]
    assert assessment["status"] == "unknown" and assessment["basis"] == "insufficient_observation"
    assert assessment["evidence_ids"] == [] and assessment["check_evidence_ids"]


def test_historical_location_is_labelled_and_current_read_is_observed(tmp_path, monkeypatch):
    versions = []

    def stale_check(snapshot, check_id, artifact_root, *, deadline, turn_id):
        versions.append(snapshot.snapshot_id)
        case = Case("opaque", "FAIL", "AssertionError", "public failure", {"path": "labels.py", "line": 2}, "c"*64)
        return CheckResult(f"check-{turn_id}", check_id, s3.DEFINITION, turn_id, snapshot.snapshot_id,
                           "FAIL", "TRUSTED_RUNNER", 1, 0, "d"*64, "synthetic-location", (case,), None, None, ())

    class MovedLine(s3.LabelAdapter):
        def run_unit(self, request, on_event):
            (request.workspace.root / "labels.py").write_bytes(b"def label(): return 'pending'\n")
            return self._finish(request, on_event)

        def continue_unit(self, request, on_event):
            assert (request.workspace.root / "labels.py").read_bytes().count(b"\n") == 1
            outcome = self._finish(request, on_event)
            ref = "restricted/fixture-read.json"
            (self.results_root / request.run_id / ref).write_bytes(canonical_json_bytes(
                {"type": "file_access.observed", "path": "labels.py", "snapshot_id": versions[-1],
                 "access_kind": "read", "access_basis": "direct_read"}))
            on_event(RawEvent(request.run_id, request.turn_id, "fixture-read", 2, "2026-11-26T00:00:00Z",
                              None, "file_access.observed", ref, "PARSED"))
            return replace(outcome, event_refs=(*outcome.event_refs, "fixture-read"))

    monkeypatch.setattr(s3, "check", stale_check)
    report, turns = s3.replay("repeat", tmp_path, adapter_type=MovedLine)
    assert len(turns) == 2 and "REFRESH_EVIDENCE" in turns[1].message and "HISTORICAL" in turns[1].message
    journal = rows(tmp_path / report.run_id, "interventions")
    assert journal[0]["strategy"] == "REFRESH_EVIDENCE" and journal[-1]["adherence"]["status"] == "performed"
    assert report.public_check_status == "FAIL" and report.recoveries_delivered == 1
