"""Build linked artifacts on demand at an eligible graph recovery boundary."""

from time import monotonic

import networkx as nx

from aah.diagnosis.graph import diagnose
from aah.domain import canonical_json_bytes, dump_record
from aah.workspace.patch import store_bytes

from . import execution, mapping, repository


class GraphUnavailable(RuntimeError):
    """Graph computation failed while the common evidence is still usable."""


def update(snapshot, history, view, detection, *, approved, store, deadline=None, clock=monotonic):
    start = clock()
    try:
        repositories = [repository.build(store.root / "artifacts", ref, store.run_id) for ref in history.values()]
        events = execution.build(view, store.run_id, snapshot.snapshot_id, approved)
        graph = mapping.link(repositories, events)
        built = clock()
        diagnosis = diagnose(graph, detection, deadline=deadline, clock=clock)
    except (nx.NetworkXException, RecursionError) as exc:
        raise GraphUnavailable(type(exc).__name__) from exc
    elapsed = clock()
    for artifact in (*repositories, events, graph):
        # IDs hash the versioned builder content independently of profiling.
        store_bytes(store.root, f"graphs/{artifact.graph_id}.json", dump_record(artifact))
    trace = diagnosis.trace
    store_bytes(store.root, f"graphs/{trace['trace_id']}.json", canonical_json_bytes(
        {"schema_version": "1.0", **trace, "candidates": diagnosis.candidates, "historical": diagnosis.historical}))
    return diagnosis, {"graph_id": graph.graph_id, "trace_id": trace["trace_id"], "status": graph.status,
                       "build_ms": max(0, int((built - start) * 1000)), "diagnosis_ms": max(0, int((elapsed - built) * 1000)),
                       "repository_ids": [a.graph_id for a in repositories], "execution_id": events.graph_id}
