import json
import subprocess
import sys
import tomllib
from datetime import timedelta
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from aah.domain import ContractError, sha256_bytes
from aah.reporting.store import RunStore, read_report
from benchmarks.smoke import docker_worker, driver, live_dev01, live_s1, runtime_checks, runtime_usage, worker
from benchmarks.smoke.public_checks import run_approved
from evaluation.smoke import fixture_request


def test_profile_has_exact_absolute_roots(tmp_path):
    work = tmp_path / "run"
    auth = tmp_path / ".auth"
    python = tmp_path / "Python" / "python.exe"
    args = live_dev01._configuration(work, auth, python)
    permission = next(arg for arg in args if arg.startswith(f"permissions.{live_dev01.PROFILE}="))
    profile = tomllib.loads(permission)["permissions"][live_dev01.PROFILE]
    assert profile["network"] == {"enabled": False}
    assert profile["filesystem"] == {
        ":root": "deny", ":minimal": "read", python.parent.as_posix(): "read", work.as_posix(): "write",
        live_dev01.CHECKOUT.as_posix(): "deny", auth.as_posix(): "deny",
    }
    sandbox = live_dev01._sandbox_command(work, auth, python)
    policy = tuple(token for setting in live_dev01.SHELL_ENV_POLICY for token in ("-c", setting))
    assert sandbox[:9] == ("-c", "windows.sandbox=mxc", *policy, "-c", permission, "sandbox")


def test_agent_shell_and_access_probe_share_a_launchable_environment(tmp_path):
    """EL-20261127-034: MXC needs SYSTEMROOT; the probe path must use the agent's shell policy."""
    config = live_dev01._configuration(tmp_path / "run", tmp_path / ".auth", tmp_path / "python.exe")
    sandbox = live_dev01._sandbox_command(tmp_path / "run", tmp_path / ".auth", tmp_path / "python.exe")
    policy = [arg for arg in config if arg.startswith("shell_environment_policy.")]
    assert policy == list(live_dev01.SHELL_ENV_POLICY)
    assert [arg for arg in sandbox if arg.startswith("shell_environment_policy.")] == policy
    assert 'shell_environment_policy.inherit="none"' not in config
    assert "SYSTEMROOT" in live_dev01.SHELL_ENV_NAMES
    assert not {"USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME"} & set(live_dev01.SHELL_ENV_NAMES)
    assert "--disable" not in sandbox and "--include-managed-config" in sandbox


def test_preflight_marker_and_error_reporting():
    complete = "\n".join(live_dev01.PROBE_STEPS).encode("ascii")
    probe = SimpleNamespace(status="EXITED", exit_code=0, stdout=complete, stderr=b"")
    assert live_dev01._preflight_result(probe)["passed"]
    probe.stdout = b"PROBE_STARTED\nPROTECTED_FILES_DENIED\n"
    probe.stderr = b"WORKSPACE_WRITE_ERROR:5\npath=C:\\some-user\\private.txt"
    failed = live_dev01._preflight_result(probe)
    assert not failed["passed"]
    assert failed["steps"] == ["PROBE_STARTED", "PROTECTED_FILES_DENIED"]
    assert failed["signals"] == ["WORKSPACE_WRITE_ERROR:5"]
    probe.stderr = b"Error: cannot load \"profile value\" from C:\\user\\config.toml\nError: token sensitive\n"
    assert live_dev01._preflight_result(probe)["native_errors"] == ["Error: cannot load <value> from"]
    probe.stdout = b"Error: failed to prepare MXC sandbox: native MXC is unavailable on this executor"
    probe.stderr = b""
    assert live_dev01._preflight_result(probe)["signals"] == ["MXC_UNAVAILABLE", "MXC_SETUP_FAILED"]


def test_expired_s1_approval_is_refused():
    live_s1.validate_approval(live_s1.APPROVAL_EXPIRY - timedelta(microseconds=1))
    with pytest.raises(ContractError, match="expired"):
        live_s1.validate_approval(live_s1.APPROVAL_EXPIRY)


