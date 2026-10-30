"""HTTP surface of the P3c capture routes, against the fake (Task 4b). The fake runs on a manual clock here,
so a profile's packets exist only after `advance`, and a test knows how many there are. A capture needs
service mode on every port it names (the GUI's gate, an af_packet measurement), so each test turns it on
for port 1 first."""
import base64
import contextlib
import io
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES
from test_api import WS, client, recv
from test_api_control import assigned
from trex_web_gui import trexapi

SUMMARY = json.loads((FIXTURES / "p3c" / "summary.json").read_text())
BAD_BPF = "not a filter(("
UDP = SUMMARY["frames"]["udp"]


class Lab:
    """A fake on a manual clock with a profile ready on port 0 and port 1 in service mode (Rx capture
    target)."""

    def __init__(self, c: Any, f: Any, foreign: Any) -> None:
        """Connect the app client `c` to fake `f`, acquire ports 0 and 1 and assign the profile to both,
        put port 1 in service mode; `foreign(f)` makes the second client (`other`) that drives the fake's
        clock. Asserts each step."""
        self.c, self.f = c, f
        self.other = foreign(f)
        assigned(c, f, ports=(0, 1))                       # connects, acquires both and assigns the profile
        assert c.post("/api/ports/service", json={"ports": [1], "enabled": True}).status_code == 200

    def transmit(self, seconds: float = 1.0, pps: str = "1kpps") -> None:
        """Start the profile on port 0 at rate `pps`, then advance the fake's clock by `seconds`."""
        assert self.c.post("/api/ports/start", json={"ports": [0], "mult": pps}).status_code == 200
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        """Advance the fake's manual clock by `seconds` (an RPC from the second client); asserts the fake
        accepts it."""
        assert self.other._transmit("fake_advance_clock", params={"seconds": seconds})

    def rows(self) -> list[dict[str, Any]]:
        """The `captures` list of GET /api/capture (every capture row the app reports); asserts a 200."""
        r = self.c.get("/api/capture")
        assert r.status_code == 200, r.text
        return list(r.json()["captures"])


@pytest.fixture
def lab(fake_trex: Any, foreign_client: Any, tmp_path: Path) -> Iterator[Lab]:
    """Yield a Lab on a fresh manual-clock fake, inside a client context (the lifespan runs). Teardown
    leaves the context, which closes the app session; the foreign client and the fake stop in their own
    fixtures after it."""
    f = fake_trex("--manual-clock")
    with client(tmp_path) as c:
        yield Lab(c, f, foreign_client)


def wait_for_msg(ws: Any, pred: Any, limit: int = 400) -> dict[str, Any]:
    """The first websocket message `pred` accepts, read with recv() so each wait is bounded;
    AssertionError after `limit` messages with none."""
    for _ in range(limit):
        m = recv(ws)
        if pred(m):
            return m                          # type: ignore[no-any-return]
    raise AssertionError("message not seen")


def test_monitor_lifecycle_streams_rows(lab: Lab, tmp_path: Path) -> None:
    c = lab.c
    with c.websocket_connect(WS) as ws:
        r = c.post("/api/capture/monitor", json={"rx": [1], "bpf": "", "limit": 100})
        assert r.status_code == 200 and r.json()["monitor"]["state"] == "ACTIVE", r.text
        mid = r.json()["monitor"]["id"]
        lab.transmit(1.0)
        batch = wait_for_msg(ws, lambda m: m["type"] == "capture_pkts" and m["rows"])
        assert batch["monitor_id"] == mid and 1 <= len(batch["rows"]) <= 50
        row = batch["rows"][0]
        assert (row["proto"], row["origin"], row["port"]) == ("UDP", "RX", 1)
        assert isinstance(row["ts_rel_us"], int) and row["ts_rel_us"] >= 0 and base64.b64decode(row["binary"])
        again = c.post("/api/capture/monitor", json={"rx": [1]})
        assert again.status_code == 409 and again.json()["error"] == "monitor_active"
        assert c.delete("/api/capture/monitor").status_code == 200
        wait_for_msg(ws, lambda m: m["type"] == "capture" and m["monitor"] is None and m["captures"] == [])
        assert c.delete("/api/capture/monitor").json()["error"] == "no_monitor"


