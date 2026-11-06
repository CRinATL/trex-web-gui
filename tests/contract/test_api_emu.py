"""/api/emu: the snapshot, connect and disconnect, the namespaces, clients and counters reads, and the profile
inspect, load and remove writes, over EmuSession and tests/fake/fake_emu.py. The `no_namespace` and
`no_client` refusals come from the error path only (ruling R19)."""
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES, TEST_TOKEN, authed
from trex_web_gui.api.app import create_app
from trex_web_gui.sessions import EmuRefused
from trex_web_gui.trexapi import ServerUnreachable, TrexApiError

Factory = Callable[..., Any]
MAC0 = "00:00:00:00:00:00"
PROFILE = str(FIXTURES / "profiles" / "emu_two_ns.py")
LOAD = "/api/emu/profile/load"
REMOVE = "/api/emu/profile/remove"
WRITES = {"ctx_set_def_plugins", "ctx_add", "ctx_client_set_def_plugins", "ctx_client_add",
          "ctx_client_remove", "ctx_remove"}


def connect(c: Any, f: Any) -> dict:
    """POST /api/emu/connect to the fake; the reply, after asserting 200."""
    r = c.post("/api/emu/connect", json={"host": f.host, "port": f.port, "timeout_s": 2})
    assert r.status_code == 200, r.text
    return r.json()


def served(f: Any, name: str) -> list[dict[str, Any]]:
    """The fake's logged requests of one method."""
    return [r for r in f.requests() if r["method"] == name]


def test_snapshot_connect_and_disconnect(tmp_path: Path, fake_emu: Factory) -> None:
    f = fake_emu()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        assert c.get("/api/emu").json()["state"] == "disconnected"
        snap = connect(c, f)
        assert snap["type"] == "emu" and snap["state"] == "connected"
        assert snap["target"] == {"host": f.host, "port": f.port} and snap["info"]["version"] == "v0.1"
        assert c.get("/api/emu").json() == snap
        gone = c.post("/api/emu/disconnect")
        assert gone.status_code == 200 and gone.json()["state"] == "disconnected"


def test_connect_validates_and_maps_errors(tmp_path: Path, fake_emu: Factory) -> None:
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        bad = c.post("/api/emu/connect", json={"host": "a b", "port": 4510})
        assert bad.status_code == 422 and bad.json()["error"] == "bad_request"
        assert c.post("/api/emu/connect", json={"host": "127.0.0.1", "port": 0}).status_code == 422
        # nothing listens on port 1: unreachable, 502
        down = c.post("/api/emu/connect", json={"host": "127.0.0.1", "port": 1, "timeout_s": 0.5})
        assert down.status_code == 502 and down.json()["error"] == "unreachable"
        assert c.get("/api/emu").json()["state"] == "disconnected"


def test_reads_refuse_when_not_connected(tmp_path: Path) -> None:
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        for path, params in (("/api/emu/namespaces", {}), ("/api/emu/clients", {"vport": 0}),
                             ("/api/emu/counters", {"scope": "ctx"})):
            r = c.get(path, params=params)
            assert r.status_code == 409 and r.json()["error"] == "not_connected", path
            assert "EMU" in r.json()["message"]


def test_namespaces_body_has_no_truncated_field(tmp_path: Path, fake_emu: Factory) -> None:
    f = fake_emu("--foreign-ns", "2", "--clients", "3")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        r = c.get("/api/emu/namespaces")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"generation", "namespaces", "interrupted"}      # ruling R18
        assert body["interrupted"] is False and body["generation"] == 0
        assert body["namespaces"][1] == {"vport": 1, "tci": [0, 0], "tpid": [0, 0], "active_clients": 3,
                                         "plug_names": ["arp", "icmp"], "ours": False}


def test_clients_pages_and_validates(tmp_path: Path, fake_emu: Factory) -> None:
    f = fake_emu("--foreign-ns", "1", "--clients", "7")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        first = c.get("/api/emu/clients", params={"vport": 0, "limit": 5}).json()
        assert first["total"] == 7 and len(first["clients"]) == 5 and first["interrupted"] is False
        assert first["clients"][0]["mac"] == [0, 0, 0, 0, 0, 0]
        rest = c.get("/api/emu/clients", params={"vport": 0, "tci": "0,0", "tpid": "0,0", "offset": 5,
                                                 "limit": 5}).json()
        assert [x["mac"][5] for x in rest["clients"]] == [5, 6]
        for params in ({"vport": 0, "limit": 256}, {"vport": 0, "limit": 0}, {"vport": 0, "offset": -1},
                       {"vport": -1}, {"vport": 0, "tci": "1"}, {"vport": 0, "tpid": "a,b"},
                       {"vport": 0, "tci": "0,70000"}, {}):
            r = c.get("/api/emu/clients", params=params)
            assert r.status_code == 422 and r.json()["error"] == "bad_request", params


