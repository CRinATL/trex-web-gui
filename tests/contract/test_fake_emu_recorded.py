"""tests/fake/fake_emu.py against the E3 recording in tests/fixtures/p7/summary.json."""
import json
from collections.abc import Callable
from typing import Any

import pytest
from support import FIXTURES, e3_mismatches
from trex_web_gui.trexapi.rawrpc import RawRpc, RawRpcError

SUMMARY = json.loads((FIXTURES / "p7" / "summary.json").read_text())


def test_fake_replays_the_e3_recording(fake_emu: Callable[..., Any]) -> None:
    """Every recorded E3 step, sent to a fresh fake, gets the recorded outcome."""
    f = fake_emu()
    rpc = RawRpc(f.host, f.port, 2.0)
    try:
        rpc.api_sync("EMU", 1, 1)
        mismatches, _ = e3_mismatches(rpc.call, SUMMARY["e3"]["steps"])
    finally:
        rpc.close()
    assert mismatches == []


def test_duplicate_tunnel_text_names_the_asked_vport(fake_emu: Callable[..., Any]) -> None:
    """The recorded duplicate text names its tunnel; the fake names the one asked, not the recorded 213."""
    f = fake_emu("--foreign-ns", "100")
    rpc = RawRpc(f.host, f.port, 2.0)
    try:
        rpc.api_sync("EMU", 1, 1)
        with pytest.raises(RawRpcError) as e:
            rpc.call("ctx_add", {"tunnels": [{"vport": 77, "tci": [0, 0], "tpid": [0, 0]}]})
    finally:
        rpc.close()
    assert "77" in e.value.message and "213" not in e.value.message


def test_duplicate_client_text_names_the_asked_mac(fake_emu: Callable[..., Any]) -> None:
    """The recorded duplicate-client text quotes the MAC in colon form; the fake quotes the one asked."""
    f = fake_emu("--foreign-ns", "1", "--clients", "9")
    rpc = RawRpc(f.host, f.port, 2.0)
    try:
        rpc.api_sync("EMU", 1, 1)
        with pytest.raises(RawRpcError) as e:
            rpc.call("ctx_client_add", {"tun": {"vport": 0, "tci": [0, 0], "tpid": [0, 0]},
                                        "clients": [{"mac": [0, 0, 0, 0, 0, 5]}]})
    finally:
        rpc.close()
    assert "00:00:00:00:00:05" in e.value.message and "00:00:00:70:63:01" not in e.value.message


def test_client_remove_with_an_unknown_mac_removes_none(fake_emu: Callable[..., Any]) -> None:
    """[U:emu-client-remove-atomic] a known MAC listed before an unknown one stays."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2")
    rpc = RawRpc(f.host, f.port, 2.0)
    tun = {"vport": 0, "tci": [0, 0], "tpid": [0, 0]}
    try:
        rpc.api_sync("EMU", 1, 1)
        with pytest.raises(RawRpcError):
            rpc.call("ctx_client_remove", {"tun": tun, "macs": [[0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 9]]})
        listed = rpc.call("ctx_client_iter", {"tun": tun, "reset": True, "count": 255})
    finally:
        rpc.close()
    assert listed["data"] == [[0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 1]]


def test_a_refused_partial_add_bumps_the_epoch(fake_emu: Callable[..., Any]) -> None:
    """[U:emu-partial-write-epoch] an open cursor is interrupted by a ctx_add that added 5 and then failed."""
    f = fake_emu("--foreign-ns", "1")
    rpc = RawRpc(f.host, f.port, 2.0)
    new = {"vport": 5, "tci": [0, 0], "tpid": [0, 0]}
    old = {"vport": 0, "tci": [0, 0], "tpid": [0, 0]}
    try:
        rpc.api_sync("EMU", 1, 1)
        rpc.call("ctx_iter", {"reset": True, "count": 1})
        with pytest.raises(RawRpcError):
            rpc.call("ctx_add", {"tunnels": [new, old]})
        with pytest.raises(RawRpcError, match="interupted"):
            rpc.call("ctx_iter", {"reset": False, "count": 1})
    finally:
        rpc.close()


def test_a_client_counter_without_a_mac_is_an_error_not_a_dead_fake(fake_emu: Callable[..., Any]) -> None:
    """A client counter request that omits `mac` (or `tun`) answers a JSON-RPC error; the fake lives."""
    f = fake_emu("--foreign-ns", "1")
    rpc = RawRpc(f.host, f.port, 2.0)
    tun = {"vport": 0, "tci": [0, 0], "tpid": [0, 0]}
    body = {"meta": True, "zero": False, "mask": None, "clear": False}
    try:
        rpc.api_sync("EMU", 1, 1)
        with pytest.raises(RawRpcError):
            rpc.call("ipfix_c_cnt", {**body, "tun": tun})
        with pytest.raises(RawRpcError):
            rpc.call("ipfix_c_cnt", {**body, "mac": [0, 0, 0, 0, 0, 0]})
        rpc.call("ping", {})
    finally:
        rpc.close()
    assert f.proc.poll() is None
