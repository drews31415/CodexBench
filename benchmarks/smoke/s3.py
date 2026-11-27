"""Offline D integration fixture with a publicly identifiable test and its dependency."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from aah.domain import (CheckResult, CheckStatus, Condition, FixedRequirements, RepositoryRef, RunRequest,
                        TestCaseResult, canonical_json_bytes, resolve_condition, sha256_bytes)
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run
from aah.workspace import export_revision, read_snapshot_file

from .s1 import CONFIG, SyntheticAdapter


TEST = b"from labels import label\n\ndef test_label():\n    assert label() == 'ready'\n"
RIGHT = b"def label():\n    return 'ready'\n"
WRONG = b"def label():\n    return 'pending'\n"
CASE = "test_labels.py::test_label"
CHECK = "S3-public-label"
DEFINITION = sha256_bytes(canonical_json_bytes([CASE, TEST.hex(), RIGHT.hex(), WRONG.hex()]))


def check(snapshot, check_id, artifact_root, *, deadline, turn_id):
    if check_id != CHECK:
        raise ValueError("unexpected fixture check")
    code = read_snapshot_file(artifact_root, snapshot, "labels.py")
    test = read_snapshot_file(artifact_root, snapshot, "test_labels.py")
    if test != TEST or code not in {RIGHT, WRONG}:
        raise ValueError("fixture receipt cannot classify this snapshot")
    # These two known literal-return programs are the complete synthetic input domain.
    status = CheckStatus.PASS if code == RIGHT else CheckStatus.FAIL
    failure = status == CheckStatus.FAIL
    case = TestCaseResult(CASE, status, "AssertionError" if failure else None,
                          "expected ready, got pending" if failure else None, None,
                          sha256_bytes(b"fixture-pending-v1") if failure else None)
    return CheckResult(str(uuid4()), CHECK, DEFINITION, turn_id, snapshot.snapshot_id, status, "TRUSTED_RUNNER",
                       int(failure), 0, sha256_bytes(b"s3-offline-fixture-v1"), "known-program-v1", (case,), None, None, ())


class LabelAdapter(SyntheticAdapter):
    def run_unit(self, request, on_event):
        (request.workspace.root / "labels.py").write_bytes(WRONG)
        return self._finish(request, on_event)

    def continue_unit(self, request, on_event):
        (request.workspace.root / "labels.py").write_bytes(RIGHT)
        return self._finish(request, on_event)


def replay(scenario: str, output: Path, *, condition=Condition.D, cap=2048, adapter_type=LabelAdapter):
    if scenario not in {"repeat", "regression"}:
        raise ValueError("unknown synthetic scenario")
    with TemporaryDirectory(prefix="aah-s3-") as temp:
        root = Path(temp) / "public"
        root.mkdir()
        (root / "labels.py").write_bytes(RIGHT if scenario == "regression" else WRONG)
        (root / "test_labels.py").write_bytes(TEST)
        mandatory = "Preserve the public function signature."
        request = RunRequest(str(uuid4()), "S3-label", None, RepositoryRef("S3-label", root, export_revision(root)),
                             FixedRequirements("Make label() return ready.", mandatory,
                                               sha256_bytes(mandatory.encode()), "public-copy-only", (CHECK,)),
                             resolve_condition(condition), DEFINITION, "user")
        adapter = adapter_type(output)
        report = run(request, runtime=adapter, config=CONFIG, public_root=root, work_root=Path(temp) / "work",
                     results_root=output, limits=Limits(30_000, 3, 1, 100, 5_000), check=check,
                     supplement_cap=cap, baseline_checks=True)
        return report, adapter.requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("repeat", "regression"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report, turns = replay(args.scenario, args.output_dir)
    expected = "RELOCALIZE" if args.scenario == "repeat" else "REVIEW_REGRESSION"
    ok = (len(turns) == 2 and expected in turns[1].message and report.public_check_status == "PASS"
          and report.stop_reason == "COMPLETED" and report.artifact_status == "complete")
    print("SYNTHETIC", args.scenario, expected if ok else "FAILED", report.run_id)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
