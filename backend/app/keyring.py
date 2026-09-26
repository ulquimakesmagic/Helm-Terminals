"""Rotate across several API keys or endpoints.

Settings like SOLANA_RPC_URL and BAGS_API_KEY accept a comma-separated list. Requests are spread across the
list round-robin; one that is rate-limited or failing is rested for a while and the next one is used. Keys are
never put in error messages: they are referred to by position ("key 2 of 3").
"""

import time


def parse_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    raw = value if isinstance(value, list) else value.replace("\n", ",").split(",")
    out: list[str] = []
    for v in raw:
        v = v.strip()
        if v and v not in out:
            out.append(v)
    return out


class KeyRing:
    def __init__(self, items: list[str], cooldown_s: float = 30.0) -> None:
        self.items = items
        self.cooldown_s = cooldown_s
        self._next = 0
        self._rest_until: dict[str, float] = {}
        self.stats = {i: {"ok": 0, "failed": 0} for i in items}

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def label(self, item: str) -> str:
        return f"{self.kind} {self.items.index(item) + 1} of {len(self.items)}" if item in self.items else self.kind

    kind = "key"

    def order(self) -> list[str]:
        """Every item to try for one request: rested ones last, starting point rotates each call."""
        if not self.items:
            return []
        n = len(self.items)
        start = self._next % n
        self._next += 1
        rotated = self.items[start:] + self.items[:start]
        now = time.monotonic()
        ready = [i for i in rotated if self._rest_until.get(i, 0) <= now]
        resting = sorted((i for i in rotated if i not in ready), key=lambda i: self._rest_until[i])
        return ready + resting

    def ok(self, item: str) -> None:
        self._rest_until.pop(item, None)
        if item in self.stats:
            self.stats[item]["ok"] += 1

    def fail(self, item: str, rest_s: float | None = None) -> None:
        self._rest_until[item] = time.monotonic() + (self.cooldown_s if rest_s is None else rest_s)
        if item in self.stats:
            self.stats[item]["failed"] += 1

    def summary(self) -> dict:
        now = time.monotonic()
        return {"count": len(self.items), "resting": sum(1 for i in self.items if self._rest_until.get(i, 0) > now)}


def retry_after(headers, default: float) -> float:
    try:
        return max(1.0, min(600.0, float(headers.get("retry-after"))))
    except (TypeError, ValueError):
        return default
