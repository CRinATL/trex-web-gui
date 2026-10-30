"""TestFX's expected outputs as backend tests (P4b Task 3; behaviour only, from TestPacketEditorUI.java).

Each test keeps the name of the Java test it ports and checks the value that test expected, on the real
packet worker. The Java cases that are only about the UI (collapse state, context menus, ESC, undo) are
Task 6 and Task 7 vitest cases."""
import base64
import struct
from pathlib import Path
from typing import Any

import pytest
from test_packet_engine import b64, rebuilt, with_engine, write_pcap
from trex_web_gui.studio.packetdoc import model_of
from trex_web_gui.trexapi.packet import PacketEngine, PacketError

ETHER_RAW = [{"id": "Ether"}, {"id": "Raw"}]
ETHER_ONLY = [{"id": "Ether"}]
# Ether.type 0x9000 is named by the host's /etc/ethertypes, not by the editor: a host whose table has no
# entry shows "0x9000", an older (ebtables-era) table shows "LOOP". Both are the same number (ruling R12).
ETHER_TYPE_DEFAULT = {"0x9000", "LOOP"}
SEQ = 951057939


def raw_load(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Ether / Raw whose `load` is the BYTES `value`."""
    return [{"id": "Ether"}, {"id": "Raw", "fields": [{"id": "load", "value": value}]}]


async def hvalues(e: PacketEngine, packet: list[dict[str, Any]], layer: int, field: str) -> str:
    """The displayed value of `field` in layer `layer` of the built `packet`."""
    r = await e.build(packet, None)
    assert r["error"] is None, r["error"]
    return str(next(f for f in r["data"][layer]["fields"] if f["id"] == field)["hvalue"])


def ether_type(value: str) -> list[dict[str, Any]]:
    """An Ether whose `type` is the text `value`."""
    return [{"id": "Ether", "fields": [{"id": "type", "value": value}]}]


def tcp_frame(e: PacketEngine, payload: int = 0) -> Any:
    """Coroutine: the bytes of Ether/IP/TCP(seq=SEQ) with a Raw of `payload` bytes (none when 0)."""
    async def go() -> bytes:
        """Build the frame."""
        layers: list[dict[str, Any]] = [
            {"id": "Ether"}, {"id": "IP"},
            {"id": "TCP", "fields": [{"id": "seq", "value": SEQ}, {"id": "dport", "value": 80}]}]
        if payload:
            layers.append({"id": "Raw", "fields": [{"id": "load", "value": {
                "vtype": "BYTES", "generate": "template_code", "template_code": "32", "size": payload}}]})
        r = await e.build(layers, None)
        assert r["error"] is None and r["binary"], r["error"]
        return base64.b64decode(r["binary"])
    return go()


def test_should_build_payload() -> None:
    async def body(e: PacketEngine) -> None:
        assert await hvalues(e, ETHER_RAW, 1, "load") == "dummy"         # an appended Raw is "dummy"
        text = {"vtype": "BYTES", "base64": base64.b64encode(b"dummy abc").decode()}
        assert await hvalues(e, raw_load(text), 1, "load") == "dummy abc"
    with_engine(body)


def test_should_build_payload_random_ascii() -> None:
    async def body(e: PacketEngine) -> None:
        value = {"vtype": "BYTES", "generate": "random_ascii", "size": 32}
        got = await hvalues(e, raw_load(value), 1, "load")
        assert got == 'G n<B2U/+IU0TA{(|GO.d2@"@{|f yri'
    with_engine(body)


def test_should_build_payload_random_non_ascii() -> None:
    async def body(e: PacketEngine) -> None:
        value = {"vtype": "BYTES", "generate": "random_bytes", "size": 32}
        got = await hvalues(e, raw_load(value), 1, "load")
        assert got == "<binary>"
    with_engine(body)


def test_should_build_payload_code() -> None:
    async def body(e: PacketEngine) -> None:
        code = {"vtype": "BYTES", "generate": "template_code", "template_code": "32", "size": 32}
        assert await hvalues(e, raw_load(code), 1, "load") == "2" * 32
    with_engine(body)


def test_should_build_payload_code_no_size() -> None:
    async def body(e: PacketEngine) -> None:
        code = {"vtype": "BYTES", "generate": "template_code", "template_code": "3232323232"}
        assert await hvalues(e, raw_load(code), 1, "load") == "22222"
    with_engine(body)


def test_should_build_tcpip_stack() -> None:
    async def body(e: PacketEngine) -> None:
        packet = [{"id": "Ether"}, {"id": "IP"}]
        assert await hvalues(e, packet, 1, "version") == "4"
        assert await hvalues(e, packet, 0, "type") == "IPv4"             # the type follows the next layer
    with_engine(body)


def test_should_set_enum_value_as_text() -> None:
    async def body(e: PacketEngine) -> None:
        assert await hvalues(e, ether_type("0x800"), 0, "type") == "IPv4"
        # The Java test expected the default to read LOOP. The name comes from the host's /etc/ethertypes:
        # a host with no entry for 0x9000 reads its number, an older table reads LOOP.
        assert await hvalues(e, ether_type(""), 0, "type") in ETHER_TYPE_DEFAULT
        assert await hvalues(e, ether_type("0x86DD"), 0, "type") == "IPv6"
    with_engine(body)


def test_should_create_nested_ether() -> None:
    async def body(e: PacketEngine) -> None:
        packet = [{"id": "Ether"}, {"id": "Ether"}]                       # Ether/Ether is Dot3/Ether
        assert await hvalues(e, packet, 0, "type") == "0x1"
        assert await hvalues(e, packet, 1, "type") in ETHER_TYPE_DEFAULT  # the inner default
        r = await e.build(packet, None)
        assert [layer["real_id"] for layer in r["data"]] == ["Dot3", None]
    with_engine(body)


def test_shoud_has_as_raw_title_postfix() -> None:
    async def body(e: PacketEngine) -> None:
        r = await e.build([{"id": "Ether"}, {"id": "TCP"}, {"id": "IP"}], None)
        assert r["error"] is None
        assert r["data"][1]["id"] == "TCP" and r["data"][1]["real_id"] == "Raw"   # shown "as Raw"
    with_engine(body)


def test_should_change_ether_type_and_back() -> None:
    async def body(e: PacketEngine) -> None:
        ip = {"id": "IP", "fields": [{"id": "src", "value": "127.0.1.2"}]}
        assert await hvalues(e, [*ether_type("0x9000"), ip], 0, "type") in ETHER_TYPE_DEFAULT
        back = [*ether_type("IPv4"), ip]
        assert await hvalues(e, back, 0, "type") == "IPv4"
        assert await hvalues(e, back, 1, "src") == "127.0.1.2"
        r = await e.build(back, None)
        assert base64.b64decode(r["binary"])[12:14] == b"\x08\x00"
    with_engine(body)


def test_template_tcp_syn() -> None:
    async def body(e: PacketEngine) -> None:
        doc = await e.template("TCP-SYN")
        packet, fe = model_of(doc)
        r = await e.build(packet, fe)
        assert r["error"] is None
        tcp = next(layer for layer in r["data"] if layer["id"] == "TCP")
        assert next(f for f in tcp["fields"] if f["id"] == "flags")["hvalue"] == "S"
    with_engine(body)


def test_should_load_template() -> None:
    async def body(e: PacketEngine) -> None:
        packet, fe = model_of(await e.template("IPv6/UDP"))
        r = await e.build(packet, fe)
        assert r["error"] is None
        udp = next(layer for layer in r["data"] if layer["id"] == "UDP")
        assert next(f for f in udp["fields"] if f["id"] == "dport")["hvalue"] == "53"
    with_engine(body)


async def field_of(e: PacketEngine, raw: bytes, layer_id: str, field: str) -> str:
    """The displayed `field` of the first `layer_id` layer in the document derived from `raw`."""
    res = await e.doc_from_binary(b64(raw), None)
    packet, fe = model_of(res["doc"])
    r = await e.build(packet, fe)
    assert r["error"] is None, r["error"]
    layer = next(layer for layer in r["data"] if layer["id"] == layer_id)
    return str(next(f for f in layer["fields"] if f["id"] == field)["hvalue"])


def test_load_pcap_file(tmp_path: Path) -> None:
    path = tmp_path / "http.pcap"

    async def body(e: PacketEngine) -> None:
        write_pcap(path, [await tcp_frame(e)])
        page = await e.pcap_list(str(path), 0, 100)
        assert page["total"] == 1 and "TCP" in page["rows"][0]["summary"]
        raw = base64.b64decode(await e.pcap_packet(str(path), 0))
        assert await field_of(e, raw, "IP", "version") == "4"
        assert await field_of(e, raw, "TCP", "seq") == str(SEQ)
    with_engine(body)


def test_load_and_save_pcap_file(tmp_path: Path) -> None:
    first, second = tmp_path / "http.pcap", tmp_path / "http-2.pcap"

    async def body(e: PacketEngine) -> None:
        write_pcap(first, [await tcp_frame(e)])
        loaded = base64.b64decode(await e.pcap_packet(str(first), 0))
        doc = (await e.doc_from_binary(b64(loaded), None))["doc"]
        write_pcap(second, [await rebuilt(e, doc)])                 # Save as pcap: the built bytes, written
        again = base64.b64decode(await e.pcap_packet(str(second), 0))
        assert again == loaded
        assert await field_of(e, again, "IP", "version") == "4"
        assert await field_of(e, again, "TCP", "seq") == str(SEQ)
    with_engine(body)


def test_should_support_large_payload(tmp_path: Path) -> None:
    path = tmp_path / "payload_64k.pcap"
    size = 65535 - 14 - 20 - 20      # the largest TCP payload in a frame RawPcapReader returns whole

    async def body(e: PacketEngine) -> None:
        frame = await tcp_frame(e, size)
        assert struct.unpack_from(">H", frame, 16)[0] == 20 + 20 + size    # the IP length still fits
        with path.open("wb") as f:                                   # the snaplen is above the frame
            f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 1 << 18, 1))
            f.write(struct.pack("<IIII", 0, 0, len(frame), len(frame)) + frame)
        raw = base64.b64decode(await e.pcap_packet(str(path), 0))
        assert raw == frame and len(raw) == 14 + 20 + 20 + size
        res = await e.doc_from_binary(b64(raw), None)
        assert [layer["id"] for layer in res["doc"]["packet"]][:3] == ["Ether", "IP", "TCP"]
        packet, fe = model_of(res["doc"])
        r = await e.build(packet, fe)
        assert r["error"] is None and r["binary"]
        assert next(f for f in r["data"][1]["fields"] if f["id"] == "version")["hvalue"] == "4"
        assert r["binary"] == b64(raw)
        assert any(layer["id"] == "Raw" for layer in r["data"])      # the payload layer is shown
    with_engine(body)


def test_load_pcap_file_neg(tmp_path: Path) -> None:
    path = tmp_path / "http.bad-pcap"

    async def body(e: PacketEngine) -> None:
        write_pcap(path, [await tcp_frame(e)])
        path.write_bytes(path.read_bytes()[:10])                    # cut inside the file header
        with pytest.raises(PacketError) as ei:
            await e.pcap_list(str(path), 0, 100)
        assert ei.value.restarted is False
    with_engine(body)

