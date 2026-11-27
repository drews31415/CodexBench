"""Run the public AAH-S03 Main handoff across A–E with no model calls."""

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from aah.domain import (CheckResult, ContractError, EvaluationResult, FixedRequirements, RepositoryRef, RunRequest,
                        canonical_json_bytes, dump_record, load_record, resolve_condition, sha256_bytes)
from aah.experiments import runner
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run
from aah.workspace import export_revision
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, disjoint, read_regular
from benchmarks.smoke import s1
from evaluation import main_preflight
from evaluation.runner import EvaluationRequest


PROFILE = "main-synthetic-preflight-v1"
CAP = 2048
LIMITS = Limits(30_000, 1, 0, 100, 5_000)
CHECKOUT = Path(__file__).resolve().parents[2]


class Submission(s1.SyntheticAdapter):
    def run_unit(self, request, on_event):
        self.initial_revision = export_revision(request.workspace.root)
        with (request.workspace.root / main_preflight.FILE).open("ab") as source:
            source.write(main_preflight.MARKER)
        return self._finish(request, on_event)

    def continue_unit(self, request, on_event):
        raise ContractError("the synthetic Main preflight has only one initial turn")


def skipped_public_check(snapshot, check_id, artifact_root, *, deadline, turn_id):
    identity = sha256_bytes(PROFILE.encode("utf-8"))
    return CheckResult(str(uuid4()), check_id, identity, turn_id, snapshot.snapshot_id, "NOT_RUN",
                       "TRUSTED_RUNNER", None, 0, identity, PROFILE, (), None, None, ("SYNTHETIC_HANDOFF_ONLY",))


def independent_verdict(evaluator, independent: Path, report, task_id: str, run_root: Path, *, docker: bool):
    """Send one frozen patch to the separate synthetic evaluator process and check its receipt."""
    request = EvaluationRequest(report.run_id, task_id, evaluator.revision, evaluator.manifest_hash, report.frozen_patch)
    operator = independent / report.run_id
    results = operator / "results"
    store_bytes(operator, "request.json", dump_record(request))
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env["PYTHONPATH"] = os.pathsep.join(map(str, (CHECKOUT / "src", CHECKOUT)))
    command = [sys.executable, "-P", "-S", "-B", "-m", "evaluation.main_preflight",
               "--request", str(operator / "request.json"), "--artifacts", str(run_root / "artifacts"),
               "--results", str(results)]
    if docker:
        binary = shutil.which("docker")
        if binary is None:
            raise ContractError("Docker CLI is unavailable")
        command.append("--docker")
        env.update(PATH=str(Path(binary).resolve().parent), DOCKER_CONFIG=str(operator))
    subprocess.run(command, env=env, cwd=CHECKOUT, check=True, timeout=60)
    result = load_record(EvaluationResult, read_regular(results / f"{report.run_id}.json")[0])
    if ((result.private_manifest_hash, result.evaluation_environment_hash, result.test_bundle_hash)
            != (evaluator.manifest_hash, evaluator.environment_hash, evaluator.bundle_hash)):
        raise ContractError("synthetic evaluator receipt does not match pinned inputs")
    return result


