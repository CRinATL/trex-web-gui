"""HTTP surface for P2 (Task 7)."""
from pathlib import Path

import pytest
from support import FIXTURES, authed
from test_api import WS, client, recv
from trex_web_gui import trexapi
from trex_web_gui.api import app as app_module
from trex_web_gui.api.app import create_app

PROFILE = str(FIXTURES / "profiles" / "udp_for_benchmarks.py")


def connect(c, f, **kw):
    """POST /api/session/connect from client `c` to fake `f` (timeout_s 2; `kw` adds or overrides body
    fields); asserts a 200."""
    r = c.post(
        "/api/session/connect",
        json={"host": f.host, "sync_port": f.rpc_port, "async_port": f.pub_port, "timeout_s": 2, **kw},
    )
    assert r.status_code == 200, r.text


def test_full_cycle(tmp_path: Path, fake_trex):
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        assert [p["id"] for p in c.get("/api/ports").json()["ports"]] == [0, 1]
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        insp = c.post("/api/profiles/inspect", json={"path": PROFILE}).json()
        assert [t["dest"] for t in insp["tunables"]] == ["packet_len", "stream_count"]
        r = c.post(
            "/api/ports/assign", json={"ports": [0], "path": PROFILE, "tunables": ["--packet_len", "128"]}
        )
        assert r.status_code == 200 and r.json()["summary"][0]["length"] == 124
        assert r.json()["stream_count"] == 1 and list(r.json()["rate"]) == ["0"] and r.json()["rate"]["0"]
        assert c.post("/api/ports/start", json={"ports": [0], "mult": "10kpps"}).status_code == 200
        assert c.post("/api/ports/update", json={"ports": [0], "mult": "50%"}).status_code == 200
        assert c.post("/api/ports/stop", json={"ports": [0]}).status_code == 200
        assert c.post("/api/ports/release", json={"ports": [0]}).status_code == 200


def test_new_websocket_gets_current_ports(tmp_path: Path, fake_trex):
    """A reloaded tab (or a reconnected socket) joins a session that is already connected and idle."""
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        with c.websocket_connect(WS) as ws:
            assert recv(ws)["type"] == "hello"
            assert recv(ws)["state"] == "connected"
            m = recv(ws)
            assert m["type"] == "ports" and [p["id"] for p in m["ports"]] == [0, 1]


def test_command_on_stolen_port_is_409(tmp_path: Path, fake_trex, foreign_client):
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        foreign_client(f).acquire(ports=[0])
        r = c.post("/api/ports/start", json={"ports": [0], "mult": "1kpps"})
        assert r.status_code == 409 and r.json()["error"] == "not_owned"


def test_acquire_of_a_foreign_port_is_409_not_owned_unless_forced(tmp_path: Path, fake_trex, foreign_client):
    """R7: a non-force acquire of a port another client owns is refused before the RPC; force takes it."""
    f = fake_trex()
    with client(tmp_path) as c:
        foreign_client(f).acquire(ports=[0])
        connect(c, f)                       # the first sync sees the owner
        r = c.post("/api/ports/acquire", json={"ports": [0]})
        assert r.status_code == 409 and r.json()["error"] == "not_owned", r.text
        assert "Port 0 is owned by console-b; force-acquire to take it." in r.json()["message"]
        assert c.post("/api/ports/acquire", json={"ports": [1]}).status_code == 200      # nobody owns port 1
        assert c.post("/api/ports/acquire", json={"ports": [0], "force": True}).status_code == 200
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200      # ours now


def test_read_only_is_409(tmp_path: Path, fake_trex):
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f, read_only=True)
        r = c.post("/api/ports/acquire", json={"ports": [0]})
        assert r.status_code == 409 and r.json()["error"] == "read_only"


def test_bad_mult_and_profile_errors(tmp_path: Path, fake_trex):
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        assert c.post("/api/ports/start", json={"ports": [0], "mult": "fast"}).status_code == 422
        r = c.post("/api/profiles/inspect", json={"path": str(FIXTURES / "profiles" / "boom.py")})
        assert r.status_code == 422 and "boom-from-profile" in r.json()["message"]


def test_fs_list(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / ".hidden.py").write_text("")
    with client(tmp_path) as c:
        j = c.get("/api/fs/list", params={"path": str(tmp_path)}).json()
        assert [(e["name"], e["dir"]) for e in j["entries"]] == [("sub", True), ("a.py", False)]
        assert c.get("/api/fs/list", params={"path": str(tmp_path / "nope")}).status_code == 404


