"""MonitorSubscriber and MonitorSession against the fake's --pub-replay and hostile-publisher flags."""
import asyncio
import resource
import signal
import time
from pathlib import Path

import zmq
from helpers import Recorder, wait_for
from support import FIXTURES
from trex_web_gui.monitor.session import PUBLISH_EVERY_S, MonitorSession
from trex_web_gui.monitor.subscriber import POLL_MS, STALE_AFTER_S, MonitorSubscriber
from trex_web_gui.monitor.wire import Frame
from trex_web_gui.sessions import ConnectParams

STL = str(Path(FIXTURES) / "p5" / "pub-stl.jsonl")


def last(rec: Recorder) -> dict:
    """The newest `monitor` message."""
    return [m for m in rec.msgs if m["type"] == "monitor"][-1]


def rss_mib() -> float:
    """This process's current resident set, in MiB (not the lifetime peak)."""
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * resource.getpagesize() / (1 << 20)


def test_subscriber_connects_and_dispatches(fake_trex) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL)
        rec = Recorder()
        s = MonitorSession(rec)
        try:
            await s.connect(fake.host, fake.pub_port)
            await wait_for(lambda: s.snapshot()["state"] == "connected", 3.0)
            await wait_for(lambda: s.snapshot()["global"] is not None, 3.0)
            assert "m_tx_bps" in s.snapshot()["global"]  # type: ignore[operator]
            assert s.snapshot()["alive"] is True
            assert s.snapshot()["target"] == {"host": fake.host, "port": fake.pub_port}
            assert s.snapshot()["dropped_triggers"] >= 0
        finally:
            await s.close()
    asyncio.run(run())


def test_watchdog_goes_stale_then_connected_again(fake_trex) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL)
        states: list[tuple[str, float]] = []
        got = {"t": 0.0}

        def on_frame(f: Frame) -> None:
            """Note when the last frame came."""
            got["t"] = time.monotonic()
        sub = MonitorSubscriber(fake.host, fake.pub_port, on_frame,
                                lambda st: states.append((st, time.monotonic())))
        sub.start()
        try:
            await wait_for(lambda: got["t"] > 0, 3.0)
            await asyncio.sleep(0.3)
            fake.proc.send_signal(signal.SIGSTOP)
            await wait_for(lambda: states[-1][0] == "stale", 4.0)
            stale_at, last_frame = states[-1][1], got["t"]
            fake.proc.send_signal(signal.SIGCONT)
            assert stale_at - last_frame <= STALE_AFTER_S + 0.25
            await wait_for(lambda: states[-1][0] == "connected", 3.0)
            assert [st for st, _ in states][:2] == ["connecting", "connected"]
        finally:
            fake.proc.send_signal(signal.SIGCONT)
            await sub.stop()
    asyncio.run(run())


def test_garbage_is_counted_and_survived(fake_trex) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL, "--pub-garbage")
        s = MonitorSession(Recorder())
        try:
            await s.connect(fake.host, fake.pub_port)
            await wait_for(lambda: s.snapshot()["state"] == "connected", 3.0)
            await wait_for(lambda: s.snapshot()["dropped"]["oversize"] >= 2  # type: ignore[index]
                           and s.snapshot()["dropped"]["undecodable"] >= 3, 6.0)  # type: ignore[index]
            assert s.snapshot()["state"] == "connected"
        finally:
            await s.close()
    asyncio.run(run())


def test_oversized_zmq_message_is_not_received(fake_trex) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL, "--pub-big", "5000000")
        s = MonitorSession(Recorder())
        before = peak = rss_mib()
        try:
            await s.connect(fake.host, fake.pub_port)
            end = time.monotonic() + 6.0
            while time.monotonic() < end:
                snap = s.snapshot()
                assert "big" not in snap["other"]  # type: ignore[operator]
                assert snap["dropped"]["oversize"] == 0  # type: ignore[index]
                peak = max(peak, rss_mib())
                await asyncio.sleep(0.1)
            assert s.snapshot()["frames"] > 0                       # later frames still arrive
        finally:
            await s.close()
        assert peak - before < 64
    asyncio.run(run())


def test_tx_pps_rises_within_one_tick(fake_trex) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL)
        rec = Recorder()
        s = MonitorSession(rec)
        seen: dict[str, float] = {}
        orig = s.on_frame

        def on_frame(f: Frame) -> None:
            """Time the first frame with m_tx_pps >= 9000, then pass it on."""
            if f.name == "trex-global" and isinstance(f.data, dict) and f.data.get("m_tx_pps", 0) >= 9000:
                seen.setdefault("frame", time.monotonic())
            orig(f)

        s.on_frame = on_frame  # type: ignore[method-assign]
        published: list[float] = []
        pub = rec.__call__

        def record(m: dict) -> None:
            """Time the first monitor message with m_tx_pps >= 9000."""
            pub(m)
            g = m.get("global")
            if isinstance(g, dict) and g.get("m_tx_pps", 0) >= 9000:
                published.append(time.monotonic())

        s._publish = record
        try:
            await s.connect(fake.host, fake.pub_port)
            await wait_for(lambda: bool(published), 25.0)
        finally:
            await s.close()
        assert published[0] - seen["frame"] <= PUBLISH_EVERY_S + POLL_MS / 1000 + 0.05
    asyncio.run(run())


