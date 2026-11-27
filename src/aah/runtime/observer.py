"""Map trusted transport envelopes without interpreting command text as behavior."""

import json
from collections.abc import Callable
from threading import Lock

from aah.domain import (
    CheckResult, CheckStatus, ContractError, DiffRecord, EvidenceRecord, ObservationStatus, RawEvent,
    SnapshotInterval, SnapshotRef, TurnResult, canonical_json_bytes, sha256_bytes,
)
from aah.evidence.ledger import EvidenceLedger
from aah.domain.evidence import DIRECT_READ_BASES
from aah.domain.usage import UsageRecord
from aah.workspace.snapshot import public_path
from aah.reporting.store import RunStore

from .usage import UsageAccount


class Observer:
    def __init__(self, store: RunStore, ledger: EvidenceLedger, read_payload: Callable[[str], bytes] | None = None):
        self.store = store
        self.ledger = ledger
        self._read_payload = read_payload or store.read_payload
        self._raw: dict[str, RawEvent] = {}
        self._receive_lock = Lock()
        self.usage = UsageAccount()
        self._accounted_turns = {}

    def receive(self, event: RawEvent, turn_id: str) -> None:
        if event.run_id != self.store.run_id or event.turn_id != turn_id:
            raise ContractError("runtime evidence is not attributed to the active turn")
        with self._receive_lock:
            prior = self._raw.get(event.raw_id)
            if prior is not None:
                if prior != event:
                    raise ContractError("conflicting raw event identity")
                return
            self.store.append("events.raw", event)
            self._raw[event.raw_id] = event

    def _record(self, turn_id, identity, kind, payload, *, raw=None, snapshot=None, interval=None,
                status=ObservationStatus.OBSERVED, warnings=()) -> EvidenceRecord:
        return EvidenceRecord(
            sha256_bytes(canonical_json_bytes([turn_id, identity, kind])), self.store.run_id, turn_id, 0, kind,
            (raw.raw_id,) if raw else (), raw.received_at if raw else None, status,
            snapshot, interval, None, payload, warnings,
        )

    def _map_raw(self, raw: RawEvent, before: SnapshotRef | None, after: SnapshotRef | None):
        reason = "EVENT_PAYLOAD_UNAVAILABLE"
        value = None
        if raw.parse_status not in {"OK", "PARSED"}:
            reason = "EVENT_PARSE_UNKNOWN"
        else:
            try:
                value = json.loads(self._read_payload(raw.payload_ref))
                canonical_json_bytes(value)
                if not isinstance(value, dict) or value.get("type") != raw.provider_type:
                    value = None
                    reason = "EVENT_TYPE_MISMATCH"
            except (ValueError, OSError, RecursionError):
                value = None
                reason = "EVENT_PARSE_OR_PAYLOAD_UNAVAILABLE"
        interval = SnapshotInterval(before.snapshot_id, after.snapshot_id) if (
            before and after and before.complete and after.complete) else None
        if value is not None:
            kind = raw.provider_type
            if kind in ("thread.started", "turn.started", "turn.completed", "completion.declared"):
                return None
            item = value.get("item")
            if (kind in ("item.started", "item.completed") and isinstance(item, dict)
                    and item.get("type") == "command_execution" and isinstance(item.get("id"), str)
                    and item["id"].strip()):
                code = item.get("exit_code")
                status = item.get("status")
                command = {"command_id": item["id"], "argv": None, "cwd": None,
                           "exit_code": code if type(code) is int else None, "stdout_ref": None,
                           "stderr_ref": None, "status": status if status in (
                               "in_progress", "completed", "failed", "declined") else None}
                category = "COMMAND" if kind == "item.started" else "COMMAND_RESULT"
                return self._record(raw.turn_id, raw.raw_id, category, command, raw=raw,
                                    interval=interval)
            if (kind == "file_access.observed" and interval and value.get("access_kind") == "read"
                    and isinstance(value.get("access_basis"), str) and value["access_basis"] in DIRECT_READ_BASES
                    and value.get("snapshot_id") == interval.before_snapshot_id == interval.after_snapshot_id
                    and public_path(value.get("path"))):
                return self._record(raw.turn_id, raw.raw_id, "FILE_ACCESS",
                                    {"path": value["path"], "access_kind": "read", "access_basis": value["access_basis"],
                                     "span": None}, raw=raw, snapshot=interval.after_snapshot_id)
            if (kind == "search.observed" and interval and
                    value.get("snapshot_id") == interval.before_snapshot_id == interval.after_snapshot_id):
                query, scope, digest = (value.get(k) for k in ("query", "scope", "result_digest"))
                if all(isinstance(x, str) and x.strip() for x in (query, scope, digest)):
                    if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                        result_ref = value.get("result_ref")
                        return self._record(raw.turn_id, raw.raw_id, "SEARCH",
                                            {"query": query, "scope": scope,
                                             "result_ref": result_ref if isinstance(result_ref, str) and result_ref.strip() else None,
                                             "result_digest": digest}, raw=raw,
                                            snapshot=interval.after_snapshot_id)
            reason = "EVENT_UNMAPPED"
        return self._record(raw.turn_id, raw.raw_id, "UNKNOWN",
                            {"provider_type": raw.provider_type, "reason_code": reason, "raw_ref": raw.raw_id},
                            raw=raw, interval=interval, status=ObservationStatus.UNKNOWN)

    def collect(self, turn: TurnResult, before: SnapshotRef | None, after: SnapshotRef | None,
                diff: DiffRecord | None, checks: tuple[CheckResult, ...], *,
                usage_mode=None, fresh_session: bool = False, stage=None, elapsed_ms=None) -> bool | None:
        accepted = set()
        records = []
        terminal = []
        for ref in dict.fromkeys(turn.event_refs):
            raw = self._raw.get(ref)
            if raw is None or raw.turn_id != turn.turn_id:
                raise ContractError("missing or foreign raw observation")
        expected = set(turn.event_refs)
        for raw in self._raw.values():
            if raw.raw_id not in expected:
                continue
            if raw.provider_event_id is not None:
                if raw.provider_event_id in accepted:
                    continue
                accepted.add(raw.provider_event_id)
            mapped = self._map_raw(raw, before, after)
            if mapped is not None:
                records.append(mapped)
            if raw.provider_type == "turn.completed":
                terminal.append(raw)
        declaration = None
        if after and after.complete and turn.error is None and turn.status == "COMPLETED":
            source = self._raw.get(turn.completion_source_ref)
            if (source and source.raw_id in turn.event_refs and source.provider_type == "completion.declared"
                    and source.parse_status in {"OK", "PARSED"} and turn.completion_basis_id):
                try:
                    payload = json.loads(self._read_payload(source.payload_ref))
                    if (isinstance(payload, dict) and payload.get("type") == "completion.declared"
                            and payload.get("declared") is turn.completion_declared
                            and payload.get("basis_id") == turn.completion_basis_id):
                        declaration = turn.completion_declared
                except (OSError, ValueError, RecursionError):
                    pass
            records.append(self._record(
                turn.turn_id, "completion", "COMPLETION",
                {"declared": declaration, "source_ref": turn.completion_source_ref if declaration is not None else None,
                 "basis_id": turn.completion_basis_id if declaration is not None else None},
                raw=source if declaration is not None else None, snapshot=after.snapshot_id,
                status=ObservationStatus.UNKNOWN if declaration is None else ObservationStatus.OBSERVED,
            ))
        if diff and diff.complete:
            if not before or not after or (diff.before_snapshot_id, diff.after_snapshot_id) != (
                    before.snapshot_id, after.snapshot_id):
                raise ContractError("diff does not connect the observed snapshots")
            for path in diff.changed_paths:
                records.append(self._record(
                    turn.turn_id, ["diff", path], "CHANGE", {
                        "diff_ref": diff.patch_ref, "before_snapshot_id": before.snapshot_id,
                        "after_snapshot_id": after.snapshot_id, "path": path,
                        "hunks": tuple(hunk for hunk in diff.hunks if hunk["path"] == path),
                    }, snapshot=after.snapshot_id,
                ))
        if usage_mode is not None:
            usage_values = None
            if (len(terminal) == 1 and terminal[0].parse_status in {"OK", "PARSED"}
                    and turn.status == "COMPLETED" and turn.error is None and turn.process_quiescent):
                try:
                    payload = json.loads(self._read_payload(terminal[0].payload_ref))
                    if isinstance(payload, dict) and payload.get("type") == "turn.completed":
                        usage_values = payload.get("usage")
                except (OSError, ValueError, RecursionError):
                    pass
            usage_id = "usage-" + turn.turn_id
            identity = canonical_json_bytes([usage_mode, turn.session_ref, fresh_session, [e.raw_id for e in terminal],
                                             usage_values, stage])
            if turn.turn_id in self._accounted_turns:
                previous, amount = self._accounted_turns[turn.turn_id]
                if identity != previous:
                    raise ContractError("conflicting usage for a previously observed turn")
            else:
                amount = self.usage.record(usage_mode, turn.session_ref, usage_values, fresh_session=fresh_session)
                values = amount or {}
                self.store.append("usage", UsageRecord(
                    usage_id, self.store.run_id, turn.turn_id, "runtime_terminal_event", usage_mode, stage,
                    elapsed_ms, None, None, "UNKNOWN", values.get("input_tokens"), values.get("cached_input_tokens"),
                    values.get("output_tokens"), values.get("reasoning_output_tokens"),
                    amount["input_tokens"] + amount["output_tokens"] if amount else None,
                    "observed" if amount is not None else "unknown", amount, terminal[0].raw_id if len(terminal) == 1 else None))
                self._accounted_turns[turn.turn_id] = identity, amount
            records.append(self._record(turn.turn_id, ["usage", usage_id], "USAGE", {"usage_record_id": usage_id},
                                        raw=terminal[0] if len(terminal) == 1 else None,
                                        status=ObservationStatus.UNKNOWN if amount is None else ObservationStatus.OBSERVED))
        for ref in dict.fromkeys(turn.usage_refs):
            records.append(self._record(turn.turn_id, ["usage", ref], "USAGE", {"usage_record_id": ref}))
        self.ledger.append_batch(records)
        for check in checks:
            if not after or check.snapshot_id != after.snapshot_id or check.turn_id != turn.turn_id:
                raise ContractError("public check is not bound to the observed turn and snapshot")
            self.collect_check(check, turn.turn_id)
        return declaration

    def intervention(self, turn_id, ref, strategy, triggers, anchors, snapshot_id, delivery):
        record = self._record(turn_id, ref, "INTERVENTION",
                              {"intervention_id": ref, "strategy_id": strategy, "delivery_status": delivery,
                               "trigger_ids": triggers, "anchor_ids": anchors}, snapshot=snapshot_id,
                              status=ObservationStatus.UNKNOWN if delivery == "UNKNOWN_DELIVERY" else ObservationStatus.OBSERVED)
        self.ledger.append_batch((record,))

    def collect_check(self, check: CheckResult, turn_id: str) -> None:
        if check.turn_id not in (None, turn_id):
            raise ContractError("public check belongs to another turn")
        bound = check.source_class == "TRUSTED_RUNNER"
        records = [self._record(
            turn_id, ["check", check.check_result_id], "TEST",
            {"check_result_id": check.check_result_id, "check_id": check.check_id,
             "test_id": None, "status": check.status, "command_id": None}, snapshot=check.snapshot_id,
            status=ObservationStatus.OBSERVED if bound else ObservationStatus.UNKNOWN,
        )]
        for case in check.test_cases:
            if bound and check.status == CheckStatus.FAIL and case.status == CheckStatus.FAIL:
                records.append(self._record(
                    turn_id, ["failure", check.check_result_id, case.test_id], "FAILURE",
                    {"check_result_id": check.check_result_id, "fingerprint": case.fingerprint,
                     "error_type": case.error_type, "normalized_message": case.message, "stable_frame": case.frame},
                    snapshot=check.snapshot_id,
                ))
        self.ledger.append_check(check, records)