def assigned(c, f, ports=(0,), **kw):
    """connect(), acquire `ports`, then assign the udp_for_benchmarks profile to them; `kw` goes to
    connect(). Asserts each step returns 200."""
    connect(c, f, **kw)
    assert c.post("/api/ports/acquire", json={"ports": list(ports)}).status_code == 200
    r = c.post("/api/ports/assign", json={"ports": list(ports), "path": PROFILE})
    assert r.status_code == 200, r.text


def test_other_port_routes(tmp_path: Path, fake_trex):
    f = fake_trex()
    with client(tmp_path) as c:
        assigned(c, f)
        assert c.post("/api/ports/reacquire").json()["reacquired"] == []
        assert c.post("/api/ports/service", json={"ports": [0], "enabled": True}).status_code == 200
        assert c.post("/api/ports/service", json={"ports": [0], "enabled": False}).status_code == 200
        # mask defaults to 1
        r = c.post("/api/ports/service", json={"ports": [0], "enabled": False, "filtered": True})
        assert r.status_code == 200 and r.json()["ports"][0]["service_filtered"] is True
        r = c.post("/api/ports/service", json={"ports": [0], "enabled": False, "filtered": True, "mask": 255})
        assert r.status_code == 200
        r = c.post("/api/ports/service", json={"ports": [0], "enabled": False})
        assert r.status_code == 200 and r.json()["ports"][0]["service_filtered"] is False
        r = c.post("/api/ports/attr", json={"ports": [0], "promiscuous": True})
        assert r.status_code == 200 and r.json()["ports"]
        assert c.post("/api/ports/start", json={"ports": [0], "mult": "1kpps"}).status_code == 200
        assert c.post("/api/ports/pause", json={"ports": [0]}).status_code == 200
        assert c.post("/api/ports/resume", json={"ports": [0]}).status_code == 200
        r = c.get("/api/ports/0/rate")
        assert r.status_code == 200 and r.json()["rate"]
        assert c.post("/api/ports/stop", json={"ports": [0]}).status_code == 200
        assert c.post("/api/ports/reset", json={"ports": [0]}).status_code == 200
        assert c.post("/api/console", json={"enabled": True}).json() == {
            "enabled": True, "include_polling": False}
        assert c.get("/api/session").json()["rpc_console"] is True
        assert c.post("/api/console", json={"enabled": True, "include_polling": True}).json() == {
            "enabled": True, "include_polling": True}
        assert c.post("/api/console", json={"enabled": False}).json() == {
            "enabled": False, "include_polling": True}
        assert c.get("/api/session").json()["include_polling"] is True


def test_server_refusal_is_400_trex_error(tmp_path: Path, fake_trex):
    """A port with no streams: the server refuses the start, and that is not a trex-web-gui refusal."""
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        r = c.post("/api/ports/start", json={"ports": [0], "mult": "1kpps"})
        assert r.status_code == 400 and r.json()["error"] == "trex_error", r.text
        # the vendored client refuses before any RPC
        # (trex_stl_port.py: "profile [%s] does not exist in the port [%s]")
        assert "profile [_] does not exist in the port [0]" in r.json()["message"], r.text


def test_restarted_server_is_502_unreachable(tmp_path: Path, fake_trex):
    f = fake_trex()
    with client(tmp_path) as c:
        assigned(c, f)
        # applied by the fake before the next request: api_h no longer matches
        f.restart()
        r = c.post("/api/ports/start", json={"ports": [0], "mult": "1kpps"})
        assert r.status_code == 502 and r.json()["error"] == "unreachable", r.text
        assert "restarted" in r.json()["message"], r.text


@pytest.mark.parametrize("path, body", [
    ("/api/ports/start", {"ports": [0], "mult": "fast"}),
    ("/api/ports/start", {"ports": [0], "mult": "1kpps", "duration": 0}),
    ("/api/ports/start", {"ports": [0], "mult": "1kpps", "duration": 604801}),
    ("/api/ports/stop", {"ports": [0, 0]}),
    ("/api/ports/stop", {"ports": [-1]}),
    ("/api/profiles/inspect", {"path": PROFILE, "ports": [1, 1]}),
    ("/api/ports/attr", {"ports": [0], "flow_ctrl": 9}),
    ("/api/ports/service", {"ports": [0], "enabled": True, "filtered": True}),
    ("/api/ports/service", {"ports": [0], "enabled": False, "mask": 1}),
    ("/api/ports/service", {"ports": [0], "enabled": False, "filtered": True, "mask": 0}),
    ("/api/ports/service", {"ports": [0], "enabled": False, "filtered": True, "mask": 256}),
])
def test_validation_errors_use_the_error_shape(tmp_path: Path, path, body):
    with client(tmp_path) as c:
        r = c.post(path, json=body)
        assert r.status_code == 422 and r.json()["error"] == "bad_request" and r.json()["message"], r.text
        assert set(r.json()) == {"error", "message"}


