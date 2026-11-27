"""Build a read-only G2 review from public manifests and finished experiment receipts."""

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from aah.domain import (CheckResult, ContractError, EvaluationResult, RunAttempt, ScheduledSlot,
                        WireRecord, canonical_json_bytes, load_record, sha256_bytes,
                        validate_attempt_links, validate_evaluation_binding)
from aah.domain.checkpoint import CheckpointRecord
from aah.domain.report import RunReport
from aah.domain.wire import Digest, Identifier
from aah.experiments.runner import ExperimentManifest, pinned
from aah.reporting.store import read_report
from aah.workspace.patch import store_bytes, verified_bytes
from aah.workspace.snapshot import read_regular


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "benchmarks/registry_templates/main-candidates.json"
PILOT = ROOT / "results/s5-live/s5-dev-pilot-20261126"
CHECKPOINT = ROOT / "results/s4-verification/8ab8df62-951f-4727-8df4-74ecd1cf25a7"
QUALITY_CHECKS = frozenset(f"BQ{index:02}" for index in range(1, 15))
DESIGNS = (("Full", 24, 2), ("Core", 20, 2), ("Reduced", 12, 2), ("Emergency", 8, 1))


@dataclass(frozen=True)
class ReviewReceipt(WireRecord):
    task_id: Identifier
    task_family_id: Identifier
    snapshot_hash: Digest
    public_spec_hash: Digest
    public_checks_hash: Digest
    evaluator_manifest_hash: Digest
    environment_hash: Digest
    review_ref: Identifier
    author_ids: tuple[Identifier, ...]
    reviewer_id: Identifier
    checks: Mapping[Identifier, Literal["PASS", "REVIEWED_EXCEPTION"]]
    upstream_instance: Identifier | None

    def validate(self):
        if not self.author_ids or self.reviewer_id.strip().casefold() in {a.strip().casefold() for a in self.author_ids}:
            raise ContractError("BQ14 requires a reviewer independent of the authors")
        if self.checks.keys() != QUALITY_CHECKS or any(
            status == "REVIEWED_EXCEPTION" and name not in {"BQ05", "BQ06"}
            for name, status in self.checks.items()
        ):
            raise ContractError("incomplete or invalid benchmark quality evidence")


@dataclass(frozen=True)
class Candidate(WireRecord):
    candidate_id: Identifier
    source: Literal["OWN", "PUBLIC"]
    repo_id: Identifier | None
    primary_type: Literal["SINGLE", "DEPENDENCY", "REGRESSION", "COMPOSITE"]
    status: Literal["DRAFT", "BUILT", "VALIDATING", "APPROVED", "REJECTED", "LOCKED"]
    review: ReviewReceipt | None

    def validate(self):
        if self.status in {"APPROVED", "LOCKED"}:
            if not self.review or not self.repo_id:
                raise ContractError("approved candidate needs its repo and BQ review evidence")
            if self.source == "PUBLIC" and not self.review.upstream_instance:
                raise ContractError("approved public candidate needs a fixed upstream instance")


@dataclass(frozen=True)
class CandidateRegistry(WireRecord):
    candidates: tuple[Candidate, ...]


def audit_registry(path):
    raw = read_regular(path)[0]
    records = load_record(CandidateRegistry, raw).candidates
    if not records or len({r.candidate_id for r in records}) != len(records):
        raise ContractError("candidate IDs must be present and unique")
    approved = [r for r in records if r.status in {"APPROVED", "LOCKED"}]
    for field in ("task_id", "task_family_id", "snapshot_hash", "upstream_instance"):
        values = [getattr(r.review, field) for r in approved if getattr(r.review, field) is not None]
        if len(values) != len(set(values)):
            raise ContractError(f"overlapping Main candidates: {field}")
    return {
        "registry_sha256": sha256_bytes(raw),
        "status_counts": dict(Counter(r.status for r in records)),
        "approved_candidate_ids": sorted(r.candidate_id for r in approved),
        "approved_by_type": {kind: sum(r.primary_type == kind for r in approved)
                             for kind in ("SINGLE", "DEPENDENCY", "REGRESSION", "COMPOSITE")},
        "pending_candidate_ids": sorted(r.candidate_id for r in records
                                        if r.status in {"DRAFT", "BUILT", "VALIDATING"}),
    }


