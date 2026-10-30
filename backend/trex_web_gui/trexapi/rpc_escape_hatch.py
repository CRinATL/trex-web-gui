"""The raw RPCs: every `c._transmit` call under backend/trex_web_gui is in this module, and
tests/unit/test_rpc_escape_hatch.py::test_every_raw_transmit_is_listed fails when one is added elsewhere
or RAW_METHODS drifts from what is sent here.

Why each is raw: the client's own wrappers sleep inside the call (`fetch_capture_packets`,
_vendor/trex/common/trex_client.py:1058-1095) or send several RPCs (`stop_capture` stops, fetches and
removes in one call, :2560-2602), and P3c's rule is one executor job, one RPC. A capture's `fetch` is also
the only way to learn `start_ts` and `pending`, which the wrapper discards. Behaviour is covered by
tests/unit/test_capture.py (exact params and replies) and, against a server, by the capture contract tests."""
from typing import Any

# (method, command) of every raw send below, in source order.
RAW_METHODS = (("capture", "fetch"), ("capture", "stop"), ("capture", "remove"))


def _data(rc: Any) -> dict[str, Any]:
    """rc.data() for a good RC; for a bad one, a RuntimeError carrying the server's error text."""
    if not rc:
        # A plain exception, not TrexApiError: translate() returns a TrexApiError unchanged, and the callers'
        # `raise translate(e)` must classify the text (unreachable, restarted, or the server's own wording).
        raise RuntimeError(str(rc.err()))
    data: dict[str, Any] = rc.data()
    return data


def capture_fetch_raw(c: Any, capture_id: int, pkt_limit: int, snaplen: int) -> dict[str, Any]:
    """One capture fetch RPC; the reply data. Destructive: the server drains the packets it returns.
    One executor job; the reader or control client, the caller's choice."""
    rc = c._transmit("capture", params={"command": "fetch", "capture_id": capture_id,
                                                     "pkt_limit": pkt_limit, "snaplen": snaplen})
    return _data(rc)


def capture_stop_raw(c: Any, capture_id: int) -> dict[str, Any]:
    """One capture stop RPC; the reply data.
    One executor job; the reader or control client, the caller's choice."""
    rc = c._transmit("capture", params={"command": "stop", "capture_id": capture_id})
    return _data(rc)


def capture_remove_raw(c: Any, capture_id: int) -> dict[str, Any]:
    """One capture remove RPC; the reply data. The server discards the capture.
    One executor job; the reader or control client, the caller's choice."""
    rc = c._transmit("capture", params={"command": "remove", "capture_id": capture_id})
    return _data(rc)