def pcap_frames(raw: bytes) -> int:
    """The number of frames scapy reads from pcap bytes `raw` (loads the vendored client path first)."""
    trexapi.load()
    from scapy.utils import rdpcap
    return len(rdpcap(io.BytesIO(raw)))


def test_recorder_fixed_save_download(lab: Lab) -> None:
    c = lab.c
    r = c.post("/api/capture/recorders", json={"rx": [1], "limit": 50})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    lab.transmit(1.0)
    row = next(x for x in lab.rows() if x["id"] == rid)
    assert row["mine"] is True and row["mode"] == "fixed" and row["count"] == 50
    r = c.post(f"/api/capture/recorders/{rid}/save")
    assert r.status_code == 200, r.text
    f = r.json()["file"]
    assert f["pkt_count"] == 50 and f["partial"] is False and f["capture_id"] == rid
    assert [x["id"] for x in lab.rows()] == []                       # saved, so removed from the server
    d = c.get(f"/api/capture/files/{f['name']}")
    assert d.status_code == 200 and pcap_frames(d.content) == 50
    listed = c.get("/api/capture/files").json()
    assert [x["name"] for x in listed["files"]] == [f["name"]] and listed["total"] == 1
    assert c.delete(f"/api/capture/files/{f['name']}").status_code == 200
    assert c.get(f"/api/capture/files/{f['name']}").status_code == 404


