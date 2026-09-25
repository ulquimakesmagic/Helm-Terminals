"""Client for the Helm API. The bot never holds user credentials: it exchanges its service key for short-lived,
per-user bot-channel sessions, and only for Telegram accounts that were linked with a one-time code."""

import time
from typing import Any, Callable

import httpx


class ApiUnavailable(Exception):
    pass


def detail_of(data: Any) -> str:
    if isinstance(data, dict):
        d = data.get("detail", "")
        return d if isinstance(d, str) else str(d)
    return str(data)


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:
        return {"detail": r.text[:200]}


class BotApi:
    def __init__(self, http: httpx.AsyncClient, bot_key: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.http = http
        self.key = bot_key
        self.clock = clock
        self._tokens: dict[int, tuple[str, float]] = {}

    async def _service(self, path: str, body: dict) -> tuple[int, Any]:
        try:
            r = await self.http.post(path, json=body, headers={"X-Bot-Key": self.key})
        except httpx.HTTPError as e:
            raise ApiUnavailable(str(e)) from e
        return r.status_code, _json(r)

    async def link(self, code: str, tg_id: int, chat_id: int) -> tuple[int, Any]:
        return await self._service("/telegram/bot/link", {"code": code, "telegram_id": tg_id, "chat_id": chat_id})

    async def unlink(self, tg_id: int) -> tuple[int, Any]:
        self._tokens.pop(tg_id, None)
        return await self._service("/telegram/bot/unlink", {"telegram_id": tg_id})

    async def _token(self, tg_id: int, force: bool = False) -> str | None:
        cached = self._tokens.get(tg_id)
        if cached and not force and cached[1] > self.clock():
            return cached[0]
        status, data = await self._service("/telegram/bot/session", {"telegram_id": tg_id})
        if status == 404:
            self._tokens.pop(tg_id, None)
            return None
        if status != 200:
            raise ApiUnavailable(f"session request failed: {status} {detail_of(data)}")
        ttl_s = max(int(data.get("expires_in_minutes", 30)) * 60 - 120, 60)  # refresh a little early
        self._tokens[tg_id] = (data["token"], self.clock() + ttl_s)
        return data["token"]

    async def call(self, tg_id: int, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
        """Returns (status, json). Status 401 with detail 'not_linked' means the Telegram user is not linked."""
        token = await self._token(tg_id)
        if token is None:
            return 401, {"detail": "not_linked"}
        for attempt in (0, 1):
            try:
                r = await self.http.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise ApiUnavailable(str(e)) from e
            data = _json(r)
            expired = r.status_code == 401 and not detail_of(data).startswith("totp_required")
            if expired and attempt == 0:
                token = await self._token(tg_id, force=True)
                if token is None:
                    return 401, {"detail": "not_linked"}
                continue
            return r.status_code, data
        return 401, {"detail": "session_expired"}
