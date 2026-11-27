import json
from zipfile import ZipFile

import pytest

from aah.domain import sha256_bytes
from aah.workspace import export_revision
from benchmarks.smoke import driver
from benchmarks.smoke.prepare import prepare


@pytest.mark.parametrize("task_id", ["DEV-01", "DEV-02"])
def test_prepare_reproduces_only_public_input(tmp_path, task_id):
    directory = driver.task_dir(task_id)
    task = json.loads((directory / "task.json").read_bytes())
    out = tmp_path / "review.zip"
    digest = prepare(task_id, out)
    assert digest == sha256_bytes(out.read_bytes()) == prepare(task_id, tmp_path / "repeat.zip")
    with pytest.raises(FileExistsError):
        prepare(task_id, out)
    assert sha256_bytes(out.read_bytes()) == digest
    with ZipFile(out) as archive:
        assert archive.namelist() == ["workspace/module.py", "initial-message.txt", "review.json"]
        info = json.loads(archive.read("review.json"))
        message = archive.read(info["initial_message_ref"])
        assert message == (task["task"] + "\n\n" + task["mandatory_text"]).encode()
        assert info["initial_message_hash"] == sha256_bytes(message)
        assert info["mandatory_hash"] == sha256_bytes(task["mandatory_text"].encode())
        assert info["condition"] == "A" and info["approved_check_ids"] == task["approved_check_ids"]
        assert info["base_commit"] == export_revision(directory / "base")
        assert archive.read("workspace/module.py") == (directory / "base" / "module.py").read_bytes()


@pytest.mark.parametrize("change", ["added-file", "changed-module", "task-definition"])
def test_prepare_rejects_unreviewed_input_before_export(tmp_path, monkeypatch, change):
    source = driver.task_dir("DEV-01")
    directory = tmp_path / "task"
    base = directory / "base"
    base.mkdir(parents=True)
    spec = (source / "task.json").read_bytes()
    (directory / "task.json").write_bytes(b"{}" if change == "task-definition" else spec)
    module = (source / "base" / "module.py").read_bytes()
    (base / "module.py").write_bytes(b"# different" if change == "changed-module" else module)
    if change == "added-file":
        (base / "extra.txt").write_text("unreviewed", encoding="utf-8")
    monkeypatch.setattr(driver, "task_dir", lambda _: directory)
    out = tmp_path / "review.zip"
    with pytest.raises((ValueError, driver.CheckEnvironmentError)):
        prepare("DEV-01", out)
    assert not out.exists()
