import asyncio

import pytest


class _Closer:
    """A stand-in session: records its closes, and raises RuntimeError on the first one if asked."""

    def __init__(self, fail_first: bool) -> None:
        """A stand-in that has not been closed."""
        self.fail_first = fail_first
        self.closes = 0

    async def close(self) -> None:
        """Count the close, raising RuntimeError on the first when fail_first is set."""
        self.closes += 1
        if self.fail_first and self.closes == 1:
            raise RuntimeError("close failed")


def test_run_closes_every_session_when_one_close_raises(new_session) -> None:
    """A close() that raises does not skip the sessions made before it: all are closed, then it propagates.
    Sessions close last-made first, so the second-made one raises and the first-made must still be closed."""
    first, second = _Closer(fail_first=False), _Closer(fail_first=True)
    new_session.made.extend([first, second])
    with pytest.raises(RuntimeError):
        new_session.run(asyncio.sleep(0))
    assert first.closes == 1 and second.closes == 1
