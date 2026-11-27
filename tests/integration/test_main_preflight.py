import json
import subprocess
import sys
from uuid import uuid4

import pytest

from aah.domain import Condition, ContractError, FixedRequirements, RepositoryRef, RunRequest, resolve_condition, sha256_bytes
from aah.workspace import WorkspaceManager
from benchmarks.main_public import preflight
from evaluation import main_preflight
from evaluation.runner import EvaluationRequest


def test_main_preflight_binds_five_public_runs_to_separate_synthetic_verdicts(tmp_path):
    output = tmp_path / "main-preflight"
    result = preflight.execute(output, docker=False)

    assert result["evaluation_status"] == dict.fromkeys("ABCDE", "PASS")
    assert result["initial_message_hashes"]["B"] == result["initial_message_hashes"]["D"]
    assert result["initial_message_hashes"]["C"] == result["initial_message_hashes"]["E"]
    assert result["model_calls"] == 0
    assert not result["live_agent_permissions_verified"] and not result["candidate_evaluation_approved"]
    for operator in (output / "independent").iterdir():
        request = json.loads((operator / "request.json").read_bytes())
        result_path = operator / "results" / f"{request['run_id']}.json"
        verdict = json.loads(result_path.read_bytes())
        assert verdict["patch_hash"] == request["patch"]["patch_hash"]
        assert verdict["private_manifest_hash"] == request["manifest_hash"]
        run = output / "experiment/runs" / request["run_id"]
        assert not (run / "request.json").exists() and not (run / "results").exists()


def test_synthetic_evaluator_sends_only_inputs_and_rejects_other_source(tmp_path, monkeypatch):
    monkeypatch.setattr(main_preflight.docker_worker, "identity", lambda: {"test": "transport-contract"})
    evaluator = main_preflight.SyntheticEvaluator(docker=True)
    task = evaluator.task
    fixed = FixedRequirements(task.task, task.mandatory_text, sha256_bytes(task.mandatory_text.encode()),
                              "public-copy-only", task.approved_check_ids)
    workspace = WorkspaceManager(evaluator.base, tmp_path / "work", tmp_path / "artifacts",
                                 quiescence_check=lambda _: True)
    calls = []

    def transport(script, flags, payload, deadline):
        calls.append(json.loads(payload))
        return subprocess.run([sys.executable, *flags, "-c", script.decode()], input=payload,
                              capture_output=True, timeout=10)

    monkeypatch.setattr(main_preflight.docker_worker, "run", transport)
    for source in (evaluator.approved_source, evaluator.approved_source + b"# another source\n"):
        request = RunRequest(str(uuid4()), task.task_id, None, RepositoryRef(task.repo_id, evaluator.base, evaluator.revision),
                             fixed, resolve_condition(Condition.A), evaluator.manifest_hash, "user")
        handle = workspace.prepare(request)
        try:
            (handle.root / main_preflight.FILE).write_bytes(source)
            patch = workspace.freeze_patch(handle, workspace.snapshot(handle))
        finally:
            workspace.cleanup(handle)
        handoff = EvaluationRequest(request.run_id, task.task_id, evaluator.revision, evaluator.manifest_hash, patch)
        if source == evaluator.approved_source:
            assert evaluator.evaluate(handoff, workspace.artifact_root, tmp_path / "results").status == "PASS"
        else:
            with pytest.raises(ContractError, match="only the reviewed synthetic submission"):
                evaluator.evaluate(handoff, workspace.artifact_root, tmp_path / "results")

    assert len(calls) == 1 and set(calls[0]) == {"source", "inputs"}
    assert calls[0]["inputs"] == [case[0] for case in main_preflight.CASES.values()]
