"""Read local CLI metadata without starting a model session or loading user settings."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aah.domain import CapabilityStatus, SessionMode, UsageMode


@dataclass(frozen=True)
class RuntimeConfig:
    """Requested CLI identity. A requested value is never evidence of a model run."""

    model_id: str | None = None
    reasoning_effort: str | None = None
    runtime_version: str | None = None
    adapter: str = "codex_cli"
    session_mode: SessionMode | None = None

    def __post_init__(self) -> None:
        if self.adapter != "codex_cli":
            raise ValueError("only the codex_cli metadata probe is implemented")
        if self.session_mode is not None and not isinstance(self.session_mode, SessionMode):
            object.__setattr__(self, "session_mode", SessionMode(self.session_mode))
        for value in (self.model_id, self.reasoning_effort, self.runtime_version):
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+@-]{0,127}", value)
            ):
                raise ValueError("runtime configuration requires a short identifier or null")


_CHECKS = {
    "RT01": "Model identity and reasoning settings require a recorded Dev Smoke run.",
    "RT02": "Event fields and coverage require observed runtime events.",
    "RT03": "Session mode and intervention boundary require observed follow-up delivery.",
    "RT04": "Runtime cancellation and descendant cleanup require a supervised Dev Smoke run.",
    "RT05": "Instruction loading, source protection, cwd and permissions require isolation evidence.",
    "RT06": "Public checks and private evaluation require independent boundary evidence.",
    "RT07": "Token measurement, usage semantics and enforcement require observed runtime evidence.",
}
_VERSION = re.compile(r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.+-]+)?)")
_OUTPUT_LIMIT = 64 * 1024
_METADATA_FLAGS = {
    "json_events": "--json",
    "select_model": "--model",
    "set_working_directory": "--cd",
    "sandbox_mode": "--sandbox",
}


@dataclass(frozen=True)
class CommandObservation:
    argv: tuple[str, ...]
    status: str
    exit_code: int | None
    stdout_sha256: str | None
    elapsed_ms: int


@dataclass(frozen=True)
class ProbeReport:
    config: RuntimeConfig
    runtime_version: str | None
    commands: tuple[CommandObservation, ...]
    advertised: tuple[tuple[str, bool], ...]
    metadata_complete: bool
    reason: str | None
    started_at: str
    elapsed_ms: int

    def to_dict(self) -> dict:
        options = {name: None for name in (*_METADATA_FLAGS, "resume", "resume_json_events")}
        options.update(self.advertised)
        return {
            "schema_version": "1.0",
            "adapter": self.config.adapter,
            "requested": {
                "runtime_version": self.config.runtime_version,
                "model_id": self.config.model_id,
                "reasoning_effort": self.config.reasoning_effort,
                "session_mode": self.config.session_mode,
            },
            "runtime_version": self.runtime_version,
            "model_id": None,
            "reasoning_effort": None,
            "session_mode": None,
            "unit_boundary": None,
            "usage_mode": UsageMode.UNKNOWN.value,
            "observed_model_tokens": None,
            "metadata_complete": self.metadata_complete,
            "started_at": self.started_at,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
            "live_verified": False,
            "advertised": options,
            "commands": [{**asdict(item), "argv": list(item.argv)} for item in self.commands],
            "checks": {
                key: {"status": CapabilityStatus.UNVERIFIED.value, "reason": reason}
                for key, reason in _CHECKS.items()
            },
        }


def _native_binary(path: Path, excluded: Path) -> Path | None:
    """Only run native installations outside the working tree, never shell/npm shims."""
    try:
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(excluded) or not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None
        with resolved.open("rb") as stream:
            magic = stream.read(4)
    except (OSError, RuntimeError):
        return None
    if magic[:2] == b"MZ" or magic == b"\x7fELF" or magic in (
        b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"
    ):
        return resolved
    return None


def _find_codex() -> Path | None:
    excluded = Path.cwd().resolve()
    # A command can be run from a subdirectory of an evaluation repository.
    excluded = next((d for d in (excluded, *excluded.parents) if (d / ".git").exists()), excluded)
    name = "codex.exe" if os.name == "nt" else "codex"
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    system = {"win32": "win32", "linux": "linux", "darwin": "darwin"}.get(sys.platform)
    for entry in os.get_exec_path():
        directory = Path(entry)
        if not directory.is_absolute() or directory.resolve().is_relative_to(excluded):
            continue
        direct = directory / name
        native = _native_binary(direct, excluded)
        if native:
            return native
        # npm exposes a .cmd shim on Windows and a link to bin/codex.js on Unix.
        if os.name == "nt":
            if not (directory / "codex.cmd").is_file():
                continue
            package = directory / "node_modules" / "@openai" / "codex"
        elif direct.is_symlink() and direct.resolve().name == "codex.js":
            package = direct.resolve().parent.parent
        else:
            continue
        if arch and system:
            vendor = package / "node_modules" / "@openai" / f"codex-{system}-{arch}" / "vendor"
            for vendor_root in (vendor, package / "vendor"):
                for layout in ("bin", "codex"):
                    for candidate in sorted(vendor_root.glob(f"*/{layout}/{name}")):
                        native = _native_binary(candidate, excluded)
                        if native:
                            return native
    return None


def _metadata_env(root: Path) -> dict[str, str]:
    # No inherited tokens, proxies, agent sockets, user settings or session history.
    env = {
        key: str(root) for key in (
            "HOME", "USERPROFILE", "CODEX_HOME", "APPDATA", "LOCALAPPDATA",
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "TMPDIR", "TEMP", "TMP",
        )
    }
    if os.name == "nt" and "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    env["PATH"] = os.defpath
    env["LC_ALL"] = "C"
    return env


def _metadata_command(
    executable: Path, args: tuple[str, ...], root: Path
) -> tuple[str, int | None, bytes]:
    env = _metadata_env(root)
    argv = [str(executable), *args]
    deadline = time.monotonic() + 5
    if os.name == "nt":
        from .windows_job import ProcessNotQuiescent, run_windows_job

        try:
            result = run_windows_job(
                argv, cwd=root, env=env, deadline=deadline, cleanup_deadline=deadline + 2,
                output_limit=_OUTPUT_LIMIT,
            )
        except ProcessNotQuiescent:
            return "PROCESS_NOT_QUIESCENT", None, b""
        return result.status, result.exit_code, result.stdout

    # Use a private file so the parent never buffers arbitrary amounts of stdout.
    with tempfile.TemporaryFile(dir=root) as output, subprocess.Popen(
        argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output,
        stderr=subprocess.DEVNULL, shell=False, start_new_session=True,
    ) as child:
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            return "TIMED_OUT", child.returncode, b""
        output.seek(0)
        data = output.read(_OUTPUT_LIMIT + 1)
        if len(data) > _OUTPUT_LIMIT:
            return "OUTPUT_LIMIT", child.returncode, b""
        return "EXITED", child.returncode, data


def _has_option(help_text: str, option: str) -> bool:
    return bool(re.search(rf"^\s*(?:-\w,\s*)?{re.escape(option)}(?:\s|$)", help_text, re.MULTILINE))


def probe_codex(config: RuntimeConfig | None = None) -> ProbeReport:
    config = config if config is not None else RuntimeConfig()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started_ns = time.monotonic_ns()
    commands: list[CommandObservation] = []
    advertised: dict[str, bool] = {}
    runtime_version = None

    def report(reason: str | None = None) -> ProbeReport:
        return ProbeReport(
            config, runtime_version, tuple(commands), tuple(advertised.items()), reason is None, reason,
            started_at, (time.monotonic_ns() - started_ns) // 1_000_000,
        )

    try:
        executable = _find_codex()
    except (OSError, RuntimeError):
        return report("CLI_DISCOVERY_ERROR")
    if executable is None:
        return report("NATIVE_CLI_NOT_FOUND")
    with tempfile.TemporaryDirectory(prefix="aah-codex-probe-") as temporary:
        root = Path(temporary)

        def observe(args: tuple[str, ...]) -> str | None:
            start = time.monotonic_ns()
            try:
                status, code, output = _metadata_command(executable, args, root)
            except OSError:
                status, code, output = "OS_ERROR", None, b""
            elapsed = (time.monotonic_ns() - start) // 1_000_000
            digest = hashlib.sha256(output).hexdigest() if status == "EXITED" else None
            commands.append(CommandObservation(("codex", *args), status, code, digest, elapsed))
            if status == "EXITED" and code == 0:
                return output.decode("utf-8", errors="replace")
            return None

        version = observe(("--version",))
        match = _VERSION.fullmatch(version.strip()) if version else None
        if not match:
            return report("VERSION_UNOBSERVED")
        runtime_version = match.group(1)
        if config.runtime_version is not None and runtime_version != config.runtime_version:
            return report("RUNTIME_VERSION_MISMATCH")
        exec_help = observe(("exec", "--help"))
        if not exec_help or not re.search(r"^Usage: codex exec(?:\s|$)", exec_help, re.MULTILINE):
            return report("EXEC_HELP_UNOBSERVED")
        advertised.update({name: _has_option(exec_help, flag) for name, flag in _METADATA_FLAGS.items()})
        advertised["resume"] = bool(re.search(r"^\s+resume\s+\S", exec_help, re.MULTILINE))
        if advertised["resume"]:
            resume_help = observe(("exec", "resume", "--help"))
            if not resume_help or not re.search(r"^Usage: codex exec resume(?:\s|$)", resume_help, re.MULTILINE):
                return report("RESUME_HELP_UNOBSERVED")
            advertised["resume_json_events"] = _has_option(resume_help, "--json")
        return report()
