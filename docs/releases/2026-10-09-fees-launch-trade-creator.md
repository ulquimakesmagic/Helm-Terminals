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
- The preview states "$10, paid in SOL/ETH/BNB at the live price after the token is created" (part of the confirmed
  preview) and an estimate in the coin (display only).
- At confirmation Helm prices it from Codex (`CODEX_API_KEY` required) and includes it in the dev wallet's balance
  check and in auto-funding. If the price can't be read, the launch is held back with a clear message.
- It is sent from the dev wallet to that chain's treasury **only after the token is created**; a failed launch pays
  nothing. A failed fee transfer doesn't undo the launch; it is recorded as a failed `launch_fee` revenue event.
- Ledger: `RevenueEvent(source="launch_fee", status=received|pending|failed)`; shown as "Launch fees" in Admin.
- Not charged yet on Arc (Tolly) launches, which use a separate deploy path.

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
