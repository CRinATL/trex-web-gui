"""Offline simulation of a profile: the round-trip result plus the server's own rate arithmetic.

The "GUI computes no rate" rule (spec §6.1) concerns live statistics, which come from the server; this
calculator predicts a profile that is not running, so it cannot read them and does the arithmetic itself.
It is pure: the round trip is an argument (its worker call is `trexapi.round_trip`), and nothing here
imports `trex` or `scapy`."""
import base64
from typing import Any, Literal, TypedDict

from trex_web_gui.trexapi import RoundTrip

# The server adds the FCS to the template length (src/stx/stl/trex_stl_stream.cpp:281-282 "pkt_size += 4").
FCS_BYTES = 4
# Preamble and inter-frame gap on top of a frame (src/stx/stl/trex_stl_stream.h:251-252 "m_bps_L1").
L1_OVERHEAD = 20


class SimStream(TypedDict):
    """One stream's predicted figures; None where the offline data cannot give one."""

    index: int
    name: str | int | None
    length: int                  # template bytes plus the FCS
    mode: str
    rate_type: str
    rate_value: float
    pps: float | None
    bps_l1: float | None
    bps_l2: float | None
    percent: float | None
    pkts: int | None
    burst_s: float | None
    enabled: bool


class Chain(TypedDict):
    """A run of streams linked by `next`, from a self-starting stream."""

    start: str
    steps: list[str]
    end: Literal["stop", "loop"]
    loop_to: str | None
    action_count: int
    pkts: int | None


class Totals(TypedDict):
    """Sums over the enabled streams: an upper bound, since a chain runs one stream at a time."""

    pps: float
    bps_l1: float
    bps_l2: float
    percent: float
    burst_pkts: int


class RoundTripView(TypedDict):
    """`RoundTrip` as the report carries it."""

    equal: bool
    differences: list[str]


class SimReport(TypedDict):
    """What the Simulate dialog shows."""

    round_trip: RoundTripView
    streams: list[SimStream]
    totals: Totals
    chains: list[Chain]
    unreachable: list[str]
    notes: list[str]


def label(streams: list[dict[str, Any]], i: int) -> str:
    """The stream's name as text, `#<index>` when it has none."""
    name = streams[i].get("name")
    return f"#{i}" if name is None else str(name)


def sim_stream(i: int, s: dict[str, Any]) -> SimStream:
    """The figures of stream `i`: rates from the server's formulas with L = template length + 4."""
    length = len(base64.b64decode(s["packet"]["binary"])) + FCS_BYTES
    mode = s["mode"]
    rate = mode["rate"]
    kind, value = rate["type"], float(rate["value"])
    pps: float | None = None
    percent: float | None = None
    if kind == "pps":
        pps = value
    elif kind == "bps_L1":
        pps = value / ((length + L1_OVERHEAD) * 8)
    elif kind == "bps_L2":
        pps = value / (length * 8)
    else:
        percent = value                 # the line speed is unknown offline
    pkts: int | None = None
    if mode["type"] == "single_burst":
        pkts = mode["total_pkts"]
    elif mode["type"] == "multi_burst":
        pkts = mode["pkts_per_burst"] * mode["count"]
    burst_s: float | None = None
    if pkts is not None and pps is not None:
        burst_s = pkts / pps
        if mode["type"] == "multi_burst":
            burst_s += (mode["count"] - 1) * mode["ibg"] / 1e6
    return {
        "index": i, "name": s.get("name"), "length": length, "mode": mode["type"],
        "rate_type": kind, "rate_value": value, "pps": pps,
        "bps_l1": None if pps is None else pps * (length + L1_OVERHEAD) * 8,
        "bps_l2": None if pps is None else pps * length * 8,
        "percent": percent, "pkts": pkts, "burst_s": burst_s, "enabled": s["enabled"],
    }


def total(parts: list[int | None]) -> int | None:
    """The sum of `parts`, None when any is None."""
    return None if any(p is None for p in parts) else sum(p for p in parts if p is not None)


def chains_of(streams: list[dict[str, Any]], sims: list[SimStream], notes: list[str]) -> list[Chain]:
    """One chain per enabled self-starting stream, following `next` (matched by name and type) until a
    stream with none, a disabled stream (which the server refuses, `disabled_next`) or a stream already
    visited (a loop)."""
    by_name = {(type(s["name"]), s["name"]): i for i, s in enumerate(streams) if s.get("name") is not None}
    chains: list[Chain] = []
    for start, s in enumerate(streams):
        if not (s["enabled"] and s["self_start"]):
            continue
        order = [start]
        end: Literal["stop", "loop"] = "stop"
        loop_to: int | None = None
        while (nxt := streams[order[-1]].get("next")) is not None:
            j = by_name[(type(nxt), nxt)]
            if not streams[j]["enabled"]:          # refused on validate: report() notes it (disabled_next)
                break
            if j in order:
                end, loop_to = "loop", j
                break
            order.append(j)
        steps = [label(streams, i) for i in order]
        count = streams[order[-1]]["action_count"] if end == "loop" else 0
        pkts: int | None
        if end == "stop":
            pkts = total([sims[i]["pkts"] for i in order])
        elif count == 0:
            pkts = None
            notes.append(f"the chain from {steps[0]} loops forever (action_count 0)")
        else:
            at = order.index(loop_to) if loop_to is not None else 0
            before = total([sims[i]["pkts"] for i in order[:at]])
            body = total([sims[i]["pkts"] for i in order[at:]])
            pkts = None if body is None or before is None else before + body * count
        chains.append({
            "start": steps[0], "steps": steps, "end": end,
            "loop_to": None if loop_to is None else label(streams, loop_to),
            "action_count": count, "pkts": pkts,
        })
    return chains


