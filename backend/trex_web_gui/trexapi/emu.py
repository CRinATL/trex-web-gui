"""The bounded RPC client for a TRex EMU server (spec §4.1). EmuRpc sits on RawRpc, so it is not the vendored
EMUClient and the backend never imports `trex.emu`.

Every request goes through `METHODS`, an allow-list checked before any I/O, and none ever carries
`clear: true`: a counter clear would change what other clients of the shared server see. Error wording is the
server's own, recorded in tests/fixtures/p7/negatives.json; if the server rewords, those fixtures and
`translate_emu` change together."""
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import EmuUnsupported, ServerRestarted, ServerUnreachable, TrexApiError, UnsupportedServerVersion
from .rawrpc import RawRpc, RawRpcError

EMU_API = ("EMU", 1, 1)                     # name, major, minor; the spike's ruling R0.2
EMU_REPLY_MAX_BYTES = 4 * 1024 * 1024       # a decoded reply over this is refused, however it is framed

CHUNK = 255                 # the server's most keys or clients per request and per page (`lte=255`)
MAX_NAMESPACES = 4096       # a namespace listing longer than this is refused
MAX_CLIENT_KEYS = 65536     # likewise for one namespace's client listing
LISTING_RETRIES = 2         # a listing the server interrupts is restarted this many times, then flagged
_INTERRUPTED = "interupted"     # the server's spelling: " iterator was interupted , reset and start again "

# plugin -> (namespace counter method, client counter method), None where the plugin has none. The keys are
# the vendored module names (emu_plugin_<key>.py); transporte's counters are named transe on the server. A
# unit test rebuilds this from the vendored plugin sources and from tests/fixtures/p7/counters.json.
PLUGIN_COUNTERS: dict[str, tuple[str | None, str | None]] = {
    "appsim": (None, "appsim_client_cnt"),
    "arp": ("arp_ns_cnt", None),
    "cdp": (None, "cdp_client_cnt"),
    "dhcp": (None, "dhcp_client_cnt"),
    "dhcpsrv": (None, "dhcpsrv_c_cnt"),
    "dhcpv6": (None, "dhcpv6_client_cnt"),
    "dns": ("dns_ns_cnt", "dns_c_cnt"),
    "dot1x": (None, "dot1x_client_cnt"),
    "icmp": ("icmp_ns_cnt", None),
    "igmp": ("igmp_ns_cnt", None),
    "ipfix": ("ipfix_ns_cnt", "ipfix_c_cnt"),
    "ipv6": ("ipv6_ns_cnt", None),
    "lldp": (None, "lldp_client_cnt"),
    "mdns": ("mdns_ns_cnt", "mdns_c_cnt"),
    "tdl": (None, "tdl_c_cnt"),
    "transport": (None, "transport_client_cnt"),
    "transporte": ("transe_ns_cnt", None),
}
# A plugin's name in the server's `plug_names` where it is not the PLUGIN_COUNTERS key (recorded:
# tests/fixtures/p7/counters.json, each plugin's `server_name`).
PLUGIN_SERVER_NAME: dict[str, str] = {"transporte": "transe"}
# counter method -> the scope its request names: "ctx" (none), "ns" (a tunnel), "client" (a tunnel and a MAC)
COUNTER_SCOPES: dict[str, str] = {"ctx_cnt": "ctx"} | {
    cmd: scope for ns_cmd, c_cmd in PLUGIN_COUNTERS.values()
    for cmd, scope in ((ns_cmd, "ns"), (c_cmd, "client")) if cmd}

# The methods EmuRpc.call will send. api_sync_v2 goes through RawRpc.api_sync, not through call. The writes
# are the vendored client's load and removal sequence without ctx_stop_autotriggers (the shipped server
# lacks it).
METHODS = frozenset({"get_version", "ping", "ctx_iter", "ctx_get_info", "ctx_client_iter",
                     "ctx_client_get_info", "ctx_set_def_plugins", "ctx_add", "ctx_client_set_def_plugins",
                     "ctx_client_add", "ctx_client_remove", "ctx_remove"}) | frozenset(COUNTER_SCOPES)
RATE_MIN, RATE_MAX = 1, 100000      # a write's max_rate, in clients per second

_API_MISMATCH = re.compile(r"Server API (\w+):\((\d+):(\d+)\) does not match the client API")
_NO_PLUGIN = "no valid plugin"      # the server's text for a plugin not loaded in that scope (ruling R11)
# transport_client_cnt's text for a client with the transport plugin loaded but no transport context
# (tests/fixtures/p7/counters.json): no counters there, like a plugin not loaded, not a failed read.
_NO_TRANSPORT_CONTEXT = "does not have a transport context"
RESTARTED = "The EMU server restarted (its API handler changed); connect again."


