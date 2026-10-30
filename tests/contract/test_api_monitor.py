"""/api/monitor, --monitor-only (409 on every mutating family) and --monitor (spec §6.1, §6.2)."""
import time
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from support import FIXTURES, TEST_TOKEN, authed
from test_api import WS, recv
from trex_web_gui.api.app import create_app

STL = str(Path(FIXTURES) / "p5" / "pub-stl.jsonl")
WRITES = {"POST", "PUT", "DELETE", "PATCH"}
FAMILIES = ("/api/daemon", "/api/profiles", "/api/capture", "/api/studio", "/api/fs")
STATE_GETS = ("/api/session", "/api/ports", "/api/ports/0/xstats", "/api/ports/0/rate")


def wait_state(c: Any, state: str, timeout: float = 5.0) -> dict:
    """Poll GET /api/monitor until its state is `state`; AssertionError on the deadline."""
    end = time.monotonic() + timeout
    while True:
        j = c.get("/api/monitor").json()
        if j["state"] == state:
            return j
        assert time.monotonic() < end, f"monitor stayed {j['state']}, wanted {state}"
        time.sleep(0.05)


def wait_data(c: Any, slot: str, timeout: float = 5.0) -> dict:
    """Poll GET /api/monitor until `slot` holds data."""
    end = time.monotonic() + timeout
    while True:
        j = c.get("/api/monitor").json()
        if j[slot] is not None:
            return j
        assert time.monotonic() < end, f"no {slot}"
        time.sleep(0.05)


def test_connect_get_disconnect(tmp_path: Path, fake_trex) -> None:
    fake = fake_trex("--pub-replay", STL)
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        first = c.get("/api/monitor").json()
        assert first["state"] == "disconnected" and first["target"] is None and first["alive"] is False
        r = c.post("/api/monitor/connect", json={"host": fake.host, "port": fake.pub_port})
        assert r.status_code == 200 and r.json()["target"] == {"host": fake.host, "port": fake.pub_port}
        wait_state(c, "connected")
        assert "m_tx_bps" in wait_data(c, "global")["global"]
        r = c.post("/api/monitor/disconnect")
        assert r.status_code == 200 and r.json()["state"] == "disconnected" and r.json()["global"] is None
        assert c.get("/api/monitor").json()["target"] is None
    assert fake_trex_served(fake) == {}


def fake_trex_served(fake: Any) -> dict[str, int]:
    """Stop the fake and return the RPC methods it served (the Monitor must have made none)."""
    fake.stop()
    return fake.served()


