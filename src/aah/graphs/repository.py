"""Index definitions and conservatively resolvable Python dependencies without importing code."""

import ast
from collections import defaultdict
from pathlib import Path, PurePosixPath

from aah.domain import ContractError, SnapshotRef
from aah.workspace.patch import read_manifest, verified_bytes
from aah.workspace.snapshot import SCOPE_ID, public_path

from .artifact import Builder, Node, identity


DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _scope(body):
    """Yield lexical contents, leaving nested definitions to their own pass."""
    stack = list(reversed(body))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (*DEFINITIONS, ast.Lambda)):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _module(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[0] == "src" and len(parts) > 1:
        parts.pop(0)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(stmt, module, package_file):
    if isinstance(stmt, ast.Import):
        for item in stmt.names:
            yield item.asname or item.name.split(".")[0], item.name, item.name if item.asname else item.name.split(".")[0]
    else:
        prefix = module if package_file else module.rpartition(".")[0]
        if stmt.level:
            parts = prefix.split(".") if prefix else []
            if stmt.level > len(parts):
                return
            prefix = ".".join(parts[:len(parts) - stmt.level + 1])
        else:
            prefix = ""
        base = ".".join(s for s in (prefix, stmt.module) if s)
        for item in stmt.names:
            target = ".".join(s for s in (base, item.name) if s)
            yield item.asname or item.name, base, target


def _dotted(expr):
    suffix = []
    while isinstance(expr, ast.Attribute):
        suffix.append(expr.attr)
        expr = expr.value
    return [expr.id, *reversed(suffix)] if isinstance(expr, ast.Name) else []


def build(root: Path, snapshot: SnapshotRef, run_id: str):
    manifest = read_manifest(root, snapshot)
    if not snapshot.complete or manifest["scope_id"] != SCOPE_ID:
        raise ContractError("graph requires a complete public snapshot")
    graph = Builder()
    modules, exports = defaultdict(list), defaultdict(list)
    scopes = []
    for row in manifest["files"]:
        path = row["path"]
        if row["state"] != "present" or not path.endswith(".py") or not public_path(path):
            continue
        module, digest = _module(path), row["sha256"]
        file_id = identity(snapshot.repo_id, snapshot.snapshot_id, path, "FILE")
        modules[module].append(file_id)
        data = verified_bytes(root, "blobs", digest)
        try:
            tree = ast.parse(data, filename=path)
        except (SyntaxError, ValueError, RecursionError):
            tree = None
            graph.warnings.add(f"PARSE_ERROR:{path}")
        attrs = {"repo_id": snapshot.repo_id, "path": path, "content_hash": digest,
                 "line_count": len(data.splitlines()), "parse_status": "parsed" if tree else "parse_error"}
        graph.nodes[file_id] = Node(file_id, "FILE", "repo", snapshot.snapshot_id, "observed", (digest,), attrs)
        if tree is None:
            continue
        filename = PurePosixPath(path).name
        test_file = filename.startswith("test_") or filename.endswith("_test.py")
        stack = [(tree, file_id, "", {})]
        while stack:
            owner, owner_id, prefix, enclosing = stack.pop()
            bindings = defaultdict(list)
            children = {}
            nodes = tuple(_scope(owner.body))
            for obj in nodes:
                if isinstance(obj, DEFINITIONS):
                    qual = prefix + obj.name
                    is_class = isinstance(obj, ast.ClassDef)
                    test = test_file and obj.name.startswith("test_") and (
                        isinstance(owner, ast.Module) or isinstance(owner, ast.ClassDef)
                        and owner.name.startswith("Test") and "." not in prefix.rstrip("."))
                    kind = "CLASS" if is_class else "TEST_FUNCTION" if test else "FUNCTION"
                    symbol = identity(snapshot.repo_id, snapshot.snapshot_id, path, kind, qual, obj.lineno, obj.col_offset)
                    start = min([obj.lineno, *(d.lineno for d in obj.decorator_list)])
                    attrs = {"file_id": file_id, "path": path, "qualified_name": qual, "span": (start, obj.end_lineno),
                             "is_async": isinstance(obj, ast.AsyncFunctionDef),
                             "test_id": path + "::" + qual.replace(".", "::") if test else None}
                    graph.nodes[symbol] = Node(symbol, kind, "repo", snapshot.snapshot_id, "observed", (digest,), attrs)
                    children[obj] = symbol
                    container = owner_id if isinstance(owner, ast.ClassDef) else file_id
                    graph.edge(container, symbol, "CONTAINS", (digest,), basis="ast")
                    binding = ("symbol", symbol) if obj in owner.body and not obj.decorator_list else None
                    bindings[obj.name].append(binding)
                elif isinstance(obj, (ast.ImportFrom, ast.Import)):
                    for name, base, target in _imports(obj, module, filename == "__init__.py"):
                        bindings[name].append(("import", target) if obj in owner.body else None)
                elif isinstance(obj, ast.Name) and isinstance(obj.ctx, (ast.Store, ast.Del)):
                    bindings[obj.id].append(None)
                elif isinstance(obj, (ast.Global, ast.Nonlocal)):
                    for name in obj.names:
                        bindings[name].append(None)
                elif isinstance(obj, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and obj.name:
                    bindings[obj.name].append(None)
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in ast.walk(owner.args):
                    if isinstance(arg, ast.arg):
                        bindings[arg.arg].append(None)
            names = dict(enclosing)
            names.update({name: values[0] if len(values) == 1 else None for name, values in bindings.items()})
            if "*" in bindings:
                names = {name: None for name in names}
            if isinstance(owner, ast.Module):
                for name, binding in names.items():
                    if binding and binding[0] == "symbol":
                        exports[module + "." + name].append(binding[1])
            for obj in nodes:
                if isinstance(obj, DEFINITIONS):
                    qual = prefix + obj.name
                    child = children[obj]
                    stack.append((obj, child, qual + ".", enclosing if isinstance(owner, ast.ClassDef) else names))
                    if isinstance(obj, ast.ClassDef):
                        scopes.extend((child, file_id, module, filename, names, "INHERITS", base) for base in obj.bases)
                elif isinstance(obj, ast.Call):
                    scopes.append((owner_id, file_id, module, filename, names, "CALLS", obj.func))
                elif isinstance(obj, (ast.Import, ast.ImportFrom)):
                    scopes.append((owner_id, file_id, module, filename, names, "IMPORTS", obj))

    for owner, file_id, module, filename, names, relation, expr in scopes:
        origin = graph.nodes[owner]
        path = origin.attributes["path"]
        basis = {"line": expr.lineno, "basis": "static_binding"}
        if relation == "IMPORTS":
            for _, base, full in _imports(expr, module, filename == "__init__.py"):
                targets = modules.get(full, ()) if isinstance(expr, ast.ImportFrom) else ()
                targets = targets or modules.get(base, ())
                if len(targets) == 1:
                    graph.edge(file_id, targets[0], relation, origin.evidence_ids, **basis)
                else:
                    graph.warnings.add(f"UNRESOLVED_IMPORT:{path}:{expr.lineno}")
            continue
        parts = _dotted(expr)
        binding = names.get(parts[0]) if parts else None
        targets = ()
        if binding:
            category, value = binding
            if category == "symbol" and len(parts) == 1:
                targets = (value,)
            elif category == "import":
                full = ".".join((value, *parts[1:]))
                if len(modules.get(full.rpartition(".")[0], ())) == 1:
                    targets = exports.get(full, ())
        if len(targets) != 1 or relation == "INHERITS" and graph.nodes[targets[0]].kind != "CLASS":
            graph.warnings.add(f"UNRESOLVED_{relation}:{path}:{expr.lineno}")
            continue
        target = targets[0]
        refs = (*origin.evidence_ids, *graph.nodes[target].evidence_ids)
        graph.edge(owner, target, relation, refs, **basis)
        if relation == "CALLS" and origin.kind == "TEST_FUNCTION":
            graph.edge(owner, target, "TESTS", refs, **basis)
    return graph.finish("repository", run_id, snapshot.snapshot_id, 0)
