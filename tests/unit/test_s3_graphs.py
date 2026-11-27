"""Snapshot, attribution and bounded traversal contracts for IP15–IP18."""

from dataclasses import replace
from uuid import uuid4

from aah.diagnosis.detector import Detection, Signal, detect
from aah.diagnosis.graph import Limits, diagnose
from aah.domain import (CheckResult, CheckStatus, EvidenceRecord, RawEvent, SnapshotRef, TestCaseResult as Case,
                        TriggerId, TurnResult, UnitStatus, canonical_json_bytes, sha256_bytes)
from aah.evidence.ledger import EvidenceLedger, EvidenceView
from aah.graphs import execution, mapping, repository
from aah.graphs.artifact import Builder, Node
from aah.recovery.controller import select
from aah.reporting.store import RunStore
from aah.runtime.observer import Observer
from aah.workspace.patch import digest_ref, store_bytes
from aah.workspace.snapshot import File, manifest


RUN = str(uuid4())
NOW = "2026-11-26T00:00:00Z"


def snapshot(root, files):
    entries = {path: File(data.encode(), "100644") for path, data in files.items()}
    for file in entries.values():
        store_bytes(root, digest_ref("blobs", file.digest), file.data)
    data = canonical_json_bytes(manifest("fixture", "base", entries, entries, set()))
    digest = sha256_bytes(data)
    ref = digest_ref("snapshots", digest)
    store_bytes(root, ref, data)
    return SnapshotRef("fixture", digest, "base", ref, NOW, True)


def event(key, kind, version, payload, *, order=None, sequence=1):
    return EvidenceRecord(key, RUN, "t", sequence, kind, (), None, "observed", version, None, order, payload, ())


def receipt(ref, turn, version, *, frame=None, case_id="opaque", status=CheckStatus.FAIL):
    fail = status == CheckStatus.FAIL
    case = Case(case_id, status, "Error" if fail else None, "message" if fail else None, frame if fail else None,
                "d"*64 if fail else None)
    return CheckResult(ref, "check", "e"*64, turn, version, status, "TRUSTED_RUNNER",
                       int(fail), 1, "c"*64, "parser", (case,), None, None, ())


def test_repository_ast_resolution_version_identity_and_unknowns(tmp_path):
    files = {"pkg/base.py": "class Base: pass\ndef work(): return 1\n",
             "pkg/impl.py": "from .base import Base, work as run\nclass Impl(Base):\n    async def task(self):\n        return run()\n",
             "test_impl.py": "from pkg.base import work\ndef test_work():\n    return work()\n"
                             "def indirect(work):\n    return work()\n",
             "broken.py": "def (\n", "evaluation/hidden.py": "def hidden(): pass\n"}
    first = snapshot(tmp_path, files)
    graph = repository.build(tmp_path, first, RUN)
    assert graph.graph_id == repository.build(tmp_path, first, RUN).graph_id
    assert graph.status == "partial" and "PARSE_ERROR:broken.py" in graph.warnings
    assert all(not n.attributes["path"].startswith("evaluation") for n in graph.nodes)
    symbols = {n.attributes.get("qualified_name"): n for n in graph.nodes if n.kind != "FILE"}
    assert symbols["Impl.task"].attributes["is_async"] and symbols["test_work"].kind == "TEST_FUNCTION"
    assert symbols["test_work"].attributes["test_id"] == "test_impl.py::test_work"
    ends = {(e.source_id, e.relation, e.target_id) for e in graph.edges}
    for src, rel, tgt in [("Impl", "INHERITS", "Base"), ("Impl.task", "CALLS", "work"), ("test_work", "TESTS", "work")]:
        assert (symbols[src].node_id, rel, symbols[tgt].node_id) in ends
    assert not any(a == symbols["indirect"].node_id and r == "CALLS" for a, r, b in ends)
    old_ids = {n.node_id for n in graph.nodes}
    files["moved.py"] = files.pop("pkg/base.py")
    new = repository.build(tmp_path, snapshot(tmp_path, files), RUN)
    assert old_ids.isdisjoint(n.node_id for n in new.nodes)
    assert not any(e.relation == "INHERITS" for e in new.edges)


