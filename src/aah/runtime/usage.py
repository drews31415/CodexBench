"""Account for observed usage from typed terminal events."""

from aah.domain import UsageMode


KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


class UsageAccount:
    def __init__(self):
        self._thread_total: dict[str, dict[str, int]] = {}
        self.known_tokens = 0
        self.complete = True
        self.turns = 0

    def record(self, mode: UsageMode, session: str | None, value, *, fresh_session: bool) -> dict | None:
        self.turns += 1
        usage = {key: value[key] for key in KEYS if type(value.get(key)) is int and value[key] >= 0} if (
            isinstance(value, dict)) else {}
        amount = None
        anchored = False
        if ("input_tokens" in usage and "output_tokens" in usage
                and usage["input_tokens"] + usage["output_tokens"] > 0
                and usage.get("cached_input_tokens", 0) <= usage["input_tokens"]
                and usage.get("reasoning_output_tokens", 0) <= usage["output_tokens"]):
            if mode == UsageMode.DELTA:
                amount = usage
            elif mode == UsageMode.CUMULATIVE and session:
                prior = {} if fresh_session and session not in self._thread_total else self._thread_total.get(session)
                self._thread_total[session] = usage
                anchored = True
                if prior is not None:
                    difference = {key: usage[key] - prior.get(key, 0) for key in usage
                                  if not prior or key in prior}
                    if (all(count >= 0 for count in difference.values())
                            and difference["input_tokens"] + difference["output_tokens"] > 0
                            and difference.get("cached_input_tokens", 0) <= difference["input_tokens"]
                            and difference.get("reasoning_output_tokens", 0) <= difference["output_tokens"]):
                        amount = difference
        if amount is None:
            self.complete = False
            if mode == UsageMode.CUMULATIVE and not anchored:
                if session:
                    self._thread_total.pop(session, None)
                elif not fresh_session:
                    self._thread_total.clear()
            return None
        self.known_tokens += amount["input_tokens"] + amount["output_tokens"]
        return amount

    def summary(self, turns: int) -> dict:
        return {
            "observed_model_tokens": self.known_tokens if turns and turns == self.turns and self.complete else None,
            "known_model_tokens_subtotal": self.known_tokens,
        }