def execute(output: Path, *, docker: bool):
    try:
        evaluator = main_preflight.SyntheticEvaluator(docker=docker)
    except (subprocess.SubprocessError, FileNotFoundError) as error:
        if docker:
            raise ContractError("Docker engine or pinned image unavailable") from error
        raise
    task = evaluator.task
    output = checked_path(output, allow_missing=True)
    disjoint(evaluator.base.parent.parent, output)
    output.mkdir(parents=True, exist_ok=False)
    experiment = output / "experiment"
    independent = output / "independent"
    run_root = experiment / "runs"
    fixed = FixedRequirements(task.task, task.mandatory_text, sha256_bytes(task.mandatory_text.encode("utf-8")),
                              "public-copy-only", task.approved_check_ids)
    repository = RepositoryRef(task.repo_id, evaluator.base, evaluator.revision)
    experiment_id = "main-preflight-" + uuid4().hex
    manifest = runner.plan(
        experiment, experiment_id, tasks={task.task_id: {"base": evaluator.revision,
            "fixed_hash": sha256_bytes(dump_record(fixed)), "evaluator_manifest_hash": evaluator.manifest_hash}},
        repetitions=1, seed=20261125, config={"profile": PROFILE, "runtime": asdict(s1.CONFIG),
                                            "limits": asdict(LIMITS), "supplement_cap": CAP},
        probe={"profile": PROFILE, "model_calls": 0, "docker_worker": docker},
        environment_hash=evaluator.environment_hash,
        code_version=sha256_bytes(canonical_json_bytes([sha256_bytes(Path(file).read_bytes()) for file in (
            __file__, main_preflight.__file__, runner.__file__, s1.__file__,
        )])),
    )
    messages, workspaces, access_profiles = {}, set(), {}
    with TemporaryDirectory(prefix="aah-main-preflight-") as directory:
        workspace_root = Path(directory)
        disjoint(workspace_root, output)

        def launch(slot, run_id, claim):
            request = RunRequest(run_id, task.task_id, experiment_id, repository, fixed,
                                 resolve_condition(slot.condition), manifest.config_hash, "research")
            runtime = Submission(run_root)
            claim()
            report = run(request, runtime=runtime, config=s1.CONFIG, public_root=evaluator.base,
                         work_root=workspace_root, results_root=run_root, limits=LIMITS,
                         check=skipped_public_check, supplement_cap=CAP)
            if report.artifact_status != "complete" or not report.evaluable or len(runtime.requests) != 1:
                raise ContractError("synthetic run did not publish a complete submission")
            turn = runtime.requests[0]
            if runtime.initial_revision != evaluator.revision or turn.workspace.root.exists():
                raise ContractError("initial workspace differs or cleanup was not confirmed")
            workspaces.add(turn.workspace.root)
            messages[slot.condition] = turn.message
            access_profiles[slot.condition] = (turn.workspace.isolation_profile_id, turn.workspace.verified)

        def evaluate(slot, report, directory):
            return independent_verdict(evaluator, independent, report, slot.task_id, directory, docker=docker)

        summary = runner.execute(experiment, manifest, launch=launch, evaluate=evaluate)

    core = fixed.task + "\n\n" + fixed.mandatory_text
    if (len(workspaces) != 5 or len(messages) != 5 or set(access_profiles.values()) != {("public-copy-only", False)}
            or any(message is None or not message.startswith(core) for message in messages.values())
            or messages["A"] != core or messages["B"] != messages["D"] or messages["C"] != messages["E"]
            or any(row["evaluation_status"] != "PASS" for row in summary["runs"])):
        raise ContractError("synthetic evaluation or A–E input parity did not match expectations")
    report = {
        "profile": PROFILE, "task_id": task.task_id, "synthetic": True, "model_calls": 0,
        "worker_transport": "docker" if docker else "reviewed-local-synthetic",
        "initial_input_parity": "PASS", "workspace_permission_profile": "public-copy-only",
        "live_agent_permissions_verified": False, "candidate_evaluation_approved": False,
        "initial_revision": evaluator.revision, "fixed_requirements_hash": sha256_bytes(dump_record(fixed)),
        "initial_message_hashes": {condition: sha256_bytes(message.encode("utf-8"))
                                   for condition, message in messages.items()},
        "evaluation_status": {row["condition"]: row["evaluation_status"] for row in summary["runs"]},
    }
    store_bytes(output, "preflight.json", canonical_json_bytes(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="a new directory for this synthetic run")
    parser.add_argument("--docker", action="store_true", help="use the pinned local Docker worker")
    args = parser.parse_args()
    try:
        execute(args.output, docker=args.docker)
    except subprocess.CalledProcessError:
        print("MAIN_PREFLIGHT_BLOCKED independent synthetic evaluator failed")
        return 2
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("MAIN_PREFLIGHT_BLOCKED", str(error) if isinstance(error, ContractError) else type(error).__name__)
        return 2
    print("MAIN_PREFLIGHT_PASS", args.output / "preflight.json", "evaluated=5/5 model_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
