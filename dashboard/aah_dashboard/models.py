"""View models: plain dicts/lists built from the public store (no Streamlit). Every on-screen number comes from here."""

from __future__ import annotations

from . import fmt
from .fmt import COND_LABEL, CONDITIONS

TRIGGER = {"T1": "반복 실패 — 같은 공개 검사 실패가 이어짐", "T2": "탐색 정체 — 행동이 반복되며 진전 없음",
           "T3": "회귀 — 통과하던 공개 검사가 실패로 바뀜", "T4": "완료 선언 후 공개 필수 검사 실패",
           "T5": "UNKNOWN / INFRA — 관측 불가 또는 인프라 이상"}
STRATEGY = {"REVIEW_REGRESSION": "회귀를 만든 변경을 다시 검토", "RELOCALIZE": "근거 있는 다른 코드 위치를 다시 찾음",
            "REFRESH_EVIDENCE": "오래된 증거를 현재 파일로 갱신",
            "GENERIC_RETRY": "실패를 다시 평가하고 수정을 검증하는 일반 재시도", "CONTINUE": "개입 없이 진행",
            "STOP": "안전하게 종료"}
PHASE_GROUP = {"RUN": "Codex 실행", "OBSERVE": "하네스 관측·공개 검사", "DECIDE": "하네스 결정·진단·컨텍스트",
               "DIAGNOSE": "하네스 결정·진단·컨텍스트", "SELECT_STRATEGY": "하네스 결정·진단·컨텍스트",
               "RECOVERY_CONTEXT": "하네스 결정·진단·컨텍스트", "INITIAL_CONTEXT": "하네스 결정·진단·컨텍스트"}
STRATEGY_SHORT = {"REVIEW_REGRESSION": "회귀 검토", "RELOCALIZE": "재위치화", "REFRESH_EVIDENCE": "증거 갱신",
                  "GENERIC_RETRY": "일반 재시도", "CONTINUE": "계속", "STOP": "중단"}
STEPS = ("실패 발생", "실패 감지", "그래프 진단", "복구 전략 선택", "컨텍스트 구성", "후속 실행", "평가 결과")
FINAL = {"VALID", "VALID_EXPLORATORY", "INVALID_INFRA_COMPROMISED", "UNREVIEWED"}


# ---------------------------------------------------------------- status and overview

def status_banner(export: dict) -> tuple[str, str] | None:
    status = export.get("status")
    reason = export.get("status_reason") or ""
    if status in ("VALID", "VALID_EXPLORATORY"):
        return ("info", f"{export.get('status_label')}: {reason}") if status == "VALID_EXPLORATORY" else None
    if status and status.startswith("INVALID"):
        return ("error", f"무효 실험 ({status}). {reason} 연구 질문의 근거로 쓰지 않는다.")
    if status == "UNREVIEWED":
        return ("warning", f"유효성 미검토. {reason}")
    if status in ("IN_PROGRESS", "ANALYSIS_PENDING"):
        p = export.get("progress") or {}
        return ("warning", f"{export.get('status_label')} — 판정 {p.get('evaluated', 'N/A')}/{p.get('planned', 'N/A')} 슬롯. {reason}")
    return ("warning", f"상태 미상: {status}")


def experiment_rows(entries: list[dict]) -> list[dict]:
    rows = []
    for e in entries:
        p = e.get("progress") or {}
        rows.append({"실험": e["key"], "이름": e.get("label") or "—", "상태": e.get("status_label") or e.get("status"),
                     "판정 슬롯": f"{p.get('evaluated', 'N/A')}/{p.get('planned', 'N/A')}",
                     "데이터 내보낸 시각": fmt.stamp(e.get("exported_at")),
                     "데이터 해시": fmt.short(e.get("data_sha256")),
                     "release 고정 일치": {True: "일치", False: "불일치"}.get(e.get("pin_ok"), "—")})
    return rows


def release_rows(catalog: dict) -> list[dict]:
    return [{"release": r["release_id"], "이름": r.get("label"), "종류": r.get("kind"),
             "생성 시각": fmt.stamp(r.get("created_at")), "주 실험": r.get("primary"),
             "포함 실험": ", ".join(r.get("experiments") or {})} for r in catalog.get("releases", [])]


def kpis(export: dict) -> list[tuple[str, str, str]]:
    slots = export.get("slots") or []
    judged = [s for s in slots if s.get("evaluation_status")]
    ok = sum(1 for s in slots if s.get("y") == 1)
    out = [("계획 슬롯", fmt.num(len(slots)), "과제 × 조건 × 반복"),
           ("실제 실행", fmt.num(sum(1 for s in slots if s.get("selected_run_id"))), "실제 Codex 실행"),
           ("독립 판정", fmt.num(len(judged)), f"결측·미판정 {len(slots) - len(judged)}건")]
    if export.get("conditions") is None:
        return out + [("결과", "미확정", export.get("status_label") or "")]
    tokens = [s.get("model_tokens") for s in slots if fmt.is_num(s.get("model_tokens"))]
    runtime = [s.get("runtime_ms") for s in slots if fmt.is_num(s.get("runtime_ms"))]
    return out + [("성공 / 실패", f"{ok} / {len(judged) - ok}", "독립 판정 기준"),
                  ("복구 전달", fmt.num(sum(s.get("recoveries_delivered") or 0 for s in slots)), "B–E 개입 후 후속 턴"),
                  ("모델 토큰", fmt.num(sum(tokens)) if tokens else "N/A", "관측값 (observe_only)"),
                  ("실행 시간 합계", f"{sum(runtime) / 60000:.1f}분" if runtime else "N/A", "슬롯 실행 시간 합")]


