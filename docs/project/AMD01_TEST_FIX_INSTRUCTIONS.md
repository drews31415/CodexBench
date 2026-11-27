# AMD-01 비공개 검사 1회 수정 지침 (§5 검사 수정 허용 범위)

이 문서는 프로토콜 수정안 AMD-01(DL-031) §5의 "BQ04·05·06에서 실패한 과제는 Main 동결 전에 한 번 비공개 검사를 고칠 수 있다"를 수행하는 고정 지침이다. 수정 세션은 이 지침만 따른다. 지침 파일의 SHA-256은 운영자가 요청과 함께 알려 주며, 수정 기록의 `fix_instructions_sha256`에 그대로 적는다.

## 1. 역할과 독립성

- 당신은 **검사 수정자**다. 과제를 만들지 않았고, 연구 결과에 이해관계가 없다.
- 실험 대상 계정(OpenAI/Codex/ChatGPT)이 아닌 제공자의 **새 세션**이어야 한다. 1차 리뷰를 한 세션과도, 수정 후 재리뷰를 할 세션과도 다른 세션이다.
- 작업 범위는 이 지침이 있는 **큐레이터 키트 루트 폴더 안**으로 한정한다. codexbench 저장소, 개발 대화, 실험 결과를 열거나 찾지 않는다.
- `amd01-variants/`(대안 구현·불완전 수정본 원본)는 **열지 않는다.** 검사는 특정 변형본이 아니라 공개 명세에 맞춘다.
- Codex나 모델 실행, 실험 명령을 실행하지 않는다. Python 테스트를 직접 실행해 확인하는 것은 허용한다.
- 운영자에게 수정 방향을 묻지 않는다.

## 2. 대상과 입력

- 대상은 운영자가 요청에서 지정한 과제뿐이다. 1차 리뷰에서 BQ04·05·06 중 하나 이상이 `FAIL`이라 `HOLD`된 과제다.
- 과제마다 **한 번만** 수정한다. 이미 `amd01-fixes/AAH-XXX.json`이 있는 과제는 손대지 않는다.

| 자료 | 위치 |
| --- | --- |
| 공개 명세 | `AAH-XXX/task.json`의 `task`, `mandatory_text`, `allowed_change_paths` |
| 공개 기준본과 공개 검사 | `AAH-XXX/baseline/` |
| 참조 구현 | `AAH-XXX/reference/` |
| 비공개 검사 (수정 대상) | `AAH-XXX/curator_tests/test_functional.py`, `test_regression.py` |
| 1차 리뷰 근거 | `amd01-reviews/AAH-XXX.json`의 `rationale`, `failing_alternatives` |

## 3. 허용하는 변경

수정할 수 있는 파일은 `AAH-XXX/curator_tests/test_functional.py`와 `AAH-XXX/curator_tests/test_regression.py` 두 개뿐이다.

| 동작 | 조건 |
| --- | --- |
| `REMOVED` 단언 삭제 | 1차 리뷰가 공개 명세 근거가 없다고 지적한 단언 |
| `NARROWED` 단언 축소 | 명세가 요구하는 범위를 넘는 단언. 예를 들어 명세가 정하지 않은 오류 메시지 문구, 내부 구현 방식, 명세 밖 입력 처리를 요구하는 부분만 덜어 낸다 |
| `ADDED` 단언 추가 | 삭제·축소로 명세의 핵심 요구를 더 이상 검사하지 못하게 될 때만. 공개 명세 원문을 인용할 수 있어야 한다 |

**금지:**

- `task.json`, `baseline/`, `reference/`, 공개 검사, 다른 과제 폴더를 수정하는 것
- 명세가 요구하는 결함 검출을 약화하는 것. 기준본의 결함을 잡던 단언을 근거 없이 지우면 안 된다
- 참조 구현의 내부 구조에 맞춘 단언을 새로 넣는 것

## 4. 수정 후 직접 확인

키트 폴더에서 Python 테스트로 다음을 확인한다. 공식 판정은 운영자가 Docker에서 다시 낸다.

- 참조 구현에서 기능·회귀 검사가 모두 통과한다
- 공개 기준본에서 기능 검사는 실패하고 회귀 검사는 통과한다

어느 하나라도 맞지 않으면 수정을 되돌리지 말고 기록의 `self_check`에 `FAIL`로 적는다.

## 5. 기록

과제마다 루트의 `amd01-fixes/AAH-XXX.json`에 아래 형식으로 저장한다. `reason`과 `spec_quote`는 키트 안에만 남고 연구 저장소로 전달되지 않는다.

```json
{
  "task_id": "AAH-XXX",
  "fixer_model": "<당신의 정확한 모델 ID>",
  "fix_instructions_sha256": "<운영자가 알려 준 해시>",
  "changes": [
    {"file": "test_functional.py", "test": "<테스트 함수 이름>", "action": "REMOVED",
     "reason": "<리뷰 지적과 바꾼 이유>", "spec_quote": "<ADDED·NARROWED면 명세 원문, REMOVED면 빈 문자열 허용>"}
  ],
  "self_check": "PASS"
}
```

- `changes`는 비어 있으면 안 된다. 고칠 것이 없다고 판단하면 기록 파일을 만들지 말고 운영자에게 그렇게 알린다.
- 수정을 마친 과제의 1차 리뷰 파일 `amd01-reviews/AAH-XXX.json`은 `amd01-reviews/round1/AAH-XXX.json`으로 옮긴다. 재리뷰는 다른 새 세션이 한다.

## 6. 끝낸 뒤

운영자에게 "수정 완료"와 수정한 과제 목록만 알린다. 수정 내용이나 비공개 검사 내용은 대화에 옮기지 않는다.
