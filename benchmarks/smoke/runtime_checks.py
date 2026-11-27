"""Bounded G0 operator checks for Codex resume, interruption, and evaluator isolation."""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from stat import S_ISREG
from threading import Event
from time import monotonic
from uuid import uuid4

from aah.workspace.snapshot import checked_path, link

from . import docker_worker, runtime_usage


INTERRUPT_SECONDS = 45
HELD_SECONDS = 90
DOCKER_PROBE = b"""
import json, os, sys
from pathlib import Path

paths = json.load(sys.stdin)
denied = {}
for role, names in paths.items():
    denied[role] = []
    for mode in ('rb', 'r+b'):
        inaccessible = True
        for name in names:
            try:
                with Path(name).open(mode):
                    inaccessible = False
            except (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError):
                pass
        if inaccessible:
            denied[role].append(mode)
print(json.dumps({
    'denied': denied,
    'loopback_only': os.listdir('/sys/class/net') == ['lo'],
    'root_readonly': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
    'nonroot': os.getuid() == 65534,
    'credentials_absent': not any(name in os.environ for name in ('OPENAI_API_KEY', 'CODEX_HOME')),
}))
"""


def _docker_boundary(protected: dict[str, Path]) -> dict:
    # Use exactly the transport used by the candidate evaluator.
    paths = {}
    for role, path in protected.items():
        paths[role] = [f"/operator/{path.name}"]
        if path.drive:
            host_ref = f"{path.drive.rstrip(':').lower()}/{path.relative_to(path.anchor).as_posix()}"
            paths[role] += [f"/run/desktop/mnt/host/{host_ref}", f"/mnt/{host_ref}"]
    result = docker_worker.run(DOCKER_PROBE, ("-I", "-S", "-B"), json.dumps(paths).encode(), monotonic() + 12)
    if result.returncode != 0:
        return {"passed": False, "exit_code": result.returncode}
    observed = json.loads(result.stdout)
    passed = (observed["denied"] == {role: ["rb", "r+b"] for role in protected}
              and all(observed[key] is True for key in
                      ("loopback_only", "root_readonly", "nonroot", "credentials_absent")))
    return {"passed": passed, "exit_code": result.returncode, "observations": observed}


def _last_message(data: bytes) -> str | None:
    message = None
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            message = item["text"].strip()
    return message


def _completed(summary: dict) -> bool:
    counts = summary["event_counts"]
    return (summary["status"] == "EXITED" and summary["exit_code"] == 0
            and summary["remaining_process_count"] == 0 and bool(summary["thread_id"])
            and counts.get("turn.completed") == 1 and not counts.get("turn.failed"))


def _foreground_pid(path: Path, challenge: str) -> int | None:
    try:
        info = path.lstat()
        if link(info) or not S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 128:
            return None
        checked_path(path)
        with path.open("rb") as source:
            fields = source.read(129).decode("ascii").split()
        if len(fields) == 2 and fields[0] == challenge and fields[1].isdecimal() and len(fields[1]) < 11:
            pid = int(fields[1])
            if 0 < pid < 2**32:
                return pid
    except (OSError, ValueError):
        return None
    return None


def _interrupt(kind: str, launch, members) -> dict:
    from aah.runtime.windows_job import watch_job_process

    cancel = Event()
    observed = None
    terminated = []
    with ExitStack() as handles:
        def poll(job: int, root_pid: int, stdout: bytes) -> float | None:
            nonlocal observed
            if observed is not None:
                return None
            active = members(root_pid, stdout)
            if not active:
                return None
            try:
                with ExitStack() as pending:
                    terminated.extend(pending.enter_context(watch_job_process(pid, job)) for pid in active.values())
                    handles.enter_context(pending.pop_all())
            except ValueError:
                if set(active.values()) != {root_pid}:
                    raise
                return None  # The already-observed CLI exited between the event and the handle check.
            observed = active
            if kind == "cancel":
                cancel.set()
                return None
            return monotonic()

        summary = launch(cancel, poll)
        exited = bool(observed) and all(signaled() for signaled in terminated)
    expected = "CANCELLED" if kind == "cancel" else "TIMED_OUT"
    return {
        "passed": summary["status"] == expected and summary["remaining_process_count"] == 0 and exited,
        "expected_status": expected,
        "observed_job_members": observed,
        "observed_members_exited": exited,
    }


def _turn_in_flight(stdout: bytes) -> bool:
    started = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(event, dict):
            kind = event.get("type")
            if kind in ("turn.completed", "turn.failed"):
                return False
            if kind == "turn.started":
                started = True
    return started