@dataclass(frozen=True)
class EmuInfo:
    """get_version's answer: the EMU server's version, build date and time, builder and mode."""
    version: str
    build_date: str
    build_time: str
    built_by: str
    mode: str


Mac = tuple[int, int, int, int, int, int]


def _pair(v: Any) -> tuple[int, int]:
    """A two-int list from the wire as a tuple; TrexApiError for anything else."""
    if (isinstance(v, list) and len(v) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        return (v[0], v[1])
    raise TrexApiError("The EMU server sent a namespace key of an unexpected shape.")


@dataclass(frozen=True)
class NsKey:
    """A namespace's key: vport, and the VLAN tags tci and tpid (two each)."""
    vport: int
    tci: tuple[int, int] = (0, 0)
    tpid: tuple[int, int] = (0, 0)

    @classmethod
    def from_wire(cls, d: Any) -> "NsKey":
        """The key in a listing entry (extra keys such as `plugs` are ignored); TrexApiError if malformed."""
        vport = d.get("vport") if isinstance(d, dict) else None
        if isinstance(vport, bool) or not isinstance(vport, int):
            raise TrexApiError("The EMU server sent a namespace key of an unexpected shape.")
        return cls(vport, _pair(d.get("tci", [0, 0])), _pair(d.get("tpid", [0, 0])))

    def wire(self) -> dict[str, Any]:
        """The `tun` object a request carries."""
        return {"vport": self.vport, "tci": list(self.tci), "tpid": list(self.tpid)}


def _mac(v: Any) -> Mac:
    """A client MAC from the wire (six bytes); TrexApiError when malformed."""
    if (isinstance(v, list) and len(v) == 6
            and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < 256 for x in v)):
        return (v[0], v[1], v[2], v[3], v[4], v[5])
    raise TrexApiError("The EMU server sent a client MAC of an unexpected shape.")


@dataclass
class Listing[T]:
    """What a cursor walk found: `items` in server order, and `interrupted` when the server kept restarting
    the walk (another client changed the list) and these are the last attempt's items."""
    items: list[T] = field(default_factory=list)
    interrupted: bool = False


def translate_emu(exc: BaseException, method: str) -> TrexApiError:
    """The typed error for `exc` by the EMU server's own wording: a handshake mismatch is
    UnsupportedServerVersion naming the server's API, -32602 'Method with not a valid API' is ServerRestarted,
    -32601, a plugin that is not loaded, or a client without a transport context is EmuUnsupported(method). A
    TrexApiError that matches none of those, with its code if it has one, is returned unchanged; anything else
    is ServerUnreachable."""
    if not isinstance(exc, TrexApiError):
        return ServerUnreachable(f"Cannot reach the EMU server: {exc}")
    if not isinstance(exc, RawRpcError):
        return exc
    text = exc.message
    m = _API_MISMATCH.search(text)
    if exc.code == -32600 and m:
        return UnsupportedServerVersion(
            f"{m[2]}.{m[3]}", f"The server speaks {m[1]} API {m[2]}.{m[3]}; trex-web-gui supports only "
            f"{EMU_API[0]} API {EMU_API[1]}.{EMU_API[2]}.")
    if exc.code == -32602 and "not a valid API" in text:
        return ServerRestarted(RESTARTED)
    if exc.code == -32601 or (exc.code == -32600 and (_NO_PLUGIN in text or _NO_TRANSPORT_CONTEXT in text)):
        return EmuUnsupported(method)
    return exc


class RatePacer:
    """Holds a write to `rate` clients per second over all the chunks it is told about: `after(n)` says how
    long to wait now so that the clients sent so far are no faster than the rate counted from the pacer's
    creation. The caller sleeps (EmuSession does, on the loop, so reads and a close are not held up). A rate
    of None never waits."""

    def __init__(self, rate: float | None, clock: Callable[[], float]) -> None:
        """Start the schedule now."""
        self.rate, self._clock = rate, clock
        self._start = clock()
        self._count = 0

    def after(self, n: int) -> float:
        """`n` more clients were sent; the seconds to wait before the next chunk (0 when on schedule)."""
        self._count += n
        if not self.rate:
            return 0.0
        return max(0.0, self._start + self._count / self.rate - self._clock())


