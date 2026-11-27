"""Trusted public-check runner for Main public tasks (AAH-S03) in the pinned Docker worker.

Suite sources come from the pinned public base, never from the agent's snapshot, so a
submission that edits ``public_checks/`` cannot change what is run. Only the submitted
module sources are read from the snapshot.
"""

from __future__ import annotations

import subprocess
from time import monotonic
from uuid import uuid4

from aah.domain import (ApprovedCheckSpec, CheckResult, CheckStatus, ContractError, TestCaseResult,
                        canonical_json_bytes, sha256_bytes)
from aah.workspace import WorkspaceError, export_revision, read_snapshot_file
from aah.workspace.checks import PublicCheckRunner
from aah.workspace.snapshot import read_regular
from benchmarks.main_public.qualify import read_task
from benchmarks.smoke import docker_worker
from evaluation import source_suites


PROFILE = "main-public-docker-v1"
PARSER = "main-unittest-counts-v1"
TIMEOUT_MS = 30_000
SUITES = {"P-FUNC": "public_checks/test_functional.py", "P-REG": "public_checks/test_regression.py"}


def definition(task_id: str, *, identity: dict | None = None) -> dict:
    """Pinned inputs of the public runner; the environment hash binds the Docker image identity."""
    directory, task, spec_hash = read_task(task_id)
    base = directory / "base"
    suites = {kind: read_regular(base / path)[0] for kind, path in SUITES.items()}
    modules = sorted(p.stem for p in base.glob("*.py") if p.is_file() and p.stem.isidentifier())
    environment = sha256_bytes(canonical_json_bytes(source_suites.environment_record(
        identity if identity is not None else docker_worker.identity())))
    return {"task": task, "base": base, "revision": export_revision(base), "spec_hash": spec_hash,
            "suites": suites, "modules": modules, "environment_hash": environment,
            "suite_sha256": {kind: sha256_bytes(body) for kind, body in suites.items()}}


def public_runner(task_id: str, *, identity: dict | None = None, transport=None):
    """Return a workflow ``check`` callback bound to the task's approved public check IDs."""
    pinned = definition(task_id, identity=identity)
    task = pinned["task"]
    kinds = {check_id: check_id.rsplit("-", 2)[-2] + "-" + check_id.rsplit("-", 1)[-1]
             for check_id in task.approved_check_ids}
    if set(kinds.values()) != set(SUITES):
        raise ContractError("approved public check IDs do not map to the pinned public suites")
    specs = [ApprovedCheckSpec(
        check_id=check_id, kind=kind, argv=("python", *source_suites.FLAGS, "main-public-worker"), cwd=".",
        timeout_ms=TIMEOUT_MS, parser_id=PARSER, required=True, check_version="1", environment_profile_id=PROFILE,
        trusted_test_ref=f"benchmarks/main_public/{task_id}/base/{SUITES[kind]}",
        trusted_test_hash=pinned["suite_sha256"][kind],
    ) for check_id, kind in sorted(kinds.items())]
    environment = pinned["environment_hash"]

    def run_public(snapshot, check_id, artifact_root, *, deadline, turn_id):
        def execute(bound, spec, stop_at, active_turn):
            started = monotonic()
            cases, warnings, exit_code = (), (), None
            try:
                if (bound.repo_id, bound.base_commit) != (task.repo_id, pinned["revision"]):
                    raise ContractError("public check snapshot identity mismatch")
                try:
                    modules = {name: read_snapshot_file(artifact_root, bound, name + ".py")
                               for name in pinned["modules"]}
                except WorkspaceError:
                    modules = None
                if modules is None:
                    status, warnings, exit_code = CheckStatus.FAIL, ("SUBMITTED_MODULE_MISSING",), 0
                else:
                    outcome = source_suites.run_suites(modules, {spec.kind: pinned["suites"][spec.kind]}, stop_at,
                                                       transport=transport)[spec.kind]
                    status = CheckStatus(outcome["status"])
                    exit_code = 0
                    if outcome.get("load_error"):
                        warnings = ("SUBMITTED_MODULE_IMPORT_ERROR",)
                passed = status == CheckStatus.PASS
                cases = (TestCaseResult(
                    test_id=spec.check_id, status=status, error_type=None if passed else "AssertionError",
                    message=None if passed else "Public suite reported a failure",
                    frame=None, fingerprint=None if passed else sha256_bytes(canonical_json_bytes(
                        [spec.check_id, *warnings, "SUITE_FAILED"])),
                ),)
            except docker_worker.CleanupUnconfirmed:
                raise
            except (subprocess.TimeoutExpired, TimeoutError):
                status, warnings = CheckStatus.TIMEOUT, ("PUBLIC_WORKER_TIMEOUT",)
            except (ContractError, OSError, ValueError, subprocess.SubprocessError):
                status, warnings = CheckStatus.INFRA_ERROR, ("PUBLIC_CHECK_ENVIRONMENT_ERROR",)
            return CheckResult(str(uuid4()), spec.check_id, spec.check_definition_hash, active_turn,
                               bound.snapshot_id, status, "TRUSTED_RUNNER" if exit_code == 0 else "UNVERIFIED_OBSERVATION",
                               exit_code, int((monotonic() - started) * 1000), environment, spec.parser_id,
                               cases, None, None, warnings)

        return PublicCheckRunner(specs, environment_hash=environment, execute=execute).run_approved(
            snapshot, check_id, deadline, turn_id=turn_id)

    run_public.environment_hash = environment
    run_public.suite_sha256 = pinned["suite_sha256"]
    return run_public