def settings(export: dict) -> list[tuple[str, str]]:
    m = export.get("meta") or {}
    model, budget = m.get("model") or {}, m.get("budget") or {}
    return [("모델 / runtime", " · ".join(str(x) for x in (model.get("model_id"), f"추론 {model.get('reasoning_effort')}",
                                                         f"{model.get('adapter')} {model.get('runtime_version')}",
                                                         model.get("session_mode")))),
            ("프로토콜 / 반복", f"{m.get('protocol')} / {m.get('repetitions')}회"),
            ("턴 상한", f"총 {fmt.num(budget.get('max_total_turns'))} (최초 {fmt.num(budget.get('max_initial_turns'))} + 복구 "
                       f"{fmt.num(budget.get('max_recovery_turns'))}), 슬롯당 복구 {fmt.num(budget.get('recovery_turns_per_eligible_slot'))}회"),
            ("실행 한도", f"실행당 {fmt.secs(budget.get('wall_ms_per_run'))} · 턴당 {fmt.secs(budget.get('native_turn_ms'))}"),
            ("보조 정보 상한", f"{fmt.num(budget.get('supplement_cap'))} 추정 토큰 (B–E 공통)"),
            ("토큰 한도 방식", f"{budget.get('token_limit_mode')} (관측만, 강제 아님)"),
            ("코드 버전", fmt.short(m.get("code_version"), 64))]


# ---------------------------------------------------------------- comparison

def condition_rows(export: dict) -> list[dict] | None:
    cm = export.get("conditions")
    if cm is None:
        return None
    slots = export.get("slots") or []
    rows = []
    for c in CONDITIONS:
        m = cm.get(c)
        if not m:
            continue
        rows.append({"조건": COND_LABEL[c], "계획": m["planned"], "유효": m["valid"], "성공": m["successes"],
                     "실패": m["failures"], "결측": m["missing"], "과제 평균 성공률": fmt.pct(m["tsr_task_mean"]),
                     "모델 토큰": fmt.num(m["model_tokens_total"]),
                     "성공당 토큰": fmt.num(m["cps_tokens"]) if m["cps_tokens"] is not None else f"정의 안 됨 ({m['cps_reason']})",
                     "평균 실행 시간": fmt.secs(m["mean_runtime_ms"]),
                     "복구 전달": sum(s.get("recoveries_delivered") or 0 for s in slots if s["condition"] == c)})
    return rows


def contrast(export: dict, cid: str) -> dict | None:
    return next((c for c in export.get("contrasts") or [] if c["contrast_id"] == cid), None)


def _effect(c):
    if c is None:
        return "N/A"
    return fmt.pp(c["effect"]) if c["unit"] == "pp" else (f"{fmt.signed_tokens(c['effect'])} 토큰" if c["effect"] is not None else "정의 안 됨")


def _interval(c):
    if c is None:
        return "N/A"
    if c.get("ci_lower") is None:
        return f"구간 없음 ({c.get('interpretation_status')})"
    if c["unit"] == "pp":
        return f"[{fmt.pp(c['ci_lower'])}, {fmt.pp(c['ci_upper'])}]"
    return f"[{fmt.signed_tokens(c['ci_lower'])}, {fmt.signed_tokens(c['ci_upper'])}]"


def rq_rows(export: dict) -> list[dict]:
    rows = [("RQ1 · DCC는 성공당 비용을 줄이는가", "P-R1 = CPS(C) − CPS(B)", "P-R1", "결론 불충분 — 구간이 0을 포함"),
            ("RQ2 · 그래프 복구가 일반 재시도보다 나은가", "P-R2 공통 실패 체크포인트 복구율", None, "NOT_RUN — 측정하지 않음 (AMD-01 §7.2)"),
            ("RQ3 · 통합 정책의 추가 이득", "P-R3 = E − max(C, D)", "P-R3", "결론 불충분 — 구간 방법 미승인"),
            ("RQ4 · 구성 요소별 가치", "AB1–AB4", None, "NOT_RUN — Emergency 생략"),
            ("참고 · 전체 시스템 대 기본 Codex", "E − A", "E-A", "결론 불충분"),
            ("참고 · 2×2 DCC 주효과", "CTX_MAIN", "CTX_MAIN", "결론 불충분"),
            ("참고 · 2×2 Graph 주효과", "GRAPH_MAIN", "GRAPH_MAIN", "결론 불충분 — 아래 해석 주의 참고")]
    out = []
    for question, label, cid, judgement in rows:
        c = contrast(export, cid) if cid else None
        out.append({"연구 질문": question, "사전 지정 대비": label, "점추정": _effect(c) if cid else "미측정",
                    "95% 구간": _interval(c) if cid else "N/A", "판단": judgement})
    return out


