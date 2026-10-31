"""STLClient factory with the event tap and the subscriber-crash handler (plan §4.1 Threads).

Callbacks run on the client's own subscriber thread (trex/common/trex_subscriber.py `_run_safe`);
`on_event` must only hand off (e.g. loop.call_soon_threadsafe) and must not block."""
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .loader import load

log = logging.getLogger(__name__)
LOSS_EVENTS = frozenset({"subscriber crashed", "subscriber timeout", "server stopped"})


def loss_bound(timeout_s: float, margin: float = 2.0) -> float:
    """Nominal time from server silence to "subscriber timeout", plus `margin`: 4*timeout_s + 0.5 is the
    nominal value, not a worst case; the margin absorbs poll/scheduling overhead (a real guest measured
    12.57 s against 12.5 s nominal at timeout_s=3). After connect the RPC layer retries
    ZMQ_RETRY_BASE=3 extra times, each bounded by RCVTIMEO=timeout_s (trex_req_resp_client.py:66,311-335;
    trex_conn.py:201), plus the subscriber's 0.5 s trigger wait (trex_subscriber.py get_timeout_msec)."""
    return 4 * timeout_s + 0.5 + margin
_SKIP = frozenset({"global stats update"})  # 2 Hz; the stats pump owns it (P3)
PENDING_ACQUIRE_S = 5.0     # how long a pending-acquire mark protects its port from a foreign event


def mark_pending(c: Any, ports: list[int]) -> None:
    """Executor thread, before an acquire RPC: count one more pending acquire per port and push its
    deadline to PENDING_ACQUIRE_S from now. While a port has an unexpired mark, the event tap ignores a
    "port acquired" from another session: it was published before our acquire and would clear the handler
    we are about to get (the server publishes acquisitions in the order it performs them, so ours follows).
    The marks are a dict {port: (count, deadline)} on the client, under its lock, held for the dict
    operation only."""
    deadline = time.monotonic() + PENDING_ACQUIRE_S
    with c.pending_acquire_lock:
        for port in ports:
            count = c.pending_acquire.get(port, (0, 0.0))[0]
            c.pending_acquire[port] = (count + 1, deadline)


def _decrement(c: Any, port: int) -> None:
    """One mark off `port`, the entry dropped at 0; absent entry: no-op. Takes the lock itself."""
    with c.pending_acquire_lock:
        entry = c.pending_acquire.get(port)
        if entry is None:
            return
        if entry[0] <= 1:
            del c.pending_acquire[port]
        else:
            c.pending_acquire[port] = (entry[0] - 1, entry[1])


def unmark_pending(c: Any, ports: list[int]) -> None:
    """Executor thread: take back one mark per port (an acquire that failed). A port with no entry is skipped:
    the tap already took the mark when a partial acquire or a failing reset published our own event, then
    raised."""
    for port in ports:
        _decrement(c, port)


def acquire_marked(c: Any, ports: list[int], force: bool) -> Any:
    """Executor thread: mark `ports` pending, then c.acquire(ports=ports, force=force) and return its result;
    on any exception take the marks back and re-raise."""
    mark_pending(c, ports)
    try:
        return c.acquire(ports=ports, force=force)
    except BaseException:
        unmark_pending(c, ports)
        raise


def _is_protected(c: Any, port: int) -> bool:
    """True when `port` has an unexpired mark."""
    with c.pending_acquire_lock:
        entry = c.pending_acquire.get(port)
    return entry is not None and time.monotonic() < entry[1]


@dataclass(frozen=True)
class ClientEvent:
    name: str
    args: list[object]
    message: str | None
    ts: float
    level: str = "info"


@dataclass(frozen=True)
class RpcRecord:
    direction: Literal["request", "response"]
    body: object          # parsed JSON, or the raw text when it does not parse
    ts: float
    thread: str
    poll: bool = False    # every method in the request or batch is a poll; a response inherits its request's


