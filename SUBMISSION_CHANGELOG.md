# 제출 정리 변경 이력 (SUBMISSION_CHANGELOG)

2026-11-27 · 최종 제출 정리 (Final Submission Refinement) · 모델 호출 0회

## 범위와 원칙

- 기존 구현, 테스트 결과, Main 실험 결과, 실제 실행 기록만 사용했다. 추가 Codex 라이브 실험이나 Main·Pilot·Checkpoint·Ablation 실험은 하지 않았다.
- 핵심 연구 정책, 비교군(A–E), 동결 설정, 분석 설정, 기존 결과 파일은 바꾸지 않았다. `results/`에는 쓰지 않았다(읽기 전용).
- 동결 코드 해시 범위(`src/aah`, `benchmarks/smoke`, `benchmarks/main_public`, `evaluation`, `analysis/main_analysis.py`)의 파일은 수정하지 않았다. 따라서 `MAIN_FREEZE_REPRODUCED`가 유지된다.
- 비공개 평가 자료, 정답 패치, 큐레이터 키트, 에이전트 원문(`restricted/`), 인증 정보는 열거나 공개하지 않았다.
- 커밋·푸시는 하지 않았다(사용자 승인 대상).

## 새로 만든 파일

| 파일 | 내용 |
| --- | --- |
| `analysis/submission_bundle.py` | 공개 데이터 묶음 생성기. 화이트리스트 추출, 개인정보·경로·제한 필드 검사, `build`·`check`·`verify-public`·`record-verification` 명령 |
| `analysis/submission_figures.py` | 데이터 묶음에서 SVG 그림 생성 (아키텍처, 성공률, 토큰, 효과 크기, 과제 × 조건, 복구 사례, v1/v2 감사) |
| `analysis/dashboard/template.html`, `app.js`, `style.css` | 대시보드 원본. 7개 화면, 순수 렌더 함수, CSP로 외부 요청 차단 |
| `docs/submission/dashboard.html` | 생성된 단일 파일 대시보드 (데이터·그림 내장) |
| `docs/submission/data/aah-public-bundle.json` | 대시보드 데이터. Main 80 run과 S3 합성 3 run의 정규화 기록, 입력 파일 1,025개의 SHA-256 |
| `docs/submission/data/main-v2/*`, `data/main-v1-invalid/*` | 동결 분석 산출물 사본 (`run_status.csv`, `metric_summary.csv`, `paired_contrasts.csv`, `analysis_sets.json`, `analysis.json`) |
| `docs/submission/data/verification-record.json` | 테스트·동결·분석 재현 실행 기록 |
| `docs/submission/figures/*.svg` | 제출용 그림 12개 |
| `docs/submission/EVIDENCE_PACK.md` | 심사 증빙 E01–E15 (출처·실험 ID·run_id) |
| `docs/submission/JUDGE_GUIDE.md` | 대시보드 실행과 공개 자료 검증 가이드 |
| `tests/integration/test_submission_bundle.py`, `tests/integration/dashboard_render.js` | 공개 묶음·그림·대시보드·문서 수치 정합성 테스트 16개 |
| `SUBMISSION_CHECKLIST.md`, `SUBMISSION_CHANGELOG.md` | 제출 점검표와 이 문서 |

## 수정한 파일

| 파일 | 변경 |
| --- | --- |
| `submission.md` | 공식 항목 구조(1–10, 표, ※ 안내 문구)를 유지하고 전면 개정. 핵심 가치·대상 사용자·시나리오, 구성 요소 역할, 아키텍처 다이어그램, A–E 정책·결과 표, 구현 성과와 실증 결과 분리, P-R1/P-R2/P-R3 해석, 측정·미측정 구분, v1 결함 처리, AMD-01 한계, 최종 발표 전·장기 계획 구분. 팀원 항목은 사용자 입력 필요로 표시 |
| `README.md` | 제출 자료 링크 추가. 보고서 상태 표의 오래된 "미실측" 표기를 실제 상태로 수정 |
| `docs/reports/RESULTS_ANALYSIS.md` | §2.5 끝에 "사후 기술 확인 (사전 등록 아님)" 추가. 기존 수치와 가설 판단은 변경 없음 |
| `docs/reports/EXPERIMENT_LOG.md` | append-only 색인에 EL-20261127-039 추가 |
| `.gitattributes` | `docs/submission/data/**`, `figures/**`, `dashboard.html`을 `-text`로 고정 (해시 검증 바이트 보존) |

## 기록에서 새로 확인한 사실 (사후 기술 분석)

