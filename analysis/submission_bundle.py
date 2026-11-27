"""Public submission bundle: dashboard data, figures and recomputation files (model-free, read-only on results/).

Subcommands (run from the checkout root with ``PYTHONPATH=src;.``):

``build``               read the raw Main records under ``results/`` and write ``docs/submission/``
``check``               rebuild in memory and compare every output byte for byte (needs ``results/``)
``verify-public``       verify the committed public files alone: hash chain, byte-exact recomputation of the
                        frozen AMD-01 analysis from ``run_status.csv`` and the dashboard's embedded bundle
``record-verification`` run the model-free verification commands, store their outcome, then rebuild

Privacy rule: fields are copied by whitelist only. ``restricted/`` payloads (agent messages, command text and
output), snapshot blobs, patch bodies, curator kits and private evaluation material are never opened.
Nothing here starts a model, a Codex process or a container.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]
OUTPUT = Path("docs/submission")
BUNDLE_NAME = "data/aah-public-bundle.json"
RECORD_NAME = "data/verification-record.json"
DASHBOARD_NAME = "dashboard.html"
SCHEMA_VERSION = "1.0"
BUNDLE_ID = "aah-submission-public-v1"
CONDITIONS = ("A", "B", "C", "D", "E")
POLICY = {  # TECHNICAL_SPEC §4.3 — shown for reference; every run also records its own resolved policy.
    "A": {"label": "Vanilla", "initial_context": "NONE", "diagnosis": "NONE", "strategy": "NATIVE",
          "recovery_context": "NONE", "external_intervention": False},
    "B": {"label": "Flat Retry", "initial_context": "DEFAULT", "diagnosis": "FLAT", "strategy": "GENERIC",
          "recovery_context": "DEFAULT", "external_intervention": True},
    "C": {"label": "Context-only", "initial_context": "DCC", "diagnosis": "FLAT", "strategy": "GENERIC",
          "recovery_context": "DCC", "external_intervention": True},
    "D": {"label": "Graph-only", "initial_context": "DEFAULT", "diagnosis": "GRAPH", "strategy": "TYPED",
          "recovery_context": "DEFAULT", "external_intervention": True},
    "E": {"label": "Adaptive Harness", "initial_context": "DCC", "diagnosis": "GRAPH", "strategy": "TYPED",
          "recovery_context": "DCC", "external_intervention": True},
}
EXPERIMENTS = {
    "aah-main-emergency-v2": {
        "raw": "results/main-live/aah-main-emergency-v2", "analysis": "results/analysis/aah-main-emergency-v2-amd01",
        "freeze": "configs/main/aah-main-emergency-v2-freeze.json",
        "approval": "configs/main/aah-main-emergency-v2-approval.json",
        "public_dir": "data/main-v2", "validity": "VALID_EXPLORATORY", "role": "main_result",
        "log_ids": ["EL-20261127-035", "EL-20261127-036", "EL-20261127-037", "EL-20261127-038"],
        "report_ref": "docs/reports/RESULTS_ANALYSIS.md §2.5",
    },
    "aah-main-emergency-v1": {
        "raw": "results/main-live/aah-main-emergency-v1", "analysis": "results/analysis/aah-main-emergency-v1-amd01",
        "freeze": "configs/main/aah-main-emergency-v1-freeze.json",
        "approval": "configs/main/aah-main-emergency-v1-approval.json",
        "public_dir": "data/main-v1-invalid", "validity": "INVALID_INFRA_COMPROMISED", "role": "invalidated_history",
        "log_ids": ["EL-20261127-031", "EL-20261127-032", "EL-20261127-033", "EL-20261127-034"],
        "report_ref": "docs/reports/RESULTS_ANALYSIS.md §2.4",
    },
}
# S3 synthetic verification (docs/reports/S3_VERIFICATION.md): scripted adapter, zero model calls.
SYNTHETIC = {
    "root": "results/s3-verification",
    "runs": {"1b382372-a4b8-4697-817a-1f9a631f8ca0": "DEV-01, 첫 턴 완료 선언 후 실패",
             "cd8c853f-96d0-403a-8cbc-e69444f8f990": "S3-label, 같은 실패 반복",
             "975f9c54-81b4-444c-bdc5-f21db7b36c3e": "S3-label, 통과하던 검사 회귀"},
    "report_ref": "docs/reports/S3_VERIFICATION.md",
}
ANALYSIS_FILES = ("run_status.csv", "metric_summary.csv", "paired_contrasts.csv", "analysis_sets.json",
                  "analysis.json")
FORBIDDEN_KEYS = {"aggregated_output", "text", "command", "stdout", "stderr", "payload_ref", "diff_ref",
                  "raw_refs", "result_ref", "argv", "cwd", "session_ref", "stdout_ref", "stderr_ref"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]{2,}")
ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/Users/|/home/)")
ID_PREFIX = 16


class BundleError(RuntimeError):
    pass


# ---------------------------------------------------------------- reading

class Reader:
    """Reads files under the checkout and remembers the SHA-256 of every input (provenance)."""

    def __init__(self, root: Path):
        self.root = root
        self.inputs: dict[str, str] = {}

    def path(self, rel: str) -> Path:
        return self.root / rel

    def exists(self, rel: str) -> bool:
        return self.path(rel).is_file()

    def bytes(self, rel: str) -> bytes:
        path = self.path(rel)
        if "restricted" in Path(rel).parts or "blobs" in Path(rel).parts or "patches" in Path(rel).parts:
            raise BundleError(f"refusing to read non-public record: {rel}")
        data = path.read_bytes()
        self.inputs[rel] = hashlib.sha256(data).hexdigest()
        return data

    def json(self, rel: str, default=None):
        if not self.exists(rel):
            return default
        return json.loads(self.bytes(rel).decode("utf-8"))

    def jsonl(self, rel: str) -> list[dict]:
        if not self.exists(rel):
            return []
        return [json.loads(line) for line in self.bytes(rel).decode("utf-8").splitlines() if line.strip()]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short(value):
    return value[:ID_PREFIX] if isinstance(value, str) else value


def clip(value, limit=160):
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---------------------------------------------------------------- run extraction (whitelist)

def _event(record: dict) -> dict:
    p = record.get("payload") or {}
    kind = record.get("kind")
    event = {"seq": record.get("sequence"), "turn": record.get("turn_id"), "kind": kind,
             "observation": record.get("observation_status"), "at": record.get("observed_at")}
    if kind in ("COMMAND", "COMMAND_RESULT"):
        event.update(command_id=p.get("command_id"), state=p.get("status"), exit_code=p.get("exit_code"))
    elif kind == "TEST":
        event.update(check_id=p.get("check_id"), result=p.get("status"), test_id=p.get("test_id"))
    elif kind == "FAILURE":
        event.update(error_type=p.get("error_type"), message=clip(p.get("normalized_message")),
                     frame_present=p.get("stable_frame") is not None, fingerprint=short(p.get("fingerprint")))
    elif kind == "CHANGE":
        event.update(before=short(p.get("before_snapshot_id")), after=short(p.get("after_snapshot_id")),
                     hunks=[{k: h.get(k) for k in ("path", "old_start", "old_count", "new_start", "new_count")}
                            for h in p.get("hunks") or []])
    elif kind == "COMPLETION":
        event.update(declared=p.get("declared"))
    elif kind == "INTERVENTION":
        event.update(intervention_id=p.get("intervention_id"), strategy_id=p.get("strategy_id"),
                     trigger_ids=p.get("trigger_ids"), delivery_status=p.get("delivery_status"))
    elif kind == "UNKNOWN":
        event.update(provider_type=p.get("provider_type"), reason_code=p.get("reason_code"))
    elif kind == "USAGE":
        event.update(usage_id=p.get("usage_record_id"))
    return event


def _check(record: dict) -> dict:
    cases = record.get("test_cases") or []
    return {"turn": record.get("turn_id"), "check_id": record.get("check_id"), "status": record.get("status"),
            "duration_ms": record.get("duration_ms"), "parser_id": record.get("parser_id"),
            "source_class": record.get("source_class"), "warnings": record.get("warnings") or [],
            "cases": [{"test_id": c.get("test_id"), "status": c.get("status"), "error_type": c.get("error_type"),
                       "frame_present": c.get("frame") is not None} for c in cases]}


def _decision(d: dict) -> dict:
    return {"id": short(d.get("candidate_id")), "kind": d.get("kind"), "source": d.get("source"),
            "span": d.get("span"), "provenance": d.get("provenance"), "validity": d.get("validity"),
            "selected": d.get("selected"), "reason_code": d.get("reason_code"), "score": d.get("score"),
            "components": d.get("components"), "estimated_tokens": d.get("estimated_tokens"),
            "utility_density": d.get("utility_density"), "evidence_count": len(d.get("evidence_ids") or []),
            "merged": len(d.get("merged_origins") or [])}


def _context(record: dict) -> dict:
    return {"stage": record.get("stage"), "policy": record.get("policy"), "strategy_id": record.get("strategy_id"),
            "intervention_id": record.get("intervention_id"), "status": record.get("status"),
            "cap_tokens": record.get("cap_tokens"),
            "estimated_supplement_tokens": record.get("estimated_supplement_tokens"),
            "estimated_full_tokens": record.get("estimated_full_tokens"), "estimator_id": record.get("estimator_id"),
            "retrieval_version": record.get("retrieval_version"), "elapsed_ms": record.get("elapsed_ms"),
            "warnings": record.get("warnings") or [], "package_hash": record.get("package_hash"),
            "message_hash": record.get("message_hash"), "fixed_hash": record.get("fixed_hash"),
            "core_count": len(record.get("core_ids") or []), "selected_count": len(record.get("selected_ids") or []),
            "decisions": [_decision(d) for d in record.get("decisions") or []]}


def _interventions(lines: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for line in lines:
        iid = line.get("intervention_id")
        item = grouped.setdefault(iid, {"intervention_id": iid, "events": []})
        event = line.get("event")
        item["events"].append({"event": event, "turn": line.get("turn_id")})
        if event == "DECIDED":
            graph = line.get("graph")
            item.update(trigger_ids=line.get("trigger_ids"), strategy=line.get("strategy"),
                        checkpoint=short(line.get("checkpoint_key")), check_ids=line.get("check_ids"),
                        anchor_count=len(line.get("anchors") or []), targets=line.get("targets") or [],
                        graph=None if graph is None else {
                            "status": graph.get("status"), "fallback_reason": graph.get("fallback_reason"),
                            "build_ms": graph.get("build_ms"), "diagnosis_ms": graph.get("diagnosis_ms"),
                            "graph_id": graph.get("graph_id"), "execution_id": graph.get("execution_id"),
                            "repository_ids": graph.get("repository_ids"), "trace_id": graph.get("trace_id")})
        elif event == "PACKAGE_READY":
            d = line.get("directive") or {}
            item["message_hash"] = line.get("message_hash")
            item["directive"] = {"strategy_id": d.get("strategy_id"), "objective": clip(d.get("objective"), 240),
                                 "reason": d.get("reason"), "confidence_status": d.get("confidence_status"),
                                 "completion_observation": d.get("completion_observation"),
                                 "next_checks": d.get("next_checks"), "targets": d.get("targets") or [],
                                 "path_ref_count": len(d.get("path_refs") or []),
                                 "evidence_count": len(d.get("evidence_ids") or [])}
        elif event == "OUTCOME_OBSERVED":
            a = line.get("adherence") or {}
            item["outcome"] = {"turn": line.get("turn_id"), "turn_status": line.get("turn_status"),
                               "adherence_status": a.get("status"), "adherence_basis": a.get("basis")}
    return list(grouped.values())


def _node_label(node: dict) -> str:
    a = node.get("attributes") or {}
    kind = node.get("kind")
    if node.get("namespace") == "repo":
        if kind == "FILE":
            return a.get("path") or "?"
        return f"{a.get('path') or '?'}::{a.get('qualified_name') or '?'}"
    if kind == "TEST":
        return f"{a.get('check_id') or a.get('test_id') or 'test'} = {a.get('status') or '?'}"
    if kind in ("COMMAND", "COMMAND_RESULT"):
        tail = f" exit {a.get('exit_code')}" if kind == "COMMAND_RESULT" else f" {a.get('status') or ''}"
        return f"{a.get('turn_id') or ''} {a.get('command_id') or 'cmd'}{tail}".strip()
    if kind == "FAILURE":
        return f"{a.get('test_id') or a.get('check_id') or 'failure'} · {a.get('error_type') or '?'}"
    if kind == "CHANGE":
        return f"{a.get('turn_id') or ''} change {a.get('path') or ''}".strip()
    return kind or "?"


def _graph(reader: Reader, run_rel: str, refs: dict) -> dict | None:
    def load(gid):
        return reader.json(f"{run_rel}/graphs/{gid}.json") if gid else None

    coupled, trace = load(refs.get("graph_id")), load(refs.get("trace_id"))
    if coupled is None:
        return None
    execution = load(refs.get("execution_id"))
    repositories = [load(r) for r in refs.get("repository_ids") or []]
    ids = {n["node_id"] for n in coupled.get("nodes", [])} | {e["edge_id"] for e in coupled.get("edges", [])}
    if len({short(i) for i in ids}) != len(ids):
        raise BundleError(f"{run_rel}: node/edge id prefix collision")
    namespace = {n["node_id"]: n.get("namespace") for n in coupled.get("nodes", [])}
    nodes = [{"id": short(n["node_id"]), "namespace": n.get("namespace"), "kind": n.get("kind"),
              "label": _node_label(n), "observation": n.get("observation_status"),
              "span": (n.get("attributes") or {}).get("span"),
              "turn": (n.get("attributes") or {}).get("turn_id")} for n in coupled.get("nodes", [])]
    edges = [{"id": short(e["edge_id"]), "relation": e.get("relation"), "source": short(e.get("source_id")),
              "target": short(e.get("target_id")), "observation": e.get("observation_status"),
              "basis": (e.get("attributes") or {}).get("basis"),
              "cross": namespace.get(e.get("source_id")) != namespace.get(e.get("target_id"))}
             for e in coupled.get("edges", [])]
    out = {"status": coupled.get("status"), "builder_version": coupled.get("builder_version"),
           "nodes": sorted(nodes, key=lambda n: (n["namespace"] or "", n["kind"] or "", n["label"], n["id"])),
           "edges": sorted(edges, key=lambda e: (e["relation"] or "", e["source"], e["target"], e["id"])),
           "unresolved": [{"node": short(u.get("node_id")), "relation": u.get("relation"), "reason": u.get("reason")}
                          for u in coupled.get("unresolved") or []],
           "warnings": coupled.get("warnings") or [],
           "repository": [{"status": r.get("status"), "nodes": len(r.get("nodes", [])), "edges": len(r.get("edges", [])),
                           "warnings": len(r.get("warnings") or [])} for r in repositories if r],
           "execution": None if execution is None else {"status": execution.get("status"),
                                                         "nodes": len(execution.get("nodes", [])),
                                                         "edges": len(execution.get("edges", []))},
           "trace": None}
    if trace is not None:
        out["trace"] = {
            "anchor_ids": [short(a) for a in trace.get("anchor_ids") or []],
            "allowed_relations": trace.get("allowed_relations"), "depth_limit": trace.get("depth_limit"),
            "visit_limit": trace.get("visit_limit"), "branch_limit": trace.get("branch_limit"),
            "top_k": trace.get("top_k"), "visited_count": trace.get("visited_count"),
            "termination": trace.get("termination"), "truncated": trace.get("truncated"),
            "historical_count": len(trace.get("historical") or []),
            "candidates": [{"id": short(c.get("candidate_id")), "rank": c.get("rank"), "kind": c.get("kind"),
                            "path": c.get("path"), "qualified_name": c.get("qualified_name"), "score": c.get("score"),
                            "components": c.get("score_components"), "relations": c.get("relations"),
                            "observation": c.get("observation_status"), "localized": c.get("localized"),
                            "alternative": c.get("alternative"), "warnings": c.get("warnings") or []}
                           for c in trace.get("candidates") or []],
            "paths": [{"anchor": short(p.get("anchor_id")), "nodes": [short(n) for n in p.get("node_ids") or []],
                       "edges": [short(e) for e in p.get("edge_ids") or []], "relations": p.get("relations"),
                       "directions": p.get("directions"), "cost": p.get("cost")} for p in trace.get("paths") or []],
        }
    return out


def extract_run(reader: Reader, run_rel: str, *, verdict_rel: str | None = None) -> dict:
    manifest = reader.json(f"{run_rel}/manifest.json", {}) or {}
    report = reader.json(f"{run_rel}/report.json", {}) or {}
    patch = report.get("frozen_patch") or {}
    usage = report.get("usage_summary") or {}
    operator = reader.json(f"{run_rel}/operator.json")
    dispatch = []
    for turn in range(1, (report.get("turns_used") or 0) + 1):
        d = reader.json(f"{run_rel}/turn-{turn:04d}-dispatch.json")
        if d is not None:
            dispatch.append({"turn": d.get("turn_id"), "kind": d.get("kind"), "message_hash": d.get("message_hash")})
    interventions = _interventions(reader.jsonl(f"{run_rel}/interventions.jsonl"))
    for item in interventions:
        refs = item.get("graph")
        item["graph_detail"] = _graph(reader, run_rel, refs) if refs else None
    verdict = reader.json(verdict_rel) if verdict_rel else None
    checks = [_check(c) for c in reader.jsonl(f"{run_rel}/checks.jsonl")]
    first = [c["status"] for c in checks if c["turn"] == "turn-0001"]
    return {
        "run_id": report.get("run_id") or manifest.get("run_id"), "task_id": manifest.get("task_id"),
        "condition": manifest.get("condition") or report.get("condition"),
        "policy": manifest.get("policy"), "limits": manifest.get("limits"), "supplement_cap": manifest.get("supplement_cap"),
        "approved_check_ids": manifest.get("approved_check_ids"), "permission_profile_id": manifest.get("permission_profile_id"),
        "fixed_instruction_hash": manifest.get("fixed_instruction_hash"), "runtime": manifest.get("runtime"),
        "report": {"stop_reason": report.get("stop_reason"), "turns_used": report.get("turns_used"),
                   "recoveries_delivered": report.get("recoveries_delivered"),
                   "recoveries_reserved": report.get("recoveries_reserved"),
                   "public_check_status": report.get("public_check_status"), "elapsed_ms": report.get("elapsed_ms"),
                   "evaluable": report.get("evaluable"), "artifact_status": report.get("artifact_status"),
                   "completion_declared": report.get("completion_declared"), "warnings": report.get("warnings") or [],
                   "changed_paths": patch.get("changed_paths"), "patch_hash": patch.get("patch_hash"),
                   "observed_model_tokens": usage.get("observed_model_tokens")},
        "operator": None if operator is None else {
            "permission_gate": (operator.get("permission_gate") or {}).get("status"),
            "snapshot_gate": (operator.get("snapshot_gate") or {}).get("status"),
            "launch_attempts": operator.get("launch_attempts"), "workspace_cleaned": operator.get("workspace_cleaned")},
        "verdict": None if verdict is None else {k: verdict.get(k) for k in (
            "status", "functional_status", "regression_status", "instruction_status", "error_code", "duration_ms",
            "patch_hash")},
        "first_turn_public": None if not first else ("PASS" if all(s == "PASS" for s in first) else "FAIL"),
        "dispatch": dispatch,
        "transitions": [{"seq": t.get("sequence"), "from": t.get("from_phase"), "to": t.get("to_phase"),
                         "reason": t.get("reason_code"), "elapsed_ms": t.get("elapsed_ms"),
                         "intervention_id": t.get("intervention_id"), "strategy_id": t.get("strategy_id")}
                        for t in reader.jsonl(f"{run_rel}/transitions.jsonl")],
        "events": [_event(e) for e in reader.jsonl(f"{run_rel}/evidence.jsonl")],
        "checks": checks,
        "usage": [{"turn": u.get("turn_id"), "stage": u.get("stage"), "input_tokens": u.get("input_tokens"),
                   "cached_input_tokens": u.get("cached_input_tokens"), "output_tokens": u.get("output_tokens"),
                   "reasoning_tokens": u.get("reasoning_tokens"), "total_tokens": u.get("total_tokens"),
                   "elapsed_ms": u.get("elapsed_ms"), "coverage": u.get("coverage"), "cost_kind": u.get("cost_kind")}
                  for u in reader.jsonl(f"{run_rel}/usage.jsonl")],
        "overhead": [{"stage": o.get("stage"), "turn": o.get("turn_id"), "elapsed_ms": o.get("elapsed_ms")}
                     for o in reader.jsonl(f"{run_rel}/overhead.jsonl")],
        "context": [_context(c) for c in reader.jsonl(f"{run_rel}/context.jsonl")],
        "interventions": interventions,
    }


# ---------------------------------------------------------------- experiment level

def _typed_status_rows(data: bytes) -> list[dict]:
    """run_status.csv back to the row types main_analysis.ingest produces."""
    ints = ("repetition", "y", "model_tokens", "runtime_ms", "evaluator_ms", "turns", "recoveries_delivered")
    rows = []
    for raw in csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")):
        row = {k: (None if v == "" else v) for k, v in raw.items()}
        for key in ints:
            if row.get(key) is not None:
                row[key] = int(row[key])
        rows.append(row)
    return rows


def _derived(runs: dict, slots: list[dict]) -> dict:
    by_cell = {(s["task_id"], s["condition"]): runs.get(s["selected_run_id"]) for s in slots}
    tasks = sorted({s["task_id"] for s in slots})
    commands = Counter()
    for run in runs.values():
        for e in run["events"]:
            if e["kind"] == "COMMAND_RESULT":
                commands["total"] += 1
                commands["exit_0" if e.get("exit_code") == 0 else "exit_nonzero"] += 1

    def initial_hash(run):
        return run["dispatch"][0]["message_hash"] if run and run["dispatch"] else None

    def fixed(run):
        return run["fixed_instruction_hash"] if run else None

    def same_initial(task, a, b):
        left, right = initial_hash(by_cell.get((task, a))), initial_hash(by_cell.get((task, b)))
        return left is not None and left == right

    parity = {"tasks": len(tasks),
              "B_equals_D": sum(same_initial(t, "B", "D") for t in tasks),
              "C_equals_E": sum(same_initial(t, "C", "E") for t in tasks),
              "B_equals_C": sum(same_initial(t, "B", "C") for t in tasks),
              "fixed_instruction_same_A_to_E": sum(
                  len({fixed(by_cell.get((t, c))) for c in CONDITIONS}) == 1 and fixed(by_cell.get((t, "A"))) is not None
                  for t in tasks)}
    isolation = defaultdict(lambda: {"runs": 0, "graph_diagnoses": 0, "flat_diagnoses": 0, "context": Counter()})
    graph_use = Counter()
    recoveries = []
    for slot in slots:
        run = runs.get(slot["selected_run_id"])
        if run is None:
            continue
        cell = isolation[slot["condition"]]
        cell["runs"] += 1
        for t in run["transitions"]:
            if t["to"] == "DIAGNOSE":
                cell["graph_diagnoses" if t["reason"] == "GRAPH" else "flat_diagnoses"] += 1
        for c in run["context"]:
            cell["context"][f"{c['stage']}:{c['policy']}"] += 1
        for item in run["interventions"]:
            graph = item.get("graph")
            detail = item.get("graph_detail") or {}
            trace = detail.get("trace") or {}
            if graph:
                graph_use[f"{item.get('strategy')}|{graph.get('fallback_reason') or 'NONE'}"] += 1
            recoveries.append({
                "slot_id": slot["slot_id"], "task_id": slot["task_id"], "condition": slot["condition"],
                "run_id": run["run_id"], "intervention_id": item["intervention_id"],
                "trigger_ids": item.get("trigger_ids"), "strategy": item.get("strategy"),
                "diagnosis": "GRAPH" if graph else "FLAT",
                "graph_fallback": graph.get("fallback_reason") if graph else None,
                "graph_candidates": len(trace.get("candidates") or []) if graph else None,
                "cross_edges": sum(e["cross"] for e in detail.get("edges", [])) if graph else None,
                "delivered": any(e["event"] == "DELIVERED" for e in item["events"]),
                "first_turn_public": run["first_turn_public"], "final_public": run["report"]["public_check_status"],
                "stop_reason": run["report"]["stop_reason"], "verdict": (run["verdict"] or {}).get("status")})
    no_intervention_failures = [
        {"slot_id": s["slot_id"], "task_id": s["task_id"], "condition": s["condition"], "run_id": s["selected_run_id"],
         "first_turn_public": runs[s["selected_run_id"]]["first_turn_public"],
         "final_public": runs[s["selected_run_id"]]["report"]["public_check_status"],
         "stop_reason": s["stop_reason"], "verdict": s["evaluation_status"]}
        for s in slots if s["selected_run_id"] in runs and s["y"] == 0
        and not runs[s["selected_run_id"]]["interventions"]]
    pairs = []
    for t in tasks:
        for a, b in (("B", "D"), ("C", "E")):
            ra, rb = by_cell.get((t, a)), by_cell.get((t, b))
            if ra and rb:
                messages = [[d["message_hash"] for d in ra["dispatch"]], [d["message_hash"] for d in rb["dispatch"]]]
                pairs.append({"task_id": t, "pair": a + b, "same_initial_message": initial_hash(ra) == initial_hash(rb),
                              "same_all_messages": messages[0] == messages[1] and bool(messages[0]),
                              "turns": [len(messages[0]), len(messages[1])],
                              "first_turn": [ra["first_turn_public"], rb["first_turn_public"]],
                              "verdict": [(ra["verdict"] or {}).get("status"), (rb["verdict"] or {}).get("status")]})
    return {"commands": dict(commands), "parity": parity,
            "isolation": {c: {"runs": v["runs"], "graph_diagnoses": v["graph_diagnoses"],
                              "flat_diagnoses": v["flat_diagnoses"], "context": dict(sorted(v["context"].items()))}
                          for c, v in sorted(isolation.items())},
            "graph_use": dict(sorted(graph_use.items())), "recoveries": recoveries,
            "no_intervention_failures": no_intervention_failures,
            "identical_input_pairs": {
                "pairs": pairs, "n": len(pairs),
                "first_turn_diverged": sum(p["first_turn"][0] != p["first_turn"][1] for p in pairs),
                "verdict_diverged": sum(p["verdict"][0] != p["verdict"][1] for p in pairs),
                "verdict_diverged_all_messages_identical": [
                    {"task_id": p["task_id"], "pair": p["pair"], "turns": p["turns"], "verdict": p["verdict"]}
                    for p in pairs if p["verdict"][0] != p["verdict"][1] and p["same_all_messages"]]},
            "empty_patch_failures": sum(1 for s in slots if s["y"] == 0 and s["selected_run_id"] in runs
                                        and not runs[s["selected_run_id"]]["report"]["changed_paths"]),
            "public_pass_private_fail": sum(1 for s in slots if s["y"] == 0 and s["selected_run_id"] in runs
                                            and runs[s["selected_run_id"]]["report"]["public_check_status"] == "PASS")}


def extract_experiment(reader: Reader, experiment_id: str, spec: dict) -> tuple[dict, dict[str, bytes]]:
    raw, analysis_dir = spec["raw"], spec["analysis"]
    files = {name: reader.bytes(f"{analysis_dir}/{name}") for name in ANALYSIS_FILES}
    analysis = json.loads(files["analysis.json"].decode("utf-8"))
    for name, digest in analysis["outputs"].items():
        if sha256(files[name]) != digest:
            raise BundleError(f"{experiment_id}: {name} does not match analysis.json outputs")
    slots = _typed_status_rows(files["run_status.csv"])
    experiment = reader.json(f"{raw}/experiment.json")
    locked = (reader.json(f"{raw}/locked-config.json") or {}).get("data") or {}
    probe = (reader.json(f"{raw}/runtime-probe.json") or {}).get("data") or {}
    summary = reader.json(f"{raw}/summary.json") or {}
    tasks = (reader.json(f"{raw}/tasks.json") or {}).get("data") or {}
    # Freeze and approval records are optional so that a newly discovered (unregistered) experiment can be exported.
    freeze_bytes = reader.bytes(spec["freeze"]) if spec.get("freeze") else None
    approval_bytes = reader.bytes(spec["approval"]) if spec.get("approval") else None
    freeze = json.loads(freeze_bytes.decode("utf-8")) if freeze_bytes else {}
    approval = json.loads(approval_bytes.decode("utf-8")) if approval_bytes else {}
    runs = {}
    for slot in slots:
        run_id = slot["selected_run_id"]
        if run_id is None:
            continue
        run = extract_run(reader, f"{raw}/runs/{run_id}", verdict_rel=f"{raw}/slots/{slot['slot_id']}/evaluation.json")
        run["slot_id"] = slot["slot_id"]
        runs[run_id] = run
    budget = locked.get("budget") or {}
    out = {
        "experiment_id": experiment_id, "validity": spec["validity"], "role": spec["role"],
        "log_ids": spec["log_ids"], "report_ref": spec["report_ref"], "public_dir": spec["public_dir"],
        "meta": {
            "protocol": experiment.get("protocol"), "repetitions": experiment.get("repetitions"),
            "conditions": experiment.get("conditions"), "execution_order_seed": experiment.get("execution_order_seed"),
            "code_version": experiment.get("code_version"), "config_hash": experiment.get("config_hash"),
            "environment_hash": experiment.get("environment_hash"), "schedule_hash": experiment.get("schedule_hash"),
            "task_manifest_hash": experiment.get("task_manifest_hash"), "manifest_hash": summary.get("manifest_hash"),
            "model": locked.get("model"), "amendment": locked.get("amendment"),
            "analysis_config_sha256": locked.get("analysis_config_sha256"),
            "budget": {k: budget.get(k) for k in ("max_total_turns", "max_initial_turns", "max_recovery_turns",
                                                  "recovery_turns_per_eligible_slot", "wall_ms_per_run",
                                                  "native_turn_ms", "supplement_cap", "token_limit_mode",
                                                  "extra_payment_krw", "cleanup_reserve_ms", "max_transitions")},
            "model_calls_before_run": probe.get("model_calls_before_run"),
            "freeze": {"path": spec.get("freeze"), "sha256": sha256(freeze_bytes) if freeze_bytes else None,
                       "scope": freeze.get("scope"),
                       "code_files": (freeze.get("code") or {}).get("files"),
                       "inclusion_sha256": (freeze.get("inclusion") or {}).get("sha256"),
                       "access_refresh_sha256": (freeze.get("access_refresh") or {}).get("sha256")},
            "approval": {"path": spec.get("approval"), "sha256": sha256(approval_bytes) if approval_bytes else None,
                         "approved_at": approval.get("approved_at"),
                         "approved_freeze_sha256": approval.get("approved_freeze_sha256"),
                         "disclosed_limitations": approval.get("disclosed_limitations") or [],
                         "supersedes": approval.get("supersedes")},
            "analysis": {"analysis_json_sha256": sha256(files["analysis.json"]),
                         "config_sha256": analysis.get("config_sha256"), "code_sha256": analysis.get("code_sha256"),
                         "input_set_sha256": analysis.get("input_set_sha256"), "outputs": analysis.get("outputs"),
                         "bootstrap": (analysis.get("config") or {}).get("bootstrap"),
                         "analysis_seed": (analysis.get("config") or {}).get("analysis_seed")},
        },
        "dispositions": analysis.get("dispositions"),
        "conditions": analysis.get("conditions"),
        "contrasts": analysis.get("contrasts"),
        "tasks": {t: {"task_family_id": v.get("task_family_id"), "repo_id": v.get("repo_id")}
                  for t, v in sorted(tasks.items())},
        "slots": [{**s, "first_turn_public": (runs.get(s["selected_run_id"]) or {}).get("first_turn_public"),
                   "final_public": ((runs.get(s["selected_run_id"]) or {}).get("report") or {}).get("public_check_status"),
                   "changed_paths": len(((runs.get(s["selected_run_id"]) or {}).get("report") or {}).get("changed_paths") or [])}
                  for s in slots],
        "runs": dict(sorted(runs.items())),
    }
    out["derived"] = _derived(runs, slots)
    copies = {f"{spec['public_dir']}/{name}": data for name, data in files.items()}
    return out, copies


def extract_synthetic(reader: Reader) -> dict:
    runs = {}
    for run_id, scenario in SYNTHETIC["runs"].items():
        run_rel = f"{SYNTHETIC['root']}/{run_id}"
        if not reader.exists(f"{run_rel}/report.json"):
            continue
        run = extract_run(reader, run_rel)
        run["scenario"] = scenario
        runs[run_id] = run
    return {"label": "S3 합성 검증 (스크립트 어댑터, 모델 호출 0건, 라이브 실험 아님)",
            "report_ref": SYNTHETIC["report_ref"], "runs": runs}


# ---------------------------------------------------------------- bundle, privacy and rendering

def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


def assert_public(value) -> None:
    """Refuse a bundle that carries restricted fields, absolute local paths or e-mail addresses."""
    def walk(node, where):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in FORBIDDEN_KEYS:
                    raise BundleError(f"forbidden field {key!r} at {where}")
                walk(child, f"{where}.{key}")
        elif isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, f"{where}[{i}]")
        elif isinstance(node, str):
            if EMAIL.search(node) or ABSOLUTE.search(node):
                raise BundleError(f"non-public string at {where}")
    walk(value, "$")


def build_bundle(root: Path = CHECKOUT, *, record: dict | None = None) -> tuple[dict, dict[str, bytes], dict]:
    reader = Reader(root)
    experiments, copies = {}, {}
    for experiment_id, spec in EXPERIMENTS.items():
        if not reader.exists(f"{spec['raw']}/experiment.json"):
            raise BundleError(f"raw records missing for {experiment_id}: {spec['raw']} (results/ is local and restricted)")
        experiments[experiment_id], files = extract_experiment(reader, experiment_id, spec)
        copies.update(files)
    bundle = {
        "schema_version": SCHEMA_VERSION, "bundle_id": BUNDLE_ID,
        "privacy": {"rule": "whitelist", "never_read": ["runs/*/restricted/", "artifacts/blobs/", "artifacts/patches/",
                                                         "curator kits", "private evaluation specs and results"],
                    "id_prefix_chars": ID_PREFIX},
        "policy_table": POLICY,
        "experiments": experiments,
        "synthetic": extract_synthetic(reader),
        "verification": record,
    }
    bundle["sources"] = dict(sorted(reader.inputs.items()))
    assert_public(bundle)
    return bundle, copies, reader.inputs


def _embed(bundle_bytes: bytes) -> str:
    return bundle_bytes.decode("utf-8").replace("</", "<\\/").replace("<!--", "<\\!--").strip()


def render_outputs(root: Path = CHECKOUT) -> dict[str, bytes]:
    """Every file under docs/submission/ that build writes, keyed by path relative to docs/submission/."""
    from analysis import submission_figures

    record_path = root / OUTPUT / RECORD_NAME
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else None
    bundle, copies, _ = build_bundle(root, record=record)
    bundle_bytes = canonical(bundle)
    outputs = {BUNDLE_NAME: bundle_bytes, **copies}
    figures = submission_figures.render_all(bundle)
    for name, svg in figures.items():
        outputs[f"figures/{name}"] = svg.encode("utf-8")
    dashboard = root / "analysis/dashboard"
    template = (dashboard / "template.html").read_text(encoding="utf-8")
    page = (template.replace("{{STYLE}}", (dashboard / "style.css").read_text(encoding="utf-8").strip())
            .replace("{{APP}}", (dashboard / "app.js").read_text(encoding="utf-8").strip())
            .replace("{{TITLE}}", "제출 대시보드 (제출 시점 고정본)")
            .replace("{{SUBTITLE}}", "모든 화면은 저장된 실행 기록과 동결 분석 산출물에서 읽는다 · 오프라인")
            .replace("{{BUNDLE_SHA256}}", sha256(bundle_bytes))
            .replace("{{FIGURES}}", _embed(canonical(figures)))
            .replace("{{DATA}}", _embed(bundle_bytes)))
    outputs[DASHBOARD_NAME] = page.replace("\r\n", "\n").encode("utf-8")
    return outputs


def write_outputs(outputs: dict[str, bytes], root: Path = CHECKOUT) -> None:
    base = root / OUTPUT
    for rel, data in sorted(outputs.items()):
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def check(root: Path = CHECKOUT) -> list[str]:
    """Paths whose committed bytes differ from a fresh build (empty list = reproduced)."""
    base = root / OUTPUT
    return [rel for rel, data in sorted(render_outputs(root).items())
            if not (base / rel).is_file() or (base / rel).read_bytes() != data]


# ---------------------------------------------------------------- public verification (no results/ needed)

def extract_embedded(page: str) -> bytes:
    match = re.search(r'<script id="aah-data" type="application/json">(.*?)</script>', page, re.S)
    if not match:
        raise BundleError("dashboard has no embedded bundle")
    return (match.group(1).replace("<\\!--", "<!--").replace("<\\/", "</") + "\n").encode("utf-8")


def recompute_problems(experiment_id: str, files: dict[str, bytes], exp: dict) -> tuple[list[str], str | None]:
    """Recompute the frozen analysis from public run_status.csv and compare bytes; returns (problems, summary)."""
    from analysis import main_analysis

    problems = []
    analysis = json.loads(files["analysis.json"].decode("utf-8"))
    if sha256(files["analysis.json"]) != exp["meta"]["analysis"]["analysis_json_sha256"]:
        problems.append(f"{experiment_id}: analysis.json hash differs from the bundle")
    for name, digest in analysis["outputs"].items():
        if sha256(files[name]) != digest:
            problems.append(f"{experiment_id}: {name} hash differs from analysis.json outputs")
    config = (main_analysis.AMD01_ANALYSIS_CONFIG if analysis["config_sha256"] == main_analysis.AMD01_CONFIG_SHA256
              else main_analysis.ANALYSIS_CONFIG)
    if main_analysis.sha256_bytes(main_analysis.canonical_json_bytes(config)) != analysis["config_sha256"]:
        return problems + [f"{experiment_id}: analysis configuration not recognised"], None
    rows = _typed_status_rows(files["run_status.csv"])
    metrics = main_analysis.condition_metrics(rows)
    contrasts = main_analysis.compute_contrasts(rows, config)
    summary_rows = [{"metric_id": key, "condition": c, "value": value} for c, values in metrics.items()
                    for key, value in values.items() if not isinstance(value, (list, dict))]
    fields = ("contrast_id", "unit", "n_tasks", "n_clusters", "effect", "ci_lower", "ci_upper",
              "undefined_replicates", "interpretation_status")
    fresh = {"metric_summary.csv": main_analysis._csv(summary_rows, ("metric_id", "condition", "value")),
             "paired_contrasts.csv": main_analysis._csv(contrasts, fields)}
    for name, data in fresh.items():
        if data != files[name]:
            problems.append(f"{experiment_id}: recomputed {name} differs")
    if json.loads(main_analysis.canonical_json_bytes(metrics)) != analysis["conditions"]:
        problems.append(f"{experiment_id}: recomputed condition metrics differ from analysis.json")
    if json.loads(main_analysis.canonical_json_bytes(contrasts)) != analysis["contrasts"]:
        problems.append(f"{experiment_id}: recomputed contrasts differ from analysis.json")
    if exp["conditions"] != analysis["conditions"] or exp["contrasts"] != analysis["contrasts"]:
        problems.append(f"{experiment_id}: bundle numbers differ from analysis.json")
    if [s["selected_run_id"] for s in exp["slots"]] != [r["selected_run_id"] for r in rows]:
        problems.append(f"{experiment_id}: bundle slots differ from run_status.csv")
    return problems, (f"PUBLIC_RECOMPUTE {experiment_id} slots={len(rows)} "
                      f"bootstrap={config['bootstrap']['iterations']} analysis.json={sha256(files['analysis.json'])[:12]}")


def verify_public(root: Path = CHECKOUT) -> list[str]:
    """Return problems found using committed public files only (empty list = verified)."""
    base = root / OUTPUT
    problems, lines = [], []
    bundle_bytes = (base / BUNDLE_NAME).read_bytes()
    bundle = json.loads(bundle_bytes.decode("utf-8"))
    try:
        assert_public(bundle)
    except BundleError as error:
        problems.append(f"bundle privacy: {error}")
    for experiment_id, exp in bundle["experiments"].items():
        folder = base / exp["public_dir"]
        files = {name: (folder / name).read_bytes() for name in ANALYSIS_FILES}
        found, line = recompute_problems(experiment_id, files, exp)
        problems += found
        if line:
            lines.append(line)
    page = (base / DASHBOARD_NAME).read_text(encoding="utf-8")
    if extract_embedded(page) != bundle_bytes:
        problems.append("dashboard embedded data differs from aah-public-bundle.json")
    if re.search(r'(?:src|href)\s*=\s*["\']?(?:https?:)?//', page):
        problems.append("dashboard references an external resource")
    verify_public.lines = lines
    return problems


# ---------------------------------------------------------------- verification record

RECORDED_COMMANDS = (
    ("tests", [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]),
    ("freeze_v2", [sys.executable, "-m", "benchmarks.main_public.main_freeze", "--check"]),
    ("analysis_v2", [sys.executable, "-m", "analysis.main_analysis", "--experiment",
                     "results/main-live/aah-main-emergency-v2", "--output",
                     "results/analysis/aah-main-emergency-v2-amd01", "--amd01", "--verify"]),
    ("analysis_v1", [sys.executable, "-m", "analysis.main_analysis", "--experiment",
                     "results/main-live/aah-main-emergency-v1", "--output",
                     "results/analysis/aah-main-emergency-v1-amd01", "--amd01", "--verify"]),
)


def _test_counts(text: str) -> dict:
    tail = [line for line in text.splitlines() if re.search(r"\d+ (passed|failed|error)", line)]
    counts = {k: 0 for k in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")}
    if tail:
        for number, word in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)", tail[-1]):
            counts["errors" if word.startswith("error") else word] = int(number)
    return counts


def record_verification(root: Path = CHECKOUT) -> dict:
    # Keep caller-supplied PYTHONPATH entries (e.g. a verification-only site-packages providing Streamlit).
    extra = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and p not in ("src", ".")]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", ".", *extra]), "PYTHONUTF8": "1"}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    probe = subprocess.run([sys.executable, "-c", "import streamlit; print(streamlit.__version__)"], cwd=root, env=env,
                           capture_output=True, text=True)
    dashboard_runtime = f"streamlit {probe.stdout.strip()}" if probe.returncode == 0 else "streamlit not installed (dashboard app tests skip)"
    steps = []
    for step_id, argv in RECORDED_COMMANDS:
        started = time.monotonic()
        done = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        output = (done.stdout or "") + (done.stderr or "")
        last = [line for line in output.strip().splitlines() if line.strip()]
        step = {"id": step_id, "invocation": " ".join(["python", *argv[1:]]), "exit_code": done.returncode,
                "seconds": round(time.monotonic() - started, 1),
                "result_line": ABSOLUTE.sub("<path>/", last[-1].strip()) if last else None}
        if step_id == "tests":
            step["counts"] = _test_counts(output)
        steps.append(step)
    record = {"schema_version": SCHEMA_VERSION, "recorded_on": datetime.now(timezone.utc).date().isoformat(),
              "model_calls": 0, "git_head": head, "working_tree": "submission changes not yet committed",
              "python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
              "live_tests": "not run (tests/live requires separate approval; none collected by default)",
              "dashboard_runtime": dashboard_runtime,
              "steps": steps}
    path = root / OUTPUT / RECORD_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(record))
    return record


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("build", "check", "verify-public", "record-verification"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            outputs = render_outputs()
            write_outputs(outputs)
            print("SUBMISSION_BUNDLE_WRITTEN", f"{len(outputs)} files",
                  sha256(outputs[BUNDLE_NAME])[:12], OUTPUT.as_posix())
            return 0
        if args.command == "check":
            differ = check()
            print("SUBMISSION_BUNDLE_REPRODUCED" if not differ else "SUBMISSION_BUNDLE_DIFFERS " + " ".join(differ))
            return 0 if not differ else 1
        if args.command == "verify-public":
            problems = verify_public()
            for line in getattr(verify_public, "lines", []):
                print(line)
            for problem in problems:
                print("PROBLEM", problem)
            print("SUBMISSION_PUBLIC_VERIFIED" if not problems else "SUBMISSION_PUBLIC_MISMATCH")
            return 0 if not problems else 1
        record = record_verification()
        write_outputs(render_outputs())
        failed = [s["id"] for s in record["steps"] if s["exit_code"] != 0]
        print("VERIFICATION_RECORDED", "all steps passed" if not failed else "FAILED: " + ",".join(failed))
        return 0 if not failed else 1
    except BundleError as error:
        print("SUBMISSION_BUNDLE_BLOCKED", error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