def test_counters_by_scope(tmp_path: Path, fake_emu: Factory) -> None:
    f = fake_emu("--foreign-ns", "1", "--clients", "2", "--plugins", "arp,icmp,mdns")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        ctx = c.get("/api/emu/counters", params={"scope": "ctx"}).json()
        assert ctx["unsupported"] == [] and ctx["tables"]
        row = next(iter(ctx["tables"].values()))[0]
        assert set(row) == {"name", "value", "unit", "help", "info", "zero"}
        ns = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "mdns"}).json()
        assert ns["tables"] and ns["unsupported"] == []
        cl = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": MAC0,
                                                "plugin": "mdns"}).json()
        assert cl["tables"] and cl["unsupported"] == []
        described = c.get("/api/emu/counters", params={"scope": "ctx", "meta": "true"}).json()
        assert all(r["value"] is None for rows in described["tables"].values() for r in rows)


def test_counters_without_plugin_lists_what_the_server_lacks(tmp_path: Path, fake_emu: Factory) -> None:
    """Plugins whose counters the server lacks are in `unsupported`; naming one is 409 unsupported; a plugin
    with no counter method in the scope at all (dhcp in a namespace, ruling R17) is not listed. ipfix is
    loaded in the namespace: only loaded plugins are asked."""
    f = fake_emu("--foreign-ns", "1", "--clients", "1", "--plugins", "arp,icmp,ipfix,dhcp")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        every = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0}).json()
        assert "ipfix" in every["unsupported"] and "dhcp" not in every["unsupported"]
        named = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "ipfix"})
        assert named.status_code == 409 and named.json()["error"] == "unsupported"


def test_counters_check_their_keys(tmp_path: Path, fake_emu: Factory) -> None:
    f = fake_emu("--foreign-ns", "1", "--clients", "1")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        for params in ({"scope": "ns"}, {"scope": "client", "vport": 0}, {"scope": "ctx", "vport": 0},
                       {"scope": "ns", "vport": 0, "mac": MAC0}, {"scope": "ns", "tci": "1,1"},
                       {"scope": "ctx", "plugin": "nope"}, {"scope": "other"},
                       {"scope": "client", "vport": 0, "mac": "0:0:0"}):
            r = c.get("/api/emu/counters", params=params)
            assert r.status_code == 422 and r.json()["error"] == "bad_request", params
    assert not served(f, "ctx_cnt")


def test_a_client_without_a_transport_context_is_unsupported_not_a_failed_poll(tmp_path: Path,
                                                                              fake_emu: Factory) -> None:
    """transport_client_cnt answers -32600 'client does not have a transport context' (counters.json) for a
    client with the plugin loaded but no transport context. With plugin omitted that plugin is listed in
    `unsupported` and the others' tables still come back; named, it is 409 unsupported."""
    f = fake_emu("--foreign-ns", "1", "--clients", "1", "--plugins", "arp,mdns,transport")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        r = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": MAC0})
        assert r.status_code == 200, r.text
        assert r.json()["unsupported"] == ["transport"] and r.json()["tables"]
        named = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": MAC0,
                                                   "plugin": "transport"})
        assert named.status_code == 409 and named.json()["error"] == "unsupported", named.text


