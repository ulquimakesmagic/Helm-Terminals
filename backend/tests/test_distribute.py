"""Disperse and consolidate across your own wallets, on Solana and EVM chains."""

from eth_abi import decode as abi_decode
from solders.transaction import VersionedTransaction

from app.chains.flap import selector
from app.deps import get_executor
from app.main import app
from app.services.chain_balances import stable_for
from app.services.executor import Executor
from app.signing import get_signer

from .conftest import signup
from .test_evm_wallets import ETH
from .test_flap import FlapNode

USDC_SOL = stable_for("solana")[1]


async def confirm(client, h, path, body, key):
    pf = (await client.post(path, json=body, headers=h)).json()
    r = await client.post(path, json={**body, "confirm": True, "preflight_hash": pf["preflight_hash"],
                                      "idempotency_key": key}, headers=h)
    assert r.status_code == 200, r.text
    return pf, (await client.get(f"/jobs/{r.json()['job_id']}", headers=h)).json()


async def test_solana_even_disperse_batches_and_validates(client, fake_rpc):
    h = await signup(client)
    ws = (await client.post("/wallets", json={"count": 4}, headers=h)).json()
    src, targets = ws[0], ws[1:]
    ids = [t["id"] for t in targets]

    r = await client.post("/wallets/disperse", json={"from_wallet_id": src["id"], "to_wallet_ids": [src["id"]],
                                                    "total": 1}, headers=h)
    assert r.status_code == 400  # the source can't receive
    r = await client.post("/wallets/disperse", json={"from_wallet_id": src["id"], "to_wallet_ids": ids}, headers=h)
    assert r.status_code == 422  # an even split needs a total or a percent

    pf = (await client.post("/wallets/disperse", json={"from_wallet_id": src["id"], "to_wallet_ids": ids,
                                                       "total": 9}, headers=h)).json()
    assert "less than the 9" in pf["preflight"]["warnings"][0]  # only 5 SOL in the wallet

    body = {"from_wallet_id": src["id"], "to_wallet_ids": ids, "total": 1}
    pf, job = await confirm(client, h, "/wallets/disperse", body, "disperse-sol-1")
    amounts = [t["amount"] for t in pf["preflight"]["to"]]
    assert abs(sum(amounts) - 1) < 1e-9 and pf["preflight"]["asset"] == "SOL" and pf["preflight"]["platform_fee"] == "none"
    assert job["status"] == "confirmed" and [t["kind"] for t in job["transactions"]] == ["disperse"]  # one tx for 3
    tx = VersionedTransaction.from_bytes(fake_rpc.sent[-1])
    assert len(tx.message.instructions) == 3


async def test_solana_token_consolidate_skips_empty_wallets(client, fake_rpc):
    h = await signup(client)
    ws = (await client.post("/wallets", json={"count": 3}, headers=h)).json()
    dest, a, b = ws
    fake_rpc.tokens[a["address"]] = 5_000_000  # 5 USDC
    body = {"from_wallet_ids": [a["id"], b["id"]], "to_wallet_id": dest["id"], "asset": "stable"}
    pf, job = await confirm(client, h, "/wallets/consolidate", body, "consolidate-usdc-1")
    assert pf["preflight"]["asset"] == "USDC" and pf["preflight"]["token"] == USDC_SOL
    assert pf["preflight"]["total"] == 5 and pf["preflight"]["skipped_empty"] == [b["address"]]
    assert job["status"] == "confirmed" and len(job["transactions"]) == 1
    ixs = VersionedTransaction.from_bytes(fake_rpc.sent[-1]).message.instructions
    assert len(ixs) == 2  # open the destination's token account (if needed), then transfer


async def test_evm_custom_disperse_and_launch_token_consolidate(client, fake_rpc):
    node = FlapNode("bsc")
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"bsc": node})
    try:
        h = await signup(client)
        ws = (await client.post("/wallets", json={"network": "bsc", "count": 3}, headers=h)).json()
        src, t1, t2 = ws
        node.balances[src["address"].lower()] = ETH
        body = {"from_wallet_id": src["id"], "to_wallet_ids": [t1["id"], t2["id"]], "split": "custom",
                "amounts": [0.1, 0.25]}
        pf, job = await confirm(client, h, "/wallets/disperse", body, "disperse-bnb-1")
        assert pf["preflight"]["asset"] == "BNB" and pf["preflight"]["transactions"] == 2
        assert job["status"] == "confirmed"
        assert node.balances[t2["address"].lower()] == ETH // 4

        token = "0x" + "ab" * 20
        for w in (t1, t2):
            node.tokens[w["address"].lower()] = 10**20
        body = {"from_wallet_ids": [t1["id"], t2["id"]], "to_wallet_id": src["id"], "asset": token, "percent": 50}
        pf, job = await confirm(client, h, "/wallets/consolidate", body, "consolidate-tok-1")
        assert pf["preflight"]["total"] == 100  # 50% of 100 tokens from each of two wallets
        assert job["status"] == "confirmed" and len(job["transactions"]) == 2
        sent = node.sent[-1]
        assert sent["to"].lower() == token and sent["data"][:4] == selector("transfer(address,uint256)")
        to, amount = abi_decode(["address", "uint256"], sent["data"][4:])
        assert to.lower() == src["address"].lower() and amount == 5 * 10**19

        r = await client.post("/wallets/consolidate", json={"from_wallet_ids": [t1["id"]], "to_wallet_id": src["id"],
                                                            "asset": "not-an-address"}, headers=h)
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(get_executor, None)


