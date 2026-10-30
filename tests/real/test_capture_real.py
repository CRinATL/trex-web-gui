"""P3c exit criterion against the real v3.08 guest (make check-real): the session's capture monitor,
recorders, Save and disconnect agree with an independent client playing trex-console (connected, never
acquiring), which runs its own capture through the vendored client's start_capture / stop_capture.

Every capture behaviour asserted here was measured on the af_packet guest, not on a DPDK NIC: Rx capture with
port 1 in service mode, and a Tx capture that records only with the Tx port in service mode
(tests/fixtures/p3c/summary.json `tx_capture_probe`: service off counted 0 of 151 sent, service on 50). The
behaviour on a DPDK NIC is unconfirmed."""
import asyncio
import base64
import dataclasses
import os
import time
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES, real_ports
from test_port_config_real import only_unresolved, read_port, request_of, restore_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession
from trex_web_gui.trexapi import decode, scapy_exec

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
UDP1 = str(FIXTURES / "profiles/udp_1pkt_simple.py")
MAX_COUNT_GAP = 50     # one fetch batch: the two captures start and stop a few ms apart at 100 pps
BAD_BPF = "not a filter(("
BAD_BPF_TEXT = "is not a valid pattern"         # recorded: tests/fixtures/p3c/summary.json errors.bad_bpf
GUI_RECORDERS = 8                   # Review Focus 3: a ping still works with 8 GUI recorders present


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


def rows_of(msgs: list[dict[str, Any]], monitor_id: int) -> list[dict[str, Any]]:
    """Every DecodedRow the session published for one monitor."""
    return [r for m in msgs if m.get("type") == "capture_pkts" and m["monitor_id"] == monitor_id
            for r in m["rows"]]


def oracle_stop(oracle: Any, cid: int) -> int:
    """The vendored client's stop_capture with a list output: it fetches up to the stop reply's `pkt_count`
    packets (all the stopped capture holds) into the list, then removes the capture."""
    out: list[Any] = []
    oracle.stop_capture(cid, output=out)
    return len(out)


def has_vlan(tree: dict[str, Any], vid: int) -> bool:
    """True when the first Dot1Q layer of the decoded packet `tree` has a vlan field equal to `vid`; False
    without one."""
    for layer in tree.get("data", []):
        if layer.get("id") == "Dot1Q":
            return any(f.get("id") == "vlan" and f.get("value") == vid for f in layer.get("fields", []))
    return False


