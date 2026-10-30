"""P3c capture commands of TrexSession (plan §5 P3c): the live monitor, the recorders, Save into the spool and
the poll loop. A mixin, so the session's lock, executors and loss handling stay in one class.

Reads (`capture_status`, `capture_fetch_once`) run on the reader client, writes (`capture_start`,
`capture_stop`, `capture_remove`) on the control client; every one is one executor job and one RPC. A
capture's `fetch` drains our own buffer and changes no port state; it is the one read with a side effect
the reader sends. Captures belong to no client: they survive a disconnect, so only the monitor this session
started is removed by `disconnect()`, never a recorder.

Every command takes the control client `c` and the reader `r` once and re-checks both after every await
(`_alive`): a disconnect or a reconnect in the middle must not send its next RPC to another connection.

The slot gate: one `asyncio.Lock` (`_capture_start_lock`) is held across a fresh `capture_status`, the gate
and the `capture_start` await, for the monitor and for recorders alike, and refuses at nine server captures
(the tenth slot is kept for the client's own ping, ARP and scan). The server counts STOPPED captures too.

The service-mode gate covers the Rx ports of a capture (spec §6.5, user, open question 11). Tx-only captures
are not gated. On the af_packet guest (tests/fixtures/p3c) a Tx capture of stream traffic recorded nothing
with the Tx port's service mode off; that is an af_packet measurement only, UNVERIFIED on other NICs. A
port in service mode refuses an unforced traffic start (PSV_NON_SERVICE,
`_vendor/trex/stl/trex_stl_client.py:677-679`).

The Spool is single-thread only (store/spool.py): every call to it here is on the loop thread.

Wireshark (sessions/wireshark.py): the monitor's fetched packets are fed to the pipe as they are published.
The pipe is stopped (its window left open) whenever the monitor stops, goes or is replaced, and every pipe
this session launched is killed by close(). The pipe tells the loop of an end it found itself through
`call_soon_threadsafe`, and the session publishes from there.

LLDP (Task 11): every batch the monitor fetches is also parsed for LLDP neighbours, in the same scapy-executor
job as its decoding (`decode_batch`). The table belongs to one monitor: it is emptied when a monitor starts,
stops, goes or is replaced, and when the session resets. GET /api/capture/lldp reads it (`lldp_neighbours`),
dropping the entries whose TTL ran out."""
import asyncio
import base64
import dataclasses
import logging
import math
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trex_web_gui import trexapi
from trex_web_gui.store.spool import CaptureKey, Spool, SpoolFile
from trex_web_gui.trexapi import TrexApiError, decode, lldp, scapy_exec

from . import wireshark
from .wireshark import WiresharkPipe

log = logging.getLogger(__name__)

STATUS_S = 1.0                  # capture_status cadence of the loop
FETCH_S = 0.5                   # monitor fetch cadence (2 Hz)
FETCH_LIMIT = 50                # packets per fetch job
MAX_USED_SLOTS = 9              # a start is refused at this many captures; the 10th is kept for services
SAVE_PROGRESS_JOBS = 10
SAVE_EMPTY_CUTOFF = 3           # consecutive empty fetches while `pending > 0`
GONE = "removed on the server"
SERVICE_NOTE = ("the Rx ports of a capture need service mode, and a port in service mode "
                "refuses an unforced traffic start; Tx-only captures are not gated (on the af_packet guest a "
                "Tx capture recorded nothing with the Tx port's service mode off, unverified on other NICs)")


@dataclass
class MonitorState:
    id: int
    start_ts: float
    tx: list[int]
    rx: list[int]
    bpf: str
    limit: int
    fetched: int = 0            # packets the server handed out, matched/fetched/count: one status row
    matched: int = 0            # packets the filter matched, from the status row on every poll
    count: int = 0              # packets held on the server, from the status row
    state: str = "ACTIVE"
    gone: str | None = None


def decode_rows(pkts: list[dict[str, Any]], start_ts: float) -> list[dict[str, Any]]:
    """DecodedRow per packet. Runs on the scapy executor; `summary` never raises on a packet's bytes, so only
    a malformed fetch record (a missing field, bad base64) can make this raise."""
    rows: list[dict[str, Any]] = []
    for p in pkts:
        raw = base64.b64decode(p["binary"])
        s = decode.summary(raw)
        rows.append({"index": int(p["index"]), "ts_rel_us": round((float(p["ts"]) - start_ts) * 1e6),
                     "port": int(p["port"]), "origin": str(p["origin"]), "src": s.src, "dst": s.dst,
                     "proto": s.proto, "wirelen": int(p["wirelen"]), "info": s.info, "binary": p["binary"]})
    return rows


def decode_batch(pkts: list[dict[str, Any]], start_ts: float,
                 now: float) -> tuple[list[dict[str, Any]], list[lldp.LldpNeighbour]]:
    """The rows and the LLDP neighbours of one batch, in one scapy-executor job. Neither half raises on a
    packet's bytes; a malformed fetch record can."""
    seen = [n for p in pkts
            if (n := lldp.parse(base64.b64decode(p["binary"]), int(p["port"]), now)) is not None]
    return decode_rows(pkts, start_ts), seen


