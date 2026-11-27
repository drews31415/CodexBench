"""DCC validity, policy boundaries, exact packing and workflow integration."""

import json
from dataclasses import replace

import pytest

from aah.context import dcc
from aah.context.candidates import Candidate, CandidatePool
from aah.context.default import ContextOverCap, compile_context, estimate
from aah.diagnosis.detector import Detection, Signal
from aah.domain import Condition, ContractError, SnapshotRef, TriggerId, resolve_condition
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run
from benchmarks.smoke.public_checks import run_approved
from benchmarks.smoke.s1 import CONFIG, SyntheticAdapter, replay
from evaluation.smoke import fixture_request


SNAPSHOT = SnapshotRef("DEV-01", "a" * 64, "base", "snapshots/state.json", "2026-11-26T00:00:00Z", True)
PREVIOUS = "b" * 64
DETECTION = Detection((Signal(TriggerId.T4, ("public-check-result",), ("fail",)),), ())
CORE = Candidate("failure_evidence", "public-checks/receipt", "tab-edges failed", "approved_check",
                 SNAPSHOT.snapshot_id, "v1", ("module.py",), ("public-check-result",))


def request(condition):
    return replace(fixture_request("DEV-01"), policies=resolve_condition(condition))


def guidance(source, text, **options):
    return replace(Candidate("optional_guidance", source, text, "approved_guidance", SNAPSHOT.snapshot_id, "v1"),
                   **options)


def decisions(message):
    return {row["candidate_id"]: row for row in message.decisions}


def test_validity_and_public_provenance_apply_to_the_same_pool():
    current = guidance("notes/current.md", "format_label follows the public fixture", scope=("module.py",))
    historic = guidance("notes/prior.md", "earlier input had a trailing tab", snapshot_id=PREVIOUS)
    uncertain = guidance("notes/other.md", "check labels in deployment logs", snapshot_id=None)
    retired = guidance("notes/retired.md", "obsolete trim rules", retired=True)
    graph = replace(current, kind="relation_evidence", path_refs=("graph-id",))
    private = replace(current, source="evaluation/hidden.md", text="PRIVATE_BODY")
    injected = guidance("notes/unverified.md", "UNVERIFIED_BODY", provenance="agent_text")
    pool = CandidatePool((), (current, historic, uncertain, retired, private, graph, injected), ("module.py",))
    default = compile_context(request(Condition.B), SNAPSHOT, pool, cap=2048)
    dynamic = compile_context(request(Condition.C), SNAPSHOT, pool, cap=2048)
    b, c = decisions(default), decisions(dynamic)
    assert b.keys() == c.keys()
    assert b[retired.candidate_id]["selected"] and c[retired.candidate_id]["reason_code"] == "INVALID"
    assert c[current.candidate_id]["components"]["scope"] == 1.0
    assert c[historic.candidate_id]["components"]["validity"] == 1.0
    assert c[uncertain.candidate_id]["components"]["validity"] == 0.5
    assert c[historic.candidate_id]["selected"] and '"HISTORICAL"' in dynamic.supplement
    assert all("score" not in row for row in b.values())
    for item, reason in ((private, "SECURITY_REJECTED"), (graph, "SECURITY_REJECTED"),
                         (injected, "UNSUPPORTED_PROVENANCE")):
        assert b[item.candidate_id]["reason_code"] == c[item.candidate_id]["reason_code"] == reason
    assert "PRIVATE_BODY" not in dynamic.text and "UNVERIFIED_BODY" not in default.text
    assert "evaluation/hidden.md" not in json.dumps(dynamic.receipt())


def test_dedup_preserves_provenance_and_version_scope():
    first = guidance("docs/a.md", "format_label tab 42", evidence_ids=("e-one",))
    copy = guidance("docs/b.md", first.text, evidence_ids=("e-two",))
    later = guidance("docs/c.md", first.text, revision="v2")
    literal = guidance("docs/d.md", "format_label tab 43")
    b = compile_context(request(Condition.B), SNAPSHOT, CandidatePool((), (first, copy, later, literal)), cap=4096)
    c = compile_context(request(Condition.C), SNAPSHOT, CandidatePool((), (first, copy, later, literal)), cap=4096)
    reverse = compile_context(request(Condition.C), SNAPSHOT,
                              CandidatePool((), (literal, later, copy, first)), cap=4096)
    assert c.text == reverse.text and c.receipt() == reverse.receipt()
    assert len(b.selected_ids) == 4
    representative, duplicate = sorted((first.candidate_id, copy.candidate_id))
    rows = decisions(c)
    assert rows[duplicate]["duplicate_of"] == representative
    assert {entry["source"] for entry in rows[representative]["merged_origins"]} == {"docs/a.md", "docs/b.md"}
    assert rows[later.candidate_id]["reason_code"] != "MERGED_DUPLICATE"
    assert rows[literal.candidate_id]["reason_code"] != "MERGED_DUPLICATE"
    assert {"e-one", "e-two"} <= set(c.anchor_ids)


