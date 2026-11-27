"""Run a trusted native executable inside a private Windows Job Object.

The caller supplies its authorized argv, working directory, environment and
absolute monotonic deadlines. The job controls lifetime; it grants no file,
network or identity isolation. Windows 10+ is required for the job-list attribute.
"""

from __future__ import annotations

import ctypes as ct
import math
import msvcrt
import os
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from ctypes import wintypes as wt
from dataclasses import dataclass
from pathlib import Path
from threading import Event


class _BasicLimits(ct.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ct.c_int64), ("PerJobUserTimeLimit", ct.c_int64),
        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ct.c_size_t),
        ("MaximumWorkingSetSize", ct.c_size_t), ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ct.c_size_t), ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _ExtendedLimits(ct.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits), ("IoInfo", ct.c_uint64 * 6),
        ("ProcessMemoryLimit", ct.c_size_t), ("JobMemoryLimit", ct.c_size_t),
        ("PeakProcessMemoryUsed", ct.c_size_t), ("PeakJobMemoryUsed", ct.c_size_t),
    ]


class _Accounting(ct.Structure):
    _fields_ = [
        ("CpuTimes", ct.c_int64 * 4), ("TotalPageFaultCount", wt.DWORD),
        ("TotalProcesses", wt.DWORD), ("ActiveProcesses", wt.DWORD),
        ("TotalTerminatedProcesses", wt.DWORD),
    ]


class _Startup(ct.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
        ("lpReserved2", wt.LPVOID), ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
        ("lpAttributeList", wt.LPVOID),
    ]


class _Process(ct.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
    ]


_kernel = ct.WinDLL("kernel32", use_last_error=True)


def _api(name, result_type, *argument_types):
    function = getattr(_kernel, name)
    function.argtypes = argument_types
    function.restype = result_type
    return function