class CaptureCommands:
    """Mixed into TrexSession, which provides everything declared under TYPE_CHECKING."""
    if TYPE_CHECKING:
        _publish: Callable[[dict[str, object]], None]
        _client: Any
        _reader: Any
        _params: Any
        _ports: list[trexapi.PortView]
        _busy: dict[int, str]
        _closed: bool
        _state: Any
        _bg: set[asyncio.Task[Any]]

        # TrexSession's: not_connected (and read_only when mutating) before any RPC; the control client
        def require_control(self, mutating: bool = True) -> Any: ...
        # TrexSession's: runs fn(*args) on the control executor
        async def _run(self, fn: Callable[..., Any], *args: Any) -> Any: ...
        # TrexSession's: runs fn(*args) on the reader executor
        async def _run_reader(self, fn: Callable[..., Any], *args: Any) -> Any: ...
        # TrexSession's: logs and publishes a `log` message
        def _log(self, level: str, message: str) -> None: ...
        # TrexSession's: True while c is still the control client and the session is CONNECTED
        def _connected_to(self, c: Any) -> bool: ...
        # TrexSession's: logs a failed job, marks lost on a loss-class error, returns the translated error
        def _note_failure(self, label: str, e: Exception) -> TrexApiError: ...
        # TrexSession's: CONNECTED goes LOST with the cause (CONNECTING keeps it for connect())
        def _mark_lost(self, cause: str) -> None: ...

    def _init_capture(self, spool_dir: Path | None) -> None:
        """The capture fields, set by TrexSession.__init__: no Spool yet (made on first use), the start lock,
        no monitor, captures or Wireshark pipe."""
        self._spool_dir = spool_dir
        self._spool: Spool | None = None
        self._capture_start_lock = asyncio.Lock()
        self._ending = False                # disconnect() is running: no poll loop may be started meanwhile
        self._monitor: MonitorState | None = None
        self._monitor_pending = False
        self._stopping: MonitorState | None = None      # monitor_stop is removing it: not "gone"
        self._captures: list[trexapi.CaptureRow] = []
        self._capture_task: asyncio.Task[None] | None = None
        self._saving: set[int] = set()      # replaced (never cleared) by a reset: a save holds its own set
        self._my_ids: set[int] = set()
        self._lldp: dict[lldp.Key, lldp.LldpNeighbour] = {}       # the current monitor's neighbours
        self._ws_pipe: WiresharkPipe | None = None        # the monitor's pipe, running or ended with an error
        self._ws_left: list[WiresharkPipe] = []           # stopped pipes whose windows may still be open
        self._ws_logged: weakref.WeakSet[WiresharkPipe] = weakref.WeakSet()   # pipes whose drops were logged

    # -- snapshot -------------------------------------------------------------------------------
    def capture_snapshot(self) -> dict[str, Any]:
        """The `capture` message: the monitor, the status rows (each marked `mine`, and `service` for a
        foreign row on a port a ping, scan or Apply holds) and the Wireshark state. Publishes nothing."""
        held = set(self._busy)
        mine = set(self._my_ids) | ({self._monitor.id} if self._monitor else set())
        # "TRex service": a foreign row on a port a P3b service holds; our own captures never are.
        rows = [{**r, "mine": r["id"] in self._my_ids,
                 "service": r["id"] not in mine and any(p in held for p in r["rx"])}
                for r in self._captures]
        return {"type": "capture", "ts": time.time(),
                "monitor": dataclasses.asdict(self._monitor) if self._monitor else None,
                "captures": rows, "wireshark": self._wireshark_state()}

    def _wireshark_state(self) -> dict[str, Any]:
        """The snapshot's `wireshark` field: the attached pipe's running flag and error, else not running."""
        p = self._ws_pipe
        return {"running": p.running, "error": p.error} if p else {"running": False, "error": None}

    def _capture_key(self) -> tuple[Any, ...]:
        """The rows and the monitor, compared before and after a status to decide whether to publish."""
        return (self._captures, dataclasses.asdict(self._monitor) if self._monitor else None)

    def _publish_capture(self) -> None:
        """Publish the `capture` message (capture_snapshot)."""
        self._publish(self.capture_snapshot())

    def lldp_neighbours(self) -> list[lldp.LldpNeighbour]:
        """The current monitor's LLDP neighbours whose TTL has not run out (GET /api/capture/lldp); the
        expired ones are dropped from the table."""
        return lldp.current(self._lldp, time.time())

    def _reset_capture_state(self) -> bool:
        """Forget every capture field and stop the Wireshark pipe; True when there was something to forget
        (the caller publishes)."""
        had = bool(self._monitor or self._captures or self._my_ids or self._saving)
        self._end_wireshark()
        self._monitor, self._monitor_pending, self._stopping = None, False, None
        self._captures, self._my_ids, self._saving = [], set(), set()
        self._lldp = {}
        return had

    # -- guards ---------------------------------------------------------------------------------
    def _alive(self, c: Any, r: Any) -> bool:
        """True while `c` is still the control client, the session CONNECTED and `r` still the reader: the
        re-check a capture command makes after every await."""
        return self._connected_to(c) and self._reader is r

    def _guard(self, c: Any, r: Any) -> None:
        """CommandRefused not_connected unless _alive(c, r)."""
        if not self._alive(c, r):
            raise trexapi.CommandRefused("not_connected", "The connection ended during the capture command.")

    def _gate_service(self, tx: list[int], rx: list[int]) -> None:
        """409 bad_port for a port the server does not have, then 409 service_off naming the Rx ports off
        service (Tx ports are not gated). Reads `self._ports` (published PortViews), not the client."""
        by_id = {p["id"]: p for p in self._ports}
        missing = [p for p in sorted(set(tx) | set(rx)) if p not in by_id]
        if missing:
            raise trexapi.CommandRefused("bad_port", f"Port(s) {missing} do not exist on this server.")
        off = [p for p in sorted(set(rx)) if not by_id[p]["service"]]
        if off:
            raise trexapi.CommandRefused(
                "service_off", f"Port(s) {off} are not in service mode; turn service mode on for them first "
                f"({SERVICE_NOTE}).")

    def _refuse_monitor_id(self, id: int) -> None:
        """CommandRefused capture_busy while a monitor start is in flight (whatever `id`) or when `id` is the
        monitor's."""
        if self._monitor_pending or (self._monitor is not None and self._monitor.id == id):
            raise trexapi.CommandRefused(
                "capture_busy", f"Capture {id} is the live monitor's; stop the monitor instead.")

    def _row(self, id: int) -> trexapi.CaptureRow | None:
        """The cached status row of capture `id`, or None."""
        return next((r for r in self._captures if r["id"] == id), None)

    def _refuse_endpoint(self, id: int) -> None:
        """CommandRefused endpoint_capture when the cached row of `id` streams to an endpoint; an id with no
        cached row passes."""
        row = self._row(id)
        if row is not None and row["endpoint"]:
            raise trexapi.CommandRefused(
                "endpoint_capture", f"Capture {id} streams to an endpoint; its packets are not held here.")

    def spool(self) -> Spool:
        """The session's Spool, made on first use (which creates the capture directory, mode 0700);
        CommandRefused spool_unavailable when this backend has no capture directory. Loop thread only."""
        if self._spool_dir is None:
            raise trexapi.CommandRefused("spool_unavailable", "This backend has no capture directory.")
        if self._spool is None:
            self._spool = Spool(self._spool_dir)
        return self._spool

    # -- jobs -----------------------------------------------------------------------------------
    async def _write_job(self, label: str, c: Any, fn: Callable[..., Any], *args: Any) -> Any:
        """A capture write on the control executor. "capture ID not found" is the caller's to read, so it
        is neither logged here nor a loss; anything else is logged, and a loss-class error marks lost."""
        try:
            return await self._run(fn, c, *args)
        except Exception as e:
            err = trexapi.translate(e)
            if trexapi.is_not_found(err):
                raise err from e
            raise self._note_failure(label, e) from e

    async def _read_job(self, r: Any, fn: Callable[..., Any], *args: Any) -> Any:
        """A capture read on the reader executor: a loss-class error marks lost, nothing is logged here."""
        try:
            return await self._run_reader(fn, r, *args)
        except Exception as e:
            err = trexapi.translate(e)
            if isinstance(err, trexapi.ServerUnreachable | trexapi.ServerRestarted):
                self._mark_lost(err.message)
            raise err from e

    async def capture_sync(self) -> list[trexapi.CaptureRow]:
        """One `capture_status` on the reader; returns the rows, and applies them (_apply_status, which may
        publish) only while the same reader is still CONNECTED. not_connected without a reader or when not
        CONNECTED; raises the translated error (a loss marks the session lost)."""
        r = self._reader
        if r is None or not self._state_is_connected():
            raise trexapi.CommandRefused("not_connected", "Not connected to a TRex server.")
        mon = self._monitor                        # the monitor that existed when the status was asked for
        rows: list[trexapi.CaptureRow] = await self._read_job(r, trexapi.capture_status)
        if self._reader is r and self._state_is_connected():
            self._apply_status(rows, mon)
        return rows

    def _state_is_connected(self) -> bool:
        """True when the session is CONNECTED, compared by value (SessionState is a StrEnum)."""
        return bool(self._state == "connected")          # SessionState is a StrEnum

    def _apply_status(self, rows: list[trexapi.CaptureRow], asked_with: MonitorState | None) -> None:
        """Store the rows. When `asked_with` is still the monitor, copy its counters and state from its row,
        or, with no row, end it as gone (unless monitor_stop is removing it). Publishes `capture` when the
        rows or the monitor changed."""
        before = self._capture_key()
        self._captures = rows
        mon = self._monitor
        if mon is not None and mon is asked_with:
            row = next((x for x in rows if x["id"] == mon.id), None)
            if row is None:
                if mon is not self._stopping:
                    self._monitor_gone(mon, publish=True)       # publishes this status's rows too
                    return
            else:
                mon.matched, mon.fetched, mon.count = row["matched"], row["fetched"], row["count"]
                mon.state = row["state"]
        if self._capture_key() != before:
            self._publish_capture()

    def _monitor_gone(self, mon: MonitorState, publish: bool) -> None:
        """The server no longer lists the monitor: mark it gone, forget it as ours, end its Wireshark pipe
        and LLDP table, publish once when `publish`, clear it. One log line. Nothing when `mon` is no longer
        the monitor."""
        if self._monitor is not mon:
            return
        mon.gone = GONE
        self._log("warning", f"Capture monitor {mon.id} was {GONE}.")
        self._my_ids.discard(mon.id)
        self._end_wireshark()
        self._lldp = {}
        if publish:
            self._publish_capture()         # carries `gone`; the monitor is cleared right after
        self._monitor = None

    # -- the poll loop --------------------------------------------------------------------------
    def _ensure_capture_loop(self) -> None:
        """Start the poll loop when none runs, the session is CONNECTED and not closed, no disconnect() is
        running, and there is something to watch."""
        if self._closed or self._ending or not self._state_is_connected():
            return
        if not (self._monitor or self._captures):
            return
        if self._capture_task is not None and not self._capture_task.done():
            return
        t = asyncio.get_running_loop().create_task(self._capture_loop())
        self._capture_task = t
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _capture_loop(self) -> None:
        """Every STATUS_S one `capture_status`; while the monitor is ACTIVE a fetch every
        FETCH_S. A loss-class error marks the session lost as `_stats_loop` does; any other failure logs
        once per streak. Ends when the session is not CONNECTED, or nothing is left to watch.

        The fetch cadence is a deadline (`next_fetch`), so the fetch, decode and publish time comes out of
        the period instead of adding to it. A loop more than one period behind (a stall) re-anchors at now:
        one immediate fetch, not a burst of catch-up fetches."""
        next_status = time.monotonic() + STATUS_S
        next_fetch = time.monotonic() + FETCH_S
        failing = False
        while self._state_is_connected():
            now = time.monotonic()
            if now - next_fetch > FETCH_S:
                next_fetch = now
            await asyncio.sleep(max(0.0, next_fetch - now))
            next_fetch += FETCH_S
            r = self._reader
            if r is None or not self._state_is_connected():
                return
            try:
                mon = self._monitor
                if mon is not None and mon.state == "ACTIVE":
                    await self._fetch_monitor(r, mon)
                if time.monotonic() >= next_status and self._reader is r:
                    next_status = time.monotonic() + STATUS_S
                    await self.capture_sync()
            except (trexapi.ServerRestarted, trexapi.ServerUnreachable):
                return                          # _read_job marked the session lost
            except trexapi.CommandRefused:
                return                          # the session ended under the loop
            except TrexApiError as e:
                if not failing:
                    self._log("warning", f"Capture poll failed: {e.message}")
                failing = True
                continue
            except Exception:                   # CancelledError is a BaseException and still propagates
                if not failing:
                    log.warning("Capture poll failed unexpectedly; polling continues", exc_info=True)
                failing = True
                continue
            failing = False
            if self._monitor is None and not self._captures:
                return

    async def _fetch_monitor(self, r: Any, mon: MonitorState) -> None:
        """One destructive fetch of up to FETCH_LIMIT of the monitor's packets on the reader, then
        _publish_batch. "not found" ends the monitor as gone (unless monitor_stop is removing it); any
        other error propagates, a loss having marked the session lost."""
        try:
            f: trexapi.Fetched = await self._read_job(r, trexapi.capture_fetch_once, mon.id, FETCH_LIMIT)
        except TrexApiError as e:
            if trexapi.is_not_found(e):
                if mon is not self._stopping:
                    self._monitor_gone(mon, publish=True)
                return
            raise
        await self._publish_batch(r, mon, f)

    async def _publish_batch(self, r: Any, mon: MonitorState, f: trexapi.Fetched) -> None:
        """Feed a batch to the monitor's Wireshark pipe, decode it on the scapy executor, then, unless the
        monitor it was fetched for is gone or replaced or `r` is no longer the reader, merge its LLDP
        neighbours and publish `capture_pkts` and `capture`. An empty batch does nothing."""
        if not f.pkts:
            return
        pipe = self._ws_pipe
        if pipe is not None and self._monitor is mon:
            for p in f.pkts:
                pipe.write(max(0.0, float(p["ts"]) - mon.start_ts), base64.b64decode(p["binary"]),
                           int(p["wirelen"]))
        rows, seen = await scapy_exec.arun(decode_batch, f.pkts, mon.start_ts, time.time())
        if self._monitor is None or self._monitor.id != mon.id or self._reader is not r:
            return                              # a stale batch: the monitor was stopped or replaced meanwhile
        lldp.merge(self._lldp, seen)
        self._publish({"type": "capture_pkts", "monitor_id": mon.id, "rows": rows})
        self._publish_capture()

    # -- monitor --------------------------------------------------------------------------------
    async def monitor_start(self, tx: list[int], rx: list[int], bpf: str, limit: int = 100) -> MonitorState:
        """Start the live monitor (cyclic, `limit` packets) and the poll loop that fetches it. Refused by
        require_control, monitor_active while one runs or starts, _gate_service, and capture_slots. Holds
        `_capture_start_lock` across a fresh capture_status, the slot gate and capture_start, re-checking
        the clients (_alive) after each await: when the connection ended during the start it stops and
        removes the new capture and refuses not_connected. Remembers the capture as ours, ends any Wireshark
        pipe and empties the LLDP table; logs and publishes `capture`. While it runs, `_monitor_pending`
        makes recorder_stop, recorder_remove and recorder_save refuse capture_busy for any id."""
        c = self.require_control()
        r = self._reader
        if self._monitor is not None or self._monitor_pending:
            raise trexapi.CommandRefused("monitor_active", "A capture monitor is already running.")
        self._monitor_pending = True
        try:
            self._gate_service(tx, rx)
            async with self._capture_start_lock:
                await self._refuse_when_full()
                self._guard(c, r)
                label = f"Start capture monitor (tx {tx}, rx {rx})"
                started: trexapi.CaptureStarted = await self._write_job(
                    label, c, trexapi.capture_start, tx, rx, limit, "cyclic", bpf)
                if not self._alive(c, r):           # disconnect() waits for this lock: a reconnect or a loss
                    await self._abandon_started(c, started.id)
                    raise trexapi.CommandRefused("not_connected",
                                                 "The connection ended while the monitor was starting.")
                self._my_ids.add(started.id)
                mon = MonitorState(id=started.id, start_ts=started.start_ts, tx=list(tx), rx=list(rx),
                                   bpf=bpf, limit=limit)
                self._end_wireshark()               # a pipe never outlives its monitor
                self._lldp = {}                     # nor a neighbour table
                self._monitor = mon
            self._log("info", f"{label}: capture {mon.id}")
            self._ensure_capture_loop()
            self._publish_capture()
            return mon
        finally:
            self._monitor_pending = False

    async def _refuse_when_full(self) -> None:
        """Called inside the start lock (it takes none): one fresh status, never the cache (empty right after
        connect); capture_slots at MAX_USED_SLOTS server captures."""
        fresh = await self.capture_sync()
        if len(fresh) >= MAX_USED_SLOTS:
            raise trexapi.CommandRefused(
                "capture_slots", f"{len(fresh)} of 10 server capture slots are used; one is kept for ping, "
                "ARP and scan. Remove a capture first.")

    async def _abandon_started(self, c: Any, id: int) -> None:
        """The connection ended while a start was in flight: stop and remove what it created, best effort."""
        for fn in (trexapi.capture_stop, trexapi.capture_remove):
            try:
                await self._run(fn, c, id)
            except Exception as e:
                log.warning("capture %s cleanup after a lost connection failed: %s", id, e)

    async def monitor_stop(self) -> None:
        """Stop the monitor: capture_stop, publish it STOPPED, drain what the server still holds as
        `capture_pkts` (destructive fetches, at most limit / FETCH_LIMIT), then capture_remove. Refused by
        require_control and no_monitor. Re-checks the clients (not_connected) after the stop and after each
        drain fetch and publish; a capture already gone ends quietly with a log line. On success or gone,
        clears the monitor, its Wireshark pipe and LLDP table (publishes `capture`) and resyncs the status;
        any other failure propagates and leaves the monitor set, already stopped on the server, shown STOPPED
        and possibly part-drained, so a second Stop resumes at the drain. Takes no lock."""
        c = self.require_control()
        r = self._reader
        mon = self._monitor
        if mon is None:
            raise trexapi.CommandRefused("no_monitor", "No capture monitor is running.")
        label = f"Stop capture monitor {mon.id}"
        self._stopping = mon
        try:
            await self._write_job(label, c, trexapi.capture_stop, mon.id)
            self._guard(c, r)
            mon.state = "STOPPED"
            self._publish_capture()
            for _ in range(math.ceil(mon.limit / FETCH_LIMIT)):          # drain what the server still holds
                f: trexapi.Fetched = await self._read_job(r, trexapi.capture_fetch_once, mon.id, FETCH_LIMIT)
                self._guard(c, r)
                await self._publish_batch(r, mon, f)
                self._guard(c, r)
                if f.pending == 0:
                    break
            await self._write_job(label, c, trexapi.capture_remove, mon.id)
        except TrexApiError as e:
            if not trexapi.is_not_found(e):
                raise
            self._log("info", f"{label}: capture {mon.id} was already gone")      # gone: ends quietly
        finally:
            self._stopping = None
        if self._monitor is mon:
            self._my_ids.discard(mon.id)
            self._end_wireshark()
            self._lldp = {}
            self._monitor = None
            self._publish_capture()
        await self._sync_quietly()

    async def _sync_quietly(self) -> None:
        """capture_sync with any Exception only logged (not published), then _ensure_capture_loop."""
        try:
            await self.capture_sync()
        except Exception as e:
            log.info("capture status after a command failed: %s", e)
        self._ensure_capture_loop()

    # -- Wireshark ------------------------------------------------------------------------------
    async def wireshark_start(self, path: str) -> None:
        """Launch Wireshark on the monitor's packets. `path` is the saved preference, never a request's. A
        pipe already running is kept (a second click does nothing). Refused once the session is closed
        (close() may already have taken its pipes to kill) and while not CONNECTED (a LOST session keeps its
        monitor, but no loop feeds it)."""
        if self._closed:
            raise trexapi.CommandRefused("not_connected", "This session is closed.")
        mon = self._monitor
        if mon is None or mon.gone:
            raise trexapi.CommandRefused("no_monitor",
                                         "Start the capture monitor first; Wireshark shows its packets.")
        if not self._state_is_connected():
            raise trexapi.CommandRefused("not_connected", "Not connected to a TRex server.")
        ok, reason = wireshark.preflight(path)
        if not ok:
            raise trexapi.CommandRefused("wireshark_unavailable", reason or "Wireshark cannot be launched.")
        if self._ws_pipe is not None and self._ws_pipe.running:
            return
        self._end_wireshark()                   # one that ended with an error
        self._ws_left = [p for p in self._ws_left if p.process_alive]      # reaps the windows closed since
        pipe = WiresharkPipe(lambda: self._wireshark_ended(pipe), asyncio.get_running_loop())
        try:
            pipe.start(path)
        except OSError as e:
            raise trexapi.CommandRefused("wireshark_unavailable",
                                         f"Wireshark could not be started: {e}") from e
        self._ws_pipe = pipe
        self._log("info", f"Wireshark started on capture monitor {mon.id} (pid {pipe.pid})")
        self._publish_capture()

    async def wireshark_stop(self) -> None:
        """Close the pipe; the window stays open. Nothing to stop is not an error."""
        if self._ws_pipe is not None:
            self._end_wireshark()
            self._publish_capture()

    def _wireshark_ended(self, pipe: WiresharkPipe) -> None:
        """The writer ended `pipe` on its own (the window closed, Wireshark exited, the open deadline). On the
        loop thread: the pipe calls this only through call_soon_threadsafe."""
        self._log_dropped(pipe)
        if pipe is self._ws_pipe:
            self._log("warning", f"Wireshark: {pipe.error}")
            self._publish_capture()

    def _log_dropped(self, pipe: WiresharkPipe) -> None:
        """Once per pipe: the frames a full queue dropped, which the snapshot does not carry."""
        if pipe.dropped and pipe not in self._ws_logged:
            self._ws_logged.add(pipe)
            self._log("warning", f"Wireshark fell behind: {pipe.dropped} packets were not sent to it "
                                 "(its queue was full).")

    def _end_wireshark(self) -> None:
        """Detach the pipe and stop it off the loop thread (stop() joins the writer); its window is killed at
        close(). write() is called only on the loop thread and only for the attached pipe, so `dropped` is
        final here."""
        p, self._ws_pipe = self._ws_pipe, None
        if p is None:
            return
        self._log_dropped(p)
        self._ws_left.append(p)
        t = asyncio.get_running_loop().create_task(asyncio.to_thread(p.stop))
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _kill_wireshark(self) -> None:
        """Kill every Wireshark this session launched (close()); kill() joins and waits, so off the loop, all
        at once: the wait is one pipe's worst case, not their sum."""
        pipes, self._ws_left = self._ws_left, []
        if self._ws_pipe is not None:
            self._log_dropped(self._ws_pipe)
            pipes.append(self._ws_pipe)
            self._ws_pipe = None
        await asyncio.gather(*(asyncio.to_thread(p.kill) for p in pipes))

    # -- recorders ------------------------------------------------------------------------------
    async def recorder_start(self, tx: list[int], rx: list[int], bpf: str, limit: int = 1000) -> int:
        """Start a recorder (fixed, `limit` packets) and return its capture id. Refused by require_control,
        _gate_service and capture_slots. Holds `_capture_start_lock` across a fresh capture_status, the slot
        gate and capture_start, re-checking the clients after each await (not_connected; a recorder started
        just before the connection ended stays on the server). Remembers the capture as ours, logs and
        resyncs the status."""
        c = self.require_control()
        r = self._reader
        self._gate_service(tx, rx)
        async with self._capture_start_lock:
            await self._refuse_when_full()
            self._guard(c, r)
            label = f"Start recorder (tx {tx}, rx {rx}, limit {limit})"
            started: trexapi.CaptureStarted = await self._write_job(
                label, c, trexapi.capture_start, tx, rx, limit, "fixed", bpf)
            if not self._alive(c, r):
                raise trexapi.CommandRefused("not_connected",
                                             "The connection ended while the recorder was starting.")
            self._my_ids.add(started.id)
        self._log("info", f"{label}: capture {started.id}")
        await self._sync_quietly()
        return started.id

    async def recorder_stop(self, id: int) -> int:
        """Stop capture `id` (capture_stop on the control client) and return its packet count. Refused by
        require_control, capture_busy (the monitor's id, a monitor start in flight, a save of `id`) and
        endpoint_capture. Resyncs the status afterwards, success or failure, while the same clients are
        connected."""
        c = self.require_control()
        r = self._reader
        self._refuse_monitor_id(id)
        if id in self._saving:
            raise trexapi.CommandRefused("capture_busy", f"Capture {id} is being saved.")
        self._refuse_endpoint(id)
        try:
            count: int = await self._write_job(f"Stop capture {id}", c, trexapi.capture_stop, id)
        finally:
            if self._alive(c, r):
                await self._sync_quietly()
        return count

    async def recorder_remove(self, id: int) -> None:
        """Remove capture `id` from the server (capture_remove on the control client). Refused by
        require_control and capture_busy (the monitor's id, a monitor start in flight, a save of `id`).
        Afterwards, success or failure, while the same clients are connected: forgets `id` as ours and
        resyncs the status."""
        c = self.require_control()
        r = self._reader
        self._refuse_monitor_id(id)
        if id in self._saving:
            raise trexapi.CommandRefused("capture_busy", f"Capture {id} is being saved.")
        try:
            await self._write_job(f"Remove capture {id}", c, trexapi.capture_remove, id)
        finally:
            if self._alive(c, r):
                self._my_ids.discard(id)
                await self._sync_quietly()

    async def recorder_save(self, id: int) -> SpoolFile:
        """Save capture `id` into the spool, remove it from the server and return the file. Refused by
        require_control, spool_unavailable, capture_busy (the monitor's id, a monitor start in flight, a
        save of `id` already running) and endpoint_capture. capture_stop always, a peek (pkt_limit 0) for
        start_ts, a new or resumed spool file, then destructive fetches of FETCH_LIMIT, each batch written
        before the clients are re-checked (not_connected), progress published as `capture_save` after the
        first fetch, then every SAVE_PROGRESS_JOBS jobs. TrexApiError when start_ts changed between peek and
        first fetch (a file this save created is deleted) or SAVE_EMPTY_CUTOFF fetches in a row bring nothing
        while packets are still pending. A failure once the file is open keeps what was written as a partial
        file (that deleted new file aside); any failure publishes a `capture_save` error (not on a cancel),
        "not found" also resyncs, and a spool OSError becomes spool_error. On success: capture_remove (a
        failure is logged, not raised, though a lost connection still marks the session lost), forgets `id` as
        ours, the final `capture_save`, a log line, and a status resync while the same clients are
        connected."""
        c = self.require_control()
        r = self._reader
        spool = self.spool()
        self._refuse_monitor_id(id)
        if id in self._saving:
            raise trexapi.CommandRefused("capture_busy", f"Capture {id} is already being saved.")
        self._refuse_endpoint(id)
        saving = self._saving               # a reset replaces the set: discard from the one this save joined
        saving.add(id)
        label = f"Save capture {id}"
        state: dict[str, Any] = {"fetched": 0, "total": 0, "note": None}
        writer = None
        try:
            try:
                await self._write_job(label, c, trexapi.capture_stop, id)   # always, not by the cached row
                self._guard(c, r)
                host = f"{self._params.host}:{self._params.sync_port}"      # the server `r` belongs to
                # fetch is destructive and a status row has no start_ts: peek (pkt_limit 0 takes nothing and
                # names the capture), open the writer, only then fetch for real
                peek: trexapi.Fetched = await self._read_job(r, trexapi.capture_fetch_once, id, 0)
                self._guard(c, r)
                key = CaptureKey(host, peek.start_ts, id)
                stale = any(f["partial"] and f["capture_id"] == id for f in spool.list())
                writer = spool.resume(key)
                fresh = writer is None              # this Save created the file: nothing in it predates it
                if writer is None:
                    writer = spool.writer(key)
                    if spool.skipped:
                        state["note"] = (f"the partial file {spool.skipped[0]} for capture {id} is "
                                         "unreadable and was left in place")
                    elif stale:
                        state["note"] = f"a partial file from another capture with id {id} was left in place"
                first: trexapi.Fetched = await self._read_job(r, trexapi.capture_fetch_once, id, FETCH_LIMIT)
                if first.start_ts != peek.start_ts:
                    if fresh:
                        writer.abort()              # only its header: a resumed file keeps what it holds
                        writer = None
                    raise TrexApiError(f"Capture {id} changed during the save (start_ts {peek.start_ts} at "
                                       f"the peek, {first.start_ts} at the first fetch); up to "
                                       f"{len(first.pkts)} packets were taken from the server and not "
                                       "written")
                self._write_pkts(writer, first, first.start_ts)      # before the guard: fetch is destructive
                self._guard(c, r)
                pending, jobs, empties = first.pending, 1, (1 if not first.pkts and first.pending > 0 else 0)
                self._save_progress(id, writer.count, writer.count + pending, state)
                while pending > 0:
                    f: trexapi.Fetched = await self._read_job(r, trexapi.capture_fetch_once, id, FETCH_LIMIT)
                    empties = 0 if f.pkts else empties + 1
                    if empties >= SAVE_EMPTY_CUTOFF and f.pending > 0:
                        raise TrexApiError(self._no_progress(f.pending))
                    self._write_pkts(writer, f, first.start_ts)
                    self._guard(c, r)
                    pending, jobs = f.pending, jobs + 1
                    if jobs % SAVE_PROGRESS_JOBS == 0:
                        self._save_progress(id, writer.count, writer.count + pending, state)
                done = writer
                writer = None
                file = done.close()
            except BaseException as e:
                if writer is not None:
                    try:
                        file_partial = writer.suspend()       # the partial stays listed with partial: true
                        log.info("capture %s save kept %s", id, file_partial["name"])
                    except OSError as oe:                     # sidecar failed (full disk): the pcap stays
                        state["sidecar"] = True
                        self._log("warning", f"{label}: the partial's sidecar was not written: {oe}")
                    writer = None
                reason = e.message if isinstance(e, TrexApiError) else str(e)
                if state.get("sidecar"):
                    reason += "; the partial's sidecar was not written"
                if not isinstance(e, asyncio.CancelledError):
                    self._publish({"type": "capture_save", "id": id, "fetched": state["fetched"],
                                   "total": state["total"], "file": None,
                                   "error": reason,
                                   **({"note": state["note"]} if state["note"] else {})})
                if isinstance(e, TrexApiError) and trexapi.is_not_found(e):
                    await self._sync_quietly()               # the row is gone: drop it from the table
                if isinstance(e, OSError):                   # the spool failed: shaped, the partial is kept
                    raise trexapi.CommandRefused(
                        "spool_error", f"The capture file could not be written: {reason}") from e
                raise
            try:
                await self._write_job(label, c, trexapi.capture_remove, id)
            except TrexApiError as e:
                if not trexapi.is_not_found(e):
                    self._log("warning", f"{label}: the file is saved but the capture was not removed: "
                                         f"{e.message}")
            self._my_ids.discard(id)
            self._publish({"type": "capture_save", "id": id, "fetched": file["pkt_count"] or 0,
                           "total": file["pkt_count"] or 0, "file": file, "error": None,
                           **({"note": state["note"]} if state["note"] else {})})
            self._log("info", f"{label}: saved {file['pkt_count']} packets to {file['name']}")
        finally:
            saving.discard(id)
        if self._alive(c, r):
            await self._sync_quietly()
        return file

    @staticmethod
    def _no_progress(pending: int) -> str:
        """The error text of a save whose fetches bring no packets while `pending` remain on the server."""
        return (f"Save made no progress: {SAVE_EMPTY_CUTOFF} fetches in a row returned no packets while "
                f"{pending} were still pending.")

    @staticmethod
    def _write_pkts(writer: Any, f: trexapi.Fetched, start_ts: float) -> None:
        """Write each fetched packet to the spool writer, its time relative to `start_ts` (never negative)."""
        for p in f.pkts:
            ts_rel = max(0.0, float(p["ts"]) - start_ts)
            writer.write(ts_rel, base64.b64decode(p["binary"]), int(p["wirelen"]))

    def _save_progress(self, id: int, fetched: int, total: int, state: dict[str, Any]) -> None:
        """Record the save's progress in `state` and publish it as a `capture_save` message."""
        state["fetched"], state["total"] = fetched, total
        self._publish({"type": "capture_save", "id": id, "fetched": fetched, "total": total, "file": None,
                       "error": None})

    # -- lifecycle ------------------------------------------------------------------------------
    async def capture_refresh(self) -> None:
        """What GET /api/capture does first: only while CONNECTED is there a reader to ask."""
        if self._state_is_connected():
            await self.capture_sync()
            self._ensure_capture_loop()

    async def _stop_capture_loop(self) -> None:
        """Detach and cancel the poll loop task, if any, and wait for it to end; its outcome is not raised."""
        t, self._capture_task = self._capture_task, None
        if t is not None and not t.done():
            t.cancel()
        if t is not None:
            await asyncio.gather(t, return_exceptions=True)

    async def _remove_own_monitor(self) -> None:
        """Best effort stop + remove of the monitor this session started. Called by disconnect() only, which
        sets `_ending` first and cancels the loop, so no loop runs here (it could not see the monitor vanish
        and publish "removed on the server"): a monitor_start that finished while this waited for the lock
        does not restart one, `_ensure_capture_loop` refusing while `_ending`. A LOST session sends nothing:
        there is no server to answer."""
        async with self._capture_start_lock:      # an in-flight start finishes first, on a live client
            mon = self._monitor
            c = self._client
            if mon is None or c is None or not self._state_is_connected():
                return
            self._stopping = mon                  # a status landing meanwhile must not call it "gone"
            try:
                for fn in (trexapi.capture_stop, trexapi.capture_remove):
                    try:
                        await self._run(fn, c, mon.id)
                    except Exception as e:
                        self._log("warning", f"Removing capture monitor {mon.id} at disconnect: "
                                             f"{trexapi.translate(e).message}")
            finally:
                self._stopping = None