def interpretation(export: dict) -> str | None:
    d = export.get("derived") or {}
    if not d or export.get("conditions") is None:
        return None
    gu = d.get("graph_use") or {}
    pairs = d.get("identical_input_pairs") or {}
    parity = d.get("parity") or {}
    typed = sum(v for k, v in gu.items() if not k.startswith("GENERIC_RETRY"))
    text = (f"그래프 진단은 {sum(gu.values())}회 호출됐고, 유형별 전략(RELOCALIZE 등)을 낸 경우는 {typed}회다. "
            f"B/D와 C/E의 첫 턴 메시지는 {parity.get('B_equals_D')}/{parity.get('tasks')}, {parity.get('C_equals_E')}/{parity.get('tasks')} "
            f"과제에서 바이트 단위로 같았다. 입력이 같은 {pairs.get('n')}쌍 중 첫 턴 공개 검사 결과가 갈린 쌍은 {pairs.get('first_turn_diverged')}개, "
            f"최종 판정이 갈린 쌍은 {pairs.get('verdict_diverged')}개다.")
    for p in pairs.get("verdict_diverged_all_messages_identical") or []:
        text += (f" {p['task_id']}의 {'·'.join(p['pair'])}는 {p['turns'][0]}개 턴 모두 바이트 동일한 메시지를 받았지만 판정이 "
                 f"{' / '.join(p['verdict'])}로 갈렸다.")
    return text + " 1회 반복 설계에서는 모델 비결정성이 조건 간 차이와 같은 크기로 나타난다. (사후 기술 분석, 사전 등록 아님)"


def matrix_rows(export: dict) -> list[dict]:
    out = []
    for s in export.get("slots") or []:
        if s.get("y") is None:
            label, outcome = "결측·미판정", "PENDING"
        else:
            ok = s["y"] == 1
            note = " NSC" if s.get("stop_reason") == "NO_SAFE_CONTINUATION" else ""
            note += " †" if not ok and s.get("final_public") == "PASS" else ""
            label, outcome = f"{'✓ S' if ok else '✗ F'} ({s.get('turns')}턴){note}", "PASS" if ok else "FAIL"
        out.append({"과제": s["task_id"], "조건": COND_LABEL.get(s["condition"], s["condition"]), "결과": outcome,
                    "표시": label, "턴": s.get("turns"), "종료": s.get("stop_reason"), "토큰": s.get("model_tokens"),
                    "run_id": s.get("selected_run_id")})
    return out


# ---------------------------------------------------------------- run views

def tasks(export: dict) -> list[str]:
    return sorted((export.get("tasks") or {}).keys())


def slot_run(export: dict, task: str, cond: str) -> dict | None:
    s = next((x for x in export.get("slots") or [] if x["task_id"] == task and x["condition"] == cond), None)
    return (export.get("runs") or {}).get(s["selected_run_id"]) if s and s.get("selected_run_id") else None


def policy_text(p: dict | None) -> str:
    if not p:
        return "N/A"
    return (f"초기 {p.get('initial_context')} · 진단 {p.get('diagnosis')} · 전략 {p.get('strategy')} · 복구 "
            f"{p.get('recovery_context')} · 외부 개입 {'있음' if p.get('external_intervention') else '없음'}")


def run_summary(run: dict) -> list[tuple[str, str]]:
    r, v = run.get("report") or {}, run.get("verdict")
    return [("run_id", f"{run.get('run_id')} · {run.get('slot_id') or 'S3 합성'}"),
            ("과제 · 조건", f"{run.get('task_id')} · {COND_LABEL.get(run.get('condition'), run.get('condition'))}"),
            ("정책 (manifest)", policy_text(run.get("policy"))),
            ("종료", f"{r.get('stop_reason')} · 턴 {r.get('turns_used')} · 복구 {r.get('recoveries_delivered')} · {fmt.secs(r.get('elapsed_ms'))}"),
            ("공개 검사 (1턴 → 최종)", f"{run.get('first_turn_public') or '기록 없음'} → {r.get('public_check_status') or '기록 없음'}"),
            ("독립 판정", (f"{v.get('status')} (기능 {v.get('functional_status')} · 회귀 {v.get('regression_status')} · 지침 "
                          f"{v.get('instruction_status')}{' · ' + v['error_code'] if v.get('error_code') else ''})") if v else "독립 판정 없음 (합성 검증)"),
            ("변경 파일", ", ".join(r.get("changed_paths") or []) or "없음 (빈 패치)"),
            ("모델 토큰", fmt.num(r.get("observed_model_tokens"))),
            ("경고", ", ".join(r.get("warnings") or []) or "없음")]


def timeline_rows(run: dict) -> list[dict]:
    tr = run.get("transitions") or []
    if not tr:
        return []
    total = max((run.get("report") or {}).get("elapsed_ms") or 0, tr[-1].get("elapsed_ms") or 0)
    rows = []
    for i, t in enumerate(tr):
        start = t.get("elapsed_ms") or 0
        end = tr[i + 1].get("elapsed_ms") or start if i + 1 < len(tr) else total
        rows.append({"단계": t.get("to"), "구분": PHASE_GROUP.get(t.get("to"), "시작·종료"), "시작(초)": start / 1000,
                     "끝(초)": max(end, start) / 1000, "사유": t.get("reason"), "개입": t.get("intervention_id") or ""})
    return rows


def _seen(value) -> str:
    return "미관측" if value is None else str(value)


