"""Bounded authentication work with deterministic blocking stand-ins, no real accounts."""
import asyncio
import threading
import time

import pytest
from fastapi import HTTPException
from app.services.passwords import _run


async def test_cancellation_does_not_free_running_password_slots():
    release, started = threading.Event(), threading.Event()
    count, lock = [0], threading.Lock()
    def work():
        with lock:
            count[0] += 1
            if count[0] == 2: started.set()
        release.wait(3)
        return True
    tasks = [asyncio.create_task(_run(work)) for _ in range(2)]
    try:
        while not started.is_set(): await asyncio.sleep(.005)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError): await tasks[0]
        with pytest.raises(HTTPException) as exc: await _run(lambda: True)
        assert exc.value.status_code == 503 and exc.value.headers["Retry-After"] == "2"
        assert count[0] == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(.02)
    assert await _run(lambda: "capacity recovered") == "capacity recovered"


async def test_bounded_password_work_keeps_event_loop_responsive():
    def expensive(): time.sleep(.12)
    # Controlled before/after experiment: identical work on-loop versus bounded off-loop.
    async def measure(off_loop):
        start = time.perf_counter()
        tick = asyncio.create_task(asyncio.sleep(.01))
        if off_loop:
            work = asyncio.create_task(_run(expensive))
            await tick
            delay = time.perf_counter() - start
            await work
        else:
            expensive()
            await tick
            delay = time.perf_counter() - start
        return delay
    before, after = await measure(False), await measure(True)
    print(f"controlled event-loop probe: before={before:.4f}s after={after:.4f}s")
    assert before >= .12 and after < .1
