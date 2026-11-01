"""Plain helpers shared by tests. They live here, not in conftest.py, because tests/contract/conftest.py
shares the module name `conftest`, so `from conftest import ...` is ambiguous."""
import contextlib
import fcntl
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "backend" / "trex_web_gui" / "_vendor"
SRC = Path(os.environ.get("TREX_SRC", os.path.expanduser("~/Projects/trex-rework/trex-core")))  # as Makefile
PIN_FILE = ROOT / "backend" / "vendor" / "TREX_PIN"

# loaded by path, so vm/ stays off sys.path
_spec = importlib.util.spec_from_file_location("guestenv", ROOT / "vm" / "guestenv.py")
assert _spec is not None and _spec.loader is not None
guestenv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guestenv)

def warnings_in(root: Path) -> int:
    """How many SyntaxWarnings compiling every .py under root raises (compiled only, nothing imported)."""
    n = 0
    for f in root.rglob("*.py"):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            compile(f.read_bytes(), str(f), "exec")
        n += sum(issubclass(x.category, SyntaxWarning) for x in w)
    return n

def real_ports() -> tuple[int, int]:
    """The selected guest's (sync, async) ports, or a test failure naming the missing eval."""
    try:
        return guestenv.ports()
    except guestenv.GuestEnvError as e:
        pytest.fail(str(e))


def real_vm_dir() -> Path:
    """The selected guest's VM dir, or a test failure naming the missing eval."""
    try:
        return guestenv.vm_dir()
    except guestenv.GuestEnvError as e:
        pytest.fail(str(e))


def pinned_sha() -> str:
    """The upstream SHA as recorded by `make vendor` (canon:VER-6: one literal, in the Makefile)."""
    return str(json.loads(PIN_FILE.read_text())["upstream_sha"])


def run_vendor_python(vendor: Path, code: str, *, system_libs: bool = True,
                      python: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run `code` in a fresh -I -B interpreter with `vendor` importable. Fresh process because
    trex/__init__ mutates sys.path and sys.modules on import. A fresh pycache_prefix forces a real
    compile: -B only stops writing .pyc, it still reads them, which would hide SyntaxWarnings."""
    import tempfile
    env = {"PATH": os.environ["PATH"], "TREX_EXT_LIBS": str(vendor / "external_libs"),
           "HOME": os.environ.get("HOME", "/tmp")}
    if system_libs:
        env["TREX_SYSTEM_LIBS"] = "zmq,yaml"
    prelude = f"import sys; sys.path.insert(0, {str(vendor)!r})\n"
    with tempfile.TemporaryDirectory() as cache:
        return subprocess.run([python or sys.executable, "-I", "-B", "-X", f"pycache_prefix={cache}",
                               "-c", prelude + code], check=False, capture_output=True, text=True, env=env,
                               timeout=60)


FAKE = ROOT / "tests" / "fake" / "fake_trex.py"
FAKE_DAEMON = ROOT / "tests" / "fake" / "fake_daemon.py"
FIXTURES = ROOT / "tests" / "fixtures"


def free_port_pair() -> tuple[int, int]:
    """Two free loopback ports (rpc, pub). Racy by nature: another process can take a port before the caller
    binds it. The fake is started on port 0 instead, which it binds itself."""
    socks = [socket.socket() for _ in range(2)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = tuple(s.getsockname()[1] for s in socks)
    for s in socks:
        s.close()
    return ports[0], ports[1]


def free_port() -> int:
    """One free loopback port. Racy by nature; the fake daemon fails loudly if it is taken."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


DEMO_PORTS_LOCK = Path(tempfile.gettempdir()) / f"trex-web-gui-demo-ports-{os.getuid()}.lock"
DEMO_PORTS_WAIT_S = 45.0  # below pytest.ini's `timeout = 60`


@contextlib.contextmanager
def demo_ports_lock(path: Path = DEMO_PORTS_LOCK, wait_s: float = DEMO_PORTS_WAIT_S) -> Iterator[None]:
    """Holds an exclusive flock on path for the with body, so tests that bind or probe the guest VM's
    ports run one at a time across xdist workers and across pytest sessions of one uid.

    Guards resolute's six ports (tests/unit/test_vm_build.py RESOLUTE_PORTS) and the GUI ports 8765, 8865
    and 8965. A lock and not `xdist_group`: that needs `--dist loadgroup`, which `make test-py` does not
    pass (its `--dist` is the Makefile's).

    The acquire polls LOCK_NB every 0.05 s for at most wait_s, then raises TimeoutError naming path, so a
    long or stopped holder is not an anonymous pytest-timeout. Never nest it: the lock is not re-entrant.
    A holder that dies frees it, because closing the descriptor releases an flock.

    The file is opened O_CREAT | O_RDWR | O_NOFOLLOW, mode 0o600, never O_TRUNC: a symlink there is
    refused and nothing is truncated. A file pre-created by another uid makes every locked test error with
    a PermissionError naming the path; that is the accepted risk, and the remedy is deleting the file."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{path} held for over {wait_s} s by another test or session; never nest this lock"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


TEST_TOKEN = "trex-web-gui-test-token-0123456789ab"       # 36 characters; TREX_WEB_GUI_TOKEN needs 16


def anon(app: FastAPI, **kw: Any) -> TestClient:
    """A client that is NOT logged in: no cookie. The negative auth tests need exactly this. `base_url`
    pins the Host header to an allowed one; "testserver" is never allowed in production. Returned
    un-entered: the caller writes `with anon(app) as c:` so the lifespan runs once."""
    return TestClient(app, base_url="http://127.0.0.1", **kw)


def authed(app: FastAPI, **kw: Any) -> TestClient:
    """anon(app), then the login GET, so the cookie jar holds this backend's cookie. The GET happens before
    the lifespan, as a bare TestClient request outside `with` does. Also returned un-entered."""
    c = anon(app, **kw)
    r = c.get("/api/auth/login?token=" + app.state.token, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c
