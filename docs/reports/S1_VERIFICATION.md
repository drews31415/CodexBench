# S1 공통 실행 검증

2026-11-26 · IP06–IP10 · **S1 완료**

| 작업 | 구현 및 확인 범위 |
| --- | --- |
| IP06 | Codex 수신 이벤트를 공개 증거로 정규화한다. 원시 참조와 미관측 근거를 보존하고 실행별 중복을 제거한다. 신뢰 공개 검사, 스냅샷과 실제 변경분을 연결한다. 원시 본문은 제한 경로에서만 읽는다. |
| IP07 | 허용 상태 전이, 단조 시간과 정리 여유, 턴·개입 예약을 적용한다. 중단과 잔존 프로세스 미확인을 구분하고, 정리 확인 뒤에만 패치를 동결한다. |
| IP08 | 기존 A–E 고정 정책을 재사용한다. A/B 실행 흐름과 A의 고정 입력, B/D DEFAULT 초기 입력의 바이트 동등성을 확인했다. C/E의 DCC 비교는 후속 통합 범위다. |
| IP09 | 공개 검사·탐색 관측에서 T1–T4를 감지하고 T5 결측을 기록한다. 우선순위는 T3 → T1 → T2 → T4다. 평면 진단을 거쳐 일반 재시도에 공개 검사 근거를 제공한다. 같은 증거 상태에서 예약은 한 번만 가능하다. 실제 CLI에서 구조화 탐색이 관측되지 않으면 T2를 보류한다. |
| IP10 및 IP11/IP14a 의존 경로 | 합성 A/B 실행과 공개 보고서 조회를 확인했다. DEFAULT 패키징에 승인된 공개 검사, 현재 실패, 이전 공개 근거 및 실제 변경 경로를 사용한다. Windows MXC에서 조건 A로 Codex 한 턴을 실행하고 공개 보고서 및 독립 Docker 판정이 저장됐음을 확인했다. |

## 합성 DEV-01 실행

| 조건 | 실행 ID | 턴 / 외부 개입 | 공개 검사 | 보고서 SHA-256 |
| --- | --- | ---: | --- | --- |
| A | `ee141d00-4b9a-47df-96e0-4fb60f296382` | 1 / 0 | FAIL | `90b7c5d533090fda6003da9412769b27b024ecfae0d8cacc424ec58fe8b1f121` |
| B | `1a2a31ce-07d0-461e-b4de-a5cfb9b753b7` | 2 / 1 | FAIL → PASS | `fc15c40f55d88700ff51b7e03c239afe98703cd75226c2143867b77fd670d380` |

B의 합성 런타임은 T4에 따른 후속 요청을 받고 고정 테스트 수정을 적용했다. 모델 추가 호출은 0건이다. 공개 결과에는 독립 평가 판정을 포함하지 않는다. [G0 조건 A에 해당하는 실제 실행과 독립 판정](S0_VERIFICATION.md#첫-실제-턴-완료-및-독립-판정)

저장소 루트에서 실행한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.smoke.s1 --condition A --output-dir results/s1-verification
python -m benchmarks.smoke.s1 --condition B --output-dir results/s1-verification
python -m aah report --run-id 1a2a31ce-07d0-461e-b4de-a5cfb9b753b7 --results-root results/s1-verification
python -m pytest -q -m 'not live'
```

새로 실행할 때는 출력에 표시된 실행 ID로 보고서를 조회한다. 실행별 디렉터리에는 `manifest.json`, `report.json`, 전이·증거·검사·사용량·개입 기록과 패치 artifact가 남는다.

## 조건 A 실제 Codex 실행

기존 DEV-01 운영자 실행기는 MXC·Docker·ChatGPT 로그인 점검을 거쳐 같은 작업 사본을 S1에 제공한다. S1은 승인된 초기 입력을 한 번 전달하고, 턴 정리·공개 검사·패치 동결·공개 보고서 저장을 담당한다. 이후 별도 Docker 평가기가 패치를 판정한다. 외부 개입과 후속 호출은 0회다.

운영자는 Docker Desktop이 실행 중인 일반 PowerShell에서 다음 명령을 실행했다. [DEV-01 승인 범위와 만료](DEV_SMOKE_PROFILE.md#승인-기록)

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.smoke.live_dev01 --execute-s1
```

출력의 `S1_LIVE_COMPLETE`는 실제 턴과 평가 경로가 실행됐음을 뜻한다. 독립 평가의 과제 판정은 별도 `PASS` 또는 `FAIL`로 남는다. 공개 결과는 `results/s1-live/<run-id>/report.json`에, 예약 및 독립 평가 결과는 운영자 기록에 남는다. 모델 전달 직전 한 번만 승인 예약을 기록하므로 사전 점검 실패는 예약을 소진하지 않는다. 모의 통합 검사에서 중복 실행 차단과 공개 결과 저장 후 독립 평가 연결을 확인했다.

개발 세션의 첫 시도는 `MXC_UNAVAILABLE` 및 Windows 접근 거부로 사전 점검에서 차단됐다. 실행 `e0ca03cb-d41e-40bc-b774-1d7186b22278`의 `operator.json` SHA-256은 `aa44632dbbfd1a13e1e653cabf7fcf2bcced2cdc76c179ef471f7da0c5b60736`이다. 해당 시도는 모델 호출 0건이었고 승인 예약 파일을 생성하지 않았다.

### 운영자 실환경 검증 결과

2026-11-26 17:00:11 KST에 시작한 운영자 실행 `08559adb-e4ad-483d-b7d5-e6e9e0796eaa`가 `S1_LIVE_COMPLETE`로 끝났다.

| 항목 | 관측 결과 |
| --- | --- |
| 실제 턴 | 조건 A, 1회 호출, CLI 종료 코드 0, 잔존 프로세스 0개, 외부 개입 0회 |
| 실행 기록 | `RUN → OBSERVE → DECIDE → FINALIZE → REPORT`. 수신 이벤트 11건, 정규화 증거 13건. 명시적 과제 완료 선언은 `null`로 유지했다. |
| 공개 검사 | 기능 `FAIL` (`tab-edges`), 회귀 `PASS` (`inner-spaces`) |
| 독립 Docker 평가 | 기능 `FAIL`, 회귀 및 지침 검사 `PASS`, 최종 판정 `FAIL`. 평가기 오류 코드 없음. |
| 동결 패치 | 변경 경로 0개. 공개 보고서·평가 요청·독립 판정의 패치 해시가 일치했다. 원본에서 패치를 재생한 스냅샷 해시도 동결 상태와 일치했다. |
| 사용량 | 입력 62,550 토큰(캐시된 입력 23,296 포함), 출력 408 토큰, 합계 62,958 토큰 |

아래 해시는 `results/s1-live/08559adb-e4ad-483d-b7d5-e6e9e0796eaa/` 기준이다.

| 파일 | SHA-256 |
| --- | --- |
| `operator.json` | `4c755f9263549906529122f65b65e028508193a853acf0f539ca6d82af641545` |
| `report.json` | `c96d3d85b8669e0d2daab296bc89268b9e157d93bf7078611c63cfa9d033a2f2` |
| `evaluation/08559adb-e4ad-483d-b7d5-e6e9e0796eaa.json` | `32b6ed09b2b87f06e28be089b45034bb1b472cc8e76f4b98d28dba5a560151db` |

기존 오프라인 테스트 150개 및 합성 A/B 결과에 조건 A의 실환경 증거를 추가해 S1을 완료로 기록했다. 1회 승인은 이 실행에 사용됐다. G1은 후속 A–E 통합 및 검증 단계에서 별도로 판정한다.
