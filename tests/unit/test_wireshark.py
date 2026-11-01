"""The Wireshark pipe (Task 9): the pre-flight and WiresharkPipe against stub "wireshark" shell scripts in
tmp_path. A stub is called as `<stub> -k -i <fifo>`, so the fifo is $3. Every test kills its stub (the `pipes`
fixture) and checks the pipe's temp dir is gone; no test waits longer than a few seconds."""
import asyncio
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui import trexapi
from trex_web_gui.sessions import wireshark
from trex_web_gui.sessions.wireshark import WiresharkPipe

trexapi.load()      # Scapy is importable only after the vendored tree is on the path
FRAME = bytes(range(60))


@pytest.fixture
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """A new event loop, never run by the fixture; closed at teardown."""
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture
def pipes(loop: asyncio.AbstractEventLoop) -> Iterator[tuple[Callable[[], WiresharkPipe], list[int]]]:
    """A factory of pipes whose on_change counts calls; each pipe is killed and its temp dir checked at
    teardown."""
    made: list[WiresharkPipe] = []
    changes: list[int] = []

    # Builds a WiresharkPipe on the fixture loop (on_change appends to changes) and records it for teardown.
    def make() -> WiresharkPipe:
        p = WiresharkPipe(lambda: changes.append(1), loop)
        made.append(p)
        return p
    yield make, changes
    for p in made:
        fifo = p.fifo
        p.kill()
        if p.pid is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(p.pid, 0)          # killed and reaped: no zombie left behind
        if fifo is not None:
            assert not fifo.parent.exists()


def stub(tmp_path: Path, body: str) -> str:
    """Writes an executable /bin/sh script named wireshark with body into tmp_path; returns its path."""
    path = tmp_path / "wireshark"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def wait_until(loop: asyncio.AbstractEventLoop, pred: Callable[[], Any], timeout: float = 5.0) -> None:
    """Poll `pred`, running the loop meanwhile so call_soon_threadsafe callbacks are delivered."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        loop.run_until_complete(asyncio.sleep(0.02))
        if pred():
            return
    raise AssertionError("condition not met in time")


def test_preflight_reasons(tmp_path: Path) -> None:
    exe = stub(tmp_path, "exit 0")
    missing = str(tmp_path / "nowhere" / "wireshark")
    assert wireshark.preflight(missing, {"DISPLAY": ":0"}) == (False, f"not found at {missing}")
    # a bare name is looked up on PATH, so "not found at wireshark" would name no place (RC1-8)
    assert wireshark.preflight("no-such-wireshark-xyz", {"DISPLAY": ":0"}) == (
        False, "'no-such-wireshark-xyz' is not installed on the backend host (not on its PATH)")
    assert wireshark.preflight(exe, {}) == (
        False, "no display on the backend host (DISPLAY/WAYLAND_DISPLAY unset)")
    assert wireshark.preflight(exe, {"DISPLAY": ""}) == (
        False, "no display on the backend host (DISPLAY/WAYLAND_DISPLAY unset)")
    assert wireshark.preflight(exe, {"DISPLAY": ":0"}) == (True, None)
    assert wireshark.preflight(exe, {"WAYLAND_DISPLAY": "wayland-0"}) == (True, None)


def test_pipe_delivers_a_pcap_header_and_frames(tmp_path: Path, pipes: Any, loop: Any) -> None:
    from scapy.utils import rdpcap
    out = tmp_path / "out.pcap"
    make, _ = pipes
    p = make()
    p.start(stub(tmp_path, f'exec cat "$3" > "{out}"'))
    assert (p.running and p.pid is not None and p.fifo is not None
            and p.fifo.parent.name.startswith("trex-web-gui-ws-"))
    p.write(0.5, FRAME, 60)
    p.write(1.25, FRAME[:42], 100)
    p.stop()
    wait_until(loop, lambda: out.exists() and out.stat().st_size == 24 + 2 * 16 + 60 + 42)
    pkts = rdpcap(str(out))
    assert [bytes(x) for x in pkts] == [FRAME, FRAME[:42]]
    assert float(pkts[0].time) == pytest.approx(0.5) and float(pkts[1].time) == pytest.approx(1.25)
    assert pkts[1].wirelen == 100
    assert not p.running and p.error is None


def test_fd_is_blocking_after_open(tmp_path: Path, pipes: Any, loop: Any) -> None:
    out = tmp_path / "out.pcap"
    make, _ = pipes
    p = make()
    p.start(stub(tmp_path, f'exec cat "$3" > "{out}"'))
    # the header is out: the fd is set up
    wait_until(loop, lambda: out.exists() and out.stat().st_size >= 24)
    fifo = str(p.fifo)
    fds = [int(n) for n in os.listdir("/proc/self/fd") if _link(n) == fifo]
    assert len(fds) == 1
    assert os.get_blocking(fds[0]) is True


def _link(name: str) -> str | None:
    """The target of this process's /proc fd entry, or None when it cannot be read."""
    try:
        return os.readlink(f"/proc/self/fd/{name}")
    except OSError:
        return None


def test_writer_open_timeout_kills_and_reports(tmp_path: Path, pipes: Any, loop: Any) -> None:
    make, changes = pipes
    p = make()
    t0 = time.monotonic()
    p.start(stub(tmp_path, "exec sleep 30"), open_deadline_s=0.5)
    pid, fifo = p.pid, p.fifo
    assert pid is not None and fifo is not None
    wait_until(loop, lambda: changes)
    assert time.monotonic() - t0 < 3.0
    assert not p.running and p.error is not None and "did not open the pipe" in p.error
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not fifo.parent.exists()


