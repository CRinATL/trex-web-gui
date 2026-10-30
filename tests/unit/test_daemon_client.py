"""The bounded daemon client (backend/trex_web_gui/daemon/client.py) against an in-test HTTP server that
replays tests/fixtures/p5 and p5-stf daemon.jsonl bodies."""
import base64
import http.server
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.daemon.client import (
    DAEMON_MAX_BYTES,
    LOG_TAIL_BYTES,
    DaemonClient,
    DaemonFault,
    DaemonUnreachable,
    StfOptions,
)

FIX = Path(__file__).resolve().parent.parent / "fixtures"
Reply = tuple[int, dict[str, str], bytes]


def recorded(fixture: str) -> list[dict[str, Any]]:
    """The rows of one daemon.jsonl."""
    return [json.loads(ln) for ln in (FIX / fixture / "daemon.jsonl").read_text().splitlines() if ln.strip()]


def replay(rows: list[dict[str, Any]]) -> Callable[[dict[str, Any]], Reply]:
    """A responder that answers with the recorded body of the first row matching the method (and params)."""
    def respond(req: dict[str, Any]) -> Reply:
        """Find the recorded row for req."""
        for r in rows:
            if r["method"] == req["method"] and (not req["params"] or r["params"] == req["params"]):
                return 200, {}, json.dumps({**r["body"], "id": req["id"]}).encode()
        return 404, {}, b""
    return respond


class Server:
    """An HTTP server on a free loopback port that records every request body and answers via `responder`."""

    def __init__(self, responder: Callable[[dict[str, Any]], Reply]) -> None:
        """Start serving."""
        self.requests: list[dict[str, Any]] = []
        self.responder = responder
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            """One POST per connection."""
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:  # noqa: N802
                """Record the request and write the responder's reply."""
                body = self.rfile.read(int(self.headers["Content-Length"]))
                req = json.loads(body)
                outer.requests.append(req)
                status, headers, data = outer.responder(req)
                try:
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def log_message(self, *a: Any) -> None:
                """Silence the access log."""

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def client(self, **kw: Any) -> DaemonClient:
        """A client aimed at this server."""
        return DaemonClient("127.0.0.1", self.port, **kw)

    def close(self) -> None:
        """Stop serving."""
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def make_server() -> Iterator[Callable[[Callable[[dict[str, Any]], Reply]], Server]]:
    """A factory for servers, all closed at teardown."""
    made: list[Server] = []

    def make(responder: Callable[[dict[str, Any]], Reply]) -> Server:
        """Start one."""
        s = Server(responder)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def ok(result: Any) -> Callable[[dict[str, Any]], Reply]:
    """A responder that always answers `result`."""
    return lambda req: (200, {}, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode())


def test_named_params_on_the_wire(make_server: Any) -> None:
    """start_trex sends named params, and trex_cmd_options as cfg first, then the options, in that order."""
    srv = make_server(ok(68))
    seq = srv.client().start_trex("/etc/trex_cfg.yaml", {"c": 2}, "me", 40, "--no-scapy-server")
    assert seq == 68
    req = srv.requests[0]
    assert req["method"] == "start_trex" and req["jsonrpc"] == "2.0"
    assert list(req["params"]["trex_cmd_options"]) == ["cfg", "c"]
    assert req["params"]["stateless"] is True and req["params"]["block_to_success"] is True
    assert req["params"]["timeout"] == 40 and req["params"]["trex_args"] == "--no-scapy-server"
    assert req["params"]["user"] == "me"
    srv.client().start_trex(None, {"c": 2}, "me", 40, "")
    assert list(srv.requests[1]["params"]["trex_cmd_options"]) == ["c"]


def test_fault_becomes_daemonfault(make_server: Any) -> None:
    """A recorded error body is a DaemonFault with its code and message."""
    srv = make_server(replay(recorded("p5")))
    with pytest.raises(DaemonFault) as ei:
        srv.client().call("no_such_method")
    assert ei.value.code == -32601 and "not supported" in ei.value.message


def test_stop_wrong_seq_idle_is_false_not_a_fault(make_server: Any) -> None:
    """The recorded stop_trex(999) answer is the value False."""
    srv = make_server(replay(recorded("p5")))
    assert srv.client().stop_trex(999) is False


