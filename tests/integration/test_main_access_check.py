import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.main_public import access_check
from benchmarks.smoke import live_dev01


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def _fake_mxc(tmp_path, *, hide_private):
    """Run the real probe without MXC; optionally emulate a sandbox that hides every outside path."""
    missing = tmp_path / "not-mounted"
    calls = []

    def launch(argv, *, cwd, env, deadline, cleanup_deadline, output_limit, inherit_console):
        args = list(argv[1:])
        calls.append(args)
        assert access_check.ENV_CANARY not in env
        if args == ["--version"]:
            return SimpleNamespace(status="EXITED", exit_code=0, stdout=b"codex-cli 0.166.0\n", stderr=b"",
                                   total_processes=1, remaining_process_count=0)
        assert "sandbox" in args and "exec" not in args
        command = args[args.index("--") + 1:]
        python, flags, (script, _port, canary, link), targets = command[0], command[1:4], command[4:8], command[8:]
        if hide_private:
            link = str(missing / "link-target")
            targets = [f"{t.split('=', 2)[0]}={t.split('=', 2)[1]}={missing / t.split('=', 2)[1]}" for t in targets]
        done = subprocess.run([python, *flags, script, _closed_port(), canary, link, *targets],
                              cwd=cwd, env=env, capture_output=True, timeout=30)
        return SimpleNamespace(status="EXITED", exit_code=done.returncode, stdout=done.stdout, stderr=done.stderr,
                               total_processes=1, remaining_process_count=0)

    return launch, calls


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(access_check, "_find_codex", lambda: tmp_path / "codex.exe")
    return home


def test_operator_environment_names_match_the_live_mxc_path():
    assert access_check.ENV_NAMES in live_dev01._run.__code__.co_consts


def test_hidden_private_files_pass_bq09_and_a_to_e_parity(tmp_path, monkeypatch, codex_home):
    launch, calls = _fake_mxc(tmp_path, hide_private=True)
    monkeypatch.setattr(access_check, "_launch", launch)
    report = access_check.execute(tmp_path / "out")

    assert report["status"] == "PASS", report.get("reason") or report.get("bq09")
    assert report["bq09"]["status"] == report["bq10"]["status"] == "PASS"
    assert report["agent_turns"] == report["model_calls"] == 0
    assert not any("exec" in call or "login" in call for call in calls)
    records = report["conditions"]
    assert list(records) == list("ABCDE")
    assert len({r["run_id"] for r in records.values()}) == 5
    for record in records.values():
        assert record["workspace_cleaned"] and record["fresh_work_root"]
        assert record["probe"]["result"]["public_files"] == record["public_file_hashes"]
        assert set(record["public_file_hashes"]) >= {"csv_rows.py", "public_checks/test_functional.py",
                                                     "public_checks/test_regression.py"}
        assert all(record["host_checks"].values())
        settings = json.dumps(record["permission_settings"])
        assert "<workspace>" in settings and str(tmp_path) not in settings and ":\\\\" not in settings
    assert json.loads((tmp_path / "out" / "access.json").read_bytes())["status"] == "PASS"


def test_readable_private_files_fail_closed(tmp_path, monkeypatch, codex_home):
    launch, _ = _fake_mxc(tmp_path, hide_private=False)
    monkeypatch.setattr(access_check, "_launch", launch)
    report = access_check.execute(tmp_path / "out")

    assert report["status"] == "FAIL" and report["bq09"]["status"] == "FAIL"
    failures = report["bq09"]["failures"]["A"]
    assert "PRIVATE_HIDDEN_TEST_READ_ALLOWED" in failures
    assert "PRIVATE_EVALUATION_RESULT_MODIFY_ALLOWED" in failures
    assert "OUTSIDE_WORKSPACE_PARENT_PATH_READ_ALLOWED" in failures
    # The canaries are opened but never rewritten.
    assert all(r["host_checks"]["canaries_unchanged"] for r in report["conditions"].values())


def test_blocked_probe_reports_stage(tmp_path, monkeypatch, codex_home):
    def launch(argv, **kwargs):
        if list(argv[1:]) == ["--version"]:
            return SimpleNamespace(status="EXITED", exit_code=0, stdout=b"codex-cli 0.166.0\n", stderr=b"",
                                   total_processes=1, remaining_process_count=0)
        return SimpleNamespace(status="EXITED", exit_code=1, stdout=b"",
                               stderr=b"Error: failed to prepare MXC sandbox: access is denied (os error 5)",
                               total_processes=1, remaining_process_count=0)

    monkeypatch.setattr(access_check, "_launch", launch)
    report = access_check.execute(tmp_path / "out")
    assert report["status"] == "BLOCKED"
    assert (report["blocked_condition"], report["blocked_stage"]) == ("A", "MXC_PROBE:NOT_STARTED")
    assert "WINDOWS_ACCESS_DENIED" in report["conditions"]["A"]["probe"]["signals"]
    assert report["conditions"]["A"]["workspace_cleaned"]