1. **라이브 그래프 진단의 일반 재시도 대체.** 라이브 그래프 진단(v2 1회, v1 8회, Pilot 5회, S4 1회)은 모두 `INSUFFICIENT_TYPED_EVIDENCE`로 GENERIC_RETRY에 대체됐다. 원인은 공개 검사 결과(Main `main-unittest-counts-v1`, Pilot `pilot-verdict-v1`, S4 `smoke-json-v1`)에 traceback frame이 없고 테스트 ID가 코드 심볼로 해석되지 않은 데 있다. 그래서 TESTS_TARGET·FAILS_AT 교차 연결이 0개였다(15건 모두 미해결 `unresolved_test_id`·`unverified_frame_or_version`). 같은 코드가 S3 합성 검증에서는 RELOCALIZE·REVIEW_REGRESSION을 선택했다.
2. **같은 입력, 다른 판정.** v2 AAH-D04의 C·E는 2개 턴 모두 바이트 동일한 메시지를 받았지만 판정이 PASS/FAIL로 갈렸다. E − C, E − D, P-R3의 −12.5%p는 모두 이 D04-E 슬롯의 실패에서 나왔다.
3. **복구 경과.** 1턴 공개 검사 실패 후 복구를 받은 B–E 5개 슬롯 중 4개가 독립 PASS로 끝났다(기술 통계).
4. **라이브 정책 격리 재확인.** B/D·C/E 최초 메시지 동일 8/8, 필수 지침 해시 A–E 동일 8/8, 그래프·DCC 호출이 설계대로 분리됐다.

## 결정

| 결정 | 이유 | 대안 |
| --- | --- | --- |
| 대시보드를 Streamlit 대신 오프라인 단일 HTML로 구현 | Streamlit이 설치돼 있지 않다. AGENTS.md §4·§6은 검증되지 않은 새 서버·프레임워크 도입과 임의 설치를 금지한다. 심사 환경에서 설치·네트워크 없이 열려야 한다 | Streamlit 앱 (설치·서버 필요). 같은 데이터 묶음을 읽도록 추후 추가할 수 있다 |
| 원시 payload 비공개 | 프로젝트 설정 `raw_access: restricted`, 평가 프로토콜의 공개 범위 규칙 | 사례 설명은 기존 보고서의 서술을 출처와 함께 인용 |
| 사후 분석을 별도 표시 | 사전 등록 분석과 섞지 않는다 (EVALUATION_PROTOCOL, AMD-01 §8) | — |

## 2차 정리: 웹 대시보드와 공개 데이터 내보내기 (2026-11-27)

요청에 따라 Streamlit Community Cloud에 배포할 수 있는 웹 대시보드와 버전별 공개 데이터 저장소를 추가했다. 1차에서 Streamlit 대신 정적 HTML을 택했던 결정(AGENTS.md §4·§6)은 사용자의 명시적 요청으로 바뀌었다. Streamlit은 개발 검증용 임시 가상환경과 Linux 컨테이너 안에만 설치했고, 시스템 Python이나 저장소 런타임 의존성(`pyproject.toml`)은 바꾸지 않았다. 배포·푸시·저장소 공개 전환은 하지 않았다.

| 파일 | 내용 |
| --- | --- |
| `analysis/public_export.py` | 공개 데이터 내보내기. 새 실험 자동 발견, 상태 판정(유효성은 등록부로만), append-only 저장(확정 데이터 덮어쓰기 거부, 상태 이력), release 고정, catalog 재생성, `verify`·`check` |
| `configs/dashboard/experiments.json` | 사람이 검토한 실험 등록부 (상태·사유·타임라인·분석 경로·기본 release·한계 목록) |
| `public_data/` | 버전별 공개 데이터: Main v1·v2, S3 합성, release `submission-2026-11-27` |
| `dashboard/streamlit_app.py`, `dashboard/aah_dashboard/*`, `dashboard/requirements.txt`, `.streamlit/config.toml` | Streamlit 대시보드 7개 화면 (데이터 읽기·화면 값·차트·화면 분리) |
| `tests/integration/test_public_export.py`, `tests/integration/test_dashboard_app.py` | 내보내기·저장소·대시보드 테스트 (Streamlit 화면 테스트는 Streamlit이 설치된 환경에서 실행, 아니면 건너뜀) |
| `DASHBOARD_DEPLOYMENT.md`, `DASHBOARD_UPDATE_GUIDE.md` | 배포 절차(사용자 승인 단계 포함)와 데이터 갱신 절차 |
| `analysis/submission_bundle.py` | 동결·승인 기록이 없는 새 실험도 내보낼 수 있게 하고, 공개 재계산을 공용 함수(`recompute_problems`)로 분리. 제출 묶음 출력은 바이트 단위로 같다(`SUBMISSION_BUNDLE_REPRODUCED`) |
| `.gitattributes`, `.gitignore` | `public_data/**` 바이트 고정, `.streamlit/secrets.toml` 제외 |
| `submission.md`, `JUDGE_GUIDE.md`, `EVIDENCE_PACK.md`, `README.md`, `SUBMISSION_CHECKLIST.md` | 웹 대시보드 안내와 `[배포 후 URL 입력]` 자리 추가 |

