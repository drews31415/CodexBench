"""Package public inputs and verified empty baselines for curator authoring."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from aah.domain import ContractError
from aah.workspace.snapshot import read_regular, read_tree

from .qualify import TASK_IDS, baseline_evaluation_request, read_task


REMAINING = tuple(task_id for task_id in TASK_IDS if task_id != "AAH-S02")


def pack(output, handoffs, task_ids):
    entries = {}
    rows = []
    for task_id in task_ids:
        directory, task, _ = read_task(task_id)
        handoff = handoffs / task_id
        request = baseline_evaluation_request(handoff, "0" * 64)
        if request.task_id != task_id:
            raise ContractError("public handoff belongs to a different curator task")
        public_files, _ = read_tree(directory / "base")
        for name, item in public_files.items():
            entries[f"{task_id}/baseline/{name}"] = item.data
            entries[f"{task_id}/reference/{name}"] = item.data
        entries[f"{task_id}/task.json"] = read_regular(directory / "task.json")[0]
        for name in ("handoff.json", "baseline-patch.json", f"artifacts/{request.patch.patch_ref}"):
            entries[f"{task_id}/handoff/{name}"] = read_regular(handoff / name)[0]
        entries[f"{task_id}/curator_tests/"] = b""
        rows.append(f"| {task_id} | {', '.join(task.allowed_change_paths)} |")
    guide = r"""# Main 후보 큐레이터 작업 묶음

압축은 `codexbench` 밖에 풉니다. 과제별 폴더에 공개 기준본, 수정용 사본, 평가 연결용 공개 자료가 있습니다.

1. 과제 폴더의 `task.json`을 읽고 `reference/`의 허용 파일을 수정합니다.
2. `curator_tests/`에 `test_functional.py`와 `test_regression.py`를 만듭니다. 표준 unittest로 작성하고, 해당 과제의 공개 함수나 모듈을 import합니다. 기능 테스트는 공개 예제와 다른 입력으로 결함을 검출해야 합니다. 회귀 테스트는 이전부터 맞던 동작이 기준본과 참조본에서 모두 통과하는지 검사합니다.
3. Docker Desktop을 켜고, 과제의 `task.json`이 있는 폴더에서 일반 PowerShell로 실행합니다.

```powershell
$repo = 'C:\Users\dldls\OneDrive\Code\codexbench'
$env:PYTHONPATH = "$repo\src;$repo"
python -m evaluation.curator_check --kit .
```

마지막 줄이 `CURATOR_DOCKER_PAIR_PASS <과제 ID> <기록 ID>`이면 기록 ID를 남깁니다. 작성한 테스트, 참조본 및 상세 기록은 큐레이터 환경에 보관합니다. Docker 결과가 준비되면 비작성자 리뷰를 받습니다.

| 과제 | 수정 허용 파일 |
| --- | --- |
"""
    entries["START_HERE.md"] = (guide + "\n".join(rows) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, body in sorted(entries.items()):
            archive.writestr(name, body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoffs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task", choices=TASK_IDS, action="append")
    args = parser.parse_args()
    task_ids = tuple(dict.fromkeys(args.task)) if args.task else REMAINING
    try:
        pack(args.output, args.handoffs, task_ids)
    except (OSError, ValueError, KeyError) as error:
        print("CURATOR_KIT_BLOCKED", str(error))
        return 2
    print("CURATOR_KIT_READY", args.output, f"tasks={len(task_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
