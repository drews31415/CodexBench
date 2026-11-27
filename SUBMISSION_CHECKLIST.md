# 제출 전 최종 점검표 (SUBMISSION_CHECKLIST)

2026-11-27 · 기준 실험 `aah-main-emergency-v2` · 판정: PASS / FAIL / BLOCKED / USER_INPUT_REQUIRED / PLANNED

## 요약

| 판정 | 항목 |
| --- | --- |
| PASS | SUB02, SUB03, SUB04, SUB05, SUB06, SUB07, SUB08, SUB09, SUB10, SUB11, SUB12, SUB13, SUB14, SUB15 |
| USER_INPUT_REQUIRED | SUB01 |
| FAIL / BLOCKED | 없음 |

**제출 판단: 조건부 GO.** SUB01(팀원별 실제 역할·기여)을 사용자가 작성하면 제출할 수 있다. 웹 대시보드 링크를 제출하려면 아래 "웹 배포 상태"의 사용자 단계(GitHub 저장소·푸시·GitHub Pages 켜기)가 추가로 필요하다. 링크 없이도 오프라인 대시보드로 모든 결과를 확인할 수 있다. 다른 항목은 실제 실행·검증 결과로 확인했다. SUB12의 고도화 항목 자체는 계획(PLANNED)이며, 문서는 이를 완료된 성과로 표기하지 않는다.

## 항목별 판정과 근거

