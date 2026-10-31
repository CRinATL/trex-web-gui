"""P3b port configuration, service-mode bracket, ping, scan and xstats (plan P3b Task 3).

Each function is ONE client call (or one service run, or one short ARP attempt) and runs as one executor
job: the session, not this module, brackets a command with service_on / service_restore, checks the port is
idle and owned, and translates what is raised. Executor thread only (see ports.fail_fast)."""
import ipaddress
from typing import Any, Literal, NamedTuple

from .errors import CommandRefused, ServerRestarted, ServerUnreachable, TrexApiError, translate


class ServiceSaved(NamedTuple):
    was_on: bool


class ArpResult(NamedTuple):
    state: Literal["resolved", "unresolved"]
    mac: str | None
    error: str | None


class PingLine(NamedTuple):
    status: Literal["reply", "timeout", "unreachable"]
    text: str
    rtt_ms: float | None


class Neighbour(NamedTuple):
    type: str
    mac: str
    ipv6: str


class XstatsView(NamedTuple):
    port: int
    values: dict[str, int]


def service_state(c: Any, port: int) -> Literal["on", "off", "filtered"]:
    """The client reads filtered as off (stl/trex_stl_port.py:523-525) and the server does not report the
    mask, so filtered is read from the client's own flag."""
    p = c.ports[port]
    if getattr(p, "service_mode_filtered", False):
        return "filtered"
    return "on" if p.service_mode else "off"


def service_on(c: Any, port: int) -> ServiceSaved:
    """Turn service mode on when it is off (one RPC); records whether it was already on. A port in
    filtered service mode raises CommandRefused (service_filtered) and is left alone.
    Executor thread only."""
    state = service_state(c, port)
    if state == "filtered":
        raise CommandRefused("service_filtered", f"Port {port} is in filtered service mode; turn service "
                             "mode fully on or off in the Control tab first.")
    if state == "off":
        c.set_service_mode(ports=[port], enabled=True)
    return ServiceSaved(was_on=state == "on")


def service_restore(c: Any, port: int, saved: ServiceSaved) -> str | None:
    """Back to the saved mode. A refusal (server stx/stl/trex_stl_port.cpp:1334-1344) is returned as text, so
    the command's own result is never hidden by it; a loss (ServerUnreachable, ServerRestarted) is raised,
    for the session to mark."""
    if saved.was_on:
        return None
    try:
        c.set_service_mode(ports=[port], enabled=False)
    except Exception as e:
        err = translate(e)
        if isinstance(err, ServerUnreachable | ServerRestarted):
            raise err from e
        return err.message
    return None


def set_vlan(c: Any, port: int, tags: list[int]) -> None:
    """Set the port's VLAN tags in one client call; client errors propagate untranslated.
    Executor thread only."""
    c.set_vlan(ports=[port], vlan=tags)


def set_l2(c: Any, port: int, dst_mac: str) -> None:
    """Put the port in L2 mode with dst_mac, in one client call; errors propagate untranslated.
    Executor thread only."""
    c.set_l2_mode(port, dst_mac)


def set_l3(c: Any, port: int, src: str, dst: str) -> None:
    """Set the port's L3 addresses through the port-level call, which does not resolve: STLClient.set_l3_mode
    would resolve at once (trex_client.py:1752), and the resolve is a separate job (ARP failure is not a
    failed Apply). A failed RC raises the translated error."""
    rc = c.ports[port].set_l3_mode(src, dst)
    if not rc:
        raise translate(Exception(str(rc.err())))


def _unresolved_or_raise(c: Any, port: int, e: Exception) -> ArpResult:
    """The failure rule of the ARP resolve: a loss or refusal is raised translated; any other failure is
    "unresolved" with its text when src and dst are set and the layer state is unresolved, else re-raised
    untranslated (from inside the caller's except block)."""
    err = translate(e)
    if isinstance(err, ServerUnreachable | ServerRestarted | CommandRefused):
        raise err from e      # a loss must reach the session, which marks it; it is not "unresolved"
    cfg = c.ports[port].get_layer_cfg() or {}
    v4 = cfg.get("ipv4") or {}
    if v4.get("src") and v4.get("dst") and v4.get("state") == "unresolved":
        return ArpResult("unresolved", None, err.message)
    raise e


