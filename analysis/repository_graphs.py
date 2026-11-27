"""Repository Graphs for the Main public tasks, re-extracted for the dashboard (model-free, read-only).

The dashboard's graph explorer shows each Main task's code structure whether or not a graph diagnosis happened.
This module runs the existing builder (``aah.graphs.repository.build``) unchanged on each task's public base
(``benchmarks/main_public/<task>/base``) and stores the nodes and edges under ``public_data/repository_graphs/``.

These are **re-extractions from the current checkout**, not the graphs recorded during the experiments. Graphs
recorded at run time stay in each experiment export (``runs[*].interventions[*].graph_detail``); the dashboard
compares the two by node and edge labels and says so when they differ.

    python -m analysis.repository_graphs build    # write public_data/repository_graphs/
    python -m analysis.repository_graphs check    # rebuild in memory and compare bytes

Nothing here starts a model, a Codex process or a container. Only public task files are read: task.json fields
listed in TASK_FIELDS and the .py files of base/ that the builder itself accepts (public_path).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from analysis import submission_bundle as sb

CHECKOUT = sb.CHECKOUT
TASKS = Path("benchmarks/main_public")
OUTPUT = Path("public_data/repository_graphs")
RUN_ID = "dashboard-reextract"
CAPTURED_AT = "2026-11-27T00:00:00Z"  # not part of any graph identity; SnapshotRef requires a UTC value
SHORT = 16
TASK_FIELDS = ("task_id", "repo_id", "task_family_id", "allowed_change_paths", "approved_check_ids")
NOTE = ("현재 저장소의 공개 과제 원본(base/)에서 기존 그래프 빌더로 다시 추출한 Repository Graph. "
        "실험 실행 중 기록된 그래프가 아니다. 모델 호출 0.")


class GraphExportError(RuntimeError):
    pass


def _short(value: str) -> str:
    return value[:SHORT]


def task_graph(task_dir: Path) -> dict:
    """Build one task's Repository Graph from its public base with the unchanged builder."""
    # Imported here so that load() (used by the static site) needs only the committed JSON files.
    from aah.domain import SnapshotRef, canonical_json_bytes, sha256_bytes
    from aah.graphs import repository
    from aah.workspace.patch import digest_ref, store_bytes
    from aah.workspace.snapshot import manifest, read_tree, revision

    spec = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    info = {key: spec.get(key) for key in TASK_FIELDS}
    files, unavailable = read_tree(task_dir / "base")
    if unavailable:
        raise GraphExportError(f"{task_dir.name}: unreadable files in base/")
    source_revision = revision(files)
    with tempfile.TemporaryDirectory(prefix="aah-repo-graph-") as tmp:
        root = Path(tmp)
        for item in files.values():
            store_bytes(root, digest_ref("blobs", item.digest), item.data)
        data = canonical_json_bytes(manifest(info["repo_id"], source_revision, files, files, set()))
        digest = sha256_bytes(data)
        ref = digest_ref("snapshots", digest)
        store_bytes(root, ref, data)
        graph = repository.build(root, SnapshotRef(info["repo_id"], digest, source_revision, ref, CAPTURED_AT, True),
                                 RUN_ID)
    ids = [n.node_id for n in graph.nodes] + [e.edge_id for e in graph.edges]
    if len({_short(i) for i in ids}) != len(ids):
        raise GraphExportError(f"{task_dir.name}: node/edge id prefix collision")
    nodes = []
    for n in graph.nodes:
        a = dict(n.attributes)
        qual = a.get("qualified_name")
        nodes.append({"id": _short(n.node_id), "kind": n.kind, "path": a.get("path"), "qualified_name": qual,
                      "label": a.get("path") + ("::" + qual if qual else ""),
                      "span": list(a["span"]) if a.get("span") else None, "line_count": a.get("line_count"),
                      "parse_status": a.get("parse_status"), "test_id": a.get("test_id"),
                      "is_async": a.get("is_async"), "observation": n.observation_status})
    edges = [{"id": _short(e.edge_id), "relation": e.relation, "source": _short(e.source_id),
              "target": _short(e.target_id), "basis": (e.attributes or {}).get("basis"),
              "line": (e.attributes or {}).get("line"), "observation": e.observation_status} for e in graph.edges]
    out = {"schema_version": sb.SCHEMA_VERSION, "kind": "repository_graph", **info,
           "provenance": {"source": (TASKS / task_dir.name / "base").as_posix(), "source_revision": source_revision,
                          "snapshot_id": digest, "builder": "aah.graphs.repository.build",
                          "builder_version": graph.builder_version, "graph_id": graph.graph_id, "model_calls": 0,
                          "origin": "re-extracted", "note": NOTE},
           "status": graph.status,
           "files": sorted(path for path in files if path.endswith(".py")),
           "nodes": sorted(nodes, key=lambda n: (n["path"] or "", (n["span"] or [0])[0], n["kind"], n["label"], n["id"])),
           "edges": sorted(edges, key=lambda e: (e["relation"], e["source"], e["target"], e["line"] or 0, e["id"])),
           "warnings": sorted(graph.warnings),
           "unresolved": [dict(u) for u in graph.unresolved]}
    sb.assert_public(out)
    return out


