"""Run the reviewed Smoke worker in the pinned local Docker Desktop image."""

import re
import subprocess
from time import monotonic
from uuid import uuid4

from .docker_probe import ENGINE, IMAGE


RESTRICTIONS = (
    "--pull=never", "--network", "none", "--read-only", "--user", "65534:65534",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "16",
    "--memory", "128m", "--log-driver", "none",
)
CLI = ("docker", "--host", ENGINE)


class CleanupUnconfirmed(RuntimeError):
    """The caller must stop scheduling work until this container is reconciled."""


def identity() -> dict:
    engine = subprocess.run(
        [*CLI, "version", "--format", "{{.Server.Version}} {{.Server.Os}}/{{.Server.Arch}}"],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout.strip()
    image_id = subprocess.run(
        [*CLI, "image", "inspect", "--format", "{{.Id}}", IMAGE],
        capture_output=True, text=True, check=True, timeout=5,
    ).stdout.strip()
    if not engine or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("missing local Docker engine or image identity")
    return {"engine": engine, "image": IMAGE, "image_id": image_id, "restrictions": RESTRICTIONS}


def run(script: bytes, flags: tuple[str, ...], request: bytes, deadline: float,
        *, worker_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    name = "aah-eval-" + uuid4().hex
    label = "aah.s0.evaluation=" + name
    argv = [
        *CLI, "run", "--rm", "-i", "--name", name, "--label", label, *RESTRICTIONS,
        "--entrypoint", "python", IMAGE, *flags, "-c", script.decode("utf-8"), *worker_args,
    ]
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("Docker evaluation", 0)
    try:
        return subprocess.run(argv, input=request, capture_output=True, timeout=remaining)
    finally:
        # A killed CLI can leave its container running. Use only our exact name and label.
        lookup = [*CLI, "container", "ls", "-aq", "--no-trunc", "--filter", "label=" + label,
                  "--filter", f"name=^/{name}$"]
        try:
            ids = subprocess.run(lookup, capture_output=True, check=True, timeout=4).stdout.splitlines()
            for cid in ids:
                if not re.fullmatch(rb"[0-9a-f]{64}", cid):
                    raise CleanupUnconfirmed(name)
                # Auto-removal may win this race. A final inspection decides.
                subprocess.run([*CLI, "rm", "-f", cid.decode("ascii")], capture_output=True, timeout=4)
            if ids and subprocess.run(lookup, capture_output=True, check=True, timeout=4).stdout.strip():
                raise CleanupUnconfirmed(name)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CleanupUnconfirmed(name) from exc
