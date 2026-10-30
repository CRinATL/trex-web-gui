"""read_stats against the stateful fake (the client is the oracle) and against stub clients (value
cleaning)."""
import time
from types import SimpleNamespace
from typing import Any

import pytest
from trex_web_gui import trexapi
from trex_web_gui.trexapi import MAX_PGIDS, ServerUnreachable, read_pgid_stats, read_stats, read_util


def _client_with_traffic(fake_trex, foreign_client, mult="10kpps"):
    """A client on a fake as gui-a: port 0 force-acquired, one 100 pps UDP stream added and started at mult.
    foreign_client disconnects it."""
    f = fake_trex()
    c = foreign_client(f, user="gui-a")        # loads the vendored client; trex is importable from here on
    from trex.stl.api import IP, UDP, Ether, STLPktBuilder, STLStream, STLTXCont
    c.acquire(ports=[0], force=True)
    c.add_streams(STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()/("x" * 18)), mode=STLTXCont(pps=100)),
                  ports=[0])
    c.start(ports=[0], mult=mult)
    return c


def test_read_stats_matches_the_client_oracle(fake_trex, foreign_client) -> None:
    c = _client_with_traffic(fake_trex, foreign_client)
    time.sleep(0.5)
    view = read_stats(c, 1.0)
    oracle = c.get_stats()
    assert set(view) == {"ts", "interval_s", "global", "ports", "total"} and view["interval_s"] == 1.0
    assert set(view["ports"]) == {"0", "1"}
    assert view["ports"]["0"]["tx_pps"] == pytest.approx(10_000) == oracle[0]["tx_pps"]
    for pid in (0, 1):
        for key in ("tx_pps", "rx_pps", "tx_bps", "rx_bps"):
            assert view["ports"][str(pid)][key] == pytest.approx(oracle[pid][key])
        for key in ("opackets", "ipackets"):   # read first, so never ahead; behind by one interval at most
            behind = oracle[pid][key] - view["ports"][str(pid)][key]
            assert 0 <= behind <= 10_000, (pid, key, behind)
    assert view["global"]["tx_pps"] == pytest.approx(oracle["global"]["tx_pps"])


class _Stub:
    def __init__(self, raw: Any = None, exc: BaseException | None = None) -> None:
        """Holds a port-0 table, the raw stats to return and the exception to raise instead (or None)."""
        self.ports = {0: object()}
        self._raw, self._exc = raw, exc

    def _get_stats_common(self, ports=None, sync_now=True):
        """Raw stats call: asserts ports [0] and sync_now; raises the held exception, else returns the
        stats."""
        assert ports == [0] and sync_now is True
        if self._exc:
            raise self._exc
        return self._raw


def test_non_numeric_becomes_none() -> None:
    raw = {"global": {"tx_pps": "N/A", "rx_pps": 5, "cpu_util": "high", "queue_full": float("nan")},
           0: {"opackets": 7, "tx_pps": None, "rx_pps": "N/A", "tx_util": float("inf"), "flag": True},
           "total": {"opackets": 7, "obytes": "N/A"}}
    v = read_stats(_Stub(raw), 0.5)
    assert v["global"] == {"tx_pps": None, "rx_pps": 5.0, "cpu_util": None, "queue_full": None}
    assert v["ports"] == {"0": {"opackets": 7.0, "tx_pps": None, "rx_pps": None, "tx_util": None,
                                "flag": None}}
    assert v["total"] == {"opackets": 7.0, "obytes": None}


def test_read_stats_translates_trex_errors() -> None:
    trexapi.load()
    from trex.stl.api import TRexError
    with pytest.raises(ServerUnreachable) as ei:
        read_stats(_Stub(exc=TRexError("Failed to get server response")), 1.0)
    assert "Cannot reach the TRex server" in ei.value.message


# -- PGID and utilization reads (P3a Task 3) ---------------------------------------------------------


