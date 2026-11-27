"""Prepare public Dev Smoke inputs for operator review without starting a run."""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from aah.domain import Condition, canonical_json_bytes, sha256_bytes
from aah.workspace.snapshot import File, checked_path, excluded, read_regular, revision

from . import driver


def prepare(task_id: str, output: Path) -> str:
    task_dir = driver.task_dir(task_id)
    task_hash = driver.TASK_DIGESTS[task_id]
    spec = driver.load_bundle(task_dir / "task.json", task_hash)
    base = checked_path(task_dir / "base")
    if {p.name for p in base.iterdir() if not excluded(p.name)} != {"module.py"}:
        raise ValueError("unexpected public base file set")
    data, mode = read_regular(base / "module.py")
    if sha256_bytes(data.replace(b"\r\n", b"\n")) != driver.worker.REVIEWED[task_id][1][0]:
        raise ValueError("public baseline digest mismatch")

    # This fixture has no native instruction file. Both fixed texts are sent once.
    message = (spec["task"] + "\n\n" + spec["mandatory_text"]).encode("utf-8")
    review = {
        "schema_version": "1.0", "purpose": "dev-smoke-input-review",
        "task_id": task_id, "task_definition_hash": task_hash, "condition": Condition.A.value,
        "base_commit": revision({"module.py": File(data, mode)}),
        "workspace_ref": "workspace", "initial_message_ref": "initial-message.txt",
        "initial_message_hash": sha256_bytes(message),
        "mandatory_hash": sha256_bytes(spec["mandatory_text"].encode("utf-8")),
        "approved_check_ids": spec["approved_check_ids"],
    }
    output = checked_path(output, allow_missing=True)
    checkout = driver.ROOT.parents[1]
    if output.is_relative_to(checkout) and not output.is_relative_to(checkout / "results"):
        raise ValueError("store review outputs under results/ or outside the checkout")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        staged = Path(staging) / "review.zip"
        with ZipFile(staged, "w") as archive:
            # Include only these members, with fixed metadata for repeatable hashes.
            for name, content, permissions in (
                ("workspace/module.py", data, int(mode, 8)),
                ("initial-message.txt", message, 0o100644),
                ("review.json", canonical_json_bytes(review), 0o100644),
            ):
                entry = ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = permissions << 16
                archive.writestr(entry, content)
        digest = sha256_bytes(staged.read_bytes())
        # Publish the complete archive once. Windows rename also rejects an existing target.
        if os.name == "nt":
            staged.rename(output)
        else:
            os.link(staged, output)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=tuple(driver.TASK_DIGESTS), required=True)
    parser.add_argument("--output", type=Path, required=True, help="new review ZIP path")
    args = parser.parse_args()
    try:
        digest = prepare(args.task, args.output)
    except (OSError, ValueError, driver.CheckEnvironmentError) as error:
        print(f"Could not prepare Dev Smoke input: {error}", file=sys.stderr)
        return 2
    print(f"{args.task}  sha256={digest}  {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
