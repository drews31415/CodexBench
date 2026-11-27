import json
import subprocess
import sys
from pathlib import Path
from shutil import copytree
from textwrap import indent
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aah.domain import Condition, ContractError, FixedRequirements, RepositoryRef, RunRequest, resolve_condition, sha256_bytes
from aah.workspace import WorkspaceManager, export_revision
from evaluation import curator_check
from evaluation.runner import EvaluationRequest


def test_curator_evaluation_binds_two_synthetic_patches_to_one_suite(tmp_path, monkeypatch):
    base = tmp_path / "public/base"
    base.mkdir(parents=True)
    filename = "line_total.py"
    (base / "measure.py").write_text("def measure(lines):\n    return len(lines)\n")
    (base / filename).write_text(
        "from measure import measure\n\ndef invoice_total(lines):\n    return measure(lines) + 2 if lines else 0\n")
    task = SimpleNamespace(repo_id="synthetic", task="synthetic list counter", mandatory_text="keep the entry point",
                           approved_check_ids=(), allowed_change_paths=(filename,))
    kit = tmp_path / "curator"
    copytree(base, kit / "baseline")
    copytree(base, kit / "reference")
    (kit / "task.json").write_text('{"task_id":"SYNTHETIC"}')
    task_hash = sha256_bytes((kit / "task.json").read_bytes())
    tests = kit / "curator_tests"
    tests.mkdir()
    cases = {
        "functional": (
            "for lines in ([1], [1, 2]):\n"
            "    with self.subTest(lines=lines):\n"
            "        self.assertEqual(invoice_total(lines), len(lines))\n"
        ),
        "regression": "self.assertEqual(invoice_total([]), 0)\n",
    }
    for kind, case in cases.items():
        (tests / curator_check.SUITES[kind]).write_text(
            "import unittest\nfrom line_total import invoice_total\n\n"
            "class Contract(unittest.TestCase):\n    def test_count(self):\n" + indent(case, "        "))
    initial = export_revision(base)
    fixed = FixedRequirements(task.task, task.mandatory_text, sha256_bytes(task.mandatory_text.encode()),
                              "public-copy-only", ())
    request = RunRequest(str(uuid4()), "SYNTHETIC", None, RepositoryRef(task.repo_id, base, initial), fixed,
                         resolve_condition(Condition.A), task_hash, "user")
    manager = WorkspaceManager(base, tmp_path / "work", kit / "handoff/artifacts", quiescence_check=lambda _: True)
    handle = manager.prepare(request)
    patch = manager.freeze_patch(handle, manager.snapshot(handle))
    manager.cleanup(handle)
    monkeypatch.setattr(curator_check, "read_task", lambda _: (base.parent, task, task_hash))
    monkeypatch.setattr(curator_check, "baseline_evaluation_request",
                        lambda _, digest: EvaluationRequest(patch.run_id, request.task_id, initial, digest, patch))
    def unexpected_docker(*args, **kwargs):
        raise AssertionError("preflight called Docker")

    monkeypatch.setattr(curator_check.docker_worker, "identity", unexpected_docker)
    monkeypatch.setattr(curator_check.docker_worker, "run", unexpected_docker)
    with pytest.raises(ContractError, match="reference is unchanged.*line_total.py"):
        curator_check.evaluate_pair(kit, receipt_id=str(uuid4()), preflight_only=True)
    (kit / "reference" / filename).write_text(
        "from measure import measure\n\ndef invoice_total(lines):\n    return measure(lines)\n")
    unexpected_file = kit / "reference" / "notes.txt"
    unexpected_file.write_text("Synthetic unexpected file")
    with pytest.raises(ContractError, match="reference may modify only.*line_total.py"):
        curator_check.evaluate_pair(kit, receipt_id=str(uuid4()), preflight_only=True)
    unexpected_file.unlink()
    assert curator_check.evaluate_pair(kit, receipt_id=str(uuid4()), preflight_only=True) is None
    assert not list(kit.glob("curator-docker-*"))
    monkeypatch.setattr(curator_check.docker_worker, "identity", lambda: {"engine": "synthetic-local-test"})
    monkeypatch.setattr(curator_check.docker_worker, "run", lambda script, flags, payload, deadline: subprocess.run(
        [sys.executable, *flags, "-c", script.decode("utf-8")], input=payload, capture_output=True, timeout=10))
    output = curator_check.evaluate_pair(kit, receipt_id=str(uuid4()))
    receipt = json.loads(output.read_bytes())
    assert receipt["status"] == "CURATOR_DOCKER_PAIR_PASS"
    assert (receipt["baseline"]["status"], receipt["reference"]["status"]) == ("FAIL", "PASS")
    assert receipt["baseline"]["private_manifest_hash"] == receipt["reference"]["private_manifest_hash"]
    assert receipt["baseline"]["patch_hash"] != receipt["reference"]["patch_hash"]
    assert (output.parent / "reference-patch.json").is_file()


def test_curator_reports_the_entry_that_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["curator_check", "--kit", str(tmp_path), "--preflight"])
    assert curator_check.main() == 2
    assert capsys.readouterr().out == "CURATOR_DOCKER_BLOCKED kit task.json: Expected path is missing\n"


@pytest.mark.parametrize(("variant", "kind", "source", "counts"), [
    ("baseline", "functional", b"def test_function(): pass\n", "tests=0, errors=0"),
    ("reference", "regression",
     b"import unittest\nclass Example(unittest.TestCase):\n"
     b"    def test_example(self): raise ValueError('synthetic private message')\n", "tests=1, errors=1"),
])
def test_curator_reports_incomplete_suite_counts(variant, kind, source, counts, monkeypatch):
    monkeypatch.setattr(curator_check.docker_worker, "run", lambda script, flags, payload, deadline: subprocess.run(
        [sys.executable, *flags, "-c", script.decode("utf-8")], input=payload, capture_output=True, timeout=10))

    with pytest.raises(ContractError) as error:
        curator_check._suite_statuses(variant, {}, {kind: source})

    assert str(error.value) == (
        f"{variant} {kind} ({curator_check.SUITES[kind]}): curator suite is incomplete "
        f"({counts}, skipped=0, expected_failures=0, unexpected_successes=0)"
    )
