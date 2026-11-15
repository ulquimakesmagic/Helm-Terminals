# Helm fees on TRON and Arc (10 October 2026)

The $10 launch fee and the trading fee now apply to SunPump (TRON) and Tolly (Arc) launches, as on Helm's other chains.

| Fee | SunPump (TRON) | Tolly (Arc) | Meme.Cooking (NEAR) |
|---|---|---|---|
| Launch fee ($10, `launch_fee_cents`) | in TRX at the live Codex price | in USDC (Arc's coin, $1) | none: no verified NEAR price source |
| Trading fee (`trade_fee_bps_extra`, default `PLATFORM_FEE_BPS`) | 1% of TRX spent / received | 1% of USDC spent / received | none: deposits are auction bids |
| Creator-reward share | none (no native split) | none | none |

How it works
- Launch fee: the same signed-maximum and charge-after-live rules as on Solana and EVM. The preview shows $10, an estimate and a signed maximum (estimate +10%);
  confirmation refuses if the live price needs more coin. Sent from the dev wallet to that chain's treasury only once
  the launch is live; a failure is recorded as a failed `launch_fee` event and never undoes the launch.
- Trading fee: a separate transfer from each trading wallet to the chain's treasury after its trade confirms (the
  trading wallet pays that transfer's small network fee; the preview says so).
  - Buys: a share of what was spent, at most the amount the user chose (less if the balance fell by less).
  - Sells: a share of the coin received, at most the sale's quoted proceeds (SunPump's quote; Tolly's simulated
    swap), so money arriving in the wallet from elsewhere during a sell is never charged. Nothing if the quote or a
    balance read is missing.
  - Fees under 1 TRX / 0.01 USDC are skipped (a TRON transfer can burn ~0.27 TRX of bandwidth). Recorded as
    `trade_fee` revenue events.
- Both use the same checked transfer builders as user transfers (`routes/chains.py`).

Admin
- New treasury networks: `tron`, `near`, `arc` (Admin > Treasury, now labelled TRON, NEAR and Arc). **Set the TRON and Arc treasuries**, or these
  fees stay off. A TRON treasury must be an activated account (holding some TRX), or fee transfers pay activation.
- New setting: `trade_fee_bps_extra` (0–200 bps), shown as "Trading fee: SunPump (TRON) and Tolly (Arc)". It defaults
  to `PLATFORM_FEE_BPS` (1%), so setting a TRON or Arc treasury turns it on unless an admin sets it to 0.

Known gaps
- Same as the main launch fee: no fee when a launch only goes live later via `/recheck`; failed fees aren't retried.
- A fee transfer that times out stays `pending`; TRON/Arc fees aren't reconciled automatically (check the explorer).
- The deploy balance warning covers the dev buy plus the fee maximum, not SunPump's own 20 TRX launch fee, energy or
  gas (the launchpad checks those). A launch can succeed and Helm's fee then fail for lack of balance; nothing is
  overcharged, the fee is recorded as failed.
- No fees on NEAR until a NEAR price source is added.
