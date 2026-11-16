# Clanker V5 launch integration — 16 November 2026

New Clanker drafts use V5 on Base, BNB Chain and Robinhood Chain. Existing V4
tokens keep their original pools, recipient allocations and V4 claim execution.
Undeployed V4 drafts must be edited and saved before a V5 deployment is reviewed.
Cloned Clanker drafts start on V5; a live or previously submitted token is never
reset to a fresh draft. V5 draft allocations read the live factory supply. A V4
draft with an existing allocation plan requires a fresh V5 draft, so its old
100-billion-token plan and locks cannot silently become a different supply.

## Supported launch flow

The adapter encodes the official SDK 5.1.0 factory calls locally. It exposes the
selected pair, separate buy/sell tax, holder-dividend and burn allocations, minimum
holder balance, factory-controlled anti-sniping and creator metadata editability.
The whole supply enters permanently locked, single-sided Uniswap V4 liquidity.
These are V5 controls; V4 vesting, airdrop and custom sniper schedules are not
silently substituted.

The discovery snapshot has 347 Base, 23 BNB Chain and 196 Robinhood Chain quotes,
including native/stable assets and upstream-listed crypto and stock tokens. It is
not permission to trade every asset. The selected factory must admit the exact
address and decimals at deployment time; stock pairs require issuer eligibility
confirmation. Search labels retain addresses to distinguish duplicate symbols.

Native pairs support atomic native-coin creator buys. USDC on Base/BNB Chain and
USDG on Robinhood Chain use the verified native-to-stable V3 hop followed by the
V5 launch pool. USDG has **6 decimals**, BNB Chain USDC **18 decimals**, and Base
USDC **6 decimals**. Other registered pairs currently require zero creator buy
in Helm: no unverified route data is accepted from the browser.

Clanker's current factory bounds are 1–10% buy/sell tax. The factory currently
sets the opening anti-sniping fee to 99% over 20 seconds when enabled. These are
upstream fees and policies, read live and frozen in the approval, not Helm trade
fees. A changed factory policy/configuration requires another review.

## Economics and claims

Helm charges no launch fee. Gas and any upstream factory fee remain separate.
Clanker's protocol fee is removed before configured dividend/burn allocations;
the remaining creator rewards have distinct 97.5% creator and 2.5% Helm slots.
Helm's normal 2.5% trading fee and admin/account exemptions are unchanged.

Creator claims pay the selected recipient's entire balance in the pair currency
**across V5 pools**, rather than a token-specific amount. Holder-dividend claims
are a separate, token-specific action. They simulate the payout, check exclusion
and payable state, and require the receiver to be the selected owned wallet.
Custom receivers must be reset upstream before claiming through Helm. Claims
have no additional Helm fee; the selected managed wallet pays network gas.

## Contract identity and approval boundary

The checked-in ABI artifact records the official source revision, published
package revision and SHA-512 integrity. Factory, hook, escrow and token-deployer
runtime SHA-256 values are pinned per network. Chain identity, launch admission,
registered quote, supply, tax policy and configuration version are checked before
transaction construction. Runtime identity is not an independent contract audit.

All state in a factory snapshot uses the same block tag. A read replica lagging
behind its advertised head triggers a bounded retry of the whole snapshot at a
recent block. Genuine contract reverts are not swallowed. Factory validation and
simulation still apply after a retry; stale approved terms cannot be signed.

Token addresses are mined locally with fresh cryptographic salts and a bounded
search. The predicted token is persisted before broadcasting, and existing Helm
transaction journaling/reconciliation prevents an uncertain deployment from
being retried as a fresh launch. No SDK private key or hosted deployment service
is used.

## Validation and remaining limits

Nine unsigned mainnet `eth_call` launches passed: native, stablecoin and one stock
pair on each supported chain. Six additional native/stable creator-buy simulations
passed, including the final minimum output. Buy probes used an ephemeral caller
balance override in `eth_call`; **no wallet was funded, no transaction signed or
broadcast, and no token was deployed**. These checks do not certify a funded
production lifecycle or an aggregator route for every secondary-market pair.

Tests cover factory changes invalidating consent, wrong chain/code/quote,
recipient slots, exact units and minimum output, live draft-to-execution flow,
dividend exclusion/empty/custom-receiver rejection, and legacy V4 claims. Browser
checks cover pair search, eligibility reset, conditional creator buys, invalid
allocations, accessibility and overflow at 375, 659 and 1440 CSS pixels.

No new Helm factory deployment or database migration is needed for this adapter.
Pons V2 funding/deployment and the other gates in the
[mainnet setup guide](mainnet-integration-setup.md) remain separate.

Primary references: [SDK V5](https://docs.clanker.world/sdk-reference/v5),
[deployed contracts](https://docs.clanker.world/references/deployed-contracts),
[quote registry](https://docs.clanker.world/references/supported-quote-tokens),
[SDK source](https://github.com/clanker-devco/clanker-sdk).
