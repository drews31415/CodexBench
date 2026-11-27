"""Run the S4 DEV-01 comparison through the workflow and a separate evaluator."""

import argparse
import json
import subprocess
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from contextlib import contextmanager
from tempfile import mkdtemp
from uuid import UUID, uuid4

from aah.domain import (CapabilityStatus, CheckStatus, Condition, ContractError, EvaluationResult, EvaluationStatus,
                        InterventionBoundary, RuntimeCapabilities, SessionMode, UsageMode, canonical_json_bytes,
                        dump_record, load_record, resolve_condition, sha256_bytes)
from aah.experiments import runner
from aah.reporting.store import RunStore
from aah.runtime import RuntimeConfig
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run, run_prepared_experiment
from aah.workspace.patch import store_bytes
from evaluation.runner import EvaluationRequest, fixture_manifest_hash
from evaluation.smoke import CHECKOUT, _evaluate_separately, fixture_request

from . import driver, live_dev01, live_s1, s1
from .public_checks import PUBLIC_BUNDLES, run_approved


LIVE_ID = "s4-dev01-live-20261126"
LIVE_ROOT = CHECKOUT / "results" / "s4-live" / LIVE_ID
CAP = 2048
LIVE_CONFIG = RuntimeConfig(live_dev01.MODEL, live_dev01.EFFORT, "0.166.0", session_mode=SessionMode.SAME_SESSION)
LIVE_PROBE = "results/s0-verification/live-dev01-a0c90602-5e15-46d6-b5e1-a746dcbe6972/report.json"
LIVE_LIMITS = Limits(225_000, 2, 1, 100, 15_000)
OFFLINE_LIMITS = Limits(30_000, 2, 1, 100, 5_000)


@contextmanager
def work_directory():
    directory = Path(mkdtemp(prefix="aah-s4-"))
    try:
        yield directory
    finally:
        # WorkspaceManager owns cleanup and may retain a non-quiescent workspace.
        if not any(directory.iterdir()):
            directory.rmdir()


def code_version():
    paths = [p for base in ("src/aah", "benchmarks/smoke", "evaluation")
             for p in (CHECKOUT / base).rglob("*.py")]
    return sha256_bytes(canonical_json_bytes({p.relative_to(CHECKOUT).as_posix():
                        sha256_bytes(p.read_bytes().replace(b"\r\n", b"\n")) for p in paths}))


class LiveRuntime(live_s1.Dev01EventRuntime):
    session_id = None
    followed = False

    def probe_capabilities(self, config):
        if config != LIVE_CONFIG:
            raise ContractError("live runtime settings differ from the approved smoke profile")
        return RuntimeCapabilities("codex_cli", config.runtime_version, config.model_id,
                                   InterventionBoundary.POST_COMPLETION, (SessionMode.SAME_SESSION,),
                                   {"RT03": CapabilityStatus.VERIFIED}, UsageMode.CUMULATIVE, "operator.json")

    def open_run(self, request, workspace):
        if (self.opened or request != self.request or request.experiment_id != LIVE_ID or request.task_id != "DEV-01"
                or workspace != self.workspace or not self.safe_to_finalize()
                or request.policies != resolve_condition(request.policies.condition)):
            raise ContractError("unbound S4 live run")
        self.opened = True

    def _turn(self, turn, on_event, *, followup):
        if (not self.opened or turn.run_id != self.request.run_id or turn.workspace != self.workspace
                or not self.safe_to_finalize() or not turn.message or turn.deadline_ms_remaining <= 0
                or sha256_bytes(turn.message.encode()) != turn.message_hash):
            raise ContractError("unbound S4 turn")
        if followup:
            if (self.followed or not self.used or not self.session_id or turn.session_ref != self.session_id
                    or self.request.policies.condition == Condition.A or turn.kind != "RECOVERY" or not turn.intervention_id):
                raise ContractError("S4 recovery exceeds the approved scope")
            self.followed = True
        else:
            if self.used or turn.kind != "INITIAL" or turn.session_ref is not None or turn.intervention_id:
                raise ContractError("S4 initial turn already used or invalid")
            self.used = True
        with (self.directory / (turn.kind + "-dispatch.json")).open("xb") as out:
            out.write(canonical_json_bytes({"turn_id": turn.turn_id, "message_hash": turn.message_hash,
                                            "session_ref": turn.session_ref, "intervention_id": turn.intervention_id}))
        result = self.launch(turn.message.encode(), turn.deadline_ms_remaining / 1000,
                             persist=self.request.policies.external_intervention, session_id=turn.session_ref)
        outcome = self.outcome(turn, result, on_event)
        if outcome.status == "COMPLETED":
            if not outcome.session_ref or str(UUID(outcome.session_ref)) != outcome.session_ref:
                raise ContractError("missing verified CLI session identity")
            if followup and outcome.session_ref != self.session_id:
                raise ContractError("resumed a different CLI session")
            self.session_id = outcome.session_ref
        return outcome

    def run_unit(self, turn, on_event):
        return self._turn(turn, on_event, followup=False)

    def continue_unit(self, turn, on_event):
        return self._turn(turn, on_event, followup=True)


