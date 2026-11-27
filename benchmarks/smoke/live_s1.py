"""The single-use, operator-approved DEV-01 runtime for the S1 workflow."""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from aah.domain import (
    CancelResult, CleanupResult, ContractError, ErrorInfo, InterventionBoundary, Phase, RawEvent,
    RuntimeCapabilities, TurnResult, UnitStatus, UsageMode, canonical_json_bytes,
)
from aah.workspace.snapshot import checked_path


APPROVAL_EXPIRY = datetime(2026, 11, 27, 14, 59, tzinfo=timezone.utc)
APPROVED_INPUT = "72529d0ad3c1e6179a702aa7c4193a55a52da366c5068b0d6c169467a2cebae6"
RESERVATION_NAME = "dev01-s1-approval-20261126.json"


def validate_approval(now: datetime) -> None:
    if now >= APPROVAL_EXPIRY:
        raise ContractError("The DEV-01 approval expired at 2026-11-27 23:59 KST")


class Dev01EventRuntime:
    def __init__(self, request, workspace, directory: Path, config, launch, summarize,
                 known_events, safe_to_finalize, *, now=lambda: datetime.now(timezone.utc)):
        self.request = request
        self.workspace = workspace
        self.directory = directory
        self.config = config
        self.launch = launch
        self.summarize = summarize
        self.known_events = known_events
        self.safe_to_finalize = safe_to_finalize
        self.now = now
        self.opened = False
        self.used = False

    def _events(self, turn_id, stdout, on_event):
        refs = []
        for number, line in enumerate(stdout.splitlines(), start=1):
            if not line.strip():
                continue
            kind, parse = None, "UNKNOWN"
            try:
                value = json.loads(line)
                if isinstance(value, dict) and isinstance(value.get("type"), str):
                    kind = value["type"] if value["type"] in self.known_events else None
                    parse = "PARSED" if kind else "UNKNOWN"
            except (ValueError, RecursionError):
                pass
            raw_id = f"{turn_id}-raw-{number:05}"
            ref = f"restricted/{raw_id}.json"
            path = checked_path(self.directory / ref, allow_missing=True)
            path.parent.mkdir(exist_ok=True)
            with path.open("xb") as output:
                output.write(line)
            timestamp = self.now().isoformat().replace("+00:00", "Z")
            on_event(RawEvent(self.request.run_id, turn_id, raw_id, number, timestamp, None, kind, ref, parse))
            refs.append(raw_id)
        return tuple(refs)

    def outcome(self, request, result, on_event):
        summary = self.summarize(result.stdout)
        refs = self._events(request.turn_id, result.stdout, on_event)
        quiet = result.remaining_process_count == 0 and self.safe_to_finalize()
        counts = summary["event_counts"]
        complete = (result.status == "EXITED" and result.exit_code == 0 and summary["thread_id"]
                    and counts.get("thread.started") == 1 and counts.get("turn.started") == 1
                    and counts.get("turn.completed") == 1 and not counts.get("turn.failed"))
        status = {"TIMED_OUT": UnitStatus.TIMEOUT, "CANCELLED": UnitStatus.INTERRUPTED}.get(
            result.status, UnitStatus.COMPLETED if complete else UnitStatus.FAILED)
        error = None if status != UnitStatus.FAILED else ErrorInfo(
            next(iter(summary["failure_codes"]), "CODEX_TURN_INCOMPLETE"), Phase.RUN,
            "Codex did not finish the approved turn", False, None)
        return TurnResult(request.turn_id, status, summary["thread_id"], None, None, None, False,
                          result.exit_code, refs, (), error, quiet)

    def cancel(self, run_id):
        quiet = run_id == self.request.run_id and self.safe_to_finalize()
        return CancelResult(quiet, 0 if quiet else None, None)

    def close_run(self, run_id):
        quiet = run_id == self.request.run_id and self.safe_to_finalize()
        return CleanupResult(quiet, 0 if quiet else None, None)


class Dev01Runtime(Dev01EventRuntime):
    def probe_capabilities(self, config):
        if config != self.config:
            raise ContractError("Unapproved DEV-01 runtime settings")
        return RuntimeCapabilities("codex_cli", config.runtime_version, config.model_id,
                                   InterventionBoundary.UNSUPPORTED, (), {}, UsageMode.CUMULATIVE, "operator.json")

    def open_run(self, request, workspace):
        if (self.opened or request != self.request or workspace != self.workspace or request.task_id != "DEV-01"
                or request.policies.condition != "A" or request.policies.external_intervention
                or not self.safe_to_finalize()):
            raise ContractError("Unapproved DEV-01 workspace or condition")
        validate_approval(self.now())
        self.opened = True

    def run_unit(self, request, on_event):
        started = monotonic()
        if (not self.opened or self.used or request.kind != "INITIAL" or request.workspace != self.workspace
                or request.run_id != self.request.run_id or request.session_ref is not None
                or request.message_hash != APPROVED_INPUT or request.deadline_ms_remaining <= 0
                or not self.safe_to_finalize()):
            raise ContractError("The requested turn is outside the one-shot DEV-01 approval")
        try:
            now = self.now()
            validate_approval(now)
            reservation = checked_path(self.directory.parent / RESERVATION_NAME, allow_missing=True)
            with reservation.open("xb") as output:
                output.write(canonical_json_bytes({"run_id": request.run_id,
                                                   "reserved_at": now.isoformat(),
                                                   "prompt_sha256": request.message_hash}))
        except FileExistsError:
            return TurnResult(request.turn_id, UnitStatus.FAILED, None, None, None, None, False, None, (), (),
                              ErrorInfo("DEV01_APPROVAL_ALREADY_USED", Phase.RUN, "DEV-01 approval already used",
                                        False, None), True)
        self.used = True
        remaining = min((APPROVAL_EXPIRY - self.now()).total_seconds(),
                        request.deadline_ms_remaining / 1000 - (monotonic() - started))
        if remaining <= 0:
            return TurnResult(request.turn_id, UnitStatus.TIMEOUT, None, None, None, None, False, None, (), (), None, True)
        result = self.launch(request.message.encode("utf-8"), remaining)
        return self.outcome(request, result, on_event)

    def continue_unit(self, request, on_event):
        raise ContractError("DEV-01 approval does not allow follow-up")

