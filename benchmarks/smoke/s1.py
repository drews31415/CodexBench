"""Replay public DEV-01 in conditions A through E with a synthetic adapter."""

import argparse
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from aah.domain import (
    CancelResult, CapabilityStatus, CleanupResult, Condition, InterventionBoundary, RawEvent,
    RuntimeCapabilities, SessionMode, TurnResult, UnitStatus, UsageMode, canonical_json_bytes, resolve_condition,
)
from aah.runtime import RuntimeConfig
from aah.workflow.budget import Limits
from aah.workflow.orchestrator import run
from evaluation.smoke import fixture_request

from .public_checks import run_approved


CONFIG = RuntimeConfig("synthetic", "low", "fixture-v1", session_mode=SessionMode.SAME_SESSION)


class SyntheticAdapter:
    """Only writes a predetermined public fixture change, without running commands."""

    def __init__(self, results_root: Path):
        self.results_root = results_root
        self.requests = []

    def probe_capabilities(self, config):
        return RuntimeCapabilities(
            "fixture", config.runtime_version, config.model_id, InterventionBoundary.TURN_END, tuple(SessionMode),
            {f"RT{number:02d}": CapabilityStatus.VERIFIED for number in range(1, 8)},
            UsageMode.UNKNOWN, "manifest.json",
        )

    def open_run(self, request, workspace):
        return None

    def run_unit(self, request, on_event):
        return self._finish(request, on_event)

    def continue_unit(self, request, on_event):
        path = request.workspace.root / "module.py"
        original = path.read_bytes()
        corrected = original.replace(b'text.strip(" ")', b"text.strip()")
        if original == corrected:
            raise ValueError("synthetic fixture no longer contains its expected baseline")
        path.write_bytes(corrected)
        return self._finish(request, on_event)

    def _finish(self, request, on_event):
        self.requests.append(request)
        ref = f"restricted/{request.turn_id}.json"
        target = self.results_root / request.run_id / ref
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(canonical_json_bytes({
            "type": "completion.declared", "declared": True, "basis_id": "fixture-completion-declaration",
        }))
        raw_id = f"raw-{request.turn_id}"
        on_event(RawEvent(request.run_id, request.turn_id, raw_id, 1, "2026-11-26T00:00:00Z",
                          None, "completion.declared", ref, "PARSED"))
        return TurnResult(request.turn_id, UnitStatus.COMPLETED, "synthetic-session", True, raw_id,
                          "fixture-completion-declaration", False, 0, (raw_id,), (), None, True)

    def cancel(self, run_id):
        return CancelResult(True, 0, None)

    def close_run(self, run_id):
        return CleanupResult(True, 0, None)


def replay(condition: Condition, output: Path):
    request = replace(fixture_request("DEV-01"), policies=resolve_condition(condition))
    runtime = SyntheticAdapter(output)
    with TemporaryDirectory(prefix="aah-s1-") as temp:
        report = run(request, runtime=runtime, config=CONFIG, public_root=request.repository.source_path,
                     work_root=Path(temp), results_root=output, limits=Limits(30_000, 4, 2, 100, 5_000),
                     check=run_approved, supplement_cap=2048)
    return report, runtime.requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=tuple(Condition), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report, _ = replay(Condition(args.condition), args.output_dir)
    print("SYNTHETIC", report.condition, report.stop_reason, report.public_check_status, report.run_id)
    return 0 if report.stop_reason == "COMPLETED" and report.artifact_status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
