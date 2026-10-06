"""Fixes from the 29 Sep 2026 audit (docs/audits/helm-professional-audit.md): each test names the finding it covers."""

import base64

import httpx
import pytest
from sqlalchemy import select
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from app.chains import registry
from app.chains.flap import FlapAdapter
from app.db import get_sessionmaker
from app.deps import get_executor, run_job
from app.main import app
from app.models import AdminAudit, FeeRecord, Job, Launch, RevenueEvent, Transaction, User, Wallet
from app.services.executor import Executor, run_single
from app.signing import get_signer

from .conftest import enable_2fa, signup
from .test_admin import EVM_TREASURY, FakeRelay, boss
from .test_evm_wallets import ETH
from .test_extra_chains import ArcRPC, _nosleep
from .test_flap import PNG, FlapNode, upload_transport

PW = "correct-horse-battery"


# --- REV-3: Relay reconciliation only zeroes a fee on a final outcome ------------------------------------------


async def test_relay_fee_is_only_zeroed_on_a_final_outcome():
    from app.services.revenue import verify_route_fee

    relay = FakeRelay({"delayed": {"status": "delayed"}, "submitted": {"status": "submitted"},
                       "new": {"status": "some-future-status"}, "failed": {"status": "failure"}})
    evs = {k: RevenueEvent(source="route_fee", network="base", asset="USDC", amount_raw=3000, decimals=6,
                           estimated=True, status="claimable", destination=EVM_TREASURY, reference=k)
           for k in ("delayed", "submitted", "new", "failed")}
    got = {k: await verify_route_fee(relay, e) for k, e in evs.items()}
    assert got == {"delayed": "pending", "submitted": "pending", "new": "pending", "failed": "not_collected"}
    for k in ("delayed", "submitted", "new"):  # still in flight: untouched
        assert evs[k].amount_raw == 3000 and evs[k].reconciled_at is None
    assert evs["failed"].amount_raw == 0 and evs["failed"].reconciled_at


# --- REL-2: a job that crashes after sending is unconfirmed, never failed --------------------------------------


async def test_a_job_that_crashes_after_sending_is_unconfirmed(client):
    h = await signup(client, main=False)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        sent, unsent = Job(user_id=uid, kind="trade", params={}), Job(user_id=uid, kind="trade", params={})
        db.add_all([sent, unsent])
        await db.commit()
        ids = sent.id, unsent.id

    async def crash_after_send(session, job):
        session.add(Transaction(job_id=job.id, kind="buy", status="submitted", signature="sig1"))
        await session.commit()
        raise RuntimeError("node went away")

    async def crash_before_send(session, job):
        raise RuntimeError("quote failed")

    await run_job(ids[0], crash_after_send)
    await run_job(ids[1], crash_before_send)
    a = (await client.get(f"/jobs/{ids[0]}", headers=h)).json()
    b = (await client.get(f"/jobs/{ids[1]}", headers=h)).json()
    assert a["status"] == "unconfirmed" and "check the explorer" in a["error"]
    assert b["status"] == "failed" and b["error"] == "quote failed"


async def test_single_step_signature_is_committed_before_broadcast(client, fake_rpc, monkeypatch):
    """A worker crash inside send must leave a durable signature the job endpoint can recheck."""
    from solders.hash import Hash

    from app.chains.solana import build_unsigned, sol_transfer_ix
    from app.signing import get_signer

    h = await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalars().first()
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().first()
        job = Job(user_id=user.id, kind="burn", params={"network": "solana"})
        db.add(job)
        await db.commit()
        job_id, wallet_id = job.id, wallet.id
        payer = Pubkey.from_string(wallet.address)
        raw = build_unsigned(payer, [sol_transfer_ix(payer, payer, 1)], str(Hash.default()))

    async def crash_after_the_node_receives(tx_bytes):
        signature = str(VersionedTransaction.from_bytes(tx_bytes).signatures[0])
        async with get_sessionmaker()() as check_db:
            row = (await check_db.execute(select(Transaction).where(Transaction.signature == signature)))\
                .scalars().first()
            assert row and row.job_id == job_id and row.status == "pending"
        raise RuntimeError("simulated worker interruption after broadcast")

    monkeypatch.setattr(fake_rpc, "send", crash_after_the_node_receives)
    async with get_sessionmaker()() as db:
        job = await db.get(Job, job_id)
        wallet = await db.get(Wallet, wallet_id)
        with pytest.raises(RuntimeError, match="simulated worker interruption"):
            await run_single(db, Executor(fake_rpc, get_signer()), job, wallet, "burn", raw,
                             {wallet.address: wallet.signer_ref or ""})

    async with get_sessionmaker()() as db:
        tx = (await db.execute(select(Transaction).where(Transaction.job_id == job_id))).scalars().one()
        assert tx.signature and tx.status == "pending"


