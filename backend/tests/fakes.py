"""Offline chain fixtures shared by API tests and the isolated browser audit server."""
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from app.chains.base import BuiltTx
from app.chains.solana import SYSTEM_PROGRAM, build_unsigned, sol_transfer_ix
SOL = 1_000_000_000

class FakeRPC:
    def __init__(self):
        self.balances: dict[str, int] = {}
        self.tokens: dict[str, int] = {}
        self.sim_error: str | None = None
        self.next_delta = -5_000
        self.sent: list[bytes] = []
        self.supply = 1_000_000_000 * 10**6
        self.holders: list[tuple[str, int]] = []

    async def get_balance(self, a):
        return self.balances.get(a, 5 * SOL)

    async def _call(self, method, params):
        from app.chains.solana import TOKEN_PROGRAM, find_ata
        from app.services.chain_balances import stable_for
        from solders.message import from_bytes_versioned
        import base64
        if method == "getFeeForMessage":
            msg = from_bytes_versioned(base64.b64decode(params[0]))
            return {"value": 5000 * msg.header.num_required_signatures}
        if method == "getMinimumBalanceForRentExemption":
            return 2039280 if params[0] == 165 else 890880
        if method == "getAccountInfo":
            mint = Pubkey.from_string(stable_for("solana")[1])
            for owner, amount in self.tokens.items():
                if params[0] == str(find_ata(Pubkey.from_string(owner), mint)):
                    return {"value": {"owner": str(TOKEN_PROGRAM), "lamports": 2039280,
                        "data": {"parsed": {"info": {"owner": owner, "mint": str(mint),
                        "state": "initialized", "tokenAmount": {"amount": str(amount)}}}}}}
            if params[0] in self.balances:
                return {"value": {"owner": str(SYSTEM_PROGRAM), "lamports": self.balances[params[0]], "space": 0}}
            return {"value": None}
        raise AssertionError(f"Unexpected RPC method {method}")

    async def get_latest_blockhash(self):
        return str(Hash.default())

    async def get_token_balance(self, owner, mint):
        return self.tokens.get(owner, 0)

    async def get_token_supply(self, mint):
        return self.supply, 6

    async def get_mint_program(self, mint):
        from app.chains.solana import TOKEN_PROGRAM
        return TOKEN_PROGRAM

    async def get_largest_owners(self, mint):
        return self.holders

    async def get_asset(self, mint):
        return {"content": {"metadata": {"name": "Cloned", "symbol": "CLN", "description": "d"}}}

    async def simulate(self, tx, sig_verify=True):
        return self.sim_error

    async def simulate_balance_after(self, tx, address):
        if self.sim_error:
            return None
        return await self.get_balance(address) + self.next_delta

    async def wallet_effects(self, tx, wallet, watch_mints=()):
        """Offline effects for router-bound tests; adversarial cases can provide an override."""
        from app.services.solana_guard import WalletEffects
        if self.sim_error:
            return WalletEffects(0, 0, str(SYSTEM_PROGRAM), {}, {}, error=self.sim_error)
        if getattr(self, "effects_override", None) is not None:
            return self.effects_override(tx, wallet)
        before = await self.get_balance(wallet)
        return WalletEffects(before, before + self.next_delta, str(SYSTEM_PROGRAM), {}, {})

    async def account_owner(self, address):
        return getattr(self, "owners", {}).get(address)

    async def account_exists(self, address):
        return address in getattr(self, "existing_accounts", set())

    async def send(self, tx):
        self.sent.append(tx)
        payer = str(VersionedTransaction.from_bytes(tx).message.account_keys[0])
        self.balances[payer] = self.balances.get(payer, 5 * SOL) + self.next_delta
        return str(VersionedTransaction.from_bytes(tx).signatures[0])

    async def confirm(self, sig, timeout_s=60, poll_s=1.0, **_):
        return "confirmed", None


class FakeAdapter:
    id = "pumpfun"
    network = "solana"
    quote_assets = ["SOL"]

    def options_schema(self):
        return {"fields": []}

    async def build_create_tx(self, req):
        mint = Keypair()
        dev = Pubkey.from_string(req.dev_pubkey)
        ix_mint = Instruction(SYSTEM_PROGRAM, b"", [AccountMeta(mint.pubkey(), True, False)])
        raw = build_unsigned(dev, [sol_transfer_ix(dev, dev, 1), ix_mint], str(Hash.default()))
        return BuiltTx(raw=raw, extra_signers=[mint], mint=str(mint.pubkey()))

    async def _simple(self, req):
        p = Pubkey.from_string(req.wallet_pubkey)
        return BuiltTx(raw=build_unsigned(p, [sol_transfer_ix(p, p, 1)], str(Hash.default())))

    async def build_buy_tx(self, req):
        return await self._simple(req)

    async def build_sell_tx(self, req):
        return await self._simple(req)

    async def build_claim_fees_tx(self, req):
        return await self._simple(req)