class DisabledNext(TypedDict):
    """An enabled stream (`index`, `name`) whose `next` names a disabled stream."""

    index: int
    name: str | int | None
    next: str | int


def disabled_next_pairs(streams: list[Any]) -> list[DisabledNext]:
    """One entry per enabled stream whose `next` names a disabled stream: the server refuses such a profile
    on validate ("stream 127 is pointing to disabled stream 128", jammy 2026-10-24; the compiler checks only
    enabled streams, src/stx/stl/trex_stl_streams_compiler.cpp:335 "!next->m_enabled"). Names are keyed by
    (type, value); a `next` that names no stream (a hand-edited file) adds nothing. The stream page and a
    write's reply pass stored streams that were never validated, so a stream this cannot read (`_readable`)
    is skipped as source and as target, and this never raises (final review I1)."""
    ok = [_readable(s) for s in streams]
    by_name = {(type(s["name"]), s["name"]): i for i, s in enumerate(streams)
               if ok[i] and s.get("name") is not None}
    pairs: list[DisabledNext] = []
    for i, s in enumerate(streams):
        if not ok[i] or not s["enabled"] or (nxt := s.get("next")) is None:
            continue
        j = by_name.get((type(nxt), nxt))
        if j is not None and not streams[j]["enabled"]:
            pairs.append({"index": i, "name": s.get("name"), "next": nxt})
    return pairs


def _readable(s: object) -> bool:
    """`s` is an object with a bool `enabled`, and its `name` and `next` are each absent, null, a str or an
    int."""
    return (isinstance(s, dict) and type(s.get("enabled")) is bool
            and all(s.get(k) is None or type(s[k]) in (str, int) for k in ("name", "next")))


def disabled_next(streams: list[dict[str, Any]]) -> list[str]:
    """A note for each pair of `disabled_next_pairs`, naming the stream by `label`."""
    by_name = {(type(s["name"]), s["name"]): i for i, s in enumerate(streams) if s.get("name") is not None}
    return [f"the server refuses this profile on validate: {label(streams, p['index'])} goes to disabled "
            f"{label(streams, by_name[(type(p['next']), p['next'])])}" for p in disabled_next_pairs(streams)]


def report(streams: list[dict[str, Any]], rt: RoundTrip) -> SimReport:
    """The simulation of `streams` (parsed, checked): per-stream rates, totals over the enabled streams,
    the chains with their packet totals, the enabled streams nothing starts, and notes on what the
    figures leave out. `rt` is the worker's round-trip result, passed through."""
    sims = [sim_stream(i, s) for i, s in enumerate(streams)]
    on = [m for m in sims if m["enabled"]]
    notes: list[str] = disabled_next(streams)
    chains = chains_of(streams, sims, notes)
    named = {(type(s["next"]), s["next"]) for s in streams if s["enabled"] and s.get("next") is not None}
    unreachable = [label(streams, i) for i, s in enumerate(streams) if s["enabled"] and not s["self_start"]
                   and (s.get("name") is None or (type(s["name"]), s["name"]) not in named)]
    totals: Totals = {
        "pps": sum(m["pps"] or 0.0 for m in on), "bps_l1": sum(m["bps_l1"] or 0.0 for m in on),
        "bps_l2": sum(m["bps_l2"] or 0.0 for m in on), "percent": sum(m["percent"] or 0.0 for m in on),
        "burst_pkts": sum(c["pkts"] or 0 for c in chains),
    }
    if len(on) > 1:
        notes.append("totals add every enabled stream, an upper bound: a chain runs one stream at a time, "
                     "so the server's max_pps can be lower")
    if any(c["end"] == "loop" and c["action_count"] > 0 for c in chains):
        notes.append("a loop's packet total is body x action_count; the formula is checked on the guest")
    if any(s["vm"]["instructions"] for s in streams if s["enabled"]):
        notes.append("a VM that changes the packet length is not reflected: "
                     "the server uses its expected length")
    if any(s["flow_stats"].get("vxlan") or s["flow_stats"].get("ieee_1588") for s in streams):
        notes.append("VXLAN and IEEE 1588 do not reach the server (R13)")
    return {"round_trip": {"equal": rt.equal, "differences": list(rt.differences)}, "streams": sims,
            "totals": totals, "chains": chains, "unreachable": unreachable, "notes": notes}
