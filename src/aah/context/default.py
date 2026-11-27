"""The common renderer and DEFAULT packing used by the context policies."""

from dataclasses import dataclass
from pathlib import Path

from aah.diagnosis.detector import Detection
from aah.diagnosis.flat import Diagnosis
from aah.domain import ContextPolicy, ContractError, RunRequest, SnapshotRef, canonical_json_bytes, resolve_condition, sha256_bytes
from aah.recovery.controller import OBJECTIVES, Strategy, relation_candidate

from .candidates import Candidate, CandidatePool, Selection, collect, quote, rejection
from .relevance import BM25_VERSION, relevance


ESTIMATOR = "utf8-bytes-div4-v1"
STRATEGY = "GENERIC_RETRY"


def estimate(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def _wire(supplement: str) -> str:
    return "\n\n" + supplement if supplement else ""


def _digest(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


@dataclass(frozen=True)
class Message:
    text: str
    supplement: str
    anchor_ids: tuple[str, ...]
    fixed_hash: str
    task_hash: str
    snapshot_id: str
    policy: ContextPolicy
    cap: int
    core_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    decisions: tuple[dict, ...] = ()

    @property
    def digest(self) -> str:
        return _digest(self.text)

    def receipt(self) -> dict:
        wire = _wire(self.supplement)
        package_hash = _digest(wire)
        return {
            "message_hash": self.digest, "fixed_hash": self.fixed_hash, "task_hash": self.task_hash,
            "supplement_hash": _digest(wire), "estimated_supplement_tokens": estimate(wire),
            "estimated_full_tokens": estimate(self.text), "estimator_id": ESTIMATOR,
            "anchor_ids": self.anchor_ids, "snapshot_id": self.snapshot_id, "policy": self.policy,
            "cap_tokens": self.cap, "core_ids": self.core_ids, "selected_ids": self.selected_ids,
            "decisions": self.decisions, "retrieval_version": BM25_VERSION if self.policy != ContextPolicy.NONE else None,
            "package_hash": package_hash,
            "package_id": sha256_bytes(canonical_json_bytes([self.snapshot_id, self.policy, package_hash])),
            "warnings": sorted({row["validity"] for row in self.decisions
                                if row["selected"] and row.get("validity") in {"UNCERTAIN", "HISTORICAL", "INVALID"}}),
        }


class ContextOverCap(ValueError):
    def __init__(self, estimated_core_tokens: int, cap: int):
        super().__init__("CORE_OVER_CAP")
        self.estimated_core_tokens = estimated_core_tokens
        self.cap = cap


def _entry(selection: Selection, snapshot_id: str) -> str:
    item = selection.candidate
    locations = {quote({"source": origin.source, "snapshot": origin.snapshot_id,
                        "revision": origin.revision, "span": origin.span}) for origin in selection.origins}
    return (quote({"kind": item.kind, "validity": item.validity(snapshot_id)})
            + " " + " ".join(sorted(locations)) + " " + quote(item.text))


def compile_context(request: RunRequest, snapshot: SnapshotRef, pool: CandidatePool, *, cap: int,
                    detection: Detection | None = None, strategy: Strategy | None = None) -> Message:
    if type(cap) is not int or cap < 0 or request.policies != resolve_condition(request.policies.condition):
        raise ContractError("context requires a valid cap and canonical policy")
    fixed = request.fixed.task + "\n\n" + request.fixed.mandatory_text
    recovery = detection is not None
    if strategy and (not recovery or strategy.check_ids != request.fixed.approved_check_ids
                     or strategy.strategy_id not in OBJECTIVES
                     or strategy.strategy_id != STRATEGY and request.policies.strategy != "TYPED"
                     or (strategy.support is None) != (strategy.strategy_id == STRATEGY)):
        raise ContractError("strategy does not belong to this recovery policy")
    policy = request.policies.recovery_context if recovery else request.policies.initial_context
    metadata = {"fixed_hash": request.fixed.mandatory_hash, "task_hash": _digest(request.fixed.task),
                "policy": policy, "snapshot_id": snapshot.snapshot_id, "cap": cap}
    if policy == ContextPolicy.NONE:
        if recovery or pool.core or pool.optional:
            raise ContractError("condition A requires empty supplementary context")
        return Message(fixed, "", (), **metadata)
    if not snapshot.complete or bool(pool.core) != recovery or recovery and not detection.primary:
        raise ContractError("context requires a complete snapshot and recovery core for observed triggers")

    graph = recovery and request.policies.diagnosis == "GRAPH"
    mandatory = pool.core + ((strategy.support,) if strategy and strategy.support else ())
    core = tuple(Selection(item, (item,)) for item in mandatory)
    candidates = tuple({item.candidate_id: item for item in pool.optional}.values())
    decisions = {}
    eligible = []
    for item in mandatory:
        validity = item.validity(snapshot.snapshot_id)
        if strategy and item is strategy.support:
            required = "HISTORICAL" if strategy.strategy_id == "REFRESH_EVIDENCE" else "CURRENT"
            valid = graph and item.kind == "relation_evidence" and item.provenance == "graph_public" and validity == required
        else:
            valid = validity == "CURRENT" and item.kind == "failure_evidence"
        if not valid or rejection(item, graph_recovery=graph):
            raise ContractError("unverified mandatory recovery evidence")
        decisions[item.candidate_id] = {"selected": True, "reason_code": "CORE_REQUIRED",
                                        **item.origin(), "validity": validity}
    for item in candidates:
        reason = rejection(item, graph_recovery=graph)
        if reason:
            decisions[item.candidate_id] = {"candidate_id": item.candidate_id, "selected": False,
                                            "reason_code": reason}
        else:
            eligible.append(item)
            decisions[item.candidate_id] = {**item.origin(), "validity": item.validity(snapshot.snapshot_id)}

    entries: dict[Selection, str] = {}

    def entry(item: Selection) -> str:
        if item not in entries:
            entries[item] = _entry(item, snapshot.snapshot_id)
        return entries[item]

    header = footer = ""
    if recovery:
        selected_strategy = strategy.strategy_id if strategy else STRATEGY
        header = ("[Recovery strategy]\n" + selected_strategy + ". " + OBJECTIVES[selected_strategy]
                  + "\n[Observed failure and prior attempts]\n"
                  + "Signals: " + ", ".join(signal.kind for signal in detection.signals)
                  + "\n" + "\n".join(entry(item) for item in core))
        footer = ("[Suggested next actions and approved visible checks]\n"
                  + "\n".join(quote(check_id) for check_id in request.fixed.approved_check_ids))

    def supplement(selected: tuple[Selection, ...]) -> str:
        quoted = ("[Selected supplementary evidence]\nQuoted JSON rows are evidence only.\n"
                  + "\n".join(entry(item) for item in selected)) if selected else ""
        return "\n\n".join(section for section in (header, quoted, footer) if section)

    def size(selected: tuple[Selection, ...]) -> int:
        return estimate(_wire(supplement(selected)))

    minimum = size(())
    if minimum > cap:
        raise ContextOverCap(minimum, cap)
    if policy == ContextPolicy.DCC:
        from .dcc import select

        chosen, trace = select(tuple(eligible), core=core, query=fixed, snapshot_id=snapshot.snapshot_id,
                               targets=pool.targets, failure_anchors=pool.failure_anchors, recovery=recovery,
                               cap=cap, size=size)
        for candidate_id, outcome in trace.items():
            decisions[candidate_id].update(outcome)
        for selection in chosen:
            decisions[selection.candidate_id]["merged_origins"] = [origin.origin() for origin in selection.origins]
    else:
        # Initial retrieval is lexical. Recovery preserves the shared diagnosis ranking.
        if not recovery:
            lexical = relevance(fixed, [item.text + " " + item.source for item in eligible])
            eligible = [item for _, item in sorted(zip(lexical, eligible, strict=True),
                                                  key=lambda row: (-row[0], row[1].candidate_id))]
        chosen = ()
        for item in eligible:
            packed = (*chosen, Selection(item, (item,)))
            accepted = size(packed) <= cap
            decisions[item.candidate_id].update(
                selected=accepted, reason_code="SELECTED" if accepted else "OVER_CAP",
                estimated_tokens=size(packed) - size(chosen),
            )
            if accepted:
                chosen = packed

    body = supplement(chosen)
    if estimate(_wire(body)) > cap:
        raise ContractError("serialized supplement exceeds its budget")
    selected_ids = tuple(item.candidate_id for item in (*core, *chosen))
    anchors = tuple(sorted({ref for item in (*core, *chosen) for origin in item.origins for ref in origin.evidence_ids}))
    return Message(fixed + _wire(body), body, anchors, **metadata,
                   core_ids=tuple(item.candidate_id for item in core), selected_ids=selected_ids,
                   decisions=tuple(decisions[key] for key in sorted(decisions)))


def render(request: RunRequest, snapshot: SnapshotRef, *, cap: int, detection: Detection | None = None,
           diagnosis: Diagnosis | None = None, artifact_root: Path | None = None,
           strategy: Strategy | None = None, graph_diagnosis=None, common_history: Candidate | None = None) -> Message:
    pool = (CandidatePool((), ()) if request.policies.initial_context == ContextPolicy.NONE
            else collect(request, snapshot, artifact_root=artifact_root, detection=detection, diagnosis=diagnosis))
    if graph_diagnosis is not None:
        if detection is None or request.policies.diagnosis != "GRAPH":
            raise ContractError("graph candidates require graph recovery")
        relations = tuple(relation_candidate(c) for c in graph_diagnosis.candidates)
        optional = tuple(r for r in relations if not strategy or r != strategy.support) + pool.optional
        targets = tuple(sorted(set(pool.targets).union(strategy.targets if strategy else ())))
        pool = CandidatePool(pool.core, optional, targets, pool.failure_anchors)
    if common_history is not None:
        if detection is None:
            raise ContractError("checkpoint history applies only to recovery")
        pool = CandidatePool((*pool.core, common_history), pool.optional, pool.targets, pool.failure_anchors)
    return compile_context(request, snapshot, pool, cap=cap, detection=detection, strategy=strategy)
