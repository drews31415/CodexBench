"""Versioned public store for the web dashboard (model-free).

The committed store must verify on its own and equal the frozen submission snapshot; finalized data is
append-only; new or unfinished experiments are never presented as valid.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from analysis import public_export as pe
from analysis import submission_bundle as sb

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "public_data"
RAW_V2 = ROOT / "results/main-live/aah-main-emergency-v2"
ANALYSIS_V2 = ROOT / "results/analysis/aah-main-emergency-v2-amd01"
REGISTRY = pe.load_registry(ROOT)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- committed store (no results/ needed)

def test_committed_store_verifies_on_its_own():
    problems, lines = pe.verify_store(STORE, REGISTRY)
    assert problems == []
    assert any(line.startswith("RELEASE submission-2026-11-27") for line in lines)
    assert sum(line.startswith("PUBLIC_RECOMPUTE") for line in lines) == 2


def test_submission_release_equals_the_frozen_submission_snapshot():
    bundle = _json(ROOT / "docs/submission/data/aah-public-bundle.json")
    release = _json(STORE / "releases/submission-2026-11-27.json")
    assert release["primary"] == "aah-main-emergency-v2" and release["kind"] == "submission"
    for experiment_id in release["experiments"]:
        export = _json(STORE / "experiments" / experiment_id / "export.json")
        snapshot = bundle["experiments"][experiment_id]
        for key in ("conditions", "contrasts", "dispositions", "tasks", "slots", "runs", "derived", "meta"):
            assert export[key] == snapshot[key], key
        assert export["status"] == snapshot["validity"]
    assert _json(STORE / "synthetic/s3-verification/export.json")["runs"] == bundle["synthetic"]["runs"]
    assert release["verification"] == _json(ROOT / "docs/submission/data/verification-record.json")


def test_registry_agrees_with_the_submission_snapshot_spec():
    for experiment_id, spec in sb.EXPERIMENTS.items():
        entry = REGISTRY["experiments"][experiment_id]
        assert (entry["status"], entry["freeze"], entry["approval"], entry["log_ids"], entry["report_ref"],
                entry["analysis_dir"]) == (spec["validity"], spec["freeze"], spec["approval"], spec["log_ids"],
                                           spec["report_ref"], spec["analysis"])
    assert REGISTRY["synthetic"]["s3-verification"]["runs"] == sb.SYNTHETIC["runs"]


def test_status_resolution_never_assumes_validity():
    done, running = {"complete": True}, {"complete": False}
    assert pe.resolve_status(None, running, None)[0] == "IN_PROGRESS"
    status, source, warnings = pe.resolve_status({"status": "VALID"}, running, "a")
    assert (status, source) == ("IN_PROGRESS", "auto") and warnings
    assert pe.resolve_status({"status": "VALID"}, done, None)[0] == "ANALYSIS_PENDING"
    assert pe.resolve_status(None, done, None)[0] == "ANALYSIS_PENDING"
    assert pe.resolve_status(None, done, "a")[:2] == ("UNREVIEWED", "auto")
    assert pe.resolve_status({"status": "INVALID_INFRA_COMPROMISED"}, done, "a") == ("INVALID_INFRA_COMPROMISED", "registry", [])


def _export(status: str, value: int) -> dict:
    return {"experiment_id": "x", "status": status, "status_label": status, "label": "X",
            "slots": [{"y": value}], "progress": {"planned": 1, "evaluated": value}}


def test_store_is_append_only_and_keeps_status_history(tmp_path):
    store = tmp_path / "store"
    t = [f"2026-12-0{i}T00:00:00Z" for i in range(1, 7)]
    assert pe.write_entry(store, "experiments", "x", _export("IN_PROGRESS", 0), {}, {}, now=t[0]) == "WRITTEN"
    assert pe.write_entry(store, "experiments", "x", _export("IN_PROGRESS", 0), {}, {}, now=t[1]) == "NO_CHANGE"
    assert pe.write_entry(store, "experiments", "x", _export("IN_PROGRESS", 1), {}, {}, now=t[2]) == "UPDATED"
    assert pe.write_entry(store, "experiments", "x", _export("UNREVIEWED", 1), {}, {}, now=t[3]) == "STATUS_UPDATED"
    with pytest.raises(pe.StoreError):
        pe.write_entry(store, "experiments", "x", _export("UNREVIEWED", 2), {}, {}, now=t[4])
    manifest = _json(store / "experiments/x/MANIFEST.json")
    assert [h["to"] for h in manifest["status_history"]] == ["IN_PROGRESS", "UNREVIEWED"]
    assert manifest["exported_at"] == t[2] and manifest["status_updated_at"] == t[3]
    pe.write_entry(store, "experiments", "y", _export("IN_PROGRESS", 0), {}, {}, now=t[0])
    pe.create_release(store, "r1", label="R1", kind="interim", experiments=["x"], primary="x", now=t[5])
    with pytest.raises(pe.StoreError):
        pe.create_release(store, "r1", label="again", kind="interim", experiments=["x"], primary="x")
    with pytest.raises(pe.StoreError):
        pe.create_release(store, "r2", label="open", kind="interim", experiments=["y"], primary="y")
    catalog = pe.write_catalog(store, REGISTRY)
    assert catalog["data_updated_at"] == t[5] and catalog["default_release"] == "r1"
    assert pe.verify_store(store, REGISTRY)[0] == []
    (store / "experiments/x/export.json").write_text("{}", encoding="utf-8")
    assert pe.verify_store(store, REGISTRY)[0]


def test_store_rejects_restricted_fields(tmp_path):
    bad = {**_export("IN_PROGRESS", 0), "timeline": [{"text": "agent message"}]}
    with pytest.raises(sb.BundleError):
        pe.write_entry(tmp_path, "experiments", "x", bad, {}, {})


# ---------------------------------------------------------------- discovery from raw records

@pytest.mark.skipif(not RAW_V2.exists(), reason="results/ is local and access restricted")
def test_new_experiment_versions_are_discovered_with_honest_status(tmp_path):
    root = tmp_path / "checkout"
    raw = root / "results/main-live/new-run"
    shutil.copytree(RAW_V2, raw)
    for slot in ("slot-0039", "slot-0040"):
        shutil.rmtree(raw / "slots" / slot)
    registry = {**REGISTRY, "experiments": {}}
    found = pe.discover(root, registry)
    assert [f["experiment_id"] for f in found] == ["aah-main-emergency-v2"] and not found[0]["registered"]
    export, files, _ = pe.build_experiment_export(root, registry, found[0])
    assert export["status"] == "IN_PROGRESS" and export["conditions"] is None and files == {}
    assert (export["progress"]["evaluated"], export["progress"]["planned"]) == (38, 40)
    for slot in ("slot-0039", "slot-0040"):
        shutil.copytree(RAW_V2 / "slots" / slot, raw / "slots" / slot)
    export, _, _ = pe.build_experiment_export(root, registry, pe.discover(root, registry)[0])
    assert export["status"] == "ANALYSIS_PENDING" and export["contrasts"] is None
    shutil.copytree(ANALYSIS_V2, root / "results/analysis/new-analysis")
    export, files, _ = pe.build_experiment_export(root, registry, pe.discover(root, registry)[0])
    assert export["status"] == "UNREVIEWED" and export["status_source"] == "auto"
    assert export["conditions"] == _json(STORE / "experiments/aah-main-emergency-v2/export.json")["conditions"]
    assert export["meta"]["freeze"]["sha256"] is None and set(files) == set(sb.ANALYSIS_FILES)


@pytest.mark.skipif(not RAW_V2.exists(), reason="results/ is local and access restricted")
def test_committed_store_reproduces_from_raw_records():
    assert pe.check_store(ROOT) == []
