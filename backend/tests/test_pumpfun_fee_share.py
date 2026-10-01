import base64

import httpx
import pytest
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from app.chains.base import AdapterError, ClaimRequest
from app.chains.pumpfun import PUMP_FEES_PROGRAM, PumpFunAdapter
from app.chains.solana import build_unsigned


def _tx(payer, program=PUMP_FEES_PROGRAM) -> str:
    program_id = Keypair().pubkey() if program == "unknown" else Pubkey.from_string(program)
    raw = build_unsigned(payer, [Instruction(program_id, b"test", [])], str(Hash.default()))
    return base64.b64encode(raw).decode()


@pytest.mark.asyncio
async def test_pumpfun_builds_and_verifies_native_creator_share():
    creator = Keypair().pubkey()
    treasury = Keypair().pubkey()
    mint = Keypair().pubkey()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agents/sharing-config"
        body = __import__("json").loads(request.content)
        seen.update(body)
        return httpx.Response(200, json={
            "transaction": _tx(creator), "mode": "create", "sharingConfigAddress": str(Keypair().pubkey()),
            "shareholderCount": 2, "shareholders": body["shareholders"], "isGraduated": False,
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = PumpFunAdapter(agent_api_url="https://fees.test", client=client)
    raw = await adapter.build_creator_share_tx(str(mint), str(creator), {"bps": 600, "recipient": str(treasury)})
    assert raw
    assert seen == {
        "mint": str(mint), "user": str(creator),
        "shareholders": [{"address": str(creator), "bps": 9400}, {"address": str(treasury), "bps": 600}],
        "mode": "create", "frontRunningProtection": False, "tipAmount": 0, "encoding": "base64",
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_pumpfun_rejects_a_changed_share_from_builder():
    creator = Keypair().pubkey()
    treasury = Keypair().pubkey()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "transaction": _tx(creator), "mode": "create", "shareholderCount": 2,
            "shareholders": [{"address": str(creator), "bps": 9300}, {"address": str(treasury), "bps": 700}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = PumpFunAdapter(client=client)
    with pytest.raises(AdapterError, match="different shareholder split"):
        await adapter.build_creator_share_tx(str(Keypair().pubkey()), str(creator),
                                             {"bps": 600, "recipient": str(treasury)})
    await client.aclose()


@pytest.mark.asyncio
async def test_pumpfun_claim_distributes_shared_fees_and_refuses_missing_config():
    payer = Keypair().pubkey()
    mint = Keypair().pubkey()
    sharing = True

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        assert request.url.path == "/agents/collect-fees"
        assert body["mint"] == str(mint) and body["user"] == str(payer) and body["encoding"] == "base64"
        return httpx.Response(200, json={"transaction": _tx(payer), "usesSharingConfig": sharing})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = PumpFunAdapter(client=client)
    req = ClaimRequest(wallet_pubkey=str(payer), mint=str(mint),
                       options={"helm_creator_share": {"bps": 600}})
    assert (await adapter.build_claim_fees_tx(req)).raw
    sharing = False
    with pytest.raises(AdapterError, match="did not detect"):
        await adapter.build_claim_fees_tx(req)
    await client.aclose()


@pytest.mark.asyncio
async def test_pumpfun_refuses_an_unrelated_transaction_from_fee_builder():
    creator = Keypair().pubkey()
    treasury = Keypair().pubkey()

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "transaction": _tx(creator, "unknown"), "mode": "create", "shareholderCount": 2,
            "shareholders": body["shareholders"],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = PumpFunAdapter(client=client)
    with pytest.raises(AdapterError, match="unexpected program"):
        await adapter.build_creator_share_tx(str(Keypair().pubkey()), str(creator),
                                             {"bps": 600, "recipient": str(treasury)})
    await client.aclose()
