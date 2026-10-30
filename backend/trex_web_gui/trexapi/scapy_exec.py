"""The one thread Scapy_service runs on. Scapy_service swaps sys.stdout while it builds its tables and
dissects (scapy_service.py:513-516, 527-530) and keeps instance state, so its calls never interleave with
each other. This does not make Scapy single-threaded process-wide: the vendored client builds Scapy packets
on the control executor too. `warm_up()` constructs the singleton in the app lifespan, before any session
connects, so that stdout swap cannot capture another thread's output."""
import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from . import decode

log = logging.getLogger(__name__)
_lock = threading.Lock()
_executor: ThreadPoolExecutor | None = None


def _get() -> ThreadPoolExecutor:
    """The one-thread executor, created under the lock on first use or after shutdown()."""
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scapy")
        return _executor


def run(fn: Callable[..., Any], *args: Any) -> Future[Any]:
    """Submit fn to the scapy thread; its Future."""
    return _get().submit(fn, *args)


async def arun(fn: Callable[..., Any], *args: Any) -> Any:
    """fn's result, run on the scapy thread and awaited without blocking the loop."""
    return await asyncio.wrap_future(run(fn, *args))


def _construct_timed() -> None:
    """Build the Scapy_service singleton when absent, logging how long it took; runs on the scapy thread."""
    if decode._service is None:
        t0 = time.monotonic()
        decode.service()
        log.info("Scapy_service constructed in %.3f s", time.monotonic() - t0)


def warm_up() -> None:
    """Construct the singleton on the executor and wait. A second call, or one after shutdown(), keeps
    the singleton (it holds no thread) and only recreates the executor."""
    run(_construct_timed).result()


def shutdown() -> None:
    """Drop the executor, blocking until its queued jobs finish; the next run() creates a new one.
    No-op when none."""
    global _executor
    with _lock:
        ex, _executor = _executor, None
    if ex is not None:
        ex.shutdown(wait=True)
