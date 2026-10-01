# Flap Base and revenue handling

## Implemented

- `flap-sh-base` uses Base (8453), ETH funding, existing Base RPC/wallet/signing support, official Flap artwork and a dedicated form profile.
- Flap's published Base Portal is `0x0000BC1c4fD15Dd79029AF8F5d77D68ae4490000`; Tax Token V3 implementation is `0x777782e7EbB89802E2379E5fFcF7336538f43333`.
- Only tax-token launches are offered for Base. The published Base table has no standard-token implementation. Helm's supported symmetric rates remain 1%, 3%, 5% and 10%, with 1% selected initially on Base.
- Tax Token V3 uses the documented V2 migrator. Deployment still simulates the call and verifies the predicted CREATE2 address before signing.
- The commission recipient shown in the confirmation is passed into the launch calldata and saved before submission. Confirmed and late-confirmed launches record it once, without rereading a potentially changed treasury setting.
- Flap records use `configured`, an unknown amount, and explicit wording that no payout has been established. Admin filtering supports this state. It does not increase received or claimable totals.
- The unfinished Pons second-transfer fee deduction was removed. Pons claims remain wallet-wide, and the preview states that scope and that Helm makes no deduction. Admin fee support explicitly marks Pons creator-share collection unavailable.

## Configuration

No new environment variables. Existing `EVM_RPC_BASE` supplies the Base RPC (the existing public fallback is available). Set and verify a Base treasury recipient under Admin → Treasury; the existing EVM default is used if no Base override is present. That address is public; no treasury private key is needed for Flap to send commission.

Flap commission is calculated by Flap and is separate from Helm's configured 6% creator-reward share. Merely setting a recipient does not guarantee trading activity or revenue. Existing tokens keep the recipient encoded when they were launched; this does not retrofit prior deployments.

## Remaining revenue work

For Pons, verify contract recipients and escrow claim semantics, then build and independently review a per-launch splitter with the creator and treasury shares frozen at creation. It must handle claims and withdrawals without putting the creator's full proceeds under Helm's custody. An alternative requires precise per-launch attribution and durable recovery for partial payments. Neither solution is included here; wallet-wide claims cannot safely be charged as if they belonged to one launch.

For Flap, add reconciliation against documented payout events/transfers, including native and token assets, confirmations, reorg handling and deduplication by chain/transaction/log. Only proven payouts should create received entries. Helm currently records configuration, not automatic payout measurement. The admin reconcile action marks a row reviewed; it does not convert a configuration marker into a payment.

## Verification and sources

- 76 backend tests passed across Flap, Pons, revenue, admin and audit-regression suites. Eight frontend tests passed; the production TypeScript/Vite build passed.
- Read-only Base RPC confirmed bytecode at both published addresses and successfully simulated a tax-token launch with the exact predicted address. No wallet key, transaction signature or broadcast was used. This is not a funded production launch test.
- Adapter and route tests cover Base tax calldata, rejection of unsupported standard launches, recipient freezing, immediate and delayed confirmation, duplicate rechecks, admin revenue totals and Pons claims with old share options.
- [Official deployed addresses](https://docs.flap.sh/flap/developers/deployed-contract-addresses.md)
- [Official Portal launch specification](https://docs.flap.sh/flap/developers/token-launcher-developers/launch-token-through-portal)
