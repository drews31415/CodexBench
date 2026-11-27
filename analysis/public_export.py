"""Versioned public data store for the web dashboard (model-free; read-only on results/).

The store under ``public_data/`` is append-only:

``experiments/<experiment_id>/``  export.json, MANIFEST.json and (when analysed) the frozen analysis outputs
``synthetic/<suite>/``            export.json, MANIFEST.json for scripted verification runs (never live results)
``releases/<release_id>.json``    a named, immutable selection of experiments pinned by data hash
``catalog.json``                  index rebuilt deterministically from the manifests and releases

Experiment validity comes only from the human-curated registry ``configs/dashboard/experiments.json``. A newly
discovered experiment is exported as IN_PROGRESS, ANALYSIS_PENDING or UNREVIEWED and never assumed valid.
Once an experiment is finalized (analysed), its data cannot be overwritten; only its registry status may change,
and every status change is kept in the manifest history. Different experiments are never merged.

``export`` and ``release`` also rebuild the static GitHub Pages dashboard (``analysis.static_site``).

Commands (run from the checkout root with ``PYTHONPATH=src;.``)::

    python -m analysis.public_export discover
    python -m analysis.public_export export [--experiment ID ...] [--allow-update]
    python -m analysis.public_export release --id ID --label TEXT --kind submission|presentation
                                             --experiments ID,ID --primary ID [--verification FILE]
    python -m analysis.public_export verify      # store only (works without results/)
    python -m analysis.public_export check       # re-export in memory and compare (needs results/)

Fields are copied by whitelist (see ``analysis.submission_bundle``); restricted payloads, snapshot blobs, patch
bodies, curator kits, private checks and credentials are never opened.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from analysis import static_site
from analysis import submission_bundle as sb

CHECKOUT = sb.CHECKOUT
STORE = Path("public_data")
REGISTRY = Path("configs/dashboard/experiments.json")
EXPORTER_VERSION = "public-export-1"
FINAL = {"VALID", "VALID_EXPLORATORY", "INVALID_INFRA_COMPROMISED", "UNREVIEWED"}
OPEN = {"IN_PROGRESS", "ANALYSIS_PENDING"}
STATUS_FIELDS = ("status", "status_label", "status_reason", "status_source", "validity", "superseded_by",
                 "timeline", "label", "registry_warnings")
TERMINAL_DISPOSITIONS = {"VALID_SUCCESS", "VALID_FAILURE", "MISSING_INFRA", "NOT_RUN_BUDGET"}


class StoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def load_registry(root: Path = CHECKOUT, path: Path = REGISTRY) -> dict:
    return json.loads((root / path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- discovery and status

def discover(root: Path = CHECKOUT, registry: dict | None = None) -> list[dict]:
    """Every experiment root under the registry's discovery globs, with its matching analysis directories."""
    registry = registry or load_registry(root)
    analyses: dict[str, list[str]] = {}
    for pattern in registry["discovery"]["analysis_roots"]:
        for path in sorted(glob.glob(str(root / pattern / "analysis.json"))):
            try:
                experiment_id = json.loads(Path(path).read_text(encoding="utf-8")).get("experiment_id")
            except (OSError, ValueError):
                continue
            analyses.setdefault(experiment_id, []).append(_rel(root, Path(path).parent))
    found = []
    for pattern in registry["discovery"]["raw_roots"]:
        for path in sorted(glob.glob(str(root / pattern / "experiment.json"))):
            raw = Path(path).parent
            experiment_id = json.loads(Path(path).read_text(encoding="utf-8")).get("experiment_id")
            entry = registry["experiments"].get(experiment_id)
            candidates = analyses.get(experiment_id, [])
            chosen = entry.get("analysis_dir") if entry and entry.get("analysis_dir") else (
                candidates[0] if len(candidates) == 1 else None)
            found.append({"experiment_id": experiment_id, "raw": _rel(root, raw), "analysis_dir": chosen,
                          "analysis_candidates": candidates, "registered": entry is not None})
    return found


