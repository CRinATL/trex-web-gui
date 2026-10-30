"""P4b exit criteria against the real v3.08 guest (make check-real), spec section 8: a packet built in the
editor's engine, put in a library profile, assigned through TrexSession and sent leaves port 0 as the Field
Engine says. The oracle is a foreign STLClient with its own Rx capture on port 1 (as test_capture_real.py);
the packet worker and the library are in-process, and the engine is closed in `finally`.

Each test prints its measured figures (vm/real.sh runs pytest with -s)."""
import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from support import real_ports
from test_profiles_real import TX_MAX_S, server_state, settled_opackets
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession
from trex_web_gui.studio.library import ProfileLibrary
from trex_web_gui.studio.packetdoc import model_of
from trex_web_gui.studio.presets import preset
from trex_web_gui.trexapi.packet import PacketEngine

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
CAPTURE_LIMIT = 2000
FETCH_S = 10.0                     # how long the oracle waits for the captured packets to be all in
IP_SRC = slice(26, 30)             # an untagged Ethernet/IPv4 frame: the source address
FRAME_LEN_MIN, FRAME_LEN_MAX = 60, 124   # the flood's 64..128 FCS-included frames, FCS off the wire


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


def stream_of(parts: dict[str, Any], mode: dict[str, Any], flags: int = 0) -> dict[str, Any]:
    """A library stream (one started at once, no flow stats) carrying `parts` (to_stream's packet and vm)
    and `mode`, with the MAC `flags` the preset asked for."""
    return {"flags": flags, "action_count": 0, "enabled": True, "self_start": True, "start_paused": False,
            "isg": 0.0, "core_id": -1, "mode": mode, "packet": parts["packet"], "vm": parts["vm"],
            "flow_stats": {"enabled": False}}


@asynccontextmanager
async def rig() -> AsyncIterator[tuple[TrexSession, Any, PacketEngine]]:
    """A connected TrexSession owning ports 0 and 1 (taken by force) with port 1 in service mode (Rx capture
    needs it on this af_packet guest), a foreign STLClient oracle that only reads and a PacketEngine. All
    closed on exit; port 1 is put back out of service mode and port 0's traffic is stopped."""
    trexapi.load()
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    await asyncio.to_thread(oracle.connect)
    engine = PacketEngine()
    s = TrexSession(lambda m: None)
    try:
        await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
        await s.acquire([0, 1], force=True)
        await s.stop([0])
        await s.set_service([1], True)
        yield s, oracle, engine
    finally:
        try:
            await s.stop([0])
            await s.set_service([1], False)
        finally:
            await engine.close()
            await s.close()
            await asyncio.to_thread(oracle.disconnect, stop_traffic=False, release_ports=False)


async def send_and_capture(s: TrexSession, oracle: Any, lib: ProfileLibrary, name: str,
                           stream: dict[str, Any], bpf: str, expect_tx: int) -> tuple[list[bytes], int]:
    """Make profile `name` from `stream`, assign it to port 0, start it with the oracle's capture of port 1
    already running, wait until port 0 leaves TX and the capture holds the frames; the frames port 1 received
    and the oracle's opackets delta of port 0."""
    entry = lib.create(name, [stream])
    lp = await trexapi.load_profile(entry["path"], [0], [], max_result=trexapi.MAX_RESULT_PCAP)
    await s.assign_profile([0], lp, stop_first=True)
    started = await asyncio.to_thread(oracle.start_capture, rx_ports=[1], limit=CAPTURE_LIMIT, mode="cyclic",
                                      bpf_filter=bpf)
    cid = int(started["id"])
    try:
        before = await settled_opackets(oracle)
        await s.start([0], "1")
        deadline = time.monotonic() + TX_MAX_S
        while await asyncio.to_thread(server_state, oracle) == "TX":
            assert time.monotonic() < deadline, f"port 0 still in TX after {TX_MAX_S:g} s"
            await asyncio.sleep(0.1)
        delta = await settled_opackets(oracle) - before
        deadline = time.monotonic() + FETCH_S
        while time.monotonic() < deadline:                  # the Rx side trails the Tx counter
            held = (await asyncio.to_thread(oracle.get_capture_status))[cid]["count"]
            if held >= expect_tx:
                break
            await asyncio.sleep(0.2)
    finally:
        out: list[dict[str, Any]] = []
        await asyncio.to_thread(oracle.stop_capture, cid, output=out)
    return [bytes(p["binary"]) for p in out], delta


