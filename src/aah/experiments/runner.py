"""Execute pinned development slots and join public reports to independent verdicts."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Literal
from uuid import uuid4

from aah.domain import (Condition, ContractError, EvaluationResult, RunAttempt, ScheduledSlot, WireRecord,
                        canonical_json_bytes, dump_record, load_record, sha256_bytes, validate_attempt_links,
                        validate_evaluation_binding)
from aah.domain.report import RunReport
from aah.domain.wire import Count, Digest, Identifier, RelativePath
from aah.reporting.store import read_report
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, read_regular


@dataclass(frozen=True)
class ExperimentManifest(WireRecord):
    experiment_id: Identifier
    protocol: Literal["end_to_end"]
    task_manifest_ref: RelativePath
    task_manifest_hash: Digest
    conditions: tuple[Condition, ...]
    repetitions: Count
    execution_order_seed: int
    locked_config_ref: RelativePath
    config_hash: Digest
    runtime_probe_ref: RelativePath
    runtime_probe_hash: Digest
    environment_hash: Digest
    code_version: Digest
    output_root: RelativePath
    schedule_ref: RelativePath
    schedule_hash: Digest


def plan(root, experiment_id, *, tasks, repetitions, seed, config, probe, environment_hash, code_version):
    root = checked_path(root, allow_missing=True)
    root.mkdir(parents=True, exist_ok=True)
    if not tasks or type(repetitions) != int or repetitions < 1:
        raise ContractError("experiment needs tasks and positive repetitions")
    slots = []
    rng = Random(seed)
    for task_id in sorted(tasks):
        conditions = list(Condition)
        rng.shuffle(conditions)
        for rep in range(repetitions):
            block = f"{task_id}-{rep + 1}"
            for condition in conditions:
                slots.append(ScheduledSlot(experiment_id, "end_to_end", f"slot-{len(slots)+1:04}", block,
                                           "END_TO_END", task_id, None, None, condition, rep + 1, len(slots)))
            conditions = conditions[1:] + conditions[:1]
    bodies = {}
    for ref, obj in (("tasks.json", tasks), ("locked-config.json", config), ("runtime-probe.json", probe),
                     ("schedule.json", slots)):
        bodies[ref] = canonical_json_bytes({"schema_version": "1.0", "data": obj})
    hashes = {name: sha256_bytes(data) for name, data in bodies.items()}
    manifest = ExperimentManifest(experiment_id, "end_to_end", "tasks.json", hashes["tasks.json"],
                                  tuple(Condition), repetitions, seed, "locked-config.json", hashes["locked-config.json"],
                                  "runtime-probe.json", hashes["runtime-probe.json"], environment_hash,
                                  code_version, "runs", "schedule.json", hashes["schedule.json"])
    for name, data in {**bodies, "experiment.json": dump_record(manifest)}.items():
        path = checked_path(root / name, allow_missing=True)
        try:
            with path.open("xb") as out:
                out.write(data)
        except FileExistsError:
            if read_regular(path)[0] != data:
                raise ContractError(f"pinned experiment input differs: {name}")
    return manifest


def pinned(root, ref, digest):
    raw = read_regular(root / ref)[0]
    if sha256_bytes(raw) != digest:
        raise ContractError("experiment input no longer matches its manifest")
    obj = json.loads(raw)
    if set(obj) != {"schema_version", "data"} or obj["schema_version"] != "1.0":
        raise ContractError("invalid experiment input envelope")
    return obj["data"]


def token_cps(rows):
    valid = [r for r in rows if r["evaluation_status"] in {"PASS", "FAIL"}]
    successes = sum(r["evaluation_status"] == "PASS" for r in valid)
    reason = ("NO_VALID_RUNS" if not valid else "NO_SUCCESS" if not successes else
              "INCOMPLETE_COVERAGE" if any(r["model_tokens"] is None for r in valid) else None)
    return {"successes": successes, "valid_runs": len(valid), "reason": reason,
            "tokens_per_success": None if reason else sum(r["model_tokens"] for r in valid) / successes}


def execute(root: Path, manifest: ExperimentManifest, *, launch: Callable, evaluate: Callable, verify_run=None):
    root = checked_path(root)
    if read_regular(root / "experiment.json")[0] != dump_record(manifest):
        raise ContractError("execution manifest mismatch")
    tasks = pinned(root, manifest.task_manifest_ref, manifest.task_manifest_hash)
    pinned(root, manifest.locked_config_ref, manifest.config_hash)
    pinned(root, manifest.runtime_probe_ref, manifest.runtime_probe_hash)
    slots = [ScheduledSlot(**s) for s in pinned(root, manifest.schedule_ref, manifest.schedule_hash)]
    matrix = {(task, rep, condition) for task in tasks for rep in range(1, 1 + manifest.repetitions)
              for condition in manifest.conditions}
    if (len(slots) != len(matrix) or {(s.task_id, s.repetition, s.condition) for s in slots} != matrix or
            any(s.slot_id != f"slot-{i+1:04}" or s.order_index != i or s.experiment_id != manifest.experiment_id
                or s.protocol != manifest.protocol or s.slot_role != "END_TO_END" or s.block_id != f"{s.task_id}-{s.repetition}"
                or s.checkpoint_id is not None or s.variant_id is not None
                for i, s in enumerate(slots))):
        raise ContractError("schedule does not describe the experiment")
    rows, attempts, evaluations = [], [], []
    for slot in slots:
        slot_root = checked_path(root / "slots" / slot.slot_id, allow_missing=True)
        slot_root.mkdir(parents=True, exist_ok=True)
        receipt = slot_root / "attempt.json"
        if not receipt.exists():
            run_id = str(uuid4())
            def claim():
                # This creation precedes any model dispatch and is exclusive across operators.
                with receipt.open("xb") as out:
                    out.write(dump_record(RunAttempt(slot.slot_id, run_id, 1, None, None, True)))
            launch(slot, run_id, claim)
        attempt = load_record(RunAttempt, read_regular(receipt)[0])
        if attempt.slot_id != slot.slot_id:
            raise ContractError("attempt slot mismatch")
        attempts.append(attempt)
        run_root = root / manifest.output_root / attempt.run_id
        if not (run_root / "report.json").exists():
            raise ContractError(f"{slot.slot_id} has a reserved attempt without a final report: {attempt.run_id}")
        report = load_record(RunReport, read_report(root / manifest.output_root, attempt.run_id))
        if report.stop_reason == "SAFETY_VIOLATION":
            raise ContractError(f"{slot.slot_id} requires isolation review")
        if verify_run is not None:
            verify_run(slot, report, run_root)
        meta = json.loads(read_regular(run_root / "manifest.json")[0])
        task = tasks[slot.task_id]
        if (report.condition != slot.condition or (meta["task_id"], meta["experiment_id"], meta["config_hash"],
                meta["initial_commit"], meta["fixed_requirements_hash"])
                != (slot.task_id, manifest.experiment_id, manifest.config_hash, task["base"], task["fixed_hash"])):
            raise ContractError("report and scheduled attempt differ")
        result_path = slot_root / "evaluation.json"
        saved = result_path.exists()
        verdict = (load_record(EvaluationResult, read_regular(result_path)[0]) if saved
                   else evaluate(slot, report, run_root))
        if ((verdict.run_id, verdict.task_id) != (report.run_id, slot.task_id)
                or verdict.private_manifest_hash not in (None, task["evaluator_manifest_hash"])):
            raise ContractError("verdict has a foreign run, task or evaluation manifest")
        if report.frozen_patch:
            validate_evaluation_binding(verdict, report.frozen_patch)
        if not saved:
            store_bytes(slot_root, "evaluation.json", dump_record(verdict))
        evaluations.append(verdict)
        tokens = report.usage_summary["observed_model_tokens"]
        rows.append({"slot_id": slot.slot_id, "condition": slot.condition, "run_id": report.run_id,
                     "public_check_status": report.public_check_status, "stop_reason": report.stop_reason,
                     "artifact_status": report.artifact_status, "evaluable": report.evaluable,
                     "evaluation_status": verdict.status, "evaluation_error": verdict.error_code,
                     "patch_hash": verdict.patch_hash, "evaluation_ms": verdict.duration_ms,
                     "report_sha256": sha256_bytes(dump_record(report)), "evaluation_sha256": sha256_bytes(dump_record(verdict)),
                     "model_tokens": tokens, "token_coverage": "observed" if tokens is not None else "unknown",
                     "known_token_subtotal": report.usage_summary["known_model_tokens_subtotal"],
                     "cost_kind": "UNKNOWN", "cost_amount": None, "currency": None,
                     "run_elapsed_ms": report.elapsed_ms, "turns": report.turns_used, "recoveries": report.recoveries_delivered})
    validate_attempt_links(slots, attempts, evaluations)
    summary = {"schema_version": "1.0", "experiment_id": manifest.experiment_id,
               "manifest_hash": sha256_bytes(dump_record(manifest)), "runs": rows,
               "token_cps": {c: token_cps([r for r in rows if r["condition"] == c]) for c in manifest.conditions}}
    store_bytes(root, "summary.json", canonical_json_bytes(summary))
    return summary