| ID | 항목 | 판정 | 근거 |
| --- | --- | --- | --- |
| SUB01 | 팀원별 실제 역할 및 기여 내용 | **USER_INPUT_REQUIRED** | 저장소 기록으로 확인되는 사실은 세 가지뿐이다. 승인은 1인이 RH·GR·CE를 겸임했고(`configs/main/aah-main-emergency-v2-approval.json`), 커밋은 작성자 계정 1개로 기록됐으며, 교차 검토자가 없다. 실제 이름·역할·학습·기여를 확인할 수 없어 `submission.md` §9 표를 `[사용자 입력 필요]`로 남겼다. 임의 작성하지 않았다 |
| SUB02 | 심사위원용 실행 및 결과 확인 자료 | PASS | `docs/submission/JUDGE_GUIDE.md`(5분 경로·명령·기대 출력), `EVIDENCE_PACK.md`(E01–E15, 출처·run_id). `results/`를 뺀 저장소 사본에서 `verify-public`이 `SUBMISSION_PUBLIC_VERIFIED`를 출력했고, 전체 테스트는 실패 0이었다 |
| SUB03 | P-R2 미측정 상태 및 연구 한계 명시 | PASS | `submission.md` §5 ②(연구 질문 표, 측정/미측정 표)·④, `EVIDENCE_PACK.md` E06·E14, 대시보드 "조건 비교" 연구 질문 표와 "실험 감사" 한계 표에서 P-R2·AB1–AB4·LORO·과금액·엄격 토큰 예산을 미측정으로 표시했다 |
| SUB04 | 실용성 및 구현 성과 중심의 문장 개선 | PASS | `submission.md` §5 ①(검증된 구현 성과)과 ②(실증 결과)를 분리했다. §6에 실제 기록으로 보인 실용 가치 5가지를 적었고, 개선 근거가 없다는 결과는 그대로 유지했다 |
| SUB05 | A–E 비교군 설명표 | PASS | `submission.md` §4 표(정책 6열 + v2 결과 4열), `EVIDENCE_PACK.md` E02, 대시보드 조건 비교 |
| SUB06 | 시스템 아키텍처 다이어그램 | PASS | `docs/submission/figures/fig01-architecture.svg`(생성 그림, 헤드리스 렌더링으로 겹침 없음 확인), `submission.md` §4 텍스트 다이어그램, `EVIDENCE_PACK.md` E01 구성 요소 표 |
| SUB07 | 실제 실패 진단 및 복구 사례 | PASS | `EVIDENCE_PACK.md` E09(복구 5건 + 개입 없는 실패, run_id), E10(라이브 그래프 진단 대체 원인과 S3 합성 대비), `figures/v2-recovery-cases.svg` |
| SUB08 | 실험 결과 그래프 | PASS | `figures/v2-success-rate.svg`, `v2-tokens.svg`, `v2-paired-effects.svg`, `v2-task-matrix.svg`, `v1-v2-audit.svg`. 모든 값은 데이터 묶음에서 생성하며, 테스트 `test_figures_print_the_bundle_values`로 확인한다 |
| SUB09 | AMD-01 및 평가 독립성의 한계 | PASS | `submission.md` §5 ④·§10, `EVIDENCE_PACK.md` E14, 대시보드 감사 탭의 승인 시 공개 한계(승인 기록 원문). 내용은 AI 리뷰 대체, 검사 수정·리뷰 동일 모델, 5과제 검사 1회 수정, 1인 겸임 승인, Pilot 미재실행, v1 열람 |
| SUB10 | 실제 실험 데이터 기반 대시보드 | PASS | **웹 정적 페이지 `docs/dashboard/index.html`** (GitHub Pages, 24시간): `public_data/`에서 생성, release·실험 선택, Node로 모든 release·실험·화면 조합 1,000개 이상을 렌더링해 오류 0. **웹(Streamlit) `dashboard/streamlit_app.py`**: `public_data/`만 읽는다. release·실험 선택, 유효·무효·진행 중 구분, 마지막 갱신 시각을 표시한다. AppTest로 모든 화면·선택을 렌더링해 오류 0, 데스크톱·모바일 헤드리스 Chrome으로 확인했다. **오프라인 `docs/submission/dashboard.html`** 7개 화면. 데이터는 `aah-public-bundle.json`과 바이트 동일하다. 모든 탭·선택지·결측 데이터 조합을 Node로 렌더링해 `undefined`·`NaN`·오류 패널 0건을 확인했고(`dashboard_render.js`), Chrome 헤드리스로 7개 화면을 육안 확인했다 |
| SUB11 | 제출 문서 전체 문체 및 용어 정리 | PASS | 범위는 `submission.md`, `docs/submission/*.md`, 이 점검표, 변경 이력이다. 평서문(~다)과 통일 용어(조건 A–E, 독립 판정, 공개 검사, 복구 개입, DCC, 그래프 진단, 사후 기술 분석)를 썼다. 기존 연구 보고서는 수치 정합성만 갱신했고 문체는 바꾸지 않았다 |
| SUB12 | 최종 발표 전 고도화 계획 및 확장 가능성 | PASS (계획 항목은 PLANNED) | `submission.md` §7 표에 최종 발표 전 8개와 장기 2개를 구분하고, 각 항목의 근거 한계와 완료 기준을 적었다. 이번 작업에서는 수행하지 않았다 |
| SUB13 | 기존 실행 로그 기반 Recovery Replay | PASS | 대시보드 "복구 Replay"에 43개 사례가 있다(v2 개입 5·개입 없는 실패 6, v1 무효 개입 19·실패 10, S3 합성 3). 사례마다 7단계를 재생하고 자동 재생을 지원한다. 화면에 "모델 호출 없음, 라이브 아님"을 표시한다 |
| SUB14 | 제출 문서·대시보드·실험 결과의 수치 정합성 | PASS | (1) `verify-public`이 공개 CSV에서 동결 분석을 바이트 단위로 다시 계산해 일치한다. (2) 테스트 `test_submission_documents_quote_the_bundle_numbers`가 제출서·증빙의 핵심 수치와 테스트 수를 데이터 묶음·검증 기록과 대조한다. (3) 대시보드의 반올림을 Python과 같은 ties-to-even으로 맞췄다(−6.2%p, −18.8%p). (4) README의 오래된 상태 표기와 FINAL_REPORT의 테스트 수를 갱신했다 |
| SUB15 | 심사위원 환경에서의 실행 가능성 및 보안 검증 | PASS | 웹 대시보드는 Linux 컨테이너(Docker `python:3.12-slim`, `results/` 없음, `dashboard/requirements.txt`만 설치)에서 테스트 31개 통과(원시 기록·Node 필요 4개 건너뜀)와 서버 health `ok`를 확인했다. 의존성은 streamlit 하나로 고정, 인증·secrets·네트워크 요청이 없다. `results/`를 뺀 사본에서 `verify-public` 통과, 전체 테스트 실패 0. 대시보드는 CSP `default-src 'none'`, 외부 참조 0(테스트로 확인), 단일 파일. 공개 묶음은 화이트리스트 추출이며 `restricted/`·blobs·patches를 열지 않고, 제한 필드·로컬 절대 경로·이메일이 있으면 생성을 거부한다(`test_extract_run_copies_whitelisted_fields_only`, `test_reader_refuses_restricted_records`, `test_assert_public_rejects_restricted_fields_paths_and_addresses`). **미확인 환경:** macOS·Linux, Docker 없는 환경, Chrome 외 브라우저 |

## 검증 실행 기록