def verify_interruption(workspace: Path, python: Path, report: dict, launch, sandbox_launch, unchanged) -> bool:
    lifetime = report.setdefault("runtime_checks", {})["RT04"] = {"passed": False}
    for kind in ("timeout", "cancel"):
        challenge = uuid4().hex
        ready = workspace / f"aah-rt04-{kind}-{challenge[:8]}.ready"
        code = (
            "import os, pathlib, sys, time\n"
            "marker = pathlib.Path(sys.argv[1])\n"
            "stage = marker.with_suffix('.tmp')\n"
            "stage.write_text(f'{sys.argv[2]} {os.getpid()}', encoding='ascii')\n"
            "os.replace(stage, marker)\n"
            f"time.sleep({HELD_SECONDS})\n"
        )
        script = ready.with_suffix(".py")
        script.write_text(code, encoding="ascii")
        command = (str(python), "-I", "-S", "-B", str(script), str(ready), challenge)

        def foreground(root: int, _: bytes) -> dict | None:
            child = _foreground_pid(ready, challenge)
            return {"codex": root, "foreground": child} if child and child != root else None

        lifetime[f"sandbox_{kind}"] = _interrupt(
            kind, lambda stop, poll: sandbox_launch(f"sandbox_{kind}", command, cancel=stop, on_poll=poll),
            foreground,
        )
        if not lifetime[f"sandbox_{kind}"]["passed"] or not unchanged():
            return False

    for kind in ("timeout", "cancel"):
        summary = None

        def live(stop, poll):
            nonlocal summary
            _, summary = launch(f"turn_{kind}", b"Reply with READY. Do not run any tools or change files.",
                                seconds=INTERRUPT_SECONDS, cancel=stop, on_poll=poll)
            return summary

        case = _interrupt(kind, live, lambda pid, stdout: {"codex": pid} if _turn_in_flight(stdout) else None)
        counts = summary["event_counts"]
        case["passed"] &= counts.get("turn.started") == 1 and not (
            counts.get("turn.completed") or counts.get("turn.failed")
        )
        lifetime[f"turn_{kind}"] = case
        if (case["observed_job_members"] and not case["observed_members_exited"]) or not unchanged():
            return False
    lifetime["passed"] = all(lifetime[f"{mode}_{kind}"]["passed"]
                             for mode in ("sandbox", "turn") for kind in ("timeout", "cancel"))
    return lifetime["passed"]


def verify(workspace: Path, python: Path, protected: dict[str, Path], report: dict, launch, sandbox_launch) -> bool:
    """Run safety checks before live turns and verify resume only by the observed session ID."""
    expected_files = {path: path.read_bytes() for path in protected.values()}
    checks = report["runtime_checks"] = {}

    def unchanged() -> bool:
        return all(path.read_bytes() == body for path, body in expected_files.items())

    checks["RT06"] = _docker_boundary(protected)
    checks["RT06"]["host_files_unchanged"] = unchanged()
    checks["RT06"]["passed"] &= checks["RT06"]["host_files_unchanged"]
    if not checks["RT06"]["passed"]:
        return False

    protection = checks["RT05"] = {"passed": False}
    if not verify_interruption(workspace, python, report, launch, sandbox_launch, unchanged):
        return False

    challenge = "AAH-" + uuid4().hex
    first = (f"Remember the synthetic proof code {challenge} for the next turn. "
             "Do not use tools or edit files. Reply with exactly READY.").encode()
    output, initial = launch("resume_initial", first, persist=True)
    resume = checks["RT03"] = {"passed": False, "initial_ack": _last_message(output) == "READY"}
    if not unchanged():
        return False
    if _completed(initial) and resume["initial_ack"]:
        second = b"Return exactly the synthetic proof code from the previous user message. Do not use tools or edit files."
        output, later = launch("resume_followup", second, session_id=initial["thread_id"])
        resume["same_session"] = later["thread_id"] == initial["thread_id"]
        resume["proof_code_matched"] = _last_message(output) == challenge
        resume["passed"] = _completed(later) and resume["same_session"] and resume["proof_code_matched"]
    protection.update({
        "passed": report["mxc_preflight"]["passed"] and unchanged(),
        "scope": "MXC preflight and protected host bytes after real turns",
    })
    if resume["passed"] and protection["passed"]:
        report["resume_usage_delta"] = runtime_usage.resumed_delta(initial, later)
    return all(check["passed"] for check in checks.values())
