# Adaptive Agent Harness — 기술 명세서

## 그래프 기반 실패 복구 및 동적 컨텍스트 최적화를 통한 코딩 에이전트의 신뢰성과 실행 효율 향상

| 항목 | 내용 |
| --- | --- |
| 프로젝트 | Adaptive Agent Harness (AAH, 가칭) |
| 문서 | `TECHNICAL_SPEC.md` |
| 버전 | v0.1.0 |
| 작성일 | 2026-11-25 |
| 상태 | 구현용 명세 초안 — 실제 Codex 연동 및 보안 검증 전 |
| 상위 문서 | `RESEARCH_PROPOSAL.md`, `PROJECT_OVERVIEW.md`, `LITERATURE_REVIEW.md`, `METHODOLOGY.md`, `SYSTEM_ARCHITECTURE.md` |
| 후속 문서 | `EXPERIMENT_DESIGN.md`, `BENCHMARK_SPEC.md`, `EVALUATION_PROTOCOL.md`, `IMPLEMENTATION_PLAN.md` |

> 목적: 팀원과 Codex가 동일한 계약에 따라 모듈을 병행 구현하고, 정책 간 정보 누출 없이 실제 실행 및 공정한 비교 평가까지 연결할 수 있도록 구현 단위, 자료형, 인터페이스, 설정 및 수용 테스트를 정의한다.

---

## 1. 명세의 적용 범위와 우선순위

- MUST: MVP 및 핵심 비교 실험에 필요한 요구사항. SHOULD: 구현 여건에 따라 적용하되 생략 이유를 기록. MAY: 확장 기능.
- 기존 방법론의 연구 질문, A–E 조건, T1–T5, 전략 ID, pilot 가중치 및 F01–F14 / AC01–AC09 / M01–M11을 유지한다.
- 본 문서에서 새로 지정하는 클래스명, enum 세부 값, JSON 필드, 기본 구현 방식은 **제안된 구현 계약**이다. 기술 검증으로 수정할 경우 명세 버전과 변경 이유를 기록한다.
- 외부 관측·후속 턴 제어를 대상으로 한다. 모델 내부 추론, 내부 컨텍스트 삭제, 임의 세션 복제, 도구 단계 강제 변경은 지원한다고 가정하지 않는다.
- 우선순위는 실제 Codex 실행의 vertical slice, 정책 격리, 실패 복구, 재현 가능한 평가 순이다. P1 시각화 및 ablation은 P0 이후에 진행한다.

## 2. 기술 스택 및 저장소 구조

| 영역 | MVP 제안 | 결정/검증 기준 |
| --- | --- | --- |
| 언어 | Python 3.11 이상, 실제 minor 버전 고정 | 팀 환경에서 재현 가능한 버전 |
| 패키징 | `pyproject.toml`, `src` layout, lockfile | CI와 로컬 동일 의존성 |
| 데이터 계약 | dataclass/enum + 명시적 JSON validation; 필요 시 Pydantic | 한 가지 방식으로 통일 |
| CLI | argparse | 추가 CLI 프레임워크 불필요 |
| Runtime | Codex CLI `exec` 우선 probe, SDK는 대안 | RT01–RT07 통과 후 하나 선택 |
| Workflow | 단일 Python 상태 머신 | 별도 워크플로 프레임워크 불필요 |
| Graph | `ast`, NetworkX `MultiDiGraph` | 타입·방향·병렬 엣지 보존 |
| Context | BM25, 메타데이터 규칙, greedy packing | 검색/추정기 버전 고정 |
| Isolation | Git + 검증된 격리 환경(Docker 후보) | 원본·비밀·평가 자료 접근 차단 |
| Storage | JSON/JSONL, CSV 집계 | 대형 DB 불필요 |
| Testing | pytest | mock / fixture / live 분리 |

실제 의존성 버전, OS 및 런타임 버전은 pilot에서 선택하고 lockfile과 manifest에 기록한다. Windows 호스트 지원이 필요하더라도 프로세스 취소 및 격리 검증 전에는 지원을 보장하지 않는다.

```text
adaptive-agent-harness/
├── README.md
├── AGENTS.md
├── pyproject.toml
├── docs/
├── configs/
│   ├── default.yaml
│   ├── pilot.yaml
│   └── experiments/
├── src/aah/
│   ├── cli/{main,config}.py
│   ├── domain/{models,enums,errors,policies}.py
│   ├── workflow/{orchestrator,state_machine,budget}.py
│   ├── runtime/{base,codex_cli,observer,normalize}.py
│   ├── workspace/{manager,snapshot,patch,checks}.py
│   ├── evidence/{ledger,view,fingerprint}.py
│   ├── graphs/{repository,execution,mapping,service}.py
│   ├── diagnosis/{detector,gate,flat,graph}.py
│   ├── recovery/{controller,catalog,adherence}.py
│   ├── context/{candidates,default,dcc,tokenizer,renderer,guard}.py
│   └── reporting/{store,report,redaction}.py
├── experiments/{runner,conditions,checkpoint,aggregate}.py
├── evaluation/{runner,contracts}.py
├── benchmarks/                 # 공개 및 개발용 fixture만
├── analysis/
├── tests/{unit,contract,integration,isolation,live,fixtures}/
└── results/                    # git 제외, 실행별 접근 제한
```

중괄호 표기는 파일 목록의 축약 표현이다. `evaluation/`에는 평가 실행기 코드만 둘 수 있으며 private fixture, 정답 패치 및 평가 자격 증명은 별도 권한 경계에 보관한다. AAH 구현 저장소와 연구 대상 저장소를 혼동하지 않는다.

## 3. 공통 데이터 계약

### 3.1. 규약

- wire format은 UTF-8 JSON, 로그는 JSONL. `schema_version: "1.0"`을 파일 또는 레코드 envelope에 포함한다. 필수 필드 누락과 알 수 없는 major schema는 명시적으로 거절한다.
- 시간은 UTC ISO 8601 문자열(`...Z`), 경과 시간은 monotonic clock 기반 밀리초 정수, 토큰은 음이 아닌 정수 또는 `null`이다. `null`은 관측 불가이며 0과 다르다. 실제 과금액은 통화와 decimal 문자열을 함께 기록한다.
- 모든 경로는 허용 workspace 기준 상대 POSIX 경로로 정규화한다. 절대 경로, `..`, symlink 탈출, 대소문자 충돌은 검증한다. 파일 span은 1-based line, 0-based UTF-8 byte column, 끝 위치 exclusive로 통일한다.
- `run_id` 및 `checkpoint_id`는 UUID 계열, 나머지 식별자는 run 또는 저장소/스냅샷 범위가 명시된 안정적인 문자열이다. 정책 정렬에 무작위 UUID를 사용하지 않는다.
- config/snapshot/patch/content hash는 SHA-256. canonical JSON은 key 정렬, UTF-8, 고정 구분자, NaN/Infinity 금지. 설정 해시에는 비밀 대신 안전한 설정 식별자만 포함한다.
- 같은 run에서 이벤트 ID, 후보 ID, 그래프 edge ID는 유일해야 한다. 중복 원천 이벤트는 한 번만 반영한다. 원시 로그의 중복 수신 자체는 삭제하지 않는다.
- 쓰기 모듈과 읽기 전용 view를 구분하고 정책 함수는 입력 데이터를 직접 변경하지 않는다.

### 3.2. 공통 enum

```python
from enum import StrEnum

class Phase(StrEnum):
    INIT = "INIT"
    INITIAL_CONTEXT = "INITIAL_CONTEXT"
    RUN = "RUN"
    OBSERVE = "OBSERVE"
    DECIDE = "DECIDE"
    DIAGNOSE = "DIAGNOSE"
    SELECT_STRATEGY = "SELECT_STRATEGY"
    RECOVERY_CONTEXT = "RECOVERY_CONTEXT"
    FINALIZE = "FINALIZE"
    EVALUATE = "EVALUATE"  # 연구 실행기의 논리 상태, 제어부에서는 실행 금지
    REPORT = "REPORT"

class Condition(StrEnum):
    A = "A"; B = "B"; C = "C"; D = "D"; E = "E"

class ContextPolicy(StrEnum):
    NONE = "NONE"; DEFAULT = "DEFAULT"; DCC = "DCC"

class DiagnosisPolicy(StrEnum):
    NONE = "NONE"; FLAT = "FLAT"; GRAPH = "GRAPH"

class StrategyPolicy(StrEnum):
    NATIVE = "NATIVE"; GENERIC = "GENERIC"; TYPED = "TYPED"

class StrategyId(StrEnum):
    REVIEW_REGRESSION = "REVIEW_REGRESSION"
    RELOCALIZE = "RELOCALIZE"
    REFRESH_EVIDENCE = "REFRESH_EVIDENCE"
    GENERIC_RETRY = "GENERIC_RETRY"
    CONTINUE = "CONTINUE"
    STOP = "STOP"
```

다음 enum 역시 계약에 포함한다. 표의 값은 wire string과 일치한다.

| 자료형 | 허용 값 |
| --- | --- |
| ObservationStatus | `observed`, `inferred`, `unknown` |
| Validity | `CURRENT`, `INVALID`, `HISTORICAL`, `UNCERTAIN` |
| CheckStatus | `PASS`, `FAIL`, `TIMEOUT`, `INFRA_ERROR`, `NOT_RUN`, `UNKNOWN` |
| TriggerId | `T1`, `T2`, `T3`, `T4`, `T5` |
| DetectionStatus | `TRIGGERED`, `NO_TRIGGER`, `UNKNOWN`, `INFRA` |
| StrategyAdherence | `performed`, `not_observed`, `contradicted`, `unknown` |
| GraphStatus | `complete`, `partial`, `unavailable` |
| SessionMode | `SAME_SESSION`, `NEW_SESSION` |
| InterventionBoundary | `TURN_END`, `POST_COMPLETION`, `UNSUPPORTED` |
| CapabilityStatus | `VERIFIED`, `UNSUPPORTED`, `UNVERIFIED` |
| UnitStatus | `COMPLETED`, `FAILED`, `INTERRUPTED`, `TIMEOUT`, `UNKNOWN` |
| StopReason | `COMPLETED`, `GLOBAL_LIMIT`, `CANCELED`, `SAFETY_VIOLATION`, `RUNTIME_ERROR`, `PREPARATION_ERROR`, `NO_SAFE_CONTINUATION`, `ARTIFACT_ERROR` |
| EvaluationStatus | `PASS`, `FAIL`, `INFRA_ERROR`, `NOT_EVALUABLE`, `NOT_RUN` |
| UsageMode | `DELTA`, `CUMULATIVE`, `UNKNOWN` |