제출 release `submission-2026-11-27`은 최종 검증 기록(303개 통과)을 담도록 커밋 전에 다시 만들었다. 처음 만든 release는 이 세션 안에서 만들었고 커밋·공개된 적이 없다. 고정한 실험 데이터 해시는 같다.

테스트가 찾아 고친 문제: 이벤트의 미관측 값이 `None`으로 표시되던 문제("미관측"으로 표시), 저장 단계에서 내보내기 본문의 개인정보 검사가 빠져 있던 문제(저장 시 다시 검사), 차트 축 라벨 겹침, 그래프 노드 라벨 가독성.

## 3차 정리: 24시간 접근 가능한 정적 웹 페이지 (2026-11-27)

Streamlit Community Cloud 앱은 12시간 방문이 없으면 잠들기 때문에, 서버 없이 24시간 열리는 GitHub Pages용 정적 페이지를 추가했다. 배포·푸시는 하지 않았다.

| 파일 | 내용 |
| --- | --- |
| `analysis/static_site.py` | `public_data/`만 읽어 `docs/dashboard/index.html`(대시보드), `docs/index.html`(이동 안내), `docs/.nojekyll`을 만든다. `build`·`check` |
| `analysis/dashboard/app.js`, `style.css`, `template.html` | 정적 대시보드를 release·실험 선택, 상태 배너(유효·무효·진행 중·분석 전·미검토), 마지막 갱신 시각, 실험별 타임라인·상태 이력을 데이터에서 읽도록 일반화. 제출 스냅샷(`docs/submission/dashboard.html`)은 호환 어댑터로 같은 화면을 유지. 모바일 레이아웃(그리드·탭 스크롤) 수정 |
| `analysis/public_export.py` | `export`·`release` 실행 시 정적 페이지를 자동으로 다시 만든다 |
| `tests/integration/test_static_site.py`, `tests/integration/dashboard_render.js` | 정적 페이지 재현·오프라인·Pages 파일·개인정보·release별 전체 화면 렌더링·문서 수치 대조 |
| `.gitattributes` | `docs/dashboard/**`, `docs/index.html`, `docs/.nojekyll` 바이트 고정 |

정리 중 찾아 고친 문제: 정적 대시보드가 토큰 구간을 `[-6,515, 20,406]`(ASCII 마이너스, + 기호 없음)으로 표시해 문서의 `[−6,515, +20,406]`과 달랐다. 이 표기는 1차 정적 대시보드부터 있었고, 부호 표기를 문서와 같게 고쳤다.

## 4차 정리: 웹 대시보드 고도화 (2026-11-28)

처음 보는 심사위원이 문제의식·Codex 활용·핵심 기술·결과와 한계를 짧은 시간에 이해하고 증거로 이동하도록 GitHub Pages 대시보드를 고쳤다. 모델 호출 0회. 연구 정책·동결 코드(`MAIN_FREEZE_REPRODUCED` 유지)·실험 결과·제출 고정본(`docs/submission/`, `SUBMISSION_BUNDLE_REPRODUCED` 유지)은 바꾸지 않았다. 커밋·푸시는 하지 않았다. 상세 감사·대표 사례 선정 근거·개선 과제는 [DASHBOARD_REWORK_2026-11-28.md](docs/project/DASHBOARD_REWORK_2026-11-28.md).

