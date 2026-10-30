"""PortView: one port as the browser sees it, read from the STLClient's own cache (plan §4.1: port state
may change between two executor calls, so it is read as a snapshot). Executor thread only (see fail_fast)."""
import re
from typing import Any, TypedDict

from .errors import CommandRefused, translate

# Port.STATE_* order (trex/common/trex_port.py:84-92). Upstream's STATES_MAP folds STREAMS into "IDLE"
# for display; the tree needs them apart (inventory A2.3), so the server's own names are used.
STATE_NAMES = ("IDLE", "STREAMS", "TX", "PAUSE", "PCAP_TX", "ASTF_LOADED", "ASTF_PARSE", "ASTF_BUILD",
               "ASTF_CLEANUP")


class PortView(TypedDict):
    id: int
    state: str
    owner: str
    owned: bool
    mine_elsewhere: bool    # owned under this client's user name by another session; held_elsewhere_by_me
    service: bool
    service_filtered: bool
    link_up: bool | None
    speed_gbps: float | None
    promiscuous: bool | None
    multicast: bool | None
    fc_mode: int | None
    vlan: list[int]
    layer_cfg: dict[str, Any]
    rx_filter_mode: str | None
    profiles: dict[str, str]
    rx_info: dict[str, Any]
    info: dict[str, Any]


def _view(pid: int, p: Any) -> PortView:
    """One port's PortView from the client's cache, no RPC; the state reads DOWN when the link is down.
    Executor thread only."""
    attr = p.get_ts_attr() or {}                 # a deep copy under the attr lock (trex_port.py:874)
    status = p.status or {}
    link = (attr.get("link") or {}).get("up")
    st = p.state
    state = STATE_NAMES[st] if isinstance(st, int) and 0 <= st < len(STATE_NAMES) else str(st)
    if link is False:
        state = "DOWN"
    # profile_state_list follows every command and event (trex_stl_port.py:67-78, 600);
    # status["state_profile"] changes only when sync() runs, i.e. at the next poll. Owned by Task 6's
    # test_profiles_follow_commands_without_a_poll.
    profiles = {str(k): STATE_NAMES[v] if isinstance(v, int) and 0 <= v < len(STATE_NAMES) else str(v)
                for k, v in (getattr(p, "profile_state_list", None) or {}).items()}
    return PortView(
        id=pid, state=state, owner=p.get_owner(), owned=p.is_acquired(),
        mine_elsewhere=held_elsewhere_by_me(p),
        service=bool(p.service_mode),
        service_filtered=bool(getattr(p, "service_mode_filtered", False)), link_up=link,
        speed_gbps=attr.get("speed"), promiscuous=(attr.get("promiscuous") or {}).get("enabled"),
        multicast=(attr.get("multicast") or {}).get("enabled"), fc_mode=(attr.get("fc") or {}).get("mode"),
        vlan=list((attr.get("vlan") or {}).get("tags", [])), layer_cfg=dict(attr.get("layer_cfg") or {}),
        rx_filter_mode=attr.get("rx_filter_mode"), profiles=profiles,
        rx_info=dict(status.get("rx_info") or {}), info=dict(p.info))


def port_views(c: Any) -> list[PortView]:
    """A PortView for every port, in id order, from the client's cache (no RPC).
    Executor thread only."""
    return [_view(pid, c.ports[pid]) for pid in sorted(c.ports)]


def sync_port(c: Any, pid: int) -> None:
    """Refresh port `pid`'s cache from the server (one sync RPC) and drop profiles the server reports idle
    or omits. A failed sync raises the translated error. Executor thread only."""
    rc = c.ports[pid].sync()
    if rc.bad():
        # a plain Exception, so translate() runs its text matching (it returns a TrexApiError unchanged)
        raise translate(Exception(str(rc.err())))
    _drop_idle_profiles(c.ports[pid])


def sync_ports(c: Any) -> list[PortView]:
    """sync_port each port in id order, then return the views. A failed sync raises the translated error;
    earlier ports stay refreshed. Executor thread only."""
    for pid in sorted(c.ports):
        sync_port(c, pid)
    return port_views(c)


def _drop_idle_profiles(p: Any) -> None:
    """STLPort.__set_profile_state ignores state 0 (trex_stl_port.py:67-68) and nothing else removes the
    key, so a profile the server now reports IDLE (or omits), e.g. after a foreign reset, would stay in the
    cache as STREAMS. Drops the profile's state key together with its stream bookkeeping (the
    profile_stream_list entry and those ids in p.streams), the same set of things the client's
    __delete_profile (trex_stl_port.py:81-95) clears; a state key left alone makes the next
    remove_all_streams raise KeyError at trex_stl_port.py:88-89."""
    cache = getattr(p, "profile_state_list", None)
    if not isinstance(cache, dict):
        return
    if "state_profile" not in (p.status or {}):      # no server per-profile state to compare with
        return
    server = (p.status or {}).get("state_profile") or {}
    stream_lists = getattr(p, "profile_stream_list", None)
    streams = getattr(p, "streams", None)
    # list(cache): a snapshot, the subscriber thread can insert keys meanwhile
    for key in [k for k in list(cache) if server.get(k, "IDLE") == "IDLE"]:
        cache.pop(key, None)
        ids = stream_lists.pop(key, None) if isinstance(stream_lists, dict) else None
        for sid in ids or ():
            if isinstance(streams, dict):
                streams.pop(sid, None)


