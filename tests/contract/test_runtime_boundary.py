from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from aah.domain import (
    CancelResult, CapabilityStatus, CleanupResult, Condition, FixedRequirements,
    InterventionBoundary, RawEvent, RepositoryRef, RunRequest, RuntimeCapabilities,
    SessionMode, TurnRequest, UnitStatus, UsageMode, WorkspaceHandle, resolve_condition, sha256_bytes,
)
from aah.runtime import (
    CodexCliAdapter, DevSmokeAuthorization, ExecutionReceipt, ProvisionedCLI, RuntimeConfig, RuntimeNotVerified,
)
from aah.runtime import probe as metadata


def test_local_adapter_cannot_launch_or_resume_even_with_verified_workspace(tmp_path):
    adapter = CodexCliAdapter()
    run_id = str(uuid4())
    workspace = WorkspaceHandle(run_id, tmp_path, "test-commit", "fixture", verified=True)
    turn = TurnRequest(
        run_id=run_id, turn_id="turn-01", workspace=workspace, session_ref=None,
        kind="INITIAL", message=None, message_hash=None, intervention_id=None,
        deadline_ms_remaining=1000, runtime_config_ref="configs/dev.json",
    )
    with pytest.raises(RuntimeNotVerified) as blocked:
        adapter.open_run(None, workspace)
    assert blocked.value.error.code == "UNVERIFIED_RUNTIME"
    for attempt in (adapter.run_unit, adapter.continue_unit):
        outcome = attempt(turn, lambda event: pytest.fail("unexpected runtime event"))
        assert outcome.status == UnitStatus.FAILED and outcome.process_quiescent
        assert outcome.completion_declared is outcome.exit_code is None
        assert outcome.usage_refs == outcome.event_refs == ()
        assert not outcome.native_continuation_supported
        assert outcome.error.code == "UNVERIFIED_RUNTIME"
    for action in (adapter.cancel, adapter.close_run):
        result = action(run_id)
        assert not result.confirmed and result.remaining_process_count is None


def test_incomplete_probe_preserves_partial_report_without_returning_capabilities(monkeypatch):
    monkeypatch.setattr(metadata, "_find_codex", lambda: None)
    with pytest.raises(RuntimeNotVerified) as blocked:
        CodexCliAdapter().probe_capabilities(RuntimeConfig(model_id="requested-only"))
    assert blocked.value.report.reason == "NATIVE_CLI_NOT_FOUND"
    assert blocked.value.report.to_dict()["model_id"] is None


class FixtureTransport:
    """Synthetic receipts for translation tests, with no process or provider."""

    def __init__(self, request):
        self.request = request
        self.features = {f"RT{i:02d}": CapabilityStatus.VERIFIED for i in range(1, 7)}
        self.calls = []
        self.bad_event = False
        self.dev_smoke = None
        self.redeemed = False

    def inspect(self, config):
        caps = RuntimeCapabilities(
            "codex_cli", "1.2.3", "fixture", InterventionBoundary.POST_COMPLETION,
            (SessionMode.SAME_SESSION, SessionMode.NEW_SESSION), self.features, UsageMode.UNKNOWN,
            "synthetic/capabilities.json",
        )
        flags = frozenset({"--model", "--json", "--config"})
        return ProvisionedCLI(caps, flags, flags, self.dev_smoke)

    def bind(self, request, workspace, config):
        assert request == self.request and request.run_id == workspace.run_id
        return "test-binding"

    def redeem_dev_smoke(self, authorization):
        assert authorization == self.dev_smoke
        if self.redeemed:
            raise PermissionError("single-use fixture approval already consumed")
        self.redeemed = True
        return "test-binding"

    def execute(self, binding, turn_id, args, stdin, deadline_ms_remaining, on_event):
        assert binding == "test-binding" and deadline_ms_remaining == 1000
        self.calls.append((args, stdin))
        event = RawEvent(
            self.request.run_id, turn_id, f"raw-{turn_id}", 1, "2026-11-25T00:00:00Z", None,
            "fixture.unknown", "restricted/event.json", "UNKNOWN",
        )
        on_event(event)
        if self.bad_event:
            on_event(replace(event, turn_id="other-turn"))
        return ExecutionReceipt(UnitStatus.COMPLETED, 0, True, "observed-session")

    def cancel(self, binding):
        return CancelResult(True, 0, None)

    def close(self, binding):
        return CleanupResult(True, 0, None)


