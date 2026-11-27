"""A pinned controller-side reference and the public state available to every branch."""

from dataclasses import dataclass
from typing import Literal

from .evidence import CheckResult, EvidenceRecord
from .models import FrozenPatch, SnapshotRef
from .wire import ContractError, Count, Digest, Identifier, RunId, WireRecord


@dataclass(frozen=True)
class BranchRef(WireRecord):
    source_run_id: RunId
    checkpoint_id: RunId
    checkpoint_hash: Digest
    slot_id: Identifier


@dataclass(frozen=True)
class CommonEvidence(WireRecord):
    records: tuple[EvidenceRecord, ...]
    checks: tuple[CheckResult, ...]


@dataclass(frozen=True)
class RemainingLimits(WireRecord):
    wall_ms: Count
    max_turns: Count
    max_recoveries: Count
    max_transitions: Count
    cleanup_reserve_ms: Count


@dataclass(frozen=True)
class CheckpointRecord(WireRecord):
    checkpoint_id: RunId
    source_run_id: RunId
    experiment_id: Identifier
    task_id: Identifier
    snapshot: SnapshotRef
    snapshots: tuple[SnapshotRef, ...]
    workspace_bundle: FrozenPatch
    common_evidence_hash: Digest
    frozen_trigger_hash: Digest
    trigger_ids: tuple[Identifier, ...]
    anchor_ids: tuple[Identifier, ...]
    source_gate_key: Digest
    fixed_requirements_hash: Digest
    initial_policy_hash: Digest
    config_hash: Digest
    environment_hash: Digest
    common_history_hash: Digest
    last_turn_id: Identifier
    remaining_limits: RemainingLimits
    supplement_cap: Count
    initial_usage_refs: tuple[Identifier, ...]
    token_limit_mode: Literal["observe_only"]
    session_clone_verified: bool

    def validate(self):
        if (self.workspace_bundle.run_id != self.source_run_id or not self.workspace_bundle.applicable
                or self.snapshot.snapshot_id != self.workspace_bundle.snapshot_id or not self.snapshot.complete
                or self.snapshot not in self.snapshots or self.session_clone_verified):
            raise ContractError("unsupported or inconsistent checkpoint")