def test_unreachable_on_refused_port() -> None:
    """Nothing listens: DaemonUnreachable."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(DaemonUnreachable):
        DaemonClient("127.0.0.1", port, timeout_s=2).connectivity_check()


def test_client_bounds_size(make_server: Any) -> None:
    """A 9 MiB body is refused as too large."""
    big = b'{"result": "' + b"a" * (9 * 1024 * 1024) + b'"}'
    assert len(big) > DAEMON_MAX_BYTES
    srv = make_server(lambda req: (200, {}, big))
    with pytest.raises(DaemonUnreachable, match="too large"):
        srv.client(timeout_s=20).call("get_trex_log")


def test_client_bounds_drip(make_server: Any) -> None:
    """One byte every 0.5 s against a 2 s total deadline raises within 2.5 s."""
    class Drip(http.server.BaseHTTPRequestHandler):
        """Sends a valid header then drips the body."""
        def do_POST(self) -> None:  # noqa: N802
            """Drip forever."""
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                self.send_response(200)
                self.send_header("Content-Length", "100000")
                self.end_headers()
                for _ in range(40):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass

        def log_message(self, *a: Any) -> None:
            """Silence the access log."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        t0 = time.monotonic()
        with pytest.raises(DaemonUnreachable):
            DaemonClient("127.0.0.1", httpd.server_address[1]).call("is_running", timeout_s=2)
        assert time.monotonic() - t0 < 2.5
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_client_bounds_redirect(make_server: Any) -> None:
    """A 302 raises, and its target receives no request."""
    target = make_server(ok(True))
    srv = make_server(lambda req: (302, {"Location": f"http://127.0.0.1:{target.port}/"}, b""))
    with pytest.raises(DaemonUnreachable, match="302"):
        srv.client().connectivity_check()
    assert target.requests == []


def test_client_ignores_http_proxy(make_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """With http_proxy pointing at a dead port, the call still reaches the server."""
    for k in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(k, "http://127.0.0.1:1")
    srv = make_server(ok(True))
    assert srv.client().connectivity_check() is True
    assert len(srv.requests) == 1


@pytest.mark.parametrize("method", ["kill_all_trexes", "reserve_trex", "cancel_reservation"])
def test_forbidden_methods_refused_before_io(make_server: Any, method: str) -> None:
    """A forbidden method is a ValueError and the server sees nothing."""
    srv = make_server(ok(True))
    with pytest.raises(ValueError):
        srv.client().call(method)
    assert srv.requests == []


def test_prepare_astf_profile_is_forbidden(make_server: Any) -> None:
    """prepare_astf_profile imports a Python file as root: ValueError, no request."""
    srv = make_server(ok(True))
    with pytest.raises(ValueError):
        srv.client().call("prepare_astf_profile", profile_name="x")
    assert srv.requests == []


def test_base64_wrappers_decode_and_tail(make_server: Any) -> None:
    """version, config and the logs are base64-decoded; a log over LOG_TAIL_BYTES keeps its end."""
    def enc(t: bytes) -> str:
        """Base64 of t."""
        return base64.b64encode(t).decode()
    text = b"x" * (LOG_TAIL_BYTES + 100) + b"END"
    srv = make_server(lambda req: (200, {}, json.dumps({"id": 1, "result": {
        "get_trex_version": enc(b"Version : v3.08\n"), "get_trex_config": enc(b"- port_limit: 2\n"),
        "get_trex_log": enc(text), "get_trex_daemon_log": enc(b"short")}[req["method"]]}).encode()))
    c = srv.client()
    assert c.version() == "Version : v3.08\n"
    assert c.config_text() == "- port_limit: 2\n"
    assert c.daemon_log() == "short"
    tail = c.trex_log()
    assert len(tail) == LOG_TAIL_BYTES and tail.endswith("END")
    rec = make_server(replay(recorded("p5")))
    assert "v3.08" in rec.client().version()


def test_trex_cmds_pids_are_ints(make_server: Any) -> None:
    """The daemon's string pids come back as ints, and the other wrappers read the recording."""
    srv = make_server(replay(recorded("p5")))
    c = srv.client()
    cmds = c.trex_cmds()
    assert cmds[0][0] == 2662 and isinstance(cmds[0][0], int) and "_t-rex-64" in cmds[0][1]
    assert c.files_path() == "/tmp/trex_files"
    assert c.is_running() is False and c.is_reserved() is False
    assert c.running_status()["state"] == 1
    assert isinstance(c.devices(), list) and c.devices()
    assert c.metadata()[0]["id"] == "port_limit"


def test_stf_start_wire_form(make_server: Any) -> None:
    """STF sends cfg, f, d, m, l, rx_check, c in order, m a float, stateless false; zero l, rx omitted."""
    srv = make_server(ok(7))
    c = srv.client()
    assert c.start_trex("/etc/trex_cfg.yaml", {"c": 1}, "me", 40, "",
                        stf=StfOptions("cap2/sfr2.yaml", 20, 1, 1000, 100)) == 7
    raw_params = srv.requests[0]["params"]
    assert list(raw_params["trex_cmd_options"]) == ["cfg", "f", "d", "m", "l", "rx_check", "c"]
    m = raw_params["trex_cmd_options"]["m"]
    assert m == 1.0 and isinstance(m, float)
    assert raw_params["stateless"] is False
    c.start_trex("/etc/trex_cfg.yaml", {"c": 1}, "me", 40, "", stf=StfOptions("cap2/sfr2.yaml", 20))
    assert list(srv.requests[1]["params"]["trex_cmd_options"]) == ["cfg", "f", "d", "m", "c"]


def test_stf_wire_text_has_a_json_float(make_server: Any) -> None:
    """The bytes on the wire read `"m": 1.0`, not `"m": 1`."""
    seen: list[bytes] = []

    class Raw(http.server.BaseHTTPRequestHandler):
        """Keeps the raw request body."""
        def do_POST(self) -> None:  # noqa: N802
            """Record and answer."""
            seen.append(self.rfile.read(int(self.headers["Content-Length"])))
            body = b'{"id":1,"result":1}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            """Silence the access log."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Raw)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        DaemonClient("127.0.0.1", httpd.server_address[1]).start_trex(
            None, {}, "me", 40, "", stf=StfOptions("cap2/sfr2.yaml", 20, 1))
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert b'"m": 1.0' in seen[0]


