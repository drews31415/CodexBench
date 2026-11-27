"""Strict conversion between immutable domain records and versioned UTF-8 JSON."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import fields
from datetime import datetime
from enum import StrEnum
from functools import cache
from pathlib import Path, PureWindowsPath
from types import MappingProxyType, UnionType
from typing import Annotated, Any, Literal, TypeVar, Union, get_args, get_origin, get_type_hints
from uuid import UUID


SCHEMA_VERSION = "1.0"


class ContractError(ValueError):
    """A value or wire envelope violates its published contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identifier(value: str) -> None:
    if not value.strip():
        raise ContractError("expected a nonempty identifier")


def _uuid(value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise ContractError("expected a UUID") from exc


def _digest(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError("expected a lowercase SHA-256 digest")


def _count(value: int) -> None:
    if value < 0:
        raise ContractError("expected a nonnegative integer")


def _utc(value: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        raise ContractError("expected a UTC ISO 8601 timestamp ending in Z")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("invalid UTC date or time") from exc


def validate_relative_path(value: str) -> None:
    """Check canonical POSIX spelling. The workspace owner verifies actual containment."""
    if (
        "\\" in value
        or "\0" in value
        or PureWindowsPath(value).drive
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ContractError("expected a relative POSIX path")


Identifier = Annotated[str, _identifier]
RunId = Annotated[str, _uuid]
Digest = Annotated[str, _digest]
Count = Annotated[int, _count]
UTC = Annotated[str, _utc]
RelativePath = Annotated[str, validate_relative_path]


def _json_value(value: Any) -> Any:
    """Copy provider-shaped JSON into immutable containers and reject non-JSON values."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ContractError("JSON object keys must be strings")
        return MappingProxyType({key: _json_value(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_json_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("JSON numbers must be finite")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ContractError("expected a JSON value")


@cache
def _field_types(kind: type) -> dict:
    hints = get_type_hints(kind, include_extras=True)
    return {field.name: hints[field.name] for field in fields(kind)}


def _record_from_fields(kind: type, data: Mapping) -> WireRecord:
    expected = _field_types(kind).keys()
    if data.keys() != expected:
        raise ContractError(f"{kind.__name__}: missing or unexpected fields {sorted(data.keys() ^ expected)}")
    return kind(**data)


def _typed(annotation: Any, value: Any) -> Any:
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        value = _typed(args[0], value)
        for check in args[1:]:
            check(value)
        return value
    if origin in (UnionType, Union):
        for option in args:
            try:
                return _typed(option, value)
            except ContractError:
                continue
        raise ContractError("value does not match any permitted type")
    if origin is Literal:
        if value not in args:
            raise ContractError(f"expected one of {args}")
        return value
    if origin is tuple and isinstance(value, (tuple, list)):
        return tuple(_typed(args[0], item) for item in value)
    if origin is Mapping and isinstance(value, Mapping):
        return MappingProxyType({_typed(args[0], k): _typed(args[1], v) for k, v in value.items()})
    if annotation is Any:
        return _json_value(value)
    if annotation is Path:
        # Host-local handles supply a Path object. A serialized path is always portable.
        if isinstance(value, Path):
            return value
        if isinstance(value, str):
            if value != ".":
                validate_relative_path(value)
            return Path(value)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum) and isinstance(value, str):
        try:
            return annotation(value)
        except ValueError as exc:
            raise ContractError(f"unknown {annotation.__name__} value") from exc
    if isinstance(annotation, type) and issubclass(annotation, WireRecord):
        if isinstance(value, annotation):
            return value
        if isinstance(value, Mapping):
            return _record_from_fields(annotation, value)
    if type(value) is annotation:
        return value
    raise ContractError(f"expected {annotation}")


class WireRecord:
    """Frozen dataclass subclasses share strict constructor and decoder validation."""

    @property
    def schema_version(self) -> str:
        return getattr(self, "_wire_version", SCHEMA_VERSION)

    def __post_init__(self) -> None:
        for name, annotation in _field_types(type(self)).items():
            try:
                value = _typed(annotation, getattr(self, name))
            except ContractError as exc:
                raise ContractError(f"{type(self).__name__}.{name}: {exc}") from exc
            object.__setattr__(self, name, value)
        self.validate()

    def validate(self) -> None:
        """Apply relational constraints after normalizing field types."""


def _plain(value: Any) -> Any:
    if isinstance(value, WireRecord):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Path):
        path = value.as_posix()
        if path != ".":
            validate_relative_path(path)
        return path
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ContractError("JSON object keys must be strings")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContractError(f"invalid canonical JSON: {exc}") from exc


def dump_record(record: WireRecord) -> bytes:
    if not isinstance(record, WireRecord):
        raise ContractError("expected a domain record")
    return canonical_json_bytes({"schema_version": record.schema_version, **_plain(record)})


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ContractError("duplicate JSON object key")
        obj[key] = value
    return obj


RecordT = TypeVar("RecordT", bound=WireRecord)


def load_record(kind: type[RecordT], data: bytes) -> RecordT:
    if not isinstance(data, bytes):
        raise ContractError("expected UTF-8 bytes")
    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("invalid UTF-8 JSON") from exc
    # Also rejects overflow to infinity and escaped, unpaired Unicode surrogates.
    canonical_json_bytes(document)
    if not isinstance(document, dict):
        raise ContractError("record envelope must be an object")
    version = document.pop("schema_version", None)
    if not isinstance(version, str) or not re.fullmatch(r"1\.(0|[1-9]\d*)", version):
        raise ContractError("missing or unsupported schema_version")
    record = _record_from_fields(kind, document)
    # Retain a compatible minor version when recomputing an artifact's definition hash.
    object.__setattr__(record, "_wire_version", version)
    return record
