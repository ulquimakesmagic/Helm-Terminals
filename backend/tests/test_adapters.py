"""LetsBonk and Bags adapters against mocked HTTP. No real API is called."""

import json

import httpx
import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from app.chains.bags import WSOL, BagsAdapter
from app.chains.base import AdapterError, ClaimRequest, CreateRequest, TradeRequest
from app.chains.letsbonk import LetsBonkAdapter
from app.chains.solana import build_unsigned, sign_versioned, sol_transfer_ix

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def unsigned_tx(payer: Pubkey) -> bytes:
    return build_unsigned(payer, [sol_transfer_ix(payer, payer, 1)], str(Hash.default()))


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


DEV = Keypair().pubkey()
CREATE = CreateRequest(name="Helm Cat", symbol="HCAT", description="d", image_bytes=b"\x89PNG", socials={"twitter": "x.com/h"},
                       dev_pubkey=str(DEV), dev_buy_sol=0.5)


# --- LetsBonk ------------------------------------------------------------------------------------


async def test_letsbonk_create_uses_bonk_storage_and_pool():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/upload/img":
            return httpx.Response(200, text="https://ipfs.example/img")
        if req.url.path == "/upload/meta":
            seen["meta"] = json.loads(req.content)
            return httpx.Response(200, text="https://ipfs.example/meta.json")
        if req.url.path == "/api/trade-local":
            seen["trade"] = json.loads(req.content)
            return httpx.Response(200, content=unsigned_tx(DEV))
        return httpx.Response(404)

    a = LetsBonkAdapter(trade_url="https://pumpportal.test/api/trade-local", storage_url="https://store.test",
                        client=client_for(handler))
    built = await a.build_create_tx(CREATE)
    assert seen["trade"]["pool"] == "bonk" and seen["trade"]["action"] == "create"
    assert seen["trade"]["tokenMetadata"]["uri"] == "https://ipfs.example/meta.json"
    assert seen["meta"]["createdOn"] == "https://bonk.fun" and seen["meta"]["image"] == "https://ipfs.example/img"
    assert seen["meta"]["twitter"] == "x.com/h"
    assert built.mint and len(built.extra_signers) == 1  # the new mint key signs once


async def test_letsbonk_requires_image_and_refuses_claims():
    a = LetsBonkAdapter(client=client_for(lambda r: httpx.Response(500)))
    with pytest.raises(AdapterError, match="image"):
        await a.build_create_tx(CreateRequest(**{**CREATE.__dict__, "image_bytes": None}))
    with pytest.raises(AdapterError, match="bonk.fun"):
        await a.build_claim_fees_tx(ClaimRequest(wallet_pubkey=str(DEV)))


# --- Bags ----------------------------------------------------------------------------------------


def bags_handler(calls: list, mint: str, config_txs: int = 1, bundles=None):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-api-key"] == "k"
        path = req.url.path.removeprefix("/api/v1")
        body = json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") else None
        calls.append((path, body, dict(req.url.params)))
        if path == "/token-launch/create-token-info":
            return httpx.Response(200, json={"success": True, "response": {"tokenMint": mint, "tokenMetadata": "https://ipfs/meta"}})
        if path == "/fee-share/config":
            txs = [{"transaction": b58encode(unsigned_tx(DEV)), "blockhash": {"blockhash": "x", "lastValidBlockHeight": 1}}] * config_txs
            return httpx.Response(200, json={"success": True, "response": {
                "needsCreation": True, "meteoraConfigKey": "CFG", "transactions": txs, "bundles": bundles or []}})
        if path == "/token-launch/create-launch-transaction":
            return httpx.Response(200, json={"success": True, "response": b58encode(unsigned_tx(DEV))})
        if path == "/trade/quote":
            return httpx.Response(200, json={"success": True, "response": {"requestId": "q1", "outAmount": "5"}})
        if path == "/trade/swap":
            return httpx.Response(200, json={"success": True, "response": {"swapTransaction": b58encode(unsigned_tx(DEV))}})
        if path == "/token-launch/claim-txs/v3":
            return httpx.Response(200, json={"success": True, "response": [{"tx": b58encode(unsigned_tx(DEV))}] * 2})
        return httpx.Response(404, json={"success": False, "error": "not found"})
    return handler


