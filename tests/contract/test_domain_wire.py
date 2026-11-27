"""CT02 – validate version, encoding, nested records, payload kinds and nulls."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from aah.domain import (
    BudgetState, CapabilityStatus, Condition, ContractError, EvidenceRecord, FixedRequirements,
    PolicySet, RepositoryRef, RunRequest, RunState, SnapshotRef, TurnRequest, TurnResult,
    WorkspaceHandle, canonical_json_bytes, dump_record, load_record, resolve_condition, sha256_bytes,
)


RID = "77aca7af-626c-493d-a1d2-ee4eefc45bd1"
DIGEST = "a" * 64


def run_request():
    return RunRequest(
        RID, "DEV-fixture", None, RepositoryRef("fixture", Path("fixture"), "base"),
        FixedRequirements("수정", "보존\n", sha256_bytes("보존\n".encode()), "test-profile", ["check"]),
        resolve_condition(Condition.D), DIGEST, "research",
    )


def evidence(payload=None):
    return EvidenceRecord(
        evidence_id="e-1", run_id=RID, turn_id="turn-1", sequence=1, kind="TEST",
        raw_refs=["raw-1"], observed_at=None, observation_status="observed", snapshot_id=DIGEST,
        snapshot_interval=None, source_order=None, warnings=[],
        payload=payload if payload is not None else {
            "check_result_id": "result-1", "check_id": "check", "test_id": None,
            "status": "UNKNOWN", "command_id": None,
        },
    )


def test_canonical_wire_and_nested_roundtrip():
    request = run_request()
    wire = dump_record(request)
    assert load_record(RunRequest, wire) == request
    data = json.loads(wire)
    assert data["schema_version"] == "1.0"
    assert data["experiment_id"] is None
    assert data["fixed"]["approved_check_ids"] == ["check"]
    assert "수정".encode() in wire
    assert canonical_json_bytes({"z": 2, "a": 1}) == b'{"a":1,"z":2}'
    with pytest.raises(FrozenInstanceError):
        request.policies = resolve_condition(Condition.A)


@pytest.mark.parametrize("malform", [
    lambda obj: obj.pop("schema_version"),
    lambda obj: obj.update(schema_version="2.0"),
    lambda obj: obj.update(schema_version=True),
    lambda obj: obj.pop("task_id"),
    lambda obj: obj.update(slot_id="unrelated-field"),
    lambda obj: obj["repository"].pop("initial_commit"),
    lambda obj: obj["repository"].update(source_path="../fixture"),
    lambda obj: obj["repository"].update(source_path=r"C:\private\fixture"),
    lambda obj: obj["fixed"].update(mandatory_hash="abc"),
    lambda obj: obj["fixed"].update(mandatory_text="edited"),
    lambda obj: obj["fixed"].update(approved_check_ids="abc"),
    lambda obj: obj["policies"].update(external_intervention=1),
    lambda obj: obj["policies"].update(strategy="GENERIC"),
    lambda obj: obj.update(run_id="invented"),
])
def test_rejects_wrong_envelope_and_nested_payload(malform):
    data = json.loads(dump_record(run_request()))
    malform(data)
    with pytest.raises(ContractError):
        load_record(RunRequest, canonical_json_bytes(data))


@pytest.mark.parametrize(("bad_json", "reason"), [
    (b'{"a":1,"a":2}', "duplicate JSON"), (b'{"a":{"b":0,"b":1}}', "duplicate JSON"),
    (b'{"schema_version":"1.0","x":NaN}', "invalid canonical JSON"),
    (b'{"x":1e500}', "invalid canonical JSON"), (b'{}\xff', "invalid UTF-8"),
    (b'{"x":"\\ud800"}', "invalid canonical JSON"), (b'null', "must be an object"),
    (b'{"bool":true} {}', "invalid UTF-8 JSON"),
    ('{"schema_version":"1.0"}'.encode("utf-16"), "invalid UTF-8"),
])
def test_rejects_ambiguous_or_non_utf8_json(bad_json, reason):
    with pytest.raises(ContractError, match=reason):
        load_record(PolicySet, bad_json)


@pytest.mark.parametrize("fields", [
    {"captured_at": "2026-02-30T00:00:00Z"}, {"captured_at": "2026-01-01T00:00:00"},
    {"manifest_ref": "/manifest"}, {"manifest_ref": "../manifest"}, {"snapshot_id": "x"},
    {"complete": 1},
])
def test_snapshot_validation(fields):
    original = SnapshotRef("fixture", DIGEST, "base", "snapshots/m.json", "2026-11-25T00:00:00Z", True)
    assert load_record(SnapshotRef, dump_record(original)) == original
    with pytest.raises(ContractError):
        replace(original, **fields)


def test_evidence_is_a_snapshot_of_its_input():
    original = dict(evidence().payload)
    record = evidence(original)
    original["status"] = "PASS"
    assert record.payload["status"] == "UNKNOWN"
    assert load_record(EvidenceRecord, dump_record(record)) == record
    with pytest.raises(TypeError):
        record.payload["status"] = "FAIL"
    with pytest.raises(ContractError):
        replace(record, kind="COMMAND")
    with pytest.raises(ContractError):
        replace(record, payload={**record.payload, "status": "INVALID"})
    with pytest.raises(ContractError):
        replace(record, kind="COMPLETION", payload={"declared": True, "source_ref": None, "basis_id": None})


def test_turn_provenance_quiescence_and_missing_usage():
    state = BudgetState(0, 1000, 0, 0, 0, None, CapabilityStatus.UNVERIFIED)
    run = RunState(RID, "INIT", 0, 0, 0, None, 0, None, state, None)
    assert load_record(RunState, dump_record(run)).budget.observed_model_tokens is None
    assert replace(state, observed_model_tokens=0) != state
    with pytest.raises(ContractError):
        replace(run, recovery_reserved=1)
    workspace = WorkspaceHandle(RID, Path("isolated"), "base", "profile", False)
    turn = TurnRequest(RID, "turn-1", workspace, None, "INITIAL", "task", sha256_bytes(b"task"),
                       None, 400, "configs/runtime.json")
    assert load_record(TurnRequest, dump_record(turn)) == turn
    with pytest.raises(ContractError):
        replace(turn, message="different")
    with pytest.raises(ContractError):
        replace(turn, kind="RECOVERY")
    result = TurnResult("turn-1", "COMPLETED", None, None, None, None, False, 0, (), (), None, True)
    assert load_record(TurnResult, dump_record(result)).completion_declared is None
    with pytest.raises(ContractError):
        replace(result, completion_declared=True)


@pytest.mark.parametrize(("condition", "context", "diagnosis", "strategy", "enabled"), [
    ("A", "NONE", "NONE", "NATIVE", False),
    ("B", "DEFAULT", "FLAT", "GENERIC", True),
    ("C", "DCC", "FLAT", "GENERIC", True),
    ("D", "DEFAULT", "GRAPH", "TYPED", True),
    ("E", "DCC", "GRAPH", "TYPED", True),
])
def test_condition_resolver_matches_spec(condition, context, diagnosis, strategy, enabled):
    policies = resolve_condition(Condition(condition))
    assert policies.initial_context == policies.recovery_context == context
    assert (policies.diagnosis, policies.strategy, policies.external_intervention) == (
        diagnosis, strategy, enabled
    )