`COMPLETED`는 실행 종료 사유이고 성공 판정이 아니다. 후속 실행 가능 여부는 UnitStatus와 별개의 capability 및 continuation 필드로 판정한다.

### 3.3. 핵심 객체 필드

아래 타입 표기에서 `?`는 nullable, `[]`는 리스트다. domain 객체의 구체 클래스는 immutable dataclass를 기본으로 하며 `RunState`만 Orchestrator가 전이시킨다. 참조 객체의 본문은 저장소에 분리한다.

| 객체 | 필수 필드 및 타입 |
| --- | --- |
| RepositoryRef | `repo_id:str`, `source_path:Path`, `initial_commit:str` |
| FixedRequirements | `task:str`, `mandatory_text:str`, `mandatory_hash:str`, `permission_profile_id:str`, `approved_check_ids:str[]` |
| PolicySet | `condition:Condition`, `initial_context:ContextPolicy`, `recovery_context:ContextPolicy`, `diagnosis:DiagnosisPolicy`, `strategy:StrategyPolicy`, `external_intervention:bool` |
| RunRequest | `run_id:str`, `task_id:str?`, `experiment_id:str?`, `repository:RepositoryRef`, `fixed:FixedRequirements`, `policies:PolicySet`, `config_hash:str`, `mode:user/research` |
| RunState | `run_id:str`, `phase:Phase`, `turn_index:int`, `transition_count:int`, `recovery_reserved:int`, `snapshot_id:str?`, `ledger_cursor:int`, `last_intervention_id:str?`, `budget:BudgetState`, `stop_reason:StopReason?` |
| RuntimeCapabilities | `adapter:str`, `runtime_version:str`, `model_id:str`, `unit_boundary:InterventionBoundary`, `session_modes:SessionMode[]`, `features:map[str,CapabilityStatus]`, `usage_mode:UsageMode`, `probe_artifact_ref:str` |
| WorkspaceHandle | `run_id:str`, `root:Path`, `initial_commit:str`, `isolation_profile_id:str`, `verified:bool` |
| SnapshotRef | `repo_id:str`, `snapshot_id:str`, `base_commit:str`, `manifest_ref:str`, `captured_at:str`, `complete:bool` |
| BudgetState | `elapsed_ms:int`, `remaining_ms:int`, `turns_used:int`, `recovery_reserved:int`, `transitions_used:int`, `observed_model_tokens:int?`, `token_enforcement:CapabilityStatus` |
| TurnRequest | `run_id:str`, `turn_id:str`, `workspace:WorkspaceHandle`, `session_ref:str?`, `kind:INITIAL/NATIVE_CONTINUE/RECOVERY`, `message:str?`, `message_hash:str?`, `intervention_id:str?`, `deadline_ms_remaining:int`, `runtime_config_ref:str` |
| TurnResult | `turn_id:str`, `status:UnitStatus`, `session_ref:str?`, `completion_declared:bool?`, `native_continuation_supported:bool`, `exit_code:int?`, `event_refs:str[]`, `usage_refs:str[]`, `error:ErrorInfo?`, `process_quiescent:bool` |
| ObservationBatch | `run_id:str`, `turn_id:str`, `before_snapshot_id:str`, `after_snapshot_id:str?`, `evidence_ids:str[]`, `check_result_ids:str[]`, `diff_ref:str?`, `coverage:map[str,ObservationStatus]`, `ledger_cursor:int` |
| Trigger | `status:DetectionStatus`, `trigger_ids:TriggerId[]`, `primary_id:TriggerId?`, `anchor_ids:str[]`, `reason_codes:str[]`, `snapshot_id:str`, `observation_cursor:int` |
| GateDecision | `allowed:bool`, `reason_codes:str[]`, `checkpoint_key:str` |
| Diagnosis | `diagnosis_id:str`, `policy:DiagnosisPolicy`, `anchor_ids:str[]`, `candidates:DiagnosisCandidate[]`, `evidence_ids:str[]`, `status:sufficient/uncertain/insufficient`, `trace_ref:str?`, `warnings:str[]` |
| Strategy | `strategy_id:StrategyId`, `trigger_ids:TriggerId[]`, `objective:str`, `targets:str[]`, `evidence_ids:str[]`, `path_refs:str[]`, `next_checks:str[]`, `completion_observation:str`, `confidence_status:str`, `reason:str` |
| FrozenPatch | `run_id:str`, `base_commit:str`, `snapshot_id:str`, `patch_ref:str`, `patch_hash:str`, `changed_paths:str[]`, `applicable:bool` |
| ErrorInfo | `code:str`, `stage:Phase`, `safe_message:str`, `retryable:bool`, `cause_ref:str?` |

`RunState.budget`는 Budget Manager의 읽기 전용 상태다. 중복 계수 필드는 일치 여부를 검사한다. 보안상 `workspace.root`와 private 경로는 모델에게 전달하는 메시지 또는 공유 리포트에 넣지 않는다. 위 필드는 최소 계약이며 본 문서의 후속 세부 스키마를 따른다.

## 4. 설정 파일과 비교군 해석

### 4.1. Pilot 설정 예시

아래 YAML은 파싱 가능한 **초안**이다. `null`로 표시한 필수 실행값은 실제 실행 전에 명시해야 한다. 숫자는 방법론 §15에서 유지한 값과 별도 제안한 값(상태 전이 100, 정리 시간 15초)을 구분한다. 후자는 pilot에서 확정한다.

```yaml
schema_version: "1.0"
runtime:
  adapter: codex_cli
  model_id: null
  runtime_version: null
  reasoning_effort: null
  session_mode: null
  intervention_boundary: null
  capability_report: null
workspace:
  isolation_profile_id: null
  network_profile_id: null
  include_globs: ["**/*.py", "**/*.md", "**/*.toml", "**/*.yaml", "**/*.yml"]
  exclude_globs: [".git/**", ".venv/**", "**/__pycache__/**", "**/.env*", "**/private/**"]
  snapshot_scope_id: null
validation:
  checks: []  # 사전 승인된 검사 객체; 과제별 설정에서 지정
limits:
  wall_time_seconds: null
  max_turns: null
  max_recoveries: 2
  max_transitions: 100
  cleanup_reserve_seconds: 15
  max_model_tokens: null
  token_limit_mode: observe_only
context:
  initial_cap_tokens: 2048
  recovery_cap_tokens: 2048
  token_estimator_id: null
  bm25: {k1: 1.5, b: 0.75}  # 기술 명세의 신규 pilot 후보
  weights_initial: {task: 0.5, scope: 0.3, validity: 0.2, failure: 0.0}
  weights_recovery: {task: 0.25, scope: 0.2, validity: 0.2, failure: 0.35}
  duplicate_penalty: 0.2
recovery:
  repeated_failure_k: 2
  stagnation_window: 6
  duplicate_ratio_threshold: 0.67
  trigger_priority: [T3, T1, T2, T4]
  graph:
    max_depth: 4
    max_visited_states: 200
    max_branching: 20
    top_k: 8
    default_edge_cost: 1.0
    inferred_edge_penalty: 1.0
    weights: {distance: 0.4, observed: 0.3, modified: 0.15, unexplored: 0.15}
logging:
  results_root: results
  raw_access: restricted
  schema_version: "1.0"
  retention_days: null  # 배포 전 팀의 데이터 정책으로 결정
```

`workspace.include_globs`는 AST/보조 문서 색인용 초기 후보이지 스냅샷이나 최종 패치의 전체 범위를 제한하는 규칙이 아니다. snapshot scope는 관련 tracked/untracked 변경·삭제를 모두 포착해야 하며 평가 자료, 비밀정보 및 생성 캐시를 제외하도록 별도 검증한다. 파일 수 및 읽기 바이트 상한은 pilot에서 결정하고 모든 조건에서 동일하게 적용한다.

### 4.2. 설정 유효성 검사

- 실행 전 model/version/reasoning/session/검증된 격리·capability, 양수 시간·턴·추정기 설정을 요구한다. 네이티브 A도 공통 런타임 설정을 고정한다.
- `cleanup_reserve_seconds < wall_time_seconds`; `max_recoveries >= 0`; 그래프 상한은 양의 정수; 비용은 양수, 추정 penalty는 비음수. 각 가중치 합은 허용 오차 내 1.
- `checks`는 `check_id`, `argv:list[str]`, `cwd`, `timeout_seconds`, `required`, `parser_id`, `environment_profile_id`로 구성한다. 명령 문자열을 shell로 실행하지 않는다. argv 및 환경 승인은 별도 allowlist와 격리 정책으로 확인한다.
- token_limit_mode는 `observe_only` 또는 `enforced`이다. latter는 RT07 통과가 필수. 토큰 상한을 미지원인데 강제했다고 표시하지 않는다.
- unknown config key, 중복 ID, private 경로, 상충되는 조건 override는 오류로 처리한다. config는 run 시작 전에 고정하며 run 중 수정하지 않는다.

### 4.3. 비교군 resolver

| 조건 | initial_context | diagnosis | strategy | recovery_context | external_intervention |
| --- | --- | --- | --- | --- | --- | --- |
| A | NONE | NONE | NATIVE | NONE | false |
| B | DEFAULT | FLAT | GENERIC | DEFAULT | true |
| C | DCC | FLAT | GENERIC | DCC | true |
| D | DEFAULT | GRAPH | TYPED | DEFAULT | true |
| E | DCC | GRAPH | TYPED | DCC | true |

```python
def resolve_condition(condition: Condition) -> PolicySet: ...
def validate_policy_set(policy: PolicySet, *, experiment_locked: bool) -> None: ...
```

연구 모드 A–E에서 정책은 위 매핑으로만 결정한다. 개별 policy override는 금지한다. 확장 ablation은 별도 ID와 versioned manifest를 사용한다. B/D와 C/E의 초기 패키지는 동일 입력에서 byte 단위 일치해야 한다(run ID 등의 로그 메타데이터는 외부 메시지에서 제외). Graph-only의 기본 패키징은 그래프 진단의 순위를 사용할 수 있지만 DCC 점수·중복 제거·한계 효용은 사용하지 않는다.

## 5. 모듈 인터페이스

아래 Python 시그니처는 프로젝트 내부 계약이며 실행 가능한 구현 코드 전체가 아니다. `Protocol` 기반으로 fake 구현을 주입할 수 있게 한다. I/O는 전담 모듈로 제한하고 진단·정책 계산은 순수 함수에 가깝게 유지한다.

