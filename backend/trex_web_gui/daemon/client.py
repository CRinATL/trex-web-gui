"""Bounded JSON-RPC client for the TRex daemon (`trex_server.py`, port 8090).

One POST per call through `http.client.HTTPConnection` directly: no proxy is consulted, no redirect is
followed, and any status other than 200 is `DaemonUnreachable`. The body is read against one total deadline
and capped at DAEMON_MAX_BYTES, so a slow or oversized peer cannot hold a caller."""
import base64
import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..trexapi.errors import TrexApiError

DEFAULT_PORT = 8090
CALL_TIMEOUT_S = 10.0
PUSH_TIMEOUT_S = 30.0
DAEMON_MAX_BYTES = 8 * 1024 * 1024
LOG_TAIL_BYTES = 262144
# prepare_astf_profile imports a Python file as root (trex_server.py:113-122); the rest are not built (§2).
FORBIDDEN = frozenset({"kill_all_trexes", "reserve_trex", "cancel_reservation", "prepare_astf_profile"})
_READ_CHUNK = 65536


@dataclass(frozen=True)
class StfOptions:
    """The typed fields of a stateful (-f) run. DaemonSession and the routes check bounds."""
    profile: str
    d: int
    m: float = 1.0
    l: int = 0  # noqa: E741 - TRex's own flag name
    rx_check: int = 0


class DaemonFault(TrexApiError):
    """The daemon answered with a JSON-RPC `error` object."""
    def __init__(self, code: int, message: str) -> None:
        """Keep the daemon's numeric code as .code beside the message."""
        super().__init__(message)
        self.code = code


class DaemonUnreachable(TrexApiError):
    """No usable answer: refused, reset, timed out, non-200, redirected, oversized or not JSON."""


def _b64_text(value: Any) -> str:
    """Decode a base64 result string to text; anything else is a protocol error."""
    if not isinstance(value, str):
        raise DaemonUnreachable("daemon sent a non-string where base64 was expected")
    try:
        return base64.b64decode(value).decode("utf-8", errors="replace")
    except ValueError as exc:
        raise DaemonUnreachable(f"daemon sent invalid base64: {exc}") from exc


def _tail(text: str) -> str:
    """The last LOG_TAIL_BYTES bytes of text (decoded again, a split character is replaced)."""
    raw = text.encode("utf-8")
    if len(raw) <= LOG_TAIL_BYTES:
        return text
    return raw[-LOG_TAIL_BYTES:].decode("utf-8", errors="replace")


