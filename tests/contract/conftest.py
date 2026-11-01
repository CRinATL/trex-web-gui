import asyncio
import contextlib
from collections.abc import Callable, Coroutine, Iterator
from typing import Any

import pytest
from trex_web_gui.sessions import TrexSession


class SessionFactory:
    """Makes TrexSessions and runs the test body, closing every session inside the loop it ran in.
    Closing in a later asyncio.run lets a late event schedule _refresh_soon on a dead loop
    ("Task was destroyed but it is pending"). Use `new_session.run(run())` instead of `asyncio.run(run())`."""

    def __init__(self) -> None:
        """A factory that has made no session yet."""
        self.made: list[TrexSession] = []

    def __call__(self, publish: Callable[[dict], None], **kw: Any) -> TrexSession:
        """A new TrexSession(publish, **kw), remembered so run() or close_leftovers() closes it."""
        s = TrexSession(publish, **kw)
        self.made.append(s)
        return s

    def run(self, body: Coroutine[Any, Any, None]) -> None:
        """Run `body` under asyncio.run, then close every session made so far in that same loop, also when
        `body` raised. Every close runs, last made first; a close() that raises propagates after the rest."""
        # wrapped(): await the body, then close every made session in the same loop, even if it raised
        async def wrapped() -> None:
            try:
                await body
            finally:
                async with contextlib.AsyncExitStack() as stack:   # runs every close even if one raises
                    for s in self.made:
                        stack.push_async_callback(s.close)  # idempotent: a test's own close() is fine
        asyncio.run(wrapped())

    def close_leftovers(self) -> None:
        """The safety net: sessions made but never run through run(). Should never be needed."""
        with contextlib.ExitStack() as stack:            # runs every close even if one raises, then re-raises
            for s in self.made:
                stack.callback(asyncio.run, s.close())


@pytest.fixture
def new_session(fake_trex) -> Iterator[SessionFactory]:
    """A SessionFactory, with close_leftovers() run at teardown. A leaked client hangs pytest at exit: its ZMQ
    context's __del__ blocks in ctx.term() (trex_req_resp_client.py:440). Depends on fake_trex so the sessions
    close before the fakes stop."""
    factory = SessionFactory()
    yield factory
    factory.close_leftovers()
