"""SVG figures for the submission, drawn from the public bundle only (stdlib, deterministic).

Every number printed in a figure is read from the bundle that ``submission_bundle`` builds from the
frozen analysis outputs and run records; nothing is typed in by hand. Marks carry ``<title>`` and
``data-tip`` so the dashboard can show the same value on hover; values are also printed as labels.
Palette: the validated reference palette (dataviz skill), light and dark steps.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

FONT = 'system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif'
STYLE = """<style>
.bg{fill:#ffffff}.t1{fill:#191919}.t2{fill:#666666}.grid{stroke:#e7e7e7}.base{stroke:#c2c2c2}
.s1{fill:#257de0}.s1l{stroke:#257de0}.s2{fill:#f47230}.s2l{stroke:#f47230}.s3{fill:#00b894}.s3l{stroke:#00b894}
.mute{fill:#c2c2c2}.mutel{stroke:#9c9c9c}.good{fill:#0aac23}.crit{fill:#dc3c3d}.warnf{fill:#fbb315}
.panel{fill:#f9f9f9;stroke:rgba(11,11,11,0.10)}.ring{stroke:#ffffff}.inv{fill:#ffffff}.dark{fill:#0b0b0b}
.arrow{stroke:#666666;fill:none}.arrowhead{fill:#666666}.accent{stroke:#257de0;fill:none}
text{font-family:system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
@media (prefers-color-scheme:dark){.bg{fill:#191919}.t1{fill:#efefef}.t2{fill:#9c9c9c}.grid{stroke:#303030}
.base{stroke:#474747}.s1{fill:#338ced}.s1l{stroke:#338ced}.s2{fill:#e76624}.s2l{stroke:#e76624}.s3{fill:#00a888}
.s3l{stroke:#00a888}.mute{fill:#474747}.mutel{stroke:#9c9c9c}.panel{fill:#0b0b0b;stroke:rgba(255,255,255,0.10)}
.ring{stroke:#191919}.arrow{stroke:#9c9c9c}.arrowhead{fill:#9c9c9c}.accent{stroke:#338ced}}
</style>"""
LABEL = {"A": "A Vanilla", "B": "B Flat Retry", "C": "C Context-only", "D": "D Graph-only", "E": "E Adaptive"}
SHORT = {"aah-main-emergency-v2": "v2", "aah-main-emergency-v1": "v1-invalid"}


def _t(x, y, text, cls="t1", size=12, anchor="start", weight=None, extra=""):
    w = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" font-size="{size}" text-anchor="{anchor}"{w}{extra}>'
            f"{escape(str(text))}</text>")


def _svg(width, height, body, title, desc):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}" role="img" aria-labelledby="t d"><title id="t">{escape(title)}</title>'
            f'<desc id="d">{escape(desc)}</desc>{STYLE}<rect class="bg" width="{width}" height="{height}" rx="8"/>'
            f"{body}</svg>\n")


def _tip(text):
    return f' data-tip="{escape(text, {chr(34): "&quot;"})}"'


def _col(x, y, w, h, cls, tip):
    """Column with a 4px rounded data-end, square at the baseline."""
    if h <= 0:
        return ""
    r = min(4.0, h, w / 2)
    d = (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
         f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z")
    return f'<path d="{d}" class="{cls}"{_tip(tip)}><title>{escape(tip)}</title></path>'


def _hbar(x, y, w, h, cls, tip):
    """Horizontal bar from the left baseline with a rounded right end."""
    if w <= 0:
        return ""
    r = min(4.0, w, h / 2)
    d = (f"M{x:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
         f"L{x + w:.1f},{y + h - r:.1f} Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} L{x:.1f},{y + h:.1f} Z")
    return f'<path d="{d}" class="{cls}"{_tip(tip)}><title>{escape(tip)}</title></path>'


def _pct(v, digits=1):
    return "N/A" if v is None else f"{v * 100:.{digits}f}%"


def _pp(v):
    if v is None:
        return "N/A"
    value = round(v * 100, 1)
    if value == 0:
        return "0.0%p"
    return ("+" if value > 0 else "−") + f"{abs(value):.1f}%p"


def _num(v):
    return "N/A" if v is None else f"{v:,.0f}"


def _banner(exp, width):
    if exp["validity"] == "VALID_EXPLORATORY":
        return ""
    return (f'<rect x="16" y="44" width="{width - 32}" height="22" rx="4" class="crit" opacity="0.12"/>'
            + _t(26, 59, "무효 실험(INFRA_COMPROMISED): 에이전트 셸 차단. 연구 질문의 근거로 쓰지 않는다 (EL-20261127-034)",
                 "t1", 11.5, weight="600"))


def _header(exp, width, title, unit_note):
    sub = f"{exp['experiment_id']} · 과제 8개 × 조건 5개 × 1회 · {unit_note}"
    return _t(16, 24, title, "t1", 15, weight="600") + _t(16, 40, sub, "t2", 11.5) + _banner(exp, width)


# ---------------------------------------------------------------- charts

def success_rate(exp) -> str:
    width, height = 640, 330
    top = 84 if exp["validity"] != "VALID_EXPLORATORY" else 66
    left, plot_w, plot_h = 56, 560, height - top - 64
    cls = "s1" if exp["validity"] == "VALID_EXPLORATORY" else "mute"
    body = [_header(exp, width, "조건별 과제 평균 성공률 (독립 판정)", "단위: 과제 평균 성공률")]
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        y = top + plot_h * (1 - tick)
        body.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y:.1f}" y2="{y:.1f}" class="{"base" if tick == 0 else "grid"}" stroke-width="1"/>')
        body.append(_t(left - 8, y + 4, f"{int(tick * 100)}%", "t2", 11, "end"))
    slot = plot_w / 5
    for i, c in enumerate(("A", "B", "C", "D", "E")):
        m = exp["conditions"].get(c) or {}
        rate = m.get("tsr_task_mean")
        x = left + slot * i + slot / 2 - 12
        h = plot_h * (rate or 0)
        tip = f"{LABEL[c]}: {_pct(rate)} ({m.get('successes', 'N/A')}/{m.get('valid', 'N/A')} 성공)"
        body.append(_col(x, top + plot_h - h, 24, h, cls, tip))
        body.append(_t(x + 12, top + plot_h - h - 8, f"{_pct(rate)}", "t1", 12, "middle", "600"))
        body.append(_t(x + 12, top + plot_h + 18, LABEL[c], "t2", 11.5, "middle"))
        body.append(_t(x + 12, top + plot_h + 34, f"{m.get('successes', 'N/A')}/{m.get('valid', 'N/A')} 성공", "t2", 11, "middle"))
    body.append(_t(16, height - 10, f"출처: {exp['public_dir']}/metric_summary.csv (analysis.json "
                   f"{exp['meta']['analysis']['analysis_json_sha256'][:12]}…)", "t2", 10.5))
    return _svg(width, height, "".join(body), f"{exp['experiment_id']} 조건별 성공률",
                "A–E 조건별 과제 평균 성공률과 성공 건수. 값은 각 막대 위에 표시한다.")


def tokens(exp) -> str:
    width, height = 760, 270
    top = 92 if exp["validity"] != "VALID_EXPLORATORY" else 74
    cls = "s1" if exp["validity"] == "VALID_EXPLORATORY" else "mute"
    body = [_header(exp, width, "모델 토큰 사용량과 성공당 토큰 (관측값, observe_only)",
                    "단위: 모델 토큰 · 과금액 미관측")]
    panels = (("조건별 총 모델 토큰", "model_tokens_total", 30, None),
              ("성공 1건당 토큰 (실패 비용 포함)", "cps_tokens", 395, "cps_reason"))
    for title, key, x0, reason_key in panels:
        values = [(c, (exp["conditions"].get(c) or {}).get(key)) for c in ("A", "B", "C", "D", "E")]
        vmax = max([v for _, v in values if v] or [1])
        label_w, plot_w, row = 96, 200, 30
        body.append(_t(x0, top - 6, title, "t1", 12.5, weight="600"))
        body.append(f'<line x1="{x0 + label_w}" x2="{x0 + label_w}" y1="{top}" y2="{top + row * 5}" class="base" stroke-width="1"/>')
        for i, (c, v) in enumerate(values):
            y = top + row * i + 6
            body.append(_t(x0 + label_w - 8, y + 13, LABEL[c], "t2", 11.5, "end"))
            if v is None:
                reason = (exp["conditions"].get(c) or {}).get(reason_key) if reason_key else None
                body.append(_t(x0 + label_w + 6, y + 13, f"정의 안 됨 ({reason or 'N/A'})", "t2", 11.5))
                continue
            w = plot_w * v / vmax
            body.append(_hbar(x0 + label_w, y, w, 18, cls, f"{LABEL[c]}: {_num(v)} 토큰"))
            body.append(_t(x0 + label_w + w + 6, y + 13, _num(v), "t1", 11.5))
    body.append(_t(16, height - 10, f"출처: {exp['public_dir']}/metric_summary.csv · 토큰 상한은 관측만 했다(엄격한 토큰 예산 아님)",
                   "t2", 10.5))
    return _svg(width, height, "".join(body), f"{exp['experiment_id']} 토큰 사용량",
                "조건별 총 모델 토큰과 성공당 토큰을 두 패널로 나누어 표시한다. 두 측정치는 축을 공유하지 않는다.")


def paired_effects(exp) -> str:
    rows = [c for c in exp["contrasts"] if c["unit"] == "pp"]
    pr1 = next((c for c in exp["contrasts"] if c["contrast_id"] == "P-R1"), None)
    width = 720
    top = 92 if exp["validity"] != "VALID_EXPLORATORY" else 74
    row_h = 26
    height = top + row_h * len(rows) + 150
    left, plot_w = 120, 330
    lo, hi = -0.5, 0.5
    for c in rows:
        for v in (c["effect"], c["ci_lower"], c["ci_upper"]):
            if v is not None:
                lo, hi = min(lo, v), max(hi, v)

    def sx(v):
        return left + plot_w * (v - lo) / (hi - lo)

    dot = "s1" if exp["validity"] == "VALID_EXPLORATORY" else "mute"
    line = "s1l" if exp["validity"] == "VALID_EXPLORATORY" else "mutel"
    body = [_header(exp, width, "paired 대비 효과 크기와 95% 구간 (task family cluster bootstrap 10,000회)",
                    "단위: %p (성공률 차)")]
    lo, hi = math.floor(lo * 4) / 4, math.ceil(hi * 4) / 4
    ticks = [t / 4 for t in range(int(lo * 4), int(hi * 4) + 1)]
    for tick in ticks:
        x = sx(tick)
        body.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 4}" y2="{top + row_h * len(rows)}" class="{"base" if tick == 0 else "grid"}" stroke-width="1"/>')
        body.append(_t(x, top + row_h * len(rows) + 16, f"{int(tick * 100):+d}%p" if tick else "0", "t2", 11, "middle"))
    body.append(_t(left + plot_w + 24, top - 8, "점추정 [95% 구간]", "t2", 11))
    for i, c in enumerate(rows):
        y = top + row_h * i + row_h / 2
        body.append(_t(left - 10, y + 4, c["contrast_id"], "t1", 12, "end", "600" if c["contrast_id"] == "P-R3" else None))
        if c["ci_lower"] is not None and c["ci_upper"] is not None:
            x1, x2 = sx(c["ci_lower"]), sx(c["ci_upper"])
            body.append(f'<line x1="{x1:.1f}" x2="{x2:.1f}" y1="{y:.1f}" y2="{y:.1f}" class="{line}" stroke-width="2" stroke-linecap="round"/>')
            interval = f"[{_pp(c['ci_lower'])}, {_pp(c['ci_upper'])}]"
        else:
            interval = "구간 없음 (사전 승인된 방법 없음)" if c["interpretation_status"] == "EXPLORATORY_NO_INTERVAL" else "구간 없음"
        if c["effect"] is not None:
            tip = f"{c['contrast_id']}: {_pp(c['effect'])} {interval} · 과제 {c['n_tasks']} · 군집 {c['n_clusters']}"
            body.append(f'<circle cx="{sx(c["effect"]):.1f}" cy="{y:.1f}" r="5" class="{dot} ring" stroke-width="2"{_tip(tip)}><title>{escape(tip)}</title></circle>')
        body.append(_t(left + plot_w + 24, y + 4, f"{_pp(c['effect'])} {interval}", "t1", 11.5))
    y0 = top + row_h * len(rows) + 44
    body.append(_t(16, y0, "P-R1 (성공당 토큰 차, C − B): 단위가 토큰이라 위 축과 분리해 표시", "t1", 12.5, weight="600"))
    if pr1 is not None:
        if pr1["effect"] is None:
            text = f"정의 안 됨 ({pr1['interpretation_status']})"
        elif pr1["ci_lower"] is None:
            text = f"{pr1['effect']:+,.0f} 토큰 · 구간 보류 ({pr1['interpretation_status']}, 성공 0 replicate {pr1['undefined_replicates']}/10,000)"
        else:
            text = f"{pr1['effect']:+,.0f} 토큰 [{pr1['ci_lower']:+,.0f}, {pr1['ci_upper']:+,.0f}] · 0을 포함 → 결론 불충분"
        body.append(_t(16, y0 + 20, text, "t1", 12))
    body.append(_t(16, y0 + 44, "P-R2 (공통 실패 체크포인트 복구율): NOT_RUN · 측정하지 않음 (AMD-01 §7.2 조건 미충족)", "t1", 12))
    body.append(_t(16, height - 12, f"출처: {exp['public_dir']}/paired_contrasts.csv · AMD-01 §8: 유의성 검정·비열등성 판정 없음, 모든 해석은 탐색적",
                   "t2", 10.5))
    return _svg(width, height, "".join(body), f"{exp['experiment_id']} paired 대비",
                "사전 지정 대비의 점추정과 95% percentile 구간. P-R3는 점추정만, P-R1은 토큰 단위로 따로 표시한다.")


def task_matrix(exp) -> str:
    tasks = sorted(exp["tasks"])
    cell = {(s["task_id"], s["condition"]): s for s in exp["slots"]}
    width = 640
    top = 96 if exp["validity"] != "VALID_EXPLORATORY" else 78
    row_h, col_w, left = 30, 100, 104
    height = top + row_h * len(tasks) + 70
    body = [_header(exp, width, "과제 × 조건 독립 판정", "S=PASS, F=FAIL, 괄호는 사용한 턴 수")]
    for j, c in enumerate(("A", "B", "C", "D", "E")):
        body.append(_t(left + col_w * j + col_w / 2, top - 8, LABEL[c], "t2", 11.5, "middle"))
    for i, t in enumerate(tasks):
        y = top + row_h * i
        body.append(_t(left - 10, y + 19, t, "t1", 12, "end"))
        for j, c in enumerate(("A", "B", "C", "D", "E")):
            s = cell.get((t, c))
            x = left + col_w * j + 4
            if s is None or s["y"] is None:
                body.append(f'<rect x="{x}" y="{y + 3}" width="{col_w - 8}" height="{row_h - 6}" rx="4" class="mute"/>')
                body.append(_t(x + (col_w - 8) / 2, y + 19, "결측", "t1", 11.5, "middle"))
                continue
            ok = s["y"] == 1
            note = ""
            if s["stop_reason"] == "NO_SAFE_CONTINUATION":
                note += " NSC"
            if not ok and s.get("final_public") == "PASS":
                note += " †"
            label = f"{'✓ S' if ok else '✗ F'} ({s['turns']}턴){note}"
            tip = (f"{t} · {LABEL[c]}: {'PASS' if ok else 'FAIL'} · 턴 {s['turns']} · 복구 {s['recoveries_delivered']} · "
                   f"종료 {s['stop_reason']} · 토큰 {_num(s['model_tokens'])} · run {s['selected_run_id'][:8]}")
            body.append(f'<rect x="{x}" y="{y + 3}" width="{col_w - 8}" height="{row_h - 6}" rx="4" '
                        f'class="{"good" if ok else "crit"}"{_tip(tip)}><title>{escape(tip)}</title></rect>')
            body.append(_t(x + (col_w - 8) / 2, y + 19, label, "inv", 11.5, "middle", "600"))
    y = top + row_h * len(tasks) + 22
    body.append(_t(16, y, "NSC = NO_SAFE_CONTINUATION (같은 체크포인트 중복 개입 금지로 종료) · † = 공개 검사는 통과, 독립 판정은 실패",
                   "t2", 11))
    body.append(_t(16, height - 12, f"출처: {exp['public_dir']}/run_status.csv · 셀에 마우스를 올리면 run_id와 토큰을 표시",
                   "t2", 10.5))
    return _svg(width, height, "".join(body), f"{exp['experiment_id']} 과제 × 조건 판정",
                "과제별·조건별 독립 판정 결과. 상태는 색과 함께 기호(✓/✗)와 글자로 표시한다.")


def recovery_cases(exp) -> str:
    rec = exp["derived"]["recoveries"]
    none = [r for r in exp["derived"]["no_intervention_failures"] if r["first_turn_public"] == "FAIL"]
    rows = [("개입", r) for r in rec] + [("개입 없음", r) for r in none]
    width = 780
    top = 98 if exp["validity"] != "VALID_EXPLORATORY" else 80
    row_h = 40
    height = top + row_h * len(rows) + 64
    cols = (("과제·조건", 16), ("1턴 공개 검사", 150), ("진단 → 전략 (근거)", 250), ("최종 공개 검사", 580), ("독립 판정", 680))
    body = [_header(exp, width, "실패 감지와 복구 개입의 실제 경과", f"1턴 공개 검사 실패 슬롯 {len(rows)}건")]
    for name, x in cols:
        body.append(_t(x, top - 10, name, "t2", 11.5))

    def chip(x, y, status):
        ok = status == "PASS"
        cls = "good" if ok else "crit" if status in ("FAIL",) else "mute"
        text = "✓ PASS" if ok else "✗ FAIL" if status == "FAIL" else (status or "N/A")
        return (f'<rect x="{x}" y="{y + 4}" width="74" height="20" rx="4" class="{cls}"/>'
                + _t(x + 37, y + 18, text, "inv" if cls != "mute" else "t1", 11, "middle", "600"))

    for i, (kind, r) in enumerate(rows):
        y = top + row_h * i
        body.append(_t(16, y + 18, f"{r['task_id']} · {r['condition']}", "t1", 12, weight="600"))
        body.append(chip(150, y, r["first_turn_public"]))
        if kind == "개입" and r["diagnosis"] == "GRAPH":
            diag = f"GRAPH → {r['strategy']}"
            why = (f"원인 후보 {r['graph_candidates']} · 교차 연결 {r['cross_edges']} · "
                   f"{r['graph_fallback'] or '유형별 전략 근거 있음'}")
        elif kind == "개입":
            diag, why = f"FLAT → {r['strategy']}", "B·C는 설계상 일반 재시도"
        else:
            diag, why = "개입 없음", "조건 A: 외부 개입 없음 (설계상)"
        body.append(_t(250, y + 15, diag, "t1", 11.5, weight="600"))
        body.append(_t(250, y + 30, why, "t2", 10.5))
        body.append(chip(580, y, r["final_public"]))
        body.append(chip(680, y, r["verdict"]))
    passed = sum(1 for _, r in rows if _ == "개입" and r["verdict"] == "PASS")
    total = sum(1 for _, r in rows if _ == "개입")
    body.append(_t(16, top + row_h * len(rows) + 22,
                   f"개입 {total}건 중 독립 판정 PASS {passed}건 · 기술 통계이며 인과 효과가 아니다 (공통 실패 체크포인트 P-R2 미실행)",
                   "t1", 11.5, weight="600"))
    body.append(_t(16, height - 12, f"출처: {exp['public_dir']} run 기록 (interventions.jsonl, checks.jsonl, evaluation.json)",
                   "t2", 10.5))
    return _svg(width, height, "".join(body), f"{exp['experiment_id']} 복구 사례",
                "1턴 공개 검사에 실패한 슬롯의 진단·전략·최종 판정 경과")


def audit(v1, v2) -> str:
    width, height = 760, 240
    body = [_t(16, 24, "Main v1 결함 발견과 v2 재실행: 에이전트 명령 종료 코드", "t1", 15, weight="600"),
            _t(16, 40, "정규화 evidence 기록의 COMMAND_RESULT 집계 · v1 비정상 종료는 모두 MXC launcher SYSTEMROOT 누락 오류 (EL-20261127-034)",
               "t2", 11.5)]
    top, label_w, plot_w = 74, 190, 380
    vmax = max(v1["derived"]["commands"].get("total", 0), v2["derived"]["commands"].get("total", 0), 1)
    for i, (exp, name) in enumerate(((v1, "v1 (무효)"), (v2, "v2 (유효·탐색적)"))):
        cmd = exp["derived"]["commands"]
        y = top + i * 56
        body.append(_t(16, y + 14, name, "t1", 12.5, weight="600"))
        body.append(_t(16, y + 30, exp["experiment_id"], "t2", 10.5))
        x = label_w
        for key, cls, lab in (("exit_0", "s1", "exit 0"), ("exit_nonzero", "crit", "exit ≠ 0")):
            n = cmd.get(key, 0)
            w = plot_w * n / vmax
            if n:
                body.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w - 2, 1):.1f}" height="20" rx="3" class="{cls}"'
                            f'{_tip(f"{name} {lab}: {n}건")}><title>{escape(f"{name} {lab}: {n}건")}</title></rect>')
                x += w
        body.append(_t(x + 6, y + 15, f"exit 0: {cmd.get('exit_0', 0)} · exit ≠ 0: {cmd.get('exit_nonzero', 0)} (총 {cmd.get('total', 0)})",
                       "t1", 11.5))
        body.append(_t(label_w, y + 38, f"빈 패치 실패 {exp['derived']['empty_patch_failures']}건 · 성공 "
                       f"{exp['dispositions'].get('VALID_SUCCESS', 0)}/40", "t2", 11))
    body.append(f'<rect x="{label_w}" y="186" width="12" height="12" rx="2" class="s1"/>' + _t(label_w + 18, 196, "exit 0", "t2", 11)
                + f'<rect x="{label_w + 80}" y="186" width="12" height="12" rx="2" class="crit"/>' + _t(label_w + 98, 196, "exit ≠ 0", "t2", 11))
    body.append(_t(16, height - 12, "v2의 exit ≠ 0 16건은 실패하는 테스트 실행 등 정상적인 명령 결과다. 실행 불가 감지(AGENT_SHELL_UNAVAILABLE)는 0회.",
                   "t2", 10.5))
    return _svg(width, height, "".join(body), "Main v1/v2 에이전트 명령 결과",
                "v1은 에이전트 명령 167개가 모두 비정상 종료했고 v2는 97개 중 81개가 exit 0으로 끝났다.")


# ---------------------------------------------------------------- architecture (static structure, no data)

def architecture() -> str:
    width, height = 980, 600
    b = []

    def box(x, y, w, h, title, lines=(), accent=False):
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" class="panel"/>']
        if accent:
            out.append(f'<rect x="{x}" y="{y}" width="4" height="{h}" rx="2" class="s1"/>')
        out.append(_t(x + 12, y + 20, title, "t1", 12.5, weight="600"))
        for i, line in enumerate(lines):
            out.append(_t(x + 12, y + 38 + i * 15, line, "t2", 11))
        return "".join(out)

    def head(x2, y2, ang):
        p1 = (x2 - 8 * math.cos(ang - 0.4), y2 - 8 * math.sin(ang - 0.4))
        p2 = (x2 - 8 * math.cos(ang + 0.4), y2 - 8 * math.sin(ang + 0.4))
        return f'<path d="M{x2},{y2} L{p1[0]:.1f},{p1[1]:.1f} L{p2[0]:.1f},{p2[1]:.1f} Z" class="arrowhead"/>'

    def path(points, label=None, at=None):
        d = "M " + " L ".join(f"{x},{y}" for x, y in points)
        (x1, y1), (x2, y2) = points[-2], points[-1]
        out = f'<path d="{d}" class="arrow" stroke-width="1.5"/>' + head(x2, y2, math.atan2(y2 - y1, x2 - x1))
        if label:
            out += _t(at[0], at[1], label, "t2", 10.5, at[2] if len(at) > 2 else "start")
        return out

    b.append(_t(20, 28, "AAH 시스템 아키텍처 — 모델을 바꾸지 않는 외부 하네스", "t1", 16, weight="600"))
    b.append(_t(20, 46, "화살표: 실행 흐름 · 파란 띠: AAH가 추가한 제어 구성 요소 · 아래 회색 영역: 에이전트와 정책이 접근할 수 없는 독립 평가 경계",
                "t2", 11.5))
    b.append(box(20, 62, 940, 50, "Condition Resolver (A–E)",
                 ["초기 컨텍스트 NONE / DEFAULT / DCC · 진단 NONE / FLAT / GRAPH · 전략 NATIVE / GENERIC / TYPED — 조건은 resolver로만 구성 (개별 override 금지)"],
                 accent=True))
    # row 1: execution and observation (left to right)
    r1, h1 = 136, 92
    b.append(box(20, r1, 200, h1, "Workspace Manager", ["과제별 격리 작업 사본", "snapshot · diff · 최종 패치 고정", "경로 탈출 · symlink 검사"]))
    b.append(box(260, r1, 210, h1, "Codex Runtime", ["codex exec --json (CLI 0.166.0)", "Windows MXC 샌드박스", "후속 턴: exec resume (같은 세션)"]))
    b.append(box(510, r1, 210, h1, "Evidence Ledger", ["JSON 이벤트 정규화", "명령 · 파일 변경 · 사용량 · 검사", "원시 payload는 제한 영역 보관"]))
    b.append(box(760, r1, 200, h1, "Trusted Public Checks", ["Docker worker의 고정 suite", "공개 기능 · 회귀 검사", "제출 코드가 suite를 못 바꿈"]))
    b.append(path([(220, r1 + 46), (260, r1 + 46)]))
    b.append(path([(470, r1 + 46), (510, r1 + 46)]))
    b.append(path([(720, r1 + 46), (760, r1 + 46)]))
    # row 2: decide and build the recovery turn (right to left)
    r2, h2 = 284, 140
    b.append(box(760, r2, 200, h2, "Failure Detector", ["B–E 공통 규칙 · 앵커 · 상한", "T1 반복 실패", "T2 탐색 정체", "T3 회귀", "T4 완료 선언 후 실패", "T5 UNKNOWN / INFRA"], accent=True))
    b.append(box(440, r2, 280, h2, "Diagnosis", ["FLAT (B·C): 실패 증거 목록", "GRAPH (D·E):", "· Repository Graph — AST 파일·클래스·함수·호출", "· Execution Graph — 명령·검사·실패·변경",
                                                  "· Cross-Graph Mapping — TESTS_TARGET·FAILS_AT", "· 제한 탐색 depth 4 · visit 200 · top-k 8"], accent=True))
    b.append(box(230, r2, 170, h2, "Strategy Selector", ["REVIEW_REGRESSION", "RELOCALIZE", "REFRESH_EVIDENCE", "GENERIC_RETRY", "CONTINUE · STOP", "(근거 부족 → GENERIC)"], accent=True))
    b.append(box(20, r2, 170, h2, "Context Builder", ["DEFAULT (B·D)", "공통 최소 근거", "DCC (C·E)", "관련성·유효성·중복·예산", "보조 정보 상한", "2,048 추정 토큰"], accent=True))
    b.append(path([(860, r1 + h1), (860, r2)], "공개 검사 결과", (868, 262)))
    b.append(path([(760, r2 + 70), (720, r2 + 70)]))
    b.append(path([(440, r2 + 70), (400, r2 + 70)]))
    b.append(path([(230, r2 + 70), (190, r2 + 70)]))
    b.append(path([(105, r2), (105, 256), (365, 256), (365, r1 + h1)], "복구 턴 전달 (최대 2회 · 새 관측 필요 · 같은 체크포인트 중복 금지)", (113, 273)))
    # evaluation boundary (right to left)
    r3 = 456
    b.append(f'<rect x="20" y="{r3}" width="940" height="128" rx="8" class="mute" opacity="0.28"/>')
    b.append(_t(32, r3 + 20, "독립 평가 경계 — 판정 결과는 Runtime · Graph · DCC · Recovery로 역류하지 않는다", "t1", 12, weight="600"))
    b.append(box(700, r3 + 32, 248, 84, "Patch Freeze", ["정상 종료 · STOP · 한도 도달 후", "최종 패치를 고정 (patch_hash)"]))
    b.append(box(380, r3 + 32, 280, 84, "Independent Evaluator", ["저장소 밖 큐레이터 키트 (Docker)", "비공개 기능 · 회귀 · 지침 판정"]))
    b.append(box(32, r3 + 32, 308, 84, "Analysis · Evidence · Dashboard", ["동결 분석 · cluster bootstrap 10,000회", "바이트 재현 · 공개 묶음 · 대시보드"]))
    b.append(path([(900, r2 + h2), (900, r3 + 32)], "종료", (908, r3 - 10)))
    b.append(path([(700, r3 + 74), (660, r3 + 74)]))
    b.append(path([(380, r3 + 74), (340, r3 + 74)]))
    return _svg(width, height, "".join(b), "AAH 시스템 아키텍처",
                "Condition Resolver가 A–E 정책을 구성한다. Codex 실행과 관측, 공통 실패 감지, 진단, 전략 선택, 컨텍스트 구성으로 이루어진 "
                "제한된 복구 루프를 거친 뒤 최종 패치는 독립 평가 경계에서 판정된다.")


def render_all(bundle: dict) -> dict[str, str]:
    figures = {"fig01-architecture.svg": architecture()}
    exps = bundle["experiments"]
    for eid, exp in exps.items():
        tag = SHORT.get(eid, eid)
        figures[f"{tag}-success-rate.svg"] = success_rate(exp)
        figures[f"{tag}-tokens.svg"] = tokens(exp)
        figures[f"{tag}-paired-effects.svg"] = paired_effects(exp)
        figures[f"{tag}-task-matrix.svg"] = task_matrix(exp)
        figures[f"{tag}-recovery-cases.svg"] = recovery_cases(exp)
    if "aah-main-emergency-v1" in exps and "aah-main-emergency-v2" in exps:
        figures["v1-v2-audit.svg"] = audit(exps["aah-main-emergency-v1"], exps["aah-main-emergency-v2"])
    return figures
