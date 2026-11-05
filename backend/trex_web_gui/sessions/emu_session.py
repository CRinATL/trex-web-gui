"""The EMU session (spec §4.2): one connection to a TRex EMU server, its ping loop, and the reads the EMU page
makes (namespaces, client pages, counters).

Everything here runs on the event loop except the EMU RPCs, which run one job at a time on the executor `emu`
(never TrexSession's executors or the daemon's). A connection is one `_Conn`: its EmuRpc, its counter-meta
cache and its set of counter methods the server lacks, so a reconnect starts all three afresh. There is no
automatic reconnect: a restarted EMU server has lost its namespaces, so `lost` waits for the user."""
import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..trexapi.emu import (
    CHUNK,
    LISTING_RETRIES,
    PLUGIN_COUNTERS,
    RATE_MAX,
    RATE_MIN,
    EmuInfo,
    EmuRpc,
    Listing,
    Mac,
    NsKey,
    RatePacer,
)
from ..trexapi.errors import (
    EmuUnsupported,
    ServerRestarted,
    ServerUnreachable,
    TrexApiError,
    UnsupportedServerVersion,
)
from ..trexapi.profiles import LoadedEmuProfile, load_emu_profile
from ..trexapi.rawrpc import RawRpcError
from .trex_session import Publish

log = logging.getLogger(__name__)

_NOT_CONNECTED = "Not connected to an EMU server."
# Namespace-level plugins whose namespace crashes trex-emu v3.08 when removed (measured, ruling R33)
CRASH_PLUGINS = frozenset({"ipfix", "tdl"})


class EmuRefused(TrexApiError):
    """A refusal made by trex-web-gui, not by the EMU server. `code` is one of not_connected, busy,
    unsupported, unsupported_server, confirm_required, foreign_namespaces and crash_risk."""
    def __init__(self, code: str, message: str) -> None:
        """Keep the short reason as .code beside the message."""
        super().__init__(message)
        self.code = code


@dataclass(eq=False)
class _Conn:
    """One connection's state, compared by identity (it keys the coalescing); dropped whole on disconnect."""
    rpc: EmuRpc
    info: EmuInfo
    meta: dict[tuple[str, NsKey | None, Mac | None], dict[str, Any]] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)      # counter methods the server answered method-not-found


def _int_field(row: dict[str, Any], name: str) -> int:
    """`row[name]` when it is a non-negative int, else TrexApiError (a malformed server reply)."""
    v = row.get(name)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise TrexApiError(f"The EMU server sent {name} of an unexpected shape.")
    return v


