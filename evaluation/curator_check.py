"""Verify the curator's baseline and reference in the pinned Docker environment."""

import argparse
import ast
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from aah.domain import (Condition, ContractError, EvaluationResult, FixedRequirements, RepositoryRef, RunRequest,
                        canonical_json_bytes, dump_record, resolve_condition, sha256_bytes)
from aah.workspace import WorkspaceError, WorkspaceManager, apply_frozen_patch, export_revision
from aah.workspace.patch import store_bytes
from aah.workspace.snapshot import checked_path, disjoint, read_regular, read_tree
from benchmarks.main_public.qualify import baseline_evaluation_request, read_task, verify_independent_pair
from benchmarks.smoke import docker_worker


CHECKOUT = Path(__file__).resolve().parents[1]
SUITES = {"functional": "test_functional.py", "regression": "test_regression.py"}
INCOMPLETE_COUNTS = ("errors", "skipped", "expected_failures", "unexpected_successes")
FLAGS = ("-I", "-S", "-B")
WORKER = """\
import importlib.abc, importlib.util, io, json, sys, types, unittest

payload = json.load(sys.stdin)
sources = payload["modules"]
if set(sources) & (sys.modules.keys() | sys.stdlib_module_names):
    raise ValueError("task module shadows a Python runtime module")

class SourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in sources:
            return importlib.util.spec_from_loader(fullname, self, origin=fullname + ".py")

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = module.__spec__.origin
        exec(compile(sources[module.__name__], module.__file__, "exec"), module.__dict__)

sys.meta_path.insert(0, SourceLoader())
observed = {}
for kind, source in payload["suites"].items():
    for name in sources:
        sys.modules.pop(name, None)
    suite = types.ModuleType(kind)
    exec(compile(source, kind + ".py", "exec"), suite.__dict__)
    result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(suite))
    observed[kind] = {
        "tests": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
    }
print(json.dumps(observed))
"""


def _interface(source):
    signature = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            if node.name in signature:
                raise ContractError("duplicate public function definition")
            args = node.args
            signature[node.name] = (
                [arg.arg for arg in args.posonlyargs], [arg.arg for arg in args.args],
                [arg.arg for arg in args.kwonlyargs], args.vararg.arg if args.vararg else None,
                args.kwarg.arg if args.kwarg else None, [ast.dump(default) for default in args.defaults],
                [ast.dump(default) if default else None for default in args.kw_defaults], bool(node.decorator_list),
            )
    return signature


def _suite_statuses(variant, modules, suites):
    payload = canonical_json_bytes({
        "modules": {name: body.decode("utf-8-sig") for name, body in modules.items()},
        "suites": {kind: body.decode("utf-8-sig") for kind, body in suites.items()},
    })
    result = docker_worker.run(WORKER.encode("utf-8"), FLAGS, payload, monotonic() + 30)
    if result.returncode or len(result.stdout) > 16_384:
        raise ContractError(f"{variant}: curator Docker worker did not produce a complete verdict")
    records = json.loads(result.stdout)
    if not isinstance(records, dict) or records.keys() != suites.keys():
        raise ContractError(f"{variant}: curator Docker worker returned invalid suite results")
    statuses = {}
    count_fields = ("tests", "failures", *INCOMPLETE_COUNTS)
    for kind in suites:
        item = records[kind]
        # unittest counts the enclosing method once and records each failing subTest separately.
        if (not isinstance(item, dict) or item.keys() != set(count_fields)
                or any(type(item[field]) is not int or item[field] < 0 for field in count_fields)):
            raise ContractError(f"{variant} {kind}: curator Docker worker returned invalid counts")
        if item["tests"] == 0 or any(item[field] for field in INCOMPLETE_COUNTS):
            counts = ", ".join(f"{field}={item[field]}" for field in ("tests", *INCOMPLETE_COUNTS))
            raise ContractError(f"{variant} {kind} ({SUITES[kind]}): curator suite is incomplete ({counts})")
        statuses[kind] = "FAIL" if item["failures"] else "PASS"
    return statuses


