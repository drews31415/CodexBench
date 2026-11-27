"""Read the metadata probe profile. Live execution requires a separate profile."""

import json
from pathlib import Path

from aah.domain import canonical_json_bytes, sha256_bytes
from aah.runtime import RuntimeConfig


def _unique_keys(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate config field: {key}")
        result[key] = value
    return result


def load_probe_config(path: Path) -> tuple[RuntimeConfig, str]:
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    if not isinstance(data, dict) or set(data) != {"schema_version", "runtime"}:
        raise ValueError("expected schema_version and runtime in the probe profile")
    if data["schema_version"] != "1.0":
        raise ValueError("unsupported probe config version")
    runtime = data["runtime"]
    fields = {"adapter", "runtime_version", "model_id", "reasoning_effort"}
    if not isinstance(runtime, dict) or set(runtime) != fields:
        raise ValueError(f"expected runtime fields: {', '.join(sorted(fields))}")
    if runtime["adapter"] != "codex_cli":
        raise ValueError("the S0 metadata probe requires codex_cli")
    for name in fields - {"adapter"}:
        value = runtime[name]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be a nonempty string or null")
    return RuntimeConfig(**runtime), sha256_bytes(canonical_json_bytes(data))
