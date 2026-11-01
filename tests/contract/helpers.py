"""Shared by the contract tests."""
import asyncio
import time
from typing import Any

import zmq
from trex_web_gui.trexapi.ports import fail_fast


class Recorder:
    def __init__(self) -> None:
        """Starts with nothing recorded."""
        self.msgs: list[dict] = []

    def __call__(self, m: dict) -> None:
        """Record one published message; a Recorder is the `publish` callback of a session."""
        self.msgs.append(m)

    def events(self) -> list[str]:
        """The `name` of every recorded message of type "event", in order."""
        return [m["name"] for m in self.msgs if m["type"] == "event"]

    def states(self) -> list[str]:
        """The `state` of every recorded message of type "session", in order."""
        return [m["state"] for m in self.msgs if m["type"] == "session"]


async def wait_for(pred, timeout: float) -> None:
    """Poll `pred` every 0.05 s, awaiting between polls; AssertionError once `timeout` seconds pass without
    it true."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def short_transport_wait(c: Any, timeout_ms: int = 500) -> None:
    """Make the next RPCs of client `c` give up after one attempt of `timeout_ms`. For a request a fake
    refuses by dying on purpose, so it never answers: the vendored client otherwise waits 1 + ZMQ_RETRY_BASE
    attempts (trex_conn.py:198, trex_req_resp_client.py:66) of RCVTIMEO = sync_timeout each, set once at
    connect (trex_req_resp_client.py:413).
    `fail_fast` drops the retries; RCVTIMEO shortens the one attempt."""
    fail_fast(c)
    c.conn.rpc.socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