def event_detail(ev: dict) -> str:
    kind = ev.get("kind")
    if kind == "COMMAND":
        return f"명령 시작 {_seen(ev.get('command_id'))} ({_seen(ev.get('state'))})"
    if kind == "COMMAND_RESULT":
        return f"명령 종료 {_seen(ev.get('command_id'))} · exit {_seen(ev.get('exit_code'))}"
    if kind == "TEST":
        return f"공개 검사 {_seen(ev.get('check_id'))} → {_seen(ev.get('result'))}"
    if kind == "FAILURE":
        return (f"실패 {_seen(ev.get('error_type'))} · {_seen(ev.get('message'))}"
                + ("" if ev.get("frame_present") else " (traceback frame 없음)"))
    if kind == "CHANGE":
        return "파일 변경 " + (", ".join(f"{h.get('path')} −{h.get('old_count')}/+{h.get('new_count')}" for h in ev.get("hunks") or []) or "미관측")
    if kind == "COMPLETION":
        return f"완료 선언 {_seen(ev.get('declared'))}"
    if kind == "INTERVENTION":
        return (f"개입 {_seen(ev.get('intervention_id'))} · {','.join(ev.get('trigger_ids') or []) or '미관측'} → "
                f"{_seen(ev.get('strategy_id'))} ({_seen(ev.get('delivery_status'))})")
    if kind == "USAGE":
        return f"사용량 기록 {_seen(ev.get('usage_id'))}"
    if kind == "UNKNOWN":
        return f"미분류 이벤트 {_seen(ev.get('provider_type'))} — 원문은 제한 자료라 공개 데이터에 없음"
    return _seen(kind)


def event_rows(run: dict) -> list[dict]:
    return [{"#": ev.get("seq"), "턴": fmt.turn(ev.get("turn")), "UTC 시각": (ev.get("at") or "")[11:23], "종류": ev.get("kind"),
             "내용": event_detail(ev), "관측": ev.get("observation")} for ev in run.get("events") or []]


def check_rows(run: dict) -> list[dict]:
    return [{"시점": fmt.turn(c.get("turn")), "검사": c.get("check_id"), "결과": c.get("status"),
             "시간(ms)": c.get("duration_ms"), "파서": c.get("parser_id")} for c in run.get("checks") or []]


def usage_rows(run: dict) -> list[dict]:
    return [{"턴": fmt.turn(u.get("turn")), "단계": u.get("stage"), "입력": u.get("input_tokens"), "캐시 입력": u.get("cached_input_tokens"),
             "출력": u.get("output_tokens"), "합계": u.get("total_tokens"), "턴 시간": fmt.secs(u.get("elapsed_ms"))}
            for u in run.get("usage") or []]


def overhead_rows(run: dict) -> list[dict]:
    return [{"단계": o.get("stage"), "턴": fmt.turn(o.get("turn")), "시간(ms)": o.get("elapsed_ms")} for o in run.get("overhead") or []]


def intervention_kv(iv: dict) -> list[tuple[str, str]]:
    g = iv.get("graph")
    d = iv.get("directive") or {}
    o = iv.get("outcome")
    return [("개입", f"{iv.get('intervention_id')} · " + " → ".join(e["event"] for e in iv.get("events") or [])),
            ("트리거", ", ".join(f"{t} ({TRIGGER.get(t, '?')})" for t in iv.get("trigger_ids") or [])),
            ("전략", f"{iv.get('strategy')} — {STRATEGY.get(iv.get('strategy'), '')}"),
            ("진단", f"GRAPH · 상태 {g.get('status')} · 대체 사유 {g.get('fallback_reason') or '없음'} · 구축 {g.get('build_ms')}ms" if g else "FLAT (그래프 미호출)"),
            ("지시 목표", d.get("objective") or "N/A"),
            ("후속 관측", f"턴 상태 {o.get('turn_status')} · 지시 수행 {o.get('adherence_status')} ({o.get('adherence_basis')})" if o else "없음")]


# ---------------------------------------------------------------- graph

def graph_entries(export: dict | None, synthetic: list[dict]) -> list[dict]:
    out = []
    sources = ([(export, export.get("label") or export["experiment_id"], False)] if export else []) + \
              [(s, s.get("label"), True) for s in synthetic]
    for source, group, is_synth in sources:
        for run in (source.get("runs") or {}).values():
            for iv in run.get("interventions") or []:
                if iv.get("graph_detail"):
                    out.append({"key": f"{'synthetic' if is_synth else source['experiment_id']}|{run['run_id']}|{iv['intervention_id']}",
                                "group": group, "synthetic": is_synth, "run": run, "iv": iv,
                                "title": f"{run.get('scenario') or run.get('task_id')} · {run.get('condition')} · {iv['intervention_id']} → {iv.get('strategy')}"})
    return out


