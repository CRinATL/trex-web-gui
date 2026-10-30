"""TrexSession P3b commands (Task 4): the jobs, the busy guard, the active check and the restore. trexapi's
service calls are stubbed (the fake answers push_pkts as an unknown method), shaped by
tests/fixtures/p3b/summary.json; the service bracket itself (service_on, service_restore) is the real code
against a stub client."""
import asyncio
import itertools
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from typing import Any

import pytest
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.sessions import SessionState, TrexSession, port_config, trex_session
from trex_web_gui.sessions.port_config import LayerRequest
from trex_web_gui.trexapi import CommandRefused, PingLine

SUMMARY = json.loads((FIXTURES / "p3b" / "summary.json").read_text())
PEER_MAC = SUMMARY["resolved_mac"]
NEIGH = SUMMARY["scan"]["raw"]["0"][0]
IDLE, STREAMS, TX, PAUSE, PCAP_TX = 0, 1, 2, 3, 4
OK = PingLine("reply", SUMMARY["pings"]["ok"][0]["records"][0]["text"], 4.97)


class StubPort:
    def __init__(self, stack: str = "linux_based") -> None:
        """An IDLE port with service mode off and info {"stack": stack}; ownership answers are fixed."""
        self.state = IDLE
        self.service_mode = False
        self.service_mode_filtered = False
        self.info = {"stack": stack}

    def is_acquired(self) -> bool:
        """Always True."""
        return True

    def get_owner(self) -> str:
        """Always "me"."""
        return "me"


class StubClient:
    def __init__(self, stack: str = "linux_based") -> None:
        """Two StubPorts (0, 1) and a no-op conn; set_service_mode calls are logged, and the refuse, lose
        and hold knobs start off."""
        self.ports = {0: StubPort(stack), 1: StubPort(stack)}
        self.calls: list[tuple[str, int, bool]] = []     # (method, port, enabled)
        self.refuse_off: str | None = None
        self.lose_off: Exception | None = None          # raised by service off (a loss during the restore)
        self.hold_on: threading.Event | None = None     # service on waits for it
        self.conn = NS(rpc=NS(set_retry_base=lambda flag: None))

    def set_service_mode(self, ports: list[int], enabled: bool, **kw: Any) -> None:
        """Logs (method, ports[0], enabled), and acts on ports[0] only. On waits up to 5 s on hold_on. Off
        raises lose_off if set, else TrexApiError(refuse_off) if set; otherwise the port's flag is set."""
        self.calls.append(("set_service_mode", ports[0], enabled))
        if enabled and self.hold_on:
            self.hold_on.wait(5)
        if not enabled and self.lose_off:
            raise self.lose_off
        if not enabled and self.refuse_off:
            raise trexapi.TrexApiError(self.refuse_off)
        self.ports[ports[0]].service_mode = enabled

    def disconnect(self, **kw: Any) -> None:
        """No-op."""


