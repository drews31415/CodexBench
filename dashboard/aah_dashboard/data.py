"""Read the versioned public store. No Streamlit import, no network, paths relative to this file.

Caching: every read hashes the file bytes and parses once per (path, SHA-256). A new commit with new data
therefore always shows the new data, even when the server process is reused across deployments.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path

SUPPORTED_MAJOR = "1"
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "public_data"
LATEST = "__latest__"
_CACHE: "OrderedDict[tuple[str, str], object]" = OrderedDict()
_CACHE_LIMIT = 24


class DataError(RuntimeError):
    """The public store is missing, unreadable, or fails an integrity check."""


def store_root() -> Path:
    override = os.environ.get("AAH_PUBLIC_DATA")
    return Path(override) if override else DEFAULT_ROOT


def read_json(path: Path) -> tuple[object, str]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise DataError(f"읽을 수 없음: {path.name} ({type(error).__name__})") from error
    digest = hashlib.sha256(data).hexdigest()
    key = (str(path), digest)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key], digest
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise DataError(f"JSON 형식 오류: {path.name}") from error
    _CACHE[key] = value
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)
    return value, digest


def clear_cache() -> None:
    _CACHE.clear()


def _check_schema(value: dict, what: str) -> None:
    version = str(value.get("schema_version", ""))
    if version.split(".")[0] != SUPPORTED_MAJOR:
        raise DataError(f"{what}: 지원하지 않는 schema_version {version!r}")


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else store_root()
        if not (self.root / "catalog.json").is_file():
            raise DataError(f"공개 데이터 저장소가 없다: {self.root.name}/catalog.json")

    # -- catalog and releases
    def catalog(self) -> dict:
        catalog, _ = read_json(self.root / "catalog.json")
        _check_schema(catalog, "catalog.json")
        return catalog

    def release(self, release_id: str) -> dict | None:
        if release_id == LATEST:
            return None
        path = self.root / "releases" / f"{release_id}.json"
        if not path.is_file():
            raise DataError(f"release 없음: {release_id}")
        release, _ = read_json(path)
        _check_schema(release, release_id)
        return release

    def release_options(self) -> list[tuple[str, str]]:
        catalog = self.catalog()
        options = [(r["release_id"], f"{r['label']} · {r['kind']}") for r in catalog["releases"]]
        return options + [(LATEST, "최신 데이터 (모든 실험·진행 중 포함)")]

    def experiments_in(self, release_id: str) -> list[dict]:
        """Catalog entries visible in a release (pinned) or in the latest view (all), never merged."""
        catalog = self.catalog()
        entries = {e["key"]: e for e in catalog["experiments"]}
        if release_id == LATEST:
            return list(entries.values())
        release = self.release(release_id)
        out = []
        for key, pin in release["experiments"].items():
            entry = dict(entries.get(key) or {"key": key, "status": "MISSING", "status_label": "저장소에 없음"})
            entry["pin_ok"] = entry.get("data_sha256") == pin["data_sha256"]
            entry["status_at_release"] = pin.get("status_at_release")
            out.append(entry)
        primary = release.get("primary")
        return sorted(out, key=lambda e: (e["key"] != primary, e["key"]))

    # -- experiment and synthetic payloads (integrity-checked against MANIFEST)
    def _payload(self, kind: str, key: str) -> tuple[dict, dict]:
        folder = self.root / kind / key
        manifest, _ = read_json(folder / "MANIFEST.json")
        export, digest = read_json(folder / "export.json")
        if manifest["files"].get("export.json") != digest:
            raise DataError(f"{key}: export.json이 MANIFEST의 해시와 다르다 (무결성 오류)")
        _check_schema(manifest, key)
        return export, manifest

    def experiment(self, key: str) -> tuple[dict, dict]:
        return self._payload("experiments", key)

    def synthetic(self) -> list[tuple[dict, dict]]:
        return [self._payload("synthetic", s["key"]) for s in self.catalog().get("synthetic", [])]

    def verification(self, release_id: str) -> dict | None:
        release = self.release(release_id) if release_id != LATEST else None
        return (release or {}).get("verification")
