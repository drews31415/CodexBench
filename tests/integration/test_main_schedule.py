import json
from types import SimpleNamespace

import pytest

from aah.domain import Condition
from benchmarks.main_public import access_check, main_runner, main_schedule
from benchmarks.smoke import live_dev01


# Values logged for every condition in main-s03-access-20261127-030416 (EL-20261127-020).
LOGGED_HASH = "de14ca4ee0a1d7a5e995808df347a370ccb1bc011a61745d506a5c258f49356d"
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
        assert list(argv[1:]) == ["--version"], "verification must not start a Codex turn"
        return SimpleNamespace(status="EXITED", exit_code=0, stdout=b"codex-cli 0.166.0\n", stderr=b"",
                               total_processes=1, remaining_process_count=0)

    monkeypatch.setattr(access_check, "_launch", launch)
    return launches


def write_record(path, **overrides):
    _, _, repository = main_runner.task_inputs()
    conditions = {c.value: {"permission_settings_hash": LOGGED_HASH, "initial_snapshot_id": LOGGED_SNAPSHOT}
                  for c in Condition}
    for condition, values in overrides.items():
        conditions[condition].update(values)
    path.write_text(json.dumps({
        "profile": access_check.PROFILE, "task_id": "AAH-S03", "mxc_profile": live_dev01.PROFILE, "status": "PASS",
        "bq09": {"status": "PASS"}, "bq10": {"status": "PASS"}, "agent_turns": 0,
        "base_revision": repository.initial_commit, "conditions": conditions,
    }))
    return path


def test_five_slots_bind_frozen_patches_to_independent_verdicts(tmp_path, operator_path):
    output = tmp_path / "schedule"
    report = main_schedule.verify(output, access_record=write_record(tmp_path / "access.json"), record_sha256=None)

    assert report["status"] == "PASS", report.get("reason")
    assert [s["slot_id"] for s in report["slots"]] == [f"slot-{i:04}" for i in range(1, 6)]
    assert sorted(s["condition"] for s in report["slots"]) == list("ABCDE")
    assert operator_path == [["--version"]] * 5
    for slot in report["slots"]:
        assert slot["permission_gate"]["observed"] == LOGGED_HASH and slot["permission_gate"]["status"] == "MATCH"
        assert slot["snapshot_gate"]["observed"] == LOGGED_SNAPSHOT and slot["snapshot_gate"]["status"] == "MATCH"
        assert slot["launch_attempts"] == 0 and slot["public_check_status"] == "UNKNOWN"  # checks recorded NOT_RUN
        assert slot["evaluation_status"] == "PASS" and all(slot["binding"].values()), slot["binding"]
    assert len({s["run_id"] for s in report["slots"]}) == 5
    assert report["input_parity"]["B_equals_D"] and report["input_parity"]["C_equals_E"]
    assert {gap["id"] for gap in report["operational_gaps"]} >= {"LIVE_RUNTIME", "PUBLIC_CHECKS", "CURATOR_EVALUATOR"}
    for slot in report["slots"]:
        request = json.loads((output / "independent" / slot["run_id"] / "request.json").read_bytes())
        assert (request["run_id"], request["task_id"], request["patch"]["patch_hash"]) == (
            slot["run_id"], "AAH-S03", slot["patch_hash"])
    assert json.loads((output / "schedule-verification.json").read_bytes())["status"] == "PASS"


@pytest.mark.parametrize("field, value, code", [
    ("permission_settings_hash", "0" * 64, "PERMISSION_SETTINGS_MISMATCH"),
    ("initial_snapshot_id", "0" * 64, "INITIAL_SNAPSHOT_MISMATCH"),
    ("initial_snapshot_id", None, "INITIAL_SNAPSHOT_MISSING"),
])
def test_mismatched_slot_stops_before_claim_and_turn(tmp_path, operator_path, field, value, code):
    output = tmp_path / "schedule"
    # Find the third scheduled condition with a clean plan, then break only that condition.
    probe = main_schedule.verify(tmp_path / "probe", access_record=write_record(tmp_path / "ok.json"),
                                 record_sha256=None)
    schedule = probe["schedule"]
    target = schedule[2]
    operator_path.clear()
    record = write_record(tmp_path / "access.json", **{target["condition"]: {field: value}})
    report = main_schedule.verify(output, access_record=record, record_sha256=None)

    assert (report["status"], report["code"]) == ("BLOCKED", code)
    assert (report["blocked_slot"], report["blocked_condition"]) == (target["slot_id"], target["condition"])
    assert report["schedule"] == schedule
    receipt = report["slot_receipts"][target["slot_id"]]
    assert receipt["launch_attempts"] == 0 and receipt["status"] is None and receipt["workspace_cleaned"]
    # Earlier slots ran; the blocked slot was never claimed and no later slot started.
    slots = output / "experiment" / "slots"
    assert all((slots / s["slot_id"] / "attempt.json").exists() for s in schedule[:2])
    assert not (slots / target["slot_id"] / "attempt.json").exists()
    assert set(report["slot_receipts"]) == {s["slot_id"] for s in schedule[:3]}
    assert operator_path == [["--version"]] * 2  # the blocked slot starts no Codex process
    assert not (output / "independent" / receipt["run_id"]).exists()


def test_missing_access_record_blocks_before_planning(tmp_path, operator_path):
    output = tmp_path / "schedule"
    report = main_schedule.verify(output, access_record=tmp_path / "absent.json", record_sha256=None)
    assert (report["status"], report["code"]) == ("BLOCKED", "ACCESS_RECORD_MISSING")
    assert operator_path == [] and not (output / "experiment").exists() and report["slot_receipts"] == {}
