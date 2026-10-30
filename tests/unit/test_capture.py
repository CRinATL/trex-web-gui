"""Capture RPC wrappers against a stub client; reply shapes are the ones recorded in
fixtures/p3c/rpc.jsonl."""
import json
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.trexapi import (
    ServerRestarted,
    ServerUnreachable,
    TrexApiError,
    capture_fetch_once,
    capture_remove,
    capture_start,
    capture_status,
    capture_stop,
    is_not_found,
    ports_of,
)

RPC = Path(__file__).resolve().parent.parent / "fixtures" / "p3c" / "rpc.jsonl"


def _recorded(command: str, *, nonempty_pkts: bool = False) -> dict[str, Any]:
    """First recorded capture RPC for command (skipping empty-pkts replies if asked), else AssertionError."""
    for line in RPC.read_text().splitlines():
        d = json.loads(line)
        req = d["req"]
        if isinstance(req, dict) and req["method"] == "capture" and req["params"]["command"] == command:
            if nonempty_pkts and not d["resp"]["result"].get("pkts"):
                continue
            return d       # type: ignore[no-any-return]
    raise AssertionError(command)


class _RC:
    def __init__(self, data: Any = None, err: str | None = None) -> None:
        """Holds the payload and the error text; err None means success."""
        self._data, self._err = data, err

    def __bool__(self) -> bool:
        """True when no error was set, like the real return-code object."""
        return self._err is None

    def data(self) -> Any:
        """The payload given at construction."""
        return self._data

    def err(self) -> str:
        """The error text, or an empty string when none was set."""
        return self._err or ""


class _Stub:
    def __init__(self, rc: _RC | None = None) -> None:
        """Starts with nothing sent, started or in status; rc is what _transmit returns."""
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.started: dict[str, Any] = {}
        self.rc = rc
        self.status: dict[int, dict[str, Any]] = {}

    def start_capture(self, **kw: Any) -> dict[str, Any]:
        """Records the keyword arguments in started; returns a fixed {"id": 4, "ts": 261.5}."""
        self.started = kw
        return {"id": 4, "ts": 261.5}

    def get_capture_status(self) -> dict[int, dict[str, Any]]:
        """The status dict the test set, keyed by capture id."""
        return self.status

    def _transmit(self, method: str, params: dict[str, Any]) -> _RC:
        """Appends (method, params) to sent and returns rc; asserts rc was given."""
        self.sent.append((method, params))
        assert self.rc is not None
        return self.rc


def test_capture_start_passes_exact_kwargs() -> None:
    c = _Stub()
    r = capture_start(c, [0], [1, 2], 100, "cyclic", "udp", 64)
    assert c.started == {"tx_ports": [0], "rx_ports": [1, 2], "limit": 100, "mode": "cyclic",
                         "bpf_filter": "udp", "snaplen": 64}
    assert (r.id, r.start_ts) == (4, 261.5)


def test_capture_status_rows_with_port_lists() -> None:
    c = _Stub()
    row = {"id": 3, "state": "ACTIVE", "mode": "fixed", "count": 2, "matched": 9, "fetched": 1, "limit": 20,
           "bytes": 120}
    c.status = {
        7: {**row, "id": 7, "filter": {"tx": 5, "rx": 0, "bpf": "arp"}},
        3: {**row, "filter": {"tx": 0, "rx": 2, "bpf": "udp"}},
        9: {"id": 9, "state": "ACTIVE", "filter": {"tx": 0, "rx": 1, "bpf": ""}, "matched": 4, "fetched": 0,
            "endpoint": "ep", "snaplen": 96},
    }
    rows = capture_status(c)
    assert [r["id"] for r in rows] == [3, 7, 9]
    assert (rows[0]["tx"], rows[0]["rx"], rows[0]["bpf"], rows[0]["mode"]) == ([], [1], "udp", "fixed")
    assert (rows[1]["tx"], rows[1]["rx"]) == ([0, 2], [])
    assert rows[1]["endpoint"] is None and rows[1]["snaplen"] is None
    assert (rows[2]["endpoint"], rows[2]["snaplen"], rows[2]["mode"]) == ("ep", 96, None)
    assert ports_of(0) == [] and ports_of(5) == [0, 2]


def test_fetch_once_sends_exact_params() -> None:
    rec = _recorded("fetch", nonempty_pkts=True)["resp"]["result"]
    c = _Stub(_RC(rec))
    f = capture_fetch_once(c, 7, 50)
    assert c.sent == [("capture", {"command": "fetch", "capture_id": 7, "pkt_limit": 50, "snaplen": 0})]
    assert f.pkts == rec["pkts"] and f.pending == rec["pending"] and f.start_ts == rec["start_ts"]


def test_stop_returns_pkt_count() -> None:
    c = _Stub(_RC({"endpoint": "", "pkt_count": 12}))
    assert capture_stop(c, 3) == 12
    assert c.sent == [("capture", {"command": "stop", "capture_id": 3})]


def test_remove_sends_exact_params() -> None:
    c = _Stub(_RC({}))
    assert capture_remove(c, 3) is None
    assert c.sent == [("capture", {"command": "remove", "capture_id": 3})]


def test_bad_rc_is_translated_and_not_found_recognised() -> None:
    gone = json.loads((RPC.parent / "summary.json").read_text())["errors"]["fetch_9999"]
    c = _Stub(_RC(err=gone))
    for call in (lambda: capture_fetch_once(c, 9999, 50), lambda: capture_stop(c, 9999),
                 lambda: capture_remove(c, 9999)):
        with pytest.raises(TrexApiError) as ei:
            call()
        assert is_not_found(ei.value)
    assert not is_not_found(TrexApiError("capture limit has reached"))


@pytest.mark.parametrize("text,cls", [
    ("*** [RPC] - Failed to get server response at tcp://127.0.0.1:4501", ServerUnreachable),
    ("API handler provided mismatch", ServerRestarted),
    ("capture limit has reached", None)])
def test_bad_rc_text_is_classified(text: str, cls: type[TrexApiError] | None) -> None:
    c = _Stub(_RC(err=text))
    for call in (lambda: capture_fetch_once(c, 1, 50), lambda: capture_stop(c, 1),
                 lambda: capture_remove(c, 1)):
        with pytest.raises(TrexApiError) as ei:
            call()
        if cls is None:
            assert type(ei.value) is TrexApiError and "capture limit has reached" in str(ei.value)
        else:
            assert type(ei.value) is cls
