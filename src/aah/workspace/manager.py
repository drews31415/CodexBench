"""Prepare per-run public copies and retain content-addressed artifacts outside them."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from aah.domain import (CleanupResult, DiffRecord, FrozenPatch, RunRequest, SnapshotRef,
                        WorkspaceHandle, canonical_json_bytes, sha256_bytes)

from .patch import apply_frozen_patch, digest_ref, make_patch, store_bytes
from .snapshot import (File, WorkspaceError, checked_path, disjoint, manifest, read_base,
                       read_tree, write_tree)


@dataclass
class _Run:
    handle: WorkspaceHandle
    identity: os.stat_result
    repo_id: str
    base: dict[str, File]
    initial: SnapshotRef
    cleaned: bool = False


class WorkspaceManager:
    """An offline copy manager. A runtime owner separately verifies process isolation."""

    def __init__(self, public_root: Path, work_root: Path, artifact_root: Path, *,
                 quiescence_check: Callable[[WorkspaceHandle], bool] | None = None):
        self.public_root = checked_path(public_root)
        self.work_root = checked_path(work_root, allow_missing=True)
        self.artifact_root = checked_path(artifact_root, allow_missing=True)
        for a, b in ((self.public_root, self.work_root), (self.public_root, self.artifact_root),
                     (self.work_root, self.artifact_root)):
            disjoint(a, b)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._quiet = quiescence_check
        self._runs: dict[str, _Run] = {}

    def _save(self, repo_id: str, commit: str, base: dict[str, File], files: dict[str, File],
              unavailable: set[str]) -> SnapshotRef:
        body = canonical_json_bytes(manifest(repo_id, commit, base, files, unavailable))
        snapshot_id = sha256_bytes(body)
        ref = digest_ref("snapshots", snapshot_id)
        for item in files.values():
            store_bytes(self.artifact_root, digest_ref("blobs", item.digest), item.data)
        store_bytes(self.artifact_root, ref, body)
        return SnapshotRef(repo_id=repo_id, snapshot_id=snapshot_id, base_commit=commit,
                           manifest_ref=ref, complete=not unavailable,
                           captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def prepare(self, request: RunRequest, *, checkpoint_patch: FrozenPatch | None = None) -> WorkspaceHandle:
        rid = request.run_id
        if str(UUID(rid)) != rid or rid in self._runs:
            raise WorkspaceError("Expected an unused canonical run UUID")
        repo = request.repository
        source = checked_path(repo.source_path)
        if not source.is_relative_to(self.public_root):
            raise WorkspaceError("Source is outside the approved public root")
        base = read_base(source, repo.initial_commit)
        root = checked_path(self.work_root / rid, allow_missing=True)
        if checkpoint_patch is None:
            root.mkdir()
        else:
            apply_frozen_patch(source, checkpoint_patch, self.artifact_root, root, repo_id=repo.repo_id)
        identity = root.stat()
        try:
            if checkpoint_patch is None:
                write_tree(root, base)
                copied, _ = read_tree(root, base=base)
                if copied != base:
                    raise WorkspaceError("Initial workspace does not match the pinned base")
            initial = self._save(repo.repo_id, repo.initial_commit, base, base, set())
            handle = WorkspaceHandle(run_id=rid, root=root, initial_commit=repo.initial_commit,
                                     isolation_profile_id="public-copy-only", verified=False)
            self._runs[rid] = _Run(handle, identity, repo.repo_id, base, initial)
            return handle
        except BaseException:
            if os.path.samestat(checked_path(root).stat(), identity):
                shutil.rmtree(root)
            raise

    def _owned(self, workspace: WorkspaceHandle) -> _Run:
        run = self._runs.get(workspace.run_id)
        if run is None or run.handle != workspace or run.cleaned:
            raise WorkspaceError("Unknown or inactive workspace handle")
        root = checked_path(workspace.root)
        if root.parent != self.work_root or not os.path.samestat(root.stat(), run.identity):
            raise WorkspaceError("Workspace identity changed")
        return run

    def snapshot(self, workspace: WorkspaceHandle) -> SnapshotRef:
        run = self._owned(workspace)
        files, unknown = read_tree(workspace.root, strict=False, base=run.base)
        return self._save(run.repo_id, workspace.initial_commit, run.base, files, unknown)

    def diff(self, before: SnapshotRef, after: SnapshotRef) -> DiffRecord:
        patch, hunks = make_patch(self.artifact_root, before, after)
        body = canonical_json_bytes(patch)
        digest = sha256_bytes(body)
        ref = digest_ref("patches", digest)
        store_bytes(self.artifact_root, ref, body)
        return DiffRecord(diff_id=digest, before_snapshot_id=before.snapshot_id,
                          after_snapshot_id=after.snapshot_id,
                          changed_paths=tuple(change["path"] for change in patch["changes"]),
                          hunks=tuple(hunks), patch_ref=ref, complete=patch["applicable"])

    def freeze_patch(self, workspace: WorkspaceHandle, snapshot: SnapshotRef) -> FrozenPatch:
        run = self._owned(workspace)
        if self._quiet is None or self._quiet(workspace) is not True:
            raise WorkspaceError("Process quiescence must be confirmed before freezing")
        current = self.snapshot(workspace)
        if (snapshot.snapshot_id != current.snapshot_id or snapshot.repo_id != current.repo_id
                or snapshot.base_commit != current.base_commit or snapshot.complete != current.complete
                or snapshot.manifest_ref != current.manifest_ref):
            raise WorkspaceError("Snapshot no longer matches the workspace")
        diff = self.diff(run.initial, current)
        return FrozenPatch(run_id=workspace.run_id, base_commit=current.base_commit,
                           snapshot_id=current.snapshot_id, patch_ref=diff.patch_ref,
                           patch_hash=diff.diff_id, changed_paths=diff.changed_paths,
                           applicable=diff.complete)

    def cleanup(self, workspace: WorkspaceHandle) -> CleanupResult:
        run = self._runs.get(workspace.run_id)
        if run and run.cleaned and run.handle == workspace:
            return CleanupResult(confirmed=True, remaining_process_count=None, error=None)
        run = self._owned(workspace)
        if self._quiet is None or self._quiet(workspace) is not True:
            raise WorkspaceError("Process quiescence must be confirmed before cleanup")
        shutil.rmtree(workspace.root)
        run.cleaned = True
        run.base.clear()
        return CleanupResult(confirmed=True, remaining_process_count=None, error=None)