def _dot_escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def graph_model(entry: dict) -> dict:
    gd, run, iv = entry["iv"]["graph_detail"], entry["run"], entry["iv"]
    tr = gd.get("trace") or {}
    nodes, edges = gd.get("nodes") or [], gd.get("edges") or []
    label = {n["id"]: n["label"] for n in nodes}
    anchors, cands = set(tr.get("anchor_ids") or []), {c["id"] for c in tr.get("candidates") or []}
    path_edges = {e for p in tr.get("paths") or [] for e in p.get("edges") or []}
    cross = sum(1 for e in edges if e.get("cross"))
    repo_n = sum(1 for n in nodes if n.get("namespace") == "repo")
    reasons: dict[str, int] = {}
    for u in gd.get("unresolved") or []:
        key = f"{u.get('relation')}: {u.get('reason')}"
        reasons[key] = reasons.get(key, 0) + 1
    parsers = sorted({c.get("parser_id") for c in run.get("checks") or [] if c.get("parser_id")})
    fails = [c for chk in run.get("checks") or [] for c in chk.get("cases") or [] if c.get("status") == "FAIL"]
    if cross == 0:
        explain = ("warning", f"교차 연결 0개 — 원인 후보를 만들 근거가 없었다. 미해결 기록: "
                   f"{', '.join(f'{k} {v}건' for k, v in reasons.items()) or '없음'}. 공개 검사 파서 {', '.join(parsers) or 'N/A'}의 실패 사례 "
                   f"{len(fails)}건 중 traceback frame이 있는 사례는 {sum(1 for c in fails if c.get('frame_present'))}건이고, 테스트 ID는 "
                   f"{', '.join(sorted({str(c.get('test_id')) for c in fails})) or 'N/A'}다. 매핑 규칙은 근거 없는 TESTS_TARGET·FAILS_AT를 만들지 않으므로 "
                   f"앵커에서 코드 노드로 가는 경로가 없고, 정책은 {iv.get('graph', {}).get('fallback_reason') or '일반 재시도'}로 {iv.get('strategy')}를 선택했다. "
                   "개선 방향: 공개 검사 결과를 테스트 함수·frame 단위로 기록 (최종 발표 전 고도화 항목).")
    else:
        explain = ("success", f"교차 연결 {cross}개. 실패 앵커에서 근거 있는 연결을 따라 코드 노드에 도달했고, 후보 "
                   f"{len(tr.get('candidates') or [])}개와 경로 {len(tr.get('paths') or [])}개를 기록했다. 후보는 원인 확정이 아닌 가설(inferred)이다.")
    lines = ['digraph G {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.1; ranksep=0.45; compound=true;',
             'node [shape=box, style="rounded,filled", fillcolor="white", fontname="sans-serif", fontsize=13, margin="0.12,0.04"];',
             'edge [fontname="sans-serif", fontsize=11, color="#9c9c9c", fontcolor="#666666", arrowsize=0.6];']
    for ns, title, color in (("repo", "Repository Graph (코드 구조, AST)", "#257de0"), ("exec", "Execution Graph (관측된 실행·검사)", "#f47230")):
        lines.append(f'subgraph cluster_{ns} {{ label="{title}"; color="{color}"; fontname="sans-serif"; fontsize=11;')
        for n in nodes:
            if (n.get("namespace") == "repo") != (ns == "repo"):
                continue
            tag = ("앵커 " if n["id"] in anchors else "") + ("후보 " if n["id"] in cands else "")
            text = n["label"] if len(n["label"]) <= 30 else n["label"][:29] + "…"
            style = f'color="{color}"'
            if n["id"] in cands:
                style = 'color="#00b894", fillcolor="#e6f8f4", penwidth=2.5'
            if n["id"] in anchors:
                style += ", penwidth=3"
            lines.append(f'"{n["id"]}" [label="{_dot_escape(tag + n["kind"])}\\n{_dot_escape(text)}", {style}, tooltip="{_dot_escape(n["label"])}"];')
        lines.append("}")
    for e in edges:  # only cross-graph and search-path edges carry a printed label; every edge has a tooltip
        attrs = f'tooltip="{_dot_escape(e["relation"])}"'
        if e["id"] in path_edges:
            attrs += f', label="{_dot_escape(e["relation"])}", color="#00b894", penwidth=3'
        elif e.get("cross"):
            attrs += f', label="{_dot_escape(e["relation"])}", color="#00b894", penwidth=2'
        lines.append(f'"{e["source"]}" -> "{e["target"]}" [{attrs}];')
    lines.append("}")
    return {
        "tiles": [("그래프 상태", gd.get("status") or "N/A", f"builder {gd.get('builder_version') or ''}"),
                  ("노드", fmt.num(len(nodes)), f"코드 {repo_n} · 실행 {len(nodes) - repo_n}"),
                  ("엣지", fmt.num(len(edges)), f"교차 연결 {cross}개"),
                  ("미해결 연결", fmt.num(len(gd.get("unresolved") or [])), "근거 없는 연결은 만들지 않음"),
                  ("탐색", f"{tr.get('visited_count')} 방문", f"{tr.get('termination')} · truncated={str(tr.get('truncated')).lower()}"),
                  ("원인 후보", fmt.num(len(tr.get("candidates") or [])), f"top-k {tr.get('top_k')}"),
                  ("선택 전략", STRATEGY_SHORT.get(iv.get("strategy"), iv.get("strategy") or "N/A"),
                   f"{iv.get('strategy')} · {(iv.get('graph') or {}).get('fallback_reason') or '유형별 근거 있음'}")],
        "explain": explain, "dot": "\n".join(lines), "cross": cross,
        "candidates": [{"순위": c.get("rank"), "종류": c.get("kind"),
                        "위치": (c.get("path") or "") + (f"::{c['qualified_name']}" if c.get("qualified_name") else ""),
                        "점수": round(c["score"], 3) if fmt.is_num(c.get("score")) else None,
                        "구성": " · ".join(f"{k} {v}" for k, v in (c.get("components") or {}).items() if k != "variant"),
                        "관계": "→".join(c.get("relations") or []),
                        "관측": (c.get("observation") or "") + (" · 대안" if c.get("alternative") else "")} for c in tr.get("candidates") or []],
        "paths": [" → ".join(label.get(n, n) + (f" —{p['relations'][i]}({(p.get('directions') or [''] * 99)[i]})" if i < len(p.get("relations") or []) else "")
                              for i, n in enumerate(p.get("nodes") or [])) + f" · 비용 {p.get('cost')}" for p in tr.get("paths") or []],
        "unresolved": [{"관계": u.get("relation"), "사유": u.get("reason"), "노드": label.get(u.get("node"), u.get("node"))} for u in gd.get("unresolved") or []],
        "edges": [{"관계": e.get("relation"), "출발": label.get(e["source"], e["source"]), "도착": label.get(e["target"], e["target"]),
                   "관측": e.get("observation"), "근거": e.get("basis"), "교차": "예" if e.get("cross") else ""} for e in edges],
        "warnings": gd.get("warnings") or [],
        "limits": f"깊이 {tr.get('depth_limit')} · 방문 {tr.get('visit_limit')} · 분기 {tr.get('branch_limit')} · top-k {tr.get('top_k')}",
    }


