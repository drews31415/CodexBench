from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import zlib

import pytest

from aah.domain import (Condition, FixedRequirements, RepositoryRef, RunRequest,
                        resolve_condition, sha256_bytes)
from aah.workspace import (WorkspaceError, WorkspaceManager, apply_frozen_patch,
                           export_revision, read_snapshot_file)


def request(source: Path, pin: str) -> RunRequest:
    empty = sha256_bytes(b"")
    return RunRequest(str(uuid4()), None, None, RepositoryRef("sample", source, pin),
                      FixedRequirements("edit", "", empty, "offline", ()),
                      resolve_condition(Condition.A), empty, "user")


@pytest.fixture
def example(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "legacy.py").write_bytes(b"def step():\n    pass\n")
    (source / "keep.txt").write_bytes(b"a\r\nb\r\n")
    artifacts = tmp_path / "artifacts"
    manager = WorkspaceManager(source, tmp_path / "work", artifacts, quiescence_check=lambda _: True)
    req = request(source, export_revision(source))
    return manager, manager.prepare(req), req, artifacts


def test_ut01_snapshot_stable_across_order_mtime_and_runs(example):
    manager, workspace, req, store = example
    before = manager.snapshot(workspace)
    source = req.repository.source_path
    file = source / "legacy.py"
    data = file.read_bytes()
    file.unlink()
    file.write_bytes(data)
    file.touch()
    other = manager.prepare(replace(req, run_id=str(uuid4())))
    assert other.root != workspace.root
    after = manager.snapshot(other)
    assert before.snapshot_id == after.snapshot_id
    assert read_snapshot_file(store, before, "legacy.py") == data
    (other.root / "keep.txt").write_bytes(b"changed\n")
    assert (workspace.root / "keep.txt").read_bytes() == b"a\r\nb\r\n"
    assert (source / "keep.txt").read_bytes() == b"a\r\nb\r\n"
    assert not workspace.verified


def test_ut02_move_and_binary_additions_replay_from_base(example, tmp_path):
    manager, workspace, req, store = example
    before = manager.snapshot(workspace)
    (workspace.root / "pkg").mkdir()
    (workspace.root / "legacy.py").rename(workspace.root / "pkg" / "moved.py")
    (workspace.root / "new.bin").write_bytes(b"\0\xff")
    (workspace.root / "empty.file").touch()
    (workspace.root / "keep.txt").write_bytes(b"a\r\nthird")
    after = manager.snapshot(workspace)
    diff = manager.diff(before, after)
    assert diff.changed_paths == ("empty.file", "keep.txt", "legacy.py", "new.bin", "pkg/moved.py")
    assert [h for h in diff.hunks if h["path"] == "legacy.py"] == [
        {"path": "legacy.py", "old_start": 1, "old_count": 2, "new_start": 0, "new_count": 0}
    ]
    assert [h for h in diff.hunks if h["path"] == "pkg/moved.py"] == [
        {"path": "pkg/moved.py", "old_start": 0, "old_count": 0, "new_start": 1, "new_count": 2}
    ]
    entries = {e["path"]: e for e in json.loads((store / after.manifest_ref).read_bytes())["files"]}
    assert entries["legacy.py"]["state"] == "deleted"
    assert entries["pkg/moved.py"]["tracked"] is False
    assert read_snapshot_file(store, before, "legacy.py") == b"def step():\n    pass\n"
    frozen = manager.freeze_patch(workspace, after)
    assert frozen.applicable and frozen.changed_paths == diff.changed_paths
    assert frozen.patch_hash == manager.freeze_patch(workspace, after).patch_hash
    assert manager.cleanup(workspace).confirmed and manager.cleanup(workspace).confirmed
    assert not workspace.root.exists()
    dest = tmp_path / "fresh"
    replay = apply_frozen_patch(req.repository.source_path, frozen, store, dest, repo_id="sample")
    assert replay.snapshot_id == after.snapshot_id
    assert (dest / "pkg" / "moved.py").read_bytes() == b"def step():\n    pass\n"
    assert (dest / "new.bin").read_bytes() == b"\0\xff"
    assert (dest / "empty.file").stat().st_size == 0
    assert not (dest / "legacy.py").exists()
    assert export_revision(req.repository.source_path) == req.repository.initial_commit


