"""/api/daemon/* (spec §6.4, D1-D3, D5, D13) over DaemonSession and tests/fake/fake_daemon.py."""
from pathlib import Path
from typing import Any

import pytest
from support import TEST_TOKEN, authed, free_port
from trex_web_gui.api.app import create_app

CFG = "/tmp/trex_files/a.yaml"
STATE_CHANGING = {"push_file", "start_trex", "stop_trex", "force_trex_kill"}
SFR2 = {"profile": "cap2/sfr2.yaml", "d": 20, "m": 1.0, "l": 1000, "rx_check": 100}


def connect(c: Any, d: Any) -> dict:
    """POST /api/daemon/connect to the fake; the reply, after asserting 200."""
    r = c.post("/api/daemon/connect", json={"host": d.host, "port": d.port})
    assert r.status_code == 200, r.text
    return r.json()


def changed(d: Any) -> set[str]:
    """The state-changing methods the fake daemon has received."""
    return set(d.methods()) & STATE_CHANGING


# The session's status poll (daemon/session.py _poll). One can land between a test's snapshot and its
# request: connect's own poll finishing late, or the periodic one, under load (seen with 12 pytest workers,
# 2026-10-28).
STATUS_READS = {"is_running", "get_running_status", "is_reserved", "get_trex_cmds"}


def only_status_reads_since(d: Any, before: list[str]) -> bool:
    """True when every call the fake daemon received after `before` was a status read: a refused request
    reached nothing else."""
    now = d.methods()
    return now[:len(before)] == before and set(now[len(before):]) <= STATUS_READS


def test_connect_and_get(tmp_path: Path, fake_daemon: Any) -> None:
    d = fake_daemon()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        assert c.get("/api/daemon").json()["state"] == "disconnected"
        reply = connect(c, d)
        assert reply["version"].startswith("Version : v3.08") and reply["files_path"] == "/tmp/trex_files"
        got = c.get("/api/daemon").json()
        assert got["type"] == "daemon" and got["state"] == "connected"
        assert got["target"] == {"host": d.host, "port": d.port}
        pre = c.post("/api/daemon/config/preview", json={"text": reply["config_yaml"]}).json()
        assert pre["errors"] == [] and pre["values"]
        assert c.post("/api/daemon/config/preview", json={"values": pre["values"]}).json()["errors"] == []
        pushed = c.post("/api/daemon/config/push",
                        json={"yaml": reply["config_yaml"], "filename": "mine.yaml", "confirm": True})
        assert pushed.status_code == 200 and pushed.json() == {"path": "/tmp/trex_files/mine.yaml"}
        assert c.get("/api/daemon/log", params={"which": "daemon"}).status_code == 200
        assert c.get("/api/daemon/log", params={"which": "trex"}).status_code == 200
        assert c.post("/api/daemon/disconnect").status_code == 200
        assert c.get("/api/daemon").json()["state"] == "disconnected"


