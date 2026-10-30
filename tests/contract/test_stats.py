"""The 1 s stats loop (P3-min Task 4) against the stateful fake. Stats tests stay on pps-family multipliers:
the fake kills itself on a % or bps multiplier when stats are requested."""
import asyncio
import itertools
import time

import pytest
from helpers import Recorder, wait_for
from support import FIXTURES
from test_control import _bodies
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams
from trex_web_gui.trexapi import (
    MAX_PGIDS,
    RpcRecord,
    TrexApiError,
    loss_bound,
    make_client,
    read_pgid_stats,
)

STATS_METHODS = ("get_global_stats", "get_port_stats")


def params(f, **kw):
    """ConnectParams for fake `f`; `timeout_s` in `kw` (default 2.0) is the timeout, any other key goes to
    ConnectParams."""
    return ConnectParams(f.host, f.rpc_port, f.pub_port, kw.pop("timeout_s", 2.0), **kw)


def stats_msgs(rec):
    """Every "stats" message `rec` recorded, in order."""
    return [m for m in rec.msgs if m["type"] == "stats"]


def test_stats_published_every_interval(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(f))
        await wait_for(lambda: len(stats_msgs(rec)) >= 1, 2)
        n0, t0 = len(stats_msgs(rec)), time.monotonic()
        await wait_for(lambda: time.monotonic() - t0 >= 1.0, 2)
        got = stats_msgs(rec)
        assert len(got) - n0 >= 5, len(got) - n0
        assert all(b["ts"] > a["ts"] for a, b in itertools.pairwise(got))
        assert {m["interval_s"] for m in got} == {0.1}
        assert set(got[-1]) == {"type", "ts", "interval_s", "global", "ports", "total", "stats_epoch"}
        assert set(got[-1]["ports"]) == {"0", "1"}
    new_session.run(run())


def test_stats_failure_marks_lost(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(f))
        await wait_for(lambda: stats_msgs(rec), 2)
        f.stop()
        await wait_for(lambda: s.snapshot()["state"] == "lost", loss_bound(2.0))
        assert s.snapshot()["error"] and (
            "subscriber timeout" in s.snapshot()["error"]
            or "Cannot reach the TRex server" in s.snapshot()["error"]
        ), s.snapshot()["error"]
        lost_at = max(i for i, m in enumerate(rec.msgs) if m["type"] == "session" and m["state"] == "lost")
        await asyncio.sleep(0.5)
        assert [m for m in rec.msgs[lost_at:] if m["type"] == "stats"] == []
    new_session.run(run())


def test_repeating_stats_failure_logs_once_per_streak(fake_trex, new_session, monkeypatch):
    real, calls = trexapi.read_stats, []

    def flaky(c, interval_s):
        calls.append(1)
        if len(calls) in (1, 2, 3, 5, 6):                      # fail x3, succeed, fail x2
            raise TrexApiError("stats-boom")
        return real(c, interval_s)
    monkeypatch.setattr(trexapi, "read_stats", flaky)
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(f))
        await wait_for(lambda: len(calls) >= 8, 5)
        warnings = [m for m in rec.msgs if m["type"] == "log" and "stats-boom" in m["message"]]
        assert len(warnings) == 2, [m["message"] for m in warnings]    # one per streak, not one per tick
        assert s.snapshot()["state"] == "connected" and stats_msgs(rec)
    new_session.run(run())


def test_stop_waits_at_most_one_batch(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(f))
        await s.acquire([1])
        await s.assign_profile(
            [1], await trexapi.load_profile(str(FIXTURES / "profiles" / "gui_example.yaml"), [1], [])
        )
        took = []
        for _ in range(5):
            await s.start([1], "10kpps")
            await wait_for(lambda: len(stats_msgs(rec)) >= 2, 2)
            t = time.monotonic()
            await s.stop([1])
            took.append(time.monotonic() - t)
        assert max(took) < 2.0, took                             # timeout_s of params()
        # the ledger records it
        print(f"stop_latency_s max={max(took):.3f} all={[round(x, 3) for x in took]}")
    new_session.run(run())


def test_read_only_session_receives_stats(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(f, read_only=True))
        await wait_for(lambda: len(stats_msgs(rec)) >= 2, 3)
    new_session.run(run())


