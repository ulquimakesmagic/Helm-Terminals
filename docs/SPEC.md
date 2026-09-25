# Helm Terminals: transparent token launch and operations platform

Name: **Helm Terminals** (helmterminals.xyz; short form "Helm"). This is a spec and scaffold for a Proxima-style launch and wallet operations tool,
minus the features whose main purpose is to disguise holder ownership or fabricate trading activity.

## 1. What it is

A single interface to (1) deploy a token on a launchpad, (2) manage the wallets you own that hold and trade it,
and (3) operate the token after launch: manual trades, creator fees, locks, burns, transfers, P&L reporting.

Every wallet is created and controlled by the signed-in user. Nothing in Helm Terminals is designed to make one party's
holdings look like many independent holders, or to make trading look like organic demand.

## 2. Scope

### In scope (v1)

| Area | Feature |
| --- | --- |
| Accounts | Email + password, TOTP 2FA, session list and revoke, activity log |
| Wallets | Create (managed signing), import public address as watch-only, folders, fund from a Funder, withdraw |
| Funders | Reusable funding wallets with a default per network |
| Launch | Draft, review, deploy. Clone metadata from an existing token. Import an existing token by address |
| Launchpads | Adapter registry. Pump.fun implemented end to end. Others are one adapter file each (section 8) |
| Live workspace | Holdings per wallet, balances, manual buy/sell (single wallet or selected wallets, staggered or parallel), transactions feed |
| Token ops | Claim creator fees, lock supply (Streamflow), burn, send tokens, swap quote asset |
| Tracker | Watchlist of external wallet addresses with labels and bookmarks |
| Reporting | Dashboard (P&L, launches, referral), P&L card export |
| Telegram | Bot terminal: read, buy/sell, claim, burn, send, withdraw, with preview + confirm on every action (section 11) |
| Safety | Pre-flight summary before every on-chain action, per-action confirmation, spend limits, audit log |
| Fees | Optional platform fee on sells, configured in one place and shown before submit |

### Explicitly out of scope

These are the parts of Proxima that this project does **not** implement. The reasons are stated so the
boundary is a product decision and not an omission.

| Proxima feature | Why it is excluded |
| --- | --- |
| Block-0 layered bundling (Front MEV, Back MEV, temp holders, presets tuned for "less sniper" or "less top-trader visibility") | Spreads one party's supply over many wallets and delays specifically so bubble maps and holder analytics understate concentration |
| Wallet Marketplace (aged wallets filtered by CEX funding source, timezone, funding gap) | Sells wallets whose history is chosen to look like independent organic users |
| Volume Bot, Generate Activity | Same wallets trade against each other to hit a volume target with randomised timing so it looks organic |
| Cleanup (bundle wallets sell into fresh wallets) and Streamflow airdrop cleanup | Rotates supply from a concentrated set into new-looking wallets |
| Nuke / Sniper Guard auto-dump | Automated mass sell of every wallet on a trigger. Also a rug-pull primitive |
| Bubble Maps "detection mode" estimates | Only useful for tuning the above |

What replaces them: a single dev buy at launch, manual user-initiated trades, and a **Supply Transparency**
panel (section 7) that shows the user's own wallets, in aggregate, next to the token's real holder distribution.

## 3. Architecture

```
React (Vite, TS)  <--HTTPS/WS-->  FastAPI  ---> Postgres (SQLAlchemy 2, Alembic)
                                     |     \--> Redis (optional: job queue, rate limits)
                                     |
                                     +--> Signer interface
                                     |      - TurnkeySigner (prod): keys live in Turnkey, API stamps requests
                                     |      - LocalDevSigner (dev only): Fernet-encrypted keys, refuses to run in prod
                                     |
                                     +--> Chain adapters: Solana (solders + RPC), later EVM
                                     +--> Launchpad adapters: pumpfun, ...
                                     +--> Data: Helius (RPC, DAS, webhooks), Jupiter (quotes/swaps)
```

Key decisions:

- **Keys never touch the application database in production.** The DB stores Turnkey wallet/private-key IDs
  and public addresses. The app builds an unsigned transaction, sends it to the signer, and submits the result.
- **Every on-chain action is a `Job`** with a state machine: `draft -> pending -> submitted -> confirmed | failed | partial`.
  The UI polls or subscribes; failures never look like successes.
