"""Framework-agnostic bot logic. The aiogram layer only adapts Telegram messages to `on_message` / `on_callback`.

Safety rules baked in:
- Private chats only. Nothing here ever runs from a group.
- Every fund-moving command shows the API's preflight, then needs an explicit Confirm tap by the same Telegram user.
- The API decides when a 2FA code is required. The code is typed into the chat and the message is flagged for deletion.
- Confirmations expire after two minutes and can be used once.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import asyncio

from .api import ApiUnavailable, BotApi, detail_of
from .pending import Pending, PendingStore
from .render import e, num, render_job, render_launches, render_preflight, render_wallets, render_workspace, sid

log = logging.getLogger("helm_bot")

TERMINAL = ("confirmed", "failed", "partial")
MAX_LINK_FAILS, LINK_FAIL_WINDOW_S = 5, 600


@dataclass
class Button:
    label: str
    data: str


@dataclass
class Reply:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    delete_incoming: bool = False  # ask the adapter to delete the user's message (2FA codes, link codes)


Send = Callable[[Reply], Awaitable[None]]


class BotError(Exception):
    """A problem to show the user as-is."""


class NotLinked(BotError):
    pass


HELP = """<b>Helm Terminals bot</b>
/wallets  your wallets and balances
/launches  your launches
/launch &lt;id&gt;  wallets, holdings and P&amp;L
/transparency &lt;id&gt;  how much supply your wallets hold
/buy &lt;launch&gt; &lt;sol&gt; [wallets|all]
/sell &lt;launch&gt; &lt;percent&gt; [wallets|all]
/claim &lt;launch&gt; &lt;wallet&gt;  claim creator fees
/burn &lt;launch&gt; &lt;wallet&gt; &lt;percent&gt;
/send &lt;launch&gt; &lt;wallet&gt; &lt;address&gt; &lt;percent&gt;  🔐
/withdraw &lt;wallet|all&gt; &lt;address&gt;  🔐
/status &lt;job id&gt;
/unlink  disconnect this Telegram account

