"""Public workflow result without independent evaluation fields."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .evidence import CheckStatus
from .models import Condition, FrozenPatch, StopReason
from .wire import ContractError, Count, Digest, Identifier, RunId, WireRecord


@dataclass(frozen=True)
class RunReport(WireRecord):
    run_id: RunId
    condition: Condition
    manifest_hash: Digest
    stop_reason: StopReason
    completion_declared: bool | None
    public_check_status: CheckStatus
    frozen_patch: FrozenPatch | None
    evaluable: bool
    turns_used: Count
    recoveries_reserved: Count
    recoveries_delivered: Count | None
    usage_summary: Mapping[Identifier, Count | None]
    elapsed_ms: Count
    intervention_refs: tuple[Identifier, ...]
    artifact_status: Literal["complete", "incomplete"]
    warnings: tuple[str, ...]

    def validate(self) -> None:
        if self.frozen_patch is not None and self.frozen_patch.run_id != self.run_id:
            raise ContractError("report patch belongs to another run")
        if self.evaluable and (self.artifact_status != "complete" or self.frozen_patch is None
                               or not self.frozen_patch.applicable):
            raise ContractError("evaluable reports require a usable frozen patch")
