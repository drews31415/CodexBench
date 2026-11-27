import json
from dataclasses import replace
from uuid import uuid4

import pytest

from aah.domain import ContractError, EvaluationResult, load_record
from benchmarks.main_public import qualify
from evaluation.runner import EvaluationRequest


@pytest.mark.parametrize("task_id", qualify.TASK_IDS)
def test_main_candidate_stable_public_baseline_and_exportable_empty_patch(task_id, tmp_path):
    handoff = qualify.verify_candidate(task_id, tmp_path)
    repeat1, repeat2 = handoff["public_validation"]
    assert repeat1["snapshot_id"] == repeat2["snapshot_id"]
    assert repeat1["checks"] == repeat2["checks"]
    assert repeat1["checks"]["P-FUNC"]["failures"] >= 1
    assert repeat1["checks"]["P-REG"]["failures"] == 0
    assert handoff["independent_review"] == "PENDING"
    request = qualify.baseline_evaluation_request(tmp_path / task_id, "f" * 64)
    assert request.patch.changed_paths == () and request.task_id == task_id
    assert request.patch.snapshot_id == handoff["snapshot_hash"]


def test_curator_handoff_cli_and_verdict_binding(tmp_path, monkeypatch):
    task_id = "AAH-D02"
    qualify.verify_candidate(task_id, tmp_path)
    output = tmp_path / "request.json"
    monkeypatch.setattr("sys.argv", ["qualify", "--handoff-dir", str(tmp_path / task_id),
                                    "--output", str(output), "--evaluator-manifest-hash", "a" * 64])
    assert qualify.main() == 0
    request = load_record(EvaluationRequest, output.read_bytes())
    environment, bundle = "b" * 64, "c" * 64
    baseline = EvaluationResult(request.run_id, request.task_id, request.base_commit, request.patch.patch_hash,
                                request.manifest_hash, bundle, environment, "FAIL", "FAIL", "PASS", "PASS", 0, None, None)
    reference = replace(baseline, run_id=str(uuid4()), patch_hash="d" * 64, status="PASS", functional_status="PASS")
    qualify.verify_independent_pair(baseline, reference, request, environment_hash=environment, test_bundle_hash=bundle)
    with pytest.raises(ContractError, match="approved task"):
        qualify.verify_independent_pair(baseline, replace(reference, test_bundle_hash="e" * 64), request,
                                        environment_hash=environment, test_bundle_hash=bundle)
    with pytest.raises(ContractError, match="demonstrate"):
        qualify.verify_independent_pair(baseline, replace(reference, patch_hash=baseline.patch_hash), request,
                                        environment_hash=environment, test_bundle_hash=bundle)
    metadata = tmp_path / task_id / "handoff.json"
    handoff = json.loads(metadata.read_bytes())
    handoff["public_spec_hash"] = "1" * 64
    metadata.write_text(json.dumps(handoff), encoding="utf-8")
    with pytest.raises(ContractError, match="do not agree"):
        qualify.baseline_evaluation_request(tmp_path / task_id, request.manifest_hash)
