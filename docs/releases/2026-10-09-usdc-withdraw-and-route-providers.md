# USDC withdrawals, lower SOL needs, and a second route provider (9 October 2026)

## USDC withdrawals and Solana fees

**Before:** Withdraw only sent a chain's native coin. A wallet holding USDC and no SOL could not move it out, and every
Solana token send asked for ~0.00204 SOL of token-account rent per recipient, even when the recipient already had a
USDC account.

**Now:**
- Withdraw has an asset choice: the chain's coin or its dollar stablecoin (USDC; USDG on Robinhood Chain), all or a
  fixed amount per wallet, still behind 2FA.
- Solana token sends (withdraw, disperse, consolidate, sweep) count rent only for recipients whose token account is
  missing (`getAccountInfo` check; a failed read assumes missing).
- If the sending wallet lacks SOL for the fee, another of the user's own Helm-managed Solana wallets pays it as the
  transaction's fee payer (and pays any rent). The server chooses it from live balances (the destination first for
  consolidate and sweep, then main wallets, then the richest), keeps it rent-exempt, never uses a wallet whose SOL a
  sweep is emptying, and names it in the preview. Only the fee and rent leave that wallet.
- EVM stablecoin withdrawals pay gas from each wallet; the preview says how much of the chain's coin is missing.
- Sweep previews subtract the stablecoin leg's own fee from the SOL left to sweep.

## Route providers: Relay plus deBridge

Routes (Bridge, Fund from cash, launch funding) go through `ROUTE_PROVIDERS`, tried in order (default
`relay,debridge`). The first provider that can quote is used; the provider and its flat native-coin fee are part of
the signed preview bounds, and confirmation re-quotes from that provider only, refusing a lower minimum, a higher fee
or a different provider.

deBridge DLN (cross-chain only) is normalised to Relay's quote shape, so the existing bounds apply (output asset and
chain, minimum output, Solana spend guard with the fee added, EVM call bounds allowing only the approved flat fee).
The adapter also refuses a transaction that doesn't contain the recipient's address, that sends more native coin than
quoted, or that changes the input amount. Solana order transactions must be v0 and paid by the source wallet; they get
a fresh blockhash before signing.

Helm's route fee on deBridge is a DLN affiliate fee at the same rate, paid to the treasury address of the source
chain's family (EVM or Solana) when one is set, claimed from deBridge. It is shown in the review but **not yet recorded
in Helm's revenue ledger** (Relay app fees still are).

### Known limits
- Same-chain conversions (and EVM Relay swaps) still need Relay. With `ROUTE_PROVIDERS=debridge`, they return a clear
  "needs Relay" error.
- Launch funding plans are made before a quote, so they approve no provider fee; with deBridge as the only provider
  those routes are refused at confirmation rather than paying an unreviewed fee.
- deBridge is tested against mocked API responses built from its documentation (consulted 2026-10-09), not against
  the live API. Rehearse a small route on each chain pair before relying on it.

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `ROUTE_PROVIDERS` | `relay,debridge` | Order matters. |
| `DEBRIDGE_API_URL` | `https://dln.debridge.finance` | |
| `DEBRIDGE_ACCESS_TOKEN` | empty | Optional. |

## Rollback
Redeploy the previous commit. No schema change. Quotes approved before the deploy carry no provider and are re-quoted
from the default provider.