def test_stf_start_refuses_trex_args_before_io(make_server: Any) -> None:
    """Non-empty trex_args beside stf is a ValueError and no request is made."""
    srv = make_server(ok(7))
    with pytest.raises(ValueError):
        srv.client().start_trex(None, {}, "me", 40, "--no-key", stf=StfOptions("cap2/sfr2.yaml", 20))
    assert srv.requests == []


def test_files_list_and_get_file(make_server: Any) -> None:
    """The recorded cap2 listing holds sfr2.yaml, get_file returns the recorded bytes, /etc is a -33 fault."""
    rows = recorded("p5-stf")
    srv = make_server(replay(rows))
    c = srv.client()
    dirs, files = c.files_list("cap2")
    assert dirs == [] and "sfr2.yaml" in files
    wanted = {"filepath": "cap2/sfr2.yaml"}
    want = next(r for r in rows if r["method"] == "get_file" and r["params"] == wanted)
    assert c.get_file("cap2/sfr2.yaml") == base64.b64decode(want["body"]["result"])
    with pytest.raises(DaemonFault) as ei:
        c.files_list("/etc")
    assert ei.value.code == -33
    assert srv.requests[0]["params"] == {"path": "cap2"}


def test_push_file_sends_base64_of_bytes(make_server: Any) -> None:
    """Binary data (NUL and 0xFF) arrives base64-encoded and decodes back equal; text goes as UTF-8."""
    srv = make_server(ok(True))
    c = srv.client()
    blob = b"\x00\x01\xff\xfe" * 100
    assert c.push_file("tg-abc-x.pcap", blob, timeout_s=30.0) is True
    p = srv.requests[0]["params"]
    assert p["filename"] == "tg-abc-x.pcap" and base64.b64decode(p["bin_data"]) == blob
    c.push_file("a.yaml", "café: 1\n")
    assert base64.b64decode(srv.requests[1]["params"]["bin_data"]) == "café: 1\n".encode()
