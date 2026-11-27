"""EVALUATION_PROTOCOL §12/§14 fixtures for the Main analysis pipeline (hypothetical numbers, not results)."""

from pathlib import Path

import pytest

from analysis import main_analysis as m


SECTION_12 = {  # task: {condition: (success, tokens)}
    "q1": {"B": (1, 100), "C": (1, 80), "D": (1, 110), "E": (1, 90)},
    "q2": {"B": (0, 200), "C": (0, 140), "D": (1, 150), "E": (1, 120)},
    "q3": {"B": (1, 150), "C": (1, 100), "D": (0, 180), "E": (1, 110)},
    "q4": {"B": (0, 250), "C": (1, 180), "D": (1, 170), "E": (1, 130)},
}


def rows_from(table, *, families=None, missing=()):
    rows = []
    for task, cells in table.items():
        for condition, (y, tokens) in cells.items():
            gone = (task, condition) in missing
            rows.append({"task_id": task, "task_family_id": (families or {}).get(task, task), "condition": condition,
                         "repetition": 1, "selected_run_id": None if gone else f"{task}-{condition}",
                         "y": None if gone else y, "model_tokens": tokens, "runtime_ms": 10, "evaluator_ms": 1})
    return rows


def by_id(contrasts):
    return {c["contrast_id"]: c for c in contrasts}


def test_ep17_ep23_section_12_values():
    rows = rows_from(SECTION_12)
    metrics = m.condition_metrics(rows)
    assert [metrics[c]["tsr_task_mean"] for c in "BCDE"] == [0.5, 0.75, 0.75, 1.0]
    assert metrics["B"]["cps_tokens"] == 350 and metrics["E"]["cps_tokens"] == 112.5
    assert metrics["C"]["cps_tokens"] == pytest.approx(500 / 3)
    contrasts = by_id(m.compute_contrasts(rows))
    assert contrasts["CTX_MAIN"]["effect"] == 0.25 and contrasts["GRAPH_MAIN"]["effect"] == 0.25
    assert contrasts["INTERACTION"]["effect"] == 0 and contrasts["P-R3"]["effect"] == 0.25
    assert contrasts["P-R1"]["effect"] == pytest.approx(500 / 3 - 350)
    assert contrasts["P-R3"]["interpretation_status"] == "EXPLORATORY_NO_INTERVAL"


def test_ep16_ep19_missing_usage_keeps_success_but_leaves_cost_incomplete():
    table = {t: dict(c) for t, c in SECTION_12.items()}
    table["q2"]["C"] = (0, None)
    rows = rows_from(table)
    metrics = m.condition_metrics(rows)
    assert metrics["C"]["tsr_task_mean"] == 0.75
    assert metrics["C"]["cps_tokens"] is None and metrics["C"]["cps_reason"] == "INCOMPLETE_COVERAGE"
    p_r1 = by_id(m.compute_contrasts(rows))["P-R1"]
    assert p_r1["n_tasks"] == 3 and p_r1["excluded_tasks"] == {"q2": "C:COST"}


def test_ep19_missing_condition_removes_the_block_from_paired_sets():
    rows = rows_from(SECTION_12, missing={("q3", "D")})
    contrasts = by_id(m.compute_contrasts(rows))
    assert contrasts["E-B"]["n_tasks"] == 4 and contrasts["E-D"]["n_tasks"] == 3
    assert contrasts["INTERACTION"]["excluded_tasks"] == {"q3": "D:MISSING"}
    # D: 3 successes, 1 missing of 4 planned -> conservative planned-denominator range 3/4..4/4 (§11.2).
    assert m.condition_metrics(rows)["D"]["planned_bounds"] == [0.75, 1.0]


def test_ep18_ep25_zero_success_has_no_finite_cps_and_withholds_intervals():
    table = {t: {**c, "B": (0, c["B"][1])} for t, c in SECTION_12.items()}
    rows = rows_from(table, families={"q1": "f1", "q2": "f1", "q3": "f2", "q4": "f2"})
    assert m.condition_metrics(rows)["B"]["cps_reason"] == "NO_SUCCESS"
    assert by_id(m.compute_contrasts(rows))["P-R1"]["interpretation_status"] == "UNDEFINED_POINT_NO_SUCCESS"
    # B succeeds only in family f1: some bootstrap draws contain no B success.
    table["q1"]["B"] = (1, 100)
    p_r1 = by_id(m.compute_contrasts(rows_from(table, families={"q1": "f1", "q2": "f1", "q3": "f2", "q4": "f2"})))["P-R1"]
    assert p_r1["undefined_replicates"] > 0 and p_r1["ci_lower"] is None
    assert p_r1["interpretation_status"] == "UNDEFINED_REPLICATES_INTERVAL_WITHHELD"


def test_ep24_bootstrap_keeps_families_together_and_is_seeded():
    rows = rows_from(SECTION_12, families={"q1": "f1", "q2": "f1", "q3": "f2", "q4": "f2"})
    blocks, _ = m.task_blocks(rows, ("B", "E"))
    sizes = []

    def statistic(sample):
        sizes.append(len(sample))
        return m._p(sample, "E") - m._p(sample, "B")

    config = {**m.ANALYSIS_CONFIG, "bootstrap": {**m.ANALYSIS_CONFIG["bootstrap"], "iterations": 200}}
    first = m.cluster_bootstrap(blocks, statistic, config)
    assert set(sizes) == {4} and m.cluster_bootstrap(blocks, statistic, config) == first


def test_ep12_unrun_slots_stay_in_the_denominator():
    assert m.resolve_disposition(None, None, None, "PROJECT_BUDGET_EXHAUSTED") == (
        "NOT_RUN_BUDGET", "PROJECT_BUDGET_EXHAUSTED", None)
    assert m.resolve_disposition(None, None, None)[0] == "NOT_RUN_OTHER"
    assert m.resolve_disposition(object(), None, None) == ("PENDING_REVIEW", "NO_FINAL_REPORT", None)


PILOT = Path(__file__).resolve().parents[2] / "results/s5-live/s5-dev-pilot-20261126"


@pytest.mark.skipif(not PILOT.exists(), reason="results/ is local and access restricted")
def test_ep30_raw_pilot_logs_reaggregate_to_the_logged_totals(tmp_path):
    first = m.analyze(PILOT)
    assert first["dispositions"] == {"VALID_FAILURE": 7, "VALID_SUCCESS": 18}
    assert sum(v["model_tokens_total"] for v in first["conditions"].values()) == 2_298_144
    assert sum(v["runtime_ms_total"] + v["evaluator_ms_total"] for v in first["conditions"].values()) == 976_602
    m.write(first, tmp_path / "out")
    assert m.verify(PILOT, tmp_path / "out")
