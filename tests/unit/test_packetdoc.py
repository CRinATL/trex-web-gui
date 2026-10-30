"""The packet document (P4b Task 2): parsing, id checks and the literal-only rule. Pure: no worker starts."""
import json
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.studio.packetdoc import (
    DocError,
    NotRepresentable,
    check_ids,
    doc_value,
    empty_doc,
    is_literal,
    literal_expr,
    model_of,
    parse_doc,
)

TEMPLATES = (Path(__file__).resolve().parents[2] / "backend" / "trex_web_gui" / "_vendor" / "trex"
             / "scapy_server" / "templates")

DEFS: Any = {
    "layerIds": ["Ether", "IP", "UDP", "ICMPv6EchoRequest", "Raw"],
    "feInstructions": [{"id": "STLVmFlowVar"}], "feInstructionParameters": [{"id": "name"}, {"id": "size"}],
    "feParameters": [{"id": "cache_size"}]}


def doc_with(**over: Any) -> dict[str, Any]:
    """A small valid document with the given top-level keys replaced."""
    d: dict[str, Any] = {"fileType": "trex-packet-editor", "version": "1.0.0", "metadata": {"caption": "x"},
                         "packet": [{"id": "Ether", "fields": []}], "fePrarameters": {}, "feInstructions": []}
    d.update(over)
    return d


def test_parse_both_fe_parameter_spellings() -> None:
    legacy = doc_with(fePrarameters={"cache_size": "7"})
    alias = {k: v for k, v in legacy.items() if k != "fePrarameters"} | {"feParameters": {"cache_size": "7"}}
    assert parse_doc(legacy) == parse_doc(alias)
    assert parse_doc(alias)["fePrarameters"] == {"cache_size": "7"} and "feParameters" not in parse_doc(alias)
    bare = parse_doc({"packet": [{"id": "Ether"}]})
    assert bare["fePrarameters"] == {} and bare["feInstructions"] == [] and bare["packet"][0]["fields"] == []
    with pytest.raises(DocError, match="not both"):
        parse_doc(doc_with(feParameters={}))
    with pytest.raises(DocError, match="extra: unknown key") as ei:
        parse_doc(doc_with(extra=1))
    assert ei.value.where == "extra"


def test_every_server_template_parses() -> None:
    files = sorted(TEMPLATES.rglob("*.trp"))
    assert len(files) == 14
    for f in files:
        doc = parse_doc(json.loads(f.read_text()))
        assert doc["packet"][0]["id"] == "Ether" and doc["fileType"] == "trex-packet-editor", f


def test_unknown_vtype_refused() -> None:
    def layer(value: Any) -> dict[str, Any]:
        return doc_with(packet=[{"id": "Ether", "fields": [{"id": "dst", "value": value}]}])
    with pytest.raises(DocError, match="unknown vtype 'EVAL'") as ei:
        parse_doc(layer({"vtype": "EVAL", "expr": "1"}))
    assert ei.value.where == "packet[0].fields[0].value"
    with pytest.raises(DocError, match="unknown vtype"):
        parse_doc(layer([1, {"vtype": "nope"}]))
    with pytest.raises(DocError, match="cannot be a field value"):
        parse_doc(layer(object()))
    for ok in ("a", 1, 1.5, True, None, [1, "a", [2]], {"vtype": "RANDOM"}, {"vtype": "UNDEFINED"},
               {"vtype": "EXPRESSION", "expr": "1+1"}, {"vtype": "MACHINE", "base64": "AA=="},
               {"vtype": "OBJECT", "value": {"a": 1}}, {"vtype": "BYTES", "base64": "AA=="}):
        parse_doc(layer(ok))


def test_bytes_size_capped() -> None:
    def load(**v: Any) -> dict[str, Any]:
        return doc_with(packet=[{"id": "Raw", "fields": [
            {"id": "load", "value": {"vtype": "BYTES", "generate": "random_ascii", **v}}]}])
    parse_doc(load(size=1))
    parse_doc(load(size=1048576))
    parse_doc(load(total_size=1048576, size=5))
    for bad in (0, -1, 1048577, "5", True, 1.5):
        with pytest.raises(DocError, match="must be a whole number of 1-1048576") as ei:
            parse_doc(load(size=bad))
        assert ei.value.where.endswith(".size")
    with pytest.raises(DocError, match="total_size"):
        parse_doc(load(total_size=1048577))
    with pytest.raises(DocError, match="unknown generate"):
        parse_doc(load(size=1, generate="shell"))


