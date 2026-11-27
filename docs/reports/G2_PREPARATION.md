# G2 본실험 사전 검증

2026-11-27 · IP26–29, IP31 · 검토 자료 생성 · 본실험 동결 전

Dev Pilot의 25개 슬롯을 저장된 일정·보고서·동결 패치·독립 판정과 대조했다. 검증한 실측은 18 PASS, 7 FAIL, 모델 토큰 2,298,144개, 실행과 평가 합계 976.602초다. 최초 턴 공개 검사 실패는 12건, 복구 전달은 8건이었다. Main 승인 후보는 현재 **0개**다. [Dev Pilot 결과](S5_PILOT_PREPARATION.md#pilot-실환경-결과)

## Main 과제와 품질 증거

[Main 후보 레지스트리](../../benchmarks/registry_templates/main-candidates.json)에 내부 예약 16개와 공개 데이터셋 예약 8개를 등록했다. 모두 `DRAFT`다. 외부 과제에는 고정한 실제 upstream instance와 검토한 이용 범위가 필요하다. [벤치마크 품질 게이트](../evaluation/BENCHMARK_SPEC.md#111-검증-체크리스트)

시간과 예산이 작을 때 검토할 Emergency 탐색안은 유형별 2개씩이다. 아래는 각 유형의 공개 요구를 먼저 검토할 순서다. 취합된 품질 증거와 리뷰어가 확인한 독립성에 따라 동결 전 목록을 확정한다.

| 유형 | 우선 검토할 내부 후보 |
| --- | --- |
| SINGLE | AAH-S02 주문 합계 반올림, AAH-S03 CSV 선택 필드 |
| DEPENDENCY | AAH-D02 주문 취소와 재고 반환, AAH-D04 예정 실행 복원 |
| REGRESSION | AAH-R03 날짜 형식과 호환성, AAH-R04 재시도 지연과 기존 동작 |
| COMPOSITE | AAH-C02 주문 부분 실패와 예약 상태, AAH-C03 배치 유효 레코드와 오류 보고 |

실제 과제 검토는 승인된 큐레이터 영역에서 한다. 공개 레지스트리의 승인 기록에는 실제 task ID·family ID, 공개 스펙·공개 검사·스냅샷·실행 환경·평가기 manifest의 해시, BQ01–14 결과, 독립 리뷰어 ID와 검토 증거 ID가 들어간다. 도구는 비작성자 검토, 누락된 품질 항목, 같은 family·snapshot·upstream instance의 중복을 점검한다. BQ05·06의 예외에는 별도 검토 근거가 필요하다.

평가기 연결에서 먼저 필요한 증거는 동일한 고정 환경의 기준본 기능 FAIL·회귀 PASS, 참조 패치 기능·회귀·제약 PASS, 공개 스냅샷과 분리된 판정 기록이다. 실제 Main의 정답 패치와 hidden tests는 큐레이터 경계에서 관리한다. 개발 환경에는 공개 입력과 판정 바인딩을 확인할 수 있는 승인 기록을 전달한다. [평가 판정 규칙](../evaluation/EVALUATION_PROTOCOL.md#44-결과-판정-규칙)

### Main 후보 8개의 공개 기준본 검증

우선 검토 후보 8개를 [공개 평가 과제 폴더](../../benchmarks/main_public/)에 작성했다. 각 과제에는 공개 요구사항과 수정 가능 경로, 독립 시작 스냅샷, 기능 검사 및 회귀 검사가 있다. NORMAL 3개, NOISY 3개, STALE 2개다. 문서 상태가 NOISY 또는 STALE인 과제에는 역할이 명시된 보관 문서를 함께 제공했다.

후보마다 깨끗한 공개 작업공간에서 검사를 **두 번** 실행했다. 16번 모두 기능 검사의 의도된 실패와 회귀 검사의 통과를 확인했다. 실행 전후 스냅샷 해시가 일치했고, 두 번의 결과도 일치했다. 기준 상태의 빈 패치와 과제별 핸드오프 데이터를 생성했다. 집계는 `results/g2-main-public/public-baselines-v2-20261127/summary.json`이며 SHA-256은 `a04060c4206d3e8a694a93c8163a91b5e4aafdf06b4a1e2229672a883dde029a`다.

각 핸드오프에는 task ID·family, 원본 revision과 snapshot, 수정 가능 경로, 공개 검사 해시와 빈 패치가 있다. 큐레이터는 독립 평가기 manifest 해시로 기준본 평가 요청을 생성할 수 있다. 요청과 결과를 연결하는 검증기는 같은 과제·원본·suite·환경에서 기준본 기능 FAIL·회귀 PASS와 참조본 전체 PASS를 확인한다. 운영자가 후보 8개 모두의 Docker 평가 통과를 보고했다. 8개 전체의 비작성자 리뷰가 남았다. [후보 검증 및 평가 요청 도구](../../benchmarks/main_public/qualify.py)

개발용 DEV-05와 공통 원인이 생기지 않도록 C03의 실패를 입력 중단에서 식별자 예약 충돌로 바꾼 뒤 다시 검증했다. 공개 기준본·평가 요청·G2 리뷰·평가 격리 테스트 16개가 통과했다.

공개 기준본 재검증은 저장소 루트에서 다음 명령으로 실행한다. 기존 결과는 새 경로를 지정해 보존한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.main_public.qualify --output results/g2-main-public/recheck
```

### AAH-S02 큐레이터 환경 검증

2026-11-27에 운영자가 작성자 사전 점검과 실제 Docker 평가 통과를 보고했다. Docker에서 기준본 기능 검사는 의도대로 FAIL, 회귀 검사는 PASS였다. 참조본의 기능·회귀·지시 검사는 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-S02 be8d1512-b929-4ebb-a7f1-f5bacb14e4c2`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### AAH-S03 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-S03 616f4fea-b562-4c43-b0c4-5da9acf441d6`이다. 비작성자 검토와 Main 승인 절차가 남았다.

검토 증거 `f172aa1a-df99-4423-94aa-181eb57b4ac8`에서 AAH-S03은 **보류**다. BQ04·BQ06은 공개 명세에 없는 조건을 검사한 결과 FAIL로 보고됐다. 큐레이터가 해당 검사를 조정한 뒤 새 검사 묶음으로 Docker 평가를 두 번 실행하고 BQ04·BQ06을 다시 확인한다. 리뷰는 작성자 세션에서 생성된 AI 사전 검토로 기록하며, BQ14는 비작성자 팀원의 판정을 기다린다.

운영자는 검사 수정 절차 뒤 Docker 재평가 두 건의 통과를 보고했다. 실행 ID는 `c022ca54-5ded-45b4-aeb5-984069a13483`, `505b9c5d-cb97-4982-9077-7bf6e45901cc`다. 두 실행 모두 기준본 기능 FAIL(예상)·회귀 PASS 및 참조본 기능·회귀·지시 PASS였다. 두 평가 기록의 검사 묶음과 환경 해시가 각각 일치한다는 운영자 출력을 BQ08 근거로 기록했다.

운영자가 로컬 변형 검사에서 기존에 탈락했던 대안 구현 4개 모두 공개·큐레이터 기능·회귀 통과를 보고했다. 불완전 수정 5개는 모두 큐레이터 검사에서 검출됐다. 갱신된 검토 증거 `f172aa1a-df99-4423-94aa-181eb57b4ac8`에서 BQ01–08과 BQ11–13이 PASS로 보고됐다. 최초 보류 사유였던 BQ04·BQ06은 해소됐으며, BQ09·BQ10·BQ14가 조건부로 남아 최종 판정은 **보류**다. 검토 기록은 작성자 세션의 AI 재검토로 분류한다.

BQ10의 공개 입력 사전 점검에서 Main 후보 8개 각각의 A–E 조건에 새 작업 사본을 만들어 총 40개를 대조했다. 과제별로 시작 스냅샷과 필수 지침이 같고, 같은 권한 프로필 ID와 공개 검사 목록을 사용했다. B/D와 C/E의 최초 메시지도 각각 일치했다. Main 실행기를 연결하면 실제 권한 설정과 동결할 manifest에서 같은 항목을 다시 확인한다. [Pilot 조건 동등성](S5_PILOT_PREPARATION.md#복구와-조건-동등성)

BQ09에는 아래 AAH-S03의 MXC 권한 검사 결과와 Dev Docker 합성 probe `08280fd42cfa4e8b82850d002f7c448d`의 9개 통과 기록이 있다. 현재 저장소의 운영 평가 경로는 Dev fixture와 승인된 Dev 후보를 대상으로 한다. Main에서는 제출 코드를 실행하는 프로세스와 비공개 검사·판정 기록을 보유하는 평가기의 분리를 연결하고, 최종 실행 설정에 대한 격리 증거를 남긴다. [Dev Docker 합성 검사](S0_VERIFICATION.md#docker-합성-검사-결과) · [평가 격리 기준](../evaluation/EVALUATION_PROTOCOL.md)

### AAH-S03 Main 합성 사전 검증

공개 AAH-S03으로 A–E 다섯 슬롯을 생성했다. 각 슬롯에서 새 작업 사본을 만들고, 공개 코드에 합성 주석을 추가한 패치를 동결했다. 별도 평가 프로세스가 패치를 재생해 사전에 확인한 합성 소스만 실행했다. 실행 작업자에는 소스와 합성 입력만 전달했고, 기대 결과의 비교와 판정 저장은 평가 프로세스에서 수행했다. 모델 호출은 0회였다. [합성 실행기](../../benchmarks/main_public/preflight.py) · [독립 평가 사전 검증](../../evaluation/main_preflight.py)

로컬 합성 실행과 운영자 PowerShell의 고정 이미지 Docker 실행에서 A–E 다섯 판정이 각각 완료됐다. Docker 보고서 `results/g2-preparation/main-s03-docker-20261127-025315/preflight.json`은 합성 판정 5/5와 모델 호출 0회를 기록한다. 다섯 평가 요청과 판정의 실행 ID, 과제 ID, 패치·평가 manifest 해시를 대조했다. 시작 파일과 필수 지침을 확인했고 B/D 및 C/E 최초 메시지 쌍이 각각 일치했다. 실제 MXC 권한 검사와 BQ09·BQ10 판정은 [아래 절](#aah-s03-main-mxc-권한-검사-bq09bq10)에 기록했다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$g2Output = "results/g2-preparation/main-s03-docker-$(Get-Date -Format yyyyMMdd-HHmmss)"
python -m benchmarks.main_public.preflight --docker --output $g2Output
```

### AAH-S03 Main MXC 권한 검사 (BQ09·BQ10)

2026-11-27에 AAH-S03의 A–E 조건마다 새 작업 폴더와 공개 작업 사본을 만들었다. Dev/Pilot 운영 경로와 같은 MXC 실행 방식과 권한 프로필 `aah_dev01_mxc`로 확인용 프로그램을 실행했다. 이 프로필은 작업 사본만 쓰기를 허용하고 Python 설치 폴더는 읽기만 허용한다. 저장소 checkout과 Codex 로그인 폴더, 나머지 경로는 거부하며 네트워크는 끈다. 에이전트 턴과 모델 호출은 0회다. 실행한 Codex 명령은 `codex --version`과 `codex sandbox`뿐이다. 비공개 자료는 사용하지 않았다. 대신 작업 폴더 밖에 비밀값이 없는 합성 확인용 파일을 두었다. 숨은 검사, 참조 패치, 평가 결과, 작업 폴더 형제 파일, 제어부 기록 파일과 원본 공개 소스가 여기에 해당한다. [권한 검사 도구](../../benchmarks/main_public/access_check.py)

결과는 `results/g2-preparation/main-s03-access-20261127-030416/access.json`이며 SHA-256은 `0c7a236bbe9a5f31555927be4df5c3a1f2d04bc2f2cee4cc11c63a343a704c41`이다. 출력은 `MAIN_ACCESS_PASS … BQ09=PASS BQ10=PASS agent_turns=0`이다. 파일 해시, 다섯 조건의 개별 관측과 정리 상태, 동등성 비교 아홉 항목을 대조해 `EL-20261127-020`에 기록했다.

| 항목 | A–E 관측 결과 |
| --- | --- |
| 공개 파일 읽기 | `csv_rows.py`, `README.md`, `docs/archived-notes.md`, 공개 검사 2개 모두 읽음. 해시가 작업 사본과 일치 |
| 합성 비공개 파일 7개 | 읽기와 수정용 열기 모두 `DENIED`. `../` 상위 경로 접근도 포함 |
| 비공개 폴더와 작업 루트에 새 파일 생성 | 4곳 모두 `DENIED` |
| 비공개 파일 symlink | `NOT_CREATABLE` |
| 환경 변수 canary와 `.git` 메타데이터 | 둘 다 보이지 않음 |
| 작업 사본 쓰기와 호스트 loopback | 쓰기 가능, loopback `DENIED` |
| 호스트 사후 확인 | canary 바이트, 비공개 폴더 목록, 작업 루트와 스냅샷이 실행 전과 같음. 잔여 프로세스 0개. 작업 사본과 임시 폴더 정리 확인 |

**BQ09 권한 검사 PASS.** 다섯 조건 모두 접근 실패 항목이 없었다. ISO-01·02·03·05·06의 적용 항목을 확인했다. ISO-04는 네트워크 비활성 설정과 loopback 거부까지만 확인했고 외부 접속은 시도하지 않았다. ISO-07(개발·평가 자료 분리)과 ISO-08(평가기 복원)은 이 검사 범위 밖이다. ISO-08의 패치 재생 근거는 위 합성 사전 검증에 있다.

**BQ10 PASS.** 비교한 항목은 시작 스냅샷 ID·revision, 고정 요구사항과 필수 지침 해시, 공개 검사 ID, 격리 프로필 ID, 공개 파일 해시, 실제 접근 결과다. 권한 설정은 경로를 `<workspace>` 같은 표시로 바꾼 뒤 턴 설정과 sandbox 명령을 해시해 비교했다. 아홉 항목 모두 A–E에서 일치했다. 조건별 정책 차이는 설계상 외부 보조 정보에만 해당한다. 최초 메시지 동등성은 위 합성 사전 검증의 결과를 따른다.

한계: 이번 검사는 Pilot·S4가 실제로 사용한 `live_dev01` 권한 설정과 sandbox 명령을 그대로 재사용했다. Main 실행기는 슬롯마다 이 기록의 `permission_settings_hash` 값 `de14ca4e…`를 대조한다. [실행기 연결](#aah-s03-main-실행기-권한-프로필-연결) 프로필이나 경로를 바꾸면 해시가 달라져 실행기가 멈춘다. 이때는 이 검사를 다시 실행하고 새 기록을 승인받아야 한다. AAH-S03은 BQ14 비작성자 리뷰가 남아 최종 판정이 계속 **보류**다.

```powershell
$env:PYTHONPATH = "$((Resolve-Path src).Path);$((Get-Location).Path)"
$accessOutput = "results/g2-preparation/main-s03-access-$(Get-Date -Format yyyyMMdd-HHmmss)"
python -m benchmarks.main_public.access_check --output $accessOutput
```

`MAIN_ACCESS_BLOCKED <조건> <단계>`가 나오면 결과 JSON의 `blocked_stage`와 해당 조건의 `probe.signals`로 원인을 확인한다. 단계는 `CODEX_DISCOVERY`, `CODEX_VERSION`, `WORKSPACE_PREPARE`, `MXC_PROBE:<마지막 통과 단계>`, `HOST_VERIFY` 중 하나다. `MXC_PROBE:NOT_STARTED`이고 `WINDOWS_ACCESS_DENIED`가 함께 나오면 MXC 준비 단계에서 막힌 것이다. 이 경우 일반 PowerShell에서 아래 진단 명령을 실행하고, 콘솔 상속이 필요하다고 나오면 권한 검사 명령에 `--inherit-console`을 붙인다. `MAIN_ACCESS_FAIL`은 실행은 끝났지만 접근이 허용됐거나 A–E 값이 달랐다는 뜻이다. 이때는 권한을 완화하지 않고 해당 과제의 편입을 보류한다.

```powershell
python -m benchmarks.smoke.live_dev01 --diagnose-mxc
```

### AAH-S03 Main 실행기 권한 프로필 연결

AAH-S03 Main 실행기의 슬롯 준비 경로에 위 권한 검사 결과를 연결했다. 슬롯 구조는 Pilot·S4와 같다. 새 작업 사본, 같은 Codex 실행 파일, 걸러낸 환경 변수, 에이전트 턴 전달 함수를 준비한 뒤 실험 콜백에 넘긴다. 실행기는 이 슬롯에서 실제로 쓸 턴 설정과 sandbox 명령을 만든다. 이를 권한 검사와 같은 함수로 정규화하고 해시를 구한다. 이 해시를 검사 기록에서 해당 조건의 `permission_settings_hash`와 대조한다. 에이전트 턴 전달 함수는 대조를 통과한 바로 그 턴 설정을 사용한다. [Main 실행기](../../benchmarks/main_public/main_runner.py)

실행기는 다음 경우에 Codex 프로세스를 하나도 띄우지 않고 슬롯을 멈춘다. 작업 사본은 정리한다.

- 검사 기록이 없음
- 파일 SHA-256이 EL-20261127-020의 `0c7a236b…`와 다름
- 과제·공개 기준본·MXC 프로필이 다름
- A–E 중 하나라도 빠짐
- BQ09·BQ10이 PASS가 아님
- 슬롯의 실제 설정 해시가 기록과 다름
- 슬롯의 시작 스냅샷 ID가 기록에 없거나 기록과 다름 (이후 추가, [실행 일정](#aah-s03-main-실행-일정과-독립-평가-연결) 참고)

이 실행기에는 실제 실행 명령이 없다. `--verify`는 A–E 슬롯을 준비하고 대조만 한다. 에이전트 턴은 시작하지 않는다.

2026-11-27 검증 결과는 `results/g2-preparation/main-s03-runner-gate-20261127-031402/gate.json`이며 SHA-256은 `38f3fd359ae3c3de2cb417d179018ff6620990c7f478e901b12bc20424abf091`이다. A–E 다섯 슬롯 모두 기록값과 실제값이 `de14ca4ee0a1d7a5e995808df347a370ccb1bc011a61745d506a5c258f49356d`로 일치했다. 턴 전달 횟수는 0이었다. 실행한 Codex 명령은 슬롯마다 `codex --version` 하나다. 다섯 슬롯의 시작 스냅샷 ID `cef3a298…`도 권한 검사 기록과 같았고 작업 사본 정리를 확인했다. 두 증거 파일을 대조한 결과를 `EL-20261127-021`에 기록했다.

테스트 11개로 동작을 확인했다. 10개는 합성 기록과 가짜 Codex 실행기를 쓰고, 1개는 로컬에 검사 기록이 있을 때 그 해시를 대조한다. 시작 스냅샷 대조를 추가하면서 불일치·누락 테스트 2개를 더했다.

| 경우 | 기대 결과 |
| --- | --- |
| 기록 해시와 실제 설정이 일치 | A–E 준비 완료, `--version` 외 Codex 프로세스 없음 |
| 턴 전달 함수 호출(가짜 실행기) | 대조한 턴 설정 그대로 전달 |
| 기록의 C 해시가 다름 | C에서 `PERMISSION_SETTINGS_MISMATCH`로 중단, C의 Codex 프로세스 0개 |
| 실제 설정에 네트워크 허용 항목 추가 | A에서 중단, Codex 프로세스 0개 |
| 기록 없음, 파일 해시 변경, BQ09 FAIL, 조건 누락, 공개 기준본 불일치 | 각 코드로 중단, Codex 프로세스 0개 |
| 다른 경로의 호스트에서 설정 정규화 | 기록 해시 `de14ca4e…`와 동일 |

```powershell
$env:PYTHONPATH = "$((Resolve-Path src).Path);$((Get-Location).Path)"
python -m benchmarks.main_public.main_runner --verify --output "results/g2-preparation/main-s03-runner-gate-$(Get-Date -Format yyyyMMdd-HHmmss)"
python -m pytest -q tests/integration/test_main_runner.py tests/integration/test_main_access_check.py
```

`MAIN_RUNNER_GATE_BLOCKED <코드>`가 나오면 `gate.json`의 `code`와 조건별 `permission_gate`를 확인한다. `PERMISSION_SETTINGS_MISMATCH`이면 권한 설정을 기록에 맞춰 완화하거나 기록을 고치지 않는다. 변경 사유를 검토하고 권한 검사를 새로 실행해 승인받는다.

### AAH-S03 Main 실행 일정과 독립 평가 연결

AAH-S03 한 과제의 Main 일정을 A–E 각 1회, 총 5개 슬롯으로 구성했다. 기존 실험 실행기 `runner.plan`/`runner.execute`로 일정을 고정했다. 순서는 seed 20261127로 섞었다. 고정 입력에는 과제 기준본, 필수 지침 해시, 합성 평가기 manifest 해시가 들어간다. 조건별 권한 설정 해시와 시작 스냅샷 ID도 함께 고정한다. [Main 일정](../../benchmarks/main_public/main_schedule.py)

슬롯마다 위 Main 실행기의 `run_slot`을 거친다. 새 작업 사본을 만든 뒤 시작 스냅샷 ID와 권한 설정 해시를 EL-20261127-020 기록과 대조한다. 대조를 통과해야 슬롯 예약(`attempt.json`)과 턴 단계로 들어간다. 기록이 없거나 값이 다르면 해당 슬롯은 예약도 턴도 하지 않고 멈춘다. Codex 프로세스는 띄우지 않고 작업 사본은 정리한다. 이후 슬롯도 시작하지 않는다.

검증 모드의 턴 단계는 에이전트 대신 기존 합성 제출(`preflight.Submission`)이다. 이 제출은 공개 소스에 합성 주석만 추가한다. 워크플로가 패치를 동결한다. 기존 `preflight`의 평가 호출을 함수로 분리해 재사용했고, 동결 패치를 별도 프로세스의 합성 평가기에 전달한다. 일정 실행기는 판정을 슬롯 기록에 저장하기 전에 평가 요청과 판정을 대조한다. 대조 항목은 실행 ID, 과제 ID, 패치 해시, 평가 manifest 해시다.

2026-11-27 Docker 평가 검증 결과는 `results/g2-preparation/main-s03-schedule-docker-20261127-032456/schedule-verification.json`이며 SHA-256은 `090b2685d01209707908bf4f6c997449a3d511b3e3d02fcc0ab38725eff60e0d`다. 실험 manifest 해시는 `3e7670ba9ba1b8bc26dd8680c56fdb1a788cb6ef43b41aab57c22fe2d5eda7c5`, 집계 `summary.json`의 SHA-256은 `df6085e05f4656d21a28c056dc155fb0d1ed670f550306397da78e47ff744d29`다. 출력은 `MAIN_SCHEDULE_PASS … slots=5/5 evaluated=5/5 agent_turns=0 model_calls=0`이다.

| 슬롯 | 조건 | 실행 ID | 스냅샷·권한 대조 | 종료 | 독립 판정 | 연결 대조 |
| --- | --- | --- | --- | --- | --- | --- |
| slot-0001 | D | `4c2e0cc2-93e8-4b4a-9911-a0027a2f0b97` | MATCH·MATCH | COMPLETED | PASS | 6/6 |
| slot-0002 | E | `fc5172ae-8689-414f-ab30-820afaab596c` | MATCH·MATCH | COMPLETED | PASS | 6/6 |
| slot-0003 | B | `6a1e3148-33a1-41d9-bc1c-6ea4b72b18ba` | MATCH·MATCH | COMPLETED | PASS | 6/6 |
| slot-0004 | C | `fb3ca00e-dc3e-408c-9c2d-a4fa2c00c052` | MATCH·MATCH | COMPLETED | PASS | 6/6 |
| slot-0005 | A | `d37db2cc-f04c-41d9-a200-fd86469951a3` | MATCH·MATCH | COMPLETED | PASS | 6/6 |

연결 대조 6개 항목은 다음과 같다.

- 실행 ID: 보고서, 평가 요청, 패치, 판정이 같음
- 과제 ID: 실행 manifest, 요청, 판정이 같음
- 패치 해시: 동결 패치, 요청, 판정, 집계가 같음
- 평가 manifest 해시: 요청, 판정, 고정 평가기가 같음
- 슬롯에 저장된 판정이 평가기 출력과 같음
- 평가 요청과 판정이 실행 결과 폴더 밖에만 있음

다섯 슬롯의 시작 스냅샷은 모두 `cef3a298…`, 권한 설정 해시는 모두 `de14ca4e…`였다. 같은 기준본에 같은 합성 수정을 적용했으므로 패치 해시는 다섯 슬롯 모두 `55aa52cc…`로 같다. 슬롯은 실행 ID로 구분한다. 최초 메시지는 A가 과제와 필수 지침만 담았고 B/D와 C/E가 각각 같았다. 실행한 Codex 명령은 슬롯마다 `codex --version` 하나였고 턴 전달은 0회였다. 공개 검사는 연결되지 않아 `NOT_RUN`으로 기록됐다. 이 때문에 보고서의 공개 검사 집계는 `UNKNOWN`이다.

합성 테스트 5개를 추가했다. 첫째, 5개 슬롯의 대조·판정·연결이 모두 통과하는지 확인한다. 둘째, 세 번째 슬롯의 권한 해시 불일치, 시작 스냅샷 불일치, 스냅샷 기록 누락을 각각 넣는다. 세 경우 모두 해당 슬롯이 예약 전에 멈추고, 평가 요청이 없고, 이후 슬롯이 시작되지 않는지 확인한다. 셋째, 기록이 없으면 일정을 만들기 전에 멈추는지 확인한다.

**아직 연결되지 않은 운영 경로.** 아래 항목은 검증 보고서의 `operational_gaps`에도 기록했다. 모두 완료가 아니다.

| 항목 | 상태 | 내용 |
| --- | --- | --- |
| 실제 Codex 턴 런타임 | 미연결 | Main 슬롯의 `dispatch`를 쓰는 live runtime(Pilot의 `PilotRuntime`에 해당)이 없다. 검증에서는 합성 제출이 패치를 작성했다. |
| 공개 검사 실행기 | 미연결 | Main 과제용 신뢰 공개 검사 실행기가 없다. 공개 검사는 `NOT_RUN`이고, 최초 실패 감지와 T4 판정의 입력이 없다. |
| B–E 복구 턴 | 미실행 | 슬롯당 최초 턴 1회 한도로 실행했다. 실패 감지, 복구 전달, 세션 재개는 검증하지 않았다. |
| 큐레이터 독립 평가기 | 미연결 | 합성 평가기 manifest만 사용했다. 이 평가기는 검토된 합성 소스만 실행한다. 비공개 검사를 가진 큐레이터 평가기와 그 manifest 해시는 연결되지 않았다. |
| 사용량·비용 | 미측정 | 모델 토큰, 크레딧, 턴 지연이 관측되지 않았다. |
| 승인·동결 | 없음 | Main 실행 승인, 예산, 모델 설정, 동결된 G2 manifest가 없다. 실제 실행 명령도 없다. |

```powershell
$env:PYTHONPATH = "$((Resolve-Path src).Path);$((Get-Location).Path)"
python -m benchmarks.main_public.main_schedule --verify --docker --output "results/g2-preparation/main-s03-schedule-docker-$(Get-Date -Format yyyyMMdd-HHmmss)"
python -m pytest -q tests/integration/test_main_schedule.py tests/integration/test_main_runner.py tests/integration/test_main_preflight.py
```

`MAIN_SCHEDULE_BLOCKED <코드> <슬롯>`이 나오면 `schedule-verification.json`의 `blocked_slot`과 `slot_receipts`를 확인한다. `PERMISSION_SETTINGS_MISMATCH`, `INITIAL_SNAPSHOT_MISMATCH`, `INITIAL_SNAPSHOT_MISSING`이면 기록이나 권한을 맞춰 고치지 않는다. 변경 사유를 검토하고 권한 검사를 새로 실행해 승인받는다.

### AAH-S03 Main 실행 경로·운영 평가기·동결 manifest

2026-11-27에 EL-20261127-022의 합성 검증 증거 3개 파일의 해시를 다시 대조했다. 대상은 `schedule-verification.json`, `summary.json`, `experiment.json`이며 기록과 일치해 재실행하지 않았다. 그 위에 실제 Main 실행 경로를 연결했다. **실제 Main은 실행하지 않았다.** 동결 잠금 조건이 충족되지 않았고, 운영자가 준비 상태로 마무리하기로 결정했기 때문이다.

| 구성 | 구현 | 확인 |
| --- | --- | --- |
| 실행 명령 | `main_schedule --execute --curator-kit <키트>`(AMD-01 이후 `--curator-kits <키트 루트>`). 동결 manifest 잠금 확인, 큐레이터 manifest 대조, 일정·공개 검사 환경 대조를 먼저 한다. 그다음 슬롯마다 권한·스냅샷 게이트를 거쳐 `MainRuntime`으로 Codex CLI 턴을 실행하고, 기존 runner로 기록·재개한다. [Main 일정](../../benchmarks/main_public/main_schedule.py) | 합성 테스트에서 가짜 Codex 턴 5회로 5슬롯을 실행했다. 공개 검사 PASS, 큐레이터 판정 PASS, 실행 ID·패치 해시·manifest 해시 연결을 확인했다. 같은 일정을 재개해도 새 프로세스는 0개였다. 실환경에서는 잠금 미충족으로 Codex 프로세스 없이 중단됐다. |
| 공개 검사 실행기 | 고정 공개 기준본의 검사 파일을 Docker worker에서 실행한다. 제출 측 `public_checks/` 변경은 반영되지 않는다. [공개 검사](../../benchmarks/main_public/public_runner.py) · [검사 묶음 worker](../../evaluation/source_suites.py) | 실제 고정 Docker 이미지에서 확인했다. 기준본은 기능 FAIL·회귀 PASS, 합성 수정본은 둘 다 PASS, 문법 오류 제출은 import 실패로 기록된 FAIL이었다. 환경 해시는 `f98af066…`다. |
| 운영 평가기 | 큐레이터 키트(저장소 밖)의 비공개 검사로 동결 패치를 별도 프로세스에서 판정한다. 허용 경로 밖 변경이나 공개 시그니처 변경은 지시 위반 FAIL로 처리한다. [큐레이터 평가기](../../evaluation/main_curator.py) | 합성 키트로만 검증했다. 실제 비공개 검사와 manifest 해시는 큐레이터 환경에서 산출해야 하며 아직 없다. |
| 동결 manifest | 과제·모델·권한·예산·일정·평가기·분석 설정·코드 해시(116개 파일)를 시각 정보 없이 기록한다. 승인은 manifest SHA-256을 참조하는 별도 파일로 받는다. [동결 도구](../../benchmarks/main_public/main_freeze.py) | `configs/main/aah-s03-main-freeze.json` SHA-256 `001a80024b12f4f1099ef43ee33c92a7034897d9d85981749463094b85cbd7b7`, `--check` 재생성 결과 바이트 일치. AMD-01 승인으로 대체됨 |

동결 초안의 주요 값은 다음과 같다.

- **모델:** `gpt-6-luna`, low, Codex CLI 0.166.0
- **권한:** EL-020 기록(`0c7a236b…`)과 권한 해시 `de14ca4e…`, 시작 스냅샷 `cef3a298…`
- **예산:** Pilot 한도 그대로. 최초 5턴 + 복구 최대 8턴 = 13턴, 슬롯당 360초, 추가 결제 0원, `observe_only`
- **일정:** seed 20261127로 D, E, B, C, A
- **분석 설정:** SHA-256 `dc44ac58…`

1과제 Main은 Emergency 계획(8과제)보다 작으므로 결과는 탐색적이다.

**잠금 상태 `MAIN_FREEZE_NOT_LOCKED`:** 아래 세 가지가 비어 있다.

1. `CURATOR_EVALUATOR_MANIFEST_MISSING`: 큐레이터 환경에서 `python -m evaluation.main_curator --kit <키트> --manifest`로 해시를 얻는다. 그 해시로 `--write --curator-manifest-sha256 <해시>`를 실행한다.
2. `BQ_APPROVAL_MISSING`: BQ14 비작성자 리뷰를 받고 레지스트리를 `APPROVED`로 갱신한다.
3. `EXECUTION_APPROVAL_MISSING`: `configs/main/aah-s03-main-approval.json`이 필요하다. 이 파일에는 manifest SHA-256, 예산 재기재, BQ14 리뷰어·증거 ID, RH/GR/CE 공동 승인이 들어간다.

```powershell
$env:PYTHONPATH = "$((Resolve-Path src).Path);$((Get-Location).Path)"
python -m benchmarks.main_public.main_freeze --check
python -m benchmarks.main_public.main_freeze --status
python -m benchmarks.main_public.main_schedule --execute --curator-kits <큐레이터 키트 루트>
python -m pytest -q tests/integration/test_main_live.py tests/integration/test_main_analysis.py
```

`.py` 파일을 바꾸면 코드 해시가 달라진다. 그러면 `--check`가 `MAIN_FREEZE_DIFFERS`를 내고 승인도 무효가 된다. 변경 뒤에는 초안을 다시 생성하고 승인을 다시 받는다.

### AMD-01 이행 1단계: 다과제 준비와 개발 측 객관적 검사

2026-11-27에 운영자가 AMD-01([DL-031](../project/DECISION_LOG.md), 수정안 SHA-256 `8e9020f8…`)을 RH·GR·CE 겸임으로 승인했다. 선택 항목은 제안값 그대로다. 이에 따라 위의 AAH-S03 단일 과제 동결 초안(`aah-s03-main-v1`)은 **대체(SUPERSEDED)** 됐다. 초안 파일은 이력으로 남기지만, 동결 도구가 다과제용으로 바뀌어 더 이상 `--check` 대상이 아니다. 새 실험 ID는 `aah-main-emergency-v1`이다.

**코드 일반화 (모델 호출 0회)**

- **접근 검사·실행기:** `access_check --task`, `main_runner`의 과제별 접근 기록
- **동결:** 편입 결정(`--include`), 편입 과제만 담는 다과제 동결 manifest, 최소 5과제·수정안 해시·승인 파일 게이트를 추가했다. [동결 도구](../../benchmarks/main_public/main_freeze.py)
- **실행:** `--execute --curator-kits <키트 루트>`가 과제별 큐레이터 키트·접근 기록·공개 검사 실행기를 사용한다. [Main 일정](../../benchmarks/main_public/main_schedule.py)
- **분석:** AMD-01 분석 설정을 추가했다(대체 실행 0, 검정 없음, 비열등성 주장 없음, SHA-256 `7acf6117…`). 과제군·저장소는 tasks.json에서 읽는다.
- **변형본:** 공개 명세만 보고 작성한 대안 구현 16개와 불완전 수정본 24개, manifest SHA-256 `56065d5d…`. [변형본](../../benchmarks/main_public/amd01_variants.py)
- **큐레이터 측 일괄 도구와 리뷰 지침:** [amd01_curator](../../evaluation/amd01_curator.py), [AI 리뷰 지침](../project/AMD01_AI_REVIEW_INSTRUCTIONS.md)(SHA-256 `b782599c…`)
- **기타:** AAH-R03 공개 기준본에 생긴 `__pycache__`를 삭제했다. 이 경로는 `read_tree`가 무시하므로 기준본 revision `63de1c9b…`은 바뀌지 않는다.

**실환경 결과**

| 검사 | 결과 | 근거 |
| --- | --- | --- |
| MXC 접근 검사 BQ09·BQ10 (8과제 × A–E) | 8/8 PASS, 에이전트 턴 0회 | `results/g2-preparation/amd01/access-<과제>-20261127-041925/access.json` |
| 기준본 공개 검사 2회 반복 (BQ07) | 8/8 PASS. 모두 기능 FAIL·회귀 PASS로 일치 | `results/g2-preparation/amd01/objective-dev.json` (SHA-256 `2a4ff8d4…`) |
| 대안 구현 16개의 공개 검사 | 16/16 PASS | 같은 파일 |
| 불완전 수정본 24개의 공개 검사 | 21개는 공개 검사 통과(비공개 검사 판정 대상). C02 1개, D04 2개는 공개 검사에서 이미 실패 | 같은 파일 |
| family 중복 (BQ12) | 8개 family가 서로 다르고 Dev family와 겹치지 않음 | 같은 파일 |

**남은 단계 (운영자 실행)**

1. 큐레이터 키트 루트에서 일괄 판정을 실행한다. 내용은 기준본·참조본 2회(BQ02·03·08), 변형본 40개(BQ04·05·06), 과제별 큐레이터 manifest 해시다.
2. 실험 계정과 분리된 다른 제공자의 새 AI 세션을 키트 루트에서 열고, `amd01-review-instructions.md`에 따라 8개 과제를 리뷰한다.
3. 리뷰 요약을 추출한다.
4. 개발 측이 편입 결정 → 동결 → 승인 파일 → `--status` → 실행 순서로 진행한다.

```powershell
cd C:\Users\dldls\OneDrive\Code\AAH-Main-curator-kits
$repo = 'C:\Users\dldls\OneDrive\Code\codexbench'
$env:PYTHONPATH = "$repo\src;$repo"
python -m evaluation.amd01_curator evaluate --kits . --dev-report "$repo\results\g2-preparation\amd01\objective-dev.json" --output "$repo\results\g2-preparation\amd01\curator-report.json"
# 리뷰 세션 종료 후
python -m evaluation.amd01_curator reviews --kits . --output "$repo\results\g2-preparation\amd01\review-summary.json"
```

### AMD-01 이행 2단계: 1차 편입 결과와 1회 검사 수정 경로

**1차 결과 (2026-11-27, 운영자 실행, 모델 호출 0회)**

| 과제 | 큐레이터 객관 검사 (BQ02·03·08·05) | 대안 구현 비공개 검사 | 불완전 수정 검출 | AI 리뷰 | 편입 |
| --- | --- | --- | --- | --- | --- |
| AAH-C02 | 모두 PASS | 2/2 통과 | 1/3 | APPROVE | 편입 |
| AAH-C03 | 모두 PASS | 2/2 통과 | 2/3 | HOLD (BQ04 FAIL) | 제외 |
| AAH-D02 | 모두 PASS | 0/2 통과, 명세 정당화 없음 | 3/3 | HOLD (BQ04·06 FAIL) | 제외 |
| AAH-D04 | 모두 PASS | 2/2 통과 | 2/3 | HOLD (BQ04 FAIL) | 제외 |
| AAH-R03 | 모두 PASS | 2/2 통과 | 2/3 | APPROVE | 편입 |
| AAH-R04 | 모두 PASS | 1/2 통과, 명세 정당화 없음 | 2/3 | HOLD (BQ04·06 FAIL) | 제외 |
| AAH-S02 | 미판정 (키트가 키트 루트에 없음) | 미판정 | 미판정 | 없음 | 제외 |
| AAH-S03 | 모두 PASS | 2/2 통과 | 3/3 | APPROVE | 편입 |

- **편입 판정:** `BELOW_MINIMUM` 3/8. 최소 5과제(AMD-01 §6)에 못 미치므로 이 상태로는 Main을 실행하지 않는다.
- **증거 파일:**
  - 큐레이터 보고서 `results/g2-preparation/amd01/curator-report.json` (SHA-256 `14cf55fa…`)
  - 리뷰 요약 `review-summary.json` (`1f8a5e8d…`)
  - 1차 편입 기록 사본 `inclusion-round1.json` (`86f1aed6…`)
- **리뷰 근거의 위치:** 리뷰 근거 문장은 키트 안에만 있다. 개발 측은 판정 코드와 해시만 받았다.
- **재생성 확인:** 2단계 코드로 다시 판정해도 같은 3/8이다. 편입 기록에 `round` 필드가 추가돼 `configs/main/amd01-inclusion.json` 해시는 `4dac8ead…`로 바뀌었다.
- **HOLD의 의미:** C03·D04는 대안 구현이 모두 통과했는데도, 리뷰가 비공개 검사에서 공개 명세로 도출되지 않는 단언을 찾았다. D02·R04는 대안 구현이 비공개 검사에 실패했고, 리뷰가 그 실패를 명세로 정당화하지 못했다. 즉 검사가 명세 밖 요구를 한다는 판정이다.

**1회 검사 수정 경로 (AMD-01 §5)**

수정안은 BQ04·05·06에서 실패한 과제에 대해 동결 전 비공개 검사 수정 1회를 허용한다. 이를 기계적으로 강제하도록 다음을 추가했다. 테스트는 259개 모두 통과했다.

- **[수정 지침](../project/AMD01_TEST_FIX_INSTRUCTIONS.md):** SHA-256 `47fa5c43…`.
  - 실험 계정이 아닌 제공자의 새 세션이 수행하며, 1차 리뷰 세션·재리뷰 세션과 다른 세션이어야 한다.
  - `curator_tests/` 두 파일만 고칠 수 있다.
  - 변형본 원본은 열지 않는다.
  - 단언을 추가하거나 축소할 때는 명세 원문을 인용한다.
  - 수정 기록은 `amd01-fixes/<과제>.json`에 남긴다.
- **`amd01_curator hashes`:** 수정 전 키트 해시 스냅샷을 만든다(명세, 기준본, 참조본, 비공개 검사).
- **`evaluate --before`:** 스냅샷과 수정 기록을 보고서에 묶는다. 수정 기록은 코드·개수·해시만 내보낸다.
- **`main_freeze --include --round1-curator-report --round1-review-summary`가 확인하는 것:**
  - 바뀐 부분이 비공개 검사뿐인지(`NON_SUITE_KIT_CHANGE`)
  - 1차에서 BQ04·05·06 실패였는지(`FIX_NOT_PERMITTED`)
  - 수정 기록이 유효한지(`FIX_RECORD_MISSING`/`INVALID`)
  - 재리뷰가 새로 이뤄졌는지(`REVIEW_NOT_REDONE`)
  - 수정 안 한 과제의 리뷰가 바뀌지 않았는지(`REVIEW_CHANGED_WITHOUT_FIX`)
  - 큐레이터 환경이 그대로인지
  - 수정 라운드가 1회뿐인지
- **동결 기록:** 동결 manifest의 과제 항목에 `suite_fixed`와 `fix_evidence_id`를 기록한다.
- **한계:** 수정·재리뷰 세션이 서로 다른 세션인지는 도구가 검증할 수 없다. 운영자 확인과 기록에 의존한다.

**운영자 실행 순서**

```powershell
cd C:\Users\dldls\OneDrive\Code\AAH-Main-curator-kits
$repo = 'C:\Users\dldls\OneDrive\Code\codexbench'
$env:PYTHONPATH = "$repo\src;$repo"
$out = "$repo\results\g2-preparation\amd01"
# 1. S02 키트를 키트 루트로 복사 (1차에서 빠졌던 과제)
Copy-Item -Recurse ..\AAH-S02-curator-kit .\AAH-S02
# 2. 수정 전 해시 스냅샷 (수정 세션보다 먼저)
python -m evaluation.amd01_curator hashes --kits . --output "$out\kit-hashes-before-fix.json"
# 3. 수정 세션 (새 세션, 키트 루트): AAH-C03, AAH-D02, AAH-D04, AAH-R04
# 4. 2차 큐레이터 판정
python -m evaluation.amd01_curator evaluate --kits . --dev-report "$out\objective-dev.json" --before "$out\kit-hashes-before-fix.json" --output "$out\curator-report-r2.json"
# 5. 재리뷰 세션 (또 다른 새 세션, 키트 루트): 수정한 과제와 AAH-S02만
# 6. 2차 리뷰 요약
python -m evaluation.amd01_curator reviews --kits . --output "$out\review-summary-r2.json"
```

편입 판정은 개발 측에서 다음으로 낸다.

```powershell
python -m benchmarks.main_public.main_freeze --include --dev-report results\g2-preparation\amd01\objective-dev.json --curator-report results\g2-preparation\amd01\curator-report-r2.json --review-summary results\g2-preparation\amd01\review-summary-r2.json --prior-curator-report results\g2-preparation\amd01\curator-report.json --prior-review-summary results\g2-preparation\amd01\review-summary.json
```

- **S02의 처리:** S02는 2차에서 처음 판정된다. 이번 경로로는 S02에 수정 기회가 없다.
- **수정 후에도 HOLD인 과제:** 수정안 규칙대로 최종 `HOLD`다.
- **최종 편입이 4개 이하일 때:** Main을 실행하지 않고 과제별 BQ 결과만 보고한다.

### AMD-01 이행 3단계: 2차 재평가 결과와 수정 규칙 일반화

**2차 결과 (2026-11-27, 운영자 실행)**

- **큐레이터 판정 `curator-report-r2.json` (`53a2c984…`):** 8과제 모두 판정됐다.
  - **비공개 검사 변경 없음:** 8개 모두 수정 전 스냅샷과 같고 수정 기록(`amd01-fixes/`)도 없다. 수정 단계가 수행되지 않았다는 뜻이다.
  - **기존 7과제:** 큐레이터 manifest와 판정이 1차와 같다(BQ08 재현).
  - **S02 (처음 판정):** 객관 검사 PASS, 불완전 수정 3/3 검출. 대안 구현 `alt-sum-then-round`가 실패했지만 리뷰가 명세로 정당화했다.
- **리뷰 요약 `review-summary-r2.json` (`5d1a53f1…`):**
  - C03·D02·D04·R04는 수정 없이 재리뷰돼 1차와 같은 HOLD가 나왔다.
  - S02는 첫 리뷰에서 HOLD(BQ04 FAIL)다.
  - C02·R03·S03은 1차 리뷰 그대로다.
- **편입:** `BELOW_MINIMUM` 3/8. 기록은 `inclusion-round2.json` (`733646a4…`)이다. 수정 없는 재리뷰는 `REVIEW_CHANGED_WITHOUT_FIX`로 판정됐다.

**규칙 일반화.** 2단계 도구는 "수정 라운드 1회"로 구현돼 있어 2차가 그 1회를 소진한 것으로 취급했다. 그러나 AMD-01 §5의 제한은 **과제마다 비공개 검사 수정 1회**이고, 2차에서는 어느 과제도 검사를 고치지 않았다. 그래서 규칙을 과제 단위로 바꿨다.

- `--prior-curator-report`/`--prior-review-summary`를 이전 라운드 순서대로 반복해서 받는다.
- 비공개 검사가 실제로 바뀐 라운드만 그 과제의 수정 1회로 센다. 두 번째 수정은 `FIX_LIMIT_EXCEEDED`다.
- 수정 자격은 직전 라운드 판정의 BQ04·05·06 실패로 본다. 처음 판정된 S02도 이번에 자격이 생겼다.
- 수정한 과제의 재리뷰는 이전 모든 리뷰와 달라야 한다. 수정하지 않은 과제의 리뷰는 직전 라운드와 같아야 한다.
- 테스트 259개가 모두 통과했다. 1차·2차 판정을 다시 내려도 3/8로 같다.

**3차 운영자 실행 순서**

```powershell
cd C:\Users\dldls\OneDrive\Code\AAH-Main-curator-kits
$repo = 'C:\Users\dldls\OneDrive\Code\codexbench'
$env:PYTHONPATH = "$repo\src;$repo"
$out = "$repo\results\g2-preparation\amd01"
python -m evaluation.amd01_curator hashes --kits . --output "$out\kit-hashes-before-fix-r3.json"
# 수정 세션 (새 세션, 키트 루트): AAH-C03, AAH-D02, AAH-D04, AAH-R04, AAH-S02
python -m evaluation.amd01_curator evaluate --kits . --dev-report "$out\objective-dev.json" --before "$out\kit-hashes-before-fix-r3.json" --output "$out\curator-report-r3.json"
# 재리뷰 세션 (다른 새 세션): 수정 세션이 실제로 고친 과제만
python -m evaluation.amd01_curator reviews --kits . --output "$out\review-summary-r3.json"
```

개발 측 편입 판정:

```powershell
python -m benchmarks.main_public.main_freeze --include --dev-report results\g2-preparation\amd01\objective-dev.json --curator-report results\g2-preparation\amd01\curator-report-r3.json --review-summary results\g2-preparation\amd01\review-summary-r3.json --prior-curator-report results\g2-preparation\amd01\curator-report.json --prior-review-summary results\g2-preparation\amd01\review-summary.json --prior-curator-report results\g2-preparation\amd01\curator-report-r2.json --prior-review-summary results\g2-preparation\amd01\review-summary-r2.json
```

### AMD-01 이행 4단계: 3차 편입(8/8), 동결과 실행 승인

**3차 결과 (2026-11-27, 운영자 실행)**

| 과제 | 비공개 검사 수정 (3차, 과제당 1회) | 대안 구현 비공개 검사 | 불완전 수정 검출 | AI 리뷰 |
| --- | --- | --- | --- | --- |
| AAH-C02 | 없음 | 2/2 | 1/3 | APPROVE (1차) |
| AAH-C03 | NARROWED 1 | 2/2 | 2/3 | APPROVE (재리뷰) |
| AAH-D02 | NARROWED 1, REMOVED 1 | 2/2 (1차 0/2) | 2/3 (1차 3/3) | APPROVE (재리뷰) |
| AAH-D04 | NARROWED 1 | 2/2 | 2/3 | APPROVE (재리뷰) |
| AAH-R03 | 없음 | 2/2 | 2/3 | APPROVE (1차) |
| AAH-R04 | NARROWED 1 | 2/2 (1차 1/2) | 2/3 | APPROVE (재리뷰) |
| AAH-S02 | REMOVED 1 | 1/2, 실패는 명세로 정당화 | 3/3 | APPROVE (재리뷰) |
| AAH-S03 | 없음 | 2/2 | 3/3 | APPROVE (1차) |

- **증거 파일:**
  - `curator-report-r3.json` (`4fb0c7f5…`)
  - `review-summary-r3.json` (`0fec83c5…`)
  - `inclusion-round3.json`·`configs/main/amd01-inclusion.json` (`94a518a6…`)
- **편입:** `READY` 8/8. 수정한 5과제는 바뀐 것이 비공개 검사뿐이고, 수정 기록이 유효하며, 재리뷰가 새로 이뤄졌다. 수정하지 않은 3과제는 1차 리뷰가 그대로다.
- **검사 수정의 효과:** 수정 뒤 D02의 불완전 수정 검출이 3/3에서 2/3으로 줄었다. 기준(1개 이상)은 충족한다.
- **수정자·리뷰어:** 모두 `claude-opus-5-5`였다. 실험 계정(OpenAI)과 다른 제공자라 AMD-01 조건은 충족한다. 하지만 변형본을 작성한 개발 세션과 같은 모델이므로 독립성 한계로 보고한다.

**동결과 승인**

| 항목 | 값 |
| --- | --- |
| 동결 manifest | `configs/main/aah-main-emergency-v1-freeze.json`, SHA-256 `4596afb3cd3dd89efa88161386063105e23c6cca8e72815f025e10984bfe53e1`, `--check` 재생성 일치 |
| 코드 버전 | `3c2876ad…` (119개 파일) |
| 범위 | 8과제 × A–E × 1회 = 40슬롯. 체크포인트는 조건부(AMD-01 §7.2), ablation은 NOT_RUN |
| 모델 | `codex_cli`, `gpt-6-luna`, 추론 강도 low, Codex CLI 0.166.0 |
| 예산 | 초기 40턴, 복구 64턴, 합계 최대 104턴. 기존 ChatGPT 구독, 추가 결제 0원 |
| 실행 승인 | `configs/main/aah-main-emergency-v1-approval.json` (`f39483fa…`). 운영자가 RH·GR·CE를 겸임해 2026-11-27 승인 |
| 잠금 | `main_freeze --status` → `MAIN_FREEZE_LOCKED 4596afb3…` |

동결 이후 `.py` 파일이 바뀌면 코드 해시가 달라져 잠금이 풀린다. 그 경우 실행되지 않는다.

**실행 결과:** 2026-11-27 운영자 실행에서 `MAIN_EXECUTE_RECORDED … evaluated=40/40`이 나왔다. 결과는 [RESULTS_ANALYSIS §2.4](RESULTS_ANALYSIS.md#24-main-실측-결과--aah-main-emergency-v1-amd-01-emergency-탐색적)와 EL-20261127-032·033에 있다.

**실행 명령 (운영자 PowerShell, Docker Desktop 실행 중, Codex 로그인 상태)**

```powershell
cd C:\Users\dldls\OneDrive\Code\codexbench
$env:PYTHONPATH = "src;."
python -m benchmarks.main_public.main_schedule --execute --curator-kits C:\Users\dldls\OneDrive\Code\AAH-Main-curator-kits
```

### AMD-01 이행 5단계: 에이전트 셸 결함 수정과 v2 동결

**결함.** v1 실행(`aah-main-emergency-v1`)과 Dev Pilot에서 에이전트 셸 명령이 하나도 실행되지 않았다. 원인과 분석은 [RESULTS_ANALYSIS §2.4 타당성 경고](RESULTS_ANALYSIS.md#24-main-실측-결과--aah-main-emergency-v1-amd-01-emergency-탐색적)와 EL-20261127-034에 있다. v1 동결·승인·결과는 이력으로 보존한다.

**수정 (모델 호출 없는 재현으로 확인)**

| 항목 | 내용 |
| --- | --- |
| 재현 | `codex sandbox`에 턴과 같은 `shell_environment_policy.inherit="none"`을 넣자 v1과 같은 MXC `SYSTEMROOT` 오류가 났다. 정책을 빼면 명령은 실행되지만 `USERPROFILE` 등 호스트 환경 전체가 노출됐다 |
| 셸 환경 정책 | `inherit="all"` + `include_only=["SYSTEMROOT","WINDIR","COMSPEC","PATH","PATHEXT"]`. 명령은 실행되고 `USERPROFILE`은 보이지 않는다. [`live_dev01.SHELL_ENV_POLICY`](../../benchmarks/smoke/live_dev01.py) |
| 검사 경로 일치 | 접근 검사의 sandbox 명령에도 같은 정책을 넣었다. 셸 환경이 다시 깨지면 BQ09·10 검사 자체가 실패한다 |
| 실행 중 감지 | 이벤트 요약에 `command_outcomes`(완료·실행 불가·exit 0)와 `AGENT_SHELL_UNAVAILABLE`을 추가했다. Main 일정은 슬롯 기록에서 실행 불가 명령을 발견하면 그 슬롯에서 멈춘다. 같은 검사를 v1 기록 40개에 적용하면 40개 모두 걸린다(167건) |
| 권한 설정 해시 | `de14ca4e…` → `74e24628…`. 이전 접근 기록은 새 설정과 맞지 않아 실행기가 거부한다 |
| 테스트 | 263개 통과. 공유 셸 정책, 실행 불가 감지, 일정 중단, 접근 기록 교체 테스트를 추가했다 |

**검증 (실환경)**

| 검사 | 결과 | 근거 |
| --- | --- | --- |
| 8과제 MXC 접근 검사 (새 정책) | 8/8 BQ09·BQ10 PASS, 에이전트 턴 0회 | `results/g2-preparation/amd01-v2/access-<과제>-20261127-165535/access.json` |
| DEV-01 라이브 스모크 1차 | PASS. 감지 기능 추가 전이라 명령 결과는 기록되지 않음 | `results/s0-verification/live-dev01-aec39183-…/report.json` |
| DEV-01 라이브 스모크 2차 (감지 포함) | PASS. 명령 4개 완료, 실행 불가 0, exit 0 2개. 파일 편집 2회, 독립 판정 PASS | `results/s0-verification/live-dev01-3b770877-…/report.json` |

스모크는 Main 과제 노출을 피하려고 Dev 과제로 2턴 실행했다.

**v2 동결**

| 항목 | 값 |
| --- | --- |
| 실험 ID | `aah-main-emergency-v2` |
| 편입 | v1과 같은 AMD-01 3차 편입 8/8(`94a518a6…`)을 재사용한다. 과제 품질 판정은 셸 환경과 무관하다 |
| 접근 기록 교체 | `configs/main/aah-main-emergency-v2-access.json` (`5647f19b…`). 편입 기록에 묶여 있다 |
| 동결 manifest | `configs/main/aah-main-emergency-v2-freeze.json`, SHA-256 `5ef565bd48c3a684cbbf03c3a70687f4bda7f34e3ce5ba5649cc847895142e4a`. `--check` 재현 일치 |
| 코드 버전 | `2401bc7f…` |
| 범위·모델·예산 | v1과 같다. 8과제 × A–E × 1회 = 40슬롯, seed 20261127, `gpt-6-luna` low, 최대 104턴, 추가 결제 0원 |
| 잠금 | `MAIN_FREEZE_NOT_LOCKED EXECUTION_APPROVAL_MISSING`. 새 실행 승인이 필요하다 |

**v2에 남는 한계**

- **Pilot 재실행 없음:** Pilot도 셸이 막힌 환경이었다. 정책 시작값은 그 Pilot을 근거로 정해졌지만 Pilot을 다시 하지 않았다.
- **같은 과제 반복:** v1에서 같은 과제·같은 모델로 40슬롯을 실행했다. v1의 에이전트 출력은 v2에 전달되지 않는다. 다만 운영자와 개발 측은 v1 결과를 이미 봤다.

### AAH-D02 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-D02 d96a468e-2b50-4dd3-bf4f-fa3eb3a7a05f`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### AAH-D04 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-D04 7efe5a14-1bc9-45eb-ad2c-e633cdae53b1`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### AAH-R03 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-R03 3833bf45-693b-4794-8818-b75443f0aaf2`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### AAH-R04 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-R04 5e1b1463-3b92-4060-a701-d3b12c6edec7`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### AAH-C02 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-C02 2b2a7e92-2ae3-4602-8c99-3423c1709de2`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### AAH-C03 큐레이터 환경 검증

2026-11-27에 운영자가 Docker 평가 통과를 보고했다. 기준본은 기능 FAIL(예상)·회귀 PASS였고, 참조본은 기능·회귀·지시 검사 모두 PASS였다. 운영자 출력은 `CURATOR_DOCKER_PAIR_PASS AAH-C03 656ae573-e768-4c1c-874f-4e301812b317`이다. 비작성자 검토와 Main 승인 절차가 남았다.

### 큐레이터 평가 재현

`results/g2-main-public/AAH-Main-curator-kits.zip`을 저장소 밖에 풀고 `START_HERE.md`를 따른다. 과제별 `reference/`에 참조 구현을 작성하고 `curator_tests/`에 기능·회귀 검사를 작성한다. Docker Desktop을 켜고 과제의 `task.json`이 있는 폴더에서 일반 PowerShell로 실행한다. [작업 묶음 생성기](../../benchmarks/main_public/curator_kit.py)

```powershell
$repo = 'C:\Users\dldls\OneDrive\Code\codexbench'
$env:PYTHONPATH = "$repo\src;$repo"
python -m evaluation.curator_check --kit .
```

이 명령은 공개 기준본의 해시를 확인하고 수정본의 허용 변경과 공개 시그니처를 검사한다. 기준본과 참조 패치는 각각 깨끗한 상태에 재생하고, 핀된 Docker 이미지에서 동일한 큐레이터 검사 번들을 실행한다. 검증된 증거와 참조 패치는 키트 내부 `curator-docker-<ID>/`에 보관한다. 마지막 `CURATOR_DOCKER_PAIR_PASS <과제 ID> <기록 ID>` 줄만 연구 작업 기록에 전달한다.

합성 통합 테스트에는 Docker worker 코드를 로컬 프로세스로 실행한다. Docker Desktop을 사용하는 큐레이터 평가는 운영자 PowerShell에서 실행한다. [큐레이터 평가 도구](../../evaluation/curator_check.py)

`CURATOR_DOCKER_BLOCKED WorkspaceError`가 출력되면 같은 폴더에서 `python -m evaluation.curator_check --kit . --preflight`를 실행한다. 이 명령은 Docker나 비공개 검사를 실행하지 않고 키트 경로, 허용 변경, 공개 기준본 증거와 두 패치의 복원을 확인한다. 차단 메시지는 실패한 단계와 이유를 표시한다. `CURATOR_PREFLIGHT_PASS`가 나온 후 위 Docker 평가 명령을 실행한다.

### 비작성자 검토 절차

1. 해당 과제 작성에 참여하지 않은 팀원에게 [BQ01–14 기준](../evaluation/BENCHMARK_SPEC.md#111-검증-체크리스트)과 큐레이터 키트의 위치를 안내한다. 팀원은 승인된 큐레이터 환경에서 `task.json`, `baseline/`의 공개 검사, `reference/`의 참조 구현, `curator_tests/`의 비공개 검사, `curator-docker-<ID>/receipt.json`을 열어 검토한다.
2. 실험 로그의 실행 ID를 키트 안 평가 기록과 대조한다. BQ08의 반복 안정성은 동일한 입력·평가기 환경에서 기준본과 참조본 결과를 각각 최소 두 번 비교하는 것이 권장된다. 해당 기록이 부족하면 위 `curator_check` 명령을 추가 실행한다. 기준본은 기능 FAIL·회귀 PASS, 참조본은 기능·회귀·지시 PASS가 기대 결과다.
3. 명세와 테스트를 비교해 공개 요구에 있는 동작만 판정하는지 확인한다. BQ05의 부분 수정 검출과 BQ06의 대안 구현 확인이 현실적으로 어렵다면 벤치마크 명세에 따라 그 이유와 검토 근거를 남긴다. 격리, 조건 A–E의 원천 정보, 문서 상태, 과제 중복과 운영 적합성은 공개 설계 및 검증 자료와 대조한다.
4. 리뷰어가 각 과제의 BQ01–14에 결과와 근거를 남긴 뒤 승인 여부를 직접 기록한다. 수정이 필요한 과제는 보류 사유를 남긴다. 상세 기록은 큐레이터 환경에 보관하고 연구 작업 기록에는 과제 ID, 리뷰어 ID, 검토 증거 ID, 품질 항목별 판정 및 필요한 공개 해시를 전달한다.

과제별 기록 양식이다. 각 BQ 줄의 빈칸에 판정과 근거를 적는다.

```text
과제 ID =
작성자 ID =
리뷰어 ID =
검토일 =
검토 증거 ID =
대조한 Docker 실행 ID =
BQ01 요구사항 =
BQ02 초기 상태 =
BQ03 참조 구현 =
BQ04 테스트 타당성 =
BQ05 불완전 수정 =
BQ06 대안 구현 =
BQ07 공개 검사 =
BQ08 반복 안정성 =
BQ09 격리 =
BQ10 원천 정보 동등성 =
BQ11 문서 상태 =
BQ12 분리 및 중복 =
BQ13 권리 및 운영 =
BQ14 동료 리뷰 =
최종 판정(승인 / 보류) =
```

## 규모별 E2E 자원 추정

Pilot 슬롯당 평균인 약 91,926토큰과 실행·평가 39.06초를 동일하게 적용한 값이다. Main의 복잡도, 사전 점검, 체크포인트 수집·분기, P1 ablation은 별도다. 계정 잔여 크레딧과 토큰의 차감 단위는 확인이 필요하다. [규모 및 중단 기준](../evaluation/EXPERIMENT_DESIGN.md#92-사전-축소안)

| 계획 | E2E 슬롯 | 예상 모델 토큰 | 예상 실행·평가 시간 |
| --- | ---: | ---: | ---: |
| Full | 240 | 22,062,182 | 약 156분 |
| Core | 200 | 18,385,152 | 약 130분 |
| Reduced | 120 | 11,031,091 | 약 78분 |
| Emergency | 40 | 3,677,030 | 약 26분 |

Emergency의 E2E에 권고 예비량 20%를 적용하면 약 441만 모델 토큰과 31분이다. 본실험 범위는 동결 전에 팀이 승인한다. 이 산정표에는 실제 계정 결제액을 넣지 않았다.

## 공통 체크포인트

S4 합성 기록에서 고정한 source 패치, 공통 원장·이력·트리거 해시와 잔여 예산을 대조했다. B–E 네 분기에서 같은 snapshot을 검증한 뒤 `NEW_SESSION` 복구 턴으로 시작한 기록과 독립 판정을 확인했다. 모의 복원 및 손상된 source 거부 테스트를 다시 통과했다. [S4 체크포인트 검증](S4_VERIFICATION.md#조건-e와-체크포인트)

Pilot에서 공통 DEFAULT 초기 정책에 해당하는 B의 첫 턴 공개 검사 실패는 0/5였다. 다른 조건의 관측 실패는 공통 source의 확보율로 사용할 수 없다. 실제 source 확보율과 분기 비용은 미측정이다. D/E에서 실제 전달한 복구 다섯 건도 모두 일반 재시도로 전환됐으므로, 유형별 전략을 검증할 후보에는 공개 검사에서 관측 가능한 코드 위치나 테스트 연결 근거가 필요하다.

별도 Dev 실환경 검증 초안은 DEV-01~05 순서에 B 최초 턴을 과제당 최대 1회 사용한다. 첫 적격 실패를 캡처하면 B–E의 동일 외부 상태를 새 세션으로 각각 한 번 분기한다. 최대 5개 source 턴과 4개 분기 턴으로 총 9턴이다. 적격 실패가 없으면 5개 시도 및 비용만 기록한다. 이 초안은 새 세션을 실제 운영자 경로에 연결하고 모델 호출 범위를 별도 승인한 뒤 집행한다. [공통 실패 상태의 분기 규칙](../evaluation/EXPERIMENT_DESIGN.md#74-분기-실행)

## G2 판정 및 재현

**G2 승인 대기.** 공개 입력 검증을 마쳤고 운영자가 후보 8개의 큐레이터 Docker 평가 통과를 보고했다. Main 승인 후보는 0개다. AAH-S03 1과제 Main의 실행 경로와 동결 manifest 초안은 준비됐지만 잠금 조건 3개가 충족되지 않아 실행하지 않았다. [실행 경로와 동결 상태](#aah-s03-main-실행-경로운영-평가기동결-manifest) [구현계획의 G2 승인 조건](../project/IMPLEMENTATION_PLAN.md#101-승인-게이트)

1. 작성자와 다른 리뷰어가 큐레이터 환경에서 과제 명세, 평가 기록, 참조 패치와 비공개 검사를 검토하고 과제별 BQ01–14 결과와 근거, 리뷰어 ID, 검토 증거 ID를 남긴다. [벤치마크 품질 기준](../evaluation/BENCHMARK_SPEC.md#111-검증-체크리스트)
2. 승인된 과제 목록과 계정 가용 자원을 확인한다. 팀은 Main 규모, 예산, 실행 일정과 분석 규칙을 확정하고 공동 승인 후 동결한다. 별도 Dev 체크포인트 실험의 운영 경로와 예산은 병행 검토할 수 있다. 이번 검증에서 새 모델 호출은 0회였다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m analysis.g2_readiness --output results/g2-preparation/g2-review-20261127.json
python -m pytest -q tests/integration/test_g2_readiness.py
```

검토 JSON은 완료된 Dev Pilot 및 S4 합성 체크포인트 원천으로 생성한다. SHA-256은 `23b49ec8d97940f4d9dfc3cee1711cf01f76cf57e6902d2eced832ca07599947`이다. 새 G2 테스트 6개와 기존 체크포인트·평가 격리 테스트 2개가 통과했다. [구현계획의 G2 승인 조건](../project/IMPLEMENTATION_PLAN.md#101-승인-게이트)