@pytest.mark.parametrize(("chain_status", "job_status", "event_status"), [
    ("confirmed", "unconfirmed", "received"),
    ("failed", "unconfirmed", "failed"),
    ("timeout", "unconfirmed", "pending"),
])
async def test_job_recheck_resolves_solana_signature_and_fee_ledger(
    client, fake_rpc, monkeypatch, chain_status, job_status, event_status
):
    h = await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalars().first()
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalars().first()
        job = Job(user_id=user.id, kind="trade", status="unconfirmed", params={"network": "solana"})
        db.add(job)
        await db.flush()
        db.add(Transaction(job_id=job.id, wallet_id=wallet.id, kind="buy", signature="resume-this-signature",
                           status="timeout"))
        fee = FeeRecord(job_id=job.id, payer_user_id=user.id, wallet_id=wallet.id, side="buy",
                        basis_lamports=1_000_000, platform_lamports=10_000, status="pending",
                        signature="resume-this-signature")
        db.add(fee)
        await db.flush()
        from app.services.revenue import trade_fee_event

        db.add(trade_fee_event(fee, "treasury", None))
        job_id = job.id
        await db.commit()

    async def confirm(signature, **_):
        assert signature == "resume-this-signature"
        return chain_status, "execution error" if chain_status == "failed" else None

    monkeypatch.setattr(fake_rpc, "confirm", confirm)
    response = await client.get(f"/jobs/{job_id}", headers=h)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == job_status
    async with get_sessionmaker()() as db:
        tx = (await db.execute(select(Transaction).where(Transaction.job_id == job_id))).scalars().one()
        fee = (await db.execute(select(FeeRecord).where(FeeRecord.job_id == job_id))).scalars().one()
        event = (await db.execute(select(RevenueEvent).where(RevenueEvent.fee_record_id == fee.id))).scalars().one()
        assert tx.status == chain_status
        assert fee.status == ("pending" if chain_status == "timeout" else chain_status)
        assert event.status == event_status


async def test_unconfirmed_evm_job_rechecks_its_saved_receipt(client, fake_rpc, monkeypatch):
    h = await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalars().first()
        job = Job(user_id=user.id, kind="trade", status="unconfirmed", params={"network": "bsc"})
        db.add(job)
        await db.flush()
        db.add(Transaction(job_id=job.id, kind="buy", signature="0x" + "ab" * 32, status="timeout"))
        job_id = job.id
        await db.commit()

    class ReceiptNode:
        async def receipt(self, signature):
            assert signature == "0x" + "ab" * 32
            return {"status": "0x1"}

    from app.routes import launches as launches_route

    monkeypatch.setattr(launches_route, "shared_evm", lambda network: ReceiptNode())
    response = await client.get(f"/jobs/{job_id}", headers=h)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unconfirmed"  # legacy job lacks a complete execution journal
    assert response.json()["transactions"][0]["status"] == "confirmed"


async def test_deploy_recheck_recovers_hash_saved_before_worker_restart(client, fake_rpc):
    from app.deps import get_executor
    from app.signing import get_signer

    h = await signup(client)
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User))).scalars().first()
        launch = Launch(user_id=user.id, network="bsc", launchpad="pumpfun", status="deploying",
                        name="Restarted", symbol="RST", launchpad_params={})
        db.add(launch)
        await db.flush()
        job = Job(user_id=user.id, launch_id=launch.id, kind="deploy", status="unconfirmed", params={})
        db.add(job)
        await db.flush()
        db.add(Transaction(job_id=job.id, kind="deploy", signature="0x" + "cd" * 32, status="pending"))
        launch_id = launch.id
        await db.commit()

    class ReceiptNode:
        async def receipt(self, signature):
            assert signature == "0x" + "cd" * 32
            return {"status": "0x1", "logs": [], "contractAddress": "0x" + "ef" * 20}

    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": ReceiptNode()})
    try:
        response = await client.post(f"/launches/{launch_id}/recheck", headers=h)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "live"
        assert response.json()["mint_address"] == "0x" + "ef" * 20
    finally:
        app.dependency_overrides.pop(get_executor, None)


