"""Plain helpers shared by tests. They live here, not in conftest.py, because tests/contract/conftest.py
shares the module name `conftest`, so `from conftest import ...` is ambiguous."""
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import warnings
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from trex_web_gui.trexapi.errors import ServerUnreachable
from trex_web_gui.trexapi.rawrpc import RawRpcError

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "backend" / "trex_web_gui" / "_vendor"
SRC = Path(os.environ.get("TREX_SRC", os.path.expanduser("~/Projects/trex-rework/trex-core")))  # as Makefile
PIN_FILE = ROOT / "backend" / "vendor" / "TREX_PIN"

# loaded by path, so vm/ stays off sys.path
_spec = importlib.util.spec_from_file_location("guestenv", ROOT / "vm" / "guestenv.py")
assert _spec is not None and _spec.loader is not None
guestenv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guestenv)


def load_script(name: str) -> Any:
    """scripts/<name>.py loaded by path (scripts/ is not a package), fresh on each call."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
FAKE_EMU = ROOT / "tests" / "fake" / "fake_emu.py"
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
    """One free loopback port. Racy by nature: use it only for a port that nothing should listen on, or one
    a process outside the tests must be given. The fakes bind port 0 themselves (spawn_ready)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


FAKE_READY = re.compile(r"\[fake\] ready rpc=(\d+) pub=(\d+) ")       # fake_trex.py's first line
FAKE_DAEMON_READY = re.compile(r"\[fake-daemon\] ready port=(\d+)$")   # fake_daemon.py's first line
FAKE_EMU_READY = re.compile(r"\[fake-emu\] ready port=(\d+)$")           # fake_emu.py's first line


def spawn_ready(argv: Sequence[str], ready: re.Pattern[str],
                what: str) -> tuple[subprocess.Popen[str], re.Match[str]]:
    """Start `argv` with its output piped and match its first line against `ready`; return the process and the
    match (the caller stops the process). A first line that does not match kills it and raises RuntimeError
    with everything it printed, so a start failure shows its whole traceback (docs-cleanup-81). On any failure
    the child is killed if still alive and its stdout closed before the exception propagates
    (fake-daemon-leftovers-1); a decode error from the first line propagates as itself."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        first = proc.stdout.readline() if proc.stdout else ""
        m = ready.match(first.rstrip("\n"))
        if not m:
            try:                                 # a failed start is usually exiting: let it finish writing
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            # Read through the text wrapper: readline() may already hold the rest in its buffer, which
            # communicate() bypasses (it reads the raw pipe), so a failure used to show only its first line.
            rest = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"HARNESS ERROR: {what} did not start: {first + rest!r}")
        return proc, m
    except BaseException:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        finally:                                 # even when the wait above raises
            if proc.stdout is not None:
                proc.stdout.close()
        raise


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


LISTINGS = ("ctx_iter", "ctx_client_iter")


def _as_compared(method: str, outcome: dict[str, Any]) -> Any:
    """What `e3_mismatches` compares of an outcome: a listing as `empty`, `stopped` and the set of its items
    (one `json.dumps(item, sort_keys=True)` each), anything else as it is."""
    res = outcome.get("result")
    if method in LISTINGS and isinstance(res, dict):
        data = res.get("data") or []
        return {"empty": res.get("empty"), "stopped": res.get("stopped"),
                "data": {json.dumps(item, sort_keys=True) for item in data}}
    return outcome


def e3_mismatches(call: Callable[[str, dict[str, Any]], Any],
                  steps: list[dict[str, Any]]) -> tuple[list[str], int]:
    """Replay the recorded E3 `steps` through `call` and list what differs from each step's `outcome`.

    A RawRpcError is read as `{"error": {"code", "text"}}`. A listing matches on `empty`, `stopped` and its
    items as a set; every other result must be equal. The replay stops at the first `unanswered` step and
    sends neither it nor a later one, because every later step was recorded against a restarted server.
    ServerUnreachable propagates, re-raised with the name of the step that got no answer. Returns the
    mismatches (`"<name>: expected <x>, got <y>"`) and the number of steps not compared."""
    mismatches: list[str] = []
    for i, step in enumerate(steps):
        name, method, expected = step["name"], step["method"], step["outcome"]
        if "unanswered" in expected:
            return mismatches, len(steps) - i
        try:
            got: dict[str, Any] = {"result": call(method, step["params"])}
        except RawRpcError as e:
            got = {"error": {"code": e.code, "text": e.message}}
        except ServerUnreachable as e:
            raise ServerUnreachable(f"{name}: {e}") from e
        if _as_compared(method, got) != _as_compared(method, expected):
            mismatches.append(f"{name}: expected {expected}, got {got}")
    return mismatches, 0


# An IP packet with options as a user's capture had them (01 | 94 04 00 00 | 00 | 00 00: NOP, Router Alert,
# EOL, two zeros); the addresses are test ones. The option bytes are ROUTER_ALERT_FRAME[34:42].
ROUTER_ALERT_FRAME = bytes.fromhex(
    "ffffffffffff0200000000010800470000240001000040013343100000013000000101940400000000000800f7ff00000000")
