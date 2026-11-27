"""Pin the Dev Pilot proposal and its schedule without opening a model runtime."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from random import Random
from uuid import uuid4

from aah.domain import (
    Condition, FixedRequirements, RepositoryRef, RunRequest, ScheduledSlot,
    canonical_json_bytes, dump_record, resolve_condition, sha256_bytes,
)
from aah.workspace import export_revision
from aah.workspace.patch import store_bytes
from benchmarks.smoke import s4
from evaluation.smoke import CHECKOUT, FIXTURES, fixture_request


PILOT_ID = "s5-dev-pilot-20261126-draft"
DRAFT_PATH = CHECKOUT / "configs/pilot/dev-pilot-20261126-draft.json"
DRAFT_SHA256 = "faf5d934715a04eaa1e299b32163defd81b07e707d4e1380975d4eed6014941c"
SEED = 20261126
SMOKE_TASKS = ("DEV-01", "DEV-02")
NEW_TASKS = ("DEV-03", "DEV-04", "DEV-05")
TASKS = (*SMOKE_TASKS, *NEW_TASKS)
RECOVERIES_PER_SLOT = 2


def task_directory(task_id):
    if task_id not in TASKS:
        raise ValueError(f"unknown Dev Pilot task: {task_id}")
    return CHECKOUT / "benchmarks" / ("smoke" if task_id in SMOKE_TASKS else "pilot") / task_id


def task_request(task_id):
    directory = task_directory(task_id)
    if task_id in SMOKE_TASKS:
        return fixture_request(task_id)
    task_bytes = (directory / "task.json").read_bytes().replace(b"\r\n", b"\n")
    spec = json.loads(task_bytes)
    expected_checks = tuple(f"{task_id}-{kind}" for kind in ("P-FUNC", "P-REG"))
    if (spec["schema_version"], spec["task_id"], tuple(spec["approved_check_ids"])) != ("1.0", task_id, expected_checks):
        raise ValueError("invalid public Dev task specification")
    fixed = FixedRequirements(spec["task"], spec["mandatory_text"], sha256_bytes(spec["mandatory_text"].encode()),
                              "public-copy-only", expected_checks)
    source = directory / "base"
    return RunRequest(str(uuid4()), task_id, None, RepositoryRef(task_id, source, export_revision(source)), fixed,
                      resolve_condition(Condition.A), sha256_bytes(task_bytes), "user")


def build_plan():
    tasks, slots = {}, []
    rng = Random(SEED)
    for task_id in TASKS:
        request = task_request(task_id)
        directory = task_directory(task_id)
        evaluation_assets = FIXTURES / task_id
        tasks[task_id] = {
            "base_revision": request.repository.initial_commit,
            "task_hash": request.config_hash,
            "fixed_requirements_hash": sha256_bytes(dump_record(request.fixed)),
            "public_checks_hash": sha256_bytes((directory / "public.json").read_bytes().replace(b"\r\n", b"\n")),
            "review_suite_hash": sha256_bytes((evaluation_assets / "suites.json").read_bytes().replace(b"\r\n", b"\n")),
            "reference_hash": sha256_bytes((evaluation_assets / "module.py").read_bytes().replace(b"\r\n", b"\n")),
            "live_evaluation_boundary": "S4_DEV01_VERIFIED" if task_id == "DEV-01" else "PENDING",
        }
        conditions = list(Condition)
        rng.shuffle(conditions)
        for condition in conditions:
            slots.append(ScheduledSlot(PILOT_ID, "end_to_end", f"slot-{len(slots)+1:04}", f"{task_id}-1",
                                       "END_TO_END", task_id, None, None, condition, 1, len(slots)))
    max_recovery_turns = sum(resolve_condition(slot.condition).external_intervention for slot in slots) * RECOVERIES_PER_SLOT
    return {
        "schema_version": "1.0", "experiment_id": PILOT_ID, "status": "DRAFT", "launchable": False,
        "execution_order_seed": SEED, "task_assets": tasks, "slots": slots,
        "proposed_runtime": asdict(s4.LIVE_CONFIG), "supplement_cap": s4.CAP, "baseline_checks": True,
        "token_limit_mode": "observe_only",
        "proposed_limits": {
            "initial_turns_per_slot": 1, "recovery_turns_per_eligible_slot": RECOVERIES_PER_SLOT,
            "initial_turns": len(slots), "recovery_turns": max_recovery_turns,
            "total_turns": len(slots) + max_recovery_turns, "native_turn_ms": 105_000,
            "wall_ms_per_run": 360_000, "cleanup_reserve_ms": 15_000, "max_transitions": 100,
            "extra_payment_krw": 0,
        },
        "pilot_live_approval": None,
    }


def read_pinned_draft():
    raw = DRAFT_PATH.read_bytes()
    if sha256_bytes(raw) != DRAFT_SHA256 or canonical_json_bytes(build_plan()) != raw:
        raise ValueError("Pilot draft or an approved task asset changed")
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = canonical_json_bytes(build_plan())
    store_bytes(args.output.parent, args.output.name, data)
    print("PILOT_DRAFT_READY", args.output, sha256_bytes(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