def _pgid_client_with_traffic(fake_trex, foreign_client):
    """A client on a fake as gui-a: port 0 force-acquired, started at 200 pps on a flow-stats stream (pg 5)
    and a latency-stats stream (pg 7). foreign_client disconnects it."""
    f = fake_trex()
    c = foreign_client(f, user="gui-a")
    from trex.stl.api import (
        IP,
        UDP,
        Ether,
        STLFlowLatencyStats,
        STLFlowStats,
        STLPktBuilder,
        STLStream,
        STLTXCont,
    )
    c.acquire(ports=[0], force=True)
    pkt = STLPktBuilder(pkt=Ether()/IP()/UDP()/("x" * 18))
    c.add_streams([STLStream(packet=pkt, mode=STLTXCont(pps=100), flow_stats=STLFlowStats(pg_id=5)),
                   STLStream(packet=pkt, mode=STLTXCont(pps=100),
                             flow_stats=STLFlowLatencyStats(pg_id=7))], ports=[0])
    c.start(ports=[0], mult="200pps")
    return c


def test_read_pgid_stats_matches_the_client_oracle(fake_trex, foreign_client) -> None:
    c = _pgid_client_with_traffic(fake_trex, foreign_client)
    time.sleep(0.5)
    view = read_pgid_stats(c, 1.0)
    oracle = c.pgid_stats.get_stats([5, 7], relative=False)
    assert set(view) == {"ts", "interval_s", "active", "truncated", "ver_id", "flow", "latency",
                         "flow_global"}
    assert view["active"] == {"flow_stats": [5], "latency": [7]} and view["truncated"] is False
    assert view["ver_id"] == {"5": 0, "7": 1} == {str(k): v for k, v in oracle["ver_id"].items()}
    assert set(view["flow"]) == {"5", "7"} and set(view["latency"]) == {"7"}
    for pg in (5, 7):
        got, want = view["flow"][str(pg)], oracle["flow_stats"][pg]
        assert set(got) == set(want)
        # rates: the same instant, exact
        for key in ("tx_pps", "rx_pps", "tx_bps", "rx_bps", "tx_bps_l1", "rx_bps_l1"):
            assert got[key] == {str(p): v for p, v in want[key].items()}
        assert got["tx_pps"]["0"] == pytest.approx(100) and got["rx_pps"]["1"] == pytest.approx(100)
        # counters: read first, so never ahead; a short step behind
        for key in ("tx_pkts", "rx_pkts", "tx_bytes", "rx_bytes"):
            for port, v in want[key].items():
                # one second at 100 pps, 64-byte frames
                step = 100 if key.endswith("pkts") else 100 * 64
                assert 0 <= v - got[key][str(port)] <= step, (pg, key, port)
    assert view["latency"]["7"]["latency"] == {k: v for k, v in (
        (k, ({str(b): n for b, n in x.items()} if k == "histogram" else x))
        for k, x in oracle["latency"][7]["latency"].items())
        if k != "hdrh"}
    assert view["flow_global"] == {"rx_err": {"0": 0, "1": 0}, "tx_err": {"0": 0, "1": 0}}


class _PgStub:
    """The two client entry points read_pgid_stats uses, with the client's output shape (int keys)."""
    def __init__(self, active: Any, raw: Any = None, exc: BaseException | None = None) -> None:
        """Holds active ids, raw stats and exception; pgid_stats is the stub itself; calls start empty."""
        self.active, self.raw, self.exc = active, raw, exc
        self.pgid_stats = self
        self.active_calls = 0
        self.stat_calls: list[tuple[list[int], bool]] = []

    def get_active_pgids(self):
        """Counts the call, then raises the held exception or returns the active ids."""
        self.active_calls += 1
        if self.exc:
            raise self.exc
        return self.active

    def get_stats(self, ids, relative):
        """Records (list(ids), relative) in stat_calls and returns the raw stats; the exception is ignored."""
        self.stat_calls.append((list(ids), relative))
        return self.raw


