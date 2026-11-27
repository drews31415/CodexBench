# S4 통합 및 실험 실행 검증

2026-11-26 S4 오프라인 테스트 181개와 DEV-01 실환경 검증 LT02·LT04가 통과했다. 공통 실패 체크포인트의 B–E 복원은 합성 런타임으로 검증했다. 이후 사용자가 G1 공동 검토 승인을 확인했다.

## DEV-01 합성 비교

같은 공개 과제, 기준 코드, 필수 지침, 검사, 예산을 사용했다. 각 조건은 공통 기준 검사 후 시작했다. 어댑터는 최초 턴에 원본 코드를 유지하고, 복구 메시지를 받으면 공백 처리 함수를 고쳤다. 별도 평가 프로세스가 동결된 패치를 원본에 적용해 판정했다.

| 실행 순서 | 조건 | 최초 턴 | 복구 턴 | 마지막 공개 검사 | 독립 판정 |
| --- | --- | ---: | ---: | --- | --- |
| 1 | B | 1 | 1 | PASS | PASS |
| 2 | C | 1 | 1 | PASS | PASS |
| 3 | A | 1 | 0 | FAIL | FAIL |
| 4 | E | 1 | 1 | PASS | PASS |
| 5 | D | 1 | 1 | PASS | PASS |

합성 실행 폴더는 `results/s4-verification/1f81975d-05c5-48a9-a067-d02b73bc4f1e/`이다. `experiment.json`의 SHA-256은 `5e803d6b6b192ad6e26f29552936ca1237b69bd8bdcf831d88aa3e82891755a3`, `summary.json`은 `a174e836645ebeac2854e88c5a6458bef9d6c587510d1ca812e1381948ce01f6`이다. 결과의 `run_id`로 공개 보고서, 슬롯별 독립 판정 및 패치 해시를 대조할 수 있다. 이 fixture의 토큰과 화폐 비용은 미관측이다.

## 조건 E와 체크포인트

S3-label의 반복 실패에서는 그래프가 제시한 `labels.py` 근거를 담아 E가 `RELOCALIZE`를 전달했다. 후속 턴의 파일 변경을 관측했다. 같은 입력에서 B/D 및 C/E의 최초 메시지 바이트가 각각 일치했고 A/B/C의 그래프 호출, A/B/D의 DCC 호출은 각각 0건이었다. 유형별 필수 근거가 상한을 넘으면 한 번 일반 복구 메시지를 구성하고, 이 메시지도 초과하면 후속 턴 예약 전에 멈췄다.

별도 DEV-01 실험에서 B의 공통 최초 턴이 새 파일 `observed.txt`를 만들었다. 최초 적격 공개 실패 시점의 파일, 패치, 공개 원장, trigger, 이력과 잔여 예산을 저장했다. B–E가 각각 새 작업공간에서 동일 스냅샷을 검증하고 `NEW_SESSION`으로 복구를 시작했다. 네 분기는 각각 복구 턴 1회와 독립 PASS를 기록했다. 최초 턴은 원본 실행 한 곳에만 계상했다.

분기 실험 폴더는 `results/s4-verification/8ab8df62-951f-4727-8df4-74ecd1cf25a7/`이다. 체크포인트 ID는 `ee5772bd-5224-4ccf-a6ab-0ca286b4c273`, `summary.json` SHA-256은 `8bc89e0acaaae6232d3057dda365ab7cc1be7eab9734cd823116d717c6d53e08`이다. `source-plan.json`과 `branch-plan.json`에서 공통 원본과 네 슬롯의 연결을 확인할 수 있다.

별도의 사용량 fixture에서는 최초 턴 11토큰과 각 분기 5토큰을 독립적으로 기록했다. 같은 실패를 그대로 유지한 분기는 한 번만 복구를 예약했고, 체크포인트 해시를 바꾼 분기는 모델 호출 전에 중단됐다.

## 계측 및 재실행

실험의 과제, 설정, 환경, 구현 버전과 슬롯 순서는 실행 전에 고정한다. 슬롯을 먼저 예약하고, 이미 보고서가 있는 슬롯은 저장한 결과를 검증한다. 예약 후 보고서가 남지 않은 경우 해당 슬롯 ID를 알리고 중단한다. 컨트롤러의 공개 보고서에는 독립 평가 결과를 포함하지 않는다.

`usage.jsonl`은 턴별 모델 사용량의 출처·모드·관측 범위·소요 시간을 담는다. 누적 사용량은 같은 세션의 검증된 기준에서 차분하며 cached/reasoning 부분값은 총량에 중복 합산하지 않는다. `context.jsonl`과 `overhead.jsonl`에는 컨텍스트 구성, 공개 검사, 스냅샷·diff, 그래프, 패치 동결 등 로컬 단계의 시간을 기록한다. 합성 실행에서 실제 모델 사용량을 확인할 수 없어 유효한 토큰 단위 성공당 비용을 계산할 수 없다.

