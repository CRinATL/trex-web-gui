"""The stateful fake models the P3b configuration RPCs (P3b Task 2). Texts and post-command statuses come from
tests/fixtures/p3b/rpc.jsonl (recorded from a real v3.08 guest, stack "legacy"); the tests read the fake's
wire answers raw through the client's transport (`_transmit`), as the server's own handlers see them."""
import json
import time

import pytest
from support import FIXTURES

RPC = [json.loads(x) for x in (FIXTURES / "p3b" / "rpc.jsonl").read_text().splitlines()]
# the linux_based stack, recorded by vm/record_p3b_v6.py (P3b follow-up 2)
V6 = json.loads((FIXTURES / "p3b-v6" / "summary.json").read_text())


def recorded_pairs():
    """Every recorded (row index, request, response) in file order."""
    for i, r in enumerate(RPC):
        resps = r["resp"] if isinstance(r["resp"], list) else [r["resp"]]
        reqs = r["req"] if isinstance(r["req"], list) else [r["req"]]
        by_id = {x["id"]: x for x in resps}
        for q in reqs:
            yield i, q, by_id[q["id"]]


def recorded_error(method, needle):
    """The `error` object of the first recorded `method` response whose specific_err contains `needle`;
    AssertionError when none does."""
    for _, q, x in recorded_pairs():
        if q["method"] == method and needle in x.get("error", {}).get("specific_err", ""):
            return x["error"]
    raise AssertionError(f"no recorded {method} error containing {needle!r}")


def recorded_status_after(method):
    """(request params, status) for the first successful `method` on port 0 that a later recorded port 0
    get_port_status follows, the status being the next such one; AssertionError when there is none."""
    pairs = list(recorded_pairs())
    for n, (_, q, x) in enumerate(pairs):
        if q["method"] == method and q["params"]["port_id"] == 0 and "result" in x:
            for _, q2, x2 in pairs[n + 1:]:
                if q2["method"] == "get_port_status" and q2["params"]["port_id"] == 0:
                    return q["params"], x2["result"]
    raise AssertionError(f"no recorded {method}")


class Rig:
    def __init__(self, fake_trex, foreign_client, *flags):
        """Start a fake with `flags`, connect a client "gui-a" to it and acquire ports 0 and 1 (forced);
        loads the vendored `trex.stl.api` into `self.api`."""
        self.c = foreign_client(fake_trex(*flags), user="gui-a")
        from trex.stl import api
        self.api = api
        self.c.acquire(ports=[0, 1], force=True)

    def raw(self, method, port=0, **params):
        """The unchecked response of a raw `method` RPC on `port`, sent with that port's handler, non-
        blocking, plus `params`."""
        return self.c._transmit(
            method, params={"handler": self.c.ports[port].handler, "port_id": port, "block": False, **params}
        )

    def status(self, port=0):
        """The raw get_port_status data for `port`; asserts the call succeeded."""
        rc = self.c._transmit("get_port_status", params={"port_id": port})
        assert rc, rc.err()
        return rc.data()

    def attr(self, port=0):
        """The `attr` block of `port`'s status."""
        return self.status(port)["attr"]

    def start_tx(self, port=0, pps=100):
        """Add one continuous UDP stream at `pps` to `port` and start the port at that rate (the fake
        begins transmitting)."""
        t = self.api
        self.c.add_streams(
            [
                t.STLStream(
                    packet=t.STLPktBuilder(pkt=t.Ether() / t.IP() / t.UDP() / ("x" * 18)),
                    mode=t.STLTXCont(pps=pps),
                )
            ],
            ports=[port],
        )
        self.c.start(ports=[port], mult=f"{pps}pps")


@pytest.fixture
def rig(fake_trex, foreign_client):
    """Return a Rig on a default fake. It tears nothing down itself; the foreign_client fixture
    disconnects the client and fake_trex then stops the fake."""
    return Rig(fake_trex, foreign_client)


@pytest.fixture
def rig_linux(fake_trex, foreign_client):
    """Return a Rig on a fake started with `--stack linux_based`. It tears nothing down itself; the
    foreign_client fixture disconnects the client and fake_trex then stops the fake."""
    return Rig(fake_trex, foreign_client, "--stack", "linux_based")


