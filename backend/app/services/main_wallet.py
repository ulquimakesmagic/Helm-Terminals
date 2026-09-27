"""Every account's main wallet: one managed Solana wallet and one EVM key registered on each EVM chain Helm
supports (the same address works on all of them). Main wallets are marked as the account's default funder per
chain in the existing `funders` table, so no schema change is needed. Launches route USDC from them when a dev
wallet is short of its chain's coin.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..chains.evm import EVM_CHAINS
from ..models import Funder, Wallet
from ..signing.base import WalletRef

log = logging.getLogger(__name__)
MAIN_LABEL = "Main"


async def main_wallet_ids(db: AsyncSession, user_id: str) -> set[str]:
    rows = (await db.execute(select(Funder.wallet_id).where(
        Funder.user_id == user_id, Funder.is_default.is_(True), Funder.label == MAIN_LABEL))).scalars().all()
    return set(rows)


async def ensure_main_wallets(db: AsyncSession, signer, user_id: str) -> list[Wallet]:
    """Create whichever main wallets the account is missing. Returns the wallets created (maybe none).

    Doesn't commit. A signer failure is raised to the caller, after anything already created was added."""
    have = (await db.execute(select(Funder.network).where(
        Funder.user_id == user_id, Funder.is_default.is_(True), Funder.label == MAIN_LABEL))).scalars().all()
    created: list[Wallet] = []

    async def add(network: str, ref) -> None:
        same = (await db.execute(select(Wallet).where(Wallet.user_id == user_id, Wallet.network == network,
                                                      Wallet.address == ref.address))).scalar_one_or_none()
        if same:  # already in the account (e.g. its funder mark was removed): mark it, don't duplicate it
            created.append(same)
            return
        w = Wallet(user_id=user_id, network=network, address=ref.address, kind="managed",
                   signer_ref=ref.signer_ref, label=MAIN_LABEL)
        db.add(w)
        created.append(w)

    if "solana" not in have:
        await add("solana", await signer.create_wallet(user_id, "solana", MAIN_LABEL))
    missing_evm = [n for n in EVM_CHAINS if n not in have]
    if missing_evm:
        # Reuse the account's existing EVM main key when one chain already has it, so the address stays the same.
        existing = (await db.execute(select(Wallet).join(Funder, Funder.wallet_id == Wallet.id).where(
            Funder.user_id == user_id, Funder.label == MAIN_LABEL, Funder.is_default.is_(True),
            Wallet.network.in_(list(EVM_CHAINS))))).scalars().first()
        ref = (WalletRef(signer_ref=existing.signer_ref, address=existing.address) if existing
               else await signer.create_wallet(user_id, missing_evm[0], MAIN_LABEL))
        for n in missing_evm:
            await add(n, ref)
    await db.flush()
    for w in created:
        # A main wallet takes over as the chain's default funder.
        for f in (await db.execute(select(Funder).where(Funder.user_id == user_id, Funder.network == w.network))).scalars():
            f.is_default = False
        db.add(Funder(user_id=user_id, network=w.network, wallet_id=w.id, label=MAIN_LABEL, is_default=True))
    return created