async def test_bags_launch_is_config_then_launch_with_partner_share():
    calls: list = []
    mint = str(Keypair().pubkey())
    a = BagsAdapter(api_key="k", api_url="https://bags.test/api/v1", partner_wallet="PARTNER", partner_config="PCFG",
                    client=client_for(bags_handler(calls, mint)))
    built = await a.build_create_tx(CREATE)
    assert built.mint == mint and len(built.pre) == 1  # fee-share config lands before the launch
    cfg = next(b for p, b, _ in calls if p == "/fee-share/config")
    assert cfg["claimersArray"] == [str(DEV)] and cfg["basisPointsArray"] == [10_000]  # creator keeps 100%
    assert cfg["partner"] == "PARTNER" and cfg["partnerConfig"] == "PCFG"
    launch = next(b for p, b, _ in calls if p == "/token-launch/create-launch-transaction")
    assert launch["initialBuyLamports"] == 500_000_000 and launch["configKey"] == "CFG"


async def test_bags_refuses_bundles_and_missing_image():
    a = BagsAdapter(api_key="k", api_url="https://bags.test/api/v1",
                    client=client_for(bags_handler([], str(Keypair().pubkey()), bundles=[["x"]])))
    with pytest.raises(AdapterError, match="bundle"):
        await a.build_create_tx(CREATE)
    with pytest.raises(AdapterError, match="image"):
        await a.build_create_tx(CreateRequest(**{**CREATE.__dict__, "image_bytes": None}))


class _Rpc:
    async def get_token_balance(self, owner, mint):
        return 1_000_000


async def test_bags_buy_and_percentage_sell_route_through_quote_and_swap():
    calls: list = []
    mint = str(Keypair().pubkey())
    a = BagsAdapter(api_key="k", api_url="https://bags.test/api/v1", rpc=_Rpc(), client=client_for(bags_handler(calls, mint)))
    await a.build_buy_tx(TradeRequest(mint, str(DEV), "buy", 0.25, True, slippage_pct=7))
    q = [p for path, _, p in calls if path == "/trade/quote"][0]
    assert q["inputMint"] == WSOL and q["outputMint"] == mint and q["amount"] == "250000000" and q["slippageBps"] == "700"

    calls.clear()
    await a.build_sell_tx(TradeRequest(mint, str(DEV), "sell", "40%", False))
    q = [p for path, _, p in calls if path == "/trade/quote"][0]
    assert q["inputMint"] == mint and q["outputMint"] == WSOL and q["amount"] == "400000"


async def test_bags_claim_returns_every_claim_transaction_in_order():
    a = BagsAdapter(api_key="k", api_url="https://bags.test/api/v1",
                    client=client_for(bags_handler([], str(Keypair().pubkey()))))
    built = await a.build_claim_fees_tx(ClaimRequest(wallet_pubkey=str(DEV), mint="M"))
    assert built is not None and len(built.pre) == 1


async def test_bags_api_errors_become_adapter_errors():
    a = BagsAdapter(api_key="k", api_url="https://bags.test/api/v1",
                    client=client_for(lambda r: httpx.Response(401, json={"success": False, "error": "bad key"})))
    with pytest.raises(AdapterError, match="key rejected"):
        await a.build_buy_tx(TradeRequest("M", str(DEV), "buy", 0.1, True))


# --- signing a transaction a launchpad already part-signed ---------------------------------------


async def test_signing_keeps_a_signature_the_launchpad_already_added():
    """Bags generates the token mint and signs with it; Helm must keep that signature and add the wallet's."""
    wallet, mint = Keypair(), Keypair()
    ix = Instruction(Pubkey.new_unique(), b"", [AccountMeta(wallet.pubkey(), True, True), AccountMeta(mint.pubkey(), True, True)])
    msg = MessageV0.try_compile(wallet.pubkey(), [ix], [], Hash.default())
    part = VersionedTransaction.populate(msg, [Signature.default(), mint.sign_message(to_bytes_versioned(msg))])

    class S:
        async def sign_message(self, ref, message):
            return bytes(wallet.sign_message(message))

    signed = VersionedTransaction.from_bytes(await sign_versioned(bytes(part), S(), {str(wallet.pubkey()): "ref"}))
    assert signed.verify_with_results() == [True, True]


