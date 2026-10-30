"""P2 exit criteria against the real v3.08 guest (make check-real)."""
import asyncio
import os
import time

import pytest
from support import SRC, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(SRC / "scripts/stl/udp_for_benchmarks.py")


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@pytest.mark.timeout(90)
def test_console_owns_then_gui_takes_over_and_runs_tunable_profile() -> None:
    trexapi.load()
    from trex.stl.api import STLClient
    console = STLClient(username="console-b", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    console.connect()
    async def run() -> None:
        msgs: list[dict] = []
        s = TrexSession(msgs.append)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await asyncio.to_thread(console.reset, ports=[0])        # console owns port 0
            t = time.monotonic()
            while time.monotonic() - t < 4.5:
                if s.ports()[0]["owner"] == "console-b":
                    break
                await asyncio.sleep(0.05)
            seen_s = time.monotonic() - t
            print(f"owner_seen_s={seen_s:.2f}")
            assert s.ports()[0]["owner"] == "console-b"
            with pytest.raises(trexapi.CommandRefused):
                await s.start([0], "10kpps")
            await s.acquire([0], force=True)
            lp = await trexapi.load_profile(PROFILE, [0], ["--packet_len", "128"])
            await s.assign_profile([0], lp)
            await s.start([0], "10kpps")
            await asyncio.sleep(2.0)
            assert s.ports()[0]["state"] == "TX"
            await s.stop([0])
            st = await asyncio.to_thread(console.get_stats, ports=[0])
            opk, oby = st[0]["opackets"], st[0]["obytes"]
            print(f"opackets={opk} obytes={oby} bytes_per_pkt={oby / max(opk, 1):.1f}")
            assert opk > 0 and 124 <= oby / opk <= 128     # frame without/with FCS; measured value printed
        finally:
            await s.close()
    try:
        asyncio.run(run())
    finally:
        console.disconnect(stop_traffic=False, release_ports=False)


@pytest.mark.timeout(90)
def test_stop_with_a_foreign_flow_stats_profile_on_the_guest() -> None:
    """Task 8: on v3.08, with `_` idle and a foreign `bench` profile (flow stats) in TX, stop([0]) raises
    nothing and leaves `_` unchanged and `bench` in STREAMS. This is the server-side evidence for the fake's
    "stop of an inactive profile is a no-op" assumption. RX-filter state is not observed here (A3.5 is
    covered by the fake-tier test_stop_of_a_flow_stats_profile_removes_rx_filters_on_port_1)."""
    trexapi.load()
    from trex.stl.api import (
        IP,
        UDP,
        Ether,
        STLClient,
        STLFlowStats,
        STLPktBuilder,
        STLStream,
        STLTXCont,
    )
    console = STLClient(username="console-b", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    console.connect()
    async def run() -> None:
        s = TrexSession(lambda m: None)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await asyncio.to_thread(console.reset, ports=[0])
            pkt = STLPktBuilder(pkt=Ether() / IP(src="16.0.0.1", dst="48.0.0.1") / UDP(dport=1025, sport=1025)
                                / (b"x" * 18))
            stream = STLStream(packet=pkt, flow_stats=STLFlowStats(pg_id=12), mode=STLTXCont(pps=1000))
            await asyncio.to_thread(console.acquire, ports=[0], force=True)
            await asyncio.to_thread(console.add_streams, [stream], ports=["0.bench"])
            await asyncio.to_thread(console.start, ports=["0.bench"])
            await s.acquire([0], force=True)
            def server_profiles() -> dict:
                return console.ports[0].transmit(
                    "get_port_status", {"port_id": 0, "profile_id": "*", "block": False}
                ).data()["state_profile"]
            before = await asyncio.to_thread(server_profiles)
            print(f"server before stop: {before}")
            assert before.get("_", "IDLE") == "IDLE" and before.get("bench") == "TX"
            await s.stop([0])                                    # raises nothing
            after = await asyncio.to_thread(server_profiles)
            print(f"server after stop: {after}")
            assert after.get("_", "IDLE") == "IDLE" and after.get("bench") == "STREAMS"
        finally:
            await s.close()
    try:
        asyncio.run(run())
    finally:
        console.disconnect(stop_traffic=False, release_ports=False)


@pytest.mark.timeout(90)
def test_close_releases_after_streams_and_service_mode() -> None:
    """The exit path on a real server (RC1-24): a session that acquired port 0, loaded streams and turned
    service mode on and off releases the port when it closes with release_on_exit."""
    trexapi.load()
    from trex.stl.api import STLClient
    watcher = STLClient(username="watcher", server=HOST, sync_port=real_ports()[0],
                        async_port=real_ports()[1], verbose_level="none")
    watcher.connect()
    async def run() -> None:
        s = TrexSession(lambda m: None)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await s.acquire([0], force=True)
            await s.assign_profile([0], await trexapi.load_profile(PROFILE, [0], ["--packet_len", "128"]))
            await s.set_service([0], True)
            await s.set_service([0], False)
        finally:
            await s.close()
    try:
        asyncio.run(run())
        time.sleep(0.5)
        watcher.ports[0].sync()
        owner = watcher.ports[0].get_owner()
        print(f"owner_after_close={owner!r}")
        assert owner == ""
    finally:
        watcher.disconnect()