# The RPCs the GUI sends on a timer (the stats loop, the 2 s port poll, the subscriber's event poll).
POLL_METHODS = frozenset({"get_global_stats", "get_port_stats", "get_port_status", "get_async_events",
                          "get_active_pgids", "get_pgid_stats", "get_utilization",
                          "get_port_xstats_values"})
_POLL_CAPTURE_COMMANDS = frozenset({"status", "fetch"})     # start, stop and remove are user actions
_MAX_PENDING_POLL_IDS = 1000     # a lost response must not grow the id map forever


def _is_poll(e: dict[str, object]) -> bool:
    """True for an RPC the GUI sends on a timer; a capture command counts only for status and fetch."""
    if e.get("method") == "capture":
        params = e.get("params")
        return isinstance(params, dict) and params.get("command") in _POLL_CAPTURE_COMMANDS
    return e.get("method") in POLL_METHODS


def _elements(body: object) -> list[dict[str, object]]:
    """The dict members of a JSON-RPC body or batch; anything else is dropped."""
    items = body if isinstance(body, list) else [body]
    return [x for x in items if isinstance(x, dict)]


def _jsonable(v: object) -> object:
    """v when json.dumps accepts it; an exception as "Type: message"; else repr(v)."""
    if isinstance(v, BaseException):
        return f"{type(v).__name__}: {v}"
    try:
        json.dumps(v)
        return v
    except (TypeError, ValueError):
        return repr(v)


def _install_rpc_tap(c: Any, on_rpc: Callable[[RpcRecord], None]) -> None:
    """Wrap the client's verbose hook (inventory A2.10). JsonRpcClient calls verbose_msg with
    "Sending Request To Server:\\n\\n" + pretty_json(msg) before every send, and "Server Response:\\n\\n" +
    pretty_json(resp) after it (trex_req_resp_client.py:263,290). pretty_json is replaced by the identity,
    so the hook receives the raw JSON text. That also drops the cost of re-indenting every message, and
    the ANSI colours pretty_json adds when stdout is a tty (:127). Runs on whichever thread sent the
    message (the executor, or the subscriber's get_async_events), so on_rpc must only hand off.

    A "response" record appears only on the success path: upstream returns before the response
    verbose_msg when the response is empty, when the send fails or times out, and when decoding fails
    (trex_req_resp_client.py:274-287). A timed-out request therefore shows as a request with no response."""
    rpc = c.conn.rpc
    rpc.pretty_json = lambda json_str, use_colors=None: json_str
    # A response carries no method, so each request id remembers whether it was a poll. The executor and the
    # subscriber thread both send, hence the lock (upstream already serialises sends under rpc.lock,
    # trex_req_resp_client.py:254-257 (send_msg); this one is defensive).
    pending: OrderedDict[object, bool] = OrderedDict()
    lock = threading.Lock()

    def classify(direction: str, body: object) -> bool:
        """True when every RPC in the message is a poll. A request records each id's poll flag (at most
        _MAX_PENDING_POLL_IDS, oldest dropped); a response, which has no method, pops them. Holds the lock."""
        elements = _elements(body)
        if not elements:
            return False
        with lock:
            if direction == "request":
                for e in elements:
                    if "id" in e:
                        pending[e["id"]] = _is_poll(e)
                        while len(pending) > _MAX_PENDING_POLL_IDS:
                            pending.popitem(last=False)
                return all(_is_poll(e) for e in elements)
            return all([pending.pop(e.get("id"), False) for e in elements])    # list: pop every id
    prefixes: tuple[tuple[str, Literal["request", "response"]], ...] = (
        ("Sending Request To Server:", "request"), ("Server Response:", "response"))

    def verbose_tap(msg: str) -> None:
        """Turn upstream's verbose request or response message into an RpcRecord for on_rpc; other messages
        are ignored, and an on_rpc failure is logged, not raised."""
        for prefix, direction in prefixes:
            if msg.startswith(prefix):
                text = msg[len(prefix):].strip()
                try:
                    body: object = json.loads(text)
                except ValueError:
                    body = text
                try:
                    on_rpc(RpcRecord(direction, body, time.time(), threading.current_thread().name,
                                     classify(direction, body)))
                except Exception:                # never break the RPC path
                    log.exception("rpc tap failed")
                return

    rpc.verbose_msg = verbose_tap


