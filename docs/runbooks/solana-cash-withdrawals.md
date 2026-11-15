# Solana cash withdrawals and sweeps

Implemented in the root Helm checkout (not the nested `Helm-Terminals` audit clone).
No production configuration, funding, migration, or transfer is performed by this change.

## Behavior

- `/wallets/withdraw` remains native by default. `asset: "stable"` adds classic Solana
  USDC withdrawals. Fixed gross debit uses `amount`; native requests retain `amount_sol`.
  `all: true` uses the available balance. External withdrawals retain 2FA, recovery holds,
  owned-managed-wallet checks, preflight binding, and idempotent job handling.
- Backend selects a dedicated sponsor automatically when source SOL cannot cover the
  network fee and missing destination ATA deposit (or would leave invalid rent dust).
  There is no separate end-user funding step or sponsor toggle.
- Sponsor reimbursement is a **configured fixed USDC service charge**, not a live
  SOL/USD conversion. It is subtracted from the requested gross USDC debit, displayed
  before confirmation, and transferred to the sponsor atomically with the withdrawal.
  The API refuses sponsorship if that charge consumes the whole withdrawal.
- Fee estimates use `getFeeForMessage`; ATA rent is requested from RPC and is only
  budgeted if the recipient account is missing. No priority-fee instructions are added.
- Solana sweep builds one atomic transaction per wallet: USDC transfer, reimbursement
  if needed, eligible USDC ATA closure to its owner, and native SOL transfer. Exact
  network costs replace the old blanket retained SOL reserve.
- Sweeps re-prepare before signing. Only a reduced destination-account rent cost may
  change the reviewed amounts; the corresponding SOL savings also go to the destination.
  Other balance/fee/recipient changes stop that wallet's send. Multiple wallets are not
  atomic as a group; inspect each transaction in a partially completed job.
- Empty, undelegated classic USDC ATAs controlled by the wallet may be closed during
  sweep. Other assets/accounts and accounts with different close authority are untouched.
  A plain SOL withdrawal does not close token accounts.
- EVM sweeps now initialize warnings correctly, reserve for the preceding stablecoin
  send, re-read spendable native balances, and skip native sends after failed or uncertain
  stablecoin sends. EVM sponsorship and exact EVM account draining are not added.

## Operator setup

Create a dedicated Solana fee-payer wallet in the deployment's existing signing service.
Do not reuse a customer wallet, place a raw private key in these variables, or infer the
signer from a treasury destination. Initialize its classic USDC ATA and fund its SOL
balance through the normal authorized operational process.

| Variable | Meaning |
| --- | --- |
| `SOLANA_CASH_SPONSOR_ADDRESS` | Dedicated Solana fee payer public address |
| `SOLANA_CASH_SPONSOR_REF` | Its reference understood by the configured signing service |
| `SOLANA_CASH_SPONSOR_USDC_RAW` | Positive fixed USDC charge in millionths; `0` disables sponsorship |
| `SOLANA_CASH_SPONSOR_MAX_LAMPORTS` | Fee plus newly created recipient account deposit ceiling; default 3000000 |

All four are backend-only. No database migration is required. Choose the charge based
on measured costs; the code does not guarantee that an operator-set price covers SOL
costs. Example arithmetic: 50000 base units is 0.05 USDC, not a recommended price.
Monitor sponsor SOL and USDC, cap its funded exposure, and replenish operationally.
No automatic treasury top-up is implemented. Per-transaction caps are not a daily budget.
Failed on-chain transactions can consume sponsor network fees without reimbursement;
the existing simulated execution and idempotency reduce but do not eliminate this risk.

Disable sponsorship by clearing its reference or setting the reimbursement to zero.
Ordinary sufficiently SOL-funded transfers continue to work. A changed charge requires
a fresh preview; users are never silently charged a different amount.

## Limits / verification

Sponsorship covers classic mainnet USDC held in its associated token account. Non-ATA
USDC accounts, arbitrary tokens, Token-2022 extensions, and cross-chain routing are not
supported by this cash path. Rent cannot be taken out of a token account that must stay
open. An empty token account with no USDC and no SOL for the upfront network fee cannot
use USDC reimbursement; this remains an explicit unavailable state, not free sponsorship.

Validation uses mocked RPCs and disposable test databases: exact fee sizing, missing /
existing ATA, sponsor solvency and caps, tiny amounts, failed simulation, close authority,
atomic instruction order, two-signer signatures, saved-rent forwarding, duplicate replay,
and existing withdrawal/2FA flows. Two additional LiteSVM tests execute the real SPL and
System instructions with and without sponsorship, verifying emptied source accounts,
rent recovery, recipient balances, and sponsor reimbursement. No mainnet spend or
external-validator execution has been performed. Before activation, run a controlled
non-production end-to-end exercise with the actual deployment signer, then verify signer
policy and operational monitoring.

## API examples (preview only)

```json
{"wallet_ids":["owned-wallet-id"],"destination":"recipient-public-key","asset":"stable","all":true}
```

```json
{"wallet_ids":["owned-wallet-id"],"destination":"recipient-public-key","asset":"stable","all":false,"amount":10}
```

Both return a preflight; execution still requires its confirmation hash, an idempotency
key and, for withdrawals, the required 2FA code. The job records fee payer, gross/net
amounts, reimbursement, and transaction signature without recording the signer reference.
