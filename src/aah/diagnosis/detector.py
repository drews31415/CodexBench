"""Detect recovery signals from comparable public observations."""

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction

from aah.domain import CheckResult, CheckStatus, SnapshotRef, TriggerId, canonical_json_bytes, sha256_bytes
from aah.evidence.ledger import EvidenceView


@dataclass(frozen=True)
class Signal:
    kind: TriggerId
    anchors: tuple[str, ...]
    meaning: tuple[str, ...]


@dataclass(frozen=True)
class Detection:
    signals: tuple[Signal, ...]
    coverage_gaps: tuple[str, ...]

    @property
    def primary(self) -> Signal | None:
        return self.signals[0] if self.signals else None

def trusted(check: CheckResult) -> bool:
    return check.source_class == "TRUSTED_RUNNER" and check.status in (CheckStatus.PASS, CheckStatus.FAIL)


def _definition(check: CheckResult) -> tuple[str, ...]:
    return check.check_id, check.check_definition_hash, check.environment_hash, check.parser_id


def case_signal_keys(check, case):
    """Semantic keys shared by the detector and case-specific recovery attribution."""
    definition = _definition(check)
    return {
        "T1": (sha256_bytes(canonical_json_bytes((*definition, case.test_id, case.fingerprint))),),
        "T3": tuple(sha256_bytes(canonical_json_bytes((*definition, key))) for key in (case.test_id, "PASS_TO_FAIL")),
    }


def _cases(check: CheckResult) -> dict:
    return {case.test_id: case for case in check.test_cases}


def comparable(prior: CheckResult, current: CheckResult) -> bool:
    return (trusted(prior) and trusted(current) and _definition(prior) == _definition(current)
            and _cases(prior).keys() == _cases(current).keys()
            and all(case.status in (CheckStatus.PASS, CheckStatus.FAIL)
                    for check in (prior, current) for case in check.test_cases))


def _improved(prior: CheckResult, current: CheckResult) -> bool:
    return (prior.status == CheckStatus.FAIL and current.status == CheckStatus.PASS
            or any(case.status == CheckStatus.FAIL and _cases(current)[case.test_id].status == CheckStatus.PASS
                   for case in prior.test_cases))


def _search_signal(view: EvidenceView, snapshot: SnapshotRef, turn_id: str, *, search_window: int,
                   threshold: Fraction, checks_comparable: bool, improved: bool) -> tuple[Signal | None, bool]:
    searches = [record for record in view.records if record.kind == "SEARCH"]
    recent = searches[-search_window:]
    if (len(recent) < search_window or recent[-1].turn_id != turn_id or not checks_comparable
            or any(record.observation_status != "observed" or record.snapshot_id != snapshot.snapshot_id
                   or not record.payload["result_digest"] for record in recent)):
        return None, False
    start = recent[0].sequence
    if improved or any(item.sequence >= start and item.kind == "CHANGE" for item in view.records):
        return None, True
    turns = {record.turn_id for record in recent}
    current_checks = {check.check_id: check for check in view.checks if check.turn_id == turn_id}
    for check in view.checks:
        if check.turn_id in turns:
            current = current_checks.get(check.check_id)
            if current is None or check.snapshot_id != snapshot.snapshot_id or not comparable(check, current):
                return None, False
            if _improved(check, current):
                return None, True
    keys = [(item.payload["scope"], item.payload["query"], item.payload["result_digest"]) for item in recent]
    by_query = {(scope, query) for scope, query, _ in keys}
    if len(set(keys)) != len(by_query) or Fraction(len(keys) - len(set(keys)), len(keys)) < threshold:
        return None, True
    meaning = tuple(sorted(sha256_bytes(canonical_json_bytes(key)) for key in set(keys)))
    return Signal(TriggerId.T2, tuple(record.evidence_id for record in recent), meaning), True


def detect(view: EvidenceView, snapshot: SnapshotRef, turn_id: str, required_checks: tuple[str, ...], *,
           repeated: int = 2, search_window: int = 6, threshold: Fraction = Fraction(67, 100)) -> Detection:
    history = defaultdict(list)
    for check in view.checks:
        if check.check_id in required_checks:
            history[check.check_id].append(check)

    matches = defaultdict(list)
    gaps = set()
    comparisons = {}
    failing = []
    observed_completion = next((item for item in reversed(view.records)
                                if item.turn_id == turn_id and item.kind == "COMPLETION"
                                and item.snapshot_id == snapshot.snapshot_id
                                and item.observation_status == "observed"
                                and item.payload["declared"] is True), None)
    for check_id in required_checks:
        rows = history[check_id]
        if (not snapshot.complete or not rows or rows[-1].turn_id != turn_id
                or rows[-1].snapshot_id != snapshot.snapshot_id or not trusted(rows[-1])):
            gaps.add("T5_PUBLIC_CHECK_UNAVAILABLE")
            continue
        current = rows[-1]
        if current.status == CheckStatus.FAIL:
            failing.append(current)
        if len(rows) < 2 or not comparable(rows[-2], current):
            continue
        prior = rows[-2]
        comparisons[check_id] = _improved(prior, current)
        if prior.status == CheckStatus.PASS and current.status == CheckStatus.FAIL:
            matches[TriggerId.T3].append((current.check_result_id, (*_definition(current), "PASS_TO_FAIL")))
        for key, case in _cases(current).items():
            if case.status != CheckStatus.FAIL:
                continue
            if _cases(prior)[key].status == CheckStatus.PASS:
                matches[TriggerId.T3].append((current.check_result_id, (*_definition(current), key)))
            window = rows[-repeated:]
            if len(window) == repeated and case.fingerprint and all(
                comparable(row, current) and row.status == CheckStatus.FAIL
                and _cases(row)[key].status == CheckStatus.FAIL
                and _cases(row)[key].fingerprint == case.fingerprint for row in window
            ) and not any(_improved(left, right) for left, right in zip(window, window[1:])):
                matches[TriggerId.T1].extend(
                    (row.check_result_id, (*_definition(current), key, case.fingerprint)) for row in window)
    if observed_completion:
        for check in failing:
            matches[TriggerId.T4].append((check.check_result_id, (*_definition(check), "DECLARED_FAIL")))
    elif failing:
        gaps.add("T4_COMPLETION_UNVERIFIED")

    stagnant, search_covered = _search_signal(
        view, snapshot, turn_id, search_window=search_window, threshold=threshold,
        checks_comparable=bool(required_checks) and len(comparisons) == len(required_checks),
        improved=any(comparisons.values()),
    ) if snapshot.complete else (None, False)
    if not search_covered:
        gaps.add("T2_STAGNATION_UNVERIFIED")
    if any(item.kind == "UNKNOWN" and item.turn_id == turn_id for item in view.records):
        gaps.add("T5_RUNTIME_EVIDENCE_PARTIAL")
    ordered = []
    for kind in (TriggerId.T3, TriggerId.T1, TriggerId.T2, TriggerId.T4):
        if kind == TriggerId.T2 and stagnant is not None:
            ordered.append(stagnant)
        elif matches[kind]:
            anchors = {anchor for anchor, _ in matches[kind]}
            if kind == TriggerId.T4 and observed_completion:
                anchors.add(observed_completion.evidence_id)
            meaning = {sha256_bytes(canonical_json_bytes(key)) for _, key in matches[kind]}
            ordered.append(Signal(kind, tuple(sorted(anchors)), tuple(sorted(meaning))))
    return Detection(tuple(ordered), tuple(sorted(gaps)))