- **Simulate before send.** Each transaction is simulated via RPC; simulation failure blocks submission.
- **Idempotency keys** on every mutating endpoint that spends funds.

## 4. Data model (Postgres)

```
users(id, email, password_hash, totp_secret_enc, totp_enabled, created_at)
sessions(id, user_id, token_hash, ip, user_agent, created_at, expires_at, revoked_at)
funders(id, user_id, network, wallet_id, is_default, label)
wallets(id, user_id, network, address, kind[managed|watch], signer_ref, label, folder_id, created_at)
folders(id, user_id, name, color)
launches(id, user_id, network, launchpad, status[draft|deploying|live|failed|archived],
         name, symbol, description, image_uri, socials jsonb, mint_address, launchpad_params jsonb,
         dev_wallet_id, dev_buy_amount, created_at, deployed_at, archived_at)
launch_wallets(launch_id, wallet_id, role[dev|holder|funder])
jobs(id, user_id, launch_id, kind, params jsonb, status, idempotency_key, created_at, updated_at)
transactions(id, job_id, wallet_id, signature, status, error, slot, fee_lamports, created_at)
positions(launch_id, wallet_id, token_amount, cost_basis, realized_pnl, updated_at)
watchlist(id, user_id, network, address, label, bookmarked)
activity(id, user_id, kind, ref_type, ref_id, payload jsonb, created_at)
referrals(id, owner_user_id, code, uses, max_uses)
```

## 5. API (v1)

```
POST /auth/register            POST /auth/login          POST /auth/totp/enable   POST /auth/totp/verify
GET  /auth/sessions            DELETE /auth/sessions/{id}

GET  /wallets                  POST /wallets             POST /wallets/watch
POST /wallets/fund             POST /wallets/withdraw
GET  /folders                  POST /folders             PATCH /folders/{id}

GET  /launchpads               GET  /launchpads/{id}/options
POST /launches                 POST /launches/clone      POST /launches/import
GET  /launches                 GET  /launches/{id}       POST /launches/{id}/deploy
POST /launches/{id}/archive    POST /launches/{id}/restore

GET  /launches/{id}/workspace  (holdings, balances, transactions, transparency panel)
POST /launches/{id}/trade      (buy/sell; wallets[], mode=stagger|parallel; returns preflight unless confirm=true)
POST /launches/{id}/claim-fees POST /launches/{id}/lock   POST /launches/{id}/burn   POST /launches/{id}/send

GET  /dashboard                GET /activity             GET/POST/DELETE /tracker
GET  /jobs/{id}
```

Contract for spend endpoints: first call returns a `preflight` object (wallets, amounts, estimated fees, platform fee,
warnings). The client must call again with `confirm: true` and the same `preflight_hash`.

## 6. Signer interface

```python
class Signer(Protocol):
    async def create_wallet(self, user_id: str, network: str) -> WalletRef: ...
    async def sign_transaction(self, ref: str, unsigned_tx: bytes, network: str) -> bytes: ...
```

- `TurnkeySigner`: implements Turnkey's activity API (`create_private_keys`, `sign_transaction`) with API-key request
  stamping (P-256 over the request body, `X-Stamp` header). Turnkey policies cap per-key spend and can require 2FA.
- `LocalDevSigner`: Ed25519 keys generated locally, encrypted with Fernet, for local development only.
  Startup fails if `ENV=production` and this signer is selected.

## 7. Supply Transparency panel

For each live launch, show:

- Total supply held by the user's wallets on this launch (sum and percentage), in one line, always visible.
- Per-wallet balances, labelled as user-controlled.
- Token top-holder distribution from Helius DAS or RPC, with the user's wallets highlighted so they can
  never be mistaken for outside holders.
- A warning banner when the user's controlled wallets exceed a configurable share of supply (default 20%).

This is the deliberate inverse of Bubble Maps evasion. It makes the product safe to build and a differentiator.

## 8. Launchpad adapter interface and build order

```python
class LaunchpadAdapter(Protocol):
    id: str; network: str; quote_assets: list[str]
    def options_schema(self) -> dict: ...
    async def build_create_tx(self, req: CreateRequest) -> BuiltTx: ...
    async def build_buy_tx(self, req: TradeRequest) -> BuiltTx: ...
    async def build_sell_tx(self, req: TradeRequest) -> BuiltTx: ...
    async def build_claim_fees_tx(self, req: ClaimRequest) -> BuiltTx | None: ...
```

