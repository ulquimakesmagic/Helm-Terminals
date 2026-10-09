# Fees: $10 launch fee, 1% trading fee, 3% creator-reward share (9 October 2026)

| Fee | Before (code default) | Now (code default) | Admin setting |
|---|---|---|---|
| Launch fee | none | **$10 flat per launch** | `launch_fee_cents` (shown in dollars) |
| Trading fee (Solana launchpads, EVM trades via KyberSwap) | 0 (`PLATFORM_FEE_BPS`) | **1%** | `trade_fee_bps_solana`, `trade_fee_bps_evm_dex` |
| Creator-reward share (Pump.fun, Bags, Clanker) | 0, Clanker only via `CLANKER_PLATFORM_REWARD_BPS` | **3%** on all three | `creator_share_bps` |
| Pons creator-reward split | fixed 6% | unchanged, fixed 6% | none: set by the deployed contract |

**Production keeps whatever was saved in Admin > Fees.** Code defaults only apply where no admin value was saved and
the env var isn't set. After deploying, open Admin > Fees and set: trading fees 100 bps, creator share 300 bps,
launch fee $10. Check `PLATFORM_FEE_BPS` in Railway too: if it's set, it wins over the new default.

## Launch fee
- The preview states "$10, paid in SOL/ETH/BNB at the live price after the token is created", an estimate in the coin,
  and a **maximum**: the estimate plus 10%, rounded up to two significant figures. The maximum is signed (a two-minute
  quote approval); confirmation re-prices the fee and refuses if it needs more coin than that, so a price glitch can't
  take more than the user saw. A price below a sanity floor (SOL $1, ETH $10, BNB $1) is treated as a bad feed.
- Pricing comes from Codex (`CODEX_API_KEY` required). If the price can't be read, the launch is held back with a
  clear message. With fees on and a treasury set, a Codex outage blocks launches.
- The dev wallet's balance check and auto-funding include the maximum.
- It is sent from the dev wallet to that chain's treasury **only after the launch is live** (on Pump.fun, after the
  creator-share setup). A failed launch pays nothing. If sending the fee fails or errors, the launch stays live and
  the fee is recorded as a failed `launch_fee` revenue event.
- Ledger: `RevenueEvent(source="launch_fee", status=received|pending|failed)`; shown as "Launch fees" in Admin.

### Known gaps (revenue, not user funds)
- No fee is charged when a launch only becomes live later: a deploy that timed out and is confirmed by `/recheck`,
  or a Pump.fun launch whose creator-share setup finishes on `/recheck`. The job notes "not charged".
- Failed fees aren't retried, and a `pending` (timed-out) fee isn't reconciled automatically; check it on the explorer.
- The user controls the dev wallet, so moving funds out between the deploy and the fee makes the fee fail.
- Not charged on Arc (Tolly) launches, which use a separate deploy path.
- `LAUNCH_FEE_USD` is clamped to $0–$100 like the admin setting.

## Creator-reward share
One rate now applies to every launchpad with a native split. Existing launches keep the split they were created with
(it is fixed on-chain). Pons stays at 6%: its splitter address derives from the contract bytecode, so 3% there needs a
new contract version and factory deployment while keeping the old version for existing launches.

## Also
Repeated "mainnet" badges and warnings were removed from network and wallet dialogs: every network Helm offers is
mainnet.

## Rollback
Redeploy the previous commit; admin-saved values are unaffected. `CLANKER_PLATFORM_REWARD_BPS` is no longer read;
`CREATOR_SHARE_BPS` replaces it.
