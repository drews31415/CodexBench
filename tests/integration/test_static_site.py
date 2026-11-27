"""Static GitHub Pages dashboard built from the versioned public store (no server, no results/ needed)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from analysis import static_site
from analysis import submission_bundle as sb

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / static_site.PAGE
CATALOG = json.loads((ROOT / "public_data/catalog.json").read_text(encoding="utf-8"))


def test_committed_site_reproduces_from_the_public_store():
    assert static_site.check(ROOT) == []


def test_site_is_static_offline_and_ready_for_pages():
    page = PAGE.read_text(encoding="utf-8")
    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    assert not re.search(r'(?:src|href)\s*=\s*["\']?(?:https?:)?//', page)
    assert (ROOT / static_site.NOJEKYLL).is_file()
    assert 'url=dashboard/' in (ROOT / static_site.REDIRECT).read_text(encoding="utf-8")


def test_site_embeds_every_release_and_experiment_from_the_store():
    page = PAGE.read_text(encoding="utf-8")
    data, figures = static_site.site_payload(ROOT)
    embedded = json.loads(sb.extract_embedded(page).decode("utf-8"))
    assert embedded == data
    sb.assert_public(embedded)
    assert set(embedded["releases"]) == {r["release_id"] for r in CATALOG["releases"]}
    assert set(embedded["experiments"]) == {e["key"] for e in CATALOG["experiments"]}
    for key, export in embedded["experiments"].items():
        assert (key in figures) == bool(export.get("conditions"))
    release = embedded["releases"][CATALOG["default_release"]]
    assert release["verification"] == json.loads((ROOT / "docs/submission/data/verification-record.json").read_text(encoding="utf-8"))


def test_site_refuses_a_tampered_store(tmp_path):
    shutil.copytree(ROOT / "public_data", tmp_path / "public_data")
    shutil.copytree(ROOT / "analysis/dashboard", tmp_path / "analysis/dashboard")
    (tmp_path / "public_data/experiments/aah-main-emergency-v2/export.json").write_text("{}", encoding="utf-8")
    with pytest.raises(static_site.SiteError):
        static_site.site_payload(tmp_path)
    with pytest.raises(static_site.SiteError):
        static_site.site_payload(tmp_path / "missing")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed; the page needs only a browser")
def test_site_renders_every_release_experiment_and_selection():
    done = subprocess.run(["node", str(Path(__file__).with_name("dashboard_render.js")), str(PAGE)],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["fails"] == [] and result["kind"] == "site"
    assert result["releases"] == len(CATALOG["releases"]) + 1  # + the "latest data" view
    data, _ = static_site.site_payload(ROOT)
    graphs = sum(1 for e in data["experiments"].values() for r in e["runs"].values() for i in r["interventions"] if i["graph_detail"])
    graphs += sum(1 for s in data["synthetic"] for r in s["runs"].values() for i in r["interventions"] if i["graph_detail"])
    assert result["graphs"] == graphs
    docs = (ROOT / "submission.md").read_text(encoding="utf-8")
    bench = result["probes"]["benchmark:submission-2026-11-27:aah-main-emergency-v2"]
    for value in ("[−6,515, +20,406]", "+5,790 토큰", "−12.5%p", "[−18.8%p, 0.0%p]"):
        assert value in bench, value
    assert "[−6,515, +20,406]" in docs and "+5,790 토큰" in docs
    # The run summary and the invalid-experiment banner live on the audit screen since the 2026-11-28 simplification.
    assert "33 / 7" in result["probes"]["audit:submission-2026-11-27:aah-main-emergency-v2"]
    assert "무효 실험" in result["probes"]["audit:submission-2026-11-27:aah-main-emergency-v1"]


# ---------------------------------------------------------------- dashboard rework (repository graphs, browser check)

def test_repository_graphs_reproduce_from_the_public_task_bases():
    """public_data/repository_graphs/ is re-extracted by the unchanged builder from benchmarks/main_public/*/base."""
    from analysis import repository_graphs
    assert repository_graphs.check(ROOT) == []
    graphs = repository_graphs.load(ROOT)
    assert sorted(graphs["tasks"]) == sorted(p.name for p in (ROOT / "benchmarks/main_public").glob("AAH-*") if (p / "task.json").is_file())
    for task_id, g in graphs["tasks"].items():
        sb.assert_public(g)
        assert g["provenance"]["origin"] == "re-extracted" and g["provenance"]["model_calls"] == 0
        ids = {n["id"] for n in g["nodes"]}
        assert all(e["source"] in ids and e["target"] in ids for e in g["edges"]), task_id
        spec = json.loads((ROOT / "benchmarks/main_public" / task_id / "task.json").read_text(encoding="utf-8"))
        assert g["allowed_change_paths"] == spec["allowed_change_paths"]
        # The builder's TEST_FUNCTION rule is shown as recorded; the dashboard never invents TESTS edges.
        assert not any(e["relation"] == "TESTS" for e in g["edges"]) or any(n["kind"] == "TEST_FUNCTION" for n in g["nodes"])


def test_site_embeds_the_repository_graphs_and_refuses_tampering(tmp_path):
    from analysis import repository_graphs
    embedded = json.loads(sb.extract_embedded(PAGE.read_text(encoding="utf-8")).decode("utf-8"))
    assert embedded["repository_graphs"] == repository_graphs.load(ROOT)
    shutil.copytree(ROOT / "public_data", tmp_path / "public_data")
    target = tmp_path / "public_data/repository_graphs/AAH-C03.json"
    target.write_text(target.read_text(encoding="utf-8").replace("ingest_batch", "ingest_other"), encoding="utf-8")
    with pytest.raises(static_site.SiteError):
        static_site.site_payload(tmp_path)


def test_submission_snapshot_keeps_its_frozen_dashboard_sources():
    """docs/submission/dashboard.html is built from the frozen copy, so the evolving Pages dashboard cannot change it."""
    frozen = ROOT / sb.FROZEN_DASHBOARD
    page = (ROOT / "docs/submission" / sb.DASHBOARD_NAME).read_text(encoding="utf-8")
    app = (frozen / "app.js").read_text(encoding="utf-8").strip()
    assert app in page and (frozen / "style.css").read_text(encoding="utf-8").strip() in page
    assert app != (ROOT / "analysis/dashboard/app.js").read_text(encoding="utf-8").strip()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed; the page needs only a browser")
def test_site_renders_repository_graphs_replay_steps_and_notices():
    done = subprocess.run(["node", str(Path(__file__).with_name("dashboard_render.js")), str(PAGE)],
                          capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["fails"] == []
    from analysis import repository_graphs
    assert result["repos"] == len(repository_graphs.load(ROOT)["tasks"]) and result["steps"] == 8
    assert result["probes"]["stamps"][0] == "2026-11-27 21:30 UTC (2026-11-28 06:30 KST)"
    assert result["probes"]["stamps"][1] == "2026-12-31 15:05 UTC (2027-01-01 00:05 KST)"
    for key in ("badHash", "badGraph", "badCase"):
        assert "주소 확인" in result["probes"][key], key
    overview = result["probes"]["overview"]
    record = json.loads((ROOT / "docs/submission/data/verification-record.json").read_text(encoding="utf-8"))
    passed = next(s for s in record["steps"] if s["id"] == "tests")["counts"]["passed"]
    data, _ = static_site.site_payload(ROOT)
    runs = sum(len(e["runs"]) for e in data["experiments"].values()) + sum(len(s["runs"]) for s in data["synthetic"])
    for value in (f">{passed:,}<", "시스템 동작 방식", "핵심 기능 탐색", "연구 결과", "Harness Engineering", "Loop Engineering"):
        assert value in overview, value
    assert f">{runs}<" in result["probes"]["audit:submission-2026-11-27:aah-main-emergency-v2"]


def _chrome() -> str | None:
    for candidate in (os.environ.get("CHROME"), "C:/Program Files/Google/Chrome/Application/chrome.exe",
                      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium",
                      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


@pytest.mark.skipif(shutil.which("node") is None or _chrome() is None, reason="needs Node.js and a local Chrome/Edge")
def test_site_in_a_real_browser(tmp_path):
    """Desktop/mobile × light/dark rendering: no overflow, overlapping text or CSP/network/console errors; navigation,
    replay playback, graph interaction and address handling work (Chrome DevTools Protocol, no packages)."""
    shots = os.environ.get("AAH_SCREENSHOT_DIR", "")
    done = subprocess.run(["node", str(Path(__file__).with_name("dashboard_browser.js")), str(PAGE), shots],
                          capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert "skipped" not in result
    assert result["fails"] == [], result["fails"][:10]
    assert result["checks"] > 500