```python
from typing import Protocol, Callable, Sequence

class RuntimeAdapter(Protocol):
    def probe_capabilities(self, config: RuntimeConfig) -> RuntimeCapabilities: ...
    def open_run(self, request: RunRequest, workspace: WorkspaceHandle) -> str | None: ...
    def run_unit(self, request: TurnRequest, on_event: Callable[[RawEvent], None]) -> TurnResult: ...
    def continue_unit(self, request: TurnRequest, on_event: Callable[[RawEvent], None]) -> TurnResult: ...
    def cancel(self, run_id: str) -> CancelResult: ...
    def close_run(self, run_id: str) -> CleanupResult: ...

class WorkspaceManager(Protocol):
    def prepare(self, request: RunRequest) -> WorkspaceHandle: ...
    def snapshot(self, workspace: WorkspaceHandle) -> SnapshotRef: ...
    def diff(self, before: SnapshotRef, after: SnapshotRef) -> DiffRecord: ...
    def freeze_patch(self, workspace: WorkspaceHandle, snapshot: SnapshotRef) -> FrozenPatch: ...
    def cleanup(self, workspace: WorkspaceHandle) -> CleanupResult: ...

class EvidenceLedger(Protocol):
    def append_batch(self, batch: Sequence[EvidenceRecord]) -> int: ...
    def view(self, cursor: int) -> EvidenceView: ...

class Diagnoser(Protocol):
    def diagnose(self, trigger: Trigger, evidence: EvidenceView,
                 snapshot: SnapshotRef) -> Diagnosis: ...

class ContextCompiler(Protocol):
    def compile(self, request: CompileRequest) -> CompileResult: ...
```

| 모듈 / 함수 | 입력 → 출력 | 계약 |
| --- | --- | --- |
| `workflow.orchestrator.run` | RunRequest → RunReport | 상태 머신의 유일한 변경 주체. evaluator 접근 금지 |
| `workflow.state_machine.transition` | RunState, TransitionEvent → RunState | 허용 전이·한도 검증, 전이 기록 |
| `workflow.budget.can_start` | BudgetState, stage → BudgetDecision | 정리 예약 시간과 잔여 턴 확인 |
| `workflow.budget.reserve_recovery` | intervention_id, state → BudgetState | ID별 한 번만 예약; 취소 후 자동 환급 금지 |
| `runtime.observer.collect` | TurnResult, workspace, before snapshot → ObservationBatch | diff, 검사, 계측 및 snapshot 확보 후 ledger 반영 |
| `workspace.checks.run_approved` | workspace, check_id, deadline → CheckResult | 승인 목록만 실행; private 검사 불가 |
| `graphs.repository.build` | SnapshotRef → GraphArtifact | 허용 Python AST, 해석 실패 표시 |
| `graphs.execution.build` | EvidenceView → GraphArtifact | 중복 제거·관측 순서·지문 관계 |
| `graphs.mapping.link` | repository + execution + EvidenceView → CoupledGraphView | 근거와 버전 일치 검증 |
| `graphs.service.update` | snapshot, ledger → CoupledGraphView | GRAPH 조건 전용; 불완전 상태 표시 |
| `diagnosis.detector.detect` | common EvidenceView, snapshot, config → Trigger | 그래프 정보 입력 금지 |
| `diagnosis.gate.decide` | Trigger, RunState, capabilities → GateDecision | 중복 개입·한도·후속 실행 검증 |
| `diagnosis.flat.diagnose` | 공통 증거 → Diagnosis | 평면 검색; graph 파생 필드 금지 |
| `diagnosis.graph.diagnose` | 공통 증거 + CoupledGraphView(생성 시 주입) → Diagnosis | 경로·점수·근거 추적 |
| `recovery.controller.select` | Trigger, Diagnosis, PolicySet → Strategy | GENERIC 또는 TYPED; 승인 검사만 |
| `recovery.adherence.assess` | Strategy, subsequent EvidenceView → StrategyAdherence | 충분한 관측 여부와 수행 여부 분리 |
| `context.candidates.collect` | task, snapshot, stage, diagnosis? → Candidate[] | 허용된 후보만, 초기에는 diagnosis 없음 |
| `context.renderer.render` | fixed, strategy?, package? → RenderedMessage | 공통 형식; 사용자 요청·필수 요구 불변 |
| `context.guard.validate` | message, fixed, cap, policy → GuardResult | 권한·예산·정책 누출 확인 |
| `reporting.store.append` | stream, record → record ref | 제한된 경로, append-only |
| `reporting.report.build` | run artifacts → RunReport | 미관측·fallback·종료 이유 명시 |
| `experiments.runner.execute` | ExperimentManifest → RunRefs | 비교군·환경 고정, 종료 후 평가 요청 |
| `evaluation.runner.evaluate` | FrozenPatch + private task spec → EvaluationResult | 별도 실행 경계. 결과는 분석용만 |

`RuntimeConfig`, `RawEvent`, `CompileRequest` 등 세부 계약은 다음 절에서 정의한다. `CancelResult/CleanupResult`는 `confirmed:bool`, `remaining_process_count:int?`, `error:ErrorInfo?`를 반환한다. `BudgetDecision/GuardResult`는 `allowed:bool`, `reason_codes:str[]`를 포함한다. 호출 실패는 `ErrorInfo`와 함께 결과로 변환하고 내부 불변조건 위반은 즉시 안전 종료한다.

## 6. Codex Runtime Adapter 및 공개 검사

### 6.1. 실제 연동 후보

OpenAI의 공식 플랫폼 설명 및 평가 가이드에서는 비대화형 `codex exec`, SDK, app-server를 서로 다른 통합 방식으로 소개하고, 구조화된 JSON 이벤트를 평가에 활용하는 예를 제공한다([O1], [O2]). 이를 바탕으로 첫 pilot은 Python에서 CLI subprocess를 사용하는 방향을 제안한다. 정확한 flags, resume, 모델 설정 전달, 이벤트 형식, 권한 설정과 지원 범위는 설치한 버전에서 검증한다.

```text
검증 절차(실행 명령의 설계 예시)
1. codex --version / codex exec --help 결과 저장
2. 허용된 격리 환경에서 codex exec --json ... 실행
3. stdout JSONL과 stderr, exit status 분리 수집
4. 동일 세션 후속 실행 또는 새 세션 재개 가능성 검사
5. timeout/cancel 및 자식 프로세스 정리 확인
6. 사용량 필드의 의미(누적/증분/결측), 필수 지침 로딩 확인
```

실제 명령의 argv는 probe에서 확인한 flag만 조립한다. 인증 정보는 환경의 승인된 credential mechanism을 이용하고 명령행, 설정, 로그에 저장하지 않는다. `shell=True`, 권한 우회 플래그 및 실패한 격리의 자동 완화는 금지한다. CLI가 종료 시점 외의 개입을 지원하지 않으면 `POST_COMPLETION`으로 기록한다. 같은 세션이 검증되지 않으면 B–E 모두 동일한 NEW_SESSION 및 고정 이력 요약 정책을 사용한다. 공식 문서에 기능이 있다고 해서 현재 계정·버전에서 검증된 것은 아니다.

### 6.2. RawEvent와 정규화 증거

```json
{"schema_version":"1.0","run_id":"example-run","turn_id":"turn-01","raw_id":"raw-0001","received_seq":1,"received_at":"2026-11-25T00:00:00Z","provider_event_id":null,"provider_type":"example.event","payload_ref":"restricted/raw-0001.json","parse_status":"UNKNOWN"}
```

위 예시는 AAH envelope이고 공식 Codex 이벤트 예시가 아니다. 원본 payload와 stderr는 제한 접근 artifact에 저장한다. 정규화 규칙은 버전별 mapper에서 관리한다.

| EvidenceRecord 필드 | 계약 |
| --- | --- |
| `evidence_id`, `run_id`, `turn_id`, `sequence` | 고유 ID와 ledger의 수신 순서 |
| `kind` | `SEARCH`, `FILE_ACCESS`, `COMMAND`, `COMMAND_RESULT`, `CHANGE`, `TEST`, `FAILURE`, `INTERVENTION`, `USAGE`, `UNKNOWN` |
| `raw_refs`, `observed_at` | 출처와 관측 시점; 없으면 timestamp null |
| `observation_status` | observed/inferred/unknown |
| `snapshot_id` | 확인된 버전 또는 null |
| `snapshot_interval` | 정확한 버전이 없을 때 before/after ID 또는 null |
| `source_order` | 확인된 원천 순서 또는 null; 수신 순서와 분리 |
| `payload` | 종류별 구조화 객체; 원문은 ref로 참조 |
| `warnings` | 미해결 경로, 미지원 필드, 순서 불확실 등 |

종류별 payload 최소 필드:

| 종류 | 필드 |
| --- | --- |
| SEARCH | `query`, `scope`, `result_ref?`, `result_digest?` |
| FILE_ACCESS | `path`, `access_kind:read/write/unknown`, `access_basis`, `span?` |
| COMMAND / COMMAND_RESULT | `command_id`, `argv?`, `cwd?`, `exit_code?`, `stdout_ref?`, `stderr_ref?`, `status?` |
| CHANGE | `diff_ref`, `before_snapshot_id`, `after_snapshot_id`, `path`, `hunks` |
| TEST | `check_result_id`, `check_id`, `test_id?`, `status`, `command_id?` |
| FAILURE | `check_result_id`, `fingerprint?`, `error_type?`, `normalized_message?`, `stable_frame?` |
| INTERVENTION | `intervention_id`, `strategy_id`, `delivery_status`, `trigger_ids`, `anchor_ids` |
| USAGE | `usage_record_id` |
| UNKNOWN | `provider_type?`, `reason_code`, `raw_ref` |

명령 인자에 파일 경로가 나타났다는 이유만으로 READS를 생성하지 않는다. 명확한 관측 근거가 있으면 observed, 제한적 해석만 가능하면 inferred 또는 unknown. 비정상 exit code는 테스트 assertion 실패와 분리한다. 순서 미확인 이벤트에 시간적 인과 관계를 생성하지 않는다.

### 6.3. 공개 검사와 지문

`CheckResult`: `check_result_id`, `check_id`, `turn_id`, `snapshot_id`, `status`, `exit_code?`, `duration_ms`, `environment_hash`, `parser_id`, `test_cases[]`, `stdout_ref?`, `stderr_ref?`, `warnings[]`.

`TestCaseResult`: `test_id`, `status`, `error_type?`, `message?`, `frame?`, `fingerprint?`. 비교 가능한 검사 조건은 동일 check/test ID, 설정 및 실행 환경이어야 한다. 집계 실패만 관측된 경우 개별 테스트 결과를 만들어내지 않는다. 승인 검사는 turn 종료 후 quiescent workspace에서 공통 일정으로 수행하며 이전·이후 변경이 있으면 기록한다. baseline A에는 공개 검사 결과를 외부 복구 지침으로 전달하지 않는다.