def test_no_active_ids_sends_one_rpc() -> None:
    stub = _PgStub({"flow_stats": [], "latency": []})
    v = read_pgid_stats(stub, 1.0)
    # an empty list would mean "all" to the server
    assert stub.active_calls == 1 and stub.stat_calls == []
    assert (v["active"] == {"flow_stats": [], "latency": []} and v["flow"] == {} and v["latency"] == {}
            and v["ver_id"] == {})
    assert v["truncated"] is False and v["flow_global"] == {"rx_err": {}, "tx_err": {}}


def test_listed_but_absent_id_is_skipped() -> None:
    raw = {"ver_id": {"5": 0}, "flow_stats": {"global": {"rx_err": {0: 0}, "tx_err": {0: 0}},
                                              5: {"tx_pkts": {0: 3, "total": 3}}}}
    # 9 removed, 7 has seen no traffic yet
    stub = _PgStub({"flow_stats": [5, 9], "latency": [7]}, raw)
    v = read_pgid_stats(stub, 1.0)
    assert stub.stat_calls == [([5, 7, 9], False)]
    assert set(v["flow"]) == {"5"} and v["latency"] == {} and v["ver_id"] == {"5": 0}
    assert v["active"] == {"flow_stats": [5, 9], "latency": [7]}


def test_global_and_hdrh_are_dropped() -> None:
    raw = {"ver_id": {"7": 1},
           "flow_stats": {"global": {"rx_err": {0: 1}, "tx_err": {0: 2}}, 7: {"tx_pkts": {0: 1, "total": 1}}},
           "latency": {"global": {"old_flow": 0, "bad_hdr": 0},
                       7: {"err_cntrs": {"dropped": 0},
                           "latency": {"average": 12.5, "histogram": {20: 4, 300: 1}, "hdrh": "HISTFAAA"}}}}
    v = read_pgid_stats(_PgStub({"flow_stats": [], "latency": [7]}, raw), 0.5)
    assert "global" not in v["flow"] and "global" not in v["latency"]
    assert v["flow_global"] == {"rx_err": {"0": 1}, "tx_err": {"0": 2}}
    assert v["latency"] == {"7": {"err_cntrs": {"dropped": 0},
                                  "latency": {"average": 12.5, "histogram": {"20": 4, "300": 1}}}}
    assert v["interval_s"] == 0.5


def test_not_available_becomes_none() -> None:
    trexapi.load()
    from trex.common.trex_types import StatNotAvailable
    raw = {"ver_id": {"7": 0},
           "flow_stats": {7: {"rx_bytes": {0: StatNotAvailable("rx_bytes"),
                                           "total": StatNotAvailable("total")},
                              "tx_pkts": {0: 5, "total": 5}}},
           "latency": {7: {"err_cntrs": {},
                           "latency": {"average": StatNotAvailable("average"), "jitter": float("nan"),
                                       "total_max": 9, "histogram": {}}}}}
    v = read_pgid_stats(_PgStub({"flow_stats": [7], "latency": [7]}, raw), 1.0)
    assert v["flow"]["7"] == {"rx_bytes": {"0": None, "total": None}, "tx_pkts": {"0": 5, "total": 5}}
    assert v["latency"]["7"]["latency"] == {"average": None, "jitter": None, "total_max": 9, "histogram": {}}


def test_read_pgid_stats_translates_trex_errors() -> None:
    trexapi.load()
    from trex.stl.api import TRexError
    with pytest.raises(ServerUnreachable):
        read_pgid_stats(_PgStub({}, exc=TRexError("Failed to get server response")), 1.0)


def test_pgid_ids_are_capped_and_sorted() -> None:
    stub = _PgStub({"flow_stats": list(range(2000, 0, -1)), "latency": []}, {"ver_id": {}})
    v = read_pgid_stats(stub, 1.0)
    assert stub.stat_calls == [(list(range(1, MAX_PGIDS + 1)), False)] and v["truncated"] is True
    assert v["active"]["flow_stats"] == list(range(1, 2001))


class _RC:
    """A failed RC as `rpc.transmit` returns it on a timeout (JsonRpcClient._send_raw_msg_safe disconnects and
    returns RC_ERR): falsy, with the error text in `err()`."""

    def __init__(self, text: str) -> None:
        """Holds the error text."""
        self.text = text

    def __bool__(self) -> bool:
        """Always False, like a failed RC."""
        return False

    def err(self) -> str:
        """The held error text."""
        return self.text