# ---------------------------------------------------------------- context

def context_packages(run: dict) -> list[dict]:
    out = []
    for pkg in run.get("context") or []:
        used, cap = pkg.get("estimated_supplement_tokens"), pkg.get("cap_tokens")
        ratio = min(used / cap, 1.0) if fmt.is_num(used) and fmt.is_num(cap) and cap else 0.0
        dcc = pkg.get("policy") == "DCC"
        note = {"NONE": "조건 A는 보조 영역이 비어 있다. 사용자 요청과 필수 지침만 같은 경로로 전달한다 (CT07).",
                "DEFAULT": "DEFAULT는 공통 최소 근거를 그대로 담는다. DCC 전용 점수·중복 제거·한계 효용 패킹을 쓰지 않으므로 점수 열은 비어 있다."}.get(
            pkg.get("policy"), "DCC는 후보마다 관련성(BM25)·유효성·범위·실패 연관성 점수를 매기고 예산 안에서 한계 효용이 양수인 항목만 담는다. "
                               "필수 근거(CORE_REQUIRED)는 점수 없이 유지하고 중복(MERGED_DUPLICATE)은 합친다.")
        rows = []
        for d in pkg.get("decisions") or []:
            c = d.get("components") or {}
            scored = dcc and d.get("reason_code") not in ("CORE_REQUIRED", "MERGED_DUPLICATE")
            rows.append({"선택": "선택" if d.get("selected") else "제외", "사유": d.get("reason_code"), "종류": d.get("kind"),
                         "출처": (d.get("source") or "") + (f" L{d['span'][0]}–{d['span'][1]}" if d.get("span") else ""),
                         "근거": f"{d.get('provenance')} · {d.get('validity')}",
                         "점수": round(d["score"], 3) if scored and fmt.is_num(d.get("score")) else None,
                         "관련성": round(c["relevance"], 2) if scored and fmt.is_num(c.get("relevance")) else None,
                         "유효성": round(c["validity"], 2) if scored and fmt.is_num(c.get("validity")) else None,
                         "범위": round(c["scope"], 2) if scored and fmt.is_num(c.get("scope")) else None,
                         "실패": round(c["failure"], 2) if scored and fmt.is_num(c.get("failure")) else None,
                         "추정 토큰": d.get("estimated_tokens"),
                         "효용 밀도": round(d["utility_density"], 4) if scored and fmt.is_num(d.get("utility_density")) else None})
        out.append({"title": f"{pkg.get('stage')} 패키지 · {pkg.get('policy')}" + (f" · {pkg['strategy_id']}" if pkg.get("strategy_id") else ""),
                    "note": note, "ratio": ratio,
                    "tiles": [("보조 정보 추정 토큰", fmt.num(used), f"상한 {fmt.num(cap)} ({fmt.pct(ratio)})"),
                              ("전체 메시지 추정 토큰", fmt.num(pkg.get("estimated_full_tokens")), pkg.get("estimator_id") or ""),
                              ("선택 / 후보", f"{sum(1 for d in pkg.get('decisions') or [] if d.get('selected'))} / {len(pkg.get('decisions') or [])}",
                               f"핵심 근거 {pkg.get('core_count')}"),
                              ("구성 시간", f"{pkg.get('elapsed_ms')}ms", "로컬 처리, 전체 비용에 포함")],
                    "kv": [("상태 · 경고", f"{pkg.get('status')} · {', '.join(pkg.get('warnings') or []) or '없음'}"),
                           ("검색 방식", pkg.get("retrieval_version") or "N/A"), ("package_hash", pkg.get("package_hash") or "N/A"),
                           ("message_hash", pkg.get("message_hash") or "N/A")],
                    "rows": rows})
    return out


def context_comparison(export: dict, task: str) -> list[dict]:
    rows = {}
    for c in CONDITIONS:
        run = slot_run(export, task, c)
        pkg = next((p for p in (run or {}).get("context") or [] if p.get("stage") == "INITIAL"), None)
        rows[c] = {"조건": COND_LABEL[c], "정책": pkg.get("policy") if pkg else "N/A",
                   "보조 정보 추정 토큰": pkg.get("estimated_supplement_tokens") if pkg else None,
                   "선택 / 후보": f"{sum(1 for d in pkg.get('decisions') or [] if d.get('selected'))} / {len(pkg.get('decisions') or [])}" if pkg else "N/A",
                   "최초 메시지 해시": fmt.short(((run or {}).get("dispatch") or [{}])[0].get("message_hash"), 16), "동일성": ""}
    for a, b in (("B", "D"), ("C", "E")):
        same = rows[a]["최초 메시지 해시"] != "N/A" and rows[a]["최초 메시지 해시"] == rows[b]["최초 메시지 해시"]
        rows[b]["동일성"] = f"{a}와 바이트 동일" if same else f"{a}와 다름"
    return list(rows.values())


# ---------------------------------------------------------------- replay

