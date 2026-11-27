import json

import pytest

from benchmarks.smoke import docker_probe as probe


CID = "a" * 64


@pytest.mark.parametrize("readable", [False, True])
def test_boundary_and_cancel_evidence(tmp_path, monkeypatch, readable):
    work = tmp_path / "work"
    operator = tmp_path / "operator"
    work.mkdir()
    operator.mkdir()
    for name, value in probe.SENTINELS.items():
        (operator / name).write_bytes(value)
    (work / "copy.txt").write_bytes(b"public source edited")
    denied = {name: ["rb", "r+b"] for name in probe.SENTINELS}
    if readable:
        denied["evaluation.txt"] = ["r+b"]
    observed = dict(uid=65534, root_readonly=True, effective_capabilities=0, no_new_privileges=1,
                    interfaces=["lo"], protected_open_denied=denied, child_pid=7)
    state = dict(Status="exited", Running=False, Pid=0, ExitCode=137, OOMKilled=False)
    replies = {
        ("logs", CID): json.dumps(observed),
        ("inspect", "--format", "{{json .Mounts}}", CID): json.dumps([dict(Type="bind", Destination="/work", RW=True)]),
        ("top", CID, "-eo", "pid,ppid"): "PID PPID\n501 499\n502 501",
        ("kill", CID): CID,
        ("wait", CID): "137",
        ("inspect", "--format", "{{json .State}}", CID): json.dumps(state),
    }
    calls = []

    def engine(*args):
        calls.append(args)
        return replies[args]

    monkeypatch.setattr(probe, "docker", engine)
    report = {"checks": {}}
    probe.check_container(CID, report, work, operator)
    assert calls == list(replies)
    assert report["checks"]["protected_paths_unavailable"] is (not readable)
    assert all(report["checks"].values()) is (not readable)
    assert not probe.process_pair("PID PPID\n501 499\n502 499")


@pytest.mark.parametrize("lost_receipt", [False, True])
def test_cleanup_after_uncertain_create_or_start(tmp_path, monkeypatch, lost_receipt):
    calls = []

    def engine(*args):
        calls.append(args)
        if args[0] == "create":
            assert "--pull=never" in args and probe.IMAGE in args
            assert args[args.index("--mount") + 1] == f"type=bind,source={tmp_path / 'work'},target=/work"
            if lost_receipt:
                raise RuntimeError("create receipt lost")
            return CID
        if args[0] == "start":
            raise RuntimeError("start receipt lost")
        if args[:2] == ("container", "ls"):
            if lost_receipt:
                assert "label=aah.s0.probe=example" in args and "name=^/aah-s0-example$" in args
            else:
                assert f"id={CID}" in args
            return "" if ("rm", "-f", CID) in calls else CID
        return ""

    monkeypatch.setattr(probe, "docker", engine)
    report = {"checks": {}}
    with pytest.raises(RuntimeError, match="receipt lost"):
        probe.run(tmp_path, "example", report)
    assert calls.count(("rm", "-f", CID)) == 1 and report["checks"]["container_removed"]


def test_engine_denial_leaves_incomplete_report(tmp_path, monkeypatch):
    def denied(*args):
        assert args[0] == "version"
        raise RuntimeError("permission denied")

    monkeypatch.setattr(probe, "RESULTS", tmp_path)
    monkeypatch.setattr(probe, "docker", denied)
    assert probe.main() == 1
    path, = tmp_path.glob("docker-*/report.json")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["status"] == "INCOMPLETE" and result["checks"] == {}
    assert result["error"] == "permission denied"
