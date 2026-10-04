"""Reserve monotonically before remote signing; uncertainty never reuses a nonce.

A failed signing request can leave a gap. Operators must investigate the corresponding job/chain
before repairing a gap; an automated retry must never replace an uncertain signed transaction.
"""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..config import get_settings
from ..db import get_sessionmaker
from ..models import ExecutionNonce, utcnow
from ..signing.base import SigningError


async def reserve(network_id: int, address: str, observed_nonce: int) -> int:
    if not get_settings().database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
        raise SigningError("Durable execution requires PostgreSQL")
    key = f"{network_id}:{address.lower()}"
    async with get_sessionmaker()() as db:
        await db.execute(insert(ExecutionNonce).values(key=key, next_nonce=observed_nonce, updated_at=utcnow())
                         .on_conflict_do_nothing(index_elements=[ExecutionNonce.key]))
        row = (await db.execute(select(ExecutionNonce).where(ExecutionNonce.key == key).with_for_update())).scalar_one()
        nonce = max(observed_nonce, row.next_nonce)
        row.next_nonce, row.updated_at = nonce + 1, utcnow()
        await db.commit()
        return nonce
