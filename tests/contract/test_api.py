import contextlib
import http.client
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from support import ROOT, TEST_TOKEN, authed, free_port_pair, pinned_sha
from trex_web_gui import cli, trexapi
from trex_web_gui.api.app import create_app
from trex_web_gui.api.auth import cookie_name

WS = "ws://127.0.0.1/api/ws"
# uvicorn re-raises SIGINT after a clean shutdown; uv reports it as 130
SIGINT_EXIT = (0, 130, -signal.SIGINT)


def client(tmp_path: Path, **kw: Any) -> TestClient:
    """A logged-in TestClient on a fresh app over `tmp_path` (hint "lab1"), not entered: no lifespan."""
    # base_url pins the Host header to an allowed one; "testserver" is never allowed in production.
    return authed(create_app(tmp_path, server_hint="lab1", token=TEST_TOKEN, **kw))


def recv(ws: Any, timeout: float = 10.0) -> Any:
    """receive_json with a deadline, so a missing message fails the test instead of hanging it."""
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(ws.receive_json).result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)     # a timed-out receive must not make the test wait for it


def test_about(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        j = c.get("/api/about").json()
    assert j["trex_pin"]["upstream_sha"] == pinned_sha() and "5.1" in j["supported"]
    assert j["install"] == "source" and j["static_from"] is None


def test_about_names_the_served_static_dir(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    with client(tmp_path / "data", static_dir=dist) as c:
        assert c.get("/api/about").json()["static_from"] == str(dist.resolve())


def test_lifespan_warms_scapy_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trex_web_gui.trexapi import decode

    def refuse() -> Any:
        raise AssertionError("Scapy_service was constructed after startup")
    monkeypatch.setattr(decode, "_service", None)
    with client(tmp_path):
        monkeypatch.setattr(decode, "_construct", refuse)
        assert decode.service() is not None


def test_connect_ws_events_disconnect(tmp_path: Path, fake_trex) -> None:
    f = fake_trex()
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        assert recv(ws)["type"] == "hello"
        assert recv(ws)["state"] == "disconnected"
        r = c.post("/api/session/connect", json={"host": f.host, "sync_port": f.rpc_port,
                                                  "async_port": f.pub_port, "timeout_s": 2})
        assert r.status_code == 200 and r.json()["info"]["version"] == "v3.08"
        # busy, port_labels, capture, monitor, test_setups and daemon from the hello, then connecting and
        # connected
        seen = [recv(ws) for _ in range(8)]
        kinds = {(m["type"], m.get("state") or m.get("name")) for m in seen}
        assert ("session", "connecting") in kinds and ("session", "connected") in kinds
        assert c.post("/api/session/disconnect").json()["state"] == "disconnected"


def test_connect_twice_returns_409(tmp_path: Path, fake_trex) -> None:  # Review Focus 1
    f = fake_trex()
    body = {"host": f.host, "sync_port": f.rpc_port, "async_port": f.pub_port, "timeout_s": 2}
    with client(tmp_path) as c:
        assert c.post("/api/session/connect", json=body).status_code == 200
        r = c.post("/api/session/connect", json=body)
        assert r.status_code == 409 and r.json()["error"] == "busy"


def test_unsupported_version_is_422_with_version(tmp_path: Path, fake_trex) -> None:
    f = fake_trex("--reject-api")
    with client(tmp_path) as c:
        r = c.post("/api/session/connect", json={"host": f.host, "sync_port": f.rpc_port,
                                                  "async_port": f.pub_port, "timeout_s": 2})
    assert (
        r.status_code == 422 and r.json()["error"] == "unsupported_version" and "5.0" in r.json()["message"]
    )


def test_v307_server_is_422_unsupported_version(tmp_path: Path, fake_trex) -> None:
    f = fake_trex("--server-version", "v3.07")
    with client(tmp_path) as c:
        r = c.post("/api/session/connect", json={"host": f.host, "sync_port": f.rpc_port,
                                                  "async_port": f.pub_port, "timeout_s": 2})
    assert r.status_code == 422 and r.json()["error"] == "unsupported_version"
    assert (
        r.json()["message"]
        == "The server runs TRex v3.07; trex-web-gui supports only TRex v3.08 (STL API 5.1)."
    )


def test_bad_request_body_is_422(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert c.post("/api/session/connect", json={"host": "", "sync_port": 70000}).status_code == 422


def test_settings_put_get_and_invalid(tmp_path: Path) -> None:
    body = {"preferences": {"templates_dir": "", "wireshark_path": "wireshark", "workspace_dir": ""},
            "connections": [{"name": "lab", "host": "10.0.0.5", "sync_port": 4501, "async_port": 4500,
                             "timeout_s": 3.0}]}
    with client(tmp_path) as c:
        assert c.put("/api/settings", json=body).status_code == 200
        assert c.get("/api/settings").json()["settings"]["connections"][0]["name"] == "lab"
        body["connections"].append(dict(body["connections"][0]))
        r = c.put("/api/settings", json=body)
        assert r.status_code == 400 and "duplicate" in r.json()["message"]
        body["connections"].pop()
        body["preferences"]["trex_cfg_path"] = "/srv/trex/cfg.yaml"
        assert c.put("/api/settings", json=body).status_code == 200
        assert (
            c.get("/api/settings").json()["settings"]["preferences"]["trex_cfg_path"] == "/srv/trex/cfg.yaml"
        )
        body["preferences"]["trex_cfg_path"] = 5
        assert c.put("/api/settings", json=body).status_code == 422
        # empty and relative paths are refused
        for bad in ("", "etc/trex_cfg.yaml", "/etc/x\x00y"):
            body["preferences"]["trex_cfg_path"] = bad
            r = c.put("/api/settings", json=body)
            assert r.status_code == 422 and "absolute" in r.json()["message"], r.text


_GOOD_CONN = {"name": "lab", "host": "10.0.0.5", "sync_port": 4501, "async_port": 4500, "timeout_s": 3.0}


@pytest.mark.parametrize("patch", [{"sync_port": True}, {"sync_port": 4501.5}, {"name": 1}, {"host": None},
                                   {"timeout_s": "3"}])
def test_put_settings_wrong_connection_type_is_422_and_writes_nothing(
    tmp_path: Path, patch: dict[str, Any]
) -> None:
    with client(tmp_path) as c:
        r = c.put("/api/settings", json={"preferences": {}, "connections": [{**_GOOD_CONN, **patch}]})
        assert r.status_code == 422, r.text
        assert r.json()["error"] == "invalid_settings" and "wrong type" in r.json()["message"]
        assert not (tmp_path / "settings.toml").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"preferences": {"templates_dir": 5}},
        {"preferences": []},
        {"connections": 3},
        {"connections": ["x"]},
        {"connections": [{"name": "a"}]},
    ],
)
def test_put_settings_wrong_shape_is_422_and_writes_nothing(tmp_path: Path, body: dict[str, Any]) -> None:
    with client(tmp_path) as c:
        r = c.put("/api/settings", json=body)
        assert r.status_code == 422, r.text
        assert r.json()["error"] == "invalid_settings" and r.json()["message"]
        assert not (tmp_path / "settings.toml").exists()