def test_config_preview_takes_exactly_one(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        for body in ({}, {"values": {}, "text": ""}):
            r = c.post("/api/daemon/config/preview", json=body)
            assert r.status_code == 422 and r.json()["error"] == "bad_request", body


def test_refusals_map_to_409_with_code(tmp_path: Path, fake_daemon: Any) -> None:
    d, foreign = fake_daemon(), fake_daemon("--foreign")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        start = {"cfg_path": CFG, "options": {}, "trex_args": "", "timeout": 20}
        dead = free_port()      # nothing listens there, so the owners read fails (TRex on 4501 answers)
        r = c.post("/api/daemon/start", json={**start, "confirm": True})
        assert r.status_code == 409 and r.json()["error"] == "not_connected"
        connect(c, d)
        r = c.post("/api/daemon/start", json=start)
        assert r.status_code == 409 and r.json()["error"] == "confirm_required"
        r = c.post("/api/daemon/stop", json={"rpc_port": dead, "confirm": False})
        assert r.status_code == 409 and r.json()["error"] == "confirm_required"
        r = c.post("/api/daemon/stop", json={"rpc_port": dead, "confirm": True})
        assert r.status_code == 409 and r.json()["error"] == "owners_unknown"
        assert changed(d) == set()
        connect(c, foreign)
        r = c.post("/api/daemon/start", json={**start, "confirm": True})
        assert r.status_code == 409 and r.json()["error"] == "foreign_trex" and "2662" in r.json()["message"]
        assert changed(foreign) == set()


def test_stop_passes_owners_unknown_ack(tmp_path: Path, fake_daemon: Any, monkeypatch: Any) -> None:
    from trex_web_gui.daemon.session import DaemonSession
    seen: list[tuple] = []

    async def fake_stop(self: Any, rpc_port: int, confirm: bool, force: bool = False,
                        owners_unknown_ack: bool = False) -> dict:
        """Record the arguments the route passed."""
        seen.append((rpc_port, confirm, force, owners_unknown_ack))
        return {"running": False, "stop_s": 0.1}
    monkeypatch.setattr(DaemonSession, "stop", fake_stop)
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        assert c.post("/api/daemon/stop", json={"rpc_port": 4501, "confirm": True}).json() == \
            {"running": False, "stop_s": 0.1}
        c.post("/api/daemon/stop", json={"rpc_port": 4501, "confirm": True, "force": True,
                                         "owners_unknown_ack": True})
        assert seen == [(4501, True, False, False), (4501, True, True, True)]
        for bad in ({"owners_unknown_ack": "yes"}, {"owners_unknown_ack": 1}, {"force": "true"},
                    {"confirm": 1}, {"rpc_port": 0}, {"rpc_port": 65536}):
            r = c.post("/api/daemon/stop", json={"rpc_port": 4501, "confirm": True, **bad})
            assert r.status_code == 422, bad
        assert len(seen) == 2


def test_unreachable_is_502(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post("/api/daemon/connect", json={"host": "127.0.0.1", "port": free_port()})
        assert r.status_code == 502 and r.json()["error"] == "daemon_unreachable"
        assert c.get("/api/daemon").json()["state"] != "connected"


def test_fault_is_502_daemon_fault(tmp_path: Path, fake_daemon: Any) -> None:
    d = fake_daemon("--start-fails")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, d)
        r = c.post("/api/daemon/start", json={"cfg_path": CFG, "options": {}, "trex_args": "",
                                              "timeout": 20, "confirm": True})
        assert r.status_code == 502 and r.json()["error"] == "daemon_fault"
        assert r.json()["message"]


def test_start_preview_route(tmp_path: Path, fake_daemon: Any) -> None:
    d = fake_daemon()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post("/api/daemon/start/preview",
                   json={"cfg_path": CFG, "options": {"c": 2}, "trex_args": "--no-scapy-server"})
        assert r.status_code == 200
        assert r.json() == {"command_preview": "nice -n -19 ./t-rex-64 --iom 0 --cfg /tmp/trex_files/a.yaml "
                                               "-c 2 -i --no-scapy-server --no-key"}
        connect(c, d)
        out = c.post("/api/daemon/start", json={"cfg_path": CFG, "options": {"c": 2},
                                                "trex_args": "--no-scapy-server", "confirm": True})
        assert out.status_code == 200, out.text
        j = out.json()
        assert j["command_preview"] == r.json()["command_preview"] and j["run"]["mode"] == "stl"
        assert isinstance(j["seq"], int)
        assert c.get("/api/daemon").json()["running_seq"] == j["seq"]


def test_bodies_are_validated(tmp_path: Path, fake_daemon: Any) -> None:
    d = fake_daemon()
    base = {"cfg_path": CFG, "options": {}, "trex_args": "", "timeout": 20, "confirm": True}
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, d)
        for patch in ({"timeout": 4}, {"timeout": 301}, {"trex_args": "-i"}, {"trex_args": "--cf"},
                      {"trex_args": "a;b"}, {"cfg_path": "a/../b"}, {"cfg_path": "rel.yaml"},
                      {"options": {"x": 1}}, {"options": {"c": 65}}, {"mode": "bogus"}):
            assert c.post("/api/daemon/start", json={**base, **patch}).status_code == 422, patch
            if "timeout" not in patch:          # the preview takes no timeout
                assert c.post("/api/daemon/start/preview", json={
                    k: v for k, v in {**base, **patch}.items() if k not in ("timeout", "confirm")}
                ).status_code == 422, patch
        yaml = "a: 1"
        for name in ("../x.yaml", "tg-0123456789ab-x.yaml", "x.txt", "a" * 70 + ".yaml"):
            r = c.post("/api/daemon/config/push", json={"yaml": yaml, "filename": name, "confirm": True})
            assert r.status_code == 422, name
        assert c.post("/api/daemon/connect", json={"host": "a b", "port": 8090}).status_code == 422
        assert c.post("/api/daemon/connect", json={"host": "h", "port": 0}).status_code == 422
        assert c.post("/api/daemon/connect", json={"host": "h", "port": 65536}).status_code == 422
        assert c.get("/api/daemon/log", params={"which": "x"}).status_code == 422
        assert changed(d) == set()


