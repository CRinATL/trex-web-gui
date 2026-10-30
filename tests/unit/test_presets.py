"""The packet presets (P4b Task 11): flood, IP sweep, frame-size sweep and VLAN. The documents are checked
as data, and built and turned into streams through the real engine."""
import base64
import json
from typing import Any

import pytest
from test_packet_engine import with_engine
from trex_web_gui.studio.packetdoc import PacketDoc, model_of, parse_doc
from trex_web_gui.studio.presets import PRESETS, PresetError, PresetNotFound, defaults, preset
from trex_web_gui.trexapi.packet import PacketEngine

VARYING = {"frame": {"mode": "inc", "min": 64, "max": 128}}
STEPPED_MACS = {"src_mac": {"mode": "inc", "value": "00:00:00:00:00:10", "count": 5, "step": 2},
                "dst_mac": {"mode": "dec", "value": "aa:bb:cc:dd:ee:ff", "count": 4, "step": 3}}


def ids(doc: PacketDoc) -> list[str]:
    """The ids of a document's Field Engine instructions."""
    return [i["id"] for i in doc["feInstructions"]]


def param(doc: PacketDoc, name: str) -> dict[str, Any]:
    """The parameters of the flow var called `name`."""
    return next(i["parameters"] for i in doc["feInstructions"]
                if i["id"] == "STLVmFlowVar" and i["parameters"]["name"] == name)


def field(doc: PacketDoc, layer: str, name: str) -> Any:
    """The value of field `name` in the first layer `layer`."""
    return next(f["value"] for lay in doc["packet"] if lay["id"] == layer
                for f in lay["fields"] if f["id"] == name)


async def built(engine: PacketEngine, doc: PacketDoc) -> int:
    """Build `doc` and make a stream of it; the frame length without the FCS."""
    packet, fe = model_of(parse_doc(doc))
    result = await engine.build(packet, fe)
    assert result["error"] is None, result["error"]
    assert result["field_engine"] is None or result["field_engine"]["error"] is None
    assert result["binary"] is not None
    await engine.to_stream(packet, fe)
    return len(base64.b64decode(result["binary"]))


def test_each_preset_builds() -> None:
    cases = [("flood", {}), ("flood", {"l4": "tcp", **VARYING}), ("flood", {**VARYING, **STEPPED_MACS}),
             ("ip_sweep", {}),
             ("ip_sweep", {"which": "src", "checksum": "hw",
                           "src": {"start": "10.0.0.1", "end": "10.0.0.9", "op": "random"}}),
             ("frame_size_sweep", {}), ("frame_size_sweep", {"min": 100, "max": 200, "step": 4, "op": "dec"}),
             ("vlan", {}), ("vlan", {"tags": [{"vlan": 5, "prio": 3}, {"vlan": 4094, "prio": 7}]})]

    async def body(e: PacketEngine) -> None:
        assert {p for p, _ in cases} == set(PRESETS)
        for pid, params in cases:
            await built(e, preset(pid, params)["doc"])
        assert await built(e, preset("flood", {"frame": {"min": 200, "max": 300}})["doc"]) == 200 - 4
        tcp = preset("flood", {"l4": "tcp", "frame": {"min": 90, "max": 90}})["doc"]
        assert await built(e, tcp) == 90 - 4
        assert await built(e, preset("frame_size_sweep", {"min": 64, "max": 9238})["doc"]) == 9238 - 4
    with_engine(body)


def test_flood_varying_frame_instructions() -> None:
    doc = preset("flood", VARYING)["doc"]
    assert doc["feInstructions"] == [
        {"id": "STLVmFlowVar", "parameters": {"name": "pkt_len", "min_value": "60", "max_value": "124",
                                              "size": "2", "op": "inc", "step": "1"}},
        {"id": "STLVmTrimPktSize", "parameters": {"fv_name": "pkt_len"}},
        {"id": "STLVmWrFlowVar",
         "parameters": {"fv_name": "pkt_len", "pkt_offset": "IP.len", "add_val": "-14"}},
        {"id": "STLVmWrFlowVar",
         "parameters": {"fv_name": "pkt_len", "pkt_offset": "UDP.len", "add_val": "-34"}},
        {"id": "STLVmFixChecksumHw", "parameters": {"l3_offset": "IP", "l4_offset": "UDP", "l4_type": "11"}}]
    assert field(doc, "Raw", "load")["total_size"] == 128
    tcp = preset("flood", {"l4": "tcp", "frame": {"mode": "random", "min": 70, "max": 80}})["doc"]
    assert ids(tcp) == ["STLVmFlowVar", "STLVmTrimPktSize", "STLVmWrFlowVar", "STLVmFixChecksumHw"]
    assert param(tcp, "pkt_len")["op"] == "random"
    assert tcp["feInstructions"][-1]["parameters"] == {"l3_offset": "IP", "l4_offset": "TCP", "l4_type": "13"}
    assert tcp["packet"][2]["id"] == "TCP"
    dec = preset("flood", {"frame": {"mode": "dec", "min": 64, "max": 65}})["doc"]
    assert param(dec, "pkt_len")["op"] == "dec"


