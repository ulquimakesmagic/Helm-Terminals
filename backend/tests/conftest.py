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


from .fakes import FakeRPC, FakeAdapter




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
             settings.superadmin_email, settings.platform_fee_wallet_evm)
    settings.platform_fee_wallet = str(Keypair().pubkey())
    settings.platform_fee_wallet_evm = "0x" + "77" * 20
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
    settings.platform_fee_wallet_evm = saved[4]


async def signup(client, email="a@example.com", password=TEST_PW, main=True, launch_exempt=True):
    """Register and sign in. `main=False` removes the main wallets sign-up creates, for tests that count wallets
    or balances and predate main wallets."""
    r = await client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    if launch_exempt:
        # Most feature tests use a promotional account so launch payment is not an unrelated prerequisite.
        # Pricing API tests explicitly opt out; production grants remain protected by admin step-up.
        from sqlalchemy import select
        from app.db import get_sessionmaker
        from app.models import User, LaunchFeeExemption
        async with get_sessionmaker()() as db:
            account = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one()
            db.add(LaunchFeeExemption(user_id=account.id, enabled=True, reason="Feature-test launch promotion", changed_by=account.id))
            await db.commit()
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
