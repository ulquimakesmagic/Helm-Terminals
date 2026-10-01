# Launchpad interface audit — 1 October 2026

## Scope and result

Public native launch pages were inspected in the browser and compared with Helm's adapter schemas. No wallet was connected, token launched, or transaction signed. This change reorganizes existing supported controls; it does not implement full native feature parity or certify the underlying integrations.

Helm now has explicit form profiles for its 14 implemented launchpad IDs, grouped settings, pairing before funding, a draft summary, mobile layout fixes, and visible integration limits. Chain switching clears incompatible wallets. Failed settings/pair loads block draft creation and offer retry. Numeric bounds, select values and auction cap relationships are checked before submission. Bags pair loading is isolated from other launchpads.

## Browser findings and remaining integration work

| Native page | Observed native workflow | Helm scope / remaining work |
| --- | --- | --- |
| [Pump.fun](https://pump.fun/create) | Metadata, SOL/USDC/custom pairing, creator/holder rewards, optional banner and image/video | SOL creator-reward route only; other modes/media require separate integration. |
| [Bags](https://bags.fm/launch) | Share/Holders/Creator rewards, social shareholders, apps, SOL/custom pairing, creator buy, preview | Existing pair discovery and configured creator/Helm split; social allocation and apps not implemented. |
| [Clanker](https://www.clanker.world/deploy) | Fees, rewards and pool sections, optional creator vault/buy/airdrop, social tools | WETH preset and sniper-fee controls; custom pool currency, vesting, airdrop and social tools missing. Robinhood variant not independently verified in browser. |
| [Flap](https://flap.sh/launch?chain=bnb&lang=en) | Standard/tax choice; separate buy/sell rates; creator/burn/dividend/liquidity allocations; recipient/vault; payment asset; anti-farmer duration | Existing equal-tax one-year preset only. Independent taxes, allocation configuration, vaults and quote tokens need adapter and transaction-preview work. Robinhood variant not independently verified in browser. |
| [Pons](https://www.ponsfamily.com/launchpad/create) | V2/V1, paired asset, creator buy, Advanced holder sharing, recipient, creator tax, snipe exemptions | V2 ETH route and creator tax; other settings not encoded by current integration. |
| [Four.meme](https://four.meme/en/create-token) | Classic/Open Mode; identity; raised token; socials; classification; anti-sniper and tax switches | Existing BNB route/category; Open Mode, other raised tokens and configurable tax not supported. |
| [Meme.Cooking](https://meme.cooking/create) | Start time, duration, caps, team allocation, preview and launch conditions | Current immediate auction, duration, caps and own deposit; scheduled starts/team allocation missing. |
| [Jupiter Studio](https://studio.jup.ag/launch) | Meme/Custom adventure, creator management, vesting and LP-fee presentation | Landing flow inspected; deeper form interrupted by wallet-connect panel. Helm's existing API curve presets differ from current native UI; custom curve parity is not established. |
| [SunPump](https://sunpump.meme/launch) | Artwork, 20-character name/ticker, 256-character description, socials and optional TRX buy | Current adapter publishes only name/ticker; image shown only in Helm. Native metadata registration and post-graduation routing remain missing. |
| [BONKfun](https://www.bonk.fun/) | Public board includes Classic/Bonkers and USD1 filters | Creation form was not exposed on inspected public page; full native form parity remains unverified. Existing SOL adapter controls retained. |
| Tolly | Not independently inspected in this pass | Existing USDC preset displayed, with fixed launch protections disclosed. Must verify native UI before claiming parity. |
| Uniswap V2 | Not independently inspected in this pass | Helm token creation plus ETH-pool preset is distinct from Uniswap's general position builder. Supply share and LP ownership grouped explicitly. |

The image limit remains Helm's backend limit of 2 MB, not a claim about each launchpad's native limit.

## Backend follow-up risks

- Existing uncommitted changes to `pons.py`, `routes/launches.py` and `platform_config.py` predate this UI work. They were preserved and are not validated by the frontend checks.
- The pending Pons revenue implementation uses wallet-wide escrow pending value and a separate treasury transfer. Attribution, partial failure and recovery need review before it is treated as production-ready revenue collection.
- Flap's tax-token migrator/version combination needs verification against the official launcher reference before expanding tax controls. Do not infer contract compatibility from a matching native UI.
- New controls must be implemented end-to-end: schema, backend validation, calldata/API request, preview, persistence and failure handling. Never add a cosmetic control whose value the adapter ignores.

## Validation

- Frontend unit suite: 8 passing tests (including bounds, unknown selection rejection, auction cap constraints and schema-field grouping).
- Production TypeScript/Vite build checked, including a rerun after temporary preview cleanup.
- Local browser fixture: Pump.fun form rendered; changing to Flap cleared the Solana wallet; tax summary updated; Meme.Cooking showed auction sections and blocked a hard cap below the soft cap.
- Mobile check at 390 × 844: modal body and funding row had no horizontal overflow after the layout fix.
- Browser fixture was local and removed after testing. It did not use live accounts or verify real deployment transactions.
