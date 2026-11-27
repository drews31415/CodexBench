# AAH 심사위원용 실행·확인 가이드

모든 확인은 **모델 호출 없이** 가능하다. Codex 계정, API 키, Docker, 네트워크가 없어도 된다. 대시보드와 공개 데이터 검증은 저장소만으로 동작한다. 원시 실행 기록(`results/`)은 접근이 제한된 로컬 자료라 제출물에 없다.

## 0. 웹으로 보기

웹 대시보드 주소: **https://drews31415.github.io/CodexBench/dashboard/**. 서버가 없는 정적 페이지라 24시간 열리고, 설치·로그인이 필요 없다. 상단 "데이터 기준"에서 `제출본 (2026-11-27) · 고정본`을 고르면(기본값) 제출 시점 결과가 고정돼 보이며, 이후 발표용 결과가 추가돼도 바뀌지 않는다. Streamlit 버전은 선택 사항이며 공개 URL을 적지 않았다. 링크가 열리지 않으면 아래 오프라인 대시보드를 쓴다.

**웹 대시보드에서 5분 확인.** 개요 화면의 **핵심 기능 탐색** 네 줄을 순서대로 누르고, 마지막으로 **상세 실험 기록 및 검증 결과**를 연다. 각 줄은 데이터에서 규칙으로 고른 대표 사례를 바로 연다 (줄에 마우스를 올리면 열리는 사례가 보인다. 선정 기준은 `docs/project/DASHBOARD_REWORK_2026-11-28.md` §3).

| 순서 | 개요의 링크 | 열리는 사례 | 확인할 것 |
| --- | --- | --- | --- |
| 1 | 조건 비교 | Main v2 | A–E 성공 과제 수·성공당 토큰, 효과 크기와 95% 구간(모두 0 포함), 연구 질문별 측정 여부 |
| 2 | 그래프 탐색 | AAH-C03 Repository Graph | 공개 검사 → 수정 허용 파일 → 의존 모듈, 노드를 눌러 관계 확인. 위쪽 **그래프 진단 기록**으로 바꾸면 라이브 AAH-D04·E(교차 연결 0 → 일반 재시도)와 목록의 S3 합성 RELOCALIZE(교차 연결 2 → 후보 4, 라이브 아님) |
| 3 | 복구 Replay | AAH-D04 · C → PASS | 8단계 자동 재생(일시정지·처음부터). 아래 **같은 과제의 조건별 실행**을 펼치면 D04·E가 같은 메시지로 FAIL |
| 4 | 컨텍스트 최적화 | AAH-D04 · C | 후보 → 점수 → 중복 → 예산 → 선택. **선택·제외 항목**을 펼치면 점수 구성과 이유 |
| 5 | 상세 실험 기록 및 검증 결과 | 실험 감사 | 데이터 기준과 고정 해시, v1 무효 사유, 정책 격리, 동결 해시 사슬(복사 버튼), 재현 검증, 주요 한계 |

웹 대시보드는 제출 이후 화면을 개선했다. 수치와 데이터는 제출본과 같다(release가 데이터 해시를 고정). 제출 시점 화면 그대로는 아래 오프라인 `docs/submission/dashboard.html`(고정본)에서 볼 수 있다.

## 1. 5분 확인 경로

| 순서 | 할 일 | 확인할 것 |
| --- | --- | --- |
| 1 | `docs/submission/dashboard.html`을 브라우저(Chrome·Edge·Firefox·Safari)로 연다 | 상단에 데이터 묶음 SHA-256과 "모델·Codex 호출 없음" 표시 |
| 2 | **개요** 탭 | Main v2 40슬롯 실행·판정, 성공 33·실패 7, 자동 테스트 결과, v1 무효 표시 |
| 3 | **조건 비교** 탭 | A–E 성공률·토큰·성공당 토큰, 효과 크기와 95% 구간, 연구 질문별 판단 |
| 4 | **복구 Replay** 탭에서 `AAH-D04 · E → FAIL` 선택 후 ▶ 자동 재생 | 실패 발생 → 감지(T1) → 그래프 진단(후보 0, 대체) → GENERIC_RETRY → DCC 패키지 → 후속 턴 → 안전 종료·독립 FAIL |
| 5 | **그래프 탐색** 탭에서 라이브 사례와 `S3 합성 · RELOCALIZE`를 번갈아 선택 | 라이브는 교차 연결 0개로 후보가 0개, 합성은 TESTS_TARGET 교차 연결 2개와 후보 4개 |
| 6 | **실험 감사** 탭 | v1 무효 사유, 수정 과정, 동결 해시 사슬, 정책 격리 재확인, 한계와 미측정 항목 |

