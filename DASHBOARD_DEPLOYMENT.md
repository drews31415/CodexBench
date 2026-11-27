# 웹 대시보드 배포 가이드

심사위원이 설치나 Codex 인증 없이 링크만으로 실험 결과를 확인하도록 웹 대시보드를 배포하는 절차다. 두 가지 방법이 있다. "사용자 승인·인증이 필요한 단계"는 계정 소유자가 직접 해야 한다.

**현재 상태 (2026-11-28 확인).** GitHub Pages가 `main` 브랜치의 `/docs`에서 배포 중이다: https://drews31415.github.io/CodexBench/dashboard/ . 배포본은 커밋 `84b839b`의 `docs/dashboard/index.html`과 바이트 단위로 같았다(SHA-256 `48bdc6df…`). 2026-11-28 대시보드 개선본은 아직 푸시하지 않았다 — 푸시는 계정 소유자 승인 대상이다.

| 방법 | 24시간 접근 | 서버 | 권장 용도 |
| --- | --- | --- | --- |
| **A. 정적 페이지 (GitHub Pages)** | 예. 서버가 없어 잠들지 않음 | 없음 | 심사위원용 기본 링크 |
| B. Streamlit Community Cloud | 아니오. 12시간 무방문 시 휴면 | 있음 | 상호작용 차트, 발표용 보조 |

두 방법은 같은 공개 데이터 저장소(`public_data/`)와 release를 쓴다. 표시 수치는 같고, 테스트로 대조한다.

## A. 정적 페이지 (GitHub Pages, 권장)

`docs/dashboard/index.html`은 `public_data/`에서 생성한 단일 HTML 파일이다. 화면 코드, 공개 데이터, 그림이 모두 들어 있고 외부 요청이 없다(CSP `default-src 'none'`). 7개 화면, release·실험 선택, 상태 배너, 마지막 갱신 시각, 심사위원 빠른 탐색, 과제별 Repository Graph 탐색을 제공한다. `python -m analysis.public_export export`·`release`를 실행할 때마다 자동으로 다시 만들어지므로 데이터와 어긋나지 않는다(테스트 `test_committed_site_reproduces_from_the_public_store`).

| 파일 | 역할 |
| --- | --- |
| `docs/dashboard/index.html` | 대시보드 (생성물, 직접 고치지 않음) |
| `docs/index.html` | 사이트 루트에서 `dashboard/`로 이동하는 안내 페이지 |
| `docs/.nojekyll` | GitHub Pages의 Jekyll 처리를 끈다. `docs/` 안 일부 문서에 Liquid 문법(`{{`)이 있어 Jekyll이 빌드를 실패시킬 수 있기 때문이다 |
| `analysis/static_site.py` | 생성기 (`build`, `check`) |
| `analysis/dashboard/template.html`, `app.js`, `style.css` | 웹 대시보드 원본 (개선본) |
| `analysis/dashboard/submission-2026-11-27/` | 제출 시점 대시보드 원본의 고정 사본. `docs/submission/dashboard.html`은 이것으로만 만들어지므로 웹 대시보드를 고쳐도 제출본은 바뀌지 않는다 |
| `public_data/repository_graphs/` | 8개 Main 과제의 Repository Graph. `python -m analysis.repository_graphs build`가 공개 과제 원본(`benchmarks/main_public/*/base`)에서 기존 빌더로 재추출한다(모델 호출 0). 실험 시점 그래프가 아니라고 화면에 표시한다 |

### 배포 절차

1. **푸시 전 확인** (`PYTHONPATH=src;.`)
   ```powershell
   python -m analysis.repository_graphs check    # REPOSITORY_GRAPHS_REPRODUCED
   python -m analysis.static_site check          # STATIC_SITE_REPRODUCED
   python -m analysis.public_export verify       # PUBLIC_STORE_VERIFIED
   python -m pytest tests/integration/test_static_site.py -q   # 헤드리스 Chrome 화면 검사 포함 (Chrome이 없으면 건너뜀)
   ```
2. **GitHub 저장소 생성·푸시 (U1·U2).** `docs/`와 `public_data/`가 커밋에 포함됐는지 확인한다.
3. **Pages 켜기 (U7).** 저장소 Settings → Pages → Build and deployment → Source: *Deploy from a branch* → Branch: 배포 브랜치(예: `main`), Folder: `/docs` → Save.
4. **주소.** `https://<계정>.github.io/<저장소>/` (자동으로 `dashboard/`로 이동) 또는 `https://<계정>.github.io/<저장소>/dashboard/`. 첫 배포에는 몇 분이 걸릴 수 있다. 진행 상황은 Actions 탭의 *pages build and deployment*에서 본다.
5. **확인.** 비공개 창에서 주소를 열고 상단 "마지막 데이터 갱신"과 데이터 기준 `제출본 (2026-11-27)`을 확인한다. 휴대폰 화면에서도 연다.
6. **링크 기입.** `submission.md` §8, `JUDGE_GUIDE.md` §0, `README.md`의 `[GitHub Pages 배포 후 URL 입력]`을 바꾼다.

