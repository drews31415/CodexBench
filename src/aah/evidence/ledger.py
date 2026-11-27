"""Store immutable public evidence in receive order."""

from dataclasses import dataclass, replace

from aah.domain import CheckResult, ContractError, EvidenceRecord
from aah.reporting.store import RunStore


@dataclass(frozen=True)
class EvidenceView:
    records: tuple[EvidenceRecord, ...]
    checks: tuple[CheckResult, ...]


class EvidenceLedger:
    def __init__(self, store: RunStore):
        self._store = store
        self._records: dict[str, EvidenceRecord] = {}
        self._checks: dict[str, tuple[int, CheckResult]] = {}

    @property
    def cursor(self) -> int:
        return len(self._records)

    def append_batch(self, batch) -> int:
        for record in batch:
            if record.run_id != self._store.run_id:
                raise ContractError("foreign evidence")
            prior = self._records.get(record.evidence_id)
            if prior is not None:
                if prior != replace(record, sequence=prior.sequence):
                    raise ContractError("conflicting evidence identity")
                continue
            saved = replace(record, sequence=self.cursor + 1)
            self._store.append("evidence", saved)
            self._records[saved.evidence_id] = saved
        return self.cursor

    def append_check(self, check: CheckResult, records) -> None:
        prior = self._checks.get(check.check_result_id)
        if prior is not None and prior[1] != check:
            raise ContractError("conflicting check identity")
        if prior is None:
            self._store.append("checks", check)
        cursor = self.append_batch(records)
        if prior is None:
            self._checks[check.check_result_id] = (cursor, check)

    def view(self, cursor: int | None = None) -> EvidenceView:
        bound = self.cursor if cursor is None else cursor
        if not 0 <= bound <= self.cursor:
            raise ContractError("ledger cursor does not exist")
        records = tuple(self._records.values())[:bound]
        checks = tuple(check for offset, check in self._checks.values() if offset <= bound)
        return EvidenceView(records, checks)

    def restore(self, source_run_id: str, view: EvidenceView) -> None:
        if self.cursor or self._checks:
            raise ContractError("checkpoint requires a fresh ledger")
        offsets = {}
        for position, record in enumerate(view.records, 1):
            if record.run_id != source_run_id or record.sequence != position or record.evidence_id in self._records:
                raise ContractError("checkpoint evidence identity or order mismatch")
            self.append_batch((replace(record, run_id=self._store.run_id),))
            if record.kind in {"TEST", "FAILURE"}:
                offsets[record.payload["check_result_id"]] = position
        for check in view.checks:
            if check.check_result_id not in offsets or check.check_result_id in self._checks:
                raise ContractError("checkpoint check has no unique source")
            self._store.append("checks", check)
            self._checks[check.check_result_id] = (offsets[check.check_result_id], check)
