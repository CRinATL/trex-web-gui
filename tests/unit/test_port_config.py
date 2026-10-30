"""trexapi.port_config: service bracket, set_*, resolve, ping, scan and xstats (P3b Task 3). The service,
set_* and xstats functions run against the fake (the vendored client is the oracle); ping, scan and resolve
cannot (the fake answers `capture` and `push_pkts` as unknown methods), so they run against stubs of the
client's service-level calls shaped by the records in tests/fixtures/p3b/summary.json."""
import json
import time
from types import SimpleNamespace as NS
from typing import Any

import pytest
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.trexapi import (
    CommandRefused,
    ServerRestarted,
    ServerUnreachable,
    TrexApiError,
    port_config,
    translate,
)

SUMMARY = json.loads((FIXTURES / "p3b" / "summary.json").read_text())
OK_PING = SUMMARY["pings"]["ok"][0]["records"][0]
TIMEOUT_PING = SUMMARY["pings"]["timeout"]["records"][0]
V6_PING = SUMMARY["pings"]["v6"]["records"][0]
SCAN = SUMMARY["scan"]["raw"]["0"]
PEER_MAC = SUMMARY["resolved_mac"]


@pytest.fixture
def client(fake_trex, foreign_client):
    """A vendored client on a fake as gui-a, ports 0 and 1 force-acquired (foreign_client disconnects it)."""
    c = foreign_client(fake_trex(), user="gui-a")
    c.acquire(ports=[0, 1], force=True)
    return c


# ---- service bracket, against the fake

def test_service_on_off_port_returns_was_on_false(client) -> None:
    assert port_config.service_state(client, 0) == "off"
    saved = port_config.service_on(client, 0)
    assert saved == (False,) and port_config.service_state(client, 0) == "on"
    assert port_config.service_restore(client, 0, saved) is None
    assert port_config.service_state(client, 0) == "off"


def test_service_on_for_a_port_already_on_is_left_on(client) -> None:
    client.set_service_mode(ports=[0], enabled=True)
    saved = port_config.service_on(client, 0)
    assert saved.was_on is True
    assert port_config.service_restore(client, 0, saved) is None
    assert port_config.service_state(client, 0) == "on"


def test_service_on_filtered_is_refused(client) -> None:
    client.set_service_mode(ports=[0], enabled=False, filtered=True, mask=1)
    assert port_config.service_state(client, 0) == "filtered"
    with pytest.raises(CommandRefused) as e:
        port_config.service_on(client, 0)
    assert e.value.code == "service_filtered"
    assert e.value.message == ("Port 0 is in filtered service mode; turn service mode fully on or off in the "
                               "Control tab first.")


def test_service_restore_refusal_is_returned_not_raised() -> None:
    def refuse(**kw: Any) -> None:
        # server text, trex-core src/stx/stl/trex_stl_port.cpp:1334-1335
        raise TrexApiError("unable to disable service mode - please remove RX queue")
    got = port_config.service_restore(NS(set_service_mode=refuse), 0, port_config.ServiceSaved(was_on=False))
    assert got == "unable to disable service mode - please remove RX queue"


@pytest.mark.parametrize("exc", [ServerUnreachable("gone"), ServerRestarted("restarted")])
def test_service_restore_loss_is_raised(exc: TrexApiError) -> None:
    def lost(**kw: Any) -> None:
        raise exc
    with pytest.raises(type(exc)):
        port_config.service_restore(NS(set_service_mode=lost), 0, port_config.ServiceSaved(was_on=False))


# ---- set_*, against the fake

def test_set_vlan_l2_and_l3_reach_the_server(client) -> None:
    port_config.service_on(client, 0)
    port_config.set_vlan(client, 0, [10, 20])
    assert client.ports[0].get_vlan_cfg() == [10, 20]
    port_config.set_l2(client, 0, PEER_MAC)
    cfg = client.ports[0].get_layer_cfg()
    assert cfg["ether"]["dst"] == PEER_MAC
    port_config.set_vlan(client, 0, [])
    assert client.ports[0].get_vlan_cfg() == []


def test_set_l3_does_not_resolve(client) -> None:
    port_config.service_on(client, 0)
    port_config.set_l3(client, 0, "1.1.1.1", "2.2.2.2")
    v4 = client.ports[0].get_layer_cfg()["ipv4"]
    assert (v4["src"], v4["dst"], v4["state"]) == ("1.1.1.1", "2.2.2.2", "unresolved")


def test_set_l3_raises_the_translated_refusal(client) -> None:
    with pytest.raises(TrexApiError) as e:                  # service mode off: the port-level check
        port_config.set_l3(client, 0, "1.1.1.1", "2.2.2.2")
    assert "service mode must be enabled for configuring L3 mode" in e.value.message