```python
def normalize_failure(case: TestCaseResult, check_id: str) -> FailureFingerprint | None: ...
def compare_checks(previous: CheckResult, current: CheckResult) -> CheckDelta: ...
```

지문은 `(check_id, test_id, error_type, normalized_message, stable_frame)`의 canonical hash와 구조화 원본을 보존한다. 휘발성 경로·메모리 주소·시간만 버전 관리된 규칙으로 정규화하고 assertion의 의미 있는 값은 유지한다. stable frame은 미관측 시 null, 지문 일치는 동일 원인 보장이 아니다. 체크 실행 자체가 실패했거나 파싱 불가이면 T5로 처리할 수 있다.

### 6.4. Runtime probe 수용 조건

| ID | 산출물/검증 |
| --- | --- |
| RT01 | 버전·모델·추론 설정 및 실제 실행 기록 |
| RT02 | 지원 이벤트 목록, 누락 범위, 원시→정규화 fixture |
| RT03 | 실제 후속 요청 전달 및 선택한 세션 정책 증거 |
| RT04 | timeout/cancel 테스트, 잔존 프로세스 확인 |
| RT05 | 필수 지침 로딩, 권한·cwd·원본 보호 검증 |
| RT06 | 공개 검사와 private 자료 접근 경계 검증 |
| RT07 | 토큰 추정기, usage mode 및 강제 가능 여부 기록 |

RT04–RT06에서 안전한 실행을 확보하지 못하면 실제 연구 실행을 금지한다. RT03이 실패하면 복구 기능 미완료로 표시하며 mock 성공으로 대체하지 않는다.

## 7. Workspace, snapshot 및 패치 계약

`WorkspaceManager`는 원본과 분리된 workspace를 준비하며 실행별 독립 ID를 사용한다. Git 복제나 worktree만으로 보안 격리를 충족했다고 간주하지 않는다. 실행 프로세스가 완전히 정지하기 전에는 최종 snapshot/patch를 확정하지 않는다.

### 7.1. Snapshot manifest

```json
{
  "schema_version": "1.0",
  "repo_id": "example-auth",
  "base_commit": "<fixed commit>",
  "scope_id": "pilot-scope-v1",
  "files": [
    {"path":"src/auth.py","state":"present","sha256":"<content hash>","mode":"100644","tracked":true},
    {"path":"src/legacy.py","state":"deleted","sha256":null,"mode":null,"tracked":true}
  ],
  "complete": true
}
```

snapshot ID는 repo ID, base commit, scope ID와 path별 상태·hash·mode를 정렬한 canonical manifest로부터 계산한다. 캡처 시간은 hash에 넣지 않는다. 관련 untracked 파일도 포함한다. 읽기 실패 또는 누락이 있으면 incomplete로 표시하고 동일한 complete snapshot으로 취급하지 않는다. manifest가 참조하는 파일 바이트를 복원할 수 있도록 안전한 content store 또는 체크포인트 패키지에 저장한다.

`DiffRecord`: `diff_id`, `before_snapshot_id`, `after_snapshot_id`, `changed_paths[]`, `hunks[]`, `patch_ref`, `complete`. hunk는 old/new line range를 별도 저장하며 삭제 범위와 추가 범위를 혼동하지 않는다. 파일 rename은 Git의 추정 결과임을 표시하고 심볼 동일성을 자동 보장하지 않는다.

최종 패치는 **초기 commit 대비** 전체 변경을 포함해야 한다. Git diff의 tracked 변경만 기록해 신규 파일을 빠뜨리지 않는다. 바이너리·모드 변경·symlink 처리 범위는 pilot에서 검증하며 안전하게 재적용할 수 없는 경우 `applicable=false`로 기록한다. 원본 저장소를 자동 수정하거나 push하지 않는다. 독립 평가에서는 깨끗한 초기 commit에 동일 패치를 적용해 hash와 변경 목록을 검증한다.

## 8. 그래프 데이터 스키마와 구축 계약

### 8.1. 공통 GraphArtifact

각 그래프는 NetworkX MultiDiGraph를 기본 자료구조로 하되 영속 형식은 라이브러리 내부 객체가 아닌 명시적인 노드·엣지 배열이다.

```python
@dataclass(frozen=True)
class GraphNode:
    node_id: str
    kind: str
    namespace: str  # repo / exec
    snapshot_id: str | None
    observation_status: ObservationStatus
    evidence_ids: tuple[str, ...]
    attributes: dict

@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    observation_status: ObservationStatus
    evidence_ids: tuple[str, ...]
    attributes: dict
```

`GraphArtifact`: `graph_id`, `run_id`, `graph_kind:repository/execution/coupled`, `current_snapshot_id`, `ledger_cursor`, `builder_version`, `status`, `nodes[]`, `edges[]`, `warnings[]`. 해시 계산용 직렬화는 노드/엣지 ID 순으로 정렬한다. `CoupledGraphView`는 읽기 전용 artifact 및 현재 snapshot과 ledger cursor를 노출한다. 존재하지 않는 노드 ID를 향하는 엣지는 허용하지 않는다. 동일 소스·타깃이라도 관계 타입 또는 증거가 다른 엣지를 보존한다.

### 8.2. Repository Graph

| Node kind | attributes 필수 필드 |
| --- | --- |
| FILE | `repo_id`, `path`, `content_hash`, `parse_status` |
| FUNCTION | `file_id`, `qualified_name`, `span`, `is_async` |
| CLASS | `file_id`, `qualified_name`, `span` |
| TEST_FUNCTION | `file_id`, `qualified_name`, `span`, `test_id?` |

노드 ID는 repo ID, snapshot ID, path, kind 및 (심볼인 경우) qualified name·정의 위치를 포함한 canonical hash를 사용한다. 파일 ID와 심볼 ID는 별도 namespace를 사용한다. Python 함수와 테스트 함수에 대한 분류는 고정된 테스트 식별 규칙을 적용하고 모호한 테스트 ID는 null로 둔다.

| Relation | Source → Target | 구현 규칙 |
| --- | --- | --- |
| CONTAINS | FILE/CLASS → 심볼 | AST의 직접 정의 구조 |
| IMPORTS | FILE → FILE | 내부 모듈 해석 결과만; 미해결·외부 import는 보고 |
| CALLS | 호출자 → 피호출자 | 정적 확인 가능한 호출만; 모호한 대상은 inferred 또는 미해결 |
| INHERITS | CLASS → CLASS | 내부 상속 관계, 이름 해석 필요 |
| TESTS | TEST_FUNCTION → 코드 요소 | 명시적 호출/import 또는 허용된 공개 coverage |

함수 내부 중첩 정의 등은 FILE에서 해당 심볼을 참조하고 qualified name으로 범위를 구별하는 단순 구현을 허용한다. 관계의 정적 해석 한계를 기록한다. Python AST 파싱 실패 시 FILE 노드는 유지하며 해당 파일의 미추출 관계를 완전한 그래프로 위장하지 않는다. 평가 fixture는 색인하지 않는다.

### 8.3. Execution Graph

| Node kind | attributes 필수 필드 |
| --- | --- |
| SEARCH / FILE_ACCESS | `evidence_id`, `turn_id`, `query?`, `path?`, `access_basis?` |
| COMMAND | `command_id`, `turn_id`, `argv?`, `exit_code?` |
| COMMAND_RESULT | `command_id`, `exit_code?`, `result_ref?` |
| CHANGE | `diff_id`, `before_snapshot_id`, `after_snapshot_id`, `path` |
| TEST | `check_result_id`, `check_id`, `test_id?`, `status` |
| FAILURE | `check_result_id`, `fingerprint?`, `test_id?`, `error_type?` |
| INTERVENTION | `intervention_id`, `strategy_id`, `delivery_status` |

`COMMAND_RESULT`는 방법론에서 정의된 명령과 결과의 RETURNS 관계를 구현하기 위해 추가한 세부 node kind다. 이벤트 정규화 과정에서 별도 결과를 확인하지 못하면 생성하지 않는다. 실행 노드 ID는 run ID와 evidence ID를 포함한다.

| Relation | 방향 및 제약 |
| --- | --- |
| NEXT | 원천 순서가 확인된 직전 사건 → 다음 사건 |
| RETURNS | COMMAND/TEST → 관측된 결과 또는 FAILURE |
| RETRIES | 재실행 → 명시적으로 연관된 이전 시도 |
| OBSERVED_AFTER | 이전 CHANGE/INTERVENTION → 이후 관측 사건; 시간적 선후만 표현 |
| SAME_FAILURE_AS | 현재 FAILURE → 같은 지문의 직전 비교 가능한 FAILURE |

SAME_FAILURE_AS는 관측된 지문 단위 직전 사건만 연결하며 동일 실패를 완전 연결하지 않는다. 순서 또는 연결이 불확실하면 생략하거나 provenance를 명확하게 구분한다.

### 8.4. Cross-Graph Mapping

| Relation | Source → Target | 필수 연결 증거 |
| --- | --- | --- |
| READS | FILE_ACCESS → FILE/심볼 | 실제 접근 근거 및 확인된 버전 |
| CHANGES | CHANGE → FILE/심볼 | Git hunk와 AST span 대응, `role:before/after` |
| TESTS_TARGET | TEST → TEST_FUNCTION/코드 | 확인된 test ID 또는 공개 coverage |
| FAILS_AT | FAILURE → FILE/심볼 | traceback/assertion 위치와 해당 버전 |
| SUSPECTS | FAILURE → 후보 코드 | 파생 진단 경로; 기본 탐색에서 제외 |

예시(합성 fixture):

```json
{
  "edge_id":"example-edge-01",
  "source_id":"example-exec-failure-21",
  "target_id":"example-repo-test-lock-snap03",
  "relation":"FAILS_AT",
  "observation_status":"observed",
  "evidence_ids":["example-event-21"],
  "attributes":{"snapshot_id":"snap03","basis":"traceback","line":41,"derived":false}
}
```

snapshot 미확정 사건은 해당 version의 코드 노드로 강제 연결하지 않는다. 확인된 과거 노드는 역사 데이터로 보존하고 최신 후보에 자동 병합하지 않는다. mapping이 불가능하면 후보 관계를 사실로 만들지 말고 unresolved record를 저장한다. SUSPECTS는 진단 trace 또는 파생 edge 배열로 분리하여 다음 실행의 기본 그래프 탐색 입력에 포함되지 않도록 한다.

### 8.5. 탐색 및 진단 출력

`DiagnosisCandidate`: `candidate_id`, `path`, `qualified_name?`, `snapshot_id`, `evidence_ids[]`, `rank`, `score`, `score_components`, `path_ref?`, `coverage_status`, `warnings[]`.

