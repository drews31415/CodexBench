"""AMD-01 curator-side batch: private-suite judgments and AI-review summaries for all kits.

Run by the operator from the extracted curator kits root (outside codexbench). Output records
carry statuses, counts and hashes only; private suite sources and review rationales stay in the
kit folders.

  python -m evaluation.amd01_curator evaluate --kits . --dev-report <objective-dev.json> --output <file>
  python -m evaluation.amd01_curator reviews  --kits . --output <file>

The one permitted private-suite fix (AMD-01 §5) is bracketed by a kit hash snapshot:

  python -m evaluation.amd01_curator hashes   --kits . --output <before-fix.json>
  (separate fix session edits curator_tests and writes amd01-fixes/<task>.json)
  python -m evaluation.amd01_curator evaluate --kits . --dev-report <...> --before <before-fix.json> --output <file>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aah.domain import ContractError, canonical_json_bytes, sha256_bytes
from aah.workspace import export_revision
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, read_regular, read_tree
from benchmarks.main_public import amd01_variants
from evaluation.curator_check import SUITES
from evaluation.main_curator import CuratorKit


REVIEW_ITEMS = ("BQ01", "BQ04", "BQ05", "BQ06", "BQ11")
INSTRUCTIONS = Path(__file__).resolve().parents[1] / "docs/project/AMD01_AI_REVIEW_INSTRUCTIONS.md"
FIX_INSTRUCTIONS = Path(__file__).resolve().parents[1] / "docs/project/AMD01_TEST_FIX_INSTRUCTIONS.md"
FIX_ACTIONS = ("REMOVED", "NARROWED", "ADDED")


def _kits(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name in amd01_variants.VARIANTS
                  and (p / "task.json").is_file())


def kit_hashes(kit_dir: Path) -> dict:
    """Hashes of every kit component; the fix rule lets only ``suite`` change between rounds."""
    suites = {kind: sha256_bytes(read_regular(kit_dir / "curator_tests" / name)[0]) for kind, name in SUITES.items()}
    return {"task_spec": sha256_bytes(read_regular(kit_dir / "task.json")[0]),
            "baseline": export_revision(kit_dir / "baseline"), "reference": export_revision(kit_dir / "reference"),
            "suite": sha256_bytes(canonical_json_bytes(suites))}


def hashes_all(root: Path) -> dict:
    root = checked_path(root)
    return {"schema_version": "1.0", "profile": "amd01-kit-hashes-v1",
            "tasks": {kit_dir.name: kit_hashes(kit_dir) for kit_dir in _kits(root)}}


def fix_record(root: Path, task_id: str) -> dict | None:
    """Export codes, counts and the file hash of a fix record; reasons and quotes stay in the kit."""
    path = root / "amd01-fixes" / f"{task_id}.json"
    if not path.is_file():
        return None
    data = read_regular(path)[0]
    record = json.loads(data)
    problems = []
    if record.get("task_id") != task_id:
        problems.append("TASK_ID")
    if record.get("fix_instructions_sha256") != sha256_bytes(read_regular(FIX_INSTRUCTIONS)[0]):
        problems.append("INSTRUCTIONS_HASH")
    if not isinstance(record.get("fixer_model"), str) or not record["fixer_model"]:
        problems.append("FIXER_MODEL")
    changes = record.get("changes")
    if not isinstance(changes, list) or not changes:
        problems.append("CHANGES")
        changes = []
    for change in changes:
        if (not isinstance(change, dict) or change.get("action") not in FIX_ACTIONS
                or change.get("file") not in SUITES.values() or not change.get("reason")
                or (change["action"] != "REMOVED" and not change.get("spec_quote"))):
            problems.append("CHANGE_ENTRY")
            break
    if record.get("self_check") != "PASS":
        problems.append("SELF_CHECK")
    actions = [c.get("action") for c in changes if isinstance(c, dict)]
    return {"status": "INVALID" if problems else "RECORDED", "problems": problems,
            "fixer_id": f"ai-fix:{record.get('fixer_model')}", "evidence_id": sha256_bytes(data),
            "changes": {a: actions.count(a) for a in FIX_ACTIONS}}


def _short(outcome: dict) -> dict:
    return {key: outcome[key] for key in ("status", "functional", "regression", "instruction", "reason")}


def evaluate_kit(kit_dir: Path, *, identity=None, transport=None) -> dict:
    kit = CuratorKit(kit_dir, identity=identity)
    base, _ = read_tree(kit.base)
    reference, _ = read_tree(kit_dir / "reference")
    changed = {path: reference[path].data for path in reference if base.get(path) != reference.get(path)}
    baseline = [_short(kit.judge({}, transport=transport)) for _ in range(2)]
    repaired = [_short(kit.judge(changed, transport=transport)) for _ in range(2)]
    variants = {}
    for variant in amd01_variants.VARIANTS[kit.task_id]:
        files = {path: source.encode("utf-8") for path, source in variant["files"].items()}
        variants[variant["name"]] = {"kind": variant["kind"], **_short(kit.judge(files, transport=transport))}
    alternatives = {n: v for n, v in variants.items() if v["kind"] == "alternative"}
    partials = {n: v for n, v in variants.items() if v["kind"] == "partial"}
    expected_base = {"status": "FAIL", "functional": "FAIL", "regression": "PASS", "instruction": "PASS"}
    checks = {
        "BQ02": "PASS" if all({k: b[k] for k in expected_base} == expected_base for b in baseline) else "FAIL",
        "BQ03": "PASS" if all(r["status"] == "PASS" and r["instruction"] == "PASS" for r in repaired) else "FAIL",
        "BQ08": "PASS" if baseline[0] == baseline[1] and repaired[0] == repaired[1] else "FAIL",
        "BQ05": "PASS" if sum(v["status"] == "FAIL" for v in partials.values()) >= 1 else "FAIL",
        # Failing alternatives are allowed only with a spec-justified AI review (AMD-01 §5).
        "BQ04_06_objective": "PASS" if all(v["status"] == "PASS" for v in alternatives.values()) else "REVIEW",
    }
    hashes = kit_hashes(kit_dir)
    if hashes["suite"] != kit.bundle_hash:
        raise ContractError("curator suite hash differs between readers")
    return {"task_id": kit.task_id, "curator_manifest_sha256": kit.manifest_hash, "suite_sha256": kit.bundle_hash,
            "environment_sha256": kit.environment_hash, "kit_hashes": hashes, "baseline": baseline, "reference": repaired,
            "variants": variants, "alternatives_failed": sorted(n for n, v in alternatives.items() if v["status"] != "PASS"),
            "partials_detected": sum(v["status"] == "FAIL" for v in partials.values()),
            "partials_total": len(partials), "checks": checks}


def evaluate_all(root: Path, dev_report: Path | None, *, before: Path | None = None, identity=None,
                 transport=None) -> dict:
    root = checked_path(root)
    report = {"schema_version": "1.0", "profile": "amd01-curator-v1", "amendment": "AMD-01",
              "variants_manifest_sha256": amd01_variants.MANIFEST_SHA256, "model_calls": 0, "tasks": {}}
    snapshot = {}
    if before is not None:
        data = read_regular(before)[0]
        snapshot = json.loads(data)
        if snapshot.get("profile") != "amd01-kit-hashes-v1":
            raise ContractError("--before is not a kit hash snapshot")
        report["before_snapshot_sha256"] = sha256_bytes(data)
        snapshot = snapshot["tasks"]
    if dev_report is not None:
        data = read_regular(dev_report)[0]
        dev = json.loads(data)
        if dev.get("variants_manifest_sha256") != amd01_variants.MANIFEST_SHA256:
            raise ContractError("development report was made from different variants")
        report["dev_report_sha256"] = sha256_bytes(data)
        report["dev_checks"] = {t: r["checks"] for t, r in dev["tasks"].items()}
    for kit_dir in _kits(root):
        print("AMD01_CURATOR_TASK", kit_dir.name, flush=True)
        entry = report["tasks"][kit_dir.name] = evaluate_kit(kit_dir, identity=identity, transport=transport)
        entry["fix"] = fix_record(root, kit_dir.name)
        if before is not None:
            entry["before_hashes"] = snapshot.get(kit_dir.name)
    return report


def summarize_reviews(root: Path) -> dict:
    """Export verdict codes and file hashes only; rationales stay in the kit folder."""
    root = checked_path(root)
    instructions = sha256_bytes(read_regular(INSTRUCTIONS)[0])
    summary = {"schema_version": "1.0", "profile": "amd01-review-summary-v1", "instructions_sha256": instructions,
               "tasks": {}}
    for kit_dir in _kits(root):
        path = root / "amd01-reviews" / f"{kit_dir.name}.json"
        if not path.is_file():
            summary["tasks"][kit_dir.name] = {"status": "MISSING"}
            continue
        data = read_regular(path)[0]
        review = json.loads(data)
        problems = []
        if review.get("task_id") != kit_dir.name:
            problems.append("TASK_ID")
        if review.get("instructions_sha256") != instructions:
            problems.append("INSTRUCTIONS_HASH")
        verdicts = review.get("verdicts", {})
        if set(verdicts) != set(REVIEW_ITEMS) or set(verdicts.values()) - {"PASS", "FAIL"}:
            problems.append("VERDICTS")
        if review.get("decision") not in ("APPROVE", "HOLD"):
            problems.append("DECISION")
        if not isinstance(review.get("reviewer_model"), str) or not review["reviewer_model"]:
            problems.append("REVIEWER_MODEL")
        justified = {item.get("variant"): bool(item.get("justified_by_spec"))
                     for item in review.get("failing_alternatives", []) if isinstance(item, dict)}
        summary["tasks"][kit_dir.name] = {
            "status": "INVALID" if problems else "RECORDED", "problems": problems,
            "reviewer_id": f"ai-review:{review.get('reviewer_model')}", "evidence_id": sha256_bytes(data),
            "verdicts": {k: verdicts.get(k) for k in REVIEW_ITEMS}, "decision": review.get("decision"),
            "failing_alternatives_justified": justified,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("evaluate", "reviews", "hashes"))
    parser.add_argument("--kits", type=Path, required=True, help="the extracted curator kits root")
    parser.add_argument("--dev-report", type=Path, help="objective-dev.json from the development side")
    parser.add_argument("--before", type=Path, help="kit hash snapshot taken before the AMD-01 fix round")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = (evaluate_all(args.kits, args.dev_report, before=args.before) if args.mode == "evaluate"
                  else summarize_reviews(args.kits) if args.mode == "reviews" else hashes_all(args.kits))
        output = checked_path(args.output, allow_missing=True)
        store_bytes(output.parent, output.name, canonical_json_bytes(report))
        # Copies beside the kits let the review session work without opening the repository.
        if args.mode == "evaluate":
            kits = checked_path(args.kits)
            # The output record is write-once; this convenience copy follows the latest run.
            (kits / "amd01-objective-report.json").write_bytes(canonical_json_bytes(report))
            store_bytes(kits, "amd01-review-instructions.md", read_regular(INSTRUCTIONS)[0])
            store_bytes(kits, "amd01-fix-instructions.md", read_regular(FIX_INSTRUCTIONS)[0])
            for task_id, variants in amd01_variants.VARIANTS.items():
                for variant in variants:
                    for path, source in variant["files"].items():
                        store_bytes(kits / "amd01-variants" / task_id / variant["name"], path, source.encode("utf-8"))
            (kits / "amd01-reviews").mkdir(exist_ok=True)
    except (OSError, ValueError, RuntimeError) as error:
        print("AMD01_CURATOR_BLOCKED", str(error) if isinstance(error, ContractError) else type(error).__name__,
              file=sys.stderr)
        return 2
    for task_id, task in report["tasks"].items():
        if args.mode == "evaluate":
            print(task_id, task["curator_manifest_sha256"], " ".join(f"{k}={v}" for k, v in task["checks"].items()),
                  f"partials={task['partials_detected']}/{task['partials_total']}",
                  "alt_failed=" + (",".join(task["alternatives_failed"]) or "none"),
                  "fix=" + (task["fix"]["status"] if task["fix"] else "none"))
        elif args.mode == "hashes":
            print(task_id, task["suite"])
        else:
            print(task_id, task["status"], task.get("decision"), task.get("evidence_id", ""))
    print("AMD01_CURATOR_" + args.mode.upper(), output, sha256_bytes(read_regular(output)[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
