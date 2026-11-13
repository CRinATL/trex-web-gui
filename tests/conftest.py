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
from support import (
    FAKE,
    FAKE_DAEMON,
    FAKE_DAEMON_READY,
    FAKE_EMU,
    FAKE_EMU_READY,
    FAKE_READY,
    FIXTURES,
    ROOT,
    SRC,
    pinned_sha,
    spawn_ready,
)


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

    def toggle_drop(self) -> None:
        """Send SIGHUP: the fake starts (or stops) answering its --drop-on-sighup methods as unknown.
        Asynchronous: nothing waits for it."""
        self.proc.send_signal(signal.SIGHUP)

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
        """Spawn the fake on ports it binds itself (0 0) with `flags`, wait for its ready line and read the
        bound ports from it; RuntimeError (the fake killed, its output appended) when the first line is
        anything else."""
        proc, m = spawn_ready([sys.executable, "-I", "-B", str(FAKE), str(FIXTURES), "0", "0", *flags],
                              FAKE_READY, "fake")
        rpc, pub = int(m[1]), int(m[2])
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
class FakeEmu:
    """A running tests/fake/fake_emu.py: its host, bound port and process."""
    host: str
    port: int
    proc: subprocess.Popen[str]
    log: Path
    shutdown_expected: bool = False     # a test sent shutdown on purpose: teardown skips the check
    _tail: str = field(default="", repr=False)

    def requests(self) -> list[dict[str, Any]]:
        """Every request the fake has received so far, in order, as {"method", "params"} (no api_h)."""
        text = self.log.read_text() if self.log.exists() else ""
        return [json.loads(line) for line in text.splitlines()]

    def restart(self) -> None:
        """Send SIGUSR2: the fake applies a restart before its next message (new api_h, empty state).
        Asynchronous: nothing waits for it."""
        self.proc.send_signal(signal.SIGUSR2)

    def stop(self) -> None:
        """SIGTERM the fake (SIGKILL after 5 s) and keep its output for assert_clean(); does nothing once it
        has exited."""
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self._tail = self.proc.communicate(timeout=5)[0]
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self._tail = self.proc.communicate()[0]

    def assert_clean(self) -> None:
        """AssertionError when the fake printed `FORBIDDEN shutdown` (a client sent `shutdown`, ruling R12);
        reads what stop() kept, so call it after stop()."""
        assert "[fake-emu] FORBIDDEN shutdown" not in self._tail, "a client sent shutdown to the fake EMU"


@pytest.fixture
def fake_emu(tmp_path: Path) -> Iterator[Callable[..., FakeEmu]]:
    """A factory that starts a fake_emu.py process with `flags` (--hang METHOD, --huge-plain METHOD, ...) on a
    port it binds itself (0), read from its ready line, logging its requests under tmp_path; every fake it
    started is stopped at teardown."""
    started: list[FakeEmu] = []

    def start(*flags: str) -> FakeEmu:
        """Spawn the fake; RuntimeError (the fake killed, its output appended) when its first line is not
        the ready line."""
        log = tmp_path / f"fake-emu-{len(started)}.jsonl"
        proc, m = spawn_ready([sys.executable, "-I", "-B", str(FAKE_EMU), "0", "--log", str(log), *flags],
                              FAKE_EMU_READY, "fake emu")
        f = FakeEmu("127.0.0.1", int(m[1]), proc, log)
        started.append(f)
        return f

    yield start
    for f in started:
        f.stop()
    for f in started:
        if not f.shutdown_expected:
            f.assert_clean()


@dataclass
class FakeDaemon:
    """A running tests/fake/fake_daemon.py: its port, its process and every stdout line it has printed."""
    host: str
    port: int
    proc: subprocess.Popen[str]
    lines: list[str] = field(default_factory=list, repr=False)
    reader: threading.Thread | None = field(default=None, repr=False)

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
        """SIGTERM the fake (SIGKILL after 5 s) if it is still running, and wait for the reader to close the
        pipe, so `lines` is complete and no unclosed file is left behind."""
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.reader is not None:
            self.reader.join(timeout=5)


@pytest.fixture
def fake_daemon() -> Iterator[Callable[..., FakeDaemon]]:
    """A factory that starts a fake_daemon.py process with `flags` (--foreign, --hang METHOD, ...); every
    fake it started is stopped at teardown."""
    started: list[FakeDaemon] = []

    def start(*flags: str) -> FakeDaemon:
        """Spawn the fake on a port it binds itself (0) and read the port from its ready line; RuntimeError
        (the fake killed, its whole output appended) when the first line is anything else. A thread keeps
        collecting its output into `lines`."""
        proc, m = spawn_ready([sys.executable, "-I", "-B", str(FAKE_DAEMON), "0", *flags], FAKE_DAEMON_READY,
                              "fake daemon")
        d = FakeDaemon("127.0.0.1", int(m[1]), proc, [m[0]])

        def collect() -> None:
            """Append each further stdout line to d.lines until the pipe ends, then close it."""
            assert proc.stdout is not None
            with proc.stdout:
                for ln in proc.stdout:
                    d.lines.append(ln.rstrip("\n"))
        d.reader = threading.Thread(target=collect, daemon=True)
        d.reader.start()
        started.append(d)
        return d

    yield start
    for d in started:
        d.stop()