def test_bad_bodies_are_422(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        for body in ({}, {"host": ""}, {"host": "a b"}, {"host": "h/x"}, {"host": "[::1]"},
                     {"host": "h", "port": 0}, {"host": "h", "port": 65536}, {"host": "h", "port": "x"},
                     {"host": 5}, {"host": "-x"}, {"host": "_x"}, {"host": ".a"}, {"host": "-"},
                     {"host": "."}, {"host": ".."}, {"host": "a..b"}):
            r = c.post("/api/monitor/connect", json=body)
            assert r.status_code == 422 and r.json()["error"] == "bad_request", body
        assert c.post("/api/monitor/connect", content=b"not json").status_code == 422
        assert c.get("/api/monitor").json()["state"] == "disconnected"


def test_refused_endpoint_is_422_and_leaves_the_monitor_disconnected(tmp_path: Path, monkeypatch) -> None:
    """A host that passes the rules but that libzmq refuses: 422 invalid, state back to disconnected."""
    import zmq
    from trex_web_gui.monitor.subscriber import MonitorSubscriber

    def refuse(self: Any) -> None:
        """Stand-in for _open: libzmq's EINVAL."""
        raise zmq.ZMQError(zmq.EINVAL)
    monkeypatch.setattr(MonitorSubscriber, "_open", refuse)
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post("/api/monitor/connect", json={"host": "lab1", "port": 4500})
        assert r.status_code == 422 and r.json()["error"] == "invalid"
        j = c.get("/api/monitor").json()
        assert j["state"] == "disconnected" and j["target"] is None


def test_lifespan_closes_the_session_when_the_monitor_close_raises(tmp_path: Path) -> None:
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        async def boom() -> None:
            """A Monitor close that fails."""
            raise RuntimeError("monitor close failed")
        app.state.monitor.close = boom
        assert c.get("/api/monitor").status_code == 200
        session = app.state.session
    assert session._closed


def test_ws_snapshot_includes_monitor(tmp_path: Path, fake_trex) -> None:
    fake = fake_trex("--pub-replay", STL)
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        c.post("/api/monitor/connect", json={"host": fake.host, "port": fake.pub_port})
        wait_state(c, "connected")
        with c.websocket_connect(WS) as ws:
            kinds = [recv(ws)["type"] for _ in range(8)]
            assert kinds == ["hello", "session", "busy", "port_labels", "capture", "monitor", "test_setups",
                             "daemon"]
        with c.websocket_connect(WS) as ws:
            seen = [recv(ws) for _ in range(6)]
            m = seen[-1]
            assert m["type"] == "monitor" and m["target"] == {"host": fake.host, "port": fake.pub_port}


def api_routes(routes: Any) -> list[APIRoute]:
    """Every APIRoute under `routes`, looking inside the include_router wrappers (these routers carry full
    paths and no prefix)."""
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out.extend(api_routes(r.original_router.routes))
    return out


def refused(r: Any) -> bool:
    """Whether `r` is the 409 monitor_only reply."""
    return (r.status_code == 409 and r.json() == {"error": "monitor_only",
                                                   "message": "This backend runs with --monitor-only"})


def fill(path: str) -> str:
    """The route path with `{pid}` = 0 and every other parameter = x."""
    out = []
    for part in path.split("/"):
        out.append("0" if part == "{pid}" else "x" if part.startswith("{") else part)
    return "/".join(out)


def under(path: str, prefix: str) -> bool:
    """Whether `path` is `prefix` or below it."""
    return path == prefix or path.startswith(prefix + "/")


def test_monitor_only_refuses_every_family(tmp_path: Path, fake_trex) -> None:
    fake = fake_trex("--pub-replay", STL)
    control = fake_trex()                     # a TRex the refused requests are aimed at, with real bodies
    app = create_app(tmp_path, token=TEST_TOKEN, monitor_only=True, monitor_target=(fake.host, fake.pub_port))
    routes = [r for r in api_routes(app.routes) if r.path != "/{path:path}"]
    assert len(routes) > 60
    must: list[tuple[str, str]] = []              # (method, route path) that have to answer 409
    for r in routes:
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            if any(under(r.path, f) for f in FAMILIES):
                must.append((m, r.path))
            elif m in WRITES and not (under(r.path, "/api/monitor") or under(r.path, "/api/auth")):
                must.append((m, r.path))
    for needed in ("/api/session/connect", "/api/ports/start", "/api/settings/bundle/apply"):
        assert ("POST", needed) in must, needed
    assert ("PUT", "/api/settings") in must and ("PUT", "/api/test-setups/{slug}") in must
    assert ("GET", "/api/capture/files/{name}") in must          # a download is a GET, and refused
    assert ("GET", "/api/fs/list") in must                       # the host file browser is refused whole
    assert not any(p == "/api/monitor/connect" for _, p in must)
    with authed(app) as c:
        bad = [(m, p) for m, p in must if not refused(c.request(m, fill(p)))]
        assert bad == []
        # valid bodies, aimed at a live TRex: still refused, and the TRex hears nothing
        for path, body in (("/api/session/connect", {"host": control.host, "sync_port": control.rpc_port,
                                                      "async_port": control.pub_port, "timeout_s": 2}),
                           ("/api/ports/acquire", {"ports": [0]}),
                           ("/api/ports/start", {"ports": [0], "mult": "1pps"}),
                           ("/api/daemon/start", {"cfg_path": "/tmp/trex_files/a.yaml", "options": {},
                                                  "trex_args": "", "timeout": 20, "confirm": True})):
            assert refused(c.post(path, json=body)), path
        live = {p: c.get(p) for p in STATE_GETS}      # xstats and rate answer their own 409 not_connected
        assert not any(refused(r) for r in live.values()), {p: r.json() for p, r in live.items()}
        assert live["/api/session"].status_code == 200 and live["/api/ports"].status_code == 200
        assert live["/api/ports/0/xstats"].json()["error"] == "not_connected"
        assert live["/api/ports/0/rate"].json()["error"] == "not_connected"
    assert fake_trex_served(control) == {}        # no RPC from any refused request
    assert fake_trex_served(fake) == {}           # nor from the Monitor


def test_monitor_only_still_serves_monitor_and_settings_get(tmp_path: Path, fake_trex) -> None:
    fake = fake_trex("--pub-replay", STL)
    with authed(create_app(tmp_path, token=TEST_TOKEN, monitor_only=True)) as c:
        assert c.get("/api/settings").status_code == 200
        assert c.get("/api/about").status_code == 200
        assert c.get("/api/test-setups").status_code == 200
        assert c.get("/api/port-labels").status_code == 200
        assert c.get("/api/auth/status").json() == {"authenticated": True}
        assert c.get("/api/monitor").json()["state"] == "disconnected"
        r = c.post("/api/monitor/connect", json={"host": fake.host, "port": fake.pub_port})
        assert r.status_code == 200
        wait_state(c, "connected")
        assert c.post("/api/monitor/disconnect").status_code == 200
        with c.websocket_connect(WS) as ws:
            hello = recv(ws)
        assert hello["monitor_only"] is True and hello["monitor_target"] is None
        assert refused(c.put("/api/settings", json={}))              # before body validation
        assert refused(c.post("/api/session/connect", json={"host": "x", "sync_port": 0}))
        assert c.post("/api/auth/logout").status_code == 200        # the token gate's own write stays


def test_monitor_flag_auto_connects(tmp_path: Path, fake_trex) -> None:
    fake = fake_trex("--pub-replay", STL)
    app = create_app(tmp_path, token=TEST_TOKEN, monitor_only=True, monitor_target=(fake.host, fake.pub_port))
    with authed(app) as c:
        j = wait_state(c, "connected")
        assert j["target"] == {"host": fake.host, "port": fake.pub_port}
        with c.websocket_connect(WS) as ws:
            hello = recv(ws)
        assert hello["monitor_target"] == {"host": fake.host, "port": fake.pub_port}
        assert hello["monitor_only"] is True


def test_monitor_flag_without_monitor_only(tmp_path: Path, fake_trex) -> None:
    mon = fake_trex("--pub-replay", STL)
    trex = fake_trex()
    app = create_app(tmp_path, token=TEST_TOKEN, monitor_target=(mon.host, mon.pub_port))
    with authed(app) as c:
        wait_state(c, "connected")
        with c.websocket_connect(WS) as ws:
            hello = recv(ws)
        assert hello["monitor_only"] is False
        assert hello["monitor_target"] == {"host": mon.host, "port": mon.pub_port}
        r = c.post("/api/session/connect", json={"host": trex.host, "sync_port": trex.rpc_port,
                                                  "async_port": trex.pub_port, "timeout_s": 2})
        assert r.status_code == 200 and not refused(r)
        assert c.get("/api/monitor").json()["state"] == "connected"      # a Control connect leaves it alone
        c.post("/api/session/disconnect")

