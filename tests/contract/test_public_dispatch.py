from dataclasses import replace

import pytest

from aah.domain import ApprovedCheckSpec, CheckResult, ContractError, SnapshotRef
from aah.workspace import checks


HASH = "1" * 64
SNAPSHOT = SnapshotRef("toy", HASH, "base", "snapshots/one", "2026-11-25T00:00:00Z", True)


@pytest.fixture
def spec():
    return ApprovedCheckSpec(
        check_id="example", kind="P-FUNC", argv=["python", "check.py"], cwd=".",
        timeout_ms=500, parser_id="approved-json", required=True, check_version="1",
        environment_profile_id="fixture", trusted_test_ref="public/check.py", trusted_test_hash=HASH,
    )


def passed(snapshot, spec, deadline, turn):
    return CheckResult("result", spec.check_id, spec.check_definition_hash, turn, snapshot.snapshot_id,
                       "PASS", "TRUSTED_RUNNER", 0, 10, HASH, spec.parser_id, (), None, None, ())


def test_registry_identity_and_preconditions(spec, monkeypatch):
    monkeypatch.setattr(checks, "monotonic", lambda: 10)
    def unexpected(*args):
        pytest.fail("unapproved, incomplete or expired check executed")

    with pytest.raises(ContractError, match="duplicate"):
        checks.PublicCheckRunner([spec, spec], environment_hash=HASH, execute=unexpected)
    runner = checks.PublicCheckRunner([spec], environment_hash=HASH, execute=unexpected)
    with pytest.raises(ContractError, match="registry"):
        runner.run_approved(SNAPSHOT, "other", 12)
    assert runner.run_approved(SNAPSHOT, spec.check_id, 10).status == "NOT_RUN"
    assert runner.run_approved(replace(SNAPSHOT, complete=False), spec.check_id, 12).status == "NOT_RUN"


def test_deadline_and_observation_binding(spec, monkeypatch):
    monkeypatch.setattr(checks, "monotonic", lambda: 10)
    def execute(snapshot, check, deadline, turn):
        assert deadline == 10.5
        return passed(snapshot, check, deadline, turn)

    runner = checks.PublicCheckRunner([spec], environment_hash=HASH, execute=execute)
    assert runner.run_approved(SNAPSHOT, spec.check_id, 12, turn_id="t").status == "PASS"

    def late(*args):
        monkeypatch.setattr(checks, "monotonic", lambda: 11)
        return passed(*args)

    runner = checks.PublicCheckRunner([spec], environment_hash=HASH, execute=late)
    assert runner.run_approved(SNAPSHOT, spec.check_id, 12).status == "TIMEOUT"


@pytest.mark.parametrize("changed", [
    {"snapshot_id": "2" * 64}, {"parser_id": "different"}, {"turn_id": "another-turn"},
    {"environment_hash": "2" * 64}, {"check_definition_hash": "2" * 64},
])
def test_refuse_mismatched_check_even_if_it_reported_pass(spec, changed):
    def wrong_result(*args):
        return replace(passed(*args), **changed)
    runner = checks.PublicCheckRunner([spec], environment_hash=HASH, execute=wrong_result)
    with pytest.raises(ContractError):
        runner.run_approved(SNAPSHOT, spec.check_id, checks.monotonic() + 10)