바로 가기 링크도 쓸 수 있다(파일 경로 뒤에 붙임). 같은 주소 형식은 웹 대시보드에서도 동작한다(예: `https://drews31415.github.io/CodexBench/dashboard/#replay?case=…`).

- `dashboard.html#replay?case=aah-main-emergency-v2%7C6dbae23e-f630-4457-9fb3-6f7da060cc87&step=0`
- `dashboard.html#graph?graph=synthetic%7Ccd8c853f-96d0-403a-8cbc-e69444f8f990%7Cintervention-0001`
- `dashboard.html#context?exp=aah-main-emergency-v2&task=AAH-D04&cond=E`
- `dashboard.html#trace?exp=aah-main-emergency-v2&task=AAH-C03&cond=A`

## 2. 대시보드 설치와 실행

**설치가 필요 없다.** `dashboard.html` 한 파일에 화면 코드, 스타일, 공개 데이터 묶음, 그림이 모두 들어 있다.

- **실행.** 파일을 더블클릭하거나 브라우저 주소창에 파일 경로를 넣는다. 서버가 필요 없다. 로컬 서버로 보고 싶다면 `python -m http.server -d docs/submission 8000`을 실행한 뒤 `http://localhost:8000/dashboard.html`을 연다.
- **오프라인·보안.** 외부 스크립트·폰트·이미지를 불러오지 않는다. `Content-Security-Policy: default-src 'none'`으로 네트워크 요청이 차단된다. 테스트 `test_dashboard_is_offline_and_carries_the_bundle_hash`가 이를 확인한다.
- **데이터 출처.** 내장 데이터는 `docs/submission/data/aah-public-bundle.json`과 바이트 단위로 같다(`verify-public`이 확인). 각 패널 하단에 출처 파일과 해시를 표시한다.
- **Replay와 라이브 실행의 구분.** Replay는 저장된 run 기록을 단계별로 다시 보여 줄 뿐이며 모델을 호출하지 않는다. 화면 상단에 이를 표시한다.
- **표시 규칙.** 측정하지 않은 값은 `N/A` 또는 `미측정`으로 표시한다. 무효 실험(v1)을 선택하면 빨간 경고가 함께 나온다. S3 합성 기록에는 "모델 호출 0, 라이브 아님"이 표시된다.
- **화면 모드.** 운영체제의 라이트·다크 설정을 따른다. 인쇄하면 탭과 버튼이 숨겨진다.

### 왜 Streamlit이 아니라 정적 HTML인가

Streamlit을 먼저 검토했다. 그러나 이 저장소의 개발 지침(AGENTS.md §4·§6)은 필요성이 검증되지 않은 새 서버·프레임워크 도입과 임의 패키지 설치를 금지한다. 런타임 의존성도 `networkx` 하나뿐이다. 그래서 심사 환경에서 설치·서버·네트워크 없이 열리는 **단일 HTML 파일**을 택했다. 생성기(`analysis/submission_bundle.py`)는 Python 표준 라이브러리와 저장소 코드만 쓴다.

## 3. 공개 자료만으로 결과 검증 (Python 3.11+, 추가 패키지 없음)

저장소 루트에서 실행한다.

```powershell
# Windows PowerShell
$env:PYTHONPATH = "src;."
python -m analysis.submission_bundle verify-public
```

```bash
# macOS / Linux
PYTHONPATH=src:. python -m analysis.submission_bundle verify-public
```

기대 출력은 다음과 같다.

```text
PUBLIC_RECOMPUTE aah-main-emergency-v1 slots=40 bootstrap=10000 analysis.json=4de4eb3c717e
PUBLIC_RECOMPUTE aah-main-emergency-v2 slots=40 bootstrap=10000 analysis.json=4aa0297600f0
SUBMISSION_PUBLIC_VERIFIED
```

이 명령이 하는 일은 네 가지다.

1. `data/main-v2/analysis.json`의 출력 해시와 `run_status.csv`·`metric_summary.csv`·`paired_contrasts.csv`의 SHA-256을 대조한다.
2. `run_status.csv`에서 **동결된 AMD-01 분석 함수**(`analysis/main_analysis.py`, cluster bootstrap 10,000회, seed 20261126)를 다시 실행해 CSV를 바이트 단위로 대조한다.
3. 데이터 묶음의 수치가 `analysis.json`과 같은지, 공개 묶음에 제한 필드·로컬 경로·이메일 주소가 없는지 확인한다.
4. 대시보드 내장 데이터가 데이터 묶음과 같고, 외부 자원을 참조하지 않는지 확인한다.

