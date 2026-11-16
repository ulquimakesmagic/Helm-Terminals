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
3. Implement attributed normal/deployer collection with receipt proofs, referrals
   based on fees actually collected, integer rounding and duplicate protection.
4. Test historical configurations, multiple tokens in one deployer vault,
   sponsorship reimbursements, deferred claims, partial execution and recovery.
5. Enable each flow only after those checks pass; funded rehearsals remain an
   operator action when mainnet funds are available.

Bags' Robinhood Chain contracts are a separate integration. Its repository now
includes v3 ABIs while some public contract guides describe v2. Current proxy and
beacon implementations, creation parameters, taxes and post-graduation router
encoding must be verified independently. Solana's separate deployer-share rule
must not be copied to Robinhood Chain without evidence.