def test_unknown_namespace_is_409_no_namespace(tmp_path: Path, fake_emu: Factory) -> None:
    """A clients or counters read of a namespace the server does not list: its error, then a re-listing that
    lacks the key, is 409 no_namespace (ruling R19)."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2", "--plugins", "arp,icmp,mdns")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        for path, params in (("/api/emu/clients", {"vport": 9}),
                             ("/api/emu/counters", {"scope": "ns", "vport": 9, "plugin": "arp"}),
                             ("/api/emu/counters", {"scope": "client", "vport": 9, "mac": MAC0,
                                                    "plugin": "mdns"}),
                             # plugin omitted (the page's poll): the loaded-plugins read is what fails
                             ("/api/emu/counters", {"scope": "ns", "vport": 9}),
                             ("/api/emu/counters", {"scope": "client", "vport": 9, "mac": MAC0})):
            r = c.get(path, params=params)
            assert r.status_code == 409 and r.json()["error"] == "no_namespace", (path, r.text)
        # a read that succeeds lists nothing: the re-listing belongs to the error path
        before = len(served(f, "ctx_iter"))
        assert before > 0
        assert c.get("/api/emu/clients", params={"vport": 0}).status_code == 200
        assert len(served(f, "ctx_iter")) == before


def test_known_namespace_unknown_mac_is_409_no_client(tmp_path: Path, fake_emu: Factory) -> None:
    f = fake_emu("--foreign-ns", "1", "--clients", "2", "--plugins", "arp,icmp,mdns")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        for plugin in ({"plugin": "mdns"}, {}):          # named, and omitted (the loaded-plugins read fails)
            r = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": "00:00:00:00:99:99",
                                                   **plugin})
            assert r.status_code == 409 and r.json()["error"] == "no_client", (plugin, r.text)
            assert "00:00:00:00:99:99" in r.json()["message"]


def test_a_read_error_with_the_key_present_is_400(tmp_path: Path, fake_emu: Factory,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The server answers an error although the namespace and the client are listed: the error passes
    through as 400 emu_error, not as a missing key; the server's message text decides nothing."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2")
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        connect(c, f)
        session = app.state.emu

        async def broken(*_a: Any, **_k: Any) -> Any:
            """A read the server answers with an error whose text says a tunnel was not found."""
            raise TrexApiError(" tunnel not found ")
        monkeypatch.setattr(session, "clients", broken)
        r = c.get("/api/emu/clients", params={"vport": 0})
        assert r.status_code == 400 and r.json()["error"] == "emu_error", r.text
        monkeypatch.setattr(session, "counters", broken)
        r = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "arp"})
        assert r.status_code == 400 and r.json()["error"] == "emu_error", r.text
        # a client scope, the real listings: the client is listed, so the error stands
        monkeypatch.undo()
        monkeypatch.setattr(session, "counters", broken)
        r = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": MAC0, "plugin": "mdns"})
        assert r.status_code == 400 and r.json()["error"] == "emu_error", r.text


def test_a_lost_connection_is_not_asked_about_keys(tmp_path: Path, fake_emu: Factory) -> None:
    """A restarted server answers the stale api_h; the read is 502 unreachable and nothing re-lists."""
    f = fake_emu("--foreign-ns", "1", "--clients", "1")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        f.restart()
        time.sleep(0.3)         # the signal is asynchronous; the fake applies it before its next message
        r = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "arp"})
        assert r.status_code == 502 and r.json()["error"] == "unreachable", r.text
        assert not served(f, "ctx_iter")


def test_no_client_costs_one_bounded_walk_and_no_client_infos(tmp_path: Path, fake_emu: Factory) -> None:
    """Proving a MAC absent from a namespace of 600 clients is one pass of its cursor (255 per page) and asks
    for no client info."""
    f = fake_emu("--foreign-ns", "1", "--clients", "600", "--plugins", "arp,icmp,mdns")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        before = len(served(f, "ctx_client_iter"))
        r = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": "00:00:00:00:99:99",
                                               "plugin": "mdns"})
        assert r.status_code == 409 and r.json()["error"] == "no_client", r.text
        pages = len(served(f, "ctx_client_iter")) - before
        assert 3 <= pages <= 4, pages           # ceil(600 / 255) pages, plus the one that finds the end
        assert not served(f, "ctx_client_get_info")


def test_an_interrupted_walk_never_answers_no_client(tmp_path: Path, fake_emu: Factory) -> None:
    """When the server keeps interrupting the MAC walk, an absent MAC proves nothing: the read's own error
    passes through (400 emu_error)."""
    f = fake_emu("--foreign-ns", "1", "--clients", "300", "--plugins", "arp,icmp,mdns",
                 "--interrupt-iter", "1")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        r = c.get("/api/emu/counters", params={"scope": "client", "vport": 0, "mac": "00:00:00:00:99:99",
                                               "plugin": "mdns"})
        assert r.status_code == 400 and r.json()["error"] == "emu_error", r.text


def test_a_failing_relisting_leaves_the_reads_own_error(tmp_path: Path, fake_emu: Factory,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The re-listing raises too: the message shown is the read's, not the re-listing's."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2")
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        connect(c, f)

        async def read_fails(*_a: Any, **_k: Any) -> Any:
            """The read's error."""
            raise TrexApiError("the read failed")

        async def listing_fails(*_a: Any, **_k: Any) -> Any:
            """The re-listing's error."""
            raise TrexApiError("the listing failed")
        monkeypatch.setattr(app.state.emu, "counters", read_fails)
        monkeypatch.setattr(app.state.emu, "namespaces", listing_fails)
        r = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "arp"})
        assert r.status_code == 400 and r.json() == {"error": "emu_error", "message": "the read failed"}