def _plan(root, experiment_id, live_run):
    task = fixture_request("DEV-01")
    config = LIVE_CONFIG if live_run else s1.CONFIG
    limits = LIVE_LIMITS if live_run else OFFLINE_LIMITS
    settings = {"profile": "live" if live_run else "synthetic", "runtime": asdict(config), "limits": asdict(limits),
                "supplement_cap": CAP, "baseline_checks": True, "token_limit_mode": "observe_only",
                "max_extra_payment_krw": 0 if live_run else None}
    if live_run:
        raw = (CHECKOUT / LIVE_PROBE).read_bytes()
        inspection = json.loads(raw)
        if inspection["codex_version"] != LIVE_CONFIG.runtime_version or not inspection["runtime_checks"]["RT03"]["passed"]:
            raise ContractError("S4 requires the verified DEV-01 continuation profile")
        probe = {"profile": LIVE_CONFIG.runtime_version, "ref": LIVE_PROBE, "report_sha256": sha256_bytes(raw)}
    else:
        probe = json.loads(dump_record(s1.SyntheticAdapter(root).probe_capabilities(config)))
    return runner.plan(root, experiment_id, tasks={"DEV-01": {"base": task.repository.initial_commit,
                       "task_hash": task.config_hash, "fixed_hash": sha256_bytes(dump_record(task.fixed)),
                       "public_checks_hash": PUBLIC_BUNDLES["DEV-01"],
                       "evaluator_manifest_hash": fixture_manifest_hash("DEV-01", task.repository.initial_commit,
                                                                       in_docker=live_run, dev01_candidate=live_run)}},
                       repetitions=1, seed=20261126, config=settings, probe=probe,
                       environment_hash=driver.environment_hash(in_docker=live_run), code_version=code_version())


def _evaluate(root, slot, report, run_root, live_run):
    patch = report.frozen_patch
    if not report.evaluable or patch is None:
        return EvaluationResult(report.run_id, slot.task_id, patch.base_commit if patch else None,
                                patch.patch_hash if patch else None, None, None, None, EvaluationStatus.NOT_EVALUABLE,
                                CheckStatus.NOT_RUN, CheckStatus.NOT_RUN, CheckStatus.NOT_RUN, None, "NO_EVALUABLE_PATCH", None)
    directory = root / "independent" / report.run_id
    manifest_hash = fixture_manifest_hash(slot.task_id, patch.base_commit, in_docker=live_run, dev01_candidate=live_run)
    envelope = EvaluationRequest(report.run_id, slot.task_id, patch.base_commit, manifest_hash, patch)
    store_bytes(directory, "request.json", dump_record(envelope))
    results = directory / "results"
    path = results / (report.run_id + ".json")
    if not path.exists():
        _evaluate_separately(directory / "request.json", run_root / "artifacts", results,
                             in_docker=live_run, dev01_candidate=live_run)
    result = load_record(EvaluationResult, path.read_bytes())
    if result.private_manifest_hash not in (None, manifest_hash):
        raise ContractError("independent evaluation used a foreign manifest")
    return result


def execute(root, experiment_id, *, live_run=False, inherit_console=False):
    root = Path(root).absolute()
    if live_run and (root != LIVE_ROOT or experiment_id != LIVE_ID):
        raise ContractError("S4 live approval is bound to one DEV-01 schedule")
    manifest = _plan(root, experiment_id, live_run)
    runs = root / manifest.output_root
    tasks = runner.pinned(root, manifest.task_manifest_ref, manifest.task_manifest_hash)

    def launch(slot, run_id, claim):
        task = fixture_request(slot.task_id)
        pinned = tasks[slot.task_id]
        if ((task.repository.initial_commit, task.config_hash, sha256_bytes(dump_record(task.fixed)),
             PUBLIC_BUNDLES[slot.task_id]) != (pinned["base"], pinned["task_hash"], pinned["fixed_hash"],
                                               pinned["public_checks_hash"])):
            raise ContractError("development task differs from the pinned inputs")
        request = replace(task, run_id=run_id, experiment_id=experiment_id,
                          config_hash=manifest.config_hash, policies=resolve_condition(slot.condition), mode="research")
        with work_directory() as temp:
            if not live_run:
                claim()
                return run(request, runtime=s1.SyntheticAdapter(runs), config=s1.CONFIG,
                           public_root=request.repository.source_path, work_root=Path(temp), results_root=runs,
                           limits=OFFLINE_LIMITS, check=run_approved, supplement_cap=CAP, baseline_checks=True)
            store = RunStore(runs / run_id, run_id)
            operator = {"schema_version": "1.0", "run_id": run_id, "condition": slot.condition, "launch_attempts": 0}

            def prepared(manager, handle, dispatch, unchanged):
                runtime = LiveRuntime(request, handle, store.root, LIVE_CONFIG, dispatch, live_dev01._event_summary,
                                      live_dev01.EVENT_TYPES, unchanged)
                claim()
                result = run_prepared_experiment(request, runtime=runtime, config=LIVE_CONFIG, manager=manager,
                                                 handle=handle, store=store, limits=LIVE_LIMITS, supplement_cap=CAP,
                                                 baseline_checks=True, check=partial(run_approved, dev01_candidate=True))
                operator["stop_reason"] = result.stop_reason
                if result.stop_reason == "SAFETY_VIOLATION" or not unchanged():
                    operator["isolation_review_required"] = True
                    raise live_dev01.SmokeBlocked("S4 run requires isolation review")
            try:
                live_dev01._run(store.root, Path(temp), request, operator, execute=False,
                                inherit_console=inherit_console, experiment=prepared)
                operator["status"] = "LIVE_RUN_RECORDED"
            finally:
                store_bytes(store.root, "operator.json", canonical_json_bytes(operator))
    def verify_operator(slot, report, directory):
        receipt = directory / "operator.json"
        if not receipt.exists():
            raise ContractError(f"{slot.slot_id} is awaiting its operator receipt")
        entry = json.loads(receipt.read_bytes())
        if (entry.get("status") != "LIVE_RUN_RECORDED" or entry.get("isolation_review_required")
                or (entry["run_id"], entry["condition"], entry.get("stop_reason"))
                != (report.run_id, slot.condition, report.stop_reason)):
            raise ContractError(f"{slot.slot_id} requires operator review")
    return runner.execute(root, manifest, launch=launch, verify_run=verify_operator if live_run else None,
                          evaluate=lambda slot, report, directory: _evaluate(root, slot, report, directory, live_run))


