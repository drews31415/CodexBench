"""Dispatch immutable public check definitions to an operator-provisioned runner."""

import math
import re
from collections.abc import Callable, Iterable
from time import monotonic
from types import MappingProxyType
from uuid import uuid4

from aah.domain import (
    ApprovedCheckSpec, CheckResult, CheckStatus, ContractError, SnapshotRef, validate_check_binding,
)


class PublicCheckRunner:
    def __init__(
        self, specs: Iterable[ApprovedCheckSpec], *, environment_hash: str,
        execute: Callable[[SnapshotRef, ApprovedCheckSpec, float, str | None], CheckResult],
    ):
        definitions = {}
        for spec in specs:
            if spec.check_id in definitions:
                raise ContractError("duplicate approved check ID")
            definitions[spec.check_id] = spec
        if not re.fullmatch(r"[0-9a-f]{64}", environment_hash):
            raise ContractError("invalid public check environment hash")
        self._definitions = MappingProxyType(definitions)
        self._environment_hash = environment_hash
        self._execute = execute

    def run_approved(
        self, snapshot: SnapshotRef, check_id: str, deadline: float, *, turn_id: str | None = None,
    ) -> CheckResult:
        """Only execute a registered check, bound to this snapshot and monotonic deadline."""
        spec = self._definitions.get(check_id)
        if spec is None:
            raise ContractError("check ID is outside the approved public registry")
        if not math.isfinite(deadline):
            raise ContractError("expected a finite monotonic deadline")
        start = monotonic()
        stop_at = min(deadline, start + spec.timeout_ms / 1000)

        def unavailable(status: CheckStatus, reason: str) -> CheckResult:
            return CheckResult(
                check_result_id=str(uuid4()), check_id=spec.check_id,
                check_definition_hash=spec.check_definition_hash, turn_id=turn_id,
                snapshot_id=snapshot.snapshot_id, status=status, source_class="UNVERIFIED_OBSERVATION",
                exit_code=None, duration_ms=int((monotonic() - start) * 1000),
                environment_hash=self._environment_hash, parser_id=spec.parser_id,
                test_cases=(), stdout_ref=None, stderr_ref=None, warnings=(reason,),
            )

        if not snapshot.complete:
            return unavailable(CheckStatus.NOT_RUN, "INCOMPLETE_SNAPSHOT")
        if start >= stop_at:
            return unavailable(CheckStatus.NOT_RUN, "DEADLINE_EXHAUSTED")
        result = self._execute(snapshot, spec, stop_at, turn_id)
        validate_check_binding(
            result, check_id=spec.check_id, check_definition_hash=spec.check_definition_hash,
            snapshot_id=snapshot.snapshot_id, environment_hash=self._environment_hash,
        )
        if result.turn_id != turn_id or result.parser_id != spec.parser_id:
            raise ContractError("public result does not match its turn and parser")
        if monotonic() > stop_at and result.status in (CheckStatus.PASS, CheckStatus.FAIL):
            return unavailable(CheckStatus.TIMEOUT, "DEADLINE_EXCEEDED")
        return result