@pytest.mark.parametrize("patch", [
    {"mode": "stf"},                                                    # no stf
    {"mode": "stl", "stf": SFR2},                                       # stf beside stl
    {"mode": "stf", "stf": SFR2, "trex_args": "--no-scapy-server"},
    {"mode": "stf", "stf": {**SFR2, "d": 9}}, {"mode": "stf", "stf": {**SFR2, "d": 3601}},
    {"mode": "stf", "stf": {**SFR2, "d": 20.5}}, {"mode": "stf", "stf": {**SFR2, "d": True}},
    {"mode": "stf", "stf": {**SFR2, "m": 0}}, {"mode": "stf", "stf": {**SFR2, "m": 1000.5}},
    {"mode": "stf", "stf": {**SFR2, "l": 100001}}, {"mode": "stf", "stf": {**SFR2, "l": -1}},
    {"mode": "stf", "stf": {**SFR2, "rx_check": 65536}},
    {"mode": "stf", "stf": {**SFR2, "profile": "cap2/" + "a" * 300 + ".yaml"}},
    {"mode": "stf", "stf": {**SFR2, "profile": 5}},
])
def test_stf_bodies_are_validated(tmp_path: Path, fake_daemon: Any, patch: dict) -> None:
    d = fake_daemon("--stf-scale", "0.05")
    body = {"cfg_path": CFG, "options": {}, "trex_args": "", "timeout": 20, "confirm": True, **patch}
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, d)
        before = list(d.methods())
        assert c.post("/api/daemon/start", json=body).status_code == 422
        pv = {k: v for k, v in body.items() if k not in ("timeout", "confirm")}
        assert c.post("/api/daemon/start/preview", json=pv).status_code == 422
        assert only_status_reads_since(d, before) and changed(d) == set()


def test_stf_start_preview_route(tmp_path: Path, fake_daemon: Any) -> None:
    d = fake_daemon("--stf-scale", "0.05")
    want = ("nice -n -19 ./t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -f cap2/sfr2.yaml -d 20 -m 1.0 "
            "-l 1000 --rx-check 100 -c 1 --no-key")
    body = {"cfg_path": "/etc/trex_cfg.yaml", "options": {"c": 1}, "trex_args": "", "mode": "stf",
            "stf": SFR2}
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post("/api/daemon/start/preview", json=body)
        assert r.status_code == 200 and r.json() == {"command_preview": want}
        connect(c, d)
        out = c.post("/api/daemon/start", json={**body, "timeout": 20, "confirm": True})
        assert out.status_code == 200, out.text
        assert out.json()["command_preview"] == want and out.json()["run"]["mode"] == "stf"
        assert out.json()["run"]["profile"] == "cap2/sfr2.yaml"


