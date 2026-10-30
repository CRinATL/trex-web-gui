"""Bounded PUB decoding (monitor/wire.py): hostile frames are refused without allocating their claim."""
import json
import struct
import tracemalloc
import zlib

import pytest
from trex_web_gui.monitor.wire import MAGIC, MAX_FRAME_BYTES, Frame, Undecodable, decode_frame, inflate


def zipb(b: bytes) -> bytes:
    """A body in the TRex wire framing."""
    return struct.pack(">II", MAGIC, len(b)) + zlib.compress(b)


def test_decode_plain_and_zipped():
    body = json.dumps({"name": "trex-global", "data": {"m_tx_bps": 5}}).encode()
    for raw in (body, zipb(body)):
        assert decode_frame(raw) == Frame("trex-global", {"m_tx_bps": 5}, len(raw))


def test_decode_trigger_is_none():
    assert decode_frame(b"{}") is None


def test_inflate_refuses_oversize_claim():
    raw = struct.pack(">II", MAGIC, 1 << 30) + zlib.compress(b"x")
    tracemalloc.start()
    try:
        with pytest.raises(Undecodable) as e:
            inflate(raw, MAX_FRAME_BYTES)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert e.value.reason == "oversize"
    assert peak < 8 * 1024 * 1024


def test_inflate_refuses_zlib_bomb():
    raw = struct.pack(">II", MAGIC, 100) + zlib.compress(b"\0" * (16 << 20))   # lies about its length
    tracemalloc.start()
    try:
        with pytest.raises(Undecodable) as e:
            inflate(raw, MAX_FRAME_BYTES)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert e.value.reason == "oversize"
    assert peak < 8 * 1024 * 1024 + MAX_FRAME_BYTES


def test_inflate_refuses_a_plain_body_over_limit():
    with pytest.raises(Undecodable) as e:
        inflate(b"a" * 11, 10)
    assert e.value.reason == "oversize"
    assert inflate(b"a" * 10, 10) == b"a" * 10


@pytest.mark.parametrize("raw,reason", [
    (b"not json", "json"),
    (b"[1,2]", "shape"),
    (b'{"data":1}', "shape"),
    (b'{"name":"\xff"}', "utf8"),
])
def test_decode_refuses_non_objects(raw, reason):
    with pytest.raises(Undecodable) as e:
        decode_frame(raw)
    assert e.value.reason == reason


def test_inflate_reports_a_truncated_zlib_stream_as_zlib():
    full = zlib.compress(json.dumps({"name": "a", "data": list(range(500))}).encode())
    cut = struct.pack(">II", MAGIC, 100) + full[: len(full) // 2]
    with pytest.raises(Undecodable) as e:
        inflate(cut, MAX_FRAME_BYTES)
    assert e.value.reason == "zlib"
    assert inflate(struct.pack(">II", MAGIC, 100) + full, MAX_FRAME_BYTES).startswith(b'{"name"')
