"""Re-aggregate an end-to-end experiment from its raw records (EVALUATION_PROTOCOL §5–§13).

Read-only and model-free. Starts from the pinned schedule (left join), so unrun slots stay in
the denominators. Produces run_status.csv, metric_summary.csv, paired_contrasts.csv,
analysis_sets.json and analysis.json; re-running on the same inputs gives identical bytes (EP30).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from aah.domain import (ContractError, EvaluationResult, RunAttempt, ScheduledSlot, canonical_json_bytes,
                        load_record, sha256_bytes)
from aah.domain.report import RunReport
from aah.experiments.runner import ExperimentManifest, pinned
from aah.reporting.store import read_report
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, read_regular


CONDITIONS = ("A", "B", "C", "D", "E")
ANALYSIS_CONFIG = {
    "schema_version": "1.0",
    "analysis_seed": 20261126,
    "bootstrap": {"iterations": 10_000, "confidence": 0.95, "cluster_key": "task_family_id",
                  "interval": "percentile", "undefined_cps_policy": "withhold_finite_interval",
                  "min_clusters_for_interval": 2},
    "primary_contrasts": ["P-R1", "P-R3"],
    "secondary_contrasts": ["E-B", "C-B", "D-B", "E-C", "E-D", "E-A", "CTX_MAIN", "GRAPH_MAIN", "INTERACTION"],
    "p_r3_interval": "not_approved_exploratory",
    "primary_blocks": "complete",
    "optional_inference": {"significance_tests": None, "multiple_comparison": "holm", "noninferiority_margin": None},
    "missing": {"infrastructure_replacement_limit": None, "never_impute_cost_as_zero": True},
}
CONFIG_SHA256 = sha256_bytes(canonical_json_bytes(ANALYSIS_CONFIG))
# AMD-01 §8 (DL-031): no infrastructure replacement runs, no significance tests, no non-inferiority claim.
AMD01_ANALYSIS_CONFIG = {
    **ANALYSIS_CONFIG,
    "amendment": "AMD-01",
    "interpretation": "exploratory",
    "optional_inference": {"significance_tests": "none", "multiple_comparison": None, "noninferiority_margin": None},
    "missing": {"infrastructure_replacement_limit": 0, "never_impute_cost_as_zero": True},
}
AMD01_CONFIG_SHA256 = sha256_bytes(canonical_json_bytes(AMD01_ANALYSIS_CONFIG))
RUN_STATUS_FIELDS = ("slot_id", "experiment_id", "protocol", "task_id", "task_family_id", "repo_id", "condition",
                     "repetition", "selected_run_id", "disposition", "reason_code", "y", "stop_reason",
                     "evaluation_status", "patch_hash", "model_tokens", "token_coverage", "runtime_ms",
                     "evaluator_ms", "turns", "recoveries_delivered")
SIMPLE = {"E-B": ("E", "B"), "C-B": ("C", "B"), "D-B": ("D", "B"), "E-C": ("E", "C"), "E-D": ("E", "D"),
          "E-A": ("E", "A")}


# ---------------------------------------------------------------- dispositions (§5.2)

def resolve_disposition(attempt, report, verdict, not_run_reason=None):
    """Return (disposition, reason_code, y). Codes alone never exclude a slot."""
    if attempt is None:
        if not_run_reason == "PROJECT_BUDGET_EXHAUSTED":
            return "NOT_RUN_BUDGET", not_run_reason, None
        return "NOT_RUN_OTHER", not_run_reason or "NOT_STARTED", None
    if report is None:
        return "PENDING_REVIEW", "NO_FINAL_REPORT", None
    if verdict is None:
        return "PENDING_REVIEW", "NO_INDEPENDENT_VERDICT", None
    status = str(verdict.status)
    if status == "PASS":
        return "VALID_SUCCESS", None, 1
    if status == "FAIL":
        return "VALID_FAILURE", verdict.error_code or "FUNCTIONAL_FAILURE", 0
    if status == "NOT_EVALUABLE":
        return "VALID_FAILURE", verdict.error_code or "NO_SAFE_ARTIFACT", 0
    if status == "INFRA_ERROR":
        return "MISSING_INFRA", verdict.error_code or "EVALUATOR_ENV_ERROR", None
    return "PENDING_REVIEW", verdict.error_code or "UNRESOLVED_CAUSE", None


# ---------------------------------------------------------------- ingest (§10.1)

def ingest(root: Path, *, families: dict[str, str] | None = None, repos: dict[str, str] | None = None):
    """Left-join the pinned schedule to attempts, reports and verdicts; return rows and input hashes."""
    root = checked_path(root)
    inputs = {}

    def read(path: Path) -> bytes:
        data = read_regular(path)[0]
        inputs[path.relative_to(root).as_posix()] = sha256_bytes(data)
        return data

    manifest = load_record(ExperimentManifest, read(root / "experiment.json"))
    for ref in (manifest.task_manifest_ref, manifest.schedule_ref):
        read(root / ref)
    tasks = pinned(root, manifest.task_manifest_ref, manifest.task_manifest_hash)
    slots = [ScheduledSlot(**s) for s in pinned(root, manifest.schedule_ref, manifest.schedule_hash)]
    # Families and repositories pinned in the task manifest are the default cluster keys.
    families = families or {t: v["task_family_id"] for t, v in tasks.items() if isinstance(v, dict) and v.get("task_family_id")}
    repos = repos or {t: v["repo_id"] for t, v in tasks.items() if isinstance(v, dict) and v.get("repo_id")}
    rows, seen_runs = [], set()
    for slot in slots:
        slot_dir = root / "slots" / slot.slot_id
        attempt = report = verdict = None
        not_run = None
        if (slot_dir / "not_run.json").exists():
            not_run = json.loads(read(slot_dir / "not_run.json")).get("reason")
        if (slot_dir / "attempt.json").exists():
            attempt = load_record(RunAttempt, read(slot_dir / "attempt.json"))
            run_dir = root / manifest.output_root / attempt.run_id
            if (run_dir / "report.json").exists():
                read(run_dir / "report.json")
                report = load_record(RunReport, read_report(root / manifest.output_root, attempt.run_id))
            if (slot_dir / "evaluation.json").exists():
                verdict = load_record(EvaluationResult, read(slot_dir / "evaluation.json"))
        disposition, reason, y = resolve_disposition(attempt, report, verdict, not_run)
        if attempt is not None:
            if attempt.run_id in seen_runs or attempt.slot_id != slot.slot_id:
                raise ContractError(f"{slot.slot_id}: duplicate or foreign attempt")
            seen_runs.add(attempt.run_id)
            # §10.3: unbound or inconsistent records are reviewed, never silently selected.
            if report is not None and report.condition != slot.condition:
                disposition, reason, y = "PENDING_REVIEW", "CONDITION_MISMATCH", None
            if verdict is not None and (verdict.run_id != attempt.run_id or verdict.task_id != slot.task_id or (
                    report is not None and report.frozen_patch is not None
                    and verdict.patch_hash != report.frozen_patch.patch_hash)):
                disposition, reason, y = "PENDING_REVIEW", "BINDING_MISMATCH", None
        usage = report.usage_summary if report is not None else {}
        tokens = usage.get("observed_model_tokens") if report is not None else None
        rows.append({
            "slot_id": slot.slot_id, "experiment_id": slot.experiment_id, "protocol": slot.protocol,
            "task_id": slot.task_id, "task_family_id": (families or {}).get(slot.task_id, slot.task_id),
            "repo_id": (repos or {}).get(slot.task_id, slot.task_id), "condition": str(slot.condition),
            "repetition": slot.repetition, "selected_run_id": attempt.run_id if attempt else None,
            "disposition": disposition, "reason_code": reason, "y": y,
            "stop_reason": str(report.stop_reason) if report is not None else None,
            "evaluation_status": str(verdict.status) if verdict is not None else None,
            "patch_hash": verdict.patch_hash if verdict is not None else None,
            "model_tokens": tokens,
            "token_coverage": None if report is None else ("observed" if tokens is not None else "unknown"),
            "runtime_ms": report.elapsed_ms if report is not None else None,
            "evaluator_ms": verdict.duration_ms if verdict is not None else None,
            "turns": report.turns_used if report is not None else None,
            "recoveries_delivered": report.recoveries_delivered if report is not None else None,
        })
    if set(tasks) != {r["task_id"] for r in rows}:
        raise ContractError("schedule tasks differ from the pinned task manifest")
    return rows, dict(sorted(inputs.items())), manifest


# ---------------------------------------------------------------- metrics (§6–§8)

def condition_metrics(rows):
    out = {}
    for condition in CONDITIONS:
        group = [r for r in rows if r["condition"] == condition]
        if not group:
            continue
        valid = [r for r in group if r["y"] is not None]
        successes = sum(r["y"] for r in valid)
        missing = sum(r["y"] is None for r in group)
        by_task = defaultdict(list)
        for r in valid:
            by_task[r["task_id"]].append(r["y"])
        tokens = [r["model_tokens"] for r in valid]
        observed = [t for t in tokens if t is not None]
        complete = bool(valid) and len(observed) == len(valid)
        total_tokens = sum(observed) if complete else None
        runtimes = [r["runtime_ms"] for r in valid if r["runtime_ms"] is not None]
        cps_reason = ("NO_VALID_RUNS" if not valid else "INCOMPLETE_COVERAGE" if not complete
                      else "NO_SUCCESS" if not successes else None)
        out[condition] = {
            "planned": len(group), "attempted": sum(r["selected_run_id"] is not None for r in group),
            "valid": len(valid), "successes": successes, "failures": len(valid) - successes, "missing": missing,
            "tsr_task_mean": (sum(sum(v) / len(v) for v in by_task.values()) / len(by_task)) if by_task else None,
            "valid_run_rate": successes / len(valid) if valid else None,
            "planned_bounds": [successes / len(group), (successes + missing) / len(group)],
            "model_tokens_total": total_tokens, "known_token_subtotal": sum(observed),
            "token_coverage": f"{len(observed)}/{len(valid)}",
            "mean_tokens_per_valid_run": total_tokens / len(valid) if total_tokens is not None else None,
            "cps_tokens": total_tokens / successes if cps_reason is None else None, "cps_reason": cps_reason,
            "runtime_ms_total": sum(runtimes) if len(runtimes) == len(valid) and valid else None,
            "mean_runtime_ms": sum(runtimes) / len(runtimes) if runtimes else None,
            "evaluator_ms_total": sum(r["evaluator_ms"] for r in valid if r["evaluator_ms"] is not None),
        }
    return out


# ---------------------------------------------------------------- paired blocks and contrasts (§11)

def task_blocks(rows, required, *, require_cost=False):
    """Complete blocks: every planned repetition of every required condition is valid (and costed)."""
    planned = defaultdict(list)
    for r in rows:
        planned[(r["task_id"], r["condition"])].append(r)
    blocks, excluded = [], {}
    for task_id in sorted({r["task_id"] for r in rows}):
        cells = {c: planned.get((task_id, c), []) for c in required}
        problem = next((f"{c}:{'NOT_PLANNED' if not cell else 'MISSING' if any(r['y'] is None for r in cell) else 'COST'}"
                        for c, cell in cells.items()
                        if not cell or any(r["y"] is None for r in cell)
                        or (require_cost and any(r["model_tokens"] is None for r in cell))), None)
        if problem:
            excluded[task_id] = problem
            continue
        first = next(iter(cells.values()))[0]
        blocks.append({"task_id": task_id, "family": first["task_family_id"],
                       "ybar": {c: sum(r["y"] for r in cell) / len(cell) for c, cell in cells.items()},
                       "tokens": {c: (sum(r["model_tokens"] for r in cell) if require_cost else None)
                                  for c, cell in cells.items()},
                       "successes": {c: sum(r["y"] for r in cell) for c, cell in cells.items()}})
    return blocks, excluded


def _p(blocks, c):
    return sum(b["ybar"][c] for b in blocks) / len(blocks)


def _cps(blocks, c):
    successes = sum(b["successes"][c] for b in blocks)
    return None if successes == 0 else sum(b["tokens"][c] for b in blocks) / successes


CONTRASTS = {
    **{cid: ((a, b), "pp", (lambda a, b: lambda bl: _p(bl, a) - _p(bl, b))(a, b)) for cid, (a, b) in SIMPLE.items()},
    "CTX_MAIN": (("B", "C", "D", "E"), "pp",
                 lambda bl: ((_p(bl, "C") - _p(bl, "B")) + (_p(bl, "E") - _p(bl, "D"))) / 2),
    "GRAPH_MAIN": (("B", "C", "D", "E"), "pp",
                   lambda bl: ((_p(bl, "D") - _p(bl, "B")) + (_p(bl, "E") - _p(bl, "C"))) / 2),
    "INTERACTION": (("B", "C", "D", "E"), "pp",
                    lambda bl: _p(bl, "E") - _p(bl, "D") - _p(bl, "C") + _p(bl, "B")),
    "P-R3": (("C", "D", "E"), "pp", lambda bl: _p(bl, "E") - max(_p(bl, "C"), _p(bl, "D"))),
    "P-R1": (("B", "C"), "tokens_per_success",
             lambda bl: None if _cps(bl, "C") is None or _cps(bl, "B") is None else _cps(bl, "C") - _cps(bl, "B")),
}


def _round(value):
    return None if value is None else round(value, 10)


def _quantile(values, q):
    position = (len(values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def cluster_bootstrap(blocks, statistic, config=ANALYSIS_CONFIG):
    """Resample whole task families with replacement, keeping draw multiplicity (EP24)."""
    settings = config["bootstrap"]
    clusters = defaultdict(list)
    for block in blocks:
        clusters[block["family"]].append(block)
    keys = sorted(clusters)
    rng = random.Random(config["analysis_seed"])
    values, undefined = [], 0
    for _ in range(settings["iterations"]):
        sample = [block for key in rng.choices(keys, k=len(keys)) for block in clusters[key]]
        value = statistic(sample)
        if value is None:
            undefined += 1
        else:
            values.append(value)
    values.sort()
    alpha = 1 - settings["confidence"]
    interval = (_quantile(values, alpha / 2), _quantile(values, 1 - alpha / 2)) if values else (None, None)
    return interval, undefined


def compute_contrasts(rows, config=ANALYSIS_CONFIG):
    results = []
    for cid in config["primary_contrasts"] + config["secondary_contrasts"]:
        required, unit, statistic = CONTRASTS[cid]
        if not all(any(r["condition"] == c for r in rows) for c in required):
            continue
        blocks, excluded = task_blocks(rows, required, require_cost=unit == "tokens_per_success")
        record = {"contrast_id": cid, "conditions": list(required), "unit": unit, "n_tasks": len(blocks),
                  "n_clusters": len({b["family"] for b in blocks}), "excluded_tasks": excluded,
                  "effect": None, "ci_lower": None, "ci_upper": None, "undefined_replicates": None,
                  "interpretation_status": None}
        if not blocks:
            record["interpretation_status"] = "NO_COMPLETE_BLOCKS"
        else:
            record["effect"] = _round(statistic(blocks))
            if record["effect"] is None:
                record["interpretation_status"] = "UNDEFINED_POINT_NO_SUCCESS"
            elif cid == "P-R3" and config["p_r3_interval"] == "not_approved_exploratory":
                record["interpretation_status"] = "EXPLORATORY_NO_INTERVAL"
            elif record["n_clusters"] < config["bootstrap"]["min_clusters_for_interval"]:
                record["interpretation_status"] = "INSUFFICIENT_CLUSTERS"
            else:
                (low, high), undefined = cluster_bootstrap(blocks, statistic, config)
                record["undefined_replicates"] = undefined
                if undefined and unit == "tokens_per_success":
                    record["interpretation_status"] = "UNDEFINED_REPLICATES_INTERVAL_WITHHELD"
                else:
                    record.update(ci_lower=_round(low), ci_upper=_round(high), interpretation_status="ESTIMATED")
        results.append(record)
    return results


# ---------------------------------------------------------------- outputs (§10.2, EP30)

def _csv(rows, fields):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "" if row.get(k) is None else row[k] for k in fields})
    return buffer.getvalue().encode("utf-8")


def analyze(root: Path, *, families=None, repos=None, config=ANALYSIS_CONFIG) -> dict:
    rows, inputs, manifest = ingest(root, families=families, repos=repos)
    metrics = condition_metrics(rows)
    contrasts = compute_contrasts(rows, config)
    summary_rows = [{"metric_id": key, "condition": c, "value": value}
                    for c, values in metrics.items() for key, value in values.items()
                    if not isinstance(value, (list, dict))]
    contrast_fields = ("contrast_id", "unit", "n_tasks", "n_clusters", "effect", "ci_lower", "ci_upper",
                       "undefined_replicates", "interpretation_status")
    files = {
        "run_status.csv": _csv(rows, RUN_STATUS_FIELDS),
        "metric_summary.csv": _csv(summary_rows, ("metric_id", "condition", "value")),
        "paired_contrasts.csv": _csv(contrasts, contrast_fields),
        "analysis_sets.json": canonical_json_bytes({c["contrast_id"]: {
            "conditions": c["conditions"], "excluded_tasks": c["excluded_tasks"],
            "config_sha256": CONFIG_SHA256} for c in contrasts}),
    }
    result = {
        "schema_version": "1.0", "experiment_id": manifest.experiment_id, "config": config,
        "config_sha256": sha256_bytes(canonical_json_bytes(config)),
        "code_sha256": sha256_bytes(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
        "inputs": inputs, "input_set_sha256": sha256_bytes(canonical_json_bytes(inputs)),
        "dispositions": {d: sum(r["disposition"] == d for r in rows) for d in sorted({r["disposition"] for r in rows})},
        "conditions": metrics, "contrasts": contrasts,
        "outputs": {name: sha256_bytes(data) for name, data in files.items()},
    }
    files["analysis.json"] = canonical_json_bytes(result)
    result["files"] = files
    return result


def write(result: dict, output: Path) -> Path:
    output = checked_path(output, allow_missing=True)
    for name, data in result["files"].items():
        store_bytes(output, name, data)
    return output / "analysis.json"


def verify(root: Path, output: Path, **kwargs) -> bool:
    """EP30: recompute from raw records and compare with the saved outputs byte for byte."""
    fresh = analyze(root, **kwargs)
    return all(read_regular(output / name)[0] == data for name, data in fresh["files"].items())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True, help="runner root holding experiment.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--families", type=Path, help="JSON {task_id: task_family_id}")
    parser.add_argument("--verify", action="store_true", help="recompute and compare with --output")
    parser.add_argument("--amd01", action="store_true", help="use the AMD-01 (DL-031) analysis configuration")
    args = parser.parse_args()
    families = json.loads(args.families.read_text(encoding="utf-8")) if args.families else None
    config = AMD01_ANALYSIS_CONFIG if args.amd01 else ANALYSIS_CONFIG
    if args.verify:
        same = verify(args.experiment, args.output, families=families, config=config)
        print("ANALYSIS_REPRODUCED" if same else "ANALYSIS_MISMATCH", args.output)
        return 0 if same else 1
    path = write(analyze(args.experiment, families=families, config=config), args.output)
    print("ANALYSIS_WRITTEN", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
