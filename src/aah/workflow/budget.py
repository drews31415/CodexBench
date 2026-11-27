"""Monotonic limits and one-way reservations for a controller run."""

from dataclasses import dataclass
from time import monotonic

from aah.domain import BudgetState, CapabilityStatus, ContractError


@dataclass(frozen=True)
class Limits:
    wall_ms: int
    max_turns: int
    max_recoveries: int
    max_transitions: int
    cleanup_reserve_ms: int

    def __post_init__(self) -> None:
        values = (self.wall_ms, self.max_turns, self.max_recoveries,
                  self.max_transitions, self.cleanup_reserve_ms)
        if (any(type(value) is not int or value < 0 for value in values)
                or not self.max_turns or not self.max_transitions
                or not 0 < self.cleanup_reserve_ms < self.wall_ms):
            raise ContractError("invalid common run limits")


class Budget:
    def __init__(self, limits: Limits, clock=monotonic):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.deadline = self.started + limits.wall_ms / 1000
        self.work_deadline = self.deadline - limits.cleanup_reserve_ms / 1000
        self.turns: set[str] = set()
        self.recoveries: dict[str, str] = {}
        self.transitions = 0

    def can_start(self) -> bool:
        return self.work_ms > 0 and self.transitions < self.limits.max_transitions

    @property
    def work_ms(self) -> int:
        return max(0, int((self.work_deadline - self.clock()) * 1000))

    def turn_limit(self) -> bool:
        return len(self.turns) >= self.limits.max_turns

    def recovery_limit(self) -> bool:
        return len(self.recoveries) >= self.limits.max_recoveries

    def reserve(self, turn_id: str, *, intervention_id: str | None = None, checkpoint: str | None = None) -> None:
        if not self.can_start() or self.turn_limit() or turn_id in self.turns:
            raise ContractError("turn cannot be reserved")
        if (intervention_id is None) != (checkpoint is None):
            raise ContractError("recovery reservation requires an intervention and checkpoint")
        if intervention_id is not None and checkpoint is not None:
            if (self.recovery_limit() or checkpoint in self.recoveries
                    or intervention_id in self.recoveries.values()):
                raise ContractError("recovery cannot be reserved")
            self.recoveries[checkpoint] = intervention_id
        self.turns.add(turn_id)

    def state(self) -> BudgetState:
        now = self.clock()
        return BudgetState(max(0, int((now - self.started) * 1000)),
                           max(0, int((self.deadline - now) * 1000)),
                           len(self.turns), len(self.recoveries), self.transitions, None,
                           CapabilityStatus.UNSUPPORTED)