def test_stf_profiles_route(tmp_path: Path, fake_daemon: Any) -> None:
    d = fake_daemon()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        stock_q = {"source": "stock"}
        assert c.get("/api/daemon/stf/profiles", params=stock_q).json()["error"] == "not_connected"
        connect(c, d)
        stock = c.get("/api/daemon/stf/profiles", params=stock_q).json()
        assert stock["source"] == "stock" and "cap2/sfr2.yaml" in stock["files"]
        up = c.get("/api/daemon/stf/profiles", params={"source": "uploaded"})
        assert up.status_code == 200 and up.json() == {"source": "uploaded", "files": []}
        assert c.get("/api/daemon/stf/profiles", params={"source": "bogus"}).status_code == 422
        assert c.get("/api/daemon/stf/profiles").status_code == 422
        r = c.post("/api/daemon/stf/profile", json={"path": "cap2/sfr2.yaml"})
        assert r.status_code == 200
        assert r.json()["templates"] == ["cap2/Oracle.pcap", "cap2/smtp.pcap", "cap2/dns.pcap"]
        assert r.json()["errors"] == []
        r = c.post("/api/daemon/stf/profile", json={"path": "/etc/x.yaml"})
        assert r.status_code == 409 and r.json()["error"] == "invalid_profile"
        gone = c.post("/api/daemon/stf/profile", json={"path": "cap2/missing.yaml"})
        assert gone.status_code == 200 and len(gone.json()["errors"]) == 1


def test_ws_sends_the_daemon_snapshot(tmp_path: Path, fake_daemon: Any) -> None:
    from test_api import WS, recv
    d = fake_daemon()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, d)
        with c.websocket_connect(WS) as ws:
            kinds = []
            for _ in range(8):
                m = recv(ws)
                kinds.append(m["type"])
                if m["type"] == "daemon":
                    assert m["state"] == "connected" and m["target"]["port"] == d.port
            assert kinds[-1] == "daemon" and kinds.count("daemon") == 1


def test_session_owned_follows_the_control_session(tmp_path: Path, fake_trex: Any) -> None:
    """Stop's port-owner check sees the ports this backend's own TrexSession owns, and only for the same
    host and command port."""
    f = fake_trex()
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        assert app.state.session_owned(f.host, f.rpc_port) is None
        r = c.post("/api/session/connect", json={"host": f.host, "sync_port": f.rpc_port,
                                                  "async_port": f.pub_port, "timeout_s": 2})
        assert r.status_code == 200, r.text
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        assert app.state.session_owned(f.host, f.rpc_port) == {0}
        assert app.state.session_owned(f.host, f.rpc_port + 1) is None
        assert app.state.session_owned("localhost", f.rpc_port) is None


def test_hello_carries_the_daemon_hint(tmp_path: Path) -> None:
    from test_api import WS, recv
    for kw, want in (({}, {"host": "127.0.0.1", "port": 8090}),
                     ({"server_hint": "lab1", "daemon_port": 8190}, {"host": "lab1", "port": 8190})):
        with authed(create_app(tmp_path, token=TEST_TOKEN, **kw)) as c, c.websocket_connect(WS) as ws:
            assert recv(ws)["daemon_hint"] == want


WANT = "tg-0123456789ab-p.yaml"


@pytest.mark.parametrize("expected", [None, "", "p.yaml", "../tg-0123456789ab-p.yaml",
                                      "tg-0123456789ab-p.pcap", 5])