def test_changed_scope_and_quiescence(example, tmp_path):
    manager, workspace, req, _ = example
    initial = manager.snapshot(workspace)
    (workspace.root / "__pycache__").mkdir()
    (workspace.root / "__pycache__" / "cache").write_bytes(b"ignore")
    assert manager.snapshot(workspace).snapshot_id == initial.snapshot_id
    (workspace.root / "new.txt").touch()
    with pytest.raises(WorkspaceError, match="no longer matches"):
        manager.freeze_patch(workspace, initial)
    manager2 = WorkspaceManager(req.repository.source_path, tmp_path / "work2", tmp_path / "store2")
    ws = manager2.prepare(req)
    with pytest.raises(WorkspaceError, match="quiescence"):
        manager2.freeze_patch(ws, manager2.snapshot(ws))
    with pytest.raises(WorkspaceError, match="quiescence"):
        manager2.cleanup(ws)
    assert ws.root.is_dir()


def test_pinned_git_objects_exclude_worktree_edits_and_out_of_scope_files(tmp_path):
    # Build a tiny public Git object fixture without any local identity, hooks or Git writes.
    public = tmp_path / "public"
    db = public / ".git"
    (db / "refs" / "heads").mkdir(parents=True)
    (db / "HEAD").write_bytes(b"ref: refs/heads/fixture\n")
    (db / "config").write_bytes(b"invalid source-specific git config {{{\n")

    def object_id(kind, data):
        raw = f"{kind} {len(data)}\0".encode() + data
        oid = hashlib.sha1(raw).hexdigest()
        target = db / "objects" / oid[:2] / oid[2:]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zlib.compress(raw))
        return oid

    blob = object_id("blob", b"public\n")
    tree = object_id("tree", b"100644 .env.local\0" + bytes.fromhex(blob)
                     + b"100755 task.py\0" + bytes.fromhex(blob))
    pin = object_id("commit", f"tree {tree}\nauthor A <a@local> 0 +0000\n"
                    f"committer A <a@local> 0 +0000\n\nfixture\n".encode())
    (public / "task.py").write_bytes(b"local edits")
    (public / "untracked.txt").touch()
    manager = WorkspaceManager(public, tmp_path / "work", tmp_path / "store",
                               quiescence_check=lambda _: True)
    ws = manager.prepare(request(public, pin))
    assert [p.name for p in ws.root.iterdir()] == ["task.py"]
    assert (ws.root / "task.py").read_bytes() == b"public\n"
    assert (public / "task.py").read_bytes() == b"local edits"
    (ws.root / "new.txt").touch()
    frozen = manager.freeze_patch(ws, manager.snapshot(ws))
    dest = tmp_path / "replayed"
    apply_frozen_patch(public, frozen, tmp_path / "store", dest, repo_id="sample")
    assert (dest / "task.py").read_bytes() == b"public\n"
    assert (dest / "new.txt").exists()


def test_tampering_or_wrong_base_prevents_replay(example, tmp_path):
    manager, ws, req, store = example
    ref = manager.snapshot(ws)
    patch = manager.freeze_patch(ws, ref)
    path = store / patch.patch_ref
    original = path.read_bytes()
    path.write_bytes(b"invalid")
    dest = tmp_path / "replay"
    with pytest.raises(WorkspaceError, match="hash"):
        apply_frozen_patch(req.repository.source_path, patch, store, dest, repo_id="sample")
    assert not dest.exists()
    path.write_bytes(original)
    (req.repository.source_path / "keep.txt").write_bytes(b"altered source")
    with pytest.raises(WorkspaceError, match="pinned"):
        apply_frozen_patch(req.repository.source_path, patch, store, dest, repo_id="sample")
    assert not dest.exists()


def test_multilink_metadata_and_unreadable_file_are_never_frozen(example, monkeypatch):
    manager, workspace, _, store = example
    target = workspace.root / "keep.txt"
    target.write_bytes(b"unavailable")
    real_lstat = Path.lstat

    def synthetic_multilink(path):
        value = real_lstat(path)
        if path == target:
            return SimpleNamespace(st_mode=value.st_mode, st_nlink=2, st_file_attributes=0)
        return value

    monkeypatch.setattr(Path, "lstat", synthetic_multilink)
    snapshot = manager.snapshot(workspace)
    assert not snapshot.complete
    entries = json.loads((store / snapshot.manifest_ref).read_bytes())["files"]
    assert next(e for e in entries if e["path"] == "keep.txt")["state"] == "unavailable"
    assert not (store / "blobs" / sha256_bytes(b"unavailable")).exists()
    frozen = manager.freeze_patch(workspace, snapshot)
    assert not frozen.applicable
