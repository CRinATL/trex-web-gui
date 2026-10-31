"""One TRex server connection (plan §4.1) with two clients: the control client and a read-only reader (never
acquires, never releases and sends no write RPC; a capture `fetch` drains our own buffer and changes no
port state, it is the one read with a side effect the reader sends). Each client is touched only by its own
executor thread, `_executor` for the control client and `_reader_executor` for the reader, with two
exceptions on the loop thread, which apply to both clients. (1) connect() reads `conn.is_connected()` /
`conn.get_disconnection_cause()`; those are plain attribute reads, benign under the GIL. (2) close() calls
trexapi.fail_fast(), a plain int store (trex_req_resp_client.py:97-101); queued on an executor it would wait
behind the very RPC it exists to cut short (controller ruling R7). Client events arrive on each client's
subscriber thread and are handed to the loop with call_soon_threadsafe."""
import asyncio
import contextlib
import dataclasses
import json
import logging
import socket
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from trex_web_gui import trexapi
from trex_web_gui.hostinfo import read_cfg, resolve
from trex_web_gui.trexapi import ClientEvent, TrexApiError

from .capture import CaptureCommands
from .port_config import PortConfigCommands

log = logging.getLogger(__name__)
_T = TypeVar("_T")
RPC_BODY_MAX = 64 * 1024               # most characters of a body's JSON text the RPC console publishes
_PROCESS_START_NS = time.time_ns()     # prefix of connect_seq: a GUI restart never repeats a value
Publish = Callable[[dict[str, object]], None]


class SessionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    LOST = "lost"


class SessionBusy(TrexApiError):
    pass


@dataclass(frozen=True)
class ConnectParams:
    host: str
    sync_port: int = 4501
    async_port: int = 4500
    timeout_s: float = 3.0       # A1.1 default
    read_only: bool = False      # A1.4 observer: never acquires; mutating commands are refused


@dataclass
class ServerInfo:
    version: str
    mode: str
    build_date: str
    hostname: str
    port_count: int
    core_type: str
    ports: list[dict[str, object]]