def test_set_l2_layer_cfg_matches_recording(rig):
    params, want = recorded_status_after("set_l2")
    rc = rig.raw("set_l2", dst_mac=params["dst_mac"])
    assert rc and rc.data() == {}
    assert rig.attr()["layer_cfg"] == want["attr"]["layer_cfg"]
    # the recorded effect: IPv4 forgotten, ether kept
    assert want["attr"]["layer_cfg"]["ipv4"] == {"state": "none"}


def test_set_l3_resolved_mac_sets_resolved(rig):
    mac = "aa:bb:cc:dd:ee:ff"
    rc = rig.raw("set_l3", src_addr="1.1.1.1", dst_addr="1.1.1.99", resolved_mac=mac)
    assert rc and rc.data() == {}
    cfg = rig.attr()["layer_cfg"]
    assert cfg["ipv4"] == {"src": "1.1.1.1", "dst": "1.1.1.99", "state": "resolved"}
    assert cfg["ether"]["dst"] == mac and cfg["ether"]["state"] == "configured"


def test_set_l3_without_mac_is_unresolved(rig):
    before = rig.attr()["layer_cfg"]["ether"]["dst"]
    rc = rig.raw("set_l3", src_addr="1.1.1.1", dst_addr="1.1.1.99")
    assert rc and rc.data() == {}
    cfg = rig.attr()["layer_cfg"]
    assert cfg["ipv4"] == {"src": "1.1.1.1", "dst": "1.1.1.99", "state": "unresolved"}
    # as recorded: dst kept, no longer valid
    assert cfg["ether"]["state"] == "unconfigured" and cfg["ether"]["dst"] == before


def test_set_vlan_two_tags(rig):
    assert rig.raw("set_vlan", vlan=[10, 20])
    assert rig.attr()["vlan"] == {"tags": [10, 20]}
    assert rig.raw("set_vlan", vlan=[])
    assert rig.attr()["vlan"] == {"tags": []}


def test_conf_ipv6_auto_and_off(rig_linux):
    """The shapes the real linux_based stack reported (tests/fixtures/p3b-v6), not modelled ones."""
    rig = rig_linux
    assert V6["port_info"]["0"]["stack"] == "linux_based"
    assert rig.raw("conf_ipv6", enabled=True, src_ipv6="")
    assert rig.attr()["layer_cfg"]["ipv6"] == V6["ipv6_auto_layer_cfg"]["ipv6"]
    assert rig.raw("conf_ipv6", enabled=True, src_ipv6="2001:db8::1")
    assert rig.attr()["layer_cfg"]["ipv6"] == V6["ipv6_source_layer_cfg"]["0"]["ipv6"]
    assert rig.raw("conf_ipv6", enabled=False, src_ipv6="")
    assert rig.attr()["layer_cfg"]["ipv6"] == V6["ipv6_off_layer_cfg"]["ipv6"]
    assert rig.c._transmit("get_system_info").data()["ports"][0]["stack"] == "linux_based"


def test_set_l2_drops_ipv6_as_recorded(rig_linux):
    """linux_based: set_l2 forgets IPv6 as well as IPv4 (tests/fixtures/p3b-v6, steps resolve_back ->
    set_l2)."""
    st = {x["step"]: x["oracle"]["layer_cfg"] for x in V6["steps"]}
    assert st["resolve_back"]["ipv6"]["enabled"] and st["set_l2"]["ipv6"] == {"enabled": False}
    rig = rig_linux
    assert rig.raw("conf_ipv6", enabled=True, src_ipv6="2001:db8::1")
    assert rig.raw("set_l2", dst_mac=V6["hw_mac_port1"])
    assert rig.attr()["layer_cfg"]["ipv6"] == st["set_l2"]["ipv6"]
    assert rig.attr()["layer_cfg"]["ipv4"] == st["set_l2"]["ipv4"]


def test_conf_ipv6_enable_refused_on_legacy(rig):
    want = recorded_error("conf_ipv6", "IPv6 is not supported with current stack")
    for src in ("", "2001:db8::1"):
        rc = rig.raw("conf_ipv6", enabled=True, src_ipv6=src)
        assert not rc and want["specific_err"] in rc.err()
    assert rig.attr()["layer_cfg"]["ipv6"] == {"enabled": False}
    assert rig.raw("conf_ipv6", enabled=False, src_ipv6="")                      # off succeeds
    assert rig.c._transmit("get_system_info").data()["ports"][0]["stack"] == "legacy"


