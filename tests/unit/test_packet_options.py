"""The options codec (`packet_options.py`): option bytes to named rows and back, for IP and TCP."""
import base64
from typing import Any

import pytest
from trex_web_gui.trexapi import loader

loader.load()

from trex_web_gui.trexapi import packet_options as po  # noqa: E402  (Scapy needs the loader first)

RA = bytes.fromhex("0194040000000000")            # NOP | Router Alert | EOL | 2 zeros
RA12 = bytes.fromhex("940400000000000000000000")  # Router Alert | EOL | 7 zeros (Review Focus 1)
RR40 = bytes([7, 39, 4]) + bytes.fromhex("10000001") * 9 + b"\x00"     # RR with 9 routers | EOL: 40 bytes
TOO_LONG = "options are {n} bytes; the header holds 40 at most"


def roundtrip(layer: Any, data: bytes) -> bytes:
    """Decode `data` and encode the rows as the editor sends them back, unchanged."""
    d = po.decode(layer, data)
    return po.encode(layer, d["rows"], d["padding"], d["length"])


def b64(data: bytes) -> str:
    """Base64 text of `data`."""
    return base64.b64encode(data).decode()


def test_decode_names_options_and_counts_padding_after_eol() -> None:
    d = po.decode("IP", RA)
    assert [r["name"] for r in d["rows"]] == ["NOP", "Router_Alert", "EOL"]
    assert (d["padding"], d["length"], d["padded"], d["problem"], d["layer"]) == (2, 6, 0, None, "IP")
    assert d["rows"][1]["fields"] == {"alert": 0}
    assert base64.b64decode(d["rows"][1]["raw"]) == bytes.fromhex("94040000")
    assert d["rows"][1]["number"] == 0x94


def test_decode_walks_on_after_an_eol_that_is_not_followed_by_only_zeros() -> None:
    d = po.decode("IP", bytes.fromhex("0001000000000000"))
    assert [r["name"] for r in d["rows"]] == ["EOL", "NOP", "EOL", "EOL", "EOL", "EOL", "EOL", "EOL"]
    assert d["padding"] == 0


def test_encode_of_unedited_rows_is_the_input() -> None:
    for layer, data in (("IP", RA), ("IP", RA12), ("IP", RR40), ("TCP", bytes.fromhex("020405b401010402"))):
        assert roundtrip(layer, data) == data
    assert len(RR40) == 40
    assert [r["name"] for r in po.decode("IP", RR40)["rows"]] == ["RR", "EOL"]


def test_an_edited_row_is_rebuilt_and_the_rest_kept() -> None:
    d = po.decode("IP", RA)
    d["rows"][1]["fields"] = {"alert": 1}
    d["rows"][1]["edited"] = True
    assert po.encode("IP", d["rows"], d["padding"], d["length"]) == bytes.fromhex("0194040001000000")


def test_a_row_without_raw_is_built_even_when_edited_is_false() -> None:
    row: po.OptionRow = {"name": "Router_Alert", "fields": {"alert": "router_shall_examine_packet"}}
    assert po.encode("IP", [row], 0, 0) == bytes.fromhex("94040000")


def test_padding_is_recomputed_when_the_length_changes() -> None:
    d = po.decode("IP", RA)
    assert po.encode("IP", d["rows"][1:], d["padding"], d["length"]) == bytes.fromhex("9404000000000000")
    d12 = po.decode("IP", RA12)
    d12["rows"][0]["fields"] = {"alert": 5}
    d12["rows"][0]["edited"] = True
    out = po.encode("IP", d12["rows"], d12["padding"], d12["length"])
    assert out == bytes.fromhex("940400050000000000000000") and len(out) == 12


def test_a_row_the_class_cannot_reproduce_is_an_other_row() -> None:
    r = po.decode("IP", bytes.fromhex("14040000"))["rows"][0]
    assert (r["name"], r["number"], r["fields"]) == (None, 0x14, {"value": "0000"})
    assert po.decode("TCP", bytes.fromhex("050a0000000100000002"))["rows"][0]["name"] == "SAck"
    assert po.decode("TCP", bytes.fromhex("0507000000010000"))["rows"][0]["name"] is None
    for tail in ("1e01", "1e00"):
        data = bytes.fromhex(tail + "0101")
        d = po.decode("TCP", data)
        assert [r["name"] for r in d["rows"]] == [None, "NOP", "NOP"] and d["rows"][0]["number"] == 30
        assert base64.b64decode(d["rows"][0]["raw"]) == data[:2]
        assert roundtrip("TCP", data) == data