def document(path):
    return json.loads(read_regular(path)[0])


def audit_pilot(root):
    manifest_raw = read_regular(root / "experiment.json")[0]
    manifest = load_record(ExperimentManifest, manifest_raw)
    tasks = pinned(root, manifest.task_manifest_ref, manifest.task_manifest_hash)
    pinned(root, manifest.locked_config_ref, manifest.config_hash)
    pinned(root, manifest.runtime_probe_ref, manifest.runtime_probe_hash)
    slots = [ScheduledSlot(**slot) for slot in pinned(root, manifest.schedule_ref, manifest.schedule_hash)]
    summary_raw = read_regular(root / "summary.json")[0]
    summary = json.loads(summary_raw)
    expected = {(task, rep, condition) for task in tasks for condition in "ABCDE"
                for rep in range(1, manifest.repetitions + 1)}
    actual = {(s.task_id, s.repetition, s.condition) for s in slots}
    if (not expected or manifest.conditions != tuple("ABCDE")
            or summary["experiment_id"] != manifest.experiment_id
            or summary["manifest_hash"] != sha256_bytes(manifest_raw) or len(slots) != len(expected)
            or actual != expected or len(summary["runs"]) != len(slots)):
        raise ContractError("Pilot schedule and summary do not agree")

    attempts, results = [], []
    first_failures, repairs, typed = Counter(), Counter(), Counter()
    usage, time_ms = [], []
    for index, (slot, row) in enumerate(zip(slots, summary["runs"], strict=True), 1):
        if slot.order_index != index - 1 or slot.slot_id != f"slot-{index:04}":
            raise ContractError("Pilot slot ordering changed")
        directory = root / "slots" / slot.slot_id
        attempt = load_record(RunAttempt, read_regular(directory / "attempt.json")[0])
        payload = read_regular(directory / "evaluation.json")[0]
        evaluation = load_record(EvaluationResult, payload)
        report_bytes = read_report(root / manifest.output_root, attempt.run_id)
        report = load_record(RunReport, report_bytes)
        run_dir = root / manifest.output_root / attempt.run_id
        task = tasks[slot.task_id]
        meta = document(run_dir / "manifest.json")
        if (not attempt.selected_for_analysis or attempt.attempt_index != 1 or attempt.replacement_of
                or (attempt.run_id, attempt.slot_id) != (row["run_id"], row["slot_id"])
                or report.condition != row["condition"] or report.condition != slot.condition
                or (meta["task_id"], meta["experiment_id"], meta["config_hash"], meta["fixed_requirements_hash"])
                != (slot.task_id, manifest.experiment_id, manifest.config_hash, task["fixed_hash"])
                or sha256_bytes(report_bytes) != row["report_sha256"] or sha256_bytes(payload) != row["evaluation_sha256"]
                or evaluation.status != row["evaluation_status"] or evaluation.status not in {"PASS", "FAIL"}
                or evaluation.private_manifest_hash != task["evaluator_manifest_hash"]
                or evaluation.test_bundle_hash != task["review_suite_hash"]
                or evaluation.evaluation_environment_hash != manifest.environment_hash or not report.evaluable):
            raise ContractError(f"unverified Pilot artifacts for {slot.slot_id}")
        validate_evaluation_binding(evaluation, report.frozen_patch)
        verified_bytes(run_dir / "artifacts", "patches", evaluation.patch_hash)
        if read_regular(root / "independent" / attempt.run_id / "results" / (attempt.run_id + ".json"))[0] != payload:
            raise ContractError(f"independent verdict mismatch for {slot.slot_id}")
        tokens = report.usage_summary["observed_model_tokens"]
        if (report.turns_used != row["turns"] or report.recoveries_delivered != row["recoveries"]
                or tokens is None or tokens != row["model_tokens"] or report.elapsed_ms != row["run_elapsed_ms"]
                or evaluation.duration_ms != row["evaluation_ms"]):
            raise ContractError(f"Pilot usage or elapsed time mismatch for {slot.slot_id}")
        journal = run_dir / "interventions.jsonl"
        log = [json.loads(line) for line in read_regular(journal)[0].splitlines()] if journal.is_file() else []
        delivered = Counter(entry["intervention_id"] for entry in log if entry["event"] == "DELIVERED")
        if delivered.total() != report.recoveries_delivered or any(n != 1 for n in delivered.values()):
            raise ContractError(f"Pilot intervention mismatch for {slot.slot_id}")
        for entry in log:
            if (entry["event"] == "PACKAGE_READY" and entry["intervention_id"] in delivered
                    and entry["strategy"] != "GENERIC_RETRY"):
                typed[slot.condition] += 1
        checks = [load_record(CheckResult, line) for line in read_regular(run_dir / "checks.jsonl")[0].splitlines()]
        first_failures[slot.condition] += any(check.turn_id == "turn-0001" and check.status == "FAIL"
                                             and check.source_class == "TRUSTED_RUNNER" for check in checks)
        repairs[slot.condition] += report.recoveries_delivered
        attempts.append(attempt)
        results.append(evaluation)
        usage.append(tokens)
        time_ms.append(report.elapsed_ms + evaluation.duration_ms)
    validate_attempt_links(slots, attempts, results)
    return {
        "experiment_id": manifest.experiment_id, "summary_sha256": sha256_bytes(summary_raw),
        "evaluated": len(results), "passed": sum(r.status == "PASS" for r in results),
        "first_public_failures_by_condition": {c: first_failures[c] for c in "ABCDE"},
        "recovery_turns_by_condition": {c: repairs[c] for c in "ABCDE"},
        "typed_recovery_deliveries": typed.total(),
        "total_model_tokens": sum(usage), "total_execution_and_evaluation_ms": sum(time_ms),
    }


