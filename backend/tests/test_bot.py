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
    saved = (s.bot_api_key, s.bot_daily_buy_limit_sol)
    s.bot_api_key = KEY
    yield KEY
    s.bot_api_key, s.bot_daily_buy_limit_sol = saved


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


async def test_buy_needs_a_confirm_tap_and_runs_exactly_once(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)

    out = await say(ctl, 1001, f"/buy {sid(lid)} 0.1")
    assert "Review before confirming" in out.text and len(out.replies[0].buttons[0]) == 2
    assert fake_rpc.sent and len(fake_rpc.sent) == 1  # only the deploy tx so far: nothing sent by the preview
    before = len(fake_rpc.sent)

    done = await tap(ctl, 1001, out.confirm_data)
    assert "Submitted" in done.replies[0].text and "✅" in done.replies[-1].text
    assert len(fake_rpc.sent) == before + len(holders)  # one buy per attached holder wallet

    again = await tap(ctl, 1001, out.confirm_data)  # double tap
    assert "expired or was already used" in again.text
    assert len(fake_rpc.sent) == before + len(holders)


async def test_cancel_sends_nothing_and_other_users_cannot_confirm(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/buy {sid(lid)} 0.1")
    stranger = await tap(ctl, 555, out.confirm_data)  # a different Telegram user pressing the button
    assert "expired or was already used" in stranger.text and len(fake_rpc.sent) == base

    cancelled = await tap(ctl, 1001, out.confirm_data.replace("ok:", "no:"))
    assert "Cancelled" in cancelled.text and len(fake_rpc.sent) == base


async def test_confirmations_expire_after_two_minutes(client, bot_key, fake_rpc):
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    h = await signup(client)
    ctl = Controller(BotApi(client, KEY, clock=clock), PendingStore(clock=clock), poll_interval=0.01, clock=clock)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/buy {sid(lid)} 0.1")
    now[0] += 121
    assert "expired" in (await tap(ctl, 1001, out.confirm_data)).text and len(fake_rpc.sent) == base


async def test_daily_buy_limit_blocks_the_button_not_just_the_send(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, holders = await live_launch(client, h, fake_rpc)  # 2 holder wallets
    await link(client, h, ctl)
    get_settings().bot_daily_buy_limit_sol = 5.0

    too_big = await say(ctl, 1001, f"/buy {sid(lid)} 3")  # 6 SOL total
    assert "daily buy limit" in too_big.text and not too_big.replies[0].buttons

    ok = await say(ctl, 1001, f"/buy {sid(lid)} 2")  # 4 SOL total
    assert "✅" in (await tap(ctl, 1001, ok.confirm_data)).replies[-1].text

    second = await say(ctl, 1001, f"/buy {sid(lid)} 2")  # 4 + 4 > 5
    assert "daily buy limit" in second.text and not second.replies[0].buttons


async def test_sell_does_not_ask_for_2fa_even_when_enabled(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, _, _ = await live_launch(client, h, fake_rpc)
    await enable_2fa(client, h)
    await link(client, h, ctl)

    out = await say(ctl, 1001, f"/sell {sid(lid)} 100")
    assert "2FA" not in out.text
    done = await tap(ctl, 1001, out.confirm_data)
    assert "✅" in done.replies[-1].text and ctl.awaiting == {}


# --- withdrawals: the only bot action that needs 2FA ---------------------------------------------


async def test_withdraw_is_blocked_until_2fa_is_set_up(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    await link(client, h, ctl)
    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    assert "Enable 2FA" in out.text and not out.replies[0].buttons


async def test_withdraw_flow_with_2fa_code(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    secret = await enable_2fa(client, h)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    assert "2FA" in out.text
    prompt = await tap(ctl, 1001, out.confirm_data)
    assert "6-digit" in prompt.text and 1001 in ctl.awaiting

    wrong = await say(ctl, 1001, "000000")
    assert "didn't work" in wrong.text and wrong.replies[0].delete_incoming and len(fake_rpc.sent) == base

    right = await say(ctl, 1001, await totp_code(secret))
    assert right.replies[0].delete_incoming and "Submitted" in right.replies[0].text
    assert "✅" in right.replies[-1].text and len(fake_rpc.sent) == base + 1
    assert ctl.awaiting == {}


async def test_three_wrong_codes_cancel_the_withdrawal(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    await enable_2fa(client, h)
    await link(client, h, ctl)
    base = len(fake_rpc.sent)

    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    await tap(ctl, 1001, out.confirm_data)
    for _ in range(2):
        assert "didn't work" in (await say(ctl, 1001, "111111")).text
    assert "Too many wrong codes" in (await say(ctl, 1001, "111111")).text
    assert ctl.awaiting == {} and len(fake_rpc.sent) == base


async def test_non_code_text_and_cancel_while_waiting_for_2fa(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    a, b = await make_wallets(client, h, 2)
    await enable_2fa(client, h)
    await link(client, h, ctl)
    out = await say(ctl, 1001, f"/withdraw {sid(a['id'])} {b['address']}")
    await tap(ctl, 1001, out.confirm_data)
    assert "6-digit" in (await say(ctl, 1001, "what is this")).text
    assert "Cancelled" in (await say(ctl, 1001, "/cancel")).text and ctl.awaiting == {}


async def test_claim_burn_and_send_commands(client, bot_key, fake_rpc):
    h = await signup(client)
    ctl = make_ctl(client)
    lid, dev, holders = await live_launch(client, h, fake_rpc, holders=1)
    fake_rpc.tokens[holders[0]["address"]] = 1_000_000
    secret = await enable_2fa(client, h)
    await link(client, h, ctl)

    claim = await say(ctl, 1001, f"/claim {sid(lid)} {sid(dev['id'])}")
    assert "✅" in (await tap(ctl, 1001, claim.confirm_data)).replies[-1].text

    burn = await say(ctl, 1001, f"/burn {sid(lid)} {sid(holders[0]['id'])} 50")
    assert "irreversible" in burn.text
    assert "✅" in (await tap(ctl, 1001, burn.confirm_data)).replies[-1].text

    send = await say(ctl, 1001, f"/send {sid(lid)} {sid(holders[0]['id'])} {dev['address']} 10")
    await tap(ctl, 1001, send.confirm_data)
    assert "✅" in (await say(ctl, 1001, await totp_code(secret))).replies[-1].text


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