# --- PRD-1 / REL-2: an EVM deploy that times out can't be deployed twice ----------------------------------------


class SlowFlapNode(FlapNode):
    """Sends fine, but the receipt can't be read in time; later the node answers."""

    def __init__(self):
        super().__init__("bsc")
        self.receipts: dict | None = None  # None: the node errors; {} : unknown hash; {hash: receipt}
        self.known: set[str] = set()

    async def receipt(self, h):
        if self.receipts is None:
            raise httpx.ConnectError("node unreachable")
        return self.receipts.get(h)

    async def call(self, method, params):
        if method == "eth_getTransactionByHash":
            return {"hash": params[0]} if params[0] in self.known else None
        return await super().call(method, params)


async def test_evm_deploy_that_times_out_stays_deploying_until_the_receipt_decides(client, fake_rpc, monkeypatch):
    from app.services import evm_exec

    monkeypatch.setattr(evm_exec.asyncio, "sleep", _nosleep)
    node = SlowFlapNode()
    registry._cache["flap-sh"] = FlapAdapter("flap-sh", "bsc", node, client=httpx.AsyncClient(transport=upload_transport([])))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    try:
        h = await signup(client)
        dev = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]
        node.balances[dev["address"].lower()] = ETH
        lid = (await client.post("/launches", json={"launchpad": "flap-sh", "name": "Slow Cat", "symbol": "slow",
                                                    "dev_wallet_id": dev["id"], "dev_buy_sol": 0.01,
                                                    "image_b64": base64.b64encode(PNG).decode()}, headers=h)).json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "slow-1"}, headers=h)
        assert (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()["status"] == "unconfirmed"
        l = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert l["status"] == "deploying" and l["deploy_tx"]
        # it can't be deployed again; archiving and restoring brings it back as deploying, never a draft
        again = await client.post(f"/launches/{lid}/deploy", json={}, headers=h)
        assert again.status_code == 409
        assert (await client.post(f"/launches/{lid}/archive", headers=h)).json()["status"] == "archived"
        assert (await client.post(f"/launches/{lid}/restore", headers=h)).json()["status"] == "deploying"
        # still unreadable: stays deploying
        assert (await client.post(f"/launches/{lid}/recheck", json={}, headers=h)).json()["status"] == "deploying"
        # the receipt shows up: live, with the token the plan predicted
        node.receipts = {l["deploy_tx"]: {"status": "0x1", "logs": [], "gasUsed": "0x1", "effectiveGasPrice": "0x1"}}
        l2 = (await client.post(f"/launches/{lid}/recheck", json={}, headers=h)).json()
        assert l2["status"] == "live" and l2["mint_address"] and l2["mint_address"].lower().endswith("8888")
    finally:
        registry._cache.pop("flap-sh", None)
        app.dependency_overrides.pop(get_executor, None)


async def test_a_new_deploy_attempt_forgets_the_previous_attempts_hash(client, fake_rpc, monkeypatch):
    """A reverted first attempt leaves its hash behind; the retry must not be judged by that old receipt."""
    from app.services import evm_exec

    monkeypatch.setattr(evm_exec.asyncio, "sleep", _nosleep)
    node = SlowFlapNode()
    registry._cache["flap-sh"] = FlapAdapter("flap-sh", "bsc", node, client=httpx.AsyncClient(transport=upload_transport([])))
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    try:
        h = await signup(client)
        dev = (await client.post("/wallets", json={"network": "bsc", "count": 1}, headers=h)).json()[0]
        node.balances[dev["address"].lower()] = ETH
        lid = (await client.post("/launches", json={"launchpad": "flap-sh", "name": "Twice", "symbol": "twc",
                                                    "dev_wallet_id": dev["id"], "dev_buy_sol": 0.01,
                                                    "image_b64": base64.b64encode(PNG).decode()}, headers=h)).json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                          "idempotency_key": "t-1"}, headers=h)
        first = (await client.get(f"/launches/{lid}", headers=h)).json()["deploy_tx"]
        node.receipts = {first: {"status": "0x0"}}  # the first attempt reverted
        l = (await client.post(f"/launches/{lid}/recheck", json={}, headers=h)).json()
        assert l["status"] == "failed" and l["deploy_error"]
        node.receipts = None  # the retry times out too
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                          "idempotency_key": "t-2"}, headers=h)
        l = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert l["status"] == "deploying" and l["deploy_tx"] != first and not l["deploy_error"]
        node.receipts = {first: {"status": "0x0"}}  # only the old hash has a receipt: the retry isn't judged by it
        assert (await client.post(f"/launches/{lid}/recheck", json={}, headers=h)).json()["status"] == "deploying"
    finally:
        registry._cache.pop("flap-sh", None)
        app.dependency_overrides.pop(get_executor, None)