def audit_synthetic_checkpoint(root):
    source_raw = read_regular(root / "source-plan.json")[0]
    source_plan = json.loads(source_raw)
    summary = document(root / "summary.json")
    plan = document(root / "branch-plan.json")
    source_slot, = [ScheduledSlot(**s) for s in source_plan["slots"]]
    if source_slot.slot_id != "source-1" or source_slot.slot_role != "CHECKPOINT_SOURCE":
        raise ContractError("unexpected synthetic source slot")
    attempt = load_record(RunAttempt, read_regular(root / "attempts" / (source_slot.slot_id + ".json"))[0])
    source = root / "runs" / attempt.run_id
    raw = read_regular(source / "checkpoint.json")[0]
    record = load_record(CheckpointRecord, raw)
    if (source_plan.get("synthetic") is not True or plan["parent_hash"] != sha256_bytes(source_raw)
            or plan["checkpoint_hash"] != sha256_bytes(raw) or record.source_run_id != attempt.run_id
            or record.task_id != source_slot.task_id or record.experiment_id != source_slot.experiment_id
            or record.config_hash != plan["parent_hash"]
            or summary["source_run_id"] != attempt.run_id or summary["checkpoint_id"] != record.checkpoint_id):
        raise ContractError("synthetic source evidence disagrees with checkpoint plan")
    source_report = load_record(RunReport, read_report(root / "runs", attempt.run_id))
    if source_report.condition != "B" or source_report.turns_used != 1 or source_report.recoveries_reserved:
        raise ContractError("synthetic source must freeze before external recovery")
    verified_bytes(source / "artifacts", "patches", record.workspace_bundle.patch_hash)
    verified_bytes(source, "common-evidence", record.common_evidence_hash)
    verified_bytes(source, "common-history", record.common_history_hash)
    verified_bytes(source, "triggers", record.frozen_trigger_hash)
    if [slot["condition"] for slot in plan["slots"]] != list("BCDE") or len(summary["branches"]) != 4:
        raise ContractError("synthetic checkpoint requires four paired branches")
    branches = []
    for slot, branch in zip(plan["slots"], summary["branches"], strict=True):
        if slot["slot_id"] != f'branch-{slot["condition"]}':
            raise ContractError("unexpected synthetic branch slot")
        receipt = load_record(RunAttempt, read_regular(root / "attempts" / (slot["slot_id"] + ".json"))[0])
        report = load_record(RunReport, read_report(root / "runs", receipt.run_id))
        run_dir = root / "runs" / receipt.run_id
        verdict_path = root / "independent" / receipt.run_id / "results" / (receipt.run_id + ".json")
        verdict = load_record(EvaluationResult, read_regular(verdict_path)[0])
        meta = document(run_dir / "manifest.json")
        steps = [json.loads(line) for line in read_regular(run_dir / "transitions.jsonl")[0].splitlines()]
        if (receipt.slot_id != slot["slot_id"] or receipt.run_id != branch["run_id"]
                or report.condition != branch["condition"] or report.condition != slot["condition"]
                or report.turns_used != 1 or report.recoveries_delivered != 1 or not report.evaluable
                or meta["checkpoint_branch"]["checkpoint_hash"] != plan["checkpoint_hash"]
                or meta["checkpoint_branch"]["source_run_id"] != record.source_run_id
                or meta["checkpoint_branch"]["checkpoint_id"] != record.checkpoint_id
                or meta["limits"] != asdict(record.remaining_limits) or meta["runtime"]["session_mode"] != "NEW_SESSION"
                or (verdict.task_id, verdict.status) != (record.task_id, branch["evaluation_status"])
                or branch["history_hash"] != record.common_history_hash
                or steps[0]["to_phase"] != "RESTORE_VERIFY" or steps[1]["snapshot_id"] != record.snapshot.snapshot_id
                or [step["reason_code"] for step in steps if step["to_phase"] == "RUN"] != ["RECOVERY"]):
            raise ContractError("synthetic branch did not share the approved external state")
        validate_evaluation_binding(verdict, report.frozen_patch)
        branches.append(report.condition)
    return {"synthetic": True, "checkpoint_hash": plan["checkpoint_hash"], "branches": branches,
            "source_run_id": record.source_run_id, "live_source_or_branch_verified": False}


