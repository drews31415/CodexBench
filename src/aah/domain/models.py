"""Run, workspace and runtime records from Technical Specification sections 3–7."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from .wire import (
    ContractError, Count, Digest, Identifier, RelativePath, RunId, UTC, WireRecord, sha256_bytes,
)


class Phase(StrEnum):
    INIT = "INIT"
    RESTORE_VERIFY = "RESTORE_VERIFY"
    INITIAL_CONTEXT = "INITIAL_CONTEXT"
    RUN = "RUN"
    OBSERVE = "OBSERVE"
    DECIDE = "DECIDE"
    DIAGNOSE = "DIAGNOSE"
    SELECT_STRATEGY = "SELECT_STRATEGY"
    RECOVERY_CONTEXT = "RECOVERY_CONTEXT"
    FINALIZE = "FINALIZE"
    REPORT = "REPORT"


class Condition(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class ContextPolicy(StrEnum):
    NONE = "NONE"
    DEFAULT = "DEFAULT"
    DCC = "DCC"


class DiagnosisPolicy(StrEnum):
    NONE = "NONE"
    FLAT = "FLAT"
    GRAPH = "GRAPH"


class StrategyPolicy(StrEnum):
    NATIVE = "NATIVE"
    GENERIC = "GENERIC"
    TYPED = "TYPED"


class SessionMode(StrEnum):
    SAME_SESSION = "SAME_SESSION"
    NEW_SESSION = "NEW_SESSION"


class InterventionBoundary(StrEnum):
    TURN_END = "TURN_END"
    POST_COMPLETION = "POST_COMPLETION"
    UNSUPPORTED = "UNSUPPORTED"


class UsageMode(StrEnum):
    DELTA = "DELTA"
    CUMULATIVE = "CUMULATIVE"
    UNKNOWN = "UNKNOWN"


class CapabilityStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"


class UnitStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class StopReason(StrEnum):
    COMPLETED = "COMPLETED"
    GLOBAL_LIMIT = "GLOBAL_LIMIT"
    CANCELED = "CANCELED"
    SAFETY_VIOLATION = "SAFETY_VIOLATION"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    PREPARATION_ERROR = "PREPARATION_ERROR"
    NO_SAFE_CONTINUATION = "NO_SAFE_CONTINUATION"
    ARTIFACT_ERROR = "ARTIFACT_ERROR"


@dataclass(frozen=True)
class RepositoryRef(WireRecord):
    repo_id: Identifier
    source_path: Path
    initial_commit: Identifier


@dataclass(frozen=True)
class FixedRequirements(WireRecord):
    task: Identifier
    mandatory_text: str
    mandatory_hash: Digest
    permission_profile_id: Identifier
    approved_check_ids: tuple[Identifier, ...]

    def validate(self) -> None:
        if len(set(self.approved_check_ids)) != len(self.approved_check_ids):
            raise ContractError("duplicate approved check ID")
        if sha256_bytes(self.mandatory_text.encode("utf-8")) != self.mandatory_hash:
            raise ContractError("mandatory_hash does not match mandatory_text")


@dataclass(frozen=True)
class PolicySet(WireRecord):
    condition: Condition
    initial_context: ContextPolicy
    recovery_context: ContextPolicy
    diagnosis: DiagnosisPolicy
    strategy: StrategyPolicy
    external_intervention: bool


_CONDITIONS = {
    Condition.A: (ContextPolicy.NONE, DiagnosisPolicy.NONE, StrategyPolicy.NATIVE, False),
    Condition.B: (ContextPolicy.DEFAULT, DiagnosisPolicy.FLAT, StrategyPolicy.GENERIC, True),
    Condition.C: (ContextPolicy.DCC, DiagnosisPolicy.FLAT, StrategyPolicy.GENERIC, True),
    Condition.D: (ContextPolicy.DEFAULT, DiagnosisPolicy.GRAPH, StrategyPolicy.TYPED, True),
    Condition.E: (ContextPolicy.DCC, DiagnosisPolicy.GRAPH, StrategyPolicy.TYPED, True),
}


def resolve_condition(condition: Condition) -> PolicySet:
    try:
        context, diagnosis, strategy, intervention = _CONDITIONS[condition]
    except KeyError as exc:
        raise ContractError("unknown experimental condition") from exc
    return PolicySet(condition, context, context, diagnosis, strategy, intervention)


def validate_policy_set(policy: PolicySet, *, experiment_locked: bool) -> None:
    if experiment_locked and policy != resolve_condition(policy.condition):
        raise ContractError("policy overrides are forbidden in research conditions")


@dataclass(frozen=True)
class RunRequest(WireRecord):
    run_id: RunId
    task_id: Identifier | None
    experiment_id: Identifier | None
    repository: RepositoryRef
    fixed: FixedRequirements
    policies: PolicySet
    config_hash: Digest
    mode: Literal["user", "research"]

    def validate(self) -> None:
        validate_policy_set(self.policies, experiment_locked=self.mode == "research")


@dataclass(frozen=True)
class BudgetState(WireRecord):
    elapsed_ms: Count
    remaining_ms: Count
    turns_used: Count
    recovery_reserved: Count
    transitions_used: Count
    observed_model_tokens: Count | None
    token_enforcement: CapabilityStatus


@dataclass(frozen=True)
class RunState(WireRecord):
    run_id: RunId
    phase: Phase
    turn_index: Count
    transition_count: Count
    recovery_reserved: Count
    snapshot_id: Digest | None
    ledger_cursor: Count
    last_intervention_id: Identifier | None
    budget: BudgetState
    stop_reason: StopReason | None

    def validate(self) -> None:
        if (self.transition_count, self.recovery_reserved) != (
            self.budget.transitions_used, self.budget.recovery_reserved
        ):
            raise ContractError("run counters disagree with budget")


@dataclass(frozen=True)
class RuntimeCapabilities(WireRecord):
    adapter: Identifier
    runtime_version: Identifier
    model_id: Identifier
    unit_boundary: InterventionBoundary
    session_modes: tuple[SessionMode, ...]
    features: Mapping[Identifier, CapabilityStatus]
    usage_mode: UsageMode
    probe_artifact_ref: RelativePath


@dataclass(frozen=True)
class WorkspaceHandle(WireRecord):
    run_id: RunId
    root: Path
    initial_commit: Identifier
    isolation_profile_id: Identifier
    verified: bool


@dataclass(frozen=True)
class SnapshotRef(WireRecord):
    repo_id: Identifier
    snapshot_id: Digest
    base_commit: Identifier
    manifest_ref: RelativePath
    captured_at: UTC
    complete: bool


@dataclass(frozen=True)
class ErrorInfo(WireRecord):
    code: Identifier
    stage: Phase
    safe_message: str
    retryable: bool
    cause_ref: RelativePath | None


@dataclass(frozen=True)
class CleanupResult(WireRecord):
    confirmed: bool
    remaining_process_count: Count | None
    error: ErrorInfo | None

    def validate(self) -> None:
        if self.confirmed and self.remaining_process_count:
            raise ContractError("cleanup cannot be confirmed with remaining processes")


@dataclass(frozen=True)
class CancelResult(CleanupResult):
    pass


@dataclass(frozen=True)
class TurnRequest(WireRecord):
    run_id: RunId
    turn_id: Identifier
    workspace: WorkspaceHandle
    session_ref: Identifier | None
    kind: Literal["INITIAL", "NATIVE_CONTINUE", "RECOVERY"]
    message: str | None
    message_hash: Digest | None
    intervention_id: Identifier | None
    deadline_ms_remaining: Count
    runtime_config_ref: RelativePath

    def validate(self) -> None:
        if self.run_id != self.workspace.run_id:
            raise ContractError("workspace belongs to another run")
        expected = None if self.message is None else sha256_bytes(self.message.encode("utf-8"))
        if self.message_hash != expected:
            raise ContractError("message_hash does not match message")
        if (self.kind == "RECOVERY") != (self.intervention_id is not None):
            raise ContractError("recovery turns require a unique intervention ID")


@dataclass(frozen=True)
class TurnResult(WireRecord):
    turn_id: Identifier
    status: UnitStatus
    session_ref: Identifier | None
    completion_declared: bool | None
    completion_source_ref: Identifier | None
    completion_basis_id: Identifier | None
    native_continuation_supported: bool
    exit_code: int | None
    event_refs: tuple[Identifier, ...]
    usage_refs: tuple[Identifier, ...]
    error: ErrorInfo | None
    process_quiescent: bool

    def validate(self) -> None:
        if self.completion_declared and not (self.completion_source_ref and self.completion_basis_id):
            raise ContractError("a completion declaration requires explicit source and basis")


@dataclass(frozen=True)
class RawEvent(WireRecord):
    run_id: RunId
    turn_id: Identifier
    raw_id: Identifier
    received_seq: Count
    received_at: UTC
    provider_event_id: Identifier | None
    provider_type: Identifier | None
    payload_ref: RelativePath
    parse_status: Identifier


def _unique_paths(paths: tuple[str, ...]) -> None:
    if len({path.casefold() for path in paths}) != len(paths):
        raise ContractError("duplicate or case-colliding changed path")


@dataclass(frozen=True)
class DiffRecord(WireRecord):
    diff_id: Identifier
    before_snapshot_id: Digest
    after_snapshot_id: Digest
    changed_paths: tuple[RelativePath, ...]
    hunks: tuple[Mapping[str, Any], ...]
    patch_ref: RelativePath
    complete: bool

    def validate(self) -> None:
        _unique_paths(self.changed_paths)


@dataclass(frozen=True)
class FrozenPatch(WireRecord):
    run_id: RunId
    base_commit: Identifier
    snapshot_id: Digest
    patch_ref: RelativePath
    patch_hash: Digest
    changed_paths: tuple[RelativePath, ...]
    applicable: bool

    def validate(self) -> None:
        _unique_paths(self.changed_paths)