async def test_evm_deploy_recheck_fails_only_on_a_revert(client, fake_rpc):
    from datetime import timedelta

    from app.models import utcnow
    from app.routes.launches import _recheck_evm_deploy

    node = SlowFlapNode()
    node.receipts = {"0xrev": {"status": "0x0"}}
    ex = Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    await signup(client)
    async with get_sessionmaker()() as db:
        u = (await db.execute(select(User))).scalars().first()
        old = (utcnow() - timedelta(hours=1)).isoformat()
        rev = Launch(user_id=u.id, network="bsc", launchpad="flap-sh", name="R", symbol="R", status="deploying",
                     launchpad_params={"evm_deploy_tx": "0xrev", "unconfirmed_since": old})
        gone = Launch(user_id=u.id, network="bsc", launchpad="flap-sh", name="G", symbol="G", status="deploying",
                      launchpad_params={"evm_deploy_tx": "0xgone", "unconfirmed_since": old})
        pending = Launch(user_id=u.id, network="bsc", launchpad="flap-sh", name="P", symbol="P", status="deploying",
                         launchpad_params={"evm_deploy_tx": "0xpend", "unconfirmed_since": old})
        db.add_all([rev, gone, pending])
        await db.commit()
        node.known.add("0xpend")  # still in the mempool: it can land
        for l in (rev, gone, pending):
            await _recheck_evm_deploy(l, db, ex, u)
        assert rev.status == "failed" and "reverted" in rev.launchpad_params["deploy_error"]
        # a hash this node doesn't know may still be mined elsewhere: never "failed" on that alone
        assert gone.status == "deploying" and pending.status == "deploying"


# --- PRD-4: a transfer waiting on the user's own wallet isn't "lost" ------------------------------------------


async def test_a_transfer_waiting_for_the_users_wallet_isnt_called_lost(client):
    from datetime import timedelta

    from app.models import utcnow

    h = await signup(client, main=False)
    async with get_sessionmaker()() as db:
        uid = (await db.execute(select(User.id))).scalars().first()
        old = utcnow() - timedelta(hours=2)
        waiting = Job(user_id=uid, kind="external_transfer", status="pending", params={"network": "arc"},
                      created_at=old, updated_at=old)
        helm = Job(user_id=uid, kind="trade", status="submitted", params={}, created_at=old, updated_at=old)
        db.add_all([waiting, helm])
        await db.commit()
        ids = waiting.id, helm.id
    w = (await client.get(f"/jobs/{ids[0]}", headers=h)).json()
    assert w["status"] == "pending" and w["network"] == "arc"
    assert (await client.get(f"/jobs/{ids[1]}", headers=h)).json()["status"] == "unconfirmed"


# --- PRD-2: allocation locks apply to the generic Arc token transfer -------------------------------------------


async def test_generic_token_transfer_respects_allocation_locks(client, monkeypatch):
    from app.config import get_settings
    from app.services import allocations

    s = get_settings()
    monkeypatch.setattr(s, "extra_networks", "arc")
    rpc = ArcRPC()
    rpc.balance = 10**20
    app.dependency_overrides[get_executor] = lambda: Executor(None, get_signer(), evm_rpcs={"arc": rpc})
    token = "0x" + "ef" * 20
    try:
        h = await signup(client, main=False)
        w = (await client.post("/wallets", json={"network": "arc", "count": 1}, headers=h)).json()[0]
        seen = []

        async def fake_lock(db, launch, wallet, balance_raw, out_raw, decimals):
            seen.append((launch.symbol, balance_raw, out_raw))
            return "2 $MINE locked by your allocation plan"

        monkeypatch.setattr(allocations, "lock_warning", fake_lock)
        body = {"wallet_id": w["id"], "to": "0x" + "cd" * 20, "amount": "1.5", "token": token}
        assert not (await client.post("/chains/arc/transfer", json=body, headers=h)).json()["preflight"]["warnings"]
        async with get_sessionmaker()() as db:  # the token is one of this user's launches
            u = (await db.execute(select(User))).scalars().first()
            db.add(Launch(user_id=u.id, network="arc", launchpad="tolly", name="Mine", symbol="MINE", status="live",
                          mint_address=token))
            await db.commit()
        pf = (await client.post("/chains/arc/transfer", json=body, headers=h)).json()["preflight"]
        assert any("locked by your allocation plan" in x for x in pf["warnings"])
        assert seen and seen[-1][0] == "MINE" and seen[-1][2] == 1_500_000
    finally:
        app.dependency_overrides.pop(get_executor, None)