async def test_launch_token_disperse_attaches_wallets_for_transparency(client, fake_rpc):
    from .test_automation import live_launch

    h = await signup(client)
    launch_id, dev, _ = await live_launch(client, h, fake_rpc, holders=1)
    mint = (await client.get(f"/launches/{launch_id}", headers=h)).json()["mint_address"]
    fresh = (await client.post("/wallets", json={"count": 2}, headers=h)).json()
    fake_rpc.tokens[dev["address"]] = 10**9
    body = {"from_wallet_id": dev["id"], "to_wallet_ids": [w["id"] for w in fresh], "asset": mint, "percent": 50}
    pf, job = await confirm(client, h, "/wallets/disperse", body, "disperse-launch-1")
    assert "transparency panel" in pf["preflight"]["transparency"]
    ws = (await client.get(f"/launches/{launch_id}/workspace", headers=h)).json()
    attached = {w["address"] for w in ws["wallets"]}
    assert all(w["address"] in attached for w in fresh)


async def test_sweep_moves_stable_then_coin(client, fake_rpc):
    h = await signup(client)
    ws = (await client.post("/wallets", json={"count": 4}, headers=h)).json()
    dest, a, b = ws[0], ws[1], ws[2]
    fake_rpc.tokens[a["address"]] = 3_000_000   # 3 USDC
    fake_rpc.tokens[b["address"]] = 500_000     # 0.5 USDC
    body = {"from_wallet_ids": [a["id"], b["id"]], "to_wallet_id": dest["id"]}
    r = await client.post("/wallets/sweep", json={"from_wallet_ids": [dest["id"]], "to_wallet_id": dest["id"]}, headers=h)
    assert r.status_code == 400  # the destination can't also be a source
    pf, job = await confirm(client, h, "/wallets/sweep", body, "sweep-1")
    p = pf["preflight"]
    assert p["wallets_swept"] == 2 and p["skipped_empty"] == []
    assert p["total_stable"] == 3.5 and abs(p["total_native"] - 2 * (5 - 0.000005)) < 1e-9
    assert job["status"] == "confirmed", job
    kinds = [t["kind"] for t in job["transactions"]]
    assert kinds == ["sweep", "sweep"]  # atomic token transfer, rent recovery, SOL transfer per source


async def test_sweep_underfunded_wallet_returns_warning_not_server_error(client, fake_rpc):
    h = await signup(client)
    dest, source = (await client.post("/wallets", json={"count": 2}, headers=h)).json()
    fake_rpc.tokens[source["address"]] = 1_000_000
    fake_rpc.balances[source["address"]] = 0
    r = await client.post("/wallets/sweep", headers=h, json={
        "from_wallet_ids": [source["id"]], "to_wallet_id": dest["id"]})
    assert r.status_code == 200, r.text
    assert "not configured" in r.json()["preflight"]["warnings"][0]
    assert not fake_rpc.sent


async def test_sweep_rent_saved_after_first_wallet_is_also_transferred(client, fake_rpc):
    h = await signup(client)
    dest, a, b = (await client.post("/wallets", json={"count": 3}, headers=h)).json()
    for w in (a, b):
        fake_rpc.tokens[w["address"]] = 1_000_000
    original_send = fake_rpc.send
    async def send(raw):
        result = await original_send(raw)
        fake_rpc.tokens[dest["address"]] = 2_000_000
        return result
    fake_rpc.send = send
    pf, job = await confirm(client, h, "/wallets/sweep", {
        "from_wallet_ids": [a["id"], b["id"]], "to_wallet_id": dest["id"]}, "saved-rent")
    assert job["status"] == "confirmed", job
    native = []
    for raw in fake_rpc.sent:
        msg = VersionedTransaction.from_bytes(raw).message
        native.append(int.from_bytes(msg.instructions[-1].data[4:12], "little"))
    assert native[1] - native[0] == 2_039_280


async def test_sponsored_sweep_signs_both_wallets_and_replay_does_not_resend(client, fake_rpc, monkeypatch):
    from app.config import get_settings
    from app.db import get_sessionmaker
    from app.models import Wallet
    h = await signup(client)
    dest, source, sponsor = (await client.post("/wallets", json={"count": 3}, headers=h)).json()
    async with get_sessionmaker()() as session:
        wallet = await session.get(Wallet, sponsor["id"])
        ref = wallet.signer_ref
    settings = get_settings()
    monkeypatch.setattr(settings, "solana_cash_sponsor_address", sponsor["address"])
    monkeypatch.setattr(settings, "solana_cash_sponsor_ref", ref)
    monkeypatch.setattr(settings, "solana_cash_sponsor_usdc_raw", 50_000)
    fake_rpc.balances[source["address"]] = 0
    fake_rpc.balances[sponsor["address"]] = 100_000_000
    fake_rpc.tokens[source["address"]] = 1_000_000
    fake_rpc.tokens[sponsor["address"]] = 0
    body = {"from_wallet_ids": [source["id"]], "to_wallet_id": dest["id"]}
    pf, job = await confirm(client, h, "/wallets/sweep", body, "sponsor-sweep")
    assert job["status"] == "confirmed", job
    assert pf["preflight"]["fee_reimbursement_usdc"] == 0.05
    assert VersionedTransaction.from_bytes(fake_rpc.sent[-1]).verify_with_results() == [True, True]
    count = len(fake_rpc.sent)
    replay = await client.post("/wallets/sweep", headers=h, json={**body,
        "confirm": True, "preflight_hash": pf["preflight_hash"], "idempotency_key": "sponsor-sweep"})
    assert replay.status_code == 200 and len(fake_rpc.sent) == count