def assess(root: Path, raw: str) -> tuple[list[dict], dict]:
    """Left-joined slot rows (main_analysis.ingest) and the experiment's progress."""
    from analysis import main_analysis

    rows, inputs, _ = main_analysis.ingest(root / raw)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["disposition"]] = counts.get(r["disposition"], 0) + 1
    open_slots = [r["slot_id"] for r in rows if r["disposition"] not in TERMINAL_DISPOSITIONS
                  and not (r["disposition"] == "NOT_RUN_OTHER" and r["reason_code"] not in (None, "NOT_STARTED"))]
    progress = {"planned": len(rows), "attempted": sum(r["selected_run_id"] is not None for r in rows),
                "evaluated": sum(r["evaluation_status"] is not None for r in rows),
                "dispositions": dict(sorted(counts.items())), "open_slots": len(open_slots), "complete": not open_slots}
    return rows, {"progress": progress, "inputs": inputs}


def resolve_status(entry: dict | None, progress: dict, analysis_dir: str | None) -> tuple[str, str, list[str]]:
    """(status, source, warnings). Registry validity applies only to complete, analysed experiments."""
    warnings = []
    if not progress["complete"]:
        if entry and entry.get("status") in FINAL:
            warnings.append("REGISTRY_STATUS_IGNORED_EXPERIMENT_INCOMPLETE")
        return "IN_PROGRESS", "auto", warnings
    if analysis_dir is None:
        if entry and entry.get("status") in FINAL:
            warnings.append("REGISTRY_STATUS_IGNORED_NO_ANALYSIS")
        return "ANALYSIS_PENDING", "auto", warnings
    if entry and entry.get("status"):
        return entry["status"], "registry", warnings
    return "UNREVIEWED", "auto", warnings


# ---------------------------------------------------------------- export

def _status_block(registry: dict, entry: dict | None, status: str, source: str, warnings: list[str]) -> dict:
    entry = entry or {}
    return {"status": status, "status_label": registry["statuses"].get(status, status), "status_source": source,
            "status_reason": entry.get("status_reason") if source == "registry" else {
                "IN_PROGRESS": "계획한 슬롯 중 아직 실행·판정되지 않은 슬롯이 있다. 결과는 확정되지 않았다.",
                "ANALYSIS_PENDING": "모든 슬롯이 끝났지만 동결 분석 산출물이 아직 없다.",
                "UNREVIEWED": "실행과 분석은 끝났지만 유효성 검토가 등록되지 않았다. 유효한 결과로 간주하지 않는다.",
            }.get(status), "validity": status, "superseded_by": entry.get("superseded_by"),
            "timeline": entry.get("timeline") or [], "label": entry.get("label"), "registry_warnings": warnings}


def build_experiment_export(root: Path, registry: dict, found: dict) -> tuple[dict, dict[str, bytes], dict]:
    """Export one experiment: (export, analysis files, source inputs)."""
    experiment_id = found["experiment_id"]
    entry = registry["experiments"].get(experiment_id)
    rows, assessed = assess(root, found["raw"])
    status, source, warnings = resolve_status(entry, assessed["progress"], found["analysis_dir"])
    if found["analysis_dir"] is None and len(found["analysis_candidates"]) > 1:
        warnings.append("ANALYSIS_AMBIGUOUS_SET_ANALYSIS_DIR_IN_REGISTRY")
    reader = sb.Reader(root)
    public_dir = f"experiments/{experiment_id}"
    if status in FINAL:
        spec = {"raw": found["raw"], "analysis": found["analysis_dir"], "public_dir": public_dir,
                "freeze": (entry or {}).get("freeze"), "approval": (entry or {}).get("approval"),
                "validity": status, "role": (entry or {}).get("role", "unreviewed"),
                "log_ids": (entry or {}).get("log_ids", []), "report_ref": (entry or {}).get("report_ref")}
        export, copies = sb.extract_experiment(reader, experiment_id, spec)
        files = {Path(name).name: data for name, data in copies.items()}
    else:
        export, files = _open_experiment_export(reader, experiment_id, found, rows, entry, public_dir), {}
    export.update(_status_block(registry, entry, status, source, warnings))
    export["progress"] = assessed["progress"]
    export["source"] = {"raw_root": found["raw"], "analysis_dir": found["analysis_dir"],
                        "analysis_candidates": found["analysis_candidates"]}
    sb.assert_public(export)
    inputs = {**reader.inputs, **{f"{found['raw']}/{k}": v for k, v in assessed["inputs"].items()}}
    return export, files, dict(sorted(inputs.items()))


