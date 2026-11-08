"""A TCP reachability probe shared by the Control session's reconnect and the daemon session's stop check."""
import asyncio
import contextlib


async def port_accepts(host: str, port: int, timeout_s: float) -> bool:
    """Whether a TCP connect to host:port succeeds within `timeout_s`; the socket is closed, and its close
    awaited within the same bound (an error or timeout of the close is not the probe's answer)."""
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
    except (OSError, TimeoutError):
        return False
    w.close()
    with contextlib.suppress(OSError, TimeoutError):
        await asyncio.wait_for(w.wait_closed(), timeout_s)
    return True
