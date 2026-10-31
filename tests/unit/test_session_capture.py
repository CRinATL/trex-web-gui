"""TrexSession P3c capture commands (Task 4b): the monitor, the recorders, Save, the poll loop and the
disconnect rules. The trexapi capture functions are stubbed by a small in-memory server (`Server`) that
records each call with the thread it ran on; the session, its executors, the spool and the decoder are real.
Frames are the recorded ones (tests/fixtures/p3c/summary.json). Server-side behaviour against the fake is in
tests/contract/test_api_capture.py."""
import asyncio
import base64
import collections
import json
import os
import struct
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, SessionState, TrexSession
from trex_web_gui.sessions import capture as capture_mod
from trex_web_gui.sessions import wireshark as wireshark_mod
from trex_web_gui.store.spool import CaptureKey, Spool
from trex_web_gui.trexapi import CommandRefused, Fetched, TrexApiError, scapy_exec

SUMMARY = json.loads((FIXTURES / "p3c" / "summary.json").read_text())
UDP = SUMMARY["frames"]["udp"]
FIRST = SUMMARY["first_packet"]
NOT_FOUND = "capture ID not found"


def pkt(i: int = 1, ts: float = 100.5) -> dict[str, Any]:
    """A capture packet dict: the UDP frame, RX on port 1, wirelen 60, with this index and timestamp."""
    return {"binary": UDP, "index": i, "origin": "RX", "port": 1, "ts": ts, "wirelen": 60}


def pkts(n: int, ts: float = 100.5) -> list[dict[str, Any]]:
    """n packet dicts with indexes 1..n and timestamps 1 ms apart from ts."""
    return [pkt(i + 1, ts + i * 0.001) for i in range(n)]


def records(path: Path) -> int:
    """Number of records in a pcap file; a record counts once its 16-byte header is whole, body or not."""
    data = path.read_bytes()
    pos, n = 24, 0
    while pos + 16 <= len(data):
        pos += 16 + struct.unpack_from("<I", data, pos + 8)[0]
        n += 1
    return n


class Server:
    """The capture side of a TRex server, as the trexapi capture functions see it."""

    def __init__(self) -> None:
        """An empty fake: no rows, ids from 1, an empty call log, no scripted replies, no delays, no hook."""
        self.rows: dict[int, trexapi.CaptureRow] = {}
        self.next_id = 1
        self.calls: list[tuple[str, int | None, str]] = []       # (name, capture id, thread name)
        self.script: dict[int, collections.deque[Any]] = {}      # id -> Fetched | Exception | callable
        self.default_fetch: Callable[[int], Fetched] = lambda cid: Fetched([], 0, self.start_ts(cid))
        self.start_delay = 0.0
        self.fetch_delay = 0.0
        self.status_error: Exception | None = None
        self.status_delay = 0.0
        self.start_ts_of: dict[int, float] = {}
        self.on_stop: Callable[[int], None] | None = None
        self.hidden: set[int] = set()                            # ids the status leaves out

    def start_ts(self, cid: int) -> float:
        """The start timestamp reported for capture cid: the one set in start_ts_of, else 100.0."""
        return self.start_ts_of.get(cid, 100.0)

    def n(self, name: str) -> int:
        """How many logged calls have this name."""
        return sum(1 for c in self.calls if c[0] == name)

    def add(self, cid: int | None = None, state: str = "STOPPED", rx: tuple[int, ...] = (1,),
            endpoint: str | None = None, mode: str = "fixed") -> int:
        """Puts a row in rows (the next free id when cid is None; an existing row of that id is replaced);
        returns the id."""
        if cid is None:
            cid = self.next_id
        self.next_id = max(self.next_id, cid + 1)
        self.rows[cid] = {"id": cid, "state": state, "mode": mode, "tx": [], "rx": list(rx), "bpf": "",
                          "count": 0, "matched": 0, "fetched": 0, "limit": 100, "bytes": 0,
                          "endpoint": endpoint, "snaplen": None}
        return cid

    def queue(self, cid: int, *replies: Any) -> None:
        """Appends replies to capture cid's script: a Fetched is returned, an Exception raised, a callable
        called."""
        self.script.setdefault(cid, collections.deque()).extend(replies)

    def _log(self, name: str, cid: int | None) -> None:
        """Appends (name, cid, current thread name) to calls."""
        self.calls.append((name, cid, threading.current_thread().name))

    def start(self, c: Any, tx: list[int], rx: list[int], limit: int, mode: str, bpf: str,
              snaplen: int = 0) -> trexapi.CaptureStarted:
        """Stands in for capture_start: logs, sleeps start_delay, adds an ACTIVE row for tx, rx, mode and
        limit (bpf and snaplen unused); returns its id and start_ts."""
        self._log("start", None)
        time.sleep(self.start_delay)
        cid = self.add(state="ACTIVE", rx=tuple(rx), mode=mode)
        self.rows[cid]["tx"], self.rows[cid]["limit"] = list(tx), limit
        return trexapi.CaptureStarted(cid, self.start_ts(cid))

    def status(self, c: Any) -> list[trexapi.CaptureRow]:
        """Stands in for capture_status: logs, sleeps status_delay, raises status_error if set, else copies
        of the rows (hidden ids left out) by id."""
        self._log("status", None)
        time.sleep(self.status_delay)
        if self.status_error is not None:
            raise self.status_error
        return [dict(r) for i, r in sorted(self.rows.items()) if i not in self.hidden]   # type: ignore[misc]

    def fetch(self, c: Any, cid: int, limit: int, snaplen: int = 0) -> Fetched:
        """Stands in for capture_fetch_once. Limit 0 is a peek: logs "peek", takes nothing, reports the
        head scripted reply's count and start_ts (count 0 and start_ts(cid) when the head is not a Fetched).
        Otherwise logs "fetch", sleeps fetch_delay and pops the next scripted reply (raised if an
        Exception, called if callable), else default_fetch; an unknown id raises not-found."""
        if limit == 0:                              # the peek: takes nothing, names the capture (start_ts)
            self._log("peek", cid)
            if cid not in self.rows:
                raise TrexApiError(NOT_FOUND)
            head = (self.script.get(cid) or [None])[0]
            ts = head.start_ts if isinstance(head, Fetched) else self.start_ts(cid)
            return Fetched([], len(head.pkts) + head.pending if isinstance(head, Fetched) else 0, ts)
        self._log("fetch", cid)
        time.sleep(self.fetch_delay)
        if cid not in self.rows:
            raise TrexApiError(NOT_FOUND)
        q = self.script.get(cid)
        if q:
            r = q.popleft()
            if isinstance(r, Exception):
                raise r
            return r() if callable(r) else r       # type: ignore[no-any-return]
        return self.default_fetch(cid)

    @staticmethod
    def _connected(c: Any) -> None:
        """Raises TrexApiError when the client was disconnected (closed), as the real client does."""
        if c.closed:
            raise TrexApiError("Not connected to server")

    def stop(self, c: Any, cid: int) -> int:
        """Stands in for capture_stop: logs, requires a connected client, runs on_stop, then marks the row
        STOPPED and returns its count; unknown id raises not-found."""
        self._log("stop", cid)
        self._connected(c)
        if self.on_stop:
            self.on_stop(cid)
        if cid not in self.rows:
            raise TrexApiError(NOT_FOUND)
        self.rows[cid]["state"] = "STOPPED"
        return int(self.rows[cid]["count"])

    def remove(self, c: Any, cid: int) -> None:
        """Stands in for capture_remove: logs, requires a connected client, deletes the row; unknown id
        raises not-found."""
        self._log("remove", cid)
        self._connected(c)
        if cid not in self.rows:
            raise TrexApiError(NOT_FOUND)
        del self.rows[cid]


