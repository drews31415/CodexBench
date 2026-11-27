# Adaptive Agent Harness (AAH)

**그래프 기반 실패 복구 및 동적 컨텍스트 최적화를 통한 코딩 에이전트의 신뢰성과 실행 효율 향상**

*Graph-Guided Failure Recovery and Dynamic Context Optimization for Coding Agents*

잇츠 스터디 5기 · Codex 기반 AI 코딩 에이전트 구축

> **Project status — G0·G1 통과 · S5 Dev Pilot 25/25 평가 · G2 사전 검증** (2026-11-27)
>
> DEV-01에서 실제 Codex 실행과 독립 Docker 판정을 확인했습니다. Dev 런타임·격리 검증과 한시 Dev Smoke 공동 승인 확인을 기록해 G0를 통과했습니다. [G0 재판정](docs/reports/S0_VERIFICATION.md#g0-재판정)
>
> S1에서는 A/B 합성 실행, 조건 A의 실제 Codex 턴, 공개 보고서 조회 및 독립 Docker 판정까지 확인했습니다. 실제 DEV-01의 과제 판정은 `FAIL`입니다. [S1 재현 및 검증 범위](docs/reports/S1_VERIFICATION.md)
>
> S2에서는 공통 후보 수집과 동적 컨텍스트 구성기를 연결하고 조건 B/C의 합성 실행을 검증했습니다. [S2 검증 기록](docs/reports/S2_VERIFICATION.md)
>
> S3에서는 조건 D가 공개 검사와 코드 사이의 경로로 복구 대상을 선택하고 다음 턴에 전달하는 과정을 합성 실행으로 확인했습니다. [S3 검증 기록](docs/reports/S3_VERIFICATION.md)
>
> S4에서는 실제 DEV-01의 첫 턴 실패 후 E가 복구를 전달하는 과정을 확인했고, A–E 다섯 실행의 독립 Docker 판정을 검증했습니다. LT02와 LT04가 통과했습니다. 합성 체크포인트에서 B–E가 최초 턴을 반복하지 않고 분기하는 것도 확인했습니다. [S4 검증 기록](docs/reports/S4_VERIFICATION.md)
>
> S5 Dev Pilot은 과제 5개에서 A–E 25슬롯을 모두 실행했습니다. 독립 Docker 평가는 18건 PASS, 7건 FAIL이었고 실제 후속 복구 턴 8건을 확인했습니다. [S5 Pilot 실환경 결과](docs/reports/S5_PILOT_PREPARATION.md#pilot-실환경-결과)
>
> G2 검토용으로 Main 후보 8개의 공개 기준본과 기능·회귀 검사를 검증했습니다. 독립 평가와 비작성자 리뷰를 마친 Main 후보는 0개입니다. [G2 사전 검증](docs/reports/G2_PREPARATION.md)

[프로젝트 소개](#프로젝트-소개) · [아키텍처](#아키텍처) · [사용-계획](#사용-계획) · [실험-설계](#실험-설계) · [개발-및-기여](#개발-및-기여) · [문서-인덱스](#문서-인덱스)

## 프로젝트 소개

Adaptive Agent Harness(AAH)는 OpenAI Codex의 실행을 관측하고, 실패 또는 정체가 발생했을 때 적절한 후속 전략과 컨텍스트를 구성하는 외부 실행 하네스입니다. 단일 Codex 에이전트와 Python 저장소의 테스트 가능한 버그 수정 작업을 대상으로 합니다.

코딩 에이전트가 반복적으로 같은 테스트 오류를 만나거나 코드 의존성을 놓치는 경우, 단순히 더 많은 정보를 전달하거나 재시도 기회를 추가하는 것만으로 충분한지 알기 어렵습니다. 불필요하거나 오래된 개발 안내 역시 추가 비용을 유발할 수 있습니다. AAH는 이러한 상황이 실제로 얼마나 발생하는지 확인하고, 다음 두 정책의 효과를 검증합니다.

- **Graph-guided recovery:** 코드 구조와 관측된 실행 증거를 연결하고, 실패 관련 관계를 탐색하여 후속 복구 전략을 선택합니다.
- **Dynamic Context Compiler (DCC):** 최초 작업과 복구 전략에 필요한 보조 정보를 관련성, 적용 범위, 유효성, 중복 및 토큰 예산에 따라 구성합니다.

모델의 가중치나 내부 추론을 변경하지 않습니다. Codex의 기본 저장소 탐색 및 도구 사용을 유지하고, 공개적으로 지원되는 실행 단위에서만 개입합니다. 정상적으로 진행하는 작업에는 불필요한 복구 지침을 추가하지 않습니다.

**핵심 연구 질문:** 동일한 모델, 접근 가능한 증거 및 실행 제한에서 그래프 기반 복구와 상태 적응형 컨텍스트 최적화는 각각 어떤 추가 효과를 가지며, 두 방법의 결합은 일반 재시도보다 효과적인가?

### 대상 사용자와 결과물

| 사용자 | 제공할 가치 |
| --- | --- |
| Codex를 사용하는 개발자 | 기존 Python 프로젝트의 버그 수정 요청을 실행하고, 실패 후 복구를 지원하며, 패치와 공개 검사 결과 및 비용을 검토 |
| 에이전트 연구자 및 개발자 | 동일 과제에서 여러 실행 정책을 비교하고, 실패 증거·개입 근거·컨텍스트 선택·비용을 분석 |
| 스터디 팀 | 최신 에이전트 설계 방법을 구현하고 통제 실험을 통해 유용성과 한계를 확인 |

목표 결과물은 **CLI 기반 개발 도구 + 재현 가능한 코딩 에이전트 평가 시스템**입니다. 단순한 벤치마크 점수 비교를 넘어 실제 정책을 구현하되, 개선 효과가 없는 결과도 그대로 기록합니다.

## 주요 기능

모든 항목은 현재 설계된 기능이며, 완료 여부는 구현 및 테스트 증거로 확인합니다.

| 기능 | 설명 | 우선순위 |
| --- | --- | --- |
| Codex Runtime & Observer | 격리 작업 공간에서 Codex를 실행하고 공개 이벤트, diff, 검사 결과, 사용량을 수집 | P0 · F01–F02 |
| Initial Context Compiler | 최초 실행 전 작업별 보조 안내를 선택하고 필수 지침을 보존 | P0 · F03 |
| Coupled Graphs | Repository Graph, Execution Graph 및 타입이 있는 Cross-Graph Mapping 구축 | P0 · F04–F06 |
| Failure Detector | 공통 규칙에 따라 반복 실패, 탐색 정체, 회귀 등 개입 신호 감지 | P0 · F07 |
| Recovery Controller | 실패 관련 증거를 탐색하고 유형별 복구 전략 또는 안전한 대체 동작 선택 | P0 · F08 |
| Recovery Context Compiler | 실패 증거와 전략에 필요한 보조 정보를 제한된 예산으로 재구성 | P0 · F09 |
| Bounded Recovery Loop | 후속 턴 전달, 재관측, 종료 및 예산 관리 | P0 · F10 |
| Run Report | 최종 패치, 공개 검사, 종료 이유, 선택 근거, 관측 비용 및 경고 출력 | P0 · F11 |
| Experiment & Evaluation | A–E 비교, 공통 실패 체크포인트, 독립 평가 및 집계 | P0 · F12 |
| Visualization & Extended Ablation | 실제 로그 기반 사례 시각화 및 세부 정책 효과 분석 | P1 · F13–F14 |

P0는 핵심 구현 및 검증 범위, P1은 핵심 시스템을 확보한 이후 수행할 권장 범위입니다. 세부 수용 조건은 [프로젝트 설명서](docs/project/PROJECT_OVERVIEW.md)와 [기술 명세서](docs/design/TECHNICAL_SPEC.md)를 따릅니다.

## 아키텍처

아래는 **제안법 E(Adaptive Agent Harness)**의 개념적 흐름입니다. 모든 비교군에 그래프나 DCC를 적용하는 것은 아닙니다. 실제 정책 구성과 상태 전이는 기술 명세를 따릅니다.

```mermaid
flowchart TD
    Q["작업 요청 · 저장소 · 필수 지침"] --> W["Workflow Orchestrator / Workspace / Budget"]
    W --> IC["Initial DCC"]
    IC --> X["Codex Runtime Adapter"]
    X --> O["Observer · 공개 검사"]
    O --> L["Evidence Ledger · Snapshot"]
    L --> T{"안전 중단 · 전역 한도 · 지속 불가?"}
    T -->|Yes| P["최종 패치 고정 · Run Report"]
    T -->|No| F{"공통 개입 조건 충족?"}
    F -->|No| N{"정상 완료 또는 지원되는 지속?"}
    N -->|지속| X
    N -->|완료 또는 종료| P
    F -->|Yes| EG["Execution Graph"]
    F -->|Yes| RG["Repository Graph"]
    EG --> CG["Cross-Graph Mapping"]
    RG --> CG
    CG --> D
    D --> S["Recovery Controller / 전략 선택"]
    S --> RC["Recovery DCC"]
    RC --> X
    P -->|별도 연구 실행기| EV["Independent Evaluator"]
    EV --> A["Metrics · Analysis"]
    classDef context fill:#e3f2eb,stroke:#37836b,color:#184b3d
    classDef graph fill:#e7edf8,stroke:#536f9d,color:#283e63
    class IC,RC context
    class EG,RG,CG,D,S graph
```

독립 평가기는 최종 패치가 고정된 이후에만 동작합니다. 평가 결과나 hidden test는 복구 루프로 되돌아가지 않습니다. 실제 실행에는 안전 종료, 근거 부족, fallback 및 잔여 예산 검사도 포함합니다.

### 1. 그래프 기반 상태 표현 및 복구

| 구성 요소 | 역할 |
| --- | --- |
| Workflow Graph | 외부 실행 흐름의 상태 전이, 조건 분기, 반복 및 종료를 제어 |
| Repository Graph | 파일, 함수, 클래스, 테스트와 추출 가능한 코드 의존성을 표현 |
| Execution Graph | 관측된 탐색, 수정, 명령 실행, 테스트 실패 및 재시도를 표현 |
| Cross-Graph Mapping | 실행 이벤트와 당시 코드 스냅샷의 요소를 타입 있는 관계로 연결 |

Workflow Graph는 제어 구조이고, Repository Graph와 Execution Graph는 의사결정에 활용하는 정보 구조입니다. 실패 지문에서 관련 코드와 실행 이력을 제한된 범위로 탐색한 뒤, 원인 후보 및 후속 행동을 구성합니다. 관측된 관계와 추정된 관계를 구분하며, 관계 탐색 결과를 확정적인 인과관계로 해석하지 않습니다.

실패 감지는 B–E의 공통 정책입니다. 그래프는 실패 감지 이후의 진단과 복구 전략 선택에 사용하여 비교군 간 개입 기회가 달라지지 않도록 합니다.

### 2. Dynamic Context Compiler

DCC는 필수 지침을 제외한 선별 가능한 보조 정보에 적용됩니다. 작업이나 실패 상태에 따라 후보를 수집하고, 출처·유효성 검사, 관련성 평가, 중복 제거 및 예산 기반 패키징을 수행합니다.

| 단계 | 입력 | 출력 |
| --- | --- | --- |
| Initial DCC | 작업 요청, 초기 저장소, 보조 안내 | 작업별 보조 컨텍스트 |
| Recovery DCC | 작업 요청, 현재 저장소, 허용된 실패 증거, 복구 전략 | 후속 실행에 필요한 보조 컨텍스트 |

초기 DCC는 그래프 기반 실패 정보를 사용하지 않습니다. Context-only는 평면 증거를, 통합 제안법은 그래프 기반 진단 증거를 활용합니다. 전체 프롬프트나 Codex 내부 컨텍스트를 임의로 삭제하거나 통제한다는 의미가 아닙니다.

### 3. 피드백 기반 실행

실행 후 관측과 공개 검사를 기록하고 안전·전역 한도와 지속 가능성을 먼저 판단합니다. B–E에서는 정상 완료 처리 전에 유효 트리거와 개입 게이트를 확인합니다. 완료 선언과 공개 필수 검사 실패가 함께 관측되면 T4를 검토합니다. 외부 개입 상한이 소진돼도 지원되는 정상 지속은 가능합니다. 독립 평가는 Run Report 반환 후 실험 실행기가 요청합니다.

Harness Engineering은 전체 실행 환경, Graph Engineering은 관계 기반 진단, Context Engineering은 정보 선택, Loop Engineering은 피드백 및 종료 정책, Prompt Engineering은 구체적인 지침 전달이라는 역할을 담당합니다. 이러한 명칭을 모두 사용하기 위해 기능을 추가하지 않으며, 보편적으로 확립된 학술적 포함 관계를 주장하지 않습니다.

상세 알고리즘과 설계는 [METHODOLOGY.md](docs/research/METHODOLOGY.md), [SYSTEM_ARCHITECTURE.md](docs/design/SYSTEM_ARCHITECTURE.md)를 참고하세요.

## 사용 계획

### 현재 실행 가능한 S0 명령

Python 3.11 이상이 필요합니다. 저장소 루트에서 다음 PowerShell 명령을 실행합니다. 평가기 검증은 공개 Dev 두 과제의 초기 상태와 참조 상태를 모두 검사합니다. 출력은 새 경로에 저장합니다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m aah probe --config configs/s0-probe.json --output results/probe-01.json
python -m benchmarks.smoke.prepare --task DEV-01 --output results/dev-01-inputs.zip
python -m evaluation.smoke --output-dir results/dev-fixtures-01
python -m pytest tests/unit tests/contract
python -m pytest tests/integration tests/isolation
```

Dev 입력 ZIP에는 작업 사본, 최초 메시지 원문, 운영자용 해시 기록이 들어 있습니다. 준비 명령은 모델 호출을 시작하지 않습니다. Docker 연결 확인과 한시 실행 프로파일 초안은 [S0 검증 기록](docs/reports/S0_VERIFICATION.md#dev-01-첫-실행-준비)에 있습니다.

빌드 도구(setuptools 68 이상과 wheel)가 준비된 가상환경에서는 `python -m pip install --no-index --no-build-isolation --no-deps .`로 설치할 수 있습니다. 설치 후에는 `adaptive-codex probe --config configs/s0-probe.json`을 실행합니다. 평가기는 저장소에서 별도 명령으로 실행합니다. `probe`의 종료 코드 0은 로컬 CLI 메타데이터 수집 성공을 뜻합니다. 개별 RT 항목과 G0의 상태는 보고서에서 확인합니다.

### 지원 환경 및 설치 전제 (계획)

- Python 3.11 이상, Git, 의존성이 고정된 실행 환경
- 지원되는 Codex CLI 및 인증 환경 (SDK는 대안)
- 사용자 승인 공개 검사 명령과 실행 제한
- 안전성이 검증된 격리 작업 공간 및 별도 평가 환경

실제 모델 실행에 쓸 Python·Codex 버전, 인증·권한 프로파일과 제한 값은 Dev Smoke 승인 시 기록합니다.

### 일반 사용자 모드 (S1 이후 계획)

대상 저장소와 개발 요청을 전달합니다. 기본 정책은 E이며, 설정에는 모델, 작업 공간, 공개 검사, 시간·턴·복구 횟수 및 보조 컨텍스트 제한을 명시합니다.

```bash
# Planned CLI — not yet verified
adaptive-codex run \
  --repo ./my-project \
  --task "로그인 실패 횟수에 따른 계정 잠금 오류 수정" \
  --config configs/default.yaml
```

예상 사용 흐름은 대상 저장소 선택 → 작업 요청 및 실행 제한 확인 → 격리 실행 → 조건부 실패 복구 → 최종 패치 및 보고서 검토입니다. 변경 사항의 실제 적용, 커밋 또는 푸시는 사용자 검토 후 별도로 수행합니다.

### 연구 모드 (후속 단계 계획)

```bash
# Planned CLI — not yet verified
adaptive-codex experiment --manifest configs/experiments/main.yaml
adaptive-codex report --run-id <run-id> --results-root results
```

`<run-id>`는 실제 실행 ID로 바꿉니다. `probe`에서 외부 호출이 필요한 경우 승인된 환경과 fixture를 사용해야 합니다. `experiment`는 검증 및 동결된 manifest로 실행하는 연구용 명령이며, `report`는 모델 재호출 없이 기존 결과를 조회합니다. 고정된 본실험 조건의 임의 override는 금지합니다.

### 실행 보고서 (예정)

| 산출물 | 내용 |
| --- | --- |
| `manifest.json` | 실행·과제·조건 ID, 환경 및 설정 해시, 실행 제한 |
| `evidence.jsonl`, `checks.jsonl` | 관측된 실행 증거와 공개 검사 결과 |
| `transitions.jsonl`, `interventions.jsonl` | 상태 전이, 개입 근거, 전략 및 fallback |
| `graphs/`, `context.jsonl` | 그래프, 진단 및 컨텍스트 선택 근거 |
| `usage.jsonl` | 관측 가능한 모델 사용량과 집계 근거 |
| `final.patch`, `report.json` | 최종 패치, 공개 검증, 종료 사유 및 사용량·경고 |

원시 payload와 민감한 로그는 별도 접근 제한을 적용합니다. 일반 사용자 보고서에는 private 평가 결과를 포함하지 않습니다.

### 예시: 계정 잠금 오류 복구

아래는 기능을 설명하기 위한 가상 사례이며 실제 실행 기록이 아닙니다.

1. 초기 DCC가 인증 정책과 관련 테스트 위치 등의 보조 안내를 선별합니다.
2. Codex가 `auth.py`를 수정하지만 공개 테스트의 동일 오류가 반복됩니다.
3. 공통 감지 정책이 개입 조건을 충족한 것으로 판단합니다.
4. 연결된 그래프에서 실패 테스트와 수정 이력 및 의존성을 탐색하여 `session.py`를 추가 조사 후보로 찾습니다.
5. 복구 컨트롤러가 기존 인증 함수 반복 수정 대신 세션 갱신 로직을 검토하는 전략을 선택합니다.
6. 복구 DCC가 해당 코드, 실패 로그, 이전 시도, 유효한 보조 안내를 패키징하여 후속 턴에 전달합니다.
7. Codex의 후속 실행을 관측하고 최종 패치 및 공개 검사 결과를 반환합니다. 연구 모드에서는 독립 평가 결과를 별도로 기록합니다.

실제 원인이나 성공 여부는 평가로 확인해야 합니다. 단순한 작업에서는 외부 복구 없이 종료할 수도 있습니다.

## 실험 설계

### 연구 질문

| ID | 검증 질문 |
| --- | --- |
| RQ1 | DCC가 성공률과 필수 지침 준수의 실질적인 저하 없이 성공당 비용을 줄이는가? |
| RQ2 | 그래프 기반 복구가 동일한 실패 상태에서 일반 재시도보다 높은 복구율을 보이는가? |
| RQ3 | 두 정책의 결합이 단독 정책보다 추가적인 성능 또는 효율 개선을 제공하는가? |
| RQ4 | 그래프 간 연결, 유형별 전략 및 세부 DCC 정책의 추가 효과는 무엇인가? |
| RQ5 | 어떤 실패 및 작업 유형에서 효과가 나타나며 잘못된 개입의 부작용은 무엇인가? |

### 비교군

| 조건 | 최초 보조 정보 | 진단 | 복구 전략 | 복구 정보 |
| --- | --- | --- | --- | --- |
| A — Vanilla Codex | NONE | NONE | NATIVE | NONE |
| B — Flat Retry | DEFAULT | FLAT | GENERIC | DEFAULT |
| C — Context-only | DCC | FLAT | GENERIC | DCC |
| D — Graph-only | DEFAULT | GRAPH | TYPED | DEFAULT |
| E — Adaptive Agent Harness | DCC | GRAPH | TYPED | DCC |

A는 외부 보조 정보 및 복구 지침이 없는 기본 Codex 참조군입니다. B–E는 2×2 요인 설계를 구성합니다. B는 관련 공개 증거를 제공하는 강한 평면 재시도 기준선으로, 단순한 빈 프롬프트 재시도가 아닙니다. D는 그래프 진단 순위를 사용할 수 있지만 DCC 전용 최적화는 사용하지 않습니다. C는 그래프 파생 정보를 사용하지 않습니다.

| | Graph OFF | Graph ON |
| --- | --- | --- |
| Context OFF | B | D |
| Context ON | C | E |

B–E에서 감지 조건, 복구 기회, 허용 원천 증거, 필수 지침, 모델 및 최대 실행 제한을 통제합니다. 같은 초기 입력에서 B/D와 C/E의 초기 컨텍스트 패키지는 각각 동일해야 합니다.

### 평가 프로토콜

- **End-to-end:** 초기 작업부터 최종 패치까지 A–E 전체 성능과 비용 비교.
- **Common Failure Checkpoint:** 동일한 외부 실패 스냅샷, 공개 증거 및 잔여 예산에서 B–E로 분기. 모델의 비공개 상태를 완전히 복제한다고 가정하지 않으며 지원되는 공통 재개 방식을 사용.
- **Ablation:** AB1 그래프 연결, AB2 유형별 전략, AB3 DCC 적용 시점, AB4 그래프/평면 검색을 우선 검토. 추가 실험은 예산과 확보된 실패 상태에 따라 수행.

### 벤치마크 및 규모 (잠정)

| 구분 | 계획 |
| --- | --- |
| Smoke | 1–2개 개발용 과제 |
| Dev/Pilot | 5–8개 개발용 과제 × 5조건, 25–40회 기본 실행 |
| Main | 20–24개 평가 과제 × 5조건 × 2회, 200–240회 |
| 자체 과제 목표 (24개 Main 기준) | AAH-Suite 16개: R-AUTH, R-ORDER, R-INGEST, R-SCHED |
| 공개 과제 목표 | Python 과제 8개: SWE-Bench Pro V2 우선 검토, SWE-bench-Live 대안 |
| 체크포인트 | 최대 24개 source 시도, 목표 12개 자연 발생 실패 × 4조건 = 목표 48회 분기 |
| P1 ablation | AB1–AB4 전체 별도 실행 시 80회 추가 계획 |

24개 Main 기준 본실험 240회, 체크포인트 확보 최대 24회, 분기 목표 48회, AB1–AB4 80회로 총 392회 계획이며 smoke, pilot 및 개발 호출은 별도입니다. 실행 수는 모델 호출 수나 크레딧과 동일하지 않습니다. 예비실험에서 실제 비용과 적격 실패 발생률을 확인하고 평가 결과 열람 전에 규모를 확정합니다. 공개 과제의 instance ID, 라이선스, 실행 가능성은 미확정입니다. 공식 벤치마크와 다른 프로토콜을 사용한 경우 공식 리더보드 점수로 보고하지 않습니다.

### 평가 지표

| 지표 | 의미 |
| --- | --- |
| Task Success Rate | 최종 기능·회귀·필수 제약을 모두 통과한 비율. 주 분석은 과제 내 유효 반복 평균을 과제별 동등 가중으로 집계 |
| Failure Recovery Rate | 대비별 공통 유효 checkpoint 분기 중 최종 독립 PASS 비율. 적격·미확보·결측은 별도 집계 |
| Cost per Success | 유효 성공 및 실패 실행을 포함한 비용 / 성공 건수 (회계 범위 명시) |
| End-to-End Token Usage | 초기 및 복구 실행과 추가 모델 호출의 관측 토큰 |
| Runtime Wall Time / Local Overhead | 요청 검증부터 패치 고정 또는 안전 종료까지 및 로컬 처리 시간. 복원·독립 평가 시간은 별도 |
| Instruction Compliance | 검증 가능한 필수 지침 준수율 |
| Mechanism metrics | 도구 호출, 반복 탐색, 개입·fallback, 전략 수행, 악화 관측 등 |

과제 및 Task Family 단위 paired 비교와 cluster bootstrap(계획값 10,000회, 95% 신뢰구간), 2×2 요인 효과와 상호작용을 분석합니다. 부족한 표본이나 결측이 있는 경우 불확실성을 명시하며, 통계적 비유의성을 성능 유지의 증거로 해석하지 않습니다. 토큰·실제 과금·추정 비용·구독 크레딧은 서로 구분합니다.

### 연구 차별성 및 한계

기존 연구에도 그래프 기반 컨텍스트 제어, 코드와 실행 이력 연결, 실패 진단 및 개입 방법이 존재합니다. [선행연구 분석서](docs/research/LITERATURE_REVIEW.md)는 MemTrace, AgentTether, Rivet, PROBE, CodeTracer 등의 관련성을 검토하고 있습니다. AAH는 해당 방법의 학술적 최초성을 주장하지 않고, **실패에 조건화된 전략 선택과 컨텍스트 정책의 독립·결합 효과를 실제 Codex 환경에서 검증하는 시스템 연구**로 범위를 설정합니다. 관련 선행 방법의 직접 재현·비교는 별도 검증이 필요합니다.

현재 모델의 성공률이 높으면 개선 여지가 작을 수 있으며, 정적 분석의 불완전성, 이벤트 관측 범위, 오개입, 처리 오버헤드, 데이터 오염 및 제한된 표본이 결과에 영향을 줄 수 있습니다. 이를 실패 사례 및 비용과 함께 보고합니다.

실험 및 평가의 세부 규칙은 [EXPERIMENT_DESIGN.md](docs/evaluation/EXPERIMENT_DESIGN.md), [BENCHMARK_SPEC.md](docs/evaluation/BENCHMARK_SPEC.md), [EVALUATION_PROTOCOL.md](docs/evaluation/EVALUATION_PROTOCOL.md)를 참고하세요.

## 연구 진행 상태

현재 S0 개발 검증을 진행 중입니다. 다음 표는 향후 실험에서 측정할 지표를 표시합니다.

| 지표 | A | B | C | D | E |
| --- | --- | --- | --- | --- | --- |
| Main Task Success Rate | — | — | — | — | — |
| Checkpoint Recovery Rate | N/A | — | — | — | — |
| End-to-end Cost per Success (tokens) | — | — | — | — | — |
| Runtime Wall Time | — | — | — | — | — |
| Instruction Compliance | — | — | — | — | — |

`—`는 미측정, `N/A`는 해당 프로토콜에 배정되지 않음을 뜻합니다. 0이나 실패를 의미하지 않습니다. 추후 고정된 실험 manifest, 유효·결측 표본 수, 비용 회계 범위, 신뢰구간과 함께 실제 결과를 추가합니다.

| 게이트 | 승인 목표 | 현재 확인 상태 |
| --- | --- | --- |
| G0 | 실제 Codex 실행, runtime probe, 격리, 독립 평가 | 통과 · [판정과 근거](docs/reports/S0_VERIFICATION.md#g0-재판정) |
| G1 | 통합 MVP 및 A–E 정책 격리 | 통과 · [실행 및 승인 기록](docs/reports/S4_VERIFICATION.md#증거-경로-및-g1-상태) |
| G2 | Pilot, 과제 품질 검증 및 실험 동결 | Main 공개 기준본 8개 검증 · 독립 평가, 리뷰 및 동결 대기 |
| G3 | 본실험 및 체크포인트 기록 확보 | 미실행 |
| G4 | 분석, 비용 회계 및 한계 검토 | 미실행 |
| G5 | 제출 패키지, 실제 사례 replay 및 재현 안내 | 미완료 |

현재의 문서 준비 상태는 게이트 통과를 의미하지 않습니다. 구현 증거와 평가 결과를 확보한 후 갱신합니다.

## 저장소 구조

아래는 전체 단계의 목표 구조입니다. S0에서 구현한 경로와 재현 방법은 위 사용 계획과 검증 기록에 있습니다.

```text
adaptive-agent-harness/
├── README.md
├── AGENTS.md
├── pyproject.toml
├── docs/                       # 연구·설계 문서의 최종 배치 후보
├── configs/
│   ├── default.yaml
│   ├── pilot.yaml
│   └── experiments/
├── src/aah/
│   ├── cli/                    # CLI와 설정
│   ├── domain/                 # 공통 타입·정책·오류
│   ├── workflow/               # 상태 머신 및 예산
│   ├── runtime/                # Codex Adapter 및 관측
│   ├── workspace/              # 격리·스냅샷·패치
│   ├── evidence/               # 공통 실행 증거
│   ├── graphs/                 # 코드·실행 그래프 및 연결
│   ├── diagnosis/              # 실패 감지·진단
│   ├── recovery/               # 복구 전략
│   ├── context/                # DEFAULT / DCC
│   └── reporting/              # 저장 및 보고
├── experiments/                # 비교 실행 및 checkpoint
├── evaluation/                 # 독립 평가기 코드
├── benchmarks/                 # 공개 및 개발용 fixture만
├── analysis/                   # 집계·통계·시각화
├── tests/                      # unit / contract / integration / isolation / live / fixtures
└── results/                    # Git 제외 및 접근 제한
```

루트의 README·AGENTS와 `docs/{research,design,evaluation,project,reports}`에서 관련 문서를 찾을 수 있습니다. 평가 제한 자료는 별도 권한 경계에서 관리합니다.

### 기술 스택 (후보)

| 영역 | 설계 기준 |
| --- | --- |
| Language / Packaging | Python 3.11+, `pyproject.toml`, src layout |
| Runtime | Codex CLI 우선 검증, SDK 대안 |
| Workflow | Python 상태 머신 |
| Graph | Python AST, NetworkX MultiDiGraph |
| Context | BM25, 메타데이터 규칙, Greedy Packing |
| Workspace | Git 및 검증된 격리 환경 (Docker 후보) |
| Artifacts | JSON, JSONL, CSV |
| Testing | pytest, mock / fixture / live 분리 |

버전과 실제 지원 환경은 probe 및 pilot 이후 고정합니다. 그래프 DB, 별도 API 서버, 멀티에이전트, 학습 기반 복구 정책은 MVP의 전제 조건이 아닙니다.

## 개발 및 기여

### 착수 순서

| 단계 | 목표 |
| --- | --- |
| S0 | 실제 Codex 실행 및 격리, 공통 계약, 최소 독립 평가 |
| S1 | 공통 하네스, Evidence Ledger, A/B 기준선 |
| S2 | DEFAULT/DCC 및 C 조건 |
| S3 | 이중 그래프, 연결, 진단·전략 및 D 조건 |
| S4 | E 통합, A–E 비교, 체크포인트 및 계측 |
| S5 | Pilot, 실험 동결, 평가, 분석 및 제출 |

벤치마크 및 평가기 준비는 병렬로 진행합니다. 먼저 실제 과제 하나의 실행과 평가를 검증하는 vertical slice를 확보하고, 복잡한 UI보다 핵심 기능을 우선합니다. [IMPLEMENTATION_PLAN.md](docs/project/IMPLEMENTATION_PLAN.md)에 IP01–IP36 작업의 의존성 및 G0–G5 승인 기준이 있습니다.

### 개발용 Codex 사용

개발 에이전트는 [AGENTS.md](AGENTS.md)를 참조하고, 현재 작업 ID 및 관련 명세를 확인한 뒤 제한된 범위에서 구현·테스트합니다. 개발용 에이전트의 세션 및 지침과 AAH가 평가하는 연구 대상 Codex의 환경을 분리합니다. 평가 조건, 필수 지침, 권한, hidden test 또는 동결된 분석 정책을 임의로 변경하지 않습니다.

권장 작업 흐름은 Issue(IP ID) → 구현 계획 → 브랜치/PR → 교차 리뷰 → 관련 테스트 → 결과 및 blocker 기록입니다. 비용을 수반하는 실제 Codex 호출은 승인된 인증·격리 환경과 명시적 실행 승인을 전제로 합니다.

### 테스트

```bash
python -m pytest tests/unit tests/contract
python -m pytest tests/integration tests/isolation
```

단위·계약 테스트는 결정적인 정책 및 타입을, 통합·격리 테스트는 실행 흐름과 정보 경계를 검증합니다. live 테스트는 실제 Codex 인터페이스를 검증하며 기본 CI에서 제외합니다. 모의 실행 통과는 실제 Codex 연동 또는 성능 향상의 증거가 아닙니다.

MVP 완료에는 F01–F12, AC01–AC08 및 관련 M01–M11 검증, RT01–RT07 확인, 적어도 한 건의 실제 실패 관측과 후속 지침 전달, A–E 정책 격리, 독립 평가 및 실행 보고가 필요합니다. 성능 가설의 지지 여부는 별도 연구 결과입니다.

### 팀 역할

| 역할 | 주요 책임 |
| --- | --- |
| RH — Runtime & Harness | 실행 어댑터, workspace, 상태 머신, 공통 증거, 예산, 실험 runner |
| GR — Graph & Recovery | 코드·실행 그래프, 연결, 진단, 복구 전략, 그래프 관련 ablation |
| CE — Context & Evaluation | DEFAULT/DCC, 컨텍스트 전달, 독립 평가기, 비용 집계, 통계 분석 |
| 공동 | benchmark, 공통 계약, 교차 리뷰, 동결, 실험, 보고서 및 발표 |

3인 역할 배정안이며 실명 및 최종 담당은 팀 합의 후 확정합니다. RH는 계측과 평가 연결, GR은 실패 및 ablation 분석으로 CE를 지원합니다. [TEAM_ROLES.md](docs/project/TEAM_ROLES.md)에 구체적인 책임과 학습·협업 기록 양식이 있습니다.

### 보안과 재현성

- 사용자 승인 작업 공간과 공개 검사 명령만 사용하며 원본 저장소와 결과 기록을 보호합니다.
- 평가기 private fixture, hidden test, reference patch 및 자격 증명은 에이전트 접근 범위 밖에 보관합니다. `.gitignore`만으로 격리를 보장하지 않습니다.
- 공개 로그에는 비밀정보와 개인정보를 제거합니다. 결과 파일은 기본적으로 공개하지 않으며 검토한 산출물만 공유합니다.
- 모델·환경·도구·초기 커밋·정책·과제·예산·분석 조건을 본실험 이전에 고정합니다. 실패·타임아웃·결측·미실행 기록을 보존합니다.
- 공개 인터페이스에서 정확한 토큰 제한이나 임의 세션 복원이 지원되지 않는 경우 그 한계를 기록하고 실험의 주장 범위를 제한합니다.

## 문서 인덱스

README는 소개와 탐색의 출발점입니다. 세부 요구사항은 아래 영역별 기준 문서와 [AGENTS.md](AGENTS.md)의 역할 매핑을 따릅니다.

| 영역 | 문서 | 내용 |
| --- | --- | --- |
| Research | [RESEARCH_PROPOSAL.md](docs/research/RESEARCH_PROPOSAL.md) | 연구 배경, 질문, 가설, 범위, 기여 후보 |
| Product | [PROJECT_OVERVIEW.md](docs/project/PROJECT_OVERVIEW.md) | 사용자 가치, F01–F14, AC01–AC09, 사용 사례 |
| Research | [LITERATURE_REVIEW.md](docs/research/LITERATURE_REVIEW.md) | 선행연구, 유사 방법, 차별성 및 주장 한계 |
| Research | [METHODOLOGY.md](docs/research/METHODOLOGY.md) | 이중 그래프, 실패 감지, 전략, DCC, 실행 루프 |
| Design | [SYSTEM_ARCHITECTURE.md](docs/design/SYSTEM_ARCHITECTURE.md) | 실행 구조, 모듈 경계, 상태 전이, 격리 |
| Design | [TECHNICAL_SPEC.md](docs/design/TECHNICAL_SPEC.md) | 타입, 인터페이스, 설정, CLI 및 테스트 |
| Evaluation | [EXPERIMENT_DESIGN.md](docs/evaluation/EXPERIMENT_DESIGN.md) | A–E, 요인설계, 체크포인트, ablation, 예산 |
| Evaluation | [BENCHMARK_SPEC.md](docs/evaluation/BENCHMARK_SPEC.md) | 자체 및 공개 과제, 데이터 계약, 품질 검증 |
| Evaluation | [EVALUATION_PROTOCOL.md](docs/evaluation/EVALUATION_PROTOCOL.md) | 성공 판정, 계측, 통계, 실패·결측 처리 |
| Development | [IMPLEMENTATION_PLAN.md](docs/project/IMPLEMENTATION_PLAN.md) | IP01–IP36, 구현 단계, 일정 및 게이트 |
| Development | [AGENTS.md](AGENTS.md) | Codex 개발 지침 및 문서 참조 규칙 |
| Collaboration | [TEAM_ROLES.md](docs/project/TEAM_ROLES.md) | 역할별 책임, 교차 리뷰, 협업 및 학습 증빙 |

처음 프로젝트를 이해하려면 README → PROJECT_OVERVIEW → RESEARCH_PROPOSAL 순서로 읽는 것을 권장합니다. 구현 담당자는 AGENTS → IMPLEMENTATION_PLAN의 해당 작업 → TECHNICAL_SPEC 및 관련 방법론을 확인하세요. 실험 담당자는 EXPERIMENT_DESIGN → BENCHMARK_SPEC → EVALUATION_PROTOCOL을 확인하세요. 문서 충돌은 담당자와 팀이 합의하고 관련 계약을 함께 수정합니다.

### 결정·보고·감사

| 문서 | 상태와 역할 |
| --- | --- |
| [DECISION_LOG.md](docs/project/DECISION_LOG.md) | 결정·미결 사항과 실제 승인 기록 |
| [EXPERIMENT_LOG.md](docs/reports/EXPERIMENT_LOG.md) | 운영·실험 기록 양식, 미실측 |
| [RESULTS_ANALYSIS.md](docs/reports/RESULTS_ANALYSIS.md) | 집계·해석 양식, 미실측 |
| [FINAL_REPORT.md](docs/reports/FINAL_REPORT.md) | 보고서 초안, 미실측 |
| [DEMO_SCENARIO.md](docs/reports/DEMO_SCENARIO.md) | 시연 계획, 실제 실행 증거 필요 |
| [DOCUMENTATION_CHANGELOG.md](DOCUMENTATION_CHANGELOG.md) | 문서 정합화 내용, 수정 전 감사 요약, 검증 및 후속 결정 |
| [DOCUMENTATION_AUDIT.md](DOCUMENTATION_AUDIT.md) | 정합화 이전 상태를 기록한 설계 감사 |

## 로드맵 및 확장 범위

우선순위는 단일 Codex, Python 저장소, 초기 및 복구 DCC, 연결 그래프 기반 진단, 제한된 복구 루프, 독립 평가 및 통제 실험입니다. 이후 실제 로그 기반 시각화와 구성 요소 제거 실험을 진행할 수 있습니다. 다른 모델·언어, 장기 경험 메모리, 학습 기반 정책, 멀티에이전트, 워크플로 자체 재배선 등은 별도 확장 후보입니다.

11월 30일 활동 종료와 12월 16일 발표는 잠정입니다. G0→G1→정식 Pilot→G2를 따르며, 공식 마감·크레딧·가용 시간은 OD-13/14의 미결 사항입니다. 실험 규모는 Pilot에서 비용·처리량·적격 checkpoint 확보율을 확인한 뒤 동결합니다.

## License & citation

라이선스, 공개 저장소 주소, 저자 표기, 릴리스 버전 및 재현 가능한 인용 정보는 팀 합의와 외부 의존성·공개 데이터 사용 조건 검토 이후 추가할 예정입니다. 현재 설계 문서 버전(v0.1.0)은 소프트웨어 릴리스를 의미하지 않습니다. 제3자 벤치마크 및 논문은 각각의 원 출처와 이용 조건을 따릅니다. 관련 참고문헌은 [LITERATURE_REVIEW.md](docs/research/LITERATURE_REVIEW.md) 및 [BENCHMARK_SPEC.md](docs/evaluation/BENCHMARK_SPEC.md)를 참고하세요.

---

**Adaptive Agent Harness — 모델 자체가 아니라, 모델이 작동하는 실행 환경과 복구 정책의 가치를 검증합니다.**
