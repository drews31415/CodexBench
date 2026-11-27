"""Exercise the common checkpoint source and four fresh-session branches offline."""

from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from aah.domain import (Condition, ContractError, RunAttempt, ScheduledSlot, SessionMode, canonical_json_bytes,
                        dump_record, load_record, resolve_condition, sha256_bytes, validate_attempt_links)
from aah.domain.checkpoint import BranchRef, CheckpointRecord
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run
from aah.workspace.patch import store_bytes
from evaluation.smoke import fixture_request

from . import s1
from .public_checks import run_approved


class CheckpointAdapter(s1.SyntheticAdapter):
    def run_unit(self, turn, callback):
        (turn.workspace.root / "observed.txt").write_bytes(b"public source turn\n")
        return super().run_unit(turn, callback)

    def continue_unit(self, turn, callback):
        marker = turn.workspace.root / "observed.txt"
        if marker.read_bytes() != b"public source turn\n" or turn.session_ref is not None:
            raise ContractError("branch did not restore the common external state")
        marker.unlink()
        return super().continue_unit(turn, callback)


def verify(root, *, adapter_type=CheckpointAdapter):
    from .s4 import _evaluate

    root = Path(root).absolute()
    root.mkdir(parents=True, exist_ok=False)
    experiment_id = root.name
    config = replace(s1.CONFIG, session_mode=SessionMode.NEW_SESSION)
    source = replace(fixture_request("DEV-01"), policies=resolve_condition(Condition.B),
                     experiment_id=experiment_id, mode="research")
    initial = Limits(60_000, 3, 2, 100, 5_000)
    runs = root / "runs"
    slots = [ScheduledSlot(experiment_id, "checkpoint", "source-1", "DEV-01", "CHECKPOINT_SOURCE",
                           source.task_id, None, None, None, None, 0)]
    attempts, verdicts, rows = [], [], []
    parent = canonical_json_bytes({"schema_version": "1.0", "slots": slots, "branches": ["B", "C", "D", "E"],
                                   "eligibility": "first eligible before external recovery", "session": config.session_mode,
                                   "limits": asdict(initial), "runtime": asdict(config), "cap": 2048, "synthetic": True})
    store_bytes(root, "source-plan.json", parent)
    source = replace(source, config_hash=sha256_bytes(parent))
    with TemporaryDirectory(prefix="aah-s4-checkpoint-") as temp:
        kwargs = dict(config=config, public_root=source.repository.source_path, work_root=Path(temp),
                      results_root=runs, check=run_approved, supplement_cap=2048)
        def dispatch(slot, request, limits, **flags):
            adapter = adapter_type(runs)
            attempt = RunAttempt(slot.slot_id, request.run_id, 1, None, None, True)
            attempts.append(attempt)
            store_bytes(root, f"attempts/{slot.slot_id}.json", dump_record(attempt))
            result = run(request, runtime=adapter, limits=limits, **kwargs, **flags)
            return result, adapter.requests
        report, source_turns = dispatch(slots[0], source, initial, capture_checkpoint=True, baseline_checks=True)
        if (report.stop_reason != "COMPLETED" or report.turns_used != 1 or report.recoveries_reserved
                or [t.kind for t in source_turns] != ["INITIAL"]):
            raise ContractError("synthetic source did not stop at its first eligible failure")
        body = (runs / source.run_id / "checkpoint.json").read_bytes()
        record = load_record(CheckpointRecord, body)
        digest = sha256_bytes(body)
        for index, condition in enumerate((Condition.B, Condition.C, Condition.D, Condition.E), 1):
            slots.append(ScheduledSlot(experiment_id, "checkpoint", f"branch-{condition}", record.checkpoint_id,
                                       "CHECKPOINT_BRANCH", source.task_id, record.checkpoint_id, None, condition, 1, index))
        store_bytes(root, "branch-plan.json", canonical_json_bytes(
            {"schema_version": "1.0", "parent_hash": sha256_bytes(parent), "checkpoint_hash": digest, "slots": slots[1:]}))
        for slot in slots[1:]:
            branch = BranchRef(source.run_id, record.checkpoint_id, digest, slot.slot_id)
            req = replace(source, run_id=str(uuid4()), policies=resolve_condition(slot.condition))
            result, turns = dispatch(slot, req, Limits(**asdict(record.remaining_limits)), branch=branch)
            if (result.stop_reason != "COMPLETED" or result.public_check_status != "PASS"
                    or [t.kind for t in turns] != ["RECOVERY"] or not result.evaluable):
                raise ContractError(f"{slot.slot_id}: unexpected checkpoint outcome {result.stop_reason} {result.warnings}")
            verdict = _evaluate(root, slot, result, runs / req.run_id, False)
            verdicts.append(verdict)
            rows.append({"condition": slot.condition, "run_id": req.run_id, "model_turns": result.turns_used,
                         "model_tokens": result.usage_summary["observed_model_tokens"], "evaluation_status": verdict.status,
                         "snapshot_id": record.snapshot.snapshot_id, "history_hash": record.common_history_hash,
                         "message_hash": turns[0].message_hash})
    validate_attempt_links(slots, attempts, verdicts)
    summary = {"schema_version": "1.0", "source_run_id": source.run_id, "checkpoint_id": record.checkpoint_id,
               "source_model_tokens": report.usage_summary["observed_model_tokens"], "source_turns": report.turns_used,
               "source_gate": record.source_gate_key, "remaining_limits": asdict(record.remaining_limits), "branches": rows}
    store_bytes(root, "summary.json", canonical_json_bytes(summary))
    return summary