`DiagnosisTrace`: `trace_id`, `anchor_ids[]`, `graph_id`, `snapshot_id`, `allowed_relations`, `visited_count`, `depth_limit`, `branch_limit`, `visit_limit`, `termination:exhausted/visit_limit/time_limit/unavailable`, `truncated:bool`, `paths[]`.

각 path에는 시작 앵커, 순서대로 방문한 node ID, edge ID, 실제 이동 방향, 각 edge cost, 합산 비용 및 근거 ID를 포함한다. `score_components`의 flat/graph variant는 discriminated union으로 직렬화한다. Flat 결과에는 distance, graph path, graph-derived provenance 필드가 존재하지 않아야 한다.

방법론 §8의 가중치 탐색을 다음처럼 구현한다.

1. 관측된 앵커를 priority queue에 넣고 `(cost, depth, stable_node_id, stable_path_key)` 순으로 정렬한다.
2. `(node_id, depth)`별 최저 비용 상태만 확장하며 유효한 최신 코드 노드의 최저 비용 경로를 기록한다.
3. RETURNS 및 OBSERVED_AFTER 역방향, SAME_FAILURE_AS/RETRIES, 교차 관계 정방향, CALLS/IMPORTS/TESTS 양방향, CONTAINS 제한 확장을 허용한다. NEXT는 기본 비활성, 필요 시 pilot에서 최근 범위와 방향을 고정한다. SUSPECTS 금지.
4. 이웃은 비용·안정 ID 순으로 정렬해 최대 분기 20을 적용한다. 깊이 4, 방문 상태 200 제한을 적용한다. 상한에 걸린 탐색은 근사 결과로 표시한다. 동일 depth 상태에서 더 비싼 경로는 폐기한다.
5. 후보 점수는 `0.4/(1+d) + 0.3*o + 0.15*m + 0.15*u`의 pilot 값으로 계산한다. 경로의 관측 수준 `o`는 구성 엣지의 최소 근거 수준(1/0.5/0; 신규 구현 규칙)으로 시작한다. `u`는 실제 관측 범위가 충분한 경우에만 적용한다. 후보 top-k 8, 동률은 정규화 경로와 심볼 ID 순.

graph 구축/진단에 실패하면 공통 증거 기반 Flat fallback을 허용한다. 최초 배정 조건을 유지하고 graph 비용 및 fallback 이유를 기록한다.

## 9. Failure Detector와 Recovery Controller

### 9.1. 감지 입력 및 상태

Detector는 공통 `EvidenceView`, 현재 snapshot, 설정을 받는다. view에는 원천 관측, 검사 결과, 공개 diff 및 coverage만 노출한다. GraphArtifact, DCC 점수, 독립 평가 결과는 타입 및 생성 경로에서 제외한다.

| ID | 구현 규칙 | 미관측 처리 |
| --- | --- | --- |
| T1 | 같은 실패 지문이 서로 다른 연속 비교 가능 공개 검사에서 k=2회 발생하고 검증 개선 없음 | 검사 비교 불가면 UNKNOWN; 한 검사의 중복 로그는 제외 |
| T2 | 최근 w=6개의 비교 가능한 탐색 행동 중 중복 비율 >=0.67, 새 증거·검증 개선·실질 코드 진전 없음 | 6개 미만 또는 진전 근거 불충분이면 UNKNOWN |
| T3 | 같은 검사 조건의 이전 PASS → 현재 FAIL | 이전 성공 또는 비교 조건 불명확하면 보류 |
| T4 | 관측된 Codex 완료 선언과 공개 필수 검사 FAIL | 완료 선언/검사 상태 unknown이면 보류 |
| T5 | 실행·검사 증거 분류 불가 또는 환경 장애 | 안전한 지속 불가 시 INFRA 종료, 강제 전략 금지 |

T2의 탐색 키는 종류·경로·질의·범위의 정규화 조합이다. 이전 동일 키와 비교해 관련 파일 내용 변화나 신규 반환 증거가 없을 때만 중복으로 센다. pilot 구현에서는 정규화된 결과 digest의 변화, 공개 검사 상태 개선 및 관련 코드 diff를 진전의 보수적 대리지표로 사용한다. 의미적 진전을 완전히 판단하는 알고리즘은 아님을 기록한다. 증거 부족 시 정체를 확정하지 않는다.

동시 발생 시 `T3 > T1 > T2 > T4`를 primary로 사용하고 모든 신호를 보존한다. 안전 위반 및 지속 불가 인프라 장애가 항상 먼저 처리된다. T5가 일부 증거의 결측만 의미하는 경우, 다른 충분히 관측된 신호까지 무조건 폐기하지 않는다.

### 9.2. 개입 게이트

`checkpoint_key`는 현재 snapshot ID, 관련 공개 실패/행동 앵커, ledger cursor의 조합으로 구성한다. 단순 usage/log 수신만으로 새로운 실질 관측이 발생했다고 판단하지 않는다.

- 관측된 T1–T4, 검증된 후속 실행 경계, 잔여 턴·시간, 개입 횟수 미소진이 모두 필요하다.
- 동일 실패 상태에 신규 실질 관측 없이 개입을 다시 발행하지 않는다.
- `GateDecision`에 `NO_TRIGGER`, `INSUFFICIENT_EVIDENCE`, `NO_NEW_OBSERVATION`, `DUPLICATE_CHECKPOINT`, `RECOVERY_CAP`, `GLOBAL_LIMIT`, `NO_SUPPORTED_CONTINUATION` 등의 reason code를 남긴다.

### 9.3. 전략 선택 계약

| 전략 | 입력 조건 | 전달 목표 및 검증 계획 |
| --- | --- | --- |
| REVIEW_REGRESSION | T3 + 변경/실패의 관련 근거 | 변경 영향 조사, 기존 동작 보존, 승인 회귀 검사 |
| REFRESH_EVIDENCE | 유효 트리거 + stale 근거로 현재 판단 제한 | 현재 snapshot의 코드와 근거 재확인 |
| RELOCALIZE | T1/T2 + 근거 있는 대안 코드 후보 | 이전 가정 재검토, 관련 의존성 조사 및 공개 검사 |
| GENERIC_RETRY | 유효 트리거이나 구체 전략 근거 부족; B/C 기본 | 관측 오류와 변경 재검토, 수정 후 검증 |
| CONTINUE | 안전한 정상 지속 | 외부 복구 지침 없이 지원된 네이티브 지속 |
| STOP | 정상 완료, 한도 또는 안전한 지속 불가 | 종료 및 결과 보존 |

TYPED 정책의 구체 전략 우선순위는 표 순서와 방법론 §9를 따른다. GENERIC 정책은 진단 후보를 일반 복구 근거로 사용할 수 있으나 typed 전략 ID를 선택하지 않는다. Graph fallback에서 구체 관계가 불충분하면 GENERIC_RETRY로 전환한다. 전략 객체에는 근거 ID·타깃·사전 승인 검사 ID를 포함하며 파일 변경이나 테스트를 직접 실행하지 않는다. DCC가 전략 ID를 변경할 수 없다.

`recovery.adherence.assess`는 전략별 예상 행동과 충분히 관측된 다음 실행 결과를 비교한다. 예를 들어 RELOCALIZE의 대상 파일 접근 또는 해당 diff가 관측된 경우 performed의 근거가 될 수 있다. 미관측을 미수행으로 단정하지 않는다. 각 판단 기준 및 관측 가능성은 pilot 및 평가 프로토콜에서 고정한다.

## 10. Dynamic Context Compiler (DCC)

### 10.1. 후보 및 패키지 스키마

```json
{
  "candidate_id":"example-auth-guide-001",
  "kind":"optional_guidance",
  "source":"docs/auth-guide.md",
  "scope":["src/auth/"],
  "revision":"v2",
  "snapshot_id":"snap03",
  "content_hash":"<sha256>",
  "validity":"CURRENT",
  "provenance":"observed",
  "text":"인증 모듈의 공개 검사를 참고한다.",
  "evidence_ids":[],
  "path_refs":[]
}
```

허용 kind: `optional_guidance`, `code_reference`, `code_excerpt`, `failure_evidence`, `change_evidence`, `prior_attempt`, `relation_evidence`. `path_refs` 및 relation_evidence는 Graph 활성 복구 입력에서만 허용한다. 후보 ID는 출처·스냅샷·범위·내용 해시를 기반으로 안정적으로 생성한다. 필수 지침은 Candidate가 아니라 FixedRequirements로 전달한다. 원문이 크면 구조적 경계에서 분할하고 chunk에 원본 출처와 span을 유지한다.

| 객체 | 필수 필드 |
| --- | --- |
| CompileRequest | `stage:INITIAL/RECOVERY`, `policy`, `fixed`, `snapshot_id`, `candidates[]`, `core_candidate_ids[]`, `diagnosis?`, `strategy?`, `cap_tokens`, `estimator_id` |
| CandidateDecision | `candidate_id`, `selected:bool`, `reason_code`, `score?`, `components?`, `duplicate_of?`, `estimated_tokens?` |
| ContextPackage | `package_id`, `stage`, `policy`, `snapshot_id`, `core_ids[]`, `selected_ids[]`, `decisions[]`, `rendered_supplement`, `estimated_supplement_tokens`, `estimator_id`, `package_hash`, `warnings[]` |
| CompileResult | `status:OK/CORE_OVER_CAP/INVALID_INPUT/ERROR`, `package?`, `error?` |
| RenderedMessage | `fixed_hash`, `task_hash`, `supplement_hash?`, `message_hash`, `text`, `estimated_supplement_tokens`, `estimated_full_tokens`, `estimator_id` |

모든 조건에 공통인 최소 근거(core)는 복구 트리거 설명, 전략을 뒷받침하는 최소 관측 증거, 승인된 다음 검사 지침을 포함한다. 초기에는 실패 및 전략 정보가 없다. core와 optional 후보 모두 권한·출처 경계 검사를 통과해야 한다. 사용자 요청 및 필수 지침은 cap 최적화에서 제외하지만 전체 입력 비용에는 포함한다.

### 10.2. DEFAULT와 DCC

| 처리 단계 | DEFAULT (B/D) | DCC (C/E) |
| --- | --- | --- |
| 공통 안전/역사 표시 | 적용 | 적용 |
| 기본 검색 또는 진단 순위 | 적용 | 후보 입력으로 적용 |
| 명시적 무효 보조 정보 제외 및 유효성 점수 | DCC 전용 처리 미적용 (공통 안전 검사는 별개) | 적용 |
| 정확 중복 후보 병합·유사도 penalty | 미적용 | 적용 |
| 선택 기준 | 안정된 기존 순위로 순차 패킹 | 한계 효용 밀도 기반 greedy packing |
| 최종 상한과 필수 지침 guard | 동일 | 동일 |

