"""/api/studio/packet routes (P4b Task 4): the editor's engine, templates and the pcap picker, via the app.
Every test enters its client, so the lifespan runs, every request shares one event loop, and the engine is
closed at exit."""
import asyncio
import base64
import json
import statistics
import struct
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from support import TEST_TOKEN, anon, authed
from test_api_control import connect
from test_api_studio import RECORDED, burst
from trex_web_gui.api.app import create_app
from trex_web_gui.api.packet import scan_capture
from trex_web_gui.store.settings import SettingsStore
from trex_web_gui.trexapi import packet as packet_module
from trex_web_gui.trexapi.packet import PacketEngine

URL = "/api/studio/packet"
FRAME = bytes.fromhex("00112233445566778899aabb0800") + b"\x45" + b"\x00" * 33        # 48 bytes, junk IP


def write_pcap(path: Path, frames: list[bytes]) -> None:
    """A little-endian classic pcap of Ethernet frames."""
    with path.open("wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for i, fr in enumerate(frames):
            f.write(struct.pack("<IIII", i, 0, len(fr), len(fr)) + fr)


def gone(pid: int) -> bool:
    """True when process `pid` no longer exists or is a zombie."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return True
    return state == "Z"


@pytest.fixture
def packet_client(tmp_path: Path) -> Iterator[TestClient]:
    """An entered, logged-in client over a fresh app (the lifespan runs, and closes the engine)."""
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        yield c


def engine_of(c: TestClient) -> PacketEngine:
    """The app's packet engine (the test seam, app.state.packet_engine)."""
    engine = c.app.state.packet_engine                       # type: ignore[attr-defined]
    assert isinstance(engine, PacketEngine)
    return engine


def syn_doc() -> dict[str, Any]:
    """Ether / IP / TCP SYN, no payload."""
    return {"packet": [{"id": "Ether"}, {"id": "IP"},
                       {"id": "TCP", "fields": [{"id": "flags", "value": 2}, {"id": "dport", "value": 80}]}]}


def expr_doc(expr: str) -> dict[str, Any]:
    """Ether / IP whose id is the EXPRESSION `expr`."""
    return {"packet": [{"id": "Ether"}, {"id": "IP", "fields": [
        {"id": "id", "value": {"vtype": "EXPRESSION", "expr": expr}}]}]}


def payload_doc(size: int) -> dict[str, Any]:
    """Ether / Raw whose load is `size` bytes of the code pattern."""
    return {"packet": [{"id": "Ether"}, {"id": "Raw", "fields": [{"id": "load", "value": {
        "vtype": "BYTES", "generate": "template_code", "template_code": "32", "size": size}}]}]}


def fe_doc(**params: str) -> dict[str, Any]:
    """syn_doc with a flow var written into IP.src; `params` override the flow var's parameters."""
    flow = {"name": "ip_src", "min_value": "16.0.0.1", "max_value": "16.0.0.10", "size": "4", "op": "inc",
            **params}
    return {**syn_doc(), "feInstructions": [
        {"id": "STLVmFlowVar", "parameters": flow},
        {"id": "STLVmWrFlowVar", "parameters": {"fv_name": flow["name"], "pkt_offset": "IP.src"}}],
        "fePrarameters": {"cache_size": "10"}}


def forbid_op(engine: PacketEngine, op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any `engine.call` for `op` fail the test (the worker must not be asked)."""
    real = engine.call

    async def call(name: str, args: dict[str, Any], *a: Any, **k: Any) -> dict[str, Any]:
        """Fail for `op`, else pass through."""
        assert name != op, f"the worker was asked for {op}"
        return await real(name, args, *a, **k)
    monkeypatch.setattr(engine, "call", call)


def test_definitions_etag_304(packet_client: TestClient) -> None:
    c = packet_client
    r = c.get(f"{URL}/definitions")
    assert r.status_code == 200 and len(r.json()["layerIds"]) > 300
    tag = r.headers["etag"]
    assert tag == f'"{r.json()["etag"]}"'
    again = c.get(f"{URL}/definitions", headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
    assert c.get(f"{URL}/definitions", headers={"If-None-Match": f"W/{tag}, \"x\""}).status_code == 304
    assert c.get(f"{URL}/definitions", headers={"If-None-Match": '"other"'}).status_code == 200


def test_build_route(packet_client: TestClient) -> None:
    c = packet_client
    ok = c.post(f"{URL}/build", json={"doc": syn_doc()})
    assert ok.status_code == 200 and ok.json()["error"] is None and ok.json()["binary"]
    assert [layer["id"] for layer in ok.json()["data"]] == ["Ether", "IP", "TCP"]


def test_build_error_is_200_with_error(packet_client: TestClient) -> None:
    r = packet_client.post(f"{URL}/build", json={"doc": expr_doc("1/0")})
    assert r.status_code == 200 and "ZeroDivisionError" in r.json()["error"]


def test_unknown_protocol_refused_before_the_worker(packet_client: TestClient,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    c = packet_client
    assert c.get(f"{URL}/definitions").status_code == 200                # fetched first: the cache is warm
    forbid_op(engine_of(c), "build", monkeypatch)
    r = c.post(f"{URL}/build", json={"doc": {"packet": [{"id": "Ether"}, {"id": "__import__"}]}})
    assert r.status_code == 422 and r.json()["error"] == "invalid_doc" and r.json()["where"] == "layer 1"
    r = c.post(f"{URL}/build", json={"doc": {"packet": [{"id": "__import__"}]}})
    assert r.json()["where"] == "layer 0" and "__import__" in r.json()["message"]
    ins = {**syn_doc(), "feInstructions": [{"id": "Nope", "parameters": {}}]}
    assert c.post(f"{URL}/build", json={"doc": ins}).json()["where"] == "instruction 0"
    glob = {**syn_doc(), "fePrarameters": {"nope": "1"}}
    assert c.post(f"{URL}/build", json={"doc": glob}).json()["where"] == "fePrarameters.nope"
    shape = c.post(f"{URL}/build", json={"doc": {"packet": [{"id": "Ether", "fields": [{"id": "src"}]}]}})
    assert shape.status_code == 422 and shape.json()["where"] == "packet[0].fields[0]"
    assert c.post(f"{URL}/build", json={"doc": syn_doc(), "extra": 1}).json()["error"] == "bad_request"


def test_unauthenticated_build_is_refused_and_starts_no_worker(tmp_path: Path) -> None:
    with anon(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post(f"{URL}/build", json={"doc": expr_doc("__import__('os').getpid()")})
        assert r.status_code == 401
        assert engine_of(c).starts == 0


def test_cross_origin_build_is_refused(packet_client: TestClient) -> None:
    r = packet_client.post(f"{URL}/build", json={"doc": syn_doc()}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert engine_of(packet_client).starts == 0


def test_body_over_cap_is_413(packet_client: TestClient) -> None:
    r = packet_client.post(f"{URL}/build", content=b'{"doc": "' + b"x" * (4 * 1024 * 1024) + b'"}',
                           headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    assert engine_of(packet_client).starts == 0


def test_payload_classes_and_param_values_routes(packet_client: TestClient) -> None:
    c = packet_client
    r = c.post(f"{URL}/payload-classes", json={"doc": {"packet": [{"id": "Ether"}]}})
    assert r.status_code == 200 and "IP" in r.json()["classes"]
    r = c.post(f"{URL}/param-values", json={"doc": fe_doc(), "param_id": "name"})
    assert r.status_code == 200 and isinstance(r.json()["map"], dict)


def test_to_stream_assignable_on_the_fake(tmp_path: Path, fake_trex: Any, foreign_client: Any) -> None:
    f = fake_trex()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post(f"{URL}/to-stream", json={"doc": syn_doc()})
        assert r.status_code == 200, r.text
        parts = r.json()
        built = c.post(f"{URL}/build", json={"doc": syn_doc()}).json()
        assert parts["packet"]["binary"] == built["binary"] and parts["vm"] == {"instructions": []}
        stream = {**RECORDED, **burst("syn"), "packet": parts["packet"], "vm": parts["vm"]}
        assert c.post("/api/studio/profiles", json={"name": "Syn"}).status_code == 200
        rev = c.get("/api/studio/profiles/syn").json()["rev"]
        w = c.put("/api/studio/profiles/syn/streams", json={"rev": rev, "added": [stream]})
        assert w.status_code == 200, w.text
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        path = c.get("/api/studio/profiles").json()["profiles"][0]["path"]
        a = c.post("/api/ports/assign", json={"ports": [0], "path": path})
        assert a.status_code == 200 and a.json()["stream_count"] == 1, a.text
        other = foreign_client(f)
        data = other.ports[0].transmit("get_all_streams", {"port_id": 0, "profile_id": "*"}).data()
        on_port = list(data["profiles"].get("_", {}).values())
    assert [s["packet"]["binary"] for s in on_port] == [built["binary"]]


def test_to_stream_carries_the_field_engine(packet_client: TestClient) -> None:
    r = packet_client.post(f"{URL}/to-stream", json={"doc": fe_doc()})
    assert r.status_code == 200 and [i["type"] for i in r.json()["vm"]["instructions"]] == [
        "flow_var", "write_flow_var"]


def test_to_stream_over_9238_bytes_names_the_length(packet_client: TestClient) -> None:
    c = packet_client
    r = c.post(f"{URL}/to-stream", json={"doc": payload_doc(9239 - 14 + 1)})
    assert r.status_code == 422 and r.json()["error"] == "invalid_stream"
    assert r.json()["field"] == "packet.binary" and "9240 bytes" in r.json()["message"]
    assert c.post(f"{URL}/to-stream", json={"doc": payload_doc(9238 - 14)}).status_code == 200
    assert c.post(f"{URL}/build", json={"doc": payload_doc(9239 - 14 + 1)}).status_code == 200  # shown


def test_to_stream_unsafe_vm_name_refused(packet_client: TestClient) -> None:
    c = packet_client
    r = c.post(f"{URL}/to-stream", json={"doc": fe_doc(name='a"b')})
    assert r.status_code == 422 and r.json()["error"] == "invalid_stream", r.text
    assert r.json()["field"].startswith("vm.instructions[0]")


def test_to_stream_fe_error_is_422_packet_error(packet_client: TestClient) -> None:
    r = packet_client.post(f"{URL}/to-stream", json={"doc": fe_doc(min_value="nonsense")})
    assert r.status_code == 422 and r.json()["error"] == "packet_error" and r.json()["message"], r.text
    assert engine_of(packet_client).process is not None and engine_of(packet_client).starts == 1


def test_vm_python_route(packet_client: TestClient) -> None:
    c = packet_client
    r = c.post(f"{URL}/vm-python", json={"doc": fe_doc()})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code.startswith("vm = STLVM()") and "vm.var(name='ip_src'" in code and "STLStream(" not in code
    unsafe = c.post(f"{URL}/vm-python", json={"doc": fe_doc(name='a"b')})
    assert unsafe.status_code == 422 and unsafe.json()["error"] == "packet_error"
    assert "unsafe for code" in unsafe.json()["message"]


def test_doc_from_binary_and_decompile_routes(packet_client: TestClient) -> None:
    c = packet_client
    parts = c.post(f"{URL}/to-stream", json={"doc": fe_doc()}).json()
    r = c.post(f"{URL}/doc-from-binary", json={"binary": parts["packet"]["binary"], "vm": parts["vm"]})
    assert r.status_code == 200, r.text
    assert [layer["id"] for layer in r.json()["doc"]["packet"]] == ["Ether", "IP", "TCP"]
    assert [i["id"] for i in r.json()["doc"]["feInstructions"]] == ["STLVmFlowVar", "STLVmWrFlowVar"]
    d = c.post(f"{URL}/decompile", json={"binary": parts["packet"]["binary"], "vm": parts["vm"]})
    assert d.status_code == 200 and [i["id"] for i in d.json()["instructions"]][0] == "STLVmFlowVar"
    assert c.post(f"{URL}/doc-from-binary", json={"binary": "%%%"}).json()["error"] == "bad_request"
    assert c.post(f"{URL}/doc-from-binary", json={"binary": parts["packet"]["binary"],
                                                  "vm": {"instructions": [], "bogus": 1}}).status_code == 422


def test_reconstruct_route_keeps_a_generator(packet_client: TestClient) -> None:
    c = packet_client
    doc = {"packet": [{"id": "Ether"}, {"id": "IP", "fields": [{"id": "ttl", "value": 33}]},
                      {"id": "UDP", "fields": [{"id": "sport", "value": 1025}, {"id": "dport", "value": 12}]},
                      {"id": "Raw", "fields": [{"id": "load", "value": {
                          "vtype": "BYTES", "generate": "random_ascii", "size": 16}}]}]}
    raw = bytearray(base64.b64decode(c.post(f"{URL}/build", json={"doc": doc}).json()["binary"]))
    raw[22] = 99
    r = c.post(f"{URL}/reconstruct", json={"doc": doc, "binary": base64.b64encode(bytes(raw)).decode(),
                                           "edited": {"layer": 1, "field": "ttl"}})
    assert r.status_code == 200, r.text
    layers = r.json()["doc"]["packet"]
    assert r.json()["warnings"] == [] and layers[1]["fields"] == [{"id": "ttl", "value": 99}]
    assert layers[3]["fields"][0]["value"]["generate"] == "random_ascii"
    none = c.post(f"{URL}/reconstruct", json={"doc": doc, "binary": base64.b64encode(bytes(raw)).decode(),
                                              "edited": None})
    assert none.status_code == 200 and none.json()["warnings"][0].startswith("the byte edit re-derived")
    assert c.post(f"{URL}/reconstruct", json={"doc": doc, "binary": "AA=="}).status_code == 422


def test_server_template_id_must_be_listed(packet_client: TestClient, monkeypatch: pytest.MonkeyPatch
                                         ) -> None:
    c = packet_client
    listing = c.get(f"{URL}/templates").json()
    ids = [t["id"] for t in listing["server"]]
    ids = [i for i in ids if not i.startswith("trex-web-gui/")]
    assert "IGMP-JOIN" in ids and len(ids) == 14
    forbid_op(engine_of(c), "template", monkeypatch)
    for bad in ("../../etc/passwd", "nope", "IGMP-JOIN/../Ether", "%2e%2e/x"):
        assert c.get(f"{URL}/templates/server/{bad}").status_code == 404, bad
    monkeypatch.undo()
    first = next(i for i in ids if i != "IGMP-JOIN")
    r = c.get(f"{URL}/templates/server/{first}")
    assert r.status_code == 200 and r.json()["doc"]["packet"][0]["id"] == "Ether"


def test_igmp_join_is_listed_unbuildable_and_refused(packet_client: TestClient) -> None:
    """R4/R9: the listing marks it, and the route does not offer a template that fails."""
    c = packet_client
    rows = {t["id"]: t for t in c.get(f"{URL}/templates").json()["server"]}
    assert rows["IGMP-JOIN"]["buildable"] is False and "IGMP" in rows["IGMP-JOIN"]["reason"]
    assert all(t["buildable"] and t["reason"] is None for i, t in rows.items() if i != "IGMP-JOIN")
    r = c.get(f"{URL}/templates/server/IGMP-JOIN")
    assert r.status_code == 422 and r.json()["error"] == "packet_error"


def test_user_template_routes(packet_client: TestClient, tmp_path: Path) -> None:
    c = packet_client
    assert c.get(f"{URL}/templates").json()["user"] == []
    assert c.get(f"{URL}/templates").json()["dir"] == str(tmp_path / "templates")
    r = c.put(f"{URL}/templates/user/12/34", json={"doc": syn_doc()})
    assert r.status_code == 200 and r.json()["doc"]["packet"][2]["id"] == "TCP"
    assert (tmp_path / "templates" / "12" / "34.trp").is_file()
    again = c.put(f"{URL}/templates/user/12/34", json={"doc": syn_doc()})
    assert again.status_code == 409 and again.json()["error"] == "exists"
    assert c.put(f"{URL}/templates/user/12/34", json={"doc": syn_doc(), "overwrite": True}).status_code == 200
    assert c.get(f"{URL}/templates").json()["user"] == ["12/34"]
    assert c.get(f"{URL}/templates/user/12/34").json()["doc"] == r.json()["doc"]
    unknown = c.put(f"{URL}/templates/user/x", json={"doc": {"packet": [{"id": "Nope"}]}})
    assert unknown.json()["where"] == "layer 0"
    assert c.put(f"{URL}/templates/user/a%5Cb", json={"doc": syn_doc()}).status_code == 404
    assert c.get(f"{URL}/templates/user/a%5Cb").status_code == 404
    assert c.get(f"{URL}/templates/user/missing").status_code == 404
    assert c.delete(f"{URL}/templates/user/12/34").json() == {}
    assert c.delete(f"{URL}/templates/user/12/34").status_code == 404
    big = {"packet": [{"id": "Ether"}], "metadata": {"caption": "x" * (3 * 1024 * 1024)}}
    assert c.put(f"{URL}/templates/user/big", json={"doc": big}).status_code == 200
    # a body under the cap whose stored (indented) form is over it: the store's own limit, 413
    many = {"packet": [{"id": "Ether"}] * 150_000}
    assert len(json.dumps(many)) < 4 * 1024 * 1024
    r = c.put(f"{URL}/templates/user/many", json={"doc": many})
    assert r.status_code == 413 and r.json()["error"] == "too_large", r.text[:200]
    assert not (tmp_path / "templates" / "many.trp").exists()


def test_a_value_with_a_colon_does_not_move_where(packet_client: TestClient) -> None:
    value = {"vtype": "BYTES", "generate": "a: b"}
    doc = {"packet": [{"id": "Ether", "fields": [{"id": "src", "value": value}]}]}
    r = packet_client.post(f"{URL}/build", json={"doc": doc})
    assert r.status_code == 422 and r.json()["where"] == "packet[0].fields[0].value"
    assert "a: b" in r.json()["message"]
    bad = {"packet": [{"id": "Ether"}], "feInstructions": [{"id": "Nope", "parameters": {}}]}
    assert packet_client.post(f"{URL}/build", json={"doc": bad}).json()["where"] == "instruction 0"


def test_put_with_a_bad_name_is_404_and_starts_no_worker(packet_client: TestClient) -> None:
    for name in ("a%5Cb", "a%00b", "a//b", "x" * 201, "%2e%2e"):
        r = packet_client.put(f"{URL}/templates/user/{name}", json={"doc": {"packet": [{"id": "Nope"}]}})
        assert r.status_code == 404 and r.json()["error"] == "not_found", (name, r.text)
    assert engine_of(packet_client).starts == 0


def test_user_template_dir_follows_the_preference(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    with authed(create_app(tmp_path / "data", token=TEST_TOKEN)) as c:
        s = SettingsStore(tmp_path / "data")
        loaded = s.load()
        loaded.preferences.templates_dir = str(other)
        s.save(loaded)
        assert c.put(f"{URL}/templates/user/p", json={"doc": syn_doc()}).status_code == 200
        assert (other / "p.trp").is_file() and c.get(f"{URL}/templates").json()["dir"] == str(other)


def write_capture(path: Path, frames: list[bytes] | None = None) -> Path:
    """A classic pcap at `path` holding `frames` (three junk-IP frames by default)."""
    write_pcap(path, frames if frames is not None else [FRAME] * 3)
    return path


def test_pcap_list_packet_and_clamps(packet_client: TestClient, tmp_path: Path) -> None:
    c = packet_client
    path = write_capture(tmp_path / "many.pcap", [FRAME[:14] + bytes([i]) * 20 for i in range(150)])
    r = c.post(f"{URL}/pcap/list", json={"path": str(path), "offset": -5, "limit": 1000})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 150 and len(page["rows"]) == 100 and page["warnings"] == []
    assert [x["index"] for x in page["rows"]] == list(range(100)) and page["rows"][0]["length"] == 34
    assert set(page["rows"][0]) == {"index", "length", "summary"}
    tail = c.post(f"{URL}/pcap/list", json={"path": str(path), "offset": 140, "limit": 0}).json()
    assert [x["index"] for x in tail["rows"]] == [140]                       # limit 0 clamps to 1
    p = c.post(f"{URL}/pcap/packet", json={"path": str(path), "index": 7})
    assert p.status_code == 200, p.text
    assert base64.b64decode(p.json()["binary"]) == FRAME[:14] + bytes([7]) * 20
    assert p.json()["doc"]["packet"][0]["id"] == "Ether" and isinstance(p.json()["warnings"], list)
    assert c.post(f"{URL}/pcap/packet", json={"path": str(path), "index": 150}).status_code == 422
    assert c.post(f"{URL}/pcap/packet", json={"path": str(path), "index": -1}).status_code == 422
    rel = c.post(f"{URL}/pcap/list", json={"path": "many.pcap"})
    assert rel.status_code == 422 and rel.json()["error"] == "bad_request"
    assert c.post(f"{URL}/pcap/list", json={"path": str(tmp_path / "gone.pcap")}).status_code == 404
    assert c.post(f"{URL}/pcap/list", json={"path": str(tmp_path)}).status_code == 422       # not a file
    both = c.post(f"{URL}/pcap/list", json={"path": str(path), "upload_id": "0" * 32})
    assert both.status_code == 422 and c.post(f"{URL}/pcap/list", json={}).status_code == 422


def test_pcap_by_upload_id(packet_client: TestClient, tmp_path: Path) -> None:
    c = packet_client
    data = write_capture(tmp_path / "up.pcap").read_bytes()
    up = c.put("/api/studio/uploads", params={"name": "up.pcap"}, content=data)
    assert up.status_code == 200, up.text
    r = c.post(f"{URL}/pcap/list", json={"upload_id": up.json()["upload_id"]})
    assert r.status_code == 200 and r.json()["total"] == 3
    one = c.post(f"{URL}/pcap/packet", json={"upload_id": up.json()["upload_id"], "index": 2})
    assert one.status_code == 200
    gone_id = c.post(f"{URL}/pcap/list", json={"upload_id": "f" * 32})
    assert gone_id.status_code == 404 and gone_id.json()["error"] == "not_found"


def test_pcap_over_the_cap_is_413(tmp_path: Path) -> None:
    with authed(create_app(tmp_path / "data", token=TEST_TOKEN)) as c:
        s = SettingsStore(tmp_path / "data")
        loaded = s.load()
        loaded.preferences.pcap_size_cap_mib = 1
        s.save(loaded)
        big = write_capture(tmp_path / "big.pcap", [FRAME + b"\x00" * 60_000] * 20)           # about 1.2 MiB
        assert big.stat().st_size > 1024 * 1024
        for route, extra in (("list", {}), ("packet", {"index": 0})):
            r = c.post(f"{URL}/pcap/{route}", json={"path": str(big), **extra})
            assert r.status_code == 413 and r.json()["error"] == "too_large", r.text
        assert engine_of(c).starts == 0                                         # the worker never opened it
        small = write_capture(tmp_path / "small.pcap")
        assert c.post(f"{URL}/pcap/list", json={"path": str(small)}).status_code == 200


def test_pcap_not_a_pcap_is_422(packet_client: TestClient, tmp_path: Path) -> None:
    junk = tmp_path / "junk.pcap"
    junk.write_bytes(b"this is not a capture file at all" * 4)
    r = packet_client.post(f"{URL}/pcap/list", json={"path": str(junk)})
    assert r.status_code == 422 and r.json()["error"] == "packet_error"
    assert "not a pcap" in r.json()["message"]
    assert engine_of(packet_client).starts == 1                              # refused, not restarted


def test_pcap_warns_about_a_capture_that_ends_mid_record(packet_client: TestClient, tmp_path: Path) -> None:
    path = write_capture(tmp_path / "cut.pcap", [FRAME] * 3)
    path.write_bytes(path.read_bytes()[:-10])                                # the last record loses 10 bytes
    r = packet_client.post(f"{URL}/pcap/list", json={"path": str(path)})
    assert r.status_code == 200      # the reader shows the cut record as a short packet, without a word
    assert len(r.json()["warnings"]) == 1
    assert "ends inside record 2 (10 bytes missing)" in r.json()["warnings"][0]
    p = packet_client.post(f"{URL}/pcap/packet", json={"path": str(path), "index": 0})
    assert any("ends inside record 2" in w for w in p.json()["warnings"])
    path.write_bytes(path.read_bytes()[:24 + 16 + len(FRAME) + 5])   # inside record 1's header
    r = packet_client.post(f"{URL}/pcap/list", json={"path": str(path)})
    assert "ends inside the header of record 1" in r.json()["warnings"][0]


def capture_with(path: Path, records: list[tuple[int, int]]) -> Path:
    """A classic pcap whose records are `(incl_len, orig_len)`, each holding incl_len zero bytes after a short
    Ethernet header."""
    with path.open("wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 1 << 20, 1))
        for i, (incl, orig) in enumerate(records):
            f.write(struct.pack("<IIII", i, 0, incl, orig) + FRAME[:14].ljust(incl, b"\x00"))
    return path


def test_pcap_warns_about_truncated_and_oversize_records(packet_client: TestClient, tmp_path: Path) -> None:
    path = capture_with(tmp_path / "trunc.pcap", [(60, 60), (60, 1514), (70_000, 70_000), (80_000, 90_000)])
    r = packet_client.post(f"{URL}/pcap/list", json={"path": str(path)})
    assert r.status_code == 200 and r.json()["total"] == 4, r.text
    w = r.json()["warnings"]
    assert w == ["record 1 holds 60 of its 1514 bytes (the capture was cut short)",
                 "record 2 is 70000 bytes; the packet is cut at 65535 when loaded",
                 "record 3 holds 80000 of its 90000 bytes (the capture was cut short)",
                 "record 3 is 80000 bytes; the packet is cut at 65535 when loaded"]
    only = packet_client.post(f"{URL}/pcap/packet", json={"path": str(path), "index": 1}).json()["warnings"]
    assert [x for x in only if x.startswith("record")] == [
        "record 1 holds 60 of its 1514 bytes (the capture was cut short)"]
    clean = packet_client.post(f"{URL}/pcap/packet", json={"path": str(path), "index": 0}).json()["warnings"]
    assert not any(x.startswith("record") for x in clean)


def pcapng(blocks: list[tuple[int, bytes]], end: str = "<") -> bytes:
    """A pcapng file: a section header, an interface block, then `blocks` as (type, body)."""
    def block(kind: int, body: bytes) -> bytes:
        """One block: type, total length, body padded to 4, total length."""
        body += b"\x00" * (-len(body) % 4)
        return struct.pack(f"{end}II", kind, len(body) + 12) + body + struct.pack(f"{end}I", len(body) + 12)
    shb = struct.pack(f"{end}IHHq", 0x1A2B3C4D, 1, 0, -1)
    idb = struct.pack(f"{end}HHI", 1, 0, 0)
    return b"".join([block(0x0A0D0D0A, shb), block(1, idb), *(block(k, b) for k, b in blocks)])


def epb(captured: int, original: int, end: str = "<") -> tuple[int, bytes]:
    """An Enhanced Packet Block of `captured` data bytes that were `original` on the wire."""
    data = FRAME[:14].ljust(captured, b"\x00")
    return 6, struct.pack(f"{end}IIIII", 0, 0, 0, captured, original) + data


def test_pcapng_is_scanned_like_a_classic_pcap(packet_client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "a.pcapng"
    path.write_bytes(pcapng([epb(60, 60), epb(60, 1514), epb(70_000, 70_000)]))
    assert scan_capture(str(path), None) == [
        "record 1 holds 60 of its 1514 bytes (the capture was cut short)",
        "record 2 is 70000 bytes; the packet is cut at 65535 when loaded"]
    assert scan_capture(str(path), 0) == []
    be = tmp_path / "be.pcapng"
    be.write_bytes(pcapng([epb(60, 60, ">"), epb(20, 99, ">")], ">"))
    assert scan_capture(str(be), None) == ["record 1 holds 20 of its 99 bytes (the capture was cut short)"]
    spb = tmp_path / "spb.pcapng"
    spb.write_bytes(pcapng([(3, struct.pack("<I", 100) + b"\x00" * 40)]))
    assert scan_capture(str(spb), None) == ["record 0 holds 40 of its 100 bytes (the capture was cut short)"]
    r = packet_client.post(f"{URL}/pcap/list", json={"path": str(path)})        # the same texts on the route
    assert r.status_code == 200 and r.json()["total"] == 3, r.text
    assert r.json()["warnings"][0] == "record 1 holds 60 of its 1514 bytes (the capture was cut short)"


def test_pcapng_cut_mid_block_warns(packet_client: TestClient, tmp_path: Path) -> None:
    whole = pcapng([epb(60, 60), epb(60, 60)])
    path = tmp_path / "cut.pcapng"
    path.write_bytes(whole[:-10])
    assert scan_capture(str(path), None) == [
        "the capture ends inside record 1 (10 bytes missing); it and any later records are not shown"]
    path.write_bytes(whole[:-(len(whole) - len(pcapng([epb(60, 60)]))) + 4])
    assert scan_capture(str(path), None) == [
        "the capture ends inside the header of record 1; it and any later records are not shown"]
    r = packet_client.post(f"{URL}/pcap/packet", json={"path": str(path), "index": 0})
    assert r.status_code == 200
    assert any("ends inside the header of record 1" in w for w in r.json()["warnings"])


def test_scan_capture_lists_ten_and_counts_the_rest(tmp_path: Path) -> None:
    path = capture_with(tmp_path / "snap.pcap", [(20, 40)] * 14)
    notes = scan_capture(str(path), None)
    assert len(notes) == 11 and notes[-1] == "and 4 more records are cut"
    assert scan_capture(str(path), 12) == ["record 12 holds 20 of its 40 bytes (the capture was cut short)"]
    big = tmp_path / "be.pcap"                                                # big-endian magic
    with big.open("wb") as f:
        f.write(struct.pack(">IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        f.write(struct.pack(">IIII", 0, 0, 20, 40) + b"\x00" * 20)
    assert scan_capture(str(big), None) == ["record 0 holds 20 of its 40 bytes (the capture was cut short)"]
    other = tmp_path / "x.bin"
    other.write_bytes(b"\x0a\x0d\x0d\x0a" + b"\x00" * 40)                      # pcapng: not scanned
    assert scan_capture(str(other), None) == []
    short = tmp_path / "short"
    short.write_bytes(b"abc")
    assert scan_capture(str(short), None) == []


def test_engine_restart_is_503_then_recovers(packet_client: TestClient) -> None:
    c = packet_client
    engine = engine_of(c)
    assert c.post(f"{URL}/build", json={"doc": syn_doc()}).status_code == 200
    first = engine.pid
    r = c.post(f"{URL}/build", json={"doc": expr_doc("__import__('os')._exit(3)")})       # the worker dies
    assert r.status_code == 503 and r.json()["error"] == "engine_restarted", r.text
    assert "restarted" in r.json()["message"]
    again = c.post(f"{URL}/build", json={"doc": syn_doc()})
    assert again.status_code == 200 and again.json()["binary"]
    assert engine.starts == 2 and engine.pid != first


def test_engine_busy_is_503_and_kills_nothing(packet_client: TestClient,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    c = packet_client
    engine = engine_of(c)
    assert c.post(f"{URL}/build", json={"doc": syn_doc()}).status_code == 200
    pid = engine.pid
    monkeypatch.setattr(packet_module, "PACKET_QUEUE_S", 0.3)
    slow = expr_doc("__import__('time').sleep(1.5) or 7")

    def post(doc: dict[str, Any], delay: float) -> Any:
        """POST a build after `delay` seconds."""
        time.sleep(delay)
        return c.post(f"{URL}/build", json={"doc": doc})
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(post, slow, 0)
        second = pool.submit(post, syn_doc(), 0.5)
        busy, done = second.result(), first.result()
    assert busy.status_code == 503 and busy.json()["error"] == "engine_busy"
    assert done.status_code == 200 and done.json()["error"] is None
    assert engine.pid == pid and engine.starts == 1


def test_engine_is_first_called_on_the_event_loop_thread(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """R1: the worker is spawned from the loop's thread (it dies with the thread that forked it), also for
    the pcap and template routes that use threads for their files."""
    seen: list[tuple[int, int | None]] = []
    real = PacketEngine._start

    async def start(self: PacketEngine) -> None:
        """Record the spawning thread and the running loop's thread."""
        seen.append((threading.get_ident(), getattr(asyncio.get_running_loop(), "_thread_id", None)))
        await real(self)
    monkeypatch.setattr(PacketEngine, "_start", start)
    path = write_capture(tmp_path / "a.pcap")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        assert c.post(f"{URL}/pcap/list", json={"path": str(path)}).status_code == 200
        pid = engine_of(c).pid
        assert pid is not None
        assert c.put(f"{URL}/templates/user/t", json={"doc": syn_doc()}).status_code == 200
        assert c.post(f"{URL}/build", json={"doc": syn_doc()}).status_code == 200
        assert engine_of(c).pid == pid
    assert len(seen) == 1 and seen[0][0] == seen[0][1] and seen[0][0] != threading.get_ident()


def test_lifespan_closes_the_engine(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        engine = engine_of(c)
        assert c.post(f"{URL}/build", json={"doc": syn_doc()}).status_code == 200
        pid = engine.pid
        assert pid is not None and not gone(pid)
    assert engine.closed and engine.pid is None
    end = time.monotonic() + 5
    while not gone(pid) and time.monotonic() < end:
        time.sleep(0.05)
    assert gone(pid)


def test_build_route_latency(packet_client: TestClient) -> None:
    c = packet_client
    body = {"doc": syn_doc()}
    assert c.post(f"{URL}/build", json=body).status_code == 200              # warm: the worker is up
    times = []
    for _ in range(200):
        t = time.perf_counter()
        r = c.post(f"{URL}/build", json=body)
        times.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200
    times.sort()
    p50, p95 = statistics.median(times), times[int(len(times) * 0.95) - 1]
    print(f"POST /api/studio/packet/build, 200 calls: p50 {p50:.1f} ms, p95 {p95:.1f} ms (target 50 ms)")
    assert p95 < 250


def test_preset_route_returns_doc_and_flags(packet_client: TestClient) -> None:
    c = packet_client
    listing = c.get(f"{URL}/presets")
    assert listing.status_code == 200
    assert [p["id"] for p in listing.json()["presets"]] == ["flood", "ip_sweep", "frame_size_sweep", "vlan"]
    flood = listing.json()["presets"][0]
    assert flood["name"] == "Flood" and flood["defaults"]["l4"] == "udp"
    params = {**flood["defaults"], "src_mac": {"mode": "packet", "value": "02:00:00:00:00:07"},
              "frame": {"mode": "inc", "min": 64, "max": 128}}
    r = c.post(f"{URL}/presets/flood", json=params)
    assert r.status_code == 200 and r.json()["flags"] == 1, r.text
    built = c.post(f"{URL}/build", json={"doc": r.json()["doc"]})
    assert built.status_code == 200 and built.json()["error"] is None and built.json()["binary"]
    assert c.post(f"{URL}/to-stream", json={"doc": r.json()["doc"]}).status_code == 200
    assert c.post(f"{URL}/presets/vlan", json={}).json()["flags"] is None
    both = {"start": "1.1.1.1", "count": 2, "end": "1.1.1.2"}
    refused = c.post(f"{URL}/presets/ip_sweep", json={"src": both})
    assert refused.status_code == 422 and refused.json()["error"] == "invalid_params"
    assert refused.json()["where"] == "src" and "exactly one of count and end" in refused.json()["message"]
    assert c.post(f"{URL}/presets/flood", json={"sport": "80"}).json()["where"] == "sport"
    assert c.post(f"{URL}/presets/flood", json=[1]).status_code == 422


def test_unknown_preset_404(packet_client: TestClient) -> None:
    r = packet_client.post(f"{URL}/presets/simple", json={})
    assert r.status_code == 404 and r.json() == {"error": "not_found", "message": "No preset simple"}
    assert packet_client.post(f"{URL}/presets/flood", json={}).status_code == 200      # the route exists
    assert engine_of(packet_client).process is None                      # presets never start the worker


CAVEAT = "LLDP (Some NICs consume LLDP in firmware: Intel X710/XL710 under i40e is the known case)"


def lldp_doc(c: TestClient) -> dict[str, Any]:
    """The `trex-web-gui/LLDP` template as the route serves it."""
    r = c.get(f"{URL}/templates/server/trex-web-gui/LLDP")
    assert r.status_code == 200, r.text
    doc: dict[str, Any] = r.json()["doc"]
    return doc


def test_trex_web_gui_template_listed_and_served_without_the_worker(packet_client: TestClient,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The listing carries the `trex-web-gui/*` templates (read from disk by the router); serving one never
    asks the worker for op `template`."""
    c = packet_client
    rows = {t["id"]: t for t in c.get(f"{URL}/templates").json()["server"]}
    assert rows["trex-web-gui/LLDP"] == {"id": "trex-web-gui/LLDP", "name": "LLDP", "buildable": True,
                                         "reason": None}
    forbid_op(engine_of(c), "template", monkeypatch)
    assert lldp_doc(c)["packet"][0]["id"] == "Ether"
    assert c.get(f"{URL}/templates/server/trex-web-gui/Nope").status_code == 404
    # httpx folds a literal .. away, so the dots are percent-encoded
    assert c.get(f"{URL}/templates/server/trex-web-gui/%2e%2e/LLDP").status_code == 404


def test_lldp_template_builds_and_parses(packet_client: TestClient) -> None:
    """The template builds, and the project's own LLDP parser reads its TLVs back."""
    from trex_web_gui.trexapi.lldp import parse
    c = packet_client
    built = c.post(f"{URL}/build", json={"doc": lldp_doc(c)}).json()
    assert built["error"] is None
    raw = base64.b64decode(built["binary"])
    n = parse(raw, 0, 1000.0)
    assert n is not None
    assert (n["chassis_id"], n["chassis_id_subtype"]) == ("00:00:00:00:00:01", "MAC address")
    assert (n["port_id"], n["port_id_subtype"]) == ("trex-0", "locally assigned")
    assert n["ttl"] == 120 and n["system_name"] == "trex-web-gui"
    assert raw[:6] == bytes.fromhex("0180c200000e") and raw[12:14] == b"\x88\xcc"
    path = Path(__file__).resolve().parents[2] / "docs" / "profiles" / "lldp_30s.json"
    profile = json.loads(path.read_text())
    assert base64.b64decode(profile[0]["packet"]["binary"]) == raw          # the profile sends this frame


def test_lldp_caption_carries_the_caveat(packet_client: TestClient) -> None:
    """The caption is the exact wording, and names what `trexapi.lldp.NOTE` names."""
    from trex_web_gui.trexapi.lldp import NOTE
    caption = lldp_doc(packet_client)["metadata"]["caption"]
    assert caption == CAVEAT
    assert all(word in NOTE and word in caption for word in ("firmware", "Intel X710/XL710", "i40e"))


def test_dhcp_discover_template_builds_a_decodable_discover(packet_client: TestClient) -> None:
    """`trex-web-gui/DHCP-DISCOVER` builds with every layer decoded, DHCP included: TRex's own
    DHCP/DHCP-DISCOVER has a DHCP layer with no options, so its bytes decode without one."""
    c = packet_client
    rows = {t["id"]: t for t in c.get(f"{URL}/templates").json()["server"]}
    assert rows["trex-web-gui/DHCP-DISCOVER"]["buildable"] is True
    r = c.get(f"{URL}/templates/server/trex-web-gui/DHCP-DISCOVER")
    assert r.status_code == 200, r.text
    built = c.post(f"{URL}/build", json={"doc": r.json()["doc"]}).json()
    assert built["error"] is None
    assert [(layer["id"], layer["real_id"], layer["valid_structure"]) for layer in built["data"]] == [
        (name, name, True) for name in ("Ether", "IP", "UDP", "BOOTP", "DHCP")]
    raw = base64.b64decode(built["binary"])
    assert raw[:6] == b"\xff" * 6 and raw[30:34] == b"\xff" * 4                     # broadcast MAC and IP
    assert struct.unpack("!HH", raw[34:38]) == (68, 67)                               # client to server port
    bootp = raw[42:]
    assert bootp[0] == 1                                       # BOOTREQUEST
    assert bootp[28:34] == raw[6:12]                           # chaddr is the source MAC
    assert bootp[236:240] == bytes.fromhex("63825363")         # the DHCP magic cookie
    assert bootp[240:243] == bytes([53, 1, 1])                 # option 53, message type 1: DHCPDISCOVER
