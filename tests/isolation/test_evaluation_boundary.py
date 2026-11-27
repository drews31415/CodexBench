from aah.domain import CheckStatus, EvaluationStatus
from aah.workspace import WorkspaceManager
from benchmarks.smoke import driver
from benchmarks.smoke.public_checks import run_approved
from evaluation.runner import EvaluationRequest, evaluate, fixture_manifest_hash
from evaluation.smoke import fixture_request


def test_unreviewed_source_is_denied_before_process_launch(tmp_path, monkeypatch):
    """실제 OS 격리의 증거가 없는 경우 실행을 보류하는 계약."""
    request = fixture_request("DEV-01")
    artifacts = tmp_path / "artifacts"
    manager = WorkspaceManager(request.repository.source_path, tmp_path / "work", artifacts,
                               quiescence_check=lambda _: True)
    handle = manager.prepare(request)
    try:
        alternate = b'def format_label(text):\n    return text.strip()\n'
        (handle.root / "module.py").write_bytes(alternate)
        snapshot = manager.snapshot(handle)
        patch = manager.freeze_patch(handle, snapshot)
    finally:
        manager.cleanup(handle)

    def reject_launch(*args, **kwargs):
        raise AssertionError("unexpected candidate execution")

    # platform.platform() may use subprocesses to identify the system on first call.
    driver.environment_hash()
    monkeypatch.setattr(driver.subprocess, "run", reject_launch)
    check = run_approved(snapshot, request.fixed.approved_check_ids[0], artifacts)
    envelope = EvaluationRequest(request.run_id, request.task_id, patch.base_commit,
                                 fixture_manifest_hash(request.task_id, patch.base_commit), patch)
    verdict = evaluate(envelope, artifact_root=artifacts)
    assert check.status == CheckStatus.NOT_RUN and check.test_cases == ()
    assert check.warnings == ("SUBMISSION_BOUNDARY_UNVERIFIED",)
    assert verdict.status == EvaluationStatus.NOT_RUN and verdict.patch_hash == patch.patch_hash
    assert verdict.error_code == "SUBMISSION_BOUNDARY_UNVERIFIED"
    assert verdict.functional_status == verdict.regression_status == verdict.instruction_status == CheckStatus.NOT_RUN