_create_job = _api("CreateJobObjectW", wt.HANDLE, wt.LPVOID, wt.LPCWSTR)
_set_limits = _api("SetInformationJobObject", wt.BOOL, wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD)
_query_job = _api("QueryInformationJobObject", wt.BOOL, wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, wt.LPDWORD)
_terminate = _api("TerminateJobObject", wt.BOOL, wt.HANDLE, wt.DWORD)
_wait = _api("WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
_close = _api("CloseHandle", wt.BOOL, wt.HANDLE)
_init_attrs = _api("InitializeProcThreadAttributeList", wt.BOOL, wt.LPVOID, wt.DWORD, wt.DWORD, ct.POINTER(ct.c_size_t))
_update_attr = _api("UpdateProcThreadAttribute", wt.BOOL, wt.LPVOID, wt.DWORD, ct.c_size_t, wt.LPVOID, ct.c_size_t, wt.LPVOID, wt.LPVOID)
_delete_attrs = _api("DeleteProcThreadAttributeList", None, wt.LPVOID)
_create_process = _api(
    "CreateProcessW", wt.BOOL, wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID,
    wt.BOOL, wt.DWORD, wt.LPVOID, wt.LPCWSTR, ct.POINTER(_Startup), ct.POINTER(_Process),
)
_get_exit_code = _api("GetExitCodeProcess", wt.BOOL, wt.HANDLE, wt.LPDWORD)
_open_process = _api("OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
_in_job = _api("IsProcessInJob", wt.BOOL, wt.HANDLE, wt.HANDLE, ct.POINTER(wt.BOOL))
_peek = _api("PeekNamedPipe", wt.BOOL, wt.HANDLE, wt.LPVOID, wt.DWORD, wt.LPDWORD, wt.LPDWORD, wt.LPDWORD)

_JOB_BASIC_ACCOUNTING = 1
_JOB_EXTENDED_LIMITS = 9
_KILL_ON_JOB_CLOSE = 0x2000
_ATTRIBUTE_JOB_LIST = 0x2000D
_ATTRIBUTE_HANDLE_LIST = 0x20002
_EXTENDED_STARTUPINFO_PRESENT = 0x80000
_CREATE_UNICODE_ENVIRONMENT = 0x400
_STARTF_USESTDHANDLES = 0x100


def _check(result):
    if not result:
        raise ct.WinError(ct.get_last_error())
    return result


def _accounting(job: int) -> _Accounting:
    counts = _Accounting()
    _check(_query_job(job, _JOB_BASIC_ACCOUNTING, ct.byref(counts), ct.sizeof(counts), None))
    return counts


class ProcessNotQuiescent(OSError):
    """Windows could not confirm an empty job before the cleanup deadline."""


@contextmanager
def watch_job_process(pid: int, job: int):
    """Hold a wait-only identity for a live process in this job."""
    process = _check(_open_process(0x00101000, False, pid))  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    try:
        member = wt.BOOL()
        _check(_in_job(process, job, ct.byref(member)))
        if not member.value or _wait(process, 0) != 258:  # WAIT_TIMEOUT
            raise ValueError("Process is not a live member of the job")
        yield lambda: _wait(process, 0) == 0
    finally:
        _close(process)


def _reap(job: int, deadline: float) -> _Accounting:
    try:
        # Termination closes the job to further launches and signals its handle
        # at completion. ActiveProcesses alone can reach zero during teardown.
        _check(_terminate(job, 1))
        remaining = min(max(0, deadline - time.monotonic()), 4294967)
        state = _wait(job, math.floor(remaining * 1000))
        if state == 0xFFFFFFFF:  # WAIT_FAILED
            raise ct.WinError(ct.get_last_error())
        counts = _accounting(job)
        if state != 0 or counts.ActiveProcesses:
            raise ProcessNotQuiescent(
                f"Job teardown unconfirmed (wait={state}, active={counts.ActiveProcesses})"
            )
        return counts
    except ProcessNotQuiescent:
        raise
    except OSError as error:
        raise ProcessNotQuiescent("Unable to confirm job cleanup") from error


@contextmanager
def _attributes(job, std_handles):
    size = ct.c_size_t()
    # The first call reports the required allocation with ERROR_INSUFFICIENT_BUFFER.
    if _init_attrs(None, 2, 0, ct.byref(size)) or ct.get_last_error() != 122:
        raise ct.WinError(ct.get_last_error())
    attrs = ct.create_string_buffer(size.value)
    _check(_init_attrs(attrs, 2, 0, ct.byref(size)))
    try:
        jobs = (wt.HANDLE * 1)(job)
        handles = (wt.HANDLE * 3)(*std_handles)
        _check(_update_attr(attrs, 0, _ATTRIBUTE_JOB_LIST, jobs, ct.sizeof(jobs), None, None))
        _check(_update_attr(attrs, 0, _ATTRIBUTE_HANDLE_LIST, handles, ct.sizeof(handles), None, None))
        yield ct.cast(attrs, wt.LPVOID)
    finally:
        _delete_attrs(attrs)


def _start(argv, cwd, env_block, job, resources, stdin_data, inherit_console):
    process = _Process()
    with ExitStack() as inherited:
        if stdin_data is None:
            stdin = inherited.enter_context(open(os.devnull, "rb"))
        else:
            stdin = inherited.enter_context(tempfile.TemporaryFile())
            stdin.write(stdin_data)
            stdin.seek(0)
        handles = [msvcrt.get_osfhandle(stdin.fileno())]
        readers = []
        for _ in range(2):
            read_fd, write_fd = os.pipe()
            resources.callback(os.close, read_fd)
            inherited.callback(os.close, write_fd)
            handles.append(msvcrt.get_osfhandle(write_fd))
            readers.append(read_fd)
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        with _attributes(job, handles) as attributes:
            startup = _Startup()
            startup.cb = ct.sizeof(startup)
            startup.lpAttributeList = attributes
            startup.dwFlags = _STARTF_USESTDHANDLES
            startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
            flags = _CREATE_UNICODE_ENVIRONMENT | _EXTENDED_STARTUPINFO_PRESENT
            if not inherit_console:
                flags |= subprocess.CREATE_NO_WINDOW
            command = ct.create_unicode_buffer(subprocess.list2cmdline(argv))
            _check(_create_process(argv[0], command, None, None, True, flags, env_block,
                                   str(cwd), ct.byref(startup), ct.byref(process)))
            resources.callback(_close, process.hProcess)
            _check(_close(process.hThread))
    return process.hProcess, process.dwProcessId, readers


def _drain(fd: int, buffer: bytearray, remaining: int) -> tuple[int, bool]:
    available = wt.DWORD()
    if not _peek(msvcrt.get_osfhandle(fd), None, 0, None, ct.byref(available), None):
        if ct.get_last_error() == 109:  # ERROR_BROKEN_PIPE, all writers closed.
            return 0, False
        raise ct.WinError(ct.get_last_error())
    if not available.value:
        return 0, False
    data = os.read(fd, min(available.value, remaining + 1, 65536))
    buffer.extend(data[:remaining])
    return len(data), len(data) > remaining


@dataclass(frozen=True)
class JobResult:
    status: str
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    total_processes: int
    remaining_process_count: int


def _environment_block(env: Mapping[str, str]):
    names = set()
    entries = []
    for key, value in sorted(env.items(), key=lambda item: item[0].casefold()):
        if not key or "=" in key or "\0" in key or "\0" in value or key.casefold() in names:
            raise ValueError("Invalid or duplicate environment name or value")
        names.add(key.casefold())
        entries.append(f"{key}={value}")
    return ct.create_unicode_buffer("\0".join(entries) + "\0")


def run_windows_job(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], deadline: float,
    cleanup_deadline: float, cancel: Event | None = None, output_limit: int = 65536,
    stdin_data: bytes | None = None, inherit_console: bool = False,
    on_poll: Callable[[int, int, bytes], float | None] | None = None,
) -> JobResult:
    """Capture bounded stdout/stderr and verify an empty job before returning."""
    if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
        raise ValueError("Expected an argv sequence with NUL-free strings")
    executable = Path(argv[0])
    if not executable.is_absolute() or executable.suffix.lower() != ".exe" or not cwd.is_absolute():
        raise ValueError("Expected an absolute native executable path and working directory")
    if not math.isfinite(deadline) or not math.isfinite(cleanup_deadline) or cleanup_deadline <= deadline:
        raise ValueError("Expected finite deadlines with a positive cleanup reserve")
    cleanup_reserve = cleanup_deadline - deadline
    if not isinstance(output_limit, int) or output_limit < 0:
        raise ValueError("Expected a nonnegative output byte limit")
    env_block = _environment_block(env)
    if cancel is not None and cancel.is_set():
        return JobResult("CANCELLED", None, b"", b"", 0, 0)
    if time.monotonic() >= deadline:
        return JobResult("TIMED_OUT", None, b"", b"", 0, 0)

    with ExitStack() as resources:
        job = _check(_create_job(None, None))
        resources.callback(_close, job)
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        _check(_set_limits(job, _JOB_EXTENDED_LIMITS, ct.byref(limits), ct.sizeof(limits)))
        buffers = [bytearray(), bytearray()]
        observed_stdout = b""
        try:
            process, pid, readers = _start(argv, cwd, env_block, job, resources, stdin_data, inherit_console)
            while True:
                if cancel is not None and cancel.is_set():
                    status = "CANCELLED"
                    break
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    status = "TIMED_OUT"
                    break
                if on_poll is not None:
                    if len(observed_stdout) != len(buffers[0]):
                        observed_stdout = bytes(buffers[0])
                    earlier = on_poll(job, pid, observed_stdout)
                    if earlier is not None:
                        if not math.isfinite(earlier):
                            raise ValueError("Expected a finite observer deadline")
                        deadline = min(deadline, earlier)
                        cleanup_deadline = min(cleanup_deadline, deadline + cleanup_reserve)
                    remaining_time = deadline - time.monotonic()
                    if (cancel is not None and cancel.is_set()) or remaining_time <= 0:
                        continue
                # Count before draining so zero proves no future child can write.
                active = _accounting(job).ActiveProcesses
                received = 0
                overflow = False
                for fd, buffer in zip(readers, buffers):
                    count, excess = _drain(fd, buffer, output_limit - sum(map(len, buffers)))
                    received += count
                    overflow |= excess
                if overflow:
                    status = "OUTPUT_LIMIT"
                    break
                if not received:
                    if not active:
                        status = "EXITED"
                        break
                    time.sleep(min(0.01, remaining_time))
        finally:
            # Also runs after setup errors, interrupts, or pipe failures.
            counts = _reap(job, cleanup_deadline)
        if on_poll is not None and status in {"CANCELLED", "TIMED_OUT"}:
            # Include terminal events buffered just before termination.
            for fd, buffer in zip(readers, buffers):
                while True:
                    received, overflow = _drain(fd, buffer, output_limit - sum(map(len, buffers)))
                    if overflow:
                        status = "OUTPUT_LIMIT"
                    if overflow or not received:
                        break
        code = wt.DWORD()
        _check(_get_exit_code(process, ct.byref(code)))
        return JobResult(status, code.value, bytes(buffers[0]), bytes(buffers[1]),
                         counts.TotalProcesses, counts.ActiveProcesses)
