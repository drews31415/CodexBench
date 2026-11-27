"""Filesystem checks with synthetic files, independent of provider or OS sandbox probes."""

from dataclasses import replace
import os
from uuid import uuid4

import pytest

from aah.domain import (Condition, FixedRequirements, RepositoryRef, RunRequest,
                        resolve_condition, sha256_bytes)
from aah.workspace import WorkspaceError, WorkspaceManager, export_revision


def test_public_root_and_managed_handle_boundary(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    work, store = tmp_path / "work", tmp_path / "store"
    manager = WorkspaceManager(public, work, store, quiescence_check=lambda _: True)
    with pytest.raises(WorkspaceError, match="separate"):
        WorkspaceManager(public, public / "work", store)
    pin = export_revision(public)
    empty = sha256_bytes(b"")
    req = RunRequest(str(uuid4()), None, None, RepositoryRef("sample", public, pin),
                     FixedRequirements("edit", "", empty, "offline", ()),
                     resolve_condition(Condition.B), empty, "user")
    with pytest.raises(WorkspaceError, match="outside"):
        manager.prepare(replace(req, repository=replace(req.repository, source_path=tmp_path)))
    handle = manager.prepare(req)
    with pytest.raises(WorkspaceError, match="handle"):
        manager.cleanup(replace(handle, root=public))
    assert public.is_dir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"sentinel")
    try:
        os.link(outside, handle.root / "linked.txt")
    except PermissionError:
        # This host can itself deny creating a link. Verify the object never appeared.
        assert not os.path.lexists(handle.root / "linked.txt")
    else:
        snapshot = manager.snapshot(handle)
        assert not snapshot.complete
        assert not manager.freeze_patch(handle, snapshot).applicable
    assert not (store / "blobs" / sha256_bytes(b"sentinel")).exists()
    manager.cleanup(handle)
    assert outside.read_bytes() == b"sentinel"