def replay_cases(export: dict | None, synthetic: list[dict]) -> list[dict]:
    out = []
    if export:
        name = export.get("label") or export["experiment_id"]
        for s in export.get("slots") or []:
            run = (export.get("runs") or {}).get(s.get("selected_run_id"))
            if not run:
                continue
            if run.get("interventions"):
                group = f"{name} — 개입 발생"
            elif s.get("y") == 0:
                group = f"{name} — 개입 없이 실패"
            else:
                continue
            out.append({"key": f"{export['experiment_id']}|{run['run_id']}", "group": group, "run": run, "synthetic": False,
                        "title": f"{s['task_id']} · {s['condition']} → {s.get('evaluation_status') or 'N/A'}"})
    for suite in synthetic:
        for run in (suite.get("runs") or {}).values():
            if run.get("interventions"):
                out.append({"key": f"synthetic|{run['run_id']}", "group": suite.get("label"), "run": run, "synthetic": True,
                            "title": f"{run.get('scenario') or run.get('task_id')} · {run.get('condition')}"})
    return out


def _turn_summary(run: dict, turn_id: str) -> list[tuple[str, str]]:
    events = [e for e in run.get("events") or [] if e.get("turn") == turn_id]
    cmds = [e for e in events if e.get("kind") == "COMMAND_RESULT"]
    changes = [h.get("path") for e in events if e.get("kind") == "CHANGE" for h in e.get("hunks") or []]
    return [("명령", f"{len(cmds)}개 · exit 0 {sum(1 for e in cmds if e.get('exit_code') == 0)}개"),
            ("파일 변경", ", ".join(dict.fromkeys(changes)) or "없음"), ("이벤트", f"{len(events)}개 (원문은 제한 자료)")]


def replay_step(case: dict, step: int) -> list[tuple]:
    """Blocks: ('text', str) | ('kv', [(k, v)]) | ('table', rows) | ('callout', level, str)."""
    run = case["run"]
    iv = (run.get("interventions") or [None])[0]
    policy = run.get("policy") or {}
    checks = run.get("checks") or []
    if step == 0:
        return [("text", "첫 턴이 끝난 뒤 하네스가 공개 검사를 직접 실행했다."),
                ("table", [{"검사": c.get("check_id"), "결과": c.get("status"),
                            "오류": ", ".join(x.get("error_type") or "" for x in c.get("cases") or [] if x.get("error_type")) or "—"}
                           for c in checks if c.get("turn") == "turn-0001"]),
                ("kv", _turn_summary(run, "turn-0001"))]
    if step == 1:
        if not iv:
            if policy.get("external_intervention") is False:
                return [("callout", "info", f"조건 A(Vanilla)는 외부 개입이 없도록 설계됐다. 1턴 공개 검사가 {run.get('first_turn_public')}여도 복구를 전달하지 않는다.")]
            return [("callout", "info", f"공통 감지 규칙이 개입 트리거를 내지 않았다(1턴 공개 검사 {run.get('first_turn_public')}). 공개 검사가 놓친 요구는 하네스가 알 수 없다.")]
        return [("kv", [("트리거", " / ".join(f"{t} — {TRIGGER.get(t, '?')}" for t in iv.get("trigger_ids") or [])),
                        ("대상 검사", ", ".join(iv.get("check_ids") or [])), ("앵커", f"{iv.get('anchor_count')}개 (공개 검사 결과 ID)"),
                        ("체크포인트 키", f"{iv.get('checkpoint')}… (같은 체크포인트 중복 개입 금지)")]),
                ("text", "B–E는 같은 감지 규칙·앵커·개입 상한을 쓴다 (CT04).")]
    if step == 2:
        if not iv:
            return [("text", "개입이 없어 진단 단계가 없다.")]
        if not iv.get("graph"):
            return [("callout", "info", f"조건 {run.get('condition')}는 FLAT 진단을 쓴다(설계상 그래프 미호출, CT05). 실패 증거 목록을 그대로 전략 선택에 넘긴다.")]
        gd = iv.get("graph_detail") or {}
        tr = gd.get("trace") or {}
        cross = sum(1 for e in gd.get("edges") or [] if e.get("cross"))
        return [("kv", [("그래프", f"상태 {iv['graph'].get('status')} · 노드 {len(gd.get('nodes') or [])} · 엣지 {len(gd.get('edges') or [])} · 교차 연결 {cross}"),
                        ("탐색", f"방문 {tr.get('visited_count')} · 종료 {tr.get('termination')} · 후보 {len(tr.get('candidates') or [])} · 경로 {len(tr.get('paths') or [])}"),
                        ("대체 사유", iv["graph"].get("fallback_reason") or "없음"), ("시간", f"{iv['graph'].get('build_ms')}ms (구축)")]),
                ("callout", "warning", "교차 연결이 없어 앵커에서 코드 후보에 도달하지 못했다. 근거 없는 후보를 만들지 않는 것이 설계 규칙이다.") if cross == 0 else
                ("callout", "success", f"근거 있는 연결로 코드 후보 {len(tr.get('candidates') or [])}개에 도달했다. 후보는 가설이다.")]
    if step == 3:
        if not iv:
            return [("text", f"개입이 없어 전략 선택이 없다. 종료 사유: {(run.get('report') or {}).get('stop_reason')}")]
        d = iv.get("directive") or {}
        return [("kv", [("전략", f"{iv.get('strategy')} — {STRATEGY.get(iv.get('strategy'), '')}"), ("지시 목표", d.get("objective") or "N/A"),
                        ("근거 상태", f"{d.get('confidence_status')} · 사유 {d.get('reason')}"), ("대상", ", ".join(iv.get("targets") or []) or "없음"),
                        ("다음 검사", ", ".join(d.get("next_checks") or []))])]
    if step == 4:
        pkg = next((p for p in context_packages(run) if p["title"].startswith("RECOVERY")), None)
        if not pkg:
            return [("text", "복구 컨텍스트 패키지가 없다 (개입 없음).")]
        return [("kv", [(t[0], f"{t[1]} ({t[2]})") for t in pkg["tiles"]]), ("table", pkg["rows"])]
    if step == 5:
        if not iv:
            return [("text", "후속 턴이 없다.")]
        out = [("text", "복구 패키지를 같은 세션의 다음 턴으로 전달했다 (" + " → ".join(e["event"] for e in iv.get("events") or []) + ")."),
               ("kv", _turn_summary(run, "turn-0002")),
               ("table", [{"검사 (2턴)": c.get("check_id"), "결과": c.get("status")} for c in checks if c.get("turn") == "turn-0002"])]
        if iv.get("outcome"):
            out.append(("kv", [("지시 수행 판정", f"{iv['outcome'].get('adherence_status')} ({iv['outcome'].get('adherence_basis')})")]))
        return out
    r, v = run.get("report") or {}, run.get("verdict")
    out = [("kv", [("종료 사유", r.get("stop_reason")), ("턴 · 복구", f"{r.get('turns_used')} · {r.get('recoveries_delivered')}"),
                   ("최종 공개 검사", r.get("public_check_status")),
                   ("독립 판정", f"{v.get('status')} (기능 {v.get('functional_status')} · 회귀 {v.get('regression_status')} · 지침 {v.get('instruction_status')})" if v else "독립 판정 없음 (합성 검증)"),
                   ("변경 파일", ", ".join(r.get("changed_paths") or []) or "없음 (빈 패치)"),
                   ("모델 토큰 · 시간", f"{fmt.num(r.get('observed_model_tokens'))} · {fmt.secs(r.get('elapsed_ms'))}")])]
    if r.get("stop_reason") == "NO_SAFE_CONTINUATION":
        out.append(("callout", "warning", "후속 턴 뒤 snapshot이 바뀌지 않아 새 관측이 없었다. 같은 체크포인트에 다시 개입하지 않는 규칙에 따라 안전 종료했다."))
    return out