def _open_experiment_export(reader, experiment_id, found, rows, entry, public_dir) -> dict:
    """In-progress / analysis-pending export: progress and per-run records, no metrics or contrasts."""
    raw = found["raw"]
    experiment = reader.json(f"{raw}/experiment.json") or {}
    locked = (reader.json(f"{raw}/locked-config.json") or {}).get("data") or {}
    tasks = (reader.json(f"{raw}/tasks.json") or {}).get("data") or {}
    runs = {}
    for row in rows:
        if row["selected_run_id"] is None:
            continue
        verdict = f"{raw}/slots/{row['slot_id']}/evaluation.json"
        run = sb.extract_run(reader, f"{raw}/runs/{row['selected_run_id']}",
                             verdict_rel=verdict if reader.exists(verdict) else None)
        run["slot_id"] = row["slot_id"]
        runs[row["selected_run_id"]] = run
    slots = [{**r, "first_turn_public": (runs.get(r["selected_run_id"]) or {}).get("first_turn_public"),
              "final_public": ((runs.get(r["selected_run_id"]) or {}).get("report") or {}).get("public_check_status"),
              "changed_paths": len(((runs.get(r["selected_run_id"]) or {}).get("report") or {}).get("changed_paths") or [])}
             for r in rows]
    return {
        "experiment_id": experiment_id, "role": (entry or {}).get("role", "unreviewed"),
        "log_ids": (entry or {}).get("log_ids", []), "report_ref": (entry or {}).get("report_ref"),
        "public_dir": public_dir,
        "meta": {"protocol": experiment.get("protocol"), "repetitions": experiment.get("repetitions"),
                 "conditions": experiment.get("conditions"), "code_version": experiment.get("code_version"),
                 "config_hash": experiment.get("config_hash"), "model": locked.get("model"),
                 "budget": locked.get("budget"), "analysis": None, "freeze": None, "approval": None},
        "dispositions": None, "conditions": None, "contrasts": None,
        "tasks": {t: {"task_family_id": v.get("task_family_id"), "repo_id": v.get("repo_id")}
                  for t, v in sorted(tasks.items())},
        "slots": slots, "runs": dict(sorted(runs.items())), "derived": sb._derived(runs, slots),
    }


def build_synthetic_export(root: Path, registry: dict) -> dict[str, tuple[dict, dict]]:
    out = {}
    for suite, spec in (registry.get("synthetic") or {}).items():
        reader = sb.Reader(root)
        runs = {}
        for run_id, scenario in spec["runs"].items():
            run_rel = f"{spec['root']}/{run_id}"
            if not reader.exists(f"{run_rel}/report.json"):
                continue
            run = sb.extract_run(reader, run_rel)
            run["scenario"] = scenario
            runs[run_id] = run
        export = {"suite": suite, "label": spec["label"], "report_ref": spec.get("report_ref"), "runs": runs,
                  "status": "SYNTHETIC", "status_label": "합성 검증 (라이브 아님)"}
        sb.assert_public(export)
        out[suite] = (export, dict(sorted(reader.inputs.items())))
    return out


# ---------------------------------------------------------------- store

def _hashes(export: dict, files: dict[str, bytes]) -> tuple[bytes, str, str]:
    body = sb.canonical(export)
    data_view = {k: v for k, v in export.items() if k not in STATUS_FIELDS}
    data_sha = sb.sha256(sb.canonical({"export": data_view, "files": {n: sb.sha256(b) for n, b in sorted(files.items())}}))
    content_sha = sb.sha256(sb.canonical({"export": sb.sha256(body), "files": {n: sb.sha256(b) for n, b in sorted(files.items())}}))
    return body, data_sha, content_sha


