"""Streamlit dashboard: data layer and view models (always), full app renders (when Streamlit is installed)."""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from aah_dashboard import data, fmt, models  # noqa: E402

STORE = ROOT / "public_data"
APP = ROOT / "dashboard/streamlit_app.py"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _clean(value, where):
    for text in _strings(value):
        assert "None" not in text and "nan" not in text.lower().split(), f"{where}: {text[:120]}"


# ---------------------------------------------------------------- data layer and view models (no Streamlit)

def test_store_loads_and_releases_resolve():
    store = data.Store(STORE)
    catalog = store.catalog()
    entries = store.experiments_in(catalog["default_release"])
    assert entries[0]["key"] == "aah-main-emergency-v2" and all(e["pin_ok"] for e in entries)
    assert {e["key"] for e in store.experiments_in(data.LATEST)} >= {e["key"] for e in entries}
    assert store.verification(catalog["default_release"])["steps"]


def test_every_view_model_builds_for_every_selection():
    store = data.Store(STORE)
    synthetic = [s for s, _ in store.synthetic()]
    for entry in store.experiments_in(data.LATEST):
        export, _ = store.experiment(entry["key"])
        _clean([models.kpis(export), models.settings(export), models.status_banner(export) or ""], entry["key"])
        assert models.condition_rows(export) and models.rq_rows(export) and models.matrix_rows(export)
        for task in models.tasks(export):
            for cond in fmt.CONDITIONS:
                run = models.slot_run(export, task, cond)
                assert run is not None
                _clean([models.run_summary(run), models.event_rows(run), models.timeline_rows(run)], f"{task}/{cond}")
                models.check_rows(run), models.usage_rows(run), models.overhead_rows(run), models.context_packages(run)
                for iv in run.get("interventions") or []:
                    _clean(models.intervention_kv(iv), f"{task}/{cond} intervention")
            assert len(models.context_comparison(export, task)) == 5
        for graph in models.graph_entries(export, synthetic):
            model = models.graph_model(graph)
            assert model["dot"].count("{") == model["dot"].count("}") and "->" in model["dot"]
        for case in models.replay_cases(export, synthetic):
            for step in range(len(models.STEPS)):
                blocks = models.replay_step(case, step)
                assert blocks and all(b[0] in ("text", "kv", "table", "callout") for b in blocks)
                _clean([b for b in blocks if b[0] != "table"], f"{case['key']} step {step}")


def test_dashboard_numbers_match_the_submission_documents():
    export, _ = data.Store(STORE).experiment("aah-main-emergency-v2")
    rq = {row["사전 지정 대비"]: row for row in models.rq_rows(export)}
    docs = (ROOT / "submission.md").read_text(encoding="utf-8") + (ROOT / "docs/submission/EVIDENCE_PACK.md").read_text(encoding="utf-8")
    assert rq["P-R1 = CPS(C) − CPS(B)"]["점추정"] in docs and rq["P-R1 = CPS(C) − CPS(B)"]["95% 구간"] in docs
    assert rq["P-R3 = E − max(C, D)"]["점추정"] in docs
    for row in models.condition_rows(export):
        for column in ("과제 평균 성공률", "모델 토큰", "성공당 토큰"):
            assert row[column] in docs, (row["조건"], column)
    assert models.kpis(export)[3][1] == "33 / 7"


def test_cache_shows_changed_data_without_a_restart(tmp_path):
    shutil.copytree(STORE, tmp_path / "store")
    folder = tmp_path / "store/experiments/aah-main-emergency-v2"
    store = data.Store(tmp_path / "store")
    assert store.experiment("aah-main-emergency-v2")[0]["label"] == "Main v2"
    export = json.loads((folder / "export.json").read_text(encoding="utf-8"))
    export["label"] = "Main v2 (갱신)"
    body = json.dumps(export, ensure_ascii=False).encode("utf-8")
    (folder / "export.json").write_bytes(body)
    with pytest.raises(data.DataError):  # data changed but MANIFEST not: integrity check, no stale value
        store.experiment("aah-main-emergency-v2")
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest["files"]["export.json"] = __import__("hashlib").sha256(body).hexdigest()
    (folder / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert store.experiment("aah-main-emergency-v2")[0]["label"] == "Main v2 (갱신)"


def test_missing_or_incompatible_store_is_reported(tmp_path):
    with pytest.raises(data.DataError):
        data.Store(tmp_path / "nothing")
    (tmp_path / "catalog.json").write_text(json.dumps({"schema_version": "2.0"}), encoding="utf-8")
    with pytest.raises(data.DataError):
        data.Store(tmp_path).catalog()


def test_dashboard_code_is_portable_and_self_contained():
    allowed = {"streamlit", "altair", "pandas", "aah_dashboard", "__future__"} | set(sys.stdlib_module_names)
    for path in (ROOT / "dashboard").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:\\\\|/Users/|/home/", source), path
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module.split(".")[0] in allowed, (path, node.module)
            elif isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] in allowed for a in node.names), path
    requirements = (ROOT / "dashboard/requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^streamlit==\d+\.\d+\.\d+$", requirements, re.M)
    assert (ROOT / ".streamlit/config.toml").is_file()


