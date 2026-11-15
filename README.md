# Helm Terminals

A transparent token-launch and wallet-operations platform, modelled on the useful parts of Proxima.
Read `docs/SPEC.md` first: it lists what is in scope, what is deliberately excluded and why, and the roadmap.
For deferred mainnet deployments and remaining integration requirements, use the
[mainnet setup guide](docs/mainnet-integration-setup.md) and
[current launchpad capability review](docs/launchpad-capability-review-2026-11-15.md).

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

## Telegram bot

1. Create a bot with @BotFather and copy its token.
2. In `backend/.env` set `BOT_API_KEY` (a long random string) and `TELEGRAM_BOT_USERNAME`; restart the API.
3. Start the bot with the same key:

```bash
cd backend
TELEGRAM_BOT_TOKEN=123:abc BOT_API_KEY=<same key> HELM_API_URL=http://localhost:8000 python -m helm_bot
```

4. In the web app open Settings -> Telegram -> Link Telegram, then send `/link CODE` to your bot in a private chat.

Withdrawals and token sends need 2FA (set it up under Settings first). Buys placed through Telegram are capped per day.

## Test

```bash
cd backend && pytest            # ~400 tests, no network needed (contract tests run real bytecode in a local EVM)
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

Implemented: auth + TOTP, wallets/folders/funders, fund and withdraw, launch drafts, clone/import, manual buy/sell,
claim creator fees, burn, send, supply transparency, dashboard, private performance-card image/video export, tracker,
activity log, spend previews + idempotency + simulate-before-send. Launchpad adapters cover Pump.fun, LetsBonk,
Meteora DBC, Bags, Clanker, Four.meme, flap.sh, Pons and Uniswap V2; some require server credentials or have
route-specific limits.

Trading automation has been removed: there are no scheduling routes, controls or execution worker.
Historical signed transactions remain available for reconciliation and audit. Profiles are private.
Some launchpad variants still need creator-reward collection or verified deployed contracts before activation;
see the current capability review above. Full upstream feature parity is not claimed.