Registered in `app/chains/registry.py`. A new launchpad is one file plus one registry line.

Build order (Proxima's list, minus what is excluded above):

1. Solana: **Pump.fun** (implemented in scaffold)
2. Solana: LetsBonk, Meteora DBC, Bags, Printr, Surge, Anoncoin, StonkFun
3. Base: Clanker, Bankr (requires EVM signer path)
4. BSC: Four.meme, flap.sh
5. Ethereum: Uniswap V2
6. Robinhood and Arc launchpads (verify network availability before committing)

Each adapter ships with: options schema, a recorded-fixture test, a devnet or dry-run path, and a doc page.

Note on scope: "all launchpads Proxima supports" is around 30 integrations. Each one is a separate program
interface that changes over time, so treat this as an ongoing roadmap, not a v1 deliverable.

## 9. Security and compliance

- Turnkey policies: per-key daily spend limit and allowlisted destinations for withdrawals.
- 2FA policy (app level, `TFA_REQUIRED_KINDS`): a code is required only for actions that move assets **out** to an arbitrary
  address: `withdraw` and `send_tokens`. 2FA must be set up first; otherwise those actions are refused up front.
  Trades and other actions inside the user's own wallets do not need a code. A hijacked session can therefore lose
  value on bad trades but cannot move assets out. The Telegram daily buy cap limits that exposure.
- Argon2id password hashing, TOTP secrets encrypted at rest, session tokens stored hashed.
- Rate limits on auth and spend endpoints. All spend endpoints require an idempotency key.
- Full audit log (`activity`) for wallet creation, funding, trades, exports.
- Secrets only from environment variables; no private key ever logged or returned by any endpoint.
- Legal: launching tokens and taking a fee on sells may be regulated in some jurisdictions. Get advice before
  operating this as a hosted service for other people. This spec is not legal advice.

## 10. Milestones

1. Auth, wallets, signer, dashboard shell (scaffold complete)
2. Pump.fun create + dev buy + workspace holdings (scaffold has adapter and routes; needs mainnet test with small amounts)
3. Manual multi-wallet trading, fees, claim rewards, P&L cards
4. Streamflow lock, burn, send, swap quote asset
5. Second and third launchpad adapters, then EVM
6. Hardening: Turnkey policy templates, monitoring, load test, external security review

## 11. Telegram bot (terminal)

The bot is a thin client over the same API (`backend/helm_bot`, run with `python -m helm_bot`). It has no logic of its
own for money: the API produces the preflight, enforces 2FA and idempotency, and runs the job.

**Linking.** Web Settings -> Telegram -> Link creates a one-time code (10 characters, 10 minutes, stored hashed).
The user sends `/link CODE` to the bot in a private chat. The bot calls `/telegram/bot/link` with a shared service key.
One Telegram account maps to one Helm Terminals account. Wrong codes are throttled (5 per 10 minutes per Telegram user).

**Sessions.** With the service key the bot can only ask for a short-lived (30 minute) *bot-channel* session for a
linked Telegram ID. Bot sessions use the normal API but are refused on account-security endpoints (2FA, session list,
linking). Unlinking, from the web or the bot, deletes the link and revokes bot sessions immediately.

**Spending.** Every fund-moving command replies with the preview and Confirm/Cancel buttons. Confirmations are bound
to the requesting Telegram user, expire after 2 minutes, and work once. If the API says a 2FA code is required the bot
asks for it in chat and deletes the message. Three wrong codes cancel the action. A warning in the preview (for example
"2FA not set up" or the daily limit) removes the Confirm button entirely.

**Limits.** Buys placed through Telegram are capped per rolling 24 hours (`BOT_DAILY_BUY_LIMIT_SOL`, default 5).
Deploying and launch creation stay in the web app (images, wallet attachment and review need a full screen).

**Commands.** `/wallets /launches /launch /transparency /buy /sell /claim /burn /send /withdraw /status /unlink /help`.
Ids are the first 6 characters shown in the lists.

**Known limits.** Pending confirmations are held in memory, so run one bot process (or move `PendingStore` to Redis).
The service key is powerful: anyone holding it can request sessions for linked users, so keep it in a secret store and
rotate it if leaked. Long-polling is used; switch to webhooks when running several instances.