def test_malformed_bytes_report_the_problem() -> None:
    d = po.decode("IP", bytes.fromhex("070900"))
    assert d["rows"] == [] and d["problem"] == "option 7 at byte 0 claims 9 bytes, 3 remain"
    assert po.decode("IP", bytes.fromhex("07010000"))["problem"] == "option 7 at byte 0 has length 1, below 2"
    assert po.decode("IP", bytes.fromhex("0107"))["problem"] == "option 7 at byte 1 has no length byte"
    assert po.decode("TCP", bytes.fromhex("0209"))["problem"] == "option 2 at byte 0 claims 9 bytes, 2 remain"
    assert po.decode("TCP", bytes.fromhex("01"))["problem"] is None


def test_over_40_bytes_is_refused() -> None:
    nop: po.OptionRow = {"name": "NOP", "fields": {}}
    with pytest.raises(po.OptionsTooLong) as e:
        po.encode("IP", [nop] * 41, 0, 0)       # recomputed padding would make 44: the rows alone are named
    assert str(e.value) == TOO_LONG.format(n=41)
    with pytest.raises(po.OptionsTooLong):          # it stops at the 41st row, before it reads the next
        po.encode("IP", [nop] * 41 + [{"name": "Bogus"}], 0, 0)
    assert po.encode("IP", [nop] * 40, 0, 40) == b"\x01" * 40
    with pytest.raises(po.OptionsTooLong) as e:
        po.encode("IP", [nop] * 40, 4, 40)
    assert str(e.value) == TOO_LONG.format(n=44)
    assert po.encode("IP", [nop] * 39, 0, 0) == b"\x01" * 39 + b"\x00"


def bad(layer: Any, row: po.OptionRow) -> str:
    """Encode `row` and return the BadOption message."""
    with pytest.raises(po.BadOption) as e:
        po.encode(layer, [row], 0, 0)
    return str(e.value)


def ip(name: str, **fields: Any) -> po.OptionRow:
    """A named IP row without raw."""
    return {"name": name, "fields": fields}


def test_an_unknown_name_is_refused() -> None:
    assert "'Bogus'" in bad("IP", ip("Bogus"))
    assert "'MSS'" in bad("IP", ip("MSS", value=1))


def test_an_enum_value_that_is_not_a_label_or_an_int_is_refused() -> None:
    assert "Router_Alert field alert" in bad("IP", ip("Router_Alert", alert="x"))


def test_a_boolean_is_not_an_int() -> None:
    assert "Router_Alert field alert" in bad("IP", ip("Router_Alert", alert=True))


def test_an_int_out_of_range_is_refused() -> None:
    assert "outside 0..65535" in bad("IP", ip("Router_Alert", alert=65536))
    assert "outside 0..255" in bad("IP", ip("RR", pointer=256, routers=[]))


def test_an_unknown_field_key_is_refused() -> None:
    assert "no field bogus" in bad("IP", ip("Router_Alert", alert=0, bogus=1))


def test_a_missing_field_is_refused() -> None:
    assert "missing field alert" in bad("IP", ip("Router_Alert"))


def test_an_address_must_be_a_strict_dotted_quad() -> None:
    assert "RR field routers[0]" in bad("IP", ip("RR", pointer=4, routers=["16.0.0.0/8"]))
    assert "routers[0]" in bad("IP", ip("RR", pointer=4, routers=["localhost"]))
    assert "originator_ip" in bad("IP", ip("Traceroute", id=0, outbound_hops=0, return_hops=0,
                                           originator_ip="1.2.3"))
    assert "originator_ip" in bad("IP", ip("Traceroute", id=0, outbound_hops=0, return_hops=0,
                                           originator_ip=16777216))


def test_a_list_is_bounded_before_its_elements_are_coerced() -> None:
    assert "has 10 elements, at most 9" in bad("IP", ip("RR", pointer=4, routers=["x"] * 10))
    assert "at most 9" in bad("TCP", {"name": "SAck", "fields": {"value": [0] * 10}})
    assert "is not a list" in bad("IP", ip("RR", pointer=4, routers="1.2.3.4"))
    assert "not 2" in bad("TCP", {"name": "Timestamp", "fields": {"value": [1]}})
    assert "value[1]" in bad("TCP", {"name": "Timestamp", "fields": {"value": [1, 2 ** 32]}})


