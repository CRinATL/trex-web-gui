"""One stats read: Global + per-port + total in a single StatsBatch.update (P3-min Task 4), and the PGID and
utilization reads (P3a Task 3). Executor thread only.
The numbers are the client's own to_dict() of its stats objects (the ones trex-console tui reads); rates
come from the server and the GUI computes none. Counters are relative to the client's baseline (set on
connect and on Reset), so they mean "since connect, Reset or Clear"."""
import math
import time
from typing import Any, TypedDict

from .errors import translate

# "global" is a Python keyword, hence the functional form.
StatsView = TypedDict("StatsView", {"ts": float, "interval_s": float, "global": dict[str, float | None],
                                    "ports": dict[str, dict[str, float | None]],
                                    "total": dict[str, float | None]})


def _num(v: Any) -> float | None:
    """'N/A' (the client's missing-value string), a missing key, a string, NaN or inf become None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _clean(d: Any) -> dict[str, float | None]:
    """d as {str key: _num(value)}; {} when d is not a dict."""
    return {str(k): _num(v) for k, v in d.items()} if isinstance(d, dict) else {}


def read_stats(c: Any, interval_s: float) -> StatsView:
    """One _get_stats_common call, not STLClient.get_stats (which adds get_pgid_stats). An exception from the
    call is translated, so the caller can tell a lost server from a failed read."""
    try:
        raw = c._get_stats_common(ports=sorted(c.ports), sync_now=True)
    except Exception as e:
        raise translate(e) from e
    ports = {str(k): _clean(v) for k, v in raw.items() if k not in ("global", "total")}
    return {"ts": time.time(), "interval_s": interval_s, "global": _clean(raw.get("global")),
            "ports": ports, "total": _clean(raw.get("total"))}


# Not 1152: CPgIdStats.get_stats (trex_stl_stats.py) chunks with `while index <= len` and
# max_pgid_in_query = 1152, so a list of exactly 1152 ids sends a second request with `pgids: []`, which the
# server reads as "all" and rejects when more than 1152 ids are active (trex_stl_fs.cpp, dump_json,
# MAX_ALLOWED_PGID_LIST_LEN).
MAX_PGIDS = 1151


class PgidView(TypedDict):
    ts: float
    interval_s: float
    active: dict[str, list[int]]
    truncated: bool
    ver_id: dict[str, int]
    flow: dict[str, Any]
    latency: dict[str, Any]
    flow_global: dict[str, Any]


class UtilView(TypedDict):
    ts: float
    cpu: list[dict[str, Any]]
    mbuf: dict[str, Any]


def _val(v: Any) -> Any:
    """Recursively JSON-clean a client stats value: dict keys become strings, a number is kept as it
    is (an int stays an int), and StatNotAvailable ('N/A'), any other string, NaN or inf become
    None."""
    if isinstance(v, dict):
        return {str(k): _val(x) for k, x in v.items()}
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def read_pgid_stats(c: Any, interval_s: float) -> PgidView:
    """get_active_pgids, then one get_pgid_stats for at most MAX_PGIDS ids, ascending, absolute
    values (relative=False: the client's relative mode is rebased by every start and connect). With
    no active id the second RPC is not sent (an empty list means "all" to the server). An id that is
    active but missing from the answer was removed between the two RPCs and is left out; a latency
    id with no traffic yet is simply absent from `latency`. Every error is translated through
    errors.translate."""
    try:
        active = c.get_active_pgids()
        flow_ids = sorted(int(i) for i in active.get("flow_stats") or [])
        lat_ids = sorted(int(i) for i in active.get("latency") or [])
        ids = sorted(set(flow_ids) | set(lat_ids))
        view: PgidView = {
            "ts": time.time(), "interval_s": interval_s,
            "active": {"flow_stats": flow_ids, "latency": lat_ids}, "truncated": len(ids) > MAX_PGIDS,
            "ver_id": {}, "flow": {}, "latency": {}, "flow_global": {"rx_err": {}, "tx_err": {}}}
        if not ids:
            return view
        raw = c.pgid_stats.get_stats(ids[:MAX_PGIDS], relative=False)
        view["ver_id"] = {str(k): v for k, v in (raw.get("ver_id") or {}).items()}
        for pg, st in (raw.get("flow_stats") or {}).items():
            if pg == "global":
                view["flow_global"] = {"rx_err": _val(st.get("rx_err", {})),
                                       "tx_err": _val(st.get("tx_err", {}))}
            else:
                view["flow"][str(pg)] = _val(st)
        for pg, st in (raw.get("latency") or {}).items():
            if pg == "global":
                continue                                    # always zeros (old_flow, bad_hdr)
            body = _val({k: v for k, v in st.items() if k != "latency"})
            body["latency"] = _val({k: v for k, v in st.get("latency", {}).items() if k != "hdrh"})
            view["latency"][str(pg)] = body
    except Exception as e:
        raise translate(e) from e
    return view


def read_util(c: Any) -> UtilView:
    """One get_utilization read: per-core CPU history (index 0 newest) and, per socket and pool size, mbuf
    `{free, total}`. Calls `c.util_stats.update_sync` directly: TRexClient.get_util_stats ignores the falsy RC
    that AbstractStats.update_sync returns on failure, so a timed-out read would return the previous snapshot
    as if fresh.
    Every error is translated through errors.translate."""
    try:
        rc = c.util_stats.update_sync(c.conn.rpc)
        if rc is not None and not rc:
            raise Exception(str(rc.err()))
        raw = c.util_stats.to_dict()
        cpu = [{"ports": [p for p in core.get("ports", []) if isinstance(p, int) and not isinstance(p, bool)],
                "history": [None if _num(h) is None else h for h in core.get("history", [])]}
               for core in raw.get("cpu") or []]
        mbuf: dict[str, Any] = {}
        for sock, sizes in (raw.get("mbuf_stats") or {}).items():
            mbuf[str(sock)] = {
                str(size): {"free": _val(pair[0]), "total": _val(pair[1])}
                for size, pair in sizes.items() if isinstance(pair, (list, tuple)) and len(pair) == 2}
    except Exception as e:
        raise translate(e) from e
    return {"ts": time.time(), "cpu": cpu, "mbuf": mbuf}
