"""backend/trex_web_gui/trexapi/rawrpc.py: the bare REQ client the daemon half uses to read port owners."""
import json
import struct
import threading
import zlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import zmq
from support import free_port_pair
from trex_web_gui.monitor.wire import MAGIC
from trex_web_gui.trexapi import rawrpc
from trex_web_gui.trexapi.errors import ServerUnreachable
from trex_web_gui.trexapi.rawrpc import RawRpc, RawRpcError, port_owners


def zipped(doc: dict[str, Any]) -> bytes:
    """A JSON-RPC reply in TRex's wire framing: magic, length, zlib body."""
    raw = json.dumps(doc).encode()
    return struct.pack(">II", MAGIC, len(raw)) + zlib.compress(raw)


class Rep:
    """A REP socket on a free port that answers each request with `handler(request bytes) -> reply bytes`."""

    def __init__(self, handler: Callable[[bytes], bytes]) -> None:
        """Bind and start serving on a thread."""
        self.port = free_port_pair()[0]
        self.requests: list[dict[str, Any]] = []
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.REP)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.bind(f"tcp://127.0.0.1:{self.port}")
        self._handler = handler
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Answer requests until closed."""
        while not self._stop.is_set():
            if self._sock.poll(50):
                raw = self._sock.recv()
                self.requests.append(json.loads(raw))
                self._sock.send(self._handler(raw))

    def close(self) -> None:
        """Stop serving and release the socket."""
        self._stop.set()
        self._thread.join(5)
        self._sock.close(0)
        self._ctx.destroy(linger=0)


@pytest.fixture
def make_rep() -> Iterator[Callable[[Callable[[bytes], bytes]], Rep]]:
    """A factory for Rep servers, all closed at teardown."""
    made: list[Rep] = []

    def make(handler: Callable[[bytes], bytes]) -> Rep:
        """Start one."""
        r = Rep(handler)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_call_round_trip_zipped(make_rep: Any) -> None:
    """api_sync keeps the api_h and later calls carry it; a zlib-framed reply is decoded."""
    def handler(raw: bytes) -> bytes:
        """Answer api_sync_v2 with an api_h and anything else with the params it received."""
        req = json.loads(raw)
        if req["method"] == "api_sync_v2":
            return zipped({"jsonrpc": "2.0", "id": req["id"], "result": {"api_h": "abc"}})
        return zipped({"jsonrpc": "2.0", "id": req["id"], "result": req["params"]})
    rep = make_rep(handler)
    rpc = RawRpc("127.0.0.1", rep.port, 2.0)
    try:
        assert rpc.api_sync("STL", 5, 1) == "abc"
        assert rep.requests[0]["params"] == {"name": "STL", "major": 5, "minor": 1}
        assert rpc.call("echo", {"x": 1}) == {"x": 1, "api_h": "abc"}
    finally:
        rpc.close()


def test_error_reply_is_a_raw_rpc_error(make_rep: Any) -> None:
    """A JSON-RPC error object becomes RawRpcError carrying the server's code and text."""
    rep = make_rep(lambda raw: json.dumps({"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32601, "message": "Method not found", "specific_err": "no such thing"}}).encode())
    rpc = RawRpc("127.0.0.1", rep.port, 2.0)
    try:
        with pytest.raises(RawRpcError) as e:
            rpc.call("nope")
        assert e.value.code == -32601 and "no such thing" in e.value.message
    finally:
        rpc.close()


def test_timeout_then_next_call_works(make_rep: Any) -> None:
    """A reply that never comes raises ServerUnreachable and rebuilds the socket, so the next call works."""
    first, release = threading.Event(), threading.Event()

    def handler(raw: bytes) -> bytes:
        """Hold the first request until the test lets go, answer the rest at once."""
        req = json.loads(raw)
        if not first.is_set():
            first.set()
            release.wait(5)
        return json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": "ok"}).encode()
    rep = make_rep(handler)
    rpc = RawRpc("127.0.0.1", rep.port, 0.3)
    try:
        with pytest.raises(ServerUnreachable):
            rpc.call("slow")
        release.set()
        assert rpc.call("fast") == "ok"
    finally:
        rpc.close()


def test_reply_bomb_is_refused(make_rep: Any) -> None:
    """A tiny zlib body that claims 100 bytes but inflates to 16 MiB, and one that claims 1 GiB, are refused
    without being inflated; the client still works afterwards."""
    bomb = struct.pack(">II", MAGIC, 100) + zlib.compress(b"\0" * (16 << 20))
    huge = struct.pack(">II", MAGIC, 1 << 30) + zlib.compress(b"x")
    replies = iter([bomb, huge, b'{"result": 1, "id": 1}'])
    rep = make_rep(lambda raw: next(replies))
    rpc = RawRpc("127.0.0.1", rep.port, 2.0)
    try:
        for _ in range(2):
            with pytest.raises(RawRpcError) as e:
                rpc.call("x")
            assert e.value.code == 0 and "refused" in e.value.message
        assert rpc.call("x") == 1
    finally:
        rpc.close()


def test_oversized_reply_is_refused_by_the_socket(make_rep: Any) -> None:
    """A reply over the 1 MiB decode cap is refused whether it is framed (inflate) or plain."""
    plain = json.dumps({"result": "x" * (rawrpc.RPC_REPLY_MAX_BYTES + 10), "id": 1}).encode()
    rep = make_rep(lambda raw: plain)
    rpc = RawRpc("127.0.0.1", rep.port, 2.0)
    try:
        with pytest.raises(RawRpcError, match="oversize"):
            rpc.call("x")
    finally:
        rpc.close()


def test_port_owners_sees_a_foreign_owner(fake_trex: Any, foreign_client: Any) -> None:
    """Against the fake TRex, port 0 owned by another console reads back as its name; a free port as ''."""
    f = fake_trex()
    c = foreign_client(f, "console-b")
    c.acquire(ports=[0], force=False)
    assert port_owners(f.host, f.rpc_port) == {0: "console-b", 1: ""}


def test_port_owners_on_a_dead_port_raises() -> None:
    """Nothing listening is ServerUnreachable, which the daemon session reads as 'owners unknown'."""
    with pytest.raises(ServerUnreachable):
        port_owners("127.0.0.1", free_port_pair()[0], timeout_s=0.3)