@pytest.fixture
def synthetic(tmp_path):
    run_id = str(uuid4())
    workspace = WorkspaceHandle(run_id, tmp_path, "base", "fixture", True)
    request = RunRequest(
        run_id, "task", None, RepositoryRef("repo", tmp_path, "base"),
        FixedRequirements("task", "", sha256_bytes(b""), "fixture", ()),
        resolve_condition(Condition.E), "a" * 64, "research",
    )
    prompt = "--help\n한글\n"
    turn = TurnRequest(
        run_id, "initial", workspace, None, "INITIAL", prompt, sha256_bytes(prompt.encode()),
        None, 1000, "configs/fixture.json",
    )
    return request, turn, FixtureTransport(request)


@pytest.mark.parametrize("mode", [SessionMode.NEW_SESSION, SessionMode.SAME_SESSION])
def test_transport_delivers_attributed_initial_and_recovery(synthetic, mode):
    request, initial, transport = synthetic
    adapter = CodexCliAdapter(RuntimeConfig("fixture", "high", "1.2.3", session_mode=mode), transport=transport)
    assert adapter.open_run(request, initial.workspace) is None
    observed = []
    first = adapter.run_unit(initial, observed.append)
    assert first.event_refs == ("raw-initial",) and first.event_refs[0] == observed[0].raw_id
    assert first.completion_declared is None and first.usage_refs == ()
    flags = ("--json", "--model", "fixture", "--config", 'model_reasoning_effort="high"')
    assert transport.calls == [(("exec", *flags, "--", "-"), initial.message.encode())]
    resumed = mode == SessionMode.SAME_SESSION
    recovery = replace(
        initial, turn_id="recovery", kind="RECOVERY", intervention_id="intervention",
        session_ref=first.session_ref if resumed else None,
    )
    if resumed:
        wrong = adapter.continue_unit(replace(recovery, session_ref="other-session"), observed.append)
        assert wrong.error.code == "SESSION_MISMATCH" and len(transport.calls) == 1
    second = adapter.continue_unit(recovery, observed.append)
    assert second.event_refs == ("raw-recovery",) and second.completion_declared is None
    expected = ("exec", "resume", *flags, "--", first.session_ref, "-") if resumed else ("exec", *flags, "--", "-")
    assert transport.calls[1] == (expected, initial.message.encode())
    assert adapter.continue_unit(replace(recovery, turn_id="reissued"), observed.append).error.code == "TURN_UNAVAILABLE"
    assert len(transport.calls) == 2
    assert adapter.close_run(request.run_id).confirmed
    with pytest.raises(RuntimeNotVerified):
        adapter.open_run(request, initial.workspace)


def test_misattributed_event_seals_run_and_keeps_partial_refs(synthetic):
    request, turn, transport = synthetic
    transport.bad_event = True
    config = RuntimeConfig(model_id="fixture", runtime_version="1.2.3")
    adapter = CodexCliAdapter(config, transport=transport)
    adapter.open_run(request, turn.workspace)
    observed = []
    outcome = adapter.run_unit(turn, observed.append)
    assert outcome.error.code == "DELIVERY_UNKNOWN" and not outcome.process_quiescent
    assert outcome.event_refs == ("raw-initial",) and len(observed) == 1
    assert adapter.run_unit(turn, observed.append).error.code == "TURN_UNAVAILABLE"
    assert len(transport.calls) == 1 and adapter.cancel(request.run_id).confirmed


