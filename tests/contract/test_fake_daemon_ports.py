"""The fake daemon's own port binding (docs-cleanup-81): port 0 binds a free port itself and reports it, so no
test picks a port that another xdist worker can take before the fake binds it."""
import codecs
import http.client
import locale
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from support import FAKE_DAEMON, FAKE_DAEMON_READY, spawn_ready


def test_the_fake_daemon_binds_its_own_port_when_given_zero(tmp_path: Path) -> None:
    argv = [sys.executable, "-I", "-B", str(FAKE_DAEMON), "0", "--redirect", "--tmp-root", str(tmp_path)]
    proc, m = spawn_ready(argv, FAKE_DAEMON_READY, "fake daemon")
    try:
        port = int(m[1])
        assert port != 0
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/", body=b"{}")
        r = c.getresponse()
        assert r.status == 302 and r.getheader("Location") == f"http://127.0.0.1:{port}/"   # the bound port
    finally:
        proc.terminate()      # not kill: a SIGKILLed fake leaves its temp dir (fake-daemon-register-2-5)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)


def test_a_sigterm_removes_the_fakes_temp_dir(tmp_path: Path) -> None:
    """A fake sent SIGTERM exits normally and leaves no temp dir (its handler makes the exit a normal one)."""
    argv = [sys.executable, "-I", "-B", str(FAKE_DAEMON), "0", "--tmp-root", str(tmp_path)]
    proc, _ = spawn_ready(argv, FAKE_DAEMON_READY, "fake daemon")
    try:
        assert len(list(tmp_path.glob("fake-daemon-*"))) == 1
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)
    assert list(tmp_path.glob("fake-daemon-*")) == []


def test_a_fake_that_does_not_start_shows_its_whole_output() -> None:
    """A start failure carries everything the process printed, not only its first line ('Traceback…')."""
    argv = [sys.executable, "-I", "-B", "-c", "import sys; print('Traceback (most recent call last):'); "
            "print('OSError: [Errno 98] Address already in use'); sys.exit(1)"]
    with pytest.raises(RuntimeError, match=r"HARNESS ERROR: thing did not start: .*Address already in use"):
        spawn_ready(argv, FAKE_DAEMON_READY, "thing")


@pytest.fixture
def popens(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[str]]:
    """Every Popen made during the test, so a test can inspect the child of a start that raised."""
    made: list[subprocess.Popen[str]] = []

    class Recorded(subprocess.Popen):  # type: ignore[type-arg]
        def __init__(self, *a: Any, **k: Any) -> None:
            """Popen, remembered in `made`."""
            super().__init__(*a, **k)
            made.append(self)
    monkeypatch.setattr(subprocess, "Popen", Recorded)
    return made


@pytest.mark.parametrize(("code", "rc"), [
    ("print('Traceback (most recent call last):'); print('OSError: boom'); sys.exit(1)", 1),
    ("sys.exit(3)", 3),
    ("print('hello', flush=True); time.sleep(30)", -signal.SIGKILL),
], ids=["exits-with-output", "exits-silently", "keeps-running"])
def test_a_failed_start_leaves_no_child_and_closes_its_pipe(popens, code: str, rc: int) -> None:
    """fake-daemon-leftovers-1: whatever way the start fails, the child is gone and its stdout is closed
    (an open one is a ResourceWarning: unclosed file)."""
    with pytest.raises(RuntimeError, match="HARNESS ERROR: thing did not start"):
        spawn_ready([sys.executable, "-I", "-B", "-c", f"import sys, time; {code}"], FAKE_DAEMON_READY,
                    "thing")
    [proc] = popens
    assert proc.returncode == rc
    assert proc.stdout is not None and proc.stdout.closed


def test_an_undecodable_first_line_kills_the_child_and_closes_its_pipe(popens) -> None:
    """readline() raising (bytes the locale encoding cannot decode) still kills the live child and closes the
    pipe; the decode error itself propagates."""
    enc = codecs.lookup(locale.getpreferredencoding(False)).name
    if enc not in ("utf-8", "ascii"):
        pytest.skip(f"b'\\xff' decodes under {enc}")
    code = "import sys, time; sys.stdout.buffer.write(b'\\xff\\n'); sys.stdout.flush(); time.sleep(30)"
    with pytest.raises(UnicodeDecodeError):
        spawn_ready([sys.executable, "-I", "-B", "-c", code], FAKE_DAEMON_READY, "thing")
    [proc] = popens
    assert proc.returncode == -signal.SIGKILL
    assert proc.stdout is not None and proc.stdout.closed