def test_unexpected_exception_is_500_internal_and_reraised_for_the_server_to_log(
    tmp_path: Path, monkeypatch, caplog
):
    """The handler adds no log record of its own: ServerErrorMiddleware re-raises after sending the 500, and
    uvicorn logs that traceback (measured: with an extra log.error in the handler a real uvicorn printed
    two)."""
    async def boom(*a, **k):
        raise OSError("cannot spawn")
    monkeypatch.setattr(trexapi, "load_profile", boom)
    with authed(create_app(tmp_path), raise_server_exceptions=False) as c:
        r = c.post("/api/profiles/inspect", json={"path": PROFILE})
    assert r.status_code == 500 and r.json()["error"] == "internal" and "cannot spawn" in r.json()["message"]
    assert not [rec for rec in caplog.records if rec.exc_info], (
        "the handler must not log the traceback a second time"
    )
    with authed(create_app(tmp_path)) as c, pytest.raises(OSError, match="cannot spawn"):
        c.post("/api/profiles/inspect", json={"path": PROFILE})        # re-raised: the server logs it


def test_assign_is_refused_before_the_profile_runs(tmp_path: Path, fake_trex, monkeypatch):
    """Not connected / read-only: refuse first; the profile subprocess must not start."""
    async def must_not_run(*a, **k):
        raise AssertionError("load_profile ran")
    monkeypatch.setattr(trexapi, "load_profile", must_not_run)
    f = fake_trex()
    with client(tmp_path) as c:
        r = c.post("/api/ports/assign", json={"ports": [0], "path": PROFILE})
        assert r.status_code == 409 and r.json()["error"] == "not_connected"
        connect(c, f, read_only=True)
        r = c.post("/api/ports/assign", json={"ports": [0], "path": PROFILE})
        assert r.status_code == 409 and r.json()["error"] == "read_only"


def test_assign_to_an_unowned_port_is_refused_before_the_profile_runs(tmp_path: Path, fake_trex, monkeypatch):
    spawned = []

    async def counting(*a, **k):
        spawned.append(a)
        raise AssertionError("load_profile ran")
    monkeypatch.setattr(trexapi, "load_profile", counting)
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        r = c.post("/api/ports/assign", json={"ports": [0], "path": PROFILE})
        assert r.status_code == 409 and r.json()["error"] == "not_owned", r.text
    assert spawned == []


def test_assign_to_a_transmitting_port_is_refused_before_the_profile_runs(
    tmp_path: Path, fake_trex, monkeypatch
):
    f = fake_trex()
    with client(tmp_path) as c:
        assigned(c, f)
        assert c.post("/api/ports/start", json={"ports": [0], "mult": "1kpps"}).status_code == 200

        async def must_not_run(*a, **k):
            raise AssertionError("load_profile ran")
        monkeypatch.setattr(trexapi, "load_profile", must_not_run)
        r = c.post("/api/ports/assign", json={"ports": [0], "path": PROFILE})
        assert r.status_code == 409 and r.json()["error"] == "port_active", r.text


def test_fs_list_keeps_a_broken_symlink_and_caps_the_listing(tmp_path: Path, monkeypatch):
    (tmp_path / "ok.py").write_text("x")
    (tmp_path / "dangling.py").symlink_to(tmp_path / "missing-target")
    with client(tmp_path) as c:
        j = c.get("/api/fs/list", params={"path": str(tmp_path)}).json()
        assert {(e["name"], e["size"]) for e in j["entries"]} == {("dangling.py", None), ("ok.py", 1)}
        assert j["truncated"] is False
        monkeypatch.setattr(app_module, "FS_LIST_MAX", 1)
        j = c.get("/api/fs/list", params={"path": str(tmp_path)}).json()
        assert len(j["entries"]) == 1 and j["truncated"] is True
