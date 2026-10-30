"""A bare JSON-RPC client for the TRex command port, used by the daemon half only.

It exists to read who owns each port before a daemon Stop (D1). It is not the vendored STLClient: it never
connects a session, acquires or changes anything, and every reply is decoded through `wire.inflate`, so a
hostile server cannot make it allocate more than RPC_REPLY_MAX_BYTES."""
import itertools
import json
from typing import Any

import zmq.error
from zmq.constants import SocketOption, SocketType
from zmq.sugar.context import Context

from ..monitor.wire import MAXMSGSIZE, Undecodable, inflate
from .errors import ServerUnreachable, TrexApiError

RPC_REPLY_MAX_BYTES = 1024 * 1024
MAX_PORTS = 256


class RawRpcError(TrexApiError):
    """The server answered with a JSON-RPC `error`, or with a reply this client refuses (code 0)."""
    def __init__(self, code: int, message: str) -> None:
        """Keep the server's numeric code as .code beside the message."""
        super().__init__(message)
        self.code = code


class RawRpc:
    """One REQ socket to host:port on its own zmq context. Blocking and single-threaded: one caller at a time.

    A call that gets no reply in `timeout_s` raises ServerUnreachable and rebuilds the socket, because a REQ
    socket that has sent without receiving cannot send again."""

    def __init__(self, host: str, port: int, timeout_s: float) -> None:
        """Open the socket (the connection itself is made lazily by libzmq)."""
        self._endpoint = f"tcp://{host}:{port}"
        self._timeout_ms = max(1, int(timeout_s * 1000))
        self._ctx = Context()
        self._sock: Any = None
        self._ids = itertools.count(1)
        self._api_h: str | None = None
        self._open()

    def _open(self) -> None:
        """A fresh REQ socket with the size cap, the timeouts and no linger."""
        sock = self._ctx.socket(SocketType.REQ)
        sock.setsockopt(SocketOption.LINGER, 0)
        sock.setsockopt(SocketOption.MAXMSGSIZE, MAXMSGSIZE)
        sock.setsockopt(SocketOption.SNDTIMEO, self._timeout_ms)
        sock.setsockopt(SocketOption.RCVTIMEO, self._timeout_ms)
        try:
            sock.connect(self._endpoint)
        except zmq.error.ZMQError as exc:
            sock.close(0)
            raise ServerUnreachable(f"cannot reach {self._endpoint}: {exc}") from exc
        self._sock = sock

    def _rebuild(self) -> None:
        """Drop the socket and open a new one."""
        if self._sock is not None:
            self._sock.close(0)
            self._sock = None
        self._open()

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """One request and its `result`. The api_h from api_sync() rides along in `params`.

        ServerUnreachable when nothing answers in time (the socket is rebuilt); RawRpcError for an `error`
        reply or one that is oversized, not zlib, not JSON or not an object."""
        body: dict[str, Any] = dict(params or {})
        if self._api_h is not None and method != "api_sync_v2":
            body["api_h"] = self._api_h
        raw = json.dumps({"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                          "params": body}).encode("utf-8")
        try:
            self._sock.send(raw)
            reply = self._sock.recv()
        except zmq.error.ZMQError as exc:
            self._rebuild()
            raise ServerUnreachable(f"no answer to {method} from {self._endpoint}: {exc}") from exc
        try:
            doc = json.loads(inflate(reply, RPC_REPLY_MAX_BYTES))
        except Undecodable as exc:
            raise RawRpcError(0, f"reply to {method} refused: {exc.reason}") from None
        except (ValueError, RecursionError):
            raise RawRpcError(0, f"reply to {method} is not JSON") from None
        if not isinstance(doc, dict):
            raise RawRpcError(0, f"reply to {method} is not an object")
        err = doc.get("error")
        if err is not None:
            if not isinstance(err, dict):
                raise RawRpcError(0, f"reply to {method} has a malformed error")
            code = err.get("code")
            text = err.get("specific_err") or err.get("message") or ""
            raise RawRpcError(code if isinstance(code, int) else 0, str(text))
        return doc.get("result")

    def api_sync(self, name: str, major: int, minor: int) -> str:
        """`api_sync_v2` for the API `name` major.minor; keeps and returns the server's api_h."""
        res = self.call("api_sync_v2", {"name": name, "major": major, "minor": minor})
        api_h = res.get("api_h") if isinstance(res, dict) else None
        if not isinstance(api_h, str):
            raise RawRpcError(0, "api_sync_v2 gave no api_h")
        self._api_h = api_h
        return api_h

    def close(self) -> None:
        """Close the socket and destroy the context."""
        if self._sock is not None:
            self._sock.close(0)
            self._sock = None
        self._ctx.destroy(linger=0)


def port_owners(host: str, port: int, timeout_s: float = 2.0) -> dict[int, str]:
    """{port id: owner} read from the TRex command port at host:port; a free port's owner is "".

    `api_sync("STL", 5, 1)`, `get_system_info` for `port_count`, then `get_port_status` (non-blocking) per
    port. Any failure raises (ServerUnreachable or RawRpcError), which a caller reads as 'owners unknown'."""
    rpc = RawRpc(host, port, timeout_s)
    try:
        rpc.api_sync("STL", 5, 1)
        info = rpc.call("get_system_info")
        count = info.get("port_count") if isinstance(info, dict) else None
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_PORTS:
            raise RawRpcError(0, "get_system_info gave no usable port_count")
        owners: dict[int, str] = {}
        for pid in range(count):
            status = rpc.call("get_port_status", {"port_id": pid, "block": False})
            owner = status.get("owner") if isinstance(status, dict) else None
            if not isinstance(owner, str):
                raise RawRpcError(0, f"get_port_status for port {pid} gave no owner")
            owners[pid] = owner
        return owners
    finally:
        rpc.close()
