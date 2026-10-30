"""The reader client's lifecycle (P3b follow-up 3, Task 1): a second, read-only client beside the control
one."""
import asyncio
import contextlib
import gc
import itertools
import json
import signal
import threading
import time

import pytest
import zmq
from helpers import Recorder, wait_for
from test_control import loaded
from test_session import params
from trex_web_gui import trexapi
from trex_web_gui.sessions import SessionState
from trex_web_gui.trexapi import (
    ClientEvent,
    CommandRefused,
    ServerUnreachable,
    TrexApiError,
)


def record_control(s) -> list:
    """Wrap _connect_blocking so the test can look at the control client it returned."""
    got: list = []
    real = s._connect_blocking

    # wrapped(): the real _connect_blocking, keeping the control client it returns in `got`
    def wrapped(p):
        c, info = real(p)
        got.append(c)
        return c, info
    s._connect_blocking = wrapped
    return got


def test_connect_opens_a_reader_that_never_acquires(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        s.set_rpc_console(True, include_polling=True)
        await s.connect(params(fake_trex()))
        await s.acquire([0, 1])
        assert s._reader is not None and s._reader is not s._client
        await asyncio.sleep(0.3)
        rpcs = [m for m in rec.msgs if m["type"] == "rpc" and m["thread"].startswith("trex-reader")]
        assert rpcs                                      # the handshake at least
        for m in rpcs:
            for el in m["body"] if isinstance(m["body"], list) else [m["body"]]:
                if isinstance(el, dict) and el.get("method"):
                    assert el["method"] != "acquire", el
                    assert not (el.get("params") or {}).get("handler"), el
        assert not s._reader.ports[0].is_acquired()
        assert s._client.ports[0].is_acquired()
    new_session.run(run())


def test_reader_connect_failure_disconnects_both(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        recorded = record_control(s)

        def boom(p):
            raise ServerUnreachable("x")
        s._connect_reader_blocking = boom
        with pytest.raises(ServerUnreachable):
            await s.connect(params(fake_trex()))
        assert s._state == SessionState.DISCONNECTED
        assert s._client is None and s._reader is None
        assert not recorded[0].conn.is_connected()
    new_session.run(run())


def test_a_failed_connect_is_logged_to_events_on_both_branches(fake_trex, new_session) -> None:
    """ASTF-1: a connect that fails reaches Events as an error log, whichever client failed."""
    async def run() -> None:
        f = fake_trex()
        for which in ("_connect_blocking", "_connect_reader_blocking"):
            rec = Recorder()
            s = new_session(rec)

            def boom(p):
                raise TrexApiError("refused for the test")
            setattr(s, which, boom)
            with pytest.raises(TrexApiError):
                await s.connect(params(f))
            logs = [m for m in rec.msgs if m["type"] == "log" and m["level"] == "error"]
            want = f"Connect to {f.host}:{f.rpc_port} failed: refused for the test"
            assert [m["message"] for m in logs] == [want], which
    new_session.run(run())


def test_cancel_during_reader_connect_releases_both(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        recorded = record_control(s)
        entered, release = threading.Event(), threading.Event()
        real = s._connect_reader_blocking

        def slow(p):
            entered.set()
            release.wait(10)
            return real(p)
        s._connect_reader_blocking = slow
        task = asyncio.create_task(s.connect(params(fake_trex())))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s._state == SessionState.DISCONNECTED and s._error == "Connect cancelled."
        assert s._client is None and s._reader is None
        assert not recorded[0].conn.is_connected()
    new_session.run(run())


def test_reader_lost_during_connect_never_reports_connected(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        real = s._connect_reader_blocking

        def lossy(p):
            r = real(p)
            s._on_reader_event(ClientEvent("subscriber crashed", [], "reader-x", time.time()))
            return r
        s._connect_reader_blocking = lossy
        with pytest.raises(trexapi.TrexApiError):
            await s.connect(params(fake_trex()))
        assert s._state == SessionState.LOST
        assert "reader-x" in str(s._error)
        assert "connected" not in rec.states()
        assert any(m["type"] == "log" and "Reader: reader-x" in m["message"] for m in rec.msgs)
        assert "subscriber crashed" not in rec.events()
    new_session.run(run())


def test_disconnect_and_reconnect_replace_the_reader(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        p = params(fake_trex())
        await s.connect(p)
        first = s._reader
        assert first is not None
        await s.disconnect()
        assert s._reader is None
        await s.connect(p)
        assert s._reader is not None and s._reader is not first
    new_session.run(run())


def test_close_shuts_down_both_executors(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        await s.connect(params(fake_trex()))
        await s.close()
        assert s._executor._shutdown and s._reader_executor._shutdown
    new_session.run(run())


def test_cancel_during_control_disconnect_after_reader_failure_ends_disconnected(
    fake_trex, new_session
) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        recorded = record_control(s)
        entered, release = threading.Event(), threading.Event()
        real = s._disconnect_blocking

        def boom(p):
            raise ServerUnreachable("x")

        def slow(c, release_ports=True):
            if c is recorded[0]:
                entered.set()
                release.wait(10)
            return real(c, release_ports)
        s._connect_reader_blocking = boom
        s._disconnect_blocking = slow
        task = asyncio.create_task(s.connect(params(fake_trex())))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s._state == SessionState.DISCONNECTED       # set by the finally, before the job finished
        await wait_for(lambda: not recorded[0].conn.is_connected(), 5)
    new_session.run(run())


def test_real_reader_connect_failure_disconnects_the_reader_without_release(
        fake_trex, new_session, monkeypatch) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        recorded = record_control(s)
        made: list = []
        real_make = trexapi.make_client

        def make(*a, **kw):
            c = real_make(*a, **kw)
            made.append(c)
            if len(made) == 2:
                def fail():
                    raise ServerUnreachable("reader connect failed")
                c.connect = fail
            return c
        monkeypatch.setattr(trexapi, "make_client", make)
        calls: list = []
        real = s._disconnect_blocking

        def spy(c, release=True):
            calls.append((c, release))
            return real(c, release)
        s._disconnect_blocking = spy
        with pytest.raises(ServerUnreachable):
            await s.connect(params(fake_trex()))
        assert (made[1], False) in calls
        assert s._state == SessionState.DISCONNECTED and s._client is None and s._reader is None
        assert not recorded[0].conn.is_connected()
    new_session.run(run())


# -- Task 2: the stats loop on the reader; Reset clears both -----------------------------------------


def stats_msgs(rec: Recorder) -> list:
    """Every "stats" message `rec` recorded, in order."""
    return [m for m in rec.msgs if m["type"] == "stats"]


def test_stats_keep_flowing_while_the_control_executor_is_held(fake_trex, new_session) -> None:
    async def run() -> None:
        stamps: list[float] = []

        def publish(m: dict) -> None:
            if m["type"] == "stats":
                stamps.append(time.monotonic())
        s = new_session(publish, stats_s=0.2)
        await s.connect(params(fake_trex()))
        await wait_for(lambda: len(stamps) >= 2, 3)
        t0 = time.monotonic()
        await s._run(time.sleep, 2.0)                    # holds the control executor
        t1 = time.monotonic()
        inside = [t for t in stamps if t0 <= t <= t1]
        edges = [t0, *inside, t1]
        worst = max(b - a for a, b in itertools.pairwise(edges))
        print(f"max_gap_s={worst:.3f} stats_in_hold={len(inside)}")
        assert worst < 1.0
    new_session.run(run())


def test_stats_keep_flowing_under_delay(fake_trex, new_session) -> None:
    """The same hold with a 100 ms link. One reader tick is at most stats_s + 4 reader RPCs * D =
    0.2 + 4 * 0.1 = 0.6 s, and each edge gap is at most one tick; the remaining 0.2 s of the 0.8 s bound is
    slack."""
    async def run() -> None:
        stamps: list[float] = []

        def publish(m: dict) -> None:
            if m["type"] == "stats":
                stamps.append(time.monotonic())
        s = new_session(publish, stats_s=0.2)
        await s.connect(params(fake_trex("--delay-ms", "100")))
        await wait_for(lambda: len(stamps) >= 2, 5)
        t0 = time.monotonic()
        await s._run(time.sleep, 2.0)                    # holds the control executor
        t1 = time.monotonic()
        inside = [t for t in stamps if t0 <= t <= t1]
        edges = [t0, *inside, t1]
        worst = max(b - a for a, b in itertools.pairwise(edges))
        print(f"max_gap_s={worst:.3f} stats_in_hold={len(inside)}")
        assert worst < (0.2 + 4 * 0.1) + 0.2   # one tick, plus slack
    new_session.run(run())


def test_reader_records_stay_reads(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec, stats_s=0.1)
        s.set_rpc_console(True, include_polling=True)
        await s.connect(params(fake_trex()))
        await wait_for(lambda: len(stats_msgs(rec)) >= 1, 3)
        await asyncio.sleep(0.3)                         # the handshake's records have landed
        start = len(rec.msgs)
        await wait_for(lambda: len(stats_msgs(rec)) >= 5, 3)
        await asyncio.sleep(0.2)
        methods = []
        for i, m in enumerate(rec.msgs):
            if m["type"] == "rpc" and m["thread"].startswith("trex-reader"):
                for el in m["body"] if isinstance(m["body"], list) else [m["body"]]:
                    if isinstance(el, dict) and el.get("method"):
                        methods.append((i >= start, el["method"]))
                        assert el["method"] != "acquire", el
                        assert not (el.get("params") or {}).get("handler"), el
        later = [name for after, name in methods if after]
        print(f"reader reads after the handshake={len(later)}")
        assert any(n in ("get_global_stats", "get_port_stats") for n in later), methods
    new_session.run(run())


async def start_traffic(s) -> None:
    """Acquire port 0 on session `s`, assign the profile (packet_len 128) to it and start it at 10kpps."""
    await s.acquire([0])
    await s.assign_profile([0], await loaded([0]))
    await s.start([0], "10kpps")


def test_reset_zeroes_the_readers_counters_under_the_new_epoch(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(fake_trex()))
        await start_traffic(s)
        await wait_for(lambda: any(m["ports"]["0"]["opackets"] > 0 for m in stats_msgs(rec)), 5)
        e = stats_msgs(rec)[-1]["stats_epoch"]
        nonzero = next(i for i, m in enumerate(stats_msgs(rec)) if m["ports"]["0"]["opackets"] > 0)
        await s.reset([0])
        await wait_for(lambda: stats_msgs(rec)[-1]["stats_epoch"] == e + 1, 3)
        await asyncio.sleep(0.3)
        got = stats_msgs(rec)
        first = next(i for i, m in enumerate(got) if m["stats_epoch"] == e + 1)
        assert got[first]["ports"]["0"]["opackets"] == 0, got[first]["ports"]["0"]
        assert all(m["stats_epoch"] == e + 1 for m in got[first:])
        old = [m for m in got[nonzero:first] if m["stats_epoch"] == e]
        assert old and all(m["ports"]["0"]["opackets"] > 0 for m in old)    # never zeros under the old epoch
    new_session.run(run())


def test_refused_reset_changes_neither_epoch_nor_counters(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(fake_trex()))
        await start_traffic(s)
        await wait_for(lambda: any(m["ports"]["0"]["opackets"] > 0 for m in stats_msgs(rec)), 5)
        e = s._stats_epoch
        before = stats_msgs(rec)[-1]["ports"]["0"]["opackets"]
        assert before > 0
        with pytest.raises(CommandRefused) as bad:
            await s.reset([0, 9])
        assert bad.value.code == "bad_port"
        s._set_busy(0, "ping")
        with pytest.raises(CommandRefused) as busy:
            await s.reset([0])
        assert busy.value.code == "port_busy"
        s._set_busy(0, None)
        assert s._stats_epoch == e
        n = len(stats_msgs(rec))
        await wait_for(lambda: len(stats_msgs(rec)) >= n + 3, 3)
        latest = stats_msgs(rec)[-1]
        assert latest["stats_epoch"] == e and latest["ports"]["0"]["opackets"] >= before
    new_session.run(run())


def test_a_reset_lost_in_the_control_job_runs_no_reader_job(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(fake_trex()))
        await s.acquire([0])
        e = s._stats_epoch

        def boom(*a, **kw):
            raise trexapi.ServerUnreachable("x")
        s._client.reset = boom
        with pytest.raises(trexapi.ServerUnreachable):
            await s.reset([0])
        await asyncio.sleep(0.2)
        assert s._state == SessionState.LOST
        assert s._stats_epoch == e
        assert not any(m["type"] == "log" and "Reset: reader clear failed" in m["message"] for m in rec.msgs)
    new_session.run(run())


def test_a_reader_only_loss_marks_the_session_lost(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        await s.connect(params(fake_trex()))
        s._on_reader_event(ClientEvent("subscriber timeout", [], "reader-only", time.time()))
        await wait_for(lambda: s._state == SessionState.LOST, 2)
        assert s._error == "reader-only"
        assert any(
            m["type"] == "log" and m["level"] == "error" and "Reader" in m["message"] for m in rec.msgs
        )
        assert "subscriber timeout" not in rec.events()
    new_session.run(run())


def test_a_reader_read_loss_marks_the_session_lost(fake_trex, new_session, monkeypatch) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(fake_trex()))

        real = trexapi.read_stats

        def boom(c, interval_s):
            if c is s._reader:                           # only the reader's read fails
                raise trexapi.ServerUnreachable("x")
            return real(c, interval_s)
        monkeypatch.setattr(trexapi, "read_stats", boom)
        await wait_for(lambda: s._state == SessionState.LOST, 3)
        assert s._error == "x"
    new_session.run(run())


def test_sigstop_marks_lost_once(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        f = fake_trex()
        await s.connect(params(f, 1.0))
        f.proc.send_signal(signal.SIGSTOP)
        try:
            await wait_for(lambda: s._state == SessionState.LOST, trexapi.loss_bound(1.0))
            await asyncio.sleep(0.5)
        finally:
            f.proc.send_signal(signal.SIGCONT)
        assert rec.states().count("lost") == 1
    new_session.run(run())


def test_server_restart_reported_with_its_cause(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        f = fake_trex()
        await s.connect(params(f))
        f.restart()
        await s._run(time.sleep, 2.0)
        await wait_for(lambda: rec.states()[-1] == "lost", 5)
        assert "restarted" in s.snapshot()["error"]
        assert rec.states().count("lost") == 1
    new_session.run(run())


def _timed(s, name: str, spans: dict, *, delay: float = 0.3, raises: Exception | None = None,
           after: float = 0.0) -> None:
    """Replace the blocking connect `name` on `s` with a wrapper that records spans[name] = (start, end)
    monotonic times around the real call, sleeping `delay` seconds inside. With `raises`, it sleeps `after`
    seconds, records the span and raises instead of calling the real connect."""
    real = getattr(s, name)

    # wrapped(): the timed stand-in for the connect `name`, installed on `s` below
    def wrapped(p):
        start = time.monotonic()
        if raises is not None:
            time.sleep(after)
            spans[name] = (start, time.monotonic())
            raise raises
        time.sleep(delay)
        out = real(p)
        spans[name] = (start, time.monotonic())
        return out
    setattr(s, name, wrapped)


def test_control_and_reader_connect_overlap(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        spans: dict = {}
        _timed(s, "_connect_blocking", spans, delay=1.0)
        _timed(s, "_connect_reader_blocking", spans, delay=1.0)
        t0 = time.monotonic()
        await s.connect(params(fake_trex()))
        took = time.monotonic() - t0
        (cs, ce), (rs, re_) = spans["_connect_blocking"], spans["_connect_reader_blocking"]
        assert rs < ce and cs < re_, spans               # the two intervals overlap
        assert took < 1.6, took                          # serial would be >= 2.0 s; 0.6 s of load margin
    new_session.run(run())


def test_reader_fails_while_control_still_connecting(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        recorded: list = []
        real = s._connect_blocking

        def slow_control(p):
            time.sleep(0.3)
            c, info = real(p)
            recorded.append(c)
            return c, info
        s._connect_blocking = slow_control

        def boom(p):
            raise ServerUnreachable("reader-x")
        s._connect_reader_blocking = boom
        with pytest.raises(ServerUnreachable, match="reader-x"):
            await s.connect(params(fake_trex()))
        assert s._state == SessionState.DISCONNECTED
        assert s._client is None and s._reader is None
        assert recorded and not recorded[0].conn.is_connected()     # the late control success was released
    new_session.run(run())


def test_both_fail_control_error_wins(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        logged: list[str] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: logged.append(str(ctx.get("message"))))
        spans: dict = {}
        _timed(s, "_connect_blocking", spans, raises=ServerUnreachable("control-x"), after=0.1)
        _timed(s, "_connect_reader_blocking", spans, raises=ServerUnreachable("reader-x"))
        try:
            await s.connect(params(fake_trex()))
        except ServerUnreachable as e:
            # no `as` holder survives: its traceback pins connect()'s frame
            raised = str(e)
        else:
            raise AssertionError("connect did not raise")
        assert "control-x" in raised and "reader-x" not in raised
        err = str(s.snapshot()["error"])
        assert "control-x" in err and "reader-x" not in err
        assert s._state == SessionState.DISCONNECTED
        await asyncio.sleep(0.05)
        # A future whose exception nobody retrieved has _log_traceback set; that flag is what makes asyncio
        # log "exception was never retrieved" when the future is collected. Checking it does not depend on gc
        # timing (a stale traceback can pin connect()'s frame, so the future may never be collected during the
        # test).
        unretrieved = [o for o in gc.get_objects()
                       if isinstance(o, asyncio.Future) and o.done() and getattr(o, "_log_traceback", False)]
        assert not unretrieved, unretrieved
        gc.collect()
        assert not [m for m in logged if "never retrieved" in m], logged
    new_session.run(run())


class _RawSubscriber:
    """One get_async_events subscriber played raw against the fake, as the vendored client's subscriber
    does."""

    def __init__(self, f, session_id: int) -> None:
        """Open a REQ socket (LINGER 0, 2 s receive timeout) to fake `f`'s RPC port and run the
        api_sync_v2 handshake, keeping the `api_h`; the caller close()s it."""
        self.session_id, self._rid = session_id, 0
        self.sock = zmq.Context.instance().socket(zmq.REQ)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt(zmq.RCVTIMEO, 2000)
        self.sock.connect(f"tcp://{f.host}:{f.rpc_port}")
        self.api_h = self._call("api_sync_v2", {"name": "STL", "major": 5, "minor": 1})["result"]["api_h"]

    def _call(self, method: str, params: dict) -> dict:
        """Send one JSON-RPC request with the next id and return the parsed reply; zmq.Again when none
        arrives within 2 s."""
        self._rid += 1
        self.sock.send_json({"jsonrpc": "2.0", "id": self._rid, "method": method, "params": params})
        out: dict = json.loads(self.sock.recv())
        return out

    def poll(self) -> list[int]:
        """One get_async_events for this subscriber's session id; the `seq` of each event it returns."""
        r = self._call("get_async_events", {"api_h": self.api_h, "session_id": self.session_id})
        return [ev["seq"] for ev in r["result"]["data"]]

    def gap_seqs(self) -> list[int]:
        """The event seqs from up to 10 polls, stopping once at least two have arrived."""
        seqs: list[int] = []
        for _ in range(10):
            seqs += self.poll()
            if len(seqs) >= 2:
                break
        return seqs

    def close(self) -> None:
        """Close the socket (the shared zmq context stays)."""
        self.sock.close()


def test_seq_gap_at_start_reaches_both_subscribers(fake_trex) -> None:
    f = fake_trex("--seq-gap-at-start")
    subs = [_RawSubscriber(f, 111), _RawSubscriber(f, 222)]
    try:
        assert [sub.gap_seqs() for sub in subs] == [[5, 7], [5, 7]]
    finally:
        for sub in subs:
            sub.close()


def test_armed_seq_gap_reaches_subscribers_already_seen(fake_trex) -> None:
    f = fake_trex()
    subs = [_RawSubscriber(f, 111), _RawSubscriber(f, 222)]
    try:
        assert [sub.poll() for sub in subs] == [[], []]          # both subscribed before the gap is armed
        f.arm_seq_gap()
        assert [sub.gap_seqs() for sub in subs] == [[5, 7], [5, 7]]
    finally:
        for sub in subs:
            sub.close()


def test_seq_gap_at_start_lost_once(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        with contextlib.suppress(TrexApiError):
            await s.connect(params(fake_trex("--seq-gap-at-start")))
        await asyncio.sleep(1.0)
        # <= 1, not == 1: the gap may land during the handshake, which fails the connect (DISCONNECTED, never
        # LOST), as test_loss_during_connect_never_reports_connected allows
        assert rec.states().count("lost") <= 1, rec.states()
        assert "SEQ ERROR 7 6" in str(s.snapshot()["error"])
    new_session.run(run())


def test_control_fails_reader_succeeds_releases_reader(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        spans: dict = {}
        _timed(s, "_connect_blocking", spans, raises=ServerUnreachable("control-x"), after=0.3)
        readers: list = []
        real = s._connect_reader_blocking

        def wrapped(p):
            c = real(p)
            readers.append(c)
            return c
        s._connect_reader_blocking = wrapped
        with pytest.raises(ServerUnreachable, match="control-x"):
            await s.connect(params(fake_trex()))
        assert s._state == SessionState.DISCONNECTED
        assert s._reader is None and s._client is None
        assert readers and not readers[0].conn.is_connected()
    new_session.run(run())


def test_job_cancelled_inside_connect_takes_the_cancel_path(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        recorded = record_control(s)

        def cancelled(p):
            raise asyncio.CancelledError
        s._connect_reader_blocking = cancelled
        with pytest.raises(asyncio.CancelledError):
            await s.connect(params(fake_trex()))
        assert s._state == SessionState.DISCONNECTED
        assert s._client is None and s._reader is None
        assert recorded and not recorded[0].conn.is_connected()     # the control client was released
    new_session.run(run())