@pytest.mark.skipif(sys.platform != "win32", reason="native MXC entry point")
def test_live_s1_condition_a_writes_real_workflow_artifacts_and_reserves_once(tmp_path, monkeypatch):
    from aah.runtime import windows_job

    calls = []
    credential_root = tmp_path / "auth"
    credential_root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(credential_root))
    monkeypatch.setattr(live_dev01, "_find_codex", lambda: tmp_path / "codex.exe")
    monkeypatch.setattr(live_dev01.docker_worker, "identity", lambda: {"engine": "fixture"})
    monkeypatch.setattr(live_s1, "Dev01Runtime", partial(
        live_s1.Dev01Runtime, now=lambda: live_s1.APPROVAL_EXPIRY - timedelta(hours=1)))
    observed = []

    def job(argv, *, stdin_data, deadline, cleanup_deadline, **kwargs):
        command = tuple(argv[1:])
        assert "OPENAI_API_KEY" not in kwargs["env"]
        if command == ("--version",):
            stdout = b"codex-cli 0.166.0\n"
        elif command == ("login", "status"):
            stdout = b"Logged in using ChatGPT\n"
        elif "sandbox" in command:
            stdout = ("\n".join(live_dev01.PROBE_STEPS) + "\n").encode()
        else:
            assert command[0] == "exec" and "--ephemeral" in command and "resume" not in command
            assert sha256_bytes(stdin_data) == live_s1.APPROVED_INPUT
            assert cleanup_deadline - deadline == pytest.approx(15)
            calls.append(command)
            stdout = (
                b'{"type":"thread.started","thread_id":"123e4567-e89b-12d3-a456-426614174000"}\n'
                b'{"type":"turn.started"}\n{"type":"item.completed","item":{"type":"agent_message","text":"private"}}\n'
                b'{"type":"turn.completed","usage":{"input_tokens":12,"cached_input_tokens":8,"output_tokens":4}}\n'
            )
        return SimpleNamespace(status="EXITED", exit_code=0, stdout=stdout, stderr=b"",
                               remaining_process_count=0)

    def public(*args, **kwargs):
        assert kwargs.pop("dev01_candidate") is True
        return run_approved(*args, **kwargs)

    def verdict(directory, artifact_root, request, patch):
        public_report = json.loads(read_report(directory.parent, request.run_id))
        assert public_report["frozen_patch"]["patch_hash"] == patch.patch_hash
        assert not (tmp_path / "work" / request.run_id).exists()
        assert "evaluation" not in public_report
        observed.append(public_report)
        return SimpleNamespace(status=SimpleNamespace(value="FAIL"), error_code=None)

    monkeypatch.setattr(windows_job, "run_windows_job", job)
    monkeypatch.setattr(live_dev01, "run_approved", public)
    monkeypatch.setattr(live_dev01, "_independent_verdict", verdict)

    def invoke():
        request = fixture_request("DEV-01")
        directory = tmp_path / "results" / request.run_id
        store = RunStore(directory, request.run_id)
        operator = {"launch_attempts": 0}
        code = live_dev01._run(directory, tmp_path / "work", request, operator, execute=False, s1_store=store)
        return directory, operator, code

    directory, operator, code = invoke()
    assert code == 0 and operator["status"] == "S1_LIVE_COMPLETE" and operator["evaluation"]["status"] == "FAIL"
    assert observed[0]["turns_used"] == 1 and observed[0]["recoveries_reserved"] == 0
    assert observed[0]["completion_declared"] is None
    assert observed[0]["usage_summary"]["observed_model_tokens"] == 16
    assert len(list((directory / "restricted").glob("*.json"))) == 4
    assert "private" not in (directory / "evidence.jsonl").read_text(encoding="utf-8")
    assert not (directory / "interventions.jsonl").exists()
    _, second, code = invoke()
    assert code == 1 and second["launch_attempts"] == 0 and len(calls) == 1


