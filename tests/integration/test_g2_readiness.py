import json
from dataclasses import replace

import pytest

from aah.domain import ContractError, canonical_json_bytes
from analysis import g2_readiness as g2


def test_reserved_candidate_registry_has_no_approved_task():
    review = g2.audit_registry(g2.REGISTRY)
    assert review["status_counts"] == {"DRAFT": 24}
    assert len(review["pending_candidate_ids"]) == 24
    assert not review["approved_candidate_ids"]


def receipt():
    digest = "8" * 64
    return g2.ReviewReceipt(
        "synthetic-id", "synthetic-family", digest, digest, digest, digest, digest,
        "fixture-review", ("author",), "reviewer", {key: "PASS" for key in g2.QUALITY_CHECKS}, None,
    )


@pytest.mark.parametrize(("change", "reason"), [
    ({"reviewer_id": " Author "}, "independent"),
    ({"checks": {"BQ14": "PASS"}}, "benchmark quality"),
    ({"checks": {key: "REVIEWED_EXCEPTION" for key in g2.QUALITY_CHECKS}}, "benchmark quality"),
])
def test_review_rejects_unverified_quality(change, reason):
    with pytest.raises(ContractError, match=reason):
        replace(receipt(), **change)


def test_candidate_review_requires_full_receipt_and_avoids_double_counting_family(tmp_path):
    with pytest.raises(ContractError, match="BQ review evidence"):
        g2.Candidate("AAH-S01", "OWN", "R-AUTH", "SINGLE", "APPROVED", None)
    with pytest.raises(ContractError, match="upstream instance"):
        g2.Candidate("PUB-S01", "PUBLIC", "reviewed-repo", "SINGLE", "APPROVED", receipt())
    first = g2.Candidate("AAH-S01", "OWN", "R-AUTH", "SINGLE", "APPROVED", receipt())
    path = tmp_path / "registry.json"
    path.write_bytes(canonical_json_bytes({"schema_version": "1.0", "candidates": [first]}))
    assert g2.audit_registry(path)["approved_by_type"]["SINGLE"] == 1
    second = replace(first, candidate_id="AAH-S02", review=replace(first.review, task_id="second"))
    path.write_bytes(canonical_json_bytes({"schema_version": "1.0", "candidates": [first, second]}))
    with pytest.raises(ContractError, match="task_family_id"):
        g2.audit_registry(path)


def test_resource_projections_preserve_balanced_schedule_and_use_observed_units():
    pilot = {"evaluated": 20, "total_model_tokens": 1000, "total_execution_and_evaluation_ms": 20000}
    full, core, reduced, emergency = g2.projections(pilot)
    assert [row["end_to_end_slots"] for row in (full, core, reduced, emergency)] == [240, 200, 120, 40]
    assert emergency["projected_model_tokens"] == 2000
    assert emergency["projected_execution_and_evaluation_ms"] == 40000
    assert json.loads(canonical_json_bytes(emergency))["design"] == "Emergency"
