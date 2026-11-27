"""Run unittest suites against submitted module sources in the pinned Docker worker.

Shared by the Main public-check runner (public suites) and the curator-side Main evaluator
(private suites). Only module sources and suite sources cross into the container; results
are bounded counts. A submission that cannot be imported is a FAIL, not an infrastructure error.
"""

from __future__ import annotations

import json
from time import monotonic

from aah.domain import ContractError, canonical_json_bytes, sha256_bytes
from benchmarks.smoke import docker_worker


FLAGS = ("-I", "-S", "-B")
COUNTS = ("tests", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")
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
    try:
        exec(compile(source, kind + ".py", "exec"), suite.__dict__)
    except Exception:
        observed[kind] = {"load_error": True}
        continue
    result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromModule(suite))
    observed[kind] = {
        "load_error": False, "tests": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
    }
print(json.dumps(observed))
"""
WORKER_SHA256 = sha256_bytes(WORKER.encode("utf-8"))


def environment_record(identity: dict) -> dict:
    return {"docker": identity, "flags": FLAGS, "worker": WORKER_SHA256}


def run_suites(modules: dict[str, bytes], suites: dict[str, bytes], deadline: float, *, transport=None) -> dict:
    """Return ``{kind: {"status": PASS|FAIL, ...counts}}``; raise ContractError for an unusable worker."""
    transport = transport or docker_worker.run
    payload = canonical_json_bytes({
        "modules": {name: body.decode("utf-8-sig") for name, body in modules.items()},
        "suites": {kind: body.decode("utf-8-sig") for kind, body in suites.items()},
    })
    if deadline <= monotonic():
        raise TimeoutError("suite deadline already passed")
    result = transport(WORKER.encode("utf-8"), FLAGS, payload, deadline)
    if result.returncode or len(result.stdout) > 16_384:
        raise ContractError("Docker suite worker did not return bounded results")
    records = json.loads(result.stdout)
    if not isinstance(records, dict) or records.keys() != suites.keys():
        raise ContractError("Docker suite worker returned invalid suite results")
    outcome = {}
    for kind, item in records.items():
        if item == {"load_error": True}:
            outcome[kind] = {"status": "FAIL", "load_error": True}
            continue
        if (not isinstance(item, dict) or item.keys() != {"load_error", *COUNTS} or item["load_error"] is not False
                or any(type(item[field]) is not int or item[field] < 0 for field in COUNTS)):
            raise ContractError("Docker suite worker returned invalid counts")
        if item["tests"] == 0:
            raise ContractError(f"{kind}: approved suite ran no tests")
        # Errors raised by submitted code are failures; skips or expected failures are not passes.
        failed = any(item[field] for field in ("failures", "errors", "skipped", "expected_failures",
                                                "unexpected_successes"))
        outcome[kind] = {"status": "FAIL" if failed else "PASS", **{field: item[field] for field in COUNTS}}
    return outcome
