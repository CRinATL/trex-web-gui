"""Shared by the contract tests."""
import asyncio
import time


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