# ---------------------------------------------------------------- audit

def isolation_rows(export: dict) -> tuple[list[dict], list[dict]]:
    d = export.get("derived") or {}
    p = d.get("parity") or {}
    n = p.get("tasks")
    parity = [{"확인 항목": "B/D 최초 메시지 바이트 동일 (CT03)", "결과": f"{p.get('B_equals_D')} / {n} 과제"},
              {"확인 항목": "C/E 최초 메시지 바이트 동일 (CT03)", "결과": f"{p.get('C_equals_E')} / {n} 과제"},
              {"확인 항목": "B와 C는 다른 패키지 (DEFAULT ≠ DCC)", "결과": f"{(n or 0) - (p.get('B_equals_C') or 0)} / {n} 과제에서 다름"},
              {"확인 항목": "필수 지침 해시가 A–E에서 동일", "결과": f"{p.get('fixed_instruction_same_A_to_E')} / {n} 과제"}]
    iso = [{"조건": COND_LABEL[c], "실행": (d.get("isolation") or {}).get(c, {}).get("runs"),
            "그래프 진단": (d.get("isolation") or {}).get(c, {}).get("graph_diagnoses"),
            "FLAT 진단": (d.get("isolation") or {}).get(c, {}).get("flat_diagnoses"),
            "컨텍스트 패키지": ", ".join(f"{k} ×{v}" for k, v in ((d.get("isolation") or {}).get(c, {}).get("context") or {}).items())}
           for c in CONDITIONS]
    return parity, iso


def command_rows(exports: list[dict]) -> list[dict]:
    rows = []
    for e in exports:
        cmd = (e.get("derived") or {}).get("commands") or {}
        rows.append({"실험": e["experiment_id"], "상태": e.get("status_label"), "에이전트 명령": cmd.get("total", 0),
                     "exit 0": cmd.get("exit_0", 0), "exit ≠ 0": cmd.get("exit_nonzero", 0),
                     "빈 패치 실패": (e.get("derived") or {}).get("empty_patch_failures"),
                     "성공 / 판정": f"{sum(1 for s in e.get('slots') or [] if s.get('y') == 1)} / {sum(1 for s in e.get('slots') or [] if s.get('y') is not None)}"})
    return rows


def hash_chain(export: dict) -> list[tuple[str, str]]:
    m = export.get("meta") or {}
    fr, ap, an = m.get("freeze") or {}, m.get("approval") or {}, m.get("analysis") or {}
    match = "일치" if ap.get("approved_freeze_sha256") and ap.get("approved_freeze_sha256") == fr.get("sha256") else "확인 불가"
    return [("동결 manifest", f"{fr.get('path') or 'N/A'} · {fr.get('sha256') or 'N/A'}"),
            ("승인 기록", f"{ap.get('path') or 'N/A'} · {ap.get('sha256') or 'N/A'} · {ap.get('approved_at') or ''}"),
            ("승인된 동결 해시", f"{ap.get('approved_freeze_sha256') or 'N/A'} ({match})"),
            ("편입 기록", fr.get("inclusion_sha256") or "N/A"), ("analysis.json", an.get("analysis_json_sha256") or "N/A"),
            ("분석 설정", an.get("config_sha256") or "N/A"),
            ("대체한 실험", str(ap.get("supersedes") or "없음"))]