def test_reconnect_clears_the_old_target(fake_trex) -> None:
    async def run() -> None:
        a = fake_trex("--pub-replay", STL, "--pub-names", "2")
        b = fake_trex("--pub-replay", STL)
        s = MonitorSession(Recorder())
        try:
            await s.connect(a.host, a.pub_port)
            await wait_for(lambda: "x-0" in s.snapshot()["other"], 5.0)  # type: ignore[operator]
            await s.connect(b.host, b.pub_port)
            assert s.snapshot()["other"] == {} and s.snapshot()["global"] is None
            await asyncio.sleep(1.0)
            snap = s.snapshot()
            assert not any(k.startswith("x-") for k in snap["other"])  # type: ignore[attr-defined]
            assert snap["target"] == {"host": b.host, "port": b.pub_port}
        finally:
            await s.close()
    asyncio.run(run())


def test_two_subscribers_beside_a_session(fake_trex, new_session) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL)
        rec = Recorder()
        trex = new_session(rec)
        await trex.connect(ConnectParams(fake.host, fake.rpc_port, fake.pub_port, 2.0))
        s = MonitorSession(Recorder())
        try:
            await s.connect(fake.host, fake.pub_port)
            await wait_for(lambda: s.snapshot()["state"] == "connected", 3.0)
            n = sum(1 for m in rec.msgs if m["type"] == "stats")
            await wait_for(lambda: sum(1 for m in rec.msgs if m["type"] == "stats") > n + 1, 4.0)
            assert trex.snapshot()["state"] == "connected"
        finally:
            await s.close()
    new_session.run(run())


def test_reopen_backs_off_against_a_refusing_port(fake_trex) -> None:
    """The RPC (REP) port is no PUB: its handshake fails again and again. The reopens must be spaced out."""
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL)
        sub = MonitorSubscriber(fake.host, fake.rpc_port, lambda f: None, lambda st: None)
        sub.start()
        try:
            await asyncio.sleep(2.0)
            assert 1 <= sub.reopens <= 6, sub.reopens
        finally:
            await sub.stop()
    asyncio.run(run())


def test_a_frame_that_breaks_the_handler_does_not_stop_the_task(fake_trex) -> None:
    async def run() -> None:
        fake = fake_trex("--pub-replay", STL)
        seen: list[str] = []
        states: list[str] = []

        def on_frame(f: Frame) -> None:
            """Raise on the first frame, record the rest."""
            seen.append(f.name)
            if len(seen) == 1:
                raise TypeError("boom")
        sub = MonitorSubscriber(fake.host, fake.pub_port, on_frame, states.append)
        sub.start()
        try:
            await wait_for(lambda: len(seen) >= 3, 5.0)
            assert sub.undecodable["shape"] == 1
            assert "stale" not in states
        finally:
            await sub.stop()
    asyncio.run(run())


def test_stop_survives_a_dead_poll_task_and_closes_the_context(monkeypatch) -> None:
    async def run() -> None:
        async def dies(self: MonitorSubscriber) -> None:
            """A poll task that raises."""
            raise RuntimeError("poll died")
        monkeypatch.setattr(MonitorSubscriber, "_run", dies)
        states: list[str] = []
        sub = MonitorSubscriber("127.0.0.1", 1, lambda f: None, states.append)
        sub.start()
        ctx, task = sub._ctx, sub._task
        assert ctx is not None and task is not None
        await wait_for(task.done, 3.0)
        assert states == ["stale"]
        await sub.stop()                                  # must not re-raise RuntimeError
        assert ctx.closed and sub._sock is None
    asyncio.run(run())


def test_a_refused_endpoint_leaves_nothing_open() -> None:
    async def run() -> None:
        rec = Recorder()
        s = MonitorSession(rec)
        try:
            await s.connect("_x", 4500)
            raise AssertionError("connect should have raised")
        except ValueError:
            pass
        snap = s.snapshot()
        assert snap["state"] == "disconnected" and snap["target"] is None
        assert last(rec)["state"] == "disconnected" and s._sub is None
        sub = MonitorSubscriber("_x", 4500, lambda f: None, lambda st: None)
        try:
            sub.start()
            raise AssertionError("start should have raised")
        except zmq.ZMQError:
            pass
        assert sub._ctx is None and sub._sock is None and sub._mon is None and sub._task is None
    asyncio.run(run())
