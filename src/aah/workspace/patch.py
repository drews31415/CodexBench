"""Content-addressed snapshot storage and independently replayable file patches."""

from __future__ import annotations

import base64
import binascii
import difflib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from aah.domain import FrozenPatch, SnapshotRef, canonical_json_bytes, sha256_bytes

from .snapshot import (File, WorkspaceError, checked_path, disjoint, manifest, read_base,
                       read_regular, read_tree, relative_path, write_tree)


def store_bytes(root: Path, ref: str, data: bytes) -> None:
    path = checked_path(root / relative_path(ref), allow_missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if read_regular(path)[0] != data:
            raise WorkspaceError("Existing artifact failed its content check")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        try:
            tmp.write(data)
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def digest_ref(kind: str, digest: str) -> str:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WorkspaceError("Expected a SHA-256 digest")
    return f"{kind}/{digest}" + ("" if kind == "blobs" else ".json")


def verified_bytes(root: Path, kind: str, digest: str) -> bytes:
    data, _ = read_regular(root / digest_ref(kind, digest))
    if sha256_bytes(data) != digest:
        raise WorkspaceError("Artifact hash mismatch")
    return data


def read_manifest(root: Path, ref: SnapshotRef) -> dict:
    if ref.manifest_ref != digest_ref("snapshots", ref.snapshot_id):
        raise WorkspaceError("Invalid snapshot reference")
    obj = json.loads(verified_bytes(root, "snapshots", ref.snapshot_id))
    if (obj["schema_version"] != "1.0" or obj["repo_id"] != ref.repo_id
            or obj["base_commit"] != ref.base_commit or obj["complete"] is not ref.complete):
        raise WorkspaceError("Snapshot identity mismatch")
    return obj


def read_snapshot_file(artifact_root: Path, snapshot: SnapshotRef, path: str) -> bytes:
    path = relative_path(path)
    obj = read_manifest(artifact_root, snapshot)
    if not obj["complete"]:
        raise WorkspaceError("Incomplete snapshot")
    for entry in obj["files"]:
        if entry["path"] == path and entry["state"] == "present":
            return verified_bytes(artifact_root, "blobs", entry["sha256"])
    raise WorkspaceError("File does not exist in snapshot")


def line_changes(path: str, old: bytes, new: bytes) -> list[dict]:
    if b"\0" in old or b"\0" in new:
        return []
    try:
        a, b = old.decode("utf-8").splitlines(True), new.decode("utf-8").splitlines(True)
    except UnicodeDecodeError:
        return []
    return [
        {"path": path, "old_start": i + 1 if i < j else i, "old_count": j - i,
         "new_start": k + 1 if k < end else k, "new_count": end - k}
        for tag, i, j, k, end in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if tag != "equal"
    ]


def make_patch(root: Path, before: SnapshotRef, after: SnapshotRef) -> tuple[dict, list[dict]]:
    first, last = read_manifest(root, before), read_manifest(root, after)
    if any(first[key] != last[key] for key in ("repo_id", "base_commit", "scope_id")):
        raise WorkspaceError("Snapshot origins differ")
    old = {e["path"]: e for e in first["files"] if e["state"] == "present"}
    new = {e["path"]: e for e in last["files"] if e["state"] == "present"}
    complete = before.complete and after.complete
    changes, hunks = [], []
    # Incomplete captures do not establish additions or deletions.
    if complete:
        for path in sorted(old.keys() | new.keys()):
            a, b = old.get(path), new.get(path)
            if a == b:
                continue
            prior = verified_bytes(root, "blobs", a["sha256"]) if a else b""
            data = verified_bytes(root, "blobs", b["sha256"]) if b else b""
            changes.append({"path": path, "before_hash": a["sha256"] if a else None,
                            "before_mode": a["mode"] if a else None, "mode": b["mode"] if b else None,
                            "content": base64.b64encode(data).decode("ascii") if b else None})
            hunks.extend(line_changes(path, prior, data))
    return {"schema_version": "1.0", "repo_id": before.repo_id, "base_commit": before.base_commit,
            "before_snapshot_id": before.snapshot_id, "after_snapshot_id": after.snapshot_id,
            "applicable": complete, "changes": changes}, hunks


def apply_frozen_patch(base_export: Path, patch: FrozenPatch, artifact_root: Path,
                       destination: Path, *, repo_id: str) -> SnapshotRef:
    """Validate the clean base, patch and resulting snapshot before publishing a new tree."""
    from datetime import datetime, timezone

    source, store = checked_path(base_export), checked_path(artifact_root)
    dest = checked_path(destination, allow_missing=True)
    disjoint(source, dest)
    disjoint(store, dest)
    disjoint(source, store)
    if dest.exists() or not dest.parent.is_dir():
        raise WorkspaceError("Replay needs a fresh destination in an existing directory")
    if not patch.applicable or patch.patch_ref != digest_ref("patches", patch.patch_hash):
        raise WorkspaceError("Patch cannot be applied")
    payload = json.loads(verified_bytes(store, "patches", patch.patch_hash))
    base = read_base(source, patch.base_commit)
    baseline = sha256_bytes(canonical_json_bytes(manifest(repo_id, patch.base_commit, base, base, set())))
    if (payload.get("schema_version") != "1.0" or payload.get("repo_id") != repo_id
            or payload.get("applicable") is not True or payload.get("before_snapshot_id") != baseline
            or payload.get("base_commit") != patch.base_commit
            or payload.get("after_snapshot_id") != patch.snapshot_id):
        raise WorkspaceError("Patch origin or target mismatch")
    files = dict(base)
    changes = payload["changes"]
    paths = tuple(relative_path(change["path"]) for change in changes)
    if paths != tuple(sorted(set(paths))) or paths != tuple(patch.changed_paths):
        raise WorkspaceError("Patch change list mismatch")
    for path, change in zip(paths, changes, strict=True):
        old = base.get(path)
        if ((old.digest if old else None) != change["before_hash"]
                or (old.mode if old else None) != change["before_mode"]):
            raise WorkspaceError("Patch base file mismatch")
        if change["content"] is None:
            if old is None or change["mode"] is not None:
                raise WorkspaceError("Invalid deletion")
            del files[path]
        else:
            if change["mode"] not in {"100644", "100755"}:
                raise WorkspaceError("Invalid patch file mode")
            try:
                data = base64.b64decode(change["content"], validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise WorkspaceError("Invalid patch content") from exc
            files[path] = File(data, change["mode"])
    expected = canonical_json_bytes(manifest(repo_id, patch.base_commit, base, files, set()))
    if sha256_bytes(expected) != patch.snapshot_id:
        raise WorkspaceError("Replayed snapshot differs from frozen snapshot")
    # Stage on the same volume and verify the actual files before making them visible.
    stage = Path(tempfile.mkdtemp(prefix=".aah-replay-", dir=dest.parent))
    try:
        write_tree(stage, files)
        captured, _ = read_tree(stage, base=base)
        if captured != files:
            raise WorkspaceError("Materialized files differ from the frozen bytes or modes")
        stage.rename(dest)
    finally:
        if stage.exists():
            checked_path(stage)
            shutil.rmtree(stage)
    return SnapshotRef(repo_id=repo_id, snapshot_id=patch.snapshot_id, base_commit=patch.base_commit,
                       manifest_ref=digest_ref("snapshots", patch.snapshot_id),
                       captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                       complete=True)
