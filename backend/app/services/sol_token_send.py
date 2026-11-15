"""Solana token sends that need as little SOL as possible.

Two things used to make USDC (or any SPL token) hard to move out of a wallet:

1. Helm assumed every recipient needed a new token account and asked for ~0.00204 SOL of rent per recipient even
   when the account already existed. Now the rent is only counted for recipients whose token account is missing.
2. A wallet holding only USDC had no SOL for the network fee, so it couldn't send at all. Now another of the user's
   own Helm-managed Solana wallets can pay: it is the transaction's fee payer and pays any token-account rent, and
   the USDC wallet only signs the token transfer. The paying wallet is chosen by the server from live balances,
   is always one of the same user's managed wallets, and is shown in the preview before anything is confirmed.

Nothing here moves the paying wallet's funds other than the network fee and token-account rent.
"""

from dataclasses import dataclass

from solders.pubkey import Pubkey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.solana import (
    TOKEN_PROGRAM,
    build_unsigned,
    create_ata_idempotent_ix,
    find_ata,
    token_transfer_checked_ix,
)
from ..models import Wallet

SOL_FEE = 10_000  # lamports per transaction, generous for priority fees
ATA_RENT = 2_039_280  # lamports to open a token account for a recipient that has none
SOL_RENT_MIN = 890_880  # a paying wallet must keep at least this much so it stays rent-exempt
MAX_PAYER_CANDIDATES = 20


async def missing_token_accounts(rpc, owners: list[str], mint: str, program: Pubkey = TOKEN_PROGRAM) -> set[str]:
    """Owners (wallet addresses) that have no token account for `mint` yet. On any read failure the owner is
    treated as missing, so the estimate errs towards asking for rent rather than failing on-chain."""
    out: set[str] = set()
    m = Pubkey.from_string(mint)
    for owner in dict.fromkeys(owners):
        try:
            ata = str(find_ata(Pubkey.from_string(owner), m, program))
            if not await rpc.account_exists(ata):
                out.add(owner)
        except Exception:
            out.add(owner)
    return out


@dataclass
class PayerChoice:
    wallet: Wallet | None  # None: no wallet can pay
    own: bool  # the sending wallet pays its own fees
    balance: int = 0


async def choose_fee_payer(db: AsyncSession, rpc, user_id: str, sender: Wallet, need_lamports: int,
                           prefer: list[Wallet] = (), exclude_ids: set[str] = frozenset(),
                           committed: dict[str, int] | None = None,
                           cache: dict[str, int] | None = None) -> PayerChoice:
    """Who pays `need_lamports` of fees/rent for a transaction `sender` signs.

    The sender pays if it can (keeping its account rent-exempt). Otherwise the first wallet that can, in this
    order: `prefer` (e.g. the destination of a consolidation), then the user's main wallets, then any other
    managed Solana wallet of the user's, richest first. `committed` tracks lamports already promised to earlier
    legs in the same request, so one payer isn't counted twice; `cache` keeps balances read during one request so
    many legs don't re-read the same wallets.
    """
    from .main_wallet import main_wallet_ids

    committed = committed if committed is not None else {}
    cache = cache if cache is not None else {}

    async def spare(w: Wallet) -> int:
        if w.address not in cache:
            cache[w.address] = int(await rpc.get_balance(w.address))
        bal = cache[w.address]
        return bal - committed.get(w.id, 0) - SOL_RENT_MIN

    own_spare = await spare(sender)
    if own_spare >= need_lamports:
        committed[sender.id] = committed.get(sender.id, 0) + need_lamports
        return PayerChoice(sender, True, own_spare)

    mains = await main_wallet_ids(db, user_id)
    others = list((await db.execute(select(Wallet).where(
        Wallet.user_id == user_id, Wallet.kind == "managed", Wallet.network == "solana"))).scalars())
    seen = {sender.id} | set(exclude_ids)
    ordered: list[Wallet] = []
    preferred: set[str] = set()
    for w in [*prefer, *sorted(others, key=lambda w: w.id not in mains)]:
        if w.id in seen or w.user_id != user_id or w.kind != "managed" or w.network != "solana":
            continue
        seen.add(w.id)
        ordered.append(w)
        if any(w.id == p.id for p in prefer):
            preferred.add(w.id)
    best: tuple[int, Wallet] | None = None
    for w in ordered[:MAX_PAYER_CANDIDATES]:
        try:
            s = await spare(w)
        except Exception:
            continue
        if s < need_lamports:
            continue
        if w.id in preferred or w.id in mains:  # a preferred or main wallet that can pay wins outright
            committed[w.id] = committed.get(w.id, 0) + need_lamports
            return PayerChoice(w, False, s)
        if best is None or s > best[0]:
            best = (s, w)
    if best is None:
        return PayerChoice(None, False, own_spare)
    committed[best[1].id] = committed.get(best[1].id, 0) + need_lamports
    return PayerChoice(best[1], False, best[0])


def build_token_send(owner: str, payer: str, legs: list[tuple[str, int]], mint: str, decimals: int,
                     program: Pubkey, blockhash: str) -> bytes:
    """One transaction: `owner` sends `amount` of `mint` to each destination; `payer` pays the fee and any rent."""
    o, p, m = Pubkey.from_string(owner), Pubkey.from_string(payer), Pubkey.from_string(mint)
    ixs = []
    for dest, amount in legs:
        d = Pubkey.from_string(dest)
        ixs += [create_ata_idempotent_ix(p, d, m, program), token_transfer_checked_ix(o, d, m, amount, decimals, program)]
    return build_unsigned(p, ixs, blockhash)


def refs_for(sender: Wallet, payer: Wallet) -> dict[str, str]:
    refs = {sender.address: sender.signer_ref or ""}
    if payer.address != sender.address:
        refs[payer.address] = payer.signer_ref or ""
    return refs


def payer_note(choice: PayerChoice) -> dict | None:
    """What the preview shows about who pays the network fee."""
    if choice.wallet is None or choice.own:
        return None
    return {"label": choice.wallet.label or "Wallet", "address": choice.wallet.address,
            "reason": "the sending wallet has too little SOL for the network fee, so another of your wallets pays it"}
