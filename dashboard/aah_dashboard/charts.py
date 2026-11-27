"""Altair charts (validated reference palette: one series = slot-1 blue; status = good/critical with a text label)."""

from __future__ import annotations

import altair as alt
import pandas as pd

from . import fmt, models
from .fmt import COND_LABEL, CONDITIONS

BLUE, ORANGE, AQUA = "#257de0", "#f47230", "#00b894"
GOOD, CRIT, MUTE, INK2 = "#0aac23", "#dc3c3d", "#9c9c9c", "#666666"


def _series(export: dict) -> str:
    return BLUE if export.get("status") in ("VALID", "VALID_EXPLORATORY") else MUTE


def success(export: dict) -> alt.Chart:
    cm = export["conditions"]
    df = pd.DataFrame([{"조건": c, "이름": COND_LABEL[c], "성공률": cm[c]["tsr_task_mean"] * 100,
                        "표시": f"{fmt.pct(cm[c]['tsr_task_mean'])} ({cm[c]['successes']}/{cm[c]['valid']})"}
                       for c in CONDITIONS if c in cm])
    base = alt.Chart(df).encode(
        x=alt.X("조건:N", sort=list(CONDITIONS), axis=alt.Axis(labelAngle=0, title="조건 (A–E)")),
        y=alt.Y("성공률:Q", scale=alt.Scale(domain=[0, 105]), axis=alt.Axis(title="과제 평균 성공률 (%)", tickCount=5)),
        tooltip=[alt.Tooltip("이름:N", title="조건"), alt.Tooltip("표시:N", title="성공률 (성공/유효)")])
    bars = base.mark_bar(size=24, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=_series(export))
    return (bars + base.mark_text(dy=-9, fontSize=12).encode(text="표시:N")).properties(height=300)


def tokens(export: dict, key: str, title: str) -> alt.Chart:
    cm = export["conditions"]
    df = pd.DataFrame([{"조건": COND_LABEL[c], "값": cm[c][key], "표시": fmt.num(cm[c][key])}
                       for c in CONDITIONS if c in cm and cm[c].get(key) is not None])
    base = alt.Chart(df).encode(y=alt.Y("조건:N", sort=[COND_LABEL[c] for c in CONDITIONS], title=None),
                                x=alt.X("값:Q", title=title), tooltip=["조건:N", alt.Tooltip("표시:N", title=title)])
    bars = base.mark_bar(size=18, cornerRadiusTopRight=4, cornerRadiusBottomRight=4, color=_series(export))
    return (bars + base.mark_text(align="left", dx=4, fontSize=11).encode(text="표시:N")).properties(height=210)


def forest(export: dict) -> alt.Chart:
    rows = [c for c in export["contrasts"] if c["unit"] == "pp"]
    df = pd.DataFrame([{"대비": c["contrast_id"], "효과": c["effect"] * 100,
                        "하한": c["ci_lower"] * 100 if c["ci_lower"] is not None else None,
                        "상한": c["ci_upper"] * 100 if c["ci_upper"] is not None else None,
                        "표시": f"{fmt.pp(c['effect'])} " + (f"[{fmt.pp(c['ci_lower'])}, {fmt.pp(c['ci_upper'])}]"
                                                            if c["ci_lower"] is not None else "구간 없음")} for c in rows])
    order = [c["contrast_id"] for c in rows]
    y = alt.Y("대비:N", sort=order, title=None)
    rule = alt.Chart(df.dropna(subset=["하한"])).mark_rule(strokeWidth=2, color=_series(export)).encode(
        y=y, x=alt.X("하한:Q", title="효과 (%p, 95% 구간)", scale=alt.Scale(domain=[-50, 50])), x2="상한:Q")
    point = alt.Chart(df).mark_point(filled=True, size=90, color=_series(export), stroke="white", strokeWidth=2).encode(
        y=y, x="효과:Q", tooltip=["대비:N", alt.Tooltip("표시:N", title="점추정 [95% 구간]")])
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=MUTE).encode(x="x:Q")
    return (zero + rule + point).properties(height=26 * len(rows) + 40)


def matrix(export: dict) -> alt.Chart:
    df = pd.DataFrame(models.matrix_rows(export))
    df["열"] = df["조건"].str[0]
    base = alt.Chart(df).encode(x=alt.X("열:N", sort=list(CONDITIONS), axis=alt.Axis(labelAngle=0, title=None, orient="top")),
                                y=alt.Y("과제:N", title=None))
    cells = base.mark_rect(cornerRadius=4, stroke="white", strokeWidth=2).encode(
        color=alt.Color("결과:N", scale=alt.Scale(domain=["PASS", "FAIL", "PENDING"], range=[GOOD, CRIT, MUTE]),
                        legend=alt.Legend(title="독립 판정", orient="bottom")),
        tooltip=["과제:N", "조건:N", "결과:N", "턴:Q", "종료:N", "토큰:Q", "run_id:N"])
    text = base.mark_text(color="white", fontSize=11, fontWeight="bold").encode(text="표시:N")
    return (cells + text).properties(height=34 * df["과제"].nunique() + 30)


def timeline(run: dict) -> alt.Chart | None:
    rows = models.timeline_rows(run)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["행"] = "상태"
    groups = ["Codex 실행", "하네스 관측·공개 검사", "하네스 결정·진단·컨텍스트", "시작·종료"]
    return alt.Chart(df).mark_bar(height=22, cornerRadius=3).encode(
        x=alt.X("시작(초):Q", title="경과 시간 (초)"), x2="끝(초):Q", y=alt.Y("행:N", title=None, axis=None),
        color=alt.Color("구분:N", scale=alt.Scale(domain=groups, range=[BLUE, ORANGE, AQUA, MUTE]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["단계:N", "구분:N", alt.Tooltip("시작(초):Q", format=".1f"), alt.Tooltip("끝(초):Q", format=".1f"), "사유:N", "개입:N"],
    ).properties(height=90)


def commands(rows: list[dict]) -> alt.Chart:
    long = [{"실험": r["실험"], "종료 코드": k, "명령 수": r[k]} for r in rows for k in ("exit 0", "exit ≠ 0")]
    df = pd.DataFrame(long)
    return alt.Chart(df).mark_bar(size=20).encode(
        y=alt.Y("실험:N", title=None), x=alt.X("명령 수:Q", stack="zero", title="에이전트 셸 명령 (정규화 원장)"),
        color=alt.Color("종료 코드:N", scale=alt.Scale(domain=["exit 0", "exit ≠ 0"], range=[BLUE, CRIT]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["실험:N", "종료 코드:N", "명령 수:Q"]).properties(height=60 * len(rows) + 40)
