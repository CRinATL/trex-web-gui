import contextlib
import json
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from support import FAKE, FAKE_DAEMON, FIXTURES, ROOT, SRC, free_port, free_port_pair, pinned_sha


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test that rebuilds the vendor tree, so `make test` can skip them when nothing under
    the vendor paths changed (Makefile VENDOR_TESTS). Golden tests skip when the source clone is missing."""
    for item in items:
        if item.get_closest_marker("golden") and not SRC.exists():
            item.add_marker(pytest.mark.skip(reason=f"golden needs the source clone at {SRC}"))
        if "build_vendor" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.vendor_build)


@pytest.fixture
def build_vendor(tmp_path: Path) -> Callable[[list[str]], Path]:
    """A builder `build(skip)`; skips the test when the trex-core source clone is missing."""
    if not SRC.exists():
        pytest.skip(f"negative controls need the source clone at {SRC}")

    def build(skip: list[str]) -> Path:
        """Run backend/scripts/vendor_trex.py at the pinned SHA into tmp_path with each patch named in `skip`
        skipped; return the output dir. CalledProcessError when the build fails."""
        out = tmp_path / ("vendor-skip-" + "-".join(skip))
        args = [sys.executable, "-I", "-B", str(ROOT / "backend/scripts/vendor_trex.py"), "--src", str(SRC),
                "--sha", pinned_sha(), "--out", str(out), "--patches", str(ROOT / "backend/vendor/patches")]
        for s in skip:
            args += ["--skip-patch", s]
        subprocess.run(args, check=True, capture_output=True)
        return out
    return build


@dataclass
class FakeTrex:
    host: str
    rpc_port: int
    pub_port: int
    proc: subprocess.Popen[str]
    _tail: str = field(default="", repr=False)

    def stop(self) -> None:
        """SIGCONT then SIGTERM the fake (SIGKILL after 5 s) and keep its output for served(); does nothing
        once it has exited."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGCONT)  # in case a test SIGSTOPped it
            self.proc.terminate()
            try:
                self._tail = self.proc.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self._tail = self.proc.communicate()[0]

    def arm_seq_gap(self) -> None:
        """Send SIGUSR1: the fake serves seq 5 then 7 to every subscriber until it restarts."""
        # every subscriber's next polls carry seq 5, then 7, until the fake restarts
        self.proc.send_signal(signal.SIGUSR1)

    def restart(self) -> None:
        """Send SIGUSR2: the fake applies a restart before its next message (new api_h, ports and captures
        reset, gap disarmed). Asynchronous: nothing waits for it."""
        self.proc.send_signal(signal.SIGUSR2)   # new api_h, ports reset (fake_trex.py)

    def served(self) -> dict[str, int]:
        """The method -> request count the fake printed when it was terminated; {} until stop() has read that
        line."""
        lines = [ln for ln in self._tail.splitlines() if ln.startswith("[fake] served ")]
        return json.loads(lines[-1][len("[fake] served "):]) if lines else {}


@pytest.fixture
def fake_trex() -> Iterator[Callable[..., FakeTrex]]:
    """A factory that starts a fake_trex.py process; every fake it started is stopped at teardown."""
    started: list[FakeTrex] = []

    def start(*flags: str) -> FakeTrex:
        """Spawn the fake on a free port pair with `flags` and wait for its ready line; RuntimeError (the fake
        killed) when the first line is anything else."""
        rpc, pub = free_port_pair()
        proc = subprocess.Popen([sys.executable, "-I", "-B", str(FAKE), str(FIXTURES), str(rpc), str(pub),
                                 *flags],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready = proc.stdout.readline() if proc.stdout else ""
        if not ready.startswith("[fake] ready"):
            proc.kill()
            raise RuntimeError(f"HARNESS ERROR: fake did not start: {ready!r}")
        f = FakeTrex("127.0.0.1", rpc, pub, proc)
        started.append(f)
        return f

    yield start
    for f in started:
        f.stop()


@pytest.fixture
def foreign_client(fake_trex) -> Iterator[Callable[..., Any]]:
    """A factory for second vendored STLClients against a fake, playing trex-console. Disconnected at
    teardown, before the fakes stop (a leaked client hangs pytest at exit, P1 Task 5)."""
    made: list[Any] = []

    def make(f: FakeTrex, user: str = "console-b") -> Any:
        """Load the vendored client, connect a new STLClient as `user` to fake `f` and keep it for
        teardown."""
        from trex_web_gui.trexapi import loader
        loader.load()
        from trex.stl.api import STLClient
        c = STLClient(username=user, server=f.host, sync_port=f.rpc_port, async_port=f.pub_port,
                      verbose_level="none", sync_timeout=2, async_timeout=2)
        c.connect()
        made.append(c)
        return c

    yield make
    for c in made:
        with contextlib.suppress(Exception):    # teardown: the fake may already be restarted or gone
            c.disconnect(stop_traffic=False, release_ports=False)


@dataclass
class FakeDaemon:
    """A running tests/fake/fake_daemon.py: its port, its process and every stdout line it has printed."""
    host: str
    port: int
    proc: subprocess.Popen[str]
    lines: list[str] = field(default_factory=list, repr=False)

    def methods(self) -> list[str]:
        """The JSON-RPC methods the fake has received so far, in order."""
        prefix = "[fake-daemon] method "
        return [ln[len(prefix):] for ln in list(self.lines) if ln.startswith(prefix)]

    def wait_line(self, line: str, timeout_s: float = 10.0) -> None:
        """Block until the fake has printed exactly `line` (`hang is_running`, say); AssertionError after
        timeout_s."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if line in list(self.lines):
                return
            time.sleep(0.02)
        raise AssertionError(f"fake daemon never printed {line!r}; it printed {self.lines!r}")

    def stop(self) -> None:
        """SIGTERM the fake (SIGKILL after 5 s); does nothing once it has exited."""
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


@pytest.fixture
def fake_daemon() -> Iterator[Callable[..., FakeDaemon]]:
    """A factory that starts a fake_daemon.py process with `flags` (--foreign, --hang METHOD, ...); every
    fake it started is stopped at teardown."""
    started: list[FakeDaemon] = []

    def start(*flags: str) -> FakeDaemon:
        """Spawn the fake on a free port and wait for its ready line; RuntimeError (the fake killed) when
        the first line is anything else. A thread keeps collecting its output into `lines`."""
        port = free_port()
        proc = subprocess.Popen([sys.executable, "-I", "-B", str(FAKE_DAEMON), str(port), *flags],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready = proc.stdout.readline() if proc.stdout else ""
        if not ready.startswith("[fake-daemon] ready"):
            proc.kill()
            raise RuntimeError(f"HARNESS ERROR: fake daemon did not start: {ready!r}")
        d = FakeDaemon("127.0.0.1", port, proc, [ready.rstrip("\n")])

        def collect() -> None:
            """Append each further stdout line to d.lines until the pipe closes."""
            assert proc.stdout is not None
            for ln in proc.stdout:
                d.lines.append(ln.rstrip("\n"))
        threading.Thread(target=collect, daemon=True).start()
        started.append(d)
        return d

    yield start
    for d in started:
        d.stop()
