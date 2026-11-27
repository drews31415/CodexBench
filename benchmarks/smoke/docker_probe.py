"""Run the S0 synthetic workspace and cancellation checks on local Docker Desktop."""

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ENGINE = "npipe:////./pipe/dockerDesktopLinuxEngine"
IMAGE = "python@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b"
RESULTS = Path(__file__).resolve().parents[2] / "results" / "s0-verification"
SENTINELS = {
    "source.txt": b"public source", "evaluation.txt": b"synthetic evaluation", "ledger.txt": b"synthetic ledger",
}

# Only this fixed probe runs in the container. No model, task code or evaluator is loaded.
CONTAINER_PROBE = """
import json, os, subprocess, sys
from pathlib import Path

workspace = Path('/work/copy.txt')
before = workspace.read_bytes()
workspace.write_bytes(before + b' edited')
denied = {}
for name in ('source.txt', 'evaluation.txt', 'ledger.txt'):
    denied[name] = []
    for mode in ('rb', 'r+b'):
        try:
            with (Path('/operator') / name).open(mode):
                pass
        except (FileNotFoundError, PermissionError):
            denied[name].append(mode)

status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(90)'])
print(json.dumps({
    'uid': os.getuid(), 'python': sys.version.split()[0], 'interfaces': sorted(os.listdir('/sys/class/net')),
    'root_readonly': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
    'effective_capabilities': int(status['CapEff'], 16), 'no_new_privileges': int(status['NoNewPrivs']),
    'protected_open_denied': denied, 'child_pid': child.pid,
}), flush=True)
child.wait()
"""


def docker(*args: str) -> str:
    proc = subprocess.run(
        ["docker", "--host", ENGINE, *args], stdin=subprocess.DEVNULL,
        capture_output=True, encoding="utf-8", errors="replace", timeout=12,
    )
    if proc.returncode:
        raise RuntimeError(f"docker {args[0]}: {proc.stderr.strip()[:700]}")
    if args[0] == "logs" and proc.stderr.strip():
        raise RuntimeError(f"probe process wrote stderr: {proc.stderr.strip()[:700]}")
    return proc.stdout.strip()


def process_pair(table: str) -> bool:
    rows = [line.split() for line in table.splitlines()[1:]]
    return (len(rows) == 2 and all(len(row) == 2 and all(v.isdigit() for v in row) for row in rows)
            and (rows[0][0] == rows[1][1] or rows[1][0] == rows[0][1]))


def check_container(cid: str, report: dict, work: Path, operator: Path) -> None:
    deadline = time.monotonic() + 10
    while not (output := docker("logs", cid)):
        if time.monotonic() >= deadline:
            raise RuntimeError("container did not publish its observations in time")
        time.sleep(0.2)
    obs = json.loads(output)
    report["observations"] = obs
    mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", cid))
    processes = docker("top", cid, "-eo", "pid,ppid")
    report["processes_before_cancel"] = processes.splitlines()
    checks = report["checks"]
    checks.update({
        "restricted_process": obs["uid"] == 65534 and obs["root_readonly"] is True
            and obs["effective_capabilities"] == 0 and obs["no_new_privileges"] == 1,
        "loopback_only": obs["interfaces"] == ["lo"],
        "workspace_only_mount": [(m["Type"], m["Destination"], m["RW"]) for m in mounts]
            == [("bind", "/work", True)],
        "host_copy_edited": (work / "copy.txt").read_bytes() == b"public source edited",
        "protected_paths_unavailable": obs["protected_open_denied"] == {name: ["rb", "r+b"] for name in SENTINELS},
        "parent_and_child_running": obs["child_pid"] > 1 and process_pair(processes),
    })
    docker("kill", cid)
    wait_status = docker("wait", cid)
    stopped = json.loads(docker("inspect", "--format", "{{json .State}}", cid))
    report["state_after_cancel"] = {key: stopped[key] for key in ("Status", "Running", "Pid", "ExitCode", "OOMKilled")}
    checks["container_stopped"] = (
        stopped["Status"] == "exited" and stopped["Running"] is False and stopped["Pid"] == 0
        and wait_status == "137" and stopped["ExitCode"] == 137 and stopped["OOMKilled"] is False
    )
    checks["operator_files_unchanged"] = all(
        (operator / name).read_bytes() == value for name, value in SENTINELS.items()
    )


def run(directory: Path, key: str, report: dict) -> None:
    report["engine"] = docker("version", "--format", "{{.Server.Version}} {{.Server.Os}}")
    report["image_id"] = docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
    work = directory / "work"
    operator = directory / "operator"
    work.mkdir()
    operator.mkdir()
    for name, value in SENTINELS.items():
        (operator / name).write_bytes(value)
    (work / "copy.txt").write_bytes(SENTINELS["source.txt"])
    label = f"aah.s0.probe={key}"
    name = "aah-s0-" + key
    lookup = ("container", "ls", "-aq", "--no-trunc", "--filter", f"label={label}", "--filter", f"name=^/{name}$")
    owned_id = None
    try:
        cid = docker(
            "create", "--pull=never", "--name", name, "--label", label,
            "--network", "none", "--read-only", "--user", "65534:65534", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "16", "--memory", "128m",
            "--mount", f"type=bind,source={work},target=/work", "--workdir", "/work",
            "--entrypoint", "python", IMAGE, "-I", "-B", "-c", CONTAINER_PROBE,
        )
        if not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise RuntimeError("could not identify created container")
        owned_id = cid
        lookup = ("container", "ls", "-aq", "--no-trunc", "--filter", f"id={cid}")
        report["container_id"] = cid
        docker("start", cid)
        check_container(cid, report, work, operator)
    finally:
        # A create/start timeout can lose the receipt. Reconcile only this probe's label.
        report["checks"]["container_removed"] = False
        for owned in [owned_id] if owned_id else docker(*lookup).splitlines():
            if not re.fullmatch(r"[0-9a-f]{64}", owned):
                raise RuntimeError("could not identify container for cleanup")
            docker("rm", "-f", owned)
        report["checks"]["container_removed"] = docker(*lookup) == ""


def main() -> int:
    key = uuid4().hex
    directory = RESULTS / ("docker-" + key)
    directory.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": "1.0", "profile": "synthetic-docker-probe", "probe_id": key,
        "image": IMAGE, "started_at": datetime.now(timezone.utc).isoformat(), "checks": {},
    }
    start = time.monotonic_ns()
    try:
        run(directory, key, report)
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        report.update(status="INCOMPLETE", error=str(exc)[:1000])
    report["elapsed_ms"] = (time.monotonic_ns() - start) // 1_000_000
    path = directory / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}  {path}")
    if "error" in report:
        print(report["error"], file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