Ids are the first 6 characters shown in the lists. Every action shows a preview first.
🔐 needs your 2FA code."""

LINK_TEXT = ("This Telegram account isn't linked yet.\n"
             "1. Open Helm Terminals on the web → Settings → Telegram\n"
             "2. Tap <b>Link Telegram</b> and copy the code\n"
             "3. Send it here: <code>/link YOURCODE</code>")


class Controller:
    def __init__(self, api: BotApi, pending: PendingStore | None = None, poll_interval: float = 1.5,
                 poll_timeout: float = 90, clock: Callable[[], float] = time.monotonic) -> None:
        self.api = api
        self.pending = pending or PendingStore(clock=clock)
        self.poll_interval, self.poll_timeout, self.clock = poll_interval, poll_timeout, clock
        self.awaiting: dict[int, str] = {}  # tg_id -> pending id waiting for a 2FA code
        self.link_fails: dict[int, list[float]] = {}

    # --- entry points ------------------------------------------------------------------------------

    async def on_message(self, tg_id: int, chat_id: int, chat_type: str, text: str, send: Send) -> None:
        if chat_type != "private":
            await send(Reply("For your safety I only work in a private chat with me."))
            return
        text = (text or "").strip()
        try:
            if tg_id in self.awaiting and not text.startswith("/"):
                await self._handle_totp(tg_id, text, send)
                return
            cmd, *args = text.split()
            cmd = cmd.split("@")[0].lower()
            handler = getattr(self, f"cmd_{cmd.lstrip('/')}", None) if cmd.startswith("/") else None
            if handler is None:
                await send(Reply(HELP))
                return
            await handler(tg_id, chat_id, args, send)
        except NotLinked:
            await send(Reply(LINK_TEXT))
        except BotError as err:
            await send(Reply(f"⚠️ {e(err)}"))
        except ApiUnavailable:
            await send(Reply("I can't reach Helm Terminals right now. Try again in a minute."))
        except Exception:  # never leak internals into chat
            log.exception("unhandled error in on_message")
            await send(Reply("Something went wrong on my side. Nothing was sent."))

    async def on_callback(self, tg_id: int, chat_type: str, data: str, send: Send) -> None:
        if chat_type != "private":
            return
        action, _, pid = (data or "").partition(":")
        try:
            p = self.pending.get(pid, tg_id)
            if p is None or p.submitting:
                await send(Reply("That confirmation expired or was already used. Run the command again."))
                return
            if action == "no":
                self.pending.drop(pid)
                self.awaiting.pop(tg_id, None)
                await send(Reply("Cancelled. Nothing was sent."))
            elif action == "ok":
                if p.requires_2fa:
                    self.awaiting[tg_id] = p.id
                    await send(Reply("🔐 Send your 6-digit 2FA code. I'll delete your message. /cancel to abort."))
                else:
                    await self._submit(tg_id, p, None, send)
        except NotLinked:
            await send(Reply(LINK_TEXT))
        except BotError as err:
            await send(Reply(f"⚠️ {e(err)}"))
        except ApiUnavailable:
            await send(Reply("I can't reach Helm Terminals right now. Nothing was sent; run the command again shortly."))
        except Exception:
            log.exception("unhandled error in on_callback")
            await send(Reply("Something went wrong on my side. Check /status before retrying."))

    # --- account -----------------------------------------------------------------------------------

    async def cmd_start(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if args:
            await self.cmd_link(tg_id, chat_id, args, send)
        else:
            await send(Reply(HELP + "\n\n" + LINK_TEXT))

    async def cmd_help(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        await send(Reply(HELP))

    async def cmd_link(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if not args:
            raise BotError("Usage: /link YOURCODE")
        now = self.clock()
        fails = [t for t in self.link_fails.get(tg_id, []) if now - t < LINK_FAIL_WINDOW_S]
        if len(fails) >= MAX_LINK_FAILS:
            raise BotError("Too many wrong codes. Wait 10 minutes and generate a new code in the web app.")
        status, data = await self.api.link(args[0], tg_id, chat_id)
        if status != 200:
            self.link_fails[tg_id] = fails + [now]
            await send(Reply(f"⚠️ {e(detail_of(data))}", delete_incoming=True))
            return
        self.link_fails.pop(tg_id, None)
        await send(Reply(f"✅ Linked to {e(data['account'])}. Try /wallets or /launches.\n"
                         "Withdrawals and token sends will ask for your 2FA code (set it up in the web app first).",
                         delete_incoming=True))

    async def cmd_unlink(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        status, data = await self.api.unlink(tg_id)
        if status == 404:
            raise NotLinked()
        self.awaiting.pop(tg_id, None)
        await send(Reply("Unlinked. This chat can no longer act on your account."))

    async def cmd_cancel(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        pid = self.awaiting.pop(tg_id, None)
        if pid:
            self.pending.drop(pid)
        await send(Reply("Cancelled. Nothing was sent."))

    # --- read-only ---------------------------------------------------------------------------------

    async def cmd_wallets(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        await send(Reply(render_wallets(await self._get(tg_id, "/wallets?with_balances=true"))))

    async def cmd_launches(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        await send(Reply(render_launches(await self._get(tg_id, "/launches"))))

    async def cmd_launch(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        lid = await self._launch_id(tg_id, args, "/launch <id>")
        await send(Reply(render_workspace(await self._get(tg_id, f"/launches/{lid}/workspace"))))

    async def cmd_transparency(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        lid = await self._launch_id(tg_id, args, "/transparency <id>")
        await send(Reply(render_workspace(await self._get(tg_id, f"/launches/{lid}/workspace"), "transparency")))

    async def cmd_status(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if not args:
            raise BotError("Usage: /status <job id>")
        await send(Reply(render_job(await self._get(tg_id, f"/jobs/{args[0]}"))))

    # --- spending (all go through preflight + confirm) ----------------------------------------------

    async def cmd_buy(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if len(args) < 2:
            raise BotError("Usage: /buy <launch> <sol per wallet> [wallets|all]")
        lid = await self._launch_id(tg_id, args[:1], "/buy")
        amount = self._number(args[1], "SOL amount")
        ids = await self._launch_wallets(tg_id, lid, args[2] if len(args) > 2 else "all")
        await self._start_spend(tg_id, "Buy", f"/launches/{lid}/trade", {
            "side": "buy", "wallet_ids": ids, "amount": amount, "mode": "stagger", "stagger_ms": 1000,
            "slippage_pct": 10.0}, send)

    async def cmd_sell(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if len(args) < 2:
            raise BotError("Usage: /sell <launch> <percent> [wallets|all]")
        lid = await self._launch_id(tg_id, args[:1], "/sell")
        pct = self._number(args[1].rstrip("%"), "percent")
        if pct > 100:
            raise BotError("Percent must be between 1 and 100.")
        ids = await self._launch_wallets(tg_id, lid, args[2] if len(args) > 2 else "all")
        await self._start_spend(tg_id, "Sell", f"/launches/{lid}/trade", {
            "side": "sell", "wallet_ids": ids, "amount": pct, "mode": "stagger", "stagger_ms": 1000,
            "slippage_pct": 10.0}, send)

    async def cmd_claim(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if len(args) < 2:
            raise BotError("Usage: /claim <launch> <wallet>")
        lid = await self._launch_id(tg_id, args[:1], "/claim")
        wid = await self._wallet_id(tg_id, args[1])
        await self._start_spend(tg_id, "Claim creator fees", f"/launches/{lid}/claim-fees", {"wallet_id": wid}, send)

    async def cmd_burn(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if len(args) < 3:
            raise BotError("Usage: /burn <launch> <wallet> <percent>")
        lid = await self._launch_id(tg_id, args[:1], "/burn")
        wid = await self._wallet_id(tg_id, args[1])
        await self._start_spend(tg_id, "Burn tokens (irreversible)", f"/launches/{lid}/burn",
                                {"wallet_id": wid, "percent": self._number(args[2].rstrip("%"), "percent")}, send)

    async def cmd_send(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if len(args) < 4:
            raise BotError("Usage: /send <launch> <wallet> <address> <percent>")
        lid = await self._launch_id(tg_id, args[:1], "/send")
        wid = await self._wallet_id(tg_id, args[1])
        await self._start_spend(tg_id, "Send tokens", f"/launches/{lid}/send", {
            "wallet_id": wid, "recipient": args[2], "percent": self._number(args[3].rstrip("%"), "percent")}, send)

    async def cmd_withdraw(self, tg_id: int, chat_id: int, args: list[str], send: Send) -> None:
        if len(args) < 2:
            raise BotError("Usage: /withdraw <wallet|all> <address>")
        wallets = [w for w in await self._get(tg_id, "/wallets") if w["kind"] == "managed"]
        ids = [w["id"] for w in wallets] if args[0].lower() == "all" else [await self._wallet_id(tg_id, args[0])]
        if not ids:
            raise BotError("You have no wallets that can sign.")
        await self._start_spend(tg_id, "Withdraw SOL", "/wallets/withdraw",
                                {"wallet_ids": ids, "destination": args[1], "all": True}, send)

    async def _start_spend(self, tg_id: int, title: str, path: str, body: dict, send: Send) -> None:
        status, data = await self.api.call(tg_id, "POST", path, body)
        if status == 401 and detail_of(data) == "not_linked":
            raise NotLinked()
        if status != 200:
            raise BotError(detail_of(data))
        pf, requires_2fa = data["preflight"], bool(data.get("requires_2fa"))
        text = render_preflight(title, pf, requires_2fa)
        if pf.get("warnings"):
            await send(Reply(text + "\n\nI can't send this until the warning above is resolved."))
            return
        p = self.pending.add(tg_id, title, path, body, data["preflight_hash"], requires_2fa)
        await send(Reply(text + "\n\nConfirm within 2 minutes.",
                         [[Button("✅ Confirm", f"ok:{p.id}"), Button("✖ Cancel", f"no:{p.id}")]]))

    async def _handle_totp(self, tg_id: int, text: str, send: Send) -> None:
        p = self.pending.get(self.awaiting[tg_id], tg_id)
        if p is None:
            self.awaiting.pop(tg_id, None)
            await send(Reply("That confirmation expired. Run the command again.", delete_incoming=True))
            return
        if not re.fullmatch(r"\d{6}", text):
            await send(Reply("Send just the 6-digit code, or /cancel.", delete_incoming=True))
            return
        await self._submit(tg_id, p, text, send, delete_first=True)

    async def _submit(self, tg_id: int, p: Pending, totp: str | None, send: Send, delete_first: bool = False) -> None:
        p.submitting = True
        status, data = await self.api.call(tg_id, "POST", p.path, {
            **p.body, "confirm": True, "preflight_hash": p.preflight_hash,
            "idempotency_key": f"tg-{p.id}", "totp": totp})
        detail = detail_of(data)
        if status == 200:
            self.pending.drop(p.id)
            self.awaiting.pop(tg_id, None)
            await send(Reply(f"⏳ Submitted. Job <code>{e(data['job_id'])}</code>", delete_incoming=delete_first))
            await self._wait_job(tg_id, data["job_id"], send)
            return
        p.submitting = False
        if status == 401 and detail.startswith("totp_required"):
            p.attempts += 1
            if p.attempts >= 3:
                self.pending.drop(p.id)
                self.awaiting.pop(tg_id, None)
                await send(Reply("Too many wrong codes. Nothing was sent. Run the command again.", delete_incoming=True))
            else:
                await send(Reply(f"That code didn't work ({3 - p.attempts} tries left). Send it again or /cancel.",
                                 delete_incoming=True))
            return
        self.pending.drop(p.id)
        self.awaiting.pop(tg_id, None)
        if status == 409 and detail.startswith("preflight_changed"):
            await send(Reply("Balances or prices changed since the preview. Nothing was sent. Run the command again.",
                             delete_incoming=delete_first))
        elif status == 403 and detail.startswith("2fa_setup_required"):
            await send(Reply("Set up 2FA in the web app (Settings) first, then try again.", delete_incoming=delete_first))
        else:
            await send(Reply(f"⚠️ {e(detail)}", delete_incoming=delete_first))

    async def _wait_job(self, tg_id: int, job_id: str, send: Send) -> None:
        deadline = self.clock() + self.poll_timeout
        while True:
            job = await self._get(tg_id, f"/jobs/{job_id}")
            if job["status"] in TERMINAL:
                await send(Reply(render_job(job)))
                return
            if self.clock() >= deadline:
                await send(Reply(f"Still running. Check with /status <code>{e(job_id)}</code>"))
                return
            await asyncio.sleep(self.poll_interval)

    # --- helpers -----------------------------------------------------------------------------------

    async def _get(self, tg_id: int, path: str):
        status, data = await self.api.call(tg_id, "GET", path)
        if status == 401 and detail_of(data) == "not_linked":
            raise NotLinked()
        if status != 200:
            raise BotError(detail_of(data))
        return data

    @staticmethod
    def _number(raw: str, what: str) -> float:
        try:
            v = float(raw)
        except ValueError:
            raise BotError(f"'{e(raw)}' is not a valid {what}.")
        if v <= 0:
            raise BotError(f"The {what} must be greater than zero.")
        return v

    @staticmethod
    def _resolve(items: list[dict], prefix: str, what: str) -> dict:
        p = prefix.lower()
        hits = [i for i in items if i["id"].startswith(p)]
        if not hits:
            raise BotError(f"No {what} matches '{e(prefix)}'. Check the ids in the list.")
        if len(hits) > 1:
            raise BotError(f"'{e(prefix)}' matches several {what}s. Use more characters.")
        return hits[0]

    async def _launch_id(self, tg_id: int, args: list[str], usage: str) -> str:
        if not args:
            raise BotError(f"Usage: {usage}")
        return self._resolve(await self._get(tg_id, "/launches"), args[0], "launch")["id"]

    async def _wallet_id(self, tg_id: int, prefix: str) -> str:
        wallets = [w for w in await self._get(tg_id, "/wallets") if w["kind"] == "managed"]
        return self._resolve(wallets, prefix, "wallet")["id"]

    async def _launch_wallets(self, tg_id: int, launch_id: str, spec: str) -> list[str]:
        ws = (await self._get(tg_id, f"/launches/{launch_id}/workspace"))["wallets"]
        if not ws:
            raise BotError("No wallets are attached to this launch yet. Attach them in the web app.")
        if spec.lower() == "all":
            holders = [w for w in ws if w["role"] != "dev"]
            return [w["id"] for w in (holders or ws)]  # dev wallet only when it is the only one, or named
        return [self._resolve(ws, part, "wallet")["id"] for part in spec.split(",") if part]