def test_reader_closing_the_fifo_stops_cleanly(tmp_path: Path, pipes: Any, loop: Any) -> None:
    """Review Focus 6: the window closes mid-stream; the next write sees EPIPE, nothing raises."""
    out = tmp_path / "out.pcap"
    make, changes = pipes
    p = make()
    p.start(stub(tmp_path, f'exec head -c {24 + 16 + len(FRAME)} "$3" > "{out}"'))   # one frame, then exits
    fifo = p.fifo
    assert fifo is not None
    p.write(0.0, FRAME, 60)
    wait_until(loop, lambda: out.exists() and out.stat().st_size == 24 + 16 + len(FRAME))
    for i in range(50):                  # the reader is gone: one of these hits the broken pipe, none raises
        p.write(0.1 * i, FRAME, 60)
    wait_until(loop, lambda: changes)
    assert not p.running and p.error == "Wireshark closed the pipe"
    assert not fifo.parent.exists()
    p.write(9.0, FRAME, 60)               # after the end: ignored, no exception


def test_stop_leaves_process_running_kill_ends_it(tmp_path: Path, pipes: Any, loop: Any) -> None:
    out = tmp_path / "out.pcap"
    make, changes = pipes
    p = make()
    p.start(stub(tmp_path, f'cat "$3" > "{out}"; exec sleep 30'))      # like the window: stays after EOF
    p.write(0.0, FRAME, 60)
    wait_until(loop, lambda: out.exists() and out.stat().st_size >= 24 + 16 + len(FRAME))
    fifo = p.fifo
    assert fifo is not None
    p.stop()
    pid = p.pid
    assert pid is not None
    wait_until(loop, lambda: not fifo.parent.exists())
    assert not p.running and p.error is None and changes == []
    os.kill(pid, 0)                       # still there
    p.kill()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_full_queue_drops_and_counts(tmp_path: Path, pipes: Any) -> None:
    make, _ = pipes
    p = make()
    # never opens: nothing drains the queue
    p.start(stub(tmp_path, "exec sleep 30"), open_deadline_s=10.0)
    for _ in range(wireshark.QUEUE_MAX + 5):
        p.write(0.0, FRAME, 60)
    assert p.dropped == 5
    assert p.running


def _gone(pid: int) -> bool:
    """The process is gone or a zombie (a reaped one has no /proc entry; it may vanish mid-read)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] == "Z"
    except FileNotFoundError:
        return True


def test_a_group_member_that_outlives_the_leader_is_signalled(
        tmp_path: Path, pipes: Any, loop: Any) -> None:
    """A child still in the group after the leader exited gets SIGTERM from process_alive (131)."""
    child = tmp_path / "child.pid"
    make, _ = pipes
    p = make()
    p.start(stub(tmp_path, f'sleep 30 & echo $! > "{child}"; exit 0'))     # the leader exits, its child stays
    wait_until(loop, lambda: child.exists() and child.read_text().strip() != "")
    cpid = int(child.read_text())
    wait_until(loop, lambda: not p.process_alive)       # sees the exit and signals the group
    wait_until(loop, lambda: _gone(cpid))


def test_kill_reaches_a_member_that_ignores_sigterm(tmp_path: Path, pipes: Any, loop: Any) -> None:
    """kill() ends a group member that ignores SIGTERM once the leader exited: the last SIGKILL reaches it."""
    member = tmp_path / "member.pid"
    make, _ = pipes
    p = make()
    p.start(stub(tmp_path, f"sh -c 'trap \"\" TERM; echo $$ > \"{member}\"; exec sleep 30' &\nexit 0"))
    wait_until(loop, lambda: member.exists() and member.read_text().strip() != "" and p._leader_exited())
    mpid = int(member.read_text())
    assert not _gone(mpid)
    p.kill()
    wait_until(loop, lambda: _gone(mpid))


def test_stop_while_opening_with_nothing_queued_delivers_an_empty_capture(
        tmp_path: Path, pipes: Any, loop: Any) -> None:
    """A stop before Wireshark opened the pipe still writes the pcap header, then EOF (p3c-capture-132)."""
    out = tmp_path / "out.pcap"
    make, changes = pipes
    p = make()
    p.start(stub(tmp_path, f'sleep 0.5; cat "$3" > "{out}"; exec sleep 30'))   # opens the pipe late
    p.stop()                                            # before Wireshark opened it, nothing queued
    wait_until(loop, lambda: out.exists() and out.stat().st_size == 24)        # the pcap header, then EOF
    assert p.error is None and changes == []


def test_a_stopped_pipe_past_its_open_deadline_leaves_the_window(
        tmp_path: Path, pipes: Any, loop: Any) -> None:
    """A stop then an open deadline gives up quietly and leaves Wireshark running (p3c-capture-132)."""
    make, changes = pipes
    p = make()
    p.start(stub(tmp_path, "exec sleep 30"), open_deadline_s=0.3)            # never opens the pipe
    p.stop()
    pid = p.pid
    assert pid is not None
    wait_until(loop, lambda: p.fifo is None or not p.fifo.parent.exists())   # the writer gave up
    os.kill(pid, 0)                                     # still running: the stop did not kill it
    assert p.error is None and changes == []
