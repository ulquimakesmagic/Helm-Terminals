import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .chains.base import AdapterError
from .chains.solana import RPCError, RPCUnavailable, shared_rpc
from .chains.evm import EVM_CHAINS, EvmRPCUnavailable
from .keyring import parse_list
from .config import get_settings
from .signing.base import SigningError
from .db import create_all
from .routes import auth, automations, launches, misc, profile, telegram, wallets
from .signing import get_signer


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_signer()  # fail fast on a bad signer config (e.g. local signer in production)
    await create_all()
    worker = None
    if get_settings().automation_worker_enabled:
        from .services.automation_runner import automation_worker

        worker = asyncio.create_task(automation_worker(), name="helm-scheduled-buy-worker")
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Helm Terminals", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (auth.router, wallets.router, launches.router, automations.router, misc.router, telegram.router, profile.router):
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
        try:
            rpc = shared_rpc(s.solana_rpc_url).ring.summary()
        except ValueError:
            rpc = {"count": 0, "resting": 0}
        return {"ok": True, "env": s.env, "signer": s.signer, "automations_enabled": s.automation_worker_enabled,
                "rpc_endpoints": rpc["count"], "rpc_resting": rpc["resting"],
                "bags_keys": len(parse_list(s.bags_api_key)),
                "evm_rpc": {n: {"endpoints": len(parse_list(getattr(s, f"evm_rpc_{n}", ""))) or 0,
                                "public_fallback": not parse_list(getattr(s, f"evm_rpc_{n}", ""))} for n in EVM_CHAINS}}

    return app


app = create_app()
