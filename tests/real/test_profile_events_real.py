"""A3.11 against the real v3.08 guest: profile events 10-13 and 14 reach a connected TrexSession
(make check-real). Event 17 needs a job failure the guest cannot be made to produce (plan D16)."""
import asyncio
import os
import time

import pytest
from support import real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@pytest.mark.timeout(90)
def test_profile_events_reach_the_session_on_the_guest() -> None:
    """A foreign client runs a one-burst profile and a continuous one (start, pause, resume, stop); the
    session publishes the profile events within 10 s. Prints each event's name, message and ts."""
    trexapi.load()
    from trex.stl.api import IP, UDP, Ether, STLClient, STLPktBuilder, STLStream, STLTXCont, STLTXSingleBurst

    def stream(mode):  # type: ignore[no-untyped-def]
        """One stream with a small UDP packet and the given transmit mode."""
        return STLStream(packet=STLPktBuilder(pkt=Ether() / IP() / UDP() / ("x" * 18)), mode=mode)

    oracle = STLClient(username="console-b", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    oracle.connect()
    async def run() -> None:
        msgs: list[dict] = []
        s = TrexSession(msgs.append)
        def seen(name: str, profile: str) -> dict | None:
            """The first published `name` event whose message names `profile`, else None."""
            return next((m for m in msgs if m["type"] == "event" and m["name"] == name
                         and f"0.{profile} " in m["message"]), None)
        async def until(name: str, profile: str) -> None:
            """Wait up to 10 s for the `name` event of `profile`; AssertionError when it never comes."""
            end = time.monotonic() + 10
            while seen(name, profile) is None:
                assert time.monotonic() < end, f"{name} for {profile} not seen"
                await asyncio.sleep(0.05)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await asyncio.to_thread(oracle.acquire, ports=[0], force=True)
            await asyncio.to_thread(oracle.reset, ports=[0])
            await asyncio.to_thread(oracle.add_streams, [stream(STLTXSingleBurst(total_pkts=10, pps=100))],
                                    ports=["0.p6burst"])
            await asyncio.to_thread(oracle.add_streams, [stream(STLTXCont(pps=100))], ports=["0.p6cont"])
            await asyncio.to_thread(oracle.start, ports=["0.p6burst"])
            await asyncio.to_thread(oracle.start, ports=["0.p6cont"])
            await until("profile started", "p6burst")
            await until("profile started", "p6cont")
            await until("profile finished tx", "p6burst")
            assert seen("profile finished tx", "p6burst")["message"] == "Profile 0.p6burst job done"  # type: ignore[index]
            await asyncio.to_thread(oracle.pause, ports=["0.p6cont"])
            await until("profile paused", "p6cont")
            await asyncio.to_thread(oracle.resume, ports=["0.p6cont"])
            await until("profile resumed", "p6cont")
            await asyncio.to_thread(oracle.stop, ports=["0.p6cont"])
            await until("profile stopped", "p6cont")
            for m in msgs:
                if m["type"] == "event" and m["name"].startswith("profile "):
                    print(f"{m['name']!r} message={m['message']!r} ts={m['ts']}")
        finally:
            await s.close()
    try:
        asyncio.run(run())
    finally:
        try:
            oracle.reset(ports=[0])
        finally:
            oracle.disconnect(stop_traffic=False, release_ports=False)
