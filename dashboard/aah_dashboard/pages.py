"""Streamlit rendering. Each page draws a view model from ``models``; nothing here computes results."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from . import charts, data, fmt, models
from .fmt import COND_LABEL, CONDITIONS

PAGES = {"overview": "개요", "compare": "조건 비교", "trace": "실행 추적", "graph": "그래프 탐색",
         "context": "컨텍스트", "replay": "복구 Replay", "audit": "실험 감사"}
SOURCE_NOTE = "모든 화면은 저장소의 public_data(실험 산출물에서 내보낸 공개 데이터)만 읽는다 · 모델·Codex 호출 없음"


# ---------------------------------------------------------------- helpers

def _table(rows, **kwargs):
    if not rows:
        st.caption("기록 없음")
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, **kwargs)


def _kv(pairs):
    st.markdown("\n".join(f"- **{k}**: {_md(v)}" for k, v in pairs))


def _md(value) -> str:
    return str(value).replace("*", "\\*").replace("_", "\\_").replace("<", "&lt;").replace("[", "\\[")


def _tiles(tiles):
    for start in range(0, len(tiles), 4):
        cols = st.columns(4)
        for col, (label, value, note) in zip(cols, tiles[start:start + 4]):
            col.metric(label, value, border=True)
            if note:
                col.caption(note)


def _callout(level, text):
    {"error": st.error, "warning": st.warning, "success": st.success}.get(level, st.info)(text)


def _pick(key: str, options: list, default, label: str, *, format_func=None, container=st, **kwargs):
    """Selectbox whose value survives option changes and mirrors the URL query string (deep links)."""
    if not options:
        return None
    wanted = st.query_params.get(key)
    if st.session_state.get(key) not in options:
        st.session_state[key] = wanted if wanted in options else (default if default in options else options[0])
    value = container.selectbox(label, options, key=key, format_func=format_func or str, **kwargs)
    st.query_params[key] = value
    return value


# ---------------------------------------------------------------- pages

def overview(ctx):
    st.markdown("**AAH(Adaptive Agent Harness)** 는 Codex 모델을 바꾸지 않고 바깥에서 실행을 관측하는 하네스다. 실패를 공통 규칙으로 감지하고, "
                "코드 구조(Repository Graph)와 실행 증거(Execution Graph)를 연결해 원인 후보를 좁힌다. 필요한 보조 정보만 골라(DCC) 다음 턴에 "
                "전달하며, 최종 패치는 에이전트가 볼 수 없는 독립 평가 환경에서 판정한다.")
    st.caption("측정하지 않은 항목은 '미측정' 또는 N/A로 표시한다. 실험 버전끼리 결과를 합산하지 않는다.")
    export = ctx["export"]
    st.subheader(f"{export.get('label') or export['experiment_id']} 실행 요약")
    _tiles(models.kpis(export))
    p = export.get("progress") or {}
    if p.get("planned"):
        st.progress(min(p.get("evaluated", 0) / p["planned"], 1.0), text=f"독립 판정 {p.get('evaluated')}/{p.get('planned')} 슬롯")
    st.subheader("실험 버전과 상태")
    _table(models.experiment_rows(ctx["entries"]))
    st.caption("유효 · 탐색적 = 주 결과 / 무효 = 결함 이력으로 보존 / 진행 중·분석 전·미검토 = 결과 미확정. 상태는 사람이 검토한 등록부(configs/dashboard/experiments.json)에서만 정한다.")
    st.subheader("release (결과 고정본)")
    _table(models.release_rows(ctx["catalog"]))
    verification = ctx["store"].verification(ctx["release"])
    st.subheader("자동 테스트와 재현 검증")
    if verification:
        st.caption(f"기록일 {verification.get('recorded_on')} · Python {verification.get('python')} · {verification.get('os')} · 모델 호출 {verification.get('model_calls')}회")
        _table([{"항목": s.get("id"), "명령": s.get("invocation"), "종료 코드": s.get("exit_code"), "결과": s.get("result_line"),
                 "시간(초)": s.get("seconds")} for s in verification.get("steps") or []])
    else:
        st.caption("이 데이터 기준에는 검증 기록이 첨부되지 않았다 (release를 선택하면 해당 시점의 기록이 표시된다).")
    if export.get("meta"):
        st.subheader("실행 설정 (동결 기준)")
        _kv(models.settings(export))


def compare(ctx):
    export = ctx["export"]
    rows = models.condition_rows(export)
    if rows is None:
        st.info("이 실험은 결과가 확정되지 않아(진행 중 또는 분석 전) 조건별 지표와 효과 크기를 표시하지 않는다.")
        return
    st.subheader("조건별 결과")
    _table(rows)
    st.caption(f"출처: {export.get('public_dir')}/metric_summary.csv")
    left, right = st.columns(2)
    with left:
        st.markdown("**과제 평균 성공률 (독립 판정)**")
        st.altair_chart(charts.success(export), width="stretch")
    with right:
        st.markdown("**과제 × 조건 판정**")
        st.altair_chart(charts.matrix(export), width="stretch")
        st.caption("NSC = NO_SAFE_CONTINUATION · † = 공개 검사는 통과, 독립 판정은 실패")
    left, right = st.columns(2)
    with left:
        st.markdown("**조건별 총 모델 토큰**")
        st.altair_chart(charts.tokens(export, "model_tokens_total", "모델 토큰"), width="stretch")
    with right:
        st.markdown("**성공 1건당 토큰 (실패 비용 포함)**")
        st.altair_chart(charts.tokens(export, "cps_tokens", "성공당 토큰"), width="stretch")
    st.caption("두 측정치는 단위가 같아도 의미가 달라 축을 공유하지 않는다. 토큰은 관측만 했다(observe_only).")
    st.markdown("**paired 대비 효과 크기와 95% 구간** (task family cluster bootstrap)")
    st.altair_chart(charts.forest(export), width="stretch")
    st.subheader("연구 질문별 결과")
    _table(models.rq_rows(export))
    note = models.interpretation(export)
    if note:
        st.info(note)
    st.caption(f"출처: {export.get('public_dir')}/paired_contrasts.csv · AMD-01 §8: 유의성 검정·비열등성 판정 없음")


def _run_picker(ctx):
    export = ctx["export"]
    tasks = models.tasks(export)
    left, right = st.columns(2)
    task = _pick("task", tasks, "AAH-D04", "과제", container=left,
                 format_func=lambda t: f"{t} · {(export.get('tasks') or {}).get(t, {}).get('task_family_id', '')}")
    cond = _pick("cond", list(CONDITIONS), "E", "조건", container=right, format_func=COND_LABEL.get)
    return models.slot_run(export, task, cond) if task else None, task


def trace(ctx):
    run, _ = _run_picker(ctx)
    if run is None:
        st.info("이 과제·조건의 실행 기록이 없다 (미실행 또는 진행 중).")
        return
    st.subheader("실행 요약")
    _kv(models.run_summary(run))
    st.subheader("상태 전이 타임라인")
    chart = charts.timeline(run)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    with st.expander("전이 표"):
        _table(models.timeline_rows(run))
    st.subheader("Codex 실행 이벤트 (정규화 증거 원장)")
    st.caption("명령 원문·출력·에이전트 메시지는 제한 자료라 공개 데이터에 없다.")
    _table(models.event_rows(run))
    left, right = st.columns(2)
    with left:
        st.markdown("**공개 검사 결과**")
        _table(models.check_rows(run))
    with right:
        st.markdown("**턴별 사용량**")
        _table(models.usage_rows(run))
        st.markdown("**하네스 로컬 처리 시간**")
        _table(models.overhead_rows(run))
    for iv in run.get("interventions") or []:
        st.subheader("개입 기록")
        _kv(models.intervention_kv(iv))


def graph(ctx):
    entries = models.graph_entries(ctx["export"], ctx["synthetic"])
    if not entries:
        st.info("이 실험에는 그래프 진단 기록이 없다.")
        return
    keys = [e["key"] for e in entries]
    by_key = {e["key"]: e for e in entries}
    key = _pick("graph", keys, keys[0], "그래프 진단 기록", format_func=lambda k: f"[{by_key[k]['group']}] {by_key[k]['title']}")
    entry = by_key[key]
    if entry["synthetic"]:
        st.warning("S3 합성 검증 기록 — 스크립트 어댑터, 모델 호출 0건. 그래프 진단 메커니즘의 동작 증거이며 라이브 실험 결과가 아니다.")
    model = models.graph_model(entry)
    _tiles(model["tiles"])
    _callout(*model["explain"])
    st.graphviz_chart(model["dot"], width="stretch")
    st.caption("파란 테두리 = 코드 노드 · 주황 테두리 = 실행 노드 · 초록 = 교차 연결·탐색 경로·원인 후보 · 굵은 테두리 = 실패 앵커")
    left, right = st.columns(2)
    with left:
        st.markdown("**원인 후보 (가설)**")
        _table(model["candidates"])
        st.markdown("**탐색 경로**")
        if model["paths"]:
            st.markdown("\n".join(f"{i}. {_md(p)}" for i, p in enumerate(model["paths"], 1)))
        else:
            st.caption("경로 없음 — 앵커에서 코드 노드로 가는 근거 있는 연결이 없었다.")
        st.caption(f"탐색 한도: {model['limits']}")
    with right:
        st.markdown("**미해결 연결**")
        _table(model["unresolved"])
        with st.expander(f"그래프 경고 {len(model['warnings'])}개"):
            st.code("\n".join(model["warnings"]) or "없음", language=None)
        with st.expander(f"엣지 표 ({len(model['edges'])})"):
            _table(model["edges"])


def context(ctx):
    run, task = _run_picker(ctx)
    if run is None:
        st.info("이 과제·조건의 실행 기록이 없다.")
        return
    packages = models.context_packages(run)
    if not packages:
        st.caption("컨텍스트 패키지 기록 없음")
    for pkg in packages:
        st.subheader(pkg["title"])
        st.info(pkg["note"])
        _tiles(pkg["tiles"])
        st.progress(pkg["ratio"], text=f"보조 정보 예산 사용 {fmt.pct(pkg['ratio'])}")
        _kv(pkg["kv"])
        _table(pkg["rows"])
    st.subheader("같은 과제의 조건별 최초 패키지 비교")
    _table(models.context_comparison(ctx["export"], task))
    st.caption("CT03 계약: 초기 입력이 같으면 B/D, C/E의 외부 패키지는 바이트 단위로 같아야 한다. 실제 실행 기록에서 다시 확인한다.")


def replay(ctx):
    cases = models.replay_cases(ctx["export"], ctx["synthetic"])
    st.info("Replay — 저장된 실행 기록을 단계별로 다시 보여 준다. 모델이나 Codex를 호출하지 않으며 라이브 실행이 아니다.")
    if not cases:
        st.caption("재생할 기록이 없다.")
        return
    by_key = {c["key"]: c for c in cases}
    keys = list(by_key)

    def reset():
        st.session_state["step"] = models.STEPS[0]

    key = _pick("case", keys, keys[0], "재생할 실행 기록", format_func=lambda k: f"[{by_key[k]['group']}] {by_key[k]['title']}", on_change=reset)
    case = by_key[key]
    if case["synthetic"]:
        st.warning("S3 합성 검증 기록 (스크립트 어댑터, 모델 호출 0건). 메커니즘 동작 증거이며 실험 결과가 아니다.")
    st.caption(f"run_id {case['run'].get('run_id')} · {models.policy_text(case['run'].get('policy'))}")
    autoplay = st.toggle("자동 재생 (2초 간격)", key="autoplay")

    @st.fragment(run_every=2 if autoplay else None)
    def stepper():
        if st.session_state.get("step") not in models.STEPS:
            st.session_state["step"] = models.STEPS[0]
        if autoplay and st.session_state.get("_tick") is not None:
            index = models.STEPS.index(st.session_state["step"])
            st.session_state["step"] = models.STEPS[min(index + 1, len(models.STEPS) - 1)]
        st.session_state["_tick"] = True if autoplay else None
        step = st.segmented_control("단계", list(models.STEPS), key="step", required=True,
                                    format_func=lambda s: f"{models.STEPS.index(s) + 1}. {s}")
        index = models.STEPS.index(step)
        st.progress((index + 1) / len(models.STEPS), text=f"{index + 1} / {len(models.STEPS)} · {step}")
        for block in models.replay_step(case, index):
            if block[0] == "text":
                st.markdown(_md(block[1]))
            elif block[0] == "kv":
                _kv(block[1])
            elif block[0] == "table":
                _table(block[1])
            elif block[0] == "callout":
                _callout(block[1], block[2])

    stepper()


def audit(ctx):
    export, store = ctx["export"], ctx["store"]
    st.subheader("실험 버전별 상태와 이력")
    for entry in ctx["entries"]:
        try:
            other, manifest = store.experiment(entry["key"])
        except data.DataError as error:
            st.error(str(error))
            continue
        with st.expander(f"{other.get('label') or other['experiment_id']} — {other.get('status_label')}", expanded=other is export or other["experiment_id"] == export["experiment_id"]):
            st.markdown(_md(other.get("status_reason") or ""))
            _table([{"기록": t.get("ref"), "단계": t.get("step"), "내용": t.get("detail")} for t in other.get("timeline") or []])
            st.caption(f"데이터 내보낸 시각 {fmt.stamp(manifest.get('exported_at'))} · 상태 갱신 {fmt.stamp(manifest.get('status_updated_at'))} · "
                       f"원천 파일 {manifest['source'].get('inputs')}개 (입력 해시 {fmt.short(manifest['source'].get('inputs_sha256'))}) · "
                       f"data_sha256 {fmt.short(manifest.get('data_sha256'))}")
            _table([{"시각": fmt.stamp(h.get("at")), "이전 상태": h.get("from") or "—", "새 상태": h.get("to")} for h in manifest.get("status_history") or []])
    finalized = []
    for entry in ctx["entries"]:
        try:
            other, _ = store.experiment(entry["key"])
        except data.DataError:
            continue
        if other.get("derived"):
            finalized.append(other)
    if finalized:
        st.subheader("에이전트 셸 명령 결과 (결함 발견의 근거)")
        rows = models.command_rows(finalized)
        st.altair_chart(charts.commands(rows), width="stretch")
        _table(rows)
        st.caption("v1의 비정상 종료는 모두 MXC launcher SYSTEMROOT 누락 오류였다(제한 원시 로그 검토, EL-20261127-034). v2의 exit ≠ 0은 실패하는 테스트 실행 등 정상 결과다.")
    if export.get("meta") and (export["meta"].get("freeze") or {}).get("sha256"):
        st.subheader("동결과 승인 (해시 사슬)")
        _kv(models.hash_chain(export))
        limits = (export["meta"].get("approval") or {}).get("disclosed_limitations") or []
        if limits:
            st.markdown("**승인 시 공개한 한계**")
            st.markdown("\n".join(f"- {_md(x)}" for x in limits))
    if export.get("derived") and export.get("conditions") is not None:
        st.subheader("정책 격리 — 실제 실행 기록에서 다시 확인")
        parity, iso = models.isolation_rows(export)
        _table(parity)
        _table(iso)
    st.subheader("연구의 한계와 미측정 항목")
    _table([{"항목": a, "상태": b, "근거·영향": c} for a, b, c in ctx["catalog"].get("limitations") or []])


VIEWS = {"overview": overview, "compare": compare, "trace": trace, "graph": graph, "context": context,
         "replay": replay, "audit": audit}


# ---------------------------------------------------------------- app shell

def run_app():
    st.set_page_config(page_title="AAH 실험 결과 대시보드", layout="wide", initial_sidebar_state="auto")
    try:
        store = data.Store()
        catalog = store.catalog()
    except data.DataError as error:
        st.error(f"공개 데이터를 읽지 못했다: {error}")
        st.caption("DASHBOARD_UPDATE_GUIDE.md의 내보내기 절차로 public_data/를 만든 뒤 다시 배포한다.")
        st.stop()
    releases = dict(store.release_options())
    with st.sidebar:
        st.markdown("### AAH 결과 대시보드")
        release = _pick("release", list(releases), catalog.get("default_release"), "데이터 기준", format_func=releases.get,
                        container=st.sidebar, help="release는 특정 시점에 고정한 결과다. '최신 데이터'는 진행 중인 실험까지 보여 준다.")
        entries = store.experiments_in(release)
        labels = {e["key"]: f"{e.get('label') or e['key']} · {e.get('status_label') or e.get('status')}" for e in entries}
        primary = (store.release(release) or {}).get("primary") if release != data.LATEST else None
        default_exp = primary or next((e["key"] for e in entries if e.get("status") in ("VALID", "VALID_EXPLORATORY")), None)
        exp_key = _pick("exp", list(labels), default_exp, "실험", format_func=labels.get, container=st.sidebar)
        page = st.radio("화면", list(PAGES), format_func=PAGES.get, key="page",
                        index=list(PAGES).index(st.query_params.get("page")) if st.query_params.get("page") in PAGES else 0)
        st.query_params["page"] = page
        st.divider()
        st.caption(f"마지막 데이터 갱신: {fmt.stamp(catalog.get('data_updated_at'))}")
        st.caption(f"내보내기 {catalog.get('exporter_version')} · release {len(catalog.get('releases') or [])}개 · 실험 {len(catalog.get('experiments') or [])}개")
        st.caption(SOURCE_NOTE)
    if exp_key is None:
        st.warning("이 데이터 기준에 표시할 실험이 없다.")
        st.stop()
    try:
        export, manifest = store.experiment(exp_key)
        synthetic = [s for s, _ in store.synthetic()]
    except data.DataError as error:
        st.error(str(error))
        st.stop()
    entry = next(e for e in entries if e["key"] == exp_key)
    st.title(PAGES[page])
    st.caption(f"{releases.get(release)} · {export.get('label') or exp_key} ({exp_key}) · {export.get('status_label')} · "
               f"데이터 내보낸 시각 {fmt.stamp(manifest.get('exported_at'))}")
    if entry.get("pin_ok") is False:
        st.error("이 release가 고정한 데이터와 저장소의 현재 데이터가 다르다. 표시 값을 신뢰하지 말고 데이터 저장소를 확인한다.")
    banner = models.status_banner(export)
    if banner:
        _callout(*banner)
    ctx = {"store": store, "catalog": catalog, "release": release, "entries": entries, "export": export,
           "manifest": manifest, "synthetic": synthetic}
    try:
        VIEWS[page](ctx)
    except Exception as error:  # one broken view must not take the whole dashboard down
        st.error(f"이 화면을 그리는 중 오류가 발생했다: {type(error).__name__}: {error}. 다른 화면은 계속 쓸 수 있다.")
