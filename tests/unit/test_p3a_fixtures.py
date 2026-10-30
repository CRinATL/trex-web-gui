import json
from pathlib import Path

P3A = Path(__file__).resolve().parent.parent / "fixtures" / "p3a"


def _by_condition() -> dict[str, list[dict]]:
    """The summary.json oracle samples grouped by condition: idle, load, after_stop and re_add."""
    s = json.loads((P3A / "summary.json").read_text())
    return {c: [x["oracle"] for x in s["samples"] if x["condition"] == c]
            for c in ("idle", "load", "after_stop", "re_add")}


def test_recording_has_flow_latency_and_util() -> None:
    by = _by_condition()
    assert (len(by["idle"]) >= 1 and len(by["load"]) == 3 and len(by["after_stop"]) >= 1
            and len(by["re_add"]) >= 1)
    flow = [o["pgid_stats"]["flow_stats"]["5"] for o in by["load"]]
    tx = [f["tx_pkts"]["total"] for f in flow]
    assert tx[0] < tx[1] < tx[2]
    assert abs(flow[-1]["tx_pps"]["total"] - 1000) <= 200
    lat = by["load"][-1]["pgid_stats"]["latency"]["7"]["latency"]
    assert isinstance(lat["average"], (int, float))
    before = by["after_stop"][-1]["pgid_stats"]["ver_id"]["5"]
    after = by["re_add"][-1]["pgid_stats"]["ver_id"]["5"]
    assert before != after
    mbuf = by["load"][-1]["util_stats"]["mbuf_stats"]
    sizes = {"64b", "128b", "256b", "512b", "1024b", "2048b", "4096b", "9kb"}
    assert mbuf and all(set(pools) >= sizes for pools in mbuf.values())
    for pools in mbuf.values():
        for free, total in pools.values():
            assert 0 <= free <= total


def test_recording_has_the_rpcs() -> None:
    methods = {r["req"]["method"] for r in map(json.loads, (P3A / "rpc.jsonl").read_text().splitlines())
               if isinstance(r["req"], dict)}
    assert {"get_active_pgids", "get_pgid_stats", "get_utilization"} <= methods


def test_recording_pins_the_summary_facts() -> None:
    s = json.loads((P3A / "summary.json").read_text())
    ro = s["read_only_client"]
    assert all(ro[m]["ok"] is True for m in ("get_active_pgids", "get_pgid_stats", "get_util_stats"))
    assert s["history"]["old_head_at_new_index_1"] is True
    assert s["rx_ports_counting_flow_5_when_port0_transmits"] == [1]
    assert all("7" not in o["pgid_stats"]["latency"] for o in _by_condition()["idle"])
