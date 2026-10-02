# Pons reward splitter activation runbook

Operator procedure for enabling Helm's per-launch 94/6 Pons reward splitter on Robinhood Chain
(chain 4663). Nothing in this procedure is evidence that it has been performed: the contract is not
independently reviewed, the factory is not deployed, and `PONS_REWARDS_ENABLED` ships off by design.
Every gate below fails closed — an incomplete setup blocks launch preflight rather than creating a
launch with no revenue split.

## Operating constraints

- The reviewed Solidity source is `contracts/HelmPonsRewards.sol`. The compiled bytes it must
  produce are pinned in `backend/app/chains/contracts/HelmPonsRewards*.json`; `npm run check` in
  `contracts/` refuses any drift and CI enforces it. Never deploy bytes that were not rebuilt and
  checked after the last source change.
- The deployer key never enters chat, tickets, shell history, or this repository. The deployment
  script reads it from a hidden prompt or the `HELM_DEPLOYER_KEY` environment variable in the
  operator's own shell.
- Re-running the deploy script creates a second factory. There is no reason to: if a deployment is
  uncertain, establish the outcome from the explorer and the script's verification step first.
- The factory address is frozen into every launch preview. Changing it later invalidates confirmed
  preflights for new launches (fail-closed) but does not move or strand any funds already governed
  by a deployed splitter.
- Revenue is booked only from confirmed treasury `Released` receipts on the exact configured
  factory's splitters. Configuration alone is never counted as income.

## Gate 1 — independent review

1. Engage a Solidity reviewer who did not write the contract. Send `contracts/HelmPonsRewards.sol`,
   `contracts/RewardTestHelpers.sol`, `docs/pons-reward-splitter.md`,
   `backend/tests/test_pons_contract.py`, and the integration points
   (`backend/app/chains/pons.py`, `backend/app/chains/pons_rewards.py`).
2. Ask them to verify at minimum: the cumulative 94/6 accounting and its rounding, the reentrancy
   guards, the CREATE2 front-running properties, the escrow `balanceOf`/`claim` semantics against
   the Sourcify-verified deployment, and whether the disclosed limitations (donations split like
   rewards; Pons retains a timelocked redirect authority) are acceptable to disclose to users.
3. Resolve every finding that touches fund movement. Record the review (who, date, findings,
   resolutions) in the operator's protected records.

## Gate 2 — reproducible build and tests

1. `cd contracts && npm ci --ignore-scripts && npm run build && npm run check`
2. `cd ../backend && ./.venv/Scripts/pip install -r requirements-contract-tests.txt`
3. `./.venv/Scripts/python.exe -m pytest tests/test_pons_contract.py -q`
4. Commit the rebuilt artifacts if the review changed anything; `git diff` should otherwise be
   empty. Do not proceed with locally modified artifacts.

## Gate 3 — deploy the factory

1. Fund a deployer wallet with a small amount of ETH on chain 4663 for gas. The deployer identity
   does not affect splitter addresses; any controlled wallet works.
2. Rehearse without a broadcast:
   `cd contracts && ../backend/.venv/Scripts/python.exe deploy-factory.py --rpc-url <url> --dry-run`
   (key via hidden prompt or `HELM_DEPLOYER_KEY`). The script refuses a wrong chain id, simulates
   the creation, and prints the exact gas and cost.
3. Broadcast for real by running the same command without `--dry-run` and typing `deploy` at the
   confirmation. The script then compares `keccak(eth_getCode)` against the artifact and only
   reports the address on an exact match.
4. Record the factory address and transaction hash. Verify the address on
   `robinhoodchain.blockscout.com`. The address must be identical to what step 3 printed.

## Gate 4 — configure and enable

1. Set `PONS_REWARDS_FACTORY=0x…` (from gate 3) in the deployment's secret store. Restart the API.
   A Pons launch preview at this point must fail closed only if the flag is also on; with the flag
   still off, behavior is unchanged.
2. Configure the EVM treasury (the 6% recipient) in the admin Treasury tab, backed by
   `PLATFORM_FEE_WALLET_EVM`. Use a receive-only address whose key handling follows the approved
   secrets-management system.
3. Set `PONS_REWARDS_ENABLED=true`. With the flag on but the factory missing, unverified, or the
   treasury unset, launch preflight fails closed — verify that refusal once deliberately.

## Gate 5 — live acceptance

1. Create a small Pons draft (minimal dev buy) and open the deploy preview. It must show the
   splitter address, both beneficiaries, the 94/6 terms, and the permanence/timelock warnings, and
   the preflight binds them (a factory or treasury change invalidates the confirmation).
2. Deploy. On the explorer, Pons's `creatorFeeRecipient` for the new token must be the splitter
   address.
3. Once rewards exist, claim from the Workspace claim panel with any wallet paying gas. Confirm:
   ~94% reaches the creator wallet, ~6% reaches the treasury, the gas wallet pays only gas, and the
   admin Revenue ledger books exactly one `received` ETH entry from the treasury `Released` receipt
   (no duplicates on reload or recheck).
4. Keep `PONS_REWARDS_ENABLED=true` only after every step above passed. Record the acceptance
   launch, claim transaction hashes, and ledger screenshot in the operator's records.

## Rollback

Set `PONS_REWARDS_ENABLED=false` and restart. New launches stop using the splitter immediately;
legacy wallet-wide claims resume their previous behavior. Funds already governed by a deployed
splitter remain claimable on-chain by anyone calling its `release`/`distribute` — that path does
not depend on Helm.

## Before touching real funds

- Verify the Turnkey/local signer posture independently of this runbook; the splitter receives at
  the treasury address regardless of how Helm signs.
- This runbook does not substitute for the review in gate 1. Do not deploy unaudited bytecode.
