"""Synthetic checks of the multi-task Main path (AMD-01): no Codex model, no Docker, synthetic curator suites."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aah.domain import Condition, ContractError, RunRequest, canonical_json_bytes, load_record, resolve_condition, sha256_bytes
from aah.workspace import WorkspaceManager
from analysis import main_analysis
from benchmarks.main_public import access_check, amd01_objective, amd01_variants, main_freeze, main_runner, main_schedule
from benchmarks.smoke import live_dev01
from evaluation import amd01_curator, main_curator
from evaluation.runner import EvaluationRequest, write_result


IDENTITY = {"transport": "local-synthetic-test"}
# Normalized live settings after the agent shell environment fix (EL-20261127-034).
LOGGED_HASH = "74e24628b3c06fd3ddd975fe8e57c04b8ef3848eb9981074cfdbe1f2a214c44b"
TASKS = ("AAH-S02", "AAH-S03")
MODULE = {"AAH-S02": "line_total.py", "AAH-S03": "csv_rows.py"}
SYNTHETIC_SUITES = {
    "AAH-S02": {
        "functional": b'''import unittest
from decimal import Decimal
from line_total import invoice_total

class SyntheticFunctional(unittest.TestCase):
    def test_half_up_after_sum(self):
        self.assertEqual(invoice_total([("0.125", 1)]), Decimal("0.13"))
''',
        "regression": b'''import unittest
from decimal import Decimal
from line_total import invoice_total

class SyntheticRegression(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(invoice_total([("2.50", 2)]), Decimal("5.00"))
''',
    },
    "AAH-S03": {
        "functional": b'''import unittest
from csv_rows import load_rows

class SyntheticFunctional(unittest.TestCase):
    def test_zero_empty_and_raw_id(self):
        self.assertEqual(load_rows("id,score\\n x,0\\nr,\\n"), [{"id": " x", "score": 0}, {"id": "r", "score": None}])
''',
        "regression": b'''import unittest
from csv_rows import load_rows

class SyntheticRegression(unittest.TestCase):
    def test_order(self):
        self.assertEqual([r["id"] for r in load_rows("id,score\\nz,1\\ny,2\\n")], ["z", "y"])
''',
    },
}


def local_transport(script, flags, payload, deadline):
    return subprocess.run([sys.executable, *flags, "-c", script.decode("utf-8")], input=payload,
                          capture_output=True, timeout=30)


def fix_for(task_id):
    variant = next(v for v in amd01_variants.VARIANTS[task_id] if v["kind"] == "alternative")
    return variant["files"]


class InProcessCurator:
    def __init__(self, kit):
        self.kit = main_curator.CuratorKit(kit, identity=IDENTITY)

    def manifest_hash(self, home):
        return self.kit.manifest_hash

    def evaluate(self, request_path, artifacts, results):
        request = load_record(EvaluationRequest, request_path.read_bytes())
        write_result(results, self.kit.evaluate(request, artifacts, transport=local_transport))


def fake_turn():
    events = ({"type": "thread.started", "thread_id": str(uuid4())}, {"type": "turn.started"},
              {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}})
    return SimpleNamespace(status="EXITED", exit_code=0, stdout=b"\n".join(canonical_json_bytes(e) for e in events),
                           stderr=b"", total_processes=1, remaining_process_count=0)


def snapshot_id(task_id, tmp):
    task, fixed, repository = main_runner.task_inputs(task_id)
    request = RunRequest(str(uuid4()), task_id, None, repository, fixed, resolve_condition(Condition.A),
                         sha256_bytes(b"x"), "user")
    manager = WorkspaceManager(repository.source_path, tmp / f"w-{task_id}", tmp / f"a-{task_id}",
                               quiescence_check=lambda _: True)
    handle = manager.prepare(request)
    try:
        return manager.snapshot(handle).snapshot_id
    finally:
        manager.cleanup(handle)


def make_kit(root, task_id):
    _, _, repository = main_runner.task_inputs(task_id)
    kit = root / task_id
    kit.mkdir(parents=True)
    shutil.copyfile(repository.source_path.parent / "task.json", kit / "task.json")
    shutil.copytree(repository.source_path, kit / "baseline")
    shutil.copytree(repository.source_path, kit / "reference")
    for path, source in fix_for(task_id).items():
        (kit / "reference" / path).write_text(source, encoding="utf-8")
    (kit / "curator_tests").mkdir()
    for kind, body in SYNTHETIC_SUITES[task_id].items():
        (kit / "curator_tests" / f"test_{kind}.py").write_bytes(body)
    return kit


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(access_check, "_find_codex", lambda: tmp_path / "codex.exe")
    monkeypatch.setattr(main_freeze, "MIN_TASKS", 2)
    launches = []

    def launch(argv, **kwargs):
        args = list(argv[1:])
        launches.append(args[0] if args[0] == "exec" else args)
        if args == ["--version"]:
            return SimpleNamespace(status="EXITED", exit_code=0, stdout=b"codex-cli 0.166.0\n", stderr=b"",
                                   total_processes=1, remaining_process_count=0)
        assert args[0] == "exec" and kwargs["stdin_data"]
        task_id = next(t for t, module in MODULE.items() if (kwargs["cwd"] / module).exists())
        for path, source in fix_for(task_id).items():  # the synthetic "agent" fix
            (kwargs["cwd"] / path).write_text(source, encoding="utf-8")
        return fake_turn()

    monkeypatch.setattr(access_check, "_launch", launch)
    kits = tmp_path / "kits"
    curators, entries = {}, {}
    for task_id in TASKS:
        _, _, repository = main_runner.task_inputs(task_id)
        record = tmp_path / f"access-{task_id}.json"
        record.write_text(json.dumps({
            "profile": access_check.PROFILE, "task_id": task_id, "mxc_profile": live_dev01.PROFILE, "status": "PASS",
            "bq09": {"status": "PASS"}, "bq10": {"status": "PASS"}, "agent_turns": 0,
            "base_revision": repository.initial_commit,
            "conditions": {c.value: {"permission_settings_hash": LOGGED_HASH,
                                     "initial_snapshot_id": snapshot_id(task_id, tmp_path)} for c in Condition}}))
        curators[task_id] = InProcessCurator(make_kit(kits, task_id))
        entries[task_id] = {"included": True, "reasons": [], "access_record": str(record),
                            "access_record_sha256": sha256_bytes(record.read_bytes()),
                            "curator_manifest_sha256": curators[task_id].kit.manifest_hash,
                            "reviewer_id": "ai-review:synthetic", "review_evidence_id": "e" * 64,
                            "partials_detected": "1/3"}
    inclusion = tmp_path / "inclusion.json"
    inclusion.write_bytes(main_freeze.render({
        "schema_version": "1.0", "amendment": "AMD-01", "amendment_sha256": main_freeze.AMENDMENT_SHA256,
        "included": list(TASKS), "min_tasks": 2, "status": "READY", "tasks": entries}))
    freeze = tmp_path / "freeze.json"
    main_freeze.write(freeze, inclusion, identity=IDENTITY)
    budget = main_freeze.budget_for(len(TASKS))
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({
        "approved_freeze_sha256": sha256_bytes(freeze.read_bytes()), "amendment_sha256": main_freeze.AMENDMENT_SHA256,
        "profile": budget["profile"], "max_extra_payment_krw": 0, "max_initial_turns": budget["max_initial_turns"],
        "max_recovery_turns": budget["max_recovery_turns"], "max_total_turns": budget["max_total_turns"],
        "g2_joint_approval": {"RH": "synthetic", "GR": "synthetic", "CE": "synthetic"}}))
    return SimpleNamespace(tmp=tmp_path, launches=launches, curators=curators, freeze=freeze, approval=approval,
                           inclusion=inclusion, live=tmp_path / "live", kits=kits)


def run(world, **overrides):
    kwargs = dict(freeze=world.freeze, approval=world.approval, inclusion=world.inclusion, live_parent=world.live,
                  identity=IDENTITY, transport=local_transport)
    kwargs.update(overrides)
    curators = kwargs.pop("curators", world.curators)
    return main_schedule.execute_live(curators, **kwargs)


def test_unlaunchable_agent_shell_stops_the_schedule_at_the_first_slot(world, monkeypatch):
    """EL-20261127-034: every agent command failed with an MXC launcher error in aah-main-emergency-v1."""
    blocked = {"type": "item.completed", "item": {
        "id": "item_1", "type": "command_execution", "command": "cmd /c dir", "exit_code": 1, "status": "failed",
        "aggregated_output": "MXC launcher: The supplied `process.env` cannot launch a Windows process container "
                             "because it is missing the required variable(s): SYSTEMROOT."}}

    def broken_turn():
        events = ({"type": "thread.started", "thread_id": str(uuid4())}, {"type": "turn.started"}, blocked,
                  {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}})
        return SimpleNamespace(status="EXITED", exit_code=0, stderr=b"", total_processes=1, remaining_process_count=0,
                               stdout=b"\n".join(canonical_json_bytes(e) for e in events))

    monkeypatch.setattr(sys.modules[__name__], "fake_turn", broken_turn)
    with pytest.raises(ContractError, match="AGENT_SHELL_UNAVAILABLE"):
        run(world)
    assert world.launches.count("exec") == 1


def test_access_refresh_rebinds_records_without_changing_inclusion(world, monkeypatch):
    root = world.tmp / "rechecked"
    for task_id in TASKS:
        target = root / f"access-{task_id}-20990101-000000"
        target.mkdir(parents=True)
        shutil.copyfile(world.tmp / f"access-{task_id}.json", target / "access.json")
    monkeypatch.setattr(main_freeze, "EXPERIMENT_ID", "synthetic-v2")
    refresh = main_freeze.refresh_access(root, world.inclusion)
    assert sorted(refresh["tasks"]) == list(TASKS) and refresh["tasks"][TASKS[0]]["permission_settings_hash"] == [
        LOGGED_HASH]
    path = world.tmp / "refresh.json"
    path.write_bytes(canonical_json_bytes(refresh))
    freeze = world.tmp / "freeze-v2.json"
    main_freeze.write(freeze, world.inclusion, identity=IDENTITY, access_refresh=path)
    same, draft = main_freeze.reproduce(freeze, world.inclusion, identity=IDENTITY)
    assert same and draft["access_refresh"]["sha256"] == sha256_bytes(path.read_bytes())
    assert {t["access_record"] for t in draft["tasks"].values()} == {
        v["access_record"] for v in refresh["tasks"].values()}
    path.write_bytes(canonical_json_bytes(refresh | {"inclusion_sha256": "0" * 64}))
    with pytest.raises(ContractError, match="not bound to this inclusion"):
        main_freeze.build(world.inclusion, identity=IDENTITY, access_refresh=path)


def test_freeze_is_reproducible_and_scales_budget_with_tasks(world):
    same, draft = main_freeze.reproduce(world.freeze, world.inclusion, identity=IDENTITY)
    assert same and sorted(draft["tasks"]) == list(TASKS)
    assert len(draft["schedule"]["slots"]) == 10
    assert {s["task_id"] for s in draft["schedule"]["slots"]} == set(TASKS)
    assert (draft["budget"]["max_initial_turns"], draft["budget"]["max_recovery_turns"],
            draft["budget"]["max_total_turns"]) == (10, 16, 26)
    assert draft["amendment"]["sha256"] == main_freeze.AMENDMENT_SHA256
    assert draft["analysis"]["config_sha256"] == main_analysis.AMD01_CONFIG_SHA256


def test_locked_freeze_runs_every_task_slot_with_curator_verdicts(world):
    summary = run(world)
    assert len(summary["runs"]) == 10
    assert all(r["evaluation_status"] == "PASS" and r["public_check_status"] == "PASS" and r["model_tokens"] == 6
               for r in summary["runs"])
    assert world.launches.count("exec") == 10 and world.launches.count(["--version"]) == 10
    root = world.live / main_freeze.EXPERIMENT_ID
    for row in summary["runs"]:
        verdict = json.loads((root / "independent" / row["run_id"] / "results" / f"{row['run_id']}.json").read_bytes())
        assert verdict["patch_hash"] == row["patch_hash"]
    assert run(world) == summary and world.launches.count("exec") == 10
    analysis = main_analysis.analyze(root, config=main_analysis.AMD01_ANALYSIS_CONFIG)
    assert analysis["dispositions"] == {"VALID_SUCCESS": 10}
    assert analysis["config_sha256"] == main_analysis.AMD01_CONFIG_SHA256
    assert {c["n_clusters"] for c in analysis["contrasts"]} == {2}


def _assert_blocked(world, unmet, **overrides):
    with pytest.raises(main_freeze.FreezeGateError) as caught:
        run(world, **overrides)
    assert unmet in caught.value.unmet
    assert world.launches == [] and not (world.live / main_freeze.EXPERIMENT_ID).exists()


def test_missing_approval_blocks_before_any_process(world):
    _assert_blocked(world, "EXECUTION_APPROVAL_MISSING", approval=world.tmp / "absent.json")


def test_below_minimum_task_count_blocks(world, monkeypatch):
    monkeypatch.setattr(main_freeze, "MIN_TASKS", 5)
    _assert_blocked(world, "BELOW_MINIMUM_TASKS")


def test_edited_freeze_or_missing_joint_approval_blocks(world):
    signed = json.loads(world.approval.read_bytes())
    signed["g2_joint_approval"]["GR"] = None
    world.approval.write_text(json.dumps(signed))
    data = json.loads(world.freeze.read_bytes())
    data["budget"]["max_total_turns"] = 99
    world.freeze.write_text(json.dumps(data))
    with pytest.raises(main_freeze.FreezeGateError) as caught:
        run(world)
    assert {"FREEZE_NOT_REPRODUCIBLE", "APPROVAL_NOT_BOUND_TO_FREEZE", "G2_JOINT_APPROVAL_MISSING"} <= set(
        caught.value.unmet)
    assert world.launches == []


def test_curator_mismatch_or_missing_kit_blocks_before_any_process(world):
    with pytest.raises(main_schedule.ContractError, match="curator kit is needed"):
        run(world, curators={"AAH-S03": world.curators["AAH-S03"]})
    world.curators["AAH-S02"].kit.manifest_hash = "0" * 64
    with pytest.raises(main_schedule.ContractError, match="curator evaluator manifest"):
        run(world)
    assert world.launches == []


def test_curator_batch_judges_reference_and_variants(world):
    report = amd01_curator.evaluate_kit(world.kits / "AAH-S03", identity=IDENTITY, transport=local_transport)
    assert report["checks"] == {"BQ02": "PASS", "BQ03": "PASS", "BQ08": "PASS", "BQ05": "PASS",
                                "BQ04_06_objective": "PASS"}
    assert report["variants"]["partial-strip-id"]["status"] == "FAIL"
    assert report["alternatives_failed"] == [] and report["partials_detected"] >= 1
    assert "curator_tests" not in json.dumps(report)


def test_review_summary_exports_codes_only(world, monkeypatch):
    instructions = sha256_bytes(amd01_curator.INSTRUCTIONS.read_bytes())
    (world.kits / "amd01-reviews").mkdir()
    review = {"task_id": "AAH-S03", "reviewer_model": "synthetic-model", "instructions_sha256": instructions,
              "verdicts": dict.fromkeys(amd01_curator.REVIEW_ITEMS, "PASS"), "failing_alternatives": [],
              "rationale": {"BQ04": "PRIVATE RATIONALE TEXT"}, "decision": "APPROVE"}
    path = world.kits / "amd01-reviews" / "AAH-S03.json"
    path.write_text(json.dumps(review))
    summary = amd01_curator.summarize_reviews(world.kits)
    entry = summary["tasks"]["AAH-S03"]
    assert entry["status"] == "RECORDED" and entry["decision"] == "APPROVE"
    assert entry["evidence_id"] == sha256_bytes(path.read_bytes())
    assert summary["tasks"]["AAH-S02"]["status"] == "MISSING"
    assert "PRIVATE RATIONALE TEXT" not in json.dumps(summary)


def test_inclusion_applies_amd01_rules(tmp_path):
    def dev_task(**checks):
        return {"checks": {k: "PASS" for k in main_freeze.DEV_CHECKS} | checks,
                "access": {"path": "x.json", "sha256": "a" * 64}}

    def curator_task(failed=()):
        return {"checks": {k: "PASS" for k in main_freeze.CURATOR_CHECKS}, "alternatives_failed": list(failed),
                "curator_manifest_sha256": "c" * 64, "partials_detected": 2, "partials_total": 3}

    def review(decision="APPROVE", justified=None):
        return {"status": "RECORDED", "verdicts": dict.fromkeys(main_freeze.REVIEW_ITEMS, "PASS"),
                "decision": decision, "reviewer_id": "ai-review:m", "evidence_id": "r" * 64,
                "failing_alternatives_justified": justified or {}}

    dev = {"variants_manifest_sha256": amd01_variants.MANIFEST_SHA256,
           "tasks": {t: dev_task() for t in main_freeze.CANDIDATES}}
    dev["tasks"]["AAH-C02"] = dev_task(BQ09="FAIL")
    dev_path = tmp_path / "dev.json"
    dev_path.write_bytes(canonical_json_bytes(dev))
    curator = {"variants_manifest_sha256": amd01_variants.MANIFEST_SHA256, "dev_report_sha256": sha256_bytes(
        dev_path.read_bytes()), "tasks": {t: curator_task() for t in main_freeze.CANDIDATES}}
    curator["tasks"]["AAH-D02"] = curator_task(failed=["alt-inline-release"])
    curator["tasks"]["AAH-D04"] = curator_task(failed=["alt-sorted-tuples"])
    reviews = {"tasks": {t: review() for t in main_freeze.CANDIDATES}}
    reviews["tasks"]["AAH-D04"] = review(justified={"alt-sorted-tuples": True})
    reviews["tasks"]["AAH-R03"] = review(decision="HOLD")
    for name, obj in (("curator.json", curator), ("reviews.json", reviews)):
        (tmp_path / name).write_bytes(canonical_json_bytes(obj))
    record = main_freeze.decide_inclusion(dev_path, tmp_path / "curator.json", tmp_path / "reviews.json")
    tasks = record["tasks"]
    assert tasks["AAH-C02"]["reasons"] == ["BQ09_FAIL"]
    assert tasks["AAH-D02"]["reasons"] == ["UNJUSTIFIED_ALTERNATIVE_FAILURE"]
    assert tasks["AAH-D04"]["included"] and tasks["AAH-R03"]["reasons"] == ["REVIEW_HOLD"]
    assert record["included"] == ["AAH-C03", "AAH-D04", "AAH-R04", "AAH-S02", "AAH-S03"]
    assert record["status"] == "READY"


def test_fix_record_and_kit_hashes_export_codes_only(world):
    kit = world.kits / "AAH-S03"
    before = amd01_curator.hashes_all(world.kits)["tasks"]["AAH-S03"]
    assert set(before) == {"task_spec", "baseline", "reference", "suite"}
    assert amd01_curator.fix_record(world.kits, "AAH-S03") is None
    (world.kits / "amd01-fixes").mkdir()
    record = {"task_id": "AAH-S03", "fixer_model": "synthetic-fixer",
              "fix_instructions_sha256": sha256_bytes(amd01_curator.FIX_INSTRUCTIONS.read_bytes()),
              "changes": [{"file": "test_functional.py", "test": "test_x", "action": "REMOVED",
                           "reason": "PRIVATE FIX REASON", "spec_quote": ""}], "self_check": "PASS"}
    path = world.kits / "amd01-fixes" / "AAH-S03.json"
    path.write_text(json.dumps(record))
    entry = amd01_curator.fix_record(world.kits, "AAH-S03")
    assert entry["status"] == "RECORDED" and entry["changes"] == {"REMOVED": 1, "NARROWED": 0, "ADDED": 0}
    assert entry["evidence_id"] == sha256_bytes(path.read_bytes()) and "PRIVATE FIX REASON" not in json.dumps(entry)
    record["changes"][0] |= {"action": "ADDED"}  # an added assertion must quote the public spec
    path.write_text(json.dumps(record))
    assert amd01_curator.fix_record(world.kits, "AAH-S03")["problems"] == ["CHANGE_ENTRY"]
    suite = kit / "curator_tests" / "test_functional.py"
    suite.write_text(suite.read_text() + "\n# fixed\n")
    after = amd01_curator.kit_hashes(kit)
    assert [k for k in after if after[k] != before[k]] == ["suite"]


def _round_reports(tmp_path, name, *, curator_tasks, review_tasks, dev_path, before=False):
    curator = {"variants_manifest_sha256": amd01_variants.MANIFEST_SHA256,
               "dev_report_sha256": sha256_bytes(dev_path.read_bytes()), "tasks": curator_tasks}
    if before:
        curator["before_snapshot_sha256"] = "b" * 64
    paths = tmp_path / f"{name}-curator.json", tmp_path / f"{name}-reviews.json"
    paths[0].write_bytes(canonical_json_bytes(curator))
    paths[1].write_bytes(canonical_json_bytes({"tasks": review_tasks}))
    return paths


def test_fix_round_allows_one_suite_fix_with_a_fresh_review(tmp_path):
    hashes = {"task_spec": "1" * 64, "baseline": "2" * 64, "reference": "3" * 64, "suite": "4" * 64}

    def curator_task(suite="4" * 64, **round2):
        return {"checks": {k: "PASS" for k in main_freeze.CURATOR_CHECKS}, "alternatives_failed": [],
                "curator_manifest_sha256": "m" + suite[1:], "suite_sha256": suite, "environment_sha256": "e" * 64,
                "partials_detected": 2, "partials_total": 3, **round2}

    def review(evidence="r1", failed=(), decision=None):
        verdicts = {k: "FAIL" if k in failed else "PASS" for k in main_freeze.REVIEW_ITEMS}
        return {"status": "RECORDED", "verdicts": verdicts, "decision": decision or ("HOLD" if failed else "APPROVE"),
                "reviewer_id": "ai-review:m", "evidence_id": evidence * 32, "failing_alternatives_justified": {}}

    fix = {"status": "RECORDED", "problems": [], "fixer_id": "ai-fix:f", "evidence_id": "f" * 64,
           "changes": {"REMOVED": 1, "NARROWED": 0, "ADDED": 0}}
    fixed = hashes | {"suite": "5" * 64}

    def unchanged(**extra):
        return curator_task(kit_hashes=hashes, before_hashes=hashes, fix=None, **extra)

    def refixed(now=fixed, record=fix):
        return curator_task(suite=now["suite"], kit_hashes=now, before_hashes=hashes, fix=record)

    dev = {"variants_manifest_sha256": amd01_variants.MANIFEST_SHA256,
           "tasks": {t: {"checks": {k: "PASS" for k in main_freeze.DEV_CHECKS},
                         "access": {"path": "x.json", "sha256": "a" * 64}} for t in main_freeze.CANDIDATES}}
    dev_path = tmp_path / "dev.json"
    dev_path.write_bytes(canonical_json_bytes(dev))
    round1 = {t: curator_task() for t in main_freeze.CANDIDATES if t != "AAH-S02"}
    reviews1 = {t: review() for t in round1}
    reviews1 |= {"AAH-D02": review(failed=["BQ04"]), "AAH-R04": review(failed=["BQ04"]),
                 "AAH-C03": review(failed=["BQ01"]), "AAH-D04": review(failed=["BQ06"]),
                 "AAH-R03": review(failed=["BQ04", "BQ06"])}
    r1 = _round_reports(tmp_path, "r1", curator_tasks=round1, review_tasks=reviews1, dev_path=dev_path)
    assert main_freeze.decide_inclusion(dev_path, *r1)["status"] == "BELOW_MINIMUM"

    round2 = {"AAH-S03": unchanged(), "AAH-C02": unchanged(), "AAH-S02": curator_task(kit_hashes=hashes, fix=None),
              "AAH-D02": refixed(), "AAH-R04": refixed(), "AAH-C03": refixed(),
              "AAH-D04": refixed(now=fixed | {"reference": "9" * 64}), "AAH-R03": refixed(record=None)}
    round2["AAH-S02"]["before_hashes"] = None
    reviews2 = dict(reviews1) | {"AAH-S02": review("s2"), "AAH-C02": review("c2"), "AAH-D02": review("d2"),
                                 "AAH-C03": review("c3"), "AAH-D04": review("d4"), "AAH-R03": review("r3")}
    r2 = _round_reports(tmp_path, "r2", curator_tasks=round2, review_tasks=reviews2, dev_path=dev_path, before=True)
    record = main_freeze.decide_inclusion(dev_path, *r2, prior_rounds=[r1])
    tasks = record["tasks"]
    assert record["round"] == 2 and record["included"] == ["AAH-D02", "AAH-S02", "AAH-S03"]
    assert tasks["AAH-D02"]["suite_fixed"] and tasks["AAH-D02"]["fix_evidence_id"] == "f" * 64
    assert tasks["AAH-D02"]["fix_round"] == 2 and tasks["AAH-R04"]["fix_round"] == 2
    assert tasks["AAH-R04"]["reasons"] == ["REVIEW_BQ04_FAIL", "REVIEW_HOLD", "REVIEW_NOT_REDONE"]
    assert tasks["AAH-C03"]["reasons"] == ["FIX_NOT_PERMITTED"]
    assert tasks["AAH-D04"]["reasons"] == ["NON_SUITE_KIT_CHANGE"]
    assert tasks["AAH-R03"]["reasons"] == ["FIX_RECORD_MISSING"]
    assert tasks["AAH-C02"]["reasons"] == ["REVIEW_CHANGED_WITHOUT_FIX"]

    with pytest.raises(ContractError, match="earlier rounds' reports"):
        main_freeze.decide_inclusion(dev_path, *r2)
    with pytest.raises(ContractError, match="start from the first report"):
        main_freeze.decide_inclusion(dev_path, *r2, prior_rounds=[r2])

    # Round 3: a second fix of D02 is refused; S02 (first judged in round 2) and C02 may use their one fix,
    # and an unfixed round-2 re-evaluation does not use up AAH-S03's fix.
    hashes2 = fixed | {"suite": "6" * 64}
    round3 = {t: dict(v, before_hashes=v["kit_hashes"], fix=v["fix"]) for t, v in round2.items()}
    round3["AAH-D02"] = curator_task(suite="6" * 64, kit_hashes=hashes2, before_hashes=fixed, fix=fix | {"evidence_id": "g" * 64})
    round3["AAH-S02"] = curator_task(suite="5" * 64, kit_hashes=fixed, before_hashes=hashes, fix=fix)
    round3["AAH-S03"] = curator_task(suite="5" * 64, kit_hashes=fixed, before_hashes=hashes, fix=fix)
    reviews3 = dict(reviews2) | {"AAH-D02": review("d3"), "AAH-S02": review("s3"), "AAH-S03": review("t3")}
    reviews1["AAH-S03"] = review(failed=["BQ04"])
    r1 = _round_reports(tmp_path, "r1", curator_tasks=round1, review_tasks=reviews1, dev_path=dev_path)
    r2 = _round_reports(tmp_path, "r2", curator_tasks=round2, review_tasks=reviews2 | {"AAH-S03": review(
        failed=["BQ04"])}, dev_path=dev_path, before=True)
    r3 = _round_reports(tmp_path, "r3", curator_tasks=round3, review_tasks=reviews3, dev_path=dev_path, before=True)
    tasks = main_freeze.decide_inclusion(dev_path, *r3, prior_rounds=[r1, r2])["tasks"]
    assert "FIX_LIMIT_EXCEEDED" in tasks["AAH-D02"]["reasons"] and tasks["AAH-D02"]["fix_round"] == 2
    assert tasks["AAH-S02"]["reasons"] == ["FIX_NOT_PERMITTED"]  # S02 passed its first review in round 2
    assert tasks["AAH-S03"]["included"] and tasks["AAH-S03"]["fix_round"] == 3


def test_dev_objective_report_runs_public_suites_on_variants(tmp_path):
    access_root = tmp_path / "access"
    (access_root / "access-AAH-S03-1").mkdir(parents=True)
    (access_root / "access-AAH-S03-1" / "access.json").write_text(json.dumps({
        "status": "PASS", "bq09": {"status": "PASS"}, "bq10": {"status": "PASS"},
        "conditions": {"A": {"permission_settings_hash": LOGGED_HASH, "initial_snapshot_id": "s"}}}))
    report = amd01_objective.task_report("AAH-S03", identity=IDENTITY, transport=local_transport,
                                         access_root=access_root)
    assert report["checks"] == {"BQ07": "PASS", "BQ09": "PASS", "BQ10": "PASS", "alternatives_public": "PASS",
                                "partials_count": "PASS"}
    assert report["baseline_public"][0] == {"P-FUNC": "FAIL", "P-REG": "PASS"}
    assert report["partials_passing_public"] == 3