### 주의

- **공개 범위.** GitHub Pages 사이트는 인터넷에 공개된다. 무료 요금제는 공개 저장소에서 Pages를 쓸 수 있고, 비공개 저장소의 Pages는 요금제에 따라 다르다(Settings → Pages에서 확인). `/docs` 폴더 전체가 게시되므로 `docs/` 안 문서도 함께 공개된다. 대시보드 데이터에는 과제 파일 경로·공개 검사 ID·run_id·결과 수치가 있고, 비공개 검사·정답 패치·에이전트 원문·인증 정보는 없다(U6).
- **갱신.** 배포 브랜치에 푸시하면 Pages가 다시 배포한다. 브라우저 캐시 때문에 이전 화면이 보이면 새로고침한다. 화면 상단의 데이터 SHA-256과 "마지막 데이터 갱신" 시각으로 어느 데이터인지 확인할 수 있다.
- **크기.** 현재 약 1.5MB. 실험이 늘면 실험당 약 0.7MB씩 커진다.
- **주소 형식.** `#<화면>?release=…&exp=…` 뒤에 화면별 선택(`task`·`cond`, `gmode=repo&rtask=…`, `graph=…&gnode=…`, `case=…&step=…`)이 붙는다. 탭 이동은 브라우저 뒤로·앞으로 가기로 되돌릴 수 있다. 없는 사례 ID나 화면을 주면 안내 문구와 함께 대표 사례를 보여 준다.

## B. Streamlit Community Cloud

## 1. 구성

| 파일 | 역할 |
| --- | --- |
| `dashboard/streamlit_app.py` | 진입점 (Main file path) |
| `dashboard/requirements.txt` | 의존성 (`streamlit==1.67.0` 고정). 진입점 옆에 있으므로 Community Cloud가 이 파일을 먼저 쓴다. 루트 `pyproject.toml`(연구 코드용)은 설치되지 않는다 |
| `.streamlit/config.toml` | 저장소 루트 설정 (진입점이 하위 폴더여도 루트에 있어야 함) |
| `dashboard/aah_dashboard/` | 데이터 읽기(`data.py`), 화면 값 계산(`models.py`), 차트(`charts.py`), 화면(`pages.py`) |
| `public_data/` | 버전별 공개 데이터 저장소 (Git으로 관리, 대시보드가 읽는 유일한 데이터) |

**필요 없는 것:** Codex 계정·API 키, Docker, 비공개 평가기, `results/` 원시 기록, 네트워크 요청, Streamlit secrets. 대시보드는 `public_data/`의 JSON만 읽고 모델을 호출하지 않는다. 경로는 모두 `Path(__file__)` 기준 상대 경로이며, 운영체제 의존 코드가 없다(테스트 `test_dashboard_code_is_portable_and_self_contained`).

## 2. 사용자 승인·인증이 필요한 단계

| # | 단계 | 현재 상태 | 해야 할 일 |
| --- | --- | --- | --- |
| U1 | GitHub 저장소 | **없음** (이 작업 사본에는 git remote가 설정돼 있지 않다) | GitHub에 저장소를 만들고 원격을 연결한다 (방법 A·B 공통) |
| U2 | 커밋·푸시 | 하지 않음 | 변경을 검토한 뒤 커밋해 배포 브랜치로 푸시한다. 요청하면 커밋 메시지·명령을 준비한다 |
| U3 | 공개 범위 결정 | 미결정 | 공개 저장소로 할지, 비공개 저장소 + 공개 앱으로 할지 정한다 (아래 §4) |
| U4 | Streamlit Community Cloud 계정 | 미확인 | share.streamlit.io에 GitHub 계정으로 로그인한다. 비공개 저장소라면 GitHub의 `repo` OAuth 권한을 추가로 승인해야 한다 |
| U5 | 앱 생성·공개 설정 | 미실행 | §3 절차대로 앱을 만들고, 심사위원이 로그인 없이 보려면 앱을 공개로 둔다 |
| U7 | GitHub Pages 켜기 (방법 A) | 미실행 | 저장소 Settings → Pages에서 배포 브랜치와 `/docs` 폴더를 지정한다 |
| U6 | 공개 데이터 검토 | 검토 필요 | `public_data/`에 과제 파일 경로·공개 검사 ID·run_id·결과 수치가 들어 있다. 제출처 공개 정책에 맞는지 확인한다. 비공개 검사·정답 패치·에이전트 원문·인증 정보는 들어 있지 않다(자동 검사) |

