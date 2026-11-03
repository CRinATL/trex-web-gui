"""trexapi.explain_dst_mac: the server's dest-MAC refusal at start, explained from the client's cache
(RC1-30)."""
from types import SimpleNamespace

from trex_web_gui.trexapi import explain_dst_mac

REFUSED = "start_traffic: Port 0 dest MAC is invalid and there are streams without explicit dest MAC."


def _stream(dst_mode):
    """A cached stream whose destination-MAC mode (flags bits 1-2) is `dst_mode`: 1 is from the packet."""
    return SimpleNamespace(is_explicit_dst_mac=lambda: dst_mode == 1)


def _client(layer_cfg, modes):
    """Port 0 with `layer_cfg` and profile "_" holding streams of the given destination modes, ids from 5
    up."""
    ids = list(range(5, 5 + len(modes)))
    p = SimpleNamespace(get_ts_attr=lambda: {"layer_cfg": layer_cfg}, profile_stream_list={"_": ids},
                        streams={i: _stream(m) for i, m in zip(ids, modes, strict=True)})
    return SimpleNamespace(ports={0: p})


def test_an_unresolved_gateway_and_the_streams_that_need_it():
    """The problem, then the fix on a line of its own; the ARP's source and target, and the port's confirmed
    interface name when there is one."""
    c = _client({"ipv4": {"state": "unresolved", "src": "198.51.100.5", "dst": "192.0.2.1"}}, [1, 0, 0])
    assert explain_dst_mac(c, REFUSED, {0: "ens1f0np0", 1: "ens1f1np1"}) == (
        "Port 0 (ens1f0np0) cannot start: its ARP from 198.51.100.5 for its L3 gateway 192.0.2.1 got no "
        "answer, and streams 1, 2 take the destination MAC from the port.\n"
        "Give port 0 a destination on Control > Config (an L2 destination MAC, or an L3 gateway that answers "
        "ARP), or set Destination MAC to From packet on those streams in Studio > Properties and assign the "
        "profile again.")


def test_without_a_name_or_a_source_the_port_and_gateway_still_show():
    c = _client({"ipv4": {"state": "unresolved", "dst": "192.0.2.1"}}, [0])
    text = explain_dst_mac(c, REFUSED)
    assert text is not None
    assert text.startswith("Port 0 cannot start: its ARP for its L3 gateway 192.0.2.1 got no answer, and "
                           "stream 0")
    c = _client({"ipv4": {"state": "unresolved"}}, [0])
    no_gateway = explain_dst_mac(c, REFUSED) or ""
    assert no_gateway.startswith("Port 0 cannot start: its L3 gateway did not answer ARP")


def test_no_destination_and_one_stream():
    c = _client({"ether": {"state": "unconfigured"}, "ipv4": {"state": "none"}}, [0])
    text = explain_dst_mac(c, REFUSED)
    assert text is not None
    assert text.startswith("Port 0 cannot start: it has no destination MAC set, and stream 0 takes the "
                           "destination")


def test_other_errors_and_unknown_ports_are_left_alone():
    c = _client({}, [0])
    assert explain_dst_mac(c, "start_traffic: Link state is DOWN.") is None
    assert explain_dst_mac(c, REFUSED.replace("Port 0", "Port 7")) is None
