"""Bounded weighted traversal of public evidence and versioned Python dependencies."""

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from time import monotonic

import networkx as nx

from aah.domain import ContractError
from aah.graphs.artifact import GraphArtifact, identity


# Each edge costs one unit, with one additional unit for an inferred relation.
FORWARD = {"SAME_FAILURE_AS", "RETRIES", "READS", "FAILS_AT", "CHANGES", "TESTS_TARGET",
           "CALLS", "IMPORTS", "TESTS"}
REVERSE = {"RETURNS", "OBSERVED_AFTER", "CALLS", "IMPORTS", "TESTS", "CONTAINS"}
DEPENDENCIES = {"TESTS", "CALLS", "IMPORTS"}


@dataclass(frozen=True)
class Limits:
    depth: int = 4
    branch: int = 20
    visits: int = 200
    top_k: int = 8

    def __post_init__(self):
        if any(type(n) != int or n < 1 for n in (self.depth, self.branch, self.visits, self.top_k)):
            raise ContractError("graph limits must be positive integers")


@dataclass(frozen=True)
class GraphDiagnosis:
    candidates: tuple[dict, ...]
    trace: dict
    historical: tuple[dict, ...]


def diagnose(graph: GraphArtifact, detection, *, limits=Limits(), deadline=None, clock=monotonic):
    network = nx.MultiDiGraph()
    nodes = {n.node_id: n for n in graph.nodes}
    network.add_nodes_from(nodes)
    edges = {e.edge_id: e for e in graph.edges}
    modifications = defaultdict(list)
    for e in graph.edges:
        if e.source_id not in nodes or e.target_id not in nodes:
            raise ContractError("dangling diagnosis edge")
        if e.observation_status == "unknown":
            continue
        if e.relation == "CHANGES" and e.attributes.get("role") == "after":
            modifications[e.target_id].append(nodes[e.source_id])
        cost = 1 + int(e.observation_status == "inferred")
        if e.relation in FORWARD:
            network.add_edge(e.source_id, e.target_id, e.edge_id, step=(cost, "forward"))
        if e.relation in REVERSE:
            network.add_edge(e.target_id, e.source_id, e.edge_id, step=(cost, "reverse"))

    triggers = {}
    search_basis = defaultdict(set)
    for n in graph.nodes:
        if (n.kind not in {"FAILURE", "SEARCH"} or n.namespace != "exec" or n.observation_status != "observed"
                or n.snapshot_id != graph.current_snapshot_id):
            continue
        ref = n.attributes.get("check_result_id") or n.attributes["evidence_id"]
        keys = n.attributes.get("signal_keys", {})
        matched = tuple(s.kind for s in detection.signals if ref in s.anchors and
                        (s.kind not in {"T1", "T3"} or set(keys.get(s.kind, ())).intersection(s.meaning)))
        if matched:
            triggers[n.node_id] = matched
    # A repeated search may explicitly identify a returned read. Its text alone is insufficient.
    reads = defaultdict(set)
    for n in graph.nodes:
        if n.kind == "FILE_ACCESS" and n.snapshot_id == graph.current_snapshot_id and n.observation_status == "observed":
            for ref in (*n.evidence_ids, *n.attributes.get("raw_refs", ())):
                reads[n.attributes["turn_id"], ref].add(n.node_id)
    for key, matched in tuple(triggers.items()):
        search = nodes[key]
        if "T2" not in matched or search.kind != "SEARCH":
            continue
        matches = reads.get((search.attributes["turn_id"], search.attributes.get("result_ref")), ())
        if len(matches) == 1:
            read_key, = matches
            triggers[read_key] = ("T2",)
            search_basis[read_key].update(search.evidence_ids)
    # T1 can cite previous receipts at the same snapshot. Start from the latest instance for each case.
    latest = {}
    for key in sorted(triggers):
        n = nodes[key]
        group = (n.attributes.get("check_id"), n.attributes.get("test_id")) if n.kind == "FAILURE" else (key,)
        if group not in latest or nodes[latest[group]].attributes["sequence"] < n.attributes["sequence"]:
            latest[group] = key
    anchors = tuple(sorted(latest.values()))
    frontier = []
    best = {}
    for key in anchors:
        heappush(frontier, (0, 0, key, 0, (), (key,)))
        best[key, 0] = (0, 0, ())
    paths, historic, localizations = {}, {}, {}
    visited, truncated, termination = 0, False, "exhausted"
    while frontier:
        cost, depth, key, temporal_steps, steps, route = heappop(frontier)
        if best[key, depth] != (cost, temporal_steps, steps):
            continue
        if deadline is not None and clock() >= deadline:
            termination, truncated = "time_limit", True
            break
        if visited >= limits.visits:
            termination, truncated = "visit_limit", True
            break
        visited += 1
        node = nodes[key]
        if node.namespace == "repo":
            bucket = paths if node.snapshot_id == graph.current_snapshot_id else historic
            if key not in bucket:
                bucket[key] = (cost, steps, route)
            relations = {edges[e].relation for e, _, _ in steps}
            from_search_read = route[0] in search_basis and "READS" in relations
            if (key not in localizations and ({"FAILS_AT", "TESTS_TARGET"} & relations or from_search_read)
                    and "OBSERVED_AFTER" not in relations):
                localizations[key] = (cost, steps, route)
        neighbors = sorted((data["step"][0], neighbor, edge_id, data["step"][1])
                           for _, neighbor, edge_id, data in network.out_edges(key, keys=True, data=True)
                           if neighbor not in route)
        if depth == limits.depth:
            truncated |= bool(neighbors)
            continue
        truncated |= len(neighbors) > limits.branch
        for weight, neighbor, edge_id, direction in neighbors[:limits.branch]:
            # For equal-cost paths to one state, prefer a dependency over a temporal coincidence.
            new = (cost + weight, temporal_steps + int(edges[edge_id].relation == "OBSERVED_AFTER"),
                   (*steps, (edge_id, direction, weight)))
            state = (neighbor, depth + 1)
            if state not in best or new < best[state]:
                best[state] = new
                heappush(frontier, (new[0], depth + 1, neighbor, new[1], new[2], (*route, neighbor)))

    path_rows = {}
    def record_path(path):
        cost, steps, route = path
        walked = [edges[e] for e, _, _ in steps]
        relations = tuple(e.relation for e in walked)
        basis = tuple(sorted({ref for e in walked for ref in e.evidence_ids} | search_basis.get(route[0], set())))
        pid = identity(route, steps)
        path_rows[pid] = {"path_ref": pid, "anchor_id": route[0], "node_ids": route,
                          "edge_ids": tuple(e.edge_id for e in walked), "relations": relations,
                          "directions": tuple(d for _, d, _ in steps), "costs": tuple(w for _, _, w in steps),
                          "cost": cost, "evidence_ids": basis, "search_result_evidence_ids": sorted(search_basis.get(route[0], ()))}
        return path_rows[pid]

    def scored(key, path):
        shortest = record_path(path)
        support = record_path(localizations[key]) if key in localizations else shortest
        localized = key in localizations
        anchor, target = nodes[support["anchor_id"]], nodes[key]
        observation = min(1.0 if edges[e].observation_status == "observed" else 0.5 for e in shortest["edge_ids"])
        cost = shortest["cost"]
        changes = [n for n in modifications[key] if n.attributes["turn_id"] == anchor.attributes["turn_id"]]
        modified = int(localized and bool(changes))
        change_refs = {ref for n in changes for ref in n.evidence_ids} if modified else set()
        a = target.attributes
        return {"candidate_id": key, "kind": target.kind, "path": a["path"], "qualified_name": a.get("qualified_name"),
                "snapshot_id": target.snapshot_id, "path_ref": shortest["path_ref"], "relations": shortest["relations"],
                "support_ref": support["path_ref"], "support_relations": support["relations"],
                "anchor_ref": anchor.attributes.get("check_result_id") or anchor.attributes["evidence_id"],
                "trigger_ids": triggers[anchor.node_id], "observation_status": "inferred",
                "evidence_ids": tuple(sorted(set(shortest["evidence_ids"]) | set(support["evidence_ids"]) | change_refs)),
                "score": 0.4 / (1 + cost) + 0.3 * observation + 0.15 * modified,
                "score_components": {"variant": "graph", "distance": cost, "observed": observation,
                                     "modified": modified, "unexplored": 0}, "coverage_status": "unknown",
                "warnings": ("FILE_ACCESS_COVERAGE_UNKNOWN",), "localized": localized,
                "alternative": localized and bool(DEPENDENCIES.intersection(support["relations"]))
                and (target.kind in {"CLASS", "FUNCTION"} or target.kind == "FILE" and
                     anchor.kind == "FILE_ACCESS" and a["path"] != anchor.attributes["path"])}

    candidates = sorted((scored(key, value) for key, value in paths.items()),
                        key=lambda c: (-c["score"], c["path"], c["candidate_id"]))[:limits.top_k]
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    historical = tuple(scored(key, value) for key, value in sorted(historic.items()))
    selected_paths = sorted({c[field] for c in (*candidates, *historical) for field in ("path_ref", "support_ref")})
    trace = {"graph_id": graph.graph_id, "snapshot_id": graph.current_snapshot_id, "anchor_ids": anchors,
             "allowed_relations": sorted(FORWARD | REVERSE), "visited_count": visited, "depth_limit": limits.depth,
             "branch_limit": limits.branch, "visit_limit": limits.visits, "top_k": limits.top_k,
             "termination": termination, "truncated": truncated, "paths": [path_rows[p] for p in selected_paths]}
    trace["trace_id"] = identity(trace, candidates, historical)
    return GraphDiagnosis(tuple(candidates), trace, historical)
