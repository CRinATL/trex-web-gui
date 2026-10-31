"""P3b port commands of TrexSession (plan §5 P3b, project review O2): L2/L3/VLAN/IPv6 Apply, ping, the IPv6
neighbour scan and xstats. A mixin, so the session's lock, executor and loss handling stay in one class;
every command runs its client calls as executor jobs through the session's `_job` / `_run`."""
import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from trex_web_gui import trexapi
from trex_web_gui.trexapi import TrexApiError
from trex_web_gui.trexapi.ports import STATE_NAMES

PING_INTERVAL_S = 1.0                  # between two echoes, spent on the loop, not on the executor
ARP_ATTEMPT_S = 0.5                    # one ARP try's listen: a job on the control executor
ARP_BUDGET_S = 3.0                     # the resolve's whole listen, the vendor's one try (ServiceARP)
ARP_ENDED = "the connection ended; the resolve stopped"
ARP_ENDED_RESULT = trexapi.ArpResult("unresolved", None, ARP_ENDED)
CONFIGURABLE = frozenset({"IDLE", "STREAMS", "ASTF_LOADED"})    # server stx/common/trex_port.cpp:378-380 etc.
LEGACY_IPV6 = "IPv6 configuration needs the linux_based stack; this server runs legacy"


@dataclass(frozen=True)
class LayerRequest:
    mode: Literal["L2", "L3"]
    dst_mac: str | None
    src_ipv4: str | None
    dst_ipv4: str | None
    vlan: list[int]
    ipv6: tuple[bool, str | None] | None


class ApplyResult(NamedTuple):
    arp: trexapi.ArpResult | None
    service_restore_error: str | None


class PingRow(NamedTuple):
    """A PingLine, or the final line of a ping that ended early ("refused" or "error")."""
    status: str
    text: str
    rtt_ms: float | None


class PingResult(NamedTuple):
    run: int            # the id on every `ping` message of this call (session-wide, increasing)
    lines: list[PingRow]
    service_restore_error: str | None


class ScanResult(NamedTuple):
    neighbours: list[trexapi.Neighbour]
    service_restore_error: str | None


