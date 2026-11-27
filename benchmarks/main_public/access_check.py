"""Check AAH-S03 A–E workspace access on the Main MXC path with synthetic canaries (BQ09/BQ10).

No Codex agent turn is started. Each condition gets a fresh work root and public copy. The
reviewed MXC permission profile of the Dev/Pilot operator path (``live_dev01``) runs a fixed
probe that reads the public files and attempts to read, modify or create synthetic private
files outside the workspace. The host then compares A–E snapshots, mandatory instructions,
normalized permission settings and observed access.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4

from aah.domain import (Condition, ContractError, FixedRequirements, RepositoryRef, RunRequest,
                        canonical_json_bytes, dump_record, resolve_condition, sha256_bytes)
from aah.runtime.probe import _find_codex
from aah.workspace import WorkspaceManager, export_revision
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, disjoint
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import live_dev01


PROFILE = "main-s03-mxc-access-v1"
TASK = "AAH-S03"
CONDITIONS = tuple(Condition)
CODEX_VERSION = b"codex-cli 0.166.0"
PROBE_SECONDS = 20
CLEANUP_SECONDS = live_dev01.CLEANUP_SECONDS
CHECKOUT = live_dev01.CHECKOUT
# Same names as the operator path in live_dev01._run; a test pins the two lists together.
ENV_NAMES = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
             "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
             "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE", "OS", "PROCESSOR_ARCHITECTURE",
             "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS", "CODEX_HOME", "LANG")
ENV_CANARY = "AAH_SYNTHETIC_PRIVATE_CANARY"
CANARY = b"AAH synthetic private canary. It contains no secret, answer or credential.\n"
# Synthetic stand-ins for curator-side material. Real hidden tests and answers are never used.
PRIVATE_FILES = {
    "private_hidden_test": "hidden_checks/test_private_synthetic.py",
    "private_reference_patch": "reference/patch-synthetic.diff",
    "private_evaluation_result": "evaluation/result-synthetic.json",
}
SCRIPT_NAME = "aah-main-access-probe.py"
LINK_NAME = "aah-main-access-link"
NO_ACCESS = {"DENIED", "NOT_VISIBLE"}
PROBE_STEPS = ("PROBE_STARTED", "PUBLIC_FILES_READ", "PRIVATE_ACCESS_RECORDED", "WORKSPACE_WRITABLE",
               "HOST_LOOPBACK_DENIED")
PROBE_SOURCE = r'''
import errno, hashlib, json, os, socket, sys
from pathlib import Path

print("PROBE_STARTED", flush=True)
port, env_canary, link_target, *targets = sys.argv[1:]
root = Path.cwd()
script = root / "aah-main-access-probe.py"


def code(error):
    return f"ERROR:{getattr(error, 'winerror', None) or error.errno}"


def attempt(path, mode):
    try:
        with open(path, mode):
            return "ALLOWED"
    except PermissionError:
        return "DENIED"
    except (FileNotFoundError, NotADirectoryError):
        return "NOT_VISIBLE"
    except OSError as error:
        return code(error)


public = {}
for path in sorted(root.rglob("*")):
    if path.is_file() and path != script:
        public[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
print("PUBLIC_FILES_READ", flush=True)

private, created = {}, {}
for entry in targets:
    kind, role, value = entry.split("=", 2)
    if kind == "file":
        # r+b proves write access without changing the canary bytes.
        private[role] = {"read": attempt(value, "rb"), "modify": attempt(value, "r+b")}
    else:
        outcome = attempt(os.path.join(value, "aah-probe-created.txt"), "xb")
        if outcome == "ALLOWED":
            os.unlink(os.path.join(value, "aah-probe-created.txt"))
        created[role] = outcome

try:
    os.symlink(link_target, root / "aah-main-access-link")
except OSError:
    symlink = "NOT_CREATABLE"
else:
    try:
        symlink = attempt(root / "aah-main-access-link", "rb")
    finally:
        os.unlink(root / "aah-main-access-link")

git_visible = False
for directory in (root, *root.parents):
    try:
        git_visible = git_visible or (directory / ".git").exists()
    except OSError:
        pass
print("PRIVATE_ACCESS_RECORDED", flush=True)

work_test = root / "aah-mxc-test.txt"
try:
    with work_test.open("xb") as file:
        file.write(b"synthetic")
    work_test.unlink()
except OSError as error:
    raise SystemExit(f"WORKSPACE_WRITE_ERROR:{getattr(error, 'winerror', None) or error.errno}") from None
print("WORKSPACE_WRITABLE", flush=True)

try:
    with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
        raise SystemExit("HOST_LOOPBACK_ACCESSIBLE")
except TimeoutError:
    pass
except OSError as error:
    allowed = (errno.EACCES, errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH, 10013, 10061)
    winerror = getattr(error, "winerror", None)
    if error.errno not in allowed and winerror not in allowed:
        raise SystemExit(f"LOOPBACK_PROBE_ERROR:{winerror or error.errno}") from None
print("HOST_LOOPBACK_DENIED", flush=True)

names = sorted(name.upper() for name in os.environ)
print("PROBE_RESULT " + json.dumps({
    "public_files": public, "private_files": private, "private_directory_create": created,
    "symlink_to_private": symlink, "git_metadata_visible": git_visible,
    "env_canary_visible": env_canary in os.environ,
    "env_names_sha256": hashlib.sha256(json.dumps(names).encode()).hexdigest(),
    "workspace_writable": True, "host_loopback": "DENIED",
}, sort_keys=True), flush=True)
'''


class AccessBlocked(RuntimeError):
    """The check could not reach a verdict; ``stage`` names the step that stopped."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage = stage