초기 B/D와 C/E는 각각 동일한 공통 후보·정렬·추정기를 사용한다. DCC에는 그래프 자체를 넘기지 않고 Diagnosis에서 반환한 허용된 후보와 provenance만 전달한다. 정책 비교에서 제외된 보조 정보의 원본 파일은 여전히 Codex의 허용된 탐색 범위에 있다.

### 10.3. DCC 점수 및 예산 계산

- `r_i`: 고정 토큰화 및 BM25의 후보 점수를 최대 양의 점수로 정규화해 [0,1]로 clamp. 전체 점수가 0 이하이면 모두 0. BM25 구현/토큰화 버전 고정.
- `a_i`: 직접 scope·심볼 일치 1, 상위 범위 일치 0.5, 불일치/unknown 0.
- `v_i`: CURRENT/HISTORICAL 1(역사 라벨 필수), UNCERTAIN 0.5, INVALID는 보조 후보에서 제외. 날짜가 오래됐다는 이유만으로 무효 처리하지 않는다.
- `f_i`: 실패 앵커 관련도와 전략 대상 관련도 평균. 직접 일치 1, 제공된 관련 후보 0.5, 불일치 0. 초기에는 0.

초기 가중치 `(0.5,0.3,0.2,0)`, 복구 가중치 `(0.25,0.2,0.2,0.35)`를 사용한다. exact duplicate는 정규화 본문 해시, 의미 있는 version 및 provenance를 함께 확인하고 합친 출처를 보존한다. 근사 중복도는 의미 있는 코드 숫자와 식별자를 유지한 토큰 집합 Jaccard로 시작한다. penalty λ=0.2.

```python
def select_dcc(request: CompileRequest, estimator: TokenEstimator) -> CompileResult:
    # 의사코드: renderer는 전략/라벨/구분자까지 포함한 supplement를 구성한다.
    pool = validate_and_score_optional_candidates(request)
    selected = required_core(request)
    if estimate_rendered_supplement(selected, request, estimator) > request.cap_tokens:
        return core_over_cap()
    while pool:
        # 모든 후보의 실제 재직렬화 증분과 상한을 확인한다.
        feasible = rank_feasible_positive_utility(pool, selected, request, estimator)
        if not feasible:
            break
        best = feasible[0]  # utility 내림차순, candidate_id 오름차순
        selected.append(best)
        pool.remove(best)
    return build_and_validate_package(selected, request, estimator)
```

한계 효용 밀도는 `U=(s_i - λ*Σd_ij)/max(1,Δt_i)`를 따른다. 매 선택마다 보조 메시지를 다시 직렬화하여 cap을 초과하지 않는지 검증한다. 큰 후보는 결정적으로 분할할 수 있으나 실패 메시지·코드의 의미를 손상시키지 않는다. 선택/제외 reason code: `CORE_REQUIRED`, `SELECTED`, `INVALID`, `MERGED_DUPLICATE`, `OVER_CAP`, `NON_POSITIVE_UTILITY`, `LOWER_RANK`, `UNSUPPORTED_PROVENANCE`, `SECURITY_REJECTED`.

TokenEstimator 계약:

```python
class TokenEstimator(Protocol):
    estimator_id: str
    def estimate(self, text: str) -> int: ...
```

모델 호환 tokenizer가 확인되면 사용하고, 그렇지 않으면 명시적인 공통 추정기를 pilot에서 고정한다. byte/문자 기반 근사를 실제 모델 토큰이라고 표기하지 않는다. 추정기 ID에 구현/버전 정보를 넣는다. 실제 공급자 usage와 추정 보조 크기는 별도 필드다.

### 10.4. 공통 메시지 형식 및 Guard

```text
[Fixed requirements and permissions]
<unchanged mandatory requirements>
[Original task]
<unchanged task>
[Recovery strategy]
<strategy ID / objective, only if recovery>
[Observed failure and prior attempts]
<core evidence, only if recovery>
[Selected supplementary evidence]
<source / snapshot / validity / body>
[Hypotheses]
<explicitly uncertain candidates, if any>
[Suggested next actions and approved visible checks]
<approved actions/check IDs, only if recovery>
```

B–E의 역할, 섹션 순서, 렌더링 escape 및 전달 경로를 공통으로 유지한다. 보조 영역 cap에는 빈 섹션 구분자, 출처 라벨, 전략 지침 등 실제 외부 추가 텍스트 오버헤드까지 포함한다. 필수 지침 및 요청의 hash가 원본과 일치하는지 검증한다. 저장소의 텍스트는 증거이지 권한 확대 명령이 아니다. Guard만으로 prompt injection이나 권한 우회를 완전히 차단할 수 있다고 주장하지 않고 실행 격리를 별도로 강제한다.

CORE_OVER_CAP일 때 Orchestrator가 한 번만 공통 GENERIC_RETRY fallback을 요청할 수 있다. 여전히 최소 근거가 상한에 들어가지 않으면 안전하게 종료한다. DCC에서 보조 cap을 늘리거나 필수 정보를 자르지 않는다. fallback 시 실제 비용과 시도 횟수를 보존한다.

## 11. Workflow 상태 머신 및 예산

### 11.1. 허용 전이

| 현재 | 다음 | 조건 |
| --- | --- | --- |
| INIT | INITIAL_CONTEXT / RUN / FINALIZE | 검증 성공(B–E/A), 준비 실패 |
| INITIAL_CONTEXT | RUN / FINALIZE | guard 및 한도 통과, 안전한 패키지 실패 |
| RUN | OBSERVE | 실행 단위 종료/오류를 가능한 만큼 관측 |
| OBSERVE | DECIDE / FINALIZE | ledger 반영, 지속 불가 |
| DECIDE | DIAGNOSE / RUN / FINALIZE | 유효 개입, 지원된 정상 지속, 종료 |
| DIAGNOSE | SELECT_STRATEGY / FINALIZE | 진단/flat fallback, 안전한 진행 불가 |
| SELECT_STRATEGY | RECOVERY_CONTEXT / RUN / FINALIZE | 복구, CONTINUE, STOP |
| RECOVERY_CONTEXT | RUN / FINALIZE | 패키지 및 예약 성공, fallback 후에도 불가 |
| FINALIZE | REPORT | 실행 제어부 종료 및 공개 리포트 |
| FINALIZE (실험 실행기) | EVALUATE / REPORT | evaluable patch 존재 또는 없음 |
| EVALUATE | REPORT | 독립 평가 결과/오류 기록 |
| REPORT | terminal | 추가 모델 호출 금지 |

`RECOVERY_CONTEXT` 내부 fallback은 bounded operation이며 새 그래프 진단으로 무제한 되돌아가지 않는다. 전이 우선순위: 안전 위반/취소 → 전역 한도 및 runtime 지속 불가 → T1–T4 + gate → 정상 완료/네이티브 지속 여부. 공개 필수 검사 실패가 있는 완료 선언은 T4를 먼저 고려한다. 개입 상한만 도달한 경우 외부 개입을 금지하되 검증된 정상 지속은 허용한다.

### 11.2. 회계 및 계측

| 항목 | 계수 시작점/규칙 |
| --- | --- |
| 전체 시간 | 공통 요청 검증 시작 직전 monotonic 기준부터 패치 고정/안전 종료까지 |
| 실행 단위 | adapter 실행 제출을 예약하는 시점에 1회 차감; 중복 ID 차감 금지 |
| 복구 개입 | 유효한 복구 패키지와 후속 실행 예약이 완료된 시점에 1회 차감 |
| 내부 전이 | 상태 전이마다 차감; 한도 도달 시 FINALIZE/REPORT 정리 경로만 예외 |
| 정리 시간 | 신규 실행 시작 전 공통 reserve 확보; 실제 초과 별도 기록 |
| 모델 토큰 | 관측된 단위별 사용량 합 또는 검증된 누적 차분; 미관측은 null |
| 로컬 오버헤드 | snapshot, graph build/query, DCC, 공개 검사, fallback의 경과 시간 |
| 독립 평가 | 별도 시간·비용 필드로 기록; 복구 루프 입력 금지 |

개입 예약 후 전달 실패 시 자동 환급하지 않는다. intervention ledger는 `DECIDED`, `PACKAGE_READY`, `RESERVED`, `DELIVERED`, `DELIVERY_FAILED`, `OUTCOME_OBSERVED` 등의 사건을 append한다. 전달 여부가 불명확하면 `UNKNOWN_DELIVERY`로 기록하고 중복 실행을 막는다. 준비 실패는 복구 예약을 소진하지 않더라도 실제 처리 비용과 전이 한도에 포함한다. 인프라 재시도와 모델 작업 복구는 구분하며 본실험 중 임의 재시도를 하지 않는다.

`UsageRecord`: `usage_id`, `run_id`, `turn_id?`, `stage`, `source`, `usage_mode`, `input_tokens?`, `cached_input_tokens?`, `output_tokens?`, `reasoning_tokens?`, `total_tokens?`, `elapsed_ms`, `cost_amount?`, `currency?`, `cost_kind:ACTUAL/ESTIMATED/UNKNOWN`, `coverage`, `raw_ref?`. cached/reasoning이 상위 필드의 부분집합인지 probe에서 확인하고 중복 합산하지 않는다. 같은 usage event는 한 번만 집계한다. 누적치 초기화가 불분명하면 차분을 추정하지 않는다.

### 11.3. 실행 예외 코드

| 코드 | 처리 |
| --- | --- |
| CONFIG_INVALID / ISOLATION_UNVERIFIED | 실행 시작 거부 |
| AUTH_FAILED / RUNTIME_UNAVAILABLE | 안전 정리, 환경 장애 기록 |
| EVENT_PARSE_UNKNOWN / USAGE_UNKNOWN | raw 보존, 결측 표시, 관측 가능 범위 내 지속 |
| AST_PARSE_PARTIAL / GRAPH_UNAVAILABLE | partial 표시, 평면 fallback 가능 |
| SNAPSHOT_INCOMPLETE / VERSION_MISMATCH | 역사 증거 유지, 현재 후보 강제 연결 금지 |
| CORE_OVER_CAP / GUARD_REJECTED | 공통 fallback 또는 종료, 필수 지침 유지 |
| TURN_TIMEOUT / GLOBAL_LIMIT / USER_CANCEL | 추가 호출 금지, 취소와 정리, 가능한 부분 패치 |
| PROCESS_NOT_QUIESCENT | 안전한 패치 고정 금지, 실행 환경 격리/정리 |
| PATCH_FREEZE_FAILED / ARTIFACT_WRITE_FAILED | incomplete 및 실패 상태 보존, 성공으로 표시 금지 |
| EVALUATION_INFRA_ERROR | 독립 평가 결과에만 반영; 에이전트에게 반환 금지 |

