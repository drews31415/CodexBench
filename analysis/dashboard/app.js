/* AAH research dashboard (static, GitHub Pages). Reads only the embedded public data: the versioned public store
 * (public_data/: catalog, releases, per-experiment exports, synthetic suites and the re-extracted repository graphs).
 * Every view is a pure function (state) -> HTML string so it can be rendered and tested without a browser; the DOM
 * glue at the bottom (navigation history, graph zoom/pan/selection, replay playback, copy buttons, theme) only runs
 * in a page. All interpolated values pass through esc(). Nothing here calls a model, Codex or the network.
 * The submission snapshot keeps its own frozen copy of the previous sources (analysis/dashboard/submission-2026-11-27). */
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ vocabulary
  const COND = ["A", "B", "C", "D", "E"];
  const COND_NAME = { A: "Vanilla", B: "Flat Retry", C: "Context-only", D: "Graph-only", E: "Adaptive Harness" };
  const COND_LABEL = { A: "A Vanilla", B: "B Flat Retry", C: "C Context-only", D: "D Graph-only", E: "E Adaptive Harness" };
  const COND_DESC = {
    A: "Codex 기본 실행 · 외부 개입·보조 정보 없음", B: "일반 재시도 + 기본 컨텍스트(DEFAULT)",
    C: "일반 재시도 + 동적 컨텍스트(DCC)", D: "그래프 진단·유형별 전략 + 기본 컨텍스트", E: "그래프 진단·유형별 전략 + DCC",
  };
  // TECHNICAL_SPEC §4.3. Shown only when no run of the condition carries its own resolved policy.
  const POLICY_SPEC = {
    A: { initial_context: "NONE", diagnosis: "NONE", strategy: "NATIVE", recovery_context: "NONE", external_intervention: false },
    B: { initial_context: "DEFAULT", diagnosis: "FLAT", strategy: "GENERIC", recovery_context: "DEFAULT", external_intervention: true },
    C: { initial_context: "DCC", diagnosis: "FLAT", strategy: "GENERIC", recovery_context: "DCC", external_intervention: true },
    D: { initial_context: "DEFAULT", diagnosis: "GRAPH", strategy: "TYPED", recovery_context: "DEFAULT", external_intervention: true },
    E: { initial_context: "DCC", diagnosis: "GRAPH", strategy: "TYPED", recovery_context: "DCC", external_intervention: true },
  };
  const CTX_KO = { NONE: "없음", DEFAULT: "DEFAULT", DCC: "DCC" };
  const DIAG_KO = { NONE: "없음", FLAT: "일반 (FLAT)", GRAPH: "그래프 (GRAPH)" };
  const STRAT_KO = { NATIVE: "Codex 자체 동작", GENERIC: "일반 재시도", TYPED: "유형별 전략" };
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
  const REASON = {
    SELECTED: "예산 안에서 한계 효용이 가장 높아 선택", CORE_REQUIRED: "필수 근거 — 점수와 무관하게 항상 포함",
    MERGED_DUPLICATE: "같은 내용의 중복 후보 — 하나로 병합", NON_POSITIVE_UTILITY: "한계 효용 ≤ 0 — 이미 담긴 정보와 겹침",
    OVER_CAP: "넣으면 보조 정보 예산을 넘음", LOWER_RANK: "더 높은 효용의 후보에 밀림", INVALID: "현재 스냅샷과 맞지 않는 정보",
  };
  // src/aah/context/dcc.py — used only to draw the score composition, and only when it reproduces the recorded score.
  const DCC_WEIGHTS = {
    INITIAL: { relevance: 0.5, scope: 0.3, validity: 0.2, failure: 0 },
    RECOVERY: { relevance: 0.25, scope: 0.2, validity: 0.2, failure: 0.35 },
  };
  const COMP_KO = { relevance: "관련성", scope: "범위", validity: "유효성", failure: "실패 연관" };
  const KIND = {
    FILE: { tag: "FILE", ko: "파일" }, CLASS: { tag: "CLASS", ko: "클래스" }, FUNCTION: { tag: "FN", ko: "함수·메서드" },
    TEST_FUNCTION: { tag: "TEST", ko: "테스트 함수 (빌더가 인식한 것만)" }, TEST: { tag: "CHECK", ko: "공개 검사 결과" },
    FAILURE: { tag: "FAIL", ko: "실패 관측" }, COMMAND: { tag: "CMD", ko: "명령 시작" }, COMMAND_RESULT: { tag: "EXIT", ko: "명령 종료 코드" },
    CHANGE: { tag: "DIFF", ko: "파일 변경" },
  };
  const KIND_ORDER = ["FILE", "CLASS", "FUNCTION", "TEST_FUNCTION", "TEST", "FAILURE", "COMMAND", "COMMAND_RESULT", "CHANGE"];
  const REL = {
    CONTAINS: ["contains", "포함 — 파일 → 클래스 → 함수 (AST)"], IMPORTS: ["imports", "import — 파일 → 저장소 안 파일"],
    CALLS: ["calls", "호출 — 정적 바인딩으로 대상이 하나로 확정된 것만"], INHERITS: ["inherits", "상속 — 저장소 안 클래스만"],
    TESTS: ["tests", "테스트 — TEST_FUNCTION이 호출하는 대상"], TESTS_TARGET: ["cross", "교차 · 공개 검사 결과 → 테스트 함수 (테스트 ID 검증 시)"],
    FAILS_AT: ["cross", "교차 · 실패 → 코드 위치 (traceback frame 검증 시)"], CHANGES: ["cross", "교차 · 변경 → 코드 (diff 범위)"],
    READS: ["cross", "교차 · 읽기 관측 → 파일"], OBSERVED_AFTER: ["exec", "실행 · 관측 순서 (인과관계 아님)"],
    SAME_FAILURE_AS: ["exec", "실행 · 같은 실패의 반복"], RETURNS: ["exec", "실행 · 명령·검사 → 결과"], RETRIES: ["exec", "실행 · 재시도"],
  };
  const REL_GROUP = {
    contains: "포함 CONTAINS", imports: "import IMPORTS", calls: "호출 CALLS", inherits: "상속 INHERITS", tests: "테스트 TESTS",
    cross: "교차 연결 (Cross-Graph)", exec: "실행 관계",
  };
  const relGroup = (r) => (REL[r] ? REL[r][0] : "exec");
  const WARN = {
    UNRESOLVED_CALLS: "대상이 정적 바인딩으로 하나로 확정되지 않은 호출 (내장 함수·메서드·외부 라이브러리 등). 추정해서 CALLS를 만들지 않는다.",
    UNRESOLVED_IMPORT: "저장소 밖 모듈 import (예: unittest). 저장소 파일이 아니라 연결하지 않는다.",
    UNRESOLVED_INHERITS: "저장소 밖 기반 클래스 상속 (예: unittest.TestCase).",
    PARSE_ERROR: "AST로 해석하지 못한 파일.",
    "TESTS_TARGET": "공개 검사 결과의 테스트 ID가 코드의 테스트 함수와 검증되지 않아 연결하지 않음.",
    "FAILS_AT": "traceback frame이나 파일 버전이 검증되지 않아 실패 위치를 코드에 연결하지 않음.",
  };
  // Fields the frozen submission snapshot (2026-11-27) predates; the public store carries them per experiment.
  const LEGACY = {
    "aah-main-emergency-v2": { label: "Main v2", tag: "v2", status_reason: "v1의 에이전트 셸 결함을 고친 뒤 같은 과제·조건·예산·분석으로 다시 실행한 주 결과. AMD-01 Emergency 규모(8과제 × A–E × 1회)라 모든 해석은 탐색적이다.", timeline: [] },
    "aah-main-emergency-v1": { label: "Main v1", tag: "v1-invalid", superseded_by: "aah-main-emergency-v2", status_reason: "에이전트 셸 명령이 MXC launcher의 SYSTEMROOT 누락 오류로 하나도 실행되지 않은 상태에서 실행됐다. 결함 발견 이력으로만 보존한다.", timeline: [] },
  };

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
  const p2 = (n) => String(n).padStart(2, "0");
  // UTC with the KST equivalent; the KST calendar date is shown when it differs (21:00 UTC = next day 06:00 KST).
  const stamp = (iso) => {
    if (!iso) return "N/A";
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
    if (!m) return String(iso);
    const k = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] + 9, +m[5]));
    const day = m[1] + "-" + m[2] + "-" + m[3];
    const kDay = k.getUTCFullYear() + "-" + p2(k.getUTCMonth() + 1) + "-" + p2(k.getUTCDate());
    return day + " " + m[4] + ":" + m[5] + " UTC (" + (kDay === day ? "" : kDay + " ") + p2(k.getUTCHours()) + ":" + p2(k.getUTCMinutes()) + " KST)";
  };
  const status = (s) => {
    if (s === "PASS") return '<span class="badge good"><span class="dot good"></span>PASS</span>';
    if (s === "FAIL") return '<span class="badge crit"><span class="dot crit"></span>FAIL</span>';
    if (s === null || s === undefined) return '<span class="badge"><span class="dot mute"></span>기록 없음</span>';
    return '<span class="badge warn"><span class="dot warn"></span>' + esc(s) + "</span>";
  };
  const isValid = (s) => s === "VALID" || s === "VALID_EXPLORATORY";
  const isInvalid = (s) => !!s && s.indexOf("INVALID") === 0;
  const statusBadge = (s) => {
    const cls = isValid(s) ? "good" : isInvalid(s) ? "crit" : "warn";
    return '<span class="badge ' + cls + '">' + esc(STATUS_LABEL[s] || s || "상태 미상") + "</span>";
  };
  const table = (head, rows, opts) => {
    const numeric = (opts && opts.numeric) || [];
    if (!rows.length) return '<p class="muted">' + esc((opts && opts.empty) || "기록 없음") + "</p>";
    return '<div class="tablewrap" tabindex="0"><table' + (opts && opts.cls ? ' class="' + opts.cls + '"' : "") + ">" +
      (opts && opts.caption ? '<caption class="sr">' + esc(opts.caption) + "</caption>" : "") + "<thead><tr>" +
      head.map((h, i) => '<th scope="col" class="' + (numeric.includes(i) ? "num" : "") + '">' + esc(h) + "</th>").join("") +
      "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c, i) =>
        '<td class="' + (numeric.includes(i) ? "num" : "") + '">' + c + "</td>").join("") + "</tr>").join("") +
      "</tbody></table></div>";
  };
  const kv = (pairs) => '<dl class="kv">' + pairs.map(([k, v]) => "<dt>" + esc(k) + "</dt><dd>" + v + "</dd>").join("") + "</dl>";
  const select = (key, value, options, label, title) => '<label>' + esc(label) + '<select data-key="' + esc(key) + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" +
    options.map(([v, t]) => '<option value="' + esc(v) + '"' + (v === value ? " selected" : "") + ">" + esc(t) + "</option>").join("") +
    "</select></label>";
  const panel = (title, body, opts) => {
    const o = typeof opts === "string" ? { cls: opts } : opts || {};
    return '<section class="panel ' + (o.cls || "") + '"' + (o.id ? ' id="' + esc(o.id) + '"' : "") + ">" +
      (title ? '<div class="phead"><h2>' + esc(title) + "</h2>" + (o.sub ? '<p class="psub">' + o.sub + "</p>" : "") + "</div>" : "") + body + "</section>";
  };
  const fold = (summary, body, opts) => '<details class="fold' + (opts && opts.cls ? " " + opts.cls : "") + '"' + (opts && opts.open ? " open" : "") +
    "><summary>" + summary + '</summary><div class="fold-body">' + body + "</div></details>";
  const sourceNote = (text) => '<p class="small muted src-note">출처: ' + esc(text) + "</p>";
  const tile = (label, value, note, cls) => '<div class="tile ' + (cls || "") + '"><div class="label">' + esc(label) + '</div><div class="value">' + esc(value) +
    '</div><div class="note">' + esc(note || "") + "</div></div>";
  const copyBtn = (v) => '<button class="copy" type="button" data-copy="' + esc(v) + '" aria-label="전체 값 복사">복사</button>';
  const hashShort = (h, n) => (h ? '<span class="hash"><code title="' + esc(h) + '">' + esc(String(h).slice(0, n || 12)) + "…</code>" + copyBtn(h) + "</span>" : '<span class="muted">N/A</span>');
  const hashFull = (h) => (h ? '<span class="hash full"><code>' + esc(h) + "</code>" + copyBtn(h) + "</span>" : '<span class="muted">N/A</span>');
  const condChip = (c) => '<span class="cchip c-' + esc(c) + '">' + esc(c) + "</span>";
  const condName = (c) => condChip(c) + " " + esc(COND_NAME[c] || c);
  const ICON = {
    chart: '<path d="M3 3v18h18"/><path d="M8 16v-5M13 16V7M18 16V4"/>',
    code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13 5l-2 14"/>',
    graph: '<rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5 9-5z"/><path d="m3 13 9 5 9-5"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
    shield: '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3z"/><path d="m9 12 2 2 4-4"/>',
    loop: '<path d="M4 12a8 8 0 0 1 14-5.3L20 9"/><path d="M20 4v5h-5"/><path d="M20 12a8 8 0 0 1-14 5.3L4 15"/><path d="M4 20v-5h5"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    doc: '<path d="M6 2h9l5 5v15H6z"/><path d="M14 2v6h6M9 13h8M9 17h6"/>',
    gauge: '<path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 18 4-6"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    arrow: '<path d="m9 18 6-6-6-6"/>',
    out: '<path d="M6 18 18 6M8 6h10v10"/>',
    check: '<path d="m5 12 5 5 9-10"/>',
    minus: '<path d="M6 12h12"/>',
    alert: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17v.5"/>',
  };
  const icon = (n, cls) => '<svg class="ico ' + (cls || "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (ICON[n] || "") + "</svg>";
  // Approximate rendered width (px) of a label, used only to shorten text that would overflow a node.
  const textW = (s, px) => {
    let w = 0;
    for (const ch of String(s)) {
      const c = ch.codePointAt(0);
      w += c > 0x2e80 ? 1.0 : /[A-Z0-9_]/.test(ch) ? 0.64 : /[a-z]/.test(ch) ? 0.56 : /[ .,:;'|!()\[\]·-]/.test(ch) ? 0.34 : 0.6;
    }
    return w * px;
  };
  const fit = (s, maxPx, px) => {
    s = String(s);
    if (textW(s, px) <= maxPx) return s;
    let out = "";
    for (const ch of s) {
      if (textW(out + ch + "…", px) > maxPx) break;
      out += ch;
    }
    return out + "…";
  };

  // ------------------------------------------------------------------ data model
  let D = null;  // {kind, catalog, experiments, manifests, synthetic, releases, figures, repoGraphs, bundleSha}
  let PLAYING = false;  // set by the page while replay auto-play runs
  let LAST_GRAPH = null;  // model of the graph currently drawn, read by the page for node details

  function normalize(raw, figures) {
    if (!raw) return null;
    if (raw.catalog) {
      return { kind: "site", catalog: raw.catalog, experiments: raw.experiments || {}, manifests: raw.manifests || {},
        synthetic: raw.synthetic || [], releases: raw.releases || {}, figures: figures || {},
        repoGraphs: raw.repository_graphs || null, bundleSha: null };
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
    const ids = Object.keys(experiments).sort().reverse();
    const primary = ids.find((id) => isValid(experiments[id].status)) || ids[0];
    const rel = { release_id: "submission-snapshot", label: "제출 스냅샷 (고정)", kind: "submission", primary: primary,
      experiments: Object.fromEntries(ids.map((id) => [id, {}])), verification: raw.verification || null, created_at: null };
    const syn = raw.synthetic && raw.synthetic.runs ? [{ suite: "s3-verification", label: raw.synthetic.label, report_ref: raw.synthetic.report_ref, runs: raw.synthetic.runs }] : [];
    return { kind: "snapshot", experiments: experiments, manifests: {}, synthetic: syn, figures: { _global: {} }, repoGraphs: null, bundleSha: null,
      releases: { "submission-snapshot": rel },
      catalog: { default_release: "submission-snapshot", data_updated_at: null, limitations: [], releases: [rel],
        experiments: ids.map((id) => ({ key: id, status: experiments[id].status, status_label: experiments[id].status_label, label: experiments[id].label })) } };
  }

  const C = () => (D && D.catalog) || {};
  const exp = (id) => ((D && D.experiments) || {})[id] || null;
  const releaseList = () => (C().releases || []);
  const release = (rid) => (rid === LATEST ? null : ((D && D.releases) || {})[rid] || releaseList().find((r) => r.release_id === rid) || null);
  const releaseOptions = () => releaseList().map((r) => [r.release_id, (r.label || r.release_id) + (r.kind === "submission" ? " · 고정본" : r.kind ? " · " + r.kind : "")])
    .concat(D && D.kind === "site" ? [[LATEST, "최신 데이터 (고정본 아님 · 진행 중 포함)"]] : []);
  function experimentsIn(rid) {
    if (rid === LATEST) return Object.keys((D && D.experiments) || {}).sort().reverse();
    const r = release(rid);
    if (!r) return [];
    const ids = Object.keys(r.experiments || {});
    return ids.sort((a, b) => (a === r.primary ? -1 : b === r.primary ? 1 : a < b ? 1 : -1));
  }
  function syntheticIn(rid) {
    const all = (D && D.synthetic) || [];
    const r = rid === LATEST ? null : release(rid);
    if (!r || !r.synthetic) return all;
    return all.filter((s) => Object.prototype.hasOwnProperty.call(r.synthetic, s.suite));
  }
  function pinOk(rid, id) {
    const r = release(rid);
    if (!r || !r.experiments || !r.experiments[id] || !r.experiments[id].data_sha256) return null;
    const entry = (C().experiments || []).find((e) => e.key === id);
    return !!entry && entry.data_sha256 === r.experiments[id].data_sha256;
  }
  const label = (id) => { const e = exp(id); return e ? (e.label || id) : id; };
  function primaryExp(state) {
    const r = release(state.release);
    const ids = experimentsIn(state.release);
    if (r && ids.includes(r.primary)) return r.primary;
    return ids.find((id) => exp(id) && isValid(exp(id).status)) || state.exp || ids[0] || "";
  }
  const slotRun = (e, task, cond) => {
    const s = ((e && e.slots) || []).find((x) => x.task_id === task && x.condition === cond);
    return s && s.selected_run_id ? (e.runs || {})[s.selected_run_id] || null : null;
  };
  const slotOf = (e, task, cond) => ((e && e.slots) || []).find((x) => x.task_id === task && x.condition === cond) || null;
  const turnOf = (t) => (t === null || t === undefined ? "기준(baseline)" : String(t).replace(/^turn-0*/, "턴 "));
  const policyText = (p) => (p ? "초기 " + p.initial_context + " · 진단 " + p.diagnosis + " · 전략 " + p.strategy + " · 복구 " +
    p.recovery_context + " · 외부 개입 " + (p.external_intervention ? "있음" : "없음") : "N/A");
  const compText = (c) => (c ? "거리 " + c.distance + " · 관측 " + c.observed + " · 수정 " + c.modified + " · 미탐색 " + c.unexplored : "N/A");
  const conditionPolicy = (e, c) => {
    const r = Object.values((e && e.runs) || {}).find((x) => x.condition === c && x.policy);
    return r ? { policy: r.policy, recorded: true } : { policy: POLICY_SPEC[c], recorded: false };
  };
  const verdictOf = (run) => (run && run.verdict ? run.verdict.status : null);

  function releaseStats(state) {
    const ids = experimentsIn(state.release);
    const st = { ids, valid: [], invalid: [], open: [], planned: 0, ran: 0, judged: 0, ok: 0, recov: 0, recovPass: 0, conds: new Set(),
      runs: { valid: 0, invalid: 0, open: 0, synthetic: 0 }, tests: null, testsOn: null, synth: syntheticIn(state.release) };
    ids.forEach((id) => {
      const e = exp(id);
      if (!e) return;
      const n = Object.keys(e.runs || {}).length;
      if (isValid(e.status)) {
        const s = e.slots || [];
        st.valid.push(id);
        st.runs.valid += n;
        st.planned += s.length;
        st.ran += s.filter((x) => x.selected_run_id).length;
        st.judged += s.filter((x) => x.evaluation_status).length;
        st.ok += s.filter((x) => x.y === 1).length;
        st.recov += s.reduce((a, x) => a + (x.recoveries_delivered || 0), 0);
        st.recovPass += (((e.derived || {}).recoveries) || []).filter((r) => r.verdict === "PASS").length;
        Object.keys(e.conditions || {}).forEach((c) => st.conds.add(c));
        ((e.meta || {}).conditions || []).forEach((c) => st.conds.add(c));
      } else if (isInvalid(e.status)) { st.invalid.push(id); st.runs.invalid += n; } else { st.open.push(id); st.runs.open += n; }
    });
    st.synth.forEach((s) => { st.runs.synthetic += Object.keys(s.runs || {}).length; });
    st.total = st.runs.valid + st.runs.invalid + st.runs.open + st.runs.synthetic;
    const r = release(state.release);
    const t = r && r.verification ? (r.verification.steps || []).find((x) => x.id === "tests") : null;
    st.tests = t && t.counts ? t.counts : null;
    st.testsOn = r && r.verification ? r.verification.recorded_on : null;
    return st;
  }

  // ------------------------------------------------------------------ graph and replay catalogues
  function graphEntries(state) {
    const out = [];
    for (const id of experimentsIn(state.release).filter((x) => x === state.exp)) {
      const e = exp(id);
      if (!e) continue;
      const runs = Object.values(e.runs || {}).sort((a, b) => (a.task_id + a.condition < b.task_id + b.condition ? -1 : 1));
      for (const run of runs) {
        (run.interventions || []).forEach((iv) => {
          if (iv.graph_detail) out.push({ key: id + "|" + run.run_id + "|" + iv.intervention_id, group: label(id) + " · " + (e.status_label || e.status) + " · 라이브", run, iv, exp: e, live: true });
        });
      }
    }
    for (const suite of syntheticIn(state.release)) {
      for (const run of Object.values(suite.runs || {})) {
        (run.interventions || []).forEach((iv) => {
          if (iv.graph_detail) out.push({ key: "synthetic|" + run.run_id + "|" + iv.intervention_id, group: "S3 합성 검증 · 모델 호출 0 · 라이브 아님", run, iv, exp: null, live: false });
        });
      }
    }
    return out;
  }

  function replayCases(state) {
    const out = [];
    const e = exp(state.exp);
    if (e) {
      const groups = { ivOk: [], ivFail: [], noIvFail: [], noIvOk: [] };
      for (const s of e.slots || []) {
        const run = (e.runs || {})[s.selected_run_id];
        if (!run) continue;
        const iv = (run.interventions || []).length;
        const v = s.evaluation_status || verdictOf(run) || "N/A";
        const k = iv ? (s.y === 1 ? "ivOk" : "ivFail") : s.y === 0 ? "noIvFail" : "noIvOk";
        groups[k].push({ key: state.exp + "|" + run.run_id, run, exp: e, live: true,
          title: s.task_id + " · " + COND_LABEL[s.condition] + (iv ? " · 개입 " + iv + "회" : "") + " → " + v });
      }
      const name = label(state.exp);
      [["ivOk", " — 개입 후 성공 (독립 PASS)"], ["ivFail", " — 개입 후 실패"], ["noIvFail", " — 개입 없이 실패"], ["noIvOk", " — 개입 없이 성공 (참고)"]]
        .forEach(([k, g]) => groups[k].forEach((x) => out.push(Object.assign(x, { group: name + g }))));
    }
    for (const suite of syntheticIn(state.release)) {
      for (const run of Object.values(suite.runs || {})) {
        if ((run.interventions || []).length) {
          out.push({ key: "synthetic|" + run.run_id, group: "S3 합성 검증 — 모델 호출 0, 라이브 아님", run, exp: null, live: false,
            title: (run.scenario || run.task_id) + " · " + run.condition + " → " + ((run.interventions[0] || {}).strategy || "") });
        }
      }
    }
    return out;
  }

  const repoTasks = () => (D && D.repoGraphs ? Object.keys(D.repoGraphs.tasks || {}).sort() : []);
  const repoGraph = (task) => (D && D.repoGraphs ? (D.repoGraphs.tasks || {})[task] || null : null);

  // Representative cases for the quick tour: chosen by fixed rules from the selected data, never by hand.
  function picks(state) {
    const pid = primaryExp(state);
    const e = exp(pid);
    const out = { exp: pid };
    const order = (a, b) => (a.task_id < b.task_id ? -1 : a.task_id > b.task_id ? 1 : a.condition < b.condition ? -1 : a.condition > b.condition ? 1 : 0);
    const best = (list, score) => list.slice().sort((a, b) => score(b) - score(a) || order(a, b))[0] || null;
    if (e) {
      const runs = (e.slots || []).map((s) => (e.runs || {})[s.selected_run_id]).filter(Boolean);
      const withIv = runs.filter((r) => (r.interventions || []).length);
      const failTasks = new Set(withIv.filter((r) => verdictOf(r) === "FAIL").map((r) => r.task_id));
      out.ok = best(withIv.filter((r) => verdictOf(r) === "PASS"), (r) => (failTasks.has(r.task_id) ? 2 : 0) + ((r.policy || {}).recovery_context === "DCC" ? 1 : 0));
      out.fail = best(withIv.filter((r) => verdictOf(r) === "FAIL"), (r) => ((r.interventions || []).some((iv) => iv.graph) ? 2 : 0) + (out.ok && r.task_id === out.ok.task_id ? 1 : 0));
      const dccOf = (r) => (r.context || []).filter((p) => p.policy === "DCC" && (p.decisions || []).some((d) => d.selected) && (p.decisions || []).some((d) => !d.selected));
      out.dcc = best(runs.filter((r) => dccOf(r).length), (r) => (out.ok && r.run_id === out.ok.run_id ? 1000 : 0) +
        (dccOf(r).some((p) => p.stage === "RECOVERY") ? 100 : 0) + Math.max(...dccOf(r).map((p) => p.decisions.length)));
    }
    const entries = graphEntries(Object.assign({}, state, { exp: pid }));
    out.graphLive = entries.find((x) => x.live && x.exp && x.exp.experiment_id === pid) || entries.find((x) => x.live) || null;
    const liveTrig = new Set(out.graphLive ? out.graphLive.iv.trigger_ids || [] : []);
    const syn = entries.filter((x) => !x.live);
    const synScore = (x) => {
      const tr = (x.iv.graph_detail || {}).trace || {};
      return ((tr.candidates || []).length ? 100 : 0) + ((x.iv.trigger_ids || []).some((t) => liveTrig.has(t)) ? 10 : 0) +
        ((x.iv.graph_detail || {}).edges || []).filter((ed) => ed.cross).length;
    };
    out.graphSyn = syn.slice().sort((a, b) => synScore(b) - synScore(a) || (a.key < b.key ? -1 : 1))[0] || null;
    const tasks = repoTasks().map((t) => repoGraph(t)).filter(Boolean);
    out.repo = tasks.slice().sort((a, b) => b.edges.length - a.edges.length || b.nodes.length - a.nodes.length || (a.task_id < b.task_id ? -1 : 1))[0] || null;
    return out;
  }

  // ------------------------------------------------------------------ routing helpers
  const TAB_KEYS = { trace: ["task", "cond"], context: ["task", "cond"], graph: ["gmode", "rtask", "graph", "gnode"], replay: ["case", "step"] };
  function toHash(state) {
    const keys = ["release", "exp"].concat(TAB_KEYS[state.tab] || []);
    const q = keys.filter((k) => state[k] !== undefined && state[k] !== null && state[k] !== "" &&
      !(state.tab === "graph" && ((k === "graph" && state.gmode === "repo") || (k === "rtask" && state.gmode === "diag"))))
      .map((k) => encodeURIComponent(k) + "=" + encodeURIComponent(state[k])).join("&");
    return "#" + state.tab + (q ? "?" + q : "");
  }
  // A link to another screen that keeps only the data basis (release, experiment) of the current state.
  const nav = (state, next) => toHash(Object.assign({ release: state.release, exp: state.exp }, next));
  const linkTo = (state, next, text, cls) => '<a class="' + (cls || "") + '" href="' + esc(nav(state, next)) + '">' + text + "</a>";

  // ------------------------------------------------------------------ shared header
  function basisChip(state) {
    if (state.release === LATEST) return '<span class="chip warn" title="release로 고정되지 않은 현재 저장소 데이터">최신 데이터 · 고정본 아님</span>';
    const r = release(state.release);
    if (!r) return "";
    return '<span class="chip good" title="이 release는 실험 데이터 해시를 고정한다. 이후 실험이 추가돼도 바뀌지 않는다.">' +
      icon("check", "sm") + (r.kind === "submission" ? "제출 고정본" : "고정본") + "</span>";
  }

  function filters(state, extra) {
    const exps = experimentsIn(state.release);
    const e = exp(state.exp);
    return '<div class="filters" role="group" aria-label="데이터 기준 선택">' + select("release", state.release, releaseOptions(), "데이터 기준") +
      (exps.length ? select("exp", state.exp, exps.filter((id) => id === state.exp || !isInvalid((exp(id) || {}).status)).map((id) => [id, label(id) + " · " + ((exp(id) || {}).status_label || "")]),
        "실험", e ? e.status_reason : "") : "") +
      (extra || "") + "</div>";
  }

  // Experiment status next to the data basis; the explanation is in the tooltip. Invalid and unfinished
  // experiments still get a full-width banner (statusBanner), valid ones do not.

  function notices(state) {
    const q = state._req || {};
    const out = [];
    if (q.tab && !VIEWS[q.tab]) out.push("요청한 화면 '" + q.tab + "'은 없어 개요를 표시한다.");
    if (q.release && q.release !== state.release) out.push("요청한 데이터 기준 '" + q.release + "'을 찾지 못해 기본 데이터 기준을 표시한다.");
    if (q.exp && q.exp !== state.exp) out.push("요청한 실험 '" + q.exp + "'이 이 데이터 기준에 없어 주 실험을 표시한다.");
    if (q.task && q.task !== state.task && (state.tab === "trace" || state.tab === "context")) out.push("요청한 과제 '" + q.task + "'이 없어 " + state.task + "를 표시한다.");
    if (q.cond && q.cond !== state.cond && (state.tab === "trace" || state.tab === "context")) out.push("요청한 조건 '" + q.cond + "'이 없어 " + state.cond + "를 표시한다.");
    return out.length ? '<div class="callout warn" role="status"><strong>주소 확인.</strong> ' + out.map(esc).join(" ") + "</div>" : "";
  }

  function statusBanner(e) {
    if (!e) return "";
    const s = e.status;
    if (isValid(s)) return "";
    if (isInvalid(s)) return '<div class="callout crit" role="alert"><strong>무효 실험 (' + esc(s) + ").</strong> " + esc(e.status_reason || "") +
      " 연구 질문의 근거로 쓰지 않는다" + (e.report_ref ? " (" + esc(e.report_ref) + ")" : "") + ".</div>";
    const p = e.progress || {};
    const tail = s === "IN_PROGRESS" || s === "ANALYSIS_PENDING" ? " 판정 " + esc(p.evaluated) + "/" + esc(p.planned) + " 슬롯." : "";
    return '<div class="callout warn"><strong>' + esc(STATUS_LABEL[s] || s) + ".</strong>" + tail + " " + esc(e.status_reason || "") + "</div>";
  }

  function header(state, intro) {
    let html = filters(state, "") + notices(state);
    if (pinOk(state.release, state.exp) === false) {
      html += '<div class="callout crit"><strong>release 고정 불일치.</strong> 이 release가 고정한 데이터와 현재 데이터가 다르다. 표시 값을 신뢰하지 말고 데이터 저장소를 확인한다.</div>';
    }
    if (intro) html += '<div class="viewhead"><h1>' + esc(intro[0]) + "</h1><p>" + intro[1] + "</p></div>";
    return html + statusBanner(exp(state.exp));
  }

  // ------------------------------------------------------------------ overview
  const ARCH_NODES = [
    { id: "in", owner: "aah", on: "ABCDE", t: "개발 과제·저장소 입력", v: { all: "공개 과제 원본 → 격리된 작업 사본" }, go: { tab: "graph", gmode: "repo" } },
    { id: "cfg", owner: "aah", on: "ABCDE", t: "실행 조건 설정", v: { all: "Condition Resolver · A–E 정책", A: "A — 보조 정보 없음 (NONE)", B: "B — 초기 DEFAULT", C: "C — 초기 DCC", D: "D — 초기 DEFAULT", E: "E — 초기 DCC" }, go: { tab: "benchmark" } },
    { id: "codex", owner: "codex", on: "ABCDE", t: "Codex 실행", v: { all: "코드 탐색·수정 · codex exec --json" }, go: { tab: "trace" } },
    { id: "obs", owner: "aah", on: "ABCDE", t: "실행 관측·공개 검사", v: { all: "증거 원장 기록 · 고정 검사 직접 실행" }, go: { tab: "trace" } },
    { id: "det", owner: "aah", on: "ABCDE", t: "실패 감지", v: { all: "T1 반복 · T2 정체 · T3 회귀 · T4 · T5", A: "A — 감지돼도 외부 개입 없음" }, go: { tab: "replay" } },
    { id: "dia", owner: "aah", on: "BCDE", t: "원인 진단", v: { all: "B·C 일반(FLAT) / D·E 그래프(GRAPH)", B: "일반 진단 (FLAT) · 그래프 호출 0", C: "일반 진단 (FLAT) · 그래프 호출 0", D: "그래프 진단 · 코드↔실행 증거 연결", E: "그래프 진단 · 코드↔실행 증거 연결" }, go: { tab: "graph", gmode: "diag" } },
    { id: "str", owner: "aah", on: "BCDE", t: "복구 전략 선택", v: { all: "일반 재시도 / 유형별 전략", B: "GENERIC_RETRY (설계값)", C: "GENERIC_RETRY (설계값)", D: "유형별 · 근거 부족 시 일반 재시도", E: "유형별 · 근거 부족 시 일반 재시도" }, go: { tab: "replay" } },
    { id: "ctx", owner: "aah", on: "BCDE", t: "보조 컨텍스트 구성", v: { all: "B·D DEFAULT / C·E DCC", B: "DEFAULT — 공통 최소 근거", D: "DEFAULT — 공통 최소 근거", C: "DCC — 점수·중복 제거·예산 선택", E: "DCC — 점수·중복 제거·예산 선택" }, go: { tab: "context" } },
    { id: "fin", owner: "aah", on: "ABCDE", t: "최종 패치 확정", v: { all: "완료 선언·상한 도달 시 고정" }, go: { tab: "trace" } },
    { id: "ev", owner: "eval", on: "ABCDE", t: "독립 평가", v: { all: "비공개 기능·회귀·지침 검사" }, go: { tab: "benchmark" } },
    { id: "an", owner: "eval", on: "ABCDE", t: "결과 분석", v: { all: "동결 분석 · 과제 군집 bootstrap" }, go: { tab: "benchmark" } },
  ];
  const ARCH = {
    wide: { W: 1000, H: 776, title: 15, sub: 12,
      pos: { in: [360, 20, 280, 56], cfg: [360, 100, 280, 56], codex: [360, 180, 280, 60], obs: [360, 264, 280, 56], det: [360, 344, 280, 56],
        dia: [40, 470, 270, 56], str: [40, 550, 270, 56], ctx: [40, 630, 270, 56], fin: [690, 470, 270, 56], ev: [690, 566, 270, 56], an: [690, 646, 270, 56] },
      edges: [["M500,76 V97", "ABCDE"], ["M500,156 V177", "ABCDE"], ["M500,240 V261", "ABCDE"], ["M500,320 V341", "ABCDE"],
        ["M500,400 V430 H175 V467", "BCDE"], ["M500,430 H825 V467", "ABCDE"], ["M175,526 V547", "BCDE"], ["M175,606 V627", "BCDE"],
        ["M40,658 H18 V210 H357", "BCDE", "loop"], ["M825,526 V563", "ABCDE"], ["M825,622 V643", "ABCDE"]],
      labels: [[184, 456, "개입 필요 · B–E", "BCDE", "rec"], [834, 456, "종료 조건 충족", "ABCDE", ""],
        [656, 368, "A: 감지돼도 외부 개입 없음", "A", "muted"], [656, 388, "Codex 네이티브 동작만 관측", "A", "muted"]],
      loopBox: [50, 232, 262, 60], bound: [676, 548, 298, 170], boundText: [690, 744] },
    compact: { W: 360, H: 900, title: 14, sub: 11.5,
      pos: { in: [36, 12, 290, 52], cfg: [36, 84, 290, 52], codex: [36, 156, 290, 56], obs: [36, 232, 290, 52], det: [36, 304, 290, 52],
        dia: [36, 396, 290, 52], str: [36, 468, 290, 52], ctx: [36, 540, 290, 52], fin: [36, 624, 290, 52], ev: [36, 708, 290, 52], an: [36, 780, 290, 52] },
      edges: [["M181,64 V81", "ABCDE"], ["M181,136 V153", "ABCDE"], ["M181,212 V229", "ABCDE"], ["M181,284 V301", "ABCDE"],
        ["M181,356 V393", "BCDE"], ["M181,448 V465", "BCDE"], ["M181,520 V537", "BCDE"], ["M36,566 H14 V184 H33", "BCDE", "loop"],
        ["M326,330 H346 V650 H329", "ABCDE"], ["M181,676 V705", "ABCDE"], ["M181,760 V777", "ABCDE"]],
      labels: [[190, 380, "개입 필요 · B–E", "BCDE", "rec"], [338, 614, "종료 시", "ABCDE", "end"], [46, 614, "↺ 복구 루프 · 최대 2회", "BCDE", "loopt"]],
      loopBox: null, bound: [24, 692, 314, 154], boundText: [36, 870] },
  };

  function archSvg(state, kind) {
    const L = ARCH[kind];
    const id = "arw-" + kind;
    const on = (s) => s.split("").map((c) => "on-" + c).join(" ");
    let svg = '<svg class="arch arch-' + kind + '" viewBox="0 0 ' + L.W + " " + L.H + '" role="img" aria-labelledby="' + id + '-t ' + id + '-d">' +
      '<title id="' + id + '-t">AAH 시스템 아키텍처</title><desc id="' + id + '-d">개발 과제 입력, 실행 조건 설정, Codex 실행, 실행 관측과 공개 검사, 실패 감지. 개입이 필요하면(B–E) 원인 진단, 복구 전략 선택, 보조 컨텍스트 구성 후 Codex 후속 실행으로 돌아간다(최대 2회). 종료되면 최종 패치를 확정하고 독립 평가와 결과 분석으로 이어진다. 독립 평가 결과는 에이전트에 전달되지 않는다.</desc>' +
      '<defs><marker id="' + id + '" viewBox="0 0 8 8" refX="7" refY="4" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="mk"/></marker>' +
      '<marker id="' + id + 'l" viewBox="0 0 8 8" refX="7" refY="4" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="mk loop"/></marker></defs>';
    const b = L.bound;
    svg += '<rect class="bound" x="' + b[0] + '" y="' + b[1] + '" width="' + b[2] + '" height="' + b[3] + '" rx="12"/>';
    if (L.loopBox) {
      const lb = L.loopBox;
      svg += '<g class="an ' + on("BCDE") + '"><rect class="loopbox" x="' + lb[0] + '" y="' + lb[1] + '" width="' + lb[2] + '" height="' + lb[3] + '" rx="8"/>' +
        '<text class="lt b" x="' + (lb[0] + 12) + '" y="' + (lb[1] + 24) + '">↺ 복구 피드백 루프 (B–E)</text><text class="lt" x="' + (lb[0] + 12) + '" y="' + (lb[1] + 45) + '">새 관측이 있을 때만 · 최대 2회</text></g>';
    }
    L.edges.forEach(([d, conds, cls]) => {
      svg += '<path class="an wire ' + (cls || "") + " " + on(conds) + '" d="' + d + '" marker-end="url(#' + id + (cls === "loop" ? "l" : "") + ')"/>';
    });
    L.labels.forEach(([x, y, t, conds, cls]) => {
      svg += '<text class="an lbl ' + (cls || "") + " " + on(conds) + '" x="' + x + '" y="' + y + '"' + (cls === "end" ? ' text-anchor="end"' : "") + ">" + esc(t) + "</text>";
    });
    for (const n of ARCH_NODES) {
      const [x, y, w, h] = L.pos[n.id];
      const ty = y + (h >= 56 ? 25 : 23), sy = y + (h >= 56 ? 45 : 41);
      const variants = {};
      ["all"].concat(COND).forEach((c) => { const t = n.v[c] || n.v.all; (variants[t] = variants[t] || []).push(c); });
      const owner = { codex: "Codex 영역 — 자율 문제 해결", aah: "AAH 영역 — 실행 관측·제어", eval: "독립 평가 영역 — 에이전트 비공개" }[n.owner];
      svg += '<a href="' + esc(nav(state, n.go)) + '" class="an anode o-' + n.owner + " " + on(n.on) + '"><title>' + esc(n.t + " · " + owner + " · 클릭하면 관련 화면으로 이동") + "</title>" +
        '<rect class="nb" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="10"/><rect class="nbar" x="' + x + '" y="' + y + '" width="6" height="' + h + '" rx="3"/>' +
        '<text class="nt" x="' + (x + w / 2 + 3) + '" y="' + ty + '" text-anchor="middle" style="font-size:' + L.title + 'px">' + esc(n.t) + "</text>" +
        Object.entries(variants).map(([t, cs]) => '<text class="ns v ' + cs.map((c) => "v-" + c).join(" ") + '" x="' + (x + w / 2 + 3) + '" y="' + sy + '" text-anchor="middle" style="font-size:' + L.sub + 'px">' + esc(t) + "</text>").join("") + "</a>";
    }
    const [bx, by] = L.boundText;
    svg += '<text class="lbl b evalt" x="' + bx + '" y="' + by + '">평가 경계</text><text class="lbl evalt" x="' + bx + '" y="' + (by + 19) + '">판정 결과는 Codex·복구 루프에 전달되지 않음</text>';
    return svg + "</svg>";
  }

  function archBox(state) {
    const caps = {
      all: "조건을 고르면 그 조건에서 실제로 켜지는 단계만 강조된다. 상자를 누르면 관련 화면으로 이동한다.",
      A: "A Vanilla — 외부 개입과 보조 정보가 없다. 하네스는 실행을 관측하고 공개 검사만 기록한다.",
      B: "B Flat Retry — 실패가 감지되면 일반 진단(FLAT) → 일반 재시도 → DEFAULT 컨텍스트로 후속 턴을 보낸다.",
      C: "C Context-only — B와 같은 일반 재시도에, 보조 정보만 DCC가 점수·중복 제거·예산 기준으로 고른다.",
      D: "D Graph-only — 그래프 진단으로 유형별 전략을 고르고, 보조 정보는 DEFAULT를 쓴다. 근거가 부족하면 일반 재시도로 대체한다.",
      E: "E Adaptive Harness — 그래프 진단·유형별 전략과 DCC를 함께 쓴다. 최초 메시지는 C와 바이트 단위로 같다 (CT03).",
    };
    const btns = ["all"].concat(COND).map((c) => '<button type="button" class="seg' + (c === "all" ? " on" : "") + '" data-arch="' + c + '" aria-pressed="' + (c === "all") + '">' +
      (c === "all" ? "전체 흐름" : condChip(c) + " " + esc(COND_NAME[c])) + "</button>").join("");
    return '<div class="archbox" data-cond="all"><div class="segs" role="group" aria-label="조건별 활성 단계 보기">' + btns + "</div>" +
      '<div class="archfig">' + archSvg(state, "wide") + archSvg(state, "compact") + "</div>" +
      '<div class="legend arch-legend"><span><span class="sw o-codex"></span>Codex — 자율 문제 해결</span><span><span class="sw o-aah"></span>AAH — 실행 관측·제어</span>' +
      '<span><span class="sw o-eval"></span>독립 평가 — 에이전트 비공개</span><span><span class="sw line loop"></span>복구 루프</span><span><span class="sw line bound"></span>평가 경계</span></div>' +
      '<div class="archcap" aria-live="polite">' + Object.entries(caps).map(([c, t]) => '<p class="cap cap-' + c + '">' + esc(t) + "</p>").join("") + "</div></div>";
  }

  // Simple flow of the minimal overview (geometry from the approved overview mockup).
  const FLOW = {
    nodes: [["codex", "Codex 실행", 103, 5, 134, 32], ["obs", "실행 관측 및 실패 감지", 103, 63, 134, 34], ["graph", "그래프 기반 진단", 5, 135, 151, 34],
      ["ctx", "동적 컨텍스트 구성", 185, 135, 150, 34], ["eval", "독립 평가", 103, 212, 134, 34]],
    wires: ["M170 37 V61", "M103 97 H81 V133", "M156 152 H184", "M260 135 V21 H238", "M170 97 V210"],
    labels: [["실패", 56, 119], ["종료", 179, 198], ["복구", 271, 81]],
  };
  function flowSvg() {
    return '<svg class="flow" viewBox="0 0 340 255" role="img" aria-label="Codex 실행 후 실행 관측 및 실패 감지. 실패하면 그래프 기반 진단과 동적 컨텍스트 구성을 거쳐 Codex로 복구 실행, 종료되면 독립 평가.">' +
      '<defs><marker id="flow-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6" class="mk"/></marker></defs>' +
      '<g class="fw">' + FLOW.wires.map((d) => '<path d="' + d + '"/>').join("") + "</g>" +
      FLOW.nodes.map(([k, t, x, y, w, h]) => '<g class="anode f-' + k + '"><rect class="nb" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="6"/><text x="' + (x + w / 2) + '" y="' + (y + h / 2 + 4) + '" text-anchor="middle">' + esc(t) + "</text></g>").join("") +
      FLOW.labels.map(([t, x, y]) => '<text class="fl" x="' + x + '" y="' + y + '">' + esc(t) + "</text>").join("") + "</svg>";
  }

  function aboutBody(state) {
    const e = exp(primaryExp(state));
    const m = e && e.meta && e.meta.model ? e.meta.model : null;
    const why = [["빠른 생성, 느린 이해", "AI는 결과물을 빠르게 만들지만, 그 결과와 의사결정 근거를 이해하기는 어렵다."],
      ["위임이 늘수록 통제가 필요", "맡기는 업무와 결정이 넓어질수록 실행을 통제하고 검증할 수 있어야 한다."],
      ["검증도 효율화해야 한다", "코드 생산성뿐 아니라 실패를 감지하고 확인하는 비용도 줄여야 한다."],
      ["자율성은 두고, 바깥에서 관측", "Codex의 자율적 문제 해결은 그대로 두고 외부 실행 환경에서 관측·검증한다."]];
    return '<dl class="roles"><dt><span class="rdot o-codex"></span>Codex — 문제를 푼다</dt><dd>어떤 파일을 읽고 고칠지, 어떤 테스트를 돌릴지 스스로 정한다' +
      (m ? " (" + esc(m.model_id + " · " + m.adapter + " " + m.runtime_version) + ")" : "") + ".</dd>" +
      '<dt><span class="rdot o-aah"></span>AAH — 관측하고 통제한다</dt><dd>이벤트를 증거로 기록하고, 실패하면 입력(복구 지시·보조 정보)과 개입 시점만 바꾼다. 모델 가중치·내부 추론은 건드리지 않는다.</dd>' +
      '<dt><span class="rdot o-eval"></span>독립 평가 — 판정한다</dt><dd>고정된 최종 패치를 에이전트가 볼 수 없는 비공개 검사로 판정한다. 판정은 에이전트에 돌아가지 않는다.</dd></dl>' +
      '<h3 class="sub">왜 필요한가</h3><ul class="plain">' + why.map(([t, d]) => "<li><strong>" + esc(t) + "</strong> — " + esc(d) + "</li>").join("") + "</ul>" +
      '<h3 class="sub">왜 Codex인가</h3><p>Codex CLI는 비대화식 실행(<code>codex exec --json</code>)에서 명령·파일 변경·사용량을 구조화된 이벤트로 내보내고, 같은 세션에 후속 턴을 보낼 수 있다(<code>exec resume</code>). 그래서 모델 내부를 건드리지 않고 실행을 관측하고 복구 지시를 전달할 수 있다. 이 프로젝트에서 Codex는 실제 실행 엔진이자 실험 대상이다.</p>';
  }

  function techTable(state, st) {
    const e = exp(primaryExp(state));
    const dccList = e ? Object.values(e.runs || {}).flatMap((r) => (r.context || []).filter((p) => p.policy === "DCC")) : [];
    const dccDecisions = dccList.reduce((a, p) => a + (p.decisions || []).length, 0);
    const syn = st.synth.reduce((a, x) => a + Object.values(x.runs || {}).filter((r) => (r.interventions || []).some((iv) => iv.strategy && iv.strategy !== "GENERIC_RETRY")).length, 0);
    const rows = [
      ["Harness Engineering", "Codex 실행 환경과 외부 제어 — 격리 작업 사본, 이벤트 증거 원장, 상태 머신, 조건 resolver", "runtime/ · workspace/ · workflow/ · evidence/", "라이브 " + fmt(st.ran) + "회 실행", { tab: "trace" }],
      ["Context Engineering", "관련 정보의 동적 선별 — 관련성·유효성·범위·실패 점수, 중복 병합, 예산 안 선택", "context/", "DCC 패키지 " + fmt(dccList.length) + "개 · 근거 " + fmt(dccDecisions) + "건", { tab: "context" }],
      ["Graph Engineering", "코드 구조와 실행 증거의 연결 — 검증된 근거로만 연결, 제한 탐색으로 원인 후보(가설)", "graphs/ · diagnosis/graph.py", "과제 그래프 " + repoTasks().length + "개 · 합성 유형별 전략 " + syn + "건", { tab: "graph", gmode: "repo" }],
      ["Loop Engineering", "실패 감지 후 선택적 복구 — T1–T5 감지, 전략 선택, 최대 2회, 새 관측 필수", "diagnosis/detector.py · recovery/ · workflow/", "개입 " + fmt(st.recov) + "회 · 독립 PASS " + fmt(st.recovPass) + "건", { tab: "replay" }],
    ];
    return '<h3 class="sub">핵심 기술과 구현 모듈</h3>' + table(["기술", "역할", "모듈 (src/aah/)", "기록", ""], rows.map(([t, d, m, v, g]) =>
      ["<strong>" + esc(t) + "</strong>", esc(d), "<code>" + esc(m) + "</code>", esc(v), linkTo(state, g, "보기", "nowrap")]));
  }

  function resultDetail(state, st) {
    const pid = primaryExp(state);
    const e = exp(pid);
    if (!e || !e.conditions) return '<p class="muted">이 데이터 기준의 주 실험은 결과가 확정되지 않았다.</p>';
    const cm = e.conditions;
    const d = e.derived || {};
    const cps = COND.filter((c) => cm[c] && isNum(cm[c].cps_tokens));
    const lo = cps.slice().sort((a, b) => cm[a].cps_tokens - cm[b].cps_tokens)[0], hi = cps.slice().sort((a, b) => cm[b].cps_tokens - cm[a].cps_tokens)[0];
    const cts = e.contrasts || [];
    const graphCalls = Object.values(d.graph_use || {}).reduce((a, b) => a + b, 0);
    const v1 = st.invalid.map(exp).find((x) => x && x.derived && x.derived.commands);
    const par = d.parity || {};
    const meta = e.meta || {};
    const done = [
      ["실제 Codex 기반 통제 비교 실험", label(pid) + " " + fmt(st.ran) + "회 실행·독립 판정 " + fmt(st.judged) + "건, 결측 " + fmt(st.planned - st.judged), { tab: "benchmark" }],
      ["A–E 성공률·실행 효율 측정", COND.filter((c) => cm[c]).map((c) => c + " " + cm[c].successes + "/" + cm[c].valid).join(" · ") +
        (lo ? ", 성공당 토큰 " + lo + " " + fmt(cm[lo].cps_tokens) + " ~ " + hi + " " + fmt(cm[hi].cps_tokens) : ""), { tab: "benchmark" }],
      ["적응형 복구 루프 동작", "라이브 개입 " + fmt(st.recov) + "회 전달, 그중 " + fmt(st.recovPass) + "건 독립 PASS. 합성 검증에서 RELOCALIZE·REVIEW_REGRESSION 동작", { tab: "replay" }],
      ["정책 격리 재확인", "B/D·C/E 최초 메시지 바이트 동일 " + fmt(par.B_equals_D) + "/" + fmt(par.tasks) + " · " + fmt(par.C_equals_E) + "/" + fmt(par.tasks) + " 과제", { tab: "audit" }],
      ["실행 환경 결함 발견·수정", v1 ? label(v1.experiment_id) + " 셸 명령 " + fmt(v1.derived.commands.exit_nonzero) + "/" + fmt(v1.derived.commands.total) + " 실패 → 무효 판정·보존 → " + label(pid) + " 재실행" : "기록 없음", { tab: "audit" }],
    ];
    const open = [
      ["정책별 효과 크기는 결론 전", "대비 " + cts.length + "개 모두 95% 구간이 0을 포함하거나 구간이 없다 (" + Object.keys(e.tasks || {}).length + "과제 × " + (meta.repetitions || "N/A") + "회, A도 " + (cm.A ? cm.A.successes + "/" + cm.A.valid : "N/A") + " 해결)",
        "반복 수·과제 수를 늘려 사전 등록한 대비를 다시 측정", { tab: "benchmark" }],
      ["라이브 실행에서 그래프 근거 연결 미발생", "라이브 그래프 진단 " + fmt(graphCalls) + "회는 공개 검사가 suite 단위라 테스트 ID·frame 연결이 없어 일반 재시도로 처리됐다",
        "공개 검사 결과를 테스트 함수·frame 단위로 기록", { tab: "graph", gmode: "diag" }],
      ["공통 실패 체크포인트(P-R2)·ablation", "이번 실험 범위에서는 실행하지 않았다 (미측정)", "체크포인트 복구율과 AB1–AB4 측정", { tab: "audit" }],
    ];
    const bars = COND.filter((c) => cm[c]).map((c) => '<div class="mrow"><span>' + condChip(c) + '</span><span class="mtrack"><span class="mfill c-' + c + '" style="width:' +
      (cm[c].valid ? (cm[c].successes / cm[c].valid * 100).toFixed(1) : 0) + '%"></span></span><span class="mval">' + esc(cm[c].successes + "/" + cm[c].valid) + "</span></div>").join("");
    return '<div class="rd"><div><h3 class="sub">조건별 성공 (독립 판정)</h3>' + bars + "</div>" +
      '<div><h3 class="sub">구현·측정된 것</h3><ul class="plain">' + done.map(([t, x, g]) => "<li><strong>" + esc(t) + "</strong> — " + esc(x) + " " + linkTo(state, g, "근거", "more") + "</li>").join("") + "</ul></div>" +
      '<div><h3 class="sub">현재 한계와 다음 단계</h3><ul class="plain">' + open.map(([t, x, n, g]) => "<li><strong>" + esc(t) + "</strong> — " + esc(x) + '. <span class="muted">다음 단계: ' + esc(n) + "</span> " + linkTo(state, g, "근거", "more") + "</li>").join("") + "</ul></div></div>";
  }






  function runSummary(state) {
    const e = exp(state.exp);
    if (!e) return "";
    const s = e.slots || [];
    const ran = s.filter((x) => x.selected_run_id).length;
    const judged = s.filter((x) => x.evaluation_status).length;
    const ok = s.filter((x) => x.y === 1).length;
    const tiles = [tile("계획 슬롯", fmt(s.length), "과제 × 조건 × 반복"), tile("실제 실행", fmt(ran), "실제 Codex 실행"), tile("독립 판정", fmt(judged), "결측·미판정 " + fmt(s.length - judged) + "건")];
    if (e.conditions) {
      const rec = s.reduce((a, x) => a + (x.recoveries_delivered || 0), 0);
      const tok = s.reduce((a, x) => a + (x.model_tokens || 0), 0);
      const rt = s.reduce((a, x) => a + (x.runtime_ms || 0), 0);
      tiles.push(tile("성공 / 실패", fmt(ok) + " / " + fmt(judged - ok), "독립 판정 기준"), tile("복구 전달", fmt(rec), "B–E 개입 후 후속 턴"),
        tile("모델 토큰", fmt(tok), "관측값 (observe_only)"), tile("실행 시간 합계", (rt / 60000).toFixed(1) + "분", "슬롯 실행 시간 합"));
    } else {
      tiles.push(tile("결과", "미확정", e.status_label || ""));
    }
    return panel(label(state.exp) + " 실행 요약", '<div class="tiles">' + tiles.join("") + "</div>" + sourceNote((e.public_dir || "") + "/run_status.csv"));
  }

  function runSettings(state) {
    const e = exp(state.exp);
    if (!e || !e.meta || !e.meta.model) return "";
    const m = e.meta, b = m.budget || {};
    return kv([
      ["모델 / runtime", esc([m.model.model_id, "추론 " + m.model.reasoning_effort, m.model.adapter + " " + m.model.runtime_version, m.model.session_mode].join(" · "))],
      ["프로토콜 / 반복", esc((m.protocol || "N/A") + " / " + (m.repetitions || "N/A") + "회")],
      ["턴 상한", esc("총 " + fmt(b.max_total_turns) + " (최초 " + fmt(b.max_initial_turns) + " + 복구 " + fmt(b.max_recovery_turns) + "), 슬롯당 복구 " + fmt(b.recovery_turns_per_eligible_slot) + "회")],
      ["실행 한도", esc("실행당 " + secs(b.wall_ms_per_run) + " · 턴당 " + secs(b.native_turn_ms) + " · 정리 예비 " + secs(b.cleanup_reserve_ms))],
      ["보조 정보 상한", esc(fmt(b.supplement_cap) + " 추정 토큰 (B–E 공통)")],
      ["토큰 한도 방식", esc(b.token_limit_mode || "N/A") + ' <span class="muted">(관측만, 강제 아님)</span>'],
      ["코드 버전", hashFull(m.code_version)],
    ]);
  }

  function overview(state) {
    const st = releaseStats(state);
    const pk = picks(state);
    const pid = pk.exp;
    const ve = exp(st.valid[0]);
    const design = ve ? Object.keys(ve.tasks || {}).length + "과제 × " + st.conds.size + "조건 × " + ((ve.meta || {}).repetitions || "N/A") + "회" : "";
    const t = st.tests;
    const stat = (v, l, tip) => '<div class="stat" title="' + esc(tip) + '"><div class="value">' + esc(v) + '</div><div class="label">' + esc(l) + "</div></div>";
    const run = (r) => (r ? r.task_id + " · " + COND_LABEL[r.condition] : "");
    const links = [
      [nav(state, { tab: "benchmark", exp: pid }), "chart", "조건 비교", "A–E 정책별 성능 및 실행 효율", label(pid) + " 조건 비교"],
      [pk.repo ? nav(state, { tab: "graph", gmode: "repo", rtask: pk.repo.task_id }) : null, "graph", "그래프 탐색", "실제 코드 구조 및 실패 진단",
        pk.repo ? "열리는 사례: " + pk.repo.task_id + " Repository Graph (관계 수가 가장 많은 과제)" : ""],
      [pk.ok ? nav(state, { tab: "replay", exp: pid, case: pid + "|" + pk.ok.run_id, step: "0" }) : null, "play", "복구 Replay", "에이전트 실패부터 후속 실행까지",
        pk.ok ? "열리는 사례: " + run(pk.ok) + " → PASS (개입 후 성공, 같은 과제에 실패 사례가 있는 DCC 실행)" : ""],
      [pk.dcc ? nav(state, { tab: "context", exp: pid, task: pk.dcc.task_id, cond: pk.dcc.condition }) : null, "layers", "컨텍스트 최적화", "정보 선별 및 토큰 예산 관리",
        pk.dcc ? "열리는 사례: " + run(pk.dcc) + " (선택·제외가 모두 있는 복구 DCC 패키지)" : ""],
    ];
    const e = exp(pid);
    const r = release(state.release);
    const basis = state.release === LATEST ? "최신 데이터 (고정본 아님)" : r ? (r.label || r.release_id) + " · 고정본" : "";
    return notices(state) + '<article class="ov" aria-label="AAH 개요">' +
      '<section class="ov-intro"><h1>Adaptive Agent Harness</h1><strong>Codex를 위한 검증 가능한 적응형 실행 환경</strong>' +
      '<p class="desc">AI 에이전트의 자율성을 활용하면서 실행 과정과 결과의 검증 가능성을 확보합니다.</p>' + fold("프로젝트 소개", aboutBody(state), { cls: "ovfold" }) + "</section>" +
      '<section class="stats" aria-label="핵심 성과 지표">' +
      stat(fmt(st.judged), "유효 비교 실험", (st.valid.map(label).join(", ") || "유효 실험 없음") + (design ? " · " + design : "") + " · 독립 판정 " + st.judged + "/" + st.planned) +
      stat(t ? fmt(t.passed) : "—", "자동 테스트 통과", t ? "release 검증 기록 " + (st.testsOn || "") + " · 실패 " + t.failed : "이 데이터 기준에 검증 기록 없음") +
      stat(fmt(st.conds.size), "비교 정책", COND.filter((c) => st.conds.has(c)).map((c) => COND_LABEL[c]).join(" · ")) +
      stat(fmt(repoTasks().length), "코드 그래프 탐색", "Main 과제 Repository Graph (공개 과제 원본에서 재추출)") + "</section>" +
      '<section class="sec"><h2>시스템 동작 방식</h2>' + flowSvg() + fold("상세 구조 · 조건별 단계 · 구현 모듈", archBox(state) + techTable(state, st), { cls: "ovfold archfold" }) + "</section>" +
      '<section class="sec"><h2>핵심 기능 탐색</h2><div class="links">' + links.map(([href, ic, title, desc, tip]) => (href ? '<a class="qlink" href="' + esc(href) + '" title="' + esc(tip) + '">' : '<div class="qlink off">') +
        icon(ic, "li") + "<span><strong>" + esc(title) + "</strong><small>" + esc(desc) + "</small></span>" + icon("arrow", "la") + (href ? "</a>" : "</div>")).join("") + "</div></section>" +
      '<section class="sec result"><h2>연구 결과</h2><p>' + (e && e.conditions ? "실제 Codex 비교 실험을 완료하고 적응형 복구 시스템의 동작을 검증했습니다. 탐색적 실험에서 정책의 성능 개선 효과는 입증되지 않았으며, 후속 개선에 필요한 기술적 원인을 확인했습니다."
        : "이 데이터 기준의 주 실험은 결과가 아직 확정되지 않았습니다.") + "</p>" +
      '<a class="audit-link" href="' + esc(nav(state, { tab: "audit", exp: pid })) + '">상세 실험 기록 및 검증 결과' + icon("out", "lo") + "</a>" + fold("결과 요약 자세히", resultDetail(state, st), { cls: "ovfold" }) + "</section>" +
      '</article><p class="ov-foot">데이터: ' + esc(basis) + (C().data_updated_at ? " · 마지막 갱신 " + esc(stamp(C().data_updated_at)) : "") + "</p>";
  }

  // ------------------------------------------------------------------ condition comparison
  const ci = (x) => (!x ? "N/A" : isNum(x.ci_lower) ? "[" + (x.unit === "pp" ? pp(x.ci_lower) + ", " + pp(x.ci_upper) : signedTok(x.ci_lower) + ", " + signedTok(x.ci_upper)) + "]" : "구간 없음 (" + esc(x.interpretation_status) + ")");
  const eff = (x) => (!x ? "N/A" : x.unit === "pp" ? pp(x.effect) : (isNum(x.effect) ? signedTok(x.effect) + " 토큰" : "정의 안 됨"));

  function hbars(rows, opts) {
    const max = opts.max || Math.max(1, ...rows.map((r) => (isNum(r.value) ? r.value : 0)));
    const ticks = opts.ticks || [0, max / 2, max];
    return '<div class="hbars" role="table" aria-label="' + esc(opts.aria) + '"><div class="sr" role="row"><span role="columnheader">조건</span><span role="columnheader">' + esc(opts.aria) + "</span></div>" +
      rows.map((r) => '<div class="hb-row" role="row" data-tip="' + esc(r.tip || "") + '"><span class="hb-label" role="rowheader">' + r.label + '</span><span class="hb-track" role="presentation"><span class="hb-fill c-' + esc(r.c) + '" style="width:' +
        (isNum(r.value) ? Math.max(0, Math.min(100, r.value / max * 100)).toFixed(1) : 0) + '%"></span></span><span class="hb-val" role="cell">' + esc(r.text) + "</span></div>").join("") +
      '<div class="hb-row axis" aria-hidden="true"><span></span><span class="hb-axis">' + ticks.map((t) => '<span style="left:' + (t / max * 100).toFixed(1) + '%">' + esc(opts.tickFmt ? opts.tickFmt(t) : t) + "</span>").join("") + "</span><span></span></div></div>";
  }

  function forest(rows, range, tickFmt, aria) {
    const pos = (v) => ((Math.max(-range, Math.min(range, v)) + range) / (2 * range) * 100).toFixed(2);
    const ticks = [-range, -range / 2, 0, range / 2, range];
    return '<div class="forest" role="table" aria-label="' + esc(aria) + '">' + rows.map((r) => {
      const x = r.c;
      const has = isNum(x.ci_lower);
      const crosses = !has || (x.ci_lower <= 0 && x.ci_upper >= 0);
      return '<div class="fo-row" role="row" data-tip="' + esc(r.tip || "") + '"><span class="fo-label" role="rowheader">' + r.label + '</span><span class="fo-track" role="presentation"><span class="fo-zero" style="left:50%"></span>' +
        (has ? '<span class="fo-ci' + (crosses ? "" : " sig") + '" style="left:' + pos(x.ci_lower) + "%;width:" + Math.max(0.6, pos(x.ci_upper) - pos(x.ci_lower)).toFixed(2) + '%"></span>' : "") +
        (isNum(x.effect) ? '<span class="fo-pt' + (has ? "" : " open") + '" style="left:' + pos(x.effect) + '%"></span>' : "") + '</span><span class="fo-val" role="cell">' + r.text + "</span></div>";
    }).join("") + '<div class="fo-axis" aria-hidden="true"><span class="fo-label"></span><span class="fo-ticks">' + ticks.map((t) => '<span style="left:' + pos(t) + '%">' + esc(tickFmt(t)) + "</span>").join("") + "</span><span></span></div></div>";
  }

  function benchmark(state) {
    const e = exp(state.exp);
    const parts = [header(state, ["조건 비교", "같은 Codex·과제·예산에서 다섯 정책(A–E)을 비교한다. 모든 수치는 동결 분석 산출물(analysis.json)과 독립 판정에서 읽는다."])];
    if (!e) return parts.join("") + panel("데이터 없음", "<p>선택한 실험의 기록이 없다.</p>");
    if (!e.conditions) return parts.join("") + panel("결과 미확정", "<p>이 실험은 진행 중이거나 분석 전이라 조건별 지표와 효과 크기를 표시하지 않는다.</p>");
    const cm = e.conditions;
    const conds = COND.filter((c) => cm[c]);
    const meta = e.meta || {};
    const nTasks = Object.keys(e.tasks || {}).length;
    parts.push('<p class="scope">' + esc(label(state.exp) + " · " + nTasks + "과제 × " + conds.length + "조건 × " + (meta.repetitions || "N/A") + "회 반복 (저장소 " +
      new Set(Object.values(e.tasks || {}).map((t) => t.repo_id)).size + "개) · 탐색적 규모 — 차이는 효과 크기와 95% 구간으로 판단") + "</p>");

    // condition design
    const yes = (t) => '<span class="yes">' + icon("check", "sm") + esc(t) + "</span>";
    const no = (t) => '<span class="no">' + icon("minus", "sm") + esc(t || "없음") + "</span>";
    const design = conds.map((c) => {
      const p = conditionPolicy(e, c).policy;
      const rec = !p.external_intervention ? no() : p.strategy === "TYPED" ? yes("유형별 전략") : yes("일반 재시도");
      return [condName(c), esc(COND_DESC[c]), rec, p.initial_context === "DCC" || p.recovery_context === "DCC" ? yes("DCC") : p.initial_context === "DEFAULT" ? no("DEFAULT") : no(),
        p.diagnosis === "GRAPH" ? yes("그래프 진단") : p.diagnosis === "FLAT" ? no("일반 진단") : no()];
    });
    const recorded = conds.every((c) => conditionPolicy(e, c).recorded);
    const cell = (c) => '<div class="ff-cell c-' + c + '"><span class="ff-name">' + condName(c) + '</span><span class="ff-val">' + esc(cm[c] ? cm[c].successes + "/" + cm[c].valid + " 성공" : "N/A") +
      '</span><span class="ff-sub">' + esc(cm[c] && isNum(cm[c].cps_tokens) ? "성공당 " + fmt(cm[c].cps_tokens) + " 토큰" : "") + "</span></div>";
    const ctr = Object.fromEntries((e.contrasts || []).map((x) => [x.contrast_id, x]));
    const designHtml = '<div class="grid2 design"><div>' + table(["조건", "설명", "외부 복구", "보조 정보", "그래프 기반 진단"], design) +
      '<details><summary>정책 필드 원본 (' + (recorded ? "각 실행 manifest의 resolved policy" : "명세값") + ")</summary>" +
      table(["조건", "초기 컨텍스트", "진단", "전략", "복구 컨텍스트", "외부 개입"], conds.map((c) => { const p = conditionPolicy(e, c).policy; return [condChip(c), esc(CTX_KO[p.initial_context] || p.initial_context), esc(DIAG_KO[p.diagnosis] || p.diagnosis), esc(STRAT_KO[p.strategy] || p.strategy), esc(CTX_KO[p.recovery_context] || p.recovery_context), p.external_intervention ? "있음" : "없음"]; })) + "</details></div>" +
      '<figure class="ff" aria-label="B–E 2×2 요인설계"><figcaption>B–E 2×2 요인설계 <span class="muted">(A는 설계 밖 기준선)</span></figcaption>' +
      '<div class="ff-grid"><span></span><span class="ff-h">기본 컨텍스트 DEFAULT</span><span class="ff-h">동적 컨텍스트 DCC</span>' +
      '<span class="ff-h row">일반 진단 FLAT</span>' + cell("B") + cell("C") + '<span class="ff-h row">그래프 진단 GRAPH</span>' + cell("D") + cell("E") + "</div>" +
      '<p class="small">열 방향 = 컨텍스트 주효과 (CTX_MAIN ' + esc(eff(ctr.CTX_MAIN)) + ") · 행 방향 = 그래프 주효과 (GRAPH_MAIN " + esc(eff(ctr.GRAPH_MAIN)) + ") · 상호작용 " + esc(eff(ctr.INTERACTION)) + "</p>" +
      '<div class="ff-a">' + cell("A") + '<span class="small muted">외부 개입·보조 정보 없음 (Codex 네이티브)</span></div></figure></div>';

    // charts
    const succ = hbars(conds.map((c) => ({ c, label: condName(c), value: cm[c].valid ? cm[c].successes / cm[c].valid : 0,
      text: cm[c].successes + "/" + cm[c].valid + " 과제 · " + pct(cm[c].tsr_task_mean),
      tip: COND_LABEL[c] + " · 성공 " + cm[c].successes + " · 실패 " + cm[c].failures + " · 결측 " + cm[c].missing + " · 평균 실행 " + secs(cm[c].mean_runtime_ms) })),
      { max: 1, ticks: [0, 0.25, 0.5, 0.75, 1], tickFmt: (t) => Math.round(t * 100) + "%", aria: "과제 성공률" });
    const cpsMax = Math.max(...conds.map((c) => cm[c].cps_tokens || 0), 1);
    const nice = (v) => { const p = Math.pow(10, Math.floor(Math.log10(v))); return Math.ceil(v / p) * p; };
    const cmax = nice(cpsMax);
    const cpsBars = hbars(conds.map((c) => ({ c, label: condName(c), value: cm[c].cps_tokens,
      text: cm[c].cps_tokens === null ? "정의 안 됨" : fmt(cm[c].cps_tokens), tip: COND_LABEL[c] + " · 총 토큰 " + fmt(cm[c].model_tokens_total) + " ÷ 성공 " + cm[c].successes })),
      { max: cmax, ticks: [0, cmax / 2, cmax], tickFmt: fmt, aria: "성공당 토큰" });
    const totMax = nice(Math.max(...conds.map((c) => cm[c].model_tokens_total || 0), 1));
    const totBars = hbars(conds.map((c) => ({ c, label: condName(c), value: cm[c].model_tokens_total, text: fmt(cm[c].model_tokens_total),
      tip: COND_LABEL[c] + " · 실행당 평균 " + fmt(cm[c].mean_tokens_per_valid_run) + " 토큰 · 사용량 관측 " + (cm[c].token_coverage || "N/A") })),
      { max: totMax, ticks: [0, totMax / 2, totMax], tickFmt: fmt, aria: "총 모델 토큰" });
    parts.push('<div class="grid2 charts">' + panel("과제 성공률", succ + '<p class="small muted">독립 평가(비공개 검사) 기준 · 성공 과제 수 / 전체 과제 수</p>') +
      panel("성공당 토큰", cpsBars + '<p class="small muted">낮을수록 효율 · 실패한 실행의 토큰 포함 · 관측값(observe_only)</p>') + "</div>");

    // effects
    const pr = ["E-A", "E-B", "E-C", "E-D", "C-B", "D-B", "CTX_MAIN", "GRAPH_MAIN", "INTERACTION", "P-R3"].filter((k) => ctr[k]);
    const nameOf = { "E-A": "E − A", "E-B": "E − B", "E-C": "E − C", "E-D": "E − D", "C-B": "C − B (DCC)", "D-B": "D − B (그래프)", CTX_MAIN: "컨텍스트 주효과", GRAPH_MAIN: "그래프 주효과", INTERACTION: "상호작용", "P-R3": "P-R3 E − max(C, D)" };
    const fp = forest(pr.map((k) => ({ c: ctr[k], label: esc(nameOf[k]), text: '<strong>' + esc(pp(ctr[k].effect)) + "</strong> " + ci(ctr[k]),
      tip: k + " · 과제 " + ctr[k].n_tasks + " · 군집 " + ctr[k].n_clusters + " · " + ctr[k].interpretation_status })), 0.5, (t) => (t === 0 ? "0" : (t > 0 ? "+" : "−") + Math.abs(Math.round(t * 100))) + "%p", "성공률 차이와 95% 구간");
    const tok = ctr["P-R1"] ? forest([{ c: ctr["P-R1"], label: "P-R1 CPS(C) − CPS(B)", text: "<strong>" + esc(eff(ctr["P-R1"])) + "</strong> " + ci(ctr["P-R1"]), tip: "성공당 토큰 차이 · 음수면 C가 더 효율" }],
      25000, (t) => (t === 0 ? "0" : (t > 0 ? "+" : "−") + fmt(Math.abs(t))), "성공당 토큰 차이와 95% 구간") : "";
    const boot = ((meta.analysis || {}).bootstrap || {}).iterations;
    parts.push(panel("효과 크기와 95% 구간", '<h3 class="ch">성공률 차이 (%p)</h3>' + fp + (tok ? '<h3 class="ch">성공당 토큰 차이 (RQ1 사전 지정 대비)</h3>' + tok : "") +
      '<p class="small muted">점 = 과제 평균 차이 · 가로선 = 95% 구간 (과제 군집 bootstrap ' + esc(fmt(boot)) + "회) · 구간이 0을 지나면 차이의 방향을 결론 내릴 수 없다 · 빈 점 = 구간 방법이 없는 탐색적 추정</p>" +
      sourceNote(e.public_dir + "/paired_contrasts.csv · seed " + (((meta.analysis || {}).analysis_seed) || "N/A"))));

    // task matrix
    const tasks = Object.keys(e.tasks || {}).sort();
    const mrows = tasks.map((t) => ['<strong>' + esc(t) + '</strong><br><span class="small muted">' + esc((e.tasks[t] || {}).repo_id + " · " + (e.tasks[t] || {}).task_family_id) + "</span>"].concat(conds.map((c) => {
      const s = slotOf(e, t, c);
      if (!s) return '<span class="muted">—</span>';
      const ok = s.y === 1, bad = s.y === 0;
      const tip = t + " · " + COND_LABEL[c] + " · 1턴 공개 " + seen(s.first_turn_public) + " → 최종 공개 " + seen(s.final_public) + " · 독립 " + seen(s.evaluation_status) + " · 복구 " + (s.recoveries_delivered || 0) + " · 토큰 " + fmt(s.model_tokens);
      return '<a class="mcell ' + (ok ? "pass" : bad ? "fail" : "unk") + '" href="' + esc(nav(state, { tab: "trace", task: t, cond: c })) + '" data-tip="' + esc(tip) + '" aria-label="' + esc(tip) + '">' +
        (ok ? icon("check", "sm") + "PASS" : bad ? "✕ FAIL" : "N/A") + ((s.recoveries_delivered || 0) ? '<span class="rec" title="복구 전달">↻' + s.recoveries_delivered + "</span>" : "") +
        (s.final_public === "PASS" && s.evaluation_status === "FAIL" ? '<span class="pp" title="공개 검사 PASS · 독립 판정 FAIL">공개✓</span>' : "") + "</a>";
    })));
    parts.push(panel("과제 × 조건 판정", table(["과제"].concat(conds.map((c) => c + " " + COND_NAME[c])), mrows, { cls: "matrix" }) +
      '<p class="small muted">↻n = 복구 전달 횟수 · 공개✓ = 공개 검사는 통과했지만 비공개 검사에서 실패 · 칸을 누르면 실행 추적으로 이동</p>'));

    // table + RQ
    const rows = conds.map((c) => {
      const m = cm[c];
      return [condName(c), fmt(m.planned), fmt(m.valid), fmt(m.successes), fmt(m.failures), fmt(m.missing), pct(m.tsr_task_mean),
        fmt(m.model_tokens_total), m.cps_tokens === null ? "정의 안 됨 (" + esc(m.cps_reason) + ")" : fmt(m.cps_tokens), secs(m.mean_runtime_ms),
        fmt((e.slots || []).filter((s) => s.condition === c).reduce((a, s) => a + (s.recoveries_delivered || 0), 0))];
    });
    const statsTable = table(["조건", "계획", "유효", "성공", "실패", "결측", "과제 평균 성공률", "모델 토큰", "성공당 토큰", "평균 실행 시간", "복구 전달"],
      rows, { numeric: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] }) + sourceNote(e.public_dir + "/metric_summary.csv, run_status.csv");
    const d = e.derived || {};
    const gu = d.graph_use || {};
    const typed = Object.entries(gu).filter(([k]) => !k.startsWith("GENERIC_RETRY")).reduce((a, [, v]) => a + v, 0);
    const gtotal = Object.values(gu).reduce((a, v) => a + v, 0);
    const pairs = d.identical_input_pairs || {};
    const par = d.parity || {};
    const measured = '<span class="badge good">측정</span>', notRun = '<span class="badge warn">미측정</span>';
    parts.push(panel("연구 질문별 결과", table(["연구 질문", "사전 지정 대비", "측정", "점추정", "95% 구간", "결론 (AMD-01 §8)"], [
      ["RQ1 · DCC는 성공당 비용을 줄이는가", "P-R1 = CPS(C) − CPS(B)", measured, eff(ctr["P-R1"]), ci(ctr["P-R1"]), "결론 불충분 — 구간이 0을 포함"],
      ["RQ2 · 그래프 복구가 일반 재시도보다 나은가", "P-R2 공통 실패 체크포인트 복구율", notRun, "미측정", "N/A", "NOT_RUN (AMD-01 §7.2 조건 미충족)"],
      ["RQ3 · 통합 정책의 추가 이득", "P-R3 = E − max(C, D)", measured, eff(ctr["P-R3"]), ci(ctr["P-R3"]), "결론 불충분 — 구간 방법 미승인"],
      ["RQ4 · 구성 요소별 가치", "AB1–AB4", notRun, "미측정", "N/A", "NOT_RUN (Emergency 생략)"],
      ["참고 · 전체 시스템 대 기본 Codex", "E − A", measured, eff(ctr["E-A"]), ci(ctr["E-A"]), "결론 불충분"],
      ["참고 · 2×2 DCC 주효과", "CTX_MAIN", measured, eff(ctr.CTX_MAIN), ci(ctr.CTX_MAIN), "결론 불충분"],
      ["참고 · 2×2 Graph 주효과", "GRAPH_MAIN", measured, eff(ctr.GRAPH_MAIN), ci(ctr.GRAPH_MAIN), "결론 불충분 — 아래 해석 주의 참고"],
    ].map((r) => r.map((x, i) => (i >= 2 && i <= 4 ? x : esc(x))))) +
      fold("해석 주의 — 그래프 진단 " + fmt(gtotal) + "회 · 입력이 같은 쌍 " + fmt(pairs.n) + "개 중 판정이 갈린 쌍 " + fmt(pairs.verdict_diverged) + "개", '<p><strong>사후 기술 분석, 사전 등록 아님.</strong> ' +
      "이 실험에서 그래프 진단은 " + fmt(gtotal) + "회 호출됐고, 유형별 전략(RELOCALIZE 등)을 낸 경우는 " + fmt(typed) + "회다. " +
      "B/D와 C/E의 첫 턴 입력은 " + fmt(par.B_equals_D) + "/" + fmt(par.tasks) + ", " + fmt(par.C_equals_E) + "/" + fmt(par.tasks) +
      " 과제에서 바이트 단위로 같았다. 따라서 B–D, C–E 차이는 그래프 전략의 효과로 귀속할 수 없다. 입력이 같은 쌍 " + fmt(pairs.n) +
      "개 중 첫 턴 공개 검사 결과가 갈린 쌍은 " + fmt(pairs.first_turn_diverged) + "개, 최종 판정이 갈린 쌍은 " + fmt(pairs.verdict_diverged) + "개였다." +
      (pairs.verdict_diverged_all_messages_identical || []).map((p) => " 특히 " + esc(p.task_id) + "의 " + esc(p.pair.split("").join("·")) +
        "는 " + esc(p.turns[0]) + "개 턴 모두 바이트 동일한 메시지를 받았지만 판정이 " + esc(p.verdict.join(" / ")) + "로 갈렸다.").join("") +
      " 1회 반복 설계에서는 모델 비결정성이 조건 간 차이와 같은 크기로 나타난다.</p>") +
      sourceNote(e.public_dir + "/paired_contrasts.csv · bootstrap " + fmt(boot) + "회, seed " + esc((meta.analysis || {}).analysis_seed))));
    parts.push(fold("조건 설계 — 정책 정의와 2×2 요인설계", designHtml));
    parts.push(fold("조건별 상세 통계 표 · 총 모델 토큰", statsTable + '<h3 class="ch">총 모델 토큰</h3>' + totBars + '<p class="small muted">토큰은 관측값(observe_only)이며 강제 상한이 아니다. 과금액은 미관측.</p>'));
    const figs = ((D && D.figures) || {})[state.exp] || {};
    const names = [["success-rate.svg", "성공률"], ["tokens.svg", "토큰과 성공당 토큰"], ["task-matrix.svg", "과제 × 조건 판정"], ["paired-effects.svg", "효과 크기와 95% 구간"]].filter(([n]) => figs[n]);
    if (names.length) {
      parts.push(fold("제출 시점 정적 그림 (같은 데이터의 SVG)", '<div class="grid2">' + names.map(([n, t]) => "<div><h3>" + esc(t) + '</h3><div class="fig">' + figs[n] + "</div></div>").join("") + "</div>"));
    }
    return parts.join("");
  }

  // ------------------------------------------------------------------ trace
  function runPicker(state, e) {
    const tasks = Object.keys(e.tasks || {}).sort();
    return '<div class="filters" role="group" aria-label="실행 선택">' + select("task", state.task, tasks.map((t) => [t, t + " · " + ((e.tasks[t] || {}).task_family_id || "")]), "과제") +
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
    return '<div class="fig tlfig">' + svg + "</div>" + '<div class="legend"><span><span class="sw" style="background:var(--codex)"></span>Codex 실행 (RUN)</span><span><span class="sw" style="background:var(--loop)"></span>하네스 관측·공개 검사 (OBSERVE)</span>' +
      '<span><span class="sw" style="background:var(--aah)"></span>하네스 결정·진단·컨텍스트 구성</span><span><span class="sw" style="background:var(--mute)"></span>시작·종료</span></div>';
  }

  function runHeader(run, e) {
    const r = run.report || {};
    const v = run.verdict || {};
    return kv([
      ["run_id", "<code>" + esc(run.run_id) + "</code> · " + esc(run.slot_id || "") + (e ? " · " + esc(e.experiment_id) : " · S3 합성")],
      ["과제 · 조건", esc(run.task_id) + " · " + condName(run.condition)],
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
    const parts = [header(state, ["실행 추적", "한 실행에서 Codex가 한 일과 하네스가 한 일을 시간순으로 본다."])];
    if (!e) return parts.join("") + panel("데이터 없음", "<p>선택한 실험의 기록이 없다.</p>");
    parts.push(runPicker(state, e));
    const run = slotRun(e, state.task, state.cond);
    if (!run) return parts.join("") + panel("기록 없음", "<p>이 과제·조건의 실행 기록이 없다 (미실행 또는 진행 중).</p>");
    const r = run.report || {};
    const jumps = [linkTo(state, { tab: "context", task: run.task_id, cond: run.condition }, "컨텍스트", "")];
    if ((run.interventions || []).length) jumps.push(linkTo(state, { tab: "replay", case: state.exp + "|" + run.run_id, step: "0" }, "복구 Replay", ""));
    const giv = (run.interventions || []).find((iv) => iv.graph_detail);
    if (giv) jumps.push(linkTo(state, { tab: "graph", gmode: "diag", graph: state.exp + "|" + run.run_id + "|" + giv.intervention_id }, "그래프 진단", ""));
    if (repoGraph(run.task_id)) jumps.push(linkTo(state, { tab: "graph", gmode: "repo", rtask: run.task_id }, "코드 구조", ""));
    parts.push('<div class="runline"><span class="rl-main">' + condName(run.condition) + " · <strong>" + esc(run.task_id) + "</strong></span>" +
      "<span>1턴 공개 " + status(run.first_turn_public) + " → 최종 공개 " + status(r.public_check_status) + "</span><span>독립 판정 " + (run.verdict ? status(run.verdict.status) : '<span class="muted">없음</span>') + "</span>" +
      "<span>턴 " + na(r.turns_used) + " · 복구 " + na(r.recoveries_delivered) + " · " + secs(r.elapsed_ms) + " · 토큰 " + fmt(r.observed_model_tokens) + "</span>" +
      '<span class="jumps">' + jumps.join(" · ") + "</span></div>");
    parts.push(panel("상태 전이 타임라인", timeline(run)));
    if ((run.interventions || []).length) parts.push(panel("개입", (run.interventions || []).map((iv) => interventionCard(iv)).join("")));
    parts.push(fold("실행 상세 — run_id · 정책 · 종료 · 변경 파일 · 경고", runHeader(run, e)));
    parts.push(fold("상태 전이 표 " + esc((run.transitions || []).length) + "개", table(["#", "시점", "이전", "다음", "사유", "개입"],
      (run.transitions || []).map((t) => [fmt(t.seq), secs(t.elapsed_ms), esc(t.from), esc(t.to), esc(t.reason), na(t.intervention_id)]), { numeric: [0, 1] })));
    const turns = [...new Set((run.events || []).map((ev) => ev.turn))];
    const evRows = (run.events || []).map((ev) => [fmt(ev.seq), esc(turnOf(ev.turn)), esc(ev.at ? ev.at.slice(11, 23) : ""), esc(ev.kind), eventDetail(ev), esc(ev.observation)]);
    parts.push(fold("Codex 실행 이벤트 " + esc(evRows.length) + "개 · 턴 " + esc(turns.length) + "개 (정규화 증거 원장)", '<p class="small muted">명령 원문·출력·에이전트 메시지는 제한 자료라 공개 데이터에 포함하지 않는다.</p>' +
      table(["#", "턴", "UTC 시각", "종류", "내용", "관측"], evRows, { numeric: [0] })));
    parts.push(fold("공개 검사 · 턴별 사용량 · 하네스 처리 시간", '<div class="grid2"><div><h3>공개 검사 결과</h3>' +
      table(["시점", "검사", "결과", "시간", "파서"], (run.checks || []).map((c) => [esc(turnOf(c.turn)), esc(c.check_id), status(c.status), fmt(c.duration_ms) + "ms", '<code>' + esc(c.parser_id) + "</code>"])) +
      "</div><div><h3>턴별 사용량</h3>" + table(["턴", "단계", "입력", "캐시 입력", "출력", "합계", "턴 시간"], (run.usage || []).map((u) => [esc(turnOf(u.turn)), esc(u.stage), fmt(u.input_tokens), fmt(u.cached_input_tokens), fmt(u.output_tokens), fmt(u.total_tokens), secs(u.elapsed_ms)]), { numeric: [2, 3, 4, 5, 6] }) +
      "<h3>하네스 로컬 처리 시간</h3>" + table(["단계", "턴", "시간"], (run.overhead || []).map((o) => [esc(o.stage), esc(turnOf(o.turn)), fmt(o.elapsed_ms) + "ms"]), { numeric: [2] }) + "</div></div>"));
    return parts.join("");
  }

  // ------------------------------------------------------------------ context
  function scoreBar(d, stage) {
    const w = DCC_WEIGHTS[stage];
    const c = d.components;
    if (!w || !c || !isNum(d.score)) return "";
    const parts = ["relevance", "scope", "validity", "failure"].map((k) => [k, (w[k] || 0) * (isNum(c[k]) ? c[k] : 0)]);
    const sum = parts.reduce((a, [, v]) => a + v, 0);
    if (Math.abs(sum - d.score) > 1e-6) return "";
    const tip = parts.map(([k, v]) => COMP_KO[k] + " " + (w[k] || 0) + "×" + (isNum(c[k]) ? c[k].toFixed(2) : "N/A") + "=" + v.toFixed(3)).join(" + ") + " = " + d.score.toFixed(3);
    return '<span class="sbar" data-tip="' + esc(tip) + '" role="img" aria-label="' + esc("점수 구성: " + tip) + '">' + parts.filter(([, v]) => v > 0).map(([k, v]) => '<span class="sb-' + k + '" style="width:' + (v * 100).toFixed(1) + '%"></span>').join("") + "</span>";
  }

  function contextTable(pkg) {
    const dcc = pkg.policy === "DCC";
    return table(["", "사유", "종류", "출처", "근거", "점수", "점수 구성", "관련성", "유효성", "범위", "실패", "추정 토큰", "효용 밀도"], (pkg.decisions || []).map((d) => {
      const c = d.components || {};
      const unscored = !dcc || d.reason_code === "CORE_REQUIRED" || d.reason_code === "MERGED_DUPLICATE";
      const n = (v) => (isNum(v) ? v.toFixed(2) : unscored ? "—" : "N/A");
      return [d.selected ? '<span class="badge good">선택</span>' : '<span class="badge crit">제외</span>', '<span data-tip="' + esc(REASON[d.reason_code] || "") + '">' + esc(d.reason_code) + "</span>", esc(d.kind),
        esc(d.source) + (d.span ? ' <span class="muted">L' + esc(d.span.join("–")) + "</span>" : ""), esc(d.provenance + " · " + d.validity),
        isNum(d.score) ? d.score.toFixed(3) : unscored ? "—" : "N/A", scoreBar(d, pkg.stage) || '<span class="muted">—</span>', n(c.relevance), n(c.validity), n(c.scope), n(c.failure),
        isNum(d.estimated_tokens) ? fmt(d.estimated_tokens) : "—", isNum(d.utility_density) ? d.utility_density.toFixed(4) : unscored ? "—" : "N/A"];
    }), { numeric: [5, 7, 8, 9, 10, 11, 12] });
  }

  function pipeline(pkg) {
    const ds = pkg.decisions || [];
    const dcc = pkg.policy === "DCC";
    const sel = ds.filter((d) => d.selected).length;
    const core = ds.filter((d) => d.reason_code === "CORE_REQUIRED").length;
    const cnt = (r) => ds.filter((d) => d.reason_code === r).length;
    const used = pkg.estimated_supplement_tokens, cap = pkg.cap_tokens;
    const steps = [
      ["후보 수집", fmt(ds.length) + "개", "공개 스냅샷·승인된 검사 결과에서 만든 후보 (필수 근거 " + core + "개 포함)"],
      ["관련성·유효성 평가", dcc ? fmt(ds.filter((d) => isNum(d.score)).length) + "개 채점" : "점수 없음", dcc ? "BM25 관련성·범위·유효성·실패 연관성의 가중합" : "DEFAULT는 점수를 매기지 않는다"],
      ["중복·범위 검토", dcc ? "병합 " + cnt("MERGED_DUPLICATE") + " · 무효 " + cnt("INVALID") : "적용 안 함", dcc ? "같은 내용은 하나로 병합, 현재 스냅샷과 맞지 않는 정보 제외" : "공통 최소 근거를 그대로 담는다"],
      ["토큰 예산 고려", fmt(used) + " / " + fmt(cap), dcc ? "효용 ≤ 0 " + cnt("NON_POSITIVE_UTILITY") + " · 예산 초과 " + cnt("OVER_CAP") : "B–E 공통 상한 안에서 포함"],
      ["최종 선택", sel + " 선택 · " + (ds.length - sel) + " 제외", "선택·제외 이유는 아래 표"],
      ["Codex 전달", pkg.stage === "RECOVERY" ? "복구 턴 메시지" : "첫 턴 메시지", "message_hash " + short(pkg.message_hash, 10) + "…"],
    ];
    return '<ol class="pipe' + (dcc ? "" : " plain") + '" aria-label="보조 정보 구성 단계">' + steps.map(([t, v, d], i) => '<li class="' + (!dcc && (i === 1 || i === 2) ? "off" : "") + '"><span class="pn">' + (i + 1) + "</span><strong>" + esc(t) +
      '</strong><span class="pv">' + esc(v) + '</span><span class="pd">' + esc(d) + "</span></li>").join("") + "</ol>";
  }

  function context(state) {
    const e = exp(state.exp);
    const parts = [header(state, ["컨텍스트", "Codex에 보낸 보조 정보가 어떻게 골라졌는지 본다. 사용자 요청과 필수 지침은 모든 조건에서 같다."])];
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
      const note = pkg.policy === "NONE" ? "조건 A는 보조 영역이 비어 있다. 사용자 요청과 필수 지침만 같은 경로로 전달한다 (CT07)."
        : pkg.policy === "DEFAULT" ? "DEFAULT는 공통 최소 근거를 그대로 담는다. 점수·중복 제거·한계 효용 패킹을 쓰지 않는다."
          : "DCC는 후보마다 관련성·유효성·범위·실패 연관성 점수를 매기고, 예산 안에서 한계 효용이 양수인 항목만 담는다. 필수 근거는 점수 없이 유지한다.";
      parts.push(panel((pkg.stage === "RECOVERY" ? "복구" : "최초") + " 패키지 · " + pkg.policy + (pkg.strategy_id ? " · " + pkg.strategy_id : ""), pipeline(pkg) +
        '<div class="tiles">' + tile("선택 / 후보", sel + " / " + total, "핵심 근거 " + fmt(pkg.core_count)) + tile("보조 정보 추정 토큰", fmt(used), "상한 " + fmt(cap) + " (" + pct(ratio) + ")") +
        tile("전체 메시지 추정 토큰", fmt(pkg.estimated_full_tokens), pkg.estimator_id || "") + tile("구성 시간", fmt(pkg.elapsed_ms) + "ms", "로컬 처리") + "</div>" +
        '<div class="meterrow"><span class="small muted">예산 사용</span><div class="meter" role="meter" aria-valuemin="0" aria-valuemax="' + esc(cap) + '" aria-valuenow="' + esc(used) + '" aria-label="보조 정보 예산 사용량"><span style="width:' + (ratio * 100).toFixed(1) + '%"></span></div><span class="small">' + esc(fmt(used) + " / " + fmt(cap)) + "</span></div>" +
        '<p class="small muted">' + esc(note) + "</p>" +
        fold("선택·제외 항목 " + esc(total) + "개 — 이유와 점수 구성", (pkg.policy === "DCC" ? '<div class="legend"><span><span class="sw sb-relevance"></span>관련성</span><span><span class="sw sb-scope"></span>범위</span><span><span class="sw sb-validity"></span>유효성</span><span><span class="sw sb-failure"></span>실패 연관</span><span>막대 전체 폭 = 점수 1.0 · 기록된 점수와 일치할 때만 표시</span></div>' : "") +
          contextTable(pkg) + kv([["상태 · 경고", esc(pkg.status) + " · " + esc((pkg.warnings || []).join(", ") || "없음")], ["검색 방식", esc(pkg.retrieval_version || "N/A")],
            ["package_hash", hashFull(pkg.package_hash)], ["message_hash", hashFull(pkg.message_hash)]]))));
    }
    parts.push(fold("DEFAULT와 DCC는 무엇이 다른가", table(["", "A (NONE)", "DEFAULT (B·D)", "DCC (C·E)"], [
      ["보조 정보", "없음 — 요청·필수 지침만", "공통 최소 근거를 그대로", "후보를 점수로 골라 담음"],
      ["점수 (관련성·유효성·범위·실패)", "—", "없음", "있음 (BM25 + 가중합)"],
      ["중복 병합", "—", "없음", "있음"],
      ["예산 패킹", "—", "B–E 공통 상한만 적용", "한계 효용 순으로 상한 안에서 선택"],
      ["필수 근거·지침", "같은 경로로 전달", "항상 포함", "항상 포함 (점수 없음)"],
    ].map((row) => row.map(esc))) + '<p class="small muted">공통 상한·필수 근거·직렬화 형식은 B–E가 같다.</p>'));
    const rows = COND.map((c) => {
      const r = slotRun(e, state.task, c);
      const p = r && (r.context || []).find((x) => x.stage === "INITIAL");
      const h = r && r.dispatch && r.dispatch[0] ? r.dispatch[0].message_hash : null;
      return { c, p, h };
    });
    const same = (a, b) => a.h && a.h === b.h;
    parts.push(fold("같은 과제의 조건별 최초 패키지 비교 (CT03 바이트 동일성)", table(["조건", "정책", "보조 정보 추정 토큰", "선택 / 후보", "최초 메시지 해시", "동일성"], rows.map((x) => [condName(x.c),
      esc(x.p ? x.p.policy : "N/A"), fmt(x.p ? x.p.estimated_supplement_tokens : null), x.p ? esc((x.p.decisions || []).filter((d) => d.selected).length + " / " + (x.p.decisions || []).length) : "N/A",
      hashShort(x.h, 16), x.c === "D" ? (same(rows[1], rows[3]) ? '<span class="badge good">B와 바이트 동일</span>' : '<span class="badge crit">B와 다름</span>')
        : x.c === "E" ? (same(rows[2], rows[4]) ? '<span class="badge good">C와 바이트 동일</span>' : '<span class="badge crit">C와 다름</span>') : ""]), { numeric: [2] }) +
      '<p class="small muted">초기 입력이 같으면 B/D, C/E의 외부 패키지는 바이트 단위로 같아야 한다. 실제 라이브 기록에서 다시 확인한다.</p>'));
    return parts.join("");
  }

  // ------------------------------------------------------------------ graph explorer
  const GL = { LW: 256, GAP: 96, NH: 26, RG: 6, PAD: 8, CG: 14, TOP: 46, IND: 16, HEAD: 22, M: 12, TAG: 44 };
  const isTestPath = (p) => !!p && (/(^|\/)public_checks\//.test(p) || /(^|\/)test_[^/]*\.py$/.test(p) || /_test\.py$/.test(p));

  function modelFromRepo(g) {
    return {
      source: "repo", task: g.task_id,
      nodes: (g.nodes || []).map((n) => ({ id: n.id, kind: n.kind, ns: "repo", label: n.label, path: n.path, qual: n.qualified_name, span: n.span, lines: n.line_count, obs: n.observation, testId: n.test_id, parse: n.parse_status })),
      edges: (g.edges || []).map((ed) => ({ id: ed.id, rel: ed.relation, s: ed.source, t: ed.target, basis: ed.basis, line: ed.line, obs: ed.observation, cross: false })),
      anchors: new Set(), cands: new Map(), pathEdges: new Set(), allowed: new Set(g.allowed_change_paths || []), warnings: g.warnings || [], unresolved: g.unresolved || [],
    };
  }

  function modelFromDetail(gd, task) {
    const tr = gd.trace || {};
    const rg = repoGraph(task);
    return {
      source: "diag", task,
      nodes: (gd.nodes || []).map((n) => {
        const repo = n.namespace === "repo";
        const i = repo ? String(n.label).indexOf("::") : -1;
        return { id: n.id, kind: n.kind, ns: repo ? "repo" : "exec", label: n.label, path: repo ? (i >= 0 ? n.label.slice(0, i) : n.label) : null,
          qual: repo && i >= 0 ? n.label.slice(i + 2) : null, span: n.span, obs: n.observation, turn: n.turn };
      }),
      edges: (gd.edges || []).map((ed) => ({ id: ed.id, rel: ed.relation, s: ed.source, t: ed.target, basis: ed.basis, obs: ed.observation, cross: !!ed.cross })),
      anchors: new Set(tr.anchor_ids || []), cands: new Map((tr.candidates || []).map((c) => [c.id, c])),
      pathEdges: new Set((tr.paths || []).flatMap((p) => p.edges || [])), pathNodes: new Set((tr.paths || []).flatMap((p) => p.nodes || [])),
      allowed: new Set(rg ? rg.allowed_change_paths || [] : []), warnings: gd.warnings || [], unresolved: gd.unresolved || [],
    };
  }

  function gLayout(m) {
    const byId = new Map(m.nodes.map((n) => [n.id, n]));
    const parent = new Map();
    m.edges.forEach((ed) => { if (ed.rel === "CONTAINS" && byId.has(ed.s) && byId.has(ed.t)) parent.set(ed.t, ed.s); });
    const children = new Map();
    parent.forEach((p, c) => { (children.get(p) || children.set(p, []).get(p)).push(c); });
    const spanOf = (n) => (n.span ? n.span[0] : 0);
    const sortIds = (ids) => ids.slice().sort((a, b) => spanOf(byId.get(a)) - spanOf(byId.get(b)) || (byId.get(a).label < byId.get(b).label ? -1 : 1));
    const laneDefs = { exec: "Execution Graph · 관측된 실행", test: "공개 검사 코드", allowed: "수정 허용 파일", other: "의존 모듈", src: "소스 코드" };
    const lanes = {};
    const addCluster = (key, cl) => { (lanes[key] = lanes[key] || { key, title: laneDefs[key], clusters: [] }).clusters.push(cl); };
    // repository clusters: one per FILE node, with its CONTAINS descendants
    const placed = new Set();
    const files = m.nodes.filter((n) => n.ns === "repo" && n.kind === "FILE").sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : a.id < b.id ? -1 : 1));
    const laneOf = (p) => (isTestPath(p) ? "test" : m.allowed.size ? (m.allowed.has(p) ? "allowed" : "other") : "src");
    files.forEach((f) => {
      const rows = [];
      const walk = (id, depth) => { placed.add(id); rows.push({ id, depth }); sortIds(children.get(id) || []).forEach((c) => { if (!placed.has(c)) walk(c, depth + 1); }); };
      walk(f.id, 0);
      addCluster(laneOf(f.path), { title: null, rows, allowed: m.allowed.has(f.path), path: f.path });
    });
    const loose = m.nodes.filter((n) => n.ns === "repo" && !placed.has(n.id));
    const byPath = {};
    loose.forEach((n) => { (byPath[n.path || "?"] = byPath[n.path || "?"] || []).push(n.id); });
    Object.keys(byPath).sort().forEach((p) => addCluster(laneOf(p), { title: p, rows: sortIds(byPath[p]).map((id) => ({ id, depth: 0 })), allowed: m.allowed.has(p), path: p }));
    // execution clusters by turn
    const execN = m.nodes.filter((n) => n.ns !== "repo");
    const turns = {};
    execN.forEach((n) => { const t = n.turn || "~"; (turns[t] = turns[t] || []).push(n); });
    const korder = (k) => { const i = ["TEST", "FAILURE", "COMMAND", "COMMAND_RESULT", "CHANGE"].indexOf(k); return i < 0 ? 9 : i; };
    Object.keys(turns).sort((a, b) => (a === "baseline" ? -1 : b === "baseline" ? 1 : a < b ? -1 : 1)).forEach((t) => {
      const list = turns[t].sort((a, b) => korder(a.kind) - korder(b.kind) || (a.label < b.label ? -1 : 1));
      addCluster("exec", { title: t === "~" ? "턴 미상" : turnOf(t), rows: list.map((n) => ({ id: n.id, depth: 0 })), exec: true });
    });
    const order = ["exec", "test", "allowed", "src", "other"].filter((k) => lanes[k]);
    const pos = {};
    let H = 0;
    const laneList = order.map((k, i) => {
      const ln = lanes[k];
      ln.x = GL.M + i * (GL.LW + GL.GAP);
      ln.i = i;
      let y = GL.TOP;
      ln.clusters.forEach((cl) => {
        cl.x = ln.x; cl.y = y;
        let yy = y + GL.PAD + (cl.title ? GL.HEAD : 0);
        cl.rows.forEach((r) => {
          const x = ln.x + GL.PAD + r.depth * GL.IND;
          pos[r.id] = { x, y: yy, w: GL.LW - 2 * GL.PAD - r.depth * GL.IND, h: GL.NH, lane: i, depth: r.depth };
          yy += GL.NH + GL.RG;
        });
        cl.h = yy - GL.RG + GL.PAD - y;
        cl.w = GL.LW;
        y += cl.h + GL.CG;
      });
      H = Math.max(H, y);
      return ln;
    });
    const W = GL.M + laneList.length * (GL.LW + GL.GAP) - GL.GAP + 64;
    return { lanes: laneList, pos, W: Math.max(W, 320), H: Math.max(H + 8, 140), parent };
  }

  function edgePath(a, b, rel) {
    if (rel === "CONTAINS" && a.lane === b.lane && b.depth > a.depth) {
      const x0 = a.x + 8;
      return "M" + x0 + "," + (a.y + a.h) + " V" + (b.y + b.h / 2) + " H" + (b.x - 1);
    }
    const ay = a.y + a.h / 2, by = b.y + b.h / 2;
    if (a.lane < b.lane) { const sx = a.x + a.w, tx = b.x - 2, c = (tx - sx) / 2; return "M" + sx + "," + ay + " C" + (sx + c) + "," + ay + " " + (tx - c) + "," + by + " " + tx + "," + by; }
    if (a.lane > b.lane) { const sx = a.x, tx = b.x + b.w + 2, c = (sx - tx) / 2; return "M" + sx + "," + ay + " C" + (sx - c) + "," + ay + " " + (tx + c) + "," + by + " " + tx + "," + by; }
    const x = a.x + a.w, bulge = 24 + Math.min(Math.abs(ay - by) / 5, 46);
    return "M" + x + "," + ay + " C" + (x + bulge) + "," + ay + " " + (x + bulge) + "," + by + " " + (x + 2) + "," + by;
  }

  function displayLabel(n) {
    if (n.ns !== "repo") return n.label;
    if (n.kind === "FILE") return n.path || n.label;
    const q = n.qual || n.label;
    const last = q.split(".").pop();
    return n.kind === "CLASS" ? last : last + "()";
  }

  function graphSvg(m, lay, opts) {
    const o = opts || {};
    const byId = new Map(m.nodes.map((n) => [n.id, n]));
    const merged = new Map();
    m.edges.forEach((ed) => {
      const k = ed.s + "|" + ed.t + "|" + ed.rel;
      const cur = merged.get(k);
      if (cur) { cur.n += 1; if (ed.line) cur.lines.push(ed.line); if (m.pathEdges.has(ed.id)) cur.path = true; } else merged.set(k, Object.assign({ n: 1, lines: ed.line ? [ed.line] : [], path: m.pathEdges.has(ed.id) }, ed));
    });
    const groups = [...new Set(m.edges.map((ed) => relGroup(ed.rel)))];
    let svg = '<svg class="gsvg" viewBox="0 0 ' + lay.W + " " + lay.H + '" preserveAspectRatio="xMidYMid meet" role="group" aria-label="' + esc(o.aria || "그래프") + '"><defs>' +
      groups.map((g) => '<marker id="gm-' + g + '" viewBox="0 0 8 8" refX="7" refY="4" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="mk r-' + g + '"/></marker>').join("") + "</defs>" +
      '<rect class="gbg" x="0" y="0" width="' + lay.W + '" height="' + lay.H + '"/>';
    lay.lanes.forEach((ln) => {
      const n = ln.clusters.reduce((a, cl) => a + cl.rows.length, 0);
      svg += '<text class="lanet" x="' + (ln.x + 2) + '" y="22">' + esc(ln.title) + '</text><text class="lanec" x="' + (ln.x + 2) + '" y="37">' + esc(n + "개 노드" + (ln.key === "exec" ? " · 턴별" : ln.key === "allowed" ? " · task.json allowed_change_paths" : "")) + "</text>";
      ln.clusters.forEach((cl) => {
        svg += '<rect class="gcl' + (cl.allowed ? " allowed" : "") + (cl.exec ? " exec" : "") + '" x="' + cl.x + '" y="' + cl.y + '" width="' + cl.w + '" height="' + cl.h + '" rx="9"/>';
        if (cl.title) svg += '<text class="clt" x="' + (cl.x + GL.PAD + 2) + '" y="' + (cl.y + GL.PAD + 13) + '">' + esc(fit(cl.title, cl.w - 20, 11)) + "</text>";
      });
    });
    merged.forEach((ed) => {
      const a = lay.pos[ed.s], b = lay.pos[ed.t];
      if (!a || !b) return;
      const g = relGroup(ed.rel);
      const sk = (byId.get(ed.s) || {}).kind, tk = (byId.get(ed.t) || {}).kind;
      const tip = ed.rel + (ed.n > 1 ? " ×" + ed.n : "") + " · " + (byId.get(ed.s) || {}).label + " → " + (byId.get(ed.t) || {}).label +
        (ed.lines.length ? " · L" + ed.lines.join(", L") : "") + (ed.basis ? " · 근거 " + ed.basis : "") + " · " + (ed.obs || "") + (ed.cross ? " · 교차 연결" : "") + (ed.path ? " · 탐색 경로" : "");
      const d = edgePath(a, b, ed.rel);
      svg += '<g class="ge r-' + g + (ed.path ? " onpath" : "") + '" data-s="' + esc(ed.s) + '" data-t="' + esc(ed.t) + '" data-rel="' + esc(g) + '" data-sk="' + esc(sk) + '" data-tk="' + esc(tk) + '" data-tip="' + esc(tip) + '">' +
        '<path class="hit" d="' + d + '"/><path class="ln" d="' + d + '"' + (ed.rel === "CONTAINS" ? "" : ' marker-end="url(#gm-' + g + ')"') + "/></g>";
    });
    m.nodes.forEach((n) => {
      const p = lay.pos[n.id];
      if (!p) return;
      const info = KIND[n.kind] || { tag: String(n.kind).slice(0, 5), ko: n.kind };
      const cand = m.cands.get(n.id);
      const anchor = m.anchors.has(n.id);
      const allowedFile = n.kind === "FILE" && m.allowed.has(n.path);
      const cls = "gn k-" + n.kind + " ns-" + n.ns + (anchor ? " anchor" : "") + (cand ? " cand" : "") + (allowedFile ? " allowed" : "") + (m.pathNodes && m.pathNodes.has(n.id) ? " onpath" : "");
      const right = anchor ? "앵커" : cand ? "후보 " + cand.rank : allowedFile ? "수정 허용" : n.kind === "FILE" && isNum(n.lines) ? n.lines + "줄" : n.span ? "L" + n.span[0] + "–" + n.span[1] : "";
      const rw = right ? textW(right, 10) + 10 : 0;
      const text = fit(displayLabel(n), p.w - GL.TAG - 10 - rw, 11.5);
      const tip = info.ko + " · " + n.label + (n.span ? " · L" + n.span[0] + "–" + n.span[1] : "") + " · " + (n.obs || "") + (anchor ? " · 실패 앵커" : "") + (cand ? " · 원인 후보(가설) " + cand.rank + "위" : "") + (allowedFile ? " · 수정 허용 파일" : "");
      svg += '<g class="' + cls + '" data-id="' + esc(n.id) + '" data-kind="' + esc(n.kind) + '" tabindex="0" role="button" aria-label="' + esc(tip) + '" data-tip="' + esc(tip) + '">' +
        '<rect class="nbox" x="' + p.x + '" y="' + p.y + '" width="' + p.w + '" height="' + p.h + '" rx="' + (n.kind === "FUNCTION" || n.kind === "TEST_FUNCTION" ? 13 : 5) + '"/>' +
        '<rect class="ntag" x="' + (p.x + 3) + '" y="' + (p.y + 3) + '" width="' + (GL.TAG - 6) + '" height="' + (p.h - 6) + '" rx="' + (n.kind === "FUNCTION" || n.kind === "TEST_FUNCTION" ? 10 : 3) + '"/>' +
        '<text class="tagt" x="' + (p.x + GL.TAG / 2) + '" y="' + (p.y + 17) + '" text-anchor="middle">' + esc(info.tag) + "</text>" +
        '<text class="lbt" x="' + (p.x + GL.TAG + 4) + '" y="' + (p.y + 17.5) + '">' + esc(text) + "</text>" +
        (right ? '<text class="rgt" x="' + (p.x + p.w - 7) + '" y="' + (p.y + 17) + '" text-anchor="end">' + esc(right) + "</text>" : "") + "</g>";
    });
    return svg + "</svg>";
  }

  function nodeDetail(m, id) {
    if (!m) return "";
    const byId = new Map(m.nodes.map((n) => [n.id, n]));
    const n = byId.get(id);
    if (!n) return graphIntro(m);
    const info = KIND[n.kind] || { ko: n.kind };
    const cand = m.cands.get(id);
    const out = m.edges.filter((ed) => ed.s === id), inc = m.edges.filter((ed) => ed.t === id);
    const rel = (list, dir) => (list.length ? "<ul class=\"rels\">" + list.map((ed) => {
      const o = byId.get(dir === "out" ? ed.t : ed.s) || { label: dir === "out" ? ed.t : ed.s };
      return '<li><span class="rk r-' + relGroup(ed.rel) + '">' + esc(ed.rel) + "</span> " + (dir === "out" ? "→ " : "← ") + '<button type="button" class="linkish" data-gsel="' + esc(o.id || "") + '">' + esc(o.label) + "</button>" +
        (ed.line ? ' <span class="muted">L' + esc(ed.line) + "</span>" : "") + (ed.cross ? ' <span class="badge s1">교차</span>' : "") + "</li>";
    }).join("") + "</ul>" : '<p class="muted small">없음</p>');
    const pairs = [["종류", esc(n.kind) + ' <span class="muted">— ' + esc(info.ko) + "</span>"], [n.ns === "repo" ? "위치" : "관측", esc(n.label)]];
    if (n.qual) pairs.push(["qualified name", "<code>" + esc(n.qual) + "</code>"]);
    if (n.span) pairs.push(["줄 범위", esc("L" + n.span[0] + "–" + n.span[1])]);
    if (isNum(n.lines)) pairs.push(["파일 길이", esc(n.lines + "줄") + (n.parse ? ' <span class="muted">· ' + esc(n.parse) + "</span>" : "")]);
    if (n.turn) pairs.push(["턴", esc(turnOf(n.turn))]);
    if (n.testId) pairs.push(["test_id", "<code>" + esc(n.testId) + "</code>"]);
    pairs.push(["관측 상태", esc(n.obs || "N/A")]);
    if (n.ns === "repo") pairs.push(["수정 허용", m.allowed.size ? (m.allowed.has(n.path) ? '<span class="badge good">허용 파일</span>' : "아님") : '<span class="muted">과제 정보 없음</span>']);
    if (m.anchors.has(id)) pairs.push(["역할", '<span class="badge crit">실패 앵커</span> 탐색 시작점 (관측된 실패)']);
    if (cand) pairs.push(["원인 후보", '<span class="badge s1">' + esc(cand.rank) + "위 · 점수 " + (isNum(cand.score) ? cand.score.toFixed(3) : "N/A") + "</span> " + esc(compText(cand.components)) + " · 관계 " + esc((cand.relations || []).join("→")) + ' <span class="muted">— 가설이며 확정 원인이 아님 (' + esc(cand.observation) + ")</span>"]);
    return "<h3>" + esc(displayLabel(n)) + "</h3>" + kv(pairs) + "<h4>나가는 관계 " + out.length + "</h4>" + rel(out, "out") + "<h4>들어오는 관계 " + inc.length + "</h4>" + rel(inc, "in") +
      '<p class="small"><button type="button" class="linkish" data-gsel="">선택 해제</button></p>';
  }

  function graphIntro(m) {
    if (!m) return "";
    const kinds = {};
    m.nodes.forEach((n) => { kinds[n.kind] = (kinds[n.kind] || 0) + 1; });
    const rels = {};
    m.edges.forEach((ed) => { rels[ed.rel] = (rels[ed.rel] || 0) + 1; });
    return '<h3>노드를 선택해 보세요</h3><p class="small">노드를 누르거나 Tab으로 이동해 Enter를 누르면 연결된 노드와 관계가 강조되고, 여기에 상세 정보가 나온다. 드래그로 이동, 버튼이나 Ctrl+휠로 확대·축소한다.</p>' +
      kv([["노드", esc(KIND_ORDER.filter((k) => kinds[k]).concat(Object.keys(kinds).filter((k) => !KIND_ORDER.includes(k))).map((k) => k + " " + kinds[k]).join(" · ") || "없음")],
        ["관계", esc(Object.keys(rels).sort().map((k) => k + " " + rels[k]).join(" · ") || "없음")]]);
  }

  function graphTools(m, diag) {
    return '<div class="gtool" role="toolbar" aria-label="그래프 보기 도구"><button type="button" class="btn sm" data-gz="in" aria-label="확대">＋</button><button type="button" class="btn sm" data-gz="out" aria-label="축소">－</button>' +
      '<button type="button" class="btn sm" data-gz="fit">전체 맞춤</button><button type="button" class="btn sm" data-gz="reset">초기화</button>' + (diag ? '<button type="button" class="btn sm" data-gpath aria-pressed="false">탐색 경로만 강조</button>' : "") +
      '<span class="small muted gtip">드래그로 이동 · Ctrl+휠로 확대</span></div>';
  }

  function graphFilters(m) {
    const kinds = KIND_ORDER.filter((k) => m.nodes.some((n) => n.kind === k)).concat([...new Set(m.nodes.map((n) => n.kind))].filter((k) => !KIND_ORDER.includes(k)));
    const groups = ["contains", "imports", "calls", "inherits", "tests", "cross", "exec"].filter((g) => m.edges.some((ed) => relGroup(ed.rel) === g));
    return '<div class="gfilters" role="group" aria-label="노드·관계 필터"><p class="tl2">노드</p><div class="chips">' + kinds.map((k) => '<button type="button" class="fchip k-' + esc(k) + '" data-gf="k:' + esc(k) + '" aria-pressed="true"><span class="kd"></span>' + esc((KIND[k] || {}).tag || k) + " " + m.nodes.filter((n) => n.kind === k).length + "</button>").join("") +
      '</div><p class="tl2">관계</p><div class="chips">' + groups.map((g) => '<button type="button" class="fchip r-' + g + '" data-gf="r:' + g + '" aria-pressed="true"><span class="ld"></span>' + esc(REL_GROUP[g]) + "</button>").join("") + "</div></div>";
  }

  function graphLegend(diag) {
    return '<div class="legend glegend"><span><span class="kbox k-FILE"></span>FILE</span><span><span class="kbox k-CLASS"></span>CLASS</span><span><span class="kbox k-FUNCTION round"></span>FUNCTION</span><span><span class="kbox k-TEST_FUNCTION round"></span>TEST_FUNCTION</span>' +
      (diag ? '<span><span class="kbox ns-exec"></span>실행 노드 (CHECK·FAIL·CMD·EXIT·DIFF)</span><span><span class="kbox anchor"></span>실패 앵커</span><span><span class="kbox cand"></span>원인 후보 (가설)</span>' : "") +
      '<span><span class="kbox allowed"></span>수정 허용 파일</span>' +
      '<span><span class="lsw r-contains"></span>CONTAINS</span><span><span class="lsw r-imports"></span>IMPORTS</span><span><span class="lsw r-calls"></span>CALLS</span><span><span class="lsw r-inherits"></span>INHERITS</span><span><span class="lsw r-tests"></span>TESTS</span>' +
      (diag ? '<span><span class="lsw r-cross"></span>교차 연결</span><span><span class="lsw r-exec"></span>실행 관계</span>' : "") + "</div>";
  }

  function warningsBody(m) {
    const groups = {};
    (m.warnings || []).forEach((w) => { const k = String(w).split(":")[0]; (groups[k] = groups[k] || []).push(w); });
    const rows = Object.keys(groups).sort().map((k) => [esc(k), fmt(groups[k].length), esc(WARN[k] || "")]);
    return (rows.length ? table(["종류", "개수", "의미"], rows, { numeric: [1] }) : '<p class="muted">경고 없음</p>') +
      ((m.unresolved || []).length ? "<h3>연결하지 않은 교차 관계</h3>" + table(["관계", "사유", "노드"], m.unresolved.map((u) => [esc(u.relation), esc(u.reason), esc(((m.nodes.find((n) => n.id === u.node)) || {}).label || u.node || "")])) : "") +
      "<details><summary>경고 원문 " + esc((m.warnings || []).length) + "개</summary><ul class=\"mono small\">" + (m.warnings || []).map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul></details>" +
      '<p class="small muted">빌더는 근거 없는 관계를 만들지 않는다. 경고는 연결을 포기한 지점이며 오류가 아니다.</p>';
  }

  function graphCanvas(m, lay, diag, aria, state) {
    LAST_GRAPH = m;
    const sel = state.gnode && m.nodes.some((n) => n.id === state.gnode) ? state.gnode : "";
    const h = Math.max(240, Math.min(720, Math.round(lay.H * 1.05)));
    return '<div class="gbox"><div class="gmain">' + graphTools(m, diag) + '<div class="gview" data-vb="0 0 ' + lay.W + " " + lay.H + '" data-sel="' + esc(sel) + '" style="height:' + h + "px;--gw:" + lay.W + 'px">' + graphSvg(m, lay, { aria }) + "</div>" + graphLegend(diag) + "</div>" +
      '<aside class="gside"><div class="gdetail" aria-live="polite" aria-label="선택한 노드 정보">' + (sel ? nodeDetail(m, sel) : graphIntro(m)) + "</div>" + graphFilters(m) + "</aside></div>";
  }

  // Code-side structure of a run-time graph against the re-extracted Repository Graph of the same task.
  function structureDiff(task, gd) {
    const rg = repoGraph(task);
    if (!rg || !gd) return null;
    const m = modelFromRepo(rg);
    const nodeKey = (nodes) => nodes.map((n) => n.kind + " " + n.label).sort();
    const edgeKey = (mm) => { const lab = new Map(mm.nodes.map((n) => [n.id, n.label])); return mm.edges.filter((ed) => !ed.cross && lab.has(ed.s) && lab.has(ed.t) && mm.nodes.find((n) => n.id === ed.s).ns === "repo" && mm.nodes.find((n) => n.id === ed.t).ns === "repo").map((ed) => ed.rel + " " + lab.get(ed.s) + " → " + lab.get(ed.t)).sort(); };
    const diff = (a, b) => { const cnt = {}; a.forEach((x) => { cnt[x] = (cnt[x] || 0) + 1; }); let extra = 0; b.forEach((x) => { if (cnt[x]) cnt[x] -= 1; else extra += 1; }); return [Object.values(cnt).reduce((t, v) => t + v, 0), extra]; };
    const lm = modelFromDetail(gd, task);
    const [nm, nx] = diff(nodeKey(m.nodes), nodeKey(lm.nodes.filter((n) => n.ns === "repo")));
    const [em, ex] = diff(edgeKey(m), edgeKey(lm));
    return { same: !nm && !nx && !em && !ex, nm, nx, em, ex };
  }

  function compareWithRuns(state, task) {
    const entries = graphEntries(state).filter((x) => x.live && x.run.task_id === task);
    if (!entries.length) return '<p class="muted">선택한 실험(' + esc(label(state.exp)) + ")에는 이 과제의 그래프 진단 기록이 없어 대조할 기록이 없다. 무효 실험(v1) 기록과의 대조 결과는 실험 감사 화면에 있다.</p>";
    const rows = entries.map((x) => {
      const { same, nm, nx, em, ex } = structureDiff(task, x.iv.graph_detail);
      return [esc(label(x.exp.experiment_id)) + " " + statusBadge(x.exp.status), condName(x.run.condition), esc(x.iv.intervention_id),
        esc((x.run.report || {}).changed_paths && x.run.report.changed_paths.length ? x.run.report.changed_paths.join(", ") : "없음"),
        same ? '<span class="badge good">코드 노드·관계 일치</span>' : '<span class="badge warn">다름: 노드 −' + nm + "/+" + nx + " · 관계 −" + em + "/+" + ex + "</span>",
        linkTo(state, { tab: "graph", gmode: "diag", graph: x.key }, "진단 보기", "nowrap")];
    });
    return table(["실험", "조건", "개입", "실행 중 변경 파일", "재추출 그래프와 비교", ""], rows) +
      '<p class="small muted">비교는 코드 쪽 노드(종류·경로·이름)와 코드 간 관계만 본다. 실험 시점 그래프는 그 실행의 작업 사본(에이전트 변경 포함)에서 만들어졌으므로, 파일이 바뀐 실행은 다를 수 있다.</p>';
  }

  function graph(state) {
    const parts = [header(state, ["그래프 탐색", "코드 구조(Repository Graph)와 실행 증거(Execution Graph), 둘을 잇는 연결을 탐색한다. 원인 후보는 가설이다."])];
    const entries = graphEntries(state);
    const tasks = repoTasks();
    const mode = state.gmode === "diag" || (!state.gmode && state.graph) ? "diag" : tasks.length ? "repo" : "diag";
    const modes = '<div class="segs modes" role="group" aria-label="그래프 보기 방식">' +
      '<a class="seg' + (mode === "repo" ? " on" : "") + '" aria-current="' + (mode === "repo") + '" href="' + esc(nav(state, { tab: "graph", gmode: "repo", rtask: state.rtask })) + '">과제별 Repository Graph</a>' +
      '<a class="seg' + (mode === "diag" ? " on" : "") + '" aria-current="' + (mode === "diag") + '" href="' + esc(nav(state, { tab: "graph", gmode: "diag", graph: state.graph })) + '">그래프 진단 기록 <span class="muted">' + entries.length + "</span></a></div>";
    if (mode === "repo") {
      if (!tasks.length) return parts.join("") + modes + panel("Repository Graph 없음", "<p>이 페이지에는 과제별 Repository Graph 데이터가 없다 (<code>python -m analysis.repository_graphs build</code>).</p>");
      const task = tasks.includes(state.rtask) ? state.rtask : (picks(state).repo || {}).task_id || tasks[0];
      const g = repoGraph(task);
      const m = modelFromRepo(g);
      const lay = gLayout(m);
      const prov = g.provenance || {};
      const counts = {};
      g.nodes.forEach((n) => { counts[n.kind] = (counts[n.kind] || 0) + 1; });
      const rc = {};
      g.edges.forEach((ed) => { rc[ed.relation] = (rc[ed.relation] || 0) + 1; });
      const testFnInTests = g.nodes.filter((n) => n.kind === "FUNCTION" && isTestPath(n.path) && /(^|\.)test_/.test(n.qualified_name || "")).length;
      parts.push('<div class="gbar">' + modes + select("rtask", task, tasks.map((t) => { const x = repoGraph(t); return [t, t + " · " + x.repo_id + " · " + x.task_family_id]; }), "과제") + "</div>");
      if (state.rtask && state.rtask !== task) parts.push('<div class="callout warn" role="status"><strong>주소 확인.</strong> 요청한 과제 \'' + esc(state.rtask) + "'의 그래프가 없어 " + esc(task) + "를 표시한다.</div>");
      parts.push('<p class="statline"><strong>' + esc(task) + "</strong> · 노드 " + fmt(g.nodes.length) + " · 관계 " + fmt(g.edges.length) + " · 수정 허용 <code>" + esc((g.allowed_change_paths || []).join(", ") || "N/A") +
        "</code> · 공개 검사 " + (g.approved_check_ids || []).length + '개 · <span class="muted">현재 저장소에서 재추출 (실험 시점 기록 아님)</span></p>');
      parts.push(graphCanvas(m, lay, false, task + " Repository Graph", state));
      parts.push(fold("이 그래프 읽는 법", "<p>왼쪽부터 <strong>공개 검사 코드 → 수정 허용 파일 → 의존 모듈</strong> 순으로 놓았다. 파일 상자 안에 그 파일이 포함한 클래스·함수가 들어 있고(CONTAINS), 곡선은 호출(CALLS)·import(IMPORTS) 관계다.</p>" +
        kv([["노드", esc(KIND_ORDER.filter((k) => counts[k]).map((k) => k + " " + counts[k]).join(" · "))], ["관계", esc(Object.keys(rc).sort().map((k) => k + " " + rc[k]).join(" · "))], ["빌더", esc(g.status + " · " + (prov.builder_version || ""))]]) +
        (!counts.TEST_FUNCTION && testFnInTests ? "<p><strong>테스트 함수가 FUNCTION으로 기록됨.</strong> 빌더는 <code>Test</code>로 시작하는 클래스 안의 <code>test_</code> 메서드만 TEST_FUNCTION으로 인식한다. 이 과제의 검사 클래스(" +
          esc(g.nodes.filter((n) => n.kind === "CLASS" && isTestPath(n.path)).map((n) => n.qualified_name).join(", ")) + ")는 이 규칙에 맞지 않아 테스트 메서드 " + testFnInTests + "개가 FUNCTION, 그 호출이 CALLS로 기록됐고 TESTS 관계는 0개다. 기록을 바꾸지 않고 그대로 보여 준다 (개선 과제).</p>" : "") +
        sourceNote("public_data/repository_graphs/" + task + ".json · " + (prov.source || "") + " · source revision " + short(prov.source_revision, 12) + "… · " + (prov.builder || "") + " · 모델 호출 0")));
      parts.push(fold("실험 시점 그래프와 대조", compareWithRuns(state, task)));
      parts.push(fold("미해결 관계와 경고 " + esc((g.warnings || []).length) + "개", warningsBody(m)));
      return parts.join("");
    }
    if (!entries.length) return parts.join("") + modes + panel("그래프 진단 기록 없음", "<p>이 데이터 기준에는 그래프 진단이 기록된 실행이 없다. 그래프 진단은 조건 D·E에서 개입이 일어난 실행에만 있다.</p>");
    const cur = entries.find((x) => x.key === state.graph) || picks(state).graphLive || entries[0];
    const groups = [...new Set(entries.map((x) => x.group))];
    parts.push('<div class="gbar">' + modes + '<label>그래프 진단 기록<select data-key="graph">' + groups.map((g) => '<optgroup label="' + esc(g) + '">' +
      entries.filter((x) => x.group === g).map((x) => '<option value="' + esc(x.key) + '"' + (x === cur ? " selected" : "") + ">" +
        esc((x.run.scenario || x.run.task_id) + " · " + x.run.condition + " · " + x.iv.intervention_id + " → " + x.iv.strategy) + "</option>").join("") + "</optgroup>").join("") + "</select></label></div>");
    if (state.graph && cur.key !== state.graph) parts.push('<div class="callout warn" role="status"><strong>주소 확인.</strong> 요청한 그래프 진단 기록을 이 데이터 기준에서 찾지 못해 대표 사례를 표시한다.</div>');
    const gd = cur.iv.graph_detail;
    const tr = gd.trace || {};
    const m = modelFromDetail(gd, cur.run.task_id);
    const lay = gLayout(m);
    const names = Object.fromEntries((gd.nodes || []).map((n) => [n.id, n.label]));
    const cross = (gd.edges || []).filter((x) => x.cross).length;
    const repoN = (gd.nodes || []).filter((n) => n.namespace === "repo").length;
    const reasons = {};
    (gd.unresolved || []).forEach((u) => { const k = u.relation + ": " + u.reason; reasons[k] = (reasons[k] || 0) + 1; });
    const parsers = [...new Set((cur.run.checks || []).map((c) => c.parser_id))];
    const frames = (cur.run.checks || []).flatMap((c) => c.cases || []).filter((c) => c.status === "FAIL");
    parts.push(cur.live ? '<div class="livebar live"><strong>라이브 실행 기록</strong> · ' + esc(label(cur.exp.experiment_id)) + "</div>"
      : '<div class="livebar syn"><strong>합성 검증 — 라이브 아님</strong> · 스크립트 어댑터, 모델 호출 0건. 진단 메커니즘의 동작 증거이며 실험 결과가 아니다.</div>');
    if (cur.exp && cur.exp.experiment_id !== state.exp) parts.push(statusBanner(cur.exp));
    const flow = [
      ["실패 앵커", fmt((tr.anchor_ids || []).length) + "개", "관측된 실패에서 탐색 시작"],
      ["교차 연결", fmt(cross) + "개", cross ? "검증된 근거로 실행↔코드 연결" : "검증된 근거가 없어 연결하지 않음"],
      ["제한 탐색", fmt(tr.visited_count) + " 방문", "깊이 " + tr.depth_limit + " · 방문 " + tr.visit_limit + " · " + (tr.termination || "")],
      ["원인 후보", fmt((tr.candidates || []).length) + "개", (tr.candidates || []).length ? "가설 — 경로 " + (tr.paths || []).length + "개" : "코드로 가는 경로 없음"],
      ["전략", cur.iv.strategy || "N/A", (cur.iv.graph || {}).fallback_reason ? "대체: " + cur.iv.graph.fallback_reason : "유형별 근거 있음"],
    ];
    parts.push('<ol class="pipe diag" aria-label="그래프 진단 단계">' + flow.map(([t, v, d], i) => '<li class="' + (i === 1 && !cross ? "warnstep" : "") + '"><span class="pn">' + (i + 1) + "</span><strong>" + esc(t) + '</strong><span class="pv">' + esc(v) + '</span><span class="pd">' + esc(d) + "</span></li>").join("") + "</ol>");
    parts.push(graphCanvas(m, lay, true, "결합 그래프", state));
    parts.push('<p class="small muted">노드 ' + fmt((gd.nodes || []).length) + " (코드 " + repoN + " · 실행 " + ((gd.nodes || []).length - repoN) + ") · 엣지 " + fmt((gd.edges || []).length) + " · 미해결 연결 " + fmt((gd.unresolved || []).length) +
      " · 그래프 상태 " + esc(gd.status || "N/A") + " · run " + esc(short(cur.run.run_id, 8)) + "… · 노드·엣지 ID는 앞 16자" +
      (repoGraph(cur.run.task_id) ? " · " + linkTo(state, { tab: "graph", gmode: "repo", rtask: cur.run.task_id }, "이 과제의 전체 Repository Graph", "") : "") + "</p>");
    const explain = cross === 0
      ? "<p>이 결합 그래프에는 코드↔실행 교차 연결이 0개다. 미해결 기록: " + esc(Object.entries(reasons).map(([k, v]) => k + " " + v + "건").join(", ") || "없음") + ". 공개 검사 파서 " +
        esc(parsers.join(", ")) + "는 suite 단위 결과만 남겨, 실패 사례 " + esc(frames.length) + "건 모두 traceback frame이 없고 테스트 ID가 검사 ID(" + esc([...new Set(frames.map((f) => f.test_id))].join(", ")) +
        ")다. 매핑 규칙은 근거 없는 TESTS_TARGET·FAILS_AT를 만들지 않으므로 앵커에서 코드 노드로 가는 경로가 없고, 정책은 <code>INSUFFICIENT_TYPED_EVIDENCE</code>로 GENERIC_RETRY를 선택했다. 개선 방향: 공개 검사 결과를 테스트 함수·frame 단위로 기록.</p>"
      : "<p>실패 앵커에서 TESTS_TARGET 등 근거 있는 연결을 따라 코드 노드까지 도달했고, 후보 " + esc((tr.candidates || []).length) + "개와 경로 " + esc((tr.paths || []).length) +
        "개를 기록했다. 후보는 원인 확정이 아니라 가설(observation=inferred)이다. <strong>탐색 경로만 강조</strong>를 누르면 경로가 보인다.</p>";
    parts.push(fold(cross === 0 ? "왜 원인 후보가 0개인가" : "탐색 결과 해설", explain, { open: false }));
    parts.push(fold("원인 후보 " + esc((tr.candidates || []).length) + "개와 탐색 경로", table(["순위", "종류", "위치", "점수", "구성", "관계", "관측"], (tr.candidates || []).map((c) => [fmt(c.rank), esc(c.kind), esc((c.path || "") + (c.qualified_name ? "::" + c.qualified_name : "")),
      isNum(c.score) ? c.score.toFixed(3) : "N/A", esc(compText(c.components)), esc((c.relations || []).join("→")), esc(c.observation + (c.alternative ? " · 대안" : ""))]), { numeric: [0, 3], empty: "후보 없음 — 근거 없는 후보는 만들지 않는다" }) +
      "<h3>탐색 경로</h3>" + ((tr.paths || []).length ? "<ol>" + tr.paths.map((p) => "<li>" + (p.nodes || []).map((n, i) => esc(names[n] || n) + (i < (p.relations || []).length ? ' <span class="muted">—' + esc(p.relations[i]) + "(" + esc((p.directions || [])[i] || "") + ")→</span> " : "")).join("") + ' <span class="muted">비용 ' + esc(p.cost) + "</span></li>").join("") + "</ol>" : '<p class="muted">경로 없음 — 앵커에서 코드 노드로 가는 근거 있는 연결이 없었다.</p>') +
      kv([["탐색 한도", esc("깊이 " + tr.depth_limit + " · 방문 " + tr.visit_limit + " · 분기 " + tr.branch_limit + " · top-k " + tr.top_k + (tr.truncated ? " · truncated" : ""))], ["허용 관계", esc((tr.allowed_relations || []).join(", "))]])));
    parts.push(fold("미해결 연결과 경고 " + esc((gd.warnings || []).length) + "개", warningsBody(m)));
    parts.push(fold("엣지 표 " + esc((gd.edges || []).length) + "개", table(["관계", "출발", "도착", "관측", "근거"], (gd.edges || []).map((x) => [esc(x.relation) + (x.cross ? ' <span class="badge s1">교차</span>' : ""), esc(names[x.source] || x.source), esc(names[x.target] || x.target), esc(x.observation), na(x.basis)]))));
    return parts.join("");
  }

  // ------------------------------------------------------------------ replay
  const STEPS = ["최초 Codex 실행", "공개 검사", "실패 감지", "원인 진단", "복구 전략 선택", "보조 컨텍스트 구성", "Codex 후속 실행", "최종 독립 평가"];
  const STEP_ACTOR = ["codex", "aah", "aah", "aah", "aah", "aah", "codex", "eval"];
  const ACTOR_KO = { codex: "Codex", aah: "AAH", eval: "독립 평가" };

  function turnSummary(run, turn) {
    const list = (run.events || []).filter((x) => x.turn === turn);
    const cmds = list.filter((x) => x.kind === "COMMAND_RESULT");
    const changes = list.filter((x) => x.kind === "CHANGE");
    const use = (run.usage || []).find((u) => u.turn === turn);
    return kv([["명령", fmt(cmds.length) + "개 · exit 0 " + fmt(cmds.filter((x) => x.exit_code === 0).length) + "개"],
      ["파일 변경", changes.length ? changes.map((x) => (x.hunks || []).map((h) => esc(h.path) + " −" + esc(h.old_count) + "/+" + esc(h.new_count)).join(", ")).join(" / ") : '<span class="muted">없음</span>'],
      ["완료 선언", esc(seen((list.find((x) => x.kind === "COMPLETION") || {}).declared))],
      ["모델 토큰 · 턴 시간", use ? fmt(use.total_tokens) + " · " + secs(use.elapsed_ms) : '<span class="muted">미관측</span>'],
      ["이벤트", fmt(list.length) + "개 (명령 원문·에이전트 메시지는 제한 자료)"]]);
  }

  function replayStep(state, run, i, isSynthetic) {
    const iv = (run.interventions || [])[0] || null;
    const p = run.policy || {};
    switch (i) {
      case 0: {
        const pkg = (run.context || []).find((c) => c.stage === "INITIAL");
        return "<p>하네스가 조건 " + condChip(run.condition) + "의 정책으로 첫 메시지를 만들어 Codex 첫 턴을 실행했다. Codex는 무엇을 읽고 고칠지 스스로 정한다.</p>" +
          kv([["초기 보조 정보", pkg ? esc(pkg.policy) + " · " + fmt((pkg.decisions || []).filter((d) => d.selected).length) + "/" + fmt((pkg.decisions || []).length) + " 선택 · " + fmt(pkg.estimated_supplement_tokens) + " 추정 토큰" : '<span class="muted">기록 없음</span>'],
            ["첫 메시지", run.dispatch && run.dispatch[0] ? hashShort(run.dispatch[0].message_hash, 12) : '<span class="muted">N/A</span>']]) + "<h3>1턴 Codex 행동</h3>" + turnSummary(run, "turn-0001");
      }
      case 1: {
        const base = (run.checks || []).filter((c) => c.turn === null || c.turn === undefined);
        const t1 = (run.checks || []).filter((c) => c.turn === "turn-0001");
        const fail = t1.some((c) => c.status === "FAIL");
        return "<p>첫 턴이 끝나자 하네스가 고정된 공개 검사를 <strong>직접</strong> 실행했다 (에이전트의 주장이 아니라 하네스의 관측).</p>" +
          table(["시점", "검사", "결과", "오류"], base.concat(t1).map((c) => [esc(turnOf(c.turn)), esc(c.check_id), status(c.status), esc((c.cases || []).map((x) => x.error_type || "").filter(Boolean).join(", ") || "—")])) +
          (fail ? '<div class="callout crit">1턴 뒤 공개 검사 실패가 관측됐다.</div>' : '<div class="callout">1턴 뒤 공개 검사가 모두 통과했다.</div>');
      }
      case 2:
        if (!iv) {
          if (p.external_intervention === false) return '<div class="callout">조건 A(Vanilla)는 외부 개입이 없도록 설계됐다. 1턴 공개 검사 결과가 ' + esc(seen(run.first_turn_public)) + "여도 복구를 전달하지 않는다.</div>";
          return '<div class="callout">공통 감지 규칙이 개입 트리거를 내지 않았다. 1턴 공개 검사가 ' + esc(seen(run.first_turn_public)) + "였기 때문이다. 공개 검사가 놓친 요구는 하네스가 알 수 없다 (공개 결과만 복구에 사용).</div>";
        }
        return '<div class="why"><strong>왜 개입했나.</strong> ' + esc((iv.trigger_ids || []).map((t) => t + " " + (TRIGGER[t] || "")).join(" · ")) + ".</div>" +
          kv([["트리거", esc((iv.trigger_ids || []).join(", "))], ["대상 검사", esc((iv.check_ids || []).join(", "))],
            ["앵커", fmt(iv.anchor_count) + "개 (공개 검사 결과 ID)"], ["체크포인트 키", "<code>" + esc(iv.checkpoint) + "…</code> (같은 체크포인트 중복 개입 금지)"]]) +
          '<p class="small muted">B–E는 같은 감지 규칙·앵커·개입 상한을 쓴다 (CT04).</p>';
      case 3:
        if (!iv) return '<p class="muted">개입이 없어 진단 단계가 없다.</p>';
        if (!iv.graph) return '<div class="callout">조건 ' + esc(run.condition) + "는 일반 진단(FLAT)을 쓴다 (설계상 그래프 미호출, CT05). 실패 증거 목록을 그대로 전략 선택에 넘긴다.</div>";
        {
          const gd = iv.graph_detail || {};
          const tr = gd.trace || {};
          const cross = (gd.edges || []).filter((x) => x.cross).length;
          const key = (isSynthetic ? "synthetic" : state.exp) + "|" + run.run_id + "|" + iv.intervention_id;
          return kv([["그래프", esc("상태 " + iv.graph.status + " · 노드 " + (gd.nodes || []).length + " · 엣지 " + (gd.edges || []).length + " · 교차 연결 " + cross)],
            ["탐색", esc("방문 " + tr.visited_count + " · 종료 " + tr.termination + " · 후보 " + (tr.candidates || []).length + " · 경로 " + (tr.paths || []).length)],
            ["대체 사유", na(iv.graph.fallback_reason)], ["시간", fmt(iv.graph.build_ms) + "ms (구축)"]]) +
            (cross === 0 ? '<div class="callout warn">교차 연결이 없어 앵커에서 코드 후보에 도달하지 못했다. 근거 없는 후보를 만들지 않는 것이 설계 규칙이다.</div>'
              : '<div class="callout">근거 있는 연결로 코드 후보 ' + esc((tr.candidates || []).length) + "개에 도달했다. 후보는 가설이며 확정 원인이 아니다.</div>") +
            '<p><a href="' + esc(nav(state, { tab: "graph", gmode: "diag", graph: key })) + '">그래프 탐색 화면에서 보기 →</a></p>';
        }
      case 4: {
        if (!iv) return '<p class="muted">개입이 없어 전략 선택이 없다. 종료 사유: ' + esc((run.report || {}).stop_reason) + "</p>";
        const fb = iv.graph && iv.graph.fallback_reason;
        const why = !iv.graph ? "조건 " + run.condition + "는 일반 전략(GENERIC)이 설계값이므로 GENERIC_RETRY를 쓴다."
          : fb ? "그래프 진단이 유형별 전략을 고를 근거를 찾지 못해(" + fb + ") 정책 규칙에 따라 일반 재시도로 대체했다."
            : "그래프 경로로 도달한 근거를 바탕으로 " + iv.strategy + "를 골랐다 (근거 상태 " + ((iv.directive || {}).confidence_status || "N/A") + ").";
        return '<div class="why"><strong>왜 이 전략인가.</strong> ' + esc(why) + "</div>" +
          kv([["전략", esc(iv.strategy) + ' <span class="muted">— ' + esc(STRATEGY[iv.strategy] || "") + "</span>"], ["지시 목표", esc((iv.directive || {}).objective || "N/A")],
            ["근거 상태", esc((iv.directive || {}).confidence_status || "N/A") + " · 사유 " + esc((iv.directive || {}).reason || "N/A")],
            ["대상", (iv.targets || []).length ? esc(iv.targets.join(", ")) : '<span class="muted">없음</span>'], ["다음 검사", esc(((iv.directive || {}).next_checks || []).join(", "))]]);
      }
      case 5: {
        const pkg = (run.context || []).find((c) => c.stage === "RECOVERY");
        if (!pkg) return '<p class="muted">복구 컨텍스트 패키지가 없다 (개입 없음).</p>';
        return pipeline(pkg) + kv([["정책", esc(pkg.policy) + " · 전략 " + esc(pkg.strategy_id)], ["보조 정보", fmt(pkg.estimated_supplement_tokens) + " / " + fmt(pkg.cap_tokens) + " 추정 토큰"],
          ["선택", fmt((pkg.decisions || []).filter((d) => d.selected).length) + " / " + fmt((pkg.decisions || []).length) + " · 핵심 근거 " + fmt(pkg.core_count)],
          ["message_hash", hashShort(pkg.message_hash, 16)]]) + "<details><summary>선택·제외 항목 " + esc((pkg.decisions || []).length) + "개</summary>" + contextTable(pkg) + "</details>";
      }
      case 6: {
        if (!iv) return '<p class="muted">후속 턴이 없다.</p>';
        const t2 = (run.checks || []).filter((c) => c.turn === "turn-0002");
        return "<p>복구 패키지를 같은 세션의 다음 턴으로 전달했다 (" + esc((iv.events || []).map((x) => x.event).join(" → ")) + "). Codex는 이 지시와 보조 정보를 받아 다시 스스로 작업한다.</p>" + turnSummary(run, "turn-0002") +
          table(["검사 (2턴)", "결과"], t2.map((c) => [esc(c.check_id), status(c.status)])) +
          (iv.outcome ? kv([["지시 수행 판정", esc(iv.outcome.adherence_status + " (" + iv.outcome.adherence_basis + ")")]]) : "");
      }
      default: {
        const r = run.report || {};
        const v = run.verdict;
        return "<p>최종 패치를 고정한 뒤, 에이전트가 볼 수 없는 독립 환경에서 비공개 검사로 판정했다. 이 판정은 에이전트·복구 정책에 전달되지 않는다.</p>" +
          kv([["종료 사유", esc(r.stop_reason)], ["턴 · 복구", fmt(r.turns_used) + " · " + fmt(r.recoveries_delivered)], ["최종 공개 검사", status(r.public_check_status)],
            ["독립 판정", v ? status(v.status) + ' <span class="small muted">기능 ' + esc(v.functional_status) + " · 회귀 " + esc(v.regression_status) + " · 지침 " + esc(v.instruction_status) + "</span>" : '<span class="muted">독립 판정 없음 (합성 검증)</span>'],
            ["변경 파일", (r.changed_paths || []).length ? esc(r.changed_paths.join(", ")) : '<span class="muted">없음 (빈 패치)</span>'], ["모델 토큰 · 시간", fmt(r.observed_model_tokens) + " · " + secs(r.elapsed_ms)]]) +
          (r.stop_reason === "NO_SAFE_CONTINUATION" ? '<div class="callout warn">후속 턴 뒤 snapshot이 바뀌지 않아 새 관측이 없었다. 같은 체크포인트에 다시 개입하지 않는 규칙에 따라 안전 종료했다.</div>' : "") +
          (r.public_check_status === "PASS" && v && v.status === "FAIL" ? '<div class="callout warn">공개 검사는 통과했지만 비공개 검사에서 실패했다. 공개 검사가 놓친 요구가 있었다.</div>' : "");
      }
    }
  }

  function sameTaskCompare(state, cur) {
    const e = cur.exp;
    if (!e) return "";
    const task = cur.run.task_id;
    const rows = COND.map((c) => {
      const r = slotRun(e, task, c);
      const sl = slotOf(e, task, c);
      if (!r || !sl) return [condName(c), '<span class="muted">기록 없음</span>', "", "", "", "", "", "", ""];
      const iv = (r.interventions || [])[0];
      return [condName(c) + (r.run_id === cur.run.run_id ? ' <span class="muted">(현재)</span>' : ""), status(sl.first_turn_public), iv ? esc((iv.trigger_ids || []).join(",") + " → " + iv.strategy) : '<span class="muted">없음</span>',
        iv ? (iv.graph ? "그래프 (교차 " + ((iv.graph_detail || {}).edges || []).filter((x) => x.cross).length + ")" : "일반") : "—", fmt(sl.recoveries_delivered || 0), status(sl.final_public), status(sl.evaluation_status), fmt(sl.model_tokens),
        '<a href="' + esc(nav(state, { tab: "replay", case: state.exp + "|" + r.run_id, step: "0" })) + '">Replay</a>'];
    });
    const pairs = (((e.derived || {}).identical_input_pairs || {}).pairs || []).filter((p) => p.task_id === task);
    return fold("같은 과제 " + esc(task) + "의 조건별 실행", table(["조건", "1턴 공개 검사", "개입", "진단", "복구", "최종 공개 검사", "독립 판정", "모델 토큰", ""], rows, { numeric: [4, 7] }) +
      (pairs.length ? '<p class="small">입력 동일성 (B/D, C/E): ' + pairs.map((p) => esc(p.pair.split("").join("·") + " — 첫 메시지 " + (p.same_initial_message ? "동일" : "다름") + ", 모든 메시지 " + (p.same_all_messages ? "동일" : "다름") + ", 판정 " + p.verdict.join(" / "))).join(" · ") + "</p>" : "") +
      '<p class="small muted">1회 반복 기록이다. 같은 입력에서도 판정이 갈릴 수 있으므로 이 표의 차이를 정책 효과로 해석하지 않는다.</p>');
  }

  function replay(state) {
    const parts = [header(state, ["복구 Replay", "저장된 실행 기록을 단계별로 다시 보여 준다. 모델이나 Codex를 호출하지 않는다."])];
    const cases = replayCases(state);
    if (!cases.length) return parts.join("") + panel("Replay 사례 없음", "<p>이 데이터 기준·실험에는 재생할 기록이 없다.</p>");
    const pk = picks(state);
    const want = state["case"];
    const fallback = (pk.ok && cases.find((x) => x.run.run_id === pk.ok.run_id)) || cases[0];
    const cur = cases.find((x) => x.key === want) || fallback;
    if (want && cur.key !== want) parts.push('<div class="callout warn" role="status"><strong>주소 확인.</strong> 요청한 Replay 사례를 이 실험에서 찾지 못해 대표 사례를 표시한다.</div>');
    const step = Math.max(0, Math.min(STEPS.length - 1, parseInt(state.step || "0", 10) || 0));
    const groups = [...new Set(cases.map((x) => x.group))];
    const sel = '<label>재생할 실행 기록<select data-key="case">' + groups.map((g) => '<optgroup label="' + esc(g) + '">' + cases.filter((x) => x.group === g).map((x) =>
      '<option value="' + esc(x.key) + '"' + (x === cur ? " selected" : "") + ">" + esc(x.title) + "</option>").join("") + "</optgroup>").join("") + "</select></label>";
    parts.push('<div class="filters">' + sel + '<span class="ctl" role="group" aria-label="재생 제어"><button type="button" class="btn" data-act="first"' + (step === 0 ? " disabled" : "") + ' aria-label="처음으로">⏮ 처음</button>' +
      '<button type="button" class="btn" data-act="prev"' + (step === 0 ? " disabled" : "") + '>← 이전</button><button type="button" class="btn primary" data-act="play" aria-pressed="' + PLAYING + '">' + (PLAYING ? "⏸ 일시정지" : step === STEPS.length - 1 ? "↻ 처음부터 재생" : "▶ 자동 재생") + "</button>" +
      '<button type="button" class="btn" data-act="next"' + (step === STEPS.length - 1 ? " disabled" : "") + ">다음 →</button></span></div>");
    parts.push(cur.live ? '<div class="livebar live"><strong>라이브 실행 기록</strong> · ' + esc(label(state.exp)) + "</div>"
      : '<div class="livebar syn"><strong>합성 검증 — 라이브 아님</strong> · 스크립트 어댑터, 모델 호출 0건. 메커니즘 동작 증거이며 실험 결과가 아니다.</div>');
    const iv = (cur.run.interventions || [])[0];
    const skip = (i) => !iv && i >= 3 && i <= 6;
    const v = verdictOf(cur.run);
    const stepper = '<ol class="stepper" aria-label="복구 단계">' + STEPS.map((x, i) => '<li class="st a-' + STEP_ACTOR[i] + (i === step ? " on" : i < step ? " done" : "") + (skip(i) ? " skip" : "") + '"' + (i === step ? ' aria-current="step"' : "") +
      '><button type="button" data-act="step" data-step="' + i + '"><span class="sn">' + (i + 1) + '</span><span class="sl">' + esc(x) + '</span><span class="sa">' + esc(ACTOR_KO[STEP_ACTOR[i]]) + (skip(i) ? " · 해당 없음" : "") + "</span></button></li>").join("") + "</ol>";
    parts.push('<section class="rp"><h2 class="rp-title">' + esc(cur.title) + '</h2><p class="rp-status">1턴 공개 ' + status(cur.run.first_turn_public) + " → 최종 공개 " + status((cur.run.report || {}).public_check_status) +
      " · 독립 판정 " + (cur.live ? status(v) : '<span class="muted">없음 (합성)</span>') + "</p>" + stepper +
      '<div class="progress" role="progressbar" aria-valuemin="1" aria-valuemax="' + STEPS.length + '" aria-valuenow="' + (step + 1) + '" aria-label="재생 진행"><span style="width:' + ((step + 1) / STEPS.length * 100).toFixed(1) + '%"></span></div>' +
      '<div class="stepbody a-' + STEP_ACTOR[step] + '"><h3><span class="actor a-' + STEP_ACTOR[step] + '">' + esc(ACTOR_KO[STEP_ACTOR[step]]) + "</span> " + (step + 1) + ". " + esc(STEPS[step]) + "</h3>" + replayStep(state, cur.run, step, !cur.exp) + "</div></section>");
    parts.push(fold("실행 메타데이터 — run_id · 정책", kv([["run_id", "<code>" + esc(cur.run.run_id) + "</code>"], ["정책", esc(policyText(cur.run.policy))],
      ["색 구분", "Codex의 행동 · AAH의 관측·개입 · 독립 평가 (원인 후보는 가설이며 확정 원인이 아님)"]])));
    if (cur.exp) parts.push(sameTaskCompare(state, cur));
    return parts.join("");
  }

  // ------------------------------------------------------------------ audit
  function audit(state) {
    const parts = [header(state, ["실험 감사", "연구 신뢰성과 재현성: 데이터 기준과 고정 해시, 실험 버전과 결함 수정, 사전 동결, 정책 격리, 재현 검증, 한계."])];
    const ids = experimentsIn(state.release);
    const r = release(state.release);
    const st = releaseStats(state);
    const pinRows = ids.map((id) => {
      const entry = (C().experiments || []).find((x) => x.key === id) || {};
      const pin = r && r.experiments && r.experiments[id] ? r.experiments[id].data_sha256 : null;
      const ok = pinOk(state.release, id);
      return [esc(label(id)), statusBadge((exp(id) || {}).status), pin ? hashShort(pin, 12) : '<span class="muted">고정 안 됨</span>', hashShort(entry.data_sha256, 12),
        ok === null ? '<span class="muted">—</span>' : ok ? '<span class="badge good">' + icon("check", "sm") + "일치</span>" : '<span class="badge crit">불일치</span>', esc(stamp(entry.exported_at))];
    });
    const recTiles = '<div class="tiles">' + tile("탐색 가능한 실행 기록", fmt(st.total), "아래 합계") + tile("유효", fmt(st.runs.valid), st.valid.map(label).join(", ") || "없음") +
      tile("무효 (이력 보존)", fmt(st.runs.invalid), st.invalid.map(label).join(", ") || "없음") + (st.runs.open ? tile("결과 미확정", fmt(st.runs.open), st.open.map(label).join(", ")) : "") +
      tile("합성 검증", fmt(st.runs.synthetic), "모델 호출 0 · 라이브 아님") + "</div>" +
      '<p class="small muted">연구 결론에는 유효 실험만 쓰고, 무효·합성 기록은 합산하지 않는다.</p>';
    parts.push(panel("데이터 기준과 버전", recTiles + kv([
      ["선택한 데이터 기준", state.release === LATEST ? '<span class="chip warn">최신 데이터 · 고정본 아님</span> 현재 저장소의 모든 실험 (진행 중 포함)' : r ? esc(r.label || r.release_id) + " · " + esc(r.kind || "") + " · 생성 " + esc(stamp(r.created_at)) + " " + basisChip(state) : "N/A"],
      ["주 실험", esc(r ? label(r.primary) : label(primaryExp(state)))],
      ["유효 / 무효 / 미확정", esc(st.valid.length + " / " + st.invalid.length + " / " + st.open.length + " 실험 (실행 기록 " + st.runs.valid + " / " + st.runs.invalid + " / " + st.runs.open + ", 합성 " + st.runs.synthetic + ")")],
      ["마지막 데이터 갱신", esc(stamp(C().data_updated_at))],
      ["이 페이지의 데이터 SHA-256", D && D.bundleSha ? hashFull(D.bundleSha) : '<span class="muted">페이지 메타데이터에서 읽음 (브라우저에서 표시)</span>'],
    ]) + table(["실험", "상태", "release 고정 해시", "현재 데이터 해시", "고정 일치", "내보낸 시각"], pinRows) +
      '<p class="small muted">release는 실험 데이터 해시를 고정한 이름 붙은 선택이다. 나중에 실험이 추가되거나 상태가 바뀌어도 제출본의 수치는 바뀌지 않으며, 상태가 바뀌면 현재 상태를 정직하게 함께 표시한다.</p>'));
    const blocks = ids.map((id) => {
      const e = exp(id);
      if (!e) return "";
      const m = ((D && D.manifests) || {})[id] || {};
      const graphs = Object.values(e.runs || {}).flatMap((r) => (r.interventions || []).filter((iv) => iv.graph_detail).map((iv) => structureDiff(r.task_id, iv.graph_detail))).filter(Boolean);
      const records = id === state.exp ? "" : '<p class="small">' + linkTo(state, { tab: "trace", exp: id }, esc(label(id)) + " 실행 기록 보기", "") + " · " +
        linkTo(state, { tab: "replay", exp: id }, "Replay", "") + (graphs.length ? " · " + linkTo(state, { tab: "graph", exp: id, gmode: "diag" }, "그래프 진단 기록", "") : "") +
        (isInvalid(e.status) ? ' <span class="muted">— 무효 실험은 분석 화면의 실험 목록에 나오지 않는다</span>' : "") + "</p>";
      return "<details" + (id === state.exp ? " open" : "") + "><summary><strong>" + esc(label(id)) + "</strong> " + statusBadge(e.status) + " <code>" + esc(id) + "</code></summary>" +
        "<p>" + esc(e.status_reason || "") + "</p>" + records +
        (graphs.length ? '<p class="small">실험 시점 그래프 진단 ' + graphs.length + "건 — 재추출 Repository Graph와 코드 노드·관계 일치 " + graphs.filter((g) => g.same).length + "/" + graphs.length + "건</p>" : "") +
        table(["기록", "단계", "내용"], (e.timeline || []).map((t) => [esc(t.ref), esc(t.step), esc(t.detail)])) +
        (m.exported_at ? '<p class="small muted">데이터 내보낸 시각 ' + esc(stamp(m.exported_at)) + " · 상태 갱신 " + esc(stamp(m.status_updated_at)) +
          " · 원천 파일 " + esc((m.source || {}).inputs) + "개</p>" + kv([["원천 입력 해시", hashFull((m.source || {}).inputs_sha256)], ["data_sha256", hashFull(m.data_sha256)], ["content_sha256", hashFull(m.content_sha256)]]) +
          table(["시각", "이전 상태", "새 상태"], (m.status_history || []).map((h) => [esc(stamp(h.at)), esc(h.from || "—"), esc(h.to)])) : "") + "</details>";
    });
    parts.push(runSummary(state));
    const verRows = (C().experiments || []).map((x) => {
      const p = x.progress || {};
      const ee = exp(x.key) || {};
      const sl = ee.slots || [];
      return [esc(x.key), esc(x.label || ee.label || ""), statusBadge(x.status), p.planned ? esc(p.evaluated + "/" + p.planned) : esc(sl.filter((rr) => rr.evaluation_status).length + "/" + sl.length),
        esc(stamp(x.exported_at)), hashShort(x.data_sha256, 12)];
    });
    parts.push(panel("실험 버전과 상태", table(["실험 ID", "이름", "상태", "판정 슬롯", "데이터 내보낸 시각", "데이터 해시"], verRows) +
      '<p class="small muted">유효 · 탐색적 = 주 결과 / 무효 = 결함 이력으로 보존 / 진행 중·분석 전·미검토 = 결과 미확정. 상태는 사람이 검토한 등록부에서만 정한다.</p>' + blocks.join("") +
      fold("release (결과 고정본)", table(["release", "이름", "종류", "생성 시각", "주 실험", "포함 실험"], releaseList().map((rr) =>
        [esc(rr.release_id), esc(rr.label), esc(rr.kind), esc(stamp(rr.created_at)), esc(rr.primary), esc(Object.keys(rr.experiments || {}).join(", "))])))));
    const settings = runSettings(state);
    if (settings) parts.push(fold(label(state.exp) + " 실행 설정 (동결 기준)", settings));
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
      }), { numeric: [2, 3, 4, 6] }) + '<div class="legend"><span><span class="sw" style="background:var(--codex)"></span>exit 0</span><span><span class="sw" style="background:var(--crit)"></span>exit ≠ 0</span></div>' +
        '<p class="small muted">정규화 증거 원장의 COMMAND_RESULT 집계. v1의 비정상 종료는 모두 MXC launcher SYSTEMROOT 누락 오류였다(제한 원시 로그 검토, EL-20261127-034). v2의 exit ≠ 0은 실패하는 테스트 실행 등 정상 결과다.</p>'));
    }
    const rec = r ? r.verification : null;
    parts.push(panel("동결·재현성 검증 기록 (모델 호출 0)", (rec ? kv([["기록일", esc(rec.recorded_on)], ["Python · OS", esc(rec.python + " · " + rec.os)], ["git HEAD", hashShort(rec.git_head, 12) + " (" + esc(rec.working_tree) + ")"], ["라이브 테스트", esc(rec.live_tests)]].concat(rec.dashboard_runtime ? [["대시보드 런타임", esc(rec.dashboard_runtime)]] : [])) +
      table(["항목", "명령", "종료 코드", "결과", "시간"], (rec.steps || []).map((s) => [esc(s.id), "<code>" + esc(s.invocation) + "</code>", s.exit_code === 0 ? '<span class="badge good">0</span>' : '<span class="badge crit">' + esc(s.exit_code) + "</span>", esc(s.result_line), esc(s.seconds) + "초"]))
      : '<p class="muted">이 데이터 기준에는 검증 기록이 없다. release를 고르면 해당 시점의 기록이 표시된다.</p>') +
      '<p class="small">공개 자료만으로 다시 확인: <code>python -m analysis.public_export verify</code>, <code>python -m analysis.submission_bundle verify-public</code> — run_status.csv에서 동결 AMD-01 분석(bootstrap 10,000회 포함)을 다시 계산해 바이트 단위로 대조한다.</p>'));
    const e = exp(state.exp);
    if (e && e.meta && e.meta.freeze && e.meta.freeze.sha256) {
      const m = e.meta;
      parts.push(panel(label(state.exp) + " 동결과 승인 (해시 사슬)", kv([
        ["동결 manifest", "<code>" + esc(m.freeze.path) + "</code><br>" + hashFull(m.freeze.sha256)],
        ["승인 기록", "<code>" + esc(m.approval.path) + "</code> · " + esc(m.approval.approved_at) + "<br>" + hashFull(m.approval.sha256)],
        ["승인된 동결 해시", hashFull(m.approval.approved_freeze_sha256) + " " + (m.approval.approved_freeze_sha256 === m.freeze.sha256 ? '<span class="badge good">일치</span>' : '<span class="badge crit">불일치</span>')],
        ["편입 기록 (AMD-01 3차)", hashFull(m.freeze.inclusion_sha256)],
        ["analysis.json", hashFull((m.analysis || {}).analysis_json_sha256)],
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
        return [condName(c), fmt(x.runs), fmt(x.graph_diagnoses), fmt(x.flat_diagnoses), esc(Object.entries(x.context || {}).map(([k, v]) => k + " ×" + v).join(", "))];
      }), { numeric: [1, 2, 3] }) + '<p class="small muted">A/B/C의 그래프 호출 0, A/B/D의 DCC 호출 0 (CT05·CT06), A의 외부 보조 정보 없음 (CT07).</p>'));
    }
    const gu = e && e.derived ? e.derived.graph_use || {} : {};
    const key = [
      ["규모", "8과제 × 1회 반복이라 조건 간 차이가 과제 1–2개의 판정 차이와 같고, 모델 비결정성과 구분되지 않는다."],
      ["라이브 그래프 복구 미활성", "라이브 그래프 진단 " + fmt(Object.values(gu).reduce((a, b) => a + b, 0)) + "회 모두 교차 연결 0 → 일반 재시도로 대체. 그래프 기반 복구의 효과는 라이브로 측정되지 않았다."],
      ["미측정 연구 질문", "RQ2(P-R2 공통 실패 체크포인트)와 RQ4(AB1–AB4)는 실행하지 않았다."],
    ];
    parts.push(panel("현재 결과에 영향을 미치는 주요 한계", '<div class="keylim">' + key.map(([t, d]) => '<div class="kl">' + icon("alert") + "<div><strong>" + esc(t) + "</strong><p>" + esc(d) + "</p></div></div>").join("") + "</div>" +
      "<h3>전체 한계와 미측정 항목</h3>" + table(["항목", "상태", "근거·영향"], (C().limitations || []).map((x) => x.map(esc))) +
      (e && e.derived && e.conditions ? '<div class="callout warn"><strong>데이터에서 확인한 추가 한계.</strong> 실패 중 ' + fmt(e.derived.public_pass_private_fail) + "건은 공개 검사 PASS·독립 판정 FAIL로, 공개 검사가 놓친 요구가 있거나 비공개 검사가 명세보다 엄격할 가능성이 있다 (큐레이터 사후 검토 대상, 결과를 보고 과제를 빼지 않음).</div>" : "") +
      '<p class="small muted">미측정 = 실행하지 않아 값이 없음 (0이 아님). N/A = 기록에 값이 없음. observe_only = 관측만 했고 강제하지 않음.</p>'));
    return parts.join("");
  }

  // ------------------------------------------------------------------ state and routing
  const VIEWS = { overview, benchmark, trace, graph, context, replay, audit };
  const TAB_TITLE = { overview: "개요", benchmark: "조건 비교", trace: "실행 추적", graph: "그래프 탐색", context: "컨텍스트", replay: "복구 Replay", audit: "실험 감사" };
  const DEFAULT_STATE = { tab: "overview", release: "", exp: "", task: "AAH-D04", cond: "E", gmode: "", rtask: "", graph: "", gnode: "", case: "", step: "0" };

  function parseHash(hash) {
    const h = String(hash || "").replace(/^#/, "");
    const qi = h.indexOf("?");
    const tab = qi < 0 ? h : h.slice(0, qi);
    const query = qi < 0 ? "" : h.slice(qi + 1);
    const state = Object.assign({}, DEFAULT_STATE);
    const req = {};
    if (tab && VIEWS[tab]) state.tab = tab; else if (tab) req.tab = tab;
    query.split("&").filter(Boolean).forEach((pair) => {
      const i = pair.indexOf("=");
      if (i <= 0) return;
      let k, v;
      try { k = decodeURIComponent(pair.slice(0, i)); v = decodeURIComponent(pair.slice(i + 1)); } catch (err) { return; }
      if (Object.prototype.hasOwnProperty.call(DEFAULT_STATE, k) && k !== "tab") { state[k] = v; req[k] = v; }
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
    if (state.gmode !== "repo" && state.gmode !== "diag") state.gmode = "";
    if (!/^\d+$/.test(state.step || "")) state.step = "0";
    if (+state.step > STEPS.length - 1) state.step = String(STEPS.length - 1);
    Object.defineProperty(state, "_req", { value: req, enumerable: false, writable: true });
    return state;
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
    const s = parseHash(toHash(Object.assign({}, DEFAULT_STATE, state || {})));
    return { releases: releaseOptions().map((o) => o[0]), exps: experimentsIn(s.release),
      graphKeys: D ? graphEntries(s).map((x) => x.key) : [], caseKeys: D ? replayCases(s).map((x) => x.key) : [], repoTasks: D ? repoTasks() : [], state: s };
  }

  function init(data, figures) {
    D = normalize(data, figures);
    const o = D ? options(DEFAULT_STATE) : { releases: [], exps: [], graphKeys: [], caseKeys: [], repoTasks: [] };
    return { kind: D ? D.kind : null, releases: o.releases, exps: o.exps, graphKeys: o.graphKeys, caseKeys: o.caseKeys, repoTasks: o.repoTasks, steps: STEPS.length };
  }

  const App = { init, view, parseHash, toHash, options, picks: (s) => picks(parseHash(toHash(Object.assign({}, DEFAULT_STATE, s || {})))), nodeDetail: (id) => nodeDetail(LAST_GRAPH, id),
    esc, fmt, pp, pct, stamp, COND, STEPS, LATEST };
  root.AAHDashboard = App;

  if (typeof document === "undefined") return;

  // ------------------------------------------------------------------ browser only
  let state = null;
  let timer = null;
  let G = null;  // current graph viewport {svg, base, vb, hidden:Set, sel}
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const readJson = (id) => {
    try { return JSON.parse(document.getElementById(id).textContent); } catch (e) { return null; }
  };
  init(readJson("aah-data"), readJson("aah-figures") || {});
  const metaSha = document.querySelector('meta[name="aah-bundle-sha256"]');
  if (D && metaSha) D.bundleSha = metaSha.getAttribute("content");

  // theme: auto (follows the OS) / light / dark
  const THEMES = ["auto", "light", "dark"];
  const THEME_KO = { auto: "자동", light: "라이트", dark: "다크" };
  let theme = "auto";
  try { theme = THEMES.includes(localStorage.getItem("aah-theme")) ? localStorage.getItem("aah-theme") : "auto"; } catch (e) { theme = "auto"; }
  function applyTheme() {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", theme);
    document.querySelectorAll("[data-act=theme]").forEach((b) => { b.textContent = "화면 " + THEME_KO[theme]; b.setAttribute("aria-label", "화면 모드: " + THEME_KO[theme] + " (누르면 바뀜)"); });
  }
  applyTheme();

  function render() {
    const main = document.getElementById("view");
    PLAYING = !!timer;
    main.innerHTML = view(state);
    document.querySelectorAll(".tabs [data-tab]").forEach((b) => {
      const on = b.dataset.tab === state.tab;
      b.setAttribute("aria-selected", String(on));
      b.setAttribute("tabindex", on ? "0" : "-1");
    });
    main.setAttribute("aria-labelledby", "tab-" + state.tab);
    document.title = "AAH · " + (TAB_TITLE[state.tab] || "") + " — 검증 가능한 적응형 실행 환경";
    setupGraph();
  }

  // User navigation adds a history entry (back/forward move between screens); playback replaces it.
  function go(next, replace) {
    const merged = Object.assign({}, state, next);
    state = parseHash(toHash(merged));
    const h = toHash(state);
    if (location.hash !== h) history[replace ? "replaceState" : "pushState"](null, "", h);
    render();
  }
  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }
  const fromLocation = () => { stopTimer(); state = parseHash(location.hash); render(); };
  window.addEventListener("hashchange", fromLocation);
  window.addEventListener("popstate", fromLocation);

  // ---- graph viewport
  function setVB(vb) {
    G.vb = vb;
    G.svg.setAttribute("viewBox", vb.map((v) => v.toFixed(1)).join(" "));
  }
  function zoom(f, cx, cy) {
    const [x, y, w, h] = G.vb;
    const nw = Math.max(G.base[2] / 8, Math.min(G.base[2] * 1.4, w * f));
    const k = nw / w, nh = h * k;
    const px = cx === undefined ? x + w / 2 : cx, py = cy === undefined ? y + h / 2 : cy;
    setVB([px - (px - x) * k, py - (py - y) * k, nw, nh]);
  }
  function applyFilters() {
    if (!G) return;
    G.svg.querySelectorAll(".gn").forEach((n) => { n.style.display = G.hidden.has("k:" + n.dataset.kind) ? "none" : ""; });
    G.svg.querySelectorAll(".ge").forEach((e) => {
      e.style.display = G.hidden.has("r:" + e.dataset.rel) || G.hidden.has("k:" + e.dataset.sk) || G.hidden.has("k:" + e.dataset.tk) ? "none" : "";
    });
  }
  function selectNode(id, updateHash) {
    if (!G) return;
    const svg = G.svg;
    svg.querySelectorAll(".sel,.nbr,.hl").forEach((el) => el.classList.remove("sel", "nbr", "hl"));
    const detail = document.querySelector(".gdetail");
    const node = id ? svg.querySelector('.gn[data-id="' + id.replace(/[^0-9a-zA-Z_-]/g, "") + '"]') : null;
    if (!node) {
      svg.classList.remove("has-sel");
      if (detail) detail.innerHTML = graphIntro(LAST_GRAPH);
      G.sel = null;
    } else {
      node.classList.add("sel");
      svg.classList.add("has-sel");
      svg.querySelectorAll('.ge[data-s="' + id + '"], .ge[data-t="' + id + '"]').forEach((e) => {
        e.classList.add("hl");
        const other = e.dataset.s === id ? e.dataset.t : e.dataset.s;
        const o = svg.querySelector('.gn[data-id="' + other + '"]');
        if (o) o.classList.add("nbr");
      });
      if (detail) detail.innerHTML = nodeDetail(LAST_GRAPH, id);
      G.sel = id;
    }
    if (updateHash) {
      state.gnode = node ? id : "";
      const h = toHash(state);
      if (location.hash !== h) history.replaceState(null, "", h);
    }
  }
  function setupGraph() {
    const box = document.querySelector(".gview");
    if (!box) { G = null; return; }
    const svg = box.querySelector("svg");
    const base = box.dataset.vb.split(" ").map(Number);
    G = { svg, base, vb: base.slice(), hidden: new Set(), sel: null, drag: null };
    if (box.dataset.sel) selectNode(box.dataset.sel, false);
  }

  function archSelect(btn) {
    const box = btn.closest(".archbox");
    if (!box) return;
    box.dataset.cond = btn.dataset.arch;
    box.querySelectorAll("[data-arch]").forEach((b) => { const on = b === btn; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); });
  }

  function copyText(btn) {
    const v = btn.dataset.copy;
    const done = (t) => { btn.textContent = t; setTimeout(() => { btn.textContent = "복사"; }, 1600); };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = v;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      ta.remove();
      done(ok ? "복사됨" : "선택 후 Ctrl+C");
    };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(v).then(() => done("복사됨"), fallback); else fallback();
  }

  function play() {
    if (timer) { stopTimer(); render(); return; }
    const s = parseInt(state.step || "0", 10) || 0;
    if (s >= STEPS.length - 1) state.step = "0";
    timer = setInterval(() => {
      const cur = parseInt(state.step || "0", 10) || 0;
      if (cur >= STEPS.length - 1 || state.tab !== "replay") { stopTimer(); render(); return; }
      go({ step: String(cur + 1) }, true);
    }, reduced() ? 3600 : 2600);
    go({ step: state.step }, true);
  }

  document.addEventListener("click", (event) => {
    const t = event.target;
    const tab = t.closest("[data-tab]");
    if (tab) { stopTimer(); go({ tab: tab.dataset.tab }); return; }
    const cp = t.closest("[data-copy]");
    if (cp) { copyText(cp); return; }
    const ar = t.closest("[data-arch]");
    if (ar) { archSelect(ar); return; }
    const gz = t.closest("[data-gz]");
    if (gz && G) {
      const a = gz.dataset.gz;
      if (a === "in") zoom(0.75); else if (a === "out") zoom(1 / 0.75); else if (a === "fit") setVB(G.base.slice());
      else if (a === "reset") { setVB(G.base.slice()); G.hidden.clear(); applyFilters(); document.querySelectorAll("[data-gf]").forEach((b) => b.setAttribute("aria-pressed", "true")); selectNode("", true); G.svg.classList.remove("pathonly"); document.querySelectorAll("[data-gpath]").forEach((b) => b.setAttribute("aria-pressed", "false")); }
      return;
    }
    const gf = t.closest("[data-gf]");
    if (gf && G) {
      const k = gf.dataset.gf;
      if (G.hidden.has(k)) G.hidden.delete(k); else G.hidden.add(k);
      gf.setAttribute("aria-pressed", String(!G.hidden.has(k)));
      applyFilters();
      return;
    }
    const gp = t.closest("[data-gpath]");
    if (gp && G) { const on = gp.getAttribute("aria-pressed") !== "true"; gp.setAttribute("aria-pressed", String(on)); G.svg.classList.toggle("pathonly", on); return; }
    const gs = t.closest("[data-gsel]");
    if (gs && G) { selectNode(gs.dataset.gsel, true); return; }
    const gn = t.closest(".gn");
    if (gn && G) { if (G.moved) { G.moved = false; return; } selectNode(G.sel === gn.dataset.id ? "" : gn.dataset.id, true); return; }
    const act = t.closest("[data-act]");
    if (!act) return;
    const step = parseInt(state.step || "0", 10) || 0;
    const a = act.dataset.act;
    if (a === "theme") { theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length]; try { localStorage.setItem("aah-theme", theme); } catch (e) { /* private mode */ } applyTheme(); return; }
    if (a === "skip") { document.getElementById("view").focus(); return; }
    if (a === "jump") {
      const el = document.getElementById(act.dataset.target);
      if (el) { el.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" }); el.focus({ preventScroll: true }); }
      return;
    }
    if (a === "prev") { stopTimer(); go({ step: String(Math.max(0, step - 1)) }); }
    if (a === "next") { stopTimer(); go({ step: String(Math.min(STEPS.length - 1, step + 1)) }); }
    if (a === "first") { stopTimer(); go({ step: "0" }); }
    if (a === "step") { stopTimer(); go({ step: act.dataset.step }); }
    if (a === "play") play();
  });

  document.addEventListener("keydown", (event) => {
    const t = event.target;
    if (t.matches && t.matches(".tabs [data-tab]") && ["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
      const tabs = [...document.querySelectorAll(".tabs [data-tab]")];
      const i = tabs.indexOf(t);
      const j = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (i + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault();
      stopTimer();
      go({ tab: tabs[j].dataset.tab });
      const nt = document.querySelector('.tabs [data-tab="' + tabs[j].dataset.tab + '"]');
      if (nt) nt.focus();
      return;
    }
    if (t.classList && t.classList.contains("gn") && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectNode(G && G.sel === t.dataset.id ? "" : t.dataset.id, true);
      return;
    }
    if (event.key === "Escape" && G && G.sel) selectNode("", true);
  });

  document.addEventListener("change", (event) => {
    const key = event.target.dataset && event.target.dataset.key;
    if (!key) return;
    stopTimer();
    const next = { [key]: event.target.value };
    if (key === "case" || key === "graph" || key === "rtask") { next.step = "0"; next.gnode = ""; }
    if (key === "graph") next.gmode = "diag";
    if (key === "rtask") next.gmode = "repo";
    if (key === "release") { next.exp = ""; next.graph = ""; next.case = ""; next.step = "0"; next.gnode = ""; }
    if (key === "exp") { next.graph = ""; next.case = ""; next.step = "0"; next.gnode = ""; }
    go(next);
  });

  // pan (drag) and Ctrl/⌘ + wheel zoom on the graph
  document.addEventListener("pointerdown", (event) => {
    if (!G || !event.target.closest || !event.target.closest(".gview svg") || event.button !== 0) return;
    G.drag = { x: event.clientX, y: event.clientY, vb: G.vb.slice(), id: event.pointerId };
    G.moved = false;
  });
  document.addEventListener("pointermove", (event) => {
    if (G && G.drag && event.pointerId === G.drag.id) {
      const dx = event.clientX - G.drag.x, dy = event.clientY - G.drag.y;
      if (!G.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
      if (!G.moved) { G.moved = true; G.svg.classList.add("panning"); }
      const r = G.svg.getBoundingClientRect();
      const s = Math.max(G.drag.vb[2] / r.width, G.drag.vb[3] / r.height);
      setVB([G.drag.vb[0] - dx * s, G.drag.vb[1] - dy * s, G.drag.vb[2], G.drag.vb[3]]);
    }
  });
  const endDrag = () => { if (G && G.drag) { G.drag = null; G.svg.classList.remove("panning"); } };
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  document.addEventListener("wheel", (event) => {
    if (!G || !(event.ctrlKey || event.metaKey) || !event.target.closest || !event.target.closest(".gview")) return;
    event.preventDefault();
    const r = G.svg.getBoundingClientRect();
    const s = Math.max(G.vb[2] / r.width, G.vb[3] / r.height);
    const ox = (r.width - G.vb[2] / s) / 2, oy = (r.height - G.vb[3] / s) / 2;
    zoom(event.deltaY < 0 ? 0.85 : 1 / 0.85, G.vb[0] + (event.clientX - r.left - ox) * s, G.vb[1] + (event.clientY - r.top - oy) * s);
  }, { passive: false });

  const tip = document.getElementById("tip");
  const showTip = (el, x, y) => {
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    tip.style.left = Math.max(8, Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8)) + "px";
    tip.style.top = Math.max(8, Math.min(y + 14, window.innerHeight - tip.offsetHeight - 8)) + "px";
  };
  document.addEventListener("pointermove", (event) => {
    const el = event.target.closest && event.target.closest("[data-tip]");
    if (!el || !el.getAttribute("data-tip") || (G && G.drag)) { tip.hidden = true; return; }
    showTip(el, event.clientX, event.clientY);
  });
  document.addEventListener("focusin", (event) => {
    const el = event.target.closest && event.target.closest("[data-tip]");
    if (!el || !el.getAttribute("data-tip")) { tip.hidden = true; return; }
    const r = el.getBoundingClientRect();
    showTip(el, r.left, r.bottom);
  });
  document.addEventListener("focusout", () => { tip.hidden = true; });

  state = parseHash(location.hash);
  if (location.hash && location.hash !== toHash(state)) history.replaceState(null, "", toHash(state));
  render();
})(typeof window !== "undefined" ? window : globalThis);
