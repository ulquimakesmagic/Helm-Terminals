import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .chains.base import AdapterError
from .chains.solana import RPCError, RPCUnavailable
from .chains.evm import EvmRPCUnavailable
from .config import get_settings
from .signing.base import SigningError
from .db import create_all
from .routes import admin, allocations, auth, automations, chains, bridge, ton as ton_routes, external_wallets, distribute, launches, market, misc, profile, telegram, wallets
from .signing import get_signer


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    check_production_config(settings)
    get_signer()  # fail fast on a bad signer config (e.g. local signer in production)
    worker = None
    replica_lock = None
    try:
        replica_lock = await _acquire_single_replica_lock(settings)
        await create_all()
        try:  # ledger rows for trading fees recorded before the revenue ledger existed (idempotent)
            from .db import get_sessionmaker
            from .services.revenue import backfill_trade_fees

            async with get_sessionmaker()() as db:
                await backfill_trade_fees(db)
        except Exception:
            logging.getLogger("helm").warning("revenue backfill skipped", exc_info=True)
        if settings.automation_worker_enabled:
            from .services.automation_runner import automation_worker

            worker = asyncio.create_task(automation_worker(), name="helm-scheduled-buy-worker")
        yield
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        if replica_lock is not None:
            await _release_single_replica_lock(replica_lock)


_REPLICA_LOCK_KEY = 0x48454C4D  # stable PostgreSQL advisory-lock key identifying the Helm API singleton


async def _acquire_single_replica_lock(settings):
    """Hold a PostgreSQL session lock so a second API process fails fast instead of duplicating local workers.

    SQLite is used only for local development/tests and has no multi-process production worker support.
    """
    if not settings.database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        return None
    from sqlalchemy import text

    from .db import get_engine

    conn = await get_engine().connect()
    try:
        acquired = await conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _REPLICA_LOCK_KEY})
    except Exception as exc:
        await conn.close()
        raise RuntimeError("could not verify the Helm single-replica PostgreSQL lock") from exc
    if not acquired:
        await conn.close()
        raise RuntimeError("another Helm API replica already holds the PostgreSQL singleton lock")
    return conn


async def _release_single_replica_lock(conn) -> None:
    from sqlalchemy import text

    try:
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _REPLICA_LOCK_KEY})
    except Exception:
        logging.getLogger("helm.runtime").warning("could not explicitly release the PostgreSQL singleton lock",
                                                   exc_info=True)
    finally:
        await conn.close()


DEFAULT_SECRET = "dev-secret-change-me"


def check_production_config(s) -> None:
    """Refuse to start a production deployment on development defaults. SECRET_KEY encrypts 2FA secrets (and local
    signer keys), so the public default would make them readable to anyone with the database."""
    if s.env == "test" and getattr(s, "signer", "local") == "local":
        raise RuntimeError("ENV=test cannot use SIGNER=local; use an isolated fake signer")
    if s.env != "production":
        return
    # Only the public default (or nothing) is refused. A short but real key is left alone: changing SECRET_KEY makes
    # existing 2FA secrets unreadable, so that must be a planned migration, not something a deploy forces.
    if not s.secret_key or s.secret_key == DEFAULT_SECRET:
        raise RuntimeError("ENV=production needs SECRET_KEY set to a long random value, not the default")
    if getattr(s, "signer", "local") != "turnkey":
        raise RuntimeError("ENV=production requires SIGNER=turnkey; the local signer stores encrypted keys in the database")
    if not (getattr(s, "superadmin_user_id", "") or "").strip():
        # Access fails closed in role_of until the owner ID is configured.
        logging.getLogger("helm.security").warning(
            "SUPERADMIN_USER_ID is not set: superadmin access is disabled until the owner's user ID is configured")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Helm Terminals", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.middleware("http")
    async def platform_settings(request: Request, call_next):
        # Fee rates and treasury addresses can be changed by the admin; keep this process's copy fresh (cheap: it
        # only reads the database when the cached copy is older than a few seconds).
        from .db import get_sessionmaker
        from .services import platform_config as pc

        if pc.stale():
            try:
                async with get_sessionmaker()() as db:
                    await pc.refresh(db)
            except Exception:  # never block a request on this; the last snapshot (or env defaults) applies
                pass
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # An API that only returns JSON and images: never sniff types, never render in a frame, no referrer leakage.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError):
        # FastAPI's default 422 echoes the submitted value back ("input"), which for a private key or password
        # would put it in the response (and in any logs of it). Report where and why, never the value.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]})

    for r in (admin.router, allocations.router, auth.router, chains.router, ton_routes.router, external_wallets.router, wallets.router, distribute.router, bridge.router, launches.router, automations.router,
              misc.router, telegram.router, profile.router, market.router):
        app.include_router(r)

    # Upstream failures (RPC down, launchpad API changed, signer unreachable) become clear 502s. These are
    # handled responses, so CORS headers are attached and the UI can show the message instead of a network error.
    def _upstream(status: int, msg: str):
        return JSONResponse(status_code=status, content={"detail": msg})

    @app.exception_handler(httpx.HTTPError)
    async def _http_err(_: Request, exc: httpx.HTTPError):
        return _upstream(502, f"upstream service unreachable ({type(exc).__name__}). Check SOLANA_RPC_URL and network access.")

    @app.exception_handler(RPCError)
    async def _rpc_err(_: Request, exc: RPCError):
        return _upstream(502, f"solana rpc error: {str(exc)[:200]}")

    @app.exception_handler(RPCUnavailable)
    async def _rpc_down(_: Request, exc: RPCUnavailable):
        return _upstream(502, f"Solana RPC unavailable: {str(exc)[:300]}")

    @app.exception_handler(EvmRPCUnavailable)
    async def _evm_down(_: Request, exc: EvmRPCUnavailable):
        return _upstream(502, f"EVM RPC unavailable: {str(exc)[:300]}")

    @app.exception_handler(AdapterError)
    async def _adapter_err(_: Request, exc: AdapterError):
        return _upstream(502, f"launchpad error: {str(exc)[:200]}")

    @app.exception_handler(SigningError)
    async def _sign_err(_: Request, exc: SigningError):
        return _upstream(502, f"signer error: {str(exc)[:200]}")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/public-info")
    async def public_info():
        # A deliberately small, user-facing custody statement; detailed configuration stays private.
        return {"test_signer": s.env != "production" or s.signer != "turnkey"}

    @app.get("/health/ready")
    async def readiness():
        from sqlalchemy import text
        from .db import get_engine

        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            get_signer()
        except Exception:
            logging.getLogger("helm.health").exception("readiness check failed")
            return JSONResponse(status_code=503, content={"ready": False})
        return {"ready": True}

    return app


app = create_app()
