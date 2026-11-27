import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aah import cli
from aah.cli.config import load_probe_config
from aah.domain import canonical_json_bytes, sha256_bytes


PROFILE = Path(__file__).resolve().parents[2] / "configs" / "s0-probe.json"


def test_checked_in_probe_profile_is_unpinned_and_has_canonical_hash():
    config, config_hash = load_probe_config(PROFILE)
    assert config.model_id is None
    assert config.runtime_version is None
    assert config_hash == sha256_bytes(canonical_json_bytes(json.loads(PROFILE.read_bytes())))


@pytest.mark.parametrize("change", [
    lambda text: text.replace('"1.0"', '"2.0"'),
    lambda text: text.replace('"runtime":', '"runtime": {}, "runtime":'),
    lambda text: text.replace('"model_id"', '"hidden_model_override"'),
    lambda text: text.replace('"model_id": null', '"model_id": 0'),
    lambda text: text.replace('"adapter": "codex_cli"', '"adapter": "shell"'),
])
def test_invalid_profile_never_starts_probe(tmp_path, monkeypatch, change):
    profile = tmp_path / "invalid.json"
    profile.write_text(change(PROFILE.read_text(encoding="utf-8")), encoding="utf-8")
    monkeypatch.setattr(cli, "probe_codex", lambda config: pytest.fail("invalid profile accepted"))
    assert cli.main(["probe", "--config", str(profile)]) == 2


@pytest.mark.parametrize(("complete", "code"), [(True, 0), (False, 4)])
def test_cli_preserves_partial_report_and_existing_output(tmp_path, monkeypatch, capsys, complete, code):
    report = {"schema_version": "1.0", "metadata_complete": complete, "model_id": None}
    monkeypatch.setattr(cli, "probe_codex", lambda config: SimpleNamespace(
        metadata_complete=complete, to_dict=lambda: report.copy(),
    ))
    output = tmp_path / "one" / "probe.json"
    args = ["probe", "--config", str(PROFILE), "--output", str(output)]
    assert cli.main(args) == code
    saved = output.read_bytes()
    assert json.loads(saved) == json.loads(capsys.readouterr().out)
    assert json.loads(saved)["model_id"] is None
    assert cli.main(args) == 5
    assert output.read_bytes() == saved