def test_a_failed_save_keeps_a_partial_and_a_retry_completes_it(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = lab.c
    real = trexapi.capture_fetch_once
    calls: list[int] = []

    def flaky(cl: Any, cid: int, n: int, snaplen: int = 0) -> Any:
        calls.append(cid)
        if len(calls) == 3:       # call 1 is the peek, 2 the first fetch
            raise trexapi.TrexApiError("the fetch failed once")
        return real(cl, cid, n, snaplen)
    rid = c.post("/api/capture/recorders", json={"rx": [1], "limit": 120}).json()["id"]
    lab.transmit(1.0)
    monkeypatch.setattr(trexapi, "capture_fetch_once", flaky)
    r = c.post(f"/api/capture/recorders/{rid}/save")
    assert r.status_code == 400 and "the fetch failed once" in r.json()["message"]
    (part,) = c.get("/api/capture/files").json()["files"]
    assert part["partial"] is True and part["pkt_count"] == 50
    r = c.post(f"/api/capture/recorders/{rid}/save")                  # the same capture: resumes by appending
    assert r.status_code == 200, r.text
    f = r.json()["file"]
    assert f["pkt_count"] == 120 and f["partial"] is False
    files = c.get("/api/capture/files").json()["files"]
    assert [x["name"] for x in files] == [f["name"]]                  # the partial became the final file
    assert pcap_frames(c.get(f"/api/capture/files/{f['name']}").content) == 120


def test_files_route_returns_dir_and_total(lab: Lab, tmp_path: Path) -> None:
    c = lab.c
    j = c.get("/api/capture/files").json()
    assert j == {"dir": str(tmp_path / "captures"), "files": [], "total": 0}
    assert c.get("/api/capture/files/not%0Aok.pcap").status_code == 400
    assert c.get("/api/capture/files/x.txt").json()["error"] == "bad_name"
    assert c.delete("/api/capture/files/x.txt").json()["error"] == "bad_name"
    assert c.get("/api/capture/files/none.pcap").status_code == 404
    assert c.delete("/api/capture/files/none.pcap").status_code == 404


def test_files_routes_without_a_spool_directory_are_409_spool_unavailable(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.app.state.session._spool_dir = None
        for r in (c.get("/api/capture/files"), c.get("/api/capture/files/a.pcap"),
                  c.delete("/api/capture/files/a.pcap")):
            assert r.status_code == 409 and r.json()["error"] == "spool_unavailable", r.text


def test_deleting_a_file_a_save_holds_is_409_capture_busy(tmp_path: Path) -> None:
    from trex_web_gui.store.spool import CaptureKey
    with client(tmp_path) as c:
        spool = c.app.state.session.spool()
        writer = spool.writer(CaptureKey("h:4501", 1.0, 1))
        (name,) = spool.held()
        r = c.delete(f"/api/capture/files/{name}")
        assert r.status_code == 409 and r.json()["error"] == "capture_busy", r.text
        writer.abort()


def test_a_packet_the_decoder_cannot_dissect_is_a_shaped_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trex_web_gui.trexapi import decode

    def boom(raw: bytes) -> Any:
        raise ValueError("odd bytes")
    monkeypatch.setattr(decode, "tree", boom)
    with client(tmp_path) as c:
        r = c.post("/api/capture/packet/tree", json={"binary": UDP})
        assert (
            r.status_code == 400 and r.json()["error"] == "bad_packet" and "odd bytes" in r.json()["message"]
        )


def test_invalid_bpf_is_400_with_server_text(lab: Lab) -> None:
    c = lab.c
    r = c.post("/api/capture/recorders", json={"rx": [1], "bpf": BAD_BPF})
    assert r.status_code == 400 and r.json()["error"] == "trex_error"
    assert r.json()["message"] == SUMMARY["errors"]["bad_bpf"]
    assert c.post("/api/capture/recorders", json={"rx": [1], "bpf": "x" * 1001}).status_code == 422


def test_ninth_server_capture_refuses_the_next_start(lab: Lab) -> None:
    """The GUI refuses at nine so that ping, ARP and scan keep the tenth slot; the server's own text at ten is
    Task 2's."""
    c = lab.c
    for _ in range(9):
        assert lab.other._transmit(
            "capture",
            params={"command": "start", "mode": "fixed", "limit": 10, "tx": [], "rx": [1], "filter": ""},
        )
    r = c.post("/api/capture/recorders", json={"rx": [1]})
    assert r.status_code == 409 and r.json()["error"] == "capture_slots" and "9 of 10" in r.json()["message"]
    r = c.post("/api/capture/monitor", json={"rx": [1]})
    assert r.status_code == 409 and r.json()["error"] == "capture_slots"
    assert all(x["mine"] is False for x in lab.rows())


def test_service_mode_off_is_409_service_off(lab: Lab) -> None:
    c = lab.c
    r = c.post("/api/capture/recorders", json={"rx": [0, 1]})
    assert r.status_code == 409 and r.json()["error"] == "service_off" and "[0]" in r.json()["message"]
    r = c.post("/api/capture/recorders", json={"tx": [0], "rx": [1]})
    # spec 6.5: Rx ports only; Tx port 0 is off
    assert r.status_code == 200, r.text
    r = c.post("/api/capture/recorders", json={"tx": [0]})             # a Tx-only start is not gated
    assert r.status_code == 200, r.text


def test_read_only_session_is_409_read_only(fake_trex: Any, tmp_path: Path) -> None:
    f = fake_trex("--manual-clock")
    with client(tmp_path) as c:
        r = c.post(
            "/api/session/connect",
            json={
                "host": f.host,
                "sync_port": f.rpc_port,
                "async_port": f.pub_port,
                "timeout_s": 2,
                "read_only": True,
            },
        )
        assert r.status_code == 200
        calls = (
            ("post", "/api/capture/monitor", {"rx": [1]}),
            ("post", "/api/capture/recorders", {"rx": [1]}),
            ("delete", "/api/capture/monitor", None),
            ("post", "/api/capture/recorders/1/save", None),
            ("post", "/api/capture/recorders/1/stop", None),
            ("delete", "/api/capture/recorders/1", None),
        )
        for method, path, body in calls:
            r = getattr(c, method)(path, **({} if body is None else {"json": body}))
            assert r.status_code == 409 and r.json()["error"] == "read_only", (path, r.text)
        assert c.get("/api/capture").status_code == 200                    # a read stays open


def test_limit_and_port_validation_is_422(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        for body in ({"rx": [1], "limit": 0}, {"rx": [1], "limit": 100001}, {}, {"rx": [1, 1]}, {"tx": [-1]},
                     {"tx": [0], "rx": [1, 1]}):
            for path in ("/api/capture/monitor", "/api/capture/recorders"):
                r = c.post(path, json=body)
                assert r.status_code == 422 and r.json()["error"] == "bad_request", (path, body, r.text)
        # valid
        assert c.post("/api/capture/monitor", json={"tx": [0], "rx": [0], "limit": 100000}).status_code == 409


def test_get_capture_while_disconnected_sends_no_rpc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[int] = []
    monkeypatch.setattr(trexapi, "capture_status", lambda c: sent.append(1) or [])
    with client(tmp_path) as c:
        r = c.get("/api/capture")
        assert r.status_code == 200 and r.json()["monitor"] is None and r.json()["captures"] == []
    assert sent == []


def test_get_capture_syncs(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    c = lab.c
    assert lab.other._transmit("capture", params={"command": "start", "mode": "fixed", "limit": 10, "tx": [],
                                                  "rx": [1], "filter": ""})
    sent: list[int] = []
    real = trexapi.capture_status
    monkeypatch.setattr(trexapi, "capture_status", lambda cl: sent.append(1) or real(cl))
    r = c.get("/api/capture")
    assert [x["id"] for x in r.json()["captures"]] == [1] and sent == [1]


def test_ws_hello_sends_capture_after_port_labels(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        kinds = [recv(ws)["type"] for _ in range(5)]      # disconnected: no ports message
        assert kinds.index("capture") > kinds.index("port_labels") and kinds[0] == "hello"
        assert kinds.index("busy") < kinds.index("port_labels")


def test_packet_tree_route_returns_data_and_binary(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.post("/api/capture/packet/tree", json={"binary": UDP})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["binary"] and [layer["id"] for layer in j["data"]][:3] == ["Ether", "IP", "UDP"]
        assert c.post("/api/capture/packet/tree", json={"binary": "%%%"}).status_code == 422
        assert c.post("/api/capture/packet/tree", json={}).status_code == 422


def test_capture_tree_refuses_an_extra_key(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.post("/api/capture/packet/tree", json={"binary": UDP, "model": []})
        assert r.status_code == 422 and r.json()["error"] == "bad_request" and "model" in r.json()["message"]
        assert c.post("/api/capture/packet/tree", json={"binary": UDP}).status_code == 200


def test_wireshark_without_monitor_is_409(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.post("/api/capture/wireshark")
        assert r.status_code == 409 and r.json()["error"] == "no_monitor", r.text
        r = c.delete("/api/capture/wireshark")                             # nothing to stop: the snapshot
        assert r.status_code == 200 and r.json()["wireshark"] == {"running": False, "error": None}


def test_wireshark_unavailable_reason_from_preflight(lab: Lab, tmp_path: Path) -> None:
    """The path is the saved preference, never the request; a missing binary is refused with the pre-flight
    reason."""
    c = lab.c
    missing = str(tmp_path / "no-such" / "wireshark")
    body = c.get("/api/settings").json()["settings"]
    body["preferences"]["wireshark_path"] = missing
    assert c.put("/api/settings", json=body).status_code == 200
    assert c.get("/api/capture/wireshark/preflight").json() == {
        "ok": False,
        "reason": f"not found at {missing}",
    }
    assert c.post("/api/capture/monitor", json={"rx": [1]}).status_code == 200
    # a body is ignored: the path is not taken from it
    r = c.post("/api/capture/wireshark", json={"path": "/bin/sh"})
    assert r.status_code == 409 and r.json() == {"error": "wireshark_unavailable",
                                                 "message": f"not found at {missing}"}
    assert c.get("/api/capture").json()["wireshark"] == {"running": False, "error": None}


def test_reset_through_api_then_capture_snapshot_is_empty(lab: Lab) -> None:
    c = lab.c
    with c.websocket_connect(WS) as ws:
        assert c.post("/api/capture/monitor", json={"rx": [1]}).status_code == 200
        # the server removes every capture
        assert c.post("/api/ports/reset", json={"ports": [0, 1]}).status_code == 200
        end = time.monotonic() + 3.0
        gone = 0
        while time.monotonic() < end:
            with contextlib.suppress(TimeoutError):      # quiet for 0.5 s: look again until the window ends
                m = recv(ws, timeout=0.5)
                if m["type"] == "log" and "removed on the server" in m["message"]:
                    gone += 1
        snap = c.get("/api/capture").json()
        assert snap["monitor"] is None and snap["captures"] == []
        assert gone == 1