@pytest.mark.parametrize(("raised", "status", "code"), [
    (ServerUnreachable("the server went away"), 502, "unreachable"),
    (EmuRefused("not_connected", "Disconnected while reading."), 409, "not_connected")])
def test_a_loss_during_the_relisting_is_the_answer(tmp_path: Path, fake_emu: Factory,
                                                   monkeypatch: pytest.MonkeyPatch, raised: Exception,
                                                   status: int, code: str) -> None:
    """The read fails, then the re-listing finds the connection lost or dropped: that is what is answered."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2")
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        connect(c, f)

        async def read_fails(*_a: Any, **_k: Any) -> Any:
            """The read's error."""
            raise TrexApiError("the read failed")

        async def listing_lost(*_a: Any, **_k: Any) -> Any:
            """The re-listing meets the loss."""
            raise raised
        monkeypatch.setattr(app.state.emu, "counters", read_fails)
        monkeypatch.setattr(app.state.emu, "namespaces", listing_lost)
        r = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "arp"})
        assert r.status_code == status and r.json()["error"] == code, r.text


def test_an_interrupted_namespace_walk_never_answers_no_namespace(tmp_path: Path, fake_emu: Factory) -> None:
    """With 3 namespaces and every cursor read interrupting, the listing ends interrupted without vport 9;
    that proves nothing, so the read's error (400 emu_error) passes through."""
    f = fake_emu("--foreign-ns", "3", "--clients", "1", "--interrupt-iter", "1")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        r = c.get("/api/emu/counters", params={"scope": "ns", "vport": 9, "plugin": "arp"})
        assert r.status_code == 400 and r.json()["error"] == "emu_error", r.text