class StubClient:
    def __init__(self) -> None:
        """Gives the client a conn.rpc whose set_retry_base is a no-op and a conn that reads as down, so a
        disconnect never tries the release (the stub holds no ports); closed starts False."""
        self.conn = type("Conn", (), {"rpc": type("Rpc", (), {
            "set_retry_base": staticmethod(lambda flag: None)})(),
            "is_connected": staticmethod(lambda: False)})()

    closed = False

    def disconnect(self, **kw: Any) -> None:
        """Marks the client closed, after which the Server refuses stop and remove."""
        self.closed = True          # runs on the control executor, so in its FIFO order like the real client


class Tripwire(StubClient):
    """A control client that fails the test when code reads an attribute StubClient lacks (conn, closed and
    disconnect stay readable); the service gate reads the session's `_ports` only."""

    def __getattr__(self, name: str) -> Any:
        """Fails the test: only attributes StubClient lacks reach here."""
        raise AssertionError(f"the client attribute {name!r} was read")


class Rig:
    def __init__(self, mp: pytest.MonkeyPatch, tmp_path: Path, spool: bool = True) -> None:
        """A real TrexSession on stub clients, CONNECTED and publishing into msgs, with its spool under
        tmp_path (none if spool is False); routes trexapi capture calls to a Server, sets FETCH_S 0.05
        and STATUS_S 0.1, empties port_views and turns service mode on for both ports (monkeypatch
        undoes the patches)."""
        self.msgs: list[dict[str, Any]] = []
        self.server = Server()
        self.spool_dir = tmp_path / "spool"
        self.s = TrexSession(self.msgs.append, release_on_exit=lambda: False,
                             spool_dir=self.spool_dir if spool else None)
        self.c, self.r = StubClient(), StubClient()
        self.connect_stub()
        self.mp = mp
        mp.setattr(capture_mod, "FETCH_S", 0.05)
        mp.setattr(capture_mod, "STATUS_S", 0.1)
        for name in ("start", "status", "fetch", "stop", "remove"):
            mp.setattr(trexapi, f"capture_{name}" if name != "fetch" else "capture_fetch_once",
                       getattr(self.server, name))
        mp.setattr(trexapi, "port_views", lambda c: [])
        self.service_on()

    def connect_stub(self) -> None:
        """Points the session at the stub control and reader clients in CONNECTED state with
        ConnectParams("h"); Rig.__init__ is the only caller."""
        s = self.s
        s._client, s._reader, s._state = self.c, self.r, SessionState.CONNECTED
        s._params = ConnectParams("h")

    def service_on(self, *ports: int) -> None:
        """Sets the session port list for ports 0 and 1, with service mode on for the given ports (both
        when none)."""
        self.s._ports = [{"id": p, "service": p in (ports or (0, 1))} for p in (0, 1)]    # type: ignore[misc]

    def of(self, kind: str) -> list[dict[str, Any]]:
        """The published messages of this type."""
        return [m for m in self.msgs if m["type"] == kind]

    def logs(self) -> list[str]:
        """The texts of all published log messages."""
        return [m["message"] for m in self.of("log")]

    async def until(self, pred: Callable[[], bool], timeout: float = 3.0) -> None:
        """Polls pred every 10 ms on the running loop; AssertionError after timeout seconds."""
        end = time.monotonic() + timeout
        while not pred():
            if time.monotonic() > end:
                raise AssertionError("condition not met in time")
            await asyncio.sleep(0.01)

    def run(self, body: Callable[[TrexSession], Awaitable[None]]) -> None:
        """Runs body(session) on a fresh event loop, then closes the session and shuts the scapy executor
        down, also if body fails; a close() that raises skips the executor shutdown."""
        # Binds the session to this loop, runs body, then closes the session and shuts the scapy
        # executor down.
        async def go() -> None:
            self.s._loop = asyncio.get_running_loop()
            try:
                await body(self.s)
            finally:
                await self.s.close()
                scapy_exec.shutdown()
        asyncio.run(go())


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Rig:
    """A Rig on this test's monkeypatch and tmp_path; the monkeypatch undoes the patches, nothing else is
    torn down."""
    return Rig(monkeypatch, tmp_path)


def refused(code: str) -> Any:
    """A pytest.raises context for CommandRefused whose code equals code."""
    return pytest.raises(CommandRefused, check=lambda e: e.code == code)


# -- monitor start --------------------------------------------------------------------------------
def test_monitor_start_runs_start_on_control_and_fetch_on_reader(rig: Rig) -> None:
    rig.server.queue(1, Fetched(pkts(2), 0, 100.0))

    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([0], [1], "udp", 100)
        assert (mon.id, mon.state, mon.limit) == (1, "ACTIVE", 100)
        await rig.until(lambda: bool(rig.of("capture_pkts")))
    rig.run(body)
    by_name: dict[str, set[str]] = {}
    for name, _, thread in rig.server.calls:
        by_name.setdefault(name, set()).add(thread.split("_")[0])
    assert (by_name["start"] == {"trex"} and by_name["fetch"] == {"trex-reader"}
            and by_name["status"] == {"trex-reader"})


def test_monitor_start_twice_sends_one_start(rig: Rig) -> None:
    rig.server.start_delay = 0.2
    results: list[Any] = []

    async def body(s: TrexSession) -> None:
        results.extend(await asyncio.gather(s.monitor_start([0], [1], ""), s.monitor_start([0], [1], ""),
                                            return_exceptions=True))
    rig.run(body)
    assert rig.server.n("start") == 1
    assert sorted(type(r).__name__ for r in results) == ["CommandRefused", "MonitorState"]
    assert next(r for r in results if isinstance(r, CommandRefused)).code == "monitor_active"


def test_rx_port_without_service_is_service_off_before_any_rpc(rig: Rig) -> None:
    rig.service_on(0)
    rig.s._client = Tripwire()

    async def body(s: TrexSession) -> None:
        with refused("service_off") as e:
            await s.monitor_start([], [1], "")
        assert "[1]" in e.value.message and "af_packet" in e.value.message
        with refused("service_off"):
            await s.recorder_start([], [1], "")
        rig.s._client = rig.c
    rig.run(body)
    assert rig.server.calls == []


def test_tx_only_capture_is_not_gated_and_an_rx_port_off_service_still_is(rig: Rig) -> None:
    # spec 6.5: the gate covers Rx ports; Tx port 0 is off and a Tx-only start is accepted
    rig.service_on(1)

    async def body(s: TrexSession) -> None:
        assert await s.recorder_start([0], [], "") == 1
        rig.service_on(0)               # Tx port on, Rx port 1 off: still refused for the Rx port
        with refused("service_off") as e:
            await s.recorder_start([0], [1], "")
        assert "[1]" in e.value.message and "[0]" not in e.value.message
    rig.run(body)
    assert rig.server.n("start") == 1