## 4. 자동 테스트 (모델 호출 없음)

```powershell
$env:PYTHONPATH = "src;."
python -m pip install "pytest>=8" "networkx>=3.2,<4"   # 이미 있으면 생략
python -m pytest tests -q -p no:cacheprovider
```

- 제출 정리 시점의 기록은 `docs/submission/data/verification-record.json`에 있다. 대시보드 **실험 감사** 탭에서도 볼 수 있다.
- `results/`가 없는 환경에서는 원시 기록이 필요한 통합 테스트 5개가 `results/ is local and access restricted` 사유로 건너뛰어진다. Streamlit이 설치되지 않았으면 웹 대시보드 화면 테스트 5개도 건너뛴다(`pip install -r dashboard/requirements.txt`로 설치하면 실행된다). 제출 정리 중 `results/`를 뺀 저장소 사본에서 전체 테스트를 실행해 확인했다(실패 0). `-rs` 옵션을 붙이면 건너뛴 사유가 출력된다.
- Windows의 가상환경(venv) `python.exe`는 기본 인터프리터를 자식 프로세스로 띄우는 리디렉터라, Windows Job Object 격리 테스트는 venv가 아닌 기본 Python으로 실행해야 한다.
- Docker 엔진 호출은 대부분 테스트 대역(monkeypatch)으로 대체돼 있다. 다만 위 확인은 Docker가 설치된 Windows 11 환경에서 했으며, Docker가 없는 환경의 전체 실행은 확인하지 않았다. Windows 전용 테스트(MXC·Job Object)는 다른 OS에서 건너뛴다.
- `tests/live`(실제 Codex 호출)는 별도 승인 대상이며 기본 실행에 포함되지 않는다.

## 5. 원시 기록이 있는 환경에서의 추가 검증 (팀 내부)

`results/`(접근 제한 로컬 자료)가 있는 운영 환경에서만 실행할 수 있다.

| 검증 | 명령 | 기대 출력 |
| --- | --- | --- |
| 동결 manifest 재생성 | `python -m benchmarks.main_public.main_freeze --check` | `MAIN_FREEZE_REPRODUCED 5ef565bd…` |
| v2 분석 재계산 | `python -m analysis.main_analysis --experiment results/main-live/aah-main-emergency-v2 --output results/analysis/aah-main-emergency-v2-amd01 --amd01 --verify` | `ANALYSIS_REPRODUCED` |
| 공개 묶음 재생성 대조 | `python -m analysis.submission_bundle check` | `SUBMISSION_BUNDLE_REPRODUCED` |
| 검증 기록 갱신 | `python -m analysis.submission_bundle record-verification` | `VERIFICATION_RECORDED all steps passed` |

`--verify`, `--check`, `check`는 읽기 전용이다. 기존 결과를 덮어쓰지 않는다. `build`와 `record-verification`은 `docs/submission/`만 다시 쓴다.

## 6. 공개 범위와 보안

- **포함.** 정규화된 이벤트 종류·종료 코드·검사 결과·상태 전이·개입 결정·컨텍스트 선택 근거와 점수·그래프 구조·사용량·독립 판정 상태와 해시, 동결 분석 산출물.
- **제외.** 에이전트 메시지·명령 원문·명령 출력(`restricted/`), 스냅샷·패치 본문, 큐레이터 키트, 비공개 검사와 정답 패치, 인증 정보. 생성기는 이 경로를 열지 않는다. 테스트 `test_reader_refuses_restricted_records`가 이를 확인한다.
- 테스트 계정이나 API 키가 필요하지 않다. 실제 Codex 재실행에는 실행자 본인의 ChatGPT 로그인이 필요하며, 제출물에 포함하지 않는다.

## 7. 문제 해결

| 증상 | 조치 |
| --- | --- |
| 대시보드가 빈 화면 | 브라우저의 JavaScript 사용을 확인한다. 같은 수치는 `EVIDENCE_PACK.md`와 `data/*.csv`에 있다 |
| 한글이 깨짐 | 파일을 UTF-8로 연다. Windows는 `Malgun Gothic`, macOS는 시스템 글꼴을 쓴다 |
| `verify-public`의 `ModuleNotFoundError: aah` | `PYTHONPATH`를 `src;.`(Windows) 또는 `src:.`로 설정한다 |
| 해시 불일치 | Git 줄바꿈 변환을 확인한다. `.gitattributes`가 `docs/submission/data/**`를 `-text`로 고정한다 |