def projections(pilot):
    count = pilot["evaluated"]
    return [
        {"design": name, "tasks": tasks, "repetitions": repetitions, "end_to_end_slots": slots,
         "projected_model_tokens": round(pilot["total_model_tokens"] * slots / count),
         "projected_execution_and_evaluation_ms": round(pilot["total_execution_and_evaluation_ms"] * slots / count)}
        for name, tasks, repetitions in DESIGNS
        for slots in [tasks * repetitions * len("ABCDE")]
    ]


def prepare_review(registry, pilot_dir, checkpoint_dir):
    candidates = audit_registry(registry)
    pilot = audit_pilot(pilot_dir)
    return {
        "schema_version": "1.0", "review_status": "REVIEW_REQUIRED", "benchmark": candidates,
        "pilot": pilot, "synthetic_checkpoint": audit_synthetic_checkpoint(checkpoint_dir),
        "end_to_end_projections_from_dev_pilot": projections(pilot),
        "project_credit_balance": None, "live_checkpoint_cost": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--pilot", type=Path, default=PILOT)
    parser.add_argument("--synthetic-checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        review = prepare_review(args.registry, args.pilot, args.synthetic_checkpoint)
        store_bytes(args.output.absolute().parent, args.output.name, canonical_json_bytes(review))
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, IndexError) as exc:
        print("G2_REVIEW_INPUT_ERROR", str(exc))
        return 2
    print("G2_REVIEW_PREPARED", args.output, f"approved_candidates={len(review['benchmark']['approved_candidate_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
