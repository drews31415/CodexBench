"""Prepare AAH-S03 Main slots on the verified Windows/MXC permission profile.

Each slot follows the Pilot/S4 operator shape (``live_dev01._run`` with an ``experiment``
callback): a fresh public copy, the native Codex binary and filtered environment, and a
``dispatch`` bound to the live turn configuration. Before the callback can start an agent
turn, the slot's actual turn/sandbox settings are normalized exactly as ``access_check`` did
and their hash must equal the condition's entry in the pinned BQ09/BQ10 access record.
A missing, changed or failing record, or a different hash, stops the slot before any
Codex process starts.

This module has no live Main schedule. ``--verify`` exercises the gate for A–E with no
agent turns.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4

from aah.domain import (Condition, ContractError, FixedRequirements, RepositoryRef, RunRequest,
                        canonical_json_bytes, resolve_condition, sha256_bytes)
from aah.workspace import WorkspaceManager, export_revision
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, read_regular
from benchmarks.main_public import access_check
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import live_dev01


PROFILE = "main-s03-runner-v1"
TASK = access_check.TASK
CHECKOUT = access_check.CHECKOUT
ACCESS_RECORD = CHECKOUT / "results/g2-preparation/main-s03-access-20261127-030416/access.json"
# EL-20261127-020. The record lives under the access-restricted results/ tree.
ACCESS_RECORD_SHA256 = "0c7a236bbe9a5f31555927be4df5c3a1f2d04bc2f2cee4cc11c63a343a704c41"


class PermissionGateError(ContractError):
    """The verified permission profile cannot be bound to this slot; no agent turn may start."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (f": {detail}" if detail else ""))
        self.code = code


def task_inputs(task_id: str = TASK):
    directory, task, _ = read_task(task_id)
    base = directory / "base"
    fixed = FixedRequirements(task.task, task.mandatory_text, sha256_bytes(task.mandatory_text.encode("utf-8")),
                              "public-copy-only", task.approved_check_ids)
    return task, fixed, RepositoryRef(task.repo_id, base, export_revision(base))


def load_access_record(path: Path = ACCESS_RECORD, *, sha256: str | None = ACCESS_RECORD_SHA256,
                       task_id: str = TASK) -> dict:
    """Read the BQ09/BQ10 record and refuse anything but a complete A–E PASS for this task."""
    path = checked_path(path, allow_missing=True)
    if not path.exists():
        raise PermissionGateError("ACCESS_RECORD_MISSING")
    data = read_regular(path)[0]
    if sha256 is not None and sha256_bytes(data) != sha256:
        raise PermissionGateError("ACCESS_RECORD_CHANGED", "file hash differs from the logged record")
    try:
        record = json.loads(data)
        conditions = record["conditions"]
        valid = (record["profile"] == access_check.PROFILE and record["task_id"] == task_id
                 and record["mxc_profile"] == live_dev01.PROFILE and record["status"] == "PASS"
                 and record["bq09"]["status"] == "PASS" and record["bq10"]["status"] == "PASS"
                 and record["agent_turns"] == 0 and set(conditions) == {c.value for c in Condition}
                 and all(isinstance(conditions[c]["permission_settings_hash"], str) for c in conditions))
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise PermissionGateError("ACCESS_RECORD_INVALID", f"not a complete A–E PASS for {task_id}")
    return record


def bind_permissions(record: dict, request: RunRequest, workspace: Path, credential_root: Path, python: Path):
    """Return the live turn configuration only if its normalized hash matches the access record."""
    condition = request.policies.condition.value
    if (request.task_id, request.repository.initial_commit) != (record["task_id"], record["base_revision"]):
        raise PermissionGateError("ACCESS_RECORD_TASK_MISMATCH", "task or public base differs from the record")
    config, sandbox, settings, observed = access_check.permission_settings(workspace, credential_root, python)
    expected = record["conditions"][condition]["permission_settings_hash"]
    gate = {"condition": condition, "expected": expected, "observed": observed,
            "status": "MATCH" if observed == expected else "MISMATCH"}
    return config, sandbox, settings, gate


