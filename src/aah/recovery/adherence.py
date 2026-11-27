"""Assess the dispatched strategy using observations from its own subsequent turn."""

from aah.domain import StrategyId
from aah.domain.evidence import DIRECT_READ_BASES


def assess(strategy, records, turn_id, *, delivered, before_snapshot):
    related = []
    checks = {}
    for r in records:
        if r.turn_id != turn_id or r.observation_status != "observed":
            continue
        p = r.payload
        if r.kind == "CHANGE" and p["before_snapshot_id"] == before_snapshot and p["after_snapshot_id"] == r.snapshot_id:
            if strategy.strategy_id != StrategyId.REFRESH_EVIDENCE and (
                    not strategy.targets or p["path"] in strategy.targets):
                related.append(r.evidence_id)
        elif (r.kind == "FILE_ACCESS" and r.snapshot_id == before_snapshot and p["path"] in strategy.targets
              and p["access_kind"] == "read" and p["access_basis"] in DIRECT_READ_BASES):
            related.append(r.evidence_id)
        elif r.kind == "TEST" and p["check_id"] in strategy.check_ids and p["status"] in {"PASS", "FAIL"}:
            checks[p["check_id"]] = r.evidence_id
    needs_checks = strategy.strategy_id in {StrategyId.REVIEW_REGRESSION, StrategyId.GENERIC_RETRY}
    performed = delivered and bool(related) and (not needs_checks or set(strategy.check_ids) == checks.keys())
    return {"status": "performed" if performed else "unknown",
            "evidence_ids": sorted(related) if delivered else [], "check_evidence_ids": sorted(checks.values()),
            "basis": "target_action_observed" if performed else "insufficient_observation"}
