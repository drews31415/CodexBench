"""Decide AMD-01 inclusion, then build, reproduce and gate the Emergency Main freeze (IP31).

1. ``--include`` merges the development objective report, the curator report and the AI-review
   summary into ``configs/main/amd01-inclusion.json`` under the fixed AMD-01 rules (DL-031).
2. ``--write`` builds the deterministic freeze manifest for the included tasks only.
3. ``--check`` regenerates it and compares bytes; ``--status`` lists unmet lock conditions.

The live Main command refuses to start any Codex process unless ``validate_for_execution``
returns. The earlier single-task draft ``aah-s03-main-v1`` is superseded by DL-031.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from aah.domain import ContractError, Condition, canonical_json_bytes, dump_record, sha256_bytes
from aah.experiments import runner
from aah.workspace.snapshot import read_regular
from analysis import main_analysis
from benchmarks.main_public import access_check, amd01_variants, main_runner, public_runner
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import live_dev01
from evaluation import main_curator, source_suites


CHECKOUT = main_runner.CHECKOUT
AMENDMENT = CHECKOUT / "docs/project/AMENDMENT_01_AI_REVIEW_EMERGENCY_MAIN.md"
AMENDMENT_SHA256 = "8e9020f8caf61cb99d115aa70b63540023db97c2ca23f1a13ef214568e951f73"
INCLUSION = CHECKOUT / "configs/main/amd01-inclusion.json"
FREEZE = CHECKOUT / "configs/main/aah-main-emergency-v1-freeze.json"
APPROVAL = CHECKOUT / "configs/main/aah-main-emergency-v1-approval.json"
FREEZE_ID = EXPERIMENT_ID = "aah-main-emergency-v1"
CANDIDATES = tuple(sorted(amd01_variants.VARIANTS))
MIN_TASKS = 5
SEED = 20261127
RUNTIME_VERSION = "0.166.0"
# Dev Pilot per-slot limits carried over unchanged (AMD-01 §3); totals scale with the task count.
SLOT_LIMITS = {"initial_turns_per_slot": 1, "recovery_turns_per_eligible_slot": 2, "wall_ms_per_run": 360_000,
               "native_turn_ms": 105_000, "cleanup_reserve_ms": 15_000, "max_transitions": 100,
               "supplement_cap": 2048, "extra_payment_krw": 0, "profile": "existing-chatgpt-subscription",
               "token_limit_mode": "observe_only"}
CODE_ROOTS = ("src/aah", "benchmarks/smoke", "benchmarks/main_public", "evaluation")
CODE_FILES = ("analysis/main_analysis.py",)
DEV_CHECKS = ("BQ07", "BQ09", "BQ10", "BQ12", "BQ13", "alternatives_public", "partials_count")
CURATOR_CHECKS = ("BQ02", "BQ03", "BQ05", "BQ08")
REVIEW_ITEMS = ("BQ01", "BQ04", "BQ05", "BQ06", "BQ11")


class FreezeGateError(ContractError):
    def __init__(self, unmet: list[str]):
        super().__init__("Main freeze is not executable: " + ", ".join(unmet))
        self.unmet = unmet


def _display(path: Path) -> str:
    path = Path(path)
    return path.relative_to(CHECKOUT).as_posix() if path.is_relative_to(CHECKOUT) else path.name


def _read_json(path: Path) -> tuple[dict, str]:
    data = read_regular(path)[0]
    return json.loads(data), sha256_bytes(data)


def budget_for(task_count: int) -> dict:
    slots = task_count * len(Condition)
    recovery = task_count * (len(Condition) - 1) * SLOT_LIMITS["recovery_turns_per_eligible_slot"]
    return {**SLOT_LIMITS, "max_initial_turns": slots, "max_recovery_turns": recovery,
            "max_total_turns": slots + recovery}


# ---------------------------------------------------------------- inclusion (AMD-01 §5–§6)

FIX_ELIGIBLE = {"BQ05_FAIL", "UNJUSTIFIED_ALTERNATIVE_FAILURE", "REVIEW_BQ04_FAIL", "REVIEW_BQ05_FAIL",
                "REVIEW_BQ06_FAIL"}


def _suite_fixed(c: dict | None) -> bool:
    return bool(c and c.get("before_hashes") is not None and c["before_hashes"] != c.get("kit_hashes"))


def _base_reasons(d: dict | None, c: dict | None, r: dict) -> list[str]:
    reasons = []
    if d is None:
        reasons.append("DEV_REPORT_MISSING")
    else:
        reasons += [f"{k}_FAIL" for k in DEV_CHECKS if d["checks"].get(k) != "PASS"]
    if c is None:
        reasons.append("CURATOR_REPORT_MISSING")
    else:
        reasons += [f"{k}_FAIL" for k in CURATOR_CHECKS if c["checks"].get(k) != "PASS"]
        justified = r.get("failing_alternatives_justified", {})
        if [n for n in c["alternatives_failed"] if not justified.get(n)]:
            reasons.append("UNJUSTIFIED_ALTERNATIVE_FAILURE")
    if r.get("status") != "RECORDED":
        reasons.append(f"REVIEW_{r.get('status', 'MISSING')}")
    else:
        reasons += [f"REVIEW_{k}_FAIL" for k in REVIEW_ITEMS if r["verdicts"].get(k) != "PASS"]
        if r.get("decision") != "APPROVE":
            reasons.append("REVIEW_HOLD")
    return reasons


def _fix_round_reasons(task_id: str, d: dict | None, c: dict | None, r: dict, priors: list) -> list[str]:
    """AMD-01 §5: at most one private-suite fix per task that failed BQ04·05·06, then a fresh review.

    ``priors`` are the earlier (curator, reviews) rounds, oldest first. A round in which a task's
    suite did not change is a re-evaluation and does not use up that task's fix.
    """
    if c is None:
        return []
    seen = [(pc_all["tasks"][task_id], pr_all["tasks"].get(task_id, {}))
            for pc_all, pr_all in priors if task_id in pc_all["tasks"]]
    pc, pr = seen[-1] if seen else (None, {})
    before, now = c.get("before_hashes"), c.get("kit_hashes")
    reasons = []
    if pc is not None and (before is None or before.get("suite") != pc["suite_sha256"]):
        reasons.append("BEFORE_SNAPSHOT_NOT_PREVIOUS_ROUND")
    if _suite_fixed(c):
        if [k for k in now if before.get(k) != now[k]] != ["suite"]:
            reasons.append("NON_SUITE_KIT_CHANGE")
        if pc is None:
            reasons.append("FIX_WITHOUT_PREVIOUS_ROUND")
        else:
            if not set(_base_reasons(d, pc, pr)) & FIX_ELIGIBLE:
                reasons.append("FIX_NOT_PERMITTED")
            if c["environment_sha256"] != pc["environment_sha256"]:
                reasons.append("CURATOR_ENVIRONMENT_CHANGED")
        if any(_suite_fixed(old) for old, _ in seen):
            reasons.append("FIX_LIMIT_EXCEEDED")
        if c.get("fix") is None:
            reasons.append("FIX_RECORD_MISSING")
        elif c["fix"]["status"] != "RECORDED":
            reasons.append("FIX_RECORD_INVALID")
        if r.get("evidence_id") is None or r["evidence_id"] in {old.get("evidence_id") for _, old in seen}:
            reasons.append("REVIEW_NOT_REDONE")
    else:
        if c.get("fix") is not None and (pc is None or c["fix"] != pc.get("fix")):
            reasons.append("FIX_RECORD_WITHOUT_CHANGE")
        if pc is not None and c["curator_manifest_sha256"] != pc["curator_manifest_sha256"]:
            reasons.append("CURATOR_MANIFEST_CHANGED")
        if pr.get("evidence_id") is not None and r.get("evidence_id") != pr["evidence_id"]:
            reasons.append("REVIEW_CHANGED_WITHOUT_FIX")
    return reasons


def decide_inclusion(dev_report: Path, curator_report: Path, review_summary: Path, *,
                     prior_rounds: list[tuple[Path, Path]] = ()) -> dict:
    """Decide inclusion; ``prior_rounds`` lists the earlier (curator report, review summary) pairs, oldest first."""
    dev, dev_hash = _read_json(dev_report)
    curator, curator_hash = _read_json(curator_report)
    reviews, review_hash = _read_json(review_summary)
    if (dev.get("variants_manifest_sha256"), curator.get("variants_manifest_sha256")) != (
            amd01_variants.MANIFEST_SHA256, amd01_variants.MANIFEST_SHA256):
        raise ContractError("objective reports were made from different variants")
    if curator.get("dev_report_sha256") != dev_hash:
        raise ContractError("curator report is not bound to this development report")
    inputs = {"dev_report_sha256": dev_hash, "curator_report_sha256": curator_hash,
              "review_summary_sha256": review_hash}
    priors = []
    if not prior_rounds:
        if "before_snapshot_sha256" in curator:
            raise ContractError("a fix-round curator report needs the earlier rounds' reports")
    else:
        if "before_snapshot_sha256" not in curator:
            raise ContractError("a fix-round curator report must carry the pre-fix kit snapshot")
        inputs["prior_rounds"] = []
        for index, (curator_path, review_path) in enumerate(prior_rounds):
            prior_curator, prior_curator_hash = _read_json(curator_path)
            prior_reviews, prior_review_hash = _read_json(review_path)
            if prior_curator.get("dev_report_sha256") != dev_hash:
                raise ContractError("an earlier curator report is not bound to this development report")
            if ("before_snapshot_sha256" in prior_curator) != (index > 0):
                raise ContractError("earlier rounds must start from the first report and carry snapshots after it")
            priors.append((prior_curator, prior_reviews))
            inputs["prior_rounds"].append({"curator_report_sha256": prior_curator_hash,
                                           "review_summary_sha256": prior_review_hash})
        inputs["before_snapshot_sha256"] = curator["before_snapshot_sha256"]
    tasks = {}
    for task_id in CANDIDATES:
        d = dev["tasks"].get(task_id)
        c = curator["tasks"].get(task_id)
        r = reviews["tasks"].get(task_id, {"status": "MISSING"})
        reasons = _base_reasons(d, c, r)
        if priors:
            reasons += _fix_round_reasons(task_id, d, c, r, priors)
        tasks[task_id] = {
            "included": not reasons, "reasons": reasons,
            "access_record": d["access"]["path"] if d and d.get("access") else None,
            "access_record_sha256": d["access"]["sha256"] if d and d.get("access") else None,
            "curator_manifest_sha256": c["curator_manifest_sha256"] if c else None,
            "reviewer_id": r.get("reviewer_id"), "review_evidence_id": r.get("evidence_id"),
            "partials_detected": f"{c['partials_detected']}/{c['partials_total']}" if c else None,
        }
        if priors:
            fixed_in = [i + 1 for i, (pc, _) in enumerate(priors) if _suite_fixed(pc["tasks"].get(task_id))]
            if _suite_fixed(c):
                fixed_in.append(len(priors) + 1)
            tasks[task_id]["suite_fixed"] = bool(fixed_in)
            tasks[task_id]["fix_round"] = fixed_in[0] if fixed_in else None
            fix = (c or {}).get("fix")
            tasks[task_id]["fix_evidence_id"] = fix["evidence_id"] if fix else None
    included = sorted(t for t, v in tasks.items() if v["included"])
    return {"schema_version": "1.0", "amendment": "AMD-01", "amendment_sha256": AMENDMENT_SHA256,
            "round": len(priors) + 1, "inputs": inputs, "min_tasks": MIN_TASKS, "included": included,
            "status": "READY" if len(included) >= MIN_TASKS else "BELOW_MINIMUM", "tasks": tasks}


# ---------------------------------------------------------------- freeze manifest

def code_version() -> dict:
    paths = sorted({p for base in CODE_ROOTS for p in (CHECKOUT / base).rglob("*.py")}
                   | {CHECKOUT / f for f in CODE_FILES})
    files = {p.relative_to(CHECKOUT).as_posix(): sha256_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
             for p in paths if "__pycache__" not in p.parts}
    return {"files": len(files), "code_version": sha256_bytes(canonical_json_bytes(files))}


def plan_inputs(draft: dict) -> dict:
    """The exact runner.plan arguments for the live schedule; shared by the draft and the executor."""
    tasks = {task_id: {"base": t["base_revision"], "fixed_hash": t["fixed_requirements_hash"],
                       "evaluator_manifest_hash": t["curator_manifest_sha256"],
                       "task_family_id": t["task_family_id"], "repo_id": t["repo_id"]}
             for task_id, t in draft["tasks"].items()}
    config = {"freeze_id": draft["freeze_id"], "model": draft["model"], "budget": draft["budget"],
              "analysis_config_sha256": draft["analysis"]["config_sha256"], "amendment": draft["amendment"],
              "public_profile": public_runner.PROFILE, "curator_profile": main_curator.PROFILE}
    environments = sorted({t["public_environment_sha256"] for t in draft["tasks"].values()})
    if len(environments) != 1:
        raise ContractError("included tasks use different public check environments")
    return {"experiment_id": draft["experiment_id"], "tasks": tasks, "repetitions": 1, "seed": draft["schedule"]["seed"],
            "config": config, "probe": {"access_records": {t: v["access_record_sha256"] for t, v in draft["tasks"].items()},
                                        "model_calls_before_run": 0},
            "environment_hash": environments[0], "code_version": draft["code"]["code_version"]}


def build(inclusion_path: Path = INCLUSION, *, identity: dict | None = None) -> dict:
    inclusion, inclusion_hash = _read_json(inclusion_path)
    if inclusion.get("amendment_sha256") != AMENDMENT_SHA256:
        raise ContractError("inclusion record is not bound to the approved amendment")
    tasks = {}
    for task_id in inclusion["included"]:
        entry = inclusion["tasks"][task_id]
        access_path = Path(entry["access_record"])
        record = main_runner.load_access_record(access_path if access_path.is_absolute() else CHECKOUT / access_path,
                                                sha256=entry["access_record_sha256"], task_id=task_id)
        task, fixed, repository = main_runner.task_inputs(task_id)
        _, _, spec_hash = read_task(task_id)
        public = public_runner.definition(task_id, identity=identity)
        if record["base_revision"] != repository.initial_commit:
            raise ContractError(f"{task_id} public base changed after its access check")
        tasks[task_id] = {
            "repo_id": task.repo_id, "task_family_id": task.task_family_id, "doc_state": task.doc_state,
            "public_spec_sha256": spec_hash, "base_revision": repository.initial_commit,
            "fixed_requirements_hash": sha256_bytes(dump_record(fixed)), "mandatory_hash": fixed.mandatory_hash,
            "approved_check_ids": list(fixed.approved_check_ids), "allowed_change_paths": list(task.allowed_change_paths),
            "access_record": entry["access_record"], "access_record_sha256": entry["access_record_sha256"],
            "permission_settings_hash": {c: v["permission_settings_hash"] for c, v in record["conditions"].items()},
            "initial_snapshot_id": {c: v.get("initial_snapshot_id") for c, v in record["conditions"].items()},
            "public_suite_sha256": public["suite_sha256"], "public_environment_sha256": public["environment_hash"],
            "curator_manifest_sha256": entry["curator_manifest_sha256"],
            "reviewer_id": entry["reviewer_id"], "review_evidence_id": entry["review_evidence_id"],
            "suite_fixed": entry.get("suite_fixed", False), "fix_round": entry.get("fix_round"),
            "fix_evidence_id": entry.get("fix_evidence_id"),
        }
    draft = {
        "schema_version": "1.0", "freeze_id": FREEZE_ID, "experiment_id": EXPERIMENT_ID,
        "amendment": {"id": "AMD-01", "decision": "DL-031", "sha256": AMENDMENT_SHA256},
        "inclusion": {"path": _display(inclusion_path), "sha256": inclusion_hash, "min_tasks": MIN_TASKS,
                      "excluded": {t: v["reasons"] for t, v in inclusion["tasks"].items() if not v["included"]}},
        "scope": {"plan": "Emergency (exploratory)", "conditions": [c.value for c in Condition], "repetitions": 1,
                  "checkpoint": "conditional (AMD-01 §7.2), separate experiment ID", "ablation": "NOT_RUN"},
        "tasks": tasks,
        "model": {"adapter": "codex_cli", "model_id": live_dev01.MODEL, "reasoning_effort": live_dev01.EFFORT,
                  "runtime_version": RUNTIME_VERSION, "session_mode": "SAME_SESSION"},
        "permissions": {"mxc_profile": live_dev01.PROFILE, "access_profile": access_check.PROFILE},
        "budget": budget_for(len(tasks)),
        "schedule": {"seed": SEED, "slots": None},
        "evaluator": {"public_profile": public_runner.PROFILE, "worker_sha256": source_suites.WORKER_SHA256,
                      "curator_profile": main_curator.PROFILE},
        "analysis": {"config": main_analysis.AMD01_ANALYSIS_CONFIG,
                     "config_sha256": main_analysis.AMD01_CONFIG_SHA256},
        "code": code_version(),
    }
    if tasks:
        with TemporaryDirectory(prefix="aah-main-freeze-") as temporary:
            manifest = runner.plan(Path(temporary), **plan_inputs(draft))
            slots = runner.pinned(Path(temporary), manifest.schedule_ref, manifest.schedule_hash)
        draft["schedule"]["slots"] = [{"slot_id": s["slot_id"], "task_id": s["task_id"], "condition": s["condition"]}
                                      for s in slots]
    return draft


def render(draft: dict) -> bytes:
    return json.dumps(draft, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def write(path: Path = FREEZE, inclusion: Path = INCLUSION, **kwargs) -> tuple[Path, str]:
    data = render(build(inclusion, **kwargs))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, sha256_bytes(data)


def reproduce(path: Path = FREEZE, inclusion: Path = INCLUSION, *, identity: dict | None = None) -> tuple[bool, dict]:
    data = read_regular(path)[0]
    return render(build(inclusion, identity=identity)) == data, json.loads(data)


def validate_for_execution(path: Path = FREEZE, approval: Path = APPROVAL, *, inclusion: Path | None = None,
                           identity: dict | None = None) -> dict:
    """Return the frozen draft only if every lock condition holds; otherwise raise FreezeGateError."""
    inclusion = inclusion or INCLUSION
    if not path.exists():
        raise FreezeGateError(["FREEZE_MANIFEST_MISSING"])
    if not inclusion.exists():
        raise FreezeGateError(["INCLUSION_RECORD_MISSING"])
    unmet = []
    if not AMENDMENT.exists() or sha256_bytes(read_regular(AMENDMENT)[0]) != AMENDMENT_SHA256:
        unmet.append("AMENDMENT_CHANGED")
    same, draft = reproduce(path, inclusion, identity=identity)
    if not same:
        unmet.append("FREEZE_NOT_REPRODUCIBLE")
    if len(draft["tasks"]) < MIN_TASKS:
        unmet.append("BELOW_MINIMUM_TASKS")
    if any(not t["curator_manifest_sha256"] or not t["review_evidence_id"] for t in draft["tasks"].values()):
        unmet.append("TASK_EVIDENCE_MISSING")
    if not approval.exists():
        unmet.append("EXECUTION_APPROVAL_MISSING")
    else:
        signed = json.loads(read_regular(approval)[0])
        budget = draft["budget"]
        if signed.get("approved_freeze_sha256") != sha256_bytes(read_regular(path)[0]):
            unmet.append("APPROVAL_NOT_BOUND_TO_FREEZE")
        if signed.get("amendment_sha256") != AMENDMENT_SHA256:
            unmet.append("APPROVAL_NOT_BOUND_TO_AMENDMENT")
        if ((signed.get("profile"), signed.get("max_extra_payment_krw"), signed.get("max_initial_turns"),
             signed.get("max_recovery_turns"), signed.get("max_total_turns"))
                != (budget["profile"], budget["extra_payment_krw"], budget["max_initial_turns"],
                    budget["max_recovery_turns"], budget["max_total_turns"])):
            unmet.append("BUDGET_APPROVAL_MISMATCH")
        joint = signed.get("g2_joint_approval") or {}
        if not all(joint.get(role) for role in ("RH", "GR", "CE")):
            unmet.append("G2_JOINT_APPROVAL_MISSING")
    if unmet:
        raise FreezeGateError(unmet)
    return draft


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--include", action="store_true", help="decide AMD-01 inclusion from the three reports")
    mode.add_argument("--write", action="store_true", help="(re)generate the freeze manifest")
    mode.add_argument("--check", action="store_true", help="regenerate and compare with the saved manifest")
    mode.add_argument("--status", action="store_true", help="list unmet lock conditions")
    parser.add_argument("--dev-report", type=Path)
    parser.add_argument("--curator-report", type=Path)
    parser.add_argument("--review-summary", type=Path)
    parser.add_argument("--prior-curator-report", type=Path, action="append", default=[],
                        help="AMD-01 fix round: an earlier round's curator report (repeat, oldest first)")
    parser.add_argument("--prior-review-summary", type=Path, action="append", default=[],
                        help="AMD-01 fix round: the same round's review summary (repeat, oldest first)")
    args = parser.parse_args()
    try:
        if args.include:
            if None in (args.dev_report, args.curator_report, args.review_summary):
                parser.error("--include needs --dev-report, --curator-report and --review-summary")
            if len(args.prior_curator_report) != len(args.prior_review_summary):
                parser.error("every --prior-curator-report needs its --prior-review-summary")
            record = decide_inclusion(args.dev_report, args.curator_report, args.review_summary,
                                      prior_rounds=list(zip(args.prior_curator_report, args.prior_review_summary)))
            INCLUSION.parent.mkdir(parents=True, exist_ok=True)
            INCLUSION.write_bytes(render(record))
            for task_id, entry in record["tasks"].items():
                print(task_id, "INCLUDED" if entry["included"] else "EXCLUDED " + ",".join(entry["reasons"]))
            print("AMD01_INCLUSION", record["status"], f"{len(record['included'])}/{len(CANDIDATES)}",
                  sha256_bytes(INCLUSION.read_bytes()))
            return 0 if record["status"] == "READY" else 1
        if args.write:
            path, digest = write()
            print("MAIN_FREEZE_WRITTEN", _display(path), digest)
        elif args.check:
            same, _ = reproduce()
            print("MAIN_FREEZE_REPRODUCED" if same else "MAIN_FREEZE_DIFFERS", sha256_bytes(read_regular(FREEZE)[0]))
            return 0 if same else 1
        else:
            validate_for_execution()
            print("MAIN_FREEZE_LOCKED", sha256_bytes(read_regular(FREEZE)[0]))
    except FreezeGateError as error:
        print("MAIN_FREEZE_NOT_LOCKED", " ".join(error.unmet))
        return 2
    except (OSError, ValueError, RuntimeError) as error:
        print("MAIN_FREEZE_BLOCKED", str(error) if isinstance(error, ContractError) else type(error).__name__,
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