@contextmanager
def _workspace_step(label):
    try:
        yield
    except WorkspaceError as error:
        # WorkspaceError messages are fixed contract diagnostics, without file paths or code.
        raise ContractError(f"{label}: {error}") from error
    except OSError as error:
        raise ContractError(f"{label}: {type(error).__name__}") from error


def evaluate_pair(kit, *, receipt_id, preflight_only=False):
    with _workspace_step("kit directory outside codexbench"):
        kit = checked_path(kit)
        if not kit.is_dir():
            raise ContractError("--kit must point to the extracted directory")
        try:
            disjoint(CHECKOUT, kit)
        except WorkspaceError as error:
            raise ContractError("--kit must be in a separate folder outside codexbench") from error
    with _workspace_step("kit task.json"):
        spec_data = read_regular(kit / "task.json")[0]
    task_id = json.loads(spec_data)["task_id"]
    with _workspace_step("public candidate in codexbench"):
        directory, task, spec_hash = read_task(task_id)
        source = directory / "base"
        initial = export_revision(source)
        public, _ = read_tree(source)
    with _workspace_step("kit baseline/"):
        if export_revision(kit / "baseline") != initial:
            raise ContractError("kit baseline does not match the public candidate")
    if sha256_bytes(spec_data) != spec_hash:
        raise ContractError("kit task specification does not match the public candidate")
    with _workspace_step("kit reference/"):
        reference, _ = read_tree(kit / "reference")
    changed = {name for name in public.keys() | reference.keys() if public.get(name) != reference.get(name)}
    allowed_paths = ", ".join(task.allowed_change_paths)
    if not changed:
        raise ContractError(f"reference is unchanged. Edit the allowed file(s) under reference/: {allowed_paths}")
    if changed - (set(task.allowed_change_paths) & public.keys() & reference.keys()):
        raise ContractError(f"reference may modify only these existing file(s): {allowed_paths}")
    for path in changed:
        before = _interface(public[path].data)
        after = _interface(reference[path].data)
        if any(after.get(name) != signature for name, signature in before.items()):
            raise ContractError("reference changes a public signature")
    modules = [name for name in public if "/" not in name and name.endswith(".py") and name[:-3].isidentifier()]

    suites = {}
    for kind, name in SUITES.items():
        with _workspace_step(f"kit curator_tests/{name}"):
            suites[kind] = read_regular(kit / "curator_tests" / name)[0]
    bundle = sha256_bytes(canonical_json_bytes({k: sha256_bytes(v) for k, v in suites.items()}))
    environment_record = {} if preflight_only else {
        "docker": docker_worker.identity(), "flags": FLAGS, "worker": sha256_bytes(WORKER.encode("utf-8")),
    }
    environment = sha256_bytes(canonical_json_bytes(environment_record))
    manifest_record = {
        "task_id": task_id, "public_spec_sha256": spec_hash, "base": initial,
        "suite_sha256": bundle, "environment_sha256": environment, "allowed_paths": task.allowed_change_paths,
    }
    manifest = sha256_bytes(canonical_json_bytes(manifest_record))
    with _workspace_step("kit handoff/"):
        baseline_request = baseline_evaluation_request(kit / "handoff", manifest)
        if baseline_request.task_id != task_id:
            raise ContractError("kit handoff belongs to a different task")
    fixed = FixedRequirements(task.task, task.mandatory_text, sha256_bytes(task.mandatory_text.encode()),
                              "public-copy-only", task.approved_check_ids)
    request = RunRequest(str(uuid4()), task_id, None, RepositoryRef(task.repo_id, source, initial), fixed,
                         resolve_condition(Condition.A), spec_hash, "user")
    with _workspace_step("temporary workspace or receipt"), TemporaryDirectory(prefix="aah-curator-", dir=kit) as temporary:
        root = Path(temporary)
        artifacts = root / "artifacts"
        manager = WorkspaceManager(source, root / "work", artifacts, quiescence_check=lambda _: True)
        handle = manager.prepare(request)
        try:
            for path in sorted(changed):
                (handle.root / path).write_bytes(reference[path].data)
            patch = manager.freeze_patch(handle, manager.snapshot(handle))
        finally:
            manager.cleanup(handle)
        if not patch.applicable or set(patch.changed_paths) != changed:
            raise ContractError("could not freeze the exact allowed reference change")

        def replay(variant, frozen, artifact_root):
            started = monotonic()
            work = root / (variant + "-replay")
            with _workspace_step(f"{variant} patch replay"):
                apply_frozen_patch(source, frozen, artifact_root, work, repo_id=task.repo_id)
                implementation = {path[:-3]: read_regular(work / path)[0] for path in modules}
            return implementation, monotonic() - started

        baseline_replay = replay("baseline", baseline_request.patch, kit / "handoff/artifacts")
        reference_replay = replay("reference", patch, artifacts)
        if preflight_only:
            return None

        def evaluate(variant, frozen, replayed):
            implementation, replay_seconds = replayed
            start = monotonic()
            statuses = _suite_statuses(variant, implementation, suites)
            verdict = "PASS" if all(value == "PASS" for value in statuses.values()) else "FAIL"
            return EvaluationResult(frozen.run_id, task_id, frozen.base_commit, frozen.patch_hash, manifest, bundle,
                                    environment, verdict, statuses["functional"], statuses["regression"], "PASS",
                                    int((replay_seconds + monotonic() - start) * 1000), None, None)

        baseline = evaluate("baseline", baseline_request.patch, baseline_replay)
        repaired = evaluate("reference", patch, reference_replay)
        verify_independent_pair(baseline, repaired, baseline_request, environment_hash=environment,
                                test_bundle_hash=bundle)
        output = kit / f"curator-docker-{receipt_id}"
        output.mkdir(exist_ok=False)
        for path in sorted(artifacts.rglob("*")):
            if path.is_file():
                store_bytes(output / "reference-artifacts", path.relative_to(artifacts).as_posix(), read_regular(path)[0])
        store_bytes(output, "reference-patch.json", dump_record(patch))
        store_bytes(output, "baseline-request.json", dump_record(baseline_request))
        store_bytes(output, "environment.json", canonical_json_bytes(environment_record))
        store_bytes(output, "evaluator-manifest.json", canonical_json_bytes(manifest_record))
        receipts = {"schema_version": "1.0", "status": "CURATOR_DOCKER_PAIR_PASS", "receipt_id": receipt_id,
                    "baseline": json.loads(dump_record(baseline)), "reference": json.loads(dump_record(repaired))}
        store_bytes(output, "receipt.json", canonical_json_bytes(receipts))
    return output / "receipt.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True, help="curator kit directory outside the research checkout")
    parser.add_argument("--preflight", action="store_true", help="check paths and replay both patches without using Docker")
    args = parser.parse_args()
    receipt_id = str(uuid4())
    try:
        receipt_path = evaluate_pair(args.kit, receipt_id=receipt_id, preflight_only=args.preflight)
    except subprocess.CalledProcessError:
        print("CURATOR_DOCKER_BLOCKED Docker Desktop or the pinned image is unavailable")
        return 2
    except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired, RuntimeError, KeyError, TypeError) as error:
        # Keep private test and source details in the curator environment.
        reason = str(error) if isinstance(error, (ContractError, WorkspaceError)) else type(error).__name__
        print("CURATOR_DOCKER_BLOCKED", reason)
        return 2
    if args.preflight:
        print("CURATOR_PREFLIGHT_PASS")
        return 0
    task_id = json.loads(read_regular(receipt_path)[0])["baseline"]["task_id"]
    print("baseline functional FAIL (expected), regression PASS")
    print("reference functional PASS, regression PASS, instruction PASS")
    print(f"CURATOR_DOCKER_PAIR_PASS {task_id} {receipt_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
