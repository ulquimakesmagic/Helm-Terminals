"""Flap.sh adapter (BNB Chain, Robinhood Chain and Base), using Flap's Portal contract directly.

Launch:
  1. Upload the image and metadata to Flap's upload API, which pins them to IPFS and returns a CID.
  2. Find a CREATE2 salt so the token's address ends in Flap's vanity suffix (8888 for standard tokens,
     7777 for tax tokens). The Portal deploys a minimal proxy of the token implementation, so the address
     can be computed in advance.
  3. Call Portal.newTokenV7 for BNB non-tax tokens, or the chain's supported V6 route otherwise. Helm dry-runs the call (eth_call) and
     checks that the Portal returns the address we predicted, so a wrong implementation or salt never costs gas.

Trading, while the token is on Flap's bonding curve: Portal.swapExactInput (native in for buys; tokens in for
sells, after an ERC-20 approve). Quotes come from Portal.quoteExactInput via eth_call. Once a token has moved to
a DEX, Flap's Portal no longer trades it.

Source: docs.flap.sh (Launch a token through the Portal; Trade tokens; Deployed contract addresses).
"""

import asyncio
import json
import os

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from ..chains.evm import EvmRPC, EvmRPCError
from .base import AdapterError, ClaimRequest, CreateRequest, EvmPlan, EvmStep, EvmTradeRequest
from .kyber import Graduated

ZERO = "0x0000000000000000000000000000000000000000"
MAX_UINT = 2**256 - 1

# From docs.flap.sh "Deployed contract addresses". Standard-token implementations are listed in the order to try.
FLAP_CHAINS = {
    "base": {
        "portal": "0x0000BC1c4fD15Dd79029AF8F5d77D68ae4490000",
        "standard_impls": [],
        "tax_impl": "0x777782e7EbB89802E2379E5fFcF7336538f43333",
        "dex0": "Uniswap",
    },
    "bsc": {
        "portal": "0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0",
        "standard_impls": ["0x8b4329947e34b6d56d71a3385cac122bade7d78d", "0x88881b6f03090462a969eC7f48385744Eeb63333"],
        "tax_impl": "0x024f18294970B5c76c0691b87f138A0317156422",
        "dex0": "PancakeSwap",
    },
    "robinhood": {
        "portal": "0x26605f322f7fF986f381bB9A6e3f5DAb0bEaEb09",
        "standard_impls": ["0x88882688a067FE97E11C2185b996286e53132222"],
        "tax_impl": "0x7777C8743C88B3aff3cf262135beF2c8b2e83333",
        "dex0": "Uniswap",
    },
}

# Enum values from Flap's IPortalTypes
DEX_THRESH_FOUR_FIFTHS = 1
MIGRATOR_V3 = 0
MIGRATOR_V2 = 1
DEX0 = 0
LP_FEE_STANDARD = 0
TOKEN_V2_PERMIT = 2
TOKEN_TAXED_V3 = 6
TOKEN_V3_PERMIT = 7
STANDARD_V3_BSC = "0x88881b6f03090462a969eC7f48385744Eeb63333"
TAX_RATES_PCT = (1, 3, 5, 10)  # the tax rates Flap allows

NEW_TOKEN_V6 = ("newTokenV6((string,string,string,uint8,bytes32,uint8,address,uint256,address,bytes,bytes32,bytes,"
                "uint8,uint8,uint16,uint16,uint64,uint64,uint16,uint16,uint16,uint16,uint256,address,address,uint8))")
NEW_TOKEN_V6_TYPES = ["(string,string,string,uint8,bytes32,uint8,address,uint256,address,bytes,bytes32,bytes,"
                      "uint8,uint8,uint16,uint16,uint64,uint64,uint16,uint16,uint16,uint16,uint256,address,address,uint8)"]
NEW_TOKEN_V7_TYPES = ["(string,string,string,uint8,bytes32,uint8,address,uint256,bytes,bytes32,bytes,uint8,"
                      "uint16,uint16,uint64,uint64,address,uint8,(uint8,uint16,address,address,uint256)[4])"]
NEW_TOKEN_V7 = "newTokenV7(" + NEW_TOKEN_V7_TYPES[0] + ")"
SWAP = "swapExactInput((address,address,uint256,uint256,bytes))"
QUOTE = "quoteExactInput((address,address,uint256))"
APPROVE = "approve(address,uint256)"