# ---------------------------------------------------------------- full app (Streamlit AppTest)

def _app(monkeypatch=None, store: Path | None = None):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    if monkeypatch is not None and store is not None:
        monkeypatch.setenv("AAH_PUBLIC_DATA", str(store))
    data.clear_cache()
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    return at


def _ok(at, where):
    assert not at.exception, (where, [str(e.value)[:300] for e in at.exception])
    failures = ("화면을 그리는 중 오류", "무결성 오류", "공개 데이터를 읽지 못했다", "release가 고정한 데이터")
    assert not [e for e in at.error if any(f in str(e.value) for f in failures)], (where, [e.value for e in at.error])


def test_app_renders_every_page():
    at = _app()
    _ok(at, "start")
    for page in ("overview", "compare", "trace", "graph", "context", "replay", "audit"):
        at.sidebar.radio(key="page").set_value(page).run()
        _ok(at, page)


def test_app_renders_every_selection():
    at = _app()
    store = data.Store(STORE)
    export, _ = store.experiment(at.sidebar.selectbox(key="exp").value)
    synthetic = [s for s, _ in store.synthetic()]
    at.sidebar.radio(key="page").set_value("graph").run()
    graphs = [g["key"] for g in models.graph_entries(export, synthetic)]
    assert len(at.selectbox(key="graph").options) == len(graphs) > 0
    for key in graphs:
        at.selectbox(key="graph").set_value(key).run()
        _ok(at, f"graph {key}")
    at.sidebar.radio(key="page").set_value("replay").run()
    cases = [c["key"] for c in models.replay_cases(export, synthetic)]
    assert len(at.selectbox(key="case").options) == len(cases) > 0
    for key in cases:
        at.selectbox(key="case").set_value(key).run()
        _ok(at, f"replay {key}")
        at.segmented_control(key="step").set_value(models.STEPS[-1]).run()
        _ok(at, f"replay {key} last step")
    for page in ("trace", "context"):
        at.sidebar.radio(key="page").set_value(page).run()
        for task in models.tasks(export):
            at.selectbox(key="task").set_value(task).run()
            for cond in fmt.CONDITIONS:
                at.selectbox(key="cond").set_value(cond).run()
                _ok(at, f"{page} {task} {cond}")


def test_app_marks_invalid_and_switches_release():
    at = _app()
    at.sidebar.selectbox(key="exp").select("aah-main-emergency-v1").run()
    assert any("무효 실험" in str(e.value) for e in at.error)
    at.sidebar.selectbox(key="release").select(data.LATEST).run()
    _ok(at, "latest release")


def test_app_shows_in_progress_experiment_without_results(tmp_path, monkeypatch):
    from analysis import public_export as pe

    store = tmp_path / "store"
    shutil.copytree(STORE, store)
    export = json.loads((STORE / "experiments/aah-main-emergency-v2/export.json").read_text(encoding="utf-8"))
    export.update(experiment_id="aah-main-next-v3", label="Main v3 (진행 중 예시)", status="IN_PROGRESS",
                  status_label="진행 중", status_reason="테스트용 진행 중 실험", conditions=None, contrasts=None,
                  dispositions=None, progress={"planned": 40, "evaluated": 12, "attempted": 13, "complete": False})
    pe.write_entry(store, "experiments", "aah-main-next-v3", export, {}, {}, now="2026-12-01T00:00:00Z")
    pe.write_catalog(store, pe.load_registry(ROOT))
    at = _app(monkeypatch, store)
    at.sidebar.selectbox(key="release").select(data.LATEST).run()
    at.sidebar.selectbox(key="exp").select("aah-main-next-v3").run()
    assert any("진행 중" in str(w.value) for w in at.warning)
    at.sidebar.radio(key="page").set_value("compare").run()
    assert any("확정되지 않아" in str(i.value) for i in at.info)
    _ok(at, "in progress")


def test_app_reports_a_missing_store(tmp_path, monkeypatch):
    at = _app(monkeypatch, tmp_path / "empty")
    assert any("공개 데이터를 읽지 못했다" in str(e.value) for e in at.error)