@pytest.mark.timeout(180)
def test_template_sweep_on_the_guest(tmp_path: Path) -> None:
    """The TCP-SYN template plus a 10-address ip_src sweep (inc, write at IP.src, fix the IPv4 checksum),
    through to_stream into a library profile of one burst of 100 packets at 100 pps, assigned to port 0 and
    started: the oracle's capture of port 1 holds 100 packets with exactly 10 distinct IP source addresses,
    16.0.0.1 to 16.0.0.10."""
    lib = ProfileLibrary(lambda: tmp_path / "lib")

    async def run() -> None:
        """Build, store, send, capture and compare."""
        async with rig() as (s, oracle, engine):
            doc = await engine.template("TCP-SYN")
            doc["feInstructions"] = [
                {"id": "STLVmFlowVar", "parameters": {"name": "ip_src", "min_value": "16.0.0.1",
                                                      "max_value": "16.0.0.10", "size": "4", "op": "inc"}},
                {"id": "STLVmWrFlowVar", "parameters": {"fv_name": "ip_src", "pkt_offset": "IP.src"}},
                {"id": "STLVmFixIpv4", "parameters": {"offset": "IP"}}]
            packet, fe = model_of(doc)
            parts = await engine.to_stream(packet, fe)
            stream = stream_of(parts, {"type": "single_burst", "total_pkts": 100,
                                            "rate": {"type": "pps", "value": 100.0}})
            frames, delta = await send_and_capture(s, oracle, lib, "template sweep", stream, "tcp", 100)
        sources = sorted({".".join(str(b) for b in f[IP_SRC]) for f in frames})
        print(f"template sweep: opackets delta {delta}, captured {len(frames)}, "
              f"{len(sources)} distinct IP.src {sources}")
        assert delta == 100
        assert len(frames) == 100
        assert sources == sorted(f"16.0.0.{n}" for n in range(1, 11))
    asyncio.run(run())


@pytest.mark.timeout(180)
def test_flood_preset_on_the_guest(tmp_path: Path) -> None:
    """The flood preset (UDP, frame size random 64..128), one burst of 1000 packets: the oracle's opackets
    delta of port 0 is 1000 and the captured frame lengths lie in 60..124 with more than one distinct
    value."""
    lib = ProfileLibrary(lambda: tmp_path / "lib")

    async def run() -> None:
        """Build, store, send, capture and compare."""
        async with rig() as (s, oracle, engine):
            built = preset("flood", {"l4": "udp", "frame": {"mode": "random", "min": 64, "max": 128}})
            packet, fe = model_of(built["doc"])
            parts = await engine.to_stream(packet, fe)
            stream = stream_of(parts, {"type": "single_burst", "total_pkts": 1000,
                                                     "rate": {"type": "pps", "value": 1000.0}},
                               flags=built["flags"] or 0)
            frames, delta = await send_and_capture(s, oracle, lib, "flood", stream, "udp", 1000)
        lengths = [len(f) for f in frames]
        print(f"flood: opackets delta {delta}, captured {len(frames)}, lengths "
              f"{min(lengths)}..{max(lengths)} with {len(set(lengths))} distinct values")
        assert delta == 1000
        assert frames, "the capture is empty"
        assert all(FRAME_LEN_MIN <= n <= FRAME_LEN_MAX for n in lengths)
        assert len(set(lengths)) > 1
    asyncio.run(run())