def test_candidate_id_breaks_ties_and_near_duplication_has_a_cost():
    one = guidance("notes/a.md", "abc")
    two = guidance("notes/b.md", "xyz")
    duplicate_words = guidance("notes/c.md", "abc!", revision="v2")
    items = (two, one)
    picked = compile_context(request(Condition.C), SNAPSHOT, CandidatePool((), items), cap=2048)
    assert picked.selected_ids == tuple(sorted(item.candidate_id for item in items))

    picked = compile_context(request(Condition.C), SNAPSHOT,
                             CandidatePool((), (one, duplicate_words)), cap=2048)
    rejected = next(row for row in picked.decisions if not row["selected"])
    assert rejected["reason_code"] == "NON_POSITIVE_UTILITY"
    assert rejected["utility_density"] == 0


def test_exact_overhead_including_strategy_and_fixed_input():
    large = Candidate("code_excerpt", "module.py", "def f():\n    return " + "1234" * 1000,
                      "public_snapshot", SNAPSHOT.snapshot_id, "revision", ("module.py",), span=(1, 2))
    pool = CandidatePool((CORE,), (large,), ("module.py",), ("public-check-result",))
    available = compile_context(request(Condition.C), SNAPSHOT, pool, detection=DETECTION, cap=400)
    receipt = available.receipt()
    minimum = receipt["estimated_supplement_tokens"]
    assert receipt["core_ids"] == (CORE.candidate_id,)
    assert decisions(available)[large.candidate_id]["reason_code"] == "OVER_CAP"
    assert "GENERIC_RETRY" in available.text and "DEV-01-P-FUNC" in available.text
    assert receipt["estimated_full_tokens"] == estimate(available.text)
    assert minimum == estimate("\n\n" + available.supplement)
    assert compile_context(request(Condition.C), SNAPSHOT, pool, detection=DETECTION, cap=minimum).text == available.text
    for condition in (Condition.B, Condition.C):
        with pytest.raises(ContextOverCap) as raised:
            compile_context(request(condition), SNAPSHOT, pool, detection=DETECTION, cap=minimum - 1)
        assert raised.value.estimated_core_tokens == minimum


def test_graph_and_dcc_are_excluded_from_initial_policy_controls(monkeypatch):
    legit = guidance("notes/allowed.md", "format_label")
    graph = replace(legit, kind="relation_evidence", provenance="graph_public", path_refs=("graph-edge",))
    pool = CandidatePool((), (legit, graph))
    first = compile_context(request(Condition.C), SNAPSHOT, pool, cap=2048)
    assert first.text == compile_context(request(Condition.E), SNAPSHOT, pool, cap=2048).text
    assert decisions(first)[graph.candidate_id]["reason_code"] == "SECURITY_REJECTED"
    false_core = replace(CORE, provenance="graph_public", path_refs=("graph-edge",))
    with pytest.raises(ContractError):
        compile_context(request(Condition.C), SNAPSHOT, CandidatePool((false_core,), ()),
                        cap=2048, detection=DETECTION)

    def forbidden(*args, **kwargs):
        pytest.fail("DCC called for a condition with DCC disabled")

    monkeypatch.setattr(dcc, "select", forbidden)
    b = compile_context(request(Condition.B), SNAPSHOT, pool, cap=2048)
    d = compile_context(request(Condition.D), SNAPSHOT, pool, cap=2048)
    a = compile_context(request(Condition.A), SNAPSHOT, CandidatePool((), ()), cap=0)
    assert b.text == d.text and b.text.startswith(a.text + "\n\n")
    assert a.supplement == "" and a.fixed_hash == b.fixed_hash == first.fixed_hash


def test_condition_c_uses_dcc_in_both_turns(tmp_path):
    public, requests = replay(Condition.C, tmp_path)
    assert public.condition == "C" and public.public_check_status == "PASS"
    assert [item.kind for item in requests] == ["INITIAL", "RECOVERY"]
    directory = tmp_path / public.run_id
    trace = [json.loads(line) for line in (directory / "context.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["stage"] for row in trace] == ["INITIAL", "RECOVERY"]
    assert all(row["status"] == "OK" and row["policy"] == "DCC" and row["estimator_id"] for row in trace)
    assert all(row["estimated_supplement_tokens"] <= row["cap_tokens"] for row in trace)
    assert any(row["span"] == [1, 3] and row["source"] == "module.py" for row in trace[0]["decisions"])
    assert trace[0]["core_ids"] == [] and trace[1]["core_ids"]
    assert all(turn.message.startswith(request(Condition.C).fixed.task + "\n\n") for turn in requests)
    assert "GENERIC_RETRY" in requests[1].message


def test_condition_c_stops_before_recovery_reservation_when_core_exceeds_cap(tmp_path):
    case = request(Condition.C)
    output = tmp_path / "results"
    runtime = SyntheticAdapter(output)
    report = run(case, runtime=runtime, config=CONFIG, public_root=case.repository.source_path,
                 work_root=tmp_path / "work", results_root=output, limits=Limits(30_000, 4, 2, 100, 5_000),
                 check=run_approved, supplement_cap=16)
    trace = [json.loads(line) for line in (output / case.run_id / "context.jsonl").read_bytes().splitlines()]
    assert report.condition == "C" and report.stop_reason == "NO_SAFE_CONTINUATION"
    assert report.recoveries_reserved == 0 and len(runtime.requests) == 1
    assert trace[-1]["status"] == "CORE_OVER_CAP" and trace[-1]["estimated_core_tokens"] > 16
