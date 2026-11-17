# Bags integration status — 17 November 2026

New Bags creation is unavailable until Helm can collect the agreed 2.5% across
**both normal creator and separate deployer rewards**. Existing launches retain
their saved allocations. Trading and existing normal SOL recipient claims are
preserved; no retroactive deployer charge has been added.

## Why the old SOL split is insufficient

Bags' [deployer-fee guide](https://docs.bags.fm/how-to-guides/deployer-fees)
describes a separate 25% deployer share for newly created SOL fee-share v2
configurations. The config payer becomes the deployer. Recipient basis points
divide the remaining 75%, not the combined pool. Historical configurations and
other launch flows can have different terms; do not infer their terms from new
SOL configuration behavior.

For a combined eligible reward pool of 100 units, the intended allocation is:

| Stream | Gross | Helm 2.5% of that stream | Creator remainder |
|---|---:|---:|---:|
| Normal recipient rewards | 75 | 1.875 | 73.125 |
| Separate deployer rewards | 25 | 0.625 | 24.375 |
| Combined | 100 | 2.5 | 97.5 |

Actual protocol calculations use integer raw units and rounding. Applying a
250-bps recipient allocation only to the normal pool collects 1.875% of the
combined rewards. Raising that recipient allocation silently would change the
agreed stream-level terms and would not provide exact combined accounting.

The deployer vault aggregates WSOL across tokens. A token filter on the claim
builder does not limit withdrawal of the shared vault. Collection must identify
eligible new Helm rewards from verified receipts/events and saved launch terms,
exclude historical/foreign/unattributed income, and record any owed fee separately
from a fee actually received. A 503 attribution response must never become zero.

## Implemented foundation

The new parser accepts Bags plan contract version 1 carrying Solana **v0** bytes.
It validates asset identity, declared transfer-fee ceilings against reviewed limits, message hashes,
signer order, existing Ed25519 signatures, resource budgets, packet sizes,
blockhash lifetimes, ordered dependencies and lookup-table setup instructions.
Plan contract version 1 is not Solana wire format V1.

The staged executor uses a single cumulative wallet spending budget. It saves
immutable stage bytes and expected work before signing, and the existing
executor saves the original transaction signature before broadcast. A failed,
uncertain or unreadable stage stops subsequent work. Recovery reads original
signatures; it never rebuilds or resends financial work automatically. Lookup
setup and an empty warming phase do not complete a launch or claim. Business
intent, output floors and spending limits remain fixed across setup phases.

These helpers are **not connected to a newly enabled Bags creation route**.
API job reconciliation will need to invoke the helper when the future adapter
uses these plans. Upstream format acceptance and combined reward collection are
still required. See Bags' [transaction-plan contract](https://docs.bags.fm/principles/transaction-plans).

## Live unsigned validation

The current custom-quote catalog returned 20 assets. Catalog presence does not
prove launch availability; each flow also has eligibility, review, availability
and enabled config fields. Legacy direct DAMM pairing is not a substitute for
the current Fee Share V2/DBC flow.

One clearly labeled TEST metadata record was created for API validation. No token
was deployed. An existing owned wallet's public address, with about 0.007 SOL,
passed the provider's initial balance admission. The unsigned `/fee-share/config/v2`
request then returned **503: Quote state is temporarily unavailable**, including
on a bounded retry. No wallet signature, funded deployment or transaction
broadcast occurred. Funding more wallets is not a demonstrated remedy for this
provider error. Local fixtures validate the transaction-handling foundation;
they do not establish successful live creation.

## Remaining acceptance

1. Obtain a valid unsigned current plan and verify its concrete wire fields,
   signer roles, priority-fee units and lookup setup behavior against the parser.
   Verify that protocol instructions enforce the declared transfer-fee ceilings.
2. Implement config, launch and claim adapters with frozen business arguments and
   exact quote units, preserving setup phases and operation-specific consent.
3. Connect verified deployer obligations to a separately reviewed payment path,
   with receipt reconciliation and referrals based on fees actually collected.
   Freeze and verify new-launch consent/configuration evidence before opting a
   launch into combined collection. The receipt/accounting helpers below do not
   enable this payment or launch flow by themselves.
4. Test historical configurations, multiple tokens in one deployer vault,
   sponsorship reimbursements, deferred claims, partial execution and recovery.
