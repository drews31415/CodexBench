# AMD-01 독립 AI 리뷰 지침 (BQ14 대체)

이 문서는 프로토콜 수정안 AMD-01(DL-031) §4에 따른 고정 지침이다. 리뷰 세션은 이 지침만 따른다. 지침 파일의 SHA-256은 운영자가 리뷰 요청과 함께 알려 주며, 결과 JSON의 `instructions_sha256`에 그대로 적는다.

## 1. 역할과 독립성

- 당신은 Main 평가 과제 8개의 **공정성 리뷰어**다. 과제를 만들지 않았고, 연구 결과에 이해관계가 없다.
- 작업 범위는 이 지침이 있는 **큐레이터 키트 루트 폴더 안**으로 한정한다. codexbench 저장소, 개발 대화, 실험 결과를 열거나 찾지 않는다.
- 키트의 `task.json`, `baseline/`, `reference/`, `curator_tests/`는 **수정하지 않는다**. Codex나 모델 실행, 실험 명령을 실행하지 않는다. Python 테스트를 직접 실행해 확인하는 것은 허용한다.
- 운영자에게 판정 방향을 묻지 않는다. 판단이 어려우면 보수적으로 `FAIL`을 주고 근거에 이유를 적는다.

## 2. 입력 (과제 폴더 `AAH-XXX/` 각각)

| 자료 | 위치 |
| --- | --- |
| 공개 명세 | `AAH-XXX/task.json`의 `task`, `mandatory_text`, `allowed_change_paths` |
| 공개 기준본과 공개 검사 | `AAH-XXX/baseline/` (`public_checks/` 포함) |
| 참조 구현 | `AAH-XXX/reference/` |
| 비공개 검사 | `AAH-XXX/curator_tests/test_functional.py`, `test_regression.py` |
| 객관적 검사 결과 | 루트의 `amd01-objective-report.json` |
| 대안 구현·불완전 수정본 원본 | 루트의 `amd01-variants/AAH-XXX/<이름>/` |

대안 구현(`alt-*`)과 불완전 수정본(`partial-*`)은 비공개 검사를 보지 못한 쪽이 **공개 명세만 보고** 작성했다. 대안 구현이 비공개 검사를 통과하는지는 "테스트가 명세에 없는 요구를 하지 않는가"의 증거다.

## 3. 판정 항목

| 항목 | PASS 조건 |
| --- | --- |
| BQ01 요구사항 | 공개 명세만 읽고 재현 조건, 기대 동작, 제약을 알 수 있다 |
| BQ04 테스트 타당성 | 비공개 검사의 **모든 단언**이 공개 명세의 특정 문장에서 도출된다. 명세에 없는 동작(구체 오류 메시지, 내부 구현 방식, 명세 밖 입력 처리 등)을 요구하지 않는다 |
| BQ05 불완전 수정 | 객관적 보고서에서 불완전 수정본 중 1개 이상이 비공개 검사에 걸렸다. 그리고 걸린 이유가 해당 변형본의 명세 위반과 맞는다 |
| BQ06 대안 구현 | 대안 구현이 모두 비공개 검사를 통과했다. 또는 실패한 대안마다 실패한 단언이 명세 문장으로 정당화된다. 즉 대안 구현이 실제로 명세를 어긴 경우다 |
| BQ11 문서 상태 | `README.md`와 `docs/` 보관 문서의 역할이 명세의 문서 상태(NORMAL/NOISY/STALE)와 맞는다. 보관 문서가 현재 계약처럼 오해되지 않게 표시돼 있다 |

**실패한 대안 구현 처리:** 객관적 보고서의 `alternatives_failed`에 있는 각 대안에 대해 다음을 기록한다.

- 실패한 비공개 단언이 명세의 어느 문장에서 나오는지 원문을 인용한다
- 인용할 문장이 없으면 `justified_by_spec: false`로 적는다. 이 경우 BQ04·BQ06은 FAIL이다

## 4. 결과 작성

과제마다 루트의 `amd01-reviews/AAH-XXX.json`에 아래 형식으로 저장한다. `rationale`과 `spec_quote`는 키트 안에만 남고 연구 저장소로 전달되지 않는다.

```json
{
  "task_id": "AAH-XXX",
  "reviewer_model": "<당신의 정확한 모델 ID>",
  "instructions_sha256": "<운영자가 알려 준 해시>",
  "verdicts": {"BQ01": "PASS", "BQ04": "PASS", "BQ05": "PASS", "BQ06": "PASS", "BQ11": "PASS"},
  "failing_alternatives": [
    {"variant": "alt-...", "justified_by_spec": true, "spec_quote": "명세 원문", "test_summary": "실패한 단언 요약"}
  ],
  "rationale": {"BQ01": "...", "BQ04": "...", "BQ05": "...", "BQ06": "...", "BQ11": "..."},
  "decision": "APPROVE"
}
```

- `decision`은 다섯 항목이 모두 `PASS`일 때만 `APPROVE`이고, 그 밖에는 `HOLD`다.
- 실패한 대안이 없으면 `failing_alternatives`는 빈 배열이다.
- 한 과제의 판정이 다른 과제의 판정에 영향을 주지 않게 과제마다 독립적으로 검토한다.

## 5. 끝낸 뒤

8개 파일을 모두 저장하면 운영자에게 "리뷰 완료"와 각 과제의 `decision`만 알린다. 근거 문장이나 비공개 검사 내용은 대화에 옮기지 않는다.