# --- SEC-7: provider links must be https ----------------------------------------------------------------------


def test_provider_links_are_https_only():
    from app.services.market import _https

    assert _https("https://dexscreener.com/solana/abc") == "https://dexscreener.com/solana/abc"
    for bad in ("javascript:alert(1)", "JAVASCRIPT:alert(1)", "http://x.com", "data:text/html,x", None, 5, ""):
        assert _https(bad) is None


# --- SEC-8: turning 2FA on ends the account's other web sessions ----------------------------------------------


async def test_turning_on_2fa_signs_out_other_web_sessions(client):
    h = await signup(client, main=False)
    other = await client.post("/auth/login", json={"email": "a@example.com", "password": PW})
    h2 = {"Authorization": f"Bearer {other.json()['token']}"}
    assert (await client.get("/auth/me", headers=h2)).status_code == 200
    await enable_2fa(client, h)
    assert (await client.get("/auth/me", headers=h)).status_code == 200  # this session stays
    assert (await client.get("/auth/me", headers=h2)).status_code == 401  # the other one is over


# --- SEC-11: bounded inputs --------------------------------------------------------------------------------------


async def test_oversized_login_and_socials_are_refused(client):
    r = await client.post("/auth/login", json={"email": "a@example.com", "password": "x" * 5000})
    assert r.status_code == 422 and "x" * 50 not in r.text
    h = await signup(client, main=False)
    base = {"launchpad": "pumpfun", "name": "A", "symbol": "A"}
    r = await client.post("/launches", json={**base, "socials": {f"k{i}": "v" for i in range(50)}}, headers=h)
    assert r.status_code == 422
    r = await client.post("/launches", json={**base, "socials": {"twitter": "x" * 5000}}, headers=h)
    assert r.status_code == 422
    r = await client.post("/launches", json={**base, "socials": {"twitter": "https://x.com/a"}}, headers=h)
    assert r.status_code == 201


# --- SEC-9: signer errors don't reach users verbatim -------------------------------------------------------------


async def test_turnkey_http_errors_are_generic_for_users():
    import pytest

    from app.signing.base import SigningError
    from app.signing.turnkey import TurnkeySigner
    from cryptography.hazmat.primitives.asymmetric import ec

    def handler(req):
        return httpx.Response(403, text='{"message":"policy pol_secret_123 denied org org_abc"}')

    key = ec.generate_private_key(ec.SECP256R1())
    s = TurnkeySigner("https://tk.test", "org1", "02ab", format(key.private_numbers().private_value, "064x"),
                      client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(SigningError) as e:
        await s.create_wallet("u1", "base", "W")
    assert "HTTP 403" in str(e.value) and "pol_secret" not in str(e.value) and "org_abc" not in str(e.value)


# --- PRIV-4: admin user searches are audited, without the search text --------------------------------------------


async def test_admin_user_search_is_audited_without_the_query(client):
    hb, _ = await boss(client)
    await signup(client, "someone@example.com", main=False)
    await client.get("/admin/users", headers=hb)  # plain listing: not audited
    r = await client.get("/admin/users?q=someone@example.com", headers=hb)
    assert r.status_code == 200 and r.json()["total"] == 1
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(AdminAudit).where(AdminAudit.action == "users.searched"))).scalars().all()
    assert len(rows) == 1 and rows[0].changes == {"results": 1, "query_chars": len("someone@example.com")}
    assert "someone" not in str(rows[0].changes) + str(rows[0].context)


# --- PRD-3: the Telegram bot treats unconfirmed as final and links the right explorer ---------------------------


