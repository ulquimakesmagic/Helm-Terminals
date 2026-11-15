"""Isolated browser fixtures. No real RPC, users, keys or funds are used.

Run from backend with the audit virtualenv. This tool writes only to .audit-runtime.
It must never be used as a production entry point.
"""
import asyncio
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
DATABASE = ROOT / ".audit-runtime" / "helm-redesign-browser-audit.db"
ADMIN_ID = "000000000000000000000000000000aa"
ADMIN_EMAIL = "audit-admin@example.com"
USER_EMAIL = "audit-user@example.com"
PASSWORD = "Isolated-browser-audit-2026"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
os.environ.update(DATABASE_URL=f"sqlite+aiosqlite:///{DATABASE.as_posix()}", ENV="development", SIGNER="local",
    SECRET_KEY="isolated-ui-audit-no-real-funds", CORS_ORIGINS="http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175",
    SUPERADMIN_USER_ID=ADMIN_ID, SUPERADMIN_EMAIL=ADMIN_EMAIL, SMTP_HOST="", CODEX_API_KEY="", GMGN_API_KEY="",
    BAGS_API_KEY="", JUPITER_API_KEY="", EXTRA_NETWORKS="near,tron",
    SOLANA_RPC_URL="http://127.0.0.1:9", PLATFORM_FEE_WALLET_EVM="0x" + "77" * 20,
    EVM_RPC_BASE="http://127.0.0.1:9", EVM_RPC_ETHEREUM="http://127.0.0.1:9",
    EVM_RPC_BSC="http://127.0.0.1:9", EVM_RPC_ROBINHOOD="http://127.0.0.1:9")

from solders.keypair import Keypair
os.environ["PLATFORM_FEE_WALLET"] = str(Keypair.from_seed(bytes([33]) * 32).pubkey())

from sqlalchemy import select
from app.db import create_all, get_sessionmaker
from app.models import AdminRole, Launch, LaunchWallet, User, Wallet
from app.security import encrypt_secret, hash_password
from app.main import app
from app.deps import get_rpc, get_executor
from app.services.executor import Executor
from app.services import portfolio
from app.signing import get_signer
from app.chains import registry
from tests.fakes import FakeAdapter, FakeRPC
from tests.test_extra_chains import FakeNear, FakeTron
from app.services import extra_chains

extra_chains._clients.update(near=FakeNear(), tron=FakeTron())

rpc = FakeRPC()
registry._cache["pumpfun"] = FakeAdapter()
app.dependency_overrides[get_rpc] = lambda: rpc
app.dependency_overrides[get_executor] = lambda: Executor(rpc, get_signer())


async def prices(client=None):
    return {"SOL": 150.0, "ETH": 3000.0, "BNB": 600.0}


portfolio.coin_prices = prices


async def seed():
    await create_all()
    async with get_sessionmaker()() as db:
        for email, admin in ((USER_EMAIL, False), (ADMIN_EMAIL, True)):
            user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user:
                continue
            user = User(email=email, password_hash=hash_password(PASSWORD),
                **({"id": ADMIN_ID, "totp_enabled": True, "totp_secret_enc": encrypt_secret(TOTP_SECRET)} if admin else {}))
            db.add(user)
            await db.commit()
            if admin:
                db.add(AdminRole(user_id=user.id, role="superadmin", granted_by="isolated-audit-fixture"))
            ref = await get_signer().create_wallet(user.id, "solana", "Audit example wallet")
            wallet = Wallet(user_id=user.id, network="solana", kind="managed", address=ref.address,
                            signer_ref=ref.signer_ref, label="Audit example wallet")
            db.add(wallet)
            await db.commit()
            draft = Launch(user_id=user.id, network="solana", launchpad="pumpfun", name="Audit Example",
                           symbol="AUDIT", dev_wallet_id=wallet.id)
            db.add(draft)
            await db.commit()
            db.add(LaunchWallet(launch_id=draft.id, wallet_id=wallet.id, role="dev"))
            await db.commit()
            for network in ("near", "tron"):
                extra_ref = await get_signer().create_wallet(user.id, network, f"Audit {network}")
                db.add(Wallet(user_id=user.id, network=network, kind="managed", address=extra_ref.address,
                              signer_ref=extra_ref.signer_ref, label=f"Audit {network}"))
            live = Launch(user_id=user.id, network="solana", launchpad="pumpfun", name="Test Launch",
                          symbol="TEST", status="live", mint_address=str(Keypair.from_seed(bytes([41])*32).pubkey()),
                          dev_wallet_id=wallet.id)
            db.add(live)
            await db.commit()
            db.add(LaunchWallet(launch_id=live.id,wallet_id=wallet.id,role="dev"))
            await db.commit()


async def main():
    import uvicorn
    await seed()
    print("ISOLATED AUDIT FIXTURES: simulated chain data, no real financial execution", flush=True)
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("HELM_UI_AUDIT_PORT", "8000")), log_level="warning")).serve()


if __name__ == "__main__":
    asyncio.run(main())