def test_consecutive_failures_and_check_attribution(tmp_path):
    store = RunStore(tmp_path / RUN, RUN)
    ledger = EvidenceLedger(store)
    obs = Observer(store, ledger)
    for i, status in enumerate(("FAIL", "FAIL", "FAIL", "PASS", "FAIL")):
        check = receipt(f"r{i}", f"t{i}", "b"*64, status=CheckStatus(status))
        obs.collect_check(check, f"t{i}")
        obs.collect_check(check, f"t{i}")
    view = ledger.view()
    graph = execution.build(view, RUN, "b"*64, ("check",))
    nodes = {n.node_id: n for n in graph.nodes}
    same = {(nodes[e.source_id].attributes["check_result_id"], nodes[e.target_id].attributes["check_result_id"])
            for e in graph.edges if e.relation == "SAME_FAILURE_AS"}
    assert same == {("r1", "r0"), ("r2", "r1")}
    assert len([e for e in graph.edges if e.relation == "RETURNS"]) == 4
    assert not any(e.relation == "NEXT" for e in graph.edges)
    assert not execution.build(view, RUN, "b"*64, ("another_check",)).nodes


def test_versioned_mapping_change_hunks_reads_command_and_frames(tmp_path):
    old = snapshot(tmp_path, {"m.py": "def old():\n    return 1\n"})
    new = snapshot(tmp_path, {"m.py": "\ndef current():\n    return 2\n"})
    repositories = tuple(repository.build(tmp_path, s, RUN) for s in (old, new))
    payload = {"command_id": "command", "argv": ("echo", "m.py"), "cwd": ".", "exit_code": 0, "stdout_ref": None,
               "stderr_ref": None, "status": "completed"}
    read = {"path": "m.py", "access_kind": "read", "access_basis": "direct_read", "span": None}
    records = [event("cmd", "COMMAND", None, payload, order=0),
               event("result", "COMMAND_RESULT", None, payload, order=1),
               event("unknown", "FILE_ACCESS", None, read), event("read", "FILE_ACCESS", new.snapshot_id, read),
               event("change", "CHANGE", new.snapshot_id, {"diff_ref": "patches/u.json", "path": "m.py",
                     "before_snapshot_id": old.snapshot_id, "after_snapshot_id": new.snapshot_id,
                     "hunks": ({"path": "m.py", "old_start": 1, "old_count": 2, "new_start": 2, "new_count": 2},)})]
    store = RunStore(tmp_path / RUN, RUN)
    ledger = EvidenceLedger(store)
    ledger.append_batch(records)
    obs = Observer(store, ledger)
    obs.collect_check(receipt("bound", "t", new.snapshot_id, frame={"path": "m.py", "line": 3}), "t")
    obs.collect_check(receipt("outside", "t", new.snapshot_id, frame={"path": "m.py", "line": 4}), "t")
    graph = mapping.link(repositories, execution.build(ledger.view(), RUN, new.snapshot_id, ("check",)))
    nodes = {n.node_id: n for n in graph.nodes}
    edges = [(nodes[e.source_id], e, nodes[e.target_id]) for e in graph.edges]
    assert {(s.attributes["evidence_id"], t.kind) for s,e,t in edges if e.relation == "READS"} == {("read","FILE")}
    assert {(t.attributes["qualified_name"], e.attributes["role"]) for s,e,t in edges
            if e.relation == "CHANGES" and t.kind == "FUNCTION"} == {("old","before"), ("current","after")}
    assert {(s.attributes["check_result_id"], t.kind) for s,e,t in edges if e.relation == "FAILS_AT"} == {
        ("bound","FUNCTION"), ("bound","FILE")}
    assert {(s.kind, t.kind) for s,e,t in edges if e.relation == "NEXT"} == {("COMMAND", "COMMAND_RESULT")}
    assert {s.kind for s,e,t in edges if e.relation == "RETURNS"} == {"COMMAND", "TEST"}


