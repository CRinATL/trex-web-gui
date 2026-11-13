"""The fake daemon's own port binding (docs-cleanup-81): port 0 binds a free port itself and reports it, so no
test picks a port that another xdist worker can take before the fake binds it."""
import http.client
import subprocess
import sys
from pathlib import Path

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
        proc.communicate(timeout=5)
    assert list(tmp_path.glob("fake-daemon-*")) == []


def test_a_fake_that_does_not_start_shows_its_whole_output() -> None:
    """A start failure carries everything the process printed, not only its first line ('Traceback…')."""
    argv = [sys.executable, "-I", "-B", "-c", "import sys; print('Traceback (most recent call last):'); "
            "print('OSError: [Errno 98] Address already in use'); sys.exit(1)"]
    with pytest.raises(RuntimeError, match=r"HARNESS ERROR: thing did not start: .*Address already in use"):
        spawn_ready(argv, FAKE_DAEMON_READY, "thing")