def run_slot(request: RunRequest, *, record: dict, work_root: Path, artifact_root: Path, operator: dict,
             experiment, inherit_console: bool = False) -> None:
    """Prepare one Main slot and hand ``experiment`` a dispatch bound to the verified settings."""
    if request.task_id != record["task_id"] or request.mode != "research":
        raise ContractError("the Main runner needs a research slot bound to its task's access record")
    exe, env, credential_root, python = access_check.operator_environment()
    base = request.repository.source_path
    quiet = True
    manager = WorkspaceManager(base, work_root, artifact_root, quiescence_check=lambda _: quiet)
    handle = manager.prepare(request)
    try:
        snapshot_id = manager.snapshot(handle).snapshot_id
        operator["initial_snapshot_id"] = snapshot_id
        logged = record["conditions"][request.policies.condition.value].get("initial_snapshot_id")
        operator["snapshot_gate"] = {"expected": logged, "observed": snapshot_id,
                                     "status": "MATCH" if logged == snapshot_id else
                                     "MISSING" if not isinstance(logged, str) else "MISMATCH"}
        if operator["snapshot_gate"]["status"] != "MATCH":
            raise PermissionGateError("INITIAL_SNAPSHOT_" + operator["snapshot_gate"]["status"],
                                      f"condition {request.policies.condition.value} start differs from the record")
        config, _sandbox, settings, gate = bind_permissions(record, request, handle.root, credential_root, python)
        operator["permission_gate"] = gate
        operator["permission_settings"] = settings
        if gate["status"] != "MATCH":
            # Fail closed before the first Codex process for this slot.
            raise PermissionGateError("PERMISSION_SETTINGS_MISMATCH",
                                      f"condition {gate['condition']} differs from the access record")

        def job(args, *, seconds, data=None):
            nonlocal quiet
            quiet = False
            deadline = monotonic() + seconds
            outcome = access_check._launch((str(exe), *args), cwd=handle.root, env=env, deadline=deadline,
                                           cleanup_deadline=deadline + live_dev01.CLEANUP_SECONDS,
                                           stdin_data=data, output_limit=1024 * 1024, inherit_console=inherit_console)
            quiet = outcome.remaining_process_count == 0
            return outcome

        version = job(("--version",), seconds=12)
        if version.status != "EXITED" or version.exit_code != 0 or version.stdout.strip() != access_check.CODEX_VERSION:
            raise PermissionGateError("CODEX_VERSION", "expected Codex CLI 0.166.0")

        def dispatch(message: bytes, seconds: float, *, persist: bool, session_id: str | None):
            operator["launch_attempts"] = operator.get("launch_attempts", 0) + 1
            return job(live_dev01._turn_command(config, persist=persist, session_id=session_id),
                       seconds=min(seconds, live_dev01.TURN_SECONDS), data=message)

        def unchanged():
            return quiet and export_revision(base) == request.repository.initial_commit

        experiment(manager, handle, dispatch, unchanged)
    finally:
        if quiet:
            manager.cleanup(handle)
            operator["workspace_cleaned"] = True
        else:
            operator["workspace_cleaned"] = False
            operator["workspace_preserved"] = True


def verify(output: Path, *, access_record: Path = ACCESS_RECORD, record_sha256: str | None = ACCESS_RECORD_SHA256,
           inherit_console: bool = False) -> dict:
    """Run the A–E gate on real fresh workspaces without starting an agent turn."""
    output = checked_path(output, allow_missing=True)
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "1.0", "profile": PROFILE, "task_id": TASK, "agent_turns": 0, "model_calls": 0,
              "access_record_sha256": record_sha256, "conditions": {},
              "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    try:
        record = load_access_record(access_record, sha256=record_sha256)
        task, fixed, repository = task_inputs()
        experiment_id = "main-runner-gate-" + uuid4().hex
        config_hash = sha256_bytes(canonical_json_bytes({"profile": PROFILE, "access": record_sha256}))
        for condition in Condition:
            operator = report["conditions"][condition.value] = {"launch_attempts": 0}
            request = RunRequest(str(uuid4()), task.task_id, experiment_id, repository, fixed,
                                 resolve_condition(condition), config_hash, "research")
            operator["run_id"] = request.run_id

            def no_turn(manager, handle, dispatch, unchanged, operator=operator):
                operator["ready_for_turn"] = unchanged()

            work_root = Path(mkdtemp(prefix="aah-main-runner-"))
            try:
                run_slot(request, record=record, work_root=work_root, artifact_root=output / "artifacts" / condition.value,
                         operator=operator, experiment=no_turn, inherit_console=inherit_console)
            finally:
                if work_root.exists() and not any(work_root.iterdir()):
                    work_root.rmdir()
        ready = all(o.get("ready_for_turn") and o["permission_gate"]["status"] == o["snapshot_gate"]["status"] == "MATCH"
                    and o["launch_attempts"] == 0 and o["workspace_cleaned"] for o in report["conditions"].values())
        report["status"] = "PASS" if ready else "FAIL"
    except PermissionGateError as error:
        report.update(status="BLOCKED", code=error.code, reason=str(error))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status="BLOCKED", code=getattr(error, "stage", type(error).__name__), reason=str(error)
                      if isinstance(error, (ContractError, access_check.AccessBlocked)) else type(error).__name__)
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store_bytes(output, "gate.json", canonical_json_bytes(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", required=True,
                        help="check the A–E permission gate without agent turns")
    parser.add_argument("--output", type=Path, required=True, help="a new directory for the gate report")
    parser.add_argument("--inherit-console", action="store_true", help="keep the existing console inside the job")
    args = parser.parse_args()
    report = verify(args.output, inherit_console=args.inherit_console)
    path = args.output / "gate.json"
    if report["status"] != "PASS":
        print("MAIN_RUNNER_GATE_" + report["status"], report.get("code", ""), path)
        if "reason" in report:
            print(report["reason"], file=sys.stderr)
        return 2 if report["status"] == "BLOCKED" else 1
    hashes = {o["permission_gate"]["observed"] for o in report["conditions"].values()}
    print("MAIN_RUNNER_GATE_PASS", path, f"conditions=5/5 permission_settings_hash={hashes.pop()} agent_turns=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