def test_fe_parameter_int_refused() -> None:
    with pytest.raises(DocError, match=r"fePrarameters\.cache_size: must be a string or a vtype") as ei:
        parse_doc(doc_with(fePrarameters={"cache_size": 1000}))
    assert ei.value.where == "fePrarameters.cache_size"
    ins = [{"id": "STLVmFlowVar", "parameters": {"name": "v", "size": 4}}]
    with pytest.raises(DocError, match=r"feInstructions\[0\]\.parameters\.size: must be a string") as ei:
        parse_doc(doc_with(feInstructions=ins))
    assert ei.value.where == "feInstructions[0].parameters.size"
    with pytest.raises(DocError, match="must be a string"):
        parse_doc(doc_with(feInstructions=[{"id": "STLVmFlowVar", "parameters": {"name": ["v"]}}]))
    parse_doc(doc_with(fePrarameters={"cache_size": {"vtype": "EXPRESSION", "expr": "1"}},
                       feInstructions=[{"id": "STLVmFlowVar", "parameters": {"name": "v", "size": "4"}}]))


def test_check_ids_names_the_layer() -> None:
    doc = parse_doc(doc_with(packet=[{"id": "Ether"}, {"id": "IP"}, {"id": "Nope"}]))
    with pytest.raises(DocError, match="layer 2: unknown protocol Nope"):
        check_ids(doc, DEFS)
    ins = [{"id": "STLVmFlowVar", "parameters": {"name": "v"}}]
    check_ids(parse_doc(doc_with(feInstructions=ins, fePrarameters={"cache_size": "1"})), DEFS)
    with pytest.raises(DocError, match="instruction 1: unknown instruction Bad"):
        check_ids(parse_doc(doc_with(feInstructions=[*ins, {"id": "Bad", "parameters": {}}])), DEFS)
    with pytest.raises(DocError, match="instruction 0 STLVmFlowVar: unknown parameter zap"):
        check_ids(parse_doc(doc_with(feInstructions=[{"id": "STLVmFlowVar", "parameters": {"zap": "1"}}])),
                  DEFS)
    with pytest.raises(DocError, match="unknown global parameter junk"):
        check_ids(parse_doc(doc_with(fePrarameters={"junk": "1"})), DEFS)


def test_check_ids_accepts_indirect_subclasses() -> None:
    # ICMPv6EchoRequest is a subclass of a subclass: only `layerIds` (every Packet subclass) holds it.
    defs: Any = {**DEFS, "protocols": [{"id": "Ether"}]}
    check_ids(parse_doc(doc_with(packet=[{"id": "Ether"}, {"id": "ICMPv6EchoRequest"}])), defs)


def test_model_of_without_instructions_is_none() -> None:
    packet_model, fe = model_of(parse_doc(doc_with()))
    assert packet_model == [{"id": "Ether", "fields": []}] and fe is None
    ins = [{"id": "STLVmFlowVar", "parameters": {"name": "v"}}]
    _, fe = model_of(parse_doc(doc_with(feInstructions=ins, fePrarameters={"cache_size": "9"})))
    assert fe == {"instructions": ins, "global_parameters": {"cache_size": "9"}}
    empty = empty_doc()
    assert empty["packet"] == [{"id": "Ether", "fields": []}]
    assert empty["fePrarameters"] == {"cache_size": "1000"}
    assert parse_doc(empty) == empty


def test_literal_expr() -> None:
    options = [("MSS", 1460), ("NOP", None), ("WScale", 10), ("SAckOK", b""), ("Timestamp", (1, 2))]
    assert literal_expr(options) == repr(options) and is_literal(options)
    for v in (None, 5, "a'b", b"\x00\xff", [], (), [[1], (2, [3])]):
        assert is_literal(v)

    class FakePacket:
        """Stands in for a Scapy Packet (an object whose `repr` would be code)."""
        def __repr__(self) -> str:
            return "__import__('os').system('true')"

    assert literal_expr(FakePacket()) is None
    assert literal_expr([1, FakePacket()]) is None
    assert literal_expr(("__import__('os')", FakePacket())) is None
    for v in (True, 1.5, {"a": 1}, {1}, bytearray(b"x"), [True], range(3)):
        assert literal_expr(v) is None, v

    class Flags(int):
        """An int subclass: not a literal type, so inside a list it is refused."""

    class FlagValue:
        """Stands in for Scapy's FlagValue, which is a plain object with __int__ (fields.py:1873)."""
        def __int__(self) -> int:
            return 3

    assert literal_expr(Flags(3)) is None and literal_expr([Flags(3)]) is None
    assert literal_expr(FlagValue()) is None
    deep: Any = []
    for _ in range(200):
        deep = [deep]
    assert literal_expr(deep) is None
    # A plain str value stays a str; an object that is a literal becomes an EXPRESSION; a Packet does not.
    assert doc_value("__import__('os')") == "__import__('os')"
    assert doc_value(FlagValue()) == 3 and doc_value(Flags(3)) == 3
    assert doc_value(True) == 1 and doc_value(None) is None
    assert doc_value(b"ab") == {"vtype": "BYTES", "base64": "YWI="}
    assert doc_value([("NOP", None)]) == {"vtype": "EXPRESSION", "expr": "[('NOP', None)]"}
    with pytest.raises(NotRepresentable):
        doc_value(FakePacket())
