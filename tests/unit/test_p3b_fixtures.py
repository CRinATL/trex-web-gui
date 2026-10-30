import json
from pathlib import Path

P3B = Path(__file__).resolve().parent.parent / "fixtures" / "p3b"


def _summary() -> dict:
    """The parsed fixtures/p3b/summary.json."""
    return json.loads((P3B / "summary.json").read_text())


def test_recording_has_config_ping_scan_xstats() -> None:
    s = _summary()
    hw1 = s["hw_mac_port1"]
    assert s["port_info"]["1"]["hw_mac"] == hw1
    assert s["resolved_mac"] == hw1
    oks = s["pings"]["ok"]
    assert len(oks) == 5
    assert all(len(p["records"]) == 1 and p["records"][0]["status"] == "success" for p in oks)
    assert s["pings"]["timeout"]["records"][0]["status"] == "timeout"
    assert [n["mac"] for n in s["scan"]["port1_entries"]] == [hw1]
    # xstats: the NIC counter's growth across the transmission equals the independent client's opackets growth
    by = {c: [x for x in s["xstats_samples"] if x["condition"] == c] for c in ("idle", "load", "after_stop")}
    assert len(by["load"]) == 3 and by["idle"] and by["after_stop"]
    before, after = by["idle"][-1], by["after_stop"][0]
    tx = after["xstats"]["0"]["tx_good_packets"] - before["xstats"]["0"]["tx_good_packets"]
    assert tx > 0 and tx == after["oracle"]["0"]["opackets"] - before["oracle"]["0"]["opackets"]
    assert "tx_good_packets" in s["xstats_names"]["0"] and "rx_good_packets" in s["xstats_names"]["1"]
    # refusals: the two raw ones carry the server's text, the client check its own
    r = s["refusals"]
    assert r["raw_set_l2_while_tx"]["error"] == (
        "command 'set_l2_mode' cannot be executed on current state: 'TX'")
    assert r["raw_set_vlan_three_tags"]["error"] == "Maximal number of stacked VLANs is 2, got: 3"
    assert "cannot be executed on current state: 'TX'" in r["client_set_l2_while_tx"]["error"]
    assert "port is active, please stop the port" in r["client_set_l2_while_tx_base_check"]["error"]


def test_recording_leaves_the_ports_as_it_found_them() -> None:
    s = _summary()
    for p, (src, dst) in {"0": ("1.1.1.1", "2.2.2.2"), "1": ("2.2.2.2", "1.1.1.1")}.items():
        f = s["final_state"][p]
        assert f["service"] is False and f["vlan"] == []
        assert f["layer_cfg"]["ipv4"] == {"src": src, "dst": dst, "state": "resolved"}
        assert f["layer_cfg"]["ipv6"] == {"enabled": False}
    assert s["slowest_call"]["secs"] < 4, s["slowest_call"]


def test_recording_has_the_rpcs() -> None:
    methods = {r["req"]["method"] for r in map(json.loads, (P3B / "rpc.jsonl").read_text().splitlines())
               if isinstance(r["req"], dict)}
    assert {"service", "set_vlan", "set_l2", "set_l3", "conf_ipv6", "get_port_status",
            "get_port_xstats_names", "get_port_xstats_values", "push_pkts"} <= methods
