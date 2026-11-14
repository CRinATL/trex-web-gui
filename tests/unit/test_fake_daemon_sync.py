"""FakeDaemon._sync: a fake that has exited must not lose lines its reader has yet to append."""
from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# `from conftest import FakeDaemon` resolves to tests/contract/conftest.py when the whole suite runs (the
# module name `conftest` is taken by the first conftest imported), so load tests/conftest.py by its path.
_ROOT_CONFTEST = Path(__file__).parents[1] / "conftest.py"
_spec = importlib.util.spec_from_file_location("tests_root_conftest", _ROOT_CONFTEST)
assert _spec is not None and _spec.loader is not None
_root_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root_conftest)
FakeDaemon = _root_conftest.FakeDaemon


def test_methods_after_the_fake_exited_include_every_line_it_printed() -> None:
    """The fake is synthetic by design (a child printing two method lines; no server text is involved)."""
    code = ("print('[fake-daemon] method a', flush=True); print('[fake-daemon] method b', flush=True)")
    proc = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    d = FakeDaemon("127.0.0.1", port, proc)

    def collect() -> None:
        """Append each line after a lag, the way a slow reader thread would."""
        with proc.stdout:
            for ln in proc.stdout:
                time.sleep(0.2)
                d.lines.append(ln.rstrip("\n"))

    d.reader = threading.Thread(target=collect, daemon=True)
    d.reader.start()
    proc.wait()
    assert d.methods() == ["a", "b"]
    d.stop()


def test_a_fake_that_exits_during_the_marker_request_is_joined_too() -> None:
    """`poll()` says running at entry and exited by the time the connection fails (a fake killed but not yet
    waited on): the except branch joins the reader. Synthetic like the test above: a child and a poll shim."""
    code = "print('[fake-daemon] method a', flush=True)"
    proc = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE, text=True)
    real_poll = proc.poll
    calls: list[int] = []

    def poll_once_running() -> int | None:
        """None on the first call (the entry check), the real answer after."""
        calls.append(1)
        return None if len(calls) == 1 else real_poll()

    proc.poll = poll_once_running  # type: ignore[method-assign]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    d = FakeDaemon("127.0.0.1", port, proc)

    def collect() -> None:
        """Append each line after a lag, the way a slow reader thread would."""
        with proc.stdout:
            for ln in proc.stdout:
                time.sleep(0.2)
                d.lines.append(ln.rstrip("\n"))

    d.reader = threading.Thread(target=collect, daemon=True)
    d.reader.start()
    proc.wait()
    assert d.methods() == ["a"]
    d.stop()
