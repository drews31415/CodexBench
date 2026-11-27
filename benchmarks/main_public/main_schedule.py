"""Plan the AAH-S03 Main schedule (A–E × 1) and verify it end to end with synthetic submissions.

Every slot goes through ``main_runner.run_slot``: a fresh public copy whose initial snapshot
and normalized MXC permission settings must equal the pinned access record (EL-20261127-020)
before the slot is claimed or any turn starts. In ``--verify`` the turn is the reviewed
synthetic submission from ``preflight``; the frozen patch then goes to the separate synthetic
evaluator process, and the request and verdict are joined by run ID, task ID and patch hash.

``--execute`` runs the live schedule only from a LOCKED freeze manifest (``main_freeze``): Codex CLI
turns through the verified MXC dispatch, the trusted public-check runner, and the curator
evaluator in a separate process. ``OPERATIONAL_GAPS`` describes the synthetic ``--verify`` mode.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from uuid import UUID
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from aah.domain import (Condition, ContractError, EvaluationResult, RunRequest, canonical_json_bytes, dump_record,
                        load_record, resolve_condition, sha256_bytes)
from aah.domain.report import RunReport
from aah.experiments import runner
from aah.reporting.store import RunStore, read_report
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run_prepared_experiment
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, disjoint, read_regular
from benchmarks.main_public import access_check, main_freeze, main_runner, preflight, public_runner
from benchmarks.smoke import live_dev01, s1, s4
from evaluation import main_preflight
from evaluation.runner import EvaluationRequest


PROFILE = "main-s03-schedule-v1"
TASK = main_runner.TASK
SEED = 20261127
CHECKOUT = main_runner.CHECKOUT
OPERATIONAL_GAPS = (
    {"id": "LIVE_RUNTIME", "state": "NOT_CONNECTED",
     "detail": "No live Codex runtime is bound to the Main slot dispatch; the synthetic submission writes the patch."},
    {"id": "PUBLIC_CHECKS", "state": "NOT_CONNECTED",
     "detail": "No trusted public-check runner for Main tasks; public checks are recorded as NOT_RUN."},
    {"id": "RECOVERY_TURNS", "state": "NOT_EXERCISED",
     "detail": "One initial turn per slot; B–E detection, recovery delivery and resume are not exercised."},
    {"id": "CURATOR_EVALUATOR", "state": "NOT_CONNECTED",
     "detail": "Only the synthetic evaluator manifest is used; the curator evaluator with private tests is not bound."},
    {"id": "USAGE", "state": "NOT_MEASURED", "detail": "No model tokens, credits or turn latency are observed."},
    {"id": "APPROVAL_AND_FREEZE", "state": "ABSENT",
     "detail": "No Main approval, budget, model configuration or frozen G2 manifest; no live entry point exists."},
)


def _code_version() -> str:
    files = (__file__, main_runner.__file__, access_check.__file__, preflight.__file__, main_preflight.__file__,
             runner.__file__, live_dev01.__file__)
    return sha256_bytes(canonical_json_bytes({Path(f).name: sha256_bytes(Path(f).read_bytes().replace(b"\r\n", b"\n"))
                                              for f in files}))


def plan(root: Path, experiment_id: str, *, record: dict, record_sha256: str | None, evaluator, fixed, repository):
    """Pin one task, five conditions and one repetition together with the access record bindings."""
    tasks = {TASK: {
        "base": repository.initial_commit, "fixed_hash": sha256_bytes(dump_record(fixed)),
        "evaluator_manifest_hash": evaluator.manifest_hash,
        "permission_settings_hash": {c: v["permission_settings_hash"] for c, v in record["conditions"].items()},
        "initial_snapshot_id": {c: v.get("initial_snapshot_id") for c, v in record["conditions"].items()},
    }}
    config = {"profile": PROFILE, "mode": "synthetic-verification", "runtime": asdict(s1.CONFIG),
              "limits": asdict(preflight.LIMITS), "supplement_cap": preflight.CAP,
              "mxc_profile": live_dev01.PROFILE, "public_checks": "NOT_CONNECTED"}
    probe = {"access_record_sha256": record_sha256, "access_profile": record["profile"], "model_calls": 0}
    return runner.plan(root, experiment_id, tasks=tasks, repetitions=1, seed=SEED, config=config, probe=probe,
                       environment_hash=evaluator.environment_hash, code_version=_code_version())


def _binding(row, run_root: Path, independent: Path, experiment: Path, evaluator) -> dict:
    run_id = row["run_id"]
    report = load_record(RunReport, read_report(run_root, run_id))
    request = load_record(EvaluationRequest, read_regular(independent / run_id / "request.json")[0])
    verdict = load_record(EvaluationResult, read_regular(independent / run_id / "results" / f"{run_id}.json")[0])
    stored = read_regular(experiment / "slots" / row["slot_id"] / "evaluation.json")[0]
    meta = json.loads(read_regular(run_root / run_id / "manifest.json")[0])
    patch = report.frozen_patch
    return {
        "run_id": len({report.run_id, request.run_id, request.patch.run_id, patch.run_id, verdict.run_id}) == 1,
        "task_id": len({TASK, meta["task_id"], request.task_id, verdict.task_id}) == 1,
        "patch_hash": len({patch.patch_hash, request.patch.patch_hash, verdict.patch_hash, row["patch_hash"]}) == 1,
        "manifest_hash": request.manifest_hash == verdict.private_manifest_hash == evaluator.manifest_hash,
        "slot_record_is_verdict": stored == dump_record(verdict),
        "verdict_outside_run": not any((run_root / run_id / name).exists() for name in ("request.json", "results")),
    }


def verify(output: Path, *, docker: bool = False, access_record: Path = main_runner.ACCESS_RECORD,
           record_sha256: str | None = main_runner.ACCESS_RECORD_SHA256, inherit_console: bool = False) -> dict:
    output = checked_path(output, allow_missing=True)
    output.mkdir(parents=True, exist_ok=False)
    experiment, independent = output / "experiment", output / "independent"
    run_root = experiment / "runs"
    report = {"schema_version": "1.0", "profile": PROFILE, "task_id": TASK, "synthetic": True,
              "agent_turns": 0, "model_calls": 0, "evaluator_transport": "docker" if docker else "local-synthetic",
              "access_record_sha256": record_sha256, "slots": [],
              "operational_gaps": list(OPERATIONAL_GAPS),
              "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    operators, messages, current = {}, {}, {}
    try:
        record = main_runner.load_access_record(access_record, sha256=record_sha256)
        task, fixed, repository = main_runner.task_inputs()
        evaluator = main_preflight.SyntheticEvaluator(docker=docker)
        if (evaluator.revision, evaluator.base) != (repository.initial_commit, repository.source_path):
            raise main_runner.PermissionGateError("ACCESS_RECORD_TASK_MISMATCH", "evaluator base differs")
        disjoint(repository.source_path, output)
        experiment_id = "main-s03-schedule-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest = plan(experiment, experiment_id, record=record, record_sha256=record_sha256,
                        evaluator=evaluator, fixed=fixed, repository=repository)
        report.update(experiment_id=experiment_id, manifest_hash=sha256_bytes(dump_record(manifest)),
                      schedule=[{"slot_id": s["slot_id"], "condition": s["condition"]}
                                for s in runner.pinned(experiment, manifest.schedule_ref, manifest.schedule_hash)])

        def launch(slot, run_id, claim):
            current.update(slot_id=slot.slot_id, condition=slot.condition)
            request = RunRequest(run_id, TASK, experiment_id, repository, fixed, resolve_condition(slot.condition),
                                 manifest.config_hash, "research")
            store = RunStore(run_root / run_id, run_id)
            operator = operators[slot.slot_id] = {"schema_version": "1.0", "slot_id": slot.slot_id, "run_id": run_id,
                                                  "condition": slot.condition, "launch_attempts": 0}
            runtime = preflight.Submission(run_root)

            def prepared(manager, handle, dispatch, unchanged):
                # Reached only after the snapshot and permission gates matched; claim precedes the turn.
                claim()
                result = run_prepared_experiment(
                    request, runtime=runtime, config=s1.CONFIG, manager=manager, handle=handle, store=store,
                    limits=preflight.LIMITS, check=preflight.skipped_public_check, supplement_cap=preflight.CAP,
                    baseline_checks=False)
                operator["stop_reason"] = result.stop_reason
                if (result.artifact_status != "complete" or not result.evaluable or len(runtime.requests) != 1
                        or runtime.initial_revision != repository.initial_commit or not unchanged()):
                    raise ContractError(f"{slot.slot_id} did not publish a complete synthetic submission")
                messages[slot.condition] = runtime.requests[0].message

            work_root = Path(mkdtemp(prefix="aah-main-schedule-"))
            try:
                main_runner.run_slot(request, record=record, work_root=work_root,
                                     artifact_root=store.root / "artifacts", operator=operator,
                                     experiment=prepared, inherit_console=inherit_console)
                operator["status"] = "SYNTHETIC_RUN_RECORDED"
            finally:
                operator.pop("permission_settings", None)
                store_bytes(store.root, "operator.json", canonical_json_bytes(operator))
                if work_root.exists() and not any(work_root.iterdir()):
                    work_root.rmdir()

        def verify_run(slot, run_report, directory):
            entry = operators[slot.slot_id]
            if ((entry.get("status"), entry["run_id"], entry["condition"], entry["launch_attempts"])
                    != ("SYNTHETIC_RUN_RECORDED", run_report.run_id, slot.condition, 0)
                    or entry["permission_gate"]["status"] != "MATCH" or entry["snapshot_gate"]["status"] != "MATCH"
                    or not entry.get("workspace_cleaned")):
                raise ContractError(f"{slot.slot_id} requires operator review")

        def evaluate(slot, run_report, directory):
            return preflight.independent_verdict(evaluator, independent, run_report, slot.task_id, directory,
                                                 docker=docker)

        summary = runner.execute(experiment, manifest, launch=launch, evaluate=evaluate, verify_run=verify_run)
        current.clear()
        core = fixed.task + "\n\n" + fixed.mandatory_text
        report["input_parity"] = {
            "A_is_task_and_mandatory_only": messages.get("A") == core,
            "all_start_with_task_and_mandatory": all(m and m.startswith(core) for m in messages.values()),
            "B_equals_D": messages.get("B") == messages.get("D"), "C_equals_E": messages.get("C") == messages.get("E"),
            "message_sha256": {c: sha256_bytes(m.encode("utf-8")) for c, m in sorted(messages.items())},
        }
        for row in summary["runs"]:
            entry = operators[row["slot_id"]]
            report["slots"].append({
                "slot_id": row["slot_id"], "condition": row["condition"], "run_id": row["run_id"],
                "permission_gate": entry["permission_gate"], "snapshot_gate": entry["snapshot_gate"],
                "launch_attempts": entry["launch_attempts"], "stop_reason": row["stop_reason"],
                "public_check_status": row["public_check_status"], "artifact_status": row["artifact_status"],
                "patch_hash": row["patch_hash"], "evaluation_status": row["evaluation_status"],
                "binding": _binding(row, run_root, independent, experiment, evaluator),
            })
        passed = (len(report["slots"]) == 5 and {s["condition"] for s in report["slots"]} == set("ABCDE")
                  and all(s["evaluation_status"] == "PASS" and all(s["binding"].values()) for s in report["slots"])
                  and all(v for k, v in report["input_parity"].items() if k != "message_sha256"))
        report["summary_sha256"] = sha256_bytes(read_regular(experiment / "summary.json")[0])
        report["status"] = "PASS" if passed else "FAIL"
    except main_runner.PermissionGateError as error:
        report.update(status="BLOCKED", code=error.code, reason=str(error), blocked_slot=current.get("slot_id"),
                      blocked_condition=current.get("condition"))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status="BLOCKED", code=getattr(error, "stage", type(error).__name__),
                      reason=str(error) if isinstance(error, (ContractError, access_check.AccessBlocked))
                      else type(error).__name__,
                      blocked_slot=current.get("slot_id"), blocked_condition=current.get("condition"))
    finally:
        report["slot_receipts"] = {slot_id: {key: entry.get(key) for key in (
            "condition", "run_id", "status", "launch_attempts", "snapshot_gate", "permission_gate", "workspace_cleaned")}
            for slot_id, entry in operators.items()}
        report["finished_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        store_bytes(output, "schedule-verification.json", canonical_json_bytes(report))
    return report


# ---------------------------------------------------------------- live Main (requires a LOCKED freeze)

LIVE_PARENT = CHECKOUT / "results" / "main-live"
REVIEW_STOPS = {"SAFETY_VIOLATION", "RUNTIME_ERROR", "PREPARATION_ERROR", "ARTIFACT_ERROR", "CANCELED"}


class MainRuntime(s4.LiveRuntime):
    """Codex CLI turns for one frozen Main slot, through the verified MXC dispatch."""

    def __init__(self, *args, experiment_id: str, recovery_cap: int, task_ids: frozenset = frozenset({TASK}),
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.experiment_id = experiment_id
        self.recovery_cap = recovery_cap
        self.task_ids = task_ids

    def open_run(self, request, workspace):
        if (self.opened or request != self.request or request.experiment_id != self.experiment_id
                or request.task_id not in self.task_ids or workspace != self.workspace or not self.safe_to_finalize()
                or request.policies != resolve_condition(request.policies.condition)):
            raise ContractError("unbound Main run")
        self.opened = True
        self.turns = 0
        self.recoveries = set()

    def _turn(self, turn, on_event, *, followup):
        if (not self.opened or turn.run_id != self.request.run_id or turn.workspace != self.workspace
                or not self.safe_to_finalize() or not turn.message or turn.deadline_ms_remaining <= 0
                or sha256_bytes(turn.message.encode()) != turn.message_hash
                or turn.turn_id != f"turn-{self.turns + 1:04}"):
            raise ContractError("unbound Main model turn")
        if followup:
            if (not self.turns or not self.session_id or turn.session_ref != self.session_id
                    or self.request.policies.condition == Condition.A or turn.kind != "RECOVERY"
                    or not turn.intervention_id or turn.intervention_id in self.recoveries
                    or len(self.recoveries) >= self.recovery_cap):
                raise ContractError("Main recovery exceeds the approved scope")
            self.recoveries.add(turn.intervention_id)
        elif self.turns or turn.kind != "INITIAL" or turn.session_ref is not None or turn.intervention_id:
            raise ContractError("Main initial turn already used or invalid")
        self.turns += 1
        with (self.directory / f"{turn.turn_id}-dispatch.json").open("xb") as out:
            out.write(canonical_json_bytes({"turn_id": turn.turn_id, "kind": turn.kind,
                                            "message_hash": turn.message_hash, "session_ref": turn.session_ref}))
        result = self.launch(turn.message.encode(), turn.deadline_ms_remaining / 1000,
                             persist=self.request.policies.external_intervention, session_id=turn.session_ref)
        outcome = self.outcome(turn, result, on_event)
        if outcome.status == "COMPLETED":
            if not outcome.session_ref or str(UUID(outcome.session_ref)) != outcome.session_ref:
                raise ContractError("missing verified Main session identity")
            if followup and outcome.session_ref != self.session_id:
                raise ContractError("resumed a different Main session")
            self.session_id = outcome.session_ref
        return outcome


class SubprocessCurator:
    """Run evaluation.main_curator in its own process; private suites never enter this process."""

    def __init__(self, kit: Path, *, docker_cli: str | None = None):
        self.kit = checked_path(kit)
        self.task_id = self.kit.name
        disjoint(CHECKOUT, self.kit)
        self.docker = docker_cli or shutil.which("docker")
        if self.docker is None:
            raise ContractError("Docker CLI is unavailable for the curator evaluator")

    def _run(self, args, home: Path):
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env.update(PYTHONPATH=os.pathsep.join(map(str, (CHECKOUT / "src", CHECKOUT))),
                   PATH=str(Path(self.docker).resolve().parent), DOCKER_CONFIG=str(home))
        return subprocess.run([sys.executable, "-P", "-S", "-B", "-m", "evaluation.main_curator",
                               "--kit", str(self.kit), *args], env=env, cwd=CHECKOUT, capture_output=True,
                              check=True, timeout=120)

    def manifest_hash(self, home: Path) -> str:
        words = self._run(["--manifest"], home).stdout.decode("utf-8").split()
        if len(words) != 3 or words[0] != "MAIN_CURATOR_MANIFEST" or words[1] != self.task_id:
            raise ContractError("curator evaluator did not report a manifest")
        return words[2]

    def evaluate(self, request_path: Path, artifacts: Path, results: Path) -> None:
        self._run(["--request", str(request_path), "--artifacts", str(artifacts), "--results", str(results)],
                  request_path.parent)


def execute_live(curators: dict, *, freeze: Path = main_freeze.FREEZE, approval: Path = main_freeze.APPROVAL,
                 inclusion: Path | None = None, live_parent: Path = LIVE_PARENT, identity: dict | None = None,
                 transport=None, inherit_console: bool = False) -> dict:
    """Run the frozen schedule. Every gate before the first slot fails closed with no Codex process.

    ``curators`` maps each frozen task ID to its curator evaluator (kit-side process).
    """
    draft = main_freeze.validate_for_execution(freeze, approval, inclusion=inclusion, identity=identity)
    model = draft["model"]
    if (model["model_id"], model["reasoning_effort"], model["runtime_version"]) != (
            s4.LIVE_CONFIG.model_id, s4.LIVE_CONFIG.reasoning_effort, s4.LIVE_CONFIG.runtime_version):
        raise ContractError("frozen model differs from the verified live configuration")
    frozen = draft["tasks"]
    if set(curators) != set(frozen):
        raise ContractError("a curator kit is needed for every frozen task and only those")
    root = checked_path(live_parent / draft["experiment_id"], allow_missing=True)
    root.mkdir(parents=True, exist_ok=True)
    records, inputs, checks = {}, {}, {}
    for task_id, entry in frozen.items():
        if curators[task_id].manifest_hash(root) != entry["curator_manifest_sha256"]:
            raise ContractError(f"{task_id} curator evaluator manifest differs from the frozen manifest")
        access_path = Path(entry["access_record"])
        records[task_id] = main_runner.load_access_record(
            access_path if access_path.is_absolute() else CHECKOUT / access_path,
            sha256=entry["access_record_sha256"], task_id=task_id)
        task, fixed, repository = inputs[task_id] = main_runner.task_inputs(task_id)
        if (repository.initial_commit, sha256_bytes(dump_record(fixed))) != (
                entry["base_revision"], entry["fixed_requirements_hash"]):
            raise ContractError(f"{task_id} public task changed after freezing")
        checks[task_id] = public_runner.public_runner(task_id, identity=identity, transport=transport)
        if checks[task_id].environment_hash != entry["public_environment_sha256"]:
            raise ContractError(f"{task_id} public check environment differs from the frozen manifest")
    manifest = runner.plan(root, **main_freeze.plan_inputs(draft))
    slots = runner.pinned(root, manifest.schedule_ref, manifest.schedule_hash)
    if [{"slot_id": s["slot_id"], "task_id": s["task_id"], "condition": s["condition"]}
            for s in slots] != draft["schedule"]["slots"]:
        raise ContractError("executable schedule differs from the frozen schedule")
    budget = draft["budget"]
    run_root, independent = root / "runs", root / "independent"
    task_ids = frozenset(frozen)

    def launch(slot, run_id, claim):
        task, fixed, repository = inputs[slot.task_id]
        request = RunRequest(run_id, slot.task_id, draft["experiment_id"], repository, fixed,
                             resolve_condition(slot.condition), manifest.config_hash, "research")
        store = RunStore(run_root / run_id, run_id)
        operator = {"schema_version": "1.0", "slot_id": slot.slot_id, "run_id": run_id, "task_id": slot.task_id,
                    "condition": slot.condition, "launch_attempts": 0}
        recoveries = budget["recovery_turns_per_eligible_slot"] if request.policies.external_intervention else 0
        limits = Limits(budget["wall_ms_per_run"], 1 + recoveries, recoveries, budget["max_transitions"],
                        budget["cleanup_reserve_ms"])

        def prepared(manager, handle, dispatch, unchanged):
            runtime = MainRuntime(request, handle, store.root, s4.LIVE_CONFIG, dispatch, live_dev01._event_summary,
                                  live_dev01.EVENT_TYPES, unchanged, experiment_id=draft["experiment_id"],
                                  recovery_cap=recoveries, task_ids=task_ids)
            claim()
            print("MAIN_SLOT_START", slot.slot_id, slot.task_id, slot.condition, flush=True)
            result = run_prepared_experiment(request, runtime=runtime, config=s4.LIVE_CONFIG, manager=manager,
                                             handle=handle, store=store, limits=limits, check=checks[slot.task_id],
                                             supplement_cap=budget["supplement_cap"], baseline_checks=True)
            operator["stop_reason"] = result.stop_reason
            if not unchanged() or result.stop_reason in REVIEW_STOPS:
                raise ContractError(f"Main slot {slot.slot_id} requires review: {result.stop_reason}")

        work_root = Path(mkdtemp(prefix="aah-main-live-"))
        try:
            main_runner.run_slot(request, record=records[slot.task_id], work_root=work_root,
                                 artifact_root=store.root / "artifacts", operator=operator, experiment=prepared,
                                 inherit_console=inherit_console)
            operator["status"] = "LIVE_RUN_RECORDED"
        finally:
            operator.pop("permission_settings", None)
            store_bytes(store.root, "operator.json", canonical_json_bytes(operator))
            if work_root.exists() and not any(work_root.iterdir()):
                work_root.rmdir()

    def verify_run(slot, run_report, directory):
        entry = json.loads(read_regular(directory / "operator.json")[0])
        if ((entry.get("status"), entry["run_id"], entry["task_id"], entry["condition"], entry["launch_attempts"])
                != ("LIVE_RUN_RECORDED", run_report.run_id, slot.task_id, slot.condition, run_report.turns_used)
                or entry["permission_gate"]["status"] != "MATCH" or entry["snapshot_gate"]["status"] != "MATCH"):
            raise ContractError(f"Main slot {slot.slot_id} requires operator review")

    def evaluate(slot, run_report, directory):
        expected = frozen[slot.task_id]["curator_manifest_sha256"]
        request = EvaluationRequest(run_report.run_id, slot.task_id, frozen[slot.task_id]["base_revision"],
                                    expected, run_report.frozen_patch)
        operator_dir = independent / run_report.run_id
        results = operator_dir / "results"
        path = results / f"{run_report.run_id}.json"
        if not path.exists():
            store_bytes(operator_dir, "request.json", dump_record(request))
            curators[slot.task_id].evaluate(operator_dir / "request.json", directory / "artifacts", results)
        verdict = load_record(EvaluationResult, read_regular(path)[0])
        if verdict.private_manifest_hash != expected:
            raise ContractError("curator verdict used a different evaluator manifest")
        print("MAIN_SLOT_RECORDED", slot.slot_id, slot.task_id, slot.condition, verdict.status, flush=True)
        return verdict

    return runner.execute(root, manifest, launch=launch, evaluate=evaluate, verify_run=verify_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", action="store_true",
                      help="run the five slots with synthetic submissions and no agent turns")
    mode.add_argument("--execute", action="store_true", help="run the LOCKED Main freeze with Codex CLI turns")
    parser.add_argument("--curator-kits", type=Path, help="extracted curator kits root (operator environment only)")
    parser.add_argument("--output", type=Path, help="a new directory for --verify")
    parser.add_argument("--docker", action="store_true", help="evaluate in the pinned local Docker worker")
    parser.add_argument("--inherit-console", action="store_true", help="keep the existing console inside the job")
    args = parser.parse_args()
    if args.execute:
        if args.curator_kits is None:
            parser.error("--execute needs --curator-kits")
        try:
            # Check the freeze first so an unlocked Main never touches the curator kits or Codex.
            draft = main_freeze.validate_for_execution()
            curators = {task_id: SubprocessCurator(args.curator_kits / task_id) for task_id in draft["tasks"]}
            summary = execute_live(curators, inherit_console=args.inherit_console)
        except main_freeze.FreezeGateError as error:
            print("MAIN_EXECUTE_NOT_LOCKED", " ".join(error.unmet))
            return 2
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print("MAIN_EXECUTE_BLOCKED", str(error) if isinstance(error, ContractError) else type(error).__name__)
            return 2
        evaluated = sum(row["evaluation_status"] in {"PASS", "FAIL"} for row in summary["runs"])
        print("MAIN_EXECUTE_RECORDED", LIVE_PARENT / main_freeze.EXPERIMENT_ID / "summary.json",
              f"evaluated={evaluated}/{len(summary['runs'])}")
        return 0
    if args.output is None:
        parser.error("--verify needs --output")
    report = verify(args.output, docker=args.docker, inherit_console=args.inherit_console)
    path = args.output / "schedule-verification.json"
    if report["status"] != "PASS":
        print("MAIN_SCHEDULE_" + report["status"], report.get("code", ""), report.get("blocked_slot") or "", path)
        if "reason" in report:
            print(report["reason"], file=sys.stderr)
        return 2 if report["status"] == "BLOCKED" else 1
    evaluated = sum(s["evaluation_status"] == "PASS" for s in report["slots"])
    print("MAIN_SCHEDULE_PASS", path, f"slots=5/5 evaluated={evaluated}/5 agent_turns=0 model_calls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
