import json
from pathlib import Path

P3 = Path(__file__).resolve().parent.parent / "fixtures" / "p3"


def test_recording_has_load_and_idle_samples() -> None:
    s = json.loads((P3 / "summary.json").read_text())
    by = {c: [x for x in s["samples"] if x["condition"] == c] for c in ("idle", "load", "after_stop")}
    assert len(by["idle"]) >= 1 and len(by["load"]) == 3 and len(by["after_stop"]) >= 1
    ops = [x["stats"]["0"]["opackets"] for x in by["load"]]
    assert ops[0] < ops[1] < ops[2]
    assert by["idle"][-1]["stats"]["0"]["opackets"] == 0
    # the server's rate ramps (measured 5787, 7789, 8855 pps over the three samples), so the last sample is
    # the steady one
    assert abs(by["load"][-1]["stats"]["global"]["tx_pps"] - 10_000) <= 2_000
    assert s["rx_advancing_ports_when_port0_transmits"] == [1]
    batches = [r for r in map(json.loads, (P3 / "rpc.jsonl").read_text().splitlines())
               if isinstance(r["req"], list)
               and [q["method"] for q in r["req"]]
               == ["get_global_stats", "get_port_stats", "get_port_stats"]]
    assert len(batches) >= 7