def test_unknown_port_is_refused_before_any_rpc(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        with refused("bad_port"):
            await s.monitor_start([], [7], "")
    rig.run(body)
    assert rig.server.calls == []


# -- the slot gate --------------------------------------------------------------------------------
def test_recorder_start_refuses_at_nine_captures(rig: Rig) -> None:
    for _ in range(9):
        rig.server.add()

    async def body(s: TrexSession) -> None:
        with refused("capture_slots") as e:
            await s.recorder_start([], [1], "")
        assert "9 of 10" in e.value.message
    rig.run(body)
    assert rig.server.n("start") == 0


def test_recorder_start_counts_fresh_status_not_cache(rig: Rig) -> None:
    for _ in range(9):
        rig.server.add()

    async def body(s: TrexSession) -> None:
        assert s._captures == []                      # right after connect nothing was ever listed
        with refused("capture_slots"):
            await s.recorder_start([], [1], "")
    rig.run(body)
    assert rig.server.n("status") == 1 and rig.server.n("start") == 0


def test_two_concurrent_recorder_starts_at_eight_admit_one(rig: Rig) -> None:
    for _ in range(8):
        rig.server.add()
    rig.server.start_delay = 0.1
    results: list[Any] = []

    async def body(s: TrexSession) -> None:
        results.extend(await asyncio.gather(s.recorder_start([], [1], ""), s.recorder_start([], [1], ""),
                                            return_exceptions=True))
    rig.run(body)
    assert rig.server.n("start") == 1
    assert sorted(type(r).__name__ for r in results) == ["CommandRefused", "int"]
    assert next(r for r in results if isinstance(r, CommandRefused)).code == "capture_slots"


def test_monitor_start_is_gated_by_the_same_slot_rule(rig: Rig) -> None:
    for _ in range(9):
        rig.server.add()

    async def body(s: TrexSession) -> None:
        with refused("capture_slots"):
            await s.monitor_start([], [1], "")
        assert s._monitor is None
    rig.run(body)
    assert rig.server.n("start") == 0


def test_a_status_failure_or_service_refusal_leaves_the_monitor_startable(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        rig.server.status_error = TrexApiError("boom")
        with pytest.raises(TrexApiError, match="boom"):
            await s.monitor_start([], [1], "")
        rig.server.status_error = None
        rig.service_on(0)
        with refused("service_off"):
            await s.monitor_start([], [1], "")
        rig.service_on()
        assert (await s.monitor_start([], [1], "")).id == 1
    rig.run(body)
    assert rig.server.n("start") == 1


def test_recorder_pending_is_released_when_start_returns(rig: Rig) -> None:
    for _ in range(7):
        rig.server.add()
    release = asyncio.Event()

    async def body(s: TrexSession) -> None:
        calls: list[int] = []

        async def held() -> None:
            calls.append(1)
            if len(calls) == 1:
                await release.wait()            # only the first start's post-command sync is held
        s._sync_quietly = held                  # type: ignore[method-assign]
        first = asyncio.ensure_future(s.recorder_start([], [1], ""))
        await rig.until(lambda: rig.server.n("start") == 1)
        # admitted while the first is held
        assert await asyncio.wait_for(s.recorder_start([], [1], ""), 2) == 9
        release.set()
        await first
    rig.run(body)
    assert rig.server.n("start") == 2


def test_recorder_start_starts_the_poll_loop(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        assert s._capture_task is None
        await s.recorder_start([], [1], "")
        assert s._capture_task is not None and not s._capture_task.done()
    rig.run(body)


# -- connect --------------------------------------------------------------------------------------
def _real_session(tmp_path: Path, **kw: Any) -> TrexSession:
    """A real TrexSession (no stubs, publishes discarded) spooling under tmp_path/spool; kw go to the
    constructor."""
    return TrexSession(lambda m: None, spool_dir=tmp_path / "spool", **kw)


def test_connect_syncs_captures_and_starts_loop_when_any_exist(fake_trex: Any, foreign_client: Any,
                                                               tmp_path: Path) -> None:
    f = fake_trex()
    other = foreign_client(f)
    for _ in range(2):
        rc = other._transmit("capture", params={"command": "start", "mode": "fixed", "limit": 10, "tx": [],
                                                "rx": [1], "filter": ""})
        assert rc, rc.err()
    quiet = _real_session(tmp_path)
    busy = _real_session(tmp_path)
    p = ConnectParams(f.host, f.rpc_port, f.pub_port, 2.0)

    async def go() -> None:
        try:
            await busy.connect(p)
            snap = busy.capture_snapshot()
            assert [(r["id"], r["mine"]) for r in snap["captures"]] == [(1, False), (2, False)]
            assert busy._capture_task is not None and not busy._capture_task.done()
            await busy.disconnect()
            for cid in (1, 2):
                assert other._transmit("capture", params={"command": "remove", "capture_id": cid})
            await quiet.connect(p)
            assert quiet._captures == [] and quiet._capture_task is None
        finally:
            await busy.close()
            await quiet.close()
    asyncio.run(go())


def test_connect_survives_a_capture_sync_failure(fake_trex: Any, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    f = fake_trex()
    msgs: list[dict[str, Any]] = []
    s = TrexSession(msgs.append, spool_dir=tmp_path / "spool")

    def boom(c: Any) -> Any:
        raise TrexApiError("status exploded")
    monkeypatch.setattr(trexapi, "capture_status", boom)

    async def go() -> None:
        try:
            info = await s.connect(ConnectParams(f.host, f.rpc_port, f.pub_port, 2.0))
            assert info.version == "v3.08" and s.snapshot()["state"] == "connected"
        finally:
            await s.close()
    asyncio.run(go())
    assert [m["message"] for m in msgs if m["type"] == "log" and "status exploded" in m["message"]] == [
        "Capture list failed: status exploded"]
    assert s._captures == [] and s._capture_task is None


def test_reconnect_forgets_a_stale_monitor_id(fake_trex: Any, foreign_client: Any, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """LOST, then a new connect to a server listing a foreign capture under the old monitor's id: nothing is
    fetched or removed, and the row is not ours."""
    f = fake_trex()
    other = foreign_client(f)
    assert other._transmit("capture", params={"command": "start", "mode": "fixed", "limit": 10, "tx": [],
                                              "rx": [1], "filter": ""})
    fetched: list[int] = []
    real = trexapi.capture_fetch_once
    monkeypatch.setattr(trexapi, "capture_fetch_once",
                        lambda c, cid, n, snaplen=0: fetched.append(cid) or real(c, cid, n))
    monkeypatch.setattr(capture_mod, "FETCH_S", 0.05)
    s = _real_session(tmp_path)
    s._monitor = capture_mod.MonitorState(1, 1.0, [], [1], "", 100)
    s._my_ids = {1}
    s._state = SessionState.LOST

    async def go() -> None:
        try:
            await s.connect(ConnectParams(f.host, f.rpc_port, f.pub_port, 2.0))
            await asyncio.sleep(0.4)
            snap = s.capture_snapshot()
            assert (snap["monitor"] is None
                    and [(r["id"], r["mine"]) for r in snap["captures"]] == [(1, False)])
        finally:
            await s.close()
    asyncio.run(go())
    assert fetched == []
    assert 1 in {r["id"] for r in _status(other)}


def _status(c: Any) -> list[dict[str, Any]]:
    """The rows of a raw capture status RPC sent through the client; asserts the call succeeded."""
    rc = c._transmit("capture", params={"command": "status"})
    assert rc, rc.err()
    return list(rc.data())


# -- the loop -------------------------------------------------------------------------------------
def test_monitor_fetch_flows_while_the_control_executor_is_held(rig: Rig,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_mod, "FETCH_S", 0.5)          # the production cadence
    monkeypatch.setattr(capture_mod, "STATUS_S", 1.0)
    rig.server.default_fetch = lambda cid: Fetched(pkts(1), 0, 100.0)
    stamps: list[float] = []

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        held = ThreadPoolExecutor(max_workers=1)
        s._executor = held                                    # the control executor, held below
        t0 = time.monotonic()
        asyncio.get_running_loop().run_in_executor(held, time.sleep, 2.0)
        await asyncio.sleep(2.0)
        stamps.extend(float(m["rows"][0]["index"]) for m in rig.of("capture_pkts"))
        assert time.monotonic() - t0 < 2.4
        held.shutdown(wait=True)
        s._executor = ThreadPoolExecutor(max_workers=1)
    rig.run(body)
    # 2.0 s of hold at 0.5 s per fetch, control blocked throughout
    assert len(stamps) >= 3, stamps


class FakeClock:
    """`capture_mod.time` and `capture_mod.asyncio` for the loop: `monotonic` reads a counter that only
    the loop's sleeps and the scripted fetch durations advance, so the cadence is exact, not measured."""

    def __init__(self) -> None:
        """Starts the counter at 0.0 with no recorded sleeps."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        """The counter's current value."""
        return self.now

    def __getattr__(self, name: str) -> Any:                # time.time, asyncio.Lock and the rest stay real
        return getattr(time, name) if hasattr(time, name) else getattr(asyncio, name)

    async def sleep(self, d: float) -> None:
        """Records d, advances the counter by d and yields to the loop once; waits no real time."""
        self.sleeps.append(d)
        self.now += d
        await asyncio.sleep(0)


def test_monitor_fetch_holds_its_period_when_a_fetch_takes_much_of_it(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deadline, not a pause after the work: a 0.3 s fetch still starts every 0.5 s, and a 2 s stall gives
    one immediate fetch, then the cadence again (not a burst of catch-up fetches)."""
    clock = FakeClock()
    monkeypatch.setattr(capture_mod, "time", clock)
    monkeypatch.setattr(capture_mod, "asyncio", clock)
    monkeypatch.setattr(capture_mod, "FETCH_S", 0.5)
    monkeypatch.setattr(capture_mod, "STATUS_S", 1.0)
    durations = collections.deque([0.3, 0.3, 2.0, 0.3, 0.3, 0.3])
    starts: list[float] = []

    def fetch(cid: int) -> Fetched:
        starts.append(clock.now)
        clock.now += durations.popleft() if durations else 0.0
        return Fetched([], 0, 100.0)
    rig.server.default_fetch = fetch

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await rig.until(lambda: len(starts) >= 6)
        await s._stop_capture_loop()
    rig.run(body)
    assert starts[:6] == pytest.approx([0.5, 1.0, 1.5, 3.5, 4.0, 4.5]), starts
    assert clock.sleeps[:6] == pytest.approx([0.5, 0.2, 0.2, 0.0, 0.2, 0.2]), clock.sleeps


def test_monitor_stop_drains_until_pending_zero(rig: Rig) -> None:
    rig.server.queue(1, Fetched(pkts(50), 120, 100.0), Fetched(pkts(50), 70, 100.0),
                     Fetched(pkts(50), 20, 100.0), Fetched(pkts(20), 0, 100.0))

    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([], [1], "", limit=200)
        # the loop would race the drain for these scripted replies
        s._capture_task.cancel()
        await asyncio.gather(s._capture_task, return_exceptions=True)
        await s.monitor_stop()
        assert s._monitor is None and mon.state == "STOPPED"
    rig.run(body)
    names = [c[0] for c in rig.server.calls]
    assert names.count("fetch") == 4 and len(rig.of("capture_pkts")) == 4
    assert names.index("stop") < names.index("fetch") and names[-1] in ("status", "remove")
    assert names.index("remove") > len(names) - 1 - names[::-1].index("fetch")
    assert 1 not in rig.server.rows


def test_stale_batch_after_stop_is_dropped(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    # the loop's fetch; the stop's drain gets the default
    rig.server.queue(1, Fetched(pkts(1), 0, 100.0))
    gate = threading.Event()
    real = scapy_exec.arun
    calls: list[int] = []

    async def slow_decode(fn: Any, *a: Any) -> Any:
        calls.append(1)
        await asyncio.get_running_loop().run_in_executor(None, gate.wait, 5)
        return await real(fn, *a)
    monkeypatch.setattr(scapy_exec, "arun", slow_decode)

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await rig.until(lambda: bool(calls))                 # the loop's batch is being decoded
        await s.monitor_stop()
        gate.set()
        await asyncio.sleep(0.3)
    rig.run(body)
    assert rig.of("capture_pkts") == []


def test_ts_rel_us_is_rounded_from_the_recorded_ts_and_start_ts(rig: Rig) -> None:
    p = pkt(1, FIRST["ts"])
    rig.server.start_ts_of[1] = FIRST["start_ts"]
    rig.server.queue(1, Fetched([p], 0, FIRST["start_ts"]))

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await rig.until(lambda: bool(rig.of("capture_pkts")))
    rig.run(body)
    row = rig.of("capture_pkts")[0]["rows"][0]
    assert row["ts_rel_us"] == round((FIRST["ts"] - FIRST["start_ts"]) * 1e6)
    assert (row["src"], row["dst"], row["proto"]) == ("16.0.0.1", "48.0.0.1", "UDP")
    assert rig.of("capture_pkts")[0]["monitor_id"] == 1


def test_not_found_clears_monitor_once(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        del rig.server.rows[1]                               # the fetch now says "capture ID not found"
        await rig.until(lambda: s._monitor is None)
        await asyncio.sleep(0.3)
    rig.run(body)
    assert [m for m in rig.logs() if "removed on the server" in m] == [
        "Capture monitor 1 was removed on the server."]
    assert [m["monitor"]["gone"] for m in rig.of("capture") if m["monitor"] and m["monitor"]["gone"]] == [
        "removed on the server"]


def test_monitor_removed_elsewhere_is_cleared_once(rig: Rig) -> None:
    # fetch keeps answering; only the status differs
    rig.server.default_fetch = lambda cid: Fetched([], 0, 100.0)

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        # the status stops listing it; fetch still answers
        rig.server.hidden = {1}
        await rig.until(lambda: s._monitor is None)
        await asyncio.sleep(0.3)
    rig.run(body)
    assert len([m for m in rig.logs() if "removed on the server" in m]) == 1
    assert len([m for m in rig.of("capture") if m["monitor"] and m["monitor"]["gone"]]) == 1


def test_loss_in_capture_loop_marks_lost(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        rig.server.status_error = trexapi.ServerUnreachable("Cannot reach the TRex server")
        await rig.until(lambda: s._state == SessionState.LOST)
        assert s._capture_task is None
    rig.run(body)


def test_foreign_capture_listed_not_adopted(rig: Rig) -> None:
    rig.server.add(5, state="ACTIVE")

    async def body(s: TrexSession) -> None:
        await s.capture_sync()
        snap = s.capture_snapshot()
        assert [(r["id"], r["mine"]) for r in snap["captures"]] == [(5, False)] and snap["monitor"] is None
        s._ensure_capture_loop()
        await asyncio.sleep(0.3)
        assert s._monitor is None
    rig.run(body)
    assert rig.server.n("fetch") == 0                        # a foreign capture is never fetched by the loop


def test_service_rows_flagged_while_busy(rig: Rig) -> None:
    rig.server.add(5, rx=(1,))
    rig.server.add(6, rx=(0,))

    async def body(s: TrexSession) -> None:
        await s.capture_sync()
        s._busy = {1: "ping"}
        assert {r["id"]: r["service"] for r in s.capture_snapshot()["captures"]} == {5: True, 6: False}
    rig.run(body)


def test_service_rows_are_capture_busy_for_stop_save_and_remove(rig: Rig) -> None:
    """A TRex service's capture is refused capture_busy by stop, save and remove, with no RPC sent."""
    rig.server.add(5, rx=(1,))

    async def body(s: TrexSession) -> None:
        await s.capture_sync()
        s._busy = {1: "ping"}
        for call in (s.recorder_stop, s.recorder_save, s.recorder_remove):
            with refused("capture_busy"):
                await call(5)
        assert rig.server.n("remove") == 0 and rig.server.n("stop") == 0
    rig.run(body)


def test_a_row_is_removable_once_its_service_ends(rig: Rig) -> None:
    """The guard reads _busy at call time: a row whose service ended is removable without a resync."""
    rig.server.add(5, rx=(1,))

    async def body(s: TrexSession) -> None:
        await s.capture_sync()
        s._busy = {1: "ping"}
        with refused("capture_busy"):
            await s.recorder_remove(5)
        s._busy = {}                                   # the ping ended: no resync needed
        await s.recorder_remove(5)
        assert rig.server.n("remove") == 1
    rig.run(body)


def test_our_own_captures_are_never_service_rows_during_a_ping(rig: Rig) -> None:
    rig.server.add(5, rx=(1,))                                # a foreign capture on the pinged port

    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([], [1], "")
        rec = await s.recorder_start([], [1], "", 100)
        await s.capture_sync()
        s._busy = {1: "ping"}
        rows = {r["id"]: (r["mine"], r["service"]) for r in s.capture_snapshot()["captures"]}
        assert rows == {5: (False, True), mon.id: (True, False), rec: (True, False)}
    rig.run(body)


def test_a_status_without_the_stopping_monitor_still_publishes_its_other_rows(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([], [1], "")
        await s._stop_capture_loop()
        s._stopping = mon                                     # monitor_stop is removing it: not "gone"
        rig.server.hidden = {mon.id}
        rig.server.add(5)
        rig.msgs.clear()
        await s.capture_sync()
        assert [[r["id"] for r in m["captures"]] for m in rig.of("capture")] == [[5]]
        assert s._monitor is mon and mon.gone is None
    rig.run(body)


# -- save -----------------------------------------------------------------------------------------
def test_save_loops_until_pending_zero_and_removes(rig: Rig) -> None:
    rig.server.add(5)
    rig.server.queue(5, Fetched(pkts(50), 110, 100.0), Fetched(pkts(50), 60, 100.0),
                     Fetched(pkts(50), 10, 100.0), Fetched(pkts(10), 0, 100.0))

    async def body(s: TrexSession) -> None:
        f = await s.recorder_save(5)
        assert f["pkt_count"] == 160 and not f["partial"] and f["capture_id"] == 5
        assert records(rig.spool_dir / f["name"]) == 160
    rig.run(body)
    names = [c[0] for c in rig.server.calls if c[1] == 5]
    assert names[0] == "stop" and names.count("fetch") == 4 and names[-1] == "remove"
    done = [m for m in rig.of("capture_save") if m["file"]]
    assert len(done) == 1 and done[0]["error"] is None


def test_save_failure_keeps_partial_and_resume_appends(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.start_ts_of[7] = 100.0
    rig.server.queue(7, Fetched(pkts(50), 70, 100.0), Fetched(pkts(50), 20, 100.0), TrexApiError("boom"))

    async def body(s: TrexSession) -> None:
        with pytest.raises(TrexApiError, match="boom"):
            await s.recorder_save(7)
        listed = Spool(rig.spool_dir).list()
        assert [(f["partial"], f["pkt_count"]) for f in listed] == [(True, 100)]
        assert records(rig.spool_dir / listed[0]["name"]) == 100
        assert rig.of("capture_save")[-1]["error"] == "boom" and rig.server.n("remove") == 0
        before = rig.server.n("fetch")
        rig.server.queue(7, Fetched(pkts(20), 0, 100.0))
        f = await s.recorder_save(7)
        assert rig.server.n("fetch") - before == 1                   # one more fetch of the 20 that were left
        assert (f["pkt_count"], f["partial"]) == (120, False) and records(rig.spool_dir / f["name"]) == 120
        assert [x["name"] for x in Spool(rig.spool_dir).list()] == [f["name"]]
    rig.run(body)
    assert rig.server.n("remove") == 1


def test_save_resume_requires_matching_server_and_start_ts(rig: Rig) -> None:
    sp = Spool(rig.spool_dir)
    w = sp.writer(CaptureKey("h:4501", 100.0, 7))
    w.write(0.0, base64.b64decode(UDP), 60)
    old = w.suspend()
    rig.server.add(7)                                         # the status row carries no start_ts
    rig.server.queue(7, Fetched(pkts(1, 200.5), 0, 200.0))    # the reply names a different capture

    async def body(s: TrexSession) -> None:
        f = await s.recorder_save(7)
        assert f["name"] != old["name"] and f["start_ts"] == 200.0 and f["pkt_count"] == 1
        listed = {x["name"]: x for x in Spool(rig.spool_dir).list()}
        assert listed[old["name"]]["partial"] and listed[old["name"]]["pkt_count"] == 1
    rig.run(body)
    done = [m for m in rig.of("capture_save") if m["file"]]
    assert done[0]["note"] == "a partial file from another capture with id 7 was left in place"


def test_save_opens_the_writer_after_the_peek_and_before_the_first_fetch(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.start_ts_of[7] = 300.5
    seen: list[list[str]] = []
    rig.server.queue(7, lambda: (seen.append(sorted(p.name for p in rig.spool_dir.iterdir())),
                                 Fetched(pkts(1, 301.0), 0, 300.5))[1])

    async def body(s: TrexSession) -> None:
        f = await s.recorder_save(7)
        assert f["start_ts"] == 300.5                           # the peek's start_ts keyed the file
    rig.run(body)
    assert len(seen) == 1
    assert next(n for n in seen[0] if not n.endswith(".json")).endswith(".partial.pcap")
    assert [c[0] for c in rig.server.calls if c[0] in ("peek", "fetch")][:2] == ["peek", "fetch"]


def test_a_writer_that_cannot_open_leaves_the_packets_on_the_server(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(5), 0, 100.0))

    def broken(self: Spool, key: CaptureKey) -> Any:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Spool, "writer", broken)

    async def body(s: TrexSession) -> None:
        with refused("spool_error"):
            await s.recorder_save(7)
    rig.run(body)
    assert rig.server.n("fetch") == 0 and rig.server.n("peek") == 1     # only the peek was sent
    assert rig.server.n("remove") == 0 and len(rig.server.script[7]) == 1


def test_a_start_ts_that_changes_between_peek_and_fetch_is_an_error(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(1), 0, 999.0))
    rig.server.start_ts_of[7] = 100.0
    real_peek = rig.server.fetch

    def peeking(c: Any, cid: int, limit: int, snaplen: int = 0) -> Fetched:
        if limit == 0:
            return Fetched([], 1, 100.0)                         # the peek names another start_ts
        return real_peek(c, cid, limit, snaplen)
    rig.mp.setattr(trexapi, "capture_fetch_once", peeking)

    async def body(s: TrexSession) -> None:
        with pytest.raises(TrexApiError, match="100.0.*999.0"):
            await s.recorder_save(7)
        assert Spool(rig.spool_dir).list() == []                # the fresh header-only file was removed
    rig.run(body)
    assert rig.server.n("remove") == 0


def test_a_first_fetch_that_raises_keeps_an_empty_partial(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.queue(7, TrexApiError("first fetch failed"))

    async def body(s: TrexSession) -> None:
        with pytest.raises(TrexApiError, match="first fetch failed"):
            await s.recorder_save(7)
    rig.run(body)
    assert [(f["partial"], f["pkt_count"]) for f in Spool(rig.spool_dir).list()] == [(True, 0)]
    assert rig.server.n("remove") == 0


def test_save_of_an_empty_capture_writes_an_empty_pcap(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched([], 0, 300.0))

    async def body(s: TrexSession) -> None:
        f = await s.recorder_save(7)
        assert (f["pkt_count"], f["start_ts"], f["partial"]) == (0, 300.0, False)
        assert records(rig.spool_dir / f["name"]) == 0
    rig.run(body)
    assert rig.server.n("remove") == 1


def test_save_no_progress_cutoff(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.queue(7, *[Fetched([], 5, 100.0)] * 5)

    async def body(s: TrexSession) -> None:
        with pytest.raises(TrexApiError, match="no progress"):
            await s.recorder_save(7)
    rig.run(body)
    assert rig.server.n("fetch") == 3 and rig.server.n("remove") == 0


def test_save_twice_is_capture_busy(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.fetch_delay = 0.2
    results: list[Any] = []

    async def body(s: TrexSession) -> None:
        results.extend(await asyncio.gather(s.recorder_save(7), s.recorder_save(7), return_exceptions=True))
    rig.run(body)
    assert rig.server.n("stop") == 1 and len(list(rig.spool_dir.glob("*.pcap"))) == 1
    assert sorted(type(r).__name__ for r in results) == ["CommandRefused", "dict"]
    assert next(r for r in results if isinstance(r, CommandRefused)).code == "capture_busy"


def test_remove_and_stop_during_save_are_capture_busy(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.fetch_delay = 0.3

    async def body(s: TrexSession) -> None:
        task = asyncio.ensure_future(s.recorder_save(7))
        await rig.until(lambda: 7 in s._saving)
        with refused("capture_busy"):
            await s.recorder_remove(7)
        with refused("capture_busy"):
            await s.recorder_stop(7)
        await task
    rig.run(body)
    assert rig.server.n("stop") == 1


def test_endpoint_row_refuses_save_and_stop(rig: Rig) -> None:
    rig.server.add(7, endpoint="tcp://x")

    async def body(s: TrexSession) -> None:
        await s.capture_sync()
        calls = len(rig.server.calls)
        with refused("endpoint_capture"):
            await s.recorder_save(7)
        with refused("endpoint_capture"):
            await s.recorder_stop(7)
        assert len(rig.server.calls) == calls
    rig.run(body)


def test_the_monitors_id_is_capture_busy_for_stop_save_and_remove(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([], [1], "")
        for call in (s.recorder_stop, s.recorder_save, s.recorder_remove):
            with refused("capture_busy"):
                await call(mon.id)
        assert rig.server.n("remove") == 0 and rig.server.n("stop") == 0
    rig.run(body)


def test_save_without_a_spool_dir_is_spool_unavailable(monkeypatch: pytest.MonkeyPatch,
                                                       tmp_path: Path) -> None:
    rig = Rig(monkeypatch, tmp_path, spool=False)
    rig.server.add(7)

    async def body(s: TrexSession) -> None:
        with refused("spool_unavailable"):
            await s.recorder_save(7)
    rig.run(body)
    assert rig.server.n("stop") == 0


def test_a_reconnect_during_save_keeps_every_fetched_packet_and_raises(rig: Rig) -> None:
    rig.server.add(7)
    other = StubClient()
    rig.server.queue(7, Fetched(pkts(50), 50, 100.0),
                     lambda: (setattr(rig.s, "_reader", other), Fetched(pkts(50), 0, 100.0))[1])

    async def body(s: TrexSession) -> None:
        with refused("not_connected"):
            await s.recorder_save(7)
        # the second fetch already drained 50 more from the server: they belong in the partial
        assert [(f["partial"], f["pkt_count"]) for f in Spool(rig.spool_dir).list()] == [(True, 100)]
        s._reader = rig.r
    rig.run(body)
    assert rig.server.n("remove") == 0


# -- disconnect and close -------------------------------------------------------------------------
def test_disconnect_removes_monitor_keeps_recorders(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        rid = await s.recorder_start([], [1], "")
        mon = await s.monitor_start([], [1], "")
        await s.disconnect()
        assert set(rig.server.rows) == {rid} and mon.id not in rig.server.rows
        assert s._monitor is None and s._captures == [] and s._my_ids == set()
    rig.run(body)


def test_disconnect_cancels_loop_before_removal_and_removes_once(rig: Rig) -> None:
    seen: list[bool] = []
    rig.server.on_stop = lambda cid: seen.append(rig.s._capture_task is None or rig.s._capture_task.done())

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await asyncio.sleep(0.15)                 # the loop is running and has polled
        await s.disconnect()
    rig.run(body)
    assert seen == [True]
    assert rig.server.n("stop") == 1 and rig.server.n("remove") == 1
    assert [m for m in rig.logs() if "removed on the server" in m] == []
    assert not [m for m in rig.of("capture") if m["monitor"] and m["monitor"]["gone"]]


def test_close_removes_monitor_after_fail_fast(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await s.close()                           # fail_fast, then disconnect(): the only path to the removal
    rig.run(body)
    assert rig.server.n("stop") == 1 and rig.server.n("remove") == 1


def test_disconnect_when_lost_skips_removal(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        calls = len(rig.server.calls)
        s._state = SessionState.LOST
        await s.disconnect()
        assert len(rig.server.calls) == calls
    rig.run(body)


def test_disconnect_during_monitor_start_removes_the_monitor_it_waited_for(rig: Rig) -> None:
    rig.server.start_delay = 0.3
    results: list[Any] = []

    async def body(s: TrexSession) -> None:
        start = asyncio.ensure_future(s.monitor_start([], [1], ""))
        await rig.until(lambda: rig.server.n("start") == 1)
        # waits for the start, then removes its monitor on the live client
        await s.disconnect()
        results.append(await asyncio.gather(start, return_exceptions=True))
    rig.run(body)
    (outcome,) = results[0]
    assert isinstance(outcome, capture_mod.MonitorState)
    assert rig.server.rows == {} and rig.server.n("stop") == 1 and rig.server.n("remove") == 1
    assert [m for m in rig.logs() if "Removing capture monitor" in m] == []


def test_a_reconnect_during_monitor_start_cleans_up_what_it_created(rig: Rig) -> None:
    rig.server.start_delay = 0.3
    other = StubClient()
    results: list[Any] = []

    async def body(s: TrexSession) -> None:
        start = asyncio.ensure_future(s.monitor_start([], [1], ""))
        await rig.until(lambda: rig.server.n("start") == 1)
        s._reader = other                       # a reconnect: the old control client is still live
        results.append(await asyncio.gather(start, return_exceptions=True))
        s._reader = rig.r
    rig.run(body)
    (outcome,) = results[0]
    assert isinstance(outcome, CommandRefused) and outcome.code == "not_connected"
    assert rig.server.rows == {} and rig.server.n("stop") == 1 and rig.server.n("remove") == 1


def test_a_failed_cleanup_after_a_reconnect_during_start_is_a_warning(
        rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    rig.server.start_delay = 0.2
    other = StubClient()

    def refuse(c: Any, cid: int) -> None:
        raise TrexApiError("remove refused")
    monkeypatch.setattr(trexapi, "capture_remove", refuse)

    async def body(s: TrexSession) -> None:
        start = asyncio.ensure_future(s.monitor_start([], [1], ""))
        await rig.until(lambda: rig.server.n("start") == 1)
        s._reader = other
        await asyncio.gather(start, return_exceptions=True)
        s._reader = rig.r
    with caplog.at_level("INFO"):
        rig.run(body)
    (rec,) = [r for r in caplog.records if "cleanup after a lost connection failed" in r.getMessage()]
    assert rec.levelname == "WARNING"


def test_monitor_counters_are_one_snapshot_while_batches_outrun_polls(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_mod, "STATUS_S", 0.4)
    srv = rig.server

    def drain(cid: int) -> Fetched:
        row = srv.rows[cid]
        n = min(capture_mod.FETCH_LIMIT, int(row["count"]))
        row["count"] = int(row["count"]) - n
        row["fetched"] = int(row["fetched"]) + n
        return Fetched(pkts(n), int(row["count"]), 100.0)
    srv.default_fetch = drain

    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([], [1], "", 1000)
        srv.rows[mon.id].update(matched=400, count=400)      # the server matched 400, none fetched yet
        await rig.until(lambda: len(rig.of("capture_pkts")) >= 3 and any(
            m["monitor"] and m["monitor"]["fetched"] > 0 for m in rig.of("capture")))
    rig.run(body)
    seen = [m["monitor"] for m in rig.of("capture") if m["monitor"]]
    assert seen and all(x["matched"] - x["fetched"] - x["count"] >= 0 for x in seen), seen
    assert any(x["fetched"] > 0 for x in seen)               # a poll did land, so this is not vacuous


def test_disconnect_marks_the_monitor_as_stopping_so_a_late_status_is_not_gone(rig: Rig) -> None:
    rig.server.on_stop = lambda cid: time.sleep(0.4)         # the removal outlasts the status below

    async def body(s: TrexSession) -> None:
        mon = await s.monitor_start([], [1], "")
        rig.server.hidden.add(mon.id)                        # this status omits the monitor
        rig.server.status_delay = 0.1
        sync = asyncio.ensure_future(s.capture_sync())
        await rig.until(lambda: rig.server.n("status") >= 1)
        await s.disconnect()
        await asyncio.gather(sync, return_exceptions=True)
    rig.run(body)
    assert [m for m in rig.logs() if "removed on the server" in m] == []
    assert not [m for m in rig.of("capture") if m["monitor"] and m["monitor"]["gone"]]


def test_a_save_from_a_dropped_connection_leaves_the_new_connections_entry(rig: Rig) -> None:
    rig.server.add(7)

    def reconnect_and_resave() -> Fetched:
        rig.s._reset_capture_state()                         # what connect() does
        rig.s._saving.add(7)                                 # the new connection is saving the same id
        return Fetched(pkts(2), 0, 100.0)
    rig.server.queue(7, reconnect_and_resave)

    async def body(s: TrexSession) -> None:
        await s.recorder_save(7)
        assert s._saving == {7}
    rig.run(body)


def test_monitor_stop_re_checks_the_connection_after_the_decode(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    other = StubClient()
    rig.server.queue(1, Fetched(pkts(2), 0, 100.0))
    real = capture_mod.decode_rows

    def decode_then_reconnect(rows: Any, start_ts: float) -> Any:
        rig.s._reader = other
        return real(rows, start_ts)

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        monkeypatch.setattr(capture_mod, "decode_rows", decode_then_reconnect)
        with refused("not_connected"):
            await s.monitor_stop()
        assert rig.server.n("remove") == 0
        s._reader = rig.r
    rig.run(body)


def test_a_spool_write_error_during_save_is_a_shaped_spool_error_and_keeps_the_partial(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(50), 50, 100.0), Fetched(pkts(50), 0, 100.0))
    real = capture_mod.CaptureCommands._write_pkts
    calls: list[int] = []

    def flaky(writer: Any, f: Fetched, start_ts: float) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        real(writer, f, start_ts)
    monkeypatch.setattr(capture_mod.CaptureCommands, "_write_pkts", staticmethod(flaky))

    async def body(s: TrexSession) -> None:
        with refused("spool_error") as e:
            await s.recorder_save(7)
        assert "No space left on device" in e.value.message
        assert [(f["partial"], f["pkt_count"]) for f in Spool(rig.spool_dir).list()] == [(True, 50)]
    rig.run(body)
    assert rig.server.n("remove") == 0


def test_a_sidecar_failure_inside_the_save_error_handler_is_still_a_shaped_spool_error(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(50), 50, 100.0), Fetched(pkts(50), 0, 100.0))
    real = capture_mod.CaptureCommands._write_pkts
    calls: list[int] = []

    def flaky(writer: Any, f: Fetched, start_ts: float) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        real(writer, f, start_ts)
    monkeypatch.setattr(capture_mod.CaptureCommands, "_write_pkts", staticmethod(flaky))

    real_sidecar = Spool._write_sidecar

    def no_sidecar(self: Spool, name: str, key: CaptureKey, count: int, partial: bool) -> Any:
        if count > 0:                       # writer() wrote the first one; suspend()'s is the one that fails
            raise OSError(28, "No space left on device (sidecar)")
        return real_sidecar(self, name, key, count, partial)
    monkeypatch.setattr(Spool, "_write_sidecar", no_sidecar)

    async def body(s: TrexSession) -> None:
        with refused("spool_error") as e:
            await s.recorder_save(7)
        assert "No space left on device" in e.value.message
        assert s.spool().held() == frozenset()                  # the fd and the hold were released first
        (part,) = [p for p in rig.spool_dir.iterdir() if p.name.endswith(".partial.pcap")]
        assert records(part) == 50
    rig.run(body)
    m = rig.of("capture_save")[-1]
    assert m["file"] is None and "No space left on device" in m["error"]
    assert "sidecar was not written" in m["error"]
    # the GUI log, not only the module's
    assert any("sidecar was not written" in x for x in rig.logs())
    assert rig.server.n("remove") == 0


def test_a_monitor_start_finishing_while_disconnect_waits_does_not_restart_the_loop(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.server.start_delay = 0.2
    started: list[int] = []
    real = capture_mod.CaptureCommands._capture_loop

    async def counting(self: Any) -> None:
        started.append(1)
        await real(self)
    monkeypatch.setattr(capture_mod.CaptureCommands, "_capture_loop", counting)

    async def body(s: TrexSession) -> None:
        start = asyncio.ensure_future(s.monitor_start([], [1], ""))
        await rig.until(lambda: rig.server.n("start") == 1)
        await s.disconnect()                    # past _stop_capture_loop, waiting for the start lock
        await asyncio.gather(start, return_exceptions=True)
        await asyncio.sleep(0)
    rig.run(body)
    assert started == [] and rig.server.rows == {}


def test_a_retry_resumes_the_partial_whose_suspend_sidecar_failed(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(50), 50, 100.0), Fetched(pkts(50), 0, 100.0))
    real_write = capture_mod.CaptureCommands._write_pkts
    real_sidecar = Spool._write_sidecar
    state = {"calls": 0, "full": True}

    def flaky(writer: Any, f: Fetched, start_ts: float) -> None:
        state["calls"] += 1
        if state["calls"] == 2:
            raise OSError(28, "No space left on device")
        real_write(writer, f, start_ts)

    def sidecar(self: Spool, name: str, key: CaptureKey, count: int, partial: bool) -> Any:
        if state["full"] and count > 0:
            raise OSError(28, "No space left on device (sidecar)")
        return real_sidecar(self, name, key, count, partial)
    monkeypatch.setattr(capture_mod.CaptureCommands, "_write_pkts", staticmethod(flaky))
    monkeypatch.setattr(Spool, "_write_sidecar", sidecar)

    async def body(s: TrexSession) -> None:
        with refused("spool_error"):
            await s.recorder_save(7)
        state["full"] = False
        rig.server.queue(7, Fetched(pkts(50), 0, 100.0))
        f = await s.recorder_save(7)
        assert f["pkt_count"] == 100 and f["partial"] is False
        assert len(Spool(rig.spool_dir).list()) == 1                # one file: resumed, not split in two
    rig.run(body)
    done = [m for m in rig.of("capture_save") if m["file"]]
    assert "note" not in done[0]                                    # no "another capture" claim


def test_an_unreadable_matched_partial_is_left_in_place_with_a_note(rig: Rig) -> None:
    sp = Spool(rig.spool_dir)
    w = sp.writer(CaptureKey("h:4501", 100.0, 7))
    w.write(0.0, base64.b64decode(UDP), 60)
    old = w.suspend()
    path = sp.path(old["name"])
    raw = bytearray(path.read_bytes())
    raw[0:4] = b"\x00\x00\x00\x00"
    path.write_bytes(bytes(raw))
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(2, 100.5), 0, 100.0))

    async def body(s: TrexSession) -> None:
        f = await s.recorder_save(7)
        assert f["name"] != old["name"] and f["pkt_count"] == 2
    rig.run(body)
    assert path.read_bytes() == bytes(raw)                          # untouched
    done = [m for m in rig.of("capture_save") if m["file"]]
    assert done[0]["note"] == (
        f"the partial file {old['name']} for capture 7 is unreadable and was left in place")


def test_a_start_ts_mismatch_removes_the_fresh_empty_partial_and_says_packets_were_taken(rig: Rig) -> None:
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(3), 0, 999.0))

    def peeking(c: Any, cid: int, limit: int, snaplen: int = 0) -> Fetched:
        if limit == 0:
            return Fetched([], 3, 100.0)
        return rig.server.fetch(c, cid, limit, snaplen)
    rig.mp.setattr(trexapi, "capture_fetch_once", peeking)

    async def body(s: TrexSession) -> None:
        with pytest.raises(TrexApiError, match="up to 3 packets were taken from the server and not written"):
            await s.recorder_save(7)
    rig.run(body)
    assert [p.name for p in rig.spool_dir.iterdir()] == []


def test_a_start_ts_mismatch_keeps_a_resumed_partial(rig: Rig) -> None:
    sp = Spool(rig.spool_dir)
    w = sp.writer(CaptureKey("h:4501", 100.0, 7))
    w.write(0.0, base64.b64decode(UDP), 60)
    old = w.suspend()
    rig.server.add(7)
    rig.server.queue(7, Fetched(pkts(3), 0, 999.0))

    def peeking(c: Any, cid: int, limit: int, snaplen: int = 0) -> Fetched:
        if limit == 0:
            return Fetched([], 3, 100.0)
        return rig.server.fetch(c, cid, limit, snaplen)
    rig.mp.setattr(trexapi, "capture_fetch_once", peeking)

    async def body(s: TrexSession) -> None:
        with pytest.raises(TrexApiError, match="taken from the server"):
            await s.recorder_save(7)
        assert [(f["name"], f["pkt_count"]) for f in Spool(rig.spool_dir).list()] == [(old["name"], 1)]
    rig.run(body)


def test_wireshark_is_fed_stopped_with_the_monitor_and_killed_by_close(
        rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 9: frames reach the pipe with ts relative to the monitor's start_ts; monitor_stop closes the pipe
    and leaves the window; close() kills it. The stub "wireshark" cats the fifo, then stays like a window."""
    out = tmp_path / "ws.pcap"
    exe = tmp_path / "wireshark"
    exe.write_text(f'#!/bin/sh\ncat "$3" > "{out}"\nexec sleep 30\n')
    exe.chmod(0o755)
    monkeypatch.setenv("DISPLAY", ":0")
    rig.server.queue(1, Fetched(pkts(2, ts=100.5), 0, 100.0))
    frame = base64.b64decode(UDP)
    size = 24 + 2 * (16 + len(frame))

    async def body(s: TrexSession) -> None:
        with refused("no_monitor"):
            await s.wireshark_start(str(exe))
        await s.monitor_start([], [1], "")
        # before the loop's first fetch: no await in between
        await s.wireshark_start(str(exe))
        pipe = s._ws_pipe
        assert pipe is not None and pipe.pid is not None
        assert s.capture_snapshot()["wireshark"] == {"running": True, "error": None}
        await rig.until(lambda: out.exists() and out.stat().st_size == size)
        sec, usec = struct.unpack_from("<II", out.read_bytes(), 24)
        assert (sec, usec) == (0, 500000)
        await s.monitor_stop()
        assert s._ws_pipe is None and rig.of("capture")[-1]["wireshark"] == {"running": False, "error": None}
        await rig.until(lambda: pipe.fifo is not None and not pipe.fifo.parent.exists())
        os.kill(pipe.pid, 0)                               # the window stays after the monitor stops
        await s.close()
        with pytest.raises(ProcessLookupError):
            os.kill(pipe.pid, 0)
    rig.run(body)


def ws_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str = "exec sleep 30") -> str:
    """A stub "wireshark" (called as `<stub> -k -i <fifo>`) and a display, so the pre-flight passes."""
    exe = tmp_path / "wireshark"
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(0o755)
    monkeypatch.setenv("DISPLAY", ":0")
    return str(exe)


def test_wireshark_start_is_refused_while_lost_and_after_close(rig: Rig, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    exe = ws_stub(tmp_path, monkeypatch)

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        # a LOST session keeps its monitor; no loop feeds it
        s._state = SessionState.LOST
        with refused("not_connected"):
            await s.wireshark_start(exe)
        assert s._ws_pipe is None
        s._state = SessionState.CONNECTED
        await s.close()
        # close() has taken its pipes: none may start after it
        with refused("not_connected"):
            await s.wireshark_start(exe)
        assert s._ws_pipe is None and s._ws_left == []
    rig.run(body)


def test_a_new_monitor_ends_the_old_pipe(rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every path that clears the monitor ends the pipe; monitor_start ends any pipe still attached as well,
    so a pipe can never be fed by a monitor it was not started for. Clearing `_monitor` directly reaches that
    guard."""
    exe = ws_stub(tmp_path, monkeypatch)

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await s.wireshark_start(exe)
        old = s._ws_pipe
        assert old is not None and old.running
        s._monitor = None
        await s.monitor_start([], [1], "")
        assert s._ws_pipe is None and old in s._ws_left
        await rig.until(lambda: not old.running)           # stop() runs off the loop
        assert old.process_alive                           # the window stays until close()
    rig.run(body)


def test_a_monitor_gone_from_the_server_ends_the_pipe(rig: Rig, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    exe = ws_stub(tmp_path, monkeypatch)

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await s.wireshark_start(exe)
        pipe = s._ws_pipe
        assert pipe is not None
        del rig.server.rows[1]                             # the next fetch says "capture ID not found"
        await rig.until(lambda: s._monitor is None)
        assert s._ws_pipe is None
        await rig.until(lambda: not pipe.running)
        assert pipe.process_alive
        gone = next(m for m in rig.of("capture") if m["monitor"] and m["monitor"]["gone"])
        assert gone["wireshark"] == {"running": False, "error": None}
    rig.run(body)


def test_disconnect_leaves_the_wireshark_window_open(rig: Rig, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec 6.4: a disconnect stops the pipe and leaves the window; close() kills it. The stub opens the
    pipe, as Wireshark does: a stop always lets the writer finish, so it waits for a reader."""
    exe = ws_stub(tmp_path, monkeypatch, 'cat "$3" > /dev/null; exec sleep 30')

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await s.wireshark_start(exe)
        pipe = s._ws_pipe
        assert pipe is not None and pipe.pid is not None and pipe.fifo is not None
        fifo = pipe.fifo
        await s.disconnect()
        assert s._ws_pipe is None
        await rig.until(lambda: not pipe.running)          # stop() runs off the loop
        await rig.until(lambda: not fifo.parent.exists())  # the writer closed the fifo and removed its dir
        os.kill(pipe.pid, 0)                               # the window is still there
        await s.close()
        with pytest.raises(ProcessLookupError):
            os.kill(pipe.pid, 0)
    rig.run(body)


def test_a_pipe_that_ends_on_its_own_is_published_with_its_error(rig: Rig, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    exe = ws_stub(tmp_path, monkeypatch, "exit 0")       # exits without opening the fifo

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await s.wireshark_start(exe)
        await rig.until(lambda: rig.of("capture")[-1]["wireshark"]["error"] is not None)
        assert rig.of("capture")[-1]["wireshark"] == {"running": False,
                                                      "error": "Wireshark exited before it opened the pipe"}
        # the writer sets the error before the loop runs _wireshark_ended (log, then publish), so another
        # publish can carry the error first: wait for the log line rather than expect it already there
        await rig.until(lambda: "Wireshark: Wireshark exited before it opened the pipe" in rig.logs())
        assert s._monitor is not None                      # the monitor keeps running
    rig.run(body)


def test_dropped_frames_are_logged_when_the_pipe_is_stopped(rig: Rig, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    exe = ws_stub(tmp_path, monkeypatch)                  # never opens the fifo: nothing drains the queue
    monkeypatch.setattr(wireshark_mod, "QUEUE_MAX", 1)
    rig.server.queue(1, Fetched(pkts(3), 0, 100.0))

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "")
        await s.wireshark_start(exe)
        await rig.until(lambda: bool(rig.of("capture_pkts")))
        pipe = s._ws_pipe
        assert pipe is not None and pipe.dropped == 2
        await s.wireshark_stop()
        await s.wireshark_stop()                           # nothing left to stop: no second line
        lines = [m for m in rig.logs() if "fell behind" in m]
        assert lines == ["Wireshark fell behind: 2 packets were not sent to it (its queue was full)."]
        assert "dropped" not in rig.of("capture")[-1]["wireshark"]
    rig.run(body)
