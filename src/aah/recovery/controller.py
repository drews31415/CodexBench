"""Choose a directive backed by the triggering observations and its diagnostic path."""

from dataclasses import dataclass

from aah.context.candidates import Candidate, public_path, quote
from aah.domain import ContractError, StrategyId


OBJECTIVES = {
    StrategyId.GENERIC_RETRY: "Reassess the observed failure and verify the revised change.",
    StrategyId.REVIEW_REGRESSION: "Inspect the implicated change, preserve earlier behavior and rerun the approved checks.",
    StrategyId.RELOCALIZE: "Reconsider the earlier location and inspect the related dependency before verifying a revision.",
    StrategyId.REFRESH_EVIDENCE: "Recheck the current snapshot and gather fresh evidence for the historical reference.",
}


@dataclass(frozen=True)
class Strategy:
    strategy_id: StrategyId
    evidence_ids: tuple[str, ...]
    check_ids: tuple[str, ...]
    support: Candidate | None = None

    @property
    def targets(self):
        return self.support.scope if self.support else ()

    def receipt(self, detection):
        return {"strategy_id": self.strategy_id, "trigger_ids": tuple(s.kind for s in detection.signals),
                "objective": OBJECTIVES[self.strategy_id], "targets": self.targets,
                "evidence_ids": self.evidence_ids, "path_refs": self.support.path_refs if self.support else (),
                "next_checks": self.check_ids, "confidence_status": "inferred" if self.support else "unspecified",
                "reason": "graph_supported" if self.support else "common_retry",
                "completion_observation": "subsequent_target_action" if self.support else "subsequent_change"}


def relation_candidate(row):
    if not public_path(row["path"]):
        raise ContractError("non-public diagnosis target")
    return Candidate("relation_evidence", row["path"], quote({
        "candidate": row["qualified_name"] or row["path"], "relations": row["support_relations"],
        "interpretation": "investigation candidate", "path_ref": row["support_ref"]}),
        "graph_public", row["snapshot_id"], row["candidate_id"], (row["path"],),
        row["evidence_ids"], path_refs=(row["support_ref"],))


def select(detection, diagnosis, approved, *, changed_paths=()):
    anchors = {s.kind: set(s.anchors) for s in detection.signals}
    refs = tuple(sorted(set().union(*anchors.values())))
    default = Strategy(StrategyId.GENERIC_RETRY, refs, approved)
    if not anchors:
        raise ContractError("a recovery directive requires an observed trigger")
    if diagnosis is None:
        return default
    candidates = diagnosis.candidates
    regression = [c for c in candidates if "T3" in c["trigger_ids"]
                  and c["score_components"]["modified"]]
    historical = sorted((c for c in diagnosis.historical if c["localized"]),
                        key=lambda c: (-c["score"], c["path"], c["candidate_id"]))
    alternatives = [c for c in candidates if {"T1", "T2"}.intersection(c["trigger_ids"]) and c["alternative"]
                    and c["path"] not in changed_paths]
    if regression:
        kind, row = StrategyId.REVIEW_REGRESSION, regression[0]
    elif historical and not any(c["localized"] for c in candidates):
        kind, row = StrategyId.REFRESH_EVIDENCE, historical[0]
    elif alternatives:
        kind, row = StrategyId.RELOCALIZE, alternatives[0]
    else:
        return default
    support = relation_candidate(row)
    return Strategy(kind, tuple(sorted(set(refs) | set(support.evidence_ids))), approved, support)
