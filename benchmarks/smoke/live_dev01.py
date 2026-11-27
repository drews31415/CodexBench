"""Run one operator-authorized DEV-01 turn with native MXC and Docker evaluation."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from uuid import UUID

from aah.domain import ContractError, EvaluationResult, dump_record, load_record, sha256_bytes
from aah.reporting.store import RunStore
from aah.runtime import RuntimeConfig
from aah.runtime.probe import _find_codex
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run_prepared_a
from aah.workspace import WorkspaceManager
from evaluation.runner import EvaluationRequest, fixture_manifest_hash
from evaluation.smoke import CHECKOUT, _evaluate_separately, fixture_request

from . import docker_worker, live_s1, runtime_checks, runtime_usage
from .public_checks import run_approved


PROFILE = "aah_dev01_mxc"
MODEL = "gpt-6-luna"
EFFORT = "low"
TURN_SECONDS = 105
CLEANUP_SECONDS = 15
ITEM_EVENTS = {"item.started", "item.updated", "item.completed"}
ITEM_TYPES = {"agent_message", "reasoning", "command_execution", "file_change",
              "mcp_tool_call", "collab_tool_call", "web_search", "todo_list", "error"}
EVENT_TYPES = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error"} | ITEM_EVENTS
PROBE_STEPS = ("PROBE_STARTED", "PROTECTED_FILES_DENIED", "WORKSPACE_WRITABLE", "HOST_LOOPBACK_DENIED")
PROBE_SOURCE = """
import errno, socket, sys
from pathlib import Path

print("PROBE_STARTED", flush=True)
port, *protected_paths = sys.argv[1:]
for value in protected_paths:
    path = Path(value)
    for mode in ("rb", "r+b"):
        try:
            with path.open(mode):
                raise SystemExit("PROTECTED_FILE_ACCESSIBLE")
        except (FileNotFoundError, PermissionError):
            # The host verifies the canary exists before and after the sandbox.
            continue
        except OSError as error:
            raise SystemExit(f"PROTECTED_FILE_PROBE_ERROR:{getattr(error, 'winerror', None) or error.errno}") from None
print("PROTECTED_FILES_DENIED", flush=True)

