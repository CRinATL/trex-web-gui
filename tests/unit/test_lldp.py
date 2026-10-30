"""LLDP neighbours (P3c Task 11): the stdlib TLV parser in trexapi/lldp.py, its expiry, the session's
per-monitor table, and GET /api/capture/lldp. Frames are packed here with struct (IEEE 802.1AB TLVs: a 7-bit
type, a 9-bit length, the value): the vendored Scapy's contrib/lldp.py cannot build or dissect them (the
Task 11 ruling; evidence in the workspace's task-11-lldp-contrib-broken.log), and the parser never
imports it."""
import asyncio
import base64
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from support import ROOT, authed
from test_session_capture import Rig, pkt, refused
from trex_web_gui import trexapi
from trex_web_gui.api.app import create_app
from trex_web_gui.sessions import ConnectParams, SessionState, TrexSession
from trex_web_gui.sessions import capture as capture_mod
from trex_web_gui.trexapi import Fetched, decode, lldp

trexapi.load()      # decode.summary needs the vendored Scapy on the path

NOW = 1000.0
SRC = bytes.fromhex("001122334455")
NEAREST_BRIDGE = bytes.fromhex("0180c200000e")
FIXTURE = ROOT / "frontend" / "src" / "__fixtures__" / "lldpNeighbours.json"


def tlv(t: int, value: bytes) -> bytes:
    """One LLDP TLV: a 7-bit type and 9-bit length header, then the value."""
    return struct.pack("!H", (t << 9) | len(value)) + value


def mgmt(subtype: int, address: bytes, oid: bytes = b"", n: int | None = None) -> bytes:
    """A Management Address TLV: string length n (subtype + address), subtype, address, ifIndex numbering
    subtype and number, the OID string length and the OID."""
    n = len(address) + 1 if n is None else n
    return tlv(8, bytes([n, subtype]) + address + b"\x02" + struct.pack("!I", 7) + bytes([len(oid)]) + oid)


def lldpdu(ttl: int = 120, port_id: bytes = b"Gi1/0/1", extra: bytes | None = None, *,
           chassis: bytes = b"\x04" + SRC, port: bytes | None = None, end: bytes = tlv(0, b"")) -> bytes:
    """A switch's LLDPDU: chassis ID a MAC, port ID an ifName, the optional TLVs a switch usually sends, End.
    `chassis` and `port` are whole TLV values (subtype byte first); `port` defaults to ifName `port_id`."""
    optional = (tlv(4, b"uplink to lab") + tlv(5, b"sw1") + tlv(6, b"Switch OS 1.0")
                + tlv(7, struct.pack("!HH", 0x0014, 0x0004))       # bridge+router; bridge enabled
                + mgmt(1, bytes([192, 168, 0, 1]))
                + tlv(127, bytes.fromhex("0080c2") + b"\x01\x00\x0a"))          # an org TLV: skipped
    return (tlv(1, chassis) + tlv(2, b"\x05" + port_id if port is None else port)
            + tlv(3, struct.pack("!H", ttl))
            + (optional if extra is None else extra) + end)


def frame(pdu: bytes, vlans: tuple[int, ...] = (), tpid: int = 0x8100) -> bytes:
    """An Ethernet frame to the nearest-bridge multicast address: one VLAN tag per vlans entry, then pdu."""
    tags = b"".join(struct.pack("!HH", tpid, v) for v in vlans)
    return NEAREST_BRIDGE + SRC + tags + b"\x88\xcc" + pdu


def test_parse_lldp_frame_fields() -> None:
    n = lldp.parse(frame(lldpdu()), 1, NOW)
    assert n == {
        "port": 1, "vlan": None, "src": "00:11:22:33:44:55",
        "chassis_id": "00:11:22:33:44:55", "chassis_id_subtype": "MAC address",
        "port_id": "Gi1/0/1", "port_id_subtype": "interface name", "ttl": 120,
        "system_name": "sw1", "system_description": "Switch OS 1.0", "port_description": "uplink to lab",
        "management_addresses": ["192.168.0.1"], "capabilities": ["bridge"],
        "last_seen": NOW, "expires": NOW + 120}


def test_parse_vlan_tagged_lldp() -> None:
    n = lldp.parse(frame(lldpdu(), vlans=(10,)), 0, NOW)
    assert n is not None and n["vlan"] == 10 and n["port_id"] == "Gi1/0/1" and n["ttl"] == 120
    q = lldp.parse(frame(lldpdu(), vlans=(100, 10)), 0, NOW)                    # stacked: the outer tag
    assert q is not None and q["vlan"] == 100 and q["system_name"] == "sw1"