def test_only_historical_localization_refreshes_evidence(tmp_path):
    old = snapshot(tmp_path, {"gone.py": "def f(): return 1\n"})
    new = snapshot(tmp_path, {"moved.py": "def f(): return 1\n"})
    store = RunStore(tmp_path / RUN, RUN)
    ledger = EvidenceLedger(store)
    obs = Observer(store, ledger)
    for turn, version in (("past", old), ("now", new)):
        obs.collect_check(receipt(turn, turn, version.snapshot_id, frame={"path": "gone.py", "line": 1}), turn)
    detection = detect(ledger.view(), new, "now", ("check",))
    graph = mapping.link(tuple(repository.build(tmp_path, s, RUN) for s in (old, new)),
                         execution.build(ledger.view(), RUN, new.snapshot_id, ("check",)))
    result = diagnose(graph, detection)
    assert not result.candidates and {c["snapshot_id"] for c in result.historical} == {old.snapshot_id}
    strategy = select(detection, result, ("check",))
    assert strategy.strategy_id == "REFRESH_EVIDENCE" and strategy.support.validity(new.snapshot_id) == "HISTORICAL"
    assert strategy.targets == ("gone.py",)


def test_depth_specific_routes_limits_and_forbidden_relations():
    graph = Builder()
    for key, kind in (("fail","FAILURE"), ("test","TEST"), ("change","CHANGE"),
                      ("site","TEST_FUNCTION"), ("hub","FUNCTION"), ("mid","FUNCTION"), ("target","FUNCTION"),
                      ("suspect","FUNCTION")):
        repo = kind in {"FUNCTION","TEST_FUNCTION"}
        attrs = {"path": key + ".py", "turn_id": "t", "sequence": 1, "check_result_id": "r", "evidence_id": key}
        graph.nodes[key] = Node(key, kind, "repo" if repo else "exec", "v", "observed", (key,), attrs)
    for a, b, rel in [("test","fail","RETURNS"), ("test","site","TESTS_TARGET"), ("site","hub","TESTS"),
                      ("change","fail","OBSERVED_AFTER"), ("change","hub","CHANGES"), ("hub","mid","CALLS"),
                      ("mid","target","CALLS"), ("fail","suspect","SUSPECTS"), ("fail","suspect","NEXT")]:
        status = "inferred" if rel in {"OBSERVED_AFTER", "CHANGES"} else "observed"
        graph.edge(a, b, rel, (a, b), status=status)
    artifact = graph.finish("coupled", RUN, "v", 4)
    trigger = Detection((Signal(TriggerId.T4, ("r",), ()),), ())
    result = diagnose(artifact, trigger)
    scores = {c["candidate_id"]: c for c in result.candidates}
    assert scores["hub"]["score_components"]["distance"] == 3
    assert scores["target"]["score_components"]["distance"] == 6
    assert "suspect" not in scores and all(c["score_components"]["unexplored"] == 0 for c in scores.values())
    trace = result.trace
    path = next(p for p in trace["paths"] if p["path_ref"] == scores["hub"]["path_ref"])
    assert path["directions"] == ("reverse","forward","forward") and path["cost"] == sum(path["costs"])
    assert {p for p in path["evidence_ids"]} >= {"test", "fail", "hub"}
    assert diagnose(artifact, trigger).trace == trace
    assert diagnose(artifact, trigger, limits=Limits(visits=1)).trace["termination"] == "visit_limit"
    narrow = diagnose(artifact, trigger, limits=Limits(branch=1, depth=1, top_k=1))
    assert narrow.trace["truncated"] and narrow.trace["visited_count"] == 2
    assert diagnose(artifact, trigger, clock=lambda: 4, deadline=3).trace["termination"] == "time_limit"