def test_inspect_summarises_without_a_server(tmp_path: Path) -> None:
    """POST /api/emu/profile/inspect runs the profile in the worker and needs no EMU connection."""
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        r = c.post("/api/emu/profile/inspect", json={"path": PROFILE, "tunables": ["--clients", "4"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["counts"] == {"namespaces": 2, "clients": 8} and body["plugins"] == ["arp", "icmp"]
        assert body["vports"] == [0, 1] and body["warnings"] == []
        assert [t["dest"] for t in body["tunables"]] == ["clients", "vports"]
        bad = c.post("/api/emu/profile/inspect", json={"path": str(tmp_path / "nope.py")})
        assert bad.status_code == 422 and bad.json()["error"] == "profile_error"
        assert c.post("/api/emu/profile/inspect", json={"path": PROFILE, "extra": 1}).status_code == 422


def test_inspect_reports_unknown_vports(tmp_path: Path, fake_trex: Factory) -> None:
    """With the Control session connected (the fake TRex has 2 ports) a vport past them is a warning; the
    inspection still succeeds. Not connected: no warning."""
    f = fake_trex()
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        args = {"path": PROFILE, "tunables": ["--vports", "4"]}
        assert c.post("/api/emu/profile/inspect", json=args).json()["warnings"] == []
        r = c.post("/api/session/connect", json={"host": f.host, "sync_port": f.rpc_port,
                                                 "async_port": f.pub_port, "timeout_s": 2})
        assert r.status_code == 200, r.text
        got = c.post("/api/emu/profile/inspect", json=args)
        assert got.status_code == 200 and got.json()["vports"] == [0, 1, 2, 3]
        assert got.json()["warnings"] == [
            "vport 2 is not a port of the connected TRex, which has 2",
            "vport 3 is not a port of the connected TRex, which has 2"]


def test_load_and_remove_refusals_send_nothing(tmp_path: Path, fake_emu: Factory) -> None:
    """Without `confirm`, with a rate out of range, with foreign namespaces and no ack, or not connected: the
    write is refused and the server holds what it held (the fake's log has no write)."""
    f = fake_emu("--foreign-ns", "2", "--clients", "2")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        gone = c.post(REMOVE, json={"confirm": True})
        assert gone.status_code == 409 and gone.json()["error"] == "not_connected"
        connect(c, f)
        for path, body in ((LOAD, {"path": PROFILE}), (REMOVE, {})):
            r = c.post(path, json=body)
            assert r.status_code == 409 and r.json()["error"] == "confirm_required", r.text
            r = c.post(path, json={**body, "confirm": True})
            assert r.status_code == 409 and r.json()["error"] == "foreign_namespaces", r.text
            assert "2 namespace(s)" in r.json()["message"]
            for rate in (0, 100001, "fast"):
                r = c.post(path, json={**body, "confirm": True, "foreign_ack": True, "max_rate": rate})
                assert r.status_code == 422 and r.json()["error"] == "bad_request", (rate, r.text)
        assert not WRITES & {r["method"] for r in f.requests()}
        assert c.get("/api/emu").json()["generation"] == 0


def test_crash_risk_needs_the_crash_ack(tmp_path: Path, fake_emu: Factory) -> None:
    """A foreign namespace with the ipfix plugin: 409 `crash_risk` (even with foreign_ack) and no write in the
    fake's log; with `crash_ack: true` the write goes through. `crash_ack` is a strict boolean."""
    f = fake_emu("--foreign-ns", "1", "--clients", "1", "--plugins", "arp,ipfix")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        for path, body in ((LOAD, {"path": PROFILE}), (REMOVE, {})):
            r = c.post(path, json={**body, "confirm": True, "foreign_ack": True})
            assert r.status_code == 409 and r.json()["error"] == "crash_risk", r.text
            assert "restart" in r.json()["message"]
            assert c.post(path, json={**body, "confirm": True, "foreign_ack": True,
                                      "crash_ack": "yes"}).status_code == 422
        assert not WRITES & {r["method"] for r in f.requests()}
        r = c.post(REMOVE, json={"confirm": True, "foreign_ack": True, "crash_ack": True})
        assert r.status_code == 200, r.text
        assert c.get("/api/emu/namespaces").json()["namespaces"] == []


def test_load_then_remove(tmp_path: Path, fake_emu: Factory) -> None:
    """A confirmed, acknowledged load answers the snapshot (loaded, generation 1) and the namespaces read
    shows the profile as ours; the removal empties the server (generation 2, loaded null)."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        r = c.post(LOAD, json={"path": PROFILE, "tunables": ["--clients", "2"], "confirm": True,
                               "foreign_ack": True, "max_rate": 1000})
        assert r.status_code == 200, r.text
        snap = r.json()
        assert snap["type"] == "emu" and snap["generation"] == 1 and snap["busy"] is None
        assert snap["loaded"]["path"] == PROFILE and snap["loaded"]["tunables"] == ["--clients", "2"]
        loaded = snap["loaded"]
        assert (loaded["namespaces"], loaded["clients"], loaded["partial"]) == (2, 4, False)
        listed = c.get("/api/emu/namespaces").json()
        assert listed["generation"] == 1
        assert [(n["vport"], n["active_clients"], n["ours"]) for n in listed["namespaces"]] == [
            (0, 2, True), (1, 2, True)]
        gone = c.post(REMOVE, json={"confirm": True})
        assert gone.status_code == 200 and gone.json()["generation"] == 2 and gone.json()["loaded"] is None
        assert c.get("/api/emu/namespaces").json()["namespaces"] == []


def test_load_profile_error_and_server_errors(tmp_path: Path, fake_emu: Factory) -> None:
    """A profile that does not load is 422 profile_error with nothing sent; the server's error is 400
    emu_error naming how far the load got; a lost connection is 502 unreachable."""
    f = fake_emu("--fail", "ctx_client_add=2")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        r = c.post(LOAD, json={"path": str(tmp_path / "nope.py"), "confirm": True})
        assert r.status_code == 422 and r.json()["error"] == "profile_error"
        assert not WRITES & {r["method"] for r in f.requests()}
        r = c.post(LOAD, json={"path": PROFILE, "confirm": True})
        assert r.status_code == 400 and r.json()["error"] == "emu_error", r.text
        assert "injected failure" in r.json()["message"] and "partial profile" in r.json()["message"]
        assert c.get("/api/emu").json()["loaded"]["partial"] is True
    g = fake_emu("--hang", "ctx_set_def_plugins")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        up = c.post("/api/emu/connect", json={"host": g.host, "port": g.port, "timeout_s": 0.5})
        assert up.status_code == 200
        r = c.post(LOAD, json={"path": PROFILE, "confirm": True})
        assert r.status_code == 502 and r.json()["error"] == "unreachable", r.text
        assert c.get("/api/emu").json()["state"] == "lost"


def test_write_bodies_are_strict(tmp_path: Path) -> None:
    """`confirm` and `foreign_ack` take only true booleans ("yes" and 1 are 422), and a tunable has a length
    limit (review M1, M4)."""
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        for path, body in ((LOAD, {"path": PROFILE}), (REMOVE, {})):
            for bad in ("yes", 1):
                assert c.post(path, json={**body, "confirm": bad}).status_code == 422, (path, bad)
                assert c.post(path, json={**body, "confirm": True, "foreign_ack": bad}).status_code == 422
        r = c.post(LOAD, json={"path": PROFILE, "confirm": True, "tunables": ["x" * 1001]})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
        r = c.post("/api/emu/profile/inspect", json={"path": PROFILE, "tunables": ["x" * 1001]})
        assert r.status_code == 422


def test_a_plugin_not_loaded_is_409_unsupported_and_asked_again(tmp_path: Path, fake_emu: Factory) -> None:
    """A plugin the namespace does not load (igmp; the fake loads arp and icmp) is answered 'no valid
    plugin' by the server: named, it is 409 unsupported, and unlike a method-not-found it is asked again on
    the next request, since another namespace may load it (ruling R11's second route)."""
    f = fake_emu("--foreign-ns", "1", "--clients", "1")
    with authed(create_app(tmp_path, token=TEST_TOKEN)) as c:
        connect(c, f)
        for n in (1, 2):
            r = c.get("/api/emu/counters", params={"scope": "ns", "vport": 0, "plugin": "igmp"})
            assert r.status_code == 409 and r.json()["error"] == "unsupported", r.text
            assert len(served(f, "igmp_ns_cnt")) >= n


def test_connect_and_disconnect_refusals_over_http(tmp_path: Path, fake_emu: Factory,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """409 busy for a connect while another is in flight, 409 unsupported_server for a server on another
    EMU API, and 409 busy for a disconnect the session refuses during a write."""
    import threading

    from trex_web_gui.trexapi import emu as emu_api
    hung = fake_emu("--hang", "get_version")
    app = create_app(tmp_path, token=TEST_TOKEN)
    with authed(app) as c:
        first: dict[str, Any] = {}
        t = threading.Thread(target=lambda: first.update(r=c.post(
            "/api/emu/connect", json={"host": hung.host, "port": hung.port, "timeout_s": 1})))
        t.start()
        end = time.monotonic() + 5
        while c.get("/api/emu").json()["state"] != "connecting" and time.monotonic() < end:
            time.sleep(0.02)
        busy = c.post("/api/emu/connect", json={"host": hung.host, "port": hung.port, "timeout_s": 1})
        assert busy.status_code == 409 and busy.json()["error"] == "busy", busy.text
        t.join(10)
        assert first["r"].status_code == 502          # the first one timed out against the hung method

        f = fake_emu()
        monkeypatch.setattr(emu_api, "EMU_API", ("EMU", 2, 0))
        other = c.post("/api/emu/connect", json={"host": f.host, "port": f.port, "timeout_s": 2})
        assert other.status_code == 409 and other.json()["error"] == "unsupported_server", other.text
        monkeypatch.undo()

        async def refuse() -> None:
            """The session's refusal during a write."""
            raise EmuRefused("busy", "An EMU write is in progress.")
        monkeypatch.setattr(app.state.emu, "disconnect", refuse)
        r = c.post("/api/emu/disconnect")
        assert r.status_code == 409 and r.json()["error"] == "busy", r.text
