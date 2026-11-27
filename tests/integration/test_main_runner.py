import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aah.domain import Condition, RunRequest, resolve_condition, sha256_bytes
from benchmarks.main_public import access_check, main_runner
from benchmarks.smoke import live_dev01


# permission_settings_hash of every condition in main-s03-access-20261127-030416 (EL-20261127-020).
RECORD_HASH_EL020 = "de14ca4ee0a1d7a5e995808df347a370ccb1bc011a61745d506a5c258f49356d"
# Normalized settings after the agent shell environment fix (EL-20261127-034).
LOGGED_HASH = "74e24628b3c06fd3ddd975fe8e57c04b8ef3848eb9981074cfdbe1f2a214c44b"
# initial_snapshot_id of every condition in the same record.
LOGGED_SNAPSHOT = "cef3a2987cb5fb0f1de8d1bdc6f32ae3fc2b41b3732d36a180b1caae752c8210"


@pytest.fixture
def operator_path(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(access_check, "_find_codex", lambda: tmp_path / "codex.exe")
    launches = []

    def launch(argv, **kwargs):
        launches.append(list(argv[1:]))
        assert access_check.ENV_CANARY not in kwargs["env"]
        stdout = b"codex-cli 0.166.0\n" if list(argv[1:]) == ["--version"] else b'{"type":"thread.started"}\n'
        return SimpleNamespace(status="EXITED", exit_code=0, stdout=stdout, stderr=b"",
                               total_processes=1, remaining_process_count=0)

    monkeypatch.setattr(access_check, "_launch", launch)
    return SimpleNamespace(home=home.resolve(), launches=launches)


def write_record(path, hashes=None, snapshots=None, **changes):
    _, _, repository = main_runner.task_inputs()
    record = {"profile": access_check.PROFILE, "task_id": "AAH-S03", "mxc_profile": live_dev01.PROFILE,
              "status": "PASS", "bq09": {"status": "PASS"}, "bq10": {"status": "PASS"}, "agent_turns": 0,
              "base_revision": repository.initial_commit,
              "conditions": {c.value: {"permission_settings_hash": (hashes or {}).get(c.value, LOGGED_HASH),
                                       "initial_snapshot_id": (snapshots or {}).get(c.value, LOGGED_SNAPSHOT)}
                             for c in Condition}}
    record.update(changes)
    path.write_bytes(json.dumps(record).encode())
    return path


def test_live_settings_normalize_to_the_logged_hash_on_any_host_paths(tmp_path):
    for name in ("one", "two"):
        root = tmp_path / name
        *_, observed = access_check.permission_settings(root / "ws", root / "home", root / "py" / "python.exe")
        assert observed == LOGGED_HASH


def test_pinned_access_record_matches_when_present():
    if not main_runner.ACCESS_RECORD.exists():
        pytest.skip("results/ is local and access restricted")
    record = main_runner.load_access_record()
    assert {entry["permission_settings_hash"] for entry in record["conditions"].values()} == {RECORD_HASH_EL020}
    # The pre-fix record no longer matches the live settings, so the runner refuses it.
    assert RECORD_HASH_EL020 != LOGGED_HASH


def test_matching_record_prepares_a_to_e_without_agent_turns(tmp_path, operator_path):
    report = main_runner.verify(tmp_path / "out", access_record=write_record(tmp_path / "access.json"),
                                record_sha256=None)
    assert report["status"] == "PASS", report.get("reason")
    assert operator_path.launches == [["--version"]] * 5
    for operator in report["conditions"].values():
        assert operator["permission_gate"]["status"] == "MATCH"
        assert operator["permission_gate"]["observed"] == LOGGED_HASH
        assert operator["launch_attempts"] == 0 and operator["ready_for_turn"] and operator["workspace_cleaned"]
    assert json.loads((tmp_path / "out" / "gate.json").read_bytes())["status"] == "PASS"


def test_dispatch_launches_exactly_the_verified_configuration(tmp_path, operator_path):
    record = main_runner.load_access_record(write_record(tmp_path / "access.json"), sha256=None)
    task, fixed, repository = main_runner.task_inputs()
    request = RunRequest(str(uuid4()), task.task_id, "synthetic", repository, fixed, resolve_condition(Condition.E),
                         sha256_bytes(b"synthetic"), "research")
    operator = {}
    seen = {}

    def experiment(manager, handle, dispatch, unchanged):
        seen["root"] = handle.root
        dispatch(b"synthetic message", 5, persist=False, session_id=None)

    main_runner.run_slot(request, record=record, work_root=tmp_path / "work", artifact_root=tmp_path / "artifacts",
                         operator=operator, experiment=experiment)
    config = live_dev01._configuration(seen["root"], operator_path.home, Path(sys.executable).resolve())
    assert operator_path.launches[-1] == list(live_dev01._turn_command(config))
    assert operator["launch_attempts"] == 1 and operator["workspace_cleaned"]


def test_record_hash_mismatch_stops_before_any_codex_process_for_that_slot(tmp_path, operator_path):
    record = write_record(tmp_path / "access.json", {"C": "0" * 64})
    report = main_runner.verify(tmp_path / "out", access_record=record, record_sha256=None)
    assert (report["status"], report["code"]) == ("BLOCKED", "PERMISSION_SETTINGS_MISMATCH")
    assert operator_path.launches == [["--version"]] * 2  # A and B only
    blocked = report["conditions"]["C"]
    assert blocked["permission_gate"] == {"condition": "C", "expected": "0" * 64, "observed": LOGGED_HASH,
                                          "status": "MISMATCH"}
    assert "ready_for_turn" not in blocked and blocked["launch_attempts"] == 0 and blocked["workspace_cleaned"]
    assert set(report["conditions"]) == {"A", "B", "C"}


def test_changed_live_setting_stops_before_any_codex_process(tmp_path, operator_path, monkeypatch):
    original = live_dev01._configuration
    monkeypatch.setattr(live_dev01, "_configuration",
                        lambda *args: (*original(*args), "-c", "sandbox_workspace_write.network_access=true"))
    report = main_runner.verify(tmp_path / "out", access_record=write_record(tmp_path / "access.json"),
                                record_sha256=None)
    assert (report["status"], report["code"]) == ("BLOCKED", "PERMISSION_SETTINGS_MISMATCH")
    assert operator_path.launches == []
    assert report["conditions"]["A"]["permission_gate"]["observed"] != LOGGED_HASH


@pytest.mark.parametrize("case, code", [
    ("missing", "ACCESS_RECORD_MISSING"),
    ("changed", "ACCESS_RECORD_CHANGED"),
    ("bq09_fail", "ACCESS_RECORD_INVALID"),
    ("four_conditions", "ACCESS_RECORD_INVALID"),
    ("other_base", "ACCESS_RECORD_TASK_MISMATCH"),
])
def test_unusable_access_record_blocks_before_any_codex_process(tmp_path, operator_path, case, code):
    path = tmp_path / "access.json"
    pinned = None
    if case == "changed":
        write_record(path)
        pinned = "f" * 64
    elif case == "bq09_fail":
        write_record(path, bq09={"status": "FAIL"})
    elif case == "four_conditions":
        write_record(path)
        data = json.loads(path.read_bytes())
        del data["conditions"]["E"]
        path.write_bytes(json.dumps(data).encode())
    elif case == "other_base":
        write_record(path, base_revision="1" * 64)
    report = main_runner.verify(tmp_path / "out", access_record=path, record_sha256=pinned)
    assert (report["status"], report["code"]) == ("BLOCKED", code)
    assert operator_path.launches == []
    assert all(o["launch_attempts"] == 0 for o in report["conditions"].values())


@pytest.mark.parametrize("snapshot, code", [("0" * 64, "INITIAL_SNAPSHOT_MISMATCH"), (None, "INITIAL_SNAPSHOT_MISSING")])
def test_initial_snapshot_mismatch_stops_before_any_codex_process(tmp_path, operator_path, snapshot, code):
    record = write_record(tmp_path / "access.json", snapshots={"D": snapshot})
    report = main_runner.verify(tmp_path / "out", access_record=record, record_sha256=None)
    assert (report["status"], report["code"]) == ("BLOCKED", code)
    assert operator_path.launches == [["--version"]] * 3  # A, B and C only
    blocked = report["conditions"]["D"]
    assert blocked["snapshot_gate"]["observed"] == LOGGED_SNAPSHOT and "permission_gate" not in blocked
    assert blocked["launch_attempts"] == 0 and blocked["workspace_cleaned"]