| 검증 | 결과 | 출처 |
| --- | --- | --- |
| 전체 자동 테스트 | 303개 통과, 실패 0, 건너뜀 0 (2026-11-27) | `docs/submission/data/verification-record.json` |
| Main v2 동결 재생성 | `MAIN_FREEZE_REPRODUCED 5ef565bd…` | 같은 기록 |
| v1·v2 분석 재계산 | `ANALYSIS_REPRODUCED` ×2 | 같은 기록 |
| 공개 자료 재계산 | `SUBMISSION_PUBLIC_VERIFIED` | `python -m analysis.submission_bundle verify-public` |
| 공개 묶음 재생성 | `SUBMISSION_BUNDLE_REPRODUCED` | `python -m analysis.submission_bundle check` |
| 심사 환경 모사 (`results/` 제외 사본, Streamlit 미설치) | 테스트 293개 통과, 실패 0, 건너뜀 10 (원시 기록 필요 5, Streamlit 필요 5). `verify-public`·`public_export verify`·`static_site check` 통과 | 제출 정리 중 실행 |
| 클라우드 Linux 모사 (Docker `python:3.12-slim`) | 아래 웹 배포 상태 참조 | 제출 정리 중 실행 |

## 웹 배포 상태 (DASHBOARD_DEPLOYMENT.md §2)

| 단계 | 판정 | 근거 |
| --- | --- | --- |
| 정적 페이지 (GitHub Pages, 24시간) | PASS | `docs/dashboard/index.html`, `docs/index.html`, `docs/.nojekyll`. `python -m analysis.static_site check` → `STATIC_SITE_REPRODUCED`, 데스크톱·모바일 화면 확인 |
| GitHub Pages 켜기 (U7) | USER_INPUT_REQUIRED | Settings → Pages → 배포 브랜치 `/docs` |
| 배포 가능한 Streamlit 앱·의존성·설정 | PASS | `dashboard/streamlit_app.py`, `dashboard/requirements.txt`(streamlit==1.67.0), 루트 `.streamlit/config.toml`. 로컬·Linux 컨테이너 실행 확인 |
| 공개 데이터 Exporter·버전 보존 | PASS | `python -m analysis.public_export` (`verify` → `PUBLIC_STORE_VERIFIED`, `check` → `PUBLIC_STORE_REPRODUCED`), 확정 데이터 덮어쓰기 거부·release 불변 테스트 |
| GitHub 저장소 (U1) | USER_INPUT_REQUIRED | 이 작업 사본에 git remote가 없다 |
| 커밋·푸시 (U2) | USER_INPUT_REQUIRED | 승인 없이 하지 않았다 |
| 공개 범위 결정·공개 데이터 검토 (U3·U6) | USER_INPUT_REQUIRED | 공개 저장소 또는 비공개 저장소 + 공개 앱 |
| Streamlit 계정·앱 생성·공개 설정 (U4·U5, 선택) | BLOCKED | U1–U3 이후 가능 |
| 실제 공개 URL 접속 확인 | PLANNED | 배포 직후 비공개 창으로 확인 (DASHBOARD_DEPLOYMENT.md §3.4) |

## 사용자가 직접 작성·확인할 항목

1. `submission.md` §9 팀원 표: 실제 이름, 역할, 구체적 수행 내용, 학습 내용 (SUB01).
2. `submission.md` §7의 최종 발표 전 계획: 실제 가용 일정과 우선순위 확인.
3. 대외 공개 범위: 대시보드·데이터 묶음에 과제 파일 경로와 공개 검사 ID가 들어 있다. 제출처 공개 정책에 맞는지 확인.
4. 커밋과 푸시: 이번 변경은 커밋하지 않았다. 검토 후 직접 커밋하거나 요청한다.
5. 웹 배포: GitHub Pages는 배포됨(2026-11-28 확인, https://drews31415.github.io/CodexBench/dashboard/, 링크 기입 완료). 남은 선택 항목 — 대시보드 개선본(2026-11-28) 푸시 승인, Streamlit Community Cloud 배포 여부 결정. 원래 항목: GitHub 저장소 생성·연결, Streamlit Community Cloud 앱 생성(Main file path `dashboard/streamlit_app.py`, Python 3.12), 공개 설정, 배포 URL을 `submission.md` §8·`JUDGE_GUIDE.md` §0·`README.md`의 `[GitHub Pages 배포 후 URL 입력]`(필수)과 `[배포 후 URL 입력]`(Streamlit, 선택) 자리에 기입.
