"""Bounded password work, including cancelled requests and nonexistent accounts."""
import asyncio
from weakref import WeakKeyDictionary

from fastapi import HTTPException

from ..security import hash_password, verify_password

_limits = WeakKeyDictionary()


async def _run(fn, *args):
    loop = asyncio.get_running_loop()
    limit = _limits.setdefault(loop, asyncio.Semaphore(2))
    try:
        await asyncio.wait_for(limit.acquire(), timeout=0.25)
    except TimeoutError:
        raise HTTPException(503, "Authentication is busy; please retry shortly", headers={"Retry-After": "2"})
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    # A disconnected client must not release a slot while its Argon2 thread is still working.
    task.add_done_callback(lambda _: limit.release())
    return await asyncio.shield(task)


async def hash_async(password: str) -> str:
    return await _run(hash_password, password)


async def verify_async(password: str, hashed: str | None) -> bool:
    if hashed is None:
        await hash_async(password)  # same expensive algorithm for an unknown account
        return False
    return await _run(verify_password, password, hashed)