def test_upload_needs_a_well_formed_expected_yaml_remote(tmp_path: Path, fake_daemon: Any,
                                                         expected: Any) -> None:
    """expected_yaml_remote is required and must be a tg-*.yaml name: otherwise 422 and no daemon call but
    the session's own status reads."""
    d = fake_daemon()
    body: dict = {"path": "/tmp/x.yaml", "confirm": True, "expected_target": "127.0.0.1:1"}
    if expected is not None:
        body["expected_yaml_remote"] = expected
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, d)
        before = list(d.methods())
        assert c.post("/api/daemon/stf/upload", json=body).status_code == 422
        assert only_status_reads_since(d, before)


@pytest.mark.parametrize("route", ["/api/daemon/stf/upload/preview", "/api/daemon/stf/upload"])
@pytest.mark.parametrize("body", [
    {"path": "relative.yaml"}, {"path": ""}, {"path": "/" + "a" * 4096}, {"path": 5}, {},
    {"path": "/tmp/x.yaml", "extra": 1},
])
def test_upload_bodies_are_validated(tmp_path: Path, fake_daemon: Any, route: str, body: dict) -> None:
    """A relative path, a 4097-character path, a non-string, a missing path and an extra key are 422, and
    the daemon sees no call but the session's own status reads."""
    d = fake_daemon()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, d)
        before = list(d.methods())
        full = {**body, "confirm": True, "expected_yaml_remote": WANT, "expected_target": "127.0.0.1:1"}
        r = c.post(route, json=full if route.endswith("upload") else body)
        assert r.status_code == 422, r.text
        assert only_status_reads_since(d, before)


def test_upload_routes(tmp_path: Path, fake_daemon: Any) -> None:
    """Preview, upload without confirm (409, no push), upload with confirm, a refused plan (409), a repeat
    (everything skipped)."""
    d = fake_daemon()
    pcap = tmp_path / "a.pcap"
    pcap.write_bytes(bytes.fromhex("d4c3b2a1") + b"abc")
    y = tmp_path / "p.yaml"
    y.write_text("- duration: 10\n  cap_info:\n    - {name: a.pcap, cps: 1, ipg: 1, rtt: 1, w: 1}\n")
    with authed(create_app(tmp_path / "state", token=TEST_TOKEN)) as c:
        r = c.post("/api/daemon/stf/upload/preview", json={"path": str(y)})
        assert r.json()["error"] == "not_connected"
        connect(c, d)
        prev = c.post("/api/daemon/stf/upload/preview", json={"path": str(y)})
        assert prev.status_code == 200 and prev.json()["files"][0]["exists"] is False
        want = prev.json()["yaml_remote"]
        tgt = f"{d.host}:{d.port}"
        r = c.post("/api/daemon/stf/upload",
                   json={"path": str(y), "expected_yaml_remote": want, "expected_target": tgt})
        assert r.status_code == 409 and r.json()["error"] == "confirm_required"
        assert changed(d) == set()
        go = {"path": str(y), "confirm": True, "expected_yaml_remote": want, "expected_target": tgt}
        other = c.post("/api/daemon/stf/upload", json={**go, "expected_target": f"{d.host}:{d.port + 1}"})
        assert other.status_code == 409 and other.json()["error"] == "plan_changed"
        assert c.post("/api/daemon/stf/upload", json={k: v for k, v in go.items() if k != "expected_target"}
                      ).status_code == 422
        assert changed(d) == set()
        stale = c.post("/api/daemon/stf/upload", json={**go, "expected_yaml_remote": WANT})
        assert stale.status_code == 409 and stale.json()["error"] == "plan_changed"
        assert changed(d) == set()
        out = c.post("/api/daemon/stf/upload", json=go)
        assert out.status_code == 200, out.text
        assert out.json()["profile"] == prev.json()["profile"] and len(out.json()["pushed"]) == 2
        again = c.post("/api/daemon/stf/upload", json=go).json()
        assert again["pushed"] == [] and len(again["skipped"]) == 2
        bad = c.post("/api/daemon/stf/upload/preview", json={"path": str(tmp_path / "nope.yaml")})
        assert bad.status_code == 409 and bad.json()["error"] == "invalid_profile"
