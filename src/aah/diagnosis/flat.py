"""Rank public failure observations without a repository or execution graph."""

from dataclasses import dataclass

from aah.domain import CheckResult, EvidenceRecord, SnapshotRef
from aah.evidence.ledger import EvidenceView

from .detector import Detection, trusted


@dataclass(frozen=True)
class Diagnosis:
    anchors: tuple[str, ...]
    current_checks: tuple[CheckResult, ...]
    comparable_attempts: tuple[CheckResult, ...]
    searches: tuple[EvidenceRecord, ...]
    changes: tuple[EvidenceRecord, ...]


def diagnose(detection: Detection, view: EvidenceView, snapshot: SnapshotRef,
             approved: tuple[str, ...]) -> Diagnosis:
    anchors = tuple(sorted({ref for signal in detection.signals for ref in signal.anchors}))
    checks = {check.check_id: check for check in view.checks if check.check_id in approved}
    current = tuple(checks[key] for key in approved if key in checks
                    and checks[key].snapshot_id == snapshot.snapshot_id and trusted(checks[key])
                    and (checks[key].check_result_id in anchors or checks[key].status == "FAIL"))
    ranked = tuple(check for check in reversed(view.checks) if check.check_id in approved
                   and check.check_result_id in anchors and trusted(check))
    searches = tuple(record for record in view.records
                     if record.kind == "SEARCH" and record.evidence_id in anchors)
    changes = tuple(record for record in reversed(view.records)
                    if record.kind == "CHANGE" and record.snapshot_id == snapshot.snapshot_id)
    return Diagnosis(anchors, current, ranked, searches, changes)