def held_elsewhere_by_me(p: Any) -> bool:
    """True when the port is owned under this client's user name but not by this client: another session of
    the same user holds it, typically a trex-web-gui or console that exited without releasing it. Reads the
    cache. Executor thread only."""
    return not p.is_acquired() and bool(p.owner) and p.owner == p.ctx.username


# A port held by another session of the same user: Re-acquire my ports (Control, All ports) takes it back
# (RC1-25).
def _mine_elsewhere(pid: int, owner: str) -> str:
    return f"Port {pid} is held by another session of yours ({owner}); Re-acquire my ports takes it back."


def check_ports(c: Any, ports: list[int], need_owned: bool) -> None:
    """Raise CommandRefused: bad_port for an id the server lacks, not_owned when need_owned and the client
    does not own the port. Reads the cache; sends nothing.
    Executor thread only."""
    for pid in ports:
        if pid not in c.ports:
            raise CommandRefused("bad_port", f"There is no port {pid}; the server has {len(c.ports)}.")
        p = c.ports[pid]
        if need_owned and not p.is_acquired():
            if held_elsewhere_by_me(p):
                raise CommandRefused("not_owned", _mine_elsewhere(pid, p.owner))
            owner = p.get_owner() or "nobody"
            raise CommandRefused("not_owned", f"Port {pid} is owned by {owner}; acquire it first.")


def check_not_foreign(c: Any, ports: list[int]) -> None:
    """Raise CommandRefused not_owned for the first port in `ports` that another client owns (the cache has an
    owner and this client has not acquired it); a port nobody owns, or this client owns, passes. Reads the
    cache; sends nothing. For a non-force acquire. Executor thread only."""
    for pid in ports:
        p = c.ports.get(pid)
        if p is not None and held_elsewhere_by_me(p):
            raise CommandRefused("not_owned", _mine_elsewhere(pid, p.owner))
        if p is not None and p.get_owner() and not p.is_acquired():
            raise CommandRefused("not_owned",
                                 f"Port {pid} is owned by {p.get_owner()}; force-acquire to take it.")


def fail_fast(c: Any) -> None:
    """New RPCs make one attempt instead of 1 + ZMQ_RETRY_BASE (trex_req_resp_client.py:97-101, 311-335).
    An RPC already inside its retry loop is unaffected: it computed retry_left on entry.
    Safe from the loop thread (the one exception to "executor only"): a plain store, queued on the executor
    it would wait behind the RPC it exists to cut short (controller ruling R7)."""
    c.conn.rpc.set_retry_base(False)


# The server's refusal at start (trex_stl_port.cpp:275-277): no valid destination MAC on the port, and a
# stream whose destination mode (flags bits 1-2) is not "from packet". RC1-30: the text named neither the
# port's state nor the fix.
_DST_MAC_REFUSED = re.compile(r"Port (\d+) dest MAC is invalid and there are streams without explicit")


def explain_dst_mac(c: Any, text: str) -> str | None:
    """A plain explanation of the server's dest-MAC refusal in `text`, read from the client's cache: why
    the port has no destination MAC and which streams of profile "_" take theirs from the port (numbered by
    0-based position in the sorted stream ids, not by TRex stream id), then the two ways out. None when
    `text` is another error or names an unknown port. Executor thread only."""
    m = _DST_MAC_REFUSED.search(text)
    if not m:
        return None
    pid = int(m.group(1))
    p = c.ports.get(pid)
    if p is None:
        return None
    lc = (p.get_ts_attr() or {}).get("layer_cfg") or {}
    ipv4 = lc.get("ipv4") or {}
    if ipv4.get("state") == "unresolved":
        gw = ipv4.get("dst")
        why = f"its L3 gateway {gw} did not answer ARP" if gw else "its L3 gateway did not answer ARP"
    else:
        why = "it has no destination MAC set"
    ids = sorted((getattr(p, "profile_stream_list", None) or {}).get("_", []))
    streams = getattr(p, "streams", None) or {}
    implicit = [str(i) for i, sid in enumerate(ids)
                if sid in streams and not streams[sid].is_explicit_dst_mac()]
    if not implicit:
        which = "some streams take"
    elif len(implicit) == 1:
        which = f"stream {implicit[0]} takes"
    else:
        which = f"streams {', '.join(implicit)} take"
    return (f"Port {pid} cannot start: {why}, and {which} the destination MAC from the port. Give port "
            f"{pid} a destination on Control > Config (an L2 destination MAC, or an L3 gateway that answers "
            f"ARP), or set Destination MAC to From packet on those streams in Studio > Properties and assign "
            f"the profile again.")