def write_entry(store: Path, kind: str, key: str, export: dict, files: dict[str, bytes], inputs: dict, *,
                allow_update: bool = False, now: str | None = None) -> str:
    """Write one experiment or synthetic suite; returns WRITTEN, UPDATED, STATUS_UPDATED or NO_CHANGE."""
    sb.assert_public(export)  # defense in depth: every stored payload is checked, not only fresh extractions
    folder = store / kind / key
    body, data_sha, content_sha = _hashes(export, files)
    manifest_path = folder / "MANIFEST.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    stamp = now or _now()
    status = export.get("status")
    if old is not None:
        if old["content_sha256"] == content_sha:
            return "NO_CHANGE"
        same_data = old["data_sha256"] == data_sha
        if old["status"] in FINAL and not same_data and not allow_update:
            raise StoreError(f"{key}: finalized data already in the store ({old['status']}); "
                             "export it under a new experiment ID instead of overwriting")
        outcome = "STATUS_UPDATED" if same_data else "UPDATED"
        history = old.get("status_history", []) + ([{"at": stamp, "from": old["status"], "to": status}]
                                                   if old["status"] != status else [])
        exported_at = old["exported_at"] if same_data else stamp
    else:
        outcome, history, exported_at = "WRITTEN", [{"at": stamp, "from": None, "to": status}], stamp
    manifest = {"schema_version": sb.SCHEMA_VERSION, "kind": kind, "key": key, "status": status,
                "status_label": export.get("status_label"), "label": export.get("label"),
                "exporter_version": EXPORTER_VERSION, "exported_at": exported_at, "status_updated_at": stamp,
                "data_sha256": data_sha, "content_sha256": content_sha,
                "files": {"export.json": sb.sha256(body), **{n: sb.sha256(b) for n, b in sorted(files.items())}},
                "source": {"inputs": len(inputs), "inputs_sha256": sb.sha256(sb.canonical(inputs)),
                           **(export.get("source") or {})},
                "progress": export.get("progress"), "status_history": history}
    sb.assert_public(manifest)
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    (folder / "export.json").write_bytes(body)
    manifest_path.write_bytes(sb.canonical(manifest))
    return outcome


def build_catalog(store: Path, registry: dict) -> dict:
    experiments, synthetic, releases = [], [], []
    for path in sorted((store / "experiments").glob("*/MANIFEST.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        experiments.append({k: m.get(k) for k in ("key", "status", "status_label", "label", "exported_at",
                                                   "status_updated_at", "data_sha256", "content_sha256", "progress")}
                           | {"path": f"experiments/{m['key']}", "source": m.get("source")})
    for path in sorted((store / "synthetic").glob("*/MANIFEST.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        synthetic.append({"key": m["key"], "label": m.get("label"), "path": f"synthetic/{m['key']}",
                          "exported_at": m["exported_at"], "data_sha256": m["data_sha256"]})
    for path in sorted((store / "releases").glob("*.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        releases.append({k: r.get(k) for k in ("release_id", "label", "kind", "created_at", "primary", "experiments",
                                                "synthetic", "note")})
    stamps = [e["exported_at"] for e in experiments] + [e["status_updated_at"] for e in experiments] + \
             [s["exported_at"] for s in synthetic] + [r["created_at"] for r in releases]
    default = registry.get("default_release")
    if default not in {r["release_id"] for r in releases}:
        default = max(releases, key=lambda r: r["created_at"])["release_id"] if releases else None
    return {"schema_version": sb.SCHEMA_VERSION, "exporter_version": EXPORTER_VERSION,
            "data_updated_at": max(stamps) if stamps else None, "default_release": default,
            "statuses": registry["statuses"], "limitations": registry.get("limitations", []),
            "experiments": sorted(experiments, key=lambda e: e["key"], reverse=True),
            "synthetic": synthetic, "releases": sorted(releases, key=lambda r: r["created_at"], reverse=True)}


def write_catalog(store: Path, registry: dict) -> dict:
    catalog = build_catalog(store, registry)
    sb.assert_public(catalog)
    (store / "catalog.json").write_bytes(sb.canonical(catalog))
    return catalog


