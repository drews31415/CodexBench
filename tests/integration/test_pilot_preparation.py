import json
from runpy import run_path

import pytest

from aah.domain import canonical_json_bytes
from aah.experiments import runner
from aah.workspace import WorkspaceManager, apply_frozen_patch
from benchmarks.pilot import prepare
from evaluation.smoke import FIXTURES


@pytest.mark.parametrize(("task_id", "entry_point"), [
    ("DEV-03", "can_accept"),
    ("DEV-04", "count_by_status"),
    ("DEV-05", "convert_rows"),
])
@pytest.mark.parametrize("repeat", [1, 2])
def test_pilot_fixture_baseline_and_reference_round_trip(tmp_path, task_id, entry_point, repeat):
    task = prepare.task_directory(task_id)
    external_suite = FIXTURES / task_id
    artifact_root = tmp_path / "artifacts"
    public = json.loads((task / "public.json").read_bytes())
    suite = json.loads((external_suite / "suites.json").read_bytes())
    assert {case_id for cases in public.values() for case_id, _, _ in cases}.isdisjoint(
        {case_id for cases in suite.values() for case_id, _, _ in cases}
    )
    for variant, expected_functional in (("baseline", False), ("reference", True)):
        request = prepare.task_request(task_id)
        source = request.repository.source_path
        manager = WorkspaceManager(source, tmp_path / "work", artifact_root, quiescence_check=lambda _: True)
        expected_file = (source if variant == "baseline" else external_suite) / "module.py"
        handle = manager.prepare(request)
        try:
            if variant == "reference":
                (handle.root / "module.py").write_bytes(expected_file.read_bytes())
            patch = manager.freeze_patch(handle, manager.snapshot(handle))
        finally:
            manager.cleanup(handle)
        replay = tmp_path / f"replay-{variant}"
        apply_frozen_patch(source, patch, artifact_root, replay, repo_id=task_id)
        module = replay / "module.py"
        assert module.read_bytes() == expected_file.read_bytes()
        # Only run the byte-for-byte baseline and reference authored for these fixtures.
        function = run_path(str(module))[entry_point]

        def matches(cases):
            for _, args, expected in cases:
                actual = function(*args)
                if type(actual) is not type(expected) or actual != expected:
                    return False
            return True

        assert matches(public["P-FUNC"]) is expected_functional
        assert matches(suite["functional"]) is expected_functional
        assert matches(public["P-REG"]) and matches(suite["regression"])


def test_pilot_draft_is_reproducible_and_matches_runner_schedule(tmp_path, monkeypatch):
    plan = prepare.build_plan()
    assert not plan["launchable"] and plan["pilot_live_approval"] is None
    assert len(plan["slots"]) == 25
    assert [plan["proposed_limits"][key] for key in ("initial_turns", "recovery_turns", "total_turns")] == [25, 40, 65]
    assert all(slot.condition in "ABCDE" for slot in plan["slots"])
    inputs = dict(tasks=plan["task_assets"], repetitions=1, seed=plan["execution_order_seed"],
                  config={}, probe={}, environment_hash="0"*64, code_version="0"*64)
    manifest = runner.plan(tmp_path / "schedule-reference", plan["experiment_id"], **inputs)
    schedule = runner.pinned(tmp_path / "schedule-reference", manifest.schedule_ref, manifest.schedule_hash)
    assert schedule == json.loads(canonical_json_bytes(plan["slots"]))
    draft = tmp_path / "draft.json"
    monkeypatch.setattr("sys.argv", ["prepare", "--output", str(draft)])
    assert prepare.main() == 0
    assert prepare.main() == 0
    assert draft.read_bytes() == canonical_json_bytes(plan)