def make_client(host: str, sync_port: int, async_port: int, timeout_s: float,
                on_event: Callable[[ClientEvent], None],
                on_rpc: Callable[[RpcRecord], None] | None = None) -> Any:
    """A new, unconnected STLClient with the subscriber-crash handler, the event tap and the
    pending-acquire marks (see mark_pending) installed, and the RPC tap when on_rpc is given. Loads the
    vendored trex package. on_event and on_rpc run on whichever thread fires them, so they must only
    hand off."""
    load()
    from trex.common.trex_events import Event
    from trex.stl.api import STLClient

    c = STLClient(server=host, sync_port=sync_port, async_port=async_port, verbose_level="none",
                  sync_timeout=timeout_s, async_timeout=timeout_s)
    eh = c.ctx.event_handler

    def crashed(e: BaseException) -> Any:
        """Subscriber-crash handler: marks a connected client for disconnect; returns a warning Event."""
        msg = f"subscriber thread crashed: {type(e).__name__}: {e}"
        if c.conn.is_connected():
            c.conn.mark_for_disconnect(msg)
        return Event("local", "warning", msg)

    c.pending_acquire = {}                 # port -> (count, deadline); see mark_pending
    c.pending_acquire_lock = threading.Lock()

    def _stale_foreign(c: Any, args: tuple[Any, ...]) -> str | None:
        """Subscriber thread, for a "port acquired" event: our own event takes one mark off its port and
        returns None; a foreign one on a port with an unexpired mark returns the text to report instead of
        running the handler; anything else None. Never raises (an escape here marks the client for
        disconnect); a malformed event takes the handler's path."""
        try:
            port_id, who, session_id = args[0], args[1], args[2]
            if session_id == c.ctx.session_id:
                _decrement(c, port_id)
                return None
            if _is_protected(c, port_id):
                return f"Port {port_id} was taken by '{who}' before our acquire; ignored"
        except Exception:
            log.exception("pending-acquire check failed")
        return None

    eh.register_event_handler("subscriber crashed", crashed)  # replaces upstream's SIGTERM path
    original = eh.on_event

    def tap(name: str, *args: Any, **kwargs: Any) -> Any:
        """Event handler wrapper: runs upstream's handler, except for a stale foreign "port acquired" on a
        marked port (_stale_foreign), stores its Event, and unless the name is in _SKIP passes a ClientEvent
        to on_event (a failure there is logged, not raised). An unhandled name raises."""
        # Mirrors upstream EventsHandler.on_event (trex_events.py:112-126), which returns None, so the
        # handler's Event (and its msg) must be taken here rather than from original()'s return value.
        if name not in eh.events_handlers:
            return original(name, *args, **kwargs)   # raises upstream's "unhandled event" TRexError
        stale: str | None = None
        if name == "port acquired":
            stale = _stale_foreign(c, args)
        if stale is None:
            ev = eh.events_handlers[name](*args, **kwargs)
        else:
            ev = None                  # the handler would clear our acquire in flight: the handler stays
        if ev:
            eh.events.append(ev)
        if name not in _SKIP:
            try:
                args_json = [_jsonable(a) for a in args]
                if stale is None:
                    on_event(ClientEvent(name, args_json, getattr(ev, "msg", None), time.time(),
                                         level=getattr(ev, "ev_type", "info") or "info"))
                else:
                    on_event(ClientEvent(name, args_json, stale, time.time(), level="info"))
            except Exception:  # never let our pump crash the subscriber thread
                log.exception("event tap failed for %s", name)
        return ev

    eh.on_event = tap
    if on_rpc is not None:
        _install_rpc_tap(c, on_rpc)
    return c