class Rig:
    def __init__(self, mp: pytest.MonkeyPatch, stack: str = "linux_based") -> None:
        """A TrexSession on a StubClient, already CONNECTED, publishing into msgs. Wraps its _run to count
        jobs in self.jobs. Patches trexapi: set_vlan, set_l2, set_l3, conf_ipv6, resolve, ping_once and
        scan_neighbours get logging stubs (log tuples carry the job count); port_views and read_xstats get
        fixed answers that log nothing; the ping interval is 0.05 s."""
        self.msgs: list[dict[str, Any]] = []
        self.s = TrexSession(self.msgs.append, release_on_exit=lambda: False)
        self.c = StubClient(stack)
        self.s._client, self.s._state = self.c, SessionState.CONNECTED
        self.s._params = trex_session.ConnectParams("h")
        self.jobs = 0
        self.log: list[tuple[str, int, float, float]] = []     # (name, job number, start, end)
        real_run = self.s._run

        # Counts a session job, then runs it as the real _run does.
        async def counted(fn: Any, *a: Any) -> Any:
            self.jobs += 1
            return await real_run(fn, *a)
        self.s._run = counted                                  # type: ignore[method-assign]
        mp.setattr(trexapi, "port_views", lambda c: [])
        mp.setattr(port_config, "PING_INTERVAL_S", 0.05)
        for name in ("set_vlan", "set_l2", "set_l3", "conf_ipv6"):
            mp.setattr(trexapi, name, self.stub(name, None))
        mp.setattr(trexapi, "resolve", self.stub("resolve", trexapi.ArpResult("resolved", PEER_MAC, None)))
        mp.setattr(trexapi, "ping_once", self.stub("ping_once", OK))
        mp.setattr(trexapi, "scan_neighbours", self.stub("scan", [trexapi.Neighbour(**NEIGH)]))
        mp.setattr(trexapi, "read_xstats", lambda c, p: trexapi.XstatsView(p, {"rx_good_packets": 5}))

    def stub(self, name: str, result: Any, hold_s: float = 0.0) -> Any:
        """A trexapi stand-in that sleeps hold_s, appends (name, jobs so far, start, end) to log, and
        returns result."""
        # Sleeps hold_s if set, logs (name, jobs so far, start, end), returns result.
        def call(*a: Any, **kw: Any) -> Any:
            t0 = time.monotonic()
            if hold_s:
                time.sleep(hold_s)
            self.log.append((name, self.jobs, t0, time.monotonic()))
            return result
        return call

    def of(self, kind: str) -> list[dict[str, Any]]:
        """The published messages of this type."""
        return [m for m in self.msgs if m["type"] == kind]

    def logs(self, level: str) -> list[str]:
        """The texts of the published log messages at this level."""
        return [m["message"] for m in self.of("log") if m["level"] == level]

    def run(self, body: Any) -> None:
        """Runs body(session) on a fresh loop and executor, then closes the session and restores the client
        and CONNECTED state, so run() can be called again."""
        # Binds the session to this loop with a new single-thread executor, runs body, always closes it, then
        # reconnects the stub.
        async def go() -> None:
            self.s._loop = asyncio.get_running_loop()
            self.s._closed = False
            self.s._executor = ThreadPoolExecutor(max_workers=1)             # a second run() after close()
            try:
                await body(self.s)
            finally:
                await self.s.close()
                self.s._client, self.s._state = self.c, SessionState.CONNECTED   # for a second run()
        asyncio.run(go())


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Rig:
    """A Rig on this test's monkeypatch (the patches are undone by it); nothing is torn down here."""
    return Rig(monkeypatch)


L2 = LayerRequest("L2", PEER_MAC, None, None, [10, 20], None)
L3 = LayerRequest("L3", None, "1.1.1.1", "1.1.1.99", [], None)