def test_flood_fixed_frame_has_no_trim() -> None:
    doc = preset("flood", {"frame": {"mode": "fixed", "min": 100, "max": 200}, "payload": "abc"})["doc"]
    assert ids(doc) == ["STLVmFixChecksumHw"]
    load = field(doc, "Raw", "load")
    assert load["total_size"] == 100 and base64.b64decode(load["template_base64"]) == b"abc"
    assert [lay["id"] for lay in doc["packet"]] == ["Ether", "IP", "UDP", "Raw"]
    assert field(doc, "IP", "src") == "16.0.0.1" and field(doc, "UDP", "dport") == 12
    assert doc["fePrarameters"] == {}


def test_ip_sweep_both_with_fix_ipv4() -> None:
    doc = preset("ip_sweep", {"which": "both", "src": {"start": "16.0.0.1", "count": 10, "step": 2},
                              "dst": {"start": "48.0.0.1", "count": 3, "op": "dec"},
                              "checksum": "ipv4"})["doc"]
    assert ids(doc) == ["STLVmFlowVar", "STLVmWrFlowVar", "STLVmFlowVar", "STLVmWrFlowVar", "STLVmFixIpv4"]
    assert param(doc, "ip_src") == {"name": "ip_src", "min_value": "16.0.0.1", "max_value": "16.0.0.19",
                                    "size": "4", "op": "inc", "step": "2"}
    # dec counts down from the first address (R16): TRex starts a dec variable at max_value
    assert param(doc, "ip_dst") == {"name": "ip_dst", "min_value": "47.255.255.255", "max_value": "48.0.0.1",
                                    "size": "4", "op": "dec", "step": "1"}
    assert doc["feInstructions"][1]["parameters"] == {"fv_name": "ip_src", "pkt_offset": "IP.src"}
    assert doc["feInstructions"][-1]["parameters"] == {"offset": "IP"}
    hw = preset("ip_sweep", {"which": "dst", "checksum": "hw"})["doc"]
    assert ids(hw) == ["STLVmFlowVar", "STLVmWrFlowVar", "STLVmFixChecksumHw"]
    assert hw["feInstructions"][1]["parameters"]["pkt_offset"] == "IP.dst"


def test_ip_sweep_end_gives_the_count() -> None:
    def sweep(step: int) -> PacketDoc:
        src = {"start": "16.0.0.1", "end": "16.0.0.10", "step": step}
        return preset("ip_sweep", {"which": "src", "src": src})["doc"]
    doc = sweep(1)
    assert param(doc, "ip_src")["min_value"] == "16.0.0.1"
    assert param(doc, "ip_src")["max_value"] == "16.0.0.10"
    rounded = sweep(4)
    assert param(rounded, "ip_src")["max_value"] == "16.0.0.9"              # (10 - 1) / 4 + 1 = 3 addresses
    assert param(preset("ip_sweep", {"which": "src", "src": {"start": "1.0.0.1", "end": "1.0.0.1"}})["doc"],
                 "ip_src")["max_value"] == "1.0.0.1"


def test_ip_sweep_dec_counts_down_from_the_first_address() -> None:
    def dec(src: dict[str, Any]) -> dict[str, str]:
        return param(preset("ip_sweep", {"which": "src", "src": {**src, "op": "dec"}})["doc"], "ip_src")
    counted = dec({"start": "16.0.0.10", "count": 4, "step": 2})
    assert (counted["min_value"], counted["max_value"]) == ("16.0.0.4", "16.0.0.10")
    ended = dec({"start": "16.0.0.10", "end": "16.0.0.1"})
    assert (ended["min_value"], ended["max_value"]) == ("16.0.0.1", "16.0.0.10")
    rounded = dec({"start": "16.0.0.10", "end": "16.0.0.1", "step": 4})        # 10, 6, 2
    assert (rounded["min_value"], rounded["max_value"]) == ("16.0.0.2", "16.0.0.10")
    doc = preset("ip_sweep", {"which": "src", "src": {"start": "16.0.0.10", "count": 4, "op": "dec"}})["doc"]
    assert doc["packet"][1]["fields"][0] == {"id": "src", "value": "16.0.0.10"}      # the doc holds the start
    assert dec({"start": "0.0.0.3", "count": 4})["min_value"] == "0.0.0.0"
    assert dec({"start": "255.255.255.255", "count": 1})["max_value"] == "255.255.255.255"


