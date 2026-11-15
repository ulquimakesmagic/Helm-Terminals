"""Spend bounds for Solana transactions that a third party built (audit HT-01).

PumpPortal, Bags, Jupiter (Studio and swaps) and Relay return complete transactions. Helm cannot decode every
program they may route through, so it checks what the transaction would *do* to the signing wallet instead:

1. Read the wallet's system account and every SPL Token / Token-2022 account it owns.
2. Simulate the unsigned transaction with those accounts returned post-state.
3. Refuse to sign unless:
   - the wallet still exists and is still owned by the System Program (no `Assign`);
   - its lamports drop by at most the approved debit (amount + Helm fee + a bounded network/rent allowance);
   - no token balance drops except the approved asset, by at most the approved amount;
   - no token account changes owner, gains a delegate, or gets a new close authority;
   - an approved minimum output (swaps) is actually received.

Simulation can differ from execution for a program that deliberately behaves differently, so this is a strong
check against drain-shaped payloads, not proof of intent. Any parsing or RPC failure refuses the transaction.
"""

from dataclasses import dataclass, field

SYSTEM_OWNER = "11111111111111111111111111111111"
LAMPORTS_PER_SOL = 1_000_000_000
# Base fee, priority fee within PumpPortal's defaults, and rent for a few new accounts (ATA ≈ 0.00204 SOL each).
TRADE_OVERHEAD_LAMPORTS = 15_000_000  # 0.015 SOL
# Token creation also pays rent for the mint, metadata and curve accounts and, on some launchpads, lookup tables.
CREATE_OVERHEAD_LAMPORTS = 100_000_000  # 0.1 SOL
# Fee charged by a transaction builder inside the transaction (PumpPortal's trade-local fee is 0.5%).
PROVIDER_FEE_BPS = 100  # allow up to 1% so a builder's own fee change does not block trading, without enabling drains
MAX_GUARDED_TOKEN_ACCOUNTS = 99


@dataclass(frozen=True)
class SpendLimits:
    wallet: str
    max_lamports_out: int
    token_out: dict = field(default_factory=dict)  # mint -> maximum raw decrease across the wallet's accounts
    token_in_min: dict = field(default_factory=dict)  # mint -> minimum raw increase (approved output floor)
    min_lamports_in: int | None = None  # minimum net lamport increase (a swap or sell into SOL)
    watch_mints: tuple = ()  # mints whose (possibly new) token accounts must be included, e.g. a swap output
    label: str = "transaction"


@dataclass
class TokenState:
    account: str
    mint: str | None
    amount: int
    owner: str | None
    delegate: str | None = None
    close_authority: str | None = None


@dataclass
class WalletEffects:
    lamports_before: int
    lamports_after: int | None  # None: the wallet account would no longer exist
    owner_after: str | None
    tokens_before: dict  # account -> TokenState
    tokens_after: dict  # account -> TokenState | None (None: closed)
    error: str | None = None  # simulation error, when the transaction would fail


def budget(amount_lamports: int, helm_fee_lamports: int = 0, overhead: int = TRADE_OVERHEAD_LAMPORTS) -> int:
    """Largest lamport debit a transaction spending `amount_lamports` may cause."""
    provider = amount_lamports * PROVIDER_FEE_BPS // 10_000
    return max(0, amount_lamports) + max(0, helm_fee_lamports) + provider + overhead


def check(effects: WalletEffects, limits: SpendLimits) -> str | None:
    """Reason to refuse, or None when every effect is within the approved limits."""
    if effects.error:
        return f"simulation failed: {effects.error}"
    if effects.lamports_after is None:
        return "the transaction would close the signing wallet"
    if effects.owner_after != SYSTEM_OWNER:
        return "the transaction would reassign the signing wallet to another program"
    lamports_out = effects.lamports_before - effects.lamports_after
    if lamports_out > limits.max_lamports_out:
        return (f"the transaction would spend {lamports_out / LAMPORTS_PER_SOL:.6f} SOL, more than the approved "
                f"{limits.max_lamports_out / LAMPORTS_PER_SOL:.6f} SOL")
    if limits.min_lamports_in is not None and -lamports_out < limits.min_lamports_in:
        return "the transaction would return less SOL than the approved minimum"
    decrease: dict[str, int] = {}
    increase: dict[str, int] = {}
    for account, before in effects.tokens_before.items():
        after = effects.tokens_after.get(account, before)
        if after is None:  # closed: everything it held left the wallet
            decrease[before.mint or "?"] = decrease.get(before.mint or "?", 0) + before.amount
            continue
        if after.owner != limits.wallet:
            return "the transaction would transfer ownership of one of the wallet's token accounts"
        if after.delegate and after.delegate != before.delegate:
            return "the transaction would approve a delegate on one of the wallet's token accounts"
        if after.close_authority and after.close_authority not in (before.close_authority, limits.wallet):
            return "the transaction would change who can close one of the wallet's token accounts"
        delta = after.amount - before.amount
        mint = after.mint or before.mint or "?"
        if delta < 0:
            decrease[mint] = decrease.get(mint, 0) - delta
        elif delta > 0:
            increase[mint] = increase.get(mint, 0) + delta
    for account, after in effects.tokens_after.items():
        if account in effects.tokens_before or after is None:
            continue
        if after.owner != limits.wallet:
            continue  # a new account the wallet does not own is not a wallet asset
        if after.delegate:
            return "the transaction would create a token account with a delegate"
        if after.amount > 0:
            increase[after.mint or "?"] = increase.get(after.mint or "?", 0) + after.amount
    for mint, amount in decrease.items():
        allowed = int(limits.token_out.get(mint, 0))
        if amount > allowed:
            return (f"the transaction would move {amount} raw units of token {mint[:8]}…, more than the approved "
                    f"{allowed}")
    for mint, minimum in limits.token_in_min.items():
        if increase.get(mint, 0) < int(minimum):
            return f"the transaction would deliver less of token {mint[:8]}… than the approved minimum"
    return None


async def enforce(rpc, raw: bytes, limits: SpendLimits) -> str | None:
    """Simulate `raw` and check its effects; any failure to evaluate them refuses the transaction."""
    try:
        effects = await rpc.wallet_effects(raw, limits.wallet, limits.watch_mints)
    except Exception as e:  # noqa: BLE001 - an unreadable outcome is never a reason to sign
        return f"couldn't verify the transaction's effects before signing ({type(e).__name__})"
    reason = check(effects, limits)
    return f"refused before signing: {reason}" if reason else None