def test_mxc_diagnostic_distinguishes_process_modes(tmp_path, monkeypatch):
    def direct(argv, **kwargs):
        assert "exec" not in argv and argv[-1] == "AAH_MXC_CONTROL_OK"
        return subprocess.CompletedProcess(argv, 0, b"AAH_MXC_CONTROL_OK\r\n", b"")

    calls = []

    def job(argv, *, seconds, inherit_console=False):
        calls.append((argv, inherit_console))
        passed = ":workspace" in argv or inherit_console
        return SimpleNamespace(status="EXITED", exit_code=0 if passed else 1,
                               stdout=b"AAH_MXC_CONTROL_OK\r\n" if passed else b"", stderr=b"")

    monkeypatch.setattr(live_dev01.subprocess, "run", direct)
    outcomes, console = live_dev01._mxc_controls(tmp_path / "codex.exe", tmp_path,
                                               {"SYSTEMROOT": str(tmp_path)}, (), job)
    assert [item["passed"] for item in outcomes.values()] == [True, True, True, False, True]
    assert console and [mode for _, mode in calls] == [False, False, True]


@pytest.mark.parametrize("source", [
    b"import os\ndef format_label(text):\n return text.strip()",
    b"def format_label(text=run()):\n return text.strip()",
    b"def format_label(text):\n return text.strip(run())",
    b"@run\ndef format_label(text):\n return text.strip()",
])
def test_dev01_rejects_code_outside_the_restricted_shape(source):
    assert not worker.is_dev01_candidate(source)


def test_new_candidate_needs_the_docker_profile(monkeypatch):
    source = b'def format_label(text):\n    """A different docstring."""\n    return text.strip()\n'
    assert worker.is_dev01_candidate(source) and not worker.is_reviewed("DEV-01", source)
    cases = [("whitespace", ["\tlabel\n"], "label")]
    with pytest.raises(driver.UnreviewedSource):
        driver.observe("DEV-01", source, cases, dev01_candidate=True)

    def fake_docker(script, flags, request, deadline, *, worker_args):
        assert worker_args == ("--dev01-candidate",)
        assert set(json.loads(request)) == {"source", "task_id", "inputs"}
        # Only this synthetic candidate is used by the process double.
        return subprocess.run([sys.executable, *flags, "-c", script.decode(), *worker_args],
                              input=request, capture_output=True, check=True, timeout=5)

    monkeypatch.setattr(docker_worker, "run", fake_docker)
    assert driver.observe("DEV-01", source, cases, in_docker=True, dev01_candidate=True) == ["label"]


def test_runtime_summary_excludes_provider_event_text():
    data = b'{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":true,"extra":"text"}}\n'
    data += b'{"type":"item.completed","item":{"text":"private output"}}\n'
    data += b'{"type":"unknown-sensitive-type"}\n'
    summary = live_dev01._event_summary(data)
    assert summary["usage"] == {"input_tokens": 12}
    assert summary["event_counts"] == {"turn.completed": 1, "item.completed": 1}
    assert "private output" not in json.dumps(summary)


def test_runtime_summary_distinguishes_retired_model_from_unknown_failure():
    data = b'{"type":"error","message":"The model is not supported when using a ChatGPT account."}\n'
    data += b'{"type":"turn.failed","error":{"message":"sensitive"}}\n'
    summary = live_dev01._event_summary(data)
    assert summary["failure_codes"] == ["MODEL_UNAVAILABLE"]
    assert summary["event_counts"] == {"error": 1, "turn.failed": 1}
    assert "sensitive" not in json.dumps(summary)
    unknown = live_dev01._event_summary(b'{"type":"turn.failed","error":{"message":"unknown"}}')
    assert unknown["failure_codes"] == ["UNCLASSIFIED_CODEX_ERROR"]