work_test = Path("aah-mxc-test.txt")
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
"""


def _preflight_result(probe) -> dict:
    lines = set(probe.stdout.decode("utf-8", "replace").splitlines())
    steps = [step for step in PROBE_STEPS if step in lines]
    diagnostics = (probe.stdout + b"\n" + probe.stderr).lower()
    signals = {
        b"protected_file_accessible": "PROTECTED_FILE_ACCESSIBLE",
        b"host_loopback_accessible": "HOST_LOOPBACK_ACCESSIBLE",
        b"native mxc is unavailable": "MXC_UNAVAILABLE",
        b"failed to prepare mxc sandbox": "MXC_SETUP_FAILED",
        b"os error 5": "WINDOWS_ACCESS_DENIED",
        b"access is denied": "WINDOWS_ACCESS_DENIED",
        "액세스가 거부".encode(): "WINDOWS_ACCESS_DENIED",
        b"0x80070005": "WINDOWS_ACCESS_DENIED",
        b"0x80070003": "WINDOWS_PATH_UNAVAILABLE",
        b"filesystempermissiontoml": "PERMISSION_PROFILE_INVALID",
        b"unknown permission profile": "PERMISSION_PROFILE_UNKNOWN",
        b"no module named": "PYTHON_IMPORT_FAILED",
        b"fatal python error": "PYTHON_STARTUP_FAILED",
        b"can't open file": "PYTHON_PROBE_FILE_UNAVAILABLE",
    }
    codes = list(dict.fromkeys(code for marker, code in signals.items() if marker in diagnostics))
    # Keep numeric OS errors and synthetic stage names, without host paths or raw stderr.
    for line in diagnostics.splitlines():
        name, _, error = line.strip().partition(b":")
        if (name in (b"protected_file_probe_error", b"workspace_write_error", b"loopback_probe_error")
                and error.isdigit() and len(error) <= 8):
            codes.append(line.strip().decode("ascii").upper())
    passed = probe.status == "EXITED" and probe.exit_code == 0 and len(steps) == len(PROBE_STEPS)
    result = {"status": probe.status, "exit_code": probe.exit_code, "steps": steps, "signals": codes,
              "stdout_bytes": len(probe.stdout), "stderr_bytes": len(probe.stderr), "passed": passed}
    if not passed:
        # This parser only receives offline sandbox/echo output. Keep error headings
        # without quoted values, filesystem paths or lines about credentials.
        errors = []
        for line in probe.stderr.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line.lower().startswith(("error:", "caused by:", "mxc launcher:")):
                continue
            if re.search(r"(?i)auth|token|secret|password|credential|api[_ -]?key|bearer|@", line):
                continue
            line = re.sub(r'''(?:"[^"]*"|'[^']*'|`[^`]*`)''', "<value>", line)
            line = re.split(r"(?i)[a-z]:[\\/]|\\\\|https?://", line, maxsplit=1)[0]
            line = re.sub(r"[^\x20-\x7e]", "", line).rstrip()
            if line:
                errors.append(line[:240])
            if len(errors) >= 3:
                break
        if errors:
            result["native_errors"] = errors
    return result


def _mxc_controls(exe: Path, workspace: Path, env: dict[str, str], sandbox: tuple[str, ...], job) -> tuple[dict, bool]:
    """Compare the standard and restricted profiles with fixed model-free commands."""
    cmd = str(Path(env["SYSTEMROOT"]) / "System32" / "cmd.exe")
    marker = b"AAH_MXC_CONTROL_OK"
    shell = (cmd, "/d", "/c", "echo", marker.decode("ascii"))
    shared = ("--include-managed-config", "--permission-profile")
    basic = ("-c", "windows.sandbox=mxc", "sandbox", *shared, ":workspace", "--", *shell)
    strict = (*sandbox, *shell)

    def summarize(result) -> dict:
        outcome = _preflight_result(result)
        outcome.pop("steps")
        outcome["passed"] = (result.status == "EXITED" and result.exit_code == 0
                             and marker in result.stdout.splitlines())
        return outcome

    def direct(args: tuple[str, ...]) -> dict:
        # The direct control runs only the built-in echo, without a user script or model.
        try:
            result = subprocess.run((str(exe), *args), cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=8)
            outcome = SimpleNamespace(status="EXITED", exit_code=result.returncode,
                                      stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired as exc:
            outcome = SimpleNamespace(status="TIMED_OUT", exit_code=None,
                                      stdout=exc.stdout or b"", stderr=exc.stderr or b"")
        return summarize(outcome)

    results = {
        "standard_direct": direct(basic),
        "standard_job": summarize(job(basic, seconds=8)),
        "restricted_direct": direct(strict),
        "restricted_job": summarize(job(strict, seconds=8)),
        "restricted_job_existing_console": summarize(job(strict, seconds=8, inherit_console=True)),
    }
    console = not results["restricted_job"]["passed"] and results["restricted_job_existing_console"]["passed"]
    return results, console


class SmokeBlocked(RuntimeError):
    """A precondition or postcondition for the live operator run failed."""


def _permission_override(workspace: Path, credential_root: Path, python: Path) -> str:
    # Literal absolute paths avoid inheriting a repository or the system temp tree as writable.
    filesystem = {
        ":root": "deny", ":minimal": "read", python.parent.as_posix(): "read", workspace.as_posix(): "write",
        CHECKOUT.as_posix(): "deny", credential_root.as_posix(): "deny",
    }
    entries = ", ".join(f"{json.dumps(path, ensure_ascii=False)}={json.dumps(mode)}"
                        for path, mode in filesystem.items())
    return f"permissions.{PROFILE}={{filesystem={{{entries}}}, network={{enabled=false}}}}"


def _configuration(workspace: Path, credential_root: Path, python: Path) -> tuple[str, ...]:
    overrides = (
        'windows.sandbox="mxc"', f'default_permissions="{PROFILE}"',
        'approval_policy="never"', 'web_search="disabled"', 'project_doc_max_bytes=0',
        'shell_environment_policy.inherit="none"', 'allow_login_shell=false', 'model_provider="openai"',
        _permission_override(workspace, credential_root, python),
    )
    args = tuple(token for setting in overrides for token in ("-c", setting))
    disabled = ("apps", "plugins", "hooks", "shell_snapshot", "browser_use", "computer_use", "image_generation",
                "multi_agent", "multi_agent_v2", "daemon_auto_start", "auth_elicitation")
    return (*args, *(token for feature in disabled for token in ("--disable", feature)))


def _sandbox_command(workspace: Path, credential_root: Path, python: Path) -> tuple[str, ...]:
    return ("-c", "windows.sandbox=mxc", "-c", _permission_override(workspace, credential_root, python),
            "sandbox", "--include-managed-config", "--permission-profile", PROFILE, "--cd", str(workspace), "--")


def _turn_command(config: tuple[str, ...], *, persist: bool = False, session_id: str | None = None) -> tuple[str, ...]:
    flags = ("--ignore-user-config", "--skip-git-repo-check", "--json", "--model", MODEL,
             "-c", f'model_reasoning_effort="{EFFORT}"')
    if session_id is not None:
        # Resume only a canonical ID observed in this verification run.
        return ("exec", "resume", *config, *flags, "--", str(UUID(session_id)), "-")
    return ("exec", *config, *flags, "--color", "never", *(() if persist else ("--ephemeral",)), "--", "-")


def _event_summary(data: bytes) -> dict:
    counts: Counter[str] = Counter()
    item_counts: Counter[str] = Counter()
    unknown_items = unknown_events = malformed_lines = 0
    thread = None
    usage = None
    failures = set()
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            malformed_lines += 1
            continue
        if not isinstance(event, dict) or not isinstance(kind := event.get("type"), str):
            malformed_lines += 1
            continue
        if kind not in EVENT_TYPES:
            unknown_events += 1
            continue
        counts[kind] += 1
        if kind in ITEM_EVENTS:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if isinstance(item_type, str) and item_type in ITEM_TYPES:
                item_counts[item_type] += 1
            else:
                unknown_items += 1
        elif kind == "thread.started":
            observed_id = event.get("thread_id")
            try:
                if isinstance(observed_id, str):
                    thread = str(UUID(observed_id))
            except ValueError:
                pass
        elif kind == "turn.completed":
            usage = runtime_usage.read_usage(event.get("usage"))
        if kind in {"error", "turn.failed"}:
            error = event.get("error")
            content = error if isinstance(error, dict) else event
            message = content.get("message")
            reason = message.casefold() if isinstance(message, str) else ""
            code = content.get("code")
            if code in ("model_not_found", "unsupported_model") or (
                    "model" in reason and any(term in reason for term in ("not supported", "not available",
                                                                          "does not exist", "no access"))):
                failures.add("MODEL_UNAVAILABLE")
            elif code == "usage_limit_reached" or "usage limit" in reason:
                failures.add("USAGE_LIMIT")
            elif code == "rate_limit_exceeded" or "rate limit" in reason or "http 429" in reason:
                failures.add("RATE_LIMIT")
            elif code in ("unauthorized", "invalid_api_key") or "http 401" in reason or "unauthorized" in reason:
                failures.add("AUTHENTICATION_FAILED")
            elif "stream disconnected" in reason or "failed to connect" in reason:
                failures.add("TRANSPORT_FAILED")
    if not failures and (counts["error"] or counts["turn.failed"]):
        failures.add("UNCLASSIFIED_CODEX_ERROR")
    if counts["turn.completed"] != 1 or counts["turn.failed"]:
        usage = None
    coverage = {"malformed_lines": malformed_lines, "unknown_events": unknown_events,
                "item_event_counts": dict(item_counts), "unknown_item_events": unknown_items}
    return {"thread_id": thread, "event_counts": dict(counts), "usage": usage,
            "usage_mode": runtime_usage.USAGE_MODE, "event_coverage": coverage,
            "failure_codes": sorted(failures), "stdout_sha256": sha256_bytes(data)}


def _independent_verdict(directory, artifact_root, request, patch):
    manifest = fixture_manifest_hash(request.task_id, patch.base_commit, in_docker=True, dev01_candidate=True)
    envelope = EvaluationRequest(request.run_id, request.task_id, patch.base_commit, manifest, patch)
    request_path = directory / "evaluation-request.json"
    request_path.write_bytes(dump_record(envelope))
    results = directory / "evaluation"
    _evaluate_separately(request_path, artifact_root, results, in_docker=True, dev01_candidate=True)
    verdict = load_record(EvaluationResult, (results / f"{request.run_id}.json").read_bytes())
    if (verdict.run_id, verdict.task_id, verdict.patch_hash) != (patch.run_id, request.task_id, patch.patch_hash):
        raise SmokeBlocked("The independent verdict is not bound to the runtime patch")
    return verdict


def _run(directory: Path, work_root: Path, request, report: dict, *, execute: bool,
         diagnose_mxc: bool = False, inherit_console: bool = False, verify_runtime: bool = False,
         verify_rt04: bool = False, s1_store: RunStore | None = None, experiment=None) -> int:
    if os.name != "nt":
        raise SmokeBlocked("This profile requires Windows and MXC")
    from aah.runtime.windows_job import run_windows_job

    exe = _find_codex()
    if exe is None:
        raise SmokeBlocked("Native Codex CLI not found")
    # Keep the existing login store discoverable and omit API keys and arbitrary inherited variables.
    env_names = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
                 "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
                 "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE", "OS", "PROCESSOR_ARCHITECTURE",
                 "PROCESSOR_ARCHITEW6432", "NUMBER_OF_PROCESSORS", "CODEX_HOME", "LANG")
    env = {name: os.environ[name] for name in env_names if name in os.environ}
    credential_root = Path(env.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    if credential_root.resolve() == Path(credential_root.anchor) or not credential_root.is_dir():
        raise SmokeBlocked("Could not locate the local Codex login directory")
    python = Path(sys.executable).resolve()
    baseline = request.repository.source_path / "module.py"
    frozen_base = baseline.read_bytes()
    quiet = True
    artifact_root = directory / "artifacts"
    manager = WorkspaceManager(request.repository.source_path, work_root, artifact_root,
                               quiescence_check=lambda _: quiet)
    handle = manager.prepare(request)
    try:
        config = _configuration(handle.root, credential_root, python)
        sandbox = _sandbox_command(handle.root, credential_root, python)
        public_module = handle.root / "module.py"
        preflight_canary = directory / "preflight-canary.txt"
        outside_canary = work_root / "outside-workspace-canary.txt"
        protected = (preflight_canary, outside_canary)
        if verify_runtime:
            protected += (directory / "synthetic-evaluation-input.txt", directory / "synthetic-ledger.txt")
        canary = b"synthetic canary"
        for path in protected:
            path.write_bytes(canary)

        use_console = inherit_console

        def job(args: tuple[str, ...], *, seconds: float = 12, data: bytes | None = None,
                inherit_console: bool | None = None, cancel=None, on_poll=None):
            nonlocal quiet
            quiet = False
            deadline = monotonic() + seconds
            outcome = run_windows_job((str(exe), *args), cwd=handle.root, env=env, deadline=deadline,
                                      cleanup_deadline=deadline + CLEANUP_SECONDS, stdin_data=data,
                                      cancel=cancel, on_poll=on_poll,
                                      output_limit=1024 * 1024,
                                      inherit_console=use_console if inherit_console is None else inherit_console)
            quiet = outcome.remaining_process_count == 0
            return outcome

        version = job(("--version",))
        if version.status != "EXITED" or version.exit_code != 0 or version.stdout.strip() != b"codex-cli 0.166.0":
            raise SmokeBlocked("Expected Codex CLI 0.166.0")
        report["codex_version"] = "0.166.0"
        if diagnose_mxc:
            report["mxc_controls"], use_console = _mxc_controls(exe, handle.root, env, sandbox, job)
        report["inherit_console"] = use_console
        script = handle.root / "aah-mxc-preflight.py"
        script.write_text(PROBE_SOURCE, encoding="ascii")
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                probe = job((*sandbox, str(python), "-I", "-S", "-B", str(script),
                             str(listener.getsockname()[1]), *map(str, protected)))
        finally:
            # Never expose the preflight tool as part of the DEV-01 task input.
            if quiet:
                script.unlink(missing_ok=True)
        preflight = _preflight_result(probe)
        report["mxc_preflight"] = preflight
        if not preflight["passed"]:
            if not preflight["steps"] and "MXC_UNAVAILABLE" not in preflight["signals"]:
                command = Path(env["SYSTEMROOT"]) / "System32" / "cmd.exe"
                control = job((*sandbox, str(command), "/d", "/c", "echo", "AAH_MXC_SHELL_OK"))
                preflight["shell_control_passed"] = (
                    control.status == "EXITED" and control.exit_code == 0
                    and b"AAH_MXC_SHELL_OK" in control.stdout.splitlines()
                )
                preflight["signals"].extend(code for code in _preflight_result(control)["signals"]
                                            if code not in preflight["signals"])
                preflight["shell_control"] = _preflight_result(control)
            reason = ", ".join(preflight["signals"]) or (
                "PYTHON_PROBE_DID_NOT_START" if preflight.get("shell_control_passed") else "NO_PROBE_RESULT"
            )
            raise SmokeBlocked(f"MXC preflight failed ({reason})")
        if any(path.read_bytes() != canary for path in protected) or public_module.read_bytes() != frozen_base:
            raise SmokeBlocked("Preflight modified the expected input")
        try:
            report["docker"] = docker_worker.identity()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise SmokeBlocked("Docker evaluator engine or pinned image is unavailable") from exc
        login = job(("login", "status"))
        if login.exit_code != 0 or b"logged in using chatgpt" not in (login.stdout + login.stderr).lower():
            raise SmokeBlocked("Codex did not report a ChatGPT login")
        report["auth"] = "chatgpt"
        if experiment is not None:
            def unchanged():
                return quiet and baseline.read_bytes() == frozen_base and all(p.read_bytes() == canary for p in protected)

            def dispatch(message, seconds, *, persist, session_id):
                report["launch_attempts"] += 1
                return job(_turn_command(config, persist=persist, session_id=session_id),
                           seconds=min(seconds, TURN_SECONDS), data=message)

            experiment(manager, handle, dispatch, unchanged)
            return 0
        if verify_runtime or verify_rt04 or execute or s1_store:
            report.update(token_limit_mode=runtime_usage.TOKEN_LIMIT_MODE,
                          prompt_token_estimator=runtime_usage.TOKEN_ESTIMATOR)
        if s1_store:
            def unchanged():
                return (quiet and baseline.read_bytes() == frozen_base
                        and all(path.read_bytes() == canary for path in protected))

            def launch_initial(message: bytes, seconds: float):
                report.update(initial_message_sha256=sha256_bytes(message), launch_attempts=1,
                              model_requested=MODEL, reasoning_requested=EFFORT)
                (directory / "dispatch.json").write_bytes(json.dumps(report, indent=2).encode("utf-8"))
                result = job(_turn_command(config), seconds=min(seconds, TURN_SECONDS), data=message)
                report["runtime"] = {**_event_summary(result.stdout), "status": result.status,
                                     "exit_code": result.exit_code,
                                     "remaining_process_count": result.remaining_process_count,
                                     "stderr_sha256": sha256_bytes(result.stderr)}
                return result

            runtime_config = RuntimeConfig(MODEL, EFFORT, "0.166.0")
            adapter = live_s1.Dev01Runtime(request, handle, directory, runtime_config, launch_initial,
                                          _event_summary, EVENT_TYPES, unchanged)
            workflow = run_prepared_a(
                request, runtime=adapter, config=runtime_config, manager=manager, handle=handle, store=s1_store,
                limits=Limits((TURN_SECONDS + CLEANUP_SECONDS) * 1000, 1, 0, 100, CLEANUP_SECONDS * 1000),
                check=partial(run_approved, dev01_candidate=True),
            )
            report["workflow"] = {"status": workflow.stop_reason.value, "public_check_status": workflow.public_check_status.value,
                                  "report_ref": "report.json", "artifact_status": workflow.artifact_status}
            patch = workflow.frozen_patch
            if (report["launch_attempts"] != 1 or not workflow.evaluable or patch is None
                    or workflow.stop_reason != "COMPLETED" or workflow.public_check_status not in {"PASS", "FAIL"}
                    or not unchanged()):
                report["status"] = "S1_LIVE_INCOMPLETE"
                return 1
            verdict = _independent_verdict(directory, artifact_root, request, patch)
            report["evaluation"] = {"status": verdict.status.value, "error_code": verdict.error_code}
            report["status"] = "S1_LIVE_COMPLETE" if verdict.status.value in {"PASS", "FAIL"} else "S1_LIVE_INCOMPLETE"
            return 0 if report["status"] == "S1_LIVE_COMPLETE" else 1
        if verify_runtime or verify_rt04:
            report.update(model_requested=MODEL, reasoning_requested=EFFORT, turns={})

            def launch(name, message, *, seconds=TURN_SECONDS, persist=False, session_id=None, cancel=None, on_poll=None):
                args = _turn_command(config, persist=persist, session_id=session_id)
                report["launch_attempts"] += 1
                receipt = {"prompt_sha256": sha256_bytes(message), "requested_session_id": session_id,
                           "estimated_prompt_tokens": runtime_usage.estimate_prompt_tokens(message),
                           "persist_session": persist or session_id is not None, "timeout_seconds": seconds}
                report["turns"][name] = receipt
                (directory / f"{name}-dispatch.json").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
                result = job(args, seconds=seconds, data=message, cancel=cancel, on_poll=on_poll)
                summary = _event_summary(result.stdout)
                receipt.update(summary, status=result.status, exit_code=result.exit_code,
                               total_processes=result.total_processes,
                               remaining_process_count=result.remaining_process_count)
                return result.stdout, receipt

            def sandbox_launch(name, command, *, cancel, on_poll):
                outcome = job((*sandbox, *command), seconds=runtime_checks.INTERRUPT_SECONDS,
                              cancel=cancel, on_poll=on_poll)
                summary = {"status": outcome.status, "exit_code": outcome.exit_code,
                           "total_processes": outcome.total_processes,
                           "remaining_process_count": outcome.remaining_process_count}
                report.setdefault("sandbox_turns", {})[name] = summary
                return summary

            if verify_rt04:
                def unchanged():
                    return (baseline.read_bytes() == frozen_base
                            and all(path.read_bytes() == canary for path in protected))

                passed = runtime_checks.verify_interruption(
                    handle.root, python, report, launch, sandbox_launch, unchanged,
                )
                report["status"] = "RT04_PASS" if passed else "RT04_INCOMPLETE"
            else:
                protected_roles = {
                    "original": baseline, "controller": preflight_canary, "outside": outside_canary,
                    "evaluation": protected[-2], "ledger": protected[-1],
                }
                passed = runtime_checks.verify(handle.root, python, protected_roles, report, launch, sandbox_launch)
                report["status"] = "RUNTIME_CHECKS_PASS" if passed else "RUNTIME_CHECKS_INCOMPLETE"
            return 0 if passed else 1
        if not execute:
            report["status"] = "PREFLIGHT_PASS"
            return 0

        prompt = (request.fixed.task + "\n\n" + request.fixed.mandatory_text).encode("utf-8")
        report.update(initial_message_sha256=sha256_bytes(prompt), launch_attempts=1,
                      estimated_prompt_tokens=runtime_usage.estimate_prompt_tokens(prompt),
                      model_requested=MODEL, reasoning_requested=EFFORT)
        # Save the dispatch intent before entering the CLI. The script has no retry path.
        (directory / "dispatch.json").write_bytes(json.dumps(report, indent=2).encode("utf-8"))
        turn = job(_turn_command(config), seconds=TURN_SECONDS, data=prompt)
        summary = _event_summary(turn.stdout)
        report["runtime"] = {**summary, "status": turn.status, "exit_code": turn.exit_code,
                             "remaining_process_count": turn.remaining_process_count}
        if turn.status != "EXITED" or turn.exit_code != 0 or not summary["thread_id"] or (
                summary["event_counts"].get("turn.completed") != 1 or summary["event_counts"].get("turn.failed")):
            failures = ", ".join(summary["failure_codes"]) or f"{turn.status}, exit={turn.exit_code}"
            raise SmokeBlocked(f"Codex turn did not complete ({failures})")
        if baseline.read_bytes() != frozen_base or any(path.read_bytes() != canary for path in protected):
            raise SmokeBlocked("Protected files changed during the turn")
        snapshot = manager.snapshot(handle)
        patch = manager.freeze_patch(handle, snapshot)
        if not patch.applicable:
            raise SmokeBlocked("The final patch could not be frozen")
        report["patch_hash"] = patch.patch_hash
        report["public_checks"] = {}
        for check_id in request.fixed.approved_check_ids:
            check = run_approved(snapshot, check_id, artifact_root, dev01_candidate=True)
            (directory / f"{check_id}.json").write_bytes(dump_record(check))
            report["public_checks"][check_id] = check.status.value
        verdict = _independent_verdict(directory, artifact_root, request, patch)
        report["evaluation"] = {"status": verdict.status.value, "error_code": verdict.error_code}
        report["status"] = verdict.status.value
        if verdict.status.value == "PASS" and set(report["public_checks"].values()) != {"PASS"}:
            report["status"] = "INCOMPLETE"
        return 0 if report["status"] == "PASS" else 1
    finally:
        if quiet:
            manager.cleanup(handle)
            (work_root / "outside-workspace-canary.txt").unlink(missing_ok=True)
        else:
            report["workspace_preserved"] = str(handle.root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="authorize one live ChatGPT-authenticated turn")
    mode.add_argument("--execute-s1", action="store_true", help="use the one-shot approval for S1 condition A")
    mode.add_argument("--execute-s4", action="store_true", help="run the approved DEV-01 A–E schedule")
    mode.add_argument("--diagnose-mxc", action="store_true", help="compare MXC startup paths without a model call")
    mode.add_argument("--verify-runtime", action="store_true",
                      help="authorize up to four live turns for interruption and session checks")
    mode.add_argument("--verify-rt04", action="store_true",
                      help="verify interruption only, with up to two live turns")
    parser.add_argument("--inherit-console", action="store_true", help="keep the existing console inside the job")
    args = parser.parse_args()
    if args.execute_s4:
        from .s4 import live
        return live(inherit_console=args.inherit_console)
    if args.diagnose_mxc and args.inherit_console:
        parser.error("--diagnose-mxc already tests both console modes")
    request = fixture_request("DEV-01")
    s1_store = None
    if args.execute_s1:
        directory = CHECKOUT / "results" / "s1-live" / request.run_id
        s1_store = RunStore(directory, request.run_id)
    else:
        directory = CHECKOUT / "results" / "s0-verification" / f"live-dev01-{request.run_id}"
        directory.mkdir(parents=True, exist_ok=False)
    work_root = Path(tempfile.mkdtemp(prefix="aah-dev01-"))
    report = {"schema_version": "1.0", "run_id": request.run_id, "task_id": request.task_id,
              "profile": PROFILE, "started_at": datetime.now(timezone.utc).isoformat(), "launch_attempts": 0}
    if args.verify_runtime:
        report["mode"] = "runtime-verification"
    if args.verify_rt04:
        report["mode"] = "rt04-verification"
    if args.execute_s1:
        report["mode"] = "s1-live-condition-a"
    try:
        if args.execute_s1:
            live_s1.validate_approval(datetime.now(timezone.utc))
            if (directory.parent / live_s1.RESERVATION_NAME).exists():
                raise SmokeBlocked("The one-shot DEV-01 approval was already reserved")
        exit_code = _run(directory, work_root, request, report, execute=args.execute, verify_runtime=args.verify_runtime,
                         diagnose_mxc=args.diagnose_mxc, inherit_console=args.inherit_console, verify_rt04=args.verify_rt04,
                         s1_store=s1_store)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        report["status"] = "BLOCKED"
        report["reason"] = str(exc) if isinstance(exc, (SmokeBlocked, ContractError)) else type(exc).__name__
        exit_code = 2
    finally:
        if not any(work_root.iterdir()):
            work_root.rmdir()
        path = directory / ("operator.json" if args.execute_s1 else "report.json")
        path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        for name, control in report.get("mxc_controls", {}).items():
            status = "PASS" if control["passed"] else "FAIL"
            details = ", ".join(control["signals"])
            print(f"{name} {status} (exit={control['exit_code']})" + (f" {details}" if details else ""))
        for check_id, result in report.get("runtime_checks", {}).items():
            print(f"{check_id} {'PASS' if result['passed'] else 'INCOMPLETE'}")
        print(f"{report.get('status', 'INTERRUPTED')}  {path}")
        if "reason" in report:
            print(report["reason"], file=sys.stderr)
            for heading in report.get("mxc_preflight", {}).get("native_errors", ()):
                print(heading, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