def test_hex_must_be_even_length_hexadecimal_of_its_size() -> None:
    sec = {"security": 0, "compartment": 0, "handling_restrictions": 0}
    assert "even-length" in bad("IP", ip("Security", **sec, transmission_control_code="abc"))
    assert "even-length" in bad("IP", ip("Security", **sec, transmission_control_code="zzzzzz"))
    assert "even-length" in bad("IP", ip("Security", **sec, transmission_control_code="aa bbcc"))
    assert "2 bytes, not 3" in bad("IP", ip("Security", **sec, transmission_control_code="aabb"))
    assert "over 38 bytes" in bad("TCP", {"name": "Mood", "fields": {"value": "00" * 39}})
    assert "not a hex string" in bad("TCP", {"name": "Mood", "fields": {"value": 5}})


def test_raw_must_be_strict_base64() -> None:
    assert "raw is not base64" in bad("IP", {"name": "NOP", "raw": "not base64!"})
    # lenient decoding would drop the "!" and give three bytes
    assert "raw is not base64" in bad("IP", {"name": "NOP", "raw": "A!AAA"})
    assert "raw is not base64" in bad("IP", {"name": "NOP", "raw": 5})   # type: ignore[typeddict-item]


def test_an_other_row_is_bounded() -> None:
    assert "outside 2..255" in bad("IP", {"name": None, "number": 1, "fields": {"value": ""}})
    assert "outside 2..255" in bad("IP", {"name": None, "number": 256, "fields": {"value": ""}})
    assert "over 38 bytes" in bad("IP", {"name": None, "number": 9, "fields": {"value": "00" * 39}})
    assert "exactly the field value" in bad("IP", {"name": None, "number": 9, "fields": {}})
    assert po.encode("IP", [{"name": None, "number": 9, "fields": {"value": "00" * 38}}], 0, 0) == (
        bytes([9, 40]) + bytes(38))


def test_fields_must_be_an_object() -> None:
    assert "not an object" in bad("IP", {"name": "NOP", "fields": []})   # type: ignore[typeddict-item]


def test_kinds_list_known_options_without_id_fields() -> None:
    ipk = {k["name"]: k for k in po.option_kinds("IP")}
    assert [f["name"] for f in ipk["Router_Alert"]["fields"]] == ["alert"]
    alert = ipk["Router_Alert"]["fields"][0]
    assert alert["kind"] == "enum" and alert["values"] == {"router_shall_examine_packet": 0}
    assert (alert["min"], alert["max"]) == (0, 65535)
    assert ipk["Traceroute"]["fields"][3] == {"name": "originator_ip", "kind": "address",
                                              "default": "0.0.0.0"}
    assert ipk["RR"]["fields"][1]["kind"] == "addresses"
    assert ipk["Security"]["fields"][3]["kind"] == "hex" and ipk["Security"]["fields"][3]["size"] == 3
    assert ipk["EOL"]["fields"] == [] and ipk["NOP"]["fields"] == []
    for kind in ipk.values():
        assert not {"copy_flag", "optclass", "option", "length"} & {f["name"] for f in kind["fields"]}
    numbers = [k["number"] for k in po.option_kinds("IP")]
    assert numbers == sorted(numbers) and ipk["Router_Alert"]["number"] == 0x94

    tcp = {k["name"]: k for k in po.option_kinds("TCP")}
    mss = tcp["MSS"]["fields"][0]
    assert (mss["kind"], mss["min"], mss["max"]) == ("int", 0, 65535)
    assert tcp["SAck"]["fields"][0]["widths"] == [4] and tcp["SAck"]["fields"][0]["variable"]
    assert tcp["Timestamp"]["fields"][0]["widths"] == [4, 4]
    assert tcp["AltChkSum"]["fields"][0]["widths"] == [1, 2]
    assert tcp["SAckOK"]["fields"] == [] and tcp["AltChkSumOpt"]["fields"] == []
    assert tcp["Mood"]["fields"][0]["kind"] == "hex"


def test_option_kinds_hands_out_copies() -> None:
    po.option_kinds("TCP")[0]["name"] = "changed"
    assert po.option_kinds("TCP")[0]["name"] == "EOL"


