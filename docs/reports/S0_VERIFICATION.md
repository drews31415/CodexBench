# S0 구현 및 기술 검증 기록

2026-11-26 · IP01–IP05 · **G0 GO**

Windows의 DEV-01에서 실제 Codex 턴, 작업 사본 고정, 공개 검사 및 독립 Docker 판정까지 확인했다. 파일 변경이 없는 최종 패치는 기능 검사에서 실패했다. 이벤트 정규화와 사용량은 기존 실행 기록 및 합성 fixture로 검증했다. 사용자에게서 한시 Dev Smoke 공동 승인 확인을 받아 [G0](#g0-재판정)를 통과 처리했다.

## 작업별 판정

| 작업 | 확인한 범위 | 남은 완료 조건 |
| --- | --- | --- |
| IP01 · 공통 계약 | Python 패키지, 스키마 1.0, canonical JSON, A–E 정책 매핑, 실행·턴·증거·검사·평가 객체. CT02에서 필드 누락, 버전·해시·실행 연결 불일치 거절 | RH·GR·CE의 계약 검토·합의 기록 |
| IP02 · 작업 사본 | 고정 Git 커밋 및 내용 해시로 고정한 공개 디렉터리에서 별도 사본 생성. 파일 추가·수정·삭제, 빈 파일·바이너리, 이동의 삭제/추가 표현, 초기 상태 대비 패치 재적용 및 최종 해시 일치. 정지 여부와 소유 확인 후 정리 | 공개 소스와 합성 fixture 범위의 수용 조건 통과. 실제 런타임 프로세스와의 연결은 IP05 |
| IP03 · 런타임 | CLI 0.166.0, `gpt-6-luna`/`low` 요청, 실제 완료 이벤트·사용량, 동일 스레드 후속 전달, 타임아웃·취소 관측. RT02·RT07 Dev 검증 결과 기록 | 일반 Observer·Evidence Ledger 연결은 IP06 |
| IP04 · 검사 및 평가 | DEV-01/02의 기준본·수정본 4건에서 EP01 기대 판정 확인. 검토된 코드는 Docker에서 실행하고 호스트 평가기가 판정. 공개 검사 8건과 패치 4건 대조 | EP28의 실제 Codex·제어부 접근 경계, 새 모델 패치에 대한 공개 검사·독립 평가 경로 |
| IP05 · 실제 Dev Smoke | 사용자 셸에서 실제 DEV-01 턴 1건 완료와 독립 판정 `FAIL` 확인. 별도 실행에서 강제 타임아웃·취소, 잔존 프로세스 정리 및 Dev 격리 확인 | [한시 Dev Smoke 프로파일의 RH·CE 공동 승인 기록](DEV_SMOKE_PROFILE.md#승인-기록) |

## 재현

확인한 환경은 Windows 11, Python 3.12.10, Git 2.53.0.windows.3, pytest 9.2.0이다. S0 실행 패키지에는 외부 Python 런타임 의존성이 없다. 저장소 루트에서 아래 PowerShell 명령을 실행한다. 결과를 보존하기 위해 반복 시 출력 경로를 새로 지정한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m aah probe --config configs/s0-probe.json --output results/probe-01.json
python -m evaluation.smoke --output-dir results/dev-fixtures-01
python -m pytest tests/unit tests/contract
python -m pytest tests/integration tests/isolation
```

한 과제만 재현할 때는 평가기 명령에 `--task DEV-01` 또는 `--task DEV-02`를 추가한다. 과제별 디렉터리에는 `artifacts/`에 해시로 식별한 파일·스냅샷·패치, `public-checks/`에 공개 검사 결과, `requests/`에 평가 요청, `evaluation/`에 실행 ID별 독립 판정이 남는다. 실제 모델 호출 없이 다시 확인할 수 있다.

### 실행 결과

| 실제 검증 | 관측 결과 |
| --- | --- |
| `python -m pytest -q tests/unit tests/contract` | 86 passed |
| `python -m pytest -q tests/integration tests/isolation` | 13 passed |
| `python -m pytest -q` | 99 passed, 실패·skip 없음 |
| `python -m compileall -q src evaluation benchmarks` | 종료 코드 0 |
| 오프라인 wheel 빌드, 새 가상환경 설치, `adaptive-codex --help`와 `adaptive-codex probe` | 두 명령 모두 종료 코드 0. 설치된 wheel의 Python 파일과 현재 `src/aah/` 바이트 일치 |

패키지 빌드에는 setuptools 84.2.1과 wheel 0.48.0을 사용했다. 빌드 도구가 준비된 환경에서는 `python -m pip wheel --no-index --no-build-isolation --no-deps . -w dist`로 재현할 수 있다. 결과 wheel에는 런타임 패키지와 설치 메타데이터만 들어간다. 이번에 검사한 wheel의 SHA-256은 `44a812b8e3fd7c71f5c5418569c00a2a8678aa2f849e61d94664540ff42ac3a7`이다.

## 런타임 근거

실행한 로컬 명령은 `codex --version`, `codex exec --help`, `codex exec resume --help`다. 세 명령 모두 종료 코드 0이었고 버전은 `0.166.0`이었다. 설정 해시는 `9504c6be3f856bfa8c1731a875d5bc5a23770c2ad5c79d25d5a4269e0aa1f297`이다.

원본 탐지 보고서는 `results/s0-verification/capabilities.json`에 있다. 측정 시작은 2026-11-25 23:00:32 KST다. 턴 어댑터는 승인된 실행 호스트에 전달할 argv와 표준 입력을 구성한다. 합성 transport에서 최초 전달, 후속 세션 선택, 중복 및 불명확한 전달 차단, 기한 만료와 승인 범위 불일치를 검사했다. 운영용 transport 구현·배치는 OD-01/02에서 실제 환경을 정한 뒤 연결해야 한다.

| 항목 | 이번에 관측한 것 | 실제 Codex에서 추가로 확인할 것 |
| --- | --- | --- |
| RT01 | CLI 버전 및 모델 선택 옵션 | 고정 모델·추론 설정과 실행 ID |
| RT02 | JSON 출력 옵션 | 실제 이벤트 원문과 관측 가능 필드 |
| RT03 | `exec resume` 도움말 및 JSON 옵션 | 후속 요청의 실제 전달 및 공통 세션 정책 |
| RT04 | 합성 프로세스의 시간 초과·취소·오류 시 부모와 자식의 종료 | 승인 환경에서 실제 Codex와 자손 종료, 최종 패치 고정 순서 |
| RT05 | 고정 커밋에서 분리한 사본과 원본 보존 | 네이티브 필수 지침 로딩, 계정·작업 명령·네트워크 접근 분리 |
| RT06 | 공개 검사와 오프라인 평가기의 입력·산출물 분리 | 후보 코드가 평가 자료, 원본, ledger, 판정 채널에 접근하거나 쓸 수 없는 권한 경계 |
| RT07 | 사용량 모드 `UNKNOWN`, 실측 모델 토큰 `null` | 실제 이벤트의 usage 의미와 집행 가능한 한도 |

이 호스트에서 실제 파일 링크 생성 시도는 권한으로 거부됐다. 링크 메타데이터를 주입한 단위 테스트는 해당 파일의 바이트를 보관하지 않고 스냅샷을 불완전하게 표시하는 경로를 검증했다. OS에서 모든 경로 우회가 차단된다는 판정은 보류한다.

## 공개 Dev 평가기 결과

| 과제 | 입력 결함 | 판정 대상 | 공개 기능 / 회귀 | 독립 기능 / 회귀 / 필수 지침 | 독립 판정 |
| --- | --- | --- | --- | --- | --- |
| DEV-01 | 문자열 포매터의 공백 처리 | 기준본 | FAIL / PASS | FAIL / PASS / PASS | FAIL |
| DEV-01 | 동일 과제 | 공개 수정본 | PASS / PASS | PASS / PASS / PASS | PASS |
| DEV-02 | 캐시의 만료 경계 | 기준본 | FAIL / PASS | FAIL / PASS / PASS | FAIL |
| DEV-02 | 동일 과제 | 공개 수정본 | PASS / PASS | PASS / PASS / PASS | PASS |

다음 네 건은 `results/s0-verification/public-fixtures/`의 재현 기록이다. 평가 JSON은 `<task_id>/evaluation/<run_id>.json`에 있다.

| 공개 fixture | 실행 ID | 초기 상태 대비 패치 SHA-256 |
| --- | --- | --- |
| DEV-01 기준본 | `0ad02f0d-dc03-4668-8e2c-36601f77cbc0` | `a33750e890e49d458fa01eed1b59761429a1ed287aa1163ea9b54cb1a041d479` |
| DEV-01 수정본 | `ac25cd57-74f7-4c54-af8b-c7059ebd888a` | `9945b23300fa2b7cf9038b2c5c45e7e2908bf1b07e1bb13af76ac0963d48639d` |
| DEV-02 기준본 | `de124db5-99a4-45c9-932a-0174e319aa54` | `7d27274aca5c6046b4044f21dffdcd1f1d7404a1c2f6ce04b3eb7eb88c1c89c2` |
| DEV-02 수정본 | `7761f7f2-8665-46cc-855b-c6d4474ca3f3` | `22f2acd1fba5a100c86985a47d0eef826b255b23c44d2dac07606d5270e805fc` |

이 결과는 평가기가 두 상태를 구별하는지 확인한 개발 기록이다. 작업 사본에는 과제 소스만 들어간다. 오프라인 평가기는 코드를 실행하기 전에 검토된 합성 소스의 해시를 확인한다. 임의 제출물의 실행에는 검증된 별도 격리 환경이 필요하다. 이 절의 실행 ID와 패치 해시는 실제 모델 실행이나 A–E 성능 집계에 사용하지 않는다.

## DEV-01 첫 실행 준비

2026-11-26 · IP03–IP05 · 첫 실행 준비 시점의 G0 **NO-GO**

최초 실행 후보로 DEV-01을 선택했다. 아래 명령은 과제 정의와 기준 소스의 해시를 검증하고 ZIP 한 개를 만든다. 실행 예약, 인증, 모델 호출은 발생하지 않는다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.smoke.prepare --task DEV-01 --output results/s0-verification/dev-01-input-review.zip
```

이번 ZIP의 SHA-256은 `f2f2b3b0adbf6d3e612d71a9cf9c99bb8d5f39da95af36a62a47dea5d31bc313`이다. 같은 기준 소스·파일 모드와 과제 정의로 새 경로에 재생성할 수 있다. 작업 사본에는 `workspace/module.py`만 들어간다. 운영자는 바깥의 `initial-message.txt`에 있는 UTF-8 바이트를 최초 입력으로 쓰고 `review.json`에서 그 해시와 기준 리비전을 확인한다. 모델에 ZIP 전체나 운영자 디렉터리를 마운트하지 않는다.

`python -m pytest -q -m "not live"`에서 104개가 통과했다. 그중 `tests/integration/test_smoke_prepare.py`의 5개는 DEV-01/02의 고정 입력과 ZIP 구성, 재생성 해시, 기존 출력 보존, 변경된 기준본·과제 정의의 거부를 확인한다.

| 결정 | 이번 Dev 실행 제안 | 실행 전 확정할 사항 |
| --- | --- | --- |
| OD-01 · 과제와 모델 | DEV-01, 조건 A, 최초 입력 1회, 외부 개입 0회. 사용자가 기존 Codex의 ChatGPT 로그인을 선택했다. 2026-11-26 개발 세션에서 CLI 0.166.0의 `codex login status`가 ChatGPT 인증과 종료 코드 0을 반환했다. | 실제 실행기의 인증 분리 및 CLI 버전, 고정 모델 ID·추론 설정 |
| OD-02 · 실행 경계 | Docker Desktop Linux 엔진과 아래 고정 Python 이미지에서 합성 사본·종료 및 Dev fixture 평가 확인 | 실제 Codex 실행기, 모델 인증과 작업 명령의 분리, 허용 도구·네트워크 범위, 실제 RT04–06 근거 |
| OD-03 · 예산 | 1턴, 자동 재시도 0회. 시간 상한 120초 중 정리 15초를 남기는 초안. 토큰 집행은 미검증 | 유상 호출의 금액 또는 크레딧 단위와 상한, 집행 방식, RH·CE 승인 참조와 UTC 만료 |
| OD-12 · 판정 | 공개 검사 `DEV-01-P-FUNC`, `DEV-01-P-REG`, parser `smoke-json-v1`, 검사별 timeout 5초. 알려진 기준본·수정본은 패치 고정 후 Docker 후보 실행자와 호스트 판정자로 검증 | 검토된 소스 목록 밖의 모델 패치를 평가할 실행 환경 및 검사 경계 승인 |

Docker Desktop 기동 후 이 세션에서 기본 파이프와 Linux 엔진 파이프 모두 `permission denied`를 반환했다. 활성 컨텍스트는 `desktop-linux`였다. 일반 Windows PowerShell에서 같은 엔진의 서버 응답과 로컬 이미지 목록을 확인할 수 있다.

```powershell
docker --context desktop-linux version --format '{{.Server.Version}} {{.Server.Os}}'
docker --context desktop-linux image ls --digests
```

사용자가 일반 Windows PowerShell에서 위 버전 명령을 실행해 `29.9.3 linux`를 확인했다. 이어 공식 `python:3.12-slim` 이미지를 내려받아 제한된 임시 컨테이너를 실행했다. 사용자 제공 출력은 `Python 3.12.16 uid 65534 root_readonly True net ['lo']`였고, 이미지 digest는 `sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b`였다.

**합성 검사 재현.** 저장소 루트에서 다음 명령을 실행한다. 스크립트는 위 digest를 지정하고 `--pull=never`를 사용한다. 새로 만든 합성 작업 사본만 컨테이너에 마운트한다.

```powershell
python -I benchmarks/smoke/docker_probe.py
```

검사는 실제 사본 수정, 마운트 구성, 원본·평가·ledger를 대신하는 합성 파일의 읽기·갱신 차단 및 호스트 원본 보존을 확인한다. 강제 종료 전에는 Docker가 관측한 부모와 자식 PID를 기록하고, 종료 후에는 컨테이너 상태·종료 코드와 제거 여부를 확인한다. 매 시도는 `results/s0-verification/docker-<probe_id>/report.json`에 별도로 남긴다. 개발 세션에서 실행한 첫 시도는 엔진 연결 권한 거부로 `INCOMPLETE`였다. 사용자 셸에서의 합성 검사와 이후의 실제 인증·Codex 정리·독립 평가는 별도로 확인한다.

`python -m pytest -q -m "not live"`에서 109개가 통과했다. 추가한 5개는 모의 Docker 응답으로 읽기 노출 검출, 취소 순서, 실행 응답 유실 시 정리, 엔진 접근 거부 기록을 검사한다.

### Docker 합성 검사 결과

2026-11-26 03:15:52 KST에 사용자 PowerShell에서 시작한 probe `08280fd42cfa4e8b82850d002f7c448d`는 3,375ms에 9/9개를 통과했다. 보고서와 호스트에 남은 합성 작업 사본·운영자 파일의 바이트를 대조했다. 원본은 `results/s0-verification/docker-08280fd42cfa4e8b82850d002f7c448d/report.json`이고 SHA-256은 `06d65754b57cd35c4cfd57a2cd08fc190fe66c824552cfa13ae04e421dab48ad`다.

| 검사 범위 | 관측 |
| --- | --- |
| 프로세스·네트워크 | UID 65534, 유효 capabilities 0, no-new-privileges 1, 읽기 전용 루트, 인터페이스 `lo` |
| 작업 사본과 보호 자료 | 쓰기 가능한 호스트 마운트는 `/work` 하나였다. 사본의 수정 내용을 호스트에서 확인했다. 원본·평가·ledger를 대신한 파일 3개는 컨테이너에서 읽기·갱신용으로 열 수 없었고 호스트의 값도 보존됐다. |
| 강제 종료 | Docker 프로세스 목록에서 PID 771의 자식 PID 783을 확인했다. 종료 후 `exited`, PID 0, 종료 코드 137, OOM false였고 컨테이너 제거까지 확인했다. |

이 결과는 고정 이미지와 합성 프로세스에서의 RT04–06 부분 근거다. 실제 Codex의 인증 분리·필수 지침 로딩·취소와 모델이 생성한 패치의 판정은 미검증이다.

### Docker에서 Dev 기준본·수정본 독립 평가

Docker에 연결되는 PowerShell에서 저장소 루트를 기준으로 실행한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m evaluation.smoke --docker-evaluator
```

DEV-01/02의 기준본·수정본을 각각 고정하고 복원한다. 공개 검사는 기존 호스트의 검토된 fixture 경로를 사용한다. 독립 평가에서는 검토된 과제 코드와 입력값만 고정 이미지로 전달한다. 컨테이너에 호스트 디렉터리를 마운트하지 않으며, 기대값 비교와 판정 파일 작성은 호스트의 별도 평가 프로세스에서 수행한다. 평가 manifest와 환경 해시는 로컬 fixture 평가와 구분한다.

네 건의 기대 판정은 각 과제의 기준본 `FAIL`, 수정본 `PASS`다. 회귀·필수 제약, 공개 검사 결과 및 실행 ID·패치 해시 연결도 일치해야 명령이 `PASS`를 출력한다. `results/s0-verification/docker-eval-<id>/`에 요청, 패치, 개별 결과와 요약 JSON을 남긴다. 예상과 다른 판정이나 확인되지 않은 정리가 발생하면 다음 fixture 실행을 중단한다.

개발 세션의 `python -m pytest -q -m "not live"`는 113개 모두 통과했다. 개발 세션에서 Docker 평가를 시도한 `docker-eval-7b8b339a51154485b5b0899aa220d4a7`는 첫 과제에서 환경 식별에 실패해 `INFRA_ERROR`를 기록하고 중단했다. 이 세션의 Docker 버전 조회는 파이프 접근 거부로 실패했다.

### Docker 독립 평가 결과

2026-11-26 사용자 PowerShell에서 `python -m evaluation.smoke --docker-evaluator`를 실행해 공개 Dev fixture 4건의 기대 판정을 확인했다. 두 기준본은 기능 검사에서 실패했고 두 수정본은 기능·회귀·필수 제약 검사를 모두 통과했다. 기준본도 회귀·필수 제약을 통과했다.

| 과제 | 입력 | 실행 ID | 독립 판정 | 패치 SHA-256 앞 12자리 |
| --- | --- | --- | --- | --- |
| DEV-01 | 기준본 | `25645eff-51c6-4899-9af8-34a92fe6a905` | FAIL | `a33750e890e4` |
| DEV-01 | 수정본 | `e78e740e-587d-4b12-9797-b9730c748b50` | PASS | `9945b23300fa` |
| DEV-02 | 기준본 | `25481867-2adb-45a4-871f-cf9aff3fd0b2` | FAIL | `7d27274aca5c` |
| DEV-02 | 수정본 | `102e684d-3c7c-49da-837f-064db63cbeb2` | PASS | `22f2acd1fba5` |

요약 원본은 `results/s0-verification/docker-eval-073fba59300a4ebaa13a10f53579547c/summary-35dcc766969c4666a0a89efa5fb63499.json`이고 SHA-256은 `4c1f8574d7c43e7347dc7cd001068ae2c401110c8ff0d27658039f0b2e6ecfa2`다. 요청·판정의 실행 ID, 과제·기준 리비전, 평가 manifest·검사 번들, 실제 패치 파일의 SHA-256을 대조했다. 패치 4건을 각각 별도 임시 경로에 다시 적용해 원래 fixture 바이트와의 일치를 확인했다. 공개 검사 파일 8건에서도 검사 ID·출처·상태·스냅샷이 일치했다. 독립 평가 환경 해시는 네 건 모두 `9c6dd9b29883d2bbd5d62b39c8fa3deaedf682169744d7c74cc38913835dd52b`였다.

**판정 범위.** EP01은 두 공개 Dev 과제에서 충족했다. 현재 공개 검사와 Docker 평가기는 검토된 소스 해시만 실행하므로, 새로운 모델 패치는 `SUBMISSION_BOUNDARY_UNVERIFIED`로 보류될 수 있다. 실제 실행기를 연결하고 이 경계를 검증한 뒤 승인된 DEV-01 모델 실행·독립 판정을 확보해야 G0를 재판정할 수 있다.

### DEV-01 실제 실행 사전 점검

2026-11-26 사용자가 기존 ChatGPT 로그인으로 DEV-01 실행을 요청했다. 개발 세션에서 두 실행 환경의 사전 점검을 했다.

| 환경 | 관측 |
| --- | --- |
| Docker Desktop Linux | `desktop-linux` 엔진 버전 재조회가 명명된 파이프 접근 거부(`permission denied`)로 종료됐다. |
| Codex CLI 0.166.0 Windows 샌드박스 | `mxc`와 `elevated`에서 모델 없이 임시 디렉터리의 고정 Python 명령을 실행해 봤다. 두 모드 모두 OS 접근 거부와 종료 코드 1을 기록했고 표식 출력은 없었다. `elevated`는 설정 도우미의 `orchestrator_helper_exit_nonzero`도 반환했다. |

실제 Codex 명령을 전달하기 전에 중단했다. 모델 호출은 0건이며 실행 ID·패치·독립 판정은 생성되지 않았다. 사용자 셸에서 앞서 통과한 Docker 평가 결과는 유지했다. 이 시점의 G0는 **NO-GO**였으며, 개발 세션에서 사용할 수 있는 검증된 Codex 실행 경계와 모델 패치 평가 경로가 남아 있었다.

### DEV-01 운영자 실행

사용자는 일반 Windows PowerShell에서 Codex MXC 시작 점검의 종료 코드 `0`을 확인했다. 같은 PowerShell에서 다음 명령을 실행하면 최초 한 턴을 시도한다.

```powershell
Set-Location 'C:\Users\dldls\OneDrive\Code\codexbench'
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.smoke.live_dev01 --execute
```

이 명령은 기존 ChatGPT 로그인, Codex CLI 0.166.0, `gpt-6-luna`, 추론 `low`를 사용한다. 한 턴에 최대 105초를 주고 프로세스 정리에 15초를 남긴다. AAH는 모델 실행을 재시도하지 않는다. ChatGPT 구독 사용량에는 별도 강제 토큰 상한이 없다. `--execute`를 생략하면 모델 호출 전 사전 점검까지만 실행한다.

실행기는 임시 작업 사본에 MXC 프로파일을 적용한다. 사전 점검에서는 사본의 쓰기, 운영자 합성 파일의 읽기·쓰기 거부, 테스트용 호스트 loopback 접근 차단, 고정 Docker 이미지 및 ChatGPT 로그인 상태를 확인한다. 이후 모델을 시작하고 Windows 작업 개체의 프로세스 종료를 확인한 뒤 패치를 고정한다. 공개 검사와 독립 평가에서 후보 코드는 별도의 Docker 프로세스에서 실행한다. 신규 후보는 DEV-01의 `format_label(text)`와 제한된 문자열 `strip` 호출 형태만 실행할 수 있다. 다른 구현 형태는 검토 대상으로 남긴다.

보고서와 평가 산출물은 `results/s0-verification/live-dev01-<run_id>/`에 저장한다. 개발 세션에서 모델 실행 명령을 점검한 결과 MXC 권한 오류로 실행 직전에 차단됐으며 모델 실행 요청은 0건이었다. `python -m pytest -q -m "not live"`에서는 121개가 통과했다. 일반 PowerShell에서 실제 턴, 프로세스 정리, 평가를 확인해야 한다. 운영자 실행의 이벤트 기록은 유형별 건수·스레드 ID·관측 사용량·stdout 해시로 한정한다.

### 운영자 사전 점검 실패 및 진단 보완

일반 PowerShell의 첫 실행 `e5dcb9c5-0472-4e2d-bda8-59c567049292`는 MXC 사전 점검에서 종료 코드 1을 반환했다. 이때 기록된 단계와 진단 코드는 없으며 모델 시작 시도는 0건이다.

실행기는 사전 점검 코드를 작업 사본의 임시 파일로 전달하고, Codex 턴 전에 그 파일을 제거한다. 보고서에 Python 시작, 보호 파일 차단, 사본에 대한 쓰기, 호스트 loopback 차단을 단계별로 남긴다. Python이 시작하지 않은 경우에는 같은 프로파일에서 고정된 셸 명령의 시작 여부를 확인한다. 개발 세션에서 수정본으로 재실행한 `fa62c97a-f2c3-4bb7-8717-4f86471fe832`는 `MXC_UNAVAILABLE`을 기록하고 모델 시작 전에 막혔다. 운영자 셸의 첫 실패 원인은 새 진단 결과가 나오면 확인할 수 있다.

일반 PowerShell의 두 번째 실행 `addcacab-ddb8-4c06-b694-1dc643bf4dd8`에서도 맞춤 MXC 프로파일의 Python 시작 표식과 셸 비교 명령의 표식이 관측되지 않았다. 사전 점검 종료 코드는 1이며 모델 시작 시도는 0건이다. 이 기록에는 두 프로세스의 실패를 구별할 신호가 없다.

같은 PowerShell에서 아래 진단은 실제 실행을 시작하지 않고 고정된 `echo`만 비교한다.

```powershell
python -m benchmarks.smoke.live_dev01 --diagnose-mxc
```

기본 프로파일과 제한 프로파일을 일반 프로세스와 Windows 작업 개체에서 비교한다. 제한 프로파일은 기존 콘솔을 유지하는 작업 개체에서도 비교한다. 진단 결과에는 각 종료 코드, 시작 표식, 출력 길이와 알려진 오류 코드만 기록한다. 이어 원래 사전 점검을 수행한다. 당시 개발 세션에서는 다섯 비교 명령 모두 `MXC_UNAVAILABLE`이었다. 사용자의 일반 PowerShell에서 같은 비교 기록을 얻어야 해당 환경의 실패를 진단할 수 있었다. 이 시점의 G0는 **NO-GO**였다.

운영자 진단 `85ca2f1a-e95c-405e-a64c-5e9aee48f5c2`에서 기본 MXC는 일반 프로세스와 작업 개체 모두 통과했다. 맞춤 프로파일에서는 일반·작업 개체·기존 콘솔 포함 세 형태가 동일하게 종료 코드 1, 표준 출력 0바이트, 표준 오류 556바이트를 반환했다. 실패 지점은 맞춤 프로파일을 적용하는 사전 점검으로 좁혀졌으며 모델 시작 시도는 0건이다.

후속 수정은 사전 점검 명령에 MXC와 공유 권한 프로파일만 전달한다. 모델·추론·도구 설정은 실제 턴에서 적용한다. 실패 시 합성 사전 점검의 오류 제목을 경로와 설정값을 가리고 보고서에도 남긴다. 개발 세션의 확인 결과는 여전히 `MXC_UNAVAILABLE`이며 운영자 PowerShell에서 수정본의 결과 확인이 남아 있다.

### 첫 Codex 턴 시작과 모델 교체

운영자 실행 `f04c50ee-92d4-41b0-a7d1-3f6ed3d291c6`에서 MXC 사전 점검 네 단계, Docker 엔진 및 고정 이미지 확인, ChatGPT 로그인 확인이 모두 통과했다. Codex는 `thread.started`와 `turn.started`를 남겼으나 `turn.failed`와 종료 코드 1로 종료했다. 남은 프로세스는 0개였다. 완료된 응답, 사용량, 패치, 판정은 관측되지 않았다.

당시 요청한 `gpt-5.4-mini`는 2026-08-31에 ChatGPT 로그인 방식의 Codex에서 지원 종료됐다. 계정에서 반환한 실제 오류 문장은 해당 시도에 보존되지 않았다. 기본 모델을 `gpt-6-luna`로 교체하고 Codex CLI 0.166.0의 내장 모델 목록에서 모델 ID와 `low` 추론 지원을 확인했다. 이제 실패 이벤트의 문장을 저장하지 않고 해당하는 알려진 원인 코드만 기록한다. 모델 지원 종료 및 대체 모델은 [Codex 모델 문서](https://learn.chatgpt.com/docs/models?surface=cli)에 기록돼 있다.

### 첫 실제 턴 완료 및 독립 판정

2026-11-26 15:09:06 KST에 시작한 `d5a2fcc7-3fd8-4bf2-bc2c-7a6b5270af7d`에서 MXC 보호 검사 네 단계가 통과했다. `gpt-6-luna`/`low` 요청에서 `turn.completed` 1건, CLI 종료 코드 0, 잔존 프로세스 0개를 관측했다. 이벤트는 입력 토큰 78,591개, 캐시 입력 토큰 63,616개, 출력 토큰 518개를 보고했다.

최종 패치의 `changed_paths`와 변경 내역은 비어 있었고 앞뒤 스냅샷이 일치했다. 패치 SHA-256 `a33750e890e49d458fa01eed1b59761429a1ed287aa1163ea9b54cb1a041d479`를 다시 계산하고 임시 디렉터리에 적용해 `module.py`가 기준본과 바이트 단위로 일치함을 확인했다. 요청·판정의 실행 ID, 기준 리비전, 평가 manifest 및 공개 검사 스냅샷도 일치했다.

| 항목 | 관측 |
| --- | --- |
| 공개 기능 `tab-edges` | FAIL. 문자열 양끝의 탭이 남아 기대값 `alpha`와 달랐다. |
| 공개 회귀 `inner-spaces` | PASS |
| 독립 Docker 평가 | 기능 FAIL · 회귀 PASS · 필수 지침 PASS · 최종 FAIL. 평가기 오류 코드는 비어 있다. |

원본 보고서는 `results/s0-verification/live-dev01-d5a2fcc7-3fd8-4bf2-bc2c-7a6b5270af7d/report.json`이며 SHA-256은 `67220f7efa451254f517c438edb5137d4b39a6a41942b5d28f801b958ebe2c06`다. 이벤트 유형별 개수는 남았으며, 파일 변경을 시도했는지 또는 도구에서 오류가 있었는지는 이 기록으로 확인할 수 없다.

G0의 요구사항 중 실제 Dev 과제 실행 및 독립 판정은 확보했다. 과제 실패는 실행 기록에 보존한다. 실제 타임아웃·취소, 후속 전달·세션 정책 및 남은 권한 경계 검증이 끝나면 G0 전체를 재판정한다.

### 런타임 추가 검증

기존 Dev 실행을 마친 일반 PowerShell에서 저장소 루트를 기준으로 실행한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m benchmarks.smoke.live_dev01 --verify-runtime
```

같은 MXC 프로파일과 고정 Docker 평가 이미지를 사용한다. 사전 점검을 통과한 뒤 기존 ChatGPT 로그인으로 `gpt-6-luna`/`low`를 최대 네 턴 호출한다. RT04는 각 사례당 최대 45초와 정리 여유 15초를 사용한다. RT03은 각 턴당 최대 105초와 정리 여유 15초를 사용한다. RT03용 합성 대화는 Codex가 로컬에 저장한다.

| 확인 | 판정 근거 |
| --- | --- |
| RT04 타임아웃·취소 | `codex sandbox`에서 실행한 테스트 프로세스와 Codex CLI 프로세스가 같은 Windows 작업 개체에 속하는지 확인하고 각각의 종료 신호를 확인한다. 별도의 두 실제 모델 턴에서도 `turn.started` 직후 타임아웃이나 취소를 발동하고 CLI 종료, 잔존 프로세스 0개, 정상 완료·실패 이벤트 미발행을 확인한다. 두 검증의 관측값을 각각 기록한다. |
| RT03 후속 요청 | 새 합성 턴에서 임의 코드를 전달한다. 관측한 스레드 UUID로 `exec resume`을 호출하고, 후속 턴의 세션 ID와 응답 속 코드가 일치하는지 확인한다. DEV-01 과제 평가나 LT02의 실패 복구 효과 측정에는 포함하지 않는다. |
| RT05 원본·권한 | MXC 사전 점검에서 실험 원본과 분리된 쓰기 가능한 사본, 합성 보호 파일, 호스트 loopback을 확인하고 실제 턴 전후 호스트 파일 바이트를 대조한다. |
| RT06 독립 실행 경계 | 평가기와 동일한 Docker 실행 경로에서 합성 평가 입력과 ledger의 읽기·쓰기 차단, 비루트 사용자, 읽기 전용 루트, 네트워크 및 인증 환경 변수의 제한을 확인한다. |

`RUNTIME_CHECKS_PASS`는 위 네 점검 범위의 통과를 뜻한다. 결과는 `results/s0-verification/live-dev01-<run_id>/report.json`에 남는다. 기존 DEV-01 평가 기록 `d5a2fcc7-3fd8-4bf2-bc2c-7a6b5270af7d`는 독립적인 과제 실행이다. 개발 세션의 새 검증 명령은 MXC 사전 점검에서 `MXC_UNAVAILABLE`로 종료됐으며 모델 실행 시도는 0건이다.

### 운영자 런타임 점검 결과와 RT04 재검증

2026-11-26 15:30 KST 운영자 실행 `a0c90602-5e15-46d6-b5e1-a746dcbe6972`의 결과는 다음과 같다.

| 확인 | 결과 | 관측 근거 |
| --- | --- | --- |
| RT03 | PASS | 실제 두 턴이 같은 스레드 UUID를 사용했고 후속 턴이 합성 확인 코드를 반환했다. |
| RT04 | INCOMPLETE | 두 턴 모두 완료됐고 각각 잔존 프로세스는 0개였다. 합성 명령의 시작 표식이 관측되지 않아 강제 타임아웃과 취소는 발동하지 않았다. |
| RT05 | PASS | MXC 사전 점검을 통과했고 실제 턴 이후 원본과 합성 보호 파일의 바이트가 보존됐다. 이 점검은 사전 점검과 호스트 파일 보존 범위다. |
| RT06 | PASS | Docker 평가와 동일한 경로에서 합성 보호 파일의 읽기·쓰기가 차단됐고 비루트 사용자, 읽기 전용 루트, loopback 및 인증 환경 변수 점검을 통과했다. |

원본 보고서는 `results/s0-verification/live-dev01-a0c90602-5e15-46d6-b5e1-a746dcbe6972/report.json`이며 SHA-256은 `bbd0136f33ea505d70981577007453b0316cedd7188564aadf3208234c9fd7f6`다.

같은 일반 PowerShell에서 RT04만 다시 확인한다. 사전 점검과 샌드박스 내 테스트 프로세스 정리를 통과하면 모델을 최대 두 턴 호출한다. `RT04_PASS`에는 로컬 Windows 작업 개체의 정리 증거와 실제 CLI 턴의 중단 증거가 포함된다. 모델 서비스 쪽 요청의 중단 여부는 이 결과의 관측 범위 밖이다.

```powershell
python -m benchmarks.smoke.live_dev01 --verify-rt04
```

개발 세션에서 이 명령은 MXC 사전 점검의 `MXC_UNAVAILABLE`로 중단됐고 모델 실행 시도는 0건이었다.

### 운영자 RT04 통과

2026-11-26 15:37:29 KST에 시작한 운영자 실행 `7c784b57-bb0c-401d-a0e6-d5fb57719ecc`에서 네 사례가 모두 통과했다.

| 사례 | 종료 상태 | 개별 종료 확인 | 작업 개체 내 잔존 프로세스 |
| --- | --- | --- | ---: |
| MXC 테스트 명령 타임아웃 | `TIMED_OUT` | Codex와 전경 테스트 프로세스 | 0 |
| MXC 테스트 명령 취소 | `CANCELLED` | Codex와 전경 테스트 프로세스 | 0 |
| 실제 Codex 턴 타임아웃 | `TIMED_OUT` | Codex CLI | 0 |
| 실제 Codex 턴 취소 | `CANCELLED` | Codex CLI | 0 |

실제 턴 두 건에서 각각 `thread.started`와 `turn.started`가 한 번 나왔다. 두 턴의 사용량은 `null`이다. `results/s0-verification/live-dev01-7c784b57-bb0c-401d-a0e6-d5fb57719ecc/report.json`의 SHA-256은 `441dbeb2bc2027aa59750e2c757e54e70fc6685b904e9e40f4a3fd60d60f3d74`다.

### RT02 이벤트 정규화와 관측 범위

| 이벤트 | 0.166.0 실제 기록 | S0 정규화 |
| --- | --- | --- |
| `thread.started`, `turn.started` | DEV-01, 후속 실행과 두 중단 턴에서 관측 | 유형별 건수와 유효한 스레드 UUID |
| `turn.completed` | DEV-01과 두 합성 후속 실행에서 관측 | 유형별 건수, 유효한 사용량 필드. 과제 완료 선언은 별도 증거가 필요하다. |
| `turn.failed`, `error` | 첫 실제 턴 `f04c50ee-92d4-41b0-a7d1-3f6ed3d291c6`에서 관측 | 유형별 건수와 분류된 오류 코드 |
| `item.started`, `item.completed` | DEV-01에서 관측 | 유형별 건수. 새 파서는 알려진 아이템 종류별 건수도 기록한다. |
| `item.updated` | 검토한 실제 보고서에서 미관측 | 0.166.0 이벤트 스키마와 합성 fixture에서 처리 확인 |
| 알 수 없는 이벤트·아이템, 잘린 JSON | 기존 보고서에서 건수 미수집 | 분류별 결측 건수와 stdout 해시를 기록한다. |

과거 보고서는 도구·명령 본문과 개별 아이템 종류를 보존하지 않았다. 합성 JSONL로 원시→요약 정규화를 재현하고, 명령·출력·메시지 본문 없이 위 필드가 남는지 확인했다. 정규화된 `EvidenceRecord`를 보존하는 일반 Observer와 Ledger는 IP06 범위다. [0.166.0 이벤트 스키마](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/exec/src/exec_events.rs) · [합성 fixture](../../tests/fixtures/runtime/codex-exec-0.166.jsonl)

### RT07 토큰 사용량과 집행 가능 범위

0.166.0의 실행기는 `turn.completed.usage`에 스레드의 `token_usage.total`을 기록한다. 고정 버전의 구현과 기존 동일 스레드 완료 기록을 대조해 Dev 사용량 모드를 `CUMULATIVE`로 기록했다. 검증된 같은 세션의 초기·후속 완료 두 건에 한해서 필드별 차분을 계산했다. 캐시된 입력은 입력 토큰에 포함되므로 합계에 다시 더하지 않는다. [0.166.0 집계 구현](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs) · [0.166.0 토큰 필드](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/protocol/src/protocol.rs)

| 관측 | 입력 | 캐시된 입력 | 출력 |
| --- | ---: | ---: | ---: |
| DEV-01 완료 스레드 | 78,591 | 63,616 | 518 |
| 합성 세션 최초 턴 직후 누적 | 11,629 | 8,576 | 5 |
| 합성 세션 후속 턴 직후 누적 | 23,621 | 19,968 | 27 |
| 합성 후속 턴의 차분 | 11,992 | 11,392 | 22 |
| 강제 타임아웃·취소 턴 | 결측 | 결측 | 결측 |

같은 검증을 보존된 세션 `cd683b8f-3150-420a-a92d-3de2b40321bd`와 `8678832a-c6f0-4da6-a90d-b2ca42592b3e`에도 적용했다. 후속 턴의 입력·캐시된 입력·출력 차분은 각각 11,974·11,392·23과 11,995·11,392·26이었다.

완료 이벤트 중복, 검증되지 않은 세션 경계, 누적값 감소 또는 필수 필드 결측이 있으면 차분을 확정하지 않는다. 모든 값이 0이면 CLI 기본값과 실측값을 구별할 수 없어 결측으로 처리한다. DEV-01용 추정기 `utf8-bytes-div4-v1`은 전달 문자열의 UTF-8 바이트 수를 4로 나눠 올림한다. 고정 입력 214바이트에서 추정값은 54이며, 실행 중 모델에 전달된 전체 컨텍스트를 집계한 입력 토큰 수는 78,591이었다. Dev 프로파일의 토큰 한도는 `observe_only`다. 하네스가 집행한 상한은 모델 턴 수와 벽시계 시간이며, 계정 비용·크레딧의 강제 상한은 확인되지 않았다.

## G0 재판정

**GO — 2026-11-26.** RT01–RT07의 Dev 검증 결과, RT04–RT06 안전 점검, 실제 DEV-01 실행과 독립 판정이 모두 기록됐다. DEV-01의 기능 검사 결과는 `FAIL`이다. G0에서는 실제 실행과 평가 경로가 작동했음을 확인했다.

LT01의 실제 Dev 턴·이벤트·고정 입력·패치 근거는 첫 DEV-01 실행과 RT02 기록에 있다. LT03의 중단·독립 환경·사용량 근거는 RT04·RT06·RT07에 연결했다.

사용자가 RH·CE 승인을 받았다고 확인해 실행 범위, 비용 한도 및 만료를 기록했다. G0 판정에는 기존 실행 증거를 사용했으며 모델 추가 호출은 0건이다. [DEV-01 한시 Smoke 프로파일](DEV_SMOKE_PROFILE.md#승인-기록)

RT05의 Dev 고정 지침은 최초 입력에 포함됐다. DEV-01 보고서의 최초 입력 해시와 승인 검토용 ZIP 안의 `initial-message.txt` 해시가 일치했다. RT05·RT06의 격리 증거는 사용한 Dev 프로파일의 작업 사본과 합성 보호 자료를 대상으로 한다.

## 다음 작업

S1은 IP06의 공개 이벤트 정규화와 Evidence Ledger, IP07의 예산·상태 전이, IP08의 비교군 guard, IP09의 공통 감지를 거쳐 IP10의 A/B 실행으로 연결한다. IP10에는 IP11의 DEFAULT 패키징과 IP14a의 공통 renderer도 필요하다.
