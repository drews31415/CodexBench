"""Run the common workflow against a provisioned adapter and public checks."""

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from aah.context.default import ContextOverCap, Message, STRATEGY, render
from aah.diagnosis.detector import Detection, detect, trusted
from aah.diagnosis.flat import diagnose
from aah.domain import (
    CapabilityStatus, CheckResult, CheckStatus, Condition, ContractError, InterventionBoundary,
    Phase, RunRequest, SessionMode, StopReason, TurnRequest, UnitStatus, canonical_json_bytes,
    dump_record, resolve_condition, sha256_bytes,
)
from aah.domain.report import RunReport
from aah.domain.checkpoint import BranchRef
from aah.evidence.ledger import EvidenceLedger
from aah.reporting.store import RunStore
from aah.recovery import adherence, controller
from aah.runtime import RuntimeAdapter, RuntimeConfig, RuntimeNotVerified
from aah.runtime.observer import Observer
from aah.workspace import WorkspaceManager
from aah.workspace.snapshot import checked_path, disjoint

from .budget import Budget, Limits
from . import checkpoint as checkpoints
from .state_machine import StateMachine


def run(request: RunRequest, *, runtime: RuntimeAdapter, config: RuntimeConfig,
        public_root: Path, work_root: Path, results_root: Path, limits: Limits,
        check: Callable[..., CheckResult], supplement_cap: int, baseline_checks: bool = False,
        cancelled: Callable[[], bool] = lambda: False, clock=monotonic, capture_checkpoint: bool = False,
        branch: BranchRef | None = None) -> RunReport:
    budget = Budget(limits, clock)
    if request.policies != resolve_condition(request.policies.condition):
        raise ContractError("workflow requires canonical condition policies")
    if type(supplement_cap) is not int or supplement_cap < 0:
        raise ContractError("invalid supplemental context cap")
    if capture_checkpoint and (branch or request.policies.condition != Condition.B or not request.experiment_id):
        raise ContractError("checkpoint source requires its own common DEFAULT experiment run")
    if branch and (baseline_checks or not request.experiment_id or request.policies.condition == Condition.A):
        raise ContractError("checkpoint branches require a B–E experiment without a new baseline")
    result_root = checked_path(results_root / request.run_id, allow_missing=True)
    disjoint(checked_path(public_root), result_root)
    disjoint(checked_path(work_root, allow_missing=True), result_root)
    store = RunStore(result_root, request.run_id)
    workflow = _Run(request, runtime, config, store, budget, check, supplement_cap, cancelled,
                    workspace_roots=(public_root, work_root), capture_checkpoint=capture_checkpoint, branch=branch)
    return workflow.execute(baseline_checks)


def run_prepared_a(request: RunRequest, *, runtime: RuntimeAdapter, config: RuntimeConfig,
                   manager: WorkspaceManager, handle, store: RunStore, limits: Limits,
                   check: Callable[..., CheckResult]) -> RunReport:
    """Enter S1 with an operator-owned workspace that already passed the live checks."""
    if (request.policies != resolve_condition(Condition.A) or limits.max_turns != 1
            or limits.max_recoveries != 0 or request.run_id != handle.run_id
            or request.run_id != store.run_id or manager.artifact_root != store.root / "artifacts"):
        raise ContractError("prepared workflow requires one initial A turn in the bound workspace")
    disjoint(manager.work_root, store.root)
    disjoint(manager.public_root, store.root)
    workflow = _Run(request, runtime, config, store, Budget(limits), check, 0, lambda: False)
    workflow.manager = manager
    workflow.handle = handle
    return workflow.execute(False)


def run_prepared_experiment(request, *, runtime, config, manager, handle, store, limits, check, supplement_cap,
                            baseline_checks):
    if (not request.experiment_id or request.policies != resolve_condition(request.policies.condition)
            or request.run_id != handle.run_id or store.run_id != request.run_id
            or manager.artifact_root != store.root / "artifacts" or type(supplement_cap) is not int or supplement_cap < 0):
        raise ContractError("experiment workspace, report and policy must agree")
    disjoint(manager.work_root, store.root)
    disjoint(manager.public_root, store.root)
    workflow = _Run(request, runtime, config, store, Budget(limits), check, supplement_cap, lambda: False)
    workflow.manager, workflow.handle = manager, handle
    return workflow.execute(baseline_checks)