def test_runtime_summary_normalizes_synthetic_jsonl():
    fixture = Path(__file__).parents[1] / "fixtures" / "runtime" / "codex-exec-0.166.jsonl"
    summary = live_dev01._event_summary(fixture.read_bytes())
    assert summary["thread_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert summary["event_counts"] == {
        "thread.started": 1, "turn.started": 1, "item.started": 1, "item.updated": 1,
        "item.completed": 3, "turn.completed": 1,
    }
    assert summary["event_coverage"] == {
        "malformed_lines": 1, "unknown_events": 1, "unknown_item_events": 1,
        "item_event_counts": {"command_execution": 3, "agent_message": 1},
        "command_outcomes": {"completed": 1, "launch_failed": 0, "exit_zero": 1},
    }
    assert summary["usage_mode"] == "CUMULATIVE"
    assert summary["usage"] == {"input_tokens": 12, "cached_input_tokens": 8, "output_tokens": 4,
                                "reasoning_output_tokens": 2, "cache_write_input_tokens": 0}
    assert "synthetic" not in json.dumps(summary)


def test_runtime_summary_flags_agent_commands_the_sandbox_could_not_launch(tmp_path):
    """EL-20261127-034: MXC refused every agent command because SYSTEMROOT was missing."""
    failed = {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "exit_code": 1,
              "aggregated_output": "MXC launcher: The supplied `process.env` cannot launch a Windows process "
                                   "container because it is missing the required variable(s): SYSTEMROOT."}}
    test_failure = {"type": "item.completed", "item": {"id": "item_2", "type": "command_execution", "exit_code": 1,
                    "aggregated_output": "FAILED test_functional.py::test_total"}}
    data = b"\n".join(json.dumps(e).encode() for e in (failed, test_failure))
    summary = live_dev01._event_summary(data)
    assert summary["failure_codes"] == ["AGENT_SHELL_UNAVAILABLE"]
    assert summary["event_coverage"]["command_outcomes"] == {"completed": 2, "launch_failed": 1, "exit_zero": 0}
    assert "SYSTEMROOT" not in json.dumps(summary)
    restricted = tmp_path / "restricted"
    restricted.mkdir()
    for index, event in enumerate((failed, test_failure), 1):
        (restricted / f"turn-0001-raw-{index:05d}.json").write_text(json.dumps(event))
    assert live_dev01.agent_shell_launch_failures(tmp_path) == 1


@pytest.mark.parametrize("events", [
    b'{"type":"turn.started"}\n',
    b'{"type":"turn.completed","usage":{"input_tokens":0,"output_tokens":0}}\n',
    b'{"type":"turn.completed","usage":{"input_tokens":12}}\n' * 2,
    b'{"type":"turn.completed","usage":{"input_tokens":12}}\n{"type":"turn.failed"}\n',
])
def test_runtime_summary_preserves_unavailable_usage(events):
    assert live_dev01._event_summary(events)["usage"] is None


def test_resumed_usage_delta_requires_a_single_completed_turn_in_the_same_thread():
    thread = "123e4567-e89b-12d3-a456-426614174000"
    initial = {
        "status": "EXITED", "exit_code": 0, "remaining_process_count": 0,
        "event_counts": {"thread.started": 1, "turn.started": 1, "turn.completed": 1},
        "thread_id": thread, "requested_session_id": None, "persist_session": True,
        "usage": {"input_tokens": 12, "cached_input_tokens": 8, "output_tokens": 4},
    }
    followup = dict(initial, requested_session_id=thread,
                    usage={"input_tokens": 20, "cached_input_tokens": 12, "output_tokens": 6})
    assert runtime_usage.resumed_delta(initial, followup) == {
        "input_tokens": 8, "cached_input_tokens": 4, "output_tokens": 2,
    }
    assert runtime_usage.resumed_delta(initial, dict(followup, thread_id="other")) is None
    assert runtime_usage.resumed_delta(initial, dict(followup, status="CANCELLED")) is None
    assert runtime_usage.resumed_delta(initial, dict(followup, usage=None)) is None
    assert runtime_usage.resumed_delta(initial, dict(followup, usage=initial["usage"])) is None
    assert runtime_usage.resumed_delta(followup, dict(initial, requested_session_id=thread)) is None
    assert runtime_usage.estimate_prompt_tokens("한글a".encode("utf-8")) == 2