def test_non_lldp_frame_is_none() -> None:
    udp = base64.b64decode(pkt()["binary"])
    assert lldp.parse(udp, 1, NOW) is None
    # tag, then IPv4
    assert lldp.parse(frame(lldpdu(), vlans=(10,))[:16] + b"\x08\x00" + lldpdu(), 1, NOW) is None
    assert lldp.parse(b"", 1, NOW) is None


@pytest.mark.parametrize("bad", [
    pytest.param(frame(lldpdu())[:30], id="truncated mid-TLV"),
    pytest.param(frame(lldpdu())[:13], id="truncated in the Ethernet header"),
    pytest.param(frame(tlv(1, b"\x04" + SRC) + struct.pack("!H", (2 << 9) | 200) + b"\x05x"),
                 id="length past the end"),
    pytest.param(frame(tlv(1, b"\x04" + SRC) + tlv(2, b"\x05x") + tlv(0, b"")), id="no TTL"),
    pytest.param(frame(tlv(2, b"\x05x") + tlv(1, b"\x04" + SRC) + tlv(3, b"\x00\x78") + tlv(0, b"")),
                 id="out of order"),
    pytest.param(frame(tlv(1, b"\x04") + tlv(2, b"\x05x") + tlv(3, b"\x00\x78") + tlv(0, b"")),
                 id="empty chassis ID"),
    pytest.param(frame(tlv(1, b"\x04" + SRC) + tlv(2, b"\x05x") + tlv(3, b"\x78") + tlv(0, b"")),
                 id="1-byte TTL"),
    pytest.param(frame(lldpdu(extra=tlv(1, b"\x04" + SRC))), id="a second chassis ID"),
    pytest.param(frame(lldpdu(extra=tlv(5, b"sw1") + b"\x0a", end=b"")),
                 id="a dangling byte after the last TLV"),
])
def test_truncated_or_malformed_frame_is_none(bad: bytes) -> None:
    assert lldp.parse(bad, 1, NOW) is None


V6 = bytes.fromhex("20010db8000000000000000000000001")
IPV4 = bytes([10, 0, 0, 1])
OPT = tlv(5, b"sw1")        # an optional TLV that must survive a malformed neighbour TLV


@pytest.mark.parametrize("bad_tlv, field, empty", [
    pytest.param(tlv(7, b"\x00\x04"), "capabilities", [], id="capabilities of length 2"),
    pytest.param(tlv(7, b"\x00\x04\x00\x04\x00"), "capabilities", [], id="capabilities of length 5"),
    pytest.param(tlv(8, bytes([1, 1, 1, 1, 1, 1, 1, 1])), "management_addresses", [], id="management n=1"),
    pytest.param(tlv(8, bytes([5, 1]) + IPV4 + b"\x02" + struct.pack("!I", 7)), "management_addresses", [],
                 id="management without the OID length octet"),
    pytest.param(mgmt(1, bytes(32), n=33), "management_addresses", [], id="management n=33"),
    pytest.param(tlv(8, bytes([5, 1]) + IPV4 + b"\x02" + struct.pack("!I", 7) + b"\x05ab"),
                 "management_addresses", [], id="management OID past its TLV"),
])
def test_a_malformed_optional_tlv_is_skipped_and_the_neighbour_kept(bad_tlv: bytes, field: str,
                                                                    empty: Any) -> None:
    """802.1AB 9.2.7.7: an optional TLV that fails validation is discarded; the rest of the LLDPDU stands."""
    n = lldp.parse(frame(lldpdu(extra=bad_tlv + OPT)), 1, NOW)
    assert n is not None and n[field] == empty            # type: ignore[literal-required]
    assert n["system_name"] == "sw1" and n["port_id"] == "Gi1/0/1"