@pytest.mark.parametrize("src, text", [
    ({"start": "16.0.0.1", "end": "16.0.0.10", "op": "dec"}, "above start"),
    ({"start": "0.0.0.2", "count": 4, "op": "dec"}, "below 0.0.0.0"),
    ({"start": "0.0.0.5", "count": 3, "step": 3, "op": "dec"}, "below 0.0.0.0")])
def test_ip_sweep_dec_refuses_a_bad_range(src: dict[str, Any], text: str) -> None:
    with pytest.raises(PresetError) as e:
        preset("ip_sweep", {"which": "src", "src": src})
    assert e.value.where.startswith("src") and text in str(e.value)


@pytest.mark.parametrize("src", [
    {"start": "16.0.0.1", "count": 5, "end": "16.0.0.9"}, {"start": "16.0.0.1"},
    {"start": "16.0.0.9", "end": "16.0.0.1"}, {"start": "255.255.255.250", "count": 10},
    {"start": "0.0.0.0", "end": "255.255.255.255"}])
def test_ip_sweep_bad_src_ranges_refused(src: dict[str, Any]) -> None:
    with pytest.raises(PresetError) as e:
        preset("ip_sweep", {"which": "src", "src": src})
    assert e.value.where.startswith("src")


def test_mac_inc_writes_low_32_bits() -> None:
    doc = preset("flood", STEPPED_MACS)["doc"]
    assert ids(doc) == ["STLVmFlowVar", "STLVmWrFlowVar", "STLVmFlowVar", "STLVmWrFlowVar",
                        "STLVmFixChecksumHw"]
    assert param(doc, "mac_src") == {"name": "mac_src", "min_value": "16", "max_value": "24", "size": "4",
                                     "op": "inc", "step": "2"}
    dst = param(doc, "mac_dst")                       # dec runs down from the value: ee:ff + ... low 32 bits
    low = int("ccddeeff", 16)
    assert dst["op"] == "dec" and dst["max_value"] == str(low) and dst["min_value"] == str(low - 9)
    writes = [i["parameters"] for i in doc["feInstructions"] if i["id"] == "STLVmWrFlowVar"]
    assert writes == [{"fv_name": "mac_src", "pkt_offset": "Ether.src", "offset_fixup": "2"},
                      {"fv_name": "mac_dst", "pkt_offset": "Ether.dst", "offset_fixup": "2"}]
    assert field(doc, "Ether", "src") == "00:00:00:00:00:10"
    assert field(doc, "Ether", "dst") == "aa:bb:cc:dd:ee:ff"
    for bad in ({"mode": "inc", "value": "00:00:00:00:ff:ff", "count": 65535, "step": 100000},
                {"mode": "dec", "value": "00:00:00:00:00:05", "count": 10}):
        with pytest.raises(PresetError):
            preset("flood", {"src_mac": bad})


def test_flags_bits() -> None:
    def flags(src: str, dst: str) -> int | None:
        return preset("flood", {"src_mac": {"mode": src}, "dst_mac": {"mode": dst}})["flags"]
    assert flags("trex_config", "trex_config") == 0
    assert flags("packet", "trex_config") == 1
    assert flags("trex_config", "packet") == 2
    assert flags("inc", "dec") == 3
    assert (flags("packet", "packet") or 0) >> 1 & 3 == 1
    packet = preset("flood", {"src_mac": {"mode": "packet", "value": "02:00:00:00:00:09"}})["doc"]
    assert field(packet, "Ether", "src") == "02:00:00:00:00:09" and ids(packet) == ["STLVmFixChecksumHw"]
    config = preset("flood", {})["doc"]
    assert config["packet"][0]["fields"] == []                   # the TRex configuration supplies both MACs
    for other in ("ip_sweep", "frame_size_sweep", "vlan"):
        assert preset(other, {})["flags"] is None


def test_vlan_two_tags() -> None:
    doc = preset("vlan", {"tags": [{"vlan": 10, "prio": 1}, {"vlan": 20, "prio": 2}]})["doc"]
    assert [lay["id"] for lay in doc["packet"]] == ["Ether", "Dot1Q", "Dot1Q", "IP", "UDP"]
    assert doc["feInstructions"] == [] and doc["fePrarameters"] == {}
    assert [(field(doc, "Dot1Q", "vlan"), field(doc, "Dot1Q", "prio"))] == [(10, 1)]
    assert [f["value"] for f in doc["packet"][2]["fields"]] == [20, 2]
    assert [lay["id"] for lay in preset("vlan", {})["doc"]["packet"]] == ["Ether", "Dot1Q", "IP", "UDP"]

    async def body(e: PacketEngine) -> None:
        packet, fe = model_of(parse_doc(doc))
        result = await e.build(packet, fe)
        assert [lay["id"] for lay in result["data"]] == ["Ether", "Dot1Q", "Dot1Q", "IP", "UDP"]
        vlans = [next(f["value"] for f in lay["fields"] if f["id"] == "vlan") for lay in result["data"][1:3]]
        assert vlans == [10, 20]
    with_engine(body)


