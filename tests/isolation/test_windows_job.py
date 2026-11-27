"""Native Windows process-lifetime tests with trusted synthetic children."""

import ctypes as ct
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from ctypes import wintypes as wt
from threading import Event

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job Objects")


def run_python(tmp_path, script, deadline, **kwargs):
    from aah.runtime.windows_job import run_windows_job

    return run_windows_job(
        [sys.executable, "-I", "-S", "-c", script], cwd=tmp_path,
        env={"SystemRoot": os.environ["SystemRoot"]}, deadline=deadline,
        cleanup_deadline=deadline + 2, **kwargs,
    )


@pytest.mark.parametrize("interruption", ["timeout", "cancel", "pipe_error"])
def test_cleanup_stops_live_parent_and_child(tmp_path, monkeypatch, interruption):
    from aah.runtime import windows_job

    # Open independent handles while the two Python processes are alive. These
    # handles continue identifying the same process objects after PID reuse.
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.WaitForSingleObject.restype = wt.DWORD
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL

    child = """
import os, pathlib, time
pathlib.Path("starting").write_text(f"{os.getppid()} {os.getpid()}", encoding="ascii")
os.replace("starting", "ready")
time.sleep(20)
"""
    parent = f"""
import subprocess, sys, time
subprocess.Popen([sys.executable, "-I", "-S", "-c", {child!r}])
time.sleep(20)
"""
    deadline = time.monotonic() + (3 if interruption == "timeout" else 8)
    cancel = Event()
    with ThreadPoolExecutor(max_workers=1) as pool, ExitStack() as handles:
        result_future = pool.submit(run_python, tmp_path, parent, deadline, cancel=cancel)
        try:
            marker = tmp_path / "ready"
            while not marker.exists():
                if result_future.done():
                    pytest.fail(f"Process tree never became ready: {result_future.result()!r}")
                assert time.monotonic() < deadline, "synthetic child did not start in time"
                time.sleep(0.01)
            pids = list(map(int, marker.read_text(encoding="ascii").split()))
            assert len(set(pids)) == 2
            processes = []
            for pid in pids:
                handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                assert handle, ct.WinError(ct.get_last_error())
                handles.callback(kernel.CloseHandle, handle)
                assert kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
                processes.append(handle)
            if interruption == "pipe_error":
                def failed_read(*args):
                    raise OSError("synthetic pipe error")

                monkeypatch.setattr(windows_job, "_drain", failed_read)
                with pytest.raises(OSError, match="synthetic pipe error"):
                    result_future.result(timeout=deadline + 2 - time.monotonic())
            elif interruption == "cancel":
                cancel.set()
            if interruption != "pipe_error":
                result = result_future.result(timeout=deadline + 2 - time.monotonic())
                assert result.status == ("CANCELLED" if interruption == "cancel" else "TIMED_OUT")
                assert result.total_processes >= 2
                assert result.remaining_process_count == 0
        finally:
            cancel.set()
        assert all(kernel.WaitForSingleObject(handle, 0) == 0 for handle in processes)


def test_waits_for_descendant_output_after_root_exit(tmp_path):
    child = "import time; time.sleep(0.2); print('child finished')"
    parent = f"""
import subprocess, sys
subprocess.Popen([sys.executable, "-I", "-S", "-c", {child!r}])
print("parent exiting", file=sys.stderr)
raise SystemExit(7)
"""
    result = run_python(tmp_path, parent, time.monotonic() + 5)
    assert result.status == "EXITED"
    assert result.exit_code == 7
    assert result.stdout.splitlines() == [b"child finished"]
    assert result.stderr.splitlines() == [b"parent exiting"]
    assert result.remaining_process_count == 0


def test_job_delivers_exact_stdin_bytes(tmp_path):
    data = "과제\n".encode("utf-8") + b"\x00"
    result = run_python(tmp_path, "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
                        time.monotonic() + 5, stdin_data=data)
    assert result.status == "EXITED" and result.exit_code == 0
    assert result.stdout == data and result.remaining_process_count == 0


def test_job_observer_can_bind_and_interrupt_a_live_member(tmp_path):
    from aah.runtime.windows_job import watch_job_process

    script = "import os,time; open('pid', 'w').write(str(os.getpid())); time.sleep(10)"
    marker = tmp_path / "pid"
    with ExitStack() as handles:
        signaled = None

        def on_poll(job, root_pid, _):
            nonlocal signaled
            if signaled is None and marker.is_file():
                pid = marker.read_text(encoding="ascii")
                if pid:
                    assert int(pid) == root_pid
                    signaled = handles.enter_context(watch_job_process(int(pid), job))
                    return time.monotonic() + 0.025
            return None

        result = run_python(tmp_path, script, time.monotonic() + 5, on_poll=on_poll)
        assert result.status == "TIMED_OUT" and result.remaining_process_count == 0
        assert signaled is not None and signaled()


def test_output_limit_reaps_writer(tmp_path):
    script = "import os, time; os.write(1, b'a' * 100000); time.sleep(20)"
    result = run_python(tmp_path, script, time.monotonic() + 5, output_limit=128)
    assert result.status == "OUTPUT_LIMIT"
    assert result.stdout == b"a" * 128
    assert result.remaining_process_count == 0


def test_expired_deadline_starts_nothing(tmp_path):
    result = run_python(tmp_path, "raise RuntimeError()", time.monotonic() - 1)
    assert result.status == "TIMED_OUT"
    assert result.total_processes == result.remaining_process_count == 0
    assert result.exit_code is None