## 3. 배포 절차

1. **푸시 전 확인** (저장소 루트, `PYTHONPATH=src;.`)
   ```powershell
   python -m analysis.public_export verify      # PUBLIC_STORE_VERIFIED
   python -m pytest tests -q -p no:cacheprovider
   git status                                    # public_data/, dashboard/, .streamlit/ 가 포함됐는지 확인
   ```
2. **푸시 (U1·U2).** 배포에 쓸 브랜치(예: `main`)에 푸시한다. `results/`는 `.gitignore`로 제외돼 올라가지 않는다.
3. **앱 만들기 (U4·U5).** https://share.streamlit.io 에서 *Create app* → *Deploy a public app from GitHub*를 고른다.
   - Repository: 위 저장소 · Branch: 배포 브랜치 · Main file path: `dashboard/streamlit_app.py`
   - App URL: 원하는 하위 도메인 (예: `aah-results`). 비우면 `https://[계정]-[저장소]-[경로]-[브랜치]-[해시].streamlit.app` 형식이 된다(브랜치가 main/master면 브랜치명 생략)
   - **Advanced settings → Python 3.12** (로컬·Linux 검증 버전과 같게. 기본값도 3.12). Python 버전은 배포 뒤 바꿀 수 없고, 바꾸려면 앱을 지우고 다시 배포해야 한다
   - Secrets: 비워 둔다
4. **배포 확인.** 브라우저의 비공개 창(로그인하지 않은 상태)에서 URL을 열고 다음을 본다.
   - 사이드바 하단 "마지막 데이터 갱신" 시각, 데이터 기준 `제출본 (2026-11-27)`, 실험 `Main v2 · 유효 · 탐색적`
   - 7개 화면이 오류 없이 열리는지, 휴대폰 화면에서 사이드바(`>>`)로 화면을 바꿀 수 있는지
   - 바로 가기 예: `https://<앱 주소>/?page=replay&case=aah-main-emergency-v2%7C6dbae23e-f630-4457-9fb3-6f7da060cc87`
5. **제출서에 링크 기입.** `submission.md` §8의 `[배포 후 URL 입력]`을 실제 주소로 바꾼다.

## 4. 공개 범위 (공식 문서 기준)

- 공개 앱은 URL을 아는 사람이면 볼 수 있다. 문서에 "링크를 주면 볼 수 있다"고 되어 있으나, 로그인 불필요 여부는 명시돼 있지 않다. 배포 후 비공개 창에서 직접 확인한다.
- 비공개 저장소에서 배포한 앱은 기본적으로 워크스페이스 개발자에게만 보인다. 초대된 사람은 Google 계정 또는 이메일 링크로 로그인해야 하고, 비공개 앱은 한 번에 하나만 둘 수 있다.
- 따라서 심사위원이 로그인 없이 보려면 (a) 공개 저장소에서 배포하거나 (b) 비공개 저장소에서 배포한 뒤 앱을 공개로 바꾼다. 어느 쪽이든 대시보드에 담긴 공개 데이터가 외부에 노출된다(U6).

## 5. 자동 갱신과 운영 특성

| 항목 | 동작 | 출처 |
| --- | --- | --- |
| 푸시 반영 | 배포 브랜치에 푸시하면 앱에 거의 실시간으로 반영된다. GitHub 갱신은 분당 최대 5회로 제한된다 | manage-your-app, status |
| 의존성 변경 | `dashboard/requirements.txt`를 바꾸면 전체 재배포·재설치가 자동으로 일어난다 | manage-your-app, app-dependencies |
| 데이터 캐시 | 대시보드는 파일 내용의 SHA-256을 키로 캐시한다. 새 데이터가 푸시되면 같은 서버 프로세스에서도 바로 새 값이 보인다. 데이터 파일과 MANIFEST 해시가 다르면 오래된 값 대신 무결성 오류를 표시한다 | `dashboard/aah_dashboard/data.py`, 테스트 `test_cache_shows_changed_data_without_a_restart` |
| 휴면 | 12시간 동안 방문이 없으면 앱이 잠든다. 볼 권한이 있는 누구나 "Yes, get this app back up!"으로 깨울 수 있다. 심사 직전에 한 번 열어 두기를 권장한다 | manage-your-app |
| 자원 한도 | 2024년 2월 기준 메모리 690MB–2.7GB. 공개 데이터는 약 1.3MB라 여유가 있다 | manage-your-app |
| 고정 설정 | Community Cloud가 일부 설정(`client.showErrorDetails=false` 등)을 덮어쓴다. 오류 세부 정보는 숨겨지고, 대시보드 자체 오류 메시지만 보인다 | status |

