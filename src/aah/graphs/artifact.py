"""Immutable graph interchange with deterministic multiedge identities."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from aah.domain import ContractError, ObservationStatus, WireRecord, canonical_json_bytes, sha256_bytes


def identity(*parts) -> str:
    return sha256_bytes(canonical_json_bytes(parts))


@dataclass(frozen=True)
class Node(WireRecord):
    node_id: str
    kind: str
    namespace: str
    snapshot_id: str | None
    observation_status: ObservationStatus
    evidence_ids: tuple[str, ...]
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class Edge(WireRecord):
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    observation_status: ObservationStatus
    evidence_ids: tuple[str, ...]
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class GraphArtifact(WireRecord):
    graph_kind: Literal["repository", "execution", "coupled"]
    run_id: str
    current_snapshot_id: str
    ledger_cursor: int
    builder_version: str
    status: Literal["complete", "partial", "unavailable"]
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    warnings: tuple[str, ...]
    unresolved: tuple[Mapping[str, Any], ...]
    graph_id: str

    def validate(self):
        node_ids = [n.node_id for n in self.nodes]
        edge_ids = [e.edge_id for e in self.edges]
        known = set(node_ids)
        if node_ids != sorted(known) or edge_ids != sorted(set(edge_ids)):
            raise ContractError("graph identities must be distinct and sorted")
        if any(e.source_id not in known or e.target_id not in known for e in self.edges):
            raise ContractError("graph edge has a missing endpoint")
        if self.graph_id != identity(self.graph_kind, self.run_id, self.current_snapshot_id, self.ledger_cursor,
                                     self.builder_version, self.status, self.nodes, self.edges, self.warnings, self.unresolved):
            raise ContractError("graph identity does not match its contents")


class Builder:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.warnings: set[str] = set()
        self.unresolved: dict[str, dict] = {}

    def missing(self, event, relation, reason):
        record = {"node_id": event.node_id, "snapshot_id": event.snapshot_id, "relation": relation,
                  "reason": reason, "evidence_ids": event.evidence_ids}
        self.unresolved[identity(record)] = record
        self.warnings.add(f"{relation}:{reason}")

    def edge(self, source, target, relation, evidence, *, status="observed", **attributes):
        if source not in self.nodes or target not in self.nodes:
            raise ContractError("graph edge has a missing endpoint")
        refs = tuple(sorted(set(evidence)))
        key = identity(source, target, relation, status, refs, attributes)
        self.edges[key] = Edge(key, source, target, relation, status, refs, attributes)

    def finish(self, kind, run_id, snapshot, cursor):
        fields = (kind, run_id, snapshot, cursor, "s3-graph-v1", "partial" if self.warnings else "complete",
                  tuple(self.nodes[key] for key in sorted(self.nodes)), tuple(self.edges[key] for key in sorted(self.edges)),
                  tuple(sorted(self.warnings)), tuple(self.unresolved[k] for k in sorted(self.unresolved)))
        return GraphArtifact(*fields, identity(*fields))
