"""Operator entry point for the single approved 25-slot Dev Pilot."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import UUID, uuid4

from aah.domain import (
    Condition, ContractError, EvaluationResult, UnitStatus, canonical_json_bytes, dump_record,
    load_record, resolve_condition, sha256_bytes,
)
from aah.experiments import runner
from aah.reporting.store import RunStore
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run_prepared_experiment
from aah.workspace.patch import store_bytes
from benchmarks.smoke import docker_worker, driver, live_dev01, s4
from evaluation.runner import EvaluationRequest

from . import assessment, prepare


LIVE_ID = "s5-dev-pilot-20261126"
LIVE_PARENT = prepare.CHECKOUT / "results/s5-live"
LIVE_ROOT = LIVE_PARENT / LIVE_ID
APPROVAL = prepare.CHECKOUT / "configs/pilot/dev-pilot-20261126-approval.json"


class PilotRuntime(s4.LiveRuntime):
    def open_run(self, request, workspace):
        if (self.opened or request != self.request or request.experiment_id != LIVE_ID
                or request.task_id not in prepare.TASKS or workspace != self.workspace or not self.safe_to_finalize()
                or request.policies != resolve_condition(request.policies.condition)):
            raise ContractError("unbound Pilot run")
        self.opened = True
        self.turns = 0
        self.recoveries = set()

    def _turn(self, turn, on_event, *, followup):
        if (not self.opened or turn.run_id != self.request.run_id or turn.workspace != self.workspace
                or not self.safe_to_finalize() or not turn.message or turn.deadline_ms_remaining <= 0
                or sha256_bytes(turn.message.encode()) != turn.message_hash
                or turn.turn_id != f"turn-{self.turns + 1:04}"):
            raise ContractError("unbound Pilot model turn")
        if followup:
            if (not self.turns or not self.session_id or turn.session_ref != self.session_id
                    or self.request.policies.condition == Condition.A or turn.kind != "RECOVERY"
                    or not turn.intervention_id or turn.intervention_id in self.recoveries
                    or len(self.recoveries) >= prepare.RECOVERIES_PER_SLOT):
                raise ContractError("Pilot recovery exceeds the approved scope")
            self.recoveries.add(turn.intervention_id)
        elif self.turns or turn.kind != "INITIAL" or turn.session_ref is not None or turn.intervention_id:
            raise ContractError("Pilot initial turn already used or invalid")
        self.turns += 1
        with (self.directory / f"{turn.turn_id}-dispatch.json").open("xb") as out:
            out.write(canonical_json_bytes({"turn_id": turn.turn_id, "kind": turn.kind,
                                            "message_hash": turn.message_hash, "session_ref": turn.session_ref}))
        result = self.launch(turn.message.encode(), turn.deadline_ms_remaining / 1000,
                             persist=self.request.policies.external_intervention, session_id=turn.session_ref)
        outcome = self.outcome(turn, result, on_event)
        if outcome.status == UnitStatus.COMPLETED:
            if not outcome.session_ref or str(UUID(outcome.session_ref)) != outcome.session_ref:
                raise ContractError("missing verified Pilot session identity")
            if followup and outcome.session_ref != self.session_id:
                raise ContractError("resumed a different Pilot session")
            self.session_id = outcome.session_ref
        return outcome


def approval(draft):
    raw = APPROVAL.read_bytes()
    entry = json.loads(raw)
    limits = draft["proposed_limits"]
    if (entry["experiment_id"], entry["approved_draft_sha256"], entry["profile"], entry["max_extra_payment_krw"],
            entry["max_initial_turns"], entry["max_recovery_turns"], entry["max_total_turns"]) != (
            LIVE_ID, prepare.DRAFT_SHA256, "existing-chatgpt-subscription", 0,
            limits["initial_turns"], limits["recovery_turns"], limits["total_turns"]):
        raise ContractError("Pilot schedule does not match the recorded approval")
    return sha256_bytes(raw)


def preflight(directory, draft, operator):
    with s4.work_directory() as work:
        live_dev01._run(directory, work, prepare.task_request("DEV-01"), operator, execute=False)
    environment = assessment.environment_hash()
    for task_id, asset in draft["task_assets"].items():
        source = prepare.task_directory(task_id)
        public = driver.load_bundle(source / "public.json", asset["public_checks_hash"])
        independent = driver.load_bundle(prepare.FIXTURES / task_id / "suites.json", asset["review_suite_hash"])
        groups = [public["P-FUNC"], public["P-REG"], independent["functional"], independent["regression"]]
        cases = [case for group in groups for case in group]
        for file, expected in ((source / "base/module.py", False), (prepare.FIXTURES / task_id / "module.py", True)):
            matches = iter(assessment.observe(task_id, file.read_bytes(), cases, monotonic() + 5))
            statuses = [all(tuple(next(matches) for _ in group)) for group in groups]
            if statuses != [expected, True, expected, True]:
                raise ContractError(f"{task_id} Docker baseline/reference preflight mismatch")
        operator.setdefault("docker_controls", []).append(task_id)
    return environment


def independent_verdict(directory, run_root, request):
    store_bytes(directory, "request.json", dump_record(request))
    results = directory / "results"
    result_path = results / (request.run_id + ".json")
    if not result_path.is_file():
        binary = shutil.which("docker")
        if binary is None:
            raise FileNotFoundError("Docker CLI is unavailable")
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        with TemporaryDirectory() as home:
            env.update(PYTHONPATH=os.pathsep.join(map(str, (prepare.CHECKOUT / "src", prepare.CHECKOUT))),
                       PATH=str(Path(binary).resolve().parent), DOCKER_CONFIG=home, USERPROFILE=home, HOME=home)
            subprocess.run([sys.executable, "-P", "-S", "-B", "-m", "benchmarks.pilot.assessment",
                            "--request", str(directory / "request.json"), "--artifact-root", str(run_root / "artifacts"),
                            "--results", str(results)], env=env, cwd=prepare.CHECKOUT, check=True, timeout=60)
    verdict = load_record(EvaluationResult, result_path.read_bytes())
    if verdict.error_code in {"DOCKER_CLEANUP_UNCONFIRMED", "REQUEST_IDENTITY_MISMATCH", "PILOT_EVALUATOR_ERROR"}:
        raise ContractError(f"Pilot evaluation requires operator review: {verdict.error_code}")
    return verdict


def execute(draft, environment, approval_hash):
    tasks = {key: {**asset, "base": asset["base_revision"], "fixed_hash": asset["fixed_requirements_hash"],
                   "evaluator_manifest_hash": assessment.evaluator_hash(key, asset, environment)}
             for key, asset in draft["task_assets"].items()}
    limits = draft["proposed_limits"]
    config = {**draft["proposed_runtime"], "limits": limits, "approval_sha256": approval_hash,
              "supplement_cap": draft["supplement_cap"], "baseline_checks": draft["baseline_checks"],
              "token_limit_mode": draft["token_limit_mode"]}
    source = {path.name: sha256_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
              for path in Path(__file__).parent.glob("*.py")}
    code_version = sha256_bytes(canonical_json_bytes([source, s4.code_version()]))
    probe = {"profile": assessment.PROFILE, "controls": list(prepare.TASKS), "native": "S4 verified MXC"}
    manifest = runner.plan(LIVE_ROOT, LIVE_ID, tasks=tasks, repetitions=1, seed=draft["execution_order_seed"],
                           config=config, probe=probe, environment_hash=environment, code_version=code_version)
    schedule = runner.pinned(LIVE_ROOT, manifest.schedule_ref, manifest.schedule_hash)
    if schedule != [{**slot, "experiment_id": LIVE_ID} for slot in draft["slots"]]:
        raise ContractError("Pilot executable schedule differs from the approved sequence")

    def launch(slot, run_id, claim):
        task = prepare.task_request(slot.task_id)
        asset = tasks[slot.task_id]
        if (task.repository.initial_commit, task.config_hash, sha256_bytes(dump_record(task.fixed))) != (
                asset["base"], asset["task_hash"], asset["fixed_hash"]):
            raise ContractError("Pilot task changed after planning")
        request = replace(task, run_id=run_id, experiment_id=LIVE_ID, mode="research",
                          config_hash=manifest.config_hash, policies=resolve_condition(slot.condition))
        print("PILOT_SLOT_START", slot.slot_id, slot.task_id, slot.condition, flush=True)
        store = RunStore(LIVE_ROOT / "runs" / run_id, run_id)
        operator = {"schema_version": "1.0", "run_id": run_id, "condition": slot.condition, "launch_attempts": 0}
        check = assessment.public_runner(slot.task_id, tasks[slot.task_id], environment)
        recoveries = limits["recovery_turns_per_eligible_slot"] if request.policies.external_intervention else 0
        budget = Limits(limits["wall_ms_per_run"], 1 + recoveries, recoveries, limits["max_transitions"],
                        limits["cleanup_reserve_ms"])
        cleanup_failed = False

        def check_with_cleanup(*args, **kwargs):
            nonlocal cleanup_failed
            try:
                result = check(*args, **kwargs)
                if kwargs["turn_id"] is None and result.status not in {"PASS", "FAIL"}:
                    raise ContractError(f"Pilot baseline check unavailable: {result.check_id}")
                return result
            except docker_worker.CleanupUnconfirmed:
                cleanup_failed = True
                raise

        def prepared(manager, handle, dispatch, unchanged):
            runtime = PilotRuntime(request, handle, store.root, s4.LIVE_CONFIG, dispatch,
                                   live_dev01._event_summary, live_dev01.EVENT_TYPES, unchanged)
            claim()
            report = run_prepared_experiment(request, runtime=runtime, config=s4.LIVE_CONFIG, manager=manager,
                                             handle=handle, store=store, limits=budget, check=check_with_cleanup,
                                             supplement_cap=draft["supplement_cap"], baseline_checks=True)
            operator["stop_reason"] = report.stop_reason
            if cleanup_failed or not unchanged() or report.stop_reason in {
                "SAFETY_VIOLATION", "RUNTIME_ERROR", "PREPARATION_ERROR", "ARTIFACT_ERROR", "CANCELED"
            }:
                raise ContractError(f"Pilot slot {slot.slot_id} requires review: {report.stop_reason}")

        try:
            with s4.work_directory() as work:
                live_dev01._run(store.root, work, request, operator, execute=False, experiment=prepared)
            operator["status"] = "LIVE_RUN_RECORDED"
        finally:
            store_bytes(store.root, "operator.json", canonical_json_bytes(operator))

    def verify_run(slot, report, directory):
        receipt = json.loads((directory / "operator.json").read_bytes())
        if ((receipt.get("status"), receipt["run_id"], receipt["condition"], receipt["launch_attempts"])
                != ("LIVE_RUN_RECORDED", report.run_id, slot.condition, report.turns_used)):
            raise ContractError(f"Pilot slot {slot.slot_id} requires operator review")

    def evaluate(slot, report, directory):
        request = EvaluationRequest(report.run_id, slot.task_id, tasks[slot.task_id]["base"],
                                    tasks[slot.task_id]["evaluator_manifest_hash"], report.frozen_patch)
        verdict = independent_verdict(LIVE_ROOT / "independent" / report.run_id, directory, request)
        print("PILOT_SLOT_RECORDED", slot.slot_id, slot.task_id, slot.condition, verdict.status, flush=True)
        return verdict

    return runner.execute(LIVE_ROOT, manifest, launch=launch, evaluate=evaluate, verify_run=verify_run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="run MXC and fixture controls without model calls")
    mode.add_argument("--execute", action="store_true", help="use the recorded Pilot approval")
    args = parser.parse_args()
    directory = LIVE_PARENT / "preflights" / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    operator = {"schema_version": "1.0", "launch_attempts": 0}
    try:
        draft = prepare.read_pinned_draft()
        approval_hash = approval(draft)
        environment = preflight(directory, draft, operator)
        operator["status"] = "PILOT_PREFLIGHT_PASS"
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        operator.update(status="PILOT_BLOCKED", reason=str(exc))
        print("PILOT_BLOCKED", directory / "operator.json", str(exc))
        return 2
    finally:
        store_bytes(directory, "operator.json", canonical_json_bytes(operator))
    if args.preflight:
        print("PILOT_PREFLIGHT_PASS", directory / "operator.json")
        return 0
    try:
        summary = execute(draft, environment, approval_hash)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("PILOT_BLOCKED", LIVE_ROOT, str(exc))
        return 2
    evaluated = sum(row["evaluation_status"] in {"PASS", "FAIL"} for row in summary["runs"])
    print("PILOT_RECORDED", LIVE_ROOT / "summary.json", f"evaluated={evaluated}/25")
    return 0 if evaluated == 25 else 1


if __name__ == "__main__":
    raise SystemExit(main())
