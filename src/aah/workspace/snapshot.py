"""Capture the approved public file scope without executing repository code."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from aah.domain import canonical_json_bytes, sha256_bytes


class WorkspaceError(ValueError):
    """The requested workspace operation could not be verified."""


SCOPE_ID = "public-files-v1"
EXCLUDED = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".ssh", ".aws", ".codex", "private",
}


def excluded(name: str) -> bool:
    return name.casefold() in EXCLUDED or name.casefold().startswith(".env")


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise WorkspaceError("Expected a relative POSIX path")
    for part in value.split("/"):
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or PureWindowsPath(part).is_reserved() or excluded(part)):
            raise WorkspaceError("Unsupported or out-of-scope path")
    return value


def public_path(path: str) -> bool:
    """Paths eligible for public policy evidence, inside the approved snapshot."""
    try:
        relative_path(path)
    except (TypeError, ValueError):
        return False
    return not any(part.casefold() in {"evaluation", "restricted", "results"} for part in path.split("/"))


def link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def checked_path(path: Path, *, allow_missing: bool = False) -> Path:
    path = Path(path).absolute()
    if ".." in path.parts:
        raise WorkspaceError("Parent traversal is forbidden")
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if allow_missing:
                break
            raise WorkspaceError("Expected path is missing") from None
        if link(info):
            raise WorkspaceError("Linked paths cannot be used")
    return path.resolve(strict=not allow_missing)


def disjoint(first: Path, second: Path) -> None:
    if first.is_relative_to(second) or second.is_relative_to(first):
        raise WorkspaceError("Public source, work trees and artifacts must be separate")


def read_regular(path: Path) -> tuple[bytes, str]:
    checked_path(path)
    before = path.lstat()
    if before.st_nlink != 1 or not stat.S_ISREG(before.st_mode):
        raise WorkspaceError("Only single-link regular files are supported")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags | getattr(os, "O_NONBLOCK", 0)), "rb") as file:
        opened = os.fstat(file.fileno())
        if not os.path.samestat(before, opened):
            raise WorkspaceError("File identity changed while opening")
        data = file.read()
        after = os.fstat(file.fileno())
    if (after.st_nlink != 1 or before.st_size != len(data)
            or before.st_mtime_ns != after.st_mtime_ns or before.st_mode != after.st_mode
            or not os.path.samestat(after, checked_path(path).lstat())):
        raise WorkspaceError("File changed during capture")
    return data, "100755" if before.st_mode & stat.S_IXUSR else "100644"


@dataclass(frozen=True)
class File:
    data: bytes
    mode: str

    @property
    def digest(self) -> str:
        return sha256_bytes(self.data)


def read_tree(root: Path, *, strict: bool = True,
              base: dict[str, File] | None = None) -> tuple[dict[str, File], set[str]]:
    root = checked_path(root)
    files: dict[str, File] = {}
    unavailable: set[str] = set()

    def visit(folder: Path) -> None:
        checked_path(folder)
        seen: set[str] = set()
        for entry in sorted(folder.iterdir()):
            if excluded(entry.name):
                continue
            rel = relative_path(entry.relative_to(root).as_posix())
            if entry.name.casefold() in seen:
                raise WorkspaceError("Case-colliding paths in snapshot")
            seen.add(entry.name.casefold())
            try:
                info = entry.lstat()
                if link(info):
                    raise WorkspaceError("Symlinks and reparse points are unsupported")
                if stat.S_ISDIR(info.st_mode):
                    visit(entry)
                else:
                    data, mode = read_regular(entry)
                    if os.name == "nt" and base and rel in base:
                        mode = base[rel].mode  # Windows does not expose Git's executable bit.
                    files[rel] = File(data, mode)
            except (OSError, WorkspaceError):
                if strict:
                    raise
                unavailable.add(rel)

    try:
        visit(root)
    except OSError:
        if strict:
            raise
        unavailable.add("")
    return files, unavailable


def export_revision(source: Path) -> str:
    """Content pin for a public directory export that has no Git metadata."""
    files, _ = read_tree(source)
    return revision(files)


def revision(files: dict[str, File]) -> str:
    return sha256_bytes(canonical_json_bytes([
        [path, item.mode, item.digest] for path, item in sorted(files.items())
    ]))


def git_files(source: Path, commit: str) -> dict[str, File]:
    """Read commit objects, bypassing working-tree edits, checkout filters and hooks."""
    gitdir = checked_path(source / ".git")
    if not gitdir.is_dir() or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise WorkspaceError("Expected a local public Git repository and a full commit ID")
    objects = checked_path(gitdir / "objects")
    if os.path.lexists(objects / "info/alternates"):
        raise WorkspaceError("External Git object stores are unsupported")
    # Git reads from the object directory itself. Reject filesystem indirection there
    # before allowing the trusted binary to traverse it. The public source is read-only.
    for folder, dirs, names in os.walk(objects, onerror=_raise_io):
        for name in (*dirs, *names):
            info = (Path(folder) / name).lstat()
            if link(info) or (not stat.S_ISDIR(info.st_mode)
                              and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                raise WorkspaceError("Public Git object store contains an unsupported entry")
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if k in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               GIT_ALLOW_PROTOCOL="", GIT_OBJECT_DIRECTORY=str(objects))
    executable = shutil.which("git")
    if executable is None:
        raise WorkspaceError("Git is required to read a pinned commit")
    # Git must not load the target repository's config, includes, refs, hooks or remotes.
    with tempfile.TemporaryDirectory(prefix="aah-git-reader-") as tmp:
        control = Path(tmp)
        (control / "objects").mkdir()
        (control / "refs").mkdir()
        (control / "HEAD").write_bytes(b"ref: refs/heads/unused\n")
        if len(commit) == 64:
            (control / "config").write_bytes(
                b"[core]\nrepositoryformatversion=1\n[extensions]\nobjectFormat=sha256\n")

        def git(*args: str, data: bytes = b"") -> bytes:
            result = subprocess.run(
                [executable, f"--git-dir={control}", *args], env=env, cwd=control,
                input=data, capture_output=True, timeout=30, check=False,
            )
            if result.returncode:
                raise WorkspaceError("Cannot read the pinned public Git object")
            return result.stdout

        if git("cat-file", "-t", commit).strip() != b"commit":
            raise WorkspaceError("Revision does not identify a commit")
        entries = git_entries(git("ls-tree", "-rz", "--full-tree", commit))
        ids = sorted({oid for _, oid in entries.values()})
        batch = git("cat-file", "--batch", data=b"".join(oid.encode() + b"\n" for oid in ids))
    blobs: dict[str, bytes] = {}
    offset = 0
    for oid in ids:
        end = batch.find(b"\n", offset)
        fields = batch[offset:end].split()
        if end < 0 or len(fields) != 3 or fields[0] != oid.encode() or fields[1] != b"blob":
            raise WorkspaceError("Git blob batch did not match the requested objects")
        length = int(fields[2])
        offset = end + 1 + length
        if length < 0 or batch[offset:offset + 1] != b"\n":
            raise WorkspaceError("Truncated Git blob batch")
        content = batch[end + 1:offset]
        digest = hashlib.new("sha1" if len(oid) == 40 else "sha256")
        digest.update(f"blob {length}\0".encode())
        digest.update(content)
        if digest.hexdigest() != oid:
            raise WorkspaceError("Git blob content does not match the pinned tree")
        blobs[oid] = content
        offset += 1
    if offset != len(batch):
        raise WorkspaceError("Unexpected trailing Git output")
    return {path: File(blobs[oid], mode) for path, (mode, oid) in entries.items()}


def _raise_io(error: OSError) -> None:
    raise error


def git_entries(listing: bytes) -> dict[str, tuple[str, str]]:
    result = {}
    names: dict[str, str] = {}
    for row in listing.split(b"\0"):
        if not row:
            continue
        meta, raw = row.split(b"\t", 1)
        mode, kind, oid = meta.decode("ascii").split()
        path = raw.decode("utf-8")
        parts = path.split("/")
        if any(excluded(part) for part in parts):
            continue
        relative_path(path)
        for n in range(1, len(parts) + 1):
            prefix = "/".join(parts[:n])
            if names.setdefault(prefix.casefold(), prefix) != prefix:
                raise WorkspaceError("Case-colliding paths in Git snapshot")
        if mode not in {"100644", "100755"} or kind != "blob":
            raise WorkspaceError("Unsupported Git entry (link or submodule)")
        result[path] = mode, oid
    return result


def read_base(source: Path, commit: str) -> dict[str, File]:
    source = checked_path(source)
    if not source.is_dir():
        raise WorkspaceError("Expected a public source directory")
    if os.path.lexists(source / ".git"):
        return git_files(source, commit)
    files, _ = read_tree(source)
    if revision(files) != commit:
        raise WorkspaceError("Public export differs from its pinned content hash")
    return files


def manifest(repo_id: str, commit: str, base: dict[str, File], files: dict[str, File],
             unavailable: set[str]) -> dict:
    entries = []
    for path in sorted(base.keys() | files.keys() | (unavailable - {""})):
        item = files.get(path)
        unknown = "" in unavailable or any(
            path == entry or path.startswith(entry + "/") for entry in unavailable)
        if unknown:
            item = None
        state = "unavailable" if unknown else "present" if item else "deleted"
        entries.append({"path": path, "state": state, "tracked": path in base,
                        "sha256": item.digest if item else None, "mode": item.mode if item else None})
    return {"schema_version": "1.0", "repo_id": repo_id, "base_commit": commit,
            "scope_id": SCOPE_ID, "files": entries, "complete": not unavailable}


def write_tree(destination: Path, files: dict[str, File]) -> None:
    # The caller owns a fresh, unpublished directory.
    for name, item in sorted(files.items()):
        path = destination / relative_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(item.data)
        if os.name != "nt":
            path.chmod(0o755 if item.mode == "100755" else 0o644)
