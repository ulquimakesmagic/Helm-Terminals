"""Consent, pinned deployment, legacy compatibility and exact receipt accounting."""
import httpx
import pytest
from eth_abi import decode, encode
from eth_utils import keccak
from sqlalchemy import select

from app.chains import pons_rewards as rewards, registry
from app.chains.base import AdapterError, ClaimRequest
from app.chains.pons import FEE_ESCROW, PonsAdapter, selector
from app.config import get_settings
from app.db import get_sessionmaker
from app.deps import get_executor
from app.main import app
from app.models import Job, Launch, RevenueEvent
from app.services import evm_exec, platform_config as pc
from app.services.executor import Executor
from app.signing import get_signer
from .conftest import signup
from .test_pons import PonsNode, req

FACTORY = "0x" + "fa" * 20
TREASURY = "0x" + "cd" * 20
CREATOR = "0x" + "ab" * 20


class RewardNode(PonsNode):
    ready = True
    bad_code = False
    payout = 600
    config = None

    async def call(self, method, params):
        if method == "eth_getCode":
            return ("0x" + (rewards.FACTORY_CODE_V2 if getattr(self, "factory_version", 1) == 2 else rewards.FACTORY_CODE).hex() if params[0].lower() == FACTORY.lower()
                    and not self.bad_code else "0x6000")
        if method == "eth_call" and params[0]["to"].lower() == FACTORY.lower():
            assert bytes.fromhex(params[0]["data"][2:]) == rewards.distribution_data(self.config)
            return "0x"
        return await super().call(method, params)

    async def receipt(self, h):
        if not self.ready:
            raise httpx.ConnectError("provider unavailable")
        receipt = await super().receipt(h)
        if self.sent and self.sent[-1]["to"].lower() == FACTORY.lower():
            receipt["logs"] = [{"address": self.config["splitter"], "topics": [rewards.RELEASED,
                "0x" + "00" * 12 + self.config["treasury"][2:]], "data": "0x" + encode(["uint256"], [self.payout]).hex()}]
        return receipt


@pytest.fixture
def setup_rewards(monkeypatch):
    # These two protocol stubs stand in for the separately source-verified Pons deployments.
    # The Helm factory's actual compiled runtime is still matched byte for byte in every test.
    monkeypatch.setattr(rewards, "ESCROW_CODE_HASH", keccak(bytes.fromhex("6000")).hex())
    monkeypatch.setattr(rewards, "PONS_CODE_HASH", keccak(bytes.fromhex("6000")).hex())
    s = get_settings()
    monkeypatch.setattr(s, "pons_rewards_enabled", True)
    monkeypatch.setattr(s, "pons_rewards_factory", FACTORY)
    monkeypatch.setattr(s, "platform_fee_wallet_evm", TREASURY)
    return RewardNode()


async def test_factory_verification_and_address_tampering_fail_closed(setup_rewards):
    node = setup_rewards
    c = rewards.configuration("first", CREATOR, {"bps": 600, "recipient": TREASURY}, FACTORY)
    await rewards.verify(node, c)
    node.bad_code = True
    with pytest.raises(AdapterError, match="code"):
        await rewards.verify(node, c)
    for changed in ({"splitter": CREATOR}, {"bps": 500}, {"creator": TREASURY}, {"version": 2}):
        with pytest.raises(AdapterError, match="Invalid"):
            rewards.validate({**c, **changed})
    node.bad_code = False
    node.network = "base"
    with pytest.raises(AdapterError, match="Robinhood"):
        await rewards.verify(node, c)


@pytest.mark.parametrize("dev_buy", [0, 0.01])
async def test_both_launch_paths_freeze_only_reward_recipient(setup_rewards, dev_buy):
    node = setup_rewards
    c = rewards.configuration("launch", CREATOR, {"bps": 600, "recipient": TREASURY}, FACTORY)
    share = {"bps": 600, "recipient": TREASURY}
    plan = await PonsAdapter(node).prepare_create(req(dev_buy_sol=dev_buy,
        options={"pons_rewards": c, "helm_creator_share": share}))
    params = node.launch_calls[-1][1][0]
    assert params[5].lower() == c["splitter"].lower() and params[7] is False
    assert len(plan.steps) == 1  # launch remains a single transaction
    if dev_buy:
        assert node.launch_calls[-1][1][5].lower() == CREATOR.lower()  # dev buy still goes to dev
    with pytest.raises(AdapterError, match="Missing"):
        await PonsAdapter(node).prepare_create(req(options={"helm_creator_share": share}))


def test_only_exact_successful_treasury_events_are_counted():
    c = rewards.configuration("launch", CREATOR, {"bps": 600, "recipient": TREASURY}, FACTORY)
    log = {"address": c["splitter"], "topics": [rewards.RELEASED, "0x" + "00" * 12 + TREASURY[2:]],
           "data": "0x" + encode(["uint256"], [600]).hex()}
    assert rewards.treasury_released(c, [log]) == 600
    assert rewards.treasury_released(c, [{**log, "address": FACTORY}, {**log, "removed": True},
        {**log, "topics": [rewards.RELEASED, "0x" + "00" * 12 + CREATOR[2:]]}]) == 0


