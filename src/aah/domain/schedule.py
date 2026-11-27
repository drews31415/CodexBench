"""Identity and join contracts used by CT02, independent of any experiment runner."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .evidence import EvaluationResult
from .models import Condition
from .wire import ContractError, Count, Identifier, RunId, WireRecord


@dataclass(frozen=True)
class ScheduledSlot(WireRecord):
    experiment_id: Identifier
    protocol: Literal["end_to_end", "checkpoint", "ablation"]
    slot_id: Identifier
    block_id: Identifier
    slot_role: Literal["END_TO_END", "CHECKPOINT_SOURCE", "CHECKPOINT_BRANCH", "ABLATION"]
    task_id: Identifier
    checkpoint_id: RunId | None
    variant_id: Identifier | None
    condition: Condition | None
    repetition: Count | None
    order_index: Count

@dataclass(frozen=True)
class RunAttempt(WireRecord):
    slot_id: Identifier
    run_id: RunId
    attempt_index: Count
    replacement_of: RunId | None
    approval_ref: Identifier | None
    selected_for_analysis: bool


def validate_attempt_links(slots: Sequence[ScheduledSlot], attempts: Sequence[RunAttempt],
                           evaluations: Sequence[EvaluationResult]) -> None:
    """Validate one experiment's identities before joining independent results."""
    slot_by_id = {slot.slot_id: slot for slot in slots}
    by_run = {attempt.run_id: attempt for attempt in attempts}
    if len(slot_by_id) != len(slots) or len(by_run) != len(attempts):
        raise ContractError("duplicate slot or run ID")
    indices = set()
    selected = set()
    originals = set()
    for attempt in attempts:
        if attempt.slot_id not in slot_by_id:
            raise ContractError("attempt references an unknown slot")
        key = (attempt.slot_id, attempt.attempt_index)
        if key in indices:
            raise ContractError("duplicate attempt index within slot")
        indices.add(key)
        if attempt.selected_for_analysis:
            if attempt.slot_id in selected:
                raise ContractError("multiple selected attempts in a single slot")
            selected.add(attempt.slot_id)
        if attempt.replacement_of is None:
            if attempt.slot_id in originals:
                raise ContractError("additional attempts require a replacement reference")
            originals.add(attempt.slot_id)
        else:
            prior = by_run.get(attempt.replacement_of)
            if (prior is None or prior.slot_id != attempt.slot_id
                    or prior.attempt_index >= attempt.attempt_index or not attempt.approval_ref):
                raise ContractError("replacement requires an approved earlier attempt in the same slot")
    seen_results = set()
    for result in evaluations:
        attempt = by_run.get(result.run_id)
        if attempt is None or slot_by_id[attempt.slot_id].task_id != result.task_id:
            raise ContractError("evaluation does not match an actual run and task")
        if result.run_id in seen_results:
            raise ContractError("duplicate evaluation for run")
        seen_results.add(result.run_id)