async def test_bags_pair_token_launch_orders_setup_launch_and_cleanup():
    """Stock-paired launch: lookup table + token creation first, then the launch, then table clean-up."""
    calls: list = []
    mint, quote = str(Keypair().pubkey()), str(Keypair().pubkey())
    tx = lambda n: b58encode(build_unsigned(DEV, [sol_transfer_ix(DEV, DEV, n)], str(Hash.default())))  # noqa: E731

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v1")
        calls.append((path, json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") else None))
        if path == "/token-launch/create-token-info":
            return httpx.Response(200, json={"success": True, "response": {"tokenMint": mint, "tokenMetadata": "https://ipfs/m"}})
        if path == "/token-launch/damm-v2/create-transaction":
            return httpx.Response(200, json={"success": True, "response": {"transactions": [
                {"type": "LUT_SETUP", "transaction": tx(1)}, {"type": "CREATE_TOKEN", "transaction": tx(2)},
                {"type": "LAUNCH", "transaction": tx(3)}, {"type": "LUT_DEACTIVATE", "transaction": tx(4)},
                {"type": "LUT_CLOSE", "transaction": tx(5)}], "launch": {"tokenMint": mint}}})
        return httpx.Response(404, json={"success": False, "error": "unexpected " + path})

    a = BagsAdapter(api_key="k", api_url="https://bags.test/api/v1", partner_wallet="PARTNER", client=client_for(handler))
    built = await a.build_create_tx(CreateRequest(**{**CREATE.__dict__, "options": {"quote": {"mint": quote, "symbol": "NVDAx"}}}))
    lamports = lambda raw: VersionedTransaction.from_bytes(raw).message.instructions[0].data[4:12]  # noqa: E731
    assert [int.from_bytes(lamports(r), "little") for r in built.pre] == [1, 2]
    assert int.from_bytes(lamports(built.raw), "little") == 3
    assert [int.from_bytes(lamports(r), "little") for r in built.post] == [4]  # close is skipped: needs a cooldown
    assert "/fee-share/config" not in [p for p, _ in calls]  # no fee-share config for pair launches
    body = next(b for p, b in calls if p == "/token-launch/damm-v2/create-transaction")
    assert body["quoteMint"] == quote and body["partner"] == "PARTNER" and "initialBuyQuoteAmount" not in body


async def test_bags_pair_launch_claims_point_to_bags():
    a = BagsAdapter(api_key="k", client=client_for(lambda r: httpx.Response(500)))
    with pytest.raises(AdapterError, match="bags.fm"):
        await a.build_claim_fees_tx(ClaimRequest(wallet_pubkey=str(DEV), mint="M", options={"quote": {"mint": "Q", "symbol": "TSLAx"}}))


# --- key and endpoint rotation -------------------------------------------------------------------


async def test_rpc_rotates_past_rate_limited_endpoint_and_never_leaks_urls():
    from app.chains.solana import RPCError, RPCUnavailable, SolanaRPC

    hits = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(req.url.host)
        if req.url.host == "a.test":
            return httpx.Response(429, headers={"retry-after": "5"})
        if req.url.host == "b.test":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 7}})
        return httpx.Response(500)

    rpc = SolanaRPC("https://a.test/?api-key=SECRET1, https://b.test/?api-key=SECRET2", client=client_for(handler))
    assert await rpc.get_balance(str(DEV)) == 7
    hits.clear()
    assert await rpc.get_balance(str(DEV)) == 7
    assert hits == ["b.test"]  # a.test is resting after its 429

    def all_down(req):
        return httpx.Response(503)

    down = SolanaRPC(["https://x.test/?api-key=SECRET3", "https://y.test/?api-key=SECRET4"], client=client_for(all_down))
    with pytest.raises(RPCUnavailable) as e:
        await down.get_balance(str(DEV))
    assert "SECRET" not in str(e.value) and "x.test" not in str(e.value) and "RPC endpoint 1 of 2" in str(e.value)

    def node_error(req):
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "not a mint"}})

    calls = []
    answer = SolanaRPC(["https://p.test", "https://q.test"], client=client_for(lambda r: calls.append(1) or node_error(r)))
    with pytest.raises(RPCError):
        await answer.get_token_supply(str(DEV))
    assert len(calls) == 1  # a real answer from the node isn't retried elsewhere


async def test_bags_rotates_keys_and_rests_rejected_ones():
    used = []

    def handler(req: httpx.Request) -> httpx.Response:
        key = req.headers["x-api-key"]
        used.append(key)
        if key == "k1":
            return httpx.Response(429)
        if key == "k2":
            return httpx.Response(401, json={"success": False, "error": "revoked"})
        return httpx.Response(200, json={"success": True, "response": {"tokens": []}})

    a = BagsAdapter(api_key="k1,k2 , k3", api_url="https://bags.test/api/v1", client=client_for(handler))
    assert await a.supported_quotes() == []
    assert set(used) == {"k1", "k2", "k3"} and used[-1] == "k3"
    a._quotes = None
    used.clear()
    await a.supported_quotes()
    assert used == ["k3"]  # k1 rate-limited and k2 rejected are both resting


def test_parse_list_handles_commas_newlines_and_duplicates():
    from app.keyring import parse_list

    assert parse_list(" a, b\nc,,a ") == ["a", "b", "c"]
    assert parse_list("") == [] and parse_list(None) == []
