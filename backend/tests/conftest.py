import os
import tempfile

_TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP}/test.db"
os.environ["RUN_JOBS_INLINE"] = "true"
os.environ["ENV"] = "development"
os.environ["SECRET_KEY"] = "test-secret"

import httpx  # noqa: E402
import pytest  # noqa: E402
from solders.hash import Hash  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.transaction import VersionedTransaction  # noqa: E402

from app import db as dbmod  # noqa: E402
from app.chains import registry  # noqa: E402
from app.chains.base import BuiltTx  # noqa: E402
from app.chains.solana import SYSTEM_PROGRAM, build_unsigned, sol_transfer_ix  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.deps import get_executor, get_rpc  # noqa: E402
from app.main import app  # noqa: E402
from app.services.executor import Executor  # noqa: E402
from app.signing import get_signer  # noqa: E402

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

    async def get_latest_blockhash(self):
        return str(Hash.default())

    async def get_token_balance(self, owner, mint):
        return self.tokens.get(owner, 0)

    async def get_token_supply(self, mint):
        return self.supply, 6

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

    async def send(self, tx):
        self.sent.append(tx)
        payer = str(VersionedTransaction.from_bytes(tx).message.account_keys[0])
        self.balances[payer] = self.balances.get(payer, 5 * SOL) + self.next_delta
        return f"sig{len(self.sent)}"

    async def confirm(self, sig, timeout_s=60, poll_s=1.0):
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


@pytest.fixture
def fake_rpc():
    return FakeRPC()


@pytest.fixture
async def client(fake_rpc):
    # fresh database for every test
    path = os.environ["DATABASE_URL"].split("///")[1]
    if os.path.exists(path):
        os.remove(path)
    dbmod._engine = None
    dbmod._sessionmaker = None
    await dbmod.create_all()

    settings = get_settings()
    saved = (settings.platform_fee_bps, settings.platform_fee_wallet)
    registry._cache["pumpfun"] = FakeAdapter()
    app.dependency_overrides[get_rpc] = lambda: fake_rpc
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    registry._cache.pop("pumpfun", None)
    settings.platform_fee_bps, settings.platform_fee_wallet = saved


async def signup(client, email="a@example.com", password="correct-horse-battery"):
    r = await client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def enable_2fa(client, headers):
    """Turn on TOTP for the signed-in user and return the secret."""
    import pyotp

    secret = (await client.post("/auth/totp/enable", headers=headers)).json()["secret"]
    r = await client.post("/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=headers)
    assert r.status_code == 200, r.text
    return secret


@pytest.fixture(autouse=True)
def offline_prices(monkeypatch):
    """No test reaches DefiLlama: fixed coin prices, and no historical data unless a test provides it."""
    from app.services import portfolio as pf

    async def prices(client=None):
        return {"SOL": 150.0, "ETH": 3000.0, "BNB": 600.0}

    async def history(coin, cid, days, client):
        return {}

    monkeypatch.setattr(pf, "coin_prices", prices)
    monkeypatch.setattr(pf, "_fetch_history", history)
    pf._price_cache = (0.0, {})
    pf._day_prices.clear()