class PortConfigCommands:
    """Mixed into TrexSession, which provides everything declared under TYPE_CHECKING."""
    if TYPE_CHECKING:
        _publish: Callable[[dict[str, object]], None]
        _ping_runs: int

        # TrexSession's: not_connected (and read_only when mutating) before any RPC; the control client
        def require_control(self, mutating: bool = True) -> Any: ...
        # TrexSession's: runs fn(*args) on the control executor
        async def _run(self, fn: Callable[..., Any], *args: Any) -> Any: ...
        # TrexSession's: one control-executor job; logs a failure, a loss marks the session lost
        async def _job(self, label: str, fn: Callable[..., Any], *args: Any) -> Any: ...
        # TrexSession's: logs and publishes a `log` message
        def _log(self, level: str, message: str) -> None: ...
        # TrexSession's: re-reads the port cache and publishes `ports` on a change, swallowing any failure
        async def _refresh_quietly(self, c: Any) -> None: ...
        # TrexSession's: True while c is still the control client and the session is CONNECTED
        def _connected_to(self, c: Any) -> bool: ...
        # TrexSession's: logs a failed job, marks lost on a loss-class error, returns the translated error
        def _note_failure(self, label: str, e: Exception) -> TrexApiError: ...
        # TrexSession's: CONNECTED goes LOST with the cause (CONNECTING keeps it for connect())
        def _mark_lost(self, cause: str) -> None: ...
        # TrexSession's: port_busy when a ping or scan (or, unless held_only, an Apply) holds a port
        def _refuse_busy(self, label: str, ports: list[int], held_only: bool = False) -> None: ...
        # TrexSession's: claims or frees a port and publishes `busy` on a change
        def _set_busy(self, port: int, kind: str | None) -> None: ...

    @staticmethod
    def _require_configurable(c: Any, port: int) -> None:
        """Owned, and in a state the server accepts configuration in. Never STLPort.is_writeable: it is true
        in TX and PAUSE on dynamic ports, where the server then refuses (controller ruling, Task 1)."""
        trexapi.check_ports(c, [port], need_owned=True)
        st = c.ports[port].state
        name = STATE_NAMES[st] if isinstance(st, int) and 0 <= st < len(STATE_NAMES) else str(st)
        if name not in CONFIGURABLE:
            raise trexapi.CommandRefused("port_active", f"Port {port} is transmitting; stop traffic first.")

    async def _restore_service(self, c: Any, port: int, label: str,
                               saved: trexapi.ServiceSaved) -> str | None:
        """service_restore as its own job. Skipped when the connection is gone (the server may then keep
        service mode on). Shielded: a cancelled command still puts the port back."""
        if not self._connected_to(c):
            self._log("warning", f"{label}: the connection ended; service mode may still be on "
                                 f"for port {port}")
            return None
        try:
            err: str | None = await asyncio.shield(self._run(trexapi.service_restore, c, port, saved))
        except (trexapi.ServerUnreachable, trexapi.ServerRestarted) as e:
            raise self._note_failure(label, e) from e    # a loss during the restore marks the session lost
        if err:
            self._log("warning", f"{label}: port {port} is left in service mode: {err}")
        return err

    async def _bracketed(self, c: Any, port: int, label: str, kind: str, body: Callable[[], Awaitable[Any]],
                         *, ipv6: bool = False) -> tuple[Any, str | None]:
        """Claim the port, remember its service mode and turn it on (job 1), run `body`, restore the mode
        in a finally. Returns (body's result, the restore error)."""
        self._refuse_busy(label, [port])
        self._set_busy(port, kind)
        try:
            # job 1, control executor: configurable (IPv6 also refuses a legacy stack), then service on
            def first() -> trexapi.ServiceSaved:
                self._require_configurable(c, port)
                if ipv6 and (c.ports[port].info or {}).get("stack") == "legacy":
                    raise trexapi.CommandRefused("ipv6_unsupported", LEGACY_IPV6)
                return trexapi.service_on(c, port)
            job1 = asyncio.ensure_future(self._job(label, first))
            try:
                saved = await asyncio.shield(job1)
            except asyncio.CancelledError:
                # service_on still completes on the executor: wait for it and put the port back
                try:
                    saved = await asyncio.shield(job1)
                except Exception:
                    pass                                 # job 1 failed: nothing was turned on
                else:
                    await self._restore_service(c, port, label, saved)
                raise
            restore_error: list[str | None] = []
            try:
                await self._refresh_quietly(c)           # the Control tab shows service mode on
                result = await body()
            finally:
                restore_error.append(await self._restore_service(c, port, label, saved))
            return result, restore_error[0]
        finally:
            self._set_busy(port, None)

    async def apply_layer(self, port: int, req: LayerRequest) -> ApplyResult:
        """Jobs in order: service on, VLAN, L2 or L3, then (L3) the ARP resolve, then IPv6 if given, and the
        restore. The resolve is ceil(ARP_BUDGET_S / ARP_ATTEMPT_S) short attempts, one job each, so other
        commands (Stop) run between them; the first reply, or a failure that ends it, is the result, and
        arp_unresolved is the last job when none replied. If the connection ends between attempts the resolve
        stops "unresolved" and IPv6 is not attempted. An unresolved ARP is a warning: the L3 configuration
        stays."""
        c = self.require_control()
        label = f"Apply {req.mode} configuration to port {port}"

        # wraps fn(c, port, *args) as a control-executor job that first re-checks the port is configurable
        def step(fn: Callable[..., Any], *args: Any) -> Callable[[], Any]:
            # control executor: the configurable check, then the step's client call
            def job() -> Any:
                self._require_configurable(c, port)       # re-checked per job: the state may have changed
                return fn(c, port, *args)
            return job

        # the ARP resolve as short attempts, one job each; an ended connection is the ARP_ENDED_RESULT
        async def resolve() -> trexapi.ArpResult:
            attempts = math.ceil(ARP_BUDGET_S / ARP_ATTEMPT_S)
            for i in range(attempts):
                if not self._connected_to(c):
                    return ARP_ENDED_RESULT
                r = await self._job(label, step(trexapi.arp_attempt, ARP_ATTEMPT_S, i == 0))
                if r is not None:
                    return r                      # type: ignore[no-any-return]
            no_reply: trexapi.ArpResult = await self._job(label, step(trexapi.arp_unresolved))
            return no_reply

        # inside the service bracket: the configuration jobs in order; returns the ARP result (L3 only)
        async def body() -> trexapi.ArpResult | None:
            arp: trexapi.ArpResult | None = None
            await self._job(label, step(trexapi.set_vlan, list(req.vlan)))
            if req.mode == "L2":
                await self._job(label, step(trexapi.set_l2, req.dst_mac or ""))
            else:
                await self._job(label, step(trexapi.set_l3, req.src_ipv4 or "", req.dst_ipv4 or ""))
                arp = await resolve()
                if arp is ARP_ENDED_RESULT:
                    return arp                    # like ping: nothing more runs on a connection that ended
            if req.ipv6 is not None:
                await self._job(label, step(trexapi.conf_ipv6, req.ipv6[0], req.ipv6[1]))
            return arp
        try:
            arp, restore_error = await self._bracketed(c, port, label, "apply", body,
                                                       ipv6=bool(req.ipv6 and req.ipv6[0]))
        except Exception:
            await self._refresh_quietly(c)
            raise
        if arp is not None and arp.state == "unresolved":
            self._log("warning", f"{label}: L3 set, ARP unresolved: {arp.error}")
        else:
            self._log("info", f"{label}: done")
        if self._connected_to(c):                  # a connection that ended is not refreshed, as ping does
            await self._refresh_quietly(c)
        return ApplyResult(arp, restore_error)

    async def ping(self, port: int, dst: str, count: int, pkt_size: int) -> PingResult:
        """`count` echoes, one executor job each, with the interval on the loop; the executor is free
        between echoes. A refusal or error in an echo ends the ping with a final line naming it."""
        c = self.require_control()
        label = f"Ping {dst} from port {port}"
        lines: list[PingRow] = []
        self._ping_runs += 1
        run = self._ping_runs

        # control executor: the configurable check, then one echo
        def echo() -> trexapi.PingLine:
            self._require_configurable(c, port)
            return trexapi.ping_once(c, port, dst, pkt_size)

        # records the row and publishes it as a `ping` message
        def emit(seq: int, row: PingRow) -> None:
            lines.append(row)
            self._publish({"type": "ping", "port": port, "run": run, "seq": seq, "count": count,
                           "status": row.status, "text": row.text, "rtt_ms": row.rtt_ms, "ts": time.time()})

        outcome: dict[str, str | None] = {"failure": None}

        # inside the service bracket: re-checks _connected_to before each echo and stops with a warning when
        # the connection ended; a refusal or non-loss error ends it with one final row, a loss raises
        async def body() -> None:
            for seq in range(1, count + 1):
                if not self._connected_to(c):
                    self._log("warning", f"{label}: the connection ended; the ping stopped")
                    outcome["ended"] = "yes"
                    return
                try:
                    line = await self._run(echo)
                except trexapi.CommandRefused as e:
                    outcome["failure"] = e.message
                    emit(seq, PingRow("refused", e.message, None))
                    break
                except Exception as e:
                    err = trexapi.translate(e)           # a non-loss error is logged once, by the summary
                    if isinstance(err, trexapi.ServerUnreachable | trexapi.ServerRestarted):
                        raise self._note_failure(label, e) from e
                    outcome["failure"] = err.message
                    emit(seq, PingRow("error", err.message, None))
                    break
                emit(seq, PingRow(line.status, line.text, line.rtt_ms))
                self._log("ping", f"Port {port}: {line.text}")
                if seq < count:
                    await asyncio.sleep(PING_INTERVAL_S)
        _, restore_error = await self._bracketed(c, port, label, "ping", body)
        if outcome["failure"] is not None:
            self._log("warning", f"{label}: failed: {outcome['failure']}")
        elif "ended" not in outcome:
            self._log("info", f"{label}: done")
        if self._connected_to(c):
            await self._refresh_quietly(c)
        return PingResult(run, lines, restore_error)

    async def scan(self, port: int) -> ScanResult:
        """Scan `port` for IPv6 neighbours as one job inside the service bracket (_bracketed: port_busy while
        a ping, scan or Apply holds it); logs the count and refreshes the ports. A failure is raised, logged
        by _job (a loss marks the session lost), with no closing refresh."""
        c = self.require_control()
        label = f"Scan for IPv6 neighbours on port {port}"

        # inside the service bracket: the one scan job; returns the neighbours found
        async def body() -> list[trexapi.Neighbour]:
            # control executor: the configurable check, then the neighbour scan
            def scan_job() -> list[trexapi.Neighbour]:
                self._require_configurable(c, port)
                return trexapi.scan_neighbours(c, port)
            found: list[trexapi.Neighbour] = await self._job(label, scan_job)
            return found
        found, restore_error = await self._bracketed(c, port, label, "scan", body)
        self._log("info", f"{label}: done, {len(found)} found")
        await self._refresh_quietly(c)
        return ScanResult(found, restore_error)

    async def xstats(self, port: int) -> trexapi.XstatsView:
        """One read on the control executor (not the reader the stats loop uses): no log, no refresh, no
        ownership needed; a loss-class error marks the session lost, every error is raised translated."""
        c = self.require_control(mutating=False)

        # control executor: bad_port only (ownership not needed), then read_xstats
        def job() -> trexapi.XstatsView:
            trexapi.check_ports(c, [port], need_owned=False)
            return trexapi.read_xstats(c, port)
        try:
            view: trexapi.XstatsView = await self._run(job)
        except trexapi.CommandRefused:
            raise
        except Exception as e:
            err = trexapi.translate(e)
            if isinstance(err, trexapi.ServerUnreachable | trexapi.ServerRestarted):
                self._mark_lost(err.message)
            raise err from e
        return view
