"""The stateful fake's PGID and utilization answers follow its stream state (P3a Task 2). Shapes and the
recorded values come from tests/fixtures/p3a/rpc.jsonl; the fake answers in the server's wire shape, so these
tests read it raw through the client's transport (`_transmit`), not through the client's derived view."""
import json
import time

import pytest
from support import FIXTURES

RPC = [json.loads(x) for x in (FIXTURES / "p3a" / "rpc.jsonl").read_text().splitlines()]


def recorded_results(method):
    """Generator of every recorded `result` for `method` in tests/fixtures/p3a/rpc.jsonl, in file order
    (batched calls included)."""
    for r in RPC:
        resps = r["resp"] if isinstance(r["resp"], list) else [r["resp"]]
        reqs = r["req"] if isinstance(r["req"], list) else [r["req"]]
        by_id = {x["id"]: x for x in resps}
        for q in reqs:
            if q["method"] == method:
                yield by_id[q["id"]]["result"]


def first_tx_util():
    """The first recorded get_utilization result whose first CPU core serves port 0; StopIteration when
    none does."""
    return next(u for u in recorded_results("get_utilization") if u["cpu"][0]["ports"][0] == 0)


def recorded_latency_block():
    """The `lat` block of the first non-"g" latency entry in any recorded get_pgid_stats result;
    AssertionError when there is none."""
    for res in recorded_results("get_pgid_stats"):
        for k, v in (res["latency"] or {}).items():
            if k != "g":
                return v["lat"]
    raise AssertionError("no recorded latency block")


class Rig:
    def __init__(self, fake_trex, foreign_client):
        """Start a fake, connect a client "gui-a" to it and acquire port 0 (forced); loads the vendored
        `trex.stl.api` into `self.api`."""
        self.c = foreign_client(fake_trex(), user="gui-a")
        from trex.stl import api
        self.api = api
        self.c.acquire(ports=[0], force=True)

    def stream(self, pps=100, stats=None, latency=None):
        """A continuous UDP stream at `pps`; with flow stats under pg_id `stats` if given, else latency
        stats under pg_id `latency` if given, else none. Not added to the port."""
        t = self.api
        fs = (
            t.STLFlowStats(pg_id=stats)
            if stats is not None
            else t.STLFlowLatencyStats(pg_id=latency)
            if latency is not None
            else None
        )
        return t.STLStream(
            packet=t.STLPktBuilder(pkt=t.Ether() / t.IP() / t.UDP() / ("x" * 18)),
            mode=t.STLTXCont(pps=pps),
            flow_stats=fs,
        )

    def add(self, *streams):
        """Add `streams` to port 0 through the client."""
        self.c.add_streams(list(streams), ports=[0])

    def active(self):
        """The `ids` of a raw get_active_pgids (`flow_stats` and `latency` lists); asserts the call
        succeeded."""
        rc = self.c._transmit("get_active_pgids")
        assert rc, rc.err()
        return rc.data()["ids"]

    def stats(self, pgids):
        """The raw get_pgid_stats data for `pgids`, in the server's wire shape; asserts the call succeeded."""
        rc = self.c._transmit("get_pgid_stats", params={"pgids": pgids})
        assert rc, rc.err()
        return rc.data()


@pytest.fixture
def rig(fake_trex, foreign_client):
    """Return a Rig on a default fake. It tears nothing down itself: the foreign_client fixture disconnects
    the client, then fake_trex stops the fake."""
    return Rig(fake_trex, foreign_client)


def test_active_pgids_follow_added_streams(rig):
    assert rig.active() == {"flow_stats": [], "latency": []}
    # the recorded empty answer
    assert rig.stats([]) == {"flow_stats": None, "latency": {"g": None}, "ver_id": None}
    # the last has no flow stats
    rig.add(rig.stream(stats=9), rig.stream(latency=7), rig.stream(stats=5), rig.stream())
    assert rig.active() == {"flow_stats": [5, 9], "latency": [7]}
    rig.c.remove_all_streams(ports=[0])
    assert rig.active() == {"flow_stats": [], "latency": []}


