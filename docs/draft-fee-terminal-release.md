# Draft editing, fee wallets and terminal release

## Behavior

- Undeployed drafts show artwork through an authenticated owner-only endpoint. Users can edit token details,
  retain or replace artwork, assign a developer wallet later, and return from deployment review to the draft.
  Revision checks invalidate earlier deployment consent after an edit. Live or previously signed launches cannot
  be changed back into deployable drafts.
- Every user's profile username is their referral code. Signup accepts it without case sensitivity. Renaming a
  username retains old aliases and referral attribution. A historical code owned by another account cannot be
  claimed by choosing that username. Accounts without usernames set one in Profile first.
- Admin accounts pay no Helm trading fee. The superadmin can grant or revoke an ordinary account's trading-fee
  waiver with a reason, password and fresh 2FA. Changes are audited. Standard trades remain 2.5%; creator-reward
  terms stay separate. A waived trade creates neither a collected fee nor referral earnings. Historical approved
  transactions retain their frozen fee terms during reconciliation.
- Disperse supports even, explicit, weighted and random allocations. Integer arithmetic preserves the total.
  A random split's seed and ordered recipients determine the exact reviewed allocation; reroll before review.
  Changing recipients, amounts or seed requires new consent. Ownership transparency and allocation locks apply.
- The terminal has a compact market strip, bounded chart and execution panels, visible review button, mobile
  Chart/Trade/Activity views, and a bounded, keyboard-scrollable wallet table with sticky column headings.

## Paying network fees from another wallet

| Network | Implemented behavior |
| --- | --- |
| Solana | An owned managed wallet can be the transaction fee payer and pay recipient token-account rent. Both asset owner and fee payer sign the same transaction. No gas-funding transfer or reimbursement is added. |
| Ethereum, Base, BNB Chain, Robinhood Chain | An owned managed wallet sends a reviewed native-coin gas allowance to the source first. Funding transaction fees are disclosed; unused allowance remains in the source. This is ordinary gas funding, not a relayer or account abstraction. |
| NEAR, TRON, Arc, TON | Separate fee-wallet selection is not implemented in this release. Existing network-specific transfer paths remain in use. |

Selection applies to core-network withdrawals, launch-token sends, disperse and consolidate. It does not promise
fee sponsorship for every launchpad or trade route. Automatic selection checks owned wallets and live balances;
the review names the actual payer. A native-coin batch cannot drain its selected shared fee wallet: choose a
wallet outside the source set. Funding steps are journaled before signing, and an uncertain funding receipt
stops asset transfers rather than causing an automatic retry. Withdrawals and external token sends retain 2FA.

## Validation and release constraints

- Regression tests cover private images, ownership, late developer-wallet selection, concurrent draft revisions,
  stale consent, admin waivers, historical aliases, split conservation, separate signers, funding order and uncertain receipts.
- Real System/SPL instructions executed in an isolated Solana VM with a separate fee payer, including zero-SOL
  token sources and a complete native-balance transfer. No funded production transaction was used for testing.
- Responsive and accessibility checks use isolated fake accounts at 375, 659, 800 and 1440 pixels. Third-party
  chart frames are excluded from Helm accessibility results. Representative candle screenshots use explicit fixtures.
- Before revision `42c9a01f0007`, an encrypted revision `42c9a01f0006` production backup was restored locally.
  All existing rows across 38 tables were identical after migration; one unclaimed username alias was added.
  Alembic metadata parity passed. The isolated restore cluster was stopped and removed.

This release does not deploy the unfunded Pons mainnet factory, complete Clanker V5 launch parity, or enable
the unfinished Bags custom quote-asset reward collection path. Those remain separate integration work.