def test_bot_renders_unconfirmed_jobs_with_the_right_explorer():
    from helm_bot.controller import TERMINAL
    from helm_bot.render import render_job, render_workspace

    assert "unconfirmed" in TERMINAL
    job = {"id": "j1", "kind": "withdraw", "status": "unconfirmed", "error": None, "network": "base",
           "transactions": [{"kind": "withdraw", "status": "submitted", "signature": "0x" + "ab" * 32}]}
    text = render_job(job)
    assert "❓" in text and "before trying again" in text and "https://basescan.org/tx/0x" in text
    assert "solscan" not in text
    unknown = render_job({**job, "network": None})
    assert "<a href" not in unknown  # no guessed explorer
    ws = render_workspace({"launch": {"name": "T", "symbol": "T", "status": "live", "network": "arc",
                                      "mint_address": "0x" + "ab" * 20}, "wallets": [], "rpc_error": None}, section="none")
    assert "https://explorer.arc.io/token/" in ws


# --- UX-1: the Tracker takes addresses on every chain Helm lists ---------------------------------------------------


async def test_tracker_accepts_arc_addresses_and_refuses_removed_networks(client):
    h = await signup(client, main=False)
    r = await client.post("/tracker", json={"network": "arc", "address": "0x" + "ab" * 20, "label": "arc"}, headers=h)
    assert r.status_code == 201, r.text
    for net, addr in (("arc", "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"), ("tron", "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"),
                      ("near", "alice.near"), ("ton", "UQDKbjIcfM6ezt8KjKJJLshZJJSqX7XOA4ff-W72r5gqPuwA")):
        assert (await client.post("/tracker", json={"network": net, "address": addr}, headers=h)).status_code == 400


async def test_a_multi_step_deploy_that_fails_after_the_token_exists_is_never_redeployable(client, fake_rpc):
    """Uniswap V2: the token is created, then adding liquidity reverts. Marking that "failed" would let a retry create a
    second token, so it stays deploying with a note to check the explorer."""
    from app.chains.uniswap_v2 import UniswapV2Adapter

    from .test_uniswap_v2 import NEW_TOKEN, UniNode

    class LiquidityReverts(UniNode):
        async def receipt(self, h):
            rec = await super().receipt(h)
            return {**rec, "status": "0x0"} if len(self.sent) == 3 else rec

    node = LiquidityReverts()
    registry._cache["uniswap-v2"] = UniswapV2Adapter(node)
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"ethereum": node})
    try:
        h = await signup(client)
        (dev,) = (await client.post("/wallets", json={"network": "ethereum", "count": 1}, headers=h)).json()
        node.balances[dev["address"].lower()] = 2 * ETH
        lid = (await client.post("/launches", json={"launchpad": "uniswap-v2", "name": "Half", "symbol": "half",
                                                    "dev_wallet_id": dev["id"], "dev_buy_sol": 0.5,
                                                    "options": {"lp_share_pct": 90, "lp_tokens": "burn"}},
                                 headers=h)).json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        r = await client.post(f"/launches/{lid}/deploy", json={"confirm": True, "preflight_hash": pf["preflight_hash"],
                                                              "idempotency_key": "half-1"}, headers=h)
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert [t["status"] for t in job["transactions"]] == ["confirmed", "confirmed", "failed"]
        l = (await client.get(f"/launches/{lid}", headers=h)).json()
        assert l["status"] == "deploying" and "don't deploy again" in l["deploy_note"]
        assert (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).status_code == 409
        assert NEW_TOKEN  # the token exists on-chain in this scenario; the owner settles it from the explorer
    finally:
        registry._cache.pop("uniswap-v2", None)
        app.dependency_overrides.pop(get_executor, None)


async def test_a_deploy_still_running_cant_be_archived(client):
    h = await signup(client, main=False)
    async with get_sessionmaker()() as db:
        u = (await db.execute(select(User))).scalars().first()
        l = Launch(user_id=u.id, network="bsc", launchpad="flap-sh", name="R", symbol="R", status="deploying")
        db.add(l)
        await db.flush()
        db.add(Job(user_id=u.id, launch_id=l.id, kind="deploy", status="submitted", params={}))
        await db.commit()
        lid = l.id
    r = await client.post(f"/launches/{lid}/archive", headers=h)
    assert r.status_code == 409 and "still running" in r.text
