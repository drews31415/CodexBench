from dataclasses import replace

import pytest

from aah.domain import CheckResult, CheckStatus, EvaluationResult, EvaluationStatus, load_record
from aah.workspace import WorkspaceManager
from benchmarks.smoke import driver
from benchmarks.smoke.public_checks import run_approved
from evaluation.runner import EvaluationRequest, evaluate, fixture_manifest_hash, write_result
from evaluation.smoke import fixture_request, verify


@pytest.mark.parametrize("task_id", ["DEV-01", "DEV-02"])
def test_synthetic_baseline_and_reference_round_trip(tmp_path, task_id):
    rows = verify(task_id, tmp_path)
    assert [row["verified"] for row in rows] == [True, True]
    assert [row["public"] for row in rows] == [["FAIL", "PASS"], ["PASS", "PASS"]]
    assert [row["evaluation"] for row in rows] == ["FAIL", "PASS"]
    assert rows[0]["patch_hash"] != rows[1]["patch_hash"]

    directory = tmp_path / task_id
    for row in rows:
        records = sorted((directory / "public-checks").glob(f"{row['run_id']}-*.json"))
        public = [load_record(CheckResult, file.read_bytes()) for file in records]
        assert len(public) == 2
        assert all(check.source_class == "TRUSTED_RUNNER" and check.test_cases for check in public)
        if row["variant"] == "baseline":
            assert public[0].test_cases[0].fingerprint
        file = directory / "evaluation" / f"{row['run_id']}.json"
        verdict = load_record(EvaluationResult, file.read_bytes())
        assert verdict.patch_hash == row["patch_hash"]
        original = file.read_bytes()
        with pytest.raises(FileExistsError):
            write_result(file.parent, verdict)
        assert file.read_bytes() == original
    with pytest.raises(FileExistsError):
        verify(task_id, tmp_path)


def test_incomplete_inputs_never_receive_verdict(tmp_path):
    req = fixture_request("DEV-01")
    commit = req.repository.initial_commit
    envelope = EvaluationRequest(req.run_id, req.task_id, commit, fixture_manifest_hash(req.task_id, commit), None)
    missing = evaluate(envelope, artifact_root=tmp_path)
    assert (missing.status, missing.error_code) == (EvaluationStatus.NOT_EVALUABLE, "NO_FROZEN_PATCH")
    assert missing.patch_hash is None and missing.functional_status == CheckStatus.NOT_RUN
    unknown = evaluate(replace(envelope, task_id="../unknown"), artifact_root=tmp_path)
    assert unknown.status == EvaluationStatus.NOT_RUN
    assert unknown.error_code == "UNKNOWN_SYNTHETIC_TASK"


def test_verified_extra_path_enforces_published_constraint(tmp_path):
    req = fixture_request("DEV-02")
    artifacts = tmp_path / "artifacts"
    manager = WorkspaceManager(req.repository.source_path, tmp_path / "work", artifacts,
                               quiescence_check=lambda _: True)
    handle = manager.prepare(req)
    try:
        (handle.root / "extra.txt").write_text("public synthetic addition", encoding="utf-8")
        patch = manager.freeze_patch(handle, manager.snapshot(handle))
    finally:
        manager.cleanup(handle)
    envelope = EvaluationRequest(req.run_id, req.task_id, patch.base_commit,
                                 fixture_manifest_hash(req.task_id, patch.base_commit), patch)
    verdict = evaluate(envelope, artifact_root=artifacts)
    assert verdict.status == EvaluationStatus.FAIL
    assert verdict.instruction_status == CheckStatus.FAIL
    assert verdict.functional_status == verdict.regression_status == CheckStatus.NOT_RUN
    assert verdict.patch_hash == patch.patch_hash
    wrong = evaluate(replace(envelope, manifest_hash="0" * 64), artifact_root=artifacts)
    assert wrong.error_code == "MANIFEST_MISMATCH" and wrong.status == EvaluationStatus.NOT_RUN
    (artifacts / patch.patch_ref).write_bytes(b"corrupt synthetic artifact")
    damaged = evaluate(envelope, artifact_root=artifacts)
    assert damaged.status == EvaluationStatus.INFRA_ERROR and damaged.patch_hash is None


def test_public_check_failure_to_verify_has_no_inferred_cases(tmp_path, monkeypatch):
    req = fixture_request("DEV-01")
    artifacts = tmp_path / "artifacts"
    manager = WorkspaceManager(req.repository.source_path, tmp_path / "work", artifacts,
                               quiescence_check=lambda _: True)
    handle = manager.prepare(req)
    try:
        snapshot = manager.snapshot(handle)
        with pytest.raises(ValueError):
            run_approved(snapshot, "not-approved", artifacts)
        expired = run_approved(snapshot, req.fixed.approved_check_ids[0], artifacts, deadline=0)
        assert expired.status == CheckStatus.NOT_RUN and expired.warnings == ("DEADLINE_EXHAUSTED",)

        def damaged_bundle(*_):
            raise driver.CheckEnvironmentError("synthetic bundle mismatch")

        monkeypatch.setattr(driver, "load_bundle", damaged_bundle)
        public = run_approved(snapshot, req.fixed.approved_check_ids[0], artifacts)
    finally:
        manager.cleanup(handle)
    assert public.status == CheckStatus.INFRA_ERROR
    assert public.source_class == "UNVERIFIED_OBSERVATION" and not public.test_cases
