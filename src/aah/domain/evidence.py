"""Public observations and independently produced evaluation result contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from .models import FrozenPatch
from .wire import (
    ContractError, Count, Digest, Identifier, RelativePath, RunId, UTC, WireRecord, _typed,
    dump_record, sha256_bytes, validate_relative_path,
)


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    INFRA_ERROR = "INFRA_ERROR"
    NOT_RUN = "NOT_RUN"
    UNKNOWN = "UNKNOWN"


class EvaluationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INFRA_ERROR = "INFRA_ERROR"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    NOT_RUN = "NOT_RUN"


class ObservationStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


DIRECT_READ_BASES = frozenset({"direct_read", "runtime_file_read", "file_read_event"})


class StrategyId(StrEnum):
    REVIEW_REGRESSION = "REVIEW_REGRESSION"
    RELOCALIZE = "RELOCALIZE"
    REFRESH_EVIDENCE = "REFRESH_EVIDENCE"
    GENERIC_RETRY = "GENERIC_RETRY"
    CONTINUE = "CONTINUE"
    STOP = "STOP"


class TriggerId(StrEnum):
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"


@dataclass(frozen=True)
class ApprovedCheckSpec(WireRecord):
    """Portable identity of an approved public check; the runner owns authorization."""

    check_id: Identifier
    kind: Literal["P-FUNC", "P-REG"]
    argv: tuple[str, ...]
    cwd: str
    timeout_ms: Count
    parser_id: Identifier
    required: bool
    check_version: Identifier
    environment_profile_id: Identifier
    trusted_test_ref: RelativePath
    trusted_test_hash: Digest

    def validate(self) -> None:
        if not self.argv or not self.argv[0].strip() or any("\0" in arg for arg in self.argv):
            raise ContractError("argv requires an executable and NUL-free arguments")
        if self.cwd != ".":
            validate_relative_path(self.cwd)
        if self.timeout_ms == 0:
            raise ContractError("approved check timeout_ms must be positive")

    @property
    def check_definition_hash(self) -> str:
        return sha256_bytes(dump_record(self))


@dataclass(frozen=True)
class TestCaseResult(WireRecord):
    test_id: Identifier
    status: CheckStatus
    error_type: str | None
    message: str | None
    frame: Mapping[str, Any] | str | None
    fingerprint: Digest | None


@dataclass(frozen=True)
class CheckResult(WireRecord):
    check_result_id: Identifier
    check_id: Identifier
    check_definition_hash: Digest
    turn_id: Identifier | None
    snapshot_id: Digest
    status: CheckStatus
    source_class: Literal["TRUSTED_RUNNER", "UNVERIFIED_OBSERVATION"]
    exit_code: int | None
    duration_ms: Count
    environment_hash: Digest
    parser_id: Identifier
    test_cases: tuple[TestCaseResult, ...]
    stdout_ref: RelativePath | None
    stderr_ref: RelativePath | None
    warnings: tuple[str, ...]

    def validate(self) -> None:
        if len({case.test_id for case in self.test_cases}) != len(self.test_cases):
            raise ContractError("duplicate test ID in CheckResult")


def validate_check_binding(result: CheckResult, *, check_id: str, check_definition_hash: str,
                           snapshot_id: str, environment_hash: str) -> None:
    """Compare reported identities to values from the approved check invocation."""
    if (result.check_id, result.check_definition_hash, result.snapshot_id, result.environment_hash) != (
        check_id, check_definition_hash, snapshot_id, environment_hash
    ):
        raise ContractError("check result does not match the approved definition, snapshot or environment")


@dataclass(frozen=True)
class EvaluationResult(WireRecord):
    run_id: RunId
    task_id: Identifier
    base_commit: Identifier | None
    patch_hash: Digest | None
    private_manifest_hash: Digest | None
    test_bundle_hash: Digest | None
    evaluation_environment_hash: Digest | None
    status: EvaluationStatus
    functional_status: CheckStatus
    regression_status: CheckStatus
    instruction_status: CheckStatus
    duration_ms: Count | None
    error_code: Identifier | None
    restricted_details_ref: RelativePath | None

    def validate(self) -> None:
        suites = (self.functional_status, self.regression_status, self.instruction_status)
        if self.status in (EvaluationStatus.PASS, EvaluationStatus.FAIL):
            if None in (self.base_commit, self.patch_hash, self.private_manifest_hash,
                        self.test_bundle_hash, self.evaluation_environment_hash, self.duration_ms):
                raise ContractError("a verdict requires identified inputs and an observed duration")
        elif self.error_code is None:
            raise ContractError("an unavailable evaluation requires a reason")
        if self.status == EvaluationStatus.PASS and any(s != CheckStatus.PASS for s in suites):
            raise ContractError("PASS requires every suite to pass")
        if self.status == EvaluationStatus.NOT_RUN and any(s != CheckStatus.NOT_RUN for s in suites):
            raise ContractError("an unstarted evaluation cannot have suite outcomes")
        if self.status == EvaluationStatus.FAIL and CheckStatus.FAIL not in suites:
            candidate_timeout = self.error_code == "CANDIDATE_TEST_TIMEOUT" and CheckStatus.TIMEOUT in suites
            if self.error_code != "PATCH_APPLY_REJECTED" and not candidate_timeout:
                raise ContractError("FAIL requires evidence of a candidate failure")


def validate_evaluation_binding(result: EvaluationResult, patch: FrozenPatch) -> None:
    """Require agreement for every observed identifier before linking to a frozen patch."""
    if (result.run_id != patch.run_id
            or (result.base_commit is not None and result.base_commit != patch.base_commit)
            or (result.patch_hash is not None and result.patch_hash != patch.patch_hash)):
        raise ContractError("evaluation does not match the submitted patch")


@dataclass(frozen=True)
class SnapshotInterval(WireRecord):
    before_snapshot_id: Digest
    after_snapshot_id: Digest


@dataclass(frozen=True)
class SourceSpan(WireRecord):
    start_line: Count
    start_col: Count
    end_line: Count
    end_col: Count

    def validate(self) -> None:
        if self.start_line < 1 or (self.end_line, self.end_col) < (self.start_line, self.start_col):
            raise ContractError("source span must have ordered 1-based lines and byte columns")


Payload = Mapping[str, Any]
_COMMAND = {
    "command_id": Identifier, "argv": tuple[str, ...] | None, "cwd": str | None,
    "exit_code": int | None, "stdout_ref": RelativePath | None, "stderr_ref": RelativePath | None,
    "status": Identifier | None,
}
_PAYLOAD_FIELDS = {
    "SEARCH": {"query": str, "scope": str, "result_ref": Identifier | None, "result_digest": Digest | None},
    "FILE_ACCESS": {"path": RelativePath, "access_kind": Literal["read", "write", "unknown"],
                    "access_basis": Identifier, "span": SourceSpan | None},
    "COMMAND": _COMMAND,
    "COMMAND_RESULT": _COMMAND,
    "CHANGE": {"diff_ref": RelativePath, "before_snapshot_id": Digest, "after_snapshot_id": Digest,
               "path": RelativePath, "hunks": tuple[Payload, ...]},
    "TEST": {"check_result_id": Identifier, "check_id": Identifier, "test_id": Identifier | None,
             "status": CheckStatus, "command_id": Identifier | None},
    "FAILURE": {"check_result_id": Identifier, "fingerprint": Digest | None, "error_type": str | None,
                "normalized_message": str | None, "stable_frame": Payload | str | None},
    "INTERVENTION": {"intervention_id": Identifier, "strategy_id": StrategyId, "delivery_status": Identifier,
                     "trigger_ids": tuple[TriggerId, ...], "anchor_ids": tuple[Identifier, ...]},
    "COMPLETION": {"declared": bool | None, "source_ref": Identifier | None, "basis_id": Identifier | None},
    "USAGE": {"usage_record_id": Identifier},
    "UNKNOWN": {"provider_type": Identifier | None, "reason_code": Identifier, "raw_ref": Identifier},
}


@dataclass(frozen=True)
class EvidenceRecord(WireRecord):
    evidence_id: Identifier
    run_id: RunId
    turn_id: Identifier
    sequence: Count
    kind: Identifier
    raw_refs: tuple[Identifier, ...]
    observed_at: UTC | None
    observation_status: ObservationStatus
    snapshot_id: Digest | None
    snapshot_interval: SnapshotInterval | None
    source_order: Count | None
    payload: Payload
    warnings: tuple[str, ...]

    def validate(self) -> None:
        schema = _PAYLOAD_FIELDS.get(self.kind)
        if schema is None or schema.keys() != self.payload.keys():
            raise ContractError("evidence kind and payload fields disagree")
        for key, annotation in schema.items():
            try:
                _typed(annotation, self.payload[key])
            except ContractError as exc:
                raise ContractError(f"{self.kind}.{key}: {exc}") from exc
        if self.snapshot_id is not None and self.snapshot_interval is not None:
            raise ContractError("snapshot interval applies only when the exact version is unknown")
        if self.kind in ("COMMAND", "COMMAND_RESULT") and self.payload["cwd"] not in (None, "."):
            validate_relative_path(self.payload["cwd"])
        if self.kind == "COMPLETION" and self.payload["declared"]:
            if not (self.snapshot_id and self.payload["source_ref"] and self.payload["basis_id"]):
                raise ContractError("declared completion requires a snapshot, source and basis")
