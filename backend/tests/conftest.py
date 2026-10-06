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

# Fake fixture credentials shared across the suite so sensitive call sites never inline
# credential-looking literals. These are not real secrets; the names follow the suite's
# existing PW convention on purpose.
TEST_PW = "correct-horse-battery"
TEST_NEW_PW = "new-strong-password"
TEST_BAD_PW = "wrong-password"
TEST_OPENAPI = "test-only-secret"  # fake provider credential for the market-data mocks

OWNER_EMAIL = "owner@example.com"  # the deployment's configured superadmin email for tests


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

    async def wallet_effects(self, tx, wallet, watch_mints=()):
        """Simulated effects for the spend guard: by default the wallet pays `next_delta` lamports and no token
        balance changes. Tests set `effects_override` to model a provider-built transaction that misbehaves."""
        from app.services.solana_guard import WalletEffects

        if self.sim_error:
            return WalletEffects(0, 0, "11111111111111111111111111111111", {}, {}, error=self.sim_error)
        if getattr(self, "effects_override", None) is not None:
            return self.effects_override(tx, wallet)
        before = await self.get_balance(wallet)
        return WalletEffects(before, before + self.next_delta, "11111111111111111111111111111111", {}, {})

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
    saved = (settings.platform_fee_bps, settings.platform_fee_wallet, settings.superadmin_user_id,
             settings.superadmin_email)
    settings.superadmin_user_id = ""
    settings.superadmin_email = OWNER_EMAIL  # what a configured deployment looks like
    registry._cache["pumpfun"] = FakeAdapter()
    app.dependency_overrides[get_rpc] = lambda: fake_rpc
    app.dependency_overrides[get_executor] = lambda: Executor(fake_rpc, get_signer())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    registry._cache.pop("pumpfun", None)
    settings.platform_fee_bps, settings.platform_fee_wallet = saved[0], saved[1]
    settings.superadmin_user_id, settings.superadmin_email = saved[2], saved[3]


async def signup(client, email="a@example.com", password=TEST_PW, main=True):
    """Register and sign in. `main=False` removes the main wallets sign-up creates, for tests that count wallets
    or balances and predate main wallets."""
    r = await client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    if not main:
        from sqlalchemy import delete, select

        from app.db import get_sessionmaker
        from app.models import Funder, User, Wallet

        async with get_sessionmaker()() as db:
            uid = (await db.execute(select(User.id).where(User.email == email.lower()))).scalar_one()
            await db.execute(delete(Funder).where(Funder.user_id == uid))
            await db.execute(delete(Wallet).where(Wallet.user_id == uid))
            await db.commit()
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def totp_code(secret):
    """A current 2FA code that hasn't been used yet. Helm refuses a code twice, and a test can need several within
    one 30-second step, so this forgets earlier uses first (the production check is covered in test_guard.py)."""
    import pyotp
    from sqlalchemy import delete

    from app.db import get_sessionmaker
    from app.models import TotpUse

    async with get_sessionmaker()() as db:
        await db.execute(delete(TotpUse))
        await db.commit()
    return pyotp.TOTP(secret).now()


async def enable_2fa(client, headers, password=TEST_PW):
    """Turn on TOTP for the signed-in user and return the secret."""
    r = await client.post("/auth/totp/enable", json={"password": password}, headers=headers)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = await client.post("/auth/totp/verify", json={"code": await totp_code(secret)}, headers=headers)
    assert r.status_code == 200, r.text
    return secret


@pytest.fixture(autouse=True)
def fresh_platform_config(monkeypatch):
    """Tests change fee settings mid-test: read them fresh every time instead of from the 30 s cache."""
    from app.services import platform_config as pc

    pc.reset_cache()
    monkeypatch.setattr(pc, "CACHE_S", 0.0)
    yield
    pc.reset_cache()


@pytest.fixture(autouse=True)
def offline_evm(monkeypatch):
    """No test reaches a real EVM RPC. Chains a test doesn't fake itself behave like an unreachable node (reads come
    back "unavailable"), instead of quietly calling the public endpoints (which made results depend on the network)."""
    import httpx

    from app.chains import evm, registry

    def refuse(req):
        raise httpx.ConnectError("offline in tests", request=req)

    cache: dict = {}

    def offline(network):
        if network not in cache:
            cache[network] = evm.EvmRPC(network, "", client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
        return cache[network]

    monkeypatch.setattr(evm, "shared_evm", offline)
    monkeypatch.setattr(registry, "shared_evm", offline)


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
