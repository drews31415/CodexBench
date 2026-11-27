import json
from pathlib import Path

import pytest

from aah.runtime import RuntimeConfig, probe_codex
from aah.runtime import probe as metadata


def stub_metadata(monkeypatch, answers):
    calls = []
    monkeypatch.setattr(metadata, "_find_codex", lambda: Path("fixture-codex"))

    def run(_executable, args, cwd):
        assert cwd.is_dir() and not any(cwd.iterdir())
        calls.append(args)
        return answers[args]

    monkeypatch.setattr(metadata, "_metadata_command", run)
    return calls


def test_advertised_options_do_not_verify_any_live_contract(monkeypatch):
    calls = stub_metadata(monkeypatch, {
        ("--version",): ("EXITED", 0, b"codex-cli 1.2.3\n"),
        ("exec", "--help"): ("EXITED", 0, (
            b"Usage: codex exec [OPTIONS]\nCommands:\n  resume  Resume by ID\n"
            b"Options:\n  -m, --model <MODEL>\n      --json\n"
            b"Do not treat --sandbox or --cd in prose as a supported flag.\n"
        )),
        ("exec", "resume", "--help"): ("EXITED", 0, b"Usage: codex exec resume [ID]\n"),
    })
    report = probe_codex(RuntimeConfig(model_id="sample-model", reasoning_effort="high")).to_dict()
    assert calls == [("--version",), ("exec", "--help"), ("exec", "resume", "--help")]
    assert report["metadata_complete"] and not report["live_verified"]
    assert report["runtime_version"] == "1.2.3"
    assert report["requested"]["model_id"] == "sample-model"
    assert report["model_id"] is report["reasoning_effort"] is report["observed_model_tokens"] is None
    assert report["usage_mode"] == "UNKNOWN"
    assert report["advertised"] == {
        "json_events": True, "select_model": True, "set_working_directory": False,
        "sandbox_mode": False, "resume": True, "resume_json_events": False,
    }
    assert set(report["checks"]) == {f"RT{i:02d}" for i in range(1, 8)}
    assert all(check["status"] == "UNVERIFIED" for check in report["checks"].values())
    assert all(len(item["stdout_sha256"]) == 64 for item in report["commands"])


def test_missing_cli_leaves_unobserved_fields_null(monkeypatch):
    monkeypatch.setattr(metadata, "_find_codex", lambda: None)
    report = probe_codex().to_dict()
    assert not report["metadata_complete"]
    assert report["reason"] == "NATIVE_CLI_NOT_FOUND"
    assert report["runtime_version"] is None
    assert not report["commands"]
    assert set(report["advertised"].values()) == {None}


def test_discovery_error_retains_time_without_local_path(monkeypatch):
    def inaccessible():
        raise OSError("fixture-sensitive-installation-path")

    monkeypatch.setattr(metadata, "_find_codex", inaccessible)
    report = probe_codex().to_dict()
    assert report["reason"] == "CLI_DISCOVERY_ERROR"
    assert report["started_at"].endswith("Z") and report["elapsed_ms"] >= 0
    assert "fixture-sensitive-installation-path" not in json.dumps(report)


def test_mismatched_pinned_version_stops_before_help(monkeypatch):
    calls = stub_metadata(monkeypatch, {("--version",): ("EXITED", 0, b"codex-cli 1.2.3")})
    report = probe_codex(RuntimeConfig(runtime_version="1.2.9")).to_dict()
    assert calls == [("--version",)]
    assert report["runtime_version"] == "1.2.3"
    assert report["reason"] == "RUNTIME_VERSION_MISMATCH"


@pytest.mark.parametrize("status,code", [("EXITED", 1), ("TIMED_OUT", None), ("OUTPUT_LIMIT", 1)])
def test_failure_keeps_reason_without_raw_output(monkeypatch, status, code):
    stub_metadata(monkeypatch, {("--version",): (status, code, b"sensitive-fixture-output")})
    report = probe_codex().to_dict()
    assert "sensitive-fixture-output" not in json.dumps(report)
    assert not report["metadata_complete"] and report["reason"] == "VERSION_UNOBSERVED"
    assert report["commands"][0]["status"] == status
    assert report["commands"][0]["exit_code"] == code


def test_probe_child_environment_does_not_inherit_user_context(monkeypatch, tmp_path):
    for key in ("OPENAI_API_KEY", "HTTP_PROXY", "SSH_AUTH_SOCK", "HOME", "CODEX_HOME", "PATH"):
        monkeypatch.setenv(key, "fixture-host-value")
    env = metadata._metadata_env(tmp_path)
    assert env["USERPROFILE"] == env["CODEX_HOME"] == env["HOME"] == str(tmp_path)
    assert "fixture-host-value" not in env.values()
    assert {"OPENAI_API_KEY", "HTTP_PROXY", "SSH_AUTH_SOCK"}.isdisjoint(env)


def test_workspace_executable_is_never_used_for_metadata(monkeypatch, tmp_path):
    directory = tmp_path / "bin"
    directory.mkdir()
    for name in ("codex", "codex.exe"):
        executable = directory / name
        executable.write_bytes(b"MZfixture")
        executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(directory))
    assert metadata._find_codex() is None


def test_configuration_rejects_executable_and_unstructured_text():
    with pytest.raises(TypeError):
        RuntimeConfig(executable="arbitrary")
    with pytest.raises(ValueError):
        RuntimeConfig(model_id="version\narbitrary")
    with pytest.raises(ValueError):
        RuntimeConfig(adapter="unverified")