@pytest.mark.parametrize("pid, params, where", [
    ("flood", {"l4": "icmp"}, "l4"), ("flood", {"nope": 1}, "nope"), ("flood", {"sport": "80"}, "sport"),
    ("flood", {"sport": 0}, "sport"), ("flood", {"dport": 65536}, "dport"),
    ("flood", {"src_ip": "16.0.0"}, "src_ip"), ("flood", {"dst_ip": "300.0.0.1"}, "dst_ip"),
    ("flood", {"payload": ""}, "payload"), ("flood", {"payload": "x" * 65}, "payload"),
    ("flood", {"payload": "café"}, "payload"), ("flood", {"payload": "a\nb"}, "payload"),
    ("flood", {"payload": "ab\n"}, "payload"),
    ("flood", {"dst_mac": {"value": "aa:bb:cc:dd:ee:ff\n"}}, "dst_mac.value"),
    ("flood", {"src_mac": {"mode": "inc", "count": 0}}, "src_mac.count"),
    ("flood", {"src_mac": {"mode": "inc", "count": 65536}}, "src_mac.count"),
    ("flood", {"dst_mac": {"step": 0}}, "dst_mac.step"),
    ("flood", {"dst_mac": {"value": "zz:00:00:00:00:00"}}, "dst_mac.value"),
    ("flood", {"frame": {"mode": "inc", "min": 63}}, "frame.min"),
    ("flood", {"frame": {"max": 9239}}, "frame.max"),
    ("flood", {"frame": {"min": 200, "max": 100}}, "frame"),
    ("flood", {"frame": {"mode": "x"}}, "frame.mode"),
    ("flood", [], "params"), ("flood", {"frame": 5}, "frame"),
    ("ip_sweep", {"which": "src", "dst": {"start": "1.1.1.1", "count": 1}}, "params"),
    ("ip_sweep", {"which": "some"}, "which"), ("ip_sweep", {"checksum": "crc"}, "checksum"),
    ("ip_sweep", {"src": {"start": "1.1.1.1", "count": 0}}, "src.count"),
    ("ip_sweep", {"src": {"start": "1.1.1.1", "count": 16777217}}, "src.count"),
    ("ip_sweep", {"src": {"start": "1.1.1.1", "count": 1, "step": 0}}, "src.step"),
    ("ip_sweep", {"src": {"start": "1.1.1.1", "count": 1, "op": "up"}}, "src.op"),
    ("ip_sweep", {"src": {"count": 1}}, "src.start"),
    ("frame_size_sweep", {"min": 63}, "min"), ("frame_size_sweep", {"step": 0}, "step"),
    ("frame_size_sweep", {"min": 500, "max": 400}, "params"), ("frame_size_sweep", {"op": "x"}, "op"),
    ("vlan", {"tags": []}, "tags"), ("vlan", {"tags": [{"vlan": 1}] * 3}, "tags"),
    ("vlan", {"tags": [{"vlan": 0}]}, "tags.0.vlan"), ("vlan", {"tags": [{"vlan": 4095}]}, "tags.0.vlan"),
    ("vlan", {"tags": [{"vlan": 1, "prio": 8}]}, "tags.0.prio"),
    ("vlan", {"tags": [{"prio": 1}]}, "tags.0.vlan")])
def test_params_refused(pid: str, params: Any, where: str) -> None:
    with pytest.raises(PresetError) as e:
        preset(pid, params)
    assert e.value.where == where or (where != "params" and e.value.where.startswith(where + ".")), e.value
    assert str(e.value).startswith(e.value.where + ": ")


def test_unknown_preset_and_defaults() -> None:
    with pytest.raises(PresetNotFound):
        preset("simple", {})
    for pid in PRESETS:
        assert preset(pid, defaults(pid)) == preset(pid, {})          # the form's starting values are valid


def test_no_split_by_var() -> None:
    cases = [("flood", {**VARYING, **STEPPED_MACS}), ("ip_sweep", {}), ("frame_size_sweep", {}), ("vlan", {})]
    for pid, params in cases:
        assert "split_by_var" not in json.dumps(preset(pid, params))

    async def body(e: PacketEngine) -> None:
        for pid, params in cases:
            packet, fe = model_of(parse_doc(preset(pid, params)["doc"]))
            assert "split_by_var" not in json.dumps((await e.to_stream(packet, fe))["vm"])
    with_engine(body)
