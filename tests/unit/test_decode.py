import base64
import json
import struct
import threading
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui import trexapi
from trex_web_gui.trexapi import decode, scapy_exec

trexapi.load()      # Scapy is importable only after the vendored tree is on the path

P3C = Path(__file__).resolve().parent.parent / "fixtures" / "p3c"
FRAMES = json.loads((P3C / "summary.json").read_text())["frames"]


def _raw(kind: str) -> bytes:
    """The recorded frame of this kind, base64-decoded from fixtures/p3c/summary.json."""
    return base64.b64decode(FRAMES[kind])


@pytest.fixture
def scapy_thread() -> Any:
    """Yields nothing, for tests that decode on the scapy executor as production does; shuts it down after."""
    yield
    scapy_exec.shutdown()


def test_summary_udp() -> None:
    s = decode.summary(_raw("udp"))
    assert (s.src, s.dst, s.proto) == ("16.0.0.1", "48.0.0.1", "UDP")
    assert "UDP 16.0.0.1:1025 > 48.0.0.1:12" in s.info


def test_summary_arp() -> None:
    s = decode.summary(_raw("arp"))
    assert (s.src, s.dst, s.proto) == ("1.1.1.1", "2.2.2.2", "ARP")
    assert "who has 2.2.2.2" in s.info


def test_summary_vlan() -> None:
    s = decode.summary(_raw("vlan"))
    assert s.proto == "ICMP" and "802.1Q" in s.info
    assert (s.src, s.dst) == ("1.1.1.1", "2.2.2.2")


def test_summary_ipv6_nd() -> None:
    s = decode.summary(_raw("ipv6_nd"))
    assert s.proto == "ICMPv6ND_NS" and "ICMPv6ND_NS" in s.info
    assert ":" in s.src and ":" in s.dst


def test_decode_truncated_frame_does_not_raise() -> None:
    # 30 zero bytes dissect as Dot3 + padding in the vendored Scapy 2.4.3, not Ether: the row says what
    # Scapy dissected.
    from scapy.all import Ether
    layers = [type(layer).__name__ for layer in Ether(_raw("truncated")).iterpayloads()]
    s = decode.summary(_raw("truncated"))
    assert s.proto == layers[0] == "Dot3"
    # 5 bytes are shorter than an Ethernet header, so the dissection really raises struct.error: summary
    # stays "?" with the exception text, while tree() lets it propagate
    # (tree()'s docstring: the caller catches it)
    short = bytes(5)
    bad = decode.summary(short)
    assert (bad.src, bad.dst, bad.proto) == ("", "", "?") and "unpack requires a buffer" in bad.info
    with pytest.raises(struct.error):
        decode.tree(short)


def test_tree_matches_recorded(scapy_thread: None) -> None:
    recorded = json.loads((P3C / "trees.json").read_text())["udp"]
    assert scapy_exec.run(decode.tree, _raw("udp")).result() == recorded


def test_decode_runs_on_the_scapy_thread(scapy_thread: None) -> None:
    names = scapy_exec.run(lambda: threading.current_thread().name).result()
    assert names.startswith("scapy") and names != threading.current_thread().name
    seen: list[str] = []
    real = decode.summary

    def spy(raw: bytes) -> decode.Summary:
        seen.append(threading.current_thread().name)
        return real(raw)
    s = scapy_exec.run(spy, _raw("udp")).result()
    assert s.proto == "UDP" and seen == [names]


def test_warm_up_constructs_once(monkeypatch: pytest.MonkeyPatch, scapy_thread: None,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    import time
    real = decode._construct
    built: list[str] = []

    def counting() -> Any:
        built.append(threading.current_thread().name)
        return real()
    monkeypatch.setattr(decode, "_construct", counting)
    monkeypatch.setattr(decode, "_service", None)
    t0 = time.monotonic()
    scapy_exec.warm_up()
    first = time.monotonic() - t0
    scapy_exec.warm_up()
    with capsys.disabled():
        print(f"\nScapy_service first warm_up: {first:.3f} s")
    assert len(built) == 1 and built[0].startswith("scapy")
    assert decode.service() is decode.service()
    scapy_exec.shutdown()
    scapy_exec.warm_up()           # a new executor keeps the singleton
    assert len(built) == 1


def test_summary_dissects_at_most_summary_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    # A capture row names the headers at the frame's start; dissecting a jumbo zero-filled IPv4 frame whole
    # cost 0.45 s against 0.10 s for its first 2048 bytes (gemini-security-review 2026-11-01).
    import scapy.all
    given: list[int] = []
    real_ether = scapy.all.Ether

    def recording_ether(data: bytes) -> Any:
        given.append(len(data))
        return real_ether(data)
    monkeypatch.setattr(scapy.all, "Ether", recording_ether)
    raw = bytes.fromhex("020000000002" "020000000001" "0800").ljust(9238, b"\x00")
    decode.summary(raw)
    assert given == [decode.SUMMARY_BYTES]
    udp = _raw("udp")
    assert decode.summary(udp) == decode.summary(udp[:decode.SUMMARY_BYTES])
