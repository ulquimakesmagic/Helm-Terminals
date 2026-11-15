"""The Telegram bot, exercised end to end against the real FastAPI app (in-process, fake chain)."""

import pyotp
import pytest
from sqlalchemy import update

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import LinkCode, Session, utcnow
from helm_bot.api import BotApi
from helm_bot.controller import Controller
from helm_bot.pending import PendingStore
from helm_bot.render import render_launches, render_preflight, sid

from .conftest import enable_2fa, signup, totp_code
from .test_api import live_launch, make_wallets

KEY = "test-bot-key"


@pytest.fixture
def bot_key():
    s = get_settings()
    saved = (s.bot_api_key, s.bot_daily_buy_limit_sol, s.telegram_allowed_user_ids)
    s.bot_api_key = KEY
    s.telegram_allowed_user_ids = "1,2,3,5,7,9,42,1001"
    yield KEY
    s.bot_api_key, s.bot_daily_buy_limit_sol, s.telegram_allowed_user_ids = saved


class Out:
    def __init__(self):
        self.replies = []

    async def __call__(self, reply):
        self.replies.append(reply)

    @property
    def text(self):
        return "\n".join(r.text for r in self.replies)

    @property
    def confirm_data(self):
        return self.replies[0].buttons[0][0].data


def make_ctl(client, **kw):
    return Controller(BotApi(client, KEY), poll_interval=0.01, poll_timeout=5, **kw)


async def say(c, tg, text, chat_type="private"):
    out = Out()
    await c.on_message(tg, tg, chat_type, text, out)
    return out


async def tap(c, tg, data, chat_type="private"):
    out = Out()
    await c.on_callback(tg, chat_type, data, out)
    return out


async def link(client, h, c, tg=1001):
    code = (await client.post("/telegram/link-code", headers=h)).json()["code"]
    out = await say(c, tg, f"/link {code}")
    assert "Linked" in out.text, out.text
    assert out.replies[0].delete_incoming  # the code is removed from the chat


# --- linking and sessions ------------------------------------------------------------------------


async def test_bot_endpoints_need_the_service_key(client, bot_key):
    h = await signup(client)
    assert (await client.post("/telegram/bot/session", json={"telegram_id": 1})).status_code == 403
    assert (await client.post("/telegram/bot/session", json={"telegram_id": 1}, headers={"X-Bot-Key": "nope"})).status_code == 403
    get_settings().bot_api_key = ""
    assert (await client.post("/telegram/link-code", headers=h)).status_code == 503  # integration off


async def test_bot_sessions_require_an_explicit_user_allowlist(client, bot_key):
    from sqlalchemy import select

    from app.models import TelegramLink, User

    h = await signup(client)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id).where(User.email == "a@example.com"))).scalar_one()
        db.add(TelegramLink(user_id=uid, telegram_id=88, chat_id=88))
        await db.commit()
    response = await client.post("/telegram/bot/session", json={"telegram_id": 88}, headers={"X-Bot-Key": KEY})
    assert response.status_code == 403 and "not allowlisted" in response.json()["detail"]


async def test_link_code_is_one_time_and_expires(client, bot_key):
    h = await signup(client)
    ctl = make_ctl(client)
    code = (await client.post("/telegram/link-code", headers=h)).json()["code"]
    assert "Linked" in (await say(ctl, 1, f"/link {code}")).text
    assert "invalid or expired" in (await say(ctl, 2, f"/link {code}")).text  # already used

    await client.post("/telegram/unlink", headers=h)
    code2 = (await client.post("/telegram/link-code", headers=h)).json()["code"]
    async with get_sessionmaker()() as db:
        await db.execute(update(LinkCode).values(expires_at=utcnow().replace(year=2020)))
        await db.commit()
    assert "invalid or expired" in (await say(ctl, 1, f"/link {code2}")).text


async def test_one_telegram_account_cannot_link_to_two_helm_accounts(client, bot_key):
    ctl = make_ctl(client)
    h1, h2 = await signup(client, "one@example.com"), await signup(client, "two@example.com")
    await link(client, h1, ctl, tg=42)
    code = (await client.post("/telegram/link-code", headers=h2)).json()["code"]
    assert "already linked" in (await say(ctl, 42, f"/link {code}")).text


