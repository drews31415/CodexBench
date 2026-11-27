"""The workflow's legal phase transitions."""

from dataclasses import replace

from aah.domain import ContractError, Phase, RunState
from aah.evidence.ledger import EvidenceLedger
from aah.reporting.store import RunStore

from .budget import Budget


ALLOWED = {
    Phase.INIT: {Phase.RESTORE_VERIFY, Phase.INITIAL_CONTEXT, Phase.RUN, Phase.FINALIZE},
    Phase.RESTORE_VERIFY: {Phase.DECIDE, Phase.FINALIZE},
    Phase.INITIAL_CONTEXT: {Phase.RUN, Phase.FINALIZE},
    Phase.RUN: {Phase.OBSERVE},
    Phase.OBSERVE: {Phase.DECIDE, Phase.FINALIZE},
    Phase.DECIDE: {Phase.DIAGNOSE, Phase.RUN, Phase.FINALIZE},
    Phase.DIAGNOSE: {Phase.SELECT_STRATEGY, Phase.FINALIZE},
    Phase.SELECT_STRATEGY: {Phase.RECOVERY_CONTEXT, Phase.RUN, Phase.FINALIZE},
    Phase.RECOVERY_CONTEXT: {Phase.RUN, Phase.FINALIZE},
    Phase.FINALIZE: {Phase.REPORT},
    Phase.REPORT: set(),
}


class StateMachine:
    def __init__(self, store: RunStore, budget: Budget, ledger: EvidenceLedger):
        self.store = store
        self.budget = budget
        self.ledger = ledger
        self.state = RunState(store.run_id, Phase.INIT, 0, 0, 0, None, 0, None, budget.state(), None)

    def transition(self, phase: Phase, reason: str, *, snapshot=None, intervention=None, stop=None) -> None:
        prior = self.state
        if phase not in ALLOWED[prior.phase]:
            raise ContractError("illegal workflow phase transition")
        if (self.budget.transitions >= self.budget.limits.max_transitions
                and phase not in (Phase.FINALIZE, Phase.REPORT)
                and not (prior.phase == Phase.RUN and phase == Phase.OBSERVE)):
            raise ContractError("workflow transition budget exhausted")
        self.budget.transitions += 1
        account = self.budget.state()
        self.state = replace(prior, phase=phase, transition_count=account.transitions_used,
                             recovery_reserved=account.recovery_reserved, budget=account,
                             turn_index=account.turns_used, ledger_cursor=self.ledger.cursor,
                             snapshot_id=snapshot or prior.snapshot_id,
                             last_intervention_id=intervention or prior.last_intervention_id,
                             stop_reason=stop or prior.stop_reason)
        self.store.append("transitions", {
            "sequence": self.state.transition_count, "from_phase": prior.phase, "to_phase": phase,
            "reason_code": reason, "elapsed_ms": account.elapsed_ms, "snapshot_id": self.state.snapshot_id,
            "ledger_cursor": self.state.ledger_cursor, "intervention_id": intervention,
            **({"strategy_id": "CONTINUE"} if phase == Phase.RUN and reason == "NATIVE_CONTINUE" else {}),
            **({"strategy_id": "STOP"} if phase == Phase.FINALIZE else {}),
        })