def test_default_branch_and_top_k_are_applied_independently():
    graph = Builder()
    graph.nodes["fail"] = Node("fail", "FAILURE", "exec", "v", "observed", ("r",),
                               {"check_result_id": "r", "test_id": "t", "turn_id": "t", "sequence": 1})
    for i in range(27):
        key = f"n{i:02}"
        graph.nodes[key] = Node(key, "FUNCTION", "repo", "v", "observed", ("blob",), {"path": key + ".py"})
        graph.edge("fail", key, "FAILS_AT", ("r",))
    result = diagnose(graph.finish("coupled", RUN, "v", 1), Detection((Signal(TriggerId.T4, ("r",), ()),), ()))
    assert len(result.candidates) == 8 and result.trace["visited_count"] == 21
    assert result.trace["truncated"] and result.trace["termination"] == "exhausted"
    assert [c["candidate_id"] for c in result.candidates] == [f"n{i:02}" for i in range(8)]


def test_read_envelope_requires_a_stable_public_snapshot(tmp_path):
    store = RunStore(tmp_path / RUN, RUN)
    ledger = EvidenceLedger(store)
    body = {"type": "file_access.observed", "access_kind": "read", "access_basis": "direct_read",
            "path": "m.py", "snapshot_id": "a"*64}
    obs = Observer(store, ledger, lambda _: canonical_json_bytes(body))
    snap = SnapshotRef("fixture", "a"*64, "base", "snapshots/a.json", NOW, True)
    for i, (after, path) in enumerate(((snap, "m.py"), (replace(snap, snapshot_id="b"*64), "m.py"),
                                       (snap, "restricted/hidden.py"))):
        turn, raw = f"t{i}", f"r{i}"
        body["path"] = path
        obs.receive(RawEvent(RUN, turn, raw, i, NOW, None, "file_access.observed", "restricted/test", "OK"), turn)
        result = TurnResult(turn, UnitStatus.COMPLETED, "session", None, None, None,
                            False, 0, (raw,), (), None, True)
        obs.collect(result, snap, after, None, ())
    assert [r.kind for r in ledger.view().records if r.raw_refs] == ["FILE_ACCESS", "UNKNOWN", "UNKNOWN"]


def test_stagnation_relocalizes_only_with_an_explicit_observed_result(tmp_path):
    snap = snapshot(tmp_path, {"entry.py": "from dep import label\n", "dep.py": "def label(): return 1\n"})
    version = snap.snapshot_id
    store = RunStore(tmp_path / RUN, RUN)
    ledger = EvidenceLedger(store)
    obs = Observer(store, ledger)
    obs.collect_check(receipt("p1", "before", version, status=CheckStatus.PASS), "before")
    ledger.append_batch((event("read", "FILE_ACCESS", version, {"path": "entry.py", "span": None,
                                                               "access_basis": "direct_read", "access_kind": "read"}),))
    ledger.append_batch(event(f"s{i}", "SEARCH", version, {"query": "entry", "scope": ".", "result_digest": "a"*64,
                                                            "result_ref": "read"}) for i in range(6))
    obs.collect_check(receipt("p2", "t", version, status=CheckStatus.PASS), "t")
    view = ledger.view()
    detection = detect(view, snap, "t", ("check",))
    assert [s.kind for s in detection.signals] == ["T2"]
    repo = repository.build(tmp_path, snap, RUN)
    def decision(view):
        graph = mapping.link((repo,), execution.build(view, RUN, version, ("check",)))
        return select(detection, diagnose(graph, detection), ("check",))
    strategy = decision(view)
    assert strategy.strategy_id == "RELOCALIZE" and strategy.targets == ("dep.py",)
    assert {"s0", "s5", "read"}.issubset(strategy.evidence_ids)
    without_links = tuple(replace(r, payload={**r.payload, "result_ref": None}) if r.kind == "SEARCH" else r
                          for r in view.records)
    assert decision(EvidenceView(without_links, view.checks)).strategy_id == "GENERIC_RETRY"
