# Creator-reward integration plan

Last reviewed: 2026-10-01

## Rule for Helm revenue

Creator rewards must stay payable directly to the creator wherever the launchpad supports a native split. Helm's
share must be created as a separate on-chain recipient and disclosed in the deployment preview. Helm must not make
its treasury the sole reward recipient, subtract 6%, and forward the remainder. That fallback would make Helm a
custodian and payout operator, add treasury solvency and reconciliation risk, and make creators dependent on Helm to
receive revenue that the launchpad intended for them.

If a launchpad has no native creator-reward split, Helm should use a documented partner/referral share or a separately
disclosed execution fee. A token tax, LP ownership, an auction deposit, and creator rewards are distinct economic
rights and must stay distinct in the UI and revenue ledger.

## Current capability matrix

| Launchpad | What the launch creates | Creator revenue | Native Helm 6% path | Helm behavior now | Next action |
| --- | --- | --- | --- | --- | --- |
| Bags | Bonding curve or paired pool | Bags royalty | Yes | Active: creator and Helm are separate claimers | Keep and add an integration test that verifies 94/6 recipients before signing |
| Clanker | Locked Uniswap V4 liquidity | LP rewards | Yes | Active: creator and Helm are separate locker recipients | Keep; show both recipients and percentages in every deployment preview |
| Pump.fun | Bonding curve, later PumpSwap | Creator fees | Yes, through Pump Fees sharing config | Active for new launches when the admin share is enabled: Helm builds the official one-time config after creation and keeps the launch in setup until it confirms | Monitor the official API and add direct on-chain account verification as a second independent check |
| Meteora DBC | Dynamic curve, then DAMM | Creator and partner trading fees plus configurable migration liquidity | Yes, when Helm owns the DBC config | Current Jupiter Studio config has no Helm-controlled split | Create and audit a Helm DBC config whose partner/creator economics are explicit; migrate only after devnet and mainnet-fork verification |
| Flap.sh | Standard or tax token | Optional creator tax; separate launchpad commission | No creator-share split in the current adapter | Creator tax goes directly to dev; Helm may receive Flap's separate commission on tax tokens | Keep these as separate revenue lines; do not take 6% from creator tax |
| Pons | Bonding curve | Optional creator tax | No multi-recipient split in the current adapter | Creator tax accrues for the dev wallet | Keep direct-to-creator; pursue a provider partner fee rather than routing tax through Helm |
| LetsBonk | Bonding curve | Launchpad creator fees | Not integrated or verified | Claims stay on bonk.fun | Confirm an official multi-recipient API or contract instruction before adding a share |
| Four.meme | Bonding curve | No creator reward on Helm's standard-token path | No | No creator-reward share | A tax-token mode would alter token economics and must not be used as a substitute for a platform reward split |
| Uniswap V2 | Direct pool | Fees belong to the LP-token holder | No creator-reward concept | No creator-reward share | Keep LP ownership explicit; never take an LP position merely to imitate a creator fee |
| SunPump | TRON bonding curve | No verified split in the current integration | No | No creator-reward share | Confirm an official partner or multi-recipient mechanism before enabling revenue |
| Meme.Cooking | NEAR fair-launch auction | Auction settlement, not creator fees | No | No creator-reward share | Keep auction proceeds and refunds out of creator-reward accounting |
| Tolly | Arc locked V3 pool | No verified split in the current integration | No | No creator-reward share | Confirm the deployed contracts and official partner economics before enabling revenue |

## Delivery order

1. Keep Bags and Clanker at the configured 6% native split and make the preflight show creator address, Helm treasury,
   94%, and 6% as separate immutable terms.
2. Keep Pump.fun's sharing setup as a required post-create stage. The official transaction builder is
   `POST https://fun-block.pump.fun/agents/sharing-config`; Helm validates the returned 94/6 shareholder list before
   signing and does not mark the launch live until both transactions confirm. Fee claims use `/agents/collect-fees`,
   which automatically distributes to all shareholders when a sharing config exists.
3. Build a Helm-owned Meteora DBC configuration using the direct Meteora SDK. Treat this as a new adapter version because it changes curve and
   migration economics from the current Jupiter Studio route.
4. Ask LetsBonk, Flap, Pons, Four.meme, SunPump and Tolly for documented partner or referral programs. Add each only
   when Helm can record an independently claimable revenue entitlement.
5. Keep the existing trade and route fees for launchpads without a creator split. Display them separately and never
   describe them as creator rewards.

## Sources used for the next integrations

- [Pump.fun creator fee sharing](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/CREATOR_FEE_SHARING.md)
- [Pump.fun official fee transaction API](https://github.com/pump-fun/pump-fun-skills/blob/main/coin-fees/SKILL.md)
- [Bags royalty split](https://support.bags.fm/en/articles/13434876-royalty-earned)
- [Clanker authenticated deployment rewards](https://github.com/clanker-devco/DOCS/blob/main/api-reference/authenticated/deployment.md)
- [Meteora DBC accounts and fee fields](https://github.com/MeteoraAg/docs/blob/main/developer-guides/dbc/program/accounts.mdx)
- [Meteora DBC overview](https://github.com/MeteoraAg/docs/blob/main/core-products/dbc/what-is-dbc.mdx)
