"""The shared session test rig: StubPort, StubClient and Rig (a TrexSession on a stub control client with the
trexapi calls stubbed, shaped by tests/fixtures/p3b/summary.json) and the constants the tests read.
Moved out of tests/unit/test_session_port_config.py so other session tests can build on it."""
import asyncio
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
from trex_web_gui.trexapi import PingLine

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