## 12. 실행 산출물과 저장 스키마

```text
results/<experiment-id-or-local>/<run-id>/
├── manifest.json
├── events.raw.jsonl
├── restricted/                 # 원시 payload, stderr 등
├── evidence.jsonl
├── checks.jsonl
├── transitions.jsonl
├── snapshots/
├── graphs/
├── interventions.jsonl
├── context.jsonl
├── usage.jsonl
├── final.patch
└── report.json
```

모든 경로는 제어부가 생성·검증한다. 에이전트가 결과 ledger 또는 평가기 결과를 수정할 수 없도록 workspace와 결과 경계를 분리한다. raw payload는 제한된 저장 공간에서만 읽을 수 있다. manifest/report/patch는 임시 파일을 통한 원자적 기록을 우선한다. 비정상 종료 시 가능한 기록과 incomplete marker를 남기며 원래 로그는 덮어쓰지 않는다.

| 파일 | 최소 계약 |
| --- | --- |
| manifest.json | run/task/experiment ID, 조건, config hash, 정책/코드/모델/runtime/환경 버전, 초기 commit, capability ref, 실행 한도 |
| transitions.jsonl | sequence, from/to phase, reason code, elapsed_ms, snapshot/ledger cursor, intervention ID? |
| interventions.jsonl | intervention ID, lifecycle event, trigger/anchors/diagnosis/strategy/package refs, fallback, 전달 상태, 수행 여부 |
| context.jsonl | package ID, stage, policy, candidate/decision refs, score components, estimator, size/hash, fallback/guard 결과 |
| graphs/ | GraphArtifact와 DiagnosisTrace, unresolved 기록, 구축 시간 |
| usage.jsonl | UsageRecord 및 집계 근거 |
| report.json | RunReport; 종료·patch·검사·관측 비용·제약 및 warnings |

`RunReport`: `schema_version`, `run_id`, `condition`, `manifest_hash`, `stop_reason`, `completion_declared?`, `public_check_status`, `frozen_patch?`, `evaluable`, `turns_used`, `recoveries_reserved`, `recoveries_delivered?`, `usage_summary`, `elapsed_ms`, `intervention_refs[]`, `artifact_status:complete/incomplete`, `warnings[]`. 사용자 보고에는 private test 성공 여부가 없다. 연구용 별도 결과는 평가기가 반환한 값을 `run_id`로 연결한다. 원시 이벤트의 개인정보·자격 증명은 공유 자료에서 비식별 처리하고 공개 전 검토한다.

## 13. 실험 실행기, 체크포인트 및 평가 계약

### 13.1. ExperimentManifest

필수 필드: `experiment_id`, `schema_version`, `protocol:end_to_end/checkpoint/ablation`, `task_manifest_ref`, `task_manifest_hash`, `conditions[]`, `repetitions`, `execution_order_seed`, `locked_config_ref`, `config_hash`, `runtime_probe_ref`, `environment_hash`, `code_version`, `output_root`. 과제 수와 반복 횟수, 구체적인 실행 순서는 후속 실험설계에서 고정한다. `execution_order_seed`는 조건 실행 순서의 재현용이지 모델의 난수 제어를 의미하지 않는다.

### 13.2. CheckpointRecord

`checkpoint_id`, `source_run_id`, `task_id`, `initial_policy_hash`, `snapshot_id`, `workspace_bundle_ref/hash`, `common_evidence_ref/hash`, `trigger_ids`, `anchor_ids`, `fixed_requirements_hash`, `common_history_ref/hash`, `environment_hash`, `remaining_limits`, `initial_usage_refs`, `session_clone_verified:bool`.

동일 외부 상태 복제 시 관련 untracked 파일까지 복원하고 snapshot hash가 같음을 확인한다. 사전 정의된 첫 eligible 자연 실패를 checkpoint로 선택하며 hidden test나 최종 판정을 이용하지 않는다. 분기는 동일 원천 증거 및 공통 이력 요약을 제공하고, 각 조건의 그래프/컨텍스트를 독립 재계산한다. 내부 세션 복제 미검증이면 모든 B–E 분기에 동일한 NEW_SESSION 정책을 적용한다. 분기별 독립 workspace와 새 run ID를 사용하고 초기 비용/복구 비용을 분리한다.

### 13.3. 독립 평가기

```python
class IndependentEvaluator(Protocol):
    def evaluate(self, patch: FrozenPatch, private_task_id: str) -> EvaluationResult: ...
```

위 계약은 실험 실행기가 별도 프로세스/격리 환경에 요청하는 논리 인터페이스다. Orchestrator 및 에이전트 런타임에는 evaluator 객체, private task spec, 판정 데이터 접근 권한을 부여하지 않는다.

`EvaluationResult`: `run_id`, `task_id`, `patch_hash`, `evaluation_environment_hash`, `status`, `functional_status`, `regression_status`, `instruction_status`, `duration_ms`, `error_code?`, `restricted_details_ref?`. 전체 PASS는 사전에 확정된 기능·회귀·필수 제약 판정을 모두 충족할 때만 부여한다. 깨끗한 기준 환경에서 패치 적용 실패와 평가 환경 장애를 구별한다. 세부 판정 및 제외 규칙은 평가 프로토콜에서 확정한다. 최종 패치에 대한 평가 결과는 재시도 정책으로 반환하지 않는다.

### 13.4. 확장 ablation 인터페이스 (P1)

- Unlinked: 같은 두 그래프와 원천 접근, cross-graph traversal 금지; 독립 시작점 및 결과 결합 규칙 고정.
- Typed vs Generic: 동일 checkpoint, graph 진단 결과, 보조 evidence 패키지를 고정하고 전략 메시지만 교체한다. 최종 렌더링 cap을 양쪽에서 재검증한다.
- Initial-only / Recovery-only DCC: 단계별 정책 교체, 그 외 조건 동일.
- DCC validity/dedup ablation: 공통 권한 guard는 제거하지 않는다.

기본 A–E만으로 그래프 연결 또는 전략 전환 각각의 순수한 독립 효과를 주장하지 않는다. 세부 ablation 실행은 실험설계와 가용 예산에 따른다.

## 14. CLI 인터페이스

아래는 구현할 CLI 계약이며 이미 동작하는 명령이 아니다.

```bash
adaptive-codex run --repo ./my-project \
  --task "로그인 실패 횟수에 따른 계정 잠금 오류 수정" \
  --config configs/default.yaml

adaptive-codex probe --config configs/pilot.yaml
adaptive-codex report --run-id <run-id> --results-root results
adaptive-codex experiment --manifest configs/experiments/main.yaml
```

| 명령 | 필수 옵션 | 결과 |
| --- | --- | --- |
| run | --repo, --task 또는 --task-file(상호 배타), --config | 일반 사용자 모드 E 기본, 패치와 공개 리포트 |
| probe | --config | RT01–RT07 검증 기록; 외부 호출이 필요한 테스트는 승인된 fixture 사용 |
| report | --run-id, --results-root | 저장된 결과 읽기, 모델 재호출 없음 |
| experiment | --manifest | 잠긴 조건에 따라 실험 실행, 독립 평가 및 run refs |

선택 옵션 `--budget` 하나로 전체 자원을 혼동하지 않도록 시간·턴·보조 토큰 단위를 설정 파일로 구분한다. 본실험 조건의 CLI 임의 override 금지. 일반 사용자 모드에서 조건 변경을 지원하는 것은 P1이다.

제안 exit code: 0=요청 처리가 정상 종료되어 리포트 생성(작업 성공과 별개), 2=입력/설정 오류, 3=실행 한도 또는 안전한 지속 불가, 4=runtime/환경 오류, 5=artifact 오류, 130=사용자 취소. 독립 평가 실패는 평가 결과에 기록하고 CLI 실행 성공과 구별한다. 실제 사용 예시와 exit code는 구현 테스트로 확인한다.

## 15. 보안 및 비기능 요구사항

| ID | 요구사항 | 확인 방법 |
| --- | --- | --- |
| SEC01 | 원본 저장소와 실행 복제본 분리 | 원본 hash 비교, 수정 시도 차단 |
| SEC02 | private fixture·정답·호스트 비밀 접근 금지 | 격리 경계 밖 파일 접근 테스트 |
| SEC03 | 경로 탈출·symlink·명령 인자 우회 방지 | 악성 fixture와 argv 검사 |
| SEC04 | runtime 인증 정보의 최소 전달, 로그/메시지 제외 | 비밀 canary 및 공유 리포트 검사 |
| SEC05 | 승인되지 않은 권한 상승·네트워크·자동 push 금지 | 권한·네트워크 정책 확인 |
| SEC06 | 시간 제한 및 취소 후 프로세스 정리 | 자식 프로세스 포함 실제 실행 검증 |
| SEC07 | 결과 ledger 및 평가 판정 파일 변조 방지 | workspace에서 쓰기 시도 차단 |
| NFR01 | 동일 정책 입력에 결정적 정렬/선택 | 동일 fixture 반복 및 hash 일치 |
| NFR02 | 모든 실패/중단 비용 및 이유 보존 | 장애 주입 테스트 |
| NFR03 | 스키마 및 버전 추적 가능 | manifest·artifact 참조 무결성 |
| NFR04 | 모델·런타임 비결정성 분리 | 정책 결정성 테스트와 실제 반복 실행 구분 |

실제 안전성은 설계 문서나 모의 테스트만으로 입증되지 않는다. 격리 프로파일이 검증되지 않으면 실제 연구 실행을 시작하지 않는다. 자체 코드 및 공개 과제의 라이선스·데이터 공개 범위도 별도로 검토한다.

## 16. 테스트 요구사항과 수용 기준

### 16.1. 테스트 ID 및 입력–기대 결과

