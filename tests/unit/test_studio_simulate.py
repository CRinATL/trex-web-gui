"""The offline simulation: hand-computed rates and chain totals (P4a Task 5)."""
import base64
import copy
from typing import Any

import pytest
from trex_web_gui.studio.simulate import disabled_next, disabled_next_pairs, report
from trex_web_gui.trexapi import RoundTrip

OK = RoundTrip(True, [])
PACKET = base64.b64encode(bytes(60)).decode()      # 60 bytes: L = 64 with the FCS


def stream(name: Any = None, rate: tuple[str, float] = ("pps", 1000.0), nxt: Any = None,
           self_start: bool = True, enabled: bool = True, loops: int = 0, **mode: Any) -> dict[str, Any]:
    """A valid stream; `mode` gives the mode keys (a continuous stream by default)."""
    s: dict[str, Any] = {
        "flags": 0, "action_count": loops, "enabled": enabled, "self_start": self_start,
        "start_paused": False, "isg": 0.0, "core_id": -1,
        "mode": {"type": "continuous", "rate": {"type": rate[0], "value": rate[1]}} | mode,
        "packet": {"binary": PACKET, "meta": ""}, "vm": {"instructions": []},
        "flow_stats": {"enabled": False},
    }
    if name is not None:
        s["name"] = name
    if nxt is not None:
        s["next"] = nxt
    return s


def burst(total: int, **kw: Any) -> dict[str, Any]:
    """A single-burst stream of `total` packets at 10 pps."""
    return stream(rate=("pps", 10.0), type="single_burst", total_pkts=total, **kw)


def test_pps_rate() -> None:
    """1000 pps of 64-byte frames: 512000 L2 bps, 672000 L1 bps."""
    s = report([stream()], OK)["streams"][0]
    assert s["length"] == 64 and s["pps"] == 1000 and s["bps_l2"] == 512000 and s["bps_l1"] == 672000


def test_bps_l1_rate() -> None:
    """1e6 L1 bps is 1e6 / (84 * 8) pps."""
    s = report([stream(rate=("bps_L1", 1e6))], OK)["streams"][0]
    assert s["pps"] == pytest.approx(1488.0952380952381)
    assert s["bps_l2"] == pytest.approx(761904.7619047619)


def test_bps_l2_rate() -> None:
    """1e6 L2 bps is 1e6 / (64 * 8) pps."""
    s = report([stream(rate=("bps_L2", 1e6))], OK)["streams"][0]
    assert s["pps"] == 1953.125 and s["bps_l1"] == 1312500


def test_percentage_has_no_absolute_rate() -> None:
    """A percentage gives only `percent`; the line speed is unknown offline."""
    r = report([stream(rate=("percentage", 25.0))], OK)
    s = r["streams"][0]
    assert s["percent"] == 25 and s["pps"] is None and s["bps_l1"] is None and s["bps_l2"] is None
    assert r["totals"]["percent"] == 25 and r["totals"]["pps"] == 0


def test_burst_totals() -> None:
    """A single burst of 1000 at 100 pps lasts 10 s; a multi burst of 10 x 5 is 50 packets."""
    single = stream(rate=("pps", 100.0), type="single_burst", total_pkts=1000)
    multi = stream(type="multi_burst", pkts_per_burst=10, ibg=0.0, count=5)
    a, b = report([single, multi], OK)["streams"]
    assert a["pkts"] == 1000 and a["burst_s"] == 10
    assert b["pkts"] == 50
    assert report([stream()], OK)["streams"][0]["pkts"] is None


def test_chain_to_stop_totals() -> None:
    """The burst_3st_1000pkt.py shape: S0 -> S1 -> S2, 3 x 10000 packets."""
    r = report([burst(10000, name="S0", nxt="S1"), burst(10000, name="S1", nxt="S2", self_start=False),
                burst(10000, name="S2", self_start=False)], OK)
    [c] = r["chains"]
    assert c["steps"] == ["S0", "S1", "S2"] and c["start"] == "S0" and c["end"] == "stop"
    assert c["loop_to"] is None and c["pkts"] == 30000 and r["totals"]["burst_pkts"] == 30000
    assert r["unreachable"] == []


def test_pcap_loop_is_a_bounded_cycle_not_an_error() -> None:
    """1 -> 2 -> 1 with action_count 3 is a loop of 6 packets."""
    r = report([burst(1, name=1, nxt=2), burst(1, name=2, nxt=1, self_start=False, loops=3)], OK)
    [c] = r["chains"]
    assert c["end"] == "loop" and c["loop_to"] == "1" and c["action_count"] == 3 and c["pkts"] == 6
    assert "error" not in str(r).lower()


def test_loop_forever() -> None:
    """action_count 0 on a loop has no packet total and a note."""
    r = report([burst(1, name="a", nxt="b"), burst(1, name="b", nxt="a", self_start=False)], OK)
    assert r["chains"][0]["pkts"] is None
    assert any("loops forever" in n for n in r["notes"])