저장소 루트의 PowerShell에서 재현한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m pytest -q -m 'not live'
python -m benchmarks.smoke.s4 --output-dir results/s4-verification
python -m benchmarks.smoke.s4 --checkpoint --output-dir results/s4-verification
python -m aah experiment --manifest results/s4-verification/1f81975d-05c5-48a9-a067-d02b73bc4f1e/experiment.json
```

## 실환경 검증

사용자가 RH·CE의 S4 전용 승인을 확인했다. 범위는 기존 구독에서 DEV-01 A–E 각 1회, 조건별 최초 턴 1회와 B–E의 필요 시 복구 턴 각 1회였다. 전체 상한은 9턴, 추가 결제 승인 한도는 0원이었다. Codex CLI 0.166.0, `gpt-6-luna`, `low`와 고정 이미지의 Docker 평가기를 사용했다. 모든 조건의 공개 기준 검사 결과와 환경 해시가 일치했다. 최초 전달 메시지 해시도 B/D 및 C/E에서 각각 일치했다.

개발 세션 사전 점검은 `MXC_UNAVAILABLE`, `MXC_SETUP_FAILED`, `WINDOWS_ACCESS_DENIED`로 중단됐다. `results/s4-live/preflights/05172832-229a-46de-adc8-c17da38dacfd/operator.json`에 당시 모델 호출 0건이 기록돼 있다. 이후 사용자 PowerShell에서 MXC 및 Docker 사전 점검을 통과했고 ChatGPT 로그인을 확인했다. 아래 명령이 2026-11-26 23:18 KST에 `S4_LIVE_RECORDED`와 `LT02=True LT04=True`를 출력했다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.smoke.live_dev01 --execute-s4
```

### 운영자 실행 결과

| 실행 순서 | 조건 | 첫 턴 공개 검사 | 복구 턴 | 마지막 공개 검사 | 독립 Docker 판정 | 모델 토큰 |
| --- | --- | --- | ---: | --- | --- | ---: |
| 1 | B | FAIL | 1 | PASS | PASS | 123,496 |
| 2 | C | PASS | 0 | PASS | PASS | 88,105 |
| 3 | A | FAIL | 0 | FAIL | FAIL | 35,669 |
| 4 | E | FAIL | 1 | PASS | PASS | 150,886 |
| 5 | D | PASS | 0 | PASS | PASS | 64,491 |

실제 모델 호출은 최초 5턴과 복구 2턴으로 총 7턴이었다. CLI 이벤트에서 합계 462,647모델 토큰을 관측했다. 같은 세션의 후속 턴 사용량은 두 완료 이벤트 사이의 차분으로 계산했다. 캐시된 토큰을 입력 토큰에 중복 합산하지 않았다. 결제액은 실행 로그에서 관측되지 않았다.

- **LT02 통과.** E의 첫 턴에서 최초 기준 검사와 같은 공개 실패가 반복돼 `T1`이 발동했다. 그래프 진단 근거가 유형별 전략을 선택하기에 부족해 `GENERIC_RETRY`로 전환했다. DCC가 구성한 복구 메시지를 같은 세션에 한 번 전달했고, 완료된 후속 턴의 파일 변경 및 공개 검사 PASS를 확인했다. B도 같은 공개 실패 뒤 일반 재시도 1회로 통과했다.
- **LT04 통과.** 계획한 A–E 다섯 슬롯에 각 실제 실행 1건을 연결했다. 각 슬롯의 공개 보고서, 동결 패치, 독립 평가 요청과 결과 해시가 일치했다. 최초 및 복구 턴의 사용량도 CLI 완료 이벤트와 대조했다.

DEV-01 한 과제에서 조건별 관측이 한 건씩이다. 이 결과가 뒷받침하는 범위는 하네스가 실제 실패를 감지하고 복구를 전달하며 독립 판정까지 기록한다는 점이다.

### 증거 경로 및 G1 상태

증거는 `results/s4-live/s4-dev01-live-20261126/`에 보관한다. E의 실행 ID는 `03944229-6ad9-49c7-a24b-e50ff6b3c674`이다. 해당 실행의 `checks.jsonl`, `interventions.jsonl`, `context.jsonl`, `usage.jsonl`이 실패, 복구 전달, 후속 관측과 사용량을 연결한다.

| 파일 | SHA-256 |
| --- | --- |
| `experiment.json` | `96e48dc0215e1b074669f5dfd9ba412dfe2d6b292912cd1c2f296b348929c1c2` |
| `summary.json` | `6e9b5ce8dfa806eff67f5aaf98f4c309d61782f4aab5af30b711c4348b99ac49` |
| `live-checks.json` | `03da140eedd9c1860f3baa2ffa1ffc15cf66da5118b06d57ce6f9029a8dcf5d0` |

운영자 자동 점검은 `review: pending`으로 기록을 남겼다. 이후 사용자가 이 대화에서 “G1 승인됐어. S5 Pilot 준비 진행해”라고 팀의 승인을 확인했다. 승인 사실은 실험 기록 EL-20261126-021에 등록했다. [실험 기록](EXPERIMENT_LOG.md) · [구현계획 §10.1](../project/IMPLEMENTATION_PLAN.md#101-승인-게이트)
