import json
from pathlib import Path

P3C = Path(__file__).resolve().parent.parent / "fixtures" / "p3c"

KINDS = {"udp", "arp", "vlan", "ipv6_nd", "truncated"}


def _summary() -> dict:
    """The parsed fixtures/p3c/summary.json."""
    return json.loads((P3C / "summary.json").read_text())


def _trees() -> dict:
    """The parsed fixtures/p3c/trees.json."""
    return json.loads((P3C / "trees.json").read_text())


def _layer(tree: dict, layer_id: str) -> dict | None:
    """The layer of a decoded tree with this id, or None when the tree has none."""
    return next((layer for layer in tree["data"] if layer["id"] == layer_id), None)


def test_status_and_fetch_shapes() -> None:
    s = _summary()
    assert set(s["status_row"]) == {"id", "matched", "fetched", "filter", "state", "count", "bytes", "limit",
                                    "mode"}
    assert set(s["status_row"]["filter"]) == {"tx", "rx", "bpf"}
    assert (isinstance(s["status_row"]["filter"]["tx"], int)
            and isinstance(s["status_row"]["filter"]["rx"], int))
    first = s["first_packet"]
    assert set(first["keys"]) == {"ts", "binary", "port", "index", "wirelen", "origin"}
    assert first["origin"] in {"RX", "TX"}
    assert first["index"] == 1


def test_error_texts_recorded() -> None:
    s = _summary()
    e = s["errors"]
    assert "is not a valid pattern" in e["bad_bpf"]
    assert "capture limit has reached" in e["limit"]
    for key in ("fetch_9999", "stop_9999", "remove_9999"):
        assert "capture ID not found" in e[key]
    assert e["limit"] != e["fetch_9999"]
    assert s["remove_active_allowed"] is True
    assert s["reset_removes_captures"] is True
    assert s["capture_survives_disconnect"] is True


def test_fixed_recorder_fills_to_limit() -> None:
    s = _summary()
    fixed = s["fixed_recorder"]
    assert fixed["final"]["count"] == 50
    assert fixed["stop"]["pkt_count"] == 50
    monitor = s["monitor"]["final"]
    assert monitor["matched"] > monitor["count"]


def test_service_capture_row_recorded() -> None:
    row = _summary()["service_capture"]["row"]
    assert row is not None
    assert row["filter"]["rx"] & 1
    assert row["filter"]["bpf"]


def test_frames_and_trees_present() -> None:
    s, trees = _summary(), _trees()
    assert set(s["frames"]) == KINDS
    assert set(trees) == KINDS
    dot1q = _layer(trees["vlan"], "Dot1Q")
    assert dot1q is not None
    vlan = next(f for f in dot1q["fields"] if f["id"] == "vlan")
    assert vlan["value"] == 10
    assert _layer(trees["vlan"], "ICMP") is not None
    assert _layer(trees["ipv6_nd"], "ICMPv6ND_NS") is not None
    for layer_id in ("Ether", "IP", "UDP"):
        assert _layer(trees["udp"], layer_id) is not None


def test_oracle_counts_agree() -> None:
    counts = _summary()["five_second_counts"]
    assert abs(counts["a"] - counts["b"]) <= 50


def test_truncated_frame_dissects_as_dot3_raw() -> None:
    trees = _trees()
    assert [layer["id"] for layer in trees["truncated"]["data"]] == ["Dot3", "Raw"]
    assert _layer(trees["truncated"], "Ether") is None


def test_tx_capture_probe_differs_only_in_service_mode() -> None:
    off, on = _summary()["tx_capture_probe"]
    assert (off["service"], on["service"]) == (False, True)
    for arm in (off, on):
        assert arm["force"] is True and arm["mode"] == "fixed" and arm["limit"] == 50
        assert arm["opackets_delta"] > 0                         # port 0 transmitted in both arms
    assert off["count"] == 0 and on["count"] == on["limit"]     # af_packet measurement
    assert abs(off["opackets_delta"] - on["opackets_delta"]) <= 2    # the same traffic, one variable changed


def test_fetch_peek_takes_nothing_and_names_the_capture() -> None:
    s = _summary()
    pk = s["fetch_peek"]
    assert pk["ok"] and pk["pkts"] == 0 and pk["reply_keys"] == ["pending", "pkts", "start_ts"]
    assert pk["pending"] == pk["status_count_before"] == pk["status_count_after"] > 0
    assert pk["fetched_before"] == pk["fetched_after"] == 0
    assert pk["start_ts"] == pk["next_fetch_start_ts"] == s["fixed_recorder"]["start_ts"]


def test_tx_capture_needs_service_mode() -> None:
    s = _summary()
    assert s["tx_capture_service_off"]["count"] == 0
    assert s["tx_capture_service_off"]["filter"]["tx"] & 1
    assert s["fixed_recorder"]["first_packet"]["origin"] == "TX"