def test_per_port_attr_is_independent(rig):
    a0, a1 = rig.attr(0), rig.attr(1)
    # each port keeps its recorded attr
    assert a0["layer_cfg"]["ether"]["src"] != a1["layer_cfg"]["ether"]["src"]
    assert a1["layer_cfg"]["ipv4"]["src"] == "2.2.2.2"
    assert rig.raw("set_vlan", port=0, vlan=[7])
    assert rig.raw("set_l2", port=0, dst_mac="aa:bb:cc:dd:ee:ff")
    assert rig.attr(1) == a1
    assert rig.attr(0)["vlan"] == {"tags": [7]}


def test_set_l2_while_tx_returns_recorded_text(rig):
    want = recorded_error("set_l2", "cannot be executed on current state: 'TX'")
    rig.start_tx(0)
    for method, extra in (("set_l2", {"dst_mac": "aa:bb:cc:dd:ee:ff"}),):
        rc = rig.raw(method, **extra)
        assert not rc and want["specific_err"] in rc.err()
    before = rig.attr()
    # the other write RPCs share the refusal, named for their own server command
    # (trex_port.cpp:378-380, 394-395, 410-411, 482-484)
    for method, cmd, extra in (("set_l3", "set_l3_mode", {"src_addr": "1.1.1.1", "dst_addr": "1.1.1.99"}),
                               ("set_vlan", "set_vlan_cfg", {"vlan": [5]}),
                               ("conf_ipv6", "conf_ipv6", {"enabled": False, "src_ipv6": ""})):
        rc = rig.raw(method, **extra)
        assert not rc and f"command '{cmd}' cannot be executed on current state: 'TX'" in rc.err()
    assert rig.attr() == before
    rig.c.stop(ports=[0])
    assert rig.raw("set_l2", dst_mac="aa:bb:cc:dd:ee:ff")                          # STREAMS allows it again


def test_three_vlans_returns_recorded_text(rig):
    want = recorded_error("set_vlan", "Maximal number of stacked VLANs is 2")
    rc = rig.raw("set_vlan", vlan=[10, 20, 30])
    assert not rc and want["specific_err"] in rc.err()
    assert rig.attr()["vlan"] == {"tags": []}


def test_push_pkts_is_refused(rig):
    rc = rig.c._transmit("push_pkts", params={"port_id": 0, "pkts": []})
    assert not rc and "Method push_pkts not registered" in rc.err()


def test_xstats_tx_good_packets_follows_opackets(rig):
    names = rig.c._transmit("get_port_xstats_names", params={"port_id": 0}).data()["xstats_names"]

    def xs(port):
        vals = rig.c._transmit("get_port_xstats_values", params={"port_id": port}).data()["xstats_values"]
        return dict(zip(names, vals, strict=True))

    idle0, idle1 = xs(0), xs(1)
    assert idle0["tx_good_packets"] == 0 and idle1["rx_good_packets"] == 0
    rig.start_tx(0, pps=1000)
    time.sleep(0.5)
    rig.c.stop(ports=[0])
    x0, x1 = xs(0), xs(1)
    op = rig.c._transmit("get_port_stats", params={"port_id": 0}).data()
    assert x0["tx_good_packets"] == x0["tx_q0_packets"] == op["opackets"] > 0
    assert x1["rx_good_packets"] == x1["rx_q0_packets"] == op["opackets"]            # port 1 follows its peer
    # recorded 60 B per 64 B counted
    assert x0["tx_good_bytes"] == x0["tx_q0_bytes"] == op["obytes"] - 4 * op["opackets"]
    assert x1["rx_good_bytes"] == x1["rx_q0_bytes"] == x0["tx_good_bytes"]
    # the rest stay as recorded
    for name in ("rx_missed_errors", "rx_errors", "tx_errors"):
        assert x0[name] == idle0[name] and x1[name] == idle1[name]
    assert x1["rx_missed_errors"] == 202208
