"""HTTP surface for P3b Task 4, against the fake. Ping, scan and resolve cannot run on it (it answers
`push_pkts` as an unknown method), so those routes are covered by tests/unit/test_session_port_config.py."""
from pathlib import Path

from support import FIXTURES
from test_api import client
from test_api_control import connect

MAC = "52:52:18:89:91:ea"        # the recorded peer MAC (tests/fixtures/p3b/summary.json: resolved_mac)


def layer(c, **kw):
    """POST /api/ports/layer for port 0 as L2 to the peer MAC (upper-cased, no VLAN unless `kw` overrides
    a field); returns the response unchecked."""
    return c.post(
        "/api/ports/layer", json={"port": 0, "mode": "L2", "dst_mac": MAC.upper(), "vlan": [], **kw}
    )


def test_post_layer_l2_then_ports_shows_layer_cfg(tmp_path: Path, fake_trex) -> None:
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        r = layer(c, vlan=[10, 20])
        assert r.status_code == 200, r.text
        assert r.json()["arp"] is None and r.json()["service_restore_error"] is None
        p0 = c.get("/api/ports").json()["ports"][0]
        assert p0["layer_cfg"]["ether"]["dst"] == MAC          # sent lower-case
        assert p0["vlan"] == [10, 20] and p0["service"] is False   # service mode is back off


def test_three_vlans_is_422_before_any_rpc(tmp_path: Path, fake_trex) -> None:
    # not connected: any session action would be 409
    with client(tmp_path) as c:
        r = layer(c, vlan=[1, 2, 3])
        assert r.status_code == 422 and r.json()["error"] == "bad_request", r.text
        assert layer(c, vlan=[]).status_code == 409            # the same body, valid, reaches the session
        for bad in ({"dst_mac": MAC + "\n"}, {"vlan": [0]}, {"vlan": [4096]}, {"dst_mac": "52:52:18:89:91"},
                    {"mode": "L3"}, {"mode": "L3", "src_ipv4": "1.1.1.1", "dst_ipv4": "fe80::1"}):
            r = layer(c, **bad)
            assert r.status_code == 422, (bad, r.text)
        r = layer(c, mode="L3", src_ipv4="1.1.1.1", dst_ipv4="fe80::1")
        assert "Source and destination must both be IPv4" in r.json()["message"]
        assert c.post("/api/ports/ping", json={"port": 0, "dst": "1.1.1.99", "count": 11}).status_code == 422
        assert (
            c.post("/api/ports/ping", json={"port": 0, "dst": "1.1.1.99", "pkt_size": 63}).status_code == 422
        )
        assert c.post("/api/ports/ping", json={"port": 0, "dst": "not-an-ip"}).status_code == 422


def test_layer_read_only_is_409(tmp_path: Path, fake_trex) -> None:
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f, read_only=True)
        r = layer(c)
        assert r.status_code == 409 and r.json()["error"] == "read_only"


def test_layer_foreign_owned_is_409_not_owned(tmp_path: Path, fake_trex, foreign_client) -> None:
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        foreign_client(f).acquire(ports=[0])
        r = layer(c)
        assert r.status_code == 409 and r.json()["error"] == "not_owned"
        assert c.post("/api/ports/scan6", json={"port": 0}).json()["error"] == "not_owned"
        assert c.post("/api/ports/ping", json={"port": 0, "dst": "1.1.1.99"}).json()["error"] == "not_owned"


def test_ipv6_on_legacy_is_409_ipv6_unsupported(tmp_path: Path, fake_trex) -> None:
    f = fake_trex()                                            # the recorded legacy stack
    with client(tmp_path) as c:
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        r = layer(c, ipv6={"enabled": True})
        assert r.status_code == 409 and r.json() == {
            "error": "ipv6_unsupported",
            "message": "IPv6 configuration needs the linux_based stack; this server runs legacy"}
        assert layer(c, ipv6={"enabled": False}).status_code == 200
        assert c.get("/api/ports").json()["ports"][0]["service"] is False


def test_ipv6_on_linux_based_is_applied(tmp_path: Path, fake_trex) -> None:
    f = fake_trex("--stack", "linux_based")
    with client(tmp_path) as c:
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        assert layer(c, ipv6={"enabled": True}).status_code == 200
        assert c.get("/api/ports").json()["ports"][0]["layer_cfg"]["ipv6"]["enabled"] is True


def test_apply_refused_while_transmitting_is_409_port_active(tmp_path: Path, fake_trex) -> None:
    f = fake_trex()
    profile = str(FIXTURES / "profiles" / "udp_for_benchmarks.py")
    with client(tmp_path) as c:
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        assert c.post("/api/ports/assign", json={"ports": [0], "path": profile}).status_code == 200
        assert c.post("/api/ports/start", json={"ports": [0], "mult": "1kpps"}).status_code == 200
        r = layer(c)
        assert r.status_code == 409 and r.json() == {
            "error": "port_active", "message": "Port 0 is transmitting; stop traffic first."}


def test_xstats_without_owning_is_200(tmp_path: Path, fake_trex) -> None:
    f = fake_trex()
    with client(tmp_path) as c:
        connect(c, f)
        r = c.get("/api/ports/1/xstats")                       # nothing acquired
        assert r.status_code == 200, r.text
        assert r.json()["port"] == 1 and r.json()["values"]["rx_good_packets"] >= 0
        assert c.get("/api/ports/7/xstats").json()["error"] == "bad_port"
