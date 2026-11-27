"""Codex CLI translation over an operator-provisioned execution boundary."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from aah.domain import (
    CancelResult, CapabilityStatus, CleanupResult, ErrorInfo, InterventionBoundary,
    Phase, RawEvent, RunRequest, RuntimeCapabilities, SessionMode, TurnRequest,
    TurnResult, UnitStatus, WorkspaceHandle, validate_relative_path,
)

from .probe import ProbeReport, RuntimeConfig, probe_codex


class RuntimeAdapter(Protocol):
    """A provisioned runtime implements the same boundary as a synthetic test adapter."""

    def probe_capabilities(self, config: RuntimeConfig) -> RuntimeCapabilities: ...
    def open_run(self, request: RunRequest, workspace: WorkspaceHandle) -> str | None: ...
    def run_unit(self, request: TurnRequest, on_event: Callable[[RawEvent], None]) -> TurnResult: ...
    def continue_unit(self, request: TurnRequest, on_event: Callable[[RawEvent], None]) -> TurnResult: ...
    def cancel(self, run_id: str) -> CancelResult: ...
    def close_run(self, run_id: str) -> CleanupResult: ...


def _failure(code: str, stage: Phase) -> ErrorInfo:
    return ErrorInfo(
        code=code, stage=stage, safe_message=code.replace("_", " ").capitalize(),
        retryable=False, cause_ref=None,
    )


class RuntimeNotVerified(RuntimeError):
    def __init__(self, error: ErrorInfo, report: ProbeReport | None = None) -> None:
        super().__init__(error.safe_message)
        self.error = error
        self.report = report


@dataclass(frozen=True)
class DevSmokeAuthorization:
    """Host-issued, single-use authority for an exact initial input and safety profile."""

    request: RunRequest
    workspace: WorkspaceHandle
    pinned_config: RuntimeConfig
    initial_message_hash: str
    expires_at: datetime
    approval_ref: str
    process_safety_ref: str
    workspace_safety_ref: str
    evaluator_isolation_ref: str

    def __post_init__(self) -> None:
        if self.expires_at.utcoffset() is None or not re.fullmatch(r"[0-9a-f]{64}", self.initial_message_hash):
            raise ValueError("Dev Smoke authorization requires a zoned expiry and an exact prompt hash")
        for ref in (self.approval_ref, self.process_safety_ref, self.workspace_safety_ref, self.evaluator_isolation_ref):
            validate_relative_path(ref)


@dataclass(frozen=True)
class ProvisionedCLI:
    """The host identifies its pinned installation. RT01 records actual observed identity."""

    capabilities: RuntimeCapabilities
    exec_options: frozenset[str]
    resume_options: frozenset[str]
    dev_smoke: DevSmokeAuthorization | None = None


@dataclass(frozen=True)
class ExecutionReceipt:
    """The transport owns provider-specific event parsing and restricted raw storage."""

    status: UnitStatus
    exit_code: int | None
    process_quiescent: bool
    session_ref: str | None = None
    error: ErrorInfo | None = None
    usage_refs: tuple[str, ...] = ()


class CodexTransport(Protocol):
    """Trusted out-of-process runner, injected in code and never loaded from run configuration.

    inspect() attests the exact runtime and supported options on the execution host.
    bind() must authorize the requester, approved Dev profile, config and budget,
    pin the executable, and bind cwd, fixed instructions and OS protections to the
    returned opaque handle. Binding starts no model or child process and must roll
    back any allocation on failure. execute() runs only in that binding, enforces the deadline
    and cleanup reserve, and persists raw events before calling the callback.
    cancel/close must revoke the binding atomically with respect to dispatch,
    reject further execution, and verify quiescence before confirming cleanup.
    """

    def inspect(self, config: RuntimeConfig) -> ProvisionedCLI: ...
    def bind(self, request: RunRequest, workspace: WorkspaceHandle, config: RuntimeConfig) -> str: ...
    def redeem_dev_smoke(self, authorization: DevSmokeAuthorization) -> str:
        """Verify the evidence and redeem once, atomically across clients and restarts.

        The bound host must enforce approved identity, task, prompt hash, expiry,
        budget and at most one initial execution. A failed reservation is not refunded.
        """
        ...
    def execute(
        self, binding: str, turn_id: str, args: tuple[str, ...], stdin: bytes,
        deadline_ms_remaining: int, on_event: Callable[[RawEvent], None],
    ) -> ExecutionReceipt: ...
    def cancel(self, binding: str) -> CancelResult: ...
    def close(self, binding: str) -> CleanupResult: ...


@dataclass
class _BoundRun:
    workspace: WorkspaceHandle
    cli: ProvisionedCLI
    binding: str
    external_intervention: bool
    turns: set[str] = field(default_factory=set)
    interventions: set[str] = field(default_factory=set)
    session_ref: str | None = None
    active: bool = False
    stopped: bool = False
    quiescent: bool = True
    restored_branch: bool = False


def _blocked_turn(turn_id: str, code: str, quiescent: bool) -> TurnResult:
    return TurnResult(
        turn_id=turn_id, status=UnitStatus.FAILED, session_ref=None,
        completion_declared=None, completion_source_ref=None, completion_basis_id=None,
        native_continuation_supported=False, exit_code=None, event_refs=(),
        usage_refs=(), error=_failure(code, Phase.RUN), process_quiescent=quiescent,
    )


class CodexCliAdapter:
    """No model call is possible without a separately supplied trusted transport."""

    def __init__(
        self, config: RuntimeConfig | None = None, *, transport: CodexTransport | None = None,
    ) -> None:
        self._config = config or RuntimeConfig()
        self._transport = transport
        self._runs: dict[str, _BoundRun] = {}
        self._used_runs: set[str] = set()
        self._lock = RLock()

    def _inspect(self, config: RuntimeConfig, *, for_run: bool = False) -> ProvisionedCLI:
        if self._transport is None:
            raise RuntimeNotVerified(_failure("UNVERIFIED_RUNTIME", Phase.INIT), probe_codex(config))
        try:
            cli = self._transport.inspect(config)
        except OSError:
            raise RuntimeNotVerified(_failure("RUNTIME_INSPECTION_FAILED", Phase.INIT)) from None
        caps = cli.capabilities
        required = ("RT01", "RT02", "RT04", "RT05", "RT06")
        statuses = {caps.features.get(key) for key in required}
        full_profile = statuses == {CapabilityStatus.VERIFIED}
        smoke_candidate = (
            for_run and cli.dev_smoke is not None
            and statuses <= {CapabilityStatus.VERIFIED, CapabilityStatus.UNVERIFIED}
        )
        if (
            caps.adapter != config.adapter or caps.model_id != config.model_id
            or caps.runtime_version != config.runtime_version
            or not {"--json", "--model"} <= cli.exec_options
            or (config.reasoning_effort is not None and "--config" not in cli.exec_options)
            or not (full_profile or smoke_candidate)
        ):
            raise RuntimeNotVerified(_failure("RUNTIME_PROFILE_UNVERIFIED", Phase.INIT))
        return cli

    def probe_capabilities(self, config: RuntimeConfig) -> RuntimeCapabilities:
        return self._inspect(config).capabilities

    def open_run(self, request: RunRequest, workspace: WorkspaceHandle) -> str | None:
        if self._transport is None:
            raise RuntimeNotVerified(_failure("UNVERIFIED_RUNTIME", Phase.INIT))
        if request.run_id != workspace.run_id or not workspace.verified:
            raise RuntimeNotVerified(_failure("WORKSPACE_UNVERIFIED", Phase.INIT))
        with self._lock:
            if request.run_id in self._used_runs:
                raise RuntimeNotVerified(_failure("RUN_ALREADY_RESERVED", Phase.INIT))
            cli = self._inspect(self._config, for_run=True)
            permit = cli.dev_smoke
            if permit is not None and (
                permit.request != request or permit.workspace != workspace or permit.pinned_config != self._config
                or request.experiment_id is not None or request.task_id is None
                or self._config.reasoning_effort is None or permit.expires_at <= datetime.now(timezone.utc)
            ):
                raise RuntimeNotVerified(_failure("DEV_SMOKE_SCOPE_MISMATCH", Phase.INIT))
            self._used_runs.add(request.run_id)
            try:
                if permit is None:
                    binding = self._transport.bind(request, workspace, self._config)
                else:
                    binding = self._transport.redeem_dev_smoke(permit)
            except OSError:
                raise RuntimeNotVerified(_failure("RUNTIME_BINDING_FAILED", Phase.INIT)) from None
            self._runs[request.run_id] = _BoundRun(workspace, cli, binding, request.policies.external_intervention)
        return None  # The first observed provider session ID arrives with the turn receipt.

    def run_unit(self, request: TurnRequest, on_event: Callable[[RawEvent], None]) -> TurnResult:
        return self._execute(request, on_event, followup=False)

    def open_checkpoint_run(self, request, workspace):
        if (request.mode != "research" or not request.experiment_id or not request.policies.external_intervention
                or self._config.session_mode != SessionMode.NEW_SESSION):
            raise RuntimeNotVerified(_failure("BRANCH_SESSION_UNVERIFIED", Phase.RESTORE_VERIFY))
        self.open_run(request, workspace)
        with self._lock:
            self._runs[request.run_id].restored_branch = True

    def continue_unit(self, request: TurnRequest, on_event: Callable[[RawEvent], None]) -> TurnResult:
        return self._execute(request, on_event, followup=True)

    def _args(self, run: _BoundRun, turn: TurnRequest, followup: bool) -> tuple[str, ...]:
        caps = run.cli.capabilities
        args = ["exec"]
        positional: list[str] = []
        flags = run.cli.exec_options
        if followup:
            if (
                run.cli.dev_smoke is not None or not run.external_intervention or turn.kind != "RECOVERY"
                or not (run.turns or run.restored_branch)
                or caps.features.get("RT03") != CapabilityStatus.VERIFIED
                or caps.unit_boundary == InterventionBoundary.UNSUPPORTED
                or self._config.session_mode not in caps.session_modes
            ):
                raise RuntimeNotVerified(_failure("FOLLOWUP_UNVERIFIED", Phase.RUN))
            if self._config.session_mode == SessionMode.SAME_SESSION:
                if not run.session_ref or turn.session_ref != run.session_ref:
                    raise RuntimeNotVerified(_failure("SESSION_MISMATCH", Phase.RUN))
                args.append("resume")
                positional.append(run.session_ref)
                flags = run.cli.resume_options
            elif turn.session_ref is not None:
                raise RuntimeNotVerified(_failure("NEW_SESSION_REQUIRES_EMPTY_REFERENCE", Phase.RUN))
        elif turn.kind != "INITIAL" or run.turns or turn.session_ref is not None:
            raise RuntimeNotVerified(_failure("INITIAL_TURN_MISMATCH", Phase.RUN))
        if not {"--json", "--model"} <= flags:
            raise RuntimeNotVerified(_failure("CLI_FLAGS_UNVERIFIED", Phase.RUN))
        args.extend(("--json", "--model", caps.model_id))
        if self._config.reasoning_effort is not None:
            if "--config" not in flags:
                raise RuntimeNotVerified(_failure("CLI_FLAGS_UNVERIFIED", Phase.RUN))
            args.extend(("--config", "model_reasoning_effort=" + json.dumps(self._config.reasoning_effort)))
        return (*args, "--", *positional, "-")

    def _execute(
        self, turn: TurnRequest, on_event: Callable[[RawEvent], None], *, followup: bool,
    ) -> TurnResult:
        transport = self._transport
        with self._lock:
            run = self._runs.get(turn.run_id)
            if transport is None or run is None:
                return _blocked_turn(turn.turn_id, "UNVERIFIED_RUNTIME", True)
            if (
                run.workspace != turn.workspace or run.active or run.stopped
                or turn.turn_id in run.turns or turn.intervention_id in run.interventions
            ):
                return _blocked_turn(turn.turn_id, "TURN_UNAVAILABLE", run.quiescent)
            if turn.deadline_ms_remaining <= 0 or not turn.message:
                return _blocked_turn(turn.turn_id, "TURN_INPUT_UNAVAILABLE", run.quiescent)
            remaining = turn.deadline_ms_remaining
            if (permit := run.cli.dev_smoke) is not None:
                lifetime_ms = int((permit.expires_at - datetime.now(timezone.utc)).total_seconds() * 1000)
                if lifetime_ms <= 0 or turn.message_hash != permit.initial_message_hash:
                    return _blocked_turn(turn.turn_id, "DEV_SMOKE_SCOPE_MISMATCH", run.quiescent)
                remaining = min(remaining, lifetime_ms)
            try:
                args = self._args(run, turn, followup)
            except RuntimeNotVerified as exc:
                return _blocked_turn(turn.turn_id, exc.error.code, run.quiescent)
            run.turns.add(turn.turn_id)
            if turn.intervention_id is not None:
                run.interventions.add(turn.intervention_id)
            run.active = True
            run.quiescent = False

        refs: list[str] = []

        def receive(event: RawEvent) -> None:
            if event.run_id != turn.run_id or event.turn_id != turn.turn_id:
                raise ValueError("unattributed runtime event")
            refs.append(event.raw_id)
            on_event(event)

        result = None
        try:
            receipt = transport.execute(
                run.binding, turn.turn_id, args, turn.message.encode("utf-8"),
                remaining, receive,
            )
            result = TurnResult(
                turn_id=turn.turn_id, status=receipt.status, session_ref=receipt.session_ref,
                completion_declared=None, completion_source_ref=None, completion_basis_id=None,
                native_continuation_supported=False, exit_code=receipt.exit_code,
                event_refs=tuple(refs), usage_refs=receipt.usage_refs, error=receipt.error,
                process_quiescent=receipt.process_quiescent,
            )
        except Exception:
            # The provider may already have received the message. Seal this run,
            # retain partial raw references and leave cleanup to the transport.
            result = TurnResult(
                turn_id=turn.turn_id, status=UnitStatus.UNKNOWN, session_ref=None,
                completion_declared=None, completion_source_ref=None, completion_basis_id=None,
                native_continuation_supported=False, exit_code=None, event_refs=tuple(refs),
                usage_refs=(), error=_failure("DELIVERY_UNKNOWN", Phase.RUN), process_quiescent=False,
            )
        finally:
            with self._lock:
                run.active = False
                if result is None or not result.process_quiescent:
                    run.stopped = True
                else:
                    run.quiescent = True
                    run.session_ref = result.session_ref
        return result

    def cancel(self, run_id: str) -> CancelResult:
        with self._lock:
            run = self._runs.get(run_id)
            if self._transport is None or run is None:
                return CancelResult(False, None, _failure("UNVERIFIED_RUNTIME", Phase.FINALIZE))
            run.stopped = True
            binding = run.binding
        try:
            return self._transport.cancel(binding)
        except OSError:
            return CancelResult(False, None, _failure("CANCELLATION_UNCONFIRMED", Phase.FINALIZE))

    def close_run(self, run_id: str) -> CleanupResult:
        with self._lock:
            run = self._runs.get(run_id)
            if self._transport is None or run is None:
                return CleanupResult(False, None, _failure("UNVERIFIED_RUNTIME", Phase.FINALIZE))
            run.stopped = True
            binding = run.binding
        try:
            result = self._transport.close(binding)
        except OSError:
            return CleanupResult(False, None, _failure("CLEANUP_UNCONFIRMED", Phase.FINALIZE))
        if result.confirmed:
            with self._lock:
                self._runs.pop(run_id, None)
        return result
