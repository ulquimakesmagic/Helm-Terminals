# Launchpad capability review

The Clanker rows below describe the 15 November baseline. They are superseded by
the [16 November V5 integration](clanker-v5-integration.md); other gates remain.

This matrix separates Helm's implemented routes from the complete upstream product.
Review date: 15 November 2026. Readiness is reported per operation, not inferred from
a logo, an API key, or a launchpad's own availability. No financial transaction was
broadcast during research or verification.

| Adapter | Implemented Helm flow | Additional upstream features / remaining requirement |
|---|---|---|
| Pump.fun | SOL launch, metadata, optional first buy, native creator split | Alternate quote/reward modes require corresponding transaction and asset validation; do not substitute a social/read-only API for launch execution |
| LetsBonk / Raydium LaunchLab | SOL curve route | Creator split collection is incomplete; requires protocol-compatible collection before new launches |
| Bags | SOL metadata, fee config, first buy and creator recipients; restricted quote discovery already exists | Current non-SOL launch uses a separate Fee Share V2 and transaction-plan flow; quote eligibility, stages and raw units must be verified before enabling it |
| Meteora DBC | Jupiter Studio SOL/USDC presets | Creator reward collection is incomplete; a working Jupiter key alone cannot enable creation |
| Printr | Current adapter's SOL launch flow | Native creator-reward collection is incomplete; its external execution/provider boundary needs acceptance verification |
| Flap BNB | Native quote; standard token updated to V7 with holder LP dividends and Infinity CL migration; tax V6 remains separately gated | Legacy V2-permit standard creation is disabled upstream. Alternative quotes, vault factories and independent tax allocations need their own reviewed variants and compatible reward collection |
| Flap Robinhood | Native-quote V6 route, preflight simulation | Chain-specific contract version must be checked; BNB V7 rules must not be assumed on another chain |
| Flap Base | Tax-only route | Creator reward collection is incomplete; do not advertise standard creation without a verified standard implementation |
| Clanker Base / Robinhood | Pool settings, locker rewards, native creator split | Advanced quote/tokenized-stock pairings require the exact supported deployment variant, quote eligibility and asset-aware funding/fees |
| Pons | ETH pairing, pinned new 250-bps splitter artifacts | Separate V2 factory deployment and runtime verification required; historical 600-bps factory cannot be reused |
| Four.meme | BNB route and supported token classification | Open Mode, other quote assets and custom tax variants are separate integrations |
| Uniswap V2 | Token creation and ETH liquidity, retained/burned LP tokens | Initial liquidity is not a developer trade; concentrated-liquidity builders are separate products |
| SunPump | TRON launch and curve trading | No verified SunSwap post-graduation route; preserve that limitation |
| Meme.Cooking | NEAR auction, soft/hard caps, duration and optional deposit | Auction deposits are not immediate token buys; post-settlement management depends on minted token state |
| Tolly | Arc USDC preset and locked pool | Native USDC has chain-specific units; do not treat it as ETH |
| Bankr | Integration research; not enabled | Official deployment API needs user/partner credentials and uses Bankr deployment custody. An org agreement, recipient split verification and explicit compatibility with Helm's signing model are required |
| Surge / Anoncoin / Stonk.fun | Planned | Verified official deployment interface, signing/recipient validation, collection and recovery integration remain required |
| Elpida variants | Planned | Verified mainnet contracts/programs, execution interface and final reward semantics required |

## Current operational findings

Read-only checks using Railway's configured environment confirmed Bags, Jupiter and
Pinata credentials exist. Pons V2 factory configuration is absent. Public live
`/launchpads` confirms Pump.fun, Bags and both Clanker variants are configured.
The remaining creator-bearing setup states are integration gates, not missing API
keys that can be fixed by relabelling a button. Both NEAR and TRON balance-provider
probes succeeded; wallet rendering must inspect their `balance_native` field.

## Primary references

- [Flap current launch interfaces](https://docs.flap.sh/en/flap/v2/developers/bonding-curve/launch-tokens): V7 non-tax / V6 tax, supported fee slots, quote funding and vault constraints.
- [Flap deployment registry](https://docs.flap.sh/en/flap/v2/developers/deployed-contract-addresses): separate bonding-curve and pool-launch contracts; chain-specific implementations.
- [Bags custom quote flow](https://docs.bags.fm/how-to-guides/launch-token-non-sol-quote): live quote availability, configuration snapshots, raw quote quantities and staged plans.
- [Bags developer index](https://docs.bags.fm/llms.txt): token launching, creator sharing, quote discovery and claiming APIs.
- [Meteora developer index](https://docs.meteora.ag/llms.txt): DBC, DAMM and pool-management documentation.
- [Pump developer API](https://docs.pump.fun/): this API documents read-only scopes; it does not supply the missing launch variants.
- [Bankr deploy API](https://docs.bankr.bot/token-launching/api-reference/deploy-token-launch/): user/partner credentials, custody, rate/recipient limits and chain-dependent deployment options.
- [Bankr launch overview](https://docs.bankr.bot/token-launching/overview/): quote availability and stock pairing are conditional features.

The native adapters' remaining protocol references are embedded in their source and
in `docs/proxima-infrastructure-analysis.md`. This review does not certify every
upstream option as implemented. User-facing forms must advertise only the routes
that Helm can validate and execute.