def test_tcp_kinds_encode_and_decode_every_named_option() -> None:
    cases: dict[str, tuple[dict[str, Any], str]] = {
        "MSS": ({"value": 1460}, "020405b4"), "WScale": ({"value": 7}, "030307"),
        "SAckOK": ({}, "0402"), "AltChkSumOpt": ({}, "0f02"),
        "SAck": ({"value": [1, 2]}, "050a0000000100000002"),
        "Timestamp": ({"value": [1, 2]}, "080a0000000100000002"),
        "AltChkSum": ({"value": [3, 4]}, "0e050300" "04"),
        "Mood": ({"value": "3a29"}, "19043a29"), "UTO": ({"value": 300}, "1c04012c"),
        "TFO": ({"value": [5, 6]}, "220a0000000500000006"),
    }
    for name, (fields, hexed) in cases.items():
        out = po.encode("TCP", [{"name": name, "fields": fields}], 0, 0)
        assert out.hex().startswith(hexed), name
        row = po.decode("TCP", bytes.fromhex(hexed))["rows"][0]
        assert (row["name"], row["fields"]) == (name, fields), name


def test_ip_kinds_encode_and_decode_every_named_option() -> None:
    cases: dict[str, tuple[dict[str, Any], str]] = {
        "RR": ({"pointer": 4, "routers": ["16.0.0.1", "48.0.0.1"]}, "07 0b 04 10000001 30000001"),
        "SDBM": ({"addresses": ["16.0.0.1"]}, "95 06 10000001"),
        "Security": ({"security": 1, "compartment": 2, "handling_restrictions": 3,
                      "transmission_control_code": "aabbcc"}, "82 0b 0001 0002 0003 aabbcc"),
        "Address_Extension": ({"src_ext": "16.0.0.1", "dst_ext": "48.0.0.1"}, "93 0a 10000001 30000001"),
        "MTU_Probe": ({"mtu": 1500}, "0b 04 05dc"),
    }
    for name, (fields, spaced) in cases.items():
        hexed = spaced.replace(" ", "")
        assert po.encode("IP", [{"name": name, "fields": fields}], 0, 0).hex().startswith(hexed), name
        row = po.decode("IP", bytes.fromhex(hexed))["rows"][0]
        assert (row["name"], row["fields"]) == (name, fields), name


def test_options_text_matches_the_viewer_summary() -> None:
    assert po.options_text("IP", po.decode("IP", RA)) == (
        "NOP, Router Alert (alert=router_shall_examine_packet), EOL, padding 2 bytes")
    assert po.options_text("IP", po.decode("IP", b"")) == "[]"
    assert po.options_text("IP", po.decode("IP", bytes.fromhex("070900"))) == (
        "unparsed: 070900 (option 7 at byte 0 claims 9 bytes, 3 remain)")
    assert po.options_text("IP", po.decode("IP", bytes.fromhex("19040102"))) == "option 25 (value=0102)"
    assert po.options_text("IP", po.decode("IP", bytes.fromhex("01000000"))) == "NOP, EOL, padding 2 bytes"
    assert po.options_text("IP", po.decode("IP", bytes.fromhex("010000"))) == "NOP, EOL, padding 1 byte"


def test_an_address_list_reads_as_addresses_not_a_python_list() -> None:
    rr = po.decode("IP", po.encode("IP", [ip("RR", pointer=4, routers=["16.0.0.1", "48.0.0.1"])], 0, 0))
    assert po.options_text("IP", rr) == "RR (pointer=4, routers=16.0.0.1, 48.0.0.1), EOL"


def test_tcp_options_text_and_padded() -> None:
    d = po.decode("TCP", bytes.fromhex("020405b40101"))
    assert po.options_text("TCP", d) == "MSS (value=1460), NOP, NOP"
    d["padded"] = 2
    assert po.options_text("TCP", d) == "MSS (value=1460), NOP, NOP, padded to 8 bytes"
    assert po.options_text("TCP", po.decode("TCP", bytes.fromhex("1e01"))) == "option 30 (value=)"
    ts = po.decode("TCP", bytes.fromhex("080a0000000100000002"))
    assert po.options_text("TCP", ts) == "Timestamp (value=(1, 2))"


def test_an_unparsed_text_also_says_padded() -> None:
    d = po.decode("IP", bytes.fromhex("070900"))
    d["padded"] = 1
    assert po.options_text("IP", d) == (
        "unparsed: 070900 (option 7 at byte 0 claims 9 bytes, 3 remain), padded to 4 bytes")


def test_decode_of_parsing_bytes_has_exactly_the_documented_keys() -> None:
    assert set(po.decode("IP", RA)) == {"layer", "rows", "padding", "length", "padded", "problem"}
    assert "raw" in po.decode("IP", bytes.fromhex("070900"))