class DaemonClient:
    """JSON-RPC calls to one daemon. Stateless: each call opens and closes its own connection."""

    def __init__(self, host: str, port: int, timeout_s: float = CALL_TIMEOUT_S) -> None:
        """Remember where the daemon is and the default per-call deadline."""
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    def call(self, method: str, *, timeout_s: float | None = None, **params: Any) -> Any:
        """POST one JSON-RPC request and return its `result`.

        Raises ValueError for a FORBIDDEN method (before any I/O), DaemonFault for an `error` object and
        DaemonUnreachable for everything else that goes wrong on the way."""
        if method in FORBIDDEN:
            raise ValueError(f"daemon method {method!r} is not allowed")
        total = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + total
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
        conn = http.client.HTTPConnection(self.host, self.port, timeout=total)
        # Hard backstop for what settimeout cannot bound (a header drip): shut the socket at the deadline.
        guard = threading.Timer(total, self._abort, args=(conn,))
        guard.daemon = True
        guard.start()
        try:
            conn.request("POST", "/", body=payload, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise DaemonUnreachable(f"daemon answered HTTP {resp.status}")
            body = self._read_body(conn, resp, deadline)
        except (OSError, http.client.HTTPException) as exc:
            raise DaemonUnreachable(f"daemon {self.host}:{self.port} unreachable: {exc}") from exc
        finally:
            guard.cancel()
            conn.close()
        try:
            doc = json.loads(body)
        except ValueError as exc:
            raise DaemonUnreachable("daemon sent a non-JSON body") from exc
        if not isinstance(doc, dict):
            raise DaemonUnreachable("daemon sent a non-object reply")
        err = doc.get("error")
        if err is not None:
            if not isinstance(err, dict):
                raise DaemonUnreachable("daemon sent a malformed error")
            code = err.get("code")
            raise DaemonFault(code if isinstance(code, int) else 0, str(err.get("message", "")))
        if "result" not in doc:
            raise DaemonUnreachable("daemon reply has no result")
        return doc["result"]

    @staticmethod
    def _abort(conn: http.client.HTTPConnection) -> None:
        """Shut the connection's socket down so a blocked read returns at once."""
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    @staticmethod
    def _read_body(conn: http.client.HTTPConnection, resp: http.client.HTTPResponse,
                   deadline: float) -> bytes:
        """Read the body in chunks under the total deadline and the DAEMON_MAX_BYTES cap."""
        chunks: list[bytes] = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("deadline exceeded")
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            # read1: one receive's worth, so a one-byte drip cannot make a single read outlast the deadline.
            chunk = resp.read1(_READ_CHUNK)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > DAEMON_MAX_BYTES:
                raise DaemonUnreachable("response too large")
            chunks.append(chunk)

    # --- wrappers -------------------------------------------------------------------------------------------

    def connectivity_check(self) -> bool:
        """True when the daemon answers."""
        return bool(self.call("connectivity_check"))

    def version(self) -> str:
        """`get_trex_version`, decoded (trex_server.py:207-216)."""
        return _b64_text(self.call("get_trex_version"))

    def config_text(self) -> str:
        """`get_trex_config`: the daemon's /etc/trex_cfg.yaml, decoded."""
        return _b64_text(self.call("get_trex_config"))

    def trex_log(self) -> str:
        """`get_trex_log`, decoded and tailed to LOG_TAIL_BYTES."""
        return _tail(_b64_text(self.call("get_trex_log")))

    def daemon_log(self) -> str:
        """`get_trex_daemon_log`, decoded and tailed to LOG_TAIL_BYTES."""
        return _tail(_b64_text(self.call("get_trex_daemon_log")))

    def metadata(self) -> list[dict[str, Any]]:
        """`get_trex_config_metadata`: the field descriptions of trex_cfg.yaml."""
        res = self.call("get_trex_config_metadata")
        if not isinstance(res, list):
            raise DaemonUnreachable("metadata is not a list")
        return res

    def devices(self) -> list[dict[str, Any]]:
        """`get_devices_info`: the daemon's NICs, one dict each (the daemon keys them by slot)."""
        res = self.call("get_devices_info")
        if isinstance(res, dict):
            return [d for d in res.values() if isinstance(d, dict)]
        if isinstance(res, list):
            return [d for d in res if isinstance(d, dict)]
        raise DaemonUnreachable("devices is neither a map nor a list")

    def files_path(self) -> str:
        """`get_files_path`: the directory push_file writes into."""
        return str(self.call("get_files_path"))

    def is_running(self) -> bool:
        """`is_running`."""
        return bool(self.call("is_running"))

    def running_status(self) -> dict[str, Any]:
        """`get_running_status`: {state, verbose, ...}."""
        res = self.call("get_running_status")
        if not isinstance(res, dict):
            raise DaemonUnreachable("running status is not an object")
        return res

    def is_reserved(self) -> bool:
        """`is_reserved`."""
        return bool(self.call("is_reserved"))

    def trex_cmds(self) -> list[tuple[int, str]]:
        """`get_trex_cmds`: (pid, command line) of each TRex process; the daemon sends the pid as a string."""
        res = self.call("get_trex_cmds")
        try:
            return [(int(pid), str(cmd)) for pid, cmd in res]
        except (TypeError, ValueError) as exc:
            raise DaemonUnreachable(f"malformed get_trex_cmds: {exc}") from exc

    def push_file(self, name: str, data: bytes | str, timeout_s: float = CALL_TIMEOUT_S) -> bool:
        """`push_file`: write one file into the daemon's files dir; `bin_data` is base64 (str: UTF-8)."""
        raw = data.encode("utf-8") if isinstance(data, str) else data
        return bool(self.call("push_file", timeout_s=timeout_s, filename=name,
                              bin_data=base64.b64encode(raw).decode("ascii")))

    def files_list(self, path: str) -> tuple[list[str], list[str]]:
        """`get_files_list`: the daemon's [dirs, files]. A path it refuses is a DaemonFault -33."""
        res = self.call("get_files_list", path=path)
        try:
            dirs, files = res
            return [str(d) for d in dirs], [str(f) for f in files]
        except (TypeError, ValueError) as exc:
            raise DaemonUnreachable(f"malformed get_files_list: {exc}") from exc

    def get_file(self, path: str) -> bytes:
        """`get_file`: the file's bytes (base64-decoded), within DAEMON_MAX_BYTES like every response."""
        res = self.call("get_file", filepath=path)
        if not isinstance(res, str):
            raise DaemonUnreachable("daemon sent a non-string where base64 was expected")
        try:
            return base64.b64decode(res)
        except ValueError as exc:
            raise DaemonUnreachable(f"daemon sent invalid base64: {exc}") from exc

    def start_trex(self, cfg: str | None, options: dict[str, int], user: str, timeout_s: float,
                   trex_args: str, stf: StfOptions | None = None) -> int:
        """`start_trex`, blocking until TRex is up (deadline timeout_s + 5); returns the run's seq.

        Stateless (stf None): `trex_cmd_options` is {cfg, **options} in that order, because the daemon emits
        them in insertion order and appends -i (trex_server.py:530-541). STF: {cfg, f, d, m, l?, rx_check?,
        **options}, stateless false, no -i (:523-524), and `trex_args` must be empty."""
        if stf is not None and trex_args:
            raise ValueError("trex_args must be empty for a stateful run")
        opts: dict[str, Any] = {}
        if cfg is not None:
            opts["cfg"] = cfg
        if stf is not None:
            opts.update({"f": stf.profile, "d": stf.d, "m": float(stf.m)})
            if stf.l > 0:
                opts["l"] = stf.l
            if stf.rx_check > 0:
                opts["rx_check"] = stf.rx_check
        opts.update(options)
        params: dict[str, Any] = {"trex_cmd_options": opts, "user": user, "stateless": stf is None,
                                  "block_to_success": True, "timeout": timeout_s}
        if stf is None:
            params["trex_args"] = trex_args
        return int(self.call("start_trex", timeout_s=timeout_s + 5, **params))

    def stop_trex(self, seq: int) -> bool | object:
        """`stop_trex`: True when stopped; False while idle or for a wrong seq is a value, not an error."""
        res: bool | object = self.call("stop_trex", seq=seq)
        return res

    def force_trex_kill(self) -> bool:
        """`force_trex_kill`: kill the daemon's run whoever started it."""
        return bool(self.call("force_trex_kill"))