| ID | 계층 | 입력/조건 | 기대 결과 | 추적 |
| --- | --- | --- | --- | --- |
| UT01 | 단위 | 동일 snapshot manifest, 파일 순서 다름 | snapshot hash 일치 | M02 |
| UT02 | 단위 | 파일 삭제/rename/함수 이동 | 과거 노드 자동 병합 금지 | M02 |
| UT03 | 단위 | AST 문법 오류 및 동적 호출 | FILE 유지, partial/unresolved | M04 |
| UT04 | 단위 | 중복 raw 이벤트, 동일 실패 두 검사 | ledger 중복 제거, 지문 관계 직전 연결 | M04/M05 |
| UT05 | 단위 | 경로만 포함한 명령, version unknown | READS/FAILS_AT 강제 생성 금지 | M02/M04 |
| UT06 | 단위 | 합성 이중 그래프에서 failure→test→code | 허용 경로 및 근거 일치, SUSPECTS 제외 | M03 |
| UT07 | 단위 | 동일 노드의 깊이별 경로, 고차수 노드 | 깊이·분기·방문 제한, truncated 표시 | M03 |
| UT08 | 단위 | T1 1회/2회, T2 5개/6개, T3 동일/상이 검사 | 감지·보류 조건 정확, primary 우선순위 | M05 |
| UT09 | 단위 | 완료 선언 + 공개 FAIL, T5 미관측 | T4 또는 UNKNOWN 구분 | M05 |
| UT10 | 단위 | stale/alternative/회귀 evidence | 전략 우선순위 및 일반 fallback | M05/M08 |
| UT11 | 단위 | INVALID/HISTORICAL/UNCERTAIN 후보 | DCC 정책에 따른 제외·역사 표시 | M07 |
| UT12 | 단위 | 중복 후보/동률/거대 후보 | 출처 보존, 결정적 순서, 의미 보존 | M07 |
| UT13 | 단위 | cap 경계, 전략 오버헤드, core 초과 | 최종 직렬화 검사, 공통 fallback/종료 | M01/M07 |
| UT14 | 단위 | 누적/증분/미관측 usage | 중복 합산 금지, null 유지 | M11 |
| UT15 | 단위 | 같은 checkpoint에서 재개입, 예약 후 실패 | 추가 개입 금지, 예약 환급 금지 | M09 |
| CT01 | 계약 | fake adapter의 정상/unknown/parse error | 공통 타입과 provenance 보존 | M04 |
| CT02 | 계약 | JSON schema 버전/필수 필드/참조 오류 | 명시적 검증 실패 | M11 |
| CT03 | 계약 | 같은 초기 후보와 B/D, C/E | 초기 외부 패키지 byte 동일 | M06 |
| CT04 | 계약 | 같은 ledger와 B–E | trigger·anchor 동일 | M05/M06 |
| CT05 | 계약 | A/B/C에 graph spy 주입 | 호출 0, 그래프 파생 정보 없음 | M06 |
| CT06 | 계약 | A/B/D에 DCC spy 주입 | DCC 호출 0, 전용 점수 없음 | M06 |
| CT07 | 계약 | A의 초기/후속 요청 | 외부 보조 패키지·복구 메시지 없음 | M06 |
| IT01 | 통합 | fake 정상 실행→공개 검사→종료 | 추가 개입 없이 패치·리포트 생성 | AC05/AC07 |
| IT02 | 통합 | fake 반복 실패→graph/flat→전략→후속 실행 | 실제 호출 요청 및 근거 추적 | AC03/AC04 |
| IT03 | 통합 | graph 오류/DCC core over cap | 공통 fallback, 배정 조건 유지 | AC06 |
| IT04 | 통합 | 예산/취소/프로세스 미정리 | 종료, 불완전 기록 보존 | AC05/AC06 |
| IT05 | 통합 | 신규 파일 포함 공통 checkpoint B–E 복제 | 외부 상태 hash 및 공통 이력 일치 | AC08 |
| IT06 | 통합 | 버그 초기 상태와 reference patch 평가 | 독립 평가 판정 구분, 피드백 유입 없음 | AC08 |
| IT07 | 통합 | report/patch write 실패 | artifact incomplete, 원인 및 기존 기록 보존 | AC06/AC07 |
| ST01 | 격리 | SEC01–SEC07 공격성 fixture | 접근/쓰기/권한 확대 차단, 정리 확인 | M10/AC08 |
| LT01 | 실제 | 승인된 Python fixture, 실제 Codex | RT01/02/05, 실제 로그와 patch | AC01 |
| LT02 | 실제 | 실제 공개 실패 이후 지원된 후속 실행 | 전달 증거 및 후속 관측, 성공은 별도 평가 | AC04 |
| LT03 | 실제 | timeout, usage, 독립 환경 검증 | RT04/06/07 확인 | AC06/AC08 |
| LT04 | 실제 | 동일 과제 A–E 및 독립 평가 smoke test | 조건·로그·비용·결과 연결 | AC08/AC09 |

T2 테스트의 6개 구간에서 threshold 0.67을 그대로 적용하면 4/6은 0.666…으로 임계값 미만이다. 5/6부터 충족한다. 이것은 0.67이라는 pilot 값을 그대로 구현한 결과이며, 의도한 기준이 4/6이라면 본실험 전 설정을 정확한 분수 또는 다른 임계값으로 변경해야 한다. 부동소수점 반올림으로 임의 해석하지 않는다.

### 16.2. 실행 방법 및 완료 증거

```bash
pytest tests/unit tests/contract
pytest tests/integration tests/isolation
# live는 별도 marker, 유효한 인증·격리 프로파일 및 명시적 실행 승인 필요
pytest tests/live -m live
```

위 명령 역시 구현 목표다. fake 테스트는 소프트웨어 계약을 확인하지만 실제 Codex 실행이나 연구 성능 증거가 아니다. live 테스트는 유료 모델 호출·실행 환경 접근이 발생할 수 있으므로 CI 기본 실행에서 제외한다. AC01–AC08을 충족한 뒤 본실험을 진행하고, AC09는 실제 기록 기반 정적 보고서로도 충족 가능하다.

### 16.3. MVP Definition of Done

- F01–F12(P0)의 연결된 실제 실행, AC01–AC08 및 관련 M01–M11의 검증 결과 확보.
- RT01–RT07 검증 결과 및 관측 불가 기능의 범위 명시. 안전 경계 실패 시 실험 중단.
- 실제 공개 실패를 관측하고 후속 메시지를 전달한 로그가 최소 한 건 존재. 복구 성공 여부는 별도 지표로 기록.
- A–E 정책 매핑 및 정보 누출 방지 테스트 통과. 동일 과제의 독립 평가와 비용 계측 가능.
- 정상/실패/중단 로그를 보존하고 CLI로 패치와 보고서를 조회할 수 있음.
- 연구 성능 목표(성공률 향상, 비용 감소 등)는 구현 완료와 별도로 실험을 통해 검증한다.

## 17. 구현 의존성과 변경 관리

| 단계 | 계약 고정 대상 | 병행 작업 |
| --- | --- | --- |
| S0 | domain, RuntimeAdapter, Workspace, CheckResult | runtime probe, 격리 평가기 |
| S1 | EvidenceLedger, state machine, PolicySet | A/B 및 종료/회계 테스트 |
| S2 | Candidate, CompileRequest, ContextPackage, Renderer | DCC, 기본 정책 및 parity 테스트 |
| S3 | GraphArtifact, Trigger, Diagnosis, Strategy | graph/recovery 및 제한 탐색 |
| S4 | CheckpointRecord, ExperimentManifest, RunReport | E 통합, 자동 비교 평가 |
| S5 | 분석용 export, trace 조회 | 시각화, 선택적 ablation |

Runtime & Harness / Graph & Recovery / Context & Evaluation의 3인 분담안은 유지한다. 공통 domain 및 evidence 계약을 먼저 합의하고 그래프와 DCC를 병행한다. 본 문서의 코드 블록은 스켈레톤 및 설계 계약이지 완성된 소스 코드가 아니다. 모든 명세 변경은 이유·영향받는 테스트·비교군의 공정성·적용 버전을 기록한다.

## 18. 미확정 사항 및 후속 문서 연계

| 사항 | 상태 및 결정 방법 |
| --- | --- |
| 실제 Codex 모델 ID, CLI/SDK 버전, 인증 방식 및 resume 명령 | RT01–RT07 후 고정. 공식 자료만으로 실제 계정 지원을 보장하지 않음 |
| 시간·턴 상한, 토큰 강제, 추정기 | pilot 실측과 비용 확인 후 확정 |
| 정확한 격리 프로파일, OS, 네트워크·보존 정책 | 안전 경계 통합 테스트 및 팀 검토 |
| BM25 구현/토큰화 및 graph 근거 점수 집계 | 본 문서 신규 pilot 후보, 개발용 과제에서 고정 |
| T2 임계값 및 진전 대리지표 | 방법론 수치 유지, 4/6 경계 등 pilot 확인 |
| 파일 범위·최대 크기·snapshot 복원 지원 | benchmark 환경 검증 후 고정 |
| 과제 수·반복 횟수·통계·ablation 우선순위 | `EXPERIMENT_DESIGN.md` |
| 과제 manifest, 공개 검사, private 평가 정의 | `BENCHMARK_SPEC.md` |
| 성공 판정·결측·제외·복구 분모·시간 집계 | `EVALUATION_PROTOCOL.md` |
| 단계별 담당자·작업 이슈·일정 | `IMPLEMENTATION_PLAN.md` |

## 19. 근거 및 설계 추적성

| 출처 | 확인한 위치 | 반영 내용 |
| --- | --- | --- |
| `RESEARCH_PROPOSAL.md` | §3, §5–6, §8–10 | 범위, 연구 질문, 비교군, 실용성 및 역할 |
| `PROJECT_OVERVIEW.md` | §4, §6, §10–12 | F01–F14, CLI, AC01–AC09, 역할 |
| `LITERATURE_REVIEW.md` | §5–10 | 구성요소 효과 분리, 신규성 표현 제한, 기술 검증 |
| `METHODOLOGY.md` | §4–11, §13–16 | 스키마, 탐색, T1–T5, DCC, 전략, pilot 값, M01–M11 |
| `SYSTEM_ARCHITECTURE.md` | §4–18 | 모듈 경계, 상태 머신, RT01–RT07, 파일 및 평가 격리 |

공식 기술 참고 자료(2026-11-25 확인):

- [O1] OpenAI Developers, [Codex as a platform: build on the open agent harness](https://developers.openai.com/blog/codex-as-a-platform). CLI, SDK, app-server의 통합 경로 확인.
- [O2] OpenAI Developers, [Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills). 구조화된 CLI JSON 이벤트 활용 사례 확인.

위 자료는 연동 후보의 근거이며 본 프로젝트의 실제 계정 기능 검증 또는 성능 검증을 대신하지 않는다. 기존 문서의 연구 질문과 pilot 값을 유지했으며, 새로 제안한 사항은 Python 내부 타입·enum·파일 구조·CLI 하위 명령·exit code·JSON 스키마·회계 규약·구체적인 테스트와 일부 pilot 기본값이다. `COMMAND_RESULT`는 기존 RETURNS 관계를 구현하기 위한 세부 노드 타입이며 연구 가설을 변경하지 않는다. 기존 문서는 수정하지 않았다.

---

**기술 명세의 완료 목표:** 실제 Codex 실행을 공통 계약으로 제어하고, 실패 관련 그래프 진단·전략·컨텍스트 최적화가 서로 분리된 정책으로 작동하며, 동일한 기준으로 실행 결과와 비용을 검증할 수 있는 최소 소프트웨어를 구현한다.
