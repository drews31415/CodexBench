"""Append-only controller streams and atomic public run reports."""

from pathlib import Path
from contextlib import contextmanager
from uuid import UUID

from aah.domain import ContractError, WireRecord, canonical_json_bytes, dump_record, load_record, sha256_bytes
from aah.domain.report import RunReport
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, read_regular, relative_path


class RunStore:
    STREAMS = frozenset({"events.raw", "evidence", "checks", "transitions", "interventions", "context", "usage", "overhead"})

    @contextmanager
    def measure(self, stage, clock, turn_id=None):
        start = clock()
        try:
            yield
        finally:
            self.append("overhead", {"stage": stage, "turn_id": turn_id,
                                     "elapsed_ms": max(0, int((clock() - start) * 1000))})

    def __init__(self, root: Path, run_id: str):
        self.root = checked_path(root, allow_missing=True)
        if self.root.name != str(UUID(run_id)) or self.root.name != run_id:
            raise ContractError("run output requires a canonical UUID directory")
        self.root.mkdir(parents=True, exist_ok=False)
        self.run_id = run_id

    def append(self, stream: str, record) -> None:
        if stream not in self.STREAMS:
            raise ContractError("unregistered artifact stream")
        body = dump_record(record) if isinstance(record, WireRecord) else canonical_json_bytes(
            {"schema_version": "1.0", **record})
        path = checked_path(self.root / (stream + ".jsonl"), allow_missing=True)
        if path.exists():
            info = path.lstat()
            if not path.is_file() or info.st_nlink != 1:
                raise ContractError("artifact stream must be a single-link regular file")
        with path.open("ab") as out:
            out.write(body.rstrip(b"\n") + b"\n")

    def read_payload(self, ref: str) -> bytes:
        if not ref.startswith("restricted/"):
            raise ContractError("raw payload must be stored in the restricted area")
        path = checked_path(self.root / relative_path(ref))
        if path.stat().st_size > 1024 * 1024:
            raise ContractError("raw event payload exceeds the observation limit")
        return read_regular(path)[0]

    def save_manifest(self, manifest: dict) -> str:
        body = canonical_json_bytes({"schema_version": "1.0", **manifest})
        store_bytes(self.root, "manifest.json", body)
        return sha256_bytes(body)

    def publish(self, report: RunReport) -> None:
        if report.run_id != self.run_id:
            raise ContractError("foreign run report")
        store_bytes(self.root, "report.json", dump_record(report))

    def mark_incomplete(self, reason: str) -> None:
        store_bytes(self.root, "incomplete.json",
                    canonical_json_bytes({"schema_version": "1.0", "run_id": self.run_id, "reason": reason}))


def read_report(root: Path, run_id: str) -> bytes:
    if str(UUID(run_id)) != run_id:
        raise ContractError("expected a canonical run UUID")
    root = checked_path(root)
    direct = root / run_id
    path = direct if direct.exists() else root / "local" / run_id
    data = read_regular(path / "report.json")[0]
    report = load_record(RunReport, data)
    if report.run_id != run_id:
        raise ContractError("report belongs to another run")
    if sha256_bytes(read_regular(path / "manifest.json")[0]) != report.manifest_hash:
        raise ContractError("report manifest no longer matches")
    return dump_record(report)
