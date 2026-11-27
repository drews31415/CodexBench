"""Number and time formatting shared by every view (same rounding as the reports: Python's ties-to-even)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
CONDITIONS = ("A", "B", "C", "D", "E")
COND_LABEL = {"A": "A Vanilla", "B": "B Flat Retry", "C": "C Context-only", "D": "D Graph-only",
              "E": "E Adaptive Harness"}


def is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def num(value) -> str:
    return f"{round(value):,}" if is_num(value) else "N/A"


def pct(value) -> str:
    return f"{value * 100:.1f}%" if is_num(value) else "N/A"


def pp(value) -> str:
    if not is_num(value):
        return "N/A"
    text = f"{abs(value) * 100:.1f}"
    if text == "0.0":
        return "0.0%p"
    return ("+" if value > 0 else "−") + text + "%p"


def signed_tokens(value) -> str:
    return f"{value:+,.0f}".replace("-", "−") if is_num(value) else "N/A"


def secs(ms) -> str:
    return f"{ms / 1000:.1f}초" if is_num(ms) else "N/A"


def short(value, n: int = 12) -> str:
    return str(value)[:n] if value else "N/A"


def stamp(iso: str | None) -> str:
    """'2026-11-27 10:34 UTC (19:34 KST)' from an ISO-8601 UTC timestamp."""
    if not iso:
        return "N/A"
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return f"{moment:%Y-%m-%d %H:%M} UTC ({moment.astimezone(KST):%H:%M} KST)"


def turn(turn_id) -> str:
    if turn_id is None:
        return "기준(baseline)"
    return str(turn_id).replace("turn-000", "턴 ").replace("turn-00", "턴 ")