def live(*, inherit_console=False):
    # Test the operator's actual boundaries before pinning environment/creating slots.
    directory = LIVE_ROOT.parent / "preflights" / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    operator = {"schema_version": "1.0", "launch_attempts": 0}
    try:
        with work_directory() as temp:
            live_dev01._run(directory, Path(temp), fixture_request("DEV-01"), operator,
                            execute=False, inherit_console=inherit_console)
        summary = execute(LIVE_ROOT, LIVE_ID, live_run=True, inherit_console=inherit_console)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        operator.update(status="BLOCKED", reason=str(exc))
        print("S4_LIVE_BLOCKED", str(exc), directory / "operator.json")
        return 2
    finally:
        store_bytes(directory, "operator.json", canonical_json_bytes(operator))
    lt04 = all(r["turns"] and r["evaluable"] and r["artifact_status"] == "complete"
               and r["evaluation_status"] in {"PASS", "FAIL"} and r["public_check_status"] in {"PASS", "FAIL"}
               and r["stop_reason"] in {"COMPLETED", "GLOBAL_LIMIT", "NO_SAFE_CONTINUATION"}
               for r in summary["runs"])
    lt02 = False
    for row in summary["runs"]:
        if row["condition"] != "E" or not row["recoveries"]:
            continue
        path = LIVE_ROOT / "runs" / row["run_id"]
        journal = [json.loads(line) for line in (path / "interventions.jsonl").read_bytes().splitlines()]
        checks = [json.loads(line) for line in (path / "checks.jsonl").read_bytes().splitlines()]
        lt02 = (any(c["turn_id"] == "turn-0001" and c["source_class"] == "TRUSTED_RUNNER" and c["status"] == "FAIL"
                    for c in checks) and any(j["event"] == "DELIVERED" for j in journal)
                and any(j["event"] == "OUTCOME_OBSERVED" and j["turn_status"] == "COMPLETED" for j in journal))
    gate = {"schema_version": "1.0", "LT02": lt02, "LT04": lt04, "review": "pending"}
    store_bytes(LIVE_ROOT, "live-checks.json", canonical_json_bytes(gate))
    print("S4_LIVE_RECORDED", LIVE_ROOT / "live-checks.json", f"LT02={lt02} LT04={lt04}")
    return 0 if lt02 and lt04 else 1


def replay_manifest(path):
    path = Path(path).absolute()
    if path.name != "experiment.json":
        raise ContractError("expected a pinned experiment.json")
    manifest = load_record(runner.ExperimentManifest, path.read_bytes())
    config = runner.pinned(path.parent, manifest.locked_config_ref, manifest.config_hash)
    if config.get("profile") != "synthetic":
        raise ContractError("use the approved DEV-01 operator for a live manifest")
    return execute(path.parent, manifest.experiment_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--checkpoint", action="store_true", help="exercise four recovery branches from one synthetic source")
    args = parser.parse_args()
    rid = str(uuid4())
    root = args.output_dir / rid
    if args.checkpoint:
        from .s4_checkpoint import verify
        verify(root)
        print("S4_CHECKPOINT_VERIFIED", root / "summary.json")
        return 0
    summary = execute(root, rid)
    print("S4_SYNTHETIC", root / "summary.json", [(r["condition"], r["evaluation_status"]) for r in summary["runs"]])
    return 0 if all(r["evaluation_status"] in {"PASS", "FAIL"} for r in summary["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