def test_pgid_counters_advance_only_while_tx(rig):
    rig.add(rig.stream(pps=100, stats=5))
    idle = rig.stats([5])["flow_stats"]["5"]
    assert all(v == 0 for k in idle for v in idle[k].values())
    rig.c.start(ports=[0], mult="100pps")
    first = rig.stats([5])["flow_stats"]["5"]
    time.sleep(0.5)
    second = rig.stats([5])["flow_stats"]["5"]
    assert second["tp"]["0"] > first["tp"]["0"] and second["tp"]["1"] == 0           # sent on port 0 only
    assert second["tps"]["0"] == pytest.approx(100) and second["tps"]["1"] == 0
    # 64-byte frames
    assert (abs(second["tb"]["0"] - second["tp"]["0"] * 64) <= 64
            and second["tbs"]["0"] == pytest.approx(100 * 64 * 8))
    # port 0's Tx is port 1's Rx
    assert second["rp"] == {"0": 0, "1": second["tp"]["0"]} and second["rps"]["1"] == pytest.approx(100)
    rig.c.stop(ports=[0])
    frozen = rig.stats([5])["flow_stats"]["5"]
    time.sleep(0.3)
    after = rig.stats([5])["flow_stats"]["5"]
    assert after == frozen and after["tp"]["0"] >= second["tp"]["0"]
    assert all(v == 0 for k in ("tps", "tbs", "rps", "rbs") for v in after[k].values())


def test_stream_rate_is_its_share_of_the_multiplier(rig):
    rig.add(rig.stream(pps=100, stats=5), rig.stream(pps=300, stats=6))
    rig.c.start(ports=[0], mult="1kpps")
    fs = rig.stats([5, 6])["flow_stats"]
    # 1000 x 100/400, 1000 x 300/400
    assert fs["5"]["tps"]["0"] == pytest.approx(250) and fs["6"]["tps"]["0"] == pytest.approx(750)


def test_latency_pgid_appears_only_after_its_port_has_transmitted(rig):
    rig.add(rig.stream(pps=100, stats=5), rig.stream(pps=100, latency=7))
    idle = rig.stats([5, 7])
    # listed active, absent from latency
    assert set(idle["flow_stats"]) == {"5", "7"} and idle["latency"] == {"g": None}
    rig.c.start(ports=[0], mult="100pps")
    time.sleep(0.3)
    tx = rig.stats([5, 7])
    assert tx["latency"] == {"7": {"lat": recorded_latency_block()}, "g": None}
    rig.c.stop(ports=[0])
    # present after stop
    assert rig.stats([5, 7])["latency"] == tx["latency"]


def test_ver_id_changes_on_readd(rig):
    rig.add(rig.stream(stats=5), rig.stream(latency=7))
    assert rig.stats([5, 7])["ver_id"] == {"5": 0, "7": 1}
    rig.add(rig.stream(stats=8))
    # a later pg_id takes the next number
    assert rig.stats([5, 7, 8])["ver_id"] == {"5": 0, "7": 1, "8": 2}
    rig.c.remove_all_streams(ports=[0])
    rig.add(rig.stream(stats=5), rig.stream(latency=7))
    # a re-add is a new stream
    assert rig.stats([5, 7])["ver_id"] == {"5": 3, "7": 4}


def test_empty_list_means_all_and_over_limit_is_an_error(rig):
    rig.add(*[rig.stream(pps=1, stats=i) for i in range(1, 1153)])
    # [] with 1152 active: all of them
    assert len(rig.stats([])["flow_stats"]) == 1152
    # exactly 1152 asked: fine
    assert len(rig.stats(list(range(1, 1153)))["flow_stats"]) == 1152
    rc = rig.c._transmit("get_pgid_stats", params={"pgids": list(range(1, 1154))})
    assert not rc and "Asking information for too many packet group IDs. Max allowed is 1152" in rc.err()
    rig.add(rig.stream(pps=1, stats=1153))
    rc = rig.c._transmit("get_pgid_stats", params={"pgids": []})
    assert not rc and ("Asking information for all packet group IDs. There are currently 1153 active IDs . "
                       "Max allowed is 1152. Need to limit query size.") in rc.err()
    # an explicit list still works
    assert len(rig.stats([1, 1153])["flow_stats"]) == 2


def test_fake_accepts_2000_active_pgids(rig):
    rig.add(*[rig.stream(pps=1, stats=i) for i in range(1, 2001)])
    assert rig.active()["flow_stats"] == list(range(1, 2001))


def test_utilization_replays_the_tx_sample(rig):
    want = first_tx_util()
    # pins the choice: non-idle values, newest first
    assert want["cpu"][0]["history"][:4] == [4, 3, 1, 0]
    rc = rig.c._transmit("get_utilization")
    assert rc and rc.data() == want
    rig.add(rig.stream(stats=5))
    rig.c.start(ports=[0], mult="100pps")
    # the same answer in every state
    assert rig.c._transmit("get_utilization").data() == want