출처는 2026-11-27에 확인한 https://docs.streamlit.io/deploy/streamlit-community-cloud 하위 문서다 (deploy-your-app, file-organization, app-dependencies, deploy, manage-your-app, upgrade-python, share-your-app, status). 정책은 바뀔 수 있으므로 배포 시점에 다시 확인한다.

## 6. 로컬 실행

```powershell
python -m venv .venv                      # .venv/는 .gitignore에 있다
.venv\Scripts\python -m pip install -r dashboard/requirements.txt
.venv\Scripts\python -m streamlit run dashboard/streamlit_app.py --server.address 127.0.0.1
```

macOS·Linux는 `.venv/bin/python`을 쓴다. `--server.address 127.0.0.1`은 같은 네트워크의 다른 기기에 열리지 않도록 한다. 다른 데이터 저장소로 시험하려면 환경 변수 `AAH_PUBLIC_DATA`에 경로를 지정한다.

## 7. 검증 기록 (배포 전, 모델 호출 0)

| 검증 | 결과 |
| --- | --- |
| 로컬 실행 (Windows 11, Python 3.12, Streamlit 1.67.0) | 서버 기동, 7개 화면 렌더링, 헤드리스 Chrome으로 데스크톱(1366px)·모바일(390px) 화면 확인 |
| 클라우드 환경 호환성 (Docker `python:3.12-slim`, Linux x86_64, `results/` 없음) | `dashboard/requirements.txt` 설치 후 대시보드·내보내기·제출 묶음 테스트 31개 통과(원시 기록·Node 필요 4개 건너뜀), `public_export verify`·`verify-public` 통과, `/_stcore/health` 응답 `ok` |
| 화면 테스트 (`streamlit.testing.v1.AppTest`) | 모든 화면, 그래프 진단 4건, Replay 사례 14건의 첫·마지막 단계, 실행 추적·컨텍스트 40개 조합, 무효 실험 표시, release 전환, 진행 중 실험, 데이터 없음 처리 |
| 데이터 | `python -m analysis.public_export verify` → `PUBLIC_STORE_VERIFIED`, `check` → `PUBLIC_STORE_REPRODUCED` |

| 정적 페이지 실제 브라우저 검사 (2026-11-28, `tests/integration/dashboard_browser.js`) | 헤드리스 Chrome으로 데스크톱(1366px)·모바일(390px) × 라이트·다크, 9개 화면과 8개 Repository Graph·12개 진단 그래프를 렌더링. 가로 넘침·글자 겹침·노드 밖 글자·노드 겹침·CSP 위반·네트워크 요청·콘솔 오류 0, 탭·뒤로가기·키보드·Replay 재생/일시정지·그래프 확대/필터/선택·복사·잘못된 주소 처리 확인 |

**미확인:** 실제 Streamlit Community Cloud 배포와 공개 URL 접근(U4–U5), Streamlit 버전의 2026-11-28 화면 개선 반영(정적 페이지만 개선), Chrome 외 브라우저.

## 8. 문제 해결

| 증상 | 원인과 조치 |
| --- | --- |
| "공개 데이터를 읽지 못했다" | 배포 브랜치에 `public_data/catalog.json`이 없다. `public_data/`를 커밋했는지 확인한다 |
| "무결성 오류" | `export.json`과 `MANIFEST.json`의 해시가 다르다. 손으로 고치지 말고 `python -m analysis.public_export export`로 다시 만든다 |
| "release가 고정한 데이터와 … 다르다" | release 이후 실험 데이터가 바뀌었다. 확정 데이터는 덮어쓸 수 없게 막혀 있으므로, `--allow-update`를 쓴 경우 원인을 확인한다 |
| 의존성 설치 실패 | `dashboard/requirements.txt`의 고정 버전을 확인한다. 루트 `pyproject.toml`은 대시보드 배포에 쓰이지 않는다 |
| 앱이 잠듦 | 휴면 화면의 깨우기 버튼을 누른다 (§5) |