@pytest.mark.parametrize("raw, want", [
    pytest.param(frame(lldpdu(), (20,), tpid=0x88A8), {"vlan": 20}, id="TPID 0x88a8"),
    pytest.param(frame(lldpdu(), (30,), tpid=0x9100), {"vlan": 30}, id="TPID 0x9100"),
    pytest.param(frame(lldpdu(chassis=b"\x05\x01" + IPV4)),
                 {"chassis_id": "10.0.0.1", "chassis_id_subtype": "network address"}, id="chassis IPv4"),
    pytest.param(frame(lldpdu(chassis=b"\x05\x02" + V6)), {"chassis_id": "2001:db8::1"}, id="chassis IPv6"),
    pytest.param(frame(lldpdu(chassis=b"\x05\x06" + SRC)), {"chassis_id": "00:11:22:33:44:55"},
                 id="chassis 802"),
    pytest.param(frame(lldpdu(chassis=b"\x05\x03\xab\xcd")), {"chassis_id": "abcd"},
                 id="chassis other family"),
    pytest.param(frame(lldpdu(port=b"\x04\x01" + IPV4)),
                 {"port_id": "10.0.0.1", "port_id_subtype": "network address"}, id="port IPv4"),
    pytest.param(frame(lldpdu(port=b"\x04\x02" + V6)), {"port_id": "2001:db8::1"}, id="port IPv6"),
    pytest.param(frame(lldpdu(port=b"\x04\x06" + SRC)), {"port_id": "00:11:22:33:44:55"}, id="port 802"),
    pytest.param(frame(lldpdu(port=b"\x04\x03\xab\xcd")), {"port_id": "abcd"}, id="port other family"),
    pytest.param(frame(lldpdu(port=b"\x03" + SRC)),
                 {"port_id": "00:11:22:33:44:55", "port_id_subtype": "MAC address"}, id="port MAC subtype"),
    pytest.param(frame(lldpdu(extra=mgmt(2, V6))), {"management_addresses": ["2001:db8::1"]},
                 id="IPv6 management"),
    pytest.param(frame(lldpdu(extra=mgmt(1, IPV4, oid=b"\x2b\x06\x01\x02\x01"))),
                 {"management_addresses": ["10.0.0.1"]}, id="management address with an OID"),
    pytest.param(frame(lldpdu(port=b"\x07\xff\xfe")),
                 {"port_id": "fffe", "port_id_subtype": "locally assigned"}, id="non-UTF-8 ID as hex"),
    pytest.param(frame(lldpdu(chassis=b"\x00abc")),
                 {"chassis_id": "abc", "chassis_id_subtype": "reserved (0)"}, id="reserved subtype"),
    pytest.param(frame(lldpdu(extra=OPT, end=b"")) + bytes(10), {"system_name": "sw1"},
                 id="no End, zero padding"),
    pytest.param(frame(lldpdu(extra=OPT, end=b"")), {"system_name": "sw1"}, id="no End, no padding"),
    pytest.param(frame(lldpdu(extra=OPT, end=struct.pack("!H", 3) + b"abc")), {"system_name": "sw1"},
                 id="End with a nonzero length"),
    pytest.param(frame(lldpdu(ttl=65535)), {"ttl": 65535, "expires": NOW + 65535}, id="TTL 65535"),
])
def test_parse_edge_cases(raw: bytes, want: dict[str, Any]) -> None:
    n = lldp.parse(raw, 1, NOW)
    assert n is not None and {k: n[k] for k in want} == want    # type: ignore[literal-required]


def test_expiry_after_ttl() -> None:
    table: dict[Any, lldp.LldpNeighbour] = {}
    a = lldp.parse(frame(lldpdu(ttl=30)), 1, NOW)
    b = lldp.parse(frame(lldpdu(ttl=120, port_id=b"Gi1/0/2")), 1, NOW)
    assert a is not None and b is not None
    lldp.merge(table, [a, b])
    assert [n["port_id"] for n in lldp.current(table, NOW + 29.9)] == ["Gi1/0/1", "Gi1/0/2"]
    assert [n["port_id"] for n in lldp.current(table, NOW + 30)] == ["Gi1/0/2"]     # now >= last_seen + ttl
    assert len(table) == 1                                                          # dropped, not just hidden
    again = lldp.parse(frame(lldpdu(ttl=120, port_id=b"Gi1/0/2")), 1, NOW + 100)  # refreshed: a new deadline
    assert again is not None
    lldp.merge(table, [again])
    assert [n["expires"] for n in lldp.current(table, NOW + 150)] == [NOW + 220]
    bye = lldp.parse(frame(lldpdu(ttl=0, port_id=b"Gi1/0/2")), 1, NOW + 160)      # 802.1AB shutdown LLDPDU
    assert bye is not None
    lldp.merge(table, [bye])
    assert lldp.current(table, NOW + 160) == []


def test_one_neighbour_heard_tagged_and_untagged_is_two_rows() -> None:
    table: dict[Any, lldp.LldpNeighbour] = {}
    seen = [lldp.parse(frame(lldpdu()), 1, NOW), lldp.parse(frame(lldpdu(), (10,)), 1, NOW),
            # the same as the second: replaces
            lldp.parse(frame(lldpdu(), (10,)), 1, NOW + 1)]
    lldp.merge(table, [n for n in seen if n is not None])
    rows = lldp.current(table, NOW + 2)
    assert [(n["vlan"], n["last_seen"]) for n in rows] == [(None, NOW), (10, NOW + 1)]


def test_parser_never_loads_the_scapy_contrib() -> None:
    lldp.parse(frame(lldpdu()), 1, NOW)
    decode.summary(frame(lldpdu()))
    assert "scapy.contrib.lldp" not in sys.modules


def test_decode_labels_lldp_rows() -> None:
    assert decode.summary(frame(lldpdu())).proto == "LLDP"
    assert decode.summary(frame(lldpdu(), vlans=(10,))).proto == "LLDP"
    assert decode.summary(base64.b64decode(pkt()["binary"])).proto == "UDP"


