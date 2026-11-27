"""Compute small, pure Dev submissions inside the pinned Docker worker."""

import ast
import json
import sys


ENTRY_POINTS = {
    "DEV-01": ("format_label", ("text",)),
    "DEV-02": ("is_live", ("now", "expires_at")),
    "DEV-03": ("can_accept", ("config", "queued")),
    "DEV-04": ("count_by_status", ("records", "statuses")),
    "DEV-05": ("convert_rows", ("lines",)),
}
BUILTINS = {
    function.__name__: function for function in (
        abs, all, any, bool, dict, enumerate, int, isinstance, len, list, max, min,
        range, reversed, set, sorted, str, sum, tuple, zip, ValueError, TypeError,
    )
}
METHODS = {
    "append", "count", "endswith", "extend", "fromkeys", "get", "index", "isascii",
    "isdecimal", "isdigit", "items", "keys", "lower", "lstrip", "replace",
    "rstrip", "setdefault", "split", "startswith", "strip", "values",
}
SYNTAX = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Assign, ast.AnnAssign, ast.AugAssign,
    ast.Return, ast.If, ast.For, ast.Try, ast.ExceptHandler, ast.Continue, ast.Break, ast.Pass,
    ast.Name, ast.Constant, ast.Expr, ast.Call, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
    ast.comprehension, ast.Compare, ast.IfExp, ast.BoolOp, ast.BinOp, ast.UnaryOp,
    ast.operator, ast.unaryop, ast.boolop, ast.cmpop, ast.expr_context,
)


def candidate_tree(task_id, source):
    if len(source) > 8_192 or task_id not in ENTRY_POINTS:
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    entry, params = ENTRY_POINTS[task_id]
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    if entry not in functions or tuple(arg.arg for arg in functions[entry].args.args) != params:
        return None
    allowed_calls = set(BUILTINS) | set(functions)
    for node in ast.walk(tree):
        if not isinstance(node, SYNTAX) or isinstance(node, (ast.Pow, ast.LShift, ast.RShift)):
            return None
        identifiers = (node.id,) if isinstance(node, ast.Name) else (
            (node.arg,) if isinstance(node, ast.arg) else ((node.name,) if isinstance(node, ast.FunctionDef) else ()))
        if any("__" in name for name in identifiers):
            return None
        if isinstance(node, ast.Attribute) and (node.attr not in METHODS or type(node.ctx) is not ast.Load):
            return None
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, (ast.Name, ast.Attribute))
            or isinstance(node.func, ast.Name) and node.func.id not in allowed_calls
            or any(keyword.arg is None for keyword in node.keywords)
        ):
            return None
        if isinstance(node, ast.FunctionDef):
            arguments = node.args
            if (node.decorator_list or getattr(node, "type_params", ()) or arguments.defaults
                    or arguments.posonlyargs or arguments.kwonlyargs or arguments.vararg or arguments.kwarg):
                return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if not isinstance(node, ast.FunctionDef):
            return None
    return tree


def main():
    # The host sends only source, task ID and case inputs. It keeps expected outputs.
    request = json.loads(sys.stdin.buffer.read(32_769))
    task_id = request["task_id"]
    tree = candidate_tree(task_id, request["source"])
    if tree is None:
        return 2
    namespace = {"__builtins__": BUILTINS.copy()}
    outputs = []
    try:
        exec(compile(tree, "module.py", "exec"), namespace)
        function = namespace[ENTRY_POINTS[task_id][0]]
    except (ArithmeticError, LookupError, TypeError, ValueError, NameError):
        function = None
    for args in request["inputs"]:
        try:
            value = function(*args)
            encoded = json.dumps(value, allow_nan=False)
            if len(encoded) > 16_384:
                return 2
        except (ArithmeticError, LookupError, TypeError, ValueError, NameError, RecursionError):
            outputs.append([False, None])
        else:
            outputs.append([True, value])
    sys.stdout.write(json.dumps(outputs, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