def test_conf_ipv6_is_the_thin_wrapper(client) -> None:
    port_config.service_on(client, 0)
    port_config.conf_ipv6(client, 0, False, None)           # the legacy stack accepts "off"
    with pytest.raises(Exception, match="IPv6 is not supported with current stack"):
        port_config.conf_ipv6(client, 0, True, None)        # and refuses the enable, recorded text


# ---- resolve, against a stub

class _Ports:
    def __init__(self, v4: dict[str, Any], ether: dict[str, Any] | None = None) -> None:
        """Holds the ipv4 layer config and the ether one (unconfigured when None)."""
        self.v4, self.ether = v4, ether or {"state": "unconfigured"}

    def get_layer_cfg(self) -> dict[str, Any]:
        """The held layer config as {"ipv4": ..., "ether": ...}."""
        return {"ipv4": self.v4, "ether": self.ether}


class _Resolver:
    def __init__(self, v4: dict[str, Any], exc: BaseException | None,
                 ether: dict[str, Any] | None = None) -> None:
        """Holds a port 0 stub with these layer configs, the exception resolve raises (or None), and calls."""
        self.ports = {0: _Ports(v4, ether)}
        self.exc, self.calls = exc, []

    def resolve(self, ports: list[int]) -> None:
        """Records the ports asked about, then raises the held exception if one was given."""
        self.calls.append(ports)
        if self.exc:
            raise self.exc


UNRESOLVED = {"src": "1.1.1.1", "dst": "1.1.1.99", "state": "unresolved"}


def test_resolve_success_reads_the_mac_from_layer_cfg() -> None:
    c = _Resolver({**UNRESOLVED, "state": "resolved"}, None, {"dst": PEER_MAC, "state": "configured"})
    assert port_config.resolve(c, 0) == ("resolved", PEER_MAC, None)
    assert c.calls == [[0]]


def test_resolve_unresolved_by_could_not_resolve() -> None:
    c = _Resolver(UNRESOLVED, Exception("Could not resolve following ports: [0]"))
    assert port_config.resolve(c, 0) == ("unresolved", None, "Could not resolve following ports: [0]")


def test_resolve_unresolved_when_link_down_inside_resolve() -> None:
    # the psv text: common/trex_psv.py:78 (format) with :105 (the link-down message). translate() maps it to a
    # plain TrexApiError, not CommandRefused, so the layer_cfg classification still applies to it
    text = "port(s) [0]: link is DOWN"
    assert type(translate(Exception(text))) is TrexApiError
    r = port_config.resolve(_Resolver(UNRESOLVED, Exception(text)), 0)
    assert r.state == "unresolved" and r.mac is None and r.error == text


def test_recorded_failed_resolve_text_is_not_a_refusal_or_a_loss() -> None:
    # common/trex_client.py:2345 via TRexError
    text = SUMMARY["apply_unresolved_parts"]["resolve"]["error"]
    assert text == "TRexError: Could not resolve following ports: [0]"
    assert type(translate(Exception(text))) is TrexApiError
    assert port_config.resolve(_Resolver(UNRESOLVED, Exception(text)), 0).state == "unresolved"


def test_resolve_reraises_a_lost_connection_even_with_unresolved_layer_cfg() -> None:
    # trex_req_resp_client.py:335; the session marks the loss only on a raised ServerUnreachable
    c = _Resolver(UNRESOLVED, Exception("*** [RPC] - Failed to get server response from tcp://x"))
    with pytest.raises(ServerUnreachable):
        port_config.resolve(c, 0)


def test_resolve_reraises_a_restart_and_a_refusal() -> None:
    with pytest.raises(ServerRestarted):
        port_config.resolve(_Resolver(UNRESOLVED, ServerRestarted("restarted")), 0)
    with pytest.raises(CommandRefused):
        port_config.resolve(_Resolver(UNRESOLVED, CommandRefused("not_owned", "Port 0 is owned by x")), 0)


@pytest.mark.parametrize("v4", [{"state": "none"}, {**UNRESOLVED, "state": "none"},
                                {"src": "1.1.1.1", "state": "unresolved"}, {}])
def test_resolve_reraises_when_layer_cfg_is_not_the_requested_l3(v4) -> None:
    boom = Exception("unknown method: capture")      # any failure that is not a loss or a refusal
    with pytest.raises(Exception) as e:
        port_config.resolve(_Resolver(v4, boom), 0)
    assert e.value is boom


# ---- ping, against a stub of ping_ip

def _v4_record(state: int, rtt: Any = "N/A") -> Any:
    """A vendored ServiceICMP.PINGRecord with this state and rtt from responder 2.2.2.2, ttl 128, 64 bytes."""
    from trex.common.services.trex_service_icmp import ServiceICMP
    r = ServiceICMP.PINGRecord()
    r.state, r.rtt, r.responder_ip, r.ttl, r.pkt_size = state, rtt, "2.2.2.2", 128, 64
    return r


