# Pons native reward split — implementation and activation

Status: implemented and locally tested, **not independently audited, deployed or enabled**. This document is not an audit certificate. No live transaction was signed or sent during development.

## Verified protocol compatibility

The [Pons V2 documentation](https://docs.ponsfamily.com/v2) specifies `TokenParams.creatorFeeRecipient`, native escrow `balanceOf(address)` / `claim()`, and rewards before and after graduation. The deployed factory accepts a nonzero recipient without requiring EOA status or deployed code. The escrow credits that address and its `claim()` pays `msg.sender`, so a contract can collect its own rewards.

Source verification was checked on 2026-10-02 using Sourcify's verified deployments:

- [Pons factory, chain 4663](https://sourcify.dev/server/v2/contract/4663/0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e?fields=all). Runtime keccak256: `89a27da6f703e0a7cdd4f233e7cb57604ff75b164530962d3ff7cf8483a67d84`.
- [Pons escrow, chain 4663](https://sourcify.dev/server/v2/contract/4663/0xd3afeb2a57f70ef218aa82451c51b2fb0416ac9e?fields=all). Runtime keccak256: `f25f75cfbc1637ba068dc34f69098fa4e8a80f8ee8fe7bf7820594e0b3fed2f1`.

The backend pins both code hashes and the compiled Helm factory runtime. Missing code, another chain, a proxy, or a different build is rejected. Local execution tests use a mock escrow with these verified native claim semantics; they are not a live Pons fork test.

## Flow

1. For an enabled **new native-ETH Pons launch**, the backend derives a unique launch key from Helm's launch ID and computes a CREATE2 reward address using the reviewed factory and immutable creator/treasury addresses.
2. The confirmation preview binds that address, factory, both beneficiaries, fixed 600-bps share and the permanent terms. A treasury/config change invalidates the old confirmation. No client-supplied reward addresses are accepted as launch options.
3. Pons's ordinary factory or launch-and-buy router receives that address as `creatorFeeRecipient`. Launching is still one transaction; the dev buy still goes to the dev wallet. Reward configuration is saved before broadcast, including no-dev-buy launches. The receipt resolver survives a restart.
4. Pons credits rewards to that address even before its splitter exists. At first claim the factory deploys the deterministic splitter, it claims its escrow balance, and it attempts both payouts. The preview estimates gas including this first deployment.
5. **94% goes to the creator and 6% to the frozen Helm treasury.** This applies to all native receipts at that reward address, including the creator portion of protocol fees, any creator tax, and donations. It is not 6% of trading volume or a deduction from the gas-paying wallet.

Any account can pay gas to call `distribute(creator, treasury, launchKey)` on the factory. No Helm API or signature is required for independent access. An existing splitter also exposes permissionless `collect()` and `release(beneficiary)`: ETH always goes to the immutable beneficiary, never the caller. The factory ABI is in `backend/app/chains/contracts/HelmPonsRewardsFactory.json`; saved launch responses expose all three arguments and the reward address. A rejecting beneficiary's payout stays claimable and does not block the other. Direct release has no helper gas cap, allowing more complex receiving wallets to claim separately.

## Boundaries that must be disclosed

- New launches only. Old wallet-wide Pons escrow claims retain their existing behavior with no Helm deduction. No automatic migration of existing launches.
- Native ETH only; custom quote assets and buyback-token claims are not supported. Helm continues to launch with buybacks disabled. Do not add custom pairs or enable buybacks without extending and auditing asset handling.
- No owner, upgrade, mutable percentage, recipient rotation, or arbitrary-call escape hatch. This also makes Pons self-service recipient transfers and buyback controls unavailable through these recipients. A lost or permanently rejecting recipient wallet cannot be replaced by Helm.
- Pons itself retains its documented **three-day timelocked authority to redirect future rewards**. The splitter cannot prevent that protocol-level override. Accrued balances remain payable through the old splitter.
- The 6% is fixed by this contract version, independent of the general admin creator-share slider. Changing a global setting or treasury does not modify an existing splitter. Disablement affects new launches only; existing claims use their saved, verified factory.
- Configuration is not revenue. The ledger initially records `configured` with no amount. Only matching `Released(treasury, amount)` logs from this exact splitter in a successful Helm-submitted transaction are booked as received. Repeated checks do not duplicate entries; receipt checks recover timeout/crash cases. Donations cannot be distinguished from trading rewards at payout time and are labelled accordingly.
- Third-party/direct on-chain payouts work but are not automatically indexed into Helm's ledger. Treasury reconciliation must account for them separately. No background claims or promise of automatic payout scheduling is included.
- Confirmation follows Helm's existing successful-receipt policy. This does not add a finality/reorg indexer.

## Activation checklist

1. Independently review `contracts/HelmPonsRewards.sol`, its creation/runtime artifacts, recipient permanence, CREATE2 derivation, rounding, external calls, payout accounting and protocol override risk. Resolve findings before production activation.
2. Reproduce the build with Node: in `contracts`, run `npm ci --ignore-scripts`, then `npm run check`. Compiler is pinned to Solidity 0.8.26, optimizer 200 runs, Paris EVM. No compiler download or compilation happens on the backend.
3. Install the separate test dependencies with `backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-contract-tests.txt` and run the tests listed below.
4. Deploy **only the reviewed HelmPonsRewardsFactory creation bytecode**, with no constructor arguments and zero ETH value, to Robinhood Chain **4663** using the intended deployment wallet. This step costs gas and has not been performed. Publish/verify the source and exact compiler settings on the explorer. Verify deployed runtime matches the checked-in factory artifact; do not use a proxy.
5. Configure the Robinhood treasury under Admin → Treasury, or the existing default EVM treasury fallback. Carefully verify that wallet can receive native ETH. It becomes permanent for each new launch.
6. Set `PONS_REWARDS_FACTORY` to that verified factory address, then `PONS_REWARDS_ENABLED=true`. Existing `EVM_RPC_ROBINHOOD` remains the RPC setting. No new private keys, treasury signer access, or database migrations are needed. An explicitly enabled but incomplete/mismatched setup blocks launch preflight instead of silently dropping the split.
7. Verify a launch preview shows the intended creator, treasury, unique reward address and 94/6 terms before approving any live launch. Separately approve a small live acceptance test after review; compare the recorded recipient, actual payouts and ledger with the explorer.

Rollback: set `PONS_REWARDS_ENABLED=false` to stop setting splitters on new launches. Keep the committed artifacts/code needed to claim existing splitters. Removing the backend setting does not remove the independent on-chain withdrawal path.

## Checks

- `tests/test_pons_contract.py`: actual compiled bytecode in a local EVM; counterfactual escrow credits, permissionless deployment, duplicate claims, different launch keys, recipient substitution, rejecting recipients, reentrancy, tiny/random receipts, rounding and conservation.
- `tests/test_pons_rewards.py`: both launch paths, code verification, invalid configs, stale consent, frozen treasury, legacy isolation, receipts, restart/timeout recovery and duplicate ledger prevention.
- Existing Pons, Flap revenue, admin and audit-regression suites; frontend tests and production build.

These checks reduce implementation risk; they do not replace independent contract review or live acceptance testing.