@pytest.mark.timeout(240)
def test_capture_matches_the_client_oracle(tmp_path: Path) -> None:
    trexapi.load()
    from scapy.utils import rdpcap
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    oracle.connect()                       # no acquire: captures need no ownership on the server
    start: dict[int, dict[str, Any]] = {}
    oracle_ids: list[int] = []                           # the oracle's captures still on the server

    async def run() -> None:
        msgs: list[dict[str, Any]] = []
        s = TrexSession(msgs.append, spool_dir=tmp_path / "captures")
        params = ConnectParams(HOST, *real_ports(), 3.0)
        stack = ""
        gui_ids: list[int] = []                          # recorders the test started and has not removed

        async def oracle_start(bpf: str) -> int:
            started = await asyncio.to_thread(oracle.start_capture, rx_ports=[1], limit=1000, mode="cyclic",
                                              bpf_filter=bpf)
            oracle_ids.append(started["id"])
            return int(started["id"])

        async def oracle_stop_and_remove(cid: int) -> int:
            n = await asyncio.to_thread(oracle_stop, oracle, cid)
            oracle_ids.remove(cid)
            return n

        try:
            await s.connect(params)
            for p in (0, 1):
                start[p] = await asyncio.to_thread(read_port, oracle, p)
            print(f"starting config: {start}")
            stack = s.ports()[0]["info"]["stack"]
            legacy = stack == "legacy"
            await s.acquire([0, 1], force=True)
            await s.set_service([1], True)               # Rx capture: the service-mode gate (spec 6.5)
            lp = await trexapi.load_profile(UDP1, [0], [])
            await s.assign_profile([0], lp)
            await s.start([0], "100pps")                 # port 0 off service: an unforced start

            # 1. the monitor against the oracle's own capture, same traffic, same 5 s
            mon, oid = await asyncio.gather(s.monitor_start([], [1], "udp", limit=1000), oracle_start("udp"))
            await asyncio.sleep(5.0)
            _, oracle_count = await asyncio.gather(s.monitor_stop(), oracle_stop_and_remove(oid))
            rows = rows_of(msgs, mon.id)
            print(f"monitor {mon.id}: fetched {len(rows)} (every row published, the stop's drain included); "
                  f"oracle {oid}: stop pkt_count {oracle_count}")
            assert len(rows) > 0 and oracle_count > 0
            assert abs(len(rows) - oracle_count) <= MAX_COUNT_GAP
            assert {r["proto"] for r in rows} == {"UDP"}

            # 2. negative control (canon:VER-2): a filter that matches nothing shows nothing while UDP flows
            neg, oid = await asyncio.gather(s.monitor_start([], [1], "tcp", limit=100), oracle_start("udp"))
            await asyncio.sleep(3.0)
            neg_rows = rows_of(msgs, neg.id)
            oracle_row = (await asyncio.to_thread(oracle.get_capture_status))[oid]
            await asyncio.gather(s.monitor_stop(), oracle_stop_and_remove(oid))
            print(f"negative control: monitor bpf tcp rows {len(neg_rows)}, oracle bpf udp count "
                  f"{oracle_row['count']}")
            assert oracle_row["count"] > 0
            assert neg_rows == []

            # 3. the server is the BPF validator; its text is passed through
            with pytest.raises(trexapi.TrexApiError, match=BAD_BPF_TEXT) as bad:
                await s.monitor_start([], [1], BAD_BPF)
            print(f"bad BPF: {bad.value}")

            # 4. a Tx-only recorder: not gated, but on the af_packet guest it records only with port 0 in
            # service mode (summary.json tx_capture_probe), and a service-mode port refuses an unforced start
            await s.stop([0])
            await s.set_service([0], True)
            await s.start([0], "100pps", force=True)
            rid = await s.recorder_start([0], [], "", limit=50)
            gui_ids.append(rid)
            deadline, count = time.monotonic() + 10.0, 0
            while time.monotonic() < deadline:
                row = next((r for r in await s.capture_sync() if r["id"] == rid), None)
                count = row["count"] if row else -1
                if count == 50:
                    break
                await asyncio.sleep(0.2)
            assert count == 50, f"Tx recorder {rid} held {count} packets after 10 s"
            file = await s.recorder_save(rid)
            gui_ids.remove(rid)                          # Save removes the capture once the file is complete
            await s.stop([0])
            frames = rdpcap(str(s.spool().path(file["name"])))
            stream_pkt = base64.b64decode(lp.streams_by_port[0][0]["packet"]["binary"])
            ether = (await asyncio.to_thread(read_port, oracle, 0))["layer_cfg"]["ether"]
            frame0 = bytes(frames[0])
            print(f"Tx recorder {rid}: saved {file['name']} with {len(frames)} frames; "
                  f"frame 0 {frame0.hex()}; stream packet {stream_pkt.hex()}; port 0 ether {ether}")
            assert len(frames) == 50
            # The server writes the MAC header from port 0's configuration (stream flags 0, the profile sets
            # no MAC override; recorded in tests/fixtures/p3c/rpc.jsonl), so the frame equals the stream's
            # packet from byte 12 on, and its first 12 bytes are port 0's resolved destination and its
            # source MAC. The 52-byte packet goes out padded with zeros to the 60-byte Ethernet minimum
            # (measured on the guest).
            assert len(frame0) == max(60, len(stream_pkt))
            assert frame0[12:len(stream_pkt)] == stream_pkt[12:]
            assert frame0[len(stream_pkt):] == bytes(len(frame0) - len(stream_pkt))
            assert frame0[:6] == bytes.fromhex(ether["dst"].replace(":", ""))
            assert frame0[6:12] == bytes.fromhex(ether["src"].replace(":", ""))

            # 5. VLAN 10 on both ports: the echo request reaches Rx port 1 tagged
            base = {p: request_of(start[p], legacy) for p in (0, 1)}
            assert base[0] is not None and base[1] is not None, "a port starts unconfigured"
            for p in (1, 0):                             # port 0 last: its resolve then crosses VLAN 10
                await s.apply_layer(p, dataclasses.replace(base[p], vlan=[10]))
            vmon = await s.monitor_start([], [1], "vlan and icmp", limit=100)
            ping = await s.ping(0, "2.2.2.2", 1, 64)
            print("VLAN ping: " + " | ".join(f"{r.status} {r.text}" for r in ping.lines))
            deadline = time.monotonic() + 3.0
            while not rows_of(msgs, vmon.id) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            await s.monitor_stop()
            vrows = rows_of(msgs, vmon.id)
            trees = [await scapy_exec.arun(decode.tree, base64.b64decode(r["binary"]))
                     for r in vrows]
            print(f"VLAN monitor {vmon.id}: rows {[(r['proto'], r['info']) for r in vrows]}")
            assert any(has_vlan(t, 10) for t in trees), "no row with a Dot1Q vlan 10 layer"
            for p in (0, 1, 0):                          # clear VLAN; port 0 again: its first resolve crossed
                await s.apply_layer(p, base[p])          # port 1 still tagged

            # 6. Review Focus 3: with 8 GUI recorders present a ping still gets its capture slot
            for _ in range(GUI_RECORDERS):
                gui_ids.append(await s.recorder_start([], [1], "", limit=10))
            listed = await asyncio.to_thread(oracle.get_capture_status)
            ping = await s.ping(0, "2.2.2.2", 1, 64)
            print(f"{len(listed)} captures listed; ping with {GUI_RECORDERS} recorders: "
                  + " | ".join(f"{r.status} {r.text}" for r in ping.lines))
            assert [r.status for r in ping.lines] == ["reply"]
            for cid in list(gui_ids):
                await s.recorder_remove(cid)
                gui_ids.remove(cid)

            # 7. Review Focus 4: disconnect removes our monitor and leaves a STOPPED recorder
            dmon = await s.monitor_start([], [1], "", limit=100)
            rid = await s.recorder_start([], [1], "", limit=10)
            gui_ids.append(rid)
            await s.recorder_stop(rid)
            await s.disconnect()
            listed = await asyncio.to_thread(oracle.get_capture_status)
            print(f"after disconnect the oracle lists {sorted(listed)} (monitor {dmon.id}, recorder {rid})")
            assert dmon.id not in listed
            assert rid in listed and listed[rid]["state"] == "STOPPED"
            await s.connect(params)
            await s.acquire([0, 1], force=True)
            await s.recorder_remove(rid)
            gui_ids.remove(rid)
            assert rid not in await asyncio.to_thread(oracle.get_capture_status)
        finally:
            for cid in gui_ids:                          # a failure above left these on the server
                try:
                    c = s.require_control()
                    await s._run(trexapi.capture_remove, c, cid)
                except Exception as e:                   # noqa: BLE001 - cleanup must go on
                    print(f"cleanup capture {cid}: {e}")
            if s.snapshot().get("state") != "connected":
                try:
                    await s.connect(params)
                    await s.acquire([0, 1], force=True)
                except Exception as e:                   # noqa: BLE001
                    print(f"cleanup reconnect: {e}")
            await restore_ports(s, start, stack)

    try:
        asyncio.run(run())
        linux = oracle.ports[0].info.get("stack") == "linux_based"
        unresolved_only: list[int] = []
        for p in (0, 1):
            end = read_port(oracle, p)
            print(f"ending config port {p}: {end}")
            if linux and end != start[p] and only_unresolved(end, start[p]):
                unresolved_only.append(p)
                continue
            assert end == start[p], f"port {p} is not back in its starting config"
        if unresolved_only:
            # the same recorded linux_based limit as test_session_port_config_matches_the_client_oracle
            pytest.xfail(f"linux_based: ports {unresolved_only} end unresolved after the VLAN step "
                         "(recorded limit)")
    finally:
        for cid in oracle_ids:
            try:
                oracle_stop(oracle, cid)
            except Exception as e:                       # noqa: BLE001
                print(f"cleanup oracle capture {cid}: {e}")
        oracle.disconnect(stop_traffic=False, release_ports=False)
