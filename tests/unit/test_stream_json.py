"""The recorded shape of the worker's stream JSON (P4a Task 1; Task 2's models mirror these key sets).

Measured 2026-10-23 on udp_1pkt_simple.py, flow_latency.py, gui_example.yaml and two_streams.json. These
fixtures hold only continuous streams with an empty VM, so the burst and multi-burst mode keys and the
optional stream keys (ibg, next, random_seed, vm.cache) are not recorded here."""
import asyncio
from typing import Any

import pytest
from support import FIXTURES
from trex_web_gui import trexapi

P = FIXTURES / "profiles"
FILES = ["udp_1pkt_simple.py", "flow_latency.py", "gui_example.yaml", "two_streams.json"]

STREAM_KEYS = {"action_count", "core_id", "enabled", "flags", "flow_stats", "isg", "mode", "name", "packet",
               "self_start", "start_paused", "vm"}
MODE_KEYS = {"continuous": {"rate", "type"}}
FLOW_STATS_KEYS = {"(disabled)": {"enabled"},
                   "latency": {"enabled", "ieee_1588", "rule_type", "stream_id", "vxlan"},
                   "stats": {"enabled", "ieee_1588", "rule_type", "stream_id", "vxlan"}}
PACKET_KEYS = {"binary", "meta"}
VM_KEYS = {"instructions"}


@pytest.fixture(scope="module")
def streams() -> list[dict[str, Any]]:
    """Port-0 streams of the four fixture profiles, loaded through the worker."""
    out: list[dict[str, Any]] = []
    for f in FILES:
        out += asyncio.run(trexapi.load_profile(str(P / f), [0], [])).streams_by_port[0]
    return out


def union(dicts: list[dict[str, Any]]) -> set[str]:
    """The union of the keys of `dicts`."""
    return set().union(*(set(d) for d in dicts))


def test_stream_keys(streams: list[dict[str, Any]]) -> None:
    """The union of the stream-level keys is the recorded set."""
    assert union(streams) == STREAM_KEYS


def test_mode_keys_per_type(streams: list[dict[str, Any]]) -> None:
    """The union of the mode keys, per mode type, is the recorded set."""
    modes = [s["mode"] for s in streams]
    assert {m["type"] for m in modes} == set(MODE_KEYS)
    for t, keys in MODE_KEYS.items():
        assert union([m for m in modes if m["type"] == t]) == keys


def test_flow_stats_keys_per_rule_type(streams: list[dict[str, Any]]) -> None:
    """The union of the flow_stats keys, per rule type (a disabled rule has none), is the recorded set."""
    fs = [s["flow_stats"] for s in streams]
    assert {f.get("rule_type", "(disabled)") for f in fs} == set(FLOW_STATS_KEYS)
    for t, keys in FLOW_STATS_KEYS.items():
        assert union([f for f in fs if f.get("rule_type", "(disabled)") == t]) == keys


def test_packet_and_vm_keys(streams: list[dict[str, Any]]) -> None:
    """The union of the packet keys and of the vm keys are the recorded sets."""
    assert union([s["packet"] for s in streams]) == PACKET_KEYS
    assert union([s["vm"] for s in streams]) == VM_KEYS


def test_random_seed_is_absent_when_zero(streams: list[dict[str, Any]]) -> None:
    """No stream carries random_seed, as STLStream writes it only when non-zero (spec section 9 risk 5)."""
    assert all("random_seed" not in s for s in streams)
