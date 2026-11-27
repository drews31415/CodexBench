"""Capture the first eligible public failure and restore its verified, external state."""

from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from aah.context.candidates import Candidate, public_path
from aah.domain import Condition, ContractError, SessionMode, canonical_json_bytes, dump_record, load_record, resolve_condition, sha256_bytes
from aah.domain.checkpoint import BranchRef, CheckpointRecord, CommonEvidence, RemainingLimits
from aah.evidence.ledger import EvidenceView
from aah.workspace.patch import digest_ref, read_manifest, store_bytes, verified_bytes
from aah.workspace.snapshot import read_regular


def environment(config, capabilities):
    return sha256_bytes(canonical_json_bytes([config.adapter, config.model_id, config.runtime_version,
                                              config.reasoning_effort, capabilities.adapter, capabilities.unit_boundary,
                                              capabilities.session_modes, capabilities.features, capabilities.usage_mode]))


def trigger_bytes(detection):
    return canonical_json_bytes(asdict(detection))


def history_bytes(view):
    return canonical_json_bytes({
        "checks": [[c.turn_id, c.check_id, c.status] for c in view.checks],
        "changes": [[r.turn_id, r.payload["path"]] for r in view.records if r.kind == "CHANGE"
                    and r.observation_status == "observed" and public_path(r.payload["path"])],
    })


def capture(workflow, detection, key, view):
    request, budget, store = workflow.request, workflow.budget, workflow.store
    patch = workflow.manager.freeze_patch(workflow.handle, workflow.snapshot)
    evidence = dump_record(CommonEvidence(view.records, view.checks))
    history = history_bytes(view)
    digests = []
    for kind, data in (("common-evidence", evidence), ("triggers", trigger_bytes(detection)), ("common-history", history)):
        digest = sha256_bytes(data)
        store_bytes(store.root, digest_ref(kind, digest), data)
        digests.append(digest)
    remaining = RemainingLimits(budget.state().remaining_ms, budget.limits.max_turns - len(budget.turns),
                                budget.limits.max_recoveries - len(budget.recoveries),
                                budget.limits.max_transitions - budget.transitions, budget.limits.cleanup_reserve_ms)
    record = CheckpointRecord(str(uuid4()), request.run_id, request.experiment_id, request.task_id, workflow.snapshot,
                              tuple(workflow.history.values()), patch, digests[0], digests[1],
                              tuple(s.kind.value for s in detection.signals), tuple(sorted({r for s in detection.signals for r in s.anchors})),
                              key, sha256_bytes(dump_record(request.fixed)), sha256_bytes(dump_record(request.policies)),
                              request.config_hash, environment(workflow.config, workflow.caps), digests[2],
                              workflow.last.turn_id, remaining, workflow.cap,
                              tuple(r.payload["usage_record_id"] for r in view.records if r.kind == "USAGE"), "observe_only", False)
    store_bytes(store.root, "checkpoint.json", dump_record(record))


def load_reference(source: Path, ref: BranchRef):
    data = read_regular(source / "checkpoint.json")[0]
    if sha256_bytes(data) != ref.checkpoint_hash:
        raise ContractError("checkpoint hash mismatch")
    record = load_record(CheckpointRecord, data)
    if (record.source_run_id, record.checkpoint_id) != (ref.source_run_id, ref.checkpoint_id):
        raise ContractError("branch points to a different checkpoint")
    return record


def restore(workflow, source, record):
    request, manager = workflow.request, workflow.manager
    if (request.policies.condition == Condition.A or request.run_id == record.source_run_id
            or (request.task_id, request.experiment_id, request.config_hash, request.repository.initial_commit,
                request.repository.repo_id) != (record.task_id, record.experiment_id, record.config_hash,
                                                record.snapshot.base_commit, record.snapshot.repo_id)
            or sha256_bytes(dump_record(request.fixed)) != record.fixed_requirements_hash
            or sha256_bytes(dump_record(resolve_condition(Condition.B))) != record.initial_policy_hash
            or workflow.cap != record.supplement_cap or workflow.config.session_mode != SessionMode.NEW_SESSION
            or environment(workflow.config, workflow.caps) != record.environment_hash):
        raise ContractError("checkpoint and branch inputs differ")
    source_artifacts = source / "artifacts"
    def copy(kind, digest):
        store_bytes(manager.artifact_root, digest_ref(kind, digest), verified_bytes(source_artifacts, kind, digest))
    for snapshot in record.snapshots:
        if (snapshot.repo_id, snapshot.base_commit, snapshot.complete) != (record.snapshot.repo_id, record.snapshot.base_commit, True):
            raise ContractError("checkpoint history has a foreign snapshot")
        manifest = read_manifest(source_artifacts, snapshot)
        copy("snapshots", snapshot.snapshot_id)
        for entry in manifest["files"]:
            if entry["state"] == "present":
                copy("blobs", entry["sha256"])
        workflow.history[snapshot.snapshot_id] = snapshot
    copy("patches", record.workspace_bundle.patch_hash)
    data = load_record(CommonEvidence, verified_bytes(source, "common-evidence", record.common_evidence_hash))
    view = EvidenceView(data.records, data.checks)
    history = verified_bytes(source, "common-history", record.common_history_hash)
    if history_bytes(view) != history:
        raise ContractError("common history differs from public evidence")
    for r in view.records:
        if r.kind == "CHANGE":
            patch_hash = Path(r.payload["diff_ref"]).stem
            if r.payload["diff_ref"] != digest_ref("patches", patch_hash):
                raise ContractError("invalid historical diff reference")
            copy("patches", patch_hash)
    workflow.ledger.restore(record.source_run_id, view)
    verified_bytes(source, "triggers", record.frozen_trigger_hash)
    return Candidate("failure_evidence", "public-ledger/checkpoint-history", history.decode("utf-8"), "public_ledger",
                     record.snapshot.snapshot_id, record.common_history_hash, evidence_ids=record.anchor_ids)