def test_missing_execution_boundary_proof_blocks_before_bind(synthetic):
    request, turn, transport = synthetic
    transport.features["RT06"] = CapabilityStatus.UNVERIFIED
    config = RuntimeConfig(model_id="fixture", runtime_version="1.2.3")
    adapter = CodexCliAdapter(config, transport=transport)
    with pytest.raises(RuntimeNotVerified) as error:
        adapter.open_run(request, turn.workspace)
    assert error.value.error.code == "RUNTIME_PROFILE_UNVERIFIED" and not transport.calls


def test_condition_a_does_not_dispatch_external_recovery(synthetic):
    request, turn, transport = synthetic
    request = replace(request, policies=resolve_condition(Condition.A))
    transport.request = request
    config = RuntimeConfig(model_id="fixture", runtime_version="1.2.3", session_mode=SessionMode.NEW_SESSION)
    adapter = CodexCliAdapter(config, transport=transport)
    adapter.open_run(request, turn.workspace)
    adapter.run_unit(turn, lambda event: None)
    recovery = replace(turn, turn_id="retry", kind="RECOVERY", intervention_id="intervention")
    outcome = adapter.continue_unit(recovery, lambda event: None)
    assert outcome.error.code == "FOLLOWUP_UNVERIFIED" and len(transport.calls) == 1


@pytest.fixture
def approved_smoke(synthetic):
    request, turn, transport = synthetic
    config = RuntimeConfig("fixture", "high", "1.2.3", session_mode=SessionMode.NEW_SESSION)
    transport.features = {f"RT{i:02d}": CapabilityStatus.UNVERIFIED for i in range(1, 8)}
    transport.dev_smoke = DevSmokeAuthorization(
        request, turn.workspace, config, turn.message_hash, datetime.now(timezone.utc) + timedelta(minutes=1),
        "dev/approval", "dev/process-check", "dev/workspace-check", "dev/evaluator-check",
    )
    return request, turn, transport, config


def test_dev_smoke_requires_redeemed_approval_and_runs_only_one_exact_input(approved_smoke):
    request, turn, transport, config = approved_smoke
    adapter = CodexCliAdapter(config, transport=transport)
    with pytest.raises(RuntimeNotVerified):
        adapter.probe_capabilities(config)
    adapter.open_run(request, turn.workspace)
    changed = replace(turn, message="different", message_hash=sha256_bytes(b"different"))
    assert adapter.run_unit(changed, lambda event: None).error.code == "DEV_SMOKE_SCOPE_MISMATCH"
    outcome = adapter.run_unit(turn, lambda event: None)
    assert outcome.status == UnitStatus.COMPLETED and transport.redeemed
    assert adapter.run_unit(replace(turn, turn_id="second"), lambda event: None).error is not None
    recovery = replace(turn, kind="RECOVERY", turn_id="retry", intervention_id="retry")
    assert adapter.continue_unit(recovery, lambda event: None).error is not None
    assert len(transport.calls) == 1
    assert set(transport.features.values()) == {CapabilityStatus.UNVERIFIED}
    with pytest.raises(RuntimeNotVerified):
        CodexCliAdapter(config, transport=transport).open_run(request, turn.workspace)


@pytest.mark.parametrize("reason", ["none", "expired", "experiment", "unsupported", "other-task"])
def test_dev_smoke_refuses_invalid_or_absent_authority(approved_smoke, reason):
    request, turn, transport, config = approved_smoke
    if reason == "none":
        transport.dev_smoke = None
    if reason == "expired":
        transport.dev_smoke = replace(transport.dev_smoke, expires_at=datetime.now(timezone.utc) - timedelta(seconds=2))
    if reason == "experiment":
        request = replace(request, experiment_id="experiment")
        transport.dev_smoke = replace(transport.dev_smoke, request=request)
    if reason == "unsupported":
        transport.features["RT05"] = CapabilityStatus.UNSUPPORTED
    if reason == "other-task":
        request = replace(request, task_id="not-approved")
    with pytest.raises(RuntimeNotVerified):
        CodexCliAdapter(config, transport=transport).open_run(request, turn.workspace)
    assert not transport.calls and not transport.redeemed
