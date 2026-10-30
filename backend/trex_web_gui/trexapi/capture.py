"""Capture RPCs, one executor job and one RPC each (P3c). Executor thread only: `c` is the STLClient and
every call here blocks on the wire. Reads go through the reader's client, writes through the control
client (trex_session.py); that choice is the caller's.

`fetch` is destructive (the server drains what it hands out), so `capture_fetch_once` never retries.
Errors are translated; `is_not_found` recognises the server's "capture ID not found", which the session
treats as "gone"."""
from typing import Any, Literal, NamedTuple, TypedDict

from .errors import translate
from .rpc_escape_hatch import capture_fetch_raw, capture_remove_raw, capture_stop_raw


class CaptureStarted(NamedTuple):
    id: int
    start_ts: float


class CaptureRow(TypedDict):
    id: int
    state: str
    mode: str | None
    tx: list[int]
    rx: list[int]
    bpf: str
    count: int
    matched: int
    fetched: int
    limit: int
    bytes: int
    endpoint: str | None
    snaplen: int | None


class Fetched(NamedTuple):
    pkts: list[dict[str, Any]]
    pending: int
    start_ts: float


def ports_of(mask: int) -> list[int]:
    """Port bitmask (bit i set means port i) to the sorted port list."""
    return [i for i in range(max(mask, 0).bit_length()) if mask >> i & 1]


def capture_start(c: Any, tx: list[int], rx: list[int], limit: int, mode: Literal["fixed", "cyclic"],
                  bpf: str, snaplen: int = 0) -> CaptureStarted:
    """Start a capture (one RPC); its id and the server's start timestamp.
    One executor job; the reader or control client, the caller's choice."""
    try:
        r = c.start_capture(tx_ports=tx, rx_ports=rx, limit=limit, mode=mode, bpf_filter=bpf, snaplen=snaplen)
    except Exception as e:
        raise translate(e) from e
    return CaptureStarted(int(r["id"]), float(r["ts"]))


def capture_status(c: Any) -> list[CaptureRow]:
    """Every capture the server holds, as rows ordered by id (one RPC; reads, changes nothing).
    One executor job; the reader or control client, the caller's choice."""
    try:
        by_id = c.get_capture_status()
    except Exception as e:
        raise translate(e) from e
    rows: list[CaptureRow] = []
    for cid in sorted(by_id):
        r = by_id[cid]
        f = r.get("filter") or {}
        rows.append({
            "id": int(r["id"]), "state": str(r.get("state", "")), "mode": r.get("mode"),
            "tx": ports_of(int(f.get("tx", 0))), "rx": ports_of(int(f.get("rx", 0))),
            "bpf": str(f.get("bpf", "")), "count": int(r.get("count", 0)),
            "matched": int(r.get("matched", 0)),
            "fetched": int(r.get("fetched", 0)), "limit": int(r.get("limit", 0)),
            "bytes": int(r.get("bytes", 0)),
            "endpoint": r.get("endpoint"), "snaplen": r.get("snaplen")})
    return rows


def capture_fetch_once(c: Any, capture_id: int, pkt_limit: int, snaplen: int = 0) -> Fetched:
    """Fetch up to pkt_limit packets (one RPC, no retry). Destructive: the server drains what it hands
    out, so a reply lost on the way loses those packets.
    One executor job; the reader or control client, the caller's choice."""
    try:
        d = capture_fetch_raw(c, capture_id, pkt_limit, snaplen)
    except Exception as e:
        raise translate(e) from e
    return Fetched(list(d["pkts"]), int(d["pending"]), float(d["start_ts"]))


def capture_stop(c: Any, capture_id: int) -> int:
    """Stop the capture (one RPC); the packet count the server reports. The capture stays until removed.
    One executor job; the reader or control client, the caller's choice."""
    try:
        return int(capture_stop_raw(c, capture_id)["pkt_count"])
    except Exception as e:
        raise translate(e) from e


def capture_remove(c: Any, capture_id: int) -> None:
    """Remove the capture from the server (one RPC), discarding any packets it still holds.
    One executor job; the reader or control client, the caller's choice."""
    try:
        capture_remove_raw(c, capture_id)
    except Exception as e:
        raise translate(e) from e


def is_not_found(e: BaseException) -> bool:
    """True when the error text is the server's "capture ID not found", i.e. the capture is gone."""
    return "capture ID not found" in str(e)