def arp_attempt(c: Any, port: int, listen_s: float, first: bool) -> ArpResult | None:
    """One ARP try on the port: the vendor's acquired, service and L3 checks, then a ServiceARP listening
    listen_s seconds in its own service context, which is torn down when it returns (the vendor's
    STLClient.resolve, trex_client.py:2281, run in short pieces so other jobs can run between them). On
    `first` the port's ARP is invalidated. A reply sets the port's L3 mode with the MAC (a bad RC raises
    TrexApiError) and returns "resolved"; no reply returns None (try again). A failure follows the resolve
    rule: a loss or refusal is raised translated, any other failure is "unresolved" with its text when src
    and dst are set and the layer state is unresolved, else re-raised untranslated. Executor thread only."""
    from trex.common.services.trex_service_arp import ServiceARP
    from trex.common.trex_psv import PSV_ACQUIRED, PSV_L3, PSV_SERVICE
    try:
        # the vendor's own check (trex_client.py:2299): a port reset out of L3 between attempts fails here
        c.psv.validate("arp", [port], (PSV_ACQUIRED, PSV_SERVICE, PSV_L3))
        p = c.ports[port]
        if first:
            p.invalidate_arp()
        v4 = (p.get_layer_cfg() or {}).get("ipv4") or {}
        src, dst = v4.get("src"), v4.get("dst")
        ctx = c.create_service_ctx(port)
        arp = ServiceARP(ctx, dst_ip=dst, src_ip=src, vlan=p.get_vlan_cfg(), timeout_sec=listen_s)
        ctx.run(arp)
        record = arp.get_record()
        if not record:
            return None
        rc = p.set_l3_mode(src, dst, record.dst_mac)
        if not rc:
            raise translate(Exception(str(rc.err())))
        return ArpResult("resolved", record.dst_mac, None)
    except Exception as e:
        return _unresolved_or_raise(c, port, e)


def arp_unresolved(c: Any, port: int) -> ArpResult:
    """The outcome once every attempt got no reply: "unresolved" with the vendor's no-reply text when src and
    dst are set and the layer state is unresolved, else that text raised as TrexApiError. Executor thread
    only."""
    err = translate(Exception(f"Could not resolve following ports: [{port}]"))
    v4 = (c.ports[port].get_layer_cfg() or {}).get("ipv4") or {}
    if v4.get("src") and v4.get("dst") and v4.get("state") == "unresolved":
        return ArpResult("unresolved", None, err.message)
    raise TrexApiError(err.message)


def conf_ipv6(c: Any, port: int, enabled: bool, src: str | None) -> None:
    """Enable or disable IPv6 on the port (with src), in one client call; errors propagate untranslated.
    Executor thread only."""
    c.conf_ipv6(port, enabled, src)


_PingStatus = Literal["reply", "timeout", "unreachable"]
# PINGRecord.TIMEOUT, SUCCESS, ICMP_TYPE_DEST_UNREACHABLE
_V4_STATE: dict[int, _PingStatus] = {0: "timeout", 2: "reply", 3: "unreachable"}
_V6_STATUS: dict[str, _PingStatus] = {"success": "reply", "unreachable": "unreachable", "timeout": "timeout"}


def ping_once(c: Any, port: int, dst: str, pkt_size: int) -> PingLine:
    """One echo (the 1 s interval belongs to the caller). IPv4 records are PINGRecord objects
    (services/trex_service_icmp.py:135-163; UNREACHABLE = 1 is never assigned), IPv6 records are dicts
    (services/trex_service_ipv6.py:212-237)."""
    rec = c.ping_ip(port, dst, pkt_size=pkt_size, count=1)[0]
    if isinstance(ipaddress.ip_address(dst), ipaddress.IPv4Address):
        status = _V4_STATE.get(rec.state)
        if status is None:
            raise TrexApiError(f"Unexpected ping record state {rec.state!r}.")
        rtt = rec.rtt if status == "reply" and isinstance(rec.rtt, int | float) else None
        return PingLine(status, str(rec), None if rtt is None else float(rtt))
    status = _V6_STATUS.get(rec.get("status"))
    if status is None:
        raise TrexApiError(f"Unexpected ping status {rec.get('status')!r}.")
    rtt6 = rec.get("rtt") if status == "reply" else None
    return PingLine(status, rec["formatted_string"], None if rtt6 is None else float(rtt6))


def scan_neighbours(c: Any, port: int, timeout_s: int = 3) -> list[Neighbour]:
    """One scan6 run on the port, waiting timeout_s for answers; client errors propagate untranslated.
    Executor thread only."""
    found = c.scan6(ports=[port], timeout=timeout_s)[port] or []
    return [Neighbour(type=n["type"], mac=n["mac"], ipv6=n["ipv6"]) for n in found]


def read_xstats(c: Any, port: int) -> XstatsView:
    """The server's absolute counters; update_sync returns None on success and a falsy RC on failure
    (common/stats/trex_stats.py:114-122), which STLClient.get_xstats ignores."""
    xs = c.ports[port].get_port_xstats()
    rc = xs.update_sync(c.conn.rpc)
    if rc is not None and not rc:
        raise translate(Exception(str(rc.err())))
    return XstatsView(port=port, values=dict(xs.to_dict(relative=False)))