def test_put_settings_wrong_type_leaves_existing_file_unchanged(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert (
            c.put("/api/settings", json={"preferences": {}, "connections": [_GOOD_CONN]}).status_code == 200
        )
        before = (tmp_path / "settings.toml").read_bytes()
        assert (
            c.put("/api/settings", json={"connections": [{**_GOOD_CONN, "sync_port": True}]}).status_code
            == 422
        )
        assert (tmp_path / "settings.toml").read_bytes() == before


def test_non_utf8_settings_file_starts_app_on_defaults(tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_bytes(
        '[preferences]\ntemplates_dir = "/home/ren\u00e9"\n'.encode("latin-1")
    )
    with client(tmp_path) as c:
        r = c.get("/api/settings")
        assert r.status_code == 200
        assert r.json()["load_error"] and "settings.toml" in r.json()["load_error"]
        assert r.json()["settings"]["preferences"]["templates_dir"] == ""


def test_put_settings_publishes_settings_to_an_open_websocket(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        for _ in range(8):          # hello, session, busy, port_labels, capture, monitor, test_setups, daemon
            recv(ws)
        body = {"preferences": {"wireshark_path": "/ws"}, "connections": [_GOOD_CONN]}
        r = c.put("/api/settings", json=body)
        assert r.status_code == 200
        msg = recv(ws)
        assert msg["type"] == "settings" and msg == {"type": "settings", **r.json()}
        assert msg["settings"]["preferences"]["wireshark_path"] == "/ws"


def test_get_settings_carries_data_dir(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert c.get("/api/settings").json()["data_dir"] == str(tmp_path)


def test_put_settings_oserror_is_error_shape(tmp_path: Path) -> None:
    data = tmp_path / "ro"
    data.mkdir()
    with client(data) as c:
        data.chmod(0o500)                      # readable (load works), not writable (save fails)
        try:
            r = c.put("/api/settings", json={"preferences": {}, "connections": []})
        finally:
            data.chmod(0o700)
    assert r.status_code == 500 and r.json()["error"] == "storage_error" and r.json()["message"]


def test_cli_port_range() -> None:
    p = cli.build_parser()
    assert p.parse_args(["serve", "--port", "65535"]).port == 65535
    for bad in ("0", "65536", "-1", "x"):
        with pytest.raises(SystemExit):
            p.parse_args(["serve", "--port", bad])


def make_static(tmp_path: Path) -> Path:
    """Write a static dir (index.html, app.txt, assets/) under `tmp_path`, with a secret.txt beside it that
    must not be served; returns the static dir."""
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    st = tmp_path / "static"
    (st / "assets").mkdir(parents=True)
    (st / "index.html").write_text("<html>INDEX</html>")
    (st / "app.txt").write_text("APPFILE")
    return st


def test_static_traversal_does_not_leak(tmp_path: Path) -> None:
    st = make_static(tmp_path)
    with client(tmp_path / "data", static_dir=st) as c:
        assert c.get("/app.txt").text == "APPFILE"
        assert "INDEX" in c.get("/some/client/route").text
        for url in ("/..%2Fsecret.txt", "/%2E%2E/secret.txt", "/..%2f..%2fsecret.txt"):
            r = c.get(url)
            assert "TOPSECRET" not in r.text, url
            assert "INDEX" in r.text, url
        r = c.get("/api/nope")
        assert r.status_code == 404 and r.json()["error"] == "not_found"


@pytest.mark.parametrize("url", ["/%00", "/" + "a" * 300, "/x/%00/y"])
def test_static_pathological_paths_fall_back_to_index(tmp_path: Path, url: str) -> None:
    st = make_static(tmp_path)
    with client(tmp_path / "data", static_dir=st) as c:
        r = c.get(url)
        assert r.status_code == 200 and "INDEX" in r.text


def test_host_allow_list(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert c.get("/api/about", headers={"Host": "evil.example"}).status_code == 400
        assert c.get("/api/about", headers={"Host": "127.0.0.1:8791"}).status_code == 200
        assert c.get("/api/about", headers={"Host": "localhost:5173"}).status_code == 200
        with pytest.raises(WebSocketDisconnect), c.websocket_connect(WS, headers={"Host": "evil.example"}):
            pass
        with c.websocket_connect(WS, headers={"Host": "localhost:5173"}) as ws:
            assert recv(ws)["type"] == "hello"


def test_ws_hello_sends_empty_busy_when_no_port_is_busy(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        assert [recv(ws)["type"] for _ in range(2)] == ["hello", "session"]
        assert recv(ws) == {"type": "busy", "ports": {}}


def test_unserialisable_message_is_logged_and_does_not_poison_others(
        tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(tmp_path)
    with authed(app) as c:
        with c.websocket_connect(WS) as ws1:
            assert [recv(ws1)["type"] for _ in range(8)] == ["hello", "session", "busy", "port_labels",
                                                             "capture", "monitor", "test_setups", "daemon"]
            with caplog.at_level(logging.ERROR, logger="trex_web_gui.api.app"):
                c.portal.call(app.state.hub.publish, {"type": "event", "bad": b"bytes"})
                with pytest.raises(WebSocketDisconnect):
                    recv(ws1)
        assert any("serializable" in r.getMessage() or (r.exc_info and "serializable" in str(r.exc_info[1]))
                   for r in caplog.records)
        with c.websocket_connect(WS) as ws2:
            assert [recv(ws2)["type"] for _ in range(8)] == ["hello", "session", "busy", "port_labels",
                                                             "capture", "monitor", "test_setups", "daemon"]
            c.portal.call(app.state.hub.publish, {"type": "event", "n": 1})
            assert recv(ws2)["n"] == 1


def test_serve_binds_loopback_only() -> None:
    assert cli.LOOPBACK == "127.0.0.1"
    assert "--listen" not in cli.build_parser().format_help()


def test_lifespan_shutdown_closes_a_lost_session(tmp_path: Path, fake_trex) -> None:
    """MUST-HAVE: app shutdown awaits TrexSession.close() on a LOST session (client released, executor
    down). Guards the lifespan itself; the subprocess test below guards the CLI path."""
    f = fake_trex()
    app = create_app(tmp_path)
    with authed(app) as c:
        r = c.post("/api/session/connect", json={"host": f.host, "sync_port": f.rpc_port,
                                                  "async_port": f.pub_port, "timeout_s": 0.5})
        assert r.status_code == 200
        f.proc.send_signal(signal.SIGSTOP)
        deadline = time.monotonic() + trexapi.loss_bound(0.5) + 5
        while c.get("/api/session").json()["state"] != "lost":
            assert time.monotonic() < deadline, "session never went LOST"
            time.sleep(0.1)
        s = app.state.session
        assert s._client is not None and not s._closed
    assert s._closed and s._client is None
    assert s.snapshot()["state"] == "disconnected"


def _http(port: int, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
    """One JSON request to a child `trex-web-gui serve` started with TREX_WEB_GUI_TOKEN=TEST_TOKEN, logged in
    by cookie."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Cookie": f"{cookie_name(TEST_TOKEN)}={TEST_TOKEN}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        out: dict[str, object] = json.load(r)
        return out


def test_cli_serve_exits_promptly_on_sigint_with_lost_session(tmp_path: Path, fake_trex) -> None:
    """CLI-exit SMOKE test, not the close guard: it does not fail if the close is removed (measured). The
    guard is test_lifespan_shutdown_closes_a_lost_session. This one proves `serve` with a LOST session
    runs uvicorn's lifespan shutdown and exits within a bound. SIGINT, not
    SIGTERM: uvicorn re-raises SIGTERM with the default handler (instant death, no interpreter
    finalisation)."""
    f = fake_trex()
    timeout_s = 1.0
    port = free_port_pair()[0]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "TREX_WEB_GUI_TOKEN": TEST_TOKEN}
    code = (
        "from trex_web_gui.cli import main; "
        f"main(['serve','--dev','--port','{port}','--data-dir',{str(tmp_path)!r}])"
    )
    srv = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                _http(port, "/api/about")
                break
            except OSError:
                assert srv.poll() is None and time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)
        _http(port, "/api/session/connect", {"host": f.host, "sync_port": f.rpc_port,
                                              "async_port": f.pub_port, "timeout_s": timeout_s})
        f.proc.send_signal(signal.SIGSTOP)
        deadline = time.monotonic() + trexapi.loss_bound(timeout_s) + 5
        while _http(port, "/api/session")["state"] != "lost":
            assert time.monotonic() < deadline, "session never went LOST"
            time.sleep(0.1)
        login = http.client.HTTPConnection("127.0.0.1", port, timeout=10)      # no redirect following
        login.request("GET", f"/api/auth/login?x=1&token={TEST_TOKEN}")
        assert login.getresponse().status == 303
        login.close()
        t0 = time.monotonic()
        srv.send_signal(signal.SIGINT)
        out = srv.communicate(timeout=30)[0]
        # an access line: "<date> <time> INFO:     <client> - "GET /api/auth/login…" (RC1-26 added the time)
        access = [ln for ln in out.splitlines() if "GET /api/auth/login" in ln and " INFO: " in ln]
        assert access and all(TEST_TOKEN not in ln and "token=<redacted>" in ln for ln in access), out
        rc = srv.returncode
        elapsed = time.monotonic() - t0
        print(f"SHUTDOWN_WITH_LOST_SESSION rc={rc} elapsed={elapsed:.2f}s")
        assert "Application shutdown complete." in out
        assert rc in (0, 130, -signal.SIGINT) and elapsed < 3 * trexapi.loss_bound(timeout_s)
    finally:
        if srv.poll() is None:
            srv.kill()
            srv.wait()


def serve(tmp_path: Path) -> tuple[subprocess.Popen[str], int]:
    """Start the real `trex-web-gui serve` in its own process group (as a terminal's Ctrl+C reaches uv and
    the server alike) and wait for /api/about. The caller must call stop_serve() in a finally."""
    port = free_port_pair()[0]
    proc = subprocess.Popen(["uv", "run", "--project", str(ROOT / "backend"), "trex-web-gui", "serve",
                             "--port", str(port), "--data-dir", str(tmp_path / "data")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True, env={**os.environ, "TREX_WEB_GUI_TOKEN": TEST_TOKEN})
    deadline = time.monotonic() + 60
    while True:
        try:
            _http(port, "/api/about")
            return proc, port
        except OSError:
            if proc.poll() is not None or time.monotonic() > deadline:
                stop_serve(proc)
                raise RuntimeError("HARNESS ERROR: trex-web-gui serve did not start") from None
            time.sleep(0.1)


def sigint_group(proc: subprocess.Popen[str]) -> None:
    """Send SIGINT to the process group `proc` leads, as a terminal's Ctrl+C does; `proc` must lead its own
    group, as serve() starts it."""
    os.killpg(proc.pid, signal.SIGINT)


def stop_serve(proc: subprocess.Popen[str]) -> str:
    """Make sure no process of the serve group outlives the test; returns the remaining output."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    return proc.communicate()[0] or ""


def test_double_sigint_with_open_websocket_prints_no_traceback(tmp_path: Path, fake_trex) -> None:
    """Replaces P1's test that cancelled uvicorn's LifespanOn task (uvicorn internals). P1 traced the
    traceback to receive() raising CancelledError inside the WebSocket watch_close task while a browser
    socket was open, so this opens one before the two Ctrl+C presses, 0.2 s apart. The server is connected to
    a silent fake so the shutdown outlasts 0.2 s: otherwise the second signal finds the process gone and
    the test proves nothing (a hard-exit on the second SIGINT passed it)."""
    from websockets.sync.client import connect
    f = fake_trex()
    proc, port = serve(tmp_path)
    try:
        _http(port, "/api/session/connect", {"host": f.host, "sync_port": f.rpc_port,
                                              "async_port": f.pub_port, "timeout_s": 1.0})
        f.proc.send_signal(signal.SIGSTOP)
        with connect(f"ws://127.0.0.1:{port}/api/ws",
                     additional_headers={"Cookie": f"{cookie_name(TEST_TOKEN)}={TEST_TOKEN}"}) as sock:
            assert json.loads(sock.recv(timeout=10))["type"] == "hello"
            sigint_group(proc)
            time.sleep(0.2)
            assert proc.poll() is None, "the server exited before the second SIGINT"
            sigint_group(proc)
            proc.wait(timeout=30)
    finally:
        out = stop_serve(proc)
    assert proc.returncode in SIGINT_EXIT, out
    assert "Traceback" not in out, out      # a force-exit may skip "Application shutdown complete."


def test_back_to_back_sigints_print_no_traceback(tmp_path: Path) -> None:
    """Uvicorn skips lifespan shutdown on the second SIGINT, then re-raises SIGINT so asyncio.run cancels the
    lifespan task while Starlette waits for the shutdown message: that used to log a CancelledError traceback
    (a parked P1 item). With the signals 0.02 s apart this was 14 of 15 runs."""
    proc, _ = serve(tmp_path)
    try:
        sigint_group(proc)
        time.sleep(0.02)
        sigint_group(proc)
        proc.wait(timeout=30)
    finally:
        out = stop_serve(proc)
    assert proc.returncode in SIGINT_EXIT, out
    assert "Traceback" not in out, out


def test_sigint_during_lifespan_cleanup_lets_the_close_finish(tmp_path: Path) -> None:
    """The cause of the test above failing under load (P3a Task 11): a further SIGINT during asyncio.run's
    final cancel. `uv run` forwards each SIGINT to the server again about 0.2 s later (measured: 4
    arrive for 2 sent). uvicorn captures 2 and skips lifespan shutdown. It restores the handlers and
    raises SIGINT, and asyncio.run's final cancel cancels the lifespan task, which was awaiting
    receive() after the yield. Its cleanup then closes the session. With the default handler, a
    further SIGINT during that cleanup raised KeyboardInterrupt in the event loop, and asyncio.run
    stopped with the task pending. The session close never finished. When the task was
    garbage-collected, Starlette sent the GeneratorExit as lifespan.shutdown.failed, and uvicorn
    logged its traceback at ERROR.
    Deterministic version: the close is slowed and marked. The second SIGINT is sent once uvicorn logs
    "Shutting down". The harness holds uvicorn's wait for connections until force_exit is set (as an
    open connection does in production), so the second SIGINT cannot miss it and always sets force_exit
    before the lifespan shutdown. The further SIGINT is sent while the close runs. gc.collect() makes
    the collection happen before exit, not whenever the collector next runs. The test runs `cli.main`
    directly, without uv, so every signal is one this test sends."""
    port = free_port_pair()[0]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "TREX_WEB_GUI_TOKEN": TEST_TOKEN}
    code = (
        "import asyncio, gc\n"
        "from trex_web_gui.cli import main\n"
        "from trex_web_gui.sessions import TrexSession\n"
        "real_close = TrexSession.close\n"
        "async def close(self):\n"
        "    print('HARNESS closing', flush=True)\n"
        # a close that takes a while, as one against a real server does
        "    await asyncio.sleep(1.0)\n"
        "    await real_close(self)\n"
        "    print('HARNESS closed', flush=True)\n"
        "TrexSession.close = close\n"
        # uvicorn waits for open connections before it stops; in production an open connection holds the
        # shutdown until the second SIGINT sets force_exit. The harness has none, so hold it the same way: the
        # second SIGINT then cannot miss the 0.1 s drain after "Shutting down", however loaded the machine is.
        "import uvicorn.server\n"
        "real_wait = uvicorn.server.Server._wait_tasks_to_complete\n"
        "async def wait_tasks_to_complete(self):\n"
        "    while not self.force_exit:\n"
        "        await asyncio.sleep(0.01)\n"
        "    await real_wait(self)\n"
        "uvicorn.server.Server._wait_tasks_to_complete = wait_tasks_to_complete\n"
        "try:\n"
        f"    main(['serve', '--port', '{port}', '--data-dir', {str(tmp_path / 'data')!r}])\n"
        "finally:\n"
        "    gc.collect()\n")
    srv = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
    watchdog = threading.Timer(60, srv.kill)
    watchdog.start()
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                _http(port, "/api/about")
                break
            except OSError:
                assert srv.poll() is None and time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)
        assert srv.stdout is not None
        head = ""

        def read_until(marker: str) -> None:
            nonlocal head
            while marker not in head:
                line = srv.stdout.readline() if srv.stdout else ""
                assert line, f"the server exited before {marker!r}:\n" + head
                head += line

        srv.send_signal(signal.SIGINT)
        read_until("Shutting down")
        srv.send_signal(signal.SIGINT)       # force_exit: uvicorn skips the lifespan shutdown
        read_until("HARNESS closing")        # now inside asyncio.run's final cancel
        srv.send_signal(signal.SIGINT)       # what uv's forwarded copy does in the test above
        out = head + srv.communicate(timeout=30)[0]
    finally:
        watchdog.cancel()
        if srv.poll() is None:
            srv.kill()
            srv.wait()
    assert srv.returncode in SIGINT_EXIT, out
    # force_exit ran: uvicorn LifespanOn.shutdown not called
    assert "Waiting for application shutdown." not in out, out
    assert "HARNESS closed" in out, out      # the session close ran to the end
    assert "Traceback" not in out, out
    assert cli.IGNORED_SIGINT_MESSAGE.decode().strip() in out, out  # the further SIGINT came during the close


@pytest.mark.parametrize("settle_s, within", [
    (0.0, lambda t: t + 2),                    # SIGINT right after the server went silent: nothing in flight
    # an RPC (the subscriber's get_async_events) is already in flight
    (1.0, trexapi.loss_bound)])
def test_sigint_with_silent_server_exits_within_bound(
    tmp_path: Path, fake_trex, settle_s: float, within: Any
) -> None:
    f = fake_trex()
    timeout_s = 1.0
    proc, port = serve(tmp_path)
    try:
        _http(port, "/api/session/connect", {"host": f.host, "sync_port": f.rpc_port,
                                              "async_port": f.pub_port, "timeout_s": timeout_s})
        # connect's barrier triggers send each subscriber one more get_async_events just after connect()
        # returns (FU4-1, concurrent connect); without this wait the settle=0 case can catch one in flight,
        # which is the settle=1.0 case's job
        time.sleep(0.1)
        f.proc.send_signal(signal.SIGSTOP)
        time.sleep(settle_s)
        t0 = time.monotonic()
        sigint_group(proc)
        proc.wait(timeout=60)
        elapsed = time.monotonic() - t0
    finally:
        out = stop_serve(proc)
    print(
        f"SHUTDOWN_SILENT_SERVER settle={settle_s} timeout_s={timeout_s} "
        f"rc={proc.returncode} elapsed={elapsed:.2f}s"
    )
    assert proc.returncode in SIGINT_EXIT and "Traceback" not in out, out
    assert elapsed <= within(timeout_s), f"{elapsed:.2f} s > {within(timeout_s)} s"
    assert "Waiting up to" in out and "for the TRex server to answer before exiting" in out


def test_framework_404_and_405_under_api_use_the_error_shape(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.get("/api/nope")
        assert r.status_code == 404 and r.json() == {"error": "not_found", "message": "Not Found"}
        r = c.get("/api/session/connect")                   # a POST-only route
        assert r.status_code == 405 and r.json() == {
            "error": "method_not_allowed",
            "message": "Method Not Allowed",
        }
        assert "POST" in r.headers["allow"]


def test_spa_fallback_survives_the_error_handler(tmp_path: Path) -> None:
    st = make_static(tmp_path)
    with client(tmp_path / "data", static_dir=st) as c:
        assert "INDEX" in c.get("/some/client/route").text
        r = c.get("/api/nope")
        assert r.status_code == 404 and r.json()["error"] == "not_found"
        r = c.post("/some/client/route")                     # the fallback serves GET only
        assert r.status_code == 405 and r.json()["error"] == "method_not_allowed"


# ---- port labels (P3a Task 4) ----

_LBL = {"server": "10.0.0.5:4501", "key": "aa:bb:cc:00:11:22", "name": "uplink-A",
        "description": "to the DUT"}


def test_port_labels_get_put_round_trip(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert c.get("/api/port-labels").json() == {"labels": {}, "load_error": None}
        r = c.put("/api/port-labels", json=_LBL)
        want = {"labels": {"10.0.0.5:4501": {"aa:bb:cc:00:11:22": {"name": "uplink-A",
                                                                   "description": "to the DUT"}}},
                "load_error": None}
        assert r.status_code == 200 and r.json() == want
        assert c.get("/api/port-labels").json() == want
    with client(tmp_path) as c2:                                    # persisted
        assert c2.get("/api/port-labels").json() == want
        assert c2.put("/api/port-labels", json={**_LBL, "name": "", "description": ""}).json()["labels"] == {}


@pytest.mark.parametrize("patch, field", [
    ({"name": "n" * 41}, "name"), ({"description": "d" * 501}, "description"), ({"name": "a\nb"}, "name"),
    ({"key": "AA:bb"}, "key"), ({"server": "h" * 301}, "server"), ({"name": 5}, "name"),
    ({"server": None}, "server")])
def test_port_labels_put_422_names_the_field(tmp_path: Path, patch: dict[str, Any], field: str) -> None:
    with client(tmp_path) as c:
        r = c.put("/api/port-labels", json={**_LBL, **patch})
        assert r.status_code == 422 and field in r.json()["message"] and r.json()["error"]
        assert not (tmp_path / "port_labels.toml").exists()


def test_port_labels_put_missing_field_is_422(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.put("/api/port-labels", json={"server": "h:1", "key": "#0"})
        assert r.status_code == 422 and "name" in r.json()["message"] and "description" in r.json()["message"]


def test_port_labels_corrupt_file_load_error_then_save_keeps_a_copy(tmp_path: Path) -> None:
    (tmp_path / "port_labels.toml").write_text("[[x\nbad = ")
    with client(tmp_path) as c:
        j = c.get("/api/port-labels").json()
        assert j["labels"] == {} and "port_labels.toml" in j["load_error"]
        assert (tmp_path / "port_labels.toml").read_text() == "[[x\nbad = "
        r = c.put("/api/port-labels", json=_LBL)
        assert r.status_code == 200 and r.json()["load_error"] is None
        assert len(list(tmp_path.glob("port_labels.toml.bad-*"))) == 1


def test_port_labels_put_oserror_is_error_shape(tmp_path: Path) -> None:
    data = tmp_path / "ro"
    data.mkdir()
    with client(data) as c:
        data.chmod(0o500)                      # readable (load works), not writable (save fails)
        try:
            r = c.put("/api/port-labels", json=_LBL)
        finally:
            data.chmod(0o700)
    assert r.status_code == 500 and r.json()["error"] == "storage_error" and r.json()["message"]


def test_port_labels_put_is_published(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        assert [recv(ws)["type"] for _ in range(8)] == ["hello", "session", "busy", "port_labels", "capture",
                                                      "monitor", "test_setups", "daemon"]
        c.put("/api/port-labels", json=_LBL)
        m = recv(ws)
        assert m == {"type": "port_labels", "load_error": None, "labels": {_LBL["server"]: {_LBL["key"]: {
            "name": "uplink-A", "description": "to the DUT"}}}}


def test_port_labels_two_concurrent_puts_both_persist_and_both_broadcast(tmp_path: Path) -> None:
    a = {**_LBL, "key": "#0", "name": "A"}
    b = {**_LBL, "key": "#1", "name": "B"}
    with client(tmp_path) as c, c.websocket_connect(WS) as ws1, c.websocket_connect(WS) as ws2:
        for w in (ws1, ws2):
            assert [recv(w)["type"] for _ in range(8)] == ["hello", "session", "busy", "port_labels",
                                                           "capture", "monitor", "test_setups", "daemon"]
        with ThreadPoolExecutor(max_workers=2) as ex:
            rs = list(ex.map(lambda body: c.put("/api/port-labels", json=body), (a, b)))
        assert [r.status_code for r in rs] == [200, 200]
        both = {"#0", "#1"}
        assert set(c.get("/api/port-labels").json()["labels"][_LBL["server"]]) == both
        for w in (ws1, ws2):
            last = recv(w)
            # state messages coalesce: the newest wins
            while set(last["labels"].get(_LBL["server"], {})) != both:
                last = recv(w)
            assert last["type"] == "port_labels"
    with client(tmp_path) as c2:
        assert set(c2.get("/api/port-labels").json()["labels"][_LBL["server"]]) == both


def test_port_labels_foreign_origin_is_refused(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, origin_ports=frozenset({8090}))) as c:
        r = c.put("/api/port-labels", json=_LBL, headers={"Origin": "http://evil.example"})
        assert r.status_code == 403 and r.json()["error"] == "forbidden_origin"
        assert not (tmp_path / "port_labels.toml").exists()
        assert c.put("/api/port-labels", json=_LBL, headers={"Host": "evil.example"}).status_code == 400
        assert (
            c.put("/api/port-labels", json=_LBL, headers={"Origin": "http://127.0.0.1:8090"}).status_code
            == 200
        )


def test_new_websocket_receives_the_labels_after_the_snapshot(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.put("/api/port-labels", json=_LBL)
        with c.websocket_connect(WS) as ws:
            assert [recv(ws)["type"] for _ in range(3)] == ["hello", "session", "busy"]
            m = recv(ws)
            assert m["type"] == "port_labels" and m["load_error"] is None
            assert m["labels"][_LBL["server"]][_LBL["key"]]["name"] == "uplink-A"


def test_new_websocket_after_the_last_label_was_deleted_still_gets_empty_labels(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.put("/api/port-labels", json=_LBL)
        c.put("/api/port-labels", json={**_LBL, "name": "", "description": ""})
        with c.websocket_connect(WS) as ws:
            assert [recv(ws)["type"] for _ in range(3)] == ["hello", "session", "busy"]
            assert recv(ws) == {"type": "port_labels", "labels": {}, "load_error": None}


def test_new_websocket_learns_of_a_corrupt_label_file(tmp_path: Path) -> None:
    (tmp_path / "port_labels.toml").write_text("[[x\nbad = ")
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        assert [recv(ws)["type"] for _ in range(3)] == ["hello", "session", "busy"]
        m = recv(ws)
        assert m["labels"] == {} and "port_labels.toml" in m["load_error"]


def test_ws_hello_carries_server_ports(tmp_path: Path) -> None:
    app = create_app(tmp_path, server_hint="lab1", server_ports=(4601, 4600), token=TEST_TOKEN)
    with authed(app) as c, c.websocket_connect(WS) as ws:
        hello = recv(ws)
    assert hello["server_hint"] == "lab1"
    assert hello["server_ports"] == {"sync_port": 4601, "async_port": 4600}
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        assert recv(ws)["server_ports"] is None
