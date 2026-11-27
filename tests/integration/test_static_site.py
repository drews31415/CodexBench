"""Static GitHub Pages dashboard built from the versioned public store (no server, no results/ needed)."""

from __future__ import annotations

import json
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
    assert "33 / 7" in result["probes"]["overview"]
    assert "무효 실험" in result["probes"]["overview:submission-2026-11-27:aah-main-emergency-v1"]
