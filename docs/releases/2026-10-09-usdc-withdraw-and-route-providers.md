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

Also: Solana USDC withdrawals refuse a destination that can't sign for the USDC later (an off-curve address, or an
existing account owned by a program, such as a token account or the mint), and withdrawals refuse a wallet listed
twice. Privacy note: when another wallet pays the fee, both wallets sign the same transaction, so the chain shows
they belong together.

## Route providers: Relay, with deBridge ready but off

Routes (Bridge, Fund from cash, launch funding) go through `ROUTE_PROVIDERS`, tried in order. **The default is
`relay` only.** `relay,debridge` adds deBridge DLN as a cross-chain fallback. The provider, its flat native-coin fee
and (for deBridge) Helm's fee on that order are part of the signed preview bounds; confirmation re-quotes from that
provider only and refuses a lower minimum, a higher fee or a different provider. Only Helm's deBridge adapter can set
a flat fee: a fee field in a Relay response is ignored.

What Helm checks on a deBridge quote today: the input amount is unchanged; the transaction's native value is at most
the amount (coin routes) plus the quoted `fixFee`; the recipient's address bytes appear in the transaction; Solana
order transactions are v0, paid by the source wallet, re-blockhashed and run through the spend guard with the fee
added; EVM steps go through the existing call bounds (approve exactly the amount; spender must be a contract). The
flat fee is counted in the preview's balance checks.

### Why deBridge is off by default (must be done before turning it on)
1. **Unfilled orders aren't refunded automatically.** A DLN order no solver fills stays open until the destination
   wallet (Helm-managed, and possibly without gas) sends a cancel transaction. Helm has no cancel flow yet; the review
   says so for deBridge routes.
2. **The signed order isn't decoded.** The output token, amount, chain and recipient come from deBridge's estimate,
   not from the order in the transaction. The recipient-bytes check is weak (Helm puts the recipient in the order as
   its authority too), and `tx.to` isn't checked against deBridge's contract addresses. Decoding DlnSource's order
   (and the Solana instruction) and allowlisting the contracts is required.
3. **Not tested live.** Everything is tested against mocked responses built from deBridge's docs (consulted
   2026-10-09).

Other limits: same-chain conversions and EVM Relay swaps need Relay. Launch funding plans approve no provider fee, so
with deBridge first in the list they are refused (not just with deBridge alone). Helm's fee on a deBridge route is a
DLN affiliate fee claimed from deBridge and isn't recorded in Helm's revenue ledger.

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `ROUTE_PROVIDERS` | `relay` | `relay,debridge` adds the fallback; read the section above first. |
| `DEBRIDGE_API_URL` | `https://dln.debridge.finance` | |
| `DEBRIDGE_ACCESS_TOKEN` | empty | Optional. |

## Rollback
Redeploy the previous commit. No schema change. Quotes approved before the deploy carry no provider and are re-quoted
from the default provider.