async def test_wrong_codes_are_throttled(client, bot_key):
    ctl = make_ctl(client)
    for _ in range(5):
        assert "invalid or expired" in (await say(ctl, 7, "/link AAAAAAAAAA")).text
    assert "Too many wrong codes" in (await say(ctl, 7, "/link AAAAAAAAAA")).text


async def test_bot_sessions_cannot_change_account_security(client, bot_key):
    h = await signup(client)
    await link(client, h, make_ctl(client), tg=5)
    tok = (await client.post("/telegram/bot/session", json={"telegram_id": 5}, headers={"X-Bot-Key": KEY})).json()["token"]
    bh = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/wallets", headers=bh)).status_code == 200
    for method, path in (("post", "/auth/totp/enable"), ("get", "/auth/sessions"), ("post", "/telegram/link-code"),
                         ("post", "/telegram/unlink")):
        assert (await getattr(client, method)(path, headers=bh)).status_code == 403, path


async def test_unlinking_revokes_bot_access_immediately(client, bot_key):
    h = await signup(client)
    ctl = make_ctl(client)
    await link(client, h, ctl, tg=9)
    tok = (await client.post("/telegram/bot/session", json={"telegram_id": 9}, headers={"X-Bot-Key": KEY})).json()["token"]
    assert (await client.post("/telegram/unlink", headers=h)).status_code == 204
    assert (await client.get("/wallets", headers={"Authorization": f"Bearer {tok}"})).status_code == 401
    assert (await client.post("/telegram/bot/session", json={"telegram_id": 9}, headers={"X-Bot-Key": KEY})).status_code == 404
    assert "isn't linked" in (await say(ctl, 9, "/wallets")).text


async def test_bot_api_refreshes_an_expired_session_transparently(client, bot_key):
    h = await signup(client)
    ctl = make_ctl(client)
    await link(client, h, ctl, tg=3)
    await make_wallets(client, h, 1)
    assert "Your wallets" in (await say(ctl, 3, "/wallets")).text
    async with get_sessionmaker()() as db:  # session revoked behind the bot's back
        await db.execute(update(Session).where(Session.channel == "bot").values(revoked_at=utcnow()))
        await db.commit()
    assert "Your wallets" in (await say(ctl, 3, "/wallets")).text


# --- safety rails --------------------------------------------------------------------------------


async def test_group_chats_are_refused_and_unlinked_users_get_instructions(client, bot_key):
    ctl = make_ctl(client)
    assert "private chat" in (await say(ctl, 1, "/wallets", chat_type="supergroup")).text
    assert "isn't linked" in (await say(ctl, 1, "/wallets")).text
    assert "Helm Terminals bot" in (await say(ctl, 1, "hello there")).text  # unknown text falls back to help


def test_rendering_escapes_user_controlled_text():
    txt = render_launches([{"id": "abcdef123", "name": "<b>x</b>", "symbol": "S&P", "status": "live"}])
    assert "&lt;b&gt;x&lt;/b&gt;" in txt and "S&amp;P" in txt and "<b>x</b>" not in txt
    pf = render_preflight("t", {"kind": "k", "note": "<script>", "warnings": ["<i>bad</i>"]}, False)
    assert "<script>" not in pf and "&lt;i&gt;bad&lt;/i&gt;" in pf


# --- reading -------------------------------------------------------------------------------------


async def test_read_commands(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc)
    for w in holders:
        fake_rpc.tokens[w["address"]] = int(fake_rpc.supply * 0.15)
    fake_rpc.holders = [(holders[0]["address"], int(fake_rpc.supply * 0.15)), ("someone-else", 1_000)]
    await link(client, h, ctl)

    wallets = (await say(ctl, 1001, "/wallets")).text
    assert sid(dev["id"]) in wallets and "SOL" in wallets
    assert sid(lid) in (await say(ctl, 1001, "/launches")).text
    detail = (await say(ctl, 1001, f"/launch {sid(lid)}")).text
    assert "Supply transparency" in detail and "30%" in detail and "← yours" in detail
    assert "Net SOL flow" not in (await say(ctl, 1001, f"/transparency {sid(lid)}")).text
    assert "No launch matches" in (await say(ctl, 1001, "/launch zzzzzz")).text