class _UtilStub:
    """The `util_stats` / `conn.rpc` surface `read_util` uses: `update_sync` returns None (success) or a falsy
    `_RC`, and `to_dict` returns the held raw stats, as `UtilStats` does."""

    def __init__(self, raw: Any = None, rc: _RC | None = None) -> None:
        """Holds the raw utilisation stats and the failed RC `update_sync` returns
        instead of None (or None)."""
        self.raw, self.rc = raw, rc
        self.util_stats, self.conn = self, SimpleNamespace(rpc=object())

    def update_sync(self, rpc: Any) -> Any:
        """None on success, else the held failed RC, as AbstractStats.update_sync does."""
        return self.rc

    def to_dict(self) -> Any:
        """The raw stats: on a real client, the last good snapshot even after a failed update."""
        return self.raw

    def get_util_stats(self) -> Any:
        """What `TRexClient.get_util_stats` does: ignores update_sync's RC and returns the stale dict."""
        self.update_sync(self.conn.rpc)
        return self.to_dict()


def test_read_util_shape() -> None:
    raw = {"cpu": [{"history": [4, 3, 1, 0], "ports": [0, -1]}, {"history": ["N/A", 2.5], "ports": []}],
           "mbuf_stats": {"cpu-socket-0": {"64b": [81900, 81900], "9kb": [2040, 2048]},
                          "cpu-socket-1": {"64b": [5, 10]}}}
    v = read_util(_UtilStub(raw))
    assert set(v) == {"ts", "cpu", "mbuf"}
    assert v["cpu"] == [{"ports": [0, -1], "history": [4, 3, 1, 0]}, {"ports": [], "history": [None, 2.5]}]
    assert v["mbuf"] == {
        "cpu-socket-0": {"64b": {"free": 81900, "total": 81900}, "9kb": {"free": 2040, "total": 2048}},
        "cpu-socket-1": {"64b": {"free": 5, "total": 10}}}


def test_read_util_against_the_fake_matches_the_client(fake_trex, foreign_client) -> None:
    c = foreign_client(fake_trex(), user="gui-a")
    v, oracle = read_util(c), c.get_util_stats()
    assert (v["cpu"][0]["history"] == oracle["cpu"][0]["history"]
            and v["cpu"][0]["ports"] == oracle["cpu"][0]["ports"])
    for sock, sizes in oracle["mbuf_stats"].items():
        for size, (free, total) in sizes.items():
            assert v["mbuf"][sock][size] == {"free": free, "total": total}


def test_read_util_raises_on_a_failed_rpc_instead_of_returning_stale_stats() -> None:
    # TRexClient.get_util_stats ignores update_sync's falsy RC and returns the previous snapshot;
    # read_util must not
    stale = {"cpu": [{"history": [1], "ports": [0]}], "mbuf_stats": {}}
    with pytest.raises(ServerUnreachable):
        read_util(_UtilStub(stale, rc=_RC("*** [RPC] - Failed to get server response from tcp://127.0.0.1:4501")))


def test_read_util_translates_an_exception_from_update_sync() -> None:
    trexapi.load()
    from trex.stl.api import TRexError

    class Raising(_UtilStub):
        """update_sync raises instead of returning an RC."""

        def update_sync(self, rpc: Any) -> Any:
            """Raises the TRexError a failed reconnect would."""
            raise TRexError("Failed to get server response")

    with pytest.raises(ServerUnreachable):
        read_util(Raising({}))


def test_malformed_answers_are_translated() -> None:
    from trex_web_gui.trexapi import TrexApiError
    with pytest.raises(TrexApiError):                                     # parsing is inside the try
        read_pgid_stats(_PgStub({"flow_stats": [5], "latency": []}, {"flow_stats": 7}), 1.0)
    with pytest.raises(TrexApiError):
        read_util(_UtilStub({"cpu": [None], "mbuf_stats": {}}))
