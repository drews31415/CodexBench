"""Usage observations for the pinned Codex CLI smoke runner."""

from aah.domain import UsageMode


USAGE_KEYS = (
    "input_tokens", "cached_input_tokens", "output_tokens",
    "cache_write_input_tokens", "reasoning_output_tokens",
)
REQUIRED_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
USAGE_MODE = UsageMode.CUMULATIVE
TOKEN_LIMIT_MODE = "observe_only"
TOKEN_ESTIMATOR = "utf8-bytes-div4-v1"


def estimate_prompt_tokens(message: bytes) -> int:
    """Estimate only the UTF-8 message sent by the harness."""
    return (len(message) + 3) // 4


def read_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    counts = {key: value[key] for key in USAGE_KEYS
              if type(value.get(key)) is int and value[key] >= 0}
    # Codex emits an all-zero default when no usage update was received.
    return counts if any(counts.values()) else None


def resumed_delta(initial: dict, followup: dict) -> dict[str, int] | None:
    thread = initial.get("thread_id")
    if (not thread or not initial.get("persist_session") or initial.get("requested_session_id") is not None
            or followup.get("thread_id") != thread or followup.get("requested_session_id") != thread):
        return None
    for receipt in (initial, followup):
        events = receipt.get("event_counts", {})
        if (receipt.get("status") != "EXITED" or receipt.get("exit_code") != 0
                or receipt.get("remaining_process_count") != 0
                or any(events.get(event) != 1 for event in ("thread.started", "turn.started", "turn.completed"))
                or events.get("turn.failed")):
            return None
    before = read_usage(initial.get("usage"))
    after = read_usage(followup.get("usage"))
    if before is None or after is None or any(key not in before or key not in after for key in REQUIRED_KEYS):
        return None
    delta = {key: after[key] - before[key] for key in USAGE_KEYS if key in before and key in after}
    if (not any(delta.values()) or any(value < 0 for value in delta.values())
            or delta["cached_input_tokens"] > delta["input_tokens"]
            or delta.get("reasoning_output_tokens", 0) > delta["output_tokens"]):
        return None
    return delta
