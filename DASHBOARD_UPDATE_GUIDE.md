# 대시보드 데이터 갱신 가이드

새 실험 결과를 웹 대시보드(정적 페이지와 Streamlit)에 반영하는 절차다. 실험 원시 기록(`results/`)은 Git에서 제외된 제한 자료이므로, **공개용 내보내기(exporter)** 가 원시 기록에서 공개 가능한 필드만 골라 `public_data/`에 버전별로 저장한다. 그 폴더를 커밋해 배포 브랜치에 푸시하면 Streamlit Community Cloud 앱이 자동으로 갱신된다.

```text
results/ (로컬, 제한)  ──python -m analysis.public_export export──▶  public_data/ (Git) ──push──▶  Streamlit Cloud
          configs/dashboard/experiments.json (사람이 검토한 실험 상태) ──┘        │
                                   (자동) analysis.static_site ──▶ docs/dashboard/index.html ──push──▶ GitHub Pages
```

## 1. 원칙

| 원칙 | 구현 |
| --- | --- |
| 공개 가능한 필드만 | 화이트리스트로 추출한다. `restricted/`(에이전트 메시지·명령 원문·출력), 스냅샷·패치 본문, 큐레이터 키트, 비공개 검사, 인증 정보는 열지 않는다. 제한 필드·로컬 절대 경로·이메일이 있으면 저장 자체를 거부한다 |
| 유효성은 사람이 정한다 | 상태는 `configs/dashboard/experiments.json`에만 기록한다. 등록되지 않은 새 실험은 자동 발견되지만 `진행 중`·`분석 전`·`유효성 미검토`로만 표시된다 |
| 덮어쓰지 않는다 | 확정된 실험(분석 완료)의 데이터는 바꿀 수 없다. 상태만 바뀌면 이력(`status_history`)으로 남는다. 진행 중 실험만 갱신된다 |
| 합산하지 않는다 | 화면은 한 번에 한 실험만 보여 준다. 실험 버전끼리 결과를 합치지 않는다 |
| 시점 고정 | release는 실험 데이터 해시를 고정한 이름 붙은 선택이다(예: 제출본, 최종 발표본). 만든 뒤에는 바꿀 수 없다 |
| 갱신 시각 표시 | 실험별 `exported_at`, 상태 갱신 시각, release 생성 시각이 저장되고, 화면에 "마지막 데이터 갱신"으로 표시된다 |

## 2. 실험 상태

| 상태 | 의미 | 정하는 주체 |
| --- | --- | --- |
| `VALID` / `VALID_EXPLORATORY` | 유효 / 유효·탐색적 | 등록부 (사람) |
| `INVALID_INFRA_COMPROMISED` 등 `INVALID_*` | 무효. 이력으로 보존 | 등록부 (사람) |
| `UNREVIEWED` | 실행·분석은 끝났지만 검토가 등록되지 않음. 유효로 간주하지 않음 | 자동 |
| `ANALYSIS_PENDING` | 모든 슬롯이 끝났지만 동결 분석 산출물이 없음. 지표 미공개 | 자동 |
| `IN_PROGRESS` | 실행·판정되지 않은 슬롯이 있음. 지표 미공개, 진행률만 표시 | 자동 |

등록부에 `VALID`로 적어도 실험이 끝나지 않았거나 분석이 없으면 자동 상태가 우선하고, 경고(`registry_warnings`)가 남는다.

## 3. 새 실험 반영 절차 (예: 최종 발표용 `aah-main-...-v3`)

저장소 루트에서 `PYTHONPATH=src;.`(macOS·Linux는 `src:.`)로 실행한다. 아래 명령은 모두 모델을 호출하지 않는다.

1. **발견 확인.** `results/main-live/*` 아래 새 실험이 보이는지, 상태가 무엇인지 본다.
   ```powershell
   python -m analysis.public_export discover
   ```
2. **(선택) 진행 중 내보내기.** 실험 도중에도 진행률을 공개할 수 있다. 지표와 효과 크기는 표시되지 않는다.
   ```powershell
   python -m analysis.public_export export --experiment aah-main-...-v3
   ```
3. **분석 후 검토 등록.** 프로토콜에 따라 분석(`analysis.main_analysis`)을 마친 뒤, 유효성을 검토하고 `configs/dashboard/experiments.json`에 항목을 추가한다. `label`, `status`, `status_reason`, `analysis_dir`, `freeze`, `approval`, `log_ids`, `report_ref`, `timeline`(단계별 `ref`·`step`·`detail`)을 적는다. 검토 전이면 `status`를 비워 두면 된다(`UNREVIEWED`로 표시).
4. **내보내기.**
   ```powershell
   python -m analysis.public_export export
   ```
   출력은 실험마다 `WRITTEN`(새로 씀), `UPDATED`(진행 중 데이터 갱신), `STATUS_UPDATED`(상태만 변경), `NO_CHANGE` 중 하나다. 확정 데이터를 바꾸려 하면 `PUBLIC_STORE_BLOCKED`로 멈춘다. 그때는 새 실험 ID로 다시 실행·내보낸다.