def test_console_hides_real_stats_polls(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    def published():
        return [b for m in rec.msgs if m.get("type") == "rpc" and m["direction"] == "request"
                for b in _bodies(m) if b.get("method") in STATS_METHODS]
    async def run():
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(f))
        s.set_rpc_console(True, include_polling=False)
        n = len(stats_msgs(rec))
        await asyncio.sleep(1.0)
        assert len(stats_msgs(rec)) - n >= 5                     # the loop really ran its batches meanwhile
        assert published() == []
        s.set_rpc_console(True, include_polling=True)
        await wait_for(lambda: published(), 2)
    new_session.run(run())


def kind_msgs(rec, kind):
    """Every message of type `kind` that `rec` recorded, in order."""
    return [m for m in rec.msgs if m["type"] == kind]


def test_pgid_and_util_published_every_interval(fake_trex, new_session, foreign_client):
    f, rec = fake_trex(), Recorder()
    # a stream with a pg_id on the server, running
    other = foreign_client(f, user="bench")
    from trex.stl.api import (
        IP,
        UDP,
        Ether,
        STLFlowStats,
        STLPktBuilder,
        STLStream,
        STLTXCont,
    )
    other.acquire(ports=[0], force=True)
    other.add_streams(
        STLStream(
            packet=STLPktBuilder(pkt=Ether() / IP() / UDP() / ("x" * 18)),
            mode=STLTXCont(pps=100),
            flow_stats=STLFlowStats(pg_id=5),
        ),
        ports=[0],
    )
    other.start(ports=[0], mult="100pps")
    async def run():
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(f))
        await wait_for(lambda: kind_msgs(rec, "pgid") and kind_msgs(rec, "util"), 2)
        n0 = {k: len(kind_msgs(rec, k)) for k in ("pgid", "util")}
        t0 = time.monotonic()
        await wait_for(lambda: time.monotonic() - t0 >= 1.0, 2)
        for kind in ("pgid", "util"):
            got = kind_msgs(rec, kind)
            assert len(got) - n0[kind] >= 5, (kind, len(got) - n0[kind])
            assert all(b["ts"] > a["ts"] for a, b in itertools.pairwise(got))
        pg, ut = kind_msgs(rec, "pgid")[-1], kind_msgs(rec, "util")[-1]
        assert set(pg) == {"type", "ts", "interval_s", "active", "truncated", "ver_id", "flow", "latency",
                           "flow_global"}
        assert (pg["interval_s"] == 0.1 and pg["active"] == {"flow_stats": [5], "latency": []}
                and set(pg["flow"]) == {"5"})
        assert pg["flow"]["5"]["tx_pps"]["0"] == 100 and pg["truncated"] is False
        assert set(ut) == {"type", "ts", "cpu", "mbuf"} and ut["cpu"][0]["history"][:4] == [4, 3, 1, 0]
    new_session.run(run())


def test_read_only_session_receives_pgid_and_util(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.1)
        await s.connect(params(f, read_only=True))
        await wait_for(lambda: len(kind_msgs(rec, "pgid")) >= 2 and len(kind_msgs(rec, "util")) >= 2, 3)
    new_session.run(run())


def test_pgid_failure_logs_once_per_streak(fake_trex, new_session, monkeypatch):
    real, calls = trexapi.read_pgid_stats, []

    def flaky(c, interval_s):
        calls.append(1)
        if len(calls) in (1, 2, 3, 5, 6):                      # fail x3, succeed, fail x2
            raise TrexApiError("pgid-boom")
        return real(c, interval_s)
    monkeypatch.setattr(trexapi, "read_pgid_stats", flaky)
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(f))
        await wait_for(lambda: len(calls) >= 8, 5)
        warnings = [m for m in rec.msgs if m["type"] == "log" and "pgid-boom" in m["message"]]
        assert len(warnings) == 2, [m["message"] for m in warnings]    # one per streak, not one per tick
        assert s.snapshot()["state"] == "connected" and kind_msgs(rec, "pgid")
        # the other reads were not held up
        assert len(stats_msgs(rec)) >= 6 and len(kind_msgs(rec, "util")) >= 6
        assert [m for m in rec.msgs if m["type"] == "log" and "Stats read" in m["message"]] == []
    new_session.run(run())