# Custom errors Flap is known to revert with, so failures read as plain English.
KNOWN_ERRORS = {
    "0xac5f6092": "Flap has standard (no-tax) tokens switched off on this chain right now. Choose a tax rate "
                  "(1, 3, 5 or 10%) for this launch and try again.",
    "0x77146b42": "Flap rejected the graduation threshold for this token type.",
    "0xde6137d1": "Flap rejected this call on this chain.",
}


def selector(sig: str) -> bytes:
    return keccak(text=sig)[:4]


def explain(e: EvmRPCError) -> str:
    code = (e.data or "")[:10].lower()
    return KNOWN_ERRORS.get(code, str(e))


def proxy_init_code_hash(impl: str) -> bytes:
    """keccak of the EIP-1167 minimal-proxy creation code the Portal deploys for each token."""
    code = ("3d602d80600a3d3981f3363d3d373d3d3d363d73" + impl.lower().removeprefix("0x")
            + "5af43d82803e903d91602b57fd5bf3")
    return keccak(bytes.fromhex(code))


def create2_address(deployer: str, salt: bytes, init_hash: bytes) -> str:
    raw = keccak(b"\xff" + bytes.fromhex(deployer.removeprefix("0x")) + salt + init_hash)[12:]
    return to_checksum_address(raw)


def grind_salt(portal: str, impl: str, suffix: str, max_tries: int = 5_000_000) -> tuple[bytes, str]:
    """Find a salt whose CREATE2 address ends in `suffix` (about 65k tries on average for 4 hex digits)."""
    init_hash = proxy_init_code_hash(impl)
    prefix = b"\xff" + bytes.fromhex(portal.removeprefix("0x"))
    base = os.urandom(24)
    suffix = suffix.lower()
    for i in range(max_tries):
        salt = base + i.to_bytes(8, "big")
        addr = keccak(prefix + salt + init_hash)[12:].hex()
        if addr.endswith(suffix):
            return salt, to_checksum_address("0x" + addr)
    raise AdapterError("couldn't find a vanity salt for the token address")