# --- spending: buy, sell, cancel, expiry, ownership ------------------------------------------------


async def test_bot_spending_commands_are_read_only(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    sent_before = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/buy {sid(lid)} 0.1")
    assert "read-only" in out.text and not out.replies[0].buttons
    assert len(fake_rpc.sent) == sent_before  # the bot did not submit a trade or any additional payment


async def test_bot_spend_confirmation_is_rejected_at_the_api_boundary(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, holders = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    before = len(fake_rpc.sent)
    path = f"/launches/{lid}/trade"
    body = {"side": "buy", "wallet_ids": [w["id"] for w in holders], "amount": 0.1, "mode": "parallel"}
    status, preflight = await ctl.api.call(1001, "POST", path, body)
    assert status == 200
    status, data = await ctl.api.call(1001, "POST", path, {
        **body, "confirm": True, "preflight_hash": preflight["preflight_hash"], "idempotency_key": "bot-attempt"})
    assert status == 403 and "read-only" in data["detail"]
    assert len(fake_rpc.sent) == before


async def test_cancel_sends_nothing_and_other_users_cannot_confirm(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/buy {sid(lid)} 0.1")
    assert "read-only" in out.text and len(fake_rpc.sent) == base


async def test_bot_still_supports_read_only_commands(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    assert sid(lid) in (await say(ctl, 1001, "/launches")).text
    assert "read-only" in (await say(ctl, 1001, "/help")).text


async def test_telegram_buy_limit_is_superseded_by_read_only_mode(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, holders = await live_launch(client, h, fake_rpc)  # 2 holder wallets
    await link(client, h, ctl)
    sent_before = len(fake_rpc.sent)
    assert "read-only" in (await say(ctl, 1001, f"/buy {sid(lid)} 3")).text
    assert len(fake_rpc.sent) == sent_before


async def test_bot_sells_are_blocked_even_when_2fa_is_enabled(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await enable_2fa(client, h)
    await link(client, h, ctl)

    out = await say(ctl, 1001, f"/sell {sid(lid)} 100")
    assert "read-only" in out.text and ctl.awaiting == {}


# --- withdrawals: the only bot action that needs 2FA ---------------------------------------------


async def test_bot_withdrawals_are_read_only_until_action_caps_are_reviewed(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    await link(client, h, ctl)
    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    assert "read-only" in out.text and not out.replies[0].buttons


async def test_bot_withdraw_stays_blocked_even_with_2fa(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    secret = await enable_2fa(client, h)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    assert "read-only" in out.text and len(fake_rpc.sent) == base and ctl.awaiting == {}


async def test_bot_does_not_request_or_process_2fa_for_spends(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    await enable_2fa(client, h)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)
    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    assert "read-only" in out.text
    assert not ctl.awaiting and len(fake_rpc.sent) == base


async def test_claim_burn_and_send_commands_are_read_only(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    fake_rpc.tokens[holders[0]["address"]] = 1_000_000
    await enable_2fa(client, h)
    await link(client, h, ctl)

    for command in (f"/claim {sid(lid)} {sid(dev['id'])}",
                    f"/burn {sid(lid)} {sid(holders[0]['id'])} 50",
                    f"/send {sid(lid)} {sid(holders[0]['id'])} {dev['address']} 10"):
        assert "read-only" in (await say(ctl, 1001, command)).text


async def test_bad_input_is_explained_not_sent(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)
    assert "Usage" in (await say(ctl, 1001, "/buy")).text
    assert "not a valid SOL amount" in (await say(ctl, 1001, f"/buy {sid(lid)} lots")).text
    assert "greater than zero" in (await say(ctl, 1001, f"/buy {sid(lid)} -1")).text
    assert "between 1 and 100" in (await say(ctl, 1001, f"/sell {sid(lid)} 150")).text
    assert len(fake_rpc.sent) == base


def test_keyboard_maps_buttons_for_telegram():
    from helm_bot.controller import Button, Reply
    from helm_bot.main import keyboard

    assert keyboard(Reply("x")) is None
    kb = keyboard(Reply("x", [[Button("✅ Confirm", "ok:abc"), Button("✖ Cancel", "no:abc")]]))
    assert [b.callback_data for b in kb.inline_keyboard[0]] == ["ok:abc", "no:abc"]
