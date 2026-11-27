"""CT02 – approved-check provenance and joins across planned slots, attempts and judgments."""

import json
from dataclasses import replace

import pytest

from aah.domain import (
    ApprovedCheckSpec, CheckResult, ContractError, EvaluationResult, FrozenPatch, RunAttempt, ScheduledSlot,
    TestCaseResult as Case, canonical_json_bytes, dump_record, load_record, validate_attempt_links,
    validate_check_binding, validate_evaluation_binding,
    sha256_bytes,
)


RID = "c5906726-968f-442e-a592-0045f225c7ea"
RID2 = "c5906726-968f-442e-a592-0045f225c7eb"
HASH = "0" * 64
OTHER = "1" * 64


def slot():
    return ScheduledSlot("pilot", "end_to_end", "slot", "block", "END_TO_END",
                         "DEV-fixture", None, None, "A", 1, 0)


def result():
    return EvaluationResult(RID, "DEV-fixture", "base", HASH, HASH, HASH, HASH, "PASS",
                            "PASS", "PASS", "PASS", 100, None, None)


def test_approved_definition_and_check_provenance():
    case = Case("public.test", "PASS", None, None, None, None)
    check = CheckResult("result", "public", HASH, None, HASH, "PASS", "TRUSTED_RUNNER",
                        0, 15, HASH, "fixture-v1", [case], None, None, [])
    assert load_record(CheckResult, dump_record(check)) == check
    expected = dict(check_id="public", check_definition_hash=HASH, snapshot_id=HASH, environment_hash=HASH)
    validate_check_binding(check, **expected)
    for field in expected:
        with pytest.raises(ContractError):
            validate_check_binding(check, **{**expected, field: OTHER})
    with pytest.raises(ContractError):
        replace(check, test_cases=[case, case])


def test_definition_hash_binds_the_exact_versioned_check():
    fields = {
        "check_id": "public", "kind": "P-FUNC", "argv": ["python", "-I", "check.py"],
        "cwd": ".", "timeout_ms": 5000, "parser_id": "json-v1", "required": True,
        "check_version": "1", "environment_profile_id": "synthetic", "trusted_test_ref": "checks/public",
        "trusted_test_hash": HASH,
    }
    spec = ApprovedCheckSpec(**fields)
    assert load_record(ApprovedCheckSpec, dump_record(spec)) == spec
    assert spec.check_definition_hash == sha256_bytes(canonical_json_bytes({"schema_version": "1.0", **fields}))
    compatible = canonical_json_bytes({"schema_version": "1.1", **fields})
    parsed = load_record(ApprovedCheckSpec, compatible)
    assert parsed.check_definition_hash == sha256_bytes(compatible)
    for update in [{"cwd": "checks"}, {"argv": ["python", "-I", "other.py"]},
                   {"required": False}, {"trusted_test_hash": OTHER}]:
        assert replace(spec, **update).check_definition_hash != spec.check_definition_hash
    for bad in [{"argv": []}, {"argv": "python check.py"}, {"argv": [" ", "check.py"]},
                {"argv": ["python", "a\0b"]}, {"cwd": "../unsafe"}, {"cwd": r"C:\outside"},
                {"timeout_ms": 0}, {"timeout_ms": 0.5}, {"required": 1},
                {"trusted_test_hash": "unverified"}, {"kind": "H-FUNC"}]:
        with pytest.raises(ContractError):
            replace(spec, **bad)


def test_evaluation_identity_and_nullable_components():
    verdict = result()
    patch = FrozenPatch(RID, "base", HASH, "artifacts/final.patch", HASH, [], True)
    validate_evaluation_binding(verdict, patch)
    with pytest.raises(ContractError):
        validate_evaluation_binding(replace(verdict, patch_hash=OTHER), patch)
    with pytest.raises(ContractError):
        replace(verdict, private_manifest_hash=None)
    with pytest.raises(ContractError):
        replace(verdict, regression_status="NOT_RUN")
    with pytest.raises(ContractError):
        replace(verdict, status="FAIL")
    unavailable = EvaluationResult(RID, "DEV-fixture", None, None, None, None, None,
                                   "NOT_EVALUABLE", "NOT_RUN", "NOT_RUN", "NOT_RUN",
                                   None, "NO_FROZEN_PATCH", None)
    assert load_record(EvaluationResult, dump_record(unavailable)) == unavailable
    with pytest.raises(ContractError):
        replace(unavailable, error_code=None)


def test_planned_slot_and_actual_run_remain_distinct():
    planned = [slot(), replace(slot(), slot_id="unrun")]
    attempt = RunAttempt("slot", RID, 1, None, None, True)
    validate_attempt_links(planned, [attempt], [result()])
    with pytest.raises(ContractError):
        load_record(ScheduledSlot, canonical_json_bytes({**json.loads(dump_record(slot())), "run_id": RID}))
    for attempts, verdicts in [
        ([], [result()]),
        ([replace(attempt, slot_id="unknown")], []),
        ([attempt], [replace(result(), run_id=RID2)]),
        ([attempt], [replace(result(), task_id="other")]),
        ([attempt, attempt], []),
        ([attempt], [result(), result()]),
    ]:
        with pytest.raises(ContractError):
            validate_attempt_links(planned, attempts, verdicts)


def test_attempt_replacement_must_match_parent_and_approval():
    first = RunAttempt("slot", RID, 1, None, None, False)
    replacement = RunAttempt("slot", RID2, 2, RID, "decision-1", True)
    planned = [slot(), replace(slot(), slot_id="other")]
    validate_attempt_links(planned, [replacement, first], [])
    for fields in [{"approval_ref": None}, {"slot_id": "other"}, {"attempt_index": 1},
                   {"replacement_of": RID2}]:
        with pytest.raises(ContractError):
            validate_attempt_links(planned, [first, replace(replacement, **fields)], [])
    with pytest.raises(ContractError):
        validate_attempt_links(planned, [replace(first, selected_for_analysis=True), replacement], [])