@pytest.mark.parametrize("delayed", [False, True])
async def test_new_25_split_and_historical_claims_recover_after_restart(client, fake_rpc, setup_rewards, monkeypatch, delayed):
    node = setup_rewards
    node.factory_version = 2
    monkeypatch.setattr(get_settings(), "pons_rewards_factory_v2", FACTORY)
    registry._cache["pons"] = PonsAdapter(node)
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer(), evm_rpcs={"robinhood": node})
    async def no_sleep(_):
        pass
    monkeypatch.setattr(evm_exec.asyncio, "sleep", no_sleep)
    try:
        h = await signup(client)
        dev = (await client.post("/wallets", json={"network": "robinhood", "count": 1}, headers=h)).json()[0]
        node.balances[dev["address"].lower()] = 10**18
        created = await client.post("/launches", json={"launchpad": "pons", "name": "Test", "symbol": "T",
            "dev_wallet_id": dev["id"], "options": {"creator_tax_pct": 1}}, headers=h)
        assert created.status_code == 201, created.text
        lid = created.json()["id"]
        pf = (await client.post(f"/launches/{lid}/deploy", json={}, headers=h)).json()
        assert pf["preflight"]["pons_rewards"]["version"] == 2
        assert pf["preflight"]["helm_creator_share"]["bps"] == 250
        node.config = pf["preflight"]["pons_rewards"]
        node.ready = not delayed
        sent = await client.post(f"/launches/{lid}/deploy", json={"confirm": True,
            "preflight_hash": pf["preflight_hash"], "idempotency_key": "deploy"}, headers=h)
        assert sent.status_code == 200, sent.text
        if delayed:
            async with get_sessionmaker()() as db:
                assert not (await db.execute(select(RevenueEvent).where(RevenueEvent.launch_id == lid))).scalars().all()
            from app.routes.launches import _EVM_RESOLVERS
            _EVM_RESOLVERS.clear()  # a server restart loses the in-memory resolver
        node.ready = True
        r = await client.post(f"/launches/{lid}/recheck", json={}, headers=h)
        assert r.json()["status"] == "live" and r.json()["mint_address"], r.text
        assert r.json()["pons_rewards"]["bps"] == 250
        node.factory_version = 1
        # A historical imported launch retains its immutable, previously agreed splitter.
        c = rewards.configuration(lid, dev["address"], {"bps": 600, "recipient": TREASURY}, FACTORY)
        node.config = c
        async with get_sessionmaker()() as db:
            old = await db.get(Launch, lid)
            old.launchpad_params = {**(old.launchpad_params or {}), "pons_rewards": c,
                "helm_creator_share": {"bps": 600, "recipient": TREASURY}}
            db.add(RevenueEvent(source="creator_share", network="robinhood", asset="ETH", decimals=18,
                amount_raw=None, estimated=True, status="configured", launch_id=lid,
                destination=TREASURY, payer_user_id=old.user_id, reference=c["splitter"], note="Historical terms"))
            await db.commit()
        # Later config changes/disablement do not disable or redirect an existing user's claim.
        get_settings().platform_fee_wallet_evm = "0x" + "ef" * 20
        get_settings().pons_rewards_enabled = False
        get_settings().pons_rewards_factory = ""
        node.pending_fees = 10000
        pf = (await client.post(f"/launches/{lid}/claim-fees", json={"wallet_id": dev["id"]}, headers=h)).json()
        assert pf["preflight"]["pons_rewards"]["treasury"].lower() == TREASURY.lower()
        node.ready = not delayed
        claim = await client.post(f"/launches/{lid}/claim-fees", json={"wallet_id": dev["id"], "confirm": True,
            "preflight_hash": pf["preflight_hash"], "idempotency_key": "claim"}, headers=h)
        assert claim.status_code == 200, claim.text
        node.ready = True
        for _ in range(2):
            job = await client.get(f"/jobs/{claim.json()['job_id']}", headers=h)
            assert job.json()["status"] == "confirmed", job.text
        async with get_sessionmaker()() as db:
            rows = (await db.execute(select(RevenueEvent).where(RevenueEvent.launch_id == lid))).scalars().all()
            paid = [row for row in rows if row.status == "received"]
            assert len(rows) == 3 and len(paid) == 1  # new configuration, legacy configuration and exact payout
            assert int(paid[0].amount_raw) == 600 and not paid[0].estimated
            assert paid[0].destination.lower() == TREASURY.lower()
        assert node.sent[-1]["to"].lower() == FACTORY.lower() and node.sent[-1]["value"] == 0
    finally:
        registry._cache.pop("pons", None)
        app.dependency_overrides.pop(get_executor, None)