def _rows(meta: dict[str, Any], values: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """Counter tables as rows {name, value, unit, help, info, zero}. With `values` None: every counter `meta`
    (table -> {name, meta: [...]}) describes, with value None. With `values` (table -> name -> value, sparse:
    the server leaves out zero counters): every counter `meta` describes, in meta order for every meta table,
    with value 0 where `values` omits it, then each value `meta` does not describe (info None, unit and help
    empty), then tables only `values` has. TrexApiError for a table of an unexpected shape."""
    out: dict[str, list[dict[str, Any]]] = {}
    for table in list(meta) + [t for t in (values or {}) if t not in meta]:
        described = meta.get(table)
        entries = described.get("meta") if isinstance(described, dict) else None
        if entries is not None and not (isinstance(entries, list)
                                        and all(isinstance(e, dict) for e in entries)):
            raise TrexApiError("The EMU server's counter table has an unexpected shape.")
        by_name = {e.get("name"): e for e in entries or []}
        got: dict[str, Any] = {}
        if values is not None and table in values:
            if not isinstance(values[table], dict):
                raise TrexApiError("The EMU server's counter table has an unexpected shape.")
            got = values[table]
        if values is None:
            pairs = [(e.get("name"), None) for e in entries or []]
        else:
            pairs = [(n, got.get(n, 0)) for n in by_name]
            pairs += [(n, v) for n, v in got.items() if n not in by_name]
        out[table] = [{"name": n, "value": v, "unit": by_name.get(n, {}).get("unit", ""),
                       "help": by_name.get(n, {}).get("help", ""), "info": by_name.get(n, {}).get("info"),
                       "zero": by_name.get(n, {}).get("zero", False)} for n, v in pairs]
    return out


class EmuSession:
    """At most one EMU connection. States: disconnected, connecting, connected, lost."""

    def __init__(self, publish: Publish, *, ping_s: float = 2.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        """Disconnected, with one executor `emu`; `ping_s` is the ping interval while connected. `clock` and
        `sleep` (awaited on the loop) are what a write is paced with (a test replaces both)."""
        self._publish, self._ping_s = publish, ping_s
        self._clock, self._sleep = clock, sleep
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emu")
        self._lock = asyncio.Lock()             # held by connect; a second connect is `busy`
        self._conn: _Conn | None = None
        self._state = "disconnected"
        self._target: dict[str, Any] | None = None
        self._error: str | None = None
        self._busy: dict[str, Any] | None = None    # a write in flight (Task 11): {kind, done, total}
        self._generation = 0                        # bumps after every load or remove this backend performs
        self._loaded: dict[str, Any] | None = None
        self._ours: set[NsKey] = set()              # namespaces this backend added
        self._rev = 0                               # bumps with _ours and the generation (coalescing key)
        self._ping_task: asyncio.Task[None] | None = None
        self._inflight: dict[tuple[Any, ...], asyncio.Future[Any]] = {}
        self._last_msg: dict[str, Any] | None = None
        self._closed = False

    # -- publishing ---------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """The `emu` hub message for the current state."""
        info = self._conn.info if self._conn is not None else None
        return {"type": "emu", "state": self._state, "target": dict(self._target) if self._target else None,
                "info": {k: getattr(info, k) for k in EmuInfo.__annotations__} if info else None,
                "error": self._error, "busy": dict(self._busy) if self._busy else None,
                "generation": self._generation, "loaded": dict(self._loaded) if self._loaded else None}

    def _flush(self) -> None:
        """Publish the snapshot when it differs from the last one published (coalescing)."""
        msg = self.snapshot()
        if msg != self._last_msg:
            self._last_msg = msg
            self._publish(msg)

    # -- connection ---------------------------------------------------------------------------------
    def _open(self, host: str, port: int, timeout_s: float) -> _Conn:
        """Build a new EmuRpc and connect it (executor job); the socket is closed again when that fails."""
        rpc = EmuRpc(host, port, timeout_s, clock=self._clock)
        try:
            return _Conn(rpc, rpc.connect())
        except BaseException:
            rpc.close()
            raise

    @staticmethod
    def _close_abandoned(done: Any) -> None:
        """Done-callback of a connect nobody awaits any more (cancelled): close the socket it opened."""
        if not done.cancelled() and done.exception() is None:
            done.result().rpc.close()

    async def connect(self, host: str, port: int = 4510, timeout_s: float = 3.0) -> dict[str, Any]:
        """Drop any connection, then connect to host:port with a new EmuRpc and publish `connected`.
        EmuRefused `busy` when a connect is in flight or a write is running, `unsupported_server` when the
        server speaks another EMU API, and `not_connected` when disconnect() ran meanwhile;
        ServerUnreachable or another TrexApiError when the server cannot be read (the session is then
        disconnected, with the error)."""
        if self._lock.locked() or self._busy:
            raise EmuRefused("busy", "Another EMU operation is in progress.")
        async with self._lock:
            await self._drop()
            self._state, self._target, self._error = "connecting", {"host": host, "port": port}, None
            self._flush()
            token = self._target
            job = self._executor.submit(self._open, host, port, timeout_s)
            try:
                conn = await asyncio.wrap_future(job)
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    job.add_done_callback(self._close_abandoned)    # the handshake may still finish
                if self._target is token:
                    self._state, self._target = "disconnected", None
                    self._error = str(exc) if isinstance(exc, TrexApiError) else None
                    self._flush()
                if isinstance(exc, UnsupportedServerVersion):
                    raise EmuRefused("unsupported_server", exc.message) from exc
                raise
            if self._target is not token:               # disconnect() or close() ran meanwhile
                conn.rpc.close()                        # not via the executor: close() may have shut it
                raise EmuRefused("not_connected", "Disconnected while connecting.")
            self._conn, self._state = conn, "connected"
            self._ping_task = asyncio.create_task(self._ping_loop(conn))
            self._flush()
            return self.snapshot()

    async def _drop(self) -> None:
        """Forget the connection: stop the ping loop, close the socket (queued behind a read in flight) and
        clear what belongs to it."""
        conn, self._conn = self._conn, None
        task, self._ping_task = self._ping_task, None
        self._state, self._target, self._error = "disconnected", None, None
        self._inflight.clear()
        self._ours.clear()
        self._loaded = None
        self._rev += 1
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if conn is not None:
            try:
                await self._submit(conn.rpc.close)
            except Exception:
                log.exception("closing the EMU socket failed")

    async def disconnect(self) -> None:
        """Stop the ping loop, close the connection and publish `disconnected`. EmuRefused `busy` during a
        write. A read in flight answers `not_connected` to its caller."""
        if self._busy:
            raise EmuRefused("busy", "Another EMU operation is in progress.")
        await self._drop()
        self._flush()

    async def close(self) -> None:
        """disconnect(), then shut the executor down off the loop; a second call does nothing."""
        if self._closed:
            return
        self._closed = True
        self._busy = None
        await self._drop()
        self._flush()
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)

    # -- liveness -----------------------------------------------------------------------------------
    async def _submit[T](self, fn: Callable[..., T], *args: Any) -> T:
        """One blocking call on the executor."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, functools.partial(fn, *args))

    async def _run[T](self, conn: _Conn, fn: Callable[..., T], *args: Any) -> T:
        """One job on `conn`'s behalf. EmuRefused `not_connected` when `conn` is no longer the connection
        (before the job, or by the time it ends); a ServerRestarted or ServerUnreachable from the job marks
        the session `lost` and is re-raised."""
        if conn is not self._conn or self._state != "connected":
            raise self._not_connected()
        try:
            result = await self._submit(fn, *args)
        except (ServerRestarted, ServerUnreachable) as exc:
            if conn is not self._conn:
                raise EmuRefused("not_connected", "Disconnected while reading.") from exc
            if self._state == "connected":
                self._state, self._error = "lost", str(exc)
                self._flush()
            raise
        if conn is not self._conn:
            raise EmuRefused("not_connected", "Disconnected while reading.")
        return result

    async def _ping_loop(self, conn: _Conn) -> None:
        """Ping every ping_s while this connection is `connected`; a loss ends the loop (_run marks it)."""
        while conn is self._conn and self._state == "connected":
            await asyncio.sleep(self._ping_s)
            if conn is not self._conn or self._state != "connected":
                return
            try:
                await self._run(conn, conn.rpc.ping)
            except (ServerRestarted, ServerUnreachable, EmuRefused):
                return
            except TrexApiError as exc:
                log.warning("EMU ping failed: %s", exc)

    # -- reads --------------------------------------------------------------------------------------
    def _not_connected(self) -> EmuRefused:
        """The not_connected refusal (the lost state says so in its message)."""
        if self._state == "lost":
            return EmuRefused("not_connected", f"The EMU connection was lost ({self._error}); connect again.")
        return EmuRefused("not_connected", _NOT_CONNECTED)

    def _need_conn(self) -> _Conn:
        """The connection, or EmuRefused not_connected."""
        if self._conn is None or self._state != "connected":
            raise self._not_connected()
        return self._conn

    async def _coalesced[T](self, key: tuple[Any, ...], make: Callable[[], Awaitable[T]]) -> T:
        """The result of `make()`, shared with every identical request made while it runs: a second caller
        awaits the first's job instead of starting another."""
        fut = self._inflight.get(key)
        if fut is None:
            fut = asyncio.ensure_future(make())
            self._inflight[key] = fut

            def done(f: asyncio.Future[Any]) -> None:
                """Forget the job and mark its error as seen, so an abandoned one is not logged."""
                if self._inflight.get(key) is f:
                    del self._inflight[key]
                if not f.cancelled():
                    f.exception()
            fut.add_done_callback(done)
        result: T = await asyncio.shield(fut)
        return result

    async def namespaces(self) -> dict[str, Any]:
        """{generation, namespaces: [{vport, tci, tpid, active_clients, plug_names, ours}], interrupted}: the
        server's namespaces with their info. EmuRefused not_connected; ServerUnreachable and the like."""
        conn = self._need_conn()
        # `ours` and the generation are read here, on the loop; the job gets them as arguments
        return await self._coalesced(
            ("namespaces", conn, self._rev),
            lambda: self._run(conn, self._read_namespaces, conn, frozenset(self._ours), self._generation))

    @staticmethod
    def _read_namespaces(conn: _Conn, ours: frozenset[NsKey], generation: int) -> dict[str, Any]:
        """The namespaces listing and their infos in chunks of CHUNK (one executor job)."""
        listing = conn.rpc.list_namespaces()
        infos: dict[NsKey, dict[str, Any]] = {}
        for i in range(0, len(listing.items), CHUNK):
            for row in conn.rpc.ns_info(listing.items[i:i + CHUNK]):
                infos[NsKey.from_wire(row)] = row
        out = []
        for key in listing.items:
            row = infos.get(key, {})
            plugs = row.get("plug_names")
            out.append({"vport": key.vport, "tci": list(key.tci), "tpid": list(key.tpid),
                        "active_clients": _int_field(row, "active_clients") if row else 0,
                        "plug_names": plugs if isinstance(plugs, list) else [], "ours": key in ours})
        return {"generation": generation, "namespaces": out, "interrupted": listing.interrupted}

    async def clients(self, ns: NsKey, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """{total, clients, interrupted}: the clients `offset`..`offset+limit` of `ns`, as the server
        describes them. The cursor is walked only that far; `total` is the namespace's active_clients.
        ValueError for an offset below 0 or a limit outside 1..CHUNK."""
        if offset < 0 or not 1 <= limit <= CHUNK:
            raise ValueError(f"offset must be at least 0 and limit in 1..{CHUNK}")
        conn = self._need_conn()
        return await self._coalesced(("clients", conn, ns, offset, limit),
                                     lambda: self._run(conn, self._read_clients, conn, ns, offset, limit))

    async def has_client(self, ns: NsKey, mac: Mac) -> bool | None:
        """Whether `ns` lists the client `mac`: one executor job, the cursor walk of its MACs (bounded by
        MAX_CLIENT_KEYS, restarted up to LISTING_RETRIES times when interrupted), and no client infos. None
        when it ended interrupted and `mac` was not among what it read, which proves nothing."""
        conn = self._need_conn()
        return await self._coalesced(("has_client", conn, ns, mac),
                                     lambda: self._run(conn, self._read_has_client, conn, ns, mac))

    @staticmethod
    def _read_has_client(conn: _Conn, ns: NsKey, mac: Mac) -> bool | None:
        """One executor job: the namespace's MAC listing, searched for `mac`."""
        listing = conn.rpc.list_clients(ns)
        if mac in listing.items:
            return True
        return None if listing.interrupted else False

    @staticmethod
    def _read_clients(conn: _Conn, ns: NsKey, offset: int, limit: int) -> dict[str, Any]:
        """One client page (one executor job): ns_info for the total, the cursor to offset+limit, then the
        page's infos."""
        info = conn.rpc.ns_info([ns])
        total = _int_field(info[0], "active_clients") if info else 0
        if offset >= total:
            return {"total": total, "clients": [], "interrupted": False}
        listing = conn.rpc.list_clients(ns, offset + limit)
        page = listing.items[offset:offset + limit]
        return {"total": total, "clients": conn.rpc.client_info(ns, page) if page else [],
                "interrupted": listing.interrupted}

    async def counters(self, scope: str, ns: NsKey | None = None, mac: Mac | None = None,
                       plugin: str | None = None, meta: bool = False) -> dict[str, Any]:
        """{tables: {table: [{name, value, unit, help, info, zero}]}, unsupported: [plugin]}. `scope` ctx is
        the server's own counters, ns a tunnel's plugin counters and client one client's (`ns` and `mac`
        given as the scope needs). A plugin counter names `plugin`, or leaves it out for every plugin with
        counters in that scope. A table's meta is fetched once per key for the connection, then only sparse
        values; `meta=True` answers every counter the meta describes, with value None.

        EmuRefused `unsupported` when `plugin` is named and the server lacks its counters for the scope (or
        the plugin has none there); without `plugin` such plugins are skipped and listed in `unsupported`.
        ValueError for an unknown scope, plugin, or a missing or surplus key."""
        if scope not in ("ctx", "ns", "client"):
            raise ValueError(f"{scope} is not a counter scope")
        if (scope != "ctx") != (ns is not None) or (scope == "client") != (mac is not None):
            raise ValueError(f"scope {scope} takes {'no tunnel' if scope == 'ctx' else 'a tunnel'}"
                             f"{' and a MAC' if scope == 'client' else ''}")
        if plugin is not None and plugin not in PLUGIN_COUNTERS:
            raise ValueError(f"{plugin} is not a plugin with counters")
        conn = self._need_conn()
        return await self._coalesced(
            ("counters", conn, scope, ns, mac, plugin, meta),
            lambda: self._run(conn, self._read_counters, conn, scope, ns, mac, plugin, meta))

    @staticmethod
    def _methods(scope: str, plugin: str | None) -> list[tuple[str | None, str]]:
        """(plugin, counter method) to read for a request; a named plugin without a method in the scope is
        (plugin, "") and refused by the caller."""
        if scope == "ctx":
            return [(None, "ctx_cnt")]
        idx = 0 if scope == "ns" else 1
        if plugin is not None:
            return [(plugin, PLUGIN_COUNTERS[plugin][idx] or "")]
        return [(p, m) for p, pair in sorted(PLUGIN_COUNTERS.items()) if (m := pair[idx])]

    def _read_counters(self, conn: _Conn, scope: str, ns: NsKey | None, mac: Mac | None,
                       plugin: str | None, meta: bool) -> dict[str, Any]:
        """The counter requests of one `counters` call (one executor job)."""
        tables: dict[str, list[dict[str, Any]]] = {}
        unsupported: list[str] = []
        for name, method in self._methods(scope, plugin):
            try:
                if not method or method in conn.missing:
                    raise EmuUnsupported(method or f"{name} counters in a {scope} scope")
                tables |= self._method_rows(conn, method, ns, mac, meta)
            except EmuUnsupported as exc:
                cause = exc.__cause__
                if isinstance(cause, RawRpcError) and cause.code == -32601:
                    conn.missing.add(exc.method)
                if plugin is not None:
                    raise EmuRefused("unsupported", f"The EMU server does not have the {plugin} counters "
                                     f"for this {scope}.") from exc
                if name is not None:
                    unsupported.append(name)
        return {"tables": tables, "unsupported": unsupported}

    @staticmethod
    def _method_rows(conn: _Conn, method: str, ns: NsKey | None, mac: Mac | None,
                     meta: bool) -> dict[str, list[dict[str, Any]]]:
        """One counter method's rows: its meta (fetched once per key for the connection) described values."""
        key = (method, ns, mac)
        if key not in conn.meta:
            conn.meta[key] = conn.rpc.counters(method, ns, mac, meta=True)
        if meta:
            return _rows(conn.meta[key], None)
        return _rows(conn.meta[key], conn.rpc.counters(method, ns, mac))

    # -- writes (P7b) -------------------------------------------------------------------------------
    @staticmethod
    def _check_write(max_rate: float | None, confirm: bool) -> None:
        """EmuRefused `confirm_required` unless `confirm` is True; ValueError for a max_rate outside
        RATE_MIN..RATE_MAX clients per second. Nothing has been sent."""
        if confirm is not True:
            raise EmuRefused("confirm_required", "Replacing what the EMU server holds needs `confirm`.")
        if max_rate is not None and not (type(max_rate) in (int, float) and RATE_MIN <= max_rate <= RATE_MAX):
            raise ValueError(f"max_rate must be {RATE_MIN} to {RATE_MAX} clients per second")

    def _reserve(self, kind: str) -> dict[str, Any]:
        """Mark a write of `kind` ("loading" | "removing") in flight and return its busy record. EmuRefused
        `busy` while another write or a connect is. Published at once, so the page shows it while the
        profile worker runs."""
        if self._busy or self._lock.locked():
            raise EmuRefused("busy", "Another EMU operation is in progress.")
        self._busy = busy = {"kind": kind, "done": 0, "total": 0}
        self._flush()
        return busy

    def _release(self, busy: dict[str, Any]) -> None:
        """End the write that reserved `busy` (unless close() already cleared it) and publish."""
        if self._busy is busy:
            self._busy = None
        self._flush()

    def _advance(self, busy: dict[str, Any], n: int) -> None:
        """`n` more clients done in the write that owns `busy` (called on the loop); publish."""
        if self._busy is busy:
            busy["done"] += n
            self._flush()

    def _begin_phase(self, busy: dict[str, Any], kind: str, total: int) -> None:
        """Start a phase of the write: its kind, 0 done of `total` clients; publish."""
        if self._busy is busy:
            busy.update(kind=kind, done=0, total=total)
            self._flush()

    @staticmethod
    def _refuse_foreign(foreign: list[NsKey], infos: dict[NsKey, tuple[int, list[str]]]) -> None:
        """EmuRefused `foreign_namespaces` naming their count and clients when `foreign` is not empty."""
        if foreign:
            held = sum(infos.get(k, (0, []))[0] for k in foreign)
            raise EmuRefused("foreign_namespaces", f"The server holds {len(foreign)} namespace(s) with "
                             f"{held} client(s) that this backend did not add; acknowledge to replace them.")

    @staticmethod
    def _refuse_crash(foreign: list[NsKey], infos: dict[NsKey, tuple[int, list[str]]]) -> None:
        """EmuRefused `crash_risk` naming the namespaces of `foreign` whose plugins include ipfix or tdl:
        removing one crashes trex-emu v3.08 (measured, ruling R33), so it needs `crash_ack`."""
        risky = [k for k in foreign if CRASH_PLUGINS & set(infos.get(k, (0, []))[1])]
        if risky:
            names = ", ".join(f"vport {k.vport} tci {list(k.tci)} tpid {list(k.tpid)}" for k in risky[:5])
            more = f" and {len(risky) - 5} more" if len(risky) > 5 else ""
            raise EmuRefused("crash_risk", f"Removing {len(risky)} namespace(s) this backend did not add "
                             f"({names}{more}) will crash the EMU server: they have the ipfix or tdl plugin "
                             "at namespace level, and trex-emu v3.08 panics when one is removed. The server "
                             "will need a restart; acknowledge to remove them anyway.")

    @staticmethod
    def _survey(conn: _Conn) -> tuple[Listing[NsKey], dict[NsKey, tuple[int, list[str]]]]:
        """The server's namespaces with each one's client count and plugin names (one executor job)."""
        listing = conn.rpc.list_namespaces()
        infos: dict[NsKey, tuple[int, list[str]]] = {}
        for i in range(0, len(listing.items), CHUNK):
            for row in conn.rpc.ns_info(listing.items[i:i + CHUNK]):
                plugs = row.get("plug_names")
                infos[NsKey.from_wire(row)] = (_int_field(row, "active_clients"),
                                               plugs if isinstance(plugs, list) else [])
        return listing, infos

    @staticmethod
    def _chunk(rate: float | None) -> int:
        """Clients per request: CHUNK, or at most `rate` when one is set (the vendored client's
        min(max_data_rate, chunk_size)), so a slow rate never sends a burst."""
        return CHUNK if rate is None else min(CHUNK, max(1, int(rate)))

    async def _paced(self, pacer: RatePacer, n: int) -> None:
        """After `n` clients were sent: sleep (on the loop, so reads and a cancel are not held up) until the
        pacer's schedule allows the next chunk."""
        wait = pacer.after(n)
        if wait > 0:
            await self._sleep(wait)

    async def _remove_one(self, conn: _Conn, busy: dict[str, Any], key: NsKey, pacer: RatePacer,
                          size: int) -> None:
        """Remove one namespace the way the vendored client does: walk its clients, ctx_client_remove them in
        chunks (paced), then ctx_remove it. A walk another client interrupts is read again, after what it
        listed is removed; TrexApiError when LISTING_RETRIES + 1 walks were all interrupted."""
        for _ in range(1 + LISTING_RETRIES):
            listing = await self._run(conn, conn.rpc.list_clients, key)
            for i in range(0, len(listing.items), size):
                chunk = listing.items[i:i + size]
                await self._run(conn, conn.rpc.remove_clients, key, chunk)
                self._advance(busy, len(chunk))
                await self._paced(pacer, len(chunk))
            if not listing.interrupted:
                break
        else:
            raise TrexApiError("Another EMU client keeps changing the namespace's clients; "
                               "they could not all be removed.")
        await self._run(conn, conn.rpc.remove_namespaces, [key])

    async def _remove_all(self, conn: _Conn, busy: dict[str, Any], first: Listing[NsKey],
                          infos: dict[NsKey, tuple[int, list[str]]], rate: float | None,
                          progress: "_Progress", ours: frozenset[NsKey], foreign_ack: bool,
                          crash_ack: bool = False) -> None:
        """Remove every namespace of `first`; when that listing was interrupted, list again and remove what is
        left, at most LISTING_RETRIES more times (TrexApiError when namespaces are still there). A namespace
        that has ipfix or tdl (its `plug_names` in `infos`) crashes trex-emu when removed (rulings R10, R33):
        the lost connection then says so. A namespace of a later listing that is not in `ours` (the
        start-of-write snapshot) is foreign: without `foreign_ack` the removal stops before it with EmuRefused
        `foreign_namespaces`, and without `crash_ack` with `crash_risk` when it has such a plugin."""
        pacer = RatePacer(rate, self._clock)
        size = self._chunk(rate)
        listing = first
        for _ in range(1 + LISTING_RETRIES):
            if listing is not first and not foreign_ack:
                self._refuse_foreign([k for k in listing.items if k not in ours], infos)
            if listing is not first and not crash_ack:
                self._refuse_crash([k for k in listing.items if k not in ours], infos)
            for key in listing.items:
                crashers = sorted(CRASH_PLUGINS & set(infos.get(key, (0, []))[1]))
                progress.mutated = True
                try:
                    await self._remove_one(conn, busy, key, pacer, size)
                except ServerUnreachable as exc:
                    if crashers:
                        raise ServerUnreachable(
                            "The EMU server stopped answering while a namespace that loaded the "
                            f"{' and '.join(crashers)} plugin was being removed. trex-emu crashes on that (a "
                            "known fault of the shipped server); restart it and connect again. "
                            f"({exc.message})") from exc
                    raise
                progress.removed_ns += 1
                self._ours.discard(key)
                self._rev += 1
            if not listing.interrupted:
                return
            listing, infos = await self._run(conn, self._survey, conn)
            progress.removal_total += len(listing.items)
            if not listing.items and not listing.interrupted:
                return
        raise TrexApiError("Another EMU client keeps changing the namespaces; they could not all be removed.")

    async def _add_profile(self, conn: _Conn, busy: dict[str, Any], profile: dict[str, Any],
                           rate: float | None, progress: "_Progress") -> None:
        """The vendored client's load sequence on the server: default namespace plugins, the namespaces in
        chunks, then per namespace its default client plugins and its clients (in chunks, paced to `rate`)."""
        pacer = RatePacer(rate, self._clock)
        size = self._chunk(rate)
        spaces = profile["namespaces"]
        progress.mutated = True
        await self._run(conn, conn.rpc.set_def_ns_plugins, profile["def_ns_plugs"])
        for i in range(0, len(spaces), CHUNK):
            chunk = spaces[i:i + CHUNK]
            await self._run(conn, conn.rpc.add_namespaces, [ns["fields"] for ns in chunk])
            progress.added_ns += len(chunk)
            self._ours.update(_key_of(ns["key"]) for ns in chunk)
            self._rev += 1
        for ns in spaces:
            await self._run(conn, conn.rpc.set_def_client_plugins, ns["key"], ns["def_c_plugs"])
            rows = ns["clients"]
            for i in range(0, len(rows), size):
                chunk = rows[i:i + size]
                await self._run(conn, conn.rpc.add_clients, ns["key"], chunk)
                progress.added_clients += len(chunk)
                self._advance(busy, len(chunk))
                await self._paced(pacer, len(chunk))

    async def _replace(self, conn: _Conn, busy: dict[str, Any], loaded: LoadedEmuProfile | None,
                       tunables: list[str], rate: float | None, foreign_ack: bool, crash_ack: bool) -> None:
        """Replace what the server holds with `loaded` (or with nothing): survey it, refuse
        `foreign_namespaces` without the ack and `crash_risk` without `crash_ack`, remove every namespace,
        then add the profile. The generation bumps afterwards however it ended; a failure names how far it
        got, and a load that added anything is recorded as partial."""
        progress = _Progress(total_ns=len(loaded.profile["namespaces"]) if loaded else 0,
                             total_clients=loaded.clients if loaded else 0)
        try:
            ours = frozenset(self._ours)
            listing, infos = await self._run(conn, self._survey, conn)
            if not foreign_ack:
                if listing.interrupted:         # a partial listing cannot rule foreign namespaces out
                    raise EmuRefused("foreign_namespaces", "Another EMU client kept changing the namespaces "
                                     "while they were listed, so namespaces this backend did not add cannot "
                                     "be ruled out; try again, or acknowledge to replace whatever is there.")
                self._refuse_foreign([k for k in listing.items if k not in ours], infos)
            if not crash_ack:
                self._refuse_crash([k for k in listing.items if k not in ours], infos)
            progress.removal_total = len(listing.items)
            self._begin_phase(busy, "removing", sum(c for c, _ in infos.values()))
            if self._loaded:
                self._loaded = {**self._loaded, "partial": True}
            await self._remove_all(conn, busy, listing, infos, rate, progress, ours, foreign_ack, crash_ack)
            self._loaded = None
            if loaded is not None:
                self._begin_phase(busy, "loading", loaded.clients)
                progress.adding = True
                await self._add_profile(conn, busy, loaded.profile, rate, progress)
                self._loaded = {"path": loaded.path, "tunables": list(tunables),
                                "namespaces": loaded.namespaces, "clients": loaded.clients,
                                "at": time.time(), "partial": False}
            progress.completed = True
        except asyncio.CancelledError:
            self._record_partial(loaded, tunables, progress)
            raise
        except TrexApiError as exc:
            self._record_partial(loaded, tunables, progress)
            raise self._annotated(exc, progress) from exc
        finally:
            if progress.mutated or progress.completed:
                self._generation += 1
                self._rev += 1

    def _record_partial(self, loaded: LoadedEmuProfile | None, tunables: list[str],
                        progress: "_Progress") -> None:
        """After a load that stopped in its adding phase: `loaded` says what the server now holds, partial."""
        if loaded is not None and progress.added_ns + progress.added_clients:
            self._loaded = {"path": loaded.path, "tunables": list(tunables), "namespaces": progress.added_ns,
                            "clients": progress.added_clients, "at": time.time(), "partial": True}

    def _annotated(self, exc: TrexApiError, progress: "_Progress") -> TrexApiError:
        """`exc` with how far the write got appended to its message: a lost connection stays one (and says so
        in the session's error as well), any other server error becomes a plain TrexApiError. `exc` itself
        when nothing was sent or it is a refusal."""
        if not progress.mutated:
            return exc
        lost = isinstance(exc, EmuRefused) and exc.code == "not_connected" and self._state == "lost"
        if isinstance(exc, EmuRefused) and exc.code not in ("foreign_namespaces", "crash_risk") and not lost:
            return exc
        if progress.added_ns or progress.added_clients or progress.adding:
            where = (f"removed all; added {progress.added_ns} of {progress.total_ns} namespaces and "
                     f"{progress.added_clients} of {progress.total_clients} clients; "
                     "the server now holds a partial profile")
        else:
            where = (f"removed {progress.removed_ns} of {progress.removal_total} namespaces; "
                     "the server holds the rest")
        cls: type[TrexApiError]
        if lost:                    # the ping loop found the server gone between two of the write's jobs
            cls = ServerUnreachable
        elif isinstance(exc, EmuRefused):
            if exc.code == "crash_risk":
                return EmuRefused("crash_risk", f"{exc.message} ({where})")
            return EmuRefused("foreign_namespaces", f"{exc.message} ({where})")
        else:
            cls = type(exc) if type(exc) in (ServerUnreachable, ServerRestarted) else TrexApiError
        new = cls(f"{exc.message} ({where})")
        if self._state == "lost":
            self._error = new.message
        return new

    async def load_profile(self, path: str, tunables: list[str], *, max_rate: float | None = None,
                           confirm: bool = False, foreign_ack: bool = False,
                           crash_ack: bool = False) -> dict[str, Any]:
        """Replace what the EMU server holds with the profile at `path` (run with `tunables` in the profile
        worker): remove every namespace there, then add the profile's, at most `max_rate` clients per second.
        Returns the snapshot. Refused before any RPC: EmuRefused `confirm_required`, ValueError for a bad
        max_rate, EmuRefused `not_connected` and `busy`; ProfileError when the profile does not load. Then
        one listing: EmuRefused `foreign_namespaces` unless `foreign_ack` when the server holds namespaces
        this backend did not add, then EmuRefused `crash_risk` unless `crash_ack` when one of those has the
        ipfix or tdl plugin at namespace level (removing it crashes trex-emu). Any later failure is annotated
        with how far the load got."""
        self._check_write(max_rate, confirm)
        conn = self._need_conn()
        busy = self._reserve("loading")
        try:
            loaded = await load_emu_profile(path, tunables)
            await self._replace(conn, busy, loaded, tunables, max_rate, foreign_ack, crash_ack)
        finally:
            self._release(busy)
        return self.snapshot()

    async def remove_all(self, *, max_rate: float | None = None, confirm: bool = False,
                         foreign_ack: bool = False, crash_ack: bool = False) -> dict[str, Any]:
        """Remove every namespace and client from the EMU server (each namespace's clients in chunks, then
        the namespace). Returns the snapshot. The refusals and the failure annotation are load_profile's."""
        self._check_write(max_rate, confirm)
        conn = self._need_conn()
        busy = self._reserve("removing")
        try:
            await self._replace(conn, busy, None, [], max_rate, foreign_ack, crash_ack)
        finally:
            self._release(busy)
        return self.snapshot()


@dataclass
class _Progress:
    """How far one write got, for its error message: totals asked for, counts done."""
    total_ns: int = 0
    total_clients: int = 0
    removal_total: int = 0
    removed_ns: int = 0
    added_ns: int = 0
    added_clients: int = 0
    mutated: bool = False       # a write RPC may have reached the server
    completed: bool = False     # the write ran to its end (the generation bumps even if it sent nothing)
    adding: bool = False        # the adding phase began


def _key_of(wire: dict[str, Any]) -> NsKey:
    """The NsKey of a validated profile key {vport, tci, tpid}; a tag list shorter than two is padded with 0,
    as the server lists it."""
    def pad(tags: list[int]) -> tuple[int, int]:
        """`tags` as a pair."""
        t = [*tags, 0, 0][:2]
        return (t[0], t[1])
    return NsKey(wire["vport"], pad(wire["tci"]), pad(wire["tpid"]))