def create_release(store: Path, release_id: str, *, label: str, kind: str, experiments: list[str], primary: str,
                   verification: Path | None = None, note: str | None = None, now: str | None = None) -> dict:
    path = store / "releases" / f"{release_id}.json"
    if path.exists():
        raise StoreError(f"release {release_id} already exists; releases are immutable")
    if primary not in experiments:
        raise StoreError("the primary experiment must be part of the release")
    pinned = {}
    for experiment_id in experiments:
        manifest_path = store / "experiments" / experiment_id / "MANIFEST.json"
        if not manifest_path.is_file():
            raise StoreError(f"{experiment_id} is not in the store; export it first")
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        if m["status"] not in FINAL:
            raise StoreError(f"{experiment_id} is {m['status']}; only finalized experiments can be released")
        pinned[experiment_id] = {"data_sha256": m["data_sha256"], "status_at_release": m["status"]}
    synthetic = {p.parent.name: json.loads(p.read_text(encoding="utf-8"))["data_sha256"]
                 for p in sorted((store / "synthetic").glob("*/MANIFEST.json"))}
    release = {"schema_version": sb.SCHEMA_VERSION, "release_id": release_id, "label": label, "kind": kind,
               "created_at": now or _now(), "primary": primary, "experiments": pinned, "synthetic": synthetic,
               "note": note,
               "verification": json.loads(verification.read_text(encoding="utf-8")) if verification else None}
    sb.assert_public(release)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sb.canonical(release))
    return release


def verify_store(store: Path, registry: dict) -> tuple[list[str], list[str]]:
    """(problems, lines) using the store alone: hashes, privacy, recomputation, release pins, catalog."""
    problems, lines = [], []
    data_hashes = {}
    for kind in ("experiments", "synthetic"):
        for manifest_path in sorted((store / kind).glob("*/MANIFEST.json")):
            folder, m = manifest_path.parent, json.loads(manifest_path.read_text(encoding="utf-8"))
            key = m["key"]
            for name, digest in m["files"].items():
                if not (folder / name).is_file() or sb.sha256((folder / name).read_bytes()) != digest:
                    problems.append(f"{kind}/{key}: {name} missing or hash differs from MANIFEST")
            export = json.loads((folder / "export.json").read_text(encoding="utf-8"))
            files = {n: (folder / n).read_bytes() for n in m["files"] if n != "export.json" and (folder / n).is_file()}
            _, data_sha, content_sha = _hashes(export, files)
            if (data_sha, content_sha) != (m["data_sha256"], m["content_sha256"]):
                problems.append(f"{kind}/{key}: data or content hash differs from MANIFEST")
            data_hashes[(kind, key)] = data_sha
            for value in (export, m):
                try:
                    sb.assert_public(value)
                except sb.BundleError as error:
                    problems.append(f"{kind}/{key}: privacy {error}")
            if kind == "experiments" and "analysis.json" in files:
                found, line = sb.recompute_problems(key, {n: files[n] for n in sb.ANALYSIS_FILES}, export)
                problems += found
                lines.append(f"{line} status={m['status']}" if line else f"{key}: not recomputed")
            elif kind == "experiments":
                lines.append(f"STORE {key} status={m['status']} (no frozen analysis; metrics not published)")
    for path in sorted((store / "releases").glob("*.json")):
        release = json.loads(path.read_text(encoding="utf-8"))
        for experiment_id, pin in release["experiments"].items():
            if data_hashes.get(("experiments", experiment_id)) != pin["data_sha256"]:
                problems.append(f"release {release['release_id']}: {experiment_id} data differs from the pinned hash")
        for suite, digest in (release.get("synthetic") or {}).items():
            if data_hashes.get(("synthetic", suite)) != digest:
                problems.append(f"release {release['release_id']}: synthetic {suite} differs from the pinned hash")
        lines.append(f"RELEASE {release['release_id']} experiments={len(release['experiments'])} pinned")
    catalog_path = store / "catalog.json"
    if not catalog_path.is_file() or json.loads(catalog_path.read_text(encoding="utf-8")) != build_catalog(store, registry):
        problems.append("catalog.json is missing or stale (run export to rebuild)")
    return problems, lines


# ---------------------------------------------------------------- commands

def export_all(root: Path = CHECKOUT, *, store: Path | None = None, registry: dict | None = None,
               only: list[str] | None = None, allow_update: bool = False, now: str | None = None) -> list[str]:
    registry = registry or load_registry(root)
    store = store or root / STORE
    lines = []
    for found in discover(root, registry):
        if only and found["experiment_id"] not in only:
            continue
        export, files, inputs = build_experiment_export(root, registry, found)
        outcome = write_entry(store, "experiments", found["experiment_id"], export, files, inputs,
                              allow_update=allow_update, now=now)
        lines.append(f"{outcome} {found['experiment_id']} status={export['status']} "
                     f"slots={export['progress']['evaluated']}/{export['progress']['planned']}")
    if not only:
        for suite, (export, inputs) in build_synthetic_export(root, registry).items():
            lines.append(f"{write_entry(store, 'synthetic', suite, export, {}, inputs, now=now)} synthetic/{suite}")
    write_catalog(store, registry)
    return lines