| 파일 | 내용 |
| --- | --- |
| `analysis/dashboard/app.js`, `style.css`, `template.html` | 개요 재구성(소개·역할·문제의식·핵심 지표·아키텍처·4개 기술·결과와 한계·빠른 탐색 6개와 대표 사례 자동 선택), 조건 설계·2×2·막대·forest plot, 과제별 Repository Graph와 상호작용 그래프(확대·이동·필터·선택·경로 강조), 8단계 Replay(재생·일시정지·처음부터, 같은 과제 비교), DCC 파이프라인·점수 구성, 감사 화면 데이터 기준·전체 해시 복사, 화면 모드 전환, 키보드·모바일 개선. KST 날짜 변환, 뒤로 가기, 잘못된 주소 안내 결함 수정 |
| `analysis/dashboard/submission-2026-11-27/` | 제출 시점 화면 원본의 고정 사본 (`docs/submission/dashboard.html`은 이것으로만 생성) |
| `analysis/submission_bundle.py` | 제출 대시보드를 고정 사본에서 만들도록 경로만 변경 (`FROZEN_DASHBOARD`). 출력 바이트 동일 |
| `analysis/repository_graphs.py`, `public_data/repository_graphs/` | 8개 Main 과제 Repository Graph를 공개 과제 원본에서 기존 빌더로 재추출 (`build`·`check`) |
| `analysis/static_site.py` | 재추출 그래프를 무결성 확인 후 내장, 짧은 해시 표시 |
| `docs/dashboard/index.html` | 다시 생성 |
| `tests/integration/test_static_site.py`, `dashboard_render.js`, `dashboard_browser.js` | 재추출 그래프 재현·변조 거부, 제출 고정 원본 유지, 새 화면·잘못된 주소·KST 렌더 검사, 실제 Chrome 검사(데스크톱·모바일 × 라이트·다크, 넘침·겹침·CSP·네트워크·콘솔, 상호작용) |
| `README.md`, `submission.md`, `docs/submission/JUDGE_GUIDE.md`, `SUBMISSION_CHECKLIST.md` | 배포 URL 기입, 웹 대시보드 5분 경로 |
| `DASHBOARD_DEPLOYMENT.md`, `DASHBOARD_UPDATE_GUIDE.md` | 배포 상태(2026-11-28 배포본 = 커밋 `84b839b`), 재추출 그래프 단계, 고정 원본 규칙 |

검증 (2026-11-28, Windows 11, Python 3.12.10, Node 24, Chrome): `python -m pytest tests -q -p no:cacheprovider` → 303 passed, 5 skipped (Streamlit 미설치 환경의 Streamlit 화면 테스트; 작업 전 기준 298 passed, 5 skipped). 대시보드 원본을 마지막으로 고친 뒤 `test_static_site.py`·`test_submission_bundle.py`·`test_public_export.py` 34 passed(실제 브라우저 검사 포함). `REPOSITORY_GRAPHS_REPRODUCED`, `STATIC_SITE_REPRODUCED`, `PUBLIC_STORE_VERIFIED`, `SUBMISSION_PUBLIC_VERIFIED`, `SUBMISSION_BUNDLE_REPRODUCED`, `MAIN_FREEZE_REPRODUCED 5ef565bd…`. release의 검증 기록(`verification-record.json`, 2026-11-27)은 고정본이라 다시 만들지 않았다.

## 5차 정리: 대시보드 정보 구조 간소화 (2026-11-28)

새 기능 없이 정보 구조와 디자인만 정리했다. 개요를 승인된 시안(5개 영역)대로 바꾸고, 각 화면은 핵심 시각 자료를 먼저 두고 상세 기록은 접힌 영역이나 실험 감사 화면으로 옮겼다. 기존 기능·공개 데이터·연구 결과와 한계 표기는 그대로 접근할 수 있다. 상세는 [DASHBOARD_REWORK_2026-11-28.md](docs/project/DASHBOARD_REWORK_2026-11-28.md) §7.

| 파일 | 내용 |
| --- | --- |
| `analysis/dashboard/app.js`, `style.css`, `template.html` | 개요 5개 영역, 화면별 우선순위 재배치와 접기, 중립 디자인(흰 바탕·얇은 구분선·그림자 없음), 헤더 간소화 |
| `docs/dashboard/index.html` | 다시 생성 |
| `tests/integration/test_static_site.py`, `dashboard_browser.js` | 실행 요약·무효 배너·실행 기록 수 검사를 실험 감사 화면으로 옮김, 개요 링크 5개·간단 다이어그램·접힌 아키텍처 검사, 다크 배경색 갱신 |
| `docs/submission/JUDGE_GUIDE.md` | 웹 5분 경로를 개요의 링크 4개 + 감사 링크로 갱신 |

## 6차 정리: 분석 화면은 최종 실험만 (2026-11-28)

무효 실험(Main v1)을 실험 선택 목록과 그래프 진단 기록 목록에서 숨기고, 실험 감사 화면에서만 열 수 있게 했다. 데이터·제출 release·실행 기록 구성 표기는 바꾸지 않았다. 실험 감사 화면에 실험별 그래프 대조 요약(v2 1/1, v1 8/8 일치)을 추가했다. 상세는 [DASHBOARD_REWORK_2026-11-28.md](docs/project/DASHBOARD_REWORK_2026-11-28.md) §8.

## 검증

검증 결과는 [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md)의 SUB14·SUB15와 `docs/submission/data/verification-record.json`에 기록했다.
