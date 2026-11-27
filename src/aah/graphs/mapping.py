"""Join observations only to evidenced repository locations at a verified version."""

from collections import defaultdict

from aah.domain import ContractError
from aah.domain.evidence import DIRECT_READ_BASES

from .artifact import Builder, identity


def link(repositories, execution):
    if (execution.graph_kind != "execution" or any(r.graph_kind != "repository" for r in repositories)
            or execution.current_snapshot_id not in {r.current_snapshot_id for r in repositories}):
        raise ContractError("mapping requires repository artifacts including the current snapshot")
    graph = Builder()
    file_at, symbols, tests = {}, defaultdict(list), defaultdict(list)
    for artifact in (*repositories, execution):
        if artifact.run_id != execution.run_id:
            raise ContractError("graph artifacts belong to different runs")
        graph.nodes.update((node.node_id, node) for node in artifact.nodes)
        graph.edges.update((edge.edge_id, edge) for edge in artifact.edges)
        graph.warnings.update(artifact.warnings)
        graph.unresolved.update((identity(row), row) for row in artifact.unresolved)
    for node in graph.nodes.values():
        if node.namespace != "repo":
            continue
        a = node.attributes
        loc = (node.snapshot_id, a["path"])
        if node.kind == "FILE":
            file_at[loc] = node
        else:
            symbols[loc].append(node)
            if a.get("test_id"):
                tests[node.snapshot_id, a["test_id"]].append(node)

    for event in execution.nodes:
        if event.observation_status != "observed" or event.kind not in {"TEST", "CHANGE", "FILE_ACCESS", "FAILURE"}:
            continue
        a = event.attributes
        def join(target, relation, **basis):
            graph.edge(event.node_id, target.node_id, relation, (*event.evidence_ids, *target.evidence_ids),
                       snapshot_id=target.snapshot_id, **basis)
        def unresolved(relation, reason):
            graph.missing(event, relation, reason)
        if event.kind == "TEST":
            case = a["test_id"] or (a["case_ids"][0] if len(a["case_ids"]) == 1 else None)
            matches = tests.get((event.snapshot_id, case), ())
            if len(matches) == 1:
                join(matches[0], "TESTS_TARGET", basis="approved_test_id")
            elif case:
                unresolved("TESTS_TARGET", "unresolved_test_id")
        elif event.kind == "CHANGE":
            if event.snapshot_id != a["after_snapshot_id"]:
                unresolved("CHANGES", "snapshot_mismatch")
                continue
            for role, prefix in (("before", "old"), ("after", "new")):
                loc = (a[role + "_snapshot_id"], a["path"])
                if loc not in file_at:
                    unresolved("CHANGES", role + "_file_absent")
                    continue
                join(file_at[loc], "CHANGES", role=role, basis="snapshot_diff")
                for hunk in a["hunks"]:
                    first, count = hunk.get(prefix + "_start"), hunk.get(prefix + "_count")
                    if hunk.get("path") != a["path"] or type(first) != int or type(count) != int or count <= 0:
                        continue
                    last = first + count - 1
                    for symbol in symbols[loc]:
                        low, high = symbol.attributes["span"]
                        if first <= high and low <= last:
                            join(symbol, "CHANGES", role=role, basis="diff_hunk", span=(first, last))
        elif event.kind == "FILE_ACCESS":
            if a["access_kind"] != "read":
                continue
            loc = (event.snapshot_id, a["path"])
            if loc in file_at and a["access_basis"] in DIRECT_READ_BASES:
                join(file_at[loc], "READS", basis=a["access_basis"])
            else:
                unresolved("READS", "unverified_access_or_version")
        else:
            frame = a["stable_frame"] or {}
            loc = (event.snapshot_id, frame.get("path")) if isinstance(frame.get("path"), str) else None
            line = frame.get("line")
            if loc not in file_at or type(line) != int or not 1 <= line <= file_at[loc].attributes["line_count"]:
                unresolved("FAILS_AT", "unverified_frame_or_version")
                continue
            join(file_at[loc], "FAILS_AT", basis="traceback", line=line)
            for symbol in symbols[loc]:
                low, high = symbol.attributes["span"]
                if low <= line <= high:
                    join(symbol, "FAILS_AT", basis="traceback", line=line)
    return graph.finish("coupled", execution.run_id, execution.current_snapshot_id, execution.ledger_cursor)