class FlapAdapter:
    kind = "evm"
    quote_assets = ["native"]

    def __init__(self, launchpad_id: str, network: str, rpc: EvmRPC, upload_url: str = "https://funcs.flap.sh/api/upload",
                 commission_receiver: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        if network not in FLAP_CHAINS:
            raise ValueError(f"Flap isn't configured for {network}")
        self.id = launchpad_id
        self.network = network
        self.rpc = rpc
        self.cfg = FLAP_CHAINS[network]
        self.portal = self.cfg["portal"]
        self.upload_url = upload_url
        self._commission = commission_receiver
        self._client = client or httpx.AsyncClient(timeout=60)

    @property
    def commission_receiver(self) -> str:
        """Flap's launchpad commission on tax tokens goes to this address: Helm's treasury for this chain (from the
        platform settings) unless one was given explicitly. Flap determines the commission; Helm's creator-share
        setting does not determine this rate."""
        if self._commission is not None:
            return self._commission
        from ..services import platform_config as pc

        return ""  # New pricing has no Helm partner commission.

    def options_schema(self) -> dict:
        return {
            "fields": [
                {"key": "dev_buy_sol", "type": "number", "min": 0, "label": "Dev buy (native coin)", "default": 0},
                {"key": "tax_rate_pct", "type": "select", "options": ([0] if self.cfg["standard_impls"] else []) + list(TAX_RATES_PCT), "default": 0 if self.cfg["standard_impls"] else 1,
                 "label": "Trading tax (%)",
                 "hint": "A tax token charges this on buys and sells for a year, paid to your dev wallet. "
                         "Base supports tax tokens only through Helm; 0 on other chains makes a standard token."},
                {"key": "slippage_pct", "type": "number", "min": 0, "max": 50, "default": 10},
            ],
            "socials": ["twitter", "telegram", "website"],
            "requires_image": True,
        }

    def deploy_notes(self, params: dict) -> dict:
        tax = int(params.get("tax_rate_pct") or 0)
        notes = {"token_type": f"tax token: {tax}% on every buy and sell for a year, paid to your dev wallet" if tax
                 else "standard token, no tax"}
        if tax and self.commission_receiver:
            notes["commission_receiver"] = self.commission_receiver
            notes["commission"] = "Helm is this token's commission receiver on Flap and earns Flap's launchpad commission on it"
        return notes

    # --- metadata -----------------------------------------------------------------------------

    async def upload_metadata(self, req: CreateRequest) -> str:
        if not req.image_bytes:
            raise AdapterError("Flap needs a token image. Add one to the launch and try again.")
        meta = {"description": req.description or req.name, "creator": req.dev_pubkey,
                "website": req.socials.get("website") or "", "twitter": req.socials.get("twitter") or "",
                "telegram": req.socials.get("telegram") or ""}
        query = "mutation Create($file: Upload!, $meta: MetadataInput!) {\n  create(file: $file, meta: $meta)\n}"
        files = {
            "operations": (None, json.dumps({"query": query, "variables": {"file": None, "meta": meta}}), "application/json"),
            "map": (None, json.dumps({"0": ["variables.file"]}), "application/json"),
            "0": ("image.png", req.image_bytes, "image/png"),
        }
        try:
            r = await self._client.post(self.upload_url, files=files)
        except httpx.HTTPError as e:
            raise AdapterError(f"Flap upload failed: {type(e).__name__}")
        try:
            data = r.json()
        except ValueError:
            raise AdapterError(f"Flap upload {r.status_code}: {r.text[:200]}")
        cid = (data.get("data") or {}).get("create")
        if r.status_code != 200 or not cid:
            raise AdapterError(f"Flap upload {r.status_code}: {(data.get('errors') or [{}])[0].get('message', r.text[:200])}")
        return cid

    # --- launch -------------------------------------------------------------------------------

    def _params(self, req: CreateRequest, cid: str, salt: bytes, tax_pct: int, value: int) -> tuple:
        tax_bps = tax_pct * 100
        taxed = tax_pct > 0
        receiver = (req.options or {}).get("flap_commission_receiver", self.commission_receiver)
        commission = receiver if taxed and receiver else ZERO
        return (
            req.name, req.symbol, cid, DEX_THRESH_FOUR_FIFTHS, salt, MIGRATOR_V2 if taxed else MIGRATOR_V3, ZERO, value, req.dev_pubkey,
            b"", b"\x00" * 32, b"", DEX0, LP_FEE_STANDARD,
            tax_bps, tax_bps,
            365 * 86400 if taxed else 0,  # taxDuration
            86400 if taxed else 0,  # antiFarmerDuration
            10_000 if taxed else 0, 0, 0, 0,  # mkt / deflation / dividend / lp split: all tax to the beneficiary
            0, ZERO, commission,
            TOKEN_TAXED_V3 if taxed else TOKEN_V2_PERMIT,
        )

    def encode_create(self, params: tuple) -> bytes:
        return selector(NEW_TOKEN_V6) + abi_encode(NEW_TOKEN_V6_TYPES, [params])

    def _standard_v7(self, req: CreateRequest, cid: str, salt: bytes, value: int) -> bytes:
        """BNB Portal v5.27.2: non-tax, Infinity CL migration, all LP revenue to holders.

        Source: official V2 launch guide, checked 2026-11-15. No creator-side allocation exists in this mode.
        """
        none = (0, 0, ZERO, ZERO, 0)
        holders = (2, 10_000, ZERO, ZERO, 10_000 * 10**18)
        params = (req.name, req.symbol, cid, DEX_THRESH_FOUR_FIFTHS, salt, 3, ZERO, value,
                  b"", b"\x00" * 32, b"", DEX0, 0, 0, 0, 0, ZERO, TOKEN_V3_PERMIT,
                  (holders, none, none, none))
        return selector(NEW_TOKEN_V7) + abi_encode(NEW_TOKEN_V7_TYPES, [params])

    async def prepare_create(self, req: CreateRequest) -> EvmPlan:
        tax_pct = int((req.options or {}).get("tax_rate_pct") or 0)
        if not tax_pct and not self.cfg["standard_impls"]:
            raise AdapterError("Flap on Base requires a tax token; choose a supported trading tax rate")
        if tax_pct and tax_pct not in TAX_RATES_PCT:
            raise AdapterError(f"Flap tax rate must be one of {', '.join(map(str, TAX_RATES_PCT))}%")
        value = int(round(req.dev_buy_sol * 10**18))
        cid = await self.upload_metadata(req)
        v7 = not tax_pct and self.network == "bsc"
        impls = [STANDARD_V3_BSC] if v7 else [self.cfg["tax_impl"]] if tax_pct else self.cfg["standard_impls"]
        suffix = "7777" if tax_pct else "8888"
        last_error = None
        for impl in impls:
            salt, predicted = await asyncio.to_thread(grind_salt, self.portal, impl, suffix)
            encoded_params = self._params(req, cid, salt, tax_pct, value)
            data = self._standard_v7(req, cid, salt, value) if v7 else self.encode_create(encoded_params)
            try:
                ret = await self.rpc.call("eth_call", [{"from": req.dev_pubkey, "to": self.portal,
                                                        "value": hex(value), "data": "0x" + data.hex()}, "latest"])
            except EvmRPCError as e:
                last_error = explain(e)
                continue
            got = to_checksum_address(abi_decode(["address"], bytes.fromhex(ret[2:]))[0]) if ret and len(ret) >= 66 else None
            if got == predicted:
                notes = {"token_type": "V7 non-tax token; LP revenue paid to holders in BNB" if v7 else f"tax token, {tax_pct}% on buys and sells for a year, paid to your dev wallet"
                         if tax_pct else "standard token, no tax",
                         "graduates_to": "PancakeSwap Infinity CL at 80% of supply sold" if v7 else f"{self.cfg['dex0']} when 80% of supply is sold on the curve"}
                if tax_pct and encoded_params[24] != ZERO:
                    notes["commission_receiver"] = encoded_params[24]
                    notes["commission"] = ("Helm is this token's commission receiver on Flap, so Helm earns the launchpad "
                                          "commission Flap pays on tax tokens")
                return EvmPlan(steps=[EvmStep("launch", self.portal, data, value)], token=predicted, notes=notes)
            last_error = f"the Portal would create {got}, not the expected {predicted}"
        raise AdapterError(f"Flap launch dry run failed: {last_error}")

    # --- trading ------------------------------------------------------------------------------

    async def quote(self, sender: str, token_in: str, token_out: str, amount: int) -> int:
        data = selector(QUOTE) + abi_encode(["(address,address,uint256)"], [(token_in, token_out, amount)])
        try:
            ret = await self.rpc.call("eth_call", [{"from": sender, "to": self.portal, "data": "0x" + data.hex()}, "latest"])
        except EvmRPCError as e:
            # Flap's Portal stops quoting once a token has moved to a DEX: route it through the DEX instead.
            raise Graduated(f"Flap's curve can't quote this token ({explain(e)[:120]}); it has probably moved to a DEX")
        return int(ret, 16)

    def encode_swap(self, token_in: str, token_out: str, amount: int, min_out: int) -> bytes:
        return selector(SWAP) + abi_encode(["(address,address,uint256,uint256,bytes)"],
                                           [(token_in, token_out, amount, min_out, b"")])

    async def prepare_trade(self, req: EvmTradeRequest) -> EvmPlan:
        if req.amount_wei <= 0:
            raise AdapterError("nothing to trade")
        slip = max(0.0, min(req.slippage_pct, 50.0)) / 100
        if req.side == "buy":
            out = await self.quote(req.wallet, ZERO, req.token, req.amount_wei)
            data = self.encode_swap(ZERO, req.token, req.amount_wei, int(out * (1 - slip)))
            return EvmPlan([EvmStep("buy", self.portal, data, req.amount_wei)], token=req.token,
                           notes={"expected_tokens_raw": out})
        out = await self.quote(req.wallet, req.token, ZERO, req.amount_wei)
        steps = []
        if await self.rpc.erc20_allowance(req.token, req.wallet, self.portal) < req.amount_wei:
            steps.append(EvmStep("approve", req.token, selector(APPROVE) + abi_encode(["address", "uint256"],
                                                                                       [self.portal, MAX_UINT])))
        steps.append(EvmStep("sell", self.portal, self.encode_swap(req.token, ZERO, req.amount_wei, int(out * (1 - slip)))))
        return EvmPlan(steps, token=req.token, notes={"expected_native_wei": out})

    async def prepare_claim(self, req: ClaimRequest) -> EvmPlan:
        raise AdapterError("Flap has nothing to claim here: tax on tax tokens is paid to your dev wallet automatically, "
                           "and standard tokens have no creator fee.")