5. Enable each flow only after those checks pass; funded rehearsals remain an
   operator action when mainnet funds are available.

Bags' Robinhood Chain contracts are a separate integration. Its repository now
includes v3 ABIs while some public contract guides describe v2. Current proxy and
beacon implementations, creation parameters, taxes and post-graduation router
encoding must be verified independently. Solana's separate deployer-share rule
must not be copied to Robinhood Chain without evidence.

## Reward accounting and read-only inspection

Normal-recipient treasury income can now be recorded from finalized receipts.
The evidence requires a matching Bags claim event **and** the actual classic
WSOL transfer from the expected program-derived authority/vault into the saved
treasury's WSOL account. A quote, estimated balance, vault accrual, user payout
or unrelated treasury transfer is not sufficient. This records the existing
native allocation without an additional claim charge or historical repricing.
The received asset is recorded as WSOL, the asset directly proven by the token
transfer. Thirty percent of the collected fee is reserved as referral liability,
excluded from Helm's own revenue. Receipt keys prevent duplicate booking.

The claim worker and job recovery use this evidence. A bounded reconciliation
pass also processes finalized claims after the browser closes, caching handled
receipt signatures. Unavailable evidence stays pending; it does not become zero.
This does not create a treasury signer or automatically withdraw the treasury's
normal recipient allocation. Such withdrawals require their own authorized claim
flow; only receipts that actually pay the saved treasury are booked as received.
Unsupported native-only payout/transport variants remain pending for review.

An authenticated owner can inspect a managed or watch-only Solana wallet at:

```text
GET /wallets/{wallet_id}/bags-deployer-rewards
```

This returns exact decimal raw-unit strings, token-attributed pending/accrued
amounts, the shared vault, and unattributed funds separately. Its totals follow
the [Bags deployer-balance contract](https://docs.bags.fm/api-reference/get-deployer-claimable-positions):
the vault is not added a second time. A 503 response has no fabricated balance.
The response explicitly has no Helm fee basis and does not authorize a claim.

The bounded read-only chain audit can be run from the repository root with
server credentials supplied privately in the process environment:

```powershell
python tools/bags_reward_audit.py --wallet YOUR_PUBLIC_SOLANA_ADDRESS --max-transactions 100 --out .audit-runtime/bags-reward-audit.json
```

It reads the configured RPC's finalized deployer-vault state and history, uses
canonical block order for transactions sharing a slot, authenticates program
events, verifies matching withdrawal transfers, and reconciles the full history
against the vault's claimed/pending/lifetime counters. Incomplete or pruned
history, an audit bound, unsupported administrative withdrawals, reverted CPIs
or truncated logs refuse attribution. It never signs or sends a transaction.
Its network label is the configured RPC, not deployment authorization.

The accounting helper can record unpaid deployer fee obligations only for
server-stored, explicitly opted-in new combined-policy launches. A withdrawal
keeps each token's proven accrual separate; excess unattributed funds and tokens
without eligible saved terms are excluded. Debt is not received revenue, earns
no referral until paid, and does not trigger automatic payment. Same-slot or
earlier withdrawals relative to creation are conservatively excluded pending
more precise creation-order consent evidence. No enabled creation path currently
writes this opt-in marker.

Live read-only verification found no deployer positions and no deployer vault for
the existing test payer. That verifies the empty-state path, not a funded claim.
The custom-quote configuration-plan endpoint still returned 503. A separate
unsigned request to the older SOL configuration endpoint was rejected for
insufficient payer SOL. These are different admission failures; the successful
balance read does not validate either launch builder. Funding remains deferred.
No new metadata, token
deployment, wallet signature or broadcast was created in this follow-up work.

Binary test fixtures are explicitly synthetic and encoded independently from
the official [pinned IDL](https://github.com/bagsfm/bags-idl/blob/450a04fa94e129f05229eac6ae49c22e81691914/fee-share-v2/idl.json).
The downloaded source was matched to official Git blob
`9af0f38f286ea45287f3b8ccf6f31e2e05159b0e` before release.
Program event transport follows Anchor's [event CPI tag](https://github.com/solana-foundation/anchor/blob/master/lang/src/event.rs).
Successful fixtures are protocol tests, not evidence of a successful mainnet launch.
