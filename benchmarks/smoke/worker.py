"""검토된 두 공개 합성 과제의 입력값만 계산하는 표준 라이브러리 전용 worker."""

import ast
import hashlib
import json
import sys


# 줄바꿈을 LF로 통일한 파이썬 소스의 SHA-256. 제출자가 이 목록을 구성하지 않는다.
REVIEWED = {
    "DEV-01": ("format_label", (
        "462598a4c3ec043d1f287113a89dfa363aefd82e4b458fd52737fa3c7b761bb7",
        "89a2689f5d5ab217c6034ae499051c3d04ac7ba73c55bdc27b8e623044c62259",
    )),
    "DEV-02": ("is_live", (
        "e602136181881777ae86325bbba1d37ae263343421c5de32d3f375c6b526cfc9",
        "e316734a62d1e0f15709a379d01a85f32cf205333c84b22d500864e848f59de4",
    )),
}


def is_reviewed(task_id: str, source: bytes) -> bool:
    return hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest() in REVIEWED[task_id][1]


def is_dev01_candidate(source: bytes) -> bool:
    """Allow the small DEV-01 expression runner to execute only a string strip."""
    if len(source) > 4096:
        return False
    try:
        nodes = ast.parse(source).body
    except (SyntaxError, ValueError, RecursionError):
        return False
    if len(nodes) != 1 or not isinstance(nodes[0], ast.FunctionDef):
        return False
    function = nodes[0]
    args = function.args
    if (function.name != "format_label" or function.decorator_list or function.type_comment
            or getattr(function, "type_params", ())
            or function.returns is not None or args.posonlyargs or args.kwonlyargs or args.defaults
            or args.vararg or args.kwarg or len(args.args) != 1
            or args.args[0].arg != "text" or args.args[0].annotation is not None):
        return False
    body = function.body[1:] if ast.get_docstring(function, clean=False) is not None else function.body
    if len(body) != 1 or not isinstance(body[0], ast.Return) or not isinstance(body[0].value, ast.Call):
        return False
    call = body[0].value
    return (
        isinstance(call.func, ast.Attribute) and call.func.attr == "strip"
        and isinstance(call.func.value, ast.Name) and call.func.value.id == "text"
        and not call.keywords and len(call.args) <= 1
        and all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in call.args)
    )


def main() -> int:
    request = json.loads(sys.stdin.buffer.read())
    task_id = request["task_id"]
    source = request["source"].encode("utf-8")
    candidate = sys.argv[1:] == ["--dev01-candidate"] and task_id == "DEV-01" and is_dev01_candidate(source)
    if not is_reviewed(task_id, source) and not candidate:
        return 2
    namespace = {}
    exec(compile(source, "module.py", "exec"), namespace)
    function = namespace[REVIEWED[task_id][0]]
    outputs = [function(*args) for args in request["inputs"]]
    sys.stdout.buffer.write(json.dumps(outputs).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
