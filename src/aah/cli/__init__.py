"""Operator commands for the Adaptive Agent Harness."""

import argparse
import json
import sys
from pathlib import Path

from aah.reporting.store import read_report
from aah.runtime import probe_codex

from .config import load_probe_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adaptive-codex")
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe", help="Inspect local Codex CLI metadata")
    probe.add_argument("--config", type=Path, required=True, help="S0 probe profile (UTF-8 JSON)")
    probe.add_argument("--output", type=Path, help="Also write the report to a new JSON file")
    report_command = commands.add_parser("report", help="Read a saved public run report")
    report_command.add_argument("--run-id", required=True)
    report_command.add_argument("--results-root", type=Path, required=True)
    experiment = commands.add_parser("experiment", help="Execute or verify a pinned synthetic Dev schedule from the checkout")
    experiment.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "experiment":
        try:
            from benchmarks.smoke.s4 import replay_manifest

            summary = replay_manifest(args.manifest)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"Could not run the development experiment: {exc}", file=sys.stderr)
            return 2
        print(args.manifest.parent / "summary.json")
        return 0 if all(r["evaluation_status"] in {"PASS", "FAIL"} for r in summary["runs"]) else 1

    if args.command == "report":
        try:
            data = read_report(args.results_root, args.run_id)
        except (OSError, ValueError):
            print("Could not read a public report for this run", file=sys.stderr)
            return 2
        print(json.dumps(json.loads(data), ensure_ascii=True, indent=2))
        return 0

    try:
        config, config_hash = load_probe_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"Invalid probe profile: {exc}", file=sys.stderr)
        return 2

    inspection = probe_codex(config)
    report = inspection.to_dict()
    report["config_hash"] = config_hash
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as out:
                out.write(content)
        except OSError as exc:
            print(f"Could not save probe report: {exc}", file=sys.stderr)
            return 5
    print(content, end="")
    return 0 if inspection.metadata_complete else 4
