"""Provider usage for one observed turn. Subcounts never increase the total."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .models import UsageMode
from .wire import ContractError, Count, Identifier, RunId, WireRecord


@dataclass(frozen=True)
class UsageRecord(WireRecord):
    usage_id: Identifier
    run_id: RunId
    turn_id: Identifier
    source: Literal["runtime_terminal_event"]
    usage_mode: UsageMode
    stage: Identifier | None
    elapsed_ms: Count | None
    cost_amount: float | None
    currency: Identifier | None
    cost_kind: Literal["ACTUAL", "ESTIMATED", "UNKNOWN"]
    input_tokens: Count | None
    cached_input_tokens: Count | None
    output_tokens: Count | None
    reasoning_tokens: Count | None
    total_tokens: Count | None
    coverage: Literal["observed", "unknown"]
    tokens: Mapping[str, Count] | None
    raw_ref: Identifier | None

    def validate(self):
        if (self.coverage == "observed") != (self.total_tokens is not None):
            raise ContractError("usage coverage and total disagree")
        if self.total_tokens is not None and (self.input_tokens is None or self.output_tokens is None
                                               or self.total_tokens != self.input_tokens + self.output_tokens):
            raise ContractError("invalid model token total")
        if self.cost_kind == "UNKNOWN" and (self.cost_amount is not None or self.currency is not None):
            raise ContractError("unknown cost cannot carry an amount or currency")