class _Run:
    def __init__(self, request, runtime, config, store, budget, check, cap, cancelled, *, workspace_roots=None,
                 capture_checkpoint=False, branch=None):
        self.request = request
        self.runtime = runtime
        self.config = config
        self.store = store
        self.budget = budget
        self.ledger = EvidenceLedger(store)
        self.observer = Observer(store, self.ledger)
        self.machine = StateMachine(store, budget, self.ledger)
        self.workspace_roots = workspace_roots
        self.check = check
        self.cap = cap
        self.cancelled = cancelled
        self.quiet = True
        self.opened = False
        self.handle = None
        self.snapshot = None
        self.history = {}
        self.caps = None
        self.last = None
        self.verified_completion = None
        self.latest_checks = ()
        self.warnings: set[str] = set()
        self.delivered: int | None = 0
        self.incomplete = False
        self.capture_checkpoint = capture_checkpoint
        self.branch = branch
        self.restored = None
        self.common_history = None

    def _check_snapshot(self, turn_id: str | None) -> tuple[CheckResult, ...]:
        results = []
        for check_id in self.request.fixed.approved_check_ids:
            if not self.budget.can_start():
                self.warnings.add("PUBLIC_CHECKS_INCOMPLETE")
                break
            with self.store.measure("PUBLIC_CHECK", self.budget.clock, turn_id):
                check = self.check(self.snapshot, check_id, self.manager.artifact_root,
                                   deadline=self.budget.work_deadline, turn_id=turn_id)
            if (check.check_id, check.snapshot_id, check.turn_id) != (
                check_id, self.snapshot.snapshot_id, turn_id
            ):
                raise ContractError("unexpected public check result")
            results.append(check)
        return tuple(results)

    def _prepare(self, baseline: bool) -> None:
        self.caps = self.runtime.probe_capabilities(self.config)
        if self.handle is None:
            self.manager = WorkspaceManager(
                *self.workspace_roots, self.store.root / "artifacts",
                quiescence_check=lambda workspace: workspace == self.handle and self.quiet,
            )
            patch = None
            if self.branch:
                self.machine.transition(Phase.RESTORE_VERIFY, "VERIFY_PINNED_SOURCE")
                source = self.store.root.parent / self.branch.source_run_id
                self.restored = checkpoints.load_reference(source, self.branch)
                if asdict(self.budget.limits) != asdict(self.restored.remaining_limits):
                    raise ContractError("checkpoint remaining budget mismatch")
                self.common_history = checkpoints.restore(self, source, self.restored)
                patch = self.restored.workspace_bundle
            self.handle = self.manager.prepare(self.request, checkpoint_patch=patch)
        with self.store.measure("SNAPSHOT", self.budget.clock):
            self.snapshot = self.manager.snapshot(self.handle)
        self.history[self.snapshot.snapshot_id] = self.snapshot
        if not self.snapshot.complete:
            raise ContractError("initial snapshot is incomplete")
        if self.restored:
            if self.snapshot.snapshot_id != self.restored.snapshot.snapshot_id:
                raise ContractError("restored snapshot mismatch")
            self.latest_checks = tuple(c for c in self.ledger.view().checks if c.turn_id == self.restored.last_turn_id)
        opener = getattr(self.runtime, "open_checkpoint_run", self.runtime.open_run) if self.branch else self.runtime.open_run
        opener(self.request, self.handle)
        self.opened = True
        if baseline:
            for check in self._check_snapshot(None):
                self.observer.collect_check(check, "baseline")

    def _intervention(self, ref: str, status: str, **values) -> None:
        self.store.append("interventions", {"intervention_id": ref, "event": status, **values})

    def _dispatch(self, message: Message | None, kind: str, *, intervention=None, checkpoint=None,
                  strategy=None, detection=None) -> None:
        prefix = "branch-turn" if self.branch else "turn"
        turn_id = f"{prefix}-{len(self.budget.turns) + 1:04}"
        self.budget.reserve(turn_id, intervention_id=intervention, checkpoint=checkpoint)
        if intervention is not None:
            self._intervention(intervention, "RESERVED", turn_id=turn_id, checkpoint_key=checkpoint)
        self.machine.transition(Phase.RUN, kind, snapshot=self.snapshot.snapshot_id, intervention=intervention)
        session = (self.last.session_ref if self.last and (
            kind == "NATIVE_CONTINUE" or self.config.session_mode == SessionMode.SAME_SESSION) else None)
        turn = TurnRequest(
            self.request.run_id, turn_id, self.handle, session, kind,
            message.text if message else None, message.digest if message else None, intervention,
            self.budget.work_ms, "manifest.json",
        )
        self.quiet = False
        execute = self.runtime.run_unit if kind == "INITIAL" else self.runtime.continue_unit
        started = self.budget.clock()
        outcome = execute(turn, lambda event: self.observer.receive(event, turn_id))
        runtime_ms = max(0, int((self.budget.clock() - started) * 1000))
        if outcome.turn_id != turn_id:
            raise ContractError("runtime outcome belongs to another turn")
        self.last = outcome
        self.quiet = outcome.process_quiescent
        if intervention is not None:
            if (outcome.error and outcome.error.code == "DELIVERY_UNKNOWN") or outcome.status == UnitStatus.UNKNOWN:
                self.delivered = None
                delivery = "UNKNOWN_DELIVERY"
            elif not outcome.event_refs and outcome.status != UnitStatus.COMPLETED:
                delivery = "DELIVERY_FAILED"
            else:
                if self.delivered is not None:
                    self.delivered += 1
                delivery = "DELIVERED"
            self._intervention(intervention, delivery, turn_id=turn_id)
            self.observer.intervention(turn_id, intervention, strategy.strategy_id,
                                       tuple(s.kind for s in detection.signals),
                                       tuple(sorted({a for s in detection.signals for a in s.anchors})),
                                       self.snapshot.snapshot_id, delivery)

        self.machine.transition(Phase.OBSERVE, "TURN_RETURNED")
        before = self.snapshot
        self.latest_checks = ()
        self.verified_completion = None
        accounting = {"usage_mode": self.caps.usage_mode, "fresh_session": session is None,
                      "stage": kind, "elapsed_ms": runtime_ms}
        if self.quiet:
            with self.store.measure("SNAPSHOT_DIFF", self.budget.clock, turn_id):
                self.snapshot = self.manager.snapshot(self.handle)
                diff = self.manager.diff(before, self.snapshot)
            if self.snapshot.complete:
                self.history[self.snapshot.snapshot_id] = self.snapshot
            if (outcome.status == UnitStatus.COMPLETED and outcome.error is None
                    and not self.cancelled() and self.snapshot.complete):
                self.latest_checks = self._check_snapshot(turn_id)
            self.verified_completion = self.observer.collect(
                outcome, before, self.snapshot, diff, self.latest_checks, **accounting)
        else:
            self.observer.collect(outcome, None, None, None, (), **accounting)
        if intervention is not None:
            measured = adherence.assess(strategy, self.ledger.view().records, turn_id,
                                        delivered=delivery == "DELIVERED", before_snapshot=before.snapshot_id)
            self._intervention(intervention, "OUTCOME_OBSERVED", turn_id=turn_id,
                               ledger_cursor=self.ledger.cursor, turn_status=outcome.status, adherence=measured)

    def _stop(self) -> StopReason | None:
        if not self.quiet:
            self.warnings.add("PROCESS_NOT_QUIESCENT")
            return StopReason.SAFETY_VIOLATION
        if self.cancelled() or self.last.status == UnitStatus.INTERRUPTED:
            return StopReason.CANCELED
        if self.last.status == UnitStatus.TIMEOUT or not self.budget.can_start():
            return StopReason.GLOBAL_LIMIT
        if self.last.status != UnitStatus.COMPLETED or self.last.error is not None:
            if self.last.error:
                self.warnings.add(self.last.error.code)
            return StopReason.RUNTIME_ERROR
        if not self.snapshot.complete:
            self.warnings.add("SNAPSHOT_INCOMPLETE")
            return StopReason.NO_SAFE_CONTINUATION
        return None

    def _checkpoint(self, detection: Detection) -> str:
        checks = [(check.check_id, check.check_definition_hash, check.environment_hash, check.status,
                   sorted((case.test_id, case.status, case.fingerprint) for case in check.test_cases))
                  for check in self.latest_checks if trusted(check)]
        searches = [signal.meaning for signal in detection.signals if signal.kind == "T2"]
        return sha256_bytes(canonical_json_bytes([self.snapshot.snapshot_id, sorted(checks), searches]))

    def _gate(self, detection: Detection) -> tuple[str, str | None]:
        if not self.request.policies.external_intervention:
            return "POLICY_NATIVE", None
        if not detection.primary:
            return "NO_TRIGGER", None
        if self.budget.turn_limit():
            return "GLOBAL_LIMIT", None
        if self.budget.recovery_limit():
            return "RECOVERY_CAP", None
        if (self.caps.features.get("RT03") != CapabilityStatus.VERIFIED
                or self.caps.unit_boundary == InterventionBoundary.UNSUPPORTED
                or self.config.session_mode not in self.caps.session_modes
                or self.config.session_mode == SessionMode.SAME_SESSION and not self.last.session_ref):
            return "NO_SUPPORTED_CONTINUATION", None
        key = self._checkpoint(detection)
        return ("DUPLICATE_CHECKPOINT", None) if key in self.budget.recoveries else ("ELIGIBLE", key)

    def _render(self, *, detection=None, diagnosis=None, intervention=None, strategy=None, graph=None) -> Message:
        started = self.budget.clock()
        stage = "RECOVERY" if detection is not None else "INITIAL"
        attributes = {"stage": stage, "intervention_id": intervention,
                      "strategy_id": strategy.strategy_id if strategy else None}
        try:
            message = render(self.request, self.snapshot, cap=self.cap, detection=detection,
                             diagnosis=diagnosis, artifact_root=self.manager.artifact_root,
                             strategy=strategy, graph_diagnosis=graph, common_history=self.common_history)
        except ContextOverCap as exc:
            self.store.append("context", {**attributes, "status": "CORE_OVER_CAP",
                                          "cap_tokens": exc.cap,
                                          "estimated_core_tokens": exc.estimated_core_tokens,
                                          "elapsed_ms": max(0, int((self.budget.clock() - started) * 1000))})
            raise
        self.store.append("context", {**attributes, "status": "OK", **message.receipt(),
                                      "elapsed_ms": max(0, int((self.budget.clock() - started) * 1000))})
        return message

    def _graph(self, view, detection):
        from aah.graphs import service

        started = self.budget.clock()
        try:
            return service.update(self.snapshot, self.history, view, detection,
                                  approved=self.request.fixed.approved_check_ids, store=self.store,
                                  deadline=self.budget.work_deadline, clock=self.budget.clock)
        except service.GraphUnavailable as exc:
            self.warnings.add("GRAPH_UNAVAILABLE")
            return None, {"status": "unavailable", "fallback_reason": str(exc) or type(exc).__name__,
                          "elapsed_ms": max(0, int((self.budget.clock() - started) * 1000))}

    def _work(self, baseline: bool) -> StopReason:
        if self.cancelled():
            return StopReason.CANCELED
        self._prepare(baseline)
        if self.cancelled():
            return StopReason.CANCELED
        if not self.budget.can_start():
            return StopReason.GLOBAL_LIMIT
        if not self.restored:
            if self.request.policies.external_intervention:
                self.machine.transition(Phase.INITIAL_CONTEXT, self.request.policies.initial_context)
            initial = self._render()
            if not self.budget.can_start():
                return StopReason.GLOBAL_LIMIT
            self._dispatch(initial, "INITIAL")
        while True:
            if self.last is not None and (stop := self._stop()) is not None:
                return stop
            self.machine.transition(Phase.DECIDE, "OBSERVATION_RECORDED", snapshot=self.snapshot.snapshot_id)
            view = self.ledger.view()
            turn_id = self.last.turn_id if self.last else self.restored.last_turn_id
            detection = detect(view, self.snapshot, turn_id, self.request.fixed.approved_check_ids)
            self.warnings.update(detection.coverage_gaps)
            decision, checkpoint = self._gate(detection)
            if self.restored and not self.last and (
                    checkpoint != self.restored.source_gate_key
                    or sha256_bytes(checkpoints.trigger_bytes(detection)) != self.restored.frozen_trigger_hash):
                raise ContractError("restored trigger or eligibility differs from source")
            if self.capture_checkpoint and checkpoint:
                checkpoints.capture(self, detection, checkpoint, view)
                return StopReason.COMPLETED
            if checkpoint is None:
                if not self.budget.can_start():
                    return StopReason.GLOBAL_LIMIT
                if self.last and self.last.native_continuation_supported:
                    if self.budget.turn_limit():
                        return StopReason.GLOBAL_LIMIT
                    self._dispatch(None, "NATIVE_CONTINUE")
                    continue
                if detection.primary and self.request.policies.external_intervention:
                    self.warnings.add(decision)
                    if decision == "GLOBAL_LIMIT":
                        return StopReason.GLOBAL_LIMIT
                    return StopReason.NO_SAFE_CONTINUATION
                if self.public_status() in (CheckStatus.INFRA_ERROR, CheckStatus.TIMEOUT):
                    return StopReason.RUNTIME_ERROR
                return StopReason.COMPLETED
            if not self.budget.can_start():
                return StopReason.GLOBAL_LIMIT
            ref = f"intervention-{len(self.budget.recoveries) + 1:04}"
            policy = self.request.policies
            self.machine.transition(Phase.DIAGNOSE, policy.diagnosis)
            diagnosis = diagnose(detection, view, self.snapshot, self.request.fixed.approved_check_ids)
            graph, receipt = None, None
            if policy.diagnosis == "GRAPH":
                with self.store.measure("GRAPH", self.budget.clock):
                    graph, receipt = self._graph(view, detection)
            approved = self.request.fixed.approved_check_ids
            strategy = controller.select(detection, graph, approved,
                                         changed_paths={r.payload["path"] for r in diagnosis.changes})
            if graph and strategy.strategy_id == STRATEGY:
                receipt["fallback_reason"] = "INSUFFICIENT_TYPED_EVIDENCE"
            self._intervention(ref, "DECIDED", trigger_ids=[signal.kind for signal in detection.signals],
                               anchors=diagnosis.anchors, strategy=strategy.strategy_id, checkpoint_key=checkpoint,
                               evidence_ids=strategy.evidence_ids, targets=strategy.targets, check_ids=approved,
                               **({"graph": receipt} if receipt else {}))
            if not self.budget.can_start():
                return StopReason.GLOBAL_LIMIT
            self.machine.transition(Phase.SELECT_STRATEGY, strategy.strategy_id)
            if not self.budget.can_start():
                return StopReason.GLOBAL_LIMIT
            self.machine.transition(Phase.RECOVERY_CONTEXT, self.request.policies.recovery_context)
            try:
                message = self._render(detection=detection, diagnosis=diagnosis, intervention=ref,
                                       strategy=strategy, graph=graph)
            except ContextOverCap:
                if strategy.strategy_id == STRATEGY:
                    raise
                self._intervention(ref, "FALLBACK", reason="CORE_OVER_CAP",
                                   from_strategy=strategy.strategy_id, strategy=STRATEGY)
                strategy = controller.select(detection, None, approved)
                with self.store.measure("FALLBACK_CONTEXT", self.budget.clock):
                    message = self._render(detection=detection, diagnosis=diagnosis, intervention=ref, strategy=strategy)
            self._intervention(ref, "PACKAGE_READY", message_hash=message.digest, strategy=strategy.strategy_id,
                               directive=strategy.receipt(detection))
            if self.cancelled():
                return StopReason.CANCELED
            if not self.budget.can_start():
                return StopReason.GLOBAL_LIMIT
            self._dispatch(message, "RECOVERY", intervention=ref, checkpoint=checkpoint,
                           strategy=strategy, detection=detection)
            self.common_history = None

    def public_status(self) -> CheckStatus:
        statuses = {check.status for check in self.latest_checks if trusted(check)}
        if CheckStatus.FAIL in statuses:
            return CheckStatus.FAIL
        for status in (CheckStatus.INFRA_ERROR, CheckStatus.TIMEOUT):
            if any(check.status == status for check in self.latest_checks):
                return status
        if (self.latest_checks and all(trusted(check) for check in self.latest_checks)
                and len(self.latest_checks) == len(self.request.fixed.approved_check_ids)):
            return CheckStatus.PASS
        return CheckStatus.NOT_RUN if not self.latest_checks else CheckStatus.UNKNOWN

    def _finalize(self, reason: StopReason):
        patch = None
        if self.opened:
            if not self.quiet or reason in (StopReason.CANCELED, StopReason.GLOBAL_LIMIT):
                try:
                    self.runtime.cancel(self.request.run_id)
                except Exception:
                    self.warnings.add("CANCELLATION_UNCONFIRMED")
            try:
                self.quiet = self.runtime.close_run(self.request.run_id).confirmed
            except Exception:
                self.quiet = False
        if not self.quiet:
            self.incomplete = True
            self.warnings.add("CLEANUP_UNCONFIRMED")
            return StopReason.SAFETY_VIOLATION, None
        if self.handle is not None:
            try:
                if self.budget.turns:
                    final = self.manager.snapshot(self.handle)
                    if self.snapshot is None or final.snapshot_id != self.snapshot.snapshot_id:
                        self.verified_completion = None
                        self.latest_checks = ()
                    if final.complete:
                        with self.store.measure("FREEZE_PATCH", self.budget.clock):
                            patch = self.manager.freeze_patch(self.handle, final)
                    else:
                        self.warnings.add("SNAPSHOT_INCOMPLETE")
                        self.incomplete = True
            except (OSError, ValueError):
                self.incomplete = True
                self.warnings.add("PATCH_FREEZE_FAILED")
                reason = StopReason.ARTIFACT_ERROR
            finally:
                try:
                    self.manager.cleanup(self.handle)
                except (OSError, ValueError):
                    self.incomplete = True
                    self.warnings.add("WORKSPACE_CLEANUP_FAILED")
        return reason, patch

    def execute(self, baseline: bool) -> RunReport:
        manifest_hash = self.store.save_manifest({
            "run_id": self.request.run_id, "task_id": self.request.task_id,
            "experiment_id": self.request.experiment_id, "condition": self.request.policies.condition,
            "config_hash": self.request.config_hash, "policy": asdict(self.request.policies),
            "initial_commit": self.request.repository.initial_commit,
            "fixed_requirements_hash": sha256_bytes(dump_record(self.request.fixed)),
            "fixed_instruction_hash": self.request.fixed.mandatory_hash,
            "approved_check_ids": self.request.fixed.approved_check_ids,
            "permission_profile_id": self.request.fixed.permission_profile_id,
            "runtime": asdict(self.config), "limits": asdict(self.budget.limits),
            "supplement_cap": self.cap, "baseline_checks": baseline,
            "checkpoint_source": self.capture_checkpoint, "checkpoint_branch": self.branch,
        })
        try:
            reason = self._work(baseline)
        except KeyboardInterrupt:
            reason = StopReason.CANCELED
        except ContextOverCap:
            reason = StopReason.NO_SAFE_CONTINUATION
            self.warnings.add("CORE_OVER_CAP")
        except RuntimeNotVerified as exc:
            reason = StopReason.RUNTIME_ERROR
            self.warnings.add(exc.error.code)
        except Exception as exc:
            self.incomplete = True
            reason = (StopReason.ARTIFACT_ERROR if isinstance(exc, OSError) else
                      StopReason.PREPARATION_ERROR if self.machine.state.phase == Phase.INIT else StopReason.RUNTIME_ERROR)
            self.warnings.add("WORKFLOW_" + type(exc).__name__.upper())
        reason, patch = self._finalize(reason)
        if self.budget.clock() > self.budget.deadline:
            self.warnings.add("CLEANUP_RESERVE_EXCEEDED")
        try:
            if self.machine.state.phase == Phase.RUN:
                self.machine.transition(Phase.OBSERVE, "TURN_ABORTED")
            self.machine.transition(Phase.FINALIZE, reason, stop=reason)
            self.machine.transition(Phase.REPORT, reason)
        except (OSError, ValueError):
            self.incomplete = True
            self.warnings.add("TRANSITION_WRITE_FAILED")
            reason = StopReason.ARTIFACT_ERROR
        report = RunReport(
            self.request.run_id, self.request.policies.condition, manifest_hash, reason,
            self.verified_completion, self.public_status(), patch,
            bool(patch and patch.applicable and not self.incomplete), len(self.budget.turns),
            len(self.budget.recoveries), self.delivered, self.observer.usage.summary(len(self.budget.turns)),
            self.budget.state().elapsed_ms, tuple(self.budget.recoveries.values()),
            "incomplete" if self.incomplete else "complete", tuple(sorted(self.warnings)),
        )
        if self.incomplete:
            self.store.mark_incomplete(reason)
        try:
            self.store.publish(report)
        except (OSError, ValueError):
            if not self.incomplete:
                self.store.mark_incomplete("REPORT_WRITE_FAILED")
            raise
        return report