def test_connect_seq_changes_per_connect_and_is_in_the_connecting_snapshot(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.1)
        before = s.snapshot()["connect_seq"]
        await s.connect(params(f))
        first = s.snapshot()["connect_seq"]
        connecting = next(m for m in rec.msgs if m["type"] == "session" and m["state"] == "connecting")
        # the CONNECTING snapshot already carries the new value
        assert connecting["connect_seq"] == first != before
        await s.disconnect()
        assert s.snapshot()["connect_seq"] == first                  # a disconnect is not a connect
        await s.connect(params(f))
        second = s.snapshot()["connect_seq"]
        assert second != first
        prefix, n = second.split(":")
        assert prefix == first.split(":")[0] and prefix.isdigit() and int(n) == int(first.split(":")[1]) + 1
        assert all(m["connect_seq"] in (before, first, second) for m in rec.msgs if m["type"] == "session")
    new_session.run(run())


def test_stats_epoch_bumps_on_reset(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(f))
        await s.acquire([0])
        await wait_for(lambda: stats_msgs(rec), 2)
        assert {m["stats_epoch"] for m in stats_msgs(rec)} == {0}
        await s.reset([0])
        n = len(stats_msgs(rec))
        await wait_for(lambda: len(stats_msgs(rec)) >= n + 2, 2)
        assert stats_msgs(rec)[-1]["stats_epoch"] == 1
        epochs = [m["stats_epoch"] for m in stats_msgs(rec)]
        assert epochs == sorted(epochs) and set(epochs) == {0, 1}      # never back, bumped once
        await s.reset([0])
        await wait_for(lambda: stats_msgs(rec)[-1]["stats_epoch"] == 2, 2)
    new_session.run(run())


def test_stats_epoch_bumps_even_when_reset_fails_after_clearing_stats(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec, stats_s=0.05)
        await s.connect(params(f))
        await s.acquire([0])
        await wait_for(lambda: stats_msgs(rec), 2)
        def boom(*a, **kw):
            raise TrexApiError("service-boom")
        s._client.set_service_mode = boom                # the last step of STLClient.reset, after clear_stats
        with pytest.raises(TrexApiError, match="service-boom"):
            await s.reset([0])
        await wait_for(lambda: stats_msgs(rec)[-1]["stats_epoch"] == 1, 2)
    new_session.run(run())


def _many_pgids(fake_trex, foreign_client, count):
    """Start a fake where a foreign client "bench" owns port 0 (forced) and holds `count` continuous 1 pps
    streams with pg_ids 1..`count`, added but not started; returns the fake."""
    f = fake_trex()
    other = foreign_client(f, user="bench")
    from trex.stl.api import (
        IP,
        UDP,
        Ether,
        STLFlowStats,
        STLPktBuilder,
        STLStream,
        STLTXCont,
    )
    other.acquire(ports=[0], force=True)
    pkt = STLPktBuilder(pkt=Ether()/IP()/UDP()/("x" * 18))
    other.add_streams([STLStream(packet=pkt, mode=STLTXCont(pps=1), flow_stats=STLFlowStats(pg_id=i))
                       for i in range(1, count + 1)], ports=[0])
    return f


def test_read_pgid_stats_caps_at_one_request(fake_trex, foreign_client):
    for count in (2000, 1152):
        f = _many_pgids(fake_trex, foreign_client, count)
        recs: list[RpcRecord] = []
        c = make_client(f.host, f.rpc_port, f.pub_port, 2.0, lambda ev: None, on_rpc=recs.append)
        c.connect()
        try:
            recs.clear()
            view = read_pgid_stats(c, 1.0)
        finally:
            c.disconnect()
        asked = [
            b
            for r in recs
            if r.direction == "request"
            for b in (r.body if isinstance(r.body, list) else [r.body])
            if isinstance(b, dict) and b.get("method") == "get_pgid_stats"
        ]
        assert len(asked) == 1, (count, len(asked))
        # ascending; never [] (= "all")
        assert asked[0]["params"]["pgids"] == list(range(1, MAX_PGIDS + 1)), count
        assert view["truncated"] is True and len(view["flow"]) == MAX_PGIDS
        assert view["active"]["flow_stats"] == list(range(1, count + 1))


def test_read_pgid_stats_at_exactly_the_cap_is_not_truncated(fake_trex, foreign_client):
    f = _many_pgids(fake_trex, foreign_client, MAX_PGIDS)
    recs: list[RpcRecord] = []
    c = make_client(f.host, f.rpc_port, f.pub_port, 2.0, lambda ev: None, on_rpc=recs.append)
    c.connect()
    try:
        view = read_pgid_stats(c, 1.0)
    finally:
        c.disconnect()
    assert view["truncated"] is False and len(view["flow"]) == MAX_PGIDS
