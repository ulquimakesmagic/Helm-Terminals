# Helm

A transparent token-launch and wallet-operations platform, modelled on the useful parts of Proxima.
Read `docs/SPEC.md` first: it lists what is in scope, what is deliberately excluded and why, and the roadmap.

## Run it locally

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set SOLANA_RPC_URL (a Helius key is recommended)
uvicorn app.main:app --reload   # http://localhost:8000/docs

# frontend (Node 20+)
cd ../frontend
npm install
npm run dev                     # http://localhost:5173
```

## Test

```bash
cd backend && pytest            # 26 tests, no network needed
cd ../frontend && npm run build # type-check + production build
```

## Before touching real funds

1. **Use Turnkey signing.** `SIGNER=local` keeps encrypted keys in your database and refuses to start when
   `ENV=production`. The Turnkey activity names in `app/signing/turnkey.py` follow their public API but were tested
   only against a mock. Verify them against Turnkey's current docs, and set per-key spend limits and withdrawal
   allowlists in Turnkey.
2. **Test the Pump.fun adapter with small amounts.** It uses PumpPortal's `trade-local` API and pump.fun's IPFS
   endpoint. Both are third-party and can change; the tests use a fake adapter, not the live services.
3. Use a DAS-capable RPC (Helius) for token clone and import.
4. Get legal advice before running this as a hosted service for other people.

## Status

Implemented: auth + TOTP, wallets/folders/funders, fund and withdraw, launch drafts, clone/import, Pump.fun deploy,
manual buy/sell (stagger or parallel), claim creator fees, burn, send, supply transparency panel, dashboard, P&L card
export, tracker, activity log, spend preflight + idempotency + simulate-before-send.

Not yet: Streamflow lock (returns 501), swap quote asset, EVM chains, and every launchpad other than Pump.fun
(each is one adapter file; see the roadmap in the spec).