def test_runtime_resume_uses_exact_observed_session():
    thread = "123e4567-e89b-12d3-a456-426614174000"
    assert "--ephemeral" in live_dev01._turn_command(())
    assert "--ephemeral" not in live_dev01._turn_command((), persist=True)
    resumed = live_dev01._turn_command((), session_id=thread)
    assert resumed[:2] == ("exec", "resume") and resumed[-3:] == ("--", thread, "-")
    assert "--last" not in resumed
    with pytest.raises(ValueError):
        live_dev01._turn_command((), session_id="recent")


def test_runtime_checks_verify_resume_nonce_and_isolation(tmp_path, monkeypatch):
    thread = "123e4567-e89b-12d3-a456-426614174000"
    source = tmp_path / "source.txt"
    source.write_bytes(b"protected")
    report = {"mxc_preflight": {"passed": True}}
    monkeypatch.setattr(runtime_checks, "_docker_boundary", lambda _: {"passed": True})
    monkeypatch.setattr(runtime_checks, "verify_interruption",
                        lambda *args: args[2]["runtime_checks"].update(RT04={"passed": True}) or True)
    observed = []
    proof = None

    def launch(name, message, *, persist=False, session_id=None):
        nonlocal proof
        if persist:
            proof = message.decode().split()[5]
            response = "READY"
        else:
            assert proof.encode() not in message
            response = proof
        observed.append((name, persist, session_id))
        return (
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": response}}).encode(),
            {"status": "EXITED", "exit_code": 0, "thread_id": thread,
             "remaining_process_count": 0, "requested_session_id": session_id,
             "persist_session": persist or session_id is not None,
             "event_counts": {"thread.started": 1, "turn.started": 1, "turn.completed": 1},
             "usage": {"input_tokens": 12 if persist else 20, "cached_input_tokens": 3,
                       "output_tokens": 1 if persist else 2}},
        )

    assert runtime_checks.verify(tmp_path, tmp_path / "python.exe", {"original": source}, report, launch, None)
    assert observed == [("resume_initial", True, None), ("resume_followup", False, thread)]
    assert proof not in json.dumps(report)
    assert set(report["runtime_checks"]) == {"RT03", "RT04", "RT05", "RT06"}
    assert report["resume_usage_delta"] == {"input_tokens": 8, "cached_input_tokens": 0, "output_tokens": 1}


def test_rt04_keeps_native_and_live_evidence_separate(tmp_path, monkeypatch):
    report = {}
    calls = []
    active = b'{"type":"turn.started"}\n'

    def sandbox(name, argv, **kwargs):
        calls.append(name)
        Path(argv[-2]).write_text(f"{argv[-1]} 42", encoding="ascii")

    def launch(name, message, **kwargs):
        calls.append(name)
        return active, {"event_counts": {"turn.started": 1}}

    def interrupt(kind, run, members):
        run(None, None)
        observed = members(15, active if calls[-1].startswith("turn") else b"")
        assert observed == ({"codex": 15} if calls[-1].startswith("turn") else {"codex": 15, "foreground": 42})
        return {"passed": True, "observed_job_members": observed, "observed_members_exited": True}

    monkeypatch.setattr(runtime_checks, "_interrupt", interrupt)
    assert runtime_checks.verify_interruption(tmp_path, tmp_path / "python.exe", report, launch, sandbox, lambda: True)
    assert calls == ["sandbox_timeout", "sandbox_cancel", "turn_timeout", "turn_cancel"]
    assert not runtime_checks._turn_in_flight(active + b'{"type":"turn.completed"}\n')