class _Pinger:
    def __init__(self, rec: Any) -> None:
        """Holds the one record ping_ip returns and an empty calls list."""
        self.rec, self.calls = rec, []

    def ping_ip(self, port: int, dst: str, pkt_size: int, count: int) -> list[Any]:
        """Records (port, dst, pkt_size, count) in calls and returns the held record as a one-item list."""
        self.calls.append((port, dst, pkt_size, count))
        return [self.rec]


@pytest.fixture
def vendored(client):          # returns the client; the vendored `trex` package is importable once it exists
    return client


def test_ping_once_ipv4_reply(vendored) -> None:
    c = _Pinger(_v4_record(2, OK_PING["rtt"]))
    line = port_config.ping_once(c, 0, "2.2.2.2", 64)
    assert c.calls == [(0, "2.2.2.2", 64, 1)]
    assert line.status == "reply" and line.rtt_ms == OK_PING["rtt"]
    assert line.text == f"Reply from 2.2.2.2: bytes=64, time={OK_PING['rtt']:.2f}ms, TTL=128"
    assert line.text.split(", time=")[0] == OK_PING["text"].split(", time=")[0]


def test_ping_once_ipv4_timeout(vendored) -> None:
    line = port_config.ping_once(_Pinger(_v4_record(0)), 0, "1.1.1.99", 64)
    assert line == ("timeout", TIMEOUT_PING["text"], None)


def test_ping_once_ipv4_unreachable_record(vendored) -> None:
    # a direct PINGRecord with state 3: not reachable on the real tier (the filter drops the reply,
    # Review Focus)
    from trex.common.services.trex_service_icmp import ServiceICMP
    rec = ServiceICMP.PINGRecord({"ICMP": NS(type=3), "IP": NS(src="1.1.1.99")}, 0.0, 0.1)
    assert rec.state == 3
    line = port_config.ping_once(_Pinger(rec), 0, "1.1.1.99", 64)
    assert line == ("unreachable", "Reply from 1.1.1.99: Destination host unreachable", None)


def test_ping_once_ipv6_reply_and_unreachable() -> None:
    dst = V6_PING["src_ip"]
    reply = {"formatted_string": V6_PING["text"], "src_ip": dst, "rtt": V6_PING["rtt"], "ttl": 64,
             "status": "success"}
    assert port_config.ping_once(_Pinger(reply), 0, dst, 64) == ("reply", V6_PING["text"], V6_PING["rtt"])
    gone = {"formatted_string": f"Reply from {dst}: Destination host unreachable", "status": "unreachable"}
    assert port_config.ping_once(_Pinger(gone), 0, dst, 64) == ("unreachable", gone["formatted_string"], None)
    slow = {"formatted_string": "Request timed out.", "status": "timeout"}
    assert port_config.ping_once(_Pinger(slow), 0, dst, 64) == ("timeout", "Request timed out.", None)


# ---- scan, against a stub of scan6

def test_scan_neighbours_shape() -> None:
    class C:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def scan6(self, ports: list[int], timeout: int) -> dict[int, Any]:
            self.calls.append((ports, timeout))
            return {0: SCAN}
    c = C()
    got = port_config.scan_neighbours(c, 0)
    assert c.calls == [([0], 3)]
    assert got == [("Host", PEER_MAC, "fe80::5052:18ff:fe89:91ea")]
    assert [n._asdict() for n in got] == [
        {"type": "Host", "mac": PEER_MAC, "ipv6": "fe80::5052:18ff:fe89:91ea"}]


# ---- xstats

def test_read_xstats_is_absolute(client) -> None:
    from trex.stl import api as t
    client.add_streams([t.STLStream(packet=t.STLPktBuilder(pkt=t.Ether() / t.IP() / t.UDP() / ("x" * 18)),
                                    mode=t.STLTXCont(pps=1000))], ports=[0])
    client.start(ports=[0], mult="1000pps")
    time.sleep(0.5)
    client.stop(ports=[0])
    first = port_config.read_xstats(client, 0)
    assert (first.port == 0
            and first.values["tx_good_packets"] == client.get_stats(ports=[0])[0]["opackets"] > 0)
    client.clear_stats()
    again = port_config.read_xstats(client, 0)
    assert again.values == first.values                      # relative=False: a clear does not rebase it


def test_read_xstats_raises_on_rpc_error() -> None:
    class RC:
        def __bool__(self) -> bool:
            return False

        def err(self) -> str:
            return "Failed to get server response from tcp://x"

    class XS:
        def update_sync(self, rpc: Any) -> Any:
            return RC()

    c = NS(ports={0: NS(get_port_xstats=lambda: XS())}, conn=NS(rpc=object()))
    with pytest.raises(trexapi.ServerUnreachable):
        port_config.read_xstats(c, 0)


def test_poll_methods_include_xstats_values() -> None:
    from trex_web_gui.trexapi.client import POLL_METHODS
    assert "get_port_xstats_values" in POLL_METHODS and "get_port_xstats_names" not in POLL_METHODS