def _launch(argv, **kwargs):
    from aah.runtime.windows_job import run_windows_job

    return run_windows_job(argv, **kwargs)


def _tree_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256_bytes(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file()}


def _normalize(args: tuple[str, ...], places: dict[Path, str]) -> list[str]:
    """Replace host-specific absolute paths so A–E settings compare without leaking them."""
    forms = sorted(((form, label) for path, label in places.items() for form in {str(path), path.as_posix()}),
                   key=lambda item: -len(item[0]))
    normalized = []
    for arg in args:
        for form, label in forms:
            arg = arg.replace(form, label)
        normalized.append(arg)
    if any(":\\" in arg or ":/" in arg for arg in normalized):
        raise ContractError("permission settings contain an unnormalized absolute path")
    return normalized


def operator_environment() -> tuple[Path, dict[str, str], Path, Path]:
    """Codex binary, filtered environment, login directory and Python used on the operator MXC path."""
    exe = _find_codex()
    if exe is None:
        raise AccessBlocked("CODEX_DISCOVERY", "native Codex CLI not found")
    env = {name: os.environ[name] for name in ENV_NAMES if name in os.environ}
    credential_root = Path(env.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    if credential_root == Path(credential_root.anchor) or not credential_root.is_dir():
        raise AccessBlocked("CODEX_DISCOVERY", "could not locate the local Codex login directory")
    return exe, env, credential_root, Path(sys.executable).resolve()


def permission_settings(workspace: Path, credential_root: Path, python: Path):
    """Build the live turn/sandbox arguments and their host-independent normalized hash."""
    places = {workspace: "<workspace>", CHECKOUT: "<checkout>", credential_root: "<codex-home>",
              python.parent: "<python-dir>"}
    config = live_dev01._configuration(workspace, credential_root, python)
    sandbox = live_dev01._sandbox_command(workspace, credential_root, python)
    settings = {"mxc_profile": live_dev01.PROFILE, "turn_configuration": _normalize(config, places),
                "sandbox_command": _normalize(sandbox, places)}
    return config, sandbox, settings, sha256_bytes(canonical_json_bytes(settings))


def _probe_outcome(job) -> dict:
    lines = job.stdout.decode("utf-8", "replace").splitlines()
    steps = [step for step in PROBE_STEPS if step in lines]
    diagnostics = live_dev01._preflight_result(job)
    observed = next((line.removeprefix("PROBE_RESULT ") for line in lines if line.startswith("PROBE_RESULT ")), None)
    outcome = {"status": job.status, "exit_code": job.exit_code, "steps": steps,
               "signals": diagnostics["signals"], "remaining_process_count": job.remaining_process_count,
               "result": json.loads(observed) if observed else None}
    if "native_errors" in diagnostics:
        outcome["native_errors"] = diagnostics["native_errors"]
    for line in lines:
        if line.startswith(("WORKSPACE_WRITE_ERROR:", "LOOPBACK_PROBE_ERROR:", "HOST_LOOPBACK_ACCESSIBLE")):
            outcome["signals"].append(line.strip()[:64])
    return outcome


def _access_failures(observed: dict, expected_public: dict[str, str]) -> list[str]:
    failures = []
    if observed["public_files"] != expected_public:
        failures.append("PUBLIC_FILES_MISMATCH")
    for role, modes in observed["private_files"].items():
        failures += [f"{role.upper()}_{mode.upper()}_{state}" for mode, state in modes.items() if state not in NO_ACCESS]
    failures += [f"{role.upper()}_CREATE_{state}" for role, state in observed["private_directory_create"].items()
                 if state not in NO_ACCESS]
    if observed["symlink_to_private"] not in NO_ACCESS | {"NOT_CREATABLE"}:
        failures.append(f"SYMLINK_TO_PRIVATE_{observed['symlink_to_private']}")
    if observed["git_metadata_visible"]:
        failures.append("GIT_METADATA_VISIBLE")
    if observed["env_canary_visible"]:
        failures.append("ENV_CANARY_VISIBLE")
    return failures


def _check_condition(condition, record, *, task, fixed, repository, experiment_id, config_hash, exe, env,
                     credential_root, python, private_root, controller_canary, output, inherit_console):
    run_id = str(uuid4())
    request = RunRequest(run_id, task.task_id, experiment_id, repository, fixed, resolve_condition(condition),
                         config_hash, "research")
    work_root = Path(mkdtemp(prefix="aah-main-access-"))
    record.update(run_id=run_id, policies=json.loads(dump_record(request.policies)), stage="WORKSPACE_PREPARE")
    quiet = True
    manager = handle = None
    outside_canary = work_root / "outside-workspace-canary.txt"
    try:
        disjoint(work_root, CHECKOUT)
        disjoint(work_root, private_root)
        manager = WorkspaceManager(repository.source_path, work_root, output / "artifacts" / condition.value,
                                   quiescence_check=lambda _: quiet)
        handle = manager.prepare(request)
        if [path.name for path in work_root.iterdir()] != [run_id]:
            raise AccessBlocked("WORKSPACE_PREPARE", "the new work root is not empty")
        initial = manager.snapshot(handle)
        public = _tree_hashes(handle.root)
        record.update(
            fresh_work_root=True, isolation_profile_id=handle.isolation_profile_id,
            initial_snapshot_id=initial.snapshot_id, initial_revision=export_revision(handle.root),
            fixed_requirements_hash=sha256_bytes(dump_record(fixed)), mandatory_hash=fixed.mandatory_hash,
            approved_check_ids=list(fixed.approved_check_ids), public_file_hashes=public,
        )
        outside_canary.write_bytes(CANARY)
        _config, sandbox, record["permission_settings"], record["permission_settings_hash"] = permission_settings(
            handle.root, credential_root, python)

        def job(args, seconds):
            nonlocal quiet
            quiet = False
            deadline = monotonic() + seconds
            outcome = _launch((str(exe), *args), cwd=handle.root, env=env, deadline=deadline,
                              cleanup_deadline=deadline + CLEANUP_SECONDS, output_limit=1024 * 1024,
                              inherit_console=inherit_console)
            quiet = outcome.remaining_process_count == 0
            return outcome

        record["stage"] = "CODEX_VERSION"
        version = job(("--version",), 12)
        if version.status != "EXITED" or version.exit_code != 0 or version.stdout.strip() != CODEX_VERSION:
            raise AccessBlocked("CODEX_VERSION", "expected Codex CLI 0.166.0")

        protected = {"outside_workspace_sibling": outside_canary, "controller_record": controller_canary,
                     "original_public_source": repository.source_path / task.allowed_change_paths[0],
                     **{role: private_root / relative for role, relative in PRIVATE_FILES.items()}}
        before = {role: path.read_bytes() for role, path in protected.items()}
        private_listing = sorted(p.relative_to(private_root).as_posix() for p in private_root.rglob("*"))
        targets = [f"file={role}={path}" for role, path in protected.items()]
        # Relative parent traversal from the workspace to the sibling canary.
        targets.append(f"file=outside_workspace_parent_path={Path('..') / outside_canary.name}")
        targets += [f"dir=private_{name}={private_root / name}" for name in sorted(
            {Path(relative).parts[0] for relative in PRIVATE_FILES.values()})]
        targets.append(f"dir=work_root={work_root}")

        record["stage"] = "MXC_PROBE"
        script = handle.root / SCRIPT_NAME
        script.write_text(PROBE_SOURCE, encoding="utf-8")
        os.environ[ENV_CANARY] = "synthetic-not-a-secret"
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                probe = job((*sandbox, str(python), "-I", "-S", "-B", str(script), str(listener.getsockname()[1]),
                             ENV_CANARY, str(protected["private_hidden_test"]), *targets), PROBE_SECONDS)
        finally:
            os.environ.pop(ENV_CANARY, None)
            if quiet:
                script.unlink(missing_ok=True)
                (handle.root / LINK_NAME).unlink(missing_ok=True)
        outcome = _probe_outcome(probe)
        record["probe"] = outcome
        if (probe.status != "EXITED" or probe.exit_code != 0 or outcome["result"] is None
                or len(outcome["steps"]) != len(PROBE_STEPS)):
            last = outcome["steps"][-1] if outcome["steps"] else "NOT_STARTED"
            raise AccessBlocked(f"MXC_PROBE:{last}", ", ".join(outcome["signals"]) or
                                f"status={probe.status} exit={probe.exit_code}")

        record["stage"] = "HOST_VERIFY"
        after = manager.snapshot(handle)
        record["snapshot_after_probe_id"] = after.snapshot_id
        record["host_checks"] = {
            "canaries_unchanged": all(path.read_bytes() == before[role] for role, path in protected.items()),
            "private_listing_unchanged": private_listing == sorted(
                p.relative_to(private_root).as_posix() for p in private_root.rglob("*")),
            "work_root_unchanged": sorted(p.name for p in work_root.iterdir()) == sorted([run_id, outside_canary.name]),
            "workspace_unchanged": after.snapshot_id == initial.snapshot_id and _tree_hashes(handle.root) == public,
            "processes_quiet": quiet,
        }
        failures = _access_failures(outcome["result"], public)
        failures += [name.upper() + "_FALSE" for name, passed in record["host_checks"].items() if not passed]
        record["access_failures"] = failures
        record["stage"] = "DONE"
    finally:
        if handle is not None and quiet:
            manager.cleanup(handle)
            record["workspace_cleaned"] = True
        elif handle is not None:
            record["workspace_cleaned"] = False
        if quiet:
            outside_canary.unlink(missing_ok=True)
            if work_root.exists() and not any(work_root.iterdir()):
                work_root.rmdir()


PARITY_FIELDS = ("initial_snapshot_id", "initial_revision", "fixed_requirements_hash", "mandatory_hash",
                 "approved_check_ids", "isolation_profile_id", "public_file_hashes", "permission_settings_hash")


def _parity(records: dict) -> dict:
    compared = {field: {c: records[c].get(field) for c in records} for field in PARITY_FIELDS}
    compared["observed_access"] = {c: records[c].get("probe", {}).get("result") for c in records}
    mismatches = [field for field, values in compared.items()
                  if len({canonical_json_bytes(value) for value in values.values()}) != 1 or None in values.values()]
    return {"status": "PASS" if not mismatches and len(records) == len(CONDITIONS) else "FAIL",
            "compared": list(compared), "mismatches": mismatches}


def execute(output: Path, *, task_id: str = TASK, inherit_console: bool = False) -> dict:
    output = checked_path(output, allow_missing=True)
    output.mkdir(parents=True, exist_ok=False)
    directory, task, spec_hash = read_task(task_id)
    base = directory / "base"
    fixed = FixedRequirements(task.task, task.mandatory_text, sha256_bytes(task.mandatory_text.encode("utf-8")),
                              "public-copy-only", task.approved_check_ids)
    repository = RepositoryRef(task.repo_id, base, export_revision(base))
    report = {"schema_version": "1.0", "profile": PROFILE, "task_id": task_id, "synthetic": True,
              "agent_turns": 0, "model_calls": 0, "mxc_profile": live_dev01.PROFILE,
              "public_spec_sha256": spec_hash, "base_revision": repository.initial_commit,
              "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "private_canary_sha256": sha256_bytes(CANARY), "conditions": {}}
    private_root = Path(mkdtemp(prefix="aah-main-private-synthetic-"))
    try:
        try:
            exe, env, credential_root, python = operator_environment()
        except AccessBlocked as blocked:
            report.update(status="BLOCKED", blocked_condition=None, blocked_stage=blocked.stage, reason=str(blocked))
            return report
        for relative in PRIVATE_FILES.values():
            (private_root / relative).parent.mkdir(parents=True, exist_ok=True)
            (private_root / relative).write_bytes(CANARY)
        controller_canary = output / "controller-canary.txt"
        controller_canary.write_bytes(CANARY)
        config_hash = sha256_bytes(canonical_json_bytes({"profile": PROFILE, "mxc": live_dev01.PROFILE}))
        experiment_id = "main-access-" + uuid4().hex
        report["experiment_id"] = experiment_id
        for condition in CONDITIONS:
            print("ACCESS_CONDITION_START", condition.value, flush=True)
            try:
                report["conditions"][condition.value] = record = {}
                _check_condition(
                    condition, record, task=task, fixed=fixed, repository=repository, experiment_id=experiment_id,
                    config_hash=config_hash, exe=exe, env=env, credential_root=credential_root, python=python,
                    private_root=private_root, controller_canary=controller_canary, output=output,
                    inherit_console=inherit_console)
            except AccessBlocked as blocked:
                report.update(status="BLOCKED", blocked_condition=condition.value, blocked_stage=blocked.stage,
                              reason=str(blocked))
                return report
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                report.update(status="BLOCKED", blocked_condition=condition.value,
                              blocked_stage="UNEXPECTED", reason=type(error).__name__ + (
                                  f": {error}" if isinstance(error, ContractError) else ""))
                return report
            print("ACCESS_CONDITION_DONE", condition.value,
                  "PASS" if not record["access_failures"] else "FAIL", flush=True)
        records = report["conditions"]
        bq09_failures = {c: r["access_failures"] for c, r in records.items() if r["access_failures"]}
        report["bq09"] = {
            "status": "PASS" if not bq09_failures else "FAIL", "failures": bq09_failures,
            "iso_coverage": {
                "ISO-01": "private canary files and environment canary unreadable from the MXC agent profile",
                "ISO-02": "no .git metadata visible from the workspace",
                "ISO-03": "parent-relative path and symlink to a private canary blocked",
                "ISO-04": "partial: network disabled in profile and host loopback denied; no external probe",
                "ISO-05": "synthetic evaluator result outside the workspace unreadable",
                "ISO-06": "fresh work root and public copy per condition; unchanged after probe",
                "ISO-07": "not covered by this check",
                "ISO-08": "not covered by this check (see main_preflight evaluator replay)",
            },
        }
        report["bq10"] = _parity(records)
        report["status"] = "PASS" if report["bq09"]["status"] == report["bq10"]["status"] == "PASS" else "FAIL"
        return report
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if all(r.get("workspace_cleaned", True) for r in report["conditions"].values()):
            shutil.rmtree(private_root, ignore_errors=True)
        else:
            report["private_canaries_preserved"] = True
        store_bytes(output, "access.json", canonical_json_bytes(report))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="a new directory for this check")
    parser.add_argument("--inherit-console", action="store_true", help="keep the existing console inside the job")
    parser.add_argument("--task", default=TASK, help="public Main candidate ID")
    args = parser.parse_args()
    if os.name != "nt":
        print("MAIN_ACCESS_BLOCKED PLATFORM requires Windows and native MXC")
        return 2
    report = execute(args.output, task_id=args.task, inherit_console=args.inherit_console)
    path = args.output / "access.json"
    if report["status"] == "BLOCKED":
        print("MAIN_ACCESS_BLOCKED", report["blocked_condition"], report["blocked_stage"], path)
        print(report["reason"], file=sys.stderr)
        for heading in report["conditions"].get(report["blocked_condition"], {}).get("probe", {}).get(
                "native_errors", ()):
            print(heading, file=sys.stderr)
        return 2
    print(f"MAIN_ACCESS_{report['status']}", path, f"BQ09={report['bq09']['status']}",
          f"BQ10={report['bq10']['status']}", "agent_turns=0")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