def render(root: Path = CHECKOUT) -> dict[Path, bytes]:
    task_dirs = sorted(p for p in (root / TASKS).glob("AAH-*") if (p / "task.json").is_file() and (p / "base").is_dir())
    if not task_dirs:
        raise GraphExportError(f"no public Main tasks under {TASKS.as_posix()}")
    outputs, index = {}, {}
    for task_dir in task_dirs:
        graph = task_graph(task_dir)
        body = sb.canonical(graph)
        name = f"{graph['task_id']}.json"
        outputs[OUTPUT / name] = body
        index[graph["task_id"]] = {"file": name, "sha256": sb.sha256(body),
                                   "source_revision": graph["provenance"]["source_revision"],
                                   "status": graph["status"], "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}
    manifest_body = {"schema_version": sb.SCHEMA_VERSION, "kind": "repository_graphs", "origin": "re-extracted",
                     "generator": "analysis.repository_graphs", "builder": "aah.graphs.repository.build",
                     "model_calls": 0, "note": NOTE, "tasks": index}
    outputs[OUTPUT / "MANIFEST.json"] = sb.canonical(manifest_body)
    return outputs


def load(root: Path = CHECKOUT) -> dict | None:
    """Committed graphs with integrity checks, or None when the folder does not exist."""
    folder = root / OUTPUT
    if not (folder / "MANIFEST.json").is_file():
        return None
    m = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    tasks = {}
    for task_id, entry in sorted(m["tasks"].items()):
        body = (folder / entry["file"]).read_bytes()
        if sb.sha256(body) != entry["sha256"]:
            raise GraphExportError(f"repository_graphs/{entry['file']} does not match MANIFEST.json")
        tasks[task_id] = json.loads(body.decode("utf-8"))
    return {"manifest": {k: v for k, v in m.items() if k != "tasks"}, "tasks": tasks}


def build(root: Path = CHECKOUT) -> list[str]:
    outputs = render(root)
    for rel, data in outputs.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return [rel.as_posix() for rel in outputs]


def check(root: Path = CHECKOUT) -> list[str]:
    return [rel.as_posix() for rel, data in render(root).items()
            if not (root / rel).is_file() or (root / rel).read_bytes() != data]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("build", "check"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            written = build()
            print("REPOSITORY_GRAPHS_WRITTEN", len(written), "files", OUTPUT.as_posix())
            return 0
        differ = check()
        print("REPOSITORY_GRAPHS_REPRODUCED" if not differ else "REPOSITORY_GRAPHS_DIFFER " + " ".join(differ))
        return 0 if not differ else 1
    except (GraphExportError, sb.BundleError, OSError, ValueError) as error:
        print("REPOSITORY_GRAPHS_BLOCKED", error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
