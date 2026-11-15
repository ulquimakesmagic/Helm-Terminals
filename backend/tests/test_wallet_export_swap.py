"""Wallet swaps: the guardrails around a third-party router, checked end to end offline
(fake signer RPC, fake Jupiter, mocked Relay)."""
import base64

import pytest
from solders.hash import Hash
from solders.instruction import Instruction
from solders.pubkey import Pubkey

from sqlalchemy import select

from app.main import app
from app.models import Transaction
from app.routes import wallets as wallets_module

from .conftest import signup
from .test_api import confirm, make_wallets


# --- Jupiter swaps ---------------------------------------------------------------------------------

QUOTE = {
    "inputMint": "So11111111111111111111111111111111111111112",
    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "inAmount": "100000000", "outAmount": "12225952", "otherAmountThreshold": "12164823",
    "priceImpactPct": "0", "routePlan": [{"swapInfo": {"label": "GoonFi V2"}, "percent": 100}],
}


class FakeJupiter:
    def __init__(self):
        pass

    async def quote(self, input_mint, output_mint, amount_raw, slippage_bps):
        q = dict(QUOTE)
        q["inAmount"] = str(amount_raw)
        return q

    async def swap_tx(self, quote, payer):
        # A real unsigned versioned transaction whose only required signer is the wallet itself.
        payer_key = Pubkey.from_string(payer)
        ix = Instruction(Pubkey.from_string("11111111111111111111111111111111"), b"", [])
        from app.chains.solana import build_unsigned

        raw = build_unsigned(payer_key, [ix], str(Hash.default()))
        assert base64.b64decode(base64.b64encode(raw)) == raw
        return raw

    async def token(self, mint):
        return {"symbol": "USDC", "decimals": 6}


@pytest.fixture
def fake_jupiter(monkeypatch):
    made = FakeJupiter()
    monkeypatch.setattr(wallets_module, "Jupiter", lambda: made)
    return made


def deliver(fake_rpc, mint, amount, lamports_out=100_005_000):
    """Model a well-behaved swap in simulation: the wallet pays SOL and receives `amount` of `mint`."""
    from app.services.solana_guard import TokenState, WalletEffects

    def effects(tx, wallet):
        before = 5 * 10**9
        ata = "Ata" + wallet[:20]
        return WalletEffects(before, before - lamports_out, "11111111111111111111111111111111", {},
                             {ata: TokenState(ata, mint, amount, wallet)})
    fake_rpc.effects_override = effects


async def test_swap_preflight_then_confirmed_job(client, fake_rpc, fake_jupiter):
    h = await signup(client, "swap@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    deliver(fake_rpc, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 12164823)
    body = {"wallet_id": w["id"], "input_mint": "native", "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "amount": 0.1, "slippage_pct": 0.5}
    pf, r = await confirm(client, h, "/wallets/swap", body)
    assert r.status_code == 200, r.text
    summary = pf["preflight"]
    assert summary["input"].startswith("0.1 SOL") and "USDC" in summary["output"]
    assert summary["route"] == "GoonFi V2" and summary["platform_fee"].startswith("1%")
    j = r.json()
    status = (await client.get(f"/jobs/{j['job_id']}", headers=h)).json()["status"]
    assert status == "confirmed", j
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(Transaction).where(Transaction.kind == "swap"))).scalars().all()
        assert rows and rows[-1].status == "confirmed"


async def test_swap_refuses_unknown_tokens_and_same_token(client, fake_rpc, fake_jupiter):
    h = await signup(client, "bad@example.com", main=False)
    w = (await make_wallets(client, h, 1))[0]
    same = {"wallet_id": w["id"], "input_mint": "native", "output_mint": "native", "amount": 1}
    assert (await client.post("/wallets/swap", json=same, headers=h)).status_code == 400


async def test_swap_unsupported_chain_is_refused(client, fake_rpc, fake_jupiter):
    h = await signup(client, "arc@example.com", main=False)
    r = await client.post("/wallets", json={"label": "Arc", "network": "arc"}, headers=h)
    wid = r.json()[-1]["id"]
    out = await client.post("/wallets/swap", json={"wallet_id": wid, "input_mint": "native",
                                                   "output_mint": "native", "amount": 1}, headers=h)
    assert out.status_code == 400 and "Bridge route" in out.text


async def test_evm_swap_routes_through_relay(client, fake_rpc, monkeypatch):
    """Same-chain EVM swap: Relay quote for the preflight, approve capped at the quoted need, then the swap."""
    import httpx as _httpx

    from app.chains.relay import EVM_NATIVE, Relay
    from app.deps import get_executor
    from app.services.executor import Executor
    from app.signing import get_signer

    from .test_bridge import BASE_USDC, ETH, TokenNode, relay_mock

    seen: list = []
    relay = Relay("https://relay.test", client=_httpx.AsyncClient(transport=relay_mock(seen, ["success"])))
    base = TokenNode("base")
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"base": base}, relay=relay)
    try:
        h = await signup(client, "evmswap@example.com", main=False)
        w = (await client.post("/wallets", json={"network": "base", "count": 1}, headers=h)).json()[-1]
        base.tokens[w["address"].lower()] = 50 * 10**6          # 50 USDC
        base.balances[w["address"].lower()] = ETH // 100        # gas money
        body = {"wallet_id": w["id"], "input_mint": BASE_USDC, "output_mint": "native", "amount": 20}
        pre = (await client.post("/wallets/swap", json=body, headers=h)).json()
        p = pre["preflight"]
        assert p["kind"] == "swap" and p["network"] == "base" and p["chain"] == "Base"
        assert p["input"] == "20 USDC" and p["estimated_output"] == "0.03 ETH"
        assert p["quote"]["fees_usd"]["helm_route_fee"] == 0 and p["platform_fee"].startswith("1%")
        same = await client.post("/wallets/swap", json={**body, "output_mint": BASE_USDC}, headers=h)
        assert same.status_code == 400 and "same token" in same.text
        bad = await client.post("/wallets/swap", json={**body, "input_mint": "0x1234"}, headers=h)
        assert bad.status_code == 400 and "valid token address" in bad.text

        r = await client.post("/wallets/swap", json={**body, "confirm": True, "preflight_hash": pre["preflight_hash"],
                                                     "quote_approval": pre["quote_approval"], "idempotency_key": "evm-swap"}, headers=h)
        assert r.status_code == 200, r.text
        job = (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()
        assert job["status"] == "confirmed", job
        assert [t["kind"] for t in job["transactions"]] == ["swap_approve", "swap", "trade_fee"]
        # Provider-built transactions go out byte-for-byte (the guard checked, not rewrote, them).
        assert base.sent[-3]["data"] == bytes.fromhex("095ea7b3" + "00" * 12 + "cd" * 20 + "00" * 32)
        assert base.sent[-2]["to"].lower() == "0x" + "cd" * 20 and base.sent[-2]["data"] == bytes.fromhex("abcdef")
        q = seen[-1]
        assert q["originChainId"] == 8453 and q["destinationChainId"] == 8453  # same-chain route
        assert q["originCurrency"] == BASE_USDC and q["destinationCurrency"] == EVM_NATIVE
        assert q["user"] == q["recipient"] == w["address"]
    finally:
        app.dependency_overrides.pop(get_executor, None)