# -- the session: one table per monitor -----------------------------------------------------------
@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Rig:
    """A test_session_capture Rig on this test's monkeypatch and tmp_path; the monkeypatch undoes the
    patches."""
    return Rig(monkeypatch, tmp_path)


def lldp_pkt(i: int, pdu: bytes | None = None, port: int = 1) -> dict[str, Any]:
    """A capture packet dict whose binary is the base64 LLDP frame of pdu (a default LLDPDU if None)."""
    raw = frame(pdu or lldpdu())
    return {**pkt(i), "binary": base64.b64encode(raw).decode("ascii"), "port": port, "wirelen": len(raw)}


def test_monitor_batches_feed_the_table_and_each_new_monitor_starts_empty(rig: Rig) -> None:
    rig.server.queue(1, Fetched([lldp_pkt(1), pkt(2)], 0, 100.0))

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], lldp.BPF)
        await rig.until(lambda: bool(s.lldp_neighbours()))
        assert [(n["port"], n["port_id"]) for n in s.lldp_neighbours()] == [(1, "Gi1/0/1")]
        rows = rig.of("capture_pkts")[0]["rows"]
        assert [r["proto"] for r in rows] == ["LLDP", "UDP"]           # the table still gets every row
        await s.monitor_stop()
        assert s.lldp_neighbours() == []                                 # stopped: forgotten
        rig.server.queue(2, Fetched([lldp_pkt(1)], 0, 100.0))
        await s.monitor_start([], [1], lldp.BPF)
        await rig.until(lambda: bool(s.lldp_neighbours()))
        await s.disconnect()                                             # disconnect forgets it too
        assert s.lldp_neighbours() == []
    rig.run(body)


def test_a_reconnect_after_a_loss_forgets_the_old_monitors_neighbours(fake_trex: Any, tmp_path: Path) -> None:
    """LOST keeps the monitor and its table (no disconnect ran); the next connect() resets both. The LOST
    state is seeded as test_session_capture's test_reconnect_forgets_a_stale_monitor_id does."""
    f = fake_trex()
    s = TrexSession(lambda m: None, spool_dir=tmp_path / "spool")
    s._monitor = capture_mod.MonitorState(1, 1.0, [], [1], lldp.BPF, 100)
    heard = lldp.parse(frame(lldpdu()), 1, time.time())
    assert heard is not None
    lldp.merge(s._lldp, [heard])
    s._state = SessionState.LOST
    assert s.lldp_neighbours() != []

    async def go() -> None:
        try:
            await s.connect(ConnectParams(f.host, f.rpc_port, f.pub_port, 2.0))
            assert s.lldp_neighbours() == []
        finally:
            await s.close()
    asyncio.run(go())


def test_a_gone_monitor_forgets_its_neighbours(rig: Rig) -> None:
    rig.server.queue(1, Fetched([lldp_pkt(1)], 0, 100.0))

    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], lldp.BPF)
        await rig.until(lambda: bool(s.lldp_neighbours()))
        rig.server.hidden.add(1)                                         # removed elsewhere
        await rig.until(lambda: s._monitor is None)
        assert s.lldp_neighbours() == []
    rig.run(body)


def test_the_lldp_start_is_the_monitor_start(rig: Rig) -> None:
    async def body(s: TrexSession) -> None:
        await s.monitor_start([], [1], "udp")
        with refused("monitor_active"):
            await s.monitor_start([], [1], lldp.BPF)
        assert rig.server.n("start") == 1
    rig.run(body)


def test_route_answers_neighbours_and_the_firmware_note(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, token="t" * 32)) as c:
        r = c.get("/api/capture/lldp")
    assert r.status_code == 200
    assert r.json() == {"neighbours": [], "note": lldp.NOTE}
    assert "firmware" in lldp.NOTE and "X710" in lldp.NOTE


def test_frontend_fixture_is_the_route_body() -> None:
    """lldpNeighbours.json is what GET /api/capture/lldp answers for two parsed frames (written with
    WRITE_WIRE_FIXTURES=1, compared otherwise), so the frontend's fixture has the backend's shape."""
    table: dict[Any, lldp.LldpNeighbour] = {}
    seen = [lldp.parse(frame(lldpdu()), 1, NOW),
            lldp.parse(frame(lldpdu(port_id=b"Gi1/0/2", extra=b""), vlans=(10,)), 0, NOW)]
    lldp.merge(table, [n for n in seen if n is not None])
    body = {"neighbours": lldp.current(table, NOW + 1), "note": lldp.NOTE}
    if os.environ.get("WRITE_WIRE_FIXTURES"):
        FIXTURE.write_text(json.dumps(body, indent=2) + "\n")
    assert json.loads(FIXTURE.read_text()) == body