class EmuRpc:
    """One EMU server at host:port. Blocking; called from one thread at a time (the EMU executor).

    `clock` times a ping. A write is paced by EmuSession between chunk calls (RatePacer), not in here, so that
    a slow rate never holds the one EMU executor."""

    def __init__(self, host: str, port: int, timeout_s: float, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        """Open the socket; nothing is sent until connect()."""
        self.clock = clock
        self._rpc = RawRpc(host, port, timeout_s, reply_max=EMU_REPLY_MAX_BYTES)
        self._missing: set[str] = set()     # counter methods the server answered method-not-found

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """One request and its result. ValueError, before any I/O, for a method outside METHODS or a request
        asking to clear counters; a TrexApiError (translate_emu's) for what the server or the wire does."""
        if method not in METHODS:
            raise ValueError(f"{method} is not an EMU method this client sends")
        if params and params.get("clear"):
            raise ValueError("a request that clears counters is never sent")
        try:
            return self._rpc.call(method, params)
        except TrexApiError as exc:
            raise translate_emu(exc, method) from exc

    def connect(self) -> EmuInfo:
        """api_sync_v2 for EMU_API, then get_version. UnsupportedServerVersion when the server speaks another
        EMU API, ServerUnreachable when it does not answer."""
        try:
            self._rpc.api_sync(*EMU_API)
        except TrexApiError as exc:
            raise translate_emu(exc, "api_sync_v2") from exc
        res = self.call("get_version")
        if not isinstance(res, dict) or not all(isinstance(res.get(k), str) for k in EmuInfo.__annotations__):
            raise TrexApiError("The EMU server's get_version answer has an unexpected shape.")
        return EmuInfo(**{k: res[k] for k in EmuInfo.__annotations__})

    def ping(self) -> float:
        """Seconds one ping took to answer."""
        t0 = self.clock()
        self.call("ping")
        return self.clock() - t0

    def close(self) -> None:
        """Close the socket."""
        self._rpc.close()

    def _walk_once[T](self, method: str, base: dict[str, Any], parse: Callable[[Any], T], cap: int, noun: str,
                   upto: int | None) -> tuple[list[T], bool]:
        """One pass of a cursor: (items, complete). Not complete when the server interrupted the cursor or
        repeated an item. TrexApiError when more than `cap` items arrive."""
        items: list[T] = []
        seen: set[T] = set()
        reset = True
        while upto is None or len(items) < upto:
            count = CHUNK if upto is None else min(CHUNK, upto - len(items))
            try:
                res = self.call(method, {**base, "count": count, "reset": reset})
            except RawRpcError as exc:
                if _INTERRUPTED in exc.message:
                    return items, False
                raise
            reset = False
            if not isinstance(res, dict):
                raise TrexApiError(f"The EMU server's {method} answer has an unexpected shape.")
            data = res.get("data")
            if data is not None and not isinstance(data, list):
                raise TrexApiError(f"The EMU server's {method} answer has an unexpected shape.")
            for raw in data or []:
                item = parse(raw)
                if item in seen:
                    return items, False
                seen.add(item)
                items.append(item)
                if len(items) > cap:
                    raise TrexApiError(f"The EMU server lists more than {cap} {noun}; stopped there.")
            if not data or res.get("stopped") or res.get("empty"):
                break
        return items, True

    def _walk[T](self, method: str, base: dict[str, Any], parse: Callable[[Any], T], cap: int, noun: str,
              upto: int | None = None) -> Listing[T]:
        """A whole cursor walk, restarted up to LISTING_RETRIES times when the server interrupts it; then the
        last pass's items with `interrupted` set."""
        items: list[T] = []
        for _ in range(1 + LISTING_RETRIES):
            items, complete = self._walk_once(method, base, parse, cap, noun, upto)
            if complete:
                return Listing(items)
        return Listing(items, interrupted=True)

    def list_namespaces(self) -> Listing[NsKey]:
        """Every namespace key: ctx_iter in pages of CHUNK until the server says it is done. Several RPCs, one
        executor job. TrexApiError past MAX_NAMESPACES."""
        return self._walk("ctx_iter", {}, NsKey.from_wire, MAX_NAMESPACES, "namespaces")

    def ns_info(self, keys: list[NsKey]) -> list[dict[str, Any]]:
        """ctx_get_info for at most CHUNK namespaces: their active_clients and plug_names, as the server
        sends them (ValueError for more keys than that)."""
        if len(keys) > CHUNK:
            raise ValueError(f"at most {CHUNK} namespaces per request")
        res = self.call("ctx_get_info", {"tunnels": [k.wire() for k in keys]})
        return self._dict_list(res, "ctx_get_info")

    def list_clients(self, ns: NsKey, upto: int | None = None) -> Listing[Mac]:
        """The MACs of `ns`, walked only as far as `upto` of them when given (a page walks to offset+limit,
        never the whole list). TrexApiError past MAX_CLIENT_KEYS."""
        if upto is not None and upto <= 0:
            return Listing()
        return self._walk("ctx_client_iter", {"tun": ns.wire()}, _mac, MAX_CLIENT_KEYS, "clients", upto)

    def client_info(self, ns: NsKey, macs: list[Mac]) -> list[dict[str, Any]]:
        """ctx_client_get_info for at most CHUNK clients of `ns`, as the server sends them (ValueError for
        more MACs than that)."""
        if len(macs) > CHUNK:
            raise ValueError(f"at most {CHUNK} clients per request")
        res = self.call("ctx_client_get_info", {"tun": ns.wire(), "macs": [list(m) for m in macs]})
        return self._dict_list(res, "ctx_client_get_info")

    @staticmethod
    def _dict_list(res: Any, method: str) -> list[dict[str, Any]]:
        """`res` when it is a list of objects; TrexApiError otherwise."""
        if not isinstance(res, list) or not all(isinstance(r, dict) for r in res):
            raise TrexApiError(f"The EMU server's {method} answer has an unexpected shape.")
        return res

    def counters(self, method: str, ns: NsKey | None = None, mac: Mac | None = None, *, meta: bool = False,
                 zero: bool = False, mask: list[str] | None = None) -> dict[str, Any]:
        """One counter request: ctx_cnt (no key), a plugin's namespace counters (`ns`) or client counters
        (`ns` and `mac`). `meta` asks for the tables' descriptions, otherwise the values (sparse unless
        `zero`); `mask` limits the tables of a value request only (the shipped server ignores it on a meta
        request, ruling R29). Never clears. ValueError for an unknown method or a missing or
        surplus key; EmuUnsupported when the server lacks the method (remembered, so the next call sends
        nothing) or the plugin is not loaded in that scope."""
        scope = COUNTER_SCOPES.get(method)
        if scope is None:
            raise ValueError(f"{method} is not a counter method")
        if (scope != "ctx") != (ns is not None) or (scope == "client") != (mac is not None):
            raise ValueError(f"{method} takes {'no tunnel' if scope == 'ctx' else 'a tunnel'}"
                             f"{' and a MAC' if scope == 'client' else ''}")
        if method in self._missing:
            raise EmuUnsupported(method)
        params: dict[str, Any] = {"meta": meta, "zero": zero, "mask": mask, "clear": False}
        if ns is not None:
            params["tun"] = ns.wire()
        if mac is not None:
            params["mac"] = list(mac)
        try:
            res = self.call(method, params)
        except EmuUnsupported as exc:
            cause = exc.__cause__
            if isinstance(cause, RawRpcError) and cause.code == -32601:
                self._missing.add(method)
            raise
        if not isinstance(res, dict):
            raise TrexApiError(f"The EMU server's {method} answer has an unexpected shape.")
        return res

    # -- writes (P7b) -------------------------------------------------------------------------------
    def set_def_ns_plugins(self, plugs: dict[str, Any]) -> None:
        """ctx_set_def_plugins: the plugins every later namespace gets by default."""
        self.call("ctx_set_def_plugins", {"def_plugs": plugs})

    def add_namespaces(self, entries: list[dict[str, Any]]) -> None:
        """ctx_add for the namespace entries ({vport, tci, tpid, plugs, def_c_plugs}) in chunks of CHUNK."""
        for i in range(0, len(entries), CHUNK):
            self.call("ctx_add", {"tunnels": entries[i:i + CHUNK]})

    def set_def_client_plugins(self, ns: dict[str, Any], plugs: dict[str, Any]) -> None:
        """ctx_client_set_def_plugins: the plugins every later client of namespace `ns` (wire key) gets."""
        self.call("ctx_client_set_def_plugins", {"def_plugs": plugs, "tun": ns})

    def add_clients(self, ns: dict[str, Any], clients: list[dict[str, Any]]) -> None:
        """ctx_client_add for `clients` (wire objects) of namespace `ns` in chunks of CHUNK."""
        for i in range(0, len(clients), CHUNK):
            self.call("ctx_client_add", {"clients": clients[i:i + CHUNK], "tun": ns})

    def remove_clients(self, ns: NsKey, macs: list[Mac]) -> None:
        """ctx_client_remove for `macs` of `ns` in chunks of CHUNK."""
        for i in range(0, len(macs), CHUNK):
            self.call("ctx_client_remove", {"macs": [list(m) for m in macs[i:i + CHUNK]], "tun": ns.wire()})

    def remove_namespaces(self, keys: list[NsKey]) -> None:
        """ctx_remove for `keys` in chunks of CHUNK. The caller has removed their clients first."""
        for i in range(0, len(keys), CHUNK):
            self.call("ctx_remove", {"tunnels": [k.wire() for k in keys[i:i + CHUNK]]})
