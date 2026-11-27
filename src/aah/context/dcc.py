"""Validity-aware deterministic packing with marginal utility density."""

from collections.abc import Callable
from pathlib import PurePosixPath

from .candidates import Candidate, Selection
from .relevance import relevance, tokens


INITIAL_WEIGHTS = (0.5, 0.3, 0.2, 0.0)
RECOVERY_WEIGHTS = (0.25, 0.2, 0.2, 0.35)
DUPLICATE_PENALTY = 0.2
COMPONENTS = ("relevance", "scope", "validity", "failure")


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    if not (left or right):
        return 0.0
    return len(left & right) / len(left | right)


def _scope(candidate: Candidate, targets: set[str]) -> float:
    if targets.intersection(candidate.scope):
        return 1.0
    if any(PurePosixPath(target).is_relative_to(path)
           for target in targets for path in candidate.scope):
        return 0.5
    return 0.0


def select(candidates: tuple[Candidate, ...], *, core: tuple[Selection, ...], query: str,
           snapshot_id: str, targets: tuple[str, ...], failure_anchors: tuple[str, ...], recovery: bool,
           cap: int, size: Callable[[tuple[Selection, ...]], int]) -> tuple[tuple[Selection, ...], dict]:
    enabled = [candidate for candidate in candidates if candidate.validity(snapshot_id) != "INVALID"]
    trace = {candidate.candidate_id: {"selected": False, "reason_code": "INVALID"}
             for candidate in candidates if candidate.validity(snapshot_id) == "INVALID"}

    groups: dict[tuple, list[Candidate]] = {}
    for candidate in sorted(enabled, key=lambda item: item.candidate_id):
        # Strip outer newlines only, preserving meaningful whitespace, numbers and literals.
        key = (candidate.text.strip("\n"), candidate.kind, candidate.snapshot_id, candidate.revision,
               candidate.provenance, candidate.scope, candidate.span, candidate.path_refs)
        groups.setdefault(key, []).append(candidate)
    pool = {}
    for items in groups.values():
        unique = Selection(items[0], tuple(items))
        pool[unique.candidate_id] = unique
        for duplicate in items[1:]:
            trace[duplicate.candidate_id] = {"selected": False, "reason_code": "MERGED_DUPLICATE",
                                             "duplicate_of": unique.candidate_id}
    # Normalize on the de-duplicated eligible corpus, independent of input ordering.
    corpus = [entry.candidate.text + " " + entry.candidate.source for entry in pool.values()]
    weights = RECOVERY_WEIGHTS if recovery else INITIAL_WEIGHTS
    anchor_set, target_set = set(failure_anchors), set(targets)
    terms = {key: frozenset(tokens(entry.candidate.text)) for key, entry in pool.items()}
    core_terms = [frozenset(tokens(entry.candidate.text)) for entry in core]
    overlap = {key: sum(_overlap(words, other) for other in core_terms) for key, words in terms.items()}
    scores = {}
    for (key, entry), lexical in zip(pool.items(), relevance(query, corpus), strict=True):
        item = entry.candidate
        scope_score = _scope(item, target_set)
        provenance = {ref for origin in entry.origins for ref in origin.evidence_ids}
        anchor_score = 1.0 if anchor_set.intersection(provenance) else (0.5 if scope_score else 0.0)
        valid = 0.5 if item.validity(snapshot_id) == "UNCERTAIN" else 1.0
        values = (lexical, scope_score, valid, (anchor_score + scope_score) / 2 if recovery else 0.0)
        scores[key] = sum(weight * value for weight, value in zip(weights, values, strict=True))
        trace[key] = {"selected": False, "score": scores[key], "components": dict(zip(COMPONENTS, values))}

    selected: tuple[Selection, ...] = ()
    while pool:
        current = size(selected)
        feasible = []
        for key, item in pool.items():
            final = size((*selected, item))
            incremental = max(1, final - current)
            utility = (scores[key] - DUPLICATE_PENALTY * overlap[key]) / incremental
            reason = "OVER_CAP" if final > cap else "NON_POSITIVE_UTILITY" if utility <= 0 else "LOWER_RANK"
            trace[key].update(reason_code=reason, utility_density=utility, estimated_tokens=incremental)
            if reason == "LOWER_RANK":
                feasible.append((-utility, key))
        if not feasible:
            break
        _, best = min(feasible)
        selected = (*selected, pool.pop(best))
        trace[best].update(selected=True, reason_code="SELECTED")
        for key in pool:
            overlap[key] += _overlap(terms[key], terms[best])
    return selected, trace