def check_store(root: Path = CHECKOUT, *, store: Path | None = None, registry: dict | None = None) -> list[str]:
    """Re-export from results/ in memory; finalized experiments must match the stored data hash."""
    registry = registry or load_registry(root)
    store = store or root / STORE
    problems = []
    for found in discover(root, registry):
        export, files, _ = build_experiment_export(root, registry, found)
        _, data_sha, _ = _hashes(export, files)
        manifest_path = store / "experiments" / found["experiment_id"] / "MANIFEST.json"
        if not manifest_path.is_file():
            problems.append(f"{found['experiment_id']}: discovered but not exported")
            continue
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
        if stored["data_sha256"] != data_sha:
            problems.append(f"{found['experiment_id']}: stored data differs from a fresh export ({stored['status']})")
        if stored["status"] != export["status"]:
            problems.append(f"{found['experiment_id']}: stored status {stored['status']} != current {export['status']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    exp = sub.add_parser("export")
    exp.add_argument("--experiment", action="append")
    exp.add_argument("--allow-update", action="store_true", help="overwrite finalized data (avoid; prefer a new ID)")
    rel = sub.add_parser("release")
    rel.add_argument("--id", required=True)
    rel.add_argument("--label", required=True)
    rel.add_argument("--kind", choices=("submission", "presentation", "interim"), required=True)
    rel.add_argument("--experiments", required=True)
    rel.add_argument("--primary", required=True)
    rel.add_argument("--verification", type=Path)
    rel.add_argument("--note")
    sub.add_parser("verify")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    registry = load_registry()
    store = CHECKOUT / STORE
    try:
        if args.command == "discover":
            for found in discover(CHECKOUT, registry):
                _, assessed = assess(CHECKOUT, found["raw"])
                status, source, warnings = resolve_status(registry["experiments"].get(found["experiment_id"]),
                                                          assessed["progress"], found["analysis_dir"])
                p = assessed["progress"]
                print(f"{found['experiment_id']} status={status} ({source}) evaluated={p['evaluated']}/{p['planned']} "
                      f"analysis={found['analysis_dir']} {' '.join(warnings)}".rstrip())
            return 0
        if args.command == "export":
            for line in export_all(CHECKOUT, store=store, registry=registry, only=args.experiment,
                                   allow_update=args.allow_update):
                print(line)
            print("PUBLIC_STORE_EXPORTED", (store / "catalog.json").relative_to(CHECKOUT).as_posix())
            print("STATIC_SITE_WRITTEN", " ".join(static_site.build(CHECKOUT)))
            return 0
        if args.command == "release":
            release = create_release(store, args.id, label=args.label, kind=args.kind,
                                     experiments=[e.strip() for e in args.experiments.split(",") if e.strip()],
                                     primary=args.primary, verification=args.verification, note=args.note)
            write_catalog(store, registry)
            print("PUBLIC_RELEASE_CREATED", release["release_id"], release["created_at"])
            print("STATIC_SITE_WRITTEN", " ".join(static_site.build(CHECKOUT)))
            return 0
        if args.command == "verify":
            problems, lines = verify_store(store, registry)
            for line in lines:
                print(line)
            for problem in problems:
                print("PROBLEM", problem)
            print("PUBLIC_STORE_VERIFIED" if not problems else "PUBLIC_STORE_MISMATCH")
            return 0 if not problems else 1
        problems = check_store(CHECKOUT, store=store, registry=registry)
        for problem in problems:
            print("PROBLEM", problem)
        print("PUBLIC_STORE_REPRODUCED" if not problems else "PUBLIC_STORE_DIFFERS")
        return 0 if not problems else 1
    except (StoreError, sb.BundleError, static_site.SiteError) as error:
        print("PUBLIC_STORE_BLOCKED", error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