def test_unreachable_stream_listed() -> None:
    """An enabled stream that does not self-start and that no next names is unreachable."""
    r = report([stream(name="a"), stream(name="b", self_start=False), stream(self_start=False)], OK)
    assert r["unreachable"] == ["b", "#2"]


def test_disabled_stream_not_in_totals() -> None:
    """Disabled streams are in the list but not in the totals, chains or unreachable."""
    r = report([stream(), stream(enabled=False, self_start=False)], OK)
    assert len(r["streams"]) == 2 and r["totals"]["pps"] == 1000 and r["unreachable"] == []
    assert len(r["chains"]) == 1


def test_ieee_1588_note() -> None:
    """IEEE 1588 on any stream adds the R13 note; without it there is none."""
    s = stream()
    assert not any("1588" in n for n in report([s], OK)["notes"])
    t = copy.deepcopy(s)
    t["flow_stats"] = {"enabled": True, "rule_type": "latency", "stream_id": 1, "ieee_1588": True}
    assert any("IEEE 1588" in n for n in report([t], OK)["notes"])


def test_round_trip_passed_through() -> None:
    """The round-trip result appears as given."""
    r = report([stream()], RoundTrip(False, ["stream 0 isg: 1 -> 2"]))
    assert r["round_trip"] == {"equal": False, "differences": ["stream 0 isg: 1 -> 2"]}


def test_next_to_a_disabled_stream_notes_the_server_refusal() -> None:
    """An enabled stream whose next names a disabled one: the server refuses the profile on validate (jammy
    2026-10-24), so the note names each such pair, an unnamed stream as #<index>; a disabled stream's own next
    is not checked by the server, so it adds nothing."""
    r = report([burst(1, name="a", nxt="b"), burst(1, name="b", nxt="c", enabled=False, self_start=False),
                burst(1, name="c", enabled=False, self_start=False)], OK)
    assert "the server refuses this profile on validate: a goes to disabled b" in r["notes"]
    assert not any("goes to disabled c" in n for n in r["notes"])
    assert not any("where the chain ends" in n for n in r["notes"])
    r = report([burst(1, nxt=1), burst(1, name=1, enabled=False, self_start=False)], OK)
    assert "the server refuses this profile on validate: #0 goes to disabled 1" in r["notes"]
    assert not any("refuses" in n for n in report([burst(1, name="a", nxt="b"),
                                                     burst(1, name="b", self_start=False)], OK)["notes"])


def test_disabled_next_pairs_names_the_enabled_stream_and_its_target() -> None:
    """S0 -> S1 with S1 disabled is one pair; both disabled, or S1 enabled, is none."""
    s0 = stream("S0", nxt="S1")
    assert disabled_next_pairs([s0, stream("S1", enabled=False, self_start=False)]) == [
        {"index": 0, "name": "S0", "next": "S1"}]
    assert disabled_next_pairs([stream("S0", nxt="S1", enabled=False), stream("S1", enabled=False)]) == []
    assert disabled_next_pairs([s0, stream("S1", self_start=False)]) == []


def test_disabled_next_pairs_with_an_int_target_and_an_unnamed_source() -> None:
    """An int-named target is keyed by type; an unnamed enabled stream has name None."""
    assert disabled_next_pairs([stream(nxt=1), stream(1, enabled=False)]) == [
        {"index": 0, "name": None, "next": 1}]
    assert disabled_next_pairs([stream(nxt="1"), stream(1, enabled=False)]) == []


def test_disabled_next_pairs_skips_a_next_that_names_nothing() -> None:
    """A hand-edited file can hold a next to no stream: no pair, no exception, no note."""
    streams = [stream("a", nxt="ghost")]
    assert disabled_next_pairs(streams) == [] and disabled_next(streams) == []


def test_disabled_next_pairs_skips_streams_it_cannot_read() -> None:
    """A stored, unvalidated stream that is not an object, lacks a bool `enabled`, or has a name or next that
    is not a str or int is skipped as source and as target, and nothing raises (final review I1)."""
    no_enabled = {k: v for k, v in stream("x", nxt="off").items() if k != "enabled"}
    streams: list[Any] = [stream("a", nxt="off"), stream("off", enabled=False), "junk", None, no_enabled,
                          dict(stream("y", nxt="off"), enabled="yes"), stream(["l"], nxt="off"),
                          stream("z", nxt={"n": 1}), stream(1.5, nxt="off"), stream("b", nxt="hidden"),
                          stream(["hidden"], enabled=False), dict(stream("hidden"), enabled=None)]
    assert disabled_next_pairs(streams) == [{"index": 0, "name": "a", "next": "off"}]


def test_disabled_next_note_text_is_unchanged() -> None:
    """The note is built from the pairs and reads as before."""
    assert disabled_next([stream(nxt=1), stream(1, enabled=False)]) == [
        "the server refuses this profile on validate: #0 goes to disabled 1"]