def test_apply_runs_one_job_per_call(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        r = await s.apply_layer(0, L2)
        assert r == (None, None)
        seen.append(rig.jobs)
    seen: list[int] = []
    rig.run(body)
    calls = [(n, j) for n, j, _, _ in rig.log]
    assert [n for n, _ in calls] == ["set_vlan", "set_l2"]
    assert len({j for _, j in calls}) == 2                        # each stubbed call in its own job
    assert rig.c.calls == [("set_service_mode", 0, True), ("set_service_mode", 0, False)]
    # service on, vlan, l2, restore + 2 refreshes
    assert seen == [6]


def test_apply_unresolved_is_a_warning_not_done(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trexapi, "resolve",
                        rig.stub("resolve", trexapi.ArpResult("unresolved", None, "no reply")))

    async def body(s: TrexSession) -> None:
        r = await s.apply_layer(0, L3)
        assert r.arp == ("unresolved", None, "no reply") and r.service_restore_error is None
    rig.run(body)
    assert rig.logs("warning") == ["Apply L3 configuration to port 0: L3 set, ARP unresolved: no reply"]
    assert not [m for m in rig.logs("info") if m.endswith(": done")]
    assert [n for n, *_ in rig.log] == ["set_vlan", "set_l3", "resolve"]
    assert rig.c.calls[-1] == ("set_service_mode", 0, False)      # restored either way


def test_apply_restore_failure_keeps_the_result(rig: Rig) -> None:
    rig.c.refuse_off = "unable to disable service mode - please remove RX queue"

    async def body(s: TrexSession) -> None:
        r = await s.apply_layer(0, L3)
        assert r.arp == ("resolved", PEER_MAC, None)
        assert r.service_restore_error == "unable to disable service mode - please remove RX queue"
    rig.run(body)
    assert any("port 0 is left in service mode" in m for m in rig.logs("warning"))


def test_apply_vlan_empty_list_is_sent_to_clear(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(trexapi, "set_vlan", lambda c, p, tags: sent.append(tags))
    rig.run(lambda s: s.apply_layer(0, L3))
    assert sent == [[]]


def test_apply_refused_while_active(rig: Rig) -> None:
    for st in (TX, PAUSE, PCAP_TX):
        rig.c.ports[0].state = st

        async def body(s: TrexSession) -> None:
            with pytest.raises(CommandRefused) as e:
                await s.apply_layer(0, L2)
            assert (e.value.code == "port_active"
                    and e.value.message == "Port 0 is transmitting; stop traffic first.")
        rig.run(body)
    assert rig.c.calls == [] and rig.log == []                    # no RPC at all


@pytest.mark.parametrize("state", [IDLE, STREAMS, 5])
def test_apply_allowed_when_idle_streams_or_astf_loaded(rig: Rig, state: int) -> None:
    rig.c.ports[0].state = state
    rig.run(lambda s: s.apply_layer(0, L2))
    assert [n for n, *_ in rig.log] == ["set_vlan", "set_l2"]


def test_ipv6_on_legacy_is_refused_before_any_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(monkeypatch, stack="legacy")

    async def body(s: TrexSession) -> None:
        with pytest.raises(CommandRefused) as e:
            await s.apply_layer(0, LayerRequest("L2", PEER_MAC, None, None, [], (True, None)))
        assert e.value.code == "ipv6_unsupported"
        assert e.value.message == "IPv6 configuration needs the linux_based stack; this server runs legacy"
    rig.run(body)
    assert rig.c.calls == [] and rig.log == []                    # not even service mode was touched


def test_ipv6_disabled_on_legacy_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(monkeypatch, stack="legacy")
    got: list[Any] = []
    monkeypatch.setattr(trexapi, "conf_ipv6", lambda c, p, en, src: got.append((p, en, src)))
    rig.run(lambda s: s.apply_layer(0, LayerRequest("L2", PEER_MAC, None, None, [], (False, None))))
    assert got == [(0, False, None)]


def test_ipv6_enabled_on_linux_based_is_sent(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[Any] = []
    monkeypatch.setattr(trexapi, "conf_ipv6", lambda c, p, en, src: got.append((p, en, src)))
    rig.run(lambda s: s.apply_layer(0, LayerRequest("L2", PEER_MAC, None, None, [], (True, None))))
    assert got == [(0, True, None)]


def test_filtered_port_is_refused(rig: Rig) -> None:
    rig.c.ports[1].service_mode_filtered = True

    async def body(s: TrexSession) -> None:
        for call in (lambda: s.apply_layer(1, L2), lambda: s.ping(1, "2.2.2.2", 1, 64), lambda: s.scan(1)):
            with pytest.raises(CommandRefused) as e:
                await call()
            assert e.value.code == "service_filtered"
    rig.run(body)
    assert rig.c.calls == [] and rig.s.busy() == {}


# ---- ping

def test_ping_message_fields(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        r = await s.ping(1, "2.2.2.2", 2, 64)
        assert [x.status for x in r.lines] == ["reply", "reply"] and r.service_restore_error is None
        r2 = await s.ping(1, "2.2.2.2", 1, 64)
        # session-wide, increasing: the frontend drops a late line of a finished run
        assert r2.run == r.run + 1
        runs.extend([r.run, r2.run])
    runs: list[int] = []
    rig.run(body)
    first, second = rig.of("ping")[:2], rig.of("ping")[2:]
    assert [m["run"] for m in first] == [runs[0]] * 2 and [m["run"] for m in second] == [runs[1]]
    msgs = first
    assert len(msgs) == 2
    for i, m in enumerate(msgs, 1):
        assert set(m) == {"type", "port", "run", "seq", "count", "status", "text", "rtt_ms", "ts"}
        assert ((m["port"], m["seq"], m["count"], m["status"], m["text"], m["rtt_ms"])
                == (1, i, 2, "reply", OK.text, 4.97))
    assert [m["message"] for m in rig.of("log") if m["level"] == "ping"] == [f"Port 1: {OK.text}"] * 3


def test_ping_writes_no_per_echo_done(rig: Rig) -> None:
    rig.run(lambda s: s.ping(1, "2.2.2.2", 3, 64))
    done = [m["message"] for m in rig.of("log") if "done" in m["message"]]
    assert done == ["Ping 2.2.2.2 from port 1: done"]


def test_ping_releases_the_executor_between_echoes(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(port_config, "PING_INTERVAL_S", 0.3)
    monkeypatch.setattr(trexapi, "ping_once", rig.stub("ping_once", OK, hold_s=0.05))
    probes: list[float] = []

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.ping(1, "2.2.2.2", 5, 64))
        await asyncio.sleep(0.2)
        while not task.done():
            probes.append(await s._run(time.monotonic))           # a stats job
            await asyncio.sleep(0.1)
        await task
    rig.run(body)
    echoes = [(a, b) for n, _, a, b in rig.log if n == "ping_once"]
    assert len(echoes) == 5 and len(probes) >= 4
    assert all(b - a < 0.2 for a, b in echoes)                   # no job holds the executor across echoes
    ran_between = [p for p in probes if any(b1 <= p <= a2 for (_, b1), (a2, _) in itertools.pairwise(echoes))]
    # the probes ran in the gaps, not after the ping
    assert len(ran_between) >= 3


def test_second_ping_on_the_port_is_port_busy(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    hold = threading.Event()
    monkeypatch.setattr(trexapi, "ping_once", lambda c, p, d, n: (hold.wait(5), OK)[1])

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.ping(1, "2.2.2.2", 1, 64))
        await asyncio.sleep(0.1)
        with pytest.raises(CommandRefused) as e:
            await s.ping(1, "2.2.2.2", 1, 64)
        assert e.value.code == "port_busy" and e.value.message == "Port 1 is busy pinging or scanning."
        with pytest.raises(CommandRefused) as e:
            await s.scan(1)
        assert e.value.code == "port_busy"
        hold.set()
        await task
    rig.run(body)


def hold_ping(rig: Rig, mp: pytest.MonkeyPatch, hold: threading.Event) -> None:
    """Replaces ping_once so each call blocks up to 5 s on hold, then returns OK."""
    mp.setattr(trexapi, "ping_once", lambda c, p, d, n: (hold.wait(5), OK)[1])


def test_apply_during_ping_is_port_busy(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    hold = threading.Event()
    hold_ping(rig, monkeypatch, hold)

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.ping(1, "2.2.2.2", 1, 64))
        await asyncio.sleep(0.1)
        with pytest.raises(CommandRefused) as e:
            await s.apply_layer(1, L2)
        assert e.value.code == "port_busy"
        await s.apply_layer(0, L2)                               # another port is free
        hold.set()
        await task
    rig.run(body)


def test_start_during_ping_is_port_busy(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    hold = threading.Event()
    hold_ping(rig, monkeypatch, hold)

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.ping(1, "2.2.2.2", 1, 64))
        await asyncio.sleep(0.1)
        calls: list[Any] = [lambda: s.start([1], "1kpps"), lambda: s.release([1]), lambda: s.reset([1]),
                            lambda: s.set_service([1], False), lambda: s.set_port_attr([1], promiscuous=True)]
        for call in calls:
            with pytest.raises(CommandRefused) as e:
                await call()
            assert e.value.code == "port_busy"
        hold.set()
        await task
    rig.run(body)


def test_service_restored_after_a_failed_ping(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(c: Any, p: int, d: str, n: int) -> PingLine:
        raise trexapi.TrexApiError("capture failed")
    monkeypatch.setattr(trexapi, "ping_once", boom)

    async def body(s: TrexSession) -> None:
        r = await s.ping(1, "2.2.2.2", 3, 64)
        assert [x.status for x in r.lines] == ["error"] and r.lines[0].text == "capture failed"
    rig.run(body)
    assert rig.c.calls == [("set_service_mode", 1, True), ("set_service_mode", 1, False)]
    assert rig.logs("warning") == ["Ping 2.2.2.2 from port 1: failed: capture failed"]
    assert [m["status"] for m in rig.of("ping")] == ["error"]


def test_service_restored_after_a_cancelled_ping(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(port_config, "PING_INTERVAL_S", 5)

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.ping(1, "2.2.2.2", 5, 64))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
    rig.run(body)
    assert rig.c.calls[-1] == ("set_service_mode", 1, False) and rig.s.busy() == {}


def test_ping_refused_mid_run_ends_with_a_line_naming_it(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def echo(c: Any, p: int, d: str, n: int) -> PingLine:
        rig.c.ports[1].state = TX                                # started elsewhere after the first echo
        return OK
    monkeypatch.setattr(trexapi, "ping_once", echo)

    async def body(s: TrexSession) -> None:
        r = await s.ping(1, "2.2.2.2", 3, 64)
        assert [x.status for x in r.lines] == ["reply", "refused"]
        assert r.lines[1].text == "Port 1 is transmitting; stop traffic first."
    rig.run(body)
    assert rig.c.calls[-1] == ("set_service_mode", 1, False)


def test_ping_ends_on_disconnect_without_restore(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.ping(1, "2.2.2.2", 5, 64))
        await asyncio.sleep(0.02)
        while not rig.of("ping"):
            await asyncio.sleep(0.01)
        s._client = None                                        # the connection is gone
        r = await task
        assert len(r.lines) == 1 and r.service_restore_error is None
    rig.run(body)
    assert rig.c.calls == [("set_service_mode", 1, True)]       # no restore on a dead connection
    assert any("service mode may still be on for port 1" in m for m in rig.logs("warning"))
    assert not [m for m in rig.logs("info") if "done" in m]              # neither "done" ...
    assert not [m for m in rig.of("log") if "failed" in m["message"]]    # ... nor "failed"
    assert rig.s.busy() == {}


def test_busy_state_published_and_cleared(rig: Rig) -> None:
    rig.run(lambda s: s.ping(1, "2.2.2.2", 1, 64))
    assert [m["ports"] for m in rig.of("busy")] == [{1: "ping"}, {}]

    rig2_msgs = len(rig.msgs)
    rig.run(lambda s: s.scan(0))
    assert [m["ports"] for m in rig.of("busy")][2:] == [{0: "scan"}, {}] and len(rig.msgs) > rig2_msgs
    rig.run(lambda s: s.apply_layer(0, L2))
    assert [m["ports"] for m in rig.of("busy")][4:] == [{0: "apply"}, {}]


def test_apply_publishes_busy(rig: Rig) -> None:
    rig.run(lambda s: s.apply_layer(0, L2))
    assert [m["ports"] for m in rig.of("busy")] == [{0: "apply"}, {}]


def test_scan_returns_neighbours_and_restores(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        r = await s.scan(0)
        assert r.neighbours == [trexapi.Neighbour(**NEIGH)] and r.service_restore_error is None
    rig.run(body)
    assert rig.c.calls == [("set_service_mode", 0, True), ("set_service_mode", 0, False)]


def test_xstats_poll_writes_no_log(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        for _ in range(3):
            v = await s.xstats(1)
            assert v.port == 1 and v.values == {"rx_good_packets": 5}
        seen.append(rig.jobs)
    seen: list[int] = []
    rig.run(body)
    assert rig.of("log") == [] and seen == [3]
    assert rig.of("ports")[:-1] == []                           # no refresh (the last one is close()'s)


def test_cancel_during_service_on_still_restores(rig: Rig) -> None:
    rig.c.hold_on = threading.Event()

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.apply_layer(0, L2))
        await asyncio.sleep(0.1)                                # service_on is held on the executor
        task.cancel()
        asyncio.get_running_loop().call_later(0.2, rig.c.hold_on.set)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s.busy() == {}
    rig.run(body)
    assert rig.c.calls == [("set_service_mode", 0, True), ("set_service_mode", 0, False)]
    assert rig.log == []                                        # the body never ran


def test_cancel_during_the_refresh_after_service_on_still_restores(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    hold = threading.Event()
    monkeypatch.setattr(trexapi, "port_views", lambda c: (hold.wait(5), [])[1])

    async def body(s: TrexSession) -> None:
        task = asyncio.create_task(s.scan(0))
        await asyncio.sleep(0.2)                                # service is on; the refresh is held
        assert rig.c.calls == [("set_service_mode", 0, True)]
        task.cancel()
        asyncio.get_running_loop().call_later(0.1, hold.set)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s.busy() == {}
    rig.run(body)
    assert rig.c.calls == [("set_service_mode", 0, True), ("set_service_mode", 0, False)]


def test_loss_during_the_restore_marks_the_session_lost(rig: Rig) -> None:
    rig.c.lose_off = trexapi.ServerUnreachable("Cannot reach the TRex server: timed out")

    async def body(s: TrexSession) -> None:
        with pytest.raises(trexapi.ServerUnreachable):
            await s.scan(0)
        assert s._state == SessionState.LOST and s.busy() == {}
    rig.run(body)


def test_refusal_mid_apply_still_refreshes(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    views: list[int] = []
    monkeypatch.setattr(trexapi, "port_views", lambda c: views.append(1) or [])

    def vlan_then_tx(c: Any, p: int, tags: list[int]) -> None:
        rig.c.ports[p].state = TX                                # started elsewhere
    monkeypatch.setattr(trexapi, "set_vlan", vlan_then_tx)

    async def body(s: TrexSession) -> None:
        with pytest.raises(CommandRefused) as e:
            await s.apply_layer(0, L2)
        assert e.value.code == "port_active"
        seen.append(len(views))
    seen: list[int] = []
    rig.run(body)
    assert seen == [2]                                          # after service on, and on the refusal path
    assert rig.c.calls[-1] == ("set_service_mode", 0, False)


def test_a_failed_echo_is_logged_once(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(c: Any, p: int, d: str, n: int) -> PingLine:
        raise trexapi.TrexApiError("capture failed")
    monkeypatch.setattr(trexapi, "ping_once", boom)
    rig.run(lambda s: s.ping(1, "2.2.2.2", 3, 64))
    assert [m["message"] for m in rig.of("log") if "capture failed" in m["message"]] == [
        "Ping 2.2.2.2 from port 1: failed: capture failed"]