5. **release 만들기** (발표 시점 고정).
   ```powershell
   python -m analysis.public_export release --id final-presentation-2026-12-16 --label "최종 발표본 (2026-12-16)" --kind presentation `
       --experiments aah-main-...-v3,aah-main-emergency-v2,aah-main-emergency-v1 --primary aah-main-...-v3 `
       --verification docs/submission/data/verification-record.json
   ```
   진행 중 실험은 release에 넣을 수 없다. 대시보드가 이 release를 기본으로 보여 주게 하려면 등록부의 `default_release`를 바꾸고 `export`를 한 번 더 실행해 `catalog.json`을 다시 만든다. 제출본(`submission-2026-11-27`)은 그대로 남아 사이드바에서 선택할 수 있다.
6. **검증.**
   ```powershell
   python -m analysis.public_export verify   # 해시·개인정보·분석 재계산·release 고정·catalog
   python -m analysis.public_export check    # results/에서 다시 내보내 저장본과 대조
   python -m analysis.repository_graphs check   # 과제별 Repository Graph가 공개 과제 원본과 일치하는지 (과제를 추가했다면 build)
   python -m analysis.static_site check      # 정적 페이지가 public_data/와 일치하는지 (export·release가 자동으로 다시 만든다)
   python -m pytest tests/integration/test_public_export.py tests/integration/test_dashboard_app.py -q
   ```
7. **반영 (사용자 승인).** `public_data/`, `docs/dashboard/`, `configs/dashboard/experiments.json`을 커밋하고 배포 브랜치에 푸시한다. GitHub Pages와 Streamlit 앱이 모두 갱신된다. Streamlit은 파일 해시를 캐시 키로 써서 이전 결과가 남지 않고, 정적 페이지는 브라우저 새로고침으로 최신 파일을 받는다.

## 4. 저장소 구조와 스키마

```text
public_data/
  catalog.json                         # 색인: 실험·release·합성 검증 목록, data_updated_at, default_release, 한계 목록
  experiments/<experiment_id>/
    export.json                        # 상태·진행률·실행 설정·조건별 지표·대비·슬롯·run별 정규화 기록·그래프·DCC·파생 통계
    MANIFEST.json                      # exported_at, status_updated_at, data_sha256, content_sha256, 파일 해시, 원천 입력 수·해시, status_history
    run_status.csv, metric_summary.csv, paired_contrasts.csv, analysis_sets.json, analysis.json   # 동결 분석 산출물 (분석 완료 시)
  synthetic/<suite>/export.json, MANIFEST.json   # 합성 검증 (라이브 아님)
  releases/<release_id>.json           # 고정된 실험 data_sha256, 주 실험, 생성 시각, 검증 기록
  repository_graphs/MANIFEST.json, <task>.json   # 과제별 Repository Graph (공개 과제 원본에서 재추출, 실험 시점 기록 아님)
```

- `data_sha256`은 상태 필드를 뺀 데이터와 분석 파일의 해시다. release는 이 값을 고정하므로, 나중에 실험이 무효로 바뀌어도 release 화면에는 현재 상태(무효)가 정직하게 표시된다.
- `catalog.json`은 manifest와 release에서 결정적으로 다시 만들어진다. 같은 입력이면 같은 바이트가 나온다.
- 모든 JSON은 `schema_version: "1.0"`이다. 대시보드는 major 버전이 다른 데이터를 읽지 않고 오류를 표시한다.

## 5. 문제가 생겼을 때

| 상황 | 조치 |
| --- | --- |
| 잘못된 데이터를 내보냈고 아직 커밋 전 | `public_data/experiments/<id>/`를 지우고 다시 `export` |
| 확정 데이터에 오류가 발견됨 | 기존 데이터는 그대로 두고 등록부 상태를 `INVALID_*`로 바꾼 뒤 `export`(상태 이력에 남음). 수정 실험은 새 실험 ID로 실행 |
| 분석 디렉터리가 여러 개 | 등록부 `analysis_dir`로 지정한다. 지정이 없고 후보가 여럿이면 지표를 공개하지 않는 `ANALYSIS_PENDING`으로 두고 경고 `ANALYSIS_AMBIGUOUS_SET_ANALYSIS_DIR_IN_REGISTRY`를 남긴다 |
| 데이터 무결성 오류 화면 | `export.json`을 손으로 고치지 말고 `export`로 다시 만든다 |

## 6. 화면 원본과 제출 고정본

웹 대시보드 화면은 `analysis/dashboard/`(template·app·style)에서 만든다. 제출 시점 오프라인 대시보드(`docs/submission/dashboard.html`)는 `analysis/dashboard/submission-2026-11-27/`의 고정 사본으로만 만들어진다(`submission_bundle.FROZEN_DASHBOARD`). 웹 화면을 고친 뒤에는 `python -m analysis.static_site build`만 다시 실행한다. 고정 사본은 고치지 않는다 — 고치면 `submission_bundle check`가 실패한다.
