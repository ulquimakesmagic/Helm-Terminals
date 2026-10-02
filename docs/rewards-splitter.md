# Helm rewards splitter (generalized)

Status: **implemented and locally tested; not independently audited, not deployed, not enabled.**
No live transaction has been signed. This generalizes the Pons splitter (`pons-reward-splitter.md`)
beyond native ETH so other launchpads and chains can use the same accounting.

## What it is

`contracts/HelmRewardsSplitter.sol` holds two contracts:

- **`HelmRewardsSplitter`** — the same immutable, per-launch 94/6 splitter as the Pons one
  (no owner, no upgrade path, no recipient changes, cumulative dust-resistant rounding), extended
  to pay **the chain's native coin and any ERC-20**. Assets are accounted per token: `due(asset,
  party)` derives from `balanceOf` plus cumulative releases, so rewards that accrue before the
  splitter is even deployed are paid correctly by the first distribution. `distribute(asset)` pays
  both parties in one call; a failing beneficiary is deferred, never blocking the other.
- **`HelmRewardsSplitterFactory`** — permissionless CREATE2 deployment (`predict`/`deploy`/
  `distribute`) with the same launch-key discipline: identical parameters always produce identical
  beneficiaries, and a different launch key produces a different address.

## Differences from the Pons splitter

| | Pons splitter | Generalized splitter |
|---|---|---|
| Assets | native ETH only | native + any ERC-20 (no swapping) |
| Escrow pull | `collect()` hardcoded to Pons's escrow | none — adapters claim *to* the splitter first, then distribute |
| Gas stipend | 150k per release | 300k per release (token transfers cost more) |

## Disclosed boundaries

- Donations and forced transfers of any asset follow the same 94/6 split — receipts, not proof of
  trading revenue.
- Accounting assumes standard ERC-20s: outgoing transfers deliver the full amount, and tokens that
  mint, burn or rebase balances behind the splitter's back cannot be split safely.
- The 6% is fixed per contract version; a different share means deploying a new version, never a
  parameter. Recipients are permanent.
- There is no escrow pull: where a launchpad holds rewards in its own escrow, the adapter's claim
  step must deliver them to the splitter address before `distribute`.

## Verification

`backend/tests/test_rewards_contract.py` executes the compiled bytecode in a local EVM: native and
token splits, per-asset isolation, counterfactual accrual, idempotent deploy, substitution and
replay rejection, rejecting-recipient deferral, reentrancy, and per-wei conservation of cumulative
rounding across 30 randomized rounds on two assets. `contracts/build.mjs` compiles both splitters
and `npm run check` refuses any drift between sources and checked-in artifacts.

## Activation (per chain, same discipline as Pons)

1. Independent review of `HelmRewardsSplitter.sol`.
2. `npm ci --ignore-scripts && npm run check`; run the contract tests.
3. Hand-deploy only the reviewed `HelmRewardsSplitterFactory` bytecode to the target chain; verify
   on the explorer.
4. Configure the treasury and a per-chain factory address in settings **before** any adapter uses
   it; every adapter integration must fail closed when the factory is missing or the code hash
   differs, disclose the permanent split in the deploy preview, and book revenue only from confirmed
   payout receipts.
5. Small live acceptance test per chain before enabling for real launches.
