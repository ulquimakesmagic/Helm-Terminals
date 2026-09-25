"""Confirmations waiting for the user's tap. Bound to the Telegram user who asked and expire quickly."""

import secrets
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Pending:
    id: str
    tg_id: int
    title: str
    path: str
    body: dict
    preflight_hash: str
    requires_2fa: bool
    expires_at: float
    attempts: int = 0
    submitting: bool = False
    extra: dict = field(default_factory=dict)


class PendingStore:
    """In-memory. Run a single bot process, or swap this for Redis when scaling out."""

    def __init__(self, ttl_s: float = 120, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._items: dict[str, Pending] = {}

    def add(self, tg_id: int, title: str, path: str, body: dict, preflight_hash: str, requires_2fa: bool) -> Pending:
        self._sweep()
        p = Pending(secrets.token_urlsafe(6), tg_id, title, path, body, preflight_hash, requires_2fa,
                    self.clock() + self.ttl_s)
        self._items[p.id] = p
        return p

    def get(self, pid: str, tg_id: int) -> Pending | None:
        p = self._items.get(pid)
        if p is None or p.tg_id != tg_id:  # somebody else's button: treat as nonexistent
            return None
        if p.expires_at < self.clock():
            self._items.pop(pid, None)
            return None
        return p

    def drop(self, pid: str) -> None:
        self._items.pop(pid, None)

    def _sweep(self) -> None:
        now = self.clock()
        for k in [k for k, v in self._items.items() if v.expires_at < now]:
            self._items.pop(k, None)
