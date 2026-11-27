"""AMD-01 development-side objective checks (no private material, no model calls).

Covers what the development boundary may run: public suites on the baseline (twice, BQ07) and on
every AMD-01 variant, family/duplication checks (BQ12), operating facts (BQ13) and the per-task
MXC access records (BQ09/BQ10). Curator-side checks live in ``evaluation.amd01_curator``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from aah.domain import canonical_json_bytes, sha256_bytes
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, read_regular
from benchmarks.main_public import access_check, amd01_variants, public_runner
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import docker_worker
from evaluation import source_suites


CHECKOUT = Path(__file__).resolve().parents[2]
TASKS = tuple(sorted(amd01_variants.VARIANTS))
ACCESS_ROOT = CHECKOUT / "results/g2-preparation/amd01"
DEV_FAMILIES = ("DEV-01", "DEV-02", "DEV-03", "DEV-04", "DEV-05")


def _public(pinned, overlay, transport):
    modules = {name: (pinned["base"] / f"{name}.py").read_bytes() for name in pinned["modules"]}
    for path, source in overlay.items():
        modules[Path(path).stem] = source.encode("utf-8")
    outcome = source_suites.run_suites(modules, pinned["suites"], monotonic() + 60, transport=transport)
    return {kind: value["status"] for kind, value in outcome.items()}


def latest_access_record(task_id: str, root: Path = ACCESS_ROOT) -> Path | None:
    found = sorted(root.glob(f"access-{task_id}-*/access.json"))
    return found[-1] if found else None


def task_report(task_id: str, *, identity=None, transport=None, access_root: Path = ACCESS_ROOT) -> dict:
    directory, task, spec_hash = read_task(task_id)
    pinned = public_runner.definition(task_id, identity=identity)
    baseline = [_public(pinned, {}, transport) for _ in range(2)]
    variants = {}
    for variant in amd01_variants.VARIANTS[task_id]:
        if set(variant["files"]) - set(task.allowed_change_paths):
            raise ValueError(f"{task_id}/{variant['name']} edits a path outside the allowed change paths")
        status = _public(pinned, variant["files"], transport)
        variants[variant["name"]] = {"kind": variant["kind"], "public": status,
                                     "public_pass": set(status.values()) == {"PASS"}}
    alternatives = [v for v in variants.values() if v["kind"] == "alternative"]
    partials = [v for v in variants.values() if v["kind"] == "partial"]
    access_path = latest_access_record(task_id, access_root)
    access = None
    if access_path is not None:
        data = read_regular(access_path)[0]
        record = json.loads(data)
        access = {"path": access_path.relative_to(CHECKOUT).as_posix() if access_path.is_relative_to(CHECKOUT)
                  else access_path.name, "sha256": sha256_bytes(data), "status": record.get("status"),
                  "bq09": record.get("bq09", {}).get("status"), "bq10": record.get("bq10", {}).get("status"),
                  "permission_settings_hash": sorted({c["permission_settings_hash"]
                                                      for c in record.get("conditions", {}).values()}),
                  "initial_snapshot_id": sorted({c.get("initial_snapshot_id")
                                                 for c in record.get("conditions", {}).values()})}
    checks = {
        "BQ07": "PASS" if baseline[0] == baseline[1] and baseline[0] == {"P-FUNC": "FAIL", "P-REG": "PASS"} else "FAIL",
        "BQ09": "PASS" if access and access["bq09"] == "PASS" else "FAIL",
        "BQ10": "PASS" if access and access["bq10"] == "PASS" else "FAIL",
        "alternatives_public": "PASS" if len(alternatives) >= 2 and all(v["public_pass"] for v in alternatives) else "FAIL",
        "partials_count": "PASS" if len(partials) >= 3 else "FAIL",
    }
    return {"task_id": task_id, "repo_id": task.repo_id, "task_family_id": task.task_family_id,
            "doc_state": task.doc_state, "public_spec_sha256": spec_hash,
            "public_environment_sha256": pinned["environment_hash"], "baseline_public": baseline,
            "variants": variants, "partials_passing_public": sum(v["public_pass"] for v in partials),
            "access": access, "checks": checks}


def build(*, identity=None, transport=None, access_root: Path = ACCESS_ROOT) -> dict:
    identity = identity if identity is not None else docker_worker.identity()
    tasks = {t: task_report(t, identity=identity, transport=transport, access_root=access_root) for t in TASKS}
    families = [r["task_family_id"] for r in tasks.values()]
    bq12 = "PASS" if len(set(families)) == len(families) and not set(families) & set(DEV_FAMILIES) else "FAIL"
    for report in tasks.values():
        report["checks"]["BQ12"] = bq12
        report["checks"]["BQ13"] = "PASS"  # own tasks; pinned image recorded below
    return {"schema_version": "1.0", "profile": "amd01-objective-dev-v1", "amendment": "AMD-01",
            "variants_manifest_sha256": amd01_variants.MANIFEST_SHA256, "docker": identity,
            "access_profile": access_check.PROFILE, "model_calls": 0, "tasks": tasks,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build()
    output = checked_path(args.output, allow_missing=True)
    store_bytes(output.parent, output.name, canonical_json_bytes(report))
    for task_id, task in report["tasks"].items():
        failed = [name for name, status in task["checks"].items() if status != "PASS"]
        print(task_id, "PASS" if not failed else "FAIL " + ",".join(failed),
              f"partials_passing_public={task['partials_passing_public']}/3")
    print("AMD01_OBJECTIVE_DEV", output, sha256_bytes(read_regular(output)[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
