from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .chains.base import AdapterError
from .chains.solana import RPCError
from .config import get_settings
from .signing.base import SigningError
from .db import create_all
from .routes import auth, launches, misc, telegram, wallets
from .signing import get_signer


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_signer()  # fail fast on a bad signer config (e.g. local signer in production)
    await create_all()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Helm Terminals", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (auth.router, wallets.router, launches.router, misc.router, telegram.router):
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

    @app.exception_handler(AdapterError)
    async def _adapter_err(_: Request, exc: AdapterError):
        return _upstream(502, f"launchpad error: {str(exc)[:200]}")

    @app.exception_handler(SigningError)
    async def _sign_err(_: Request, exc: SigningError):
        return _upstream(502, f"signer error: {str(exc)[:200]}")

    @app.get("/health")
    async def health():
        return {"ok": True, "env": s.env, "signer": s.signer}

    return app


app = create_app()
