"""Submission bundle, figures and dashboard (model-free).

The committed public files must verify on their own (no results/ needed); a fresh build from the raw
records must reproduce them byte for byte when results/ is present; restricted payloads never leak.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from analysis import submission_bundle as sb
from analysis import submission_figures as sf

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "docs/submission"
RAW = ROOT / "results/main-live/aah-main-emergency-v2"
SECRET = "SECRET-PAYLOAD-7f3a"


def _bundle() -> dict:
    return json.loads((PUBLIC / sb.BUNDLE_NAME).read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, list):
        path.write_text("".join(json.dumps(v) + "\n" for v in value), encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")


def _synthetic_run(root: Path, *, full: bool = True) -> str:
    run = "results/x/runs/r1"
    _write(root / run / "manifest.json", {"run_id": "r1", "task_id": "T", "condition": "B",
                                          "policy": {"initial_context": "DEFAULT"}})
    _write(root / run / "report.json", {"run_id": "r1", "condition": "B", "turns_used": 1, "stop_reason": "COMPLETED",
                                        "frozen_patch": {"changed_paths": ["a.py"], "patch_hash": "h"},
                                        "usage_summary": {"observed_model_tokens": 10}})
    if full:
        _write(root / run / "evidence.jsonl", [
            {"kind": "COMMAND_RESULT", "sequence": 1, "turn_id": "turn-0001",
             "payload": {"command_id": "item_1", "exit_code": 0, "argv": [SECRET], "aggregated_output": SECRET,
                         "stdout_ref": "restricted/" + SECRET}},
            {"kind": "UNKNOWN", "sequence": 2, "turn_id": "turn-0001",
             "payload": {"provider_type": "item.completed", "text": SECRET, "raw_ref": SECRET}}])
        _write(root / run / "checks.jsonl", [{"turn_id": "turn-0001", "check_id": "C", "status": "FAIL",
                                              "test_cases": [{"test_id": "C", "status": "FAIL", "message": SECRET,
                                                              "frame": {"path": SECRET}}]}])
        _write(root / run / "turn-0001-dispatch.json", {"turn_id": "turn-0001", "kind": "INITIAL", "message_hash": "m",
                                                        "session_ref": SECRET})
        _write(root / run / "restricted/turn-0001-raw-00001.json", {"type": "item.completed",
                                                                     "item": {"text": SECRET}})
    return run


# ---------------------------------------------------------------- privacy and robustness (synthetic fixtures)

def test_extract_run_copies_whitelisted_fields_only(tmp_path):
    run_rel = _synthetic_run(tmp_path)
    reader = sb.Reader(tmp_path)
    run = sb.extract_run(reader, run_rel)
    text = json.dumps(run)
    assert SECRET not in text
    assert run["events"][0]["exit_code"] == 0 and run["first_turn_public"] == "FAIL"
    assert run["checks"][0]["cases"][0]["frame_present"] is True
    assert not any("restricted" in path for path in reader.inputs)
    sb.assert_public(run)


def test_reader_refuses_restricted_records(tmp_path):
    run_rel = _synthetic_run(tmp_path)
    with pytest.raises(sb.BundleError):
        sb.Reader(tmp_path).bytes(f"{run_rel}/restricted/turn-0001-raw-00001.json")


def test_missing_run_records_degrade_to_empty_values(tmp_path):
    run = sb.extract_run(sb.Reader(tmp_path), _synthetic_run(tmp_path, full=False))
    assert run["events"] == [] and run["checks"] == [] and run["context"] == [] and run["interventions"] == []
    assert run["first_turn_public"] is None and run["operator"] is None and run["verdict"] is None


@pytest.mark.parametrize("value", [{"command": "x"}, {"a": [{"aggregated_output": "x"}]}, {"p": "C:\\Users\\someone\\x"},
                                   {"p": "/home/someone/x"}, {"who": "reviewer@example.org"}])
def test_assert_public_rejects_restricted_fields_paths_and_addresses(value):
    with pytest.raises(sb.BundleError):
        sb.assert_public(value)


# ---------------------------------------------------------------- committed public files (no results/ needed)

def test_committed_public_files_recompute_the_frozen_analysis():
    assert sb.verify_public() == []


def test_bundle_numbers_agree_with_frozen_csv_outputs():
    bundle = _bundle()
    for experiment in bundle["experiments"].values():
        rows = sb._typed_status_rows((PUBLIC / experiment["public_dir"] / "run_status.csv").read_bytes())
        for condition, metrics in experiment["conditions"].items():
            cell = [r for r in rows if r["condition"] == condition]
            assert metrics["successes"] == sum(r["y"] for r in cell if r["y"] is not None)
            assert metrics["model_tokens_total"] == sum(r["model_tokens"] for r in cell)
        assert experiment["dispositions"]["VALID_SUCCESS"] == sum(r["y"] == 1 for r in rows)
        runs = experiment["runs"]
        assert {r["selected_run_id"] for r in rows} == set(runs)
        derived = experiment["derived"]
        assert derived["parity"]["B_equals_D"] == derived["parity"]["C_equals_E"] == derived["parity"]["tasks"]
        assert derived["commands"]["total"] == sum(e["kind"] == "COMMAND_RESULT" for r in runs.values() for e in r["events"])
        assert derived["empty_patch_failures"] == sum(
            1 for r in rows if r["y"] == 0 and not runs[r["selected_run_id"]]["report"]["changed_paths"])


def test_figures_print_the_bundle_values():
    bundle = _bundle()
    figures = sf.render_all(bundle)
    for name, svg in figures.items():
        assert svg == (PUBLIC / "figures" / name).read_text(encoding="utf-8")
    v2 = bundle["experiments"]["aah-main-emergency-v2"]
    success = figures["v2-success-rate.svg"]
    for condition, metrics in v2["conditions"].items():
        assert f"{metrics['tsr_task_mean'] * 100:.1f}%" in success
        assert f"{metrics['successes']}/{metrics['valid']} 성공" in success
    effects = figures["v2-paired-effects.svg"]
    for contrast in v2["contrasts"]:
        if contrast["unit"] == "pp":
            assert sf._pp(contrast["effect"]) in effects


def test_dashboard_is_offline_and_carries_the_bundle_hash():
    page = (PUBLIC / sb.DASHBOARD_NAME).read_text(encoding="utf-8")
    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    assert not re.search(r'(?:src|href)\s*=\s*["\']?(?:https?:)?//', page)
    digest = sb.sha256((PUBLIC / sb.BUNDLE_NAME).read_bytes())
    assert f'name="aah-bundle-sha256" content="{digest}"' in page
    assert sb.extract_embedded(page) == (PUBLIC / sb.BUNDLE_NAME).read_bytes()


def test_verification_record_is_public():
    path = PUBLIC / sb.RECORD_NAME
    if not path.is_file():
        pytest.skip("verification record not generated yet (python -m analysis.submission_bundle record-verification)")
    record = json.loads(path.read_text(encoding="utf-8"))
    sb.assert_public(record)
    assert record["model_calls"] == 0 and {s["id"] for s in record["steps"]} == {s for s, _ in sb.RECORDED_COMMANDS}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed; the dashboard needs only a browser")
def test_dashboard_renders_every_view_without_errors():
    done = subprocess.run(["node", str(Path(__file__).with_name("dashboard_render.js")), str(PUBLIC / sb.DASHBOARD_NAME)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["fails"] == []
    bundle = _bundle()
    graph_runs = sum(1 for e in bundle["experiments"].values() for r in e["runs"].values()
                     for i in r["interventions"] if i["graph_detail"])
    graph_runs += sum(1 for r in bundle["synthetic"]["runs"].values() for i in r["interventions"] if i["graph_detail"])
    assert result["graphs"] == graph_runs
    v2 = bundle["experiments"]["aah-main-emergency-v2"]
    ok = sum(s["y"] == 1 for s in v2["slots"])
    assert f"{ok} / {len(v2['slots']) - ok}" in result["probes"]["overview"]
    assert "데이터 묶음 없음" in result["probes"]["empty"]


def _signed(value: float) -> str:
    return f"{value:+,.0f}".replace("-", "−")


def test_submission_documents_quote_the_bundle_numbers():
    """SUB14: the headline numbers in the submission documents are the ones in the frozen outputs."""
    bundle = _bundle()
    v2 = bundle["experiments"]["aah-main-emergency-v2"]
    v1 = bundle["experiments"]["aah-main-emergency-v1"]
    contrasts = {c["contrast_id"]: c for c in v2["contrasts"]}
    pr1 = contrasts["P-R1"]
    recovered = sum(r["verdict"] == "PASS" for r in v2["derived"]["recoveries"])
    required = [f"{sum(s['model_tokens'] for s in v2['slots']):,}",
                f"{_signed(pr1['effect'])} 토큰", f"[{_signed(pr1['ci_lower'])}, {_signed(pr1['ci_upper'])}]",
                sf._pp(contrasts["P-R3"]["effect"]),
                f"{v1['derived']['commands']['exit_nonzero']}/{v1['derived']['commands']['total']}",
                f"{recovered}건이 독립 판정 PASS"]
    for condition, m in v2["conditions"].items():
        required += [f"{m['successes']}/{m['valid']}", f"{m['tsr_task_mean'] * 100:.1f}%",
                     f"{m['model_tokens_total']:,}", f"{round(m['cps_tokens']):,}"]
    record_path = PUBLIC / sb.RECORD_NAME
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else None
    for doc in (ROOT / "submission.md", PUBLIC / "EVIDENCE_PACK.md"):
        text = doc.read_text(encoding="utf-8")
        missing = [value for value in required if value not in text]
        assert not missing, f"{doc.name} lacks {missing}"
        if record is not None:
            tests = next(s for s in record["steps"] if s["id"] == "tests")["counts"]
            assert "[[" not in text, f"{doc.name} still has placeholders"
            assert f"{tests['passed']}개 통과" in text, f"{doc.name} test count differs from the verification record"


# ---------------------------------------------------------------- reproduction from raw records

@pytest.mark.skipif(not RAW.exists(), reason="results/ is local and access restricted")
def test_committed_outputs_reproduce_from_raw_records():
    assert sb.check() == []
