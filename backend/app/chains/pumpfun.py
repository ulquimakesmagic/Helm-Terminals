"""Pump.fun adapter.

Uses PumpPortal's "trade-local" API, which returns an *unsigned* transaction. Helm signs it through the
Signer, so private keys are never sent to a third party. PumpPortal and pump.fun's IPFS endpoint are third
party services whose request shapes can change; keep the fixtures in tests/ current.
"""

import httpx
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction

from .base import AdapterError, BuiltTx, ClaimRequest, CreateRequest, TradeRequest
from .solana import decode_tx

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_FEES_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


class PumpFunAdapter:
    id = "pumpfun"
    network = "solana"
    quote_assets = ["SOL"]
    create_pool = "pump"

    def __init__(
        self,
        trade_url: str = "https://pumpportal.fun/api/trade-local",
        ipfs_url: str = "https://pump.fun/api/ipfs",
        agent_api_url: str = "https://fun-block.pump.fun",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.trade_url = trade_url
        self.ipfs_url = ipfs_url
        self.agent_api_url = agent_api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30)

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (SOL)", "default": 0},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
                {"key": "priority_fee_sol", "type": "number", "min": 0, "default": 0.0005},
            ],
            "socials": ["twitter", "telegram", "website"],
        }

    async def upload_metadata(self, req: CreateRequest) -> str:
        files = {}
        if req.image_bytes:
            files["file"] = ("image.png", req.image_bytes, "image/png")
        data = {
            "name": req.name,
            "symbol": req.symbol,
            "description": req.description,
            "twitter": req.socials.get("twitter", ""),
            "telegram": req.socials.get("telegram", ""),
            "website": req.socials.get("website", ""),
            "showName": "true",
        }
        r = await self._client.post(self.ipfs_url, data=data, files=files or None)
        if r.status_code != 200:
            raise AdapterError(f"metadata upload failed: {r.status_code} {r.text[:200]}")
        uri = r.json().get("metadataUri")
        if not uri:
            raise AdapterError("metadata upload returned no metadataUri")
        return uri

    async def _trade_local(self, body: dict) -> bytes:
        r = await self._client.post(self.trade_url, json=body)
        if r.status_code != 200:
            raise AdapterError(f"pumpportal {r.status_code}: {r.text[:300]}")
        if not r.content or r.content[:1] in (b"{", b"["):
            # An error payload, not a serialized transaction
            raise AdapterError(f"pumpportal returned no transaction: {r.text[:300]}")
        return r.content

    async def _agent_tx(self, path: str, body: dict) -> dict:
        """Build an unsigned fee-management transaction with pump.fun's documented public API."""
        try:
            r = await self._client.post(f"{self.agent_api_url}{path}", json=body)
        except httpx.HTTPError as e:
            raise AdapterError(f"pump.fun fee service unavailable ({type(e).__name__})") from e
        try:
            data = r.json()
        except ValueError:
            raise AdapterError(f"pump.fun fee service {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            message = data.get("error") or data.get("message") if isinstance(data, dict) else None
            raise AdapterError(f"pump.fun fee service {r.status_code}: {message or str(data)[:200]}")
        if not isinstance(data, dict) or not data.get("transaction"):
            raise AdapterError("pump.fun fee service returned no transaction")
        try:
            decode_tx(data["transaction"])
        except Exception as e:
            raise AdapterError("pump.fun fee service returned an invalid transaction") from e
        return data

    @staticmethod
    def _verify_agent_transaction(raw: bytes, payer: str, allowed_programs: set[str],
                                  required_programs: set[str]) -> None:
        """Refuse a valid-but-unrelated transaction returned by the remote builder before it reaches the signer."""
        tx = VersionedTransaction.from_bytes(raw)
        message = tx.message
        required = int(message.header.num_required_signatures)
        signers = [str(k) for k in list(message.account_keys)[:required]]
        if signers != [payer]:
            raise AdapterError("pump.fun fee transaction has unexpected required signers")
        keys = list(message.account_keys)
        try:
            programs = {str(keys[ix.program_id_index]) for ix in message.instructions}
        except (IndexError, TypeError) as e:
            raise AdapterError("pump.fun fee transaction has invalid program references") from e
        unexpected = programs - allowed_programs
        if unexpected:
            raise AdapterError("pump.fun fee transaction invokes an unexpected program")
        if not programs.intersection(required_programs):
            raise AdapterError("pump.fun fee transaction does not invoke the expected fee program")

    async def build_creator_share_tx(self, mint: str, creator: str, share: dict) -> bytes:
        """Create pump.fun's one-time native fee-sharing config for this mint.

        Both recipients are written into the pump.fun program. The creator keeps their share directly; Helm never
        receives or forwards the creator's portion.
        """
        recipient = str((share or {}).get("recipient") or "")
        bps = int((share or {}).get("bps") or 0)
        if not 0 < bps < 10_000:
            raise AdapterError("pump.fun fee share must be between 1 and 9,999 bps")
        try:
            Pubkey.from_string(mint)
            Pubkey.from_string(creator)
            Pubkey.from_string(recipient)
        except ValueError as e:
            raise AdapterError("pump.fun fee sharing received an invalid Solana address") from e
        if creator == recipient:
            raise AdapterError("creator and Helm fee-share recipient must be different wallets")
        shareholders = [{"address": creator, "bps": 10_000 - bps}, {"address": recipient, "bps": bps}]
        data = await self._agent_tx("/agents/sharing-config", {
            "mint": mint, "user": creator, "shareholders": shareholders, "mode": "create",
            "frontRunningProtection": False, "tipAmount": 0, "encoding": "base64",
        })
        returned = data.get("shareholders")
        if data.get("mode") not in (None, "create") or data.get("shareholderCount") not in (None, 2):
            raise AdapterError("pump.fun fee service returned unexpected sharing-config details")
        if returned is not None:
            normalized = [{"address": str(x.get("address") or ""), "bps": int(x.get("bps") or 0)}
                          for x in returned if isinstance(x, dict)]
            if normalized != shareholders:
                raise AdapterError("pump.fun fee service returned a different shareholder split")
        if data.get("sharingConfigAddress"):
            try:
                Pubkey.from_string(data["sharingConfigAddress"])
            except ValueError as e:
                raise AdapterError("pump.fun fee service returned an invalid sharing-config address") from e
        raw = decode_tx(data["transaction"])
        self._verify_agent_transaction(raw, creator, {COMPUTE_BUDGET_PROGRAM, PUMP_FEES_PROGRAM},
                                       {PUMP_FEES_PROGRAM})
        return raw

    async def build_create_tx(self, req: CreateRequest) -> BuiltTx:
        mint = Keypair()  # ephemeral: signs once for account creation, then is discarded
        uri = await self.upload_metadata(req)
        raw = await self._trade_local(
            {
                "publicKey": req.dev_pubkey,
                "action": "create",
                "tokenMetadata": {"name": req.name, "symbol": req.symbol, "uri": uri},
                "mint": str(mint.pubkey()),
                "denominatedInSol": "true",
                "amount": req.dev_buy_sol,
                "slippage": req.slippage_pct,
                "priorityFee": req.priority_fee_sol,
                "pool": self.create_pool,
            }
        )
        return BuiltTx(raw=raw, extra_signers=[mint], mint=str(mint.pubkey()))

    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx:
        raw = await self._trade_local(
            {
                "publicKey": req.wallet_pubkey,
                "action": "buy",
                "mint": req.mint,
                "denominatedInSol": "true" if req.denominated_in_sol else "false",
                "amount": req.amount,
                "slippage": req.slippage_pct,
                "priorityFee": req.priority_fee_sol,
                "pool": "auto",
            }
        )
        return BuiltTx(raw=raw, mint=req.mint)

    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx:
        raw = await self._trade_local(
            {
                "publicKey": req.wallet_pubkey,
                "action": "sell",
                "mint": req.mint,
                "denominatedInSol": "true" if req.denominated_in_sol else "false",
                "amount": req.amount,
                "slippage": req.slippage_pct,
                "priorityFee": req.priority_fee_sol,
                "pool": "auto",
            }
        )
        return BuiltTx(raw=raw, mint=req.mint)

    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None:
        if not req.mint:
            raise AdapterError("this launch has no token yet")
        data = await self._agent_tx("/agents/collect-fees", {
            "mint": req.mint, "user": req.wallet_pubkey, "frontRunningProtection": False,
            "tipAmount": 0, "encoding": "base64",
        })
        expected_share = ((req.options or {}).get("helm_creator_share") or {}).get("bps", 0)
        if expected_share and data.get("usesSharingConfig") is not True:
            raise AdapterError("pump.fun did not detect this launch's fee-sharing config; no distribution was sent")
        raw = decode_tx(data["transaction"])
        allowed = {COMPUTE_BUDGET_PROGRAM, PUMP_PROGRAM, PUMP_AMM_PROGRAM, PUMP_FEES_PROGRAM,
                   TOKEN_PROGRAM, TOKEN_2022_PROGRAM, ATA_PROGRAM}
        expected = {PUMP_FEES_PROGRAM} if expected_share else {PUMP_PROGRAM, PUMP_AMM_PROGRAM, PUMP_FEES_PROGRAM}
        self._verify_agent_transaction(raw, req.wallet_pubkey, allowed, expected)
        return BuiltTx(raw=raw, mint=req.mint)