class TrexSession(PortConfigCommands, CaptureCommands):
    def __init__(self, publish: Publish, *, poll_s: float = 2.0, stats_s: float = 1.0,
                 release_on_exit: Callable[[], bool] = lambda: True,
                 trex_cfg_path: Callable[[], str] = lambda: "/etc/trex_cfg.yaml",
                 hostinfo_root: Path = Path("/"), spool_dir: Path | None = None) -> None:
        """A DISCONNECTED session that connects nothing yet: one single-thread executor per client (`trex`
        for the control client, `trex-reader` for the reader), the connect/disconnect lock and the capture
        state."""
        self._publish = publish
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trex")
        self._lock = asyncio.Lock()
        self._reader_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trex-reader")
        self._client: Any = None
        self._reader: Any = None                   # the read-only second client; None when not connected
        self._state = SessionState.DISCONNECTED
        self._params: ConnectParams | None = None
        self._info: ServerInfo | None = None
        self._error: str | None = None
        self._pending_loss: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._poll_s = poll_s
        self._stats_s = stats_s
        self._release_on_exit = release_on_exit
        self._trex_cfg_path = trex_cfg_path
        self._hostinfo_root = hostinfo_root
        self._ifaces: list[dict[str, Any]] = []    # loop thread; resolved once per connect, kept when LOST
        self._ifaces_note: str | None = None
        self._ports: list[trexapi.PortView] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._stats_task: asyncio.Task[None] | None = None
        self._refresh_pending = False
        self._rpc_console = False
        self._include_polling = False
        self._restart_cause: str | None = None     # loop thread only; set via _note_restart
        self._timeout_seen = False                 # a "subscriber timeout" came in during this connection
        self._bg: set[asyncio.Task[Any]] = set()
        self._connect_n = 0                        # loop thread; the n of snapshot()'s connect_seq
        self._stats_epoch = 0                      # bumped on the reader executor by a reset
        self._ping_runs = 0                        # loop thread; the last ping run id handed out
        self._busy: dict[int, str] = {}            # loop thread; port -> "ping" | "scan" | "apply"
        self._assigned: dict[int, dict[str, Any]] = {}   # loop thread; port -> this connection's last Assign
        self._init_capture(spool_dir)

    # -- public -------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        """The `session` message: state, params, info, error, the RPC console flags, connect_seq and the
        interfaces. Publishes nothing."""
        return {"type": "session", "state": str(self._state),
                "params": dataclasses.asdict(self._params) if self._params else None,
                "info": dataclasses.asdict(self._info) if self._info else None, "error": self._error,
                "rpc_console": self._rpc_console, "include_polling": self._include_polling,
                "connect_seq": f"{_PROCESS_START_NS}:{self._connect_n}",
                "ifaces": self._ifaces, "ifaces_note": self._ifaces_note}

    async def connect(self, p: ConnectParams) -> ServerInfo:
        """Connect the control client and the reader at once, each on its own executor; returns the server
        info. Refused with TrexApiError once closed, and with SessionBusy while the lock is held or the
        session is CONNECTING or CONNECTED. Holds `_lock` throughout: disconnects a client left from a LOST
        session with release=True (its ports are released on the server), forgets the params, ports,
        interfaces and capture state (publishing `capture` when there was any), then goes CONNECTING. A
        connect that fails before CONNECTED goes DISCONNECTED with the error and raises it translated; a
        cancel releases both clients and goes DISCONNECTED, and the release waits for the connect jobs however
        often the caller is cancelled meanwhile, so a cancel can take up to the whole connect (N2); a loss
        seen during the connect goes LOST and raises TrexApiError. A cancel after CONNECTED (the ports
        lookup, the interface lookup) is not a connect cancel: it propagates and the session stays CONNECTED
        with both clients. Otherwise CONNECTED: starts the port poll and stats loops, sets the ports
        (publishing `ports`), looks up the interfaces off the loop (a failure becomes `ifaces_note`) and
        publishes `session`; then, only if still CONNECTED, lists the server's captures and starts the
        capture loop (a failure there is logged, not raised)."""
        if self._closed:
            raise TrexApiError("This session is closed; create a new one.")
        if self._lock.locked() or self._state in (SessionState.CONNECTING, SessionState.CONNECTED):
            raise SessionBusy(f"Already {self._state}; disconnect first.")
        async with self._lock:
            self._loop = asyncio.get_running_loop()
            await self._drop_client()                    # a LOST client from before
            self._params, self._info, self._error = p, None, None
            self._restart_cause = None
            self._timeout_seen = False
            self._ports = []                             # silent: the next _set_ports must broadcast
            self._ifaces, self._ifaces_note = [], None
            if self._reset_capture_state():              # a stale monitor id must never reach the next server
                self._publish(self.capture_snapshot())
            self._connect_n += 1                         # before the CONNECTING snapshot below carries it
            self._assigned.clear()                       # a new connection knows no assignment yet
            self._set(SessionState.CONNECTING)
            self._pending_loss = None
            job = self._executor.submit(self._connect_blocking, p)
            result = asyncio.wrap_future(job)
            rjob = self._reader_executor.submit(self._connect_reader_blocking, p)
            rresult = asyncio.wrap_future(rjob)
            control: tuple[Any, ServerInfo] | BaseException
            rdr: Any
            try:                                         # both connects run at once; wait for both to settle
                control, rdr = await asyncio.shield(asyncio.gather(result, rresult, return_exceptions=True))
            except asyncio.CancelledError:
                await self._abandon_connect(job, result, rjob, rresult)
                raise
            if isinstance(control, asyncio.CancelledError) or isinstance(rdr, asyncio.CancelledError):
                # a job came back cancelled: not an error to translate
                await self._abandon_connect(job, result, rjob, rresult)
                raise asyncio.CancelledError
            if isinstance(control, BaseException):       # control error wins; it released its own client
                err = trexapi.translate(control)
                self._log_connect_failure(p, err)
                if not isinstance(rdr, BaseException):
                    cleanup = self._reader_executor.submit(self._disconnect_blocking, rdr, False)
                    try:
                        await self._await_done(asyncio.wrap_future(cleanup))
                    finally:
                        self._error = err.message
                        self._set(SessionState.DISCONNECTED)
                else:
                    self._error = err.message
                    self._set(SessionState.DISCONNECTED)
                raise err from control
            client, info = control
            if isinstance(rdr, BaseException):
                err = trexapi.translate(rdr)             # _connect_reader_blocking released the reader
                self._log_connect_failure(p, err)
                cleanup = self._executor.submit(self._disconnect_blocking, client, True)
                try:
                    await self._await_done(asyncio.wrap_future(cleanup))
                finally:                                 # a cancel here must not leave CONNECTING behind
                    self._error = err.message
                    self._set(SessionState.DISCONNECTED)
                raise err from rdr
            reader = rdr
            self._client, self._info, self._reader = client, info, reader
            try:
                await asyncio.sleep(0)                   # drain handoffs queued by call_soon_threadsafe
            except asyncio.CancelledError:
                self._client = self._reader = None       # _abandon_connect releases them via the jobs
                await self._abandon_connect(job, result, rjob, rresult)
                raise
            cause = self._pending_loss
            for c in (self._client, self._reader):
                if cause is None and not c.conn.is_connected():
                    cause = str(c.conn.get_disconnection_cause() or "lost")
            if cause:                                    # lost while connecting: never report CONNECTED
                self._error = cause
                self._set(SessionState.LOST)
                raise TrexApiError(f"Connection lost during connect: {cause}")
            self._set(SessionState.CONNECTED)
            self._poll_task = asyncio.create_task(self._poll_loop())   # before the await below
            self._stats_task = asyncio.create_task(self._stats_loop())
            self._set_ports(await self._run(trexapi.port_views, self._client))
            # the worker gets plain values and touches no session state; a CancelledError here propagates and
            # the session stays CONNECTED, any other failure becomes a note (the connect itself succeeded)
            cfg_path, ports, hostname = self._trex_cfg_path(), [dict(p) for p in self._ports], info.hostname
            try:
                self._ifaces, self._ifaces_note = await asyncio.to_thread(
                    self._resolve_ifaces_blocking, cfg_path, ports, hostname)
            except Exception as e:
                log.warning("interface lookup failed", exc_info=True)
                self._ifaces, self._ifaces_note = [], f"interface lookup failed: {e}"
            self._publish(self.snapshot())
            try:
                if self._state != SessionState.CONNECTED:
                    return info                          # lost meanwhile: the loss handling owns it
                await self.capture_sync()                # lists captures that already exist, foreign ones too
                self._ensure_capture_loop()
            except Exception as e:                       # the session is CONNECTED; CancelledError propagates
                self._log("warning", f"Capture list failed: {trexapi.translate(e).message}")
            return info

    async def disconnect(self, release: bool = True) -> None:
        """Holds `_lock`: stops the capture loop, stops and removes this session's own monitor (only while
        CONNECTED, under `_capture_start_lock`), forgets the capture state (stopping the Wireshark pipe),
        disconnects both clients (the control client releases its ports when `release`; the reader never
        does) and goes DISCONNECTED with no error. Publishes `capture` when there was capture state, an empty
        `ports` and `session`. Refuses nothing: a client's disconnect error is swallowed."""
        async with self._lock:
            self._ending = True                          # a monitor_start finishing must not restart the loop
            try:
                await self._stop_capture_loop()          # first: it must not see the monitor vanish
                await self._remove_own_monitor()
                had = self._reset_capture_state()
                await self._drop_client(release)
                if had:
                    self._publish(self.capture_snapshot())
                self._restart_cause = None
                self._ports = []
                self._ifaces, self._ifaces_note = [], None
                self._publish({"type": "ports", "ports": []})
                self._error = None
                self._set(SessionState.DISCONNECTED)
            finally:
                self._ending = False

    async def close(self) -> None:
        """End the session for good; a second call returns at once. On the loop thread it sets both clients
        to fail fast, cancels this loop's background tasks, kills every Wireshark the session launched, then
        disconnects (releasing ports when `release_on_exit()` says so) and shuts both executors down off the
        loop, cancelling their queued jobs; the shutdowns run even when the disconnect raises or is
        cancelled."""
        if self._closed:
            return
        self._closed = True
        for c in (self._client, self._reader):
            if c is not None:
                trexapi.fail_fast(c)
        loop = asyncio.get_running_loop()
        for t in list(self._bg):              # a closed loop's task cannot be cancelled (call_soon raises)
            if t.get_loop() is loop:
                t.cancel()
        await self._kill_wireshark()          # first: disconnect may wait out a silent server
        try:
            await self.disconnect(release=self._release_on_exit())
        finally:                              # neither a raise nor a cancel may skip a shutdown
            try:
                await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
            finally:
                await asyncio.to_thread(self._reader_executor.shutdown, wait=True, cancel_futures=True)

    # -- ports and commands -------------------------------------------------------------------
    def ports(self) -> list[trexapi.PortView]:
        """The last published PortViews, the list itself rather than a copy; empty after connect starts and
        after a disconnect, kept when LOST."""
        return self._ports

    def busy(self) -> dict[int, str]:
        """The ports a ping, scan or Apply holds ("ping" | "scan" | "apply"), as published in `busy`."""
        return dict(self._busy)

    def set_rpc_console(self, enabled: bool, include_polling: bool | None = None) -> None:
        """include_polling None keeps the current value (toggling Capture must not reset it); the default
        at session start is False, so polling RPCs are not captured. Logs the change and publishes
        `session`."""
        self._rpc_console = enabled
        if include_polling is not None:
            self._include_polling = include_polling
        self._log("info", f"Raw JSON-RPC console {'on' if enabled else 'off'}, "
                          f"polling {'included' if self._include_polling else 'hidden'}")
        self._publish(self.snapshot())      # the flag is session state, so a reloaded tab sees it

    async def acquire(self, ports: list[int], force: bool = False) -> None:
        """Acquire `ports` (`force` takes them from their owner) through _command; a port need not be owned
        and a busy one is not refused, only a missing one. Without `force`, a port the cache shows owned by
        another client is refused not_owned before the RPC (controller ruling R7)."""
        label = f"Acquire port(s) {ports}" + (" (force)" if force else "")

        # control executor: the owner check on the cache, then the RPC
        def blocking(c: Any) -> Any:
            if not force:
                trexapi.check_not_foreign(c, ports)
            return trexapi.acquire_marked(c, ports, force)
        await self._command(label, blocking, ports=ports, need_owned=False)
        self._forget_assigned(ports)                 # what another owner left there is not this session's

    async def release(self, ports: list[int]) -> None:
        """Release `ports` through _command; refused unless owned, or while a ping or scan holds one (an Apply
        does not block it)."""
        await self._command(f"Release port(s) {ports}", lambda c: c.release(ports=ports), ports=ports,
                            guard=True)
        self._forget_assigned(ports)

    async def reacquire(self) -> list[int]:
        """A1.6, explicit (legacy did this automatically): force-acquire the ports the server lists as
        owned by this OS user but held by no handler of this session, e.g. after a reconnect with
        release-on-exit off."""
        # control executor: force-acquires the cached ports owned by this user and not acquired by this client
        def blocking(c: Any) -> list[int]:
            mine = sorted(pid for pid, p in c.ports.items()
                          if not p.is_acquired() and p.owner == c.ctx.username)
            if mine:
                trexapi.acquire_marked(c, mine, True)
            return mine
        result: list[int] = await self._command("Re-acquire my ports", blocking)
        return result

    def require_control(self, mutating: bool = True) -> Any:
        """The refusals every command makes before any RPC; returns the client. The API calls it before
        loading a profile, so a refused assign does not first run the profile subprocess."""
        c = self._client
        if c is None or self._state != SessionState.CONNECTED:
            raise trexapi.CommandRefused("not_connected", "Not connected to a TRex server.")
        if mutating and self._params is not None and self._params.read_only:
            raise trexapi.CommandRefused("read_only", "This session is read-only (observer mode). Reconnect "
                                         "without read-only to control ports.")
        return c

    async def _command(self, label: str, fn: Callable[[Any], Any], *, ports: list[int] | None = None,
                       need_owned: bool = True, mutating: bool = True, guard: bool = False) -> Any:
        """Run `fn(c)` as one control-executor job and return its result. Refused first by require_control
        and, with `guard`, by port_busy while a ping or scan holds a port; check_ports runs inside the job
        when `ports` is given. Logs the job's outcome under `label`. On success it refreshes the port cache,
        which publishes `ports` only while `c` is still the client; a refresh failure then propagates. A
        CommandRefused from the job is logged as a warning and re-raised, with no refresh. Any other failure
        is raised translated: a loss-class one marks the session lost, any other refreshes first (a refresh
        error there is suppressed). Takes no lock."""
        c = self.require_control(mutating)
        if guard and ports:
            self._refuse_busy(label, ports, held_only=True)

        # control executor: check_ports when ports were given, then fn
        def blocking() -> Any:
            if ports is not None:
                trexapi.check_ports(c, ports, need_owned=need_owned)
            return fn(c)

        try:
            result = await self._run(blocking)
        except trexapi.CommandRefused as e:
            self._log("warning", f"{label}: {e.message}")
            raise
        except Exception as e:
            err = trexapi.translate(e)
            self._log("error", f"{label}: {err.message}")
            if isinstance(err, trexapi.ServerUnreachable | trexapi.ServerRestarted):
                self._mark_lost(err.message)
            else:
                with contextlib.suppress(Exception):         # the command's own error must win
                    await self._refresh_now(c)
            raise err from e
        self._log("info", f"{label}: done")
        await self._refresh_now(c)
        return result

    @staticmethod
    def _refuse_active(c: Any, ports: list[int], stop_first: bool) -> list[int]:
        """The ports of `ports` that are transmitting, from the client's cache (call it in an executor job);
        CommandRefused port_active when there are any and `stop_first` is False."""
        active = [p for p in ports if c.ports[p].is_active()]
        if active and not stop_first:
            raise trexapi.CommandRefused(
                "port_active", f"Port(s) {active} are transmitting; stop them first or confirm "
                "stop-and-replace.")
        return active

    @staticmethod
    def _require_profile_state(c: Any, ports: list[int], wanted: set[int], what: str) -> None:
        """Refuse, from the client's cache and before any RPC, when the default profile "_" of a port is
        not in a wanted state: the client's own check looks at every profile and answers with a bare
        "are not active" when only a named profile (started by someone else) is running."""
        bad = [p for p in ports if c.ports[p].profile_state_list.get("_") not in wanted]
        if bad:
            raise trexapi.CommandRefused(
                "not_active", f"Port(s) {bad}: profile '_' is {what}; a named profile started by another "
                "client is not controlled from here (Stop stops every profile).")

    async def precheck_assign(self, ports: list[int], stop_first: bool = False) -> None:
        """Cache-only refusals (not_owned, port_active), asked before the profile subprocess runs; the
        authoritative check stays inside assign_profile."""
        c = self.require_control()

        # control executor: bad_port / not_owned, then port_active
        def blocking() -> None:
            trexapi.check_ports(c, ports, need_owned=True)
            self._refuse_active(c, ports, stop_first)
        try:
            await self._run(blocking)
        except trexapi.CommandRefused as e:
            self._log("warning", f"Assign to port(s) {ports}: {e.message}")
            raise

    async def assign_profile(self, ports: list[int], loaded: trexapi.LoadedProfile,
                             stop_first: bool = False,
                             tunables: list[str] | None = None) -> dict[int, dict[str, object]]:
        """A3.1: stop (only when asked), remove all streams, add the profile's streams per port, then
        validate each port and return its rate info. Streams belong to profile_id "_" (other profiles
        are shown read-only, A3.8). On success each port records the profile and `tunables` (assigned());
        a failure forgets the ports' records, since their streams were removed."""
        missing = [p for p in ports if p not in loaded.streams_by_port]
        if missing:
            raise trexapi.CommandRefused("bad_port", f"The profile was not loaded for port(s) {missing}.")

        # control executor, one job: the A3.1 sequence; a failure after the removal names the emptied ports
        def blocking(c: Any) -> dict[int, dict[str, object]]:
            active = self._refuse_active(c, ports, stop_first)
            if active:
                self._stop_all_profiles(c, active)
            c.remove_all_streams(ports=ports)
            for p in ports:
                try:
                    c.add_streams(trexapi.streams_from_json(loaded.streams_by_port[p]), ports=[p])
                except Exception as e:
                    left = ports[ports.index(p):]      # remove_all_streams emptied every assigned port
                    empty = f"Port {left[0]} now has" if len(left) == 1 else f"Ports {left} now have"
                    raise trexapi.TrexApiError(f"{empty} no streams: adding the profile failed on port {p}: "
                                               f"{trexapi.translate(e).message}") from e
            rates: dict[int, dict[str, object]] = {}
            for p in ports:                  # A3.1 ends with validate: the server compiles the streams here
                rc = c.ports[p].validate()
                if rc.bad():
                    with contextlib.suppress(Exception):    # the rejected streams must not stay on the ports
                        c.remove_all_streams(ports=ports)
                    empty = f"Port {ports[0]} now has" if len(ports) == 1 else f"Ports {ports} now have"
                    raise trexapi.TrexApiError(
                        f"{empty} no streams: the server rejected the assigned profile on port {p} "
                        f"on validate: {rc.err()}")
                rates[p] = dict(c.ports[p].get_profile() or {})
            return rates
        name = loaded.path.rsplit("/", 1)[-1]
        try:
            result: dict[int, dict[str, object]] = await self._command(
                f"Assign {name} to port(s) {ports}", blocking, ports=ports)
        except trexapi.CommandRefused:
            raise                                    # refused before the streams changed: the records stand
        except Exception:
            self._forget_assigned(ports)
            raise
        for p in ports:
            self._assigned[p] = {"path": loaded.path, "name": name, "tunables": list(tunables or []),
                                 "summary": loaded.summary, "stream_count": loaded.stream_count}
        return result

    def _forget_assigned(self, ports: list[int]) -> None:
        """Drops the Assign records of `ports` (loop thread)."""
        for p in ports:
            self._assigned.pop(p, None)

    async def assigned(self, port: int) -> dict[str, Any]:
        """What is on `port`, as far as this session knows: `streams` is whether the port's default profile
        holds streams (the client's cache), and `assigned` the record of this connection's last Assign to it
        (path, name, tunables, summary, stream_count), or None when there is none or the port no longer holds
        streams or is no longer owned. Start sends what is on the port, not a path (RC1-30). Refused only when
        not connected or for an unknown port."""
        c = self.require_control(mutating=False)

        # control executor: whether the port is owned and its profile "_" holds streams, from the cache
        def blocking() -> tuple[bool, bool]:
            p = c.ports.get(port)
            if p is None:
                raise trexapi.CommandRefused("bad_port", f"There is no port {port}.")
            return bool(p.is_acquired()), "_" in (getattr(p, "profile_state_list", None) or {})
        owned, streams = await self._run(blocking)
        if not owned or not streams:
            self._forget_assigned([port])
        return {"streams": streams, "assigned": self._assigned.get(port)}

    async def start(self, ports: list[int], mult: str, duration: float = -1, force: bool = False) -> None:
        """Start traffic on `ports` at `mult` (for `duration` s when positive; `force` goes to
        STLClient.start) through _command; refused unless owned, or while a ping or scan holds a port. The
        server's dest-MAC refusal is raised as trexapi.explain_dst_mac's explanation instead."""
        label = f"Start port(s) {ports} at {mult}" + (f" for {duration:g} s" if duration > 0 else "")

        # control executor: start; the server's dest-MAC refusal is replaced by what to do about it (RC1-30)
        def blocking(c: Any) -> None:
            try:
                c.start(ports=ports, mult=mult, duration=duration, force=force)
            except Exception as e:
                why = trexapi.explain_dst_mac(c, trexapi.translate(e).message)
                if why is None:
                    raise
                raise TrexApiError(why) from e
        await self._command(label, blocking, ports=ports, guard=True)

    async def update(self, ports: list[int], mult: str) -> None:
        """Change the rate of `ports` to `mult` through _command; refused unless owned, or unless profile "_"
        transmits on each (the ports are synced first, so the check sees the server's state)."""
        # control executor: sync the ports, the profile "_" check, then the update RPC
        def blocking(c: Any) -> None:
            for p in ports:
                trexapi.sync_port(c, p)
            self._require_profile_state(c, ports, {c.ports[p].STATE_TX for p in ports}, "not transmitting")
            c.update(ports=ports, mult=mult)
        await self._command(f"Update port(s) {ports} to {mult}", blocking, ports=ports)

    async def pause(self, ports: list[int]) -> None:
        """Pause `ports` through _command; refused unless owned, or unless profile "_" transmits on each (the
        ports are synced first, so the check sees the server's state)."""
        # control executor: sync the ports, the profile "_" check, then the pause RPC
        def blocking(c: Any) -> None:
            for p in ports:
                trexapi.sync_port(c, p)
            self._require_profile_state(c, ports, {c.ports[p].STATE_TX for p in ports}, "not transmitting")
            c.pause(ports=ports)
        await self._command(f"Pause port(s) {ports}", blocking, ports=ports)

    async def resume(self, ports: list[int]) -> None:
        """Resume `ports` through _command; refused unless owned, or unless profile "_" is paused on each (the
        ports are synced first, so the check sees the server's state)."""
        # control executor: sync the ports, the profile "_" check, then the resume RPC
        def blocking(c: Any) -> None:
            for p in ports:
                trexapi.sync_port(c, p)
            self._require_profile_state(c, ports, {c.ports[p].STATE_PAUSE for p in ports}, "not paused")
            c.resume(ports=ports)
        await self._command(f"Resume port(s) {ports}", blocking, ports=ports)

    @staticmethod
    def _stop_all_profiles(c: Any, ports: list[int]) -> None:
        """Stop every profile of the ports. "N.*" expands to the profiles the client has cached
        (trex_stl_client.py:313, trex_psv.py:260-262), so each port is synced first: the expansion runs over
        the profiles the server reports, with idle ones dropped, and a profile another client started is
        stopped. Only these ports are synced."""
        for p in ports:
            trexapi.sync_port(c, p)
        c.stop(ports=[f"{p}.*" for p in ports])

    async def stop(self, ports: list[int]) -> None:
        """Sync `ports` from the server, then stop every profile it reports on them (_stop_all_profiles),
        through _command; refused unless owned, never for a busy port."""
        # STLClient.stop also removes the RX filters after rx_delay_ms (A3.5; trex_stl_client.py:896-904)
        await self._command(f"Stop port(s) {ports}", lambda c: self._stop_all_profiles(c, ports), ports=ports)

    async def set_service(self, ports: list[int], enabled: bool, filtered: bool = False,
                          mask: int | None = None) -> None:
        """Set service mode on `ports` through _command: on or off by `enabled`, or filtered with `mask` when
        `filtered` (which wins); refused unless owned, or while a ping or scan holds a port."""
        mode = f"filtered (mask {mask})" if filtered else ("on" if enabled else "off")
        await self._command(
            f"Service mode {mode} on port(s) {ports}",
            lambda c: c.set_service_mode(ports=ports, enabled=enabled and not filtered, filtered=filtered,
                                         mask=mask if filtered else None), ports=ports, guard=True)

    async def set_port_attr(self, ports: list[int], *, promiscuous: bool | None = None,
                            multicast: bool | None = None, link_up: bool | None = None,
                            led_on: bool | None = None, flow_ctrl: int | None = None) -> None:
        """Set the given port attributes (None leaves one unchanged) through _command; refused with
        bad_request when none is given, unless owned, or while a ping or scan holds a port."""
        wanted = dict(promiscuous=promiscuous, multicast=multicast, link_up=link_up, led_on=led_on,
                      flow_ctrl=flow_ctrl)
        kw = {k: v for k, v in wanted.items() if v is not None}
        if not kw:
            raise trexapi.CommandRefused("bad_request", "No attribute to change.")
        await self._command(
            f"Set {kw} on port(s) {ports}", lambda c: c.set_port_attr(ports=ports, **kw), ports=ports,
            guard=True)

    async def reset(self, ports: list[int]) -> None:
        """Reset `ports` through _command (STLClient.reset, below): ownership is not needed, a port a ping or
        scan holds is refused. It also removes every capture on the server, ours and foreign, whatever ports
        were named. Once c.reset was entered, and only while the same reader is CONNECTED, one reader job
        clears the reader's counters and bumps `stats_epoch`; a loss there marks the session lost, any other
        failure is logged, and neither replaces the command's own error."""
        # STLClient.reset force-acquires, stops, removes streams, clears stats, prom off, rx queue off,
        # service off (trex_stl_client.py:347-399; A4.4). It never releases, so the port stays ours.
        # clear_stats zeroes the client's counters for every port and the global ones, whatever ports
        # were named (common/trex_client.py, _clear_stats_common), so the epoch tells every browser to
        # drop every baseline.
        # The reader client has its own baseline (the control client's reset clears only its own), so a
        # reset clears the reader's too, then bumps the epoch, in one reader job: a stats read on the same
        # executor sees old counters under the old epoch or new under new, never zeros under the old epoch.
        # The job runs whenever c.reset was entered, even if it raises: the client clears stats
        # (trex_stl_client.py:384) before the steps that can still fail (set_port_attr, remove_rx_queue,
        # stop_capture_port, remove_all_captures, set_service_mode). A refused reset never enters c.reset,
        # so it changes neither the epoch nor the counters.
        r = self._reader
        entered = False

        # control executor: notes that c.reset was entered, then resets
        def blocking(c: Any) -> None:
            nonlocal entered
            entered = True
            trexapi.mark_pending(c, ports)       # c.reset force-acquires first; its own event takes the mark
            try:
                c.reset(ports=ports)
            except BaseException:
                trexapi.unmark_pending(c, ports)
                raise

        # reader executor: clears the reader's port counters, then bumps the epoch even when the clear raised
        def reader_job(rd: Any) -> None:
            try:
                rd.clear_stats(ports=sorted(rd.ports), clear_xstats=False, clear_flow_stats=False,
                               clear_latency_stats=False)
            finally:
                self._stats_epoch += 1           # on the reader executor: a later stats job sees it

        try:
            await self._command(f"Reset port(s) {ports}", blocking, ports=ports, need_owned=False, guard=True)
        finally:
            # decided here, on the loop: after a loss nothing publishes (and a clear on a dead server waits
            # ~12 s), and a replaced reader has its own fresh baseline, so then no job and no bump
            if entered and r is not None and self._reader is r and self._state == SessionState.CONNECTED:
                try:
                    await self._run_reader(reader_job, r)
                except Exception as e:                   # the command's own error must win
                    err = trexapi.translate(e)
                    if isinstance(err, trexapi.ServerUnreachable | trexapi.ServerRestarted):
                        self._mark_lost(err.message)
                    else:
                        self._log("error", f"Reset: reader clear failed: {err.message}")

    async def rate_info(self, port: int) -> dict[str, object]:
        """The port's profile rate info after a server validate, through _command: not a mutating command (no
        read_only refusal), but the port must be owned; a rejected validate raises TrexApiError."""
        # control executor: validate, then the port's profile rate info
        def blocking(c: Any) -> dict[str, object]:
            rc = c.ports[port].validate()
            if rc.bad():
                raise trexapi.TrexApiError(str(rc.err()))
            return dict(c.ports[port].get_profile() or {})
        result: dict[str, object] = await self._command(f"Rate info for port {port}", blocking, ports=[port],
                                                        mutating=False)
        return result

    # -- P3b helpers shared with port_config.PortConfigCommands ------------------------------------
    def _refuse_busy(self, label: str, ports: list[int], held_only: bool = False) -> None:
        """409 port_busy when a ping or scan holds one of the ports. A P3b command (held_only False) also
        refuses a port an Apply is working on: two service brackets on one port would interleave."""
        for p in ports:
            kind = self._busy.get(p)
            if kind is None or (held_only and kind == "apply"):
                continue
            msg = (f"Port {p} is busy applying a configuration." if kind == "apply"
                   else f"Port {p} is busy pinging or scanning.")
            self._log("warning", f"{label}: {msg}")
            raise trexapi.CommandRefused("port_busy", msg)

    def _set_busy(self, port: int, kind: str | None) -> None:
        """Claim `port` for `kind` ("ping" | "scan" | "apply"), or free it with None; publishes `busy` when
        the map changed. Loop thread only."""
        before = self.busy()
        if kind is None:
            self._busy.pop(port, None)
        else:
            self._busy[port] = kind
        if self.busy() != before:                    # every claim is shown, Apply included
            self._publish({"type": "busy", "ports": self.busy()})

    def _note_failure(self, label: str, e: Exception) -> TrexApiError:
        """Log a failed job and mark the session lost on a loss-class error; returns the translated error."""
        err = trexapi.translate(e)
        self._log("error", f"{label}: {err.message}")
        if isinstance(err, trexapi.ServerUnreachable | trexapi.ServerRestarted):
            self._mark_lost(err.message)
        return err

    async def _job(self, label: str, fn: Callable[..., Any], *args: Any) -> Any:
        """One executor job of a multi-job command, with _command's error handling but no "done" and no
        refresh."""
        try:
            return await self._run(fn, *args)
        except trexapi.CommandRefused as e:
            self._log("warning", f"{label}: {e.message}")
            raise
        except Exception as e:
            raise self._note_failure(label, e) from e

    async def _refresh_quietly(self, c: Any) -> None:
        """_refresh_now with any Exception swallowed; a cancel still propagates."""
        with contextlib.suppress(Exception):
            await self._refresh_now(c)

    def _connected_to(self, c: Any) -> bool:
        """True while `c` is still the control client and the session is CONNECTED: the re-check a command
        makes after an await."""
        return self._client is c and self._state == SessionState.CONNECTED

    # -- port state -----------------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        """2 s get_port_status safety poll (plan §4.1 Events), which keeps the port cache honest. A restarted
        server is normally caught first by the subscriber's get_async_events plus the RPC tap (_on_rpc); this
        poll is the fallback when its tick comes first. It does NOT shorten loss detection, which stays
        loss_bound(timeout_s)."""
        while self._state == SessionState.CONNECTED:
            await asyncio.sleep(self._poll_s)
            c = self._client
            if c is None or self._state != SessionState.CONNECTED:
                return
            try:
                views = await self._run(trexapi.sync_ports, c)
            except (trexapi.ServerRestarted, trexapi.ServerUnreachable) as e:
                self._mark_lost(e.message)
                return
            except TrexApiError as e:
                self._log("warning", f"Port status poll failed: {e.message}")
                continue
            except Exception:            # CancelledError is a BaseException and still propagates
                log.warning("Port status poll failed unexpectedly; polling continues", exc_info=True)
                continue
            if self._client is c:
                self._set_ports(views)

    async def _stats_loop(self) -> None:
        """Every stats_s, three separate jobs on the reader executor: read_stats, read_pgid_stats and
        read_util, published as the coalesced `stats`, `pgid` and `util` messages (stats carries
        `stats_epoch`). They run on the read-only reader client, so a command holding the control
        executor (Stop, an Apply, a scan) no longer delays them, and Stop no longer waits behind a read: it
        waits for at most the control job already running (a port poll, say), then its own follow-up refresh.
        Three jobs rather than one: they bound how stale each kind is relative to the others. A failure
        is handled like a port-poll failure: a loss-class error marks the session lost, any other logs
        one warning per streak, with one streak counter per read. Loss detection is not shortened."""
        failing = {"stats": False, "pgid": False, "util": False}

        # reader executor: (stats_epoch, read_stats), the epoch read on the thread a reset bumps it on
        def stats_job(c: Any) -> tuple[int, Any]:
            # the epoch is read on the reader executor, so it is in order with a reset's clear and bump
            return self._stats_epoch, trexapi.read_stats(c, self._stats_s)

        reads: list[tuple[str, str, Callable[[Any], Any]]] = [
            ("stats", "Stats", stats_job),
            ("pgid", "PGID stats", lambda c: trexapi.read_pgid_stats(c, self._stats_s)),
            ("util", "Utilization", lambda c: trexapi.read_util(c))]
        while self._state == SessionState.CONNECTED:
            await asyncio.sleep(self._stats_s)
            for kind, label, job in reads:
                c = self._reader
                if c is None or self._state != SessionState.CONNECTED:
                    return
                try:
                    view = await self._run_reader(job, c)
                except (trexapi.ServerRestarted, trexapi.ServerUnreachable) as e:
                    self._mark_lost(e.message)
                    return
                except TrexApiError as e:
                    if not failing[kind]:
                        self._log("warning", f"{label} read failed: {e.message}")
                    failing[kind] = True
                    continue
                except Exception:            # CancelledError is a BaseException and still propagates
                    if not failing[kind]:
                        log.warning("%s read failed unexpectedly; polling continues", label, exc_info=True)
                    failing[kind] = True
                    continue
                failing[kind] = False
                if self._reader is c and self._state == SessionState.CONNECTED:
                    if kind == "stats":
                        epoch, stats = view
                        self._publish({"type": "stats", "stats_epoch": epoch, **stats})
                    else:
                        self._publish({"type": kind, **view})

    def _schedule_refresh(self) -> None:
        """Start one background _refresh_soon unless one is pending, the session is closed, or it has no
        client or is not CONNECTED. Loop thread only."""
        if self._closed or self._refresh_pending or self._client is None \
                or self._state != SessionState.CONNECTED:
            return
        self._refresh_pending = True
        t = asyncio.get_running_loop().create_task(self._refresh_soon())
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _refresh_soon(self) -> None:
        """After 50 ms (a burst of events coalesces), one _refresh_now if there is a client and the session is
        still CONNECTED; a failure is logged, not raised."""
        await asyncio.sleep(0.05)          # coalesce a burst: one start emits port + profile events
        self._refresh_pending = False
        c = self._client
        if c is not None and self._state == SessionState.CONNECTED:
            try:
                await self._refresh_now(c)
            except Exception:            # nobody awaits this task; log rather than "never retrieved"
                log.warning("Port refresh failed; the safety poll will catch up", exc_info=True)

    async def _refresh_now(self, c: Any) -> None:
        """One port_views job on the control executor (the client's cache, no RPC); sets the ports, publishing
        `ports` when they changed, only while `c` is still the client. The job's error propagates."""
        views = await self._run(trexapi.port_views, c)
        if self._client is c:
            self._set_ports(views)

    def _set_ports(self, views: list[trexapi.PortView]) -> None:
        """Store the views and publish `ports`, only when they differ from the current ones."""
        if views != self._ports:
            self._ports = views
            self._publish({"type": "ports", "ports": views})

    def _mark_lost(self, cause: str) -> None:
        """CONNECTING: keep the cause for connect() to act on. CONNECTED: LOST with the cause as the error,
        which cancels the loops and publishes `session`. Any other state: nothing."""
        if self._state == SessionState.CONNECTING:
            self._pending_loss = cause
        elif self._state == SessionState.CONNECTED:
            self._error = cause
            self._set(SessionState.LOST)

    def _log(self, level: str, message: str) -> None:
        """Log `message` (as info for any level but "warning" and "error") and publish it as a `log` message
        with `level` as given."""
        getattr(log, "warning" if level == "warning" else "error" if level == "error" else "info")(message)
        self._publish({"type": "log", "level": level, "message": message, "ts": time.time()})

    def _log_connect_failure(self, p: ConnectParams, err: TrexApiError) -> None:
        """Put a failed connect in Events, not only in the dialog's error."""
        self._log("error", f"Connect to {p.host}:{p.sync_port} failed: {err.message}")

    # -- executor side --------------------------------------------------------------------------
    def _resolve_ifaces_blocking(self, cfg_path: str, ports: list[dict[str, Any]],
                                 hostname: str) -> tuple[list[dict[str, Any]], str | None]:
        """Reads the TRex cfg and the local sysfs (hostinfo) and makes no RPC; a missing, unreadable or
        invalid cfg becomes the note, not an exception (a NUL byte in the path still raises ValueError).
        Touches no mutable session state: its inputs are arguments (only the immutable root is read)."""
        text, cfg_note = read_cfg(cfg_path)
        ifaces, note = resolve(text, cfg_note, ports, server_hostname=hostname,
                               local_hostname=socket.gethostname(), root=self._hostinfo_root)
        return [dataclasses.asdict(i) for i in ifaces], note

    def _connect_blocking(self, p: ConnectParams) -> tuple[Any, ServerInfo]:
        """Control executor: build and connect the control client and return it with the ServerInfo;
        UnsupportedServerVersion unless the server runs SUPPORTED_VERSION, TrexApiError unless in STL mode.
        Never touches self._client: the loop assigns the returned client, and a failure here disconnects and
        releases its own client before re-raising, so nothing built on this thread can be orphaned."""
        c = trexapi.make_client(p.host, p.sync_port, p.async_port, p.timeout_s, self._on_client_event,
                                on_rpc=self._on_rpc)
        try:
            c.connect()
            v = c.get_server_version()
            si = c.get_server_system_info()
            if v["version"] != trexapi.SUPPORTED_VERSION:    # v2.94..v3.07 also report STL API 5.1
                raise trexapi.UnsupportedServerVersion(
                    v["version"], f"The server runs TRex {v['version']}; "
                    f"trex-web-gui supports only TRex {trexapi.SUPPORTED_VERSION} (STL API 5.1).")
            if v.get("mode") != "STL":
                raise TrexApiError(trexapi.mode_refusal(str(v.get("mode"))))
            info = ServerInfo(version=v["version"], mode=v["mode"], build_date=v.get("build_date", ""),
                              hostname=si.get("hostname", ""), port_count=si["port_count"],
                              core_type=si.get("core_type", ""), ports=si.get("ports", []))
        except BaseException:
            self._disconnect_blocking(c)
            raise
        return c, info

    def _release_result(self, job: Future[tuple[Any, ServerInfo]]) -> None:
        """Control executor: disconnect, releasing its ports, the client of a connect `job` that succeeded.
        Runs after `job` on the one executor thread (FIFO), so `job` is already finished."""
        if not job.cancelled() and job.exception() is None:
            self._disconnect_blocking(job.result()[0])

    def _disconnect_blocking(self, c: Any, release: bool = True) -> None:
        """Executor side: disconnect `c` without stopping traffic, first releasing its ports when `release`
        and the link is up. The release is ours, not the client's, so its outcome is logged: the client's
        own swallows a TRexError under a silenced logger, and a port left owned on exit was then found only
        when the next session was refused it (RC1-25). A disconnect Exception is logged at debug level and
        swallowed."""
        if release and c.conn.is_connected():
            ports = c.get_acquired_ports()
            if ports:
                try:
                    c.release(ports)
                    log.info("Released port(s) %s", ports)
                except Exception as e:
                    log.warning("Releasing port(s) %s failed: %s; they stay owned by %s until re-acquired",
                                ports, e, c.ctx.username)
        try:
            c.disconnect(stop_traffic=False, release_ports=False)
        except Exception:
            log.debug("disconnect after loss raised; ignored", exc_info=True)

    def _connect_reader_blocking(self, p: ConnectParams) -> Any:
        """The reader: connected like the control client, and never acquires. A failure here disconnects
        its own client, so nothing built on this thread can be orphaned."""
        c = trexapi.make_client(p.host, p.sync_port, p.async_port, p.timeout_s, self._on_reader_event,
                                on_rpc=self._on_rpc)
        try:
            c.connect()
        except BaseException:
            self._disconnect_blocking(c, release=False)
            raise
        return c

    def _release_reader_result(self, job: Future[Any]) -> None:
        """Reader executor: disconnect, never releasing, the reader of a connect `job` that succeeded. Runs
        after `job` on the reader executor thread (FIFO), so `job` is already finished."""
        if not job.cancelled() and job.exception() is None:
            self._disconnect_blocking(job.result(), release=False)

    # -- subscriber thread → loop ---------------------------------------------------------------
    def _on_client_event(self, ev: ClientEvent) -> None:
        """Control client's subscriber thread: hand the event to _handle_event on the loop; dropped when there
        is no loop yet or it is closed."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._handle_event, ev)

    def _on_reader_event(self, ev: ClientEvent) -> None:
        """Reader's subscriber thread: hand the event to _handle_reader_event on the loop; dropped when there
        is no loop yet or it is closed."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._handle_reader_event, ev)

    def _handle_reader_event(self, ev: ClientEvent) -> None:
        """Not published as `event` (the control client's subscriber publishes each one); a loss is logged,
        so the event log still carries its cause, and marks the session lost. Once only while CONNECTED (the
        session goes LOST and later events are ignored); while CONNECTING each loss event is logged."""
        if self._state not in (SessionState.CONNECTING, SessionState.CONNECTED):
            return
        if ev.name not in trexapi.LOSS_EVENTS:
            return
        cause = self._restart_cause or ev.message or ev.name
        self._log("error", f"Reader: {cause}")
        self._mark_lost(cause)

    def _handle_event(self, ev: ClientEvent) -> None:
        """Loop thread: publish a control client event as `event` (a "subscriber resumed" only after a
        timeout was seen), schedule a port refresh for a port or profile event, and mark the session lost on
        a loss event, with the restart cause the RPC tap saw in place of the event's text."""
        if ev.name == "subscriber timeout":
            self._timeout_seen = True
        elif ev.name == "subscriber resumed" and not self._timeout_seen:
            return              # upstream also emits it when the subscriber starts: not news on every connect
        self._publish({"type": "event", "name": ev.name, "args": ev.args, "message": ev.message,
                       "ts": ev.ts, "level": ev.level})
        if ev.name.startswith(("port ", "profile ")):
            self._schedule_refresh()
        if ev.name not in trexapi.LOSS_EVENTS:
            return
        # A restarted server fails the subscriber's get_async_events poll at once (API handler mismatch) and
        # upstream reports that as "subscriber timeout", long before the port poll runs. The RPC tap saw the
        # real reason, so it wins over the event's generic text.
        cause = self._restart_cause or ev.message or ev.name
        self._mark_lost(cause)                           # CONNECTING: connect() turns this into LOST

    # -- subscriber / executor thread → loop ----------------------------------------------------
    def _on_rpc(self, r: trexapi.RpcRecord) -> None:
        """Either client's subscriber or executor thread: a ServerRestarted error in a response goes to
        _note_restart on the loop; with the RPC console on (a polling RPC only when included) the record is
        published as `rpc`, also through the loop; a body whose JSON text passes RPC_BODY_MAX is cut to a
        string and carries `truncated`, the full length. Writes no session state on this thread."""
        if r.direction == "response":      # every response, polling or not; a stats batch answers with a list
            for el in r.body if isinstance(r.body, list) else [r.body]:
                if isinstance(el, dict) and el.get("error"):
                    err = trexapi.translate(Exception(str(el["error"])))     # error responses only: cheap
                    if isinstance(err, trexapi.ServerRestarted):
                        loop = self._loop
                        if loop is not None and not loop.is_closed():
                            # FIFO per thread: lands before that thread's later loss event
                            loop.call_soon_threadsafe(self._note_restart, err.message)
                        break
        if not self._rpc_console or (r.poll and not self._include_polling):
            return                         # plain bool reads from a client thread; benign under the GIL
        loop = self._loop
        if loop is not None and not loop.is_closed():
            msg: dict[str, Any] = {"type": "rpc", "direction": r.direction, "body": r.body,
                                   "ts": r.ts, "thread": r.thread}
            text = json.dumps(r.body, default=str)       # paid only while the console is on
            if len(text) > RPC_BODY_MAX:                 # cut: the text's head, and its full length
                msg["body"], msg["truncated"] = text[:RPC_BODY_MAX], len(text)
            loop.call_soon_threadsafe(self._publish, msg)

    def _note_restart(self, cause: str) -> None:
        """Loop thread: keep the restart cause, which a later loss event reports instead of its own text."""
        self._restart_cause = cause

    # -- helpers --------------------------------------------------------------------------------
    async def _await_done(self, fut: asyncio.Future[_T]) -> _T:
        """Wait for `fut` to finish however often this task is cancelled (each wait is shielded), then
        re-raise the CancelledError if one arrived, else return its result (its exception propagates). Never
        returns normally after a cancel. Holds off a cancel for as long as `fut` runs, which is bounded by the
        job itself."""
        cancelled = False
        while not fut.done():
            try:
                await asyncio.shield(fut)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                pass                                     # fut failed; reported below unless a cancel came
        if cancelled:
            if fut.done() and not fut.cancelled():
                fut.exception()                          # retrieved: no "never retrieved" noise
            raise asyncio.CancelledError
        return fut.result()

    async def _abandon_connect(self, job: Future[tuple[Any, ServerInfo]],
                               result: asyncio.Future[tuple[Any, ServerInfo]],
                               rjob: Future[Any] | None = None,
                               rresult: asyncio.Future[Any] | None = None) -> None:
        """connect() was cancelled while `job` (and `rjob`, once started) may still be running. Queue each
        release behind its job on that job's executor (so it happens even if this wait is cancelled too),
        wait for them, then go DISCONNECTED."""
        result.add_done_callback(lambda f: f.cancelled() or f.exception())   # no "never retrieved" noise
        try:
            if rjob is not None and rresult is not None:
                rresult.add_done_callback(lambda f: f.cancelled() or f.exception())
                rcleanup = self._reader_executor.submit(self._release_reader_result, rjob)
                await self._await_done(asyncio.wrap_future(rcleanup))
        finally:
            await self._release_control_result(job)

    async def _release_control_result(self, job: Future[tuple[Any, ServerInfo]]) -> None:
        """Queue the release of `job`'s client behind it on the control executor and wait for it (shielded);
        then, even when cancelled, clear the info and go DISCONNECTED with "Connect cancelled." (publishes
        `session`)."""
        cleanup = self._executor.submit(self._release_result, job)
        try:
            await self._await_done(asyncio.wrap_future(cleanup))
        finally:
            self._info, self._error = None, "Connect cancelled."
            self._set(SessionState.DISCONNECTED)

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run `fn(*args)` on the control executor, the control client's own thread, and return its result."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    async def _run_reader(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run `fn(*args)` on the reader executor, the reader's own thread, and return its result."""
        return await asyncio.get_running_loop().run_in_executor(self._reader_executor, fn, *args)

    async def _drop_client(self, release: bool = True) -> None:
        """Detach both clients, then disconnect the control one on its executor (releasing its ports when
        `release`) and the reader on its own, never releasing. Publishes nothing."""
        c, self._client = self._client, None
        r, self._reader = self._reader, None
        try:
            if c is not None:
                await self._await_done(asyncio.wrap_future(
                    self._executor.submit(self._disconnect_blocking, c, release)))
        finally:
            if r is not None:
                await self._await_done(asyncio.wrap_future(
                    self._reader_executor.submit(self._disconnect_blocking, r, False)))

    def _set(self, st: SessionState) -> None:
        """Set the state; any state but CONNECTED cancels the port poll, stats and capture loops. Publishes
        `session`."""
        self._state = st
        if st != SessionState.CONNECTED and self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        if st != SessionState.CONNECTED and self._stats_task is not None:
            self._stats_task.cancel()
            self._stats_task = None
        if st != SessionState.CONNECTED and self._capture_task is not None:
            self._capture_task.cancel()
            self._capture_task = None
        self._publish(self.snapshot())
