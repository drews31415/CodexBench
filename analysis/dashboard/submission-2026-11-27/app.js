/* AAH results dashboard (static). Reads only the embedded data: either the versioned public store
 * (public_data/: catalog, releases, per-experiment exports) or the frozen submission snapshot bundle, which
 * normalize() maps onto the same model. Every view is a pure function (state) -> HTML string so it can be
 * rendered and tested without a browser; the DOM glue at the bottom only runs in a page.
 * All interpolated values pass through esc(). Replay re-reads stored records; it never calls a model. */
(function (root) {
  "use strict";

  const COND = ["A", "B", "C", "D", "E"];
  const COND_LABEL = { A: "A Vanilla", B: "B Flat Retry", C: "C Context-only", D: "D Graph-only", E: "E Adaptive Harness" };
  const LATEST = "__latest__";
  const STATUS_LABEL = {
    VALID: "유효", VALID_EXPLORATORY: "유효 · 탐색적", INVALID_INFRA_COMPROMISED: "무효 · 인프라 결함",
    UNREVIEWED: "완료 · 유효성 미검토", ANALYSIS_PENDING: "실행 완료 · 분석 전", IN_PROGRESS: "진행 중",
  };
  const TRIGGER = {
    T1: "반복 실패 — 같은 공개 검사 실패가 이어짐", T2: "탐색 정체 — 행동이 반복되며 진전 없음",
    T3: "회귀 — 통과하던 공개 검사가 실패로 바뀜", T4: "완료 선언 후 공개 필수 검사 실패",
    T5: "UNKNOWN / INFRA — 관측 불가 또는 인프라 이상",
  };
  const STRATEGY = {
    REVIEW_REGRESSION: "회귀를 만든 변경을 다시 검토", RELOCALIZE: "근거 있는 다른 코드 위치를 다시 찾음",
    REFRESH_EVIDENCE: "오래된 증거를 현재 파일로 갱신", GENERIC_RETRY: "실패를 다시 평가하고 수정을 검증하는 일반 재시도",
    CONTINUE: "개입 없이 진행", STOP: "안전하게 종료",
  };
  const PHASE_GROUP = {
    RUN: "run", OBSERVE: "observe", DECIDE: "decide", DIAGNOSE: "decide", SELECT_STRATEGY: "decide",
    RECOVERY_CONTEXT: "decide", INITIAL_CONTEXT: "decide",
  };
  // Fields the frozen submission snapshot (2026-11-27) predates; the public store carries them per experiment.
  const LEGACY = {
    "aah-main-emergency-v2": {
      label: "Main v2", tag: "v2",
      status_reason: "v1의 에이전트 셸 결함을 고친 뒤 같은 과제·조건·예산·분석으로 다시 실행한 주 결과. AMD-01 Emergency 규모(8과제 × A–E × 1회)라 모든 해석은 탐색적이다.",
      timeline: [
        { ref: "EL-20261127-035", step: "수정·감지 추가", detail: "모델 호출 없는 sandbox 재현으로 원인 확인. 셸 환경에 SYSTEMROOT·WINDIR·COMSPEC·PATH·PATHEXT만 전달하고 AGENT_SHELL_UNAVAILABLE 자동 감지를 추가. 접근 검사가 에이전트와 같은 셸 환경을 쓰도록 바꿔 8/8 재검사 PASS." },
        { ref: "EL-20261127-036", step: "v2 동결", detail: "과제·조건·예산·분석은 v1과 동일하게 두고 셸 환경과 접근 기록만 교체한 새 동결." },
        { ref: "EL-20261127-037", step: "v2 실행", detail: "40/40 실행·독립 판정, 결측 0, 대체 실행 0." },
        { ref: "EL-20261127-038", step: "v2 분석", detail: "AMD-01 설정으로 분석, --verify 바이트 일치. 가설 판단은 모두 결론 불충분(탐색적 규모)." },
      ],
    },
    "aah-main-emergency-v1": {
      label: "Main v1", tag: "v1-invalid", superseded_by: "aah-main-emergency-v2",
      status_reason: "에이전트 셸 명령이 MXC launcher의 SYSTEMROOT 누락 오류로 하나도 실행되지 않은 상태에서 실행됐다. 결함 발견 이력으로만 보존한다.",
      timeline: [
        { ref: "EL-20261127-031", step: "v1 동결·승인", detail: "동결 manifest 재현(MAIN_FREEZE_REPRODUCED) 후 실행 승인. 40슬롯, 최대 104턴." },
        { ref: "EL-20261127-032", step: "v1 실행", detail: "40/40 실행·판정. 겉보기 결과는 E 87.5% 대 A 0%." },
        { ref: "EL-20261127-034", step: "결함 발견", detail: "실행 후 원시 로그 검토에서 에이전트 셸 명령 167개가 모두 MXC launcher 오류임을 확인. 원인은 턴 설정 shell_environment_policy.inherit=\"none\". 사전 격리 검사는 codex sandbox 경로라 이 설정을 거치지 않았다." },
        { ref: "RESULTS_ANALYSIS §2.4", step: "무효 판정", detail: "슬롯을 골라 빼지 않고 실험 전체를 INFRA_COMPROMISED로 표기. 기록은 삭제하지 않고 보존." },
      ],
    },
  };
  const LEGACY_LIMITS = [
    ["P-R2 공통 실패 체크포인트", "NOT_RUN — 측정하지 않음", "AMD-01 §7.2 조건 1·2 미충족 (RESULTS_ANALYSIS §2.5)"],
    ["Ablation AB1–AB4", "NOT_RUN — 구성 요소별 효과 주장 없음", "Emergency 범위에서 생략 (AMD-01 §7.3)"],
    ["leave-one-repository-out 민감도", "미구현 — 미수행", "분석 코드에 없음 (AMD-01 §8)"],
    ["과금액", "미관측 (cost_kind=UNKNOWN)", "기존 구독 사용, 추가 결제 0원"],
    ["토큰 예산", "observe_only — 관측만, 강제 상한 아님", "엄격한 동일 토큰 예산이라고 주장하지 않음"],
    ["과제 검토 독립성", "사람 동료 리뷰(BQ14) 없음 — AI 리뷰로 대체 (AMD-01)", "검사 수정·리뷰 모두 claude-opus-5-5, 변형본 작성 개발 세션과 같은 모델"],
    ["승인 구조", "1인이 RH·GR·CE 승인 겸임", "교차 검토자 없음 (G4)"],
    ["비공개 검사 수정", "8과제 중 5과제(C03·D02·D04·R04·S02) 1회 수정 후 편입", "수정은 Main 실행 전, 해시 스냅샷·재리뷰로 고정"],
    ["Pilot 재실행", "셸 결함 수정 후 Pilot 재실행 없음", "정책 시작값은 셸이 막힌 Pilot 근거"],
    ["v1 열람", "v2 실행 전 v1 결과 열람", "과제·조건·예산·분석은 v1 동결 그대로 유지"],
    ["규모", "8과제 × 1회 · 저장소 3개 · 군집 8개", "검정력 사실상 없음, 천장 효과 (A가 6/8 해결)"],
    ["표기 결함", "analysis_sets.json의 config_sha256 표기가 기본 설정 해시", "수치 영향 없음, 동결 코드 보존을 위해 미수정"],
  ];

  // ------------------------------------------------------------------ formatting
  const esc = (s) => String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const fmt = (v) => (isNum(v) ? Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "N/A");
  // One decimal, ties to even — the same rule as Python's format(), which wrote the reports and figures.
  const round1 = (x) => {
    const y = x * 10, f = Math.floor(y), d = y - f;
    const n = Math.abs(d - 0.5) < 1e-9 ? (f % 2 === 0 ? f : f + 1) : d > 0.5 ? f + 1 : f;
    return n / 10;
  };
  const pct = (v) => (isNum(v) ? round1(v * 100).toFixed(1) + "%" : "N/A");
  const pp = (v) => {
    if (!isNum(v)) return "N/A";
    const x = round1(v * 100);
    return x === 0 ? "0.0%p" : (x > 0 ? "+" : "−") + Math.abs(x).toFixed(1) + "%p";
  };
  const secs = (v) => (isNum(v) ? (v / 1000).toFixed(1) + "초" : "N/A");
  const signedTok = (v) => (isNum(v) ? (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v)) : "N/A");
  const short = (s, n) => (s ? String(s).slice(0, n || 8) : "N/A");
  const seen = (v) => (v === null || v === undefined ? "미관측" : String(v));
  const na = (v) => (v === null || v === undefined || v === "" ? '<span class="muted">N/A</span>' : esc(v));
  const stamp = (iso) => {
    if (!iso) return "N/A";
    const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(iso);
    if (!m) return String(iso);
    const kst = (parseInt(m[2], 10) + 9) % 24;
    return m[1] + " " + m[2] + ":" + m[3] + " UTC (" + String(kst).padStart(2, "0") + ":" + m[3] + " KST)";
  };
  const status = (s) => {
    if (s === "PASS") return '<span class="badge good"><span class="dot good"></span>PASS</span>';
    if (s === "FAIL") return '<span class="badge crit"><span class="dot crit"></span>FAIL</span>';
    if (s === null || s === undefined) return '<span class="badge"><span class="dot mute"></span>기록 없음</span>';
    return '<span class="badge warn"><span class="dot warn"></span>' + esc(s) + "</span>";
  };
  const statusBadge = (s) => {
    const cls = s === "VALID" || s === "VALID_EXPLORATORY" ? "good" : s && s.indexOf("INVALID") === 0 ? "crit" : "warn";
    return '<span class="badge ' + cls + '">' + esc(STATUS_LABEL[s] || s || "상태 미상") + "</span>";
  };
  const table = (head, rows, opts) => {
    const numeric = (opts && opts.numeric) || [];
    if (!rows.length) return '<p class="muted">기록 없음</p>';
    return '<div class="tablewrap"><table><thead><tr>' +
      head.map((h, i) => '<th class="' + (numeric.includes(i) ? "num" : "") + '">' + esc(h) + "</th>").join("") +
      "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c, i) =>
        '<td class="' + (numeric.includes(i) ? "num" : "") + '">' + c + "</td>").join("") + "</tr>").join("") +
      "</tbody></table></div>";
  };
  const kv = (pairs) => '<dl class="kv">' + pairs.map(([k, v]) => "<dt>" + esc(k) + "</dt><dd>" + v + "</dd>").join("") + "</dl>";
  const select = (key, value, options, label) => '<label>' + esc(label) + '<select data-key="' + esc(key) + '">' +
    options.map(([v, t]) => '<option value="' + esc(v) + '"' + (v === value ? " selected" : "") + ">" + esc(t) + "</option>").join("") +
    "</select></label>";
  const panel = (title, body, cls) => '<section class="panel ' + (cls || "") + '">' + (title ? "<h2>" + esc(title) + "</h2>" : "") + body + "</section>";
  const sourceNote = (text) => '<p class="small muted">출처: ' + esc(text) + "</p>";
  const tile = (label, value, note) => '<div class="tile"><div class="label">' + esc(label) + '</div><div class="value">' + esc(value) +
    '</div><div class="note">' + esc(note || "") + "</div></div>";

  // ------------------------------------------------------------------ data model
  let D = null;  // {kind, catalog, experiments, manifests, synthetic, releases, figures}

  function normalize(raw, figures) {
    if (!raw) return null;
    if (raw.catalog) {
      return { kind: "site", catalog: raw.catalog, experiments: raw.experiments || {}, manifests: raw.manifests || {},
        synthetic: raw.synthetic || [], releases: raw.releases || {}, figures: figures || {} };
    }
    // Frozen submission snapshot bundle (aah-public-bundle.json).
    const experiments = {};
    Object.keys(raw.experiments || {}).forEach((id) => {
      const e = raw.experiments[id];
      const legacy = LEGACY[id] || {};
      const st = e.status || e.validity;
      experiments[id] = Object.assign({}, e, { status: st, status_label: STATUS_LABEL[st] || st, label: e.label || legacy.label || id,
        status_reason: e.status_reason || legacy.status_reason || "", timeline: e.timeline || legacy.timeline || [],
        superseded_by: e.superseded_by || legacy.superseded_by || null });
    });
    const figs = { _global: {} };
    Object.keys(figures || {}).forEach((name) => {
      const owner = Object.keys(LEGACY).find((id) => name.indexOf(LEGACY[id].tag + "-") === 0);
      if (owner) (figs[owner] = figs[owner] || {})[name.slice(LEGACY[owner].tag.length + 1)] = figures[name];
      else figs._global[name.replace(/^fig01-/, "").replace(/^v1-v2-/, "")] = figures[name];
    });
    const ids = Object.keys(experiments).sort().reverse();
    const primary = ids.find((id) => experiments[id].status === "VALID_EXPLORATORY" || experiments[id].status === "VALID") || ids[0];
    const release = { release_id: "submission-snapshot", label: "제출 스냅샷 (고정)", kind: "submission", primary: primary,
      experiments: Object.fromEntries(ids.map((id) => [id, {}])), verification: raw.verification || null, created_at: null };
    const syn = raw.synthetic && raw.synthetic.runs ? [{ suite: "s3-verification", label: raw.synthetic.label, report_ref: raw.synthetic.report_ref, runs: raw.synthetic.runs }] : [];
    return { kind: "snapshot", experiments: experiments, manifests: {}, synthetic: syn, figures: figs,
      releases: { "submission-snapshot": release },
      catalog: { default_release: "submission-snapshot", data_updated_at: null, limitations: LEGACY_LIMITS, releases: [release],
        experiments: ids.map((id) => ({ key: id, status: experiments[id].status, status_label: experiments[id].status_label, label: experiments[id].label })) } };
  }

  const C = () => (D && D.catalog) || {};
  const exp = (id) => ((D && D.experiments) || {})[id] || null;
  const releaseList = () => (C().releases || []);
  const release = (rid) => (rid === LATEST ? null : ((D && D.releases) || {})[rid] || releaseList().find((r) => r.release_id === rid) || null);
  const releaseOptions = () => releaseList().map((r) => [r.release_id, (r.label || r.release_id) + (r.kind ? " · " + r.kind : "")])
    .concat(D && D.kind === "site" ? [[LATEST, "최신 데이터 (모든 실험·진행 중 포함)"]] : []);
  function experimentsIn(rid) {
    if (rid === LATEST) return Object.keys((D && D.experiments) || {}).sort().reverse();
    const r = release(rid);
    if (!r) return [];
    const ids = Object.keys(r.experiments || {});
    return ids.sort((a, b) => (a === r.primary ? -1 : b === r.primary ? 1 : a < b ? 1 : -1));
  }
  function pinOk(rid, id) {
    const r = release(rid);
    if (!r || !r.experiments || !r.experiments[id] || !r.experiments[id].data_sha256) return null;
    const entry = (C().experiments || []).find((e) => e.key === id);
    return !!entry && entry.data_sha256 === r.experiments[id].data_sha256;
  }
  const label = (id) => { const e = exp(id); return e ? (e.label || id) : id; };

  function statusBanner(e) {
    if (!e) return "";
    const s = e.status;
    if (s === "VALID") return "";
    if (s === "VALID_EXPLORATORY") return '<div class="callout"><strong>' + esc(STATUS_LABEL[s]) + ".</strong> " + esc(e.status_reason || "") + "</div>";
    if (s && s.indexOf("INVALID") === 0) return '<div class="callout crit"><strong>무효 실험 (' + esc(s) + ").</strong> " + esc(e.status_reason || "") +
      " 연구 질문의 근거로 쓰지 않는다" + (e.report_ref ? " (" + esc(e.report_ref) + ")" : "") + ".</div>";
    const p = e.progress || {};
    const tail = s === "IN_PROGRESS" || s === "ANALYSIS_PENDING" ? " 판정 " + esc(p.evaluated) + "/" + esc(p.planned) + " 슬롯." : "";
    return '<div class="callout warn"><strong>' + esc(STATUS_LABEL[s] || s) + ".</strong>" + tail + " " + esc(e.status_reason || "") + "</div>";
  }

  const slotRun = (e, task, cond) => {
    const s = (e.slots || []).find((x) => x.task_id === task && x.condition === cond);
    return s && s.selected_run_id ? (e.runs || {})[s.selected_run_id] || null : null;
  };
  const turnOf = (t) => (t === null || t === undefined ? "기준(baseline)" : String(t).replace("turn-000", "턴 "));
  const policyText = (p) => (p ? "초기 " + p.initial_context + " · 진단 " + p.diagnosis + " · 전략 " + p.strategy + " · 복구 " +
    p.recovery_context + " · 외부 개입 " + (p.external_intervention ? "있음" : "없음") : "N/A");
  const compText = (c) => (c ? "거리 " + c.distance + " · 관측 " + c.observed + " · 수정 " + c.modified + " · 미탐색 " + c.unexplored : "N/A");

  function graphEntries(state) {
    const out = [];
    for (const id of experimentsIn(state.release)) {
      const e = exp(id);
      if (!e) continue;
      for (const run of Object.values(e.runs || {})) {
        (run.interventions || []).forEach((iv) => {
          if (iv.graph_detail) out.push({ key: id + "|" + run.run_id + "|" + iv.intervention_id, group: label(id) + " · " + (e.status_label || e.status), run, iv, exp: e });
        });
      }
    }
    for (const suite of (D && D.synthetic) || []) {
      for (const run of Object.values(suite.runs || {})) {
        (run.interventions || []).forEach((iv) => {
          if (iv.graph_detail) out.push({ key: "synthetic|" + run.run_id + "|" + iv.intervention_id, group: "S3 합성 검증 (모델 호출 0)", run, iv, exp: null });
        });
      }
    }
    return out;
  }

  function replayCases(state) {
    const out = [];
    const e = exp(state.exp);
    if (e) {
      for (const s of e.slots || []) {
        const run = (e.runs || {})[s.selected_run_id];
        if (!run) continue;
        const group = (run.interventions || []).length ? label(state.exp) + " — 개입 발생" : s.y === 0 ? label(state.exp) + " — 개입 없이 실패" : null;
        if (group) out.push({ key: state.exp + "|" + run.run_id, group, run, exp: e, title: s.task_id + " · " + s.condition + " → " + (s.evaluation_status || "N/A") });
      }
    }
    for (const suite of (D && D.synthetic) || []) {
      for (const run of Object.values(suite.runs || {})) {
        if ((run.interventions || []).length) {
          out.push({ key: "synthetic|" + run.run_id, group: "S3 합성 검증 (모델 호출 0, 라이브 아님)", run, exp: null, title: (run.scenario || run.task_id) + " · " + run.condition });
        }
      }
    }
    return out;
  }

  // ------------------------------------------------------------------ global filter row
  function filters(state, extra) {
    const exps = experimentsIn(state.release);
    return '<div class="filters">' + select("release", state.release, releaseOptions(), "데이터 기준") +
      (exps.length ? select("exp", state.exp, exps.map((id) => [id, label(id) + " · " + ((exp(id) || {}).status_label || "")]), "실험") : "") +
      (extra || "") + '<span class="small muted updated">' +
      (C().data_updated_at ? "마지막 데이터 갱신 " + esc(stamp(C().data_updated_at)) : "제출 시점 고정본") + "</span></div>";
  }

  function header(state) {
    const e = exp(state.exp);
    let html = filters(state, "");
    if (pinOk(state.release, state.exp) === false) {
      html += '<div class="callout crit"><strong>release 고정 불일치.</strong> 이 release가 고정한 데이터와 현재 데이터가 다르다. 표시 값을 신뢰하지 말고 데이터 저장소를 확인한다.</div>';
    }
    return html + statusBanner(e);
  }

  // ------------------------------------------------------------------ overview
  function overview(state) {
    const parts = [header(state)];
    parts.push(panel("이 대시보드가 보여주는 것",
      "<p><strong>AAH(Adaptive Agent Harness)</strong>는 Codex 모델을 바꾸지 않고 바깥에서 실행을 관측하는 하네스다. 실패를 공통 규칙으로 감지하고, " +
      "코드 구조(Repository Graph)와 실행 증거(Execution Graph)를 연결해 원인 후보를 좁힌다. 필요한 보조 정보만 골라(DCC) 다음 턴에 전달하며, " +
      "최종 패치는 에이전트가 볼 수 없는 독립 평가 환경에서 판정한다.</p>" +
      "<p>모든 수치는 동결된 분석 산출물(analysis.json)과 실행 기록에서 내보낸 공개 데이터에서 읽는다. Replay도 저장된 기록을 다시 보여 줄 뿐 모델을 호출하지 않는다. " +
      "측정하지 않은 항목은 <strong>미측정</strong> 또는 <strong>N/A</strong>로 표시하고, 실험 버전끼리 결과를 합산하지 않는다. " +
      "위의 <strong>데이터 기준</strong>에서 release(특정 시점에 고정한 결과)를 고른다.</p>" +
      table(["화면", "확인할 수 있는 것"], [
        ["조건 비교", "A–E 성공률·토큰·성공당 토큰·실행 시간, 효과 크기와 95% 구간, 연구 질문별 판단"],
        ["실행 추적", "과제·조건별 Codex 이벤트 타임라인, 공개 검사, 개입 시점, 종료·독립 판정"],
        ["그래프 탐색", "Repository/Execution Graph, Cross-Graph Mapping, 앵커·후보·탐색 경로, 미해결 연결"],
        ["컨텍스트", "DCC/DEFAULT 패키지의 선택·제외 항목, 점수 구성, 예산 사용량, 조건 간 동일성"],
        ["복구 Replay", "실패 발생 → 감지 → 진단 → 전략 → 컨텍스트 → 후속 실행 → 평가를 단계별 재생"],
        ["실험 감사", "실험 버전별 상태와 이력, 결함 수정, 동결·재현, 격리 검증, 한계와 미측정 항목"],
      ].map((r) => r.map(esc)))));
    const e = exp(state.exp);
    if (e) {
      const s = e.slots || [];
      const ran = s.filter((x) => x.selected_run_id).length;
      const judged = s.filter((x) => x.evaluation_status).length;
      const ok = s.filter((x) => x.y === 1).length;
      const tiles = [tile("계획 슬롯", fmt(s.length), "과제 × 조건 × 반복"), tile("실제 실행", fmt(ran), "실제 Codex 실행"),
        tile("독립 판정", fmt(judged), "결측·미판정 " + fmt(s.length - judged) + "건")];
      if (e.conditions) {
        const rec = s.reduce((a, x) => a + (x.recoveries_delivered || 0), 0);
        const tok = s.reduce((a, x) => a + (x.model_tokens || 0), 0);
        const rt = s.reduce((a, x) => a + (x.runtime_ms || 0), 0);
        tiles.push(tile("성공 / 실패", fmt(ok) + " / " + fmt(judged - ok), "독립 판정 기준"), tile("복구 전달", fmt(rec), "B–E 개입 후 후속 턴"),
          tile("모델 토큰", fmt(tok), "관측값 (observe_only)"), tile("실행 시간 합계", (rt / 60000).toFixed(1) + "분", "슬롯 실행 시간 합"));
      } else {
        tiles.push(tile("결과", "미확정", e.status_label || ""));
      }
      const r = release(state.release);
      const tests = r && r.verification ? (r.verification.steps || []).find((x) => x.id === "tests") : null;
      tiles.push(tile("자동 테스트", tests && tests.counts ? fmt(tests.counts.passed) + " 통과" : "기록 없음",
        tests && tests.counts ? "실패 " + fmt(tests.counts.failed) + " · 건너뜀 " + fmt(tests.counts.skipped) : "이 데이터 기준에 검증 기록 없음"));
      parts.push(panel(label(state.exp) + " 실행 요약", '<div class="tiles">' + tiles.join("") + "</div>" +
        sourceNote((e.public_dir || "") + "/run_status.csv · release 검증 기록")));
    }
    const rows = (C().experiments || []).map((x) => {
      const p = x.progress || {};
      const ee = exp(x.key) || {};
      const sl = ee.slots || [];
      return [esc(x.key), esc(x.label || ee.label || ""), statusBadge(x.status),
        p.planned ? esc(p.evaluated + "/" + p.planned) : esc(sl.filter((r) => r.evaluation_status).length + "/" + sl.length),
        esc(stamp(x.exported_at)), "<code>" + esc(short(x.data_sha256, 12)) + "</code>"];
    });
    parts.push(panel("실험 버전과 상태", table(["실험 ID", "이름", "상태", "판정 슬롯", "데이터 내보낸 시각", "데이터 해시"], rows) +
      '<p class="small muted">유효 · 탐색적 = 주 결과 / 무효 = 결함 이력으로 보존 / 진행 중·분석 전·미검토 = 결과 미확정. 상태는 사람이 검토한 등록부에서만 정한다.</p>'));
    parts.push(panel("release (결과 고정본)", table(["release", "이름", "종류", "생성 시각", "주 실험", "포함 실험"], releaseList().map((r) =>
      [esc(r.release_id), esc(r.label), esc(r.kind), esc(stamp(r.created_at)), esc(r.primary), esc(Object.keys(r.experiments || {}).join(", "))]))));
    if (e && e.meta && e.meta.model) {
      const m = e.meta, b = m.budget || {};
      parts.push(panel("실행 설정 (동결 기준)", kv([
        ["모델 / runtime", esc([m.model.model_id, "추론 " + m.model.reasoning_effort, m.model.adapter + " " + m.model.runtime_version, m.model.session_mode].join(" · "))],
        ["프로토콜 / 반복", esc((m.protocol || "N/A") + " / " + (m.repetitions || "N/A") + "회")],
        ["턴 상한", esc("총 " + fmt(b.max_total_turns) + " (최초 " + fmt(b.max_initial_turns) + " + 복구 " + fmt(b.max_recovery_turns) + "), 슬롯당 복구 " + fmt(b.recovery_turns_per_eligible_slot) + "회")],
        ["실행 한도", esc("실행당 " + secs(b.wall_ms_per_run) + " · 턴당 " + secs(b.native_turn_ms) + " · 정리 예비 " + secs(b.cleanup_reserve_ms))],
        ["보조 정보 상한", esc(fmt(b.supplement_cap) + " 추정 토큰 (B–E 공통)")],
        ["토큰 한도 방식", esc(b.token_limit_mode || "N/A") + ' <span class="muted">(관측만, 강제 아님)</span>'],
        ["코드 버전", "<code>" + esc(m.code_version || "N/A") + "</code>"],
      ])));
    }
    return parts.join("");
  }

  // ------------------------------------------------------------------ benchmark
  function benchmark(state) {
    const e = exp(state.exp);
    const parts = [header(state)];
    if (!e) return parts.join("") + panel("데이터 없음", "<p>선택한 실험의 기록이 없다.</p>");
    if (!e.conditions) return parts.join("") + panel("결과 미확정", "<p>이 실험은 진행 중이거나 분석 전이라 조건별 지표와 효과 크기를 표시하지 않는다.</p>");
    const figs = ((D && D.figures) || {})[state.exp] || {};
    const fig = (name) => (figs[name] ? '<div class="fig">' + figs[name] + "</div>" : '<p class="muted">그림 없음</p>');
    const cm = e.conditions;
    const rows = COND.filter((c) => cm[c]).map((c) => {
      const m = cm[c];
      return [esc(COND_LABEL[c]), fmt(m.planned), fmt(m.valid), fmt(m.successes), fmt(m.failures), fmt(m.missing), pct(m.tsr_task_mean),
        fmt(m.model_tokens_total), m.cps_tokens === null ? "정의 안 됨 (" + esc(m.cps_reason) + ")" : fmt(m.cps_tokens), secs(m.mean_runtime_ms),
        fmt((e.slots || []).filter((s) => s.condition === c).reduce((a, s) => a + (s.recoveries_delivered || 0), 0))];
    });
    parts.push(panel("조건별 결과 (표)", table(["조건", "계획", "유효", "성공", "실패", "결측", "과제 평균 성공률", "모델 토큰", "성공당 토큰", "평균 실행 시간", "복구 전달"],
      rows, { numeric: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] }) + sourceNote(e.public_dir + "/metric_summary.csv, run_status.csv")));
    parts.push('<div class="grid2">' + panel("성공률", fig("success-rate.svg")) + panel("토큰과 성공당 토큰", fig("tokens.svg")) + "</div>");
    parts.push('<div class="grid2">' + panel("과제 × 조건 판정", fig("task-matrix.svg")) + panel("효과 크기와 95% 구간", fig("paired-effects.svg")) + "</div>");
    const c = Object.fromEntries((e.contrasts || []).map((x) => [x.contrast_id, x]));
    const ci = (x) => (!x ? "N/A" : isNum(x.ci_lower) ? "[" + (x.unit === "pp" ? pp(x.ci_lower) + ", " + pp(x.ci_upper) : signedTok(x.ci_lower) + ", " + signedTok(x.ci_upper)) + "]" : "구간 없음 (" + esc(x.interpretation_status) + ")");
    const eff = (x) => (!x ? "N/A" : x.unit === "pp" ? pp(x.effect) : (isNum(x.effect) ? signedTok(x.effect) + " 토큰" : "정의 안 됨"));
    const d = e.derived || {};
    const gu = d.graph_use || {};
    const typed = Object.entries(gu).filter(([k]) => !k.startsWith("GENERIC_RETRY")).reduce((a, [, v]) => a + v, 0);
    const gtotal = Object.values(gu).reduce((a, v) => a + v, 0);
    const pairs = d.identical_input_pairs || {};
    const par = d.parity || {};
    parts.push(panel("연구 질문별 결과", table(["연구 질문", "사전 지정 대비", "점추정", "95% 구간", "판단 (AMD-01 §8)"], [
      ["RQ1 · DCC는 성공당 비용을 줄이는가", "P-R1 = CPS(C) − CPS(B)", eff(c["P-R1"]), ci(c["P-R1"]), "결론 불충분 — 구간이 0을 포함"],
      ["RQ2 · 그래프 복구가 일반 재시도보다 나은가", "P-R2 공통 실패 체크포인트 복구율", "미측정", "N/A", "NOT_RUN (AMD-01 §7.2 조건 미충족)"],
      ["RQ3 · 통합 정책의 추가 이득", "P-R3 = E − max(C, D)", eff(c["P-R3"]), ci(c["P-R3"]), "결론 불충분 — 구간 방법 미승인"],
      ["RQ4 · 구성 요소별 가치", "AB1–AB4", "미측정", "N/A", "NOT_RUN (Emergency 생략)"],
      ["참고 · 전체 시스템 대 기본 Codex", "E − A", eff(c["E-A"]), ci(c["E-A"]), "결론 불충분"],
      ["참고 · 2×2 DCC 주효과", "CTX_MAIN", eff(c["CTX_MAIN"]), ci(c["CTX_MAIN"]), "결론 불충분"],
      ["참고 · 2×2 Graph 주효과", "GRAPH_MAIN", eff(c["GRAPH_MAIN"]), ci(c["GRAPH_MAIN"]), "결론 불충분 — 아래 해석 주의 참고"],
    ].map((r) => r.map((x, i) => (i === 2 || i === 3 ? x : esc(x))))) +
      '<div class="callout"><strong>해석 주의 (사후 기술 분석, 사전 등록 아님).</strong> ' +
      "이 실험에서 그래프 진단은 " + fmt(gtotal) + "회 호출됐고, 유형별 전략(RELOCALIZE 등)을 낸 경우는 " + fmt(typed) + "회다. " +
      "B/D와 C/E의 첫 턴 입력은 " + fmt(par.B_equals_D) + "/" + fmt(par.tasks) + ", " + fmt(par.C_equals_E) + "/" + fmt(par.tasks) +
      " 과제에서 바이트 단위로 같았다. 따라서 B–D, C–E 차이는 그래프 전략의 효과로 귀속할 수 없다. 입력이 같은 쌍 " + fmt(pairs.n) +
      "개 중 첫 턴 공개 검사 결과가 갈린 쌍은 " + fmt(pairs.first_turn_diverged) + "개, 최종 판정이 갈린 쌍은 " + fmt(pairs.verdict_diverged) + "개였다." +
      (pairs.verdict_diverged_all_messages_identical || []).map((p) => " 특히 " + esc(p.task_id) + "의 " + esc(p.pair.split("").join("·")) +
        "는 " + esc(p.turns[0]) + "개 턴 모두 바이트 동일한 메시지를 받았지만 판정이 " + esc(p.verdict.join(" / ")) + "로 갈렸다.").join("") +
      " 1회 반복 설계에서는 모델 비결정성이 조건 간 차이와 같은 크기로 나타난다.</div>" +
      sourceNote(e.public_dir + "/paired_contrasts.csv · bootstrap " + fmt(((e.meta || {}).analysis || {}).bootstrap ? e.meta.analysis.bootstrap.iterations : null) +
        "회, seed " + esc(((e.meta || {}).analysis || {}).analysis_seed))));
    return parts.join("");
  }

  // ------------------------------------------------------------------ trace and context
  function runPicker(state, e) {
    const tasks = Object.keys(e.tasks || {}).sort();
    return '<div class="filters">' + select("task", state.task, tasks.map((t) => [t, t + " · " + ((e.tasks[t] || {}).task_family_id || "")]), "과제") +
      select("cond", state.cond, COND.map((c) => [c, COND_LABEL[c]]), "조건") + "</div>";
  }

  function eventDetail(ev) {
    switch (ev.kind) {
      case "COMMAND": return "명령 시작 " + esc(seen(ev.command_id)) + " (" + esc(seen(ev.state)) + ")";
      case "COMMAND_RESULT": return "명령 종료 " + esc(seen(ev.command_id)) + " · exit " + esc(seen(ev.exit_code));
      case "TEST": return "공개 검사 " + esc(seen(ev.check_id)) + " → " + status(ev.result);
      case "FAILURE": return "실패 " + esc(seen(ev.error_type)) + " · " + esc(seen(ev.message)) + (ev.frame_present ? "" : ' <span class="muted">(traceback frame 없음)</span>');
      case "CHANGE": return "파일 변경 " + ((ev.hunks || []).map((h) => esc(h.path) + " −" + esc(h.old_count) + "/+" + esc(h.new_count)).join(", ") || "미관측");
      case "COMPLETION": return "완료 선언 " + esc(seen(ev.declared));
      case "INTERVENTION": return "개입 " + esc(seen(ev.intervention_id)) + " · " + esc((ev.trigger_ids || []).join(",") || "미관측") + " → " + esc(seen(ev.strategy_id)) + " (" + esc(seen(ev.delivery_status)) + ")";
      case "USAGE": return "사용량 기록 " + esc(seen(ev.usage_id));
      case "UNKNOWN": return '<span class="muted">미분류 이벤트 ' + esc(seen(ev.provider_type)) + " — 원문은 제한 자료라 공개 데이터에 없음</span>";
      default: return esc(seen(ev.kind));
    }
  }

  function timeline(run) {
    const tr = run.transitions || [];
    if (!tr.length) return '<p class="muted">상태 전이 기록 없음</p>';
    const total = Math.max((run.report || {}).elapsed_ms || 0, tr[tr.length - 1].elapsed_ms || 0, 1);
    const W = 1000, L = 10, R = 10, H = 74, plot = W - L - R;
    const x = (t) => L + plot * (t / total);
    let svg = '<svg class="tl" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" aria-label="상태 전이 타임라인">';
    for (let i = 0; i < tr.length; i++) {
      const start = tr[i].elapsed_ms || 0;
      const end = i + 1 < tr.length ? tr[i + 1].elapsed_ms || start : total;
      const g = PHASE_GROUP[tr[i].to] || "other";
      const w = Math.max(x(end) - x(start), 1.5);
      const tip = tr[i].to + " · " + secs(start) + " → " + secs(end) + " · " + (tr[i].reason || "");
      svg += '<rect class="seg ' + g + '" x="' + x(start).toFixed(1) + '" y="14" width="' + Math.max(w - 1, 1).toFixed(1) + '" height="22" rx="3" data-tip="' + esc(tip) + '"><title>' + esc(tip) + "</title></rect>";
      if (tr[i].intervention_id) {
        svg += '<line x1="' + x(start).toFixed(1) + '" x2="' + x(start).toFixed(1) + '" y1="6" y2="44" stroke-width="2"/><text x="' + (x(start) + 4).toFixed(1) + '" y="10">개입 ' + esc(tr[i].intervention_id) + "</text>";
      }
    }
    for (let k = 0; k <= 4; k++) {
      const t = total * k / 4;
      svg += '<text x="' + x(t).toFixed(1) + '" y="56" text-anchor="' + (k === 0 ? "start" : k === 4 ? "end" : "middle") + '">' + esc(secs(t)) + "</text>";
    }
    svg += "</svg>";
    return svg + '<div class="legend"><span><span class="sw" style="background:var(--s1)"></span>Codex 실행 (RUN)</span><span><span class="sw" style="background:var(--s2)"></span>하네스 관측·공개 검사 (OBSERVE)</span>' +
      '<span><span class="sw" style="background:var(--s3)"></span>하네스 결정·진단·컨텍스트 구성</span><span><span class="sw" style="background:var(--mute)"></span>시작·종료</span></div>';
  }

  function runHeader(run, e) {
    const r = run.report || {};
    const v = run.verdict || {};
    return kv([
      ["run_id", "<code>" + esc(run.run_id) + "</code> · " + esc(run.slot_id || "") + (e ? " · " + esc(e.experiment_id) : " · S3 합성")],
      ["과제 · 조건", esc(run.task_id) + " · " + esc(COND_LABEL[run.condition] || run.condition)],
      ["정책 (manifest)", esc(policyText(run.policy))],
      ["종료", esc(r.stop_reason) + " · 턴 " + na(r.turns_used) + " · 복구 " + na(r.recoveries_delivered) + " · " + secs(r.elapsed_ms)],
      ["공개 검사 (1턴 → 최종)", status(run.first_turn_public) + " → " + status(r.public_check_status)],
      ["독립 판정", (run.verdict ? status(v.status) + ' <span class="small muted">기능 ' + esc(v.functional_status) + " · 회귀 " + esc(v.regression_status) + " · 지침 " + esc(v.instruction_status) + (v.error_code ? " · " + esc(v.error_code) : "") + "</span>" : '<span class="muted">독립 판정 없음 (합성 검증)</span>')],
      ["변경 파일", (r.changed_paths && r.changed_paths.length ? esc(r.changed_paths.join(", ")) : '<span class="muted">없음 (빈 패치)</span>')],
      ["모델 토큰", fmt(r.observed_model_tokens)],
      ["경고", (r.warnings || []).length ? esc(r.warnings.join(", ")) : '<span class="muted">없음</span>'],
    ]);
  }

  function interventionCard(iv) {
    const g = iv.graph;
    return kv([
      ["개입", esc(iv.intervention_id) + " · 이벤트 " + esc((iv.events || []).map((x) => x.event).join(" → "))],
      ["트리거", esc((iv.trigger_ids || []).map((t) => t + " (" + (TRIGGER[t] || "?") + ")").join(", "))],
      ["전략", esc(iv.strategy) + ' <span class="muted">— ' + esc(STRATEGY[iv.strategy] || "") + "</span>"],
      ["진단", g ? "GRAPH · 상태 " + esc(g.status) + " · 대체 사유 " + na(g.fallback_reason) + " · 그래프 구축 " + fmt(g.build_ms) + "ms" : "FLAT (그래프 미호출)"],
      ["지시 목표", esc(((iv.directive || {}).objective) || "N/A")],
      ["후속 관측", iv.outcome ? esc("턴 상태 " + iv.outcome.turn_status + " · 지시 수행 " + iv.outcome.adherence_status + " (" + iv.outcome.adherence_basis + ")") : '<span class="muted">없음</span>'],
    ]);
  }

  function trace(state) {
    const e = exp(state.exp);
    const parts = [header(state)];
    if (!e) return parts.join("") + panel("데이터 없음", "<p>선택한 실험의 기록이 없다.</p>");
    parts.push(runPicker(state, e));
    const run = slotRun(e, state.task, state.cond);
    if (!run) return parts.join("") + panel("기록 없음", "<p>이 과제·조건의 실행 기록이 없다 (미실행 또는 진행 중).</p>");
    parts.push(panel("실행 요약", runHeader(run, e)));
    parts.push(panel("상태 전이 타임라인", timeline(run) + "<details><summary>전이 표 보기</summary>" +
      table(["#", "시점", "이전", "다음", "사유", "개입"], (run.transitions || []).map((t) => [fmt(t.seq), secs(t.elapsed_ms), esc(t.from), esc(t.to), esc(t.reason), na(t.intervention_id)]), { numeric: [0, 1] }) + "</details>"));
    const turns = [...new Set((run.events || []).map((ev) => ev.turn))];
    const evRows = (run.events || []).map((ev) => [fmt(ev.seq), esc(turnOf(ev.turn)), esc(ev.at ? ev.at.slice(11, 23) : ""), esc(ev.kind), eventDetail(ev), esc(ev.observation)]);
    parts.push(panel("Codex 실행 이벤트 (정규화 증거 원장)", '<p class="small muted">턴 ' + esc(turns.length) + "개, 이벤트 " + esc(evRows.length) +
      "개. 명령 원문·출력·에이전트 메시지는 제한 자료라 공개 데이터에 포함하지 않는다.</p>" + table(["#", "턴", "UTC 시각", "종류", "내용", "관측"], evRows, { numeric: [0] })));
    parts.push('<div class="grid2">' + panel("공개 검사 결과", table(["시점", "검사", "결과", "시간", "파서"], (run.checks || []).map((c) => [esc(turnOf(c.turn)), esc(c.check_id), status(c.status), fmt(c.duration_ms) + "ms", '<code>' + esc(c.parser_id) + "</code>"]))) +
      panel("턴별 사용량", table(["턴", "단계", "입력", "캐시 입력", "출력", "합계", "턴 시간"], (run.usage || []).map((u) => [esc(turnOf(u.turn)), esc(u.stage), fmt(u.input_tokens), fmt(u.cached_input_tokens), fmt(u.output_tokens), fmt(u.total_tokens), secs(u.elapsed_ms)]), { numeric: [2, 3, 4, 5, 6] }) +
      "<h3>하네스 로컬 처리 시간</h3>" + table(["단계", "턴", "시간"], (run.overhead || []).map((o) => [esc(o.stage), esc(turnOf(o.turn)), fmt(o.elapsed_ms) + "ms"]), { numeric: [2] })) + "</div>");
    if ((run.interventions || []).length) {
      parts.push(panel("개입 기록", (run.interventions || []).map((iv) => interventionCard(iv)).join("") +
        '<p><a href="' + esc(toHash(Object.assign({}, state, { tab: "replay", case: state.exp + "|" + run.run_id, step: "0" }))) + '">이 실행을 복구 Replay로 보기 →</a></p>'));
    }
    return parts.join("");
  }

  function contextTable(pkg) {
    const dcc = pkg.policy === "DCC";
    return table(["", "사유", "종류", "출처", "근거", "점수", "관련성", "유효성", "범위", "실패", "추정 토큰", "효용 밀도"], (pkg.decisions || []).map((d) => {
      const c = d.components || {};
      const unscored = !dcc || d.reason_code === "CORE_REQUIRED" || d.reason_code === "MERGED_DUPLICATE";
      const n = (v) => (isNum(v) ? v.toFixed(2) : unscored ? "—" : "N/A");
      return [d.selected ? '<span class="badge good">선택</span>' : '<span class="badge crit">제외</span>', esc(d.reason_code), esc(d.kind),
        esc(d.source) + (d.span ? ' <span class="muted">L' + esc(d.span.join("–")) + "</span>" : ""), esc(d.provenance + " · " + d.validity),
        isNum(d.score) ? d.score.toFixed(3) : unscored ? "—" : "N/A", n(c.relevance), n(c.validity), n(c.scope), n(c.failure),
        isNum(d.estimated_tokens) ? fmt(d.estimated_tokens) : "—", isNum(d.utility_density) ? d.utility_density.toFixed(4) : unscored ? "—" : "N/A"];
    }), { numeric: [5, 6, 7, 8, 9, 10, 11] });
  }

  function context(state) {
    const e = exp(state.exp);
    const parts = [header(state)];
    if (!e) return parts.join("") + panel("데이터 없음", "<p>선택한 실험의 기록이 없다.</p>");
    parts.push(runPicker(state, e));
    const run = slotRun(e, state.task, state.cond);
    if (!run) return parts.join("") + panel("기록 없음", "<p>이 과제·조건의 실행 기록이 없다.</p>");
    const pkgs = run.context || [];
    if (!pkgs.length) parts.push(panel("컨텍스트 기록 없음", "<p>이 실행에는 컨텍스트 패키지 기록이 없다.</p>"));
    for (const pkg of pkgs) {
      const used = pkg.estimated_supplement_tokens, cap = pkg.cap_tokens;
      const ratio = isNum(used) && isNum(cap) && cap > 0 ? Math.min(used / cap, 1) : 0;
      const total = (pkg.decisions || []).length, sel = (pkg.decisions || []).filter((d) => d.selected).length;
      const note = pkg.policy === "NONE" ? '<div class="callout">조건 A는 보조 영역이 비어 있다. 사용자 요청과 필수 지침만 같은 경로로 전달한다 (CT07).</div>'
        : pkg.policy === "DEFAULT" ? '<div class="callout">DEFAULT는 공통 최소 근거를 그대로 담는다. DCC 전용 점수·중복 제거·한계 효용 패킹을 쓰지 않으므로 점수 열은 비어 있다 (—).</div>'
          : '<div class="callout">DCC는 후보마다 관련성(BM25)·유효성·범위·실패 연관성 점수를 매기고, 예산 안에서 한계 효용이 양수인 항목만 담는다. 필수 지침과 핵심 근거(CORE_REQUIRED)는 점수 없이 유지하고, 중복(MERGED_DUPLICATE)은 하나로 합친다 (— = 점수 미적용).</div>';
      parts.push(panel(pkg.stage + " 패키지 · " + pkg.policy + (pkg.strategy_id ? " · " + pkg.strategy_id : ""), note +
        '<div class="tiles">' + tile("보조 정보 추정 토큰", fmt(used), "상한 " + fmt(cap) + " (" + pct(ratio) + ")") + tile("전체 메시지 추정 토큰", fmt(pkg.estimated_full_tokens), pkg.estimator_id || "") +
        tile("선택 / 후보", sel + " / " + total, "핵심 근거 " + fmt(pkg.core_count)) + tile("구성 시간", fmt(pkg.elapsed_ms) + "ms", "로컬 처리, 전체 비용에 포함") + "</div>" +
        '<div class="meter" title="' + esc(pct(ratio)) + '"><span style="width:' + (ratio * 100).toFixed(1) + '%"></span></div>' +
        kv([["상태 · 경고", esc(pkg.status) + " · " + esc((pkg.warnings || []).join(", ") || "없음")], ["검색 방식", esc(pkg.retrieval_version || "N/A")],
          ["package_hash", "<code>" + esc(pkg.package_hash || "N/A") + "</code>"], ["message_hash", "<code>" + esc(pkg.message_hash || "N/A") + "</code>"]]) + contextTable(pkg)));
    }
    const rows = COND.map((c) => {
      const r = slotRun(e, state.task, c);
      const p = r && (r.context || []).find((x) => x.stage === "INITIAL");
      const h = r && r.dispatch && r.dispatch[0] ? r.dispatch[0].message_hash : null;
      return { c, p, h };
    });
    const same = (a, b) => a.h && a.h === b.h;
    parts.push(panel("같은 과제의 조건별 최초 패키지 비교", table(["조건", "정책", "보조 정보 추정 토큰", "선택 / 후보", "최초 메시지 해시", "동일성"], rows.map((x) => [esc(COND_LABEL[x.c]),
      esc(x.p ? x.p.policy : "N/A"), fmt(x.p ? x.p.estimated_supplement_tokens : null), x.p ? esc((x.p.decisions || []).filter((d) => d.selected).length + " / " + (x.p.decisions || []).length) : "N/A",
      "<code>" + esc(short(x.h, 16)) + "</code>", x.c === "D" ? (same(rows[1], rows[3]) ? '<span class="badge good">B와 바이트 동일</span>' : '<span class="badge crit">B와 다름</span>')
        : x.c === "E" ? (same(rows[2], rows[4]) ? '<span class="badge good">C와 바이트 동일</span>' : '<span class="badge crit">C와 다름</span>') : ""]), { numeric: [2] }) +
      '<p class="small muted">CT03 계약: 초기 입력이 같으면 B/D, C/E의 외부 패키지는 바이트 단위로 같아야 한다. 이 표는 실제 라이브 실행 기록에서 그 계약을 다시 확인한다.</p>'));
    return parts.join("");
  }

  // ------------------------------------------------------------------ graph
  function graphSvg(gd) {
    const nodes = gd.nodes || [];
    const repo = nodes.filter((n) => n.namespace === "repo");
    const execn = nodes.filter((n) => n.namespace !== "repo").sort((a, b) =>
      String(a.turn || "").localeCompare(String(b.turn || "")) || String(a.kind).localeCompare(String(b.kind)) || String(a.label).localeCompare(String(b.label)));
    const tr = gd.trace || {};
    const anchors = new Set(tr.anchor_ids || []);
    const cands = new Set((tr.candidates || []).map((c) => c.id));
    const pathEdges = new Set((tr.paths || []).flatMap((p) => p.edges || []));
    const row = 28, top = 44, laneW = 350, gap = 130, margin = 70;
    const W = margin * 2 + laneW * 2 + gap;
    const H = top + Math.max(repo.length, execn.length, 1) * row + 20;
    const pos = {};
    repo.forEach((n, i) => { pos[n.id] = { x: margin, y: top + i * row, side: "L" }; });
    execn.forEach((n, i) => { pos[n.id] = { x: margin + laneW + gap, y: top + i * row, side: "R" }; });
    let svg = '<svg class="gsvg" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" aria-label="결합 그래프">';
    svg += '<rect class="lane" x="' + (margin - 8) + '" y="' + (top - 26) + '" width="' + (laneW + 16) + '" height="' + (H - top + 12) + '" rx="8"/>';
    svg += '<rect class="lane" x="' + (margin + laneW + gap - 8) + '" y="' + (top - 26) + '" width="' + (laneW + 16) + '" height="' + (H - top + 12) + '" rx="8"/>';
    svg += '<text class="lanetitle" x="' + margin + '" y="' + (top - 9) + '">Repository Graph (코드 구조, AST)</text>';
    svg += '<text class="lanetitle" x="' + (margin + laneW + gap) + '" y="' + (top - 9) + '">Execution Graph (관측된 실행·검사)</text>';
    const ny = (id) => pos[id].y + 10;
    for (const ed of gd.edges || []) {
      const a = pos[ed.source], b = pos[ed.target];
      if (!a || !b) continue;
      let d;
      if (a.side !== b.side) {
        const ax = a.side === "L" ? a.x + laneW : a.x, bx = b.side === "L" ? b.x + laneW : b.x;
        d = "M" + ax + "," + ny(ed.source) + " C" + (ax + (a.side === "L" ? 60 : -60)) + "," + ny(ed.source) + " " + (bx + (b.side === "L" ? 60 : -60)) + "," + ny(ed.target) + " " + bx + "," + ny(ed.target);
      } else {
        const ex = a.side === "L" ? a.x : a.x + laneW;
        const bulge = (a.side === "L" ? -1 : 1) * (14 + Math.min(Math.abs(a.y - b.y) / row, 12) * 4);
        d = "M" + ex + "," + ny(ed.source) + " C" + (ex + bulge) + "," + ny(ed.source) + " " + (ex + bulge) + "," + ny(ed.target) + " " + ex + "," + ny(ed.target);
      }
      const cls = "edge" + (ed.cross ? " cross" : "") + (pathEdges.has(ed.id) ? " path" : "");
      const tip = ed.relation + " · " + ed.observation + (ed.basis ? " · 근거 " + ed.basis : "") + (ed.cross ? " · 교차 연결" : "");
      svg += '<path class="' + cls + '" d="' + d + '" data-tip="' + esc(tip) + '"><title>' + esc(tip) + "</title></path>";
    }
    for (const n of nodes) {
      const p = pos[n.id];
      if (!p) continue;
      const cls = "node " + (n.namespace === "repo" ? "repo" : "exec") + (cands.has(n.id) ? " cand" : "") + (anchors.has(n.id) ? " anchor" : "");
      const text = n.label.length > 34 ? n.label.slice(0, 33) + "…" : n.label;
      const tip = n.kind + " · " + n.label + " · " + n.observation + (anchors.has(n.id) ? " · 실패 앵커" : "") + (cands.has(n.id) ? " · 원인 후보" : "");
      svg += '<g class="' + cls + '" data-tip="' + esc(tip) + '"><title>' + esc(tip) + '</title><rect x="' + p.x + '" y="' + p.y + '" width="' + laneW + '" height="20" rx="5"/>' +
        '<text class="k" x="' + (p.x + 6) + '" y="' + (p.y + 14) + '">' + esc((anchors.has(n.id) ? "앵커 " : "") + (cands.has(n.id) ? "후보 " : "") + n.kind) + "</text>" +
        '<text x="' + (p.x + 104) + '" y="' + (p.y + 14) + '">' + esc(text) + "</text></g>";
    }
    return svg + "</svg>";
  }

  function graph(state) {
    const parts = [header(state)];
    const entries = graphEntries(state);
    if (!entries.length) return parts.join("") + panel("그래프 기록 없음", "<p>이 데이터 기준에는 그래프 진단이 기록된 실행이 없다.</p>");
    const cur = entries.find((x) => x.key === state.graph) || entries[0];
    const groups = [...new Set(entries.map((x) => x.group))];
    parts.push('<div class="filters"><label>그래프 진단 기록<select data-key="graph">' + groups.map((g) => '<optgroup label="' + esc(g) + '">' +
      entries.filter((x) => x.group === g).map((x) => '<option value="' + esc(x.key) + '"' + (x === cur ? " selected" : "") + ">" +
        esc((x.run.scenario || x.run.task_id) + " · " + x.run.condition + " · " + x.iv.intervention_id + " → " + x.iv.strategy) + "</option>").join("") + "</optgroup>").join("") + "</select></label></div>");
    const gd = cur.iv.graph_detail;
    const tr = gd.trace || {};
    const names = Object.fromEntries((gd.nodes || []).map((n) => [n.id, n.label]));
    const cross = (gd.edges || []).filter((x) => x.cross).length;
    const repoN = (gd.nodes || []).filter((n) => n.namespace === "repo").length;
    const reasons = {};
    (gd.unresolved || []).forEach((u) => { const k = u.relation + ": " + u.reason; reasons[k] = (reasons[k] || 0) + 1; });
    const parsers = [...new Set((cur.run.checks || []).map((c) => c.parser_id))];
    const frames = (cur.run.checks || []).flatMap((c) => c.cases || []).filter((c) => c.status === "FAIL");
    if (!cur.exp) parts.push('<div class="callout warn"><strong>S3 합성 검증 기록.</strong> 스크립트 어댑터가 예정된 코드를 작성했고 모델 호출은 0건이다. 그래프 진단 메커니즘의 동작 증거이며, 라이브 실험 결과가 아니다.</div>');
    else if (cur.exp.experiment_id !== state.exp) parts.push(statusBanner(cur.exp));
    parts.push('<div class="tiles">' + tile("그래프 상태", gd.status || "N/A", "builder " + (gd.builder_version || "")) +
      tile("노드", fmt((gd.nodes || []).length), "코드 " + repoN + " · 실행 " + ((gd.nodes || []).length - repoN)) +
      tile("엣지", fmt((gd.edges || []).length), "교차 연결 " + cross + "개") +
      tile("미해결 연결", fmt((gd.unresolved || []).length), "근거 없는 연결은 만들지 않음") +
      tile("탐색", fmt(tr.visited_count) + " 방문", (tr.termination || "") + (tr.truncated ? " · truncated=true (분기·후보 상한 적용)" : " · truncated=false")) +
      tile("원인 후보", fmt((tr.candidates || []).length), "top-k " + (tr.top_k || "N/A")) +
      tile("선택 전략", cur.iv.strategy || "N/A", (cur.iv.graph || {}).fallback_reason || "유형별 근거 있음") + "</div>");
    const explain = cross === 0
      ? '<div class="callout warn"><strong>왜 원인 후보가 0개인가 (기록에서 확인).</strong> 이 결합 그래프에는 코드↔실행 교차 연결이 0개다. 미해결 기록: ' +
        esc(Object.entries(reasons).map(([k, v]) => k + " " + v + "건").join(", ") || "없음") + ". 공개 검사 파서 " +
        esc(parsers.join(", ")) + "는 suite 단위 결과만 남겨, 실패 사례 " + esc(frames.length) + "건 모두 traceback frame이 없고 테스트 ID가 검사 ID(" +
        esc([...new Set(frames.map((f) => f.test_id))].join(", ")) + ")다. 매핑 규칙은 근거 없는 TESTS_TARGET·FAILS_AT를 만들지 않으므로 앵커에서 코드 노드로 가는 경로가 없고, " +
        "정책은 <code>INSUFFICIENT_TYPED_EVIDENCE</code>로 GENERIC_RETRY를 선택했다. 개선 방향: 공개 검사 결과를 테스트 함수·frame 단위로 기록 (최종 발표 전 고도화 항목).</div>"
      : '<div class="callout"><strong>교차 연결 ' + esc(cross) + "개.</strong> 실패 앵커에서 TESTS_TARGET 등 근거 있는 연결을 따라 코드 노드까지 도달했고, 후보 " + esc((tr.candidates || []).length) +
        "개와 경로 " + esc((tr.paths || []).length) + "개를 기록했다. 후보는 원인 확정이 아니라 가설(observation=inferred)이다.</div>";
    parts.push(panel("결합 그래프 (Repository · Execution · Cross-Graph Mapping)", explain +
      '<div class="legend"><span><span class="sw" style="border:2px solid var(--s1)"></span>코드 노드</span><span><span class="sw" style="border:2px solid var(--s2)"></span>실행 노드</span>' +
      '<span><span class="sw" style="background:var(--s3)"></span>교차 연결 · 탐색 경로 · 원인 후보</span><span>굵은 테두리 = 실패 앵커</span><span>회색 곡선 = 같은 그래프 안의 관계 (CONTAINS·CALLS·IMPORTS·RETURNS 등)</span></div>' +
      '<div class="fig">' + graphSvg(gd) + "</div>" + sourceNote("run " + cur.run.run_id + " graphs/" + short((cur.iv.graph || {}).graph_id, 12) + "… (노드·엣지 ID는 앞 16자)")));
    parts.push('<div class="grid2">' + panel("원인 후보 (가설)", table(["순위", "종류", "위치", "점수", "구성", "관계", "관측"], (tr.candidates || []).map((c) => [fmt(c.rank), esc(c.kind), esc((c.path || "") + (c.qualified_name ? "::" + c.qualified_name : "")),
      isNum(c.score) ? c.score.toFixed(3) : "N/A", esc(compText(c.components)), esc((c.relations || []).join("→")), esc(c.observation + (c.alternative ? " · 대안" : ""))]), { numeric: [0, 3] }) +
      "<h3>탐색 경로</h3>" + ((tr.paths || []).length ? "<ol>" + tr.paths.map((p) => "<li>" + (p.nodes || []).map((n, i) => esc(names[n] || n) + (i < (p.relations || []).length ? ' <span class="muted">—' + esc(p.relations[i]) + "(" + esc((p.directions || [])[i] || "") + ")→</span> " : "")).join("") + ' <span class="muted">비용 ' + esc(p.cost) + "</span></li>").join("") + "</ol>" : '<p class="muted">경로 없음 — 앵커에서 코드 노드로 가는 근거 있는 연결이 없었다.</p>') +
      kv([["탐색 한도", esc("깊이 " + tr.depth_limit + " · 방문 " + tr.visit_limit + " · 분기 " + tr.branch_limit + " · top-k " + tr.top_k)], ["허용 관계", esc((tr.allowed_relations || []).join(", "))]])) +
      panel("미해결 연결과 경고", table(["관계", "사유", "노드"], (gd.unresolved || []).map((u) => [esc(u.relation), esc(u.reason), esc(names[u.node] || u.node)])) +
        "<details><summary>그래프 경고 " + esc((gd.warnings || []).length) + "개</summary><ul>" + (gd.warnings || []).map((w) => "<li><code>" + esc(w) + "</code></li>").join("") + "</ul></details>" +
        "<details><summary>엣지 표 (" + esc((gd.edges || []).length) + ")</summary>" + table(["관계", "출발", "도착", "관측", "근거"], (gd.edges || []).map((x) => [esc(x.relation) + (x.cross ? ' <span class="badge s1">교차</span>' : ""), esc(names[x.source] || x.source), esc(names[x.target] || x.target), esc(x.observation), na(x.basis)])) + "</details>") + "</div>");
    return parts.join("");
  }

  // ------------------------------------------------------------------ replay
  const STEPS = ["실패 발생", "실패 감지", "그래프 진단", "복구 전략 선택", "컨텍스트 구성", "후속 실행", "평가 결과"];

  function replayStep(state, run, i, isSynthetic) {
    const iv = (run.interventions || [])[0] || null;
    const p = run.policy || {};
    const t1 = (run.checks || []).filter((c) => c.turn === "turn-0001");
    const ev = (turn) => (run.events || []).filter((x) => x.turn === turn);
    const turnSummary = (turn) => {
      const list = ev(turn);
      const cmds = list.filter((x) => x.kind === "COMMAND_RESULT");
      const changes = list.filter((x) => x.kind === "CHANGE");
      return kv([["명령", fmt(cmds.length) + "개 · exit 0 " + fmt(cmds.filter((x) => x.exit_code === 0).length) + "개"],
        ["파일 변경", changes.length ? changes.map((x) => (x.hunks || []).map((h) => esc(h.path)).join(", ")).join(" / ") : '<span class="muted">없음</span>'],
        ["이벤트", fmt(list.length) + "개 (원문은 제한 자료)"]]);
    };
    switch (i) {
      case 0:
        return "<p>첫 턴이 끝난 뒤 하네스가 공개 검사를 직접 실행했다.</p>" + table(["검사", "결과", "오류"], t1.map((c) => [esc(c.check_id), status(c.status), esc((c.cases || []).map((x) => x.error_type || "").filter(Boolean).join(", ") || "—")])) +
          "<h3>1턴 행동</h3>" + turnSummary("turn-0001");
      case 1:
        if (!iv) {
          if (p.external_intervention === false) return '<div class="callout">조건 A(Vanilla)는 외부 개입이 없도록 설계됐다. 1턴 공개 검사 결과가 ' + esc(seen(run.first_turn_public)) + "여도 복구를 전달하지 않는다.</div>";
          return '<div class="callout">공통 감지 규칙이 개입 트리거를 내지 않았다. 1턴 공개 검사가 ' + esc(seen(run.first_turn_public)) + "였기 때문이다. 공개 검사가 놓친 요구는 하네스가 알 수 없다 (공개 결과만 복구에 사용).</div>";
        }
        return kv([["트리거", esc((iv.trigger_ids || []).map((t) => t + " — " + (TRIGGER[t] || "?")).join(" / "))], ["대상 검사", esc((iv.check_ids || []).join(", "))],
          ["앵커", fmt(iv.anchor_count) + "개 (공개 검사 결과 ID)"], ["체크포인트 키", "<code>" + esc(iv.checkpoint) + "…</code> (같은 체크포인트 중복 개입 금지)"]]) +
          '<p class="small muted">B–E는 같은 감지 규칙·앵커·개입 상한을 쓴다 (CT04).</p>';
      case 2:
        if (!iv) return '<p class="muted">개입이 없어 진단 단계가 없다.</p>';
        if (!iv.graph) return '<div class="callout">조건 ' + esc(run.condition) + "는 FLAT 진단을 쓴다 (설계상 그래프 미호출, CT05). 실패 증거 목록을 그대로 전략 선택에 넘긴다.</div>";
        {
          const gd = iv.graph_detail || {};
          const tr = gd.trace || {};
          const cross = (gd.edges || []).filter((x) => x.cross).length;
          const key = (isSynthetic ? "synthetic" : state.exp) + "|" + run.run_id + "|" + iv.intervention_id;
          return kv([["그래프", esc("상태 " + iv.graph.status + " · 노드 " + (gd.nodes || []).length + " · 엣지 " + (gd.edges || []).length + " · 교차 연결 " + cross)],
            ["탐색", esc("방문 " + tr.visited_count + " · 종료 " + tr.termination + " · 후보 " + (tr.candidates || []).length + " · 경로 " + (tr.paths || []).length)],
            ["대체 사유", na(iv.graph.fallback_reason)], ["시간", fmt(iv.graph.build_ms) + "ms (구축)"]]) +
            (cross === 0 ? '<div class="callout warn">교차 연결이 없어 앵커에서 코드 후보에 도달하지 못했다. 근거 없는 후보를 만들지 않는 것이 설계 규칙이다.</div>'
              : '<div class="callout">근거 있는 연결로 코드 후보 ' + esc((tr.candidates || []).length) + "개에 도달했다. 후보는 가설이다.</div>") +
            '<p><a href="' + esc(toHash(Object.assign({}, state, { tab: "graph", graph: key }))) + '">그래프 탐색 화면에서 보기 →</a></p>';
        }
      case 3:
        if (!iv) return '<p class="muted">개입이 없어 전략 선택이 없다. 종료 사유: ' + esc((run.report || {}).stop_reason) + "</p>";
        return kv([["전략", esc(iv.strategy) + ' <span class="muted">— ' + esc(STRATEGY[iv.strategy] || "") + "</span>"], ["지시 목표", esc((iv.directive || {}).objective || "N/A")],
          ["근거 상태", esc((iv.directive || {}).confidence_status || "N/A") + " · 사유 " + esc((iv.directive || {}).reason || "N/A")],
          ["대상", (iv.targets || []).length ? esc(iv.targets.join(", ")) : '<span class="muted">없음</span>'], ["다음 검사", esc(((iv.directive || {}).next_checks || []).join(", "))]]);
      case 4: {
        const pkg = (run.context || []).find((c) => c.stage === "RECOVERY");
        if (!pkg) return '<p class="muted">복구 컨텍스트 패키지가 없다 (개입 없음).</p>';
        return kv([["정책", esc(pkg.policy) + " · 전략 " + esc(pkg.strategy_id)], ["보조 정보", fmt(pkg.estimated_supplement_tokens) + " / " + fmt(pkg.cap_tokens) + " 추정 토큰"],
          ["선택", fmt((pkg.decisions || []).filter((d) => d.selected).length) + " / " + fmt((pkg.decisions || []).length) + " · 핵심 근거 " + fmt(pkg.core_count)],
          ["message_hash", "<code>" + esc(pkg.message_hash) + "</code>"]]) + contextTable(pkg);
      }
      case 5: {
        if (!iv) return '<p class="muted">후속 턴이 없다.</p>';
        const t2 = (run.checks || []).filter((c) => c.turn === "turn-0002");
        return "<p>복구 패키지를 같은 세션의 다음 턴으로 전달했다 (" + esc((iv.events || []).map((x) => x.event).join(" → ")) + ").</p>" + turnSummary("turn-0002") +
          table(["검사 (2턴)", "결과"], t2.map((c) => [esc(c.check_id), status(c.status)])) +
          (iv.outcome ? kv([["지시 수행 판정", esc(iv.outcome.adherence_status + " (" + iv.outcome.adherence_basis + ")")]]) : "");
      }
      default: {
        const r = run.report || {};
        const v = run.verdict;
        return kv([["종료 사유", esc(r.stop_reason)], ["턴 · 복구", fmt(r.turns_used) + " · " + fmt(r.recoveries_delivered)], ["최종 공개 검사", status(r.public_check_status)],
          ["독립 판정", v ? status(v.status) + ' <span class="small muted">기능 ' + esc(v.functional_status) + " · 회귀 " + esc(v.regression_status) + " · 지침 " + esc(v.instruction_status) + "</span>" : '<span class="muted">독립 판정 없음 (합성 검증)</span>'],
          ["변경 파일", (r.changed_paths || []).length ? esc(r.changed_paths.join(", ")) : '<span class="muted">없음 (빈 패치)</span>'], ["모델 토큰 · 시간", fmt(r.observed_model_tokens) + " · " + secs(r.elapsed_ms)]]) +
          (r.stop_reason === "NO_SAFE_CONTINUATION" ? '<div class="callout warn">후속 턴 뒤 snapshot이 바뀌지 않아 새 관측이 없었다. 같은 체크포인트에 다시 개입하지 않는 규칙에 따라 안전 종료했다.</div>' : "");
      }
    }
  }

  function replay(state) {
    const parts = [header(state)];
    const cases = replayCases(state);
    if (!cases.length) return parts.join("") + panel("Replay 사례 없음", "<p>재생할 기록이 없다.</p>");
    const cur = cases.find((x) => x.key === state["case"]) || cases[0];
    const step = Math.max(0, Math.min(STEPS.length - 1, parseInt(state.step || "0", 10) || 0));
    const groups = [...new Set(cases.map((x) => x.group))];
    const sel = '<label>재생할 실행 기록<select data-key="case">' + groups.map((g) => '<optgroup label="' + esc(g) + '">' + cases.filter((x) => x.group === g).map((x) =>
      '<option value="' + esc(x.key) + '"' + (x === cur ? " selected" : "") + ">" + esc(x.title) + "</option>").join("") + "</optgroup>").join("") + "</select></label>";
    parts.push('<div class="callout"><strong>Replay — 저장된 실행 기록의 재생.</strong> 이 화면은 모델이나 Codex를 호출하지 않는다. 라이브 실행이 아니며, 각 단계의 값은 해당 run의 원장·전이·개입·컨텍스트·판정 기록에서 읽는다.</div>',
      '<div class="filters">' + sel + '<button class="btn" data-act="prev"' + (step === 0 ? " disabled" : "") + '>← 이전</button><button class="btn primary" data-act="play">▶ 자동 재생</button><button class="btn" data-act="next"' + (step === STEPS.length - 1 ? " disabled" : "") + ">다음 →</button></div>");
    if (!cur.exp) parts.push('<div class="callout warn">S3 합성 검증 기록 (스크립트 어댑터, 모델 호출 0건). 메커니즘 동작 증거이며 실험 결과가 아니다.</div>');
    parts.push(panel(cur.title, kv([["run_id", "<code>" + esc(cur.run.run_id) + "</code>"], ["정책", esc(policyText(cur.run.policy))]]) +
      '<div class="steps">' + STEPS.map((s, i) => '<button data-act="step" data-step="' + i + '" class="' + (i === step ? "on" : i < step ? "done" : "") + '">' + (i + 1) + ". " + esc(s) + "</button>").join("") + "</div>" +
      '<div class="progress"><span style="width:' + ((step + 1) / STEPS.length * 100).toFixed(1) + '%"></span></div>' +
      "<h3>" + (step + 1) + ". " + esc(STEPS[step]) + "</h3>" + replayStep(state, cur.run, step, !cur.exp)));
    return parts.join("");
  }

  // ------------------------------------------------------------------ audit
  function audit(state) {
    const parts = [header(state)];
    const ids = experimentsIn(state.release);
    const blocks = ids.map((id) => {
      const e = exp(id);
      if (!e) return "";
      const m = ((D && D.manifests) || {})[id] || {};
      return "<details" + (id === state.exp ? " open" : "") + "><summary><strong>" + esc(label(id)) + "</strong> " + statusBadge(e.status) + " <code>" + esc(id) + "</code></summary>" +
        "<p>" + esc(e.status_reason || "") + "</p>" +
        table(["기록", "단계", "내용"], (e.timeline || []).map((t) => [esc(t.ref), esc(t.step), esc(t.detail)])) +
        (m.exported_at ? '<p class="small muted">데이터 내보낸 시각 ' + esc(stamp(m.exported_at)) + " · 상태 갱신 " + esc(stamp(m.status_updated_at)) +
          " · 원천 파일 " + esc((m.source || {}).inputs) + "개 (입력 해시 " + esc(short((m.source || {}).inputs_sha256, 12)) + ") · data_sha256 " + esc(short(m.data_sha256, 12)) + "</p>" +
          table(["시각", "이전 상태", "새 상태"], (m.status_history || []).map((h) => [esc(stamp(h.at)), esc(h.from || "—"), esc(h.to)])) : "") + "</details>";
    });
    parts.push(panel("실험 버전별 상태와 이력", blocks.join("")));
    const finalized = ids.map(exp).filter((e) => e && e.derived && e.derived.commands);
    if (finalized.length) {
      const maxv = Math.max(1, ...finalized.map((e) => e.derived.commands.total || 0));
      parts.push(panel("에이전트 셸 명령 결과 (결함 발견의 근거)", table(["실험", "상태", "명령", "exit 0", "exit ≠ 0", "분포", "빈 패치 실패", "성공 / 판정"], finalized.map((e) => {
        const c = e.derived.commands;
        const w0 = ((c.exit_0 || 0) / maxv * 100).toFixed(1), w1 = ((c.exit_nonzero || 0) / maxv * 100).toFixed(1);
        const sl = e.slots || [];
        return [esc(label(e.experiment_id)), statusBadge(e.status), fmt(c.total || 0), fmt(c.exit_0 || 0), fmt(c.exit_nonzero || 0),
          '<span class="bar"><span class="b0" style="width:' + w0 + '%" title="exit 0"></span><span class="b1" style="width:' + w1 + '%" title="exit ≠ 0"></span></span>',
          fmt(e.derived.empty_patch_failures), fmt(sl.filter((s) => s.y === 1).length) + " / " + fmt(sl.filter((s) => s.y !== null && s.y !== undefined).length)];
      }), { numeric: [2, 3, 4, 6] }) + '<div class="legend"><span><span class="sw" style="background:var(--s1)"></span>exit 0</span><span><span class="sw" style="background:var(--crit)"></span>exit ≠ 0</span></div>' +
        '<p class="small muted">정규화 증거 원장의 COMMAND_RESULT 집계. v1의 비정상 종료는 모두 MXC launcher SYSTEMROOT 누락 오류였다(제한 원시 로그 검토, EL-20261127-034). v2의 exit ≠ 0은 실패하는 테스트 실행 등 정상 결과다.</p>'));
    }
    const r = release(state.release);
    const rec = r ? r.verification : null;
    parts.push(panel("동결·재현성 검증 기록 (모델 호출 0)", (rec ? kv([["기록일", esc(rec.recorded_on)], ["Python · OS", esc(rec.python + " · " + rec.os)], ["git HEAD", "<code>" + esc(rec.git_head) + "</code> (" + esc(rec.working_tree) + ")"], ["라이브 테스트", esc(rec.live_tests)]].concat(rec.dashboard_runtime ? [["대시보드 런타임", esc(rec.dashboard_runtime)]] : [])) +
      table(["항목", "명령", "종료 코드", "결과", "시간"], (rec.steps || []).map((s) => [esc(s.id), "<code>" + esc(s.invocation) + "</code>", s.exit_code === 0 ? '<span class="badge good">0</span>' : '<span class="badge crit">' + esc(s.exit_code) + "</span>", esc(s.result_line), esc(s.seconds) + "초"]))
      : '<p class="muted">이 데이터 기준에는 검증 기록이 없다. release를 고르면 해당 시점의 기록이 표시된다.</p>') +
      '<p class="small">공개 자료만으로 다시 확인: <code>python -m analysis.public_export verify</code>, <code>python -m analysis.submission_bundle verify-public</code> — run_status.csv에서 동결 AMD-01 분석(bootstrap 10,000회 포함)을 다시 계산해 바이트 단위로 대조한다.</p>'));
    const e = exp(state.exp);
    if (e && e.meta && e.meta.freeze && e.meta.freeze.sha256) {
      const m = e.meta;
      parts.push(panel(label(state.exp) + " 동결과 승인 (해시 사슬)", kv([
        ["동결 manifest", "<code>" + esc(m.freeze.path) + "</code> · <code>" + esc(m.freeze.sha256) + "</code>"],
        ["승인 기록", "<code>" + esc(m.approval.path) + "</code> · <code>" + esc(m.approval.sha256) + "</code> · " + esc(m.approval.approved_at)],
        ["승인된 동결 해시", "<code>" + esc(m.approval.approved_freeze_sha256) + "</code> " + (m.approval.approved_freeze_sha256 === m.freeze.sha256 ? '<span class="badge good">일치</span>' : '<span class="badge crit">불일치</span>')],
        ["편입 기록 (AMD-01 3차)", "<code>" + esc(m.freeze.inclusion_sha256) + "</code>"],
        ["analysis.json", "<code>" + esc((m.analysis || {}).analysis_json_sha256) + "</code>"],
        ["대체한 실험", esc(JSON.stringify(m.approval.supersedes || "없음"))],
      ]) + "<h3>승인 시 공개한 한계</h3><ul>" + (m.approval.disclosed_limitations || []).map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul>"));
    }
    if (e && e.derived && e.derived.parity && e.conditions) {
      const d = e.derived;
      parts.push(panel("정책 격리 — 실제 실행 기록에서 다시 확인", table(["확인 항목", "결과"], [
        ["B/D 최초 메시지 바이트 동일 (CT03)", fmt(d.parity.B_equals_D) + " / " + fmt(d.parity.tasks) + " 과제"],
        ["C/E 최초 메시지 바이트 동일 (CT03)", fmt(d.parity.C_equals_E) + " / " + fmt(d.parity.tasks) + " 과제"],
        ["B와 C는 다른 패키지 (DEFAULT ≠ DCC)", fmt(d.parity.tasks - d.parity.B_equals_C) + " / " + fmt(d.parity.tasks) + " 과제에서 다름"],
        ["필수 지침 해시가 A–E에서 동일", fmt(d.parity.fixed_instruction_same_A_to_E) + " / " + fmt(d.parity.tasks) + " 과제"],
      ].map((r2) => r2.map(esc))) + table(["조건", "실행", "그래프 진단", "FLAT 진단", "컨텍스트 패키지 (단계:정책)"], COND.map((c) => {
        const x = (d.isolation || {})[c] || {};
        return [esc(COND_LABEL[c]), fmt(x.runs), fmt(x.graph_diagnoses), fmt(x.flat_diagnoses), esc(Object.entries(x.context || {}).map(([k, v]) => k + " ×" + v).join(", "))];
      }), { numeric: [1, 2, 3] }) + '<p class="small muted">A/B/C의 그래프 호출 0, A/B/D의 DCC 호출 0 (CT05·CT06), A의 외부 보조 정보 없음 (CT07).</p>'));
    }
    const gu = e && e.derived ? e.derived.graph_use || {} : {};
    parts.push(panel("연구의 한계와 미측정 항목", table(["항목", "상태", "근거·영향"], (C().limitations || []).map((x) => x.map(esc))) +
      (e && e.derived && e.conditions ? '<div class="callout warn"><strong>데이터에서 확인한 추가 한계.</strong> 이 실험의 라이브 그래프 진단 ' + fmt(Object.values(gu).reduce((a, b) => a + b, 0)) +
        "회 중 유형별 전략 근거로 이어진 경우는 " + fmt(Object.entries(gu).filter(([k]) => !k.startsWith("GENERIC_RETRY")).reduce((a, [, v]) => a + v, 0)) + "회다. " +
        "실패 중 " + fmt(e.derived.public_pass_private_fail) + "건은 공개 검사 PASS·독립 판정 FAIL로, 공개 검사가 놓친 요구가 있거나 비공개 검사가 명세보다 엄격할 가능성이 있다 (큐레이터 사후 검토 대상, 결과를 보고 과제를 빼지 않음).</div>" : "")));
    return parts.join("");
  }

  // ------------------------------------------------------------------ state, routing, DOM glue
  const VIEWS = { overview, benchmark, trace, graph, context, replay, audit };
  const DEFAULT_STATE = { tab: "overview", release: "", exp: "", task: "AAH-D04", cond: "E", graph: "", case: "", step: "0" };

  function parseHash(hash) {
    const h = String(hash || "").replace(/^#/, "");
    const [tab, query] = h.split("?");
    const state = Object.assign({}, DEFAULT_STATE);
    if (tab && VIEWS[tab]) state.tab = tab;
    (query || "").split("&").filter(Boolean).forEach((pair) => {
      const i = pair.indexOf("=");
      if (i > 0) state[decodeURIComponent(pair.slice(0, i))] = decodeURIComponent(pair.slice(i + 1));
    });
    const rids = releaseOptions().map((o) => o[0]);
    if (!rids.includes(state.release)) state.release = rids.includes(C().default_release) ? C().default_release : rids[0] || "";
    const exps = experimentsIn(state.release);
    if (!exps.includes(state.exp)) {
      const r = release(state.release);
      state.exp = r && exps.includes(r.primary) ? r.primary : exps[0] || "";
    }
    const e = exp(state.exp);
    if (e) {
      const tasks = Object.keys(e.tasks || {}).sort();
      if (!tasks.includes(state.task)) state.task = tasks.includes("AAH-D04") ? "AAH-D04" : tasks[0] || "";
    }
    if (!COND.includes(state.cond)) state.cond = "E";
    return state;
  }

  function toHash(state) {
    const keys = ["release", "exp"].concat({ trace: ["task", "cond"], context: ["task", "cond"], graph: ["graph"], replay: ["case", "step"] }[state.tab] || []);
    const q = keys.filter((k) => state[k] !== undefined && state[k] !== "").map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(state[k])).join("&");
    return "#" + state.tab + (q ? "?" + q : "");
  }

  function view(state) {
    try {
      if (!D || !D.experiments) return panel("데이터 묶음 없음", "<p>내장 데이터를 읽지 못했다.</p>", "err");
      return (VIEWS[state.tab] || overview)(state);
    } catch (error) {
      return panel("화면을 그리는 중 오류", "<p>이 화면을 그리지 못했다: <code>" + esc(error && error.message) + "</code>. 다른 탭은 계속 쓸 수 있다. 데이터 묶음 해시를 확인한다.</p>", "err");
    }
  }

  function options(state) {
    const s = parseHash(toHash(state || DEFAULT_STATE));
    return { releases: releaseOptions().map((o) => o[0]), exps: experimentsIn(s.release),
      graphKeys: D ? graphEntries(s).map((x) => x.key) : [], caseKeys: D ? replayCases(s).map((x) => x.key) : [], state: s };
  }

  function init(data, figures) {
    D = normalize(data, figures);
    const o = D ? options(DEFAULT_STATE) : { releases: [], exps: [], graphKeys: [], caseKeys: [] };
    return { kind: D ? D.kind : null, releases: o.releases, exps: o.exps, graphKeys: o.graphKeys, caseKeys: o.caseKeys, steps: STEPS.length };
  }

  const App = { init, view, parseHash, toHash, options, esc, fmt, pp, pct, COND, STEPS, LATEST };
  root.AAHDashboard = App;

  if (typeof document === "undefined") return;

  // ---- browser only
  let state = null;
  let timer = null;
  const readJson = (id) => {
    try { return JSON.parse(document.getElementById(id).textContent); } catch (e) { return null; }
  };
  init(readJson("aah-data"), readJson("aah-figures") || {});

  function render() {
    const main = document.getElementById("view");
    main.innerHTML = view(state);
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === state.tab)));
  }

  function go(next) {
    state = parseHash(toHash(Object.assign({}, state, next)));
    const h = toHash(state);
    if (location.hash !== h) history.replaceState(null, "", h);
    render();
  }

  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

  window.addEventListener("hashchange", () => { stopTimer(); state = parseHash(location.hash); render(); });
  document.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) { stopTimer(); go({ tab: tab.dataset.tab }); return; }
    const act = event.target.closest("[data-act]");
    if (!act) return;
    const step = parseInt(state.step || "0", 10) || 0;
    if (act.dataset.act === "prev") { stopTimer(); go({ step: String(Math.max(0, step - 1)) }); }
    if (act.dataset.act === "next") { stopTimer(); go({ step: String(Math.min(STEPS.length - 1, step + 1)) }); }
    if (act.dataset.act === "step") { stopTimer(); go({ step: act.dataset.step }); }
    if (act.dataset.act === "play") {
      stopTimer();
      go({ step: "0" });
      timer = setInterval(() => {
        const s = parseInt(state.step || "0", 10) || 0;
        if (s >= STEPS.length - 1 || state.tab !== "replay") { stopTimer(); return; }
        go({ step: String(s + 1) });
      }, 2200);
    }
  });
  document.addEventListener("change", (event) => {
    const key = event.target.dataset && event.target.dataset.key;
    if (!key) return;
    stopTimer();
    const next = { [key]: event.target.value };
    if (key === "case" || key === "graph") next.step = "0";
    if (key === "release") { next.exp = ""; next.graph = ""; next.case = ""; next.step = "0"; }
    if (key === "exp") { next.graph = ""; next.case = ""; next.step = "0"; }
    go(next);
  });
  const tip = document.getElementById("tip");
  document.addEventListener("pointermove", (event) => {
    const el = event.target.closest && event.target.closest("[data-tip]");
    if (!el) { tip.hidden = true; return; }
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    const x = Math.min(event.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    const y = Math.min(event.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  });
  state = parseHash(location.hash);
  render();
})(typeof window !== "undefined" ? window : globalThis);
