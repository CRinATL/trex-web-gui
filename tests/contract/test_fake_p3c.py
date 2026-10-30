"""The stateful fake models the capture RPC family (P3c Task 2). Texts, key sets and shapes come from
tests/fixtures/p3c/ (recorded from a v3.08 guest, net_af_packet); the tests read the fake's wire answers raw
through the client's transport (`_transmit`), as the server's own handlers see them. Packets are synthesised
from the transmitting profile (see the fake's header): a Tx capture of stream traffic needs the Tx port's
service mode on, as the af_packet guest measured."""
import json

import pytest
from support import FIXTURES

SUMMARY = json.loads((FIXTURES / "p3c" / "summary.json").read_text())
ROW_KEYS = set(SUMMARY["status_row_keys"])
PKT_KEYS = set(SUMMARY["fetch_packet_keys"])
ERR = SUMMARY["errors"]


class Rig:
    def __init__(self, fake_trex, foreign_client, *flags):
        """Start a fake on a manual clock (plus `flags`), connect a client "gui-a" to it and acquire ports
        0 and 1 (forced); `self.make` is the foreign_client factory and `self.api` the vendored
        `trex.stl.api`."""
        self.fake = fake_trex("--manual-clock", *flags)
        self.make = foreign_client
        self.c = foreign_client(self.fake, user="gui-a")
        from trex.stl import api
        self.api = api
        self.c.acquire(ports=[0, 1], force=True)

    def cap(self, command, **params):
        """The unchecked response of a raw `capture` RPC with `command` and `params`."""
        return self.c._transmit("capture", params={"command": command, **params})

    def start(self, mode, limit, tx=(), rx=(), bpf=""):
        """Start a capture over the `tx` and `rx` ports with filter `bpf`; returns its capture_id and
        asserts the call succeeded."""
        rc = self.cap("start", mode=mode, limit=limit, tx=list(tx), rx=list(rx), filter=bpf)
        assert rc, rc.err()
        return rc.data()["capture_id"]

    def rows(self):
        """The status rows of every capture the fake holds, keyed by capture id; asserts the call
        succeeded."""
        rc = self.cap("status")
        assert rc, rc.err()
        return {r["id"]: r for r in rc.data()}

    def service(self, port, on=True):
        """Turn service mode on (or off) for `port` through a raw `service` RPC (unfiltered); asserts the
        call succeeded."""
        rc = self.c._transmit(
            "service",
            params={"handler": self.c.ports[port].handler, "port_id": port, "enabled": on, "filtered": False},
        )
        assert rc, rc.err()

    def transmit(self, port=0, pps=100, seconds=1):
        """Add one continuous UDP stream at `pps` to `port`, start the port, then advance the fake's
        manual clock by `seconds`."""
        t = self.api
        self.c.add_streams(
            [
                t.STLStream(
                    packet=t.STLPktBuilder(pkt=t.Ether() / t.IP() / t.UDP() / ("x" * 18)),
                    mode=t.STLTXCont(pps=pps),
                )
            ],
            ports=[port],
        )
        self.c.start(ports=[port], mult=f"{pps}pps")
        self.advance(seconds)

    def advance(self, seconds):
        """Advance the fake's manual clock by `seconds`; asserts the fake accepts it."""
        assert self.c._transmit("fake_advance_clock", params={"seconds": seconds})


@pytest.fixture
def rig(fake_trex, foreign_client):
    """Return a Rig on a default manual-clock fake. It tears nothing down itself; the foreign_client
    fixture disconnects the clients and fake_trex then stops the fake."""
    return Rig(fake_trex, foreign_client)


def test_capture_start_status_shapes_match_recording(rig):
    cid = rig.start("cyclic", 100, rx=[1])
    # the P3b-recorded status row ([]) must not leak: the fake answers from its own state
    rows = rig.rows()
    assert list(rows) == [cid]
    row = rows[cid]
    assert set(row) == ROW_KEYS and set(row["filter"]) == set(SUMMARY["filter_keys"])
    assert (row["state"], row["count"], row["fetched"], row["matched"], row["bytes"]) == (
        "ACTIVE", 0, 0, 0, 0)
    assert (row["filter"]["rx"], row["filter"]["tx"], row["mode"], row["limit"]) == (2, 0, "cyclic", 100)


def test_fixed_capture_fills_to_limit_and_drops_newest(rig):
    # af_packet: a Tx capture records only with the Tx port in service mode
    rig.service(0)
    cid = rig.start("fixed", 50, tx=[0])
    rig.transmit(0, 100, 1)
    row = rig.rows()[cid]
    assert (row["count"], row["matched"], row["bytes"]) == (50, 100, 50 * 60)
    pkts = rig.cap("fetch", capture_id=cid, pkt_limit=100, snaplen=0).data()["pkts"]
    assert [p["index"] for p in pkts] == list(range(1, 51))          # the newest were dropped
    assert pkts[0]["origin"] == "TX" and pkts[0]["port"] == 0


def test_tx_capture_records_nothing_without_service_mode(rig):
    cid = rig.start("fixed", 50, tx=[0])
    rig.transmit(0, 100, 1)
    row = rig.rows()[cid]
    assert (row["count"], row["matched"]) == (
        SUMMARY["tx_capture_service_off"]["count"],
        SUMMARY["tx_capture_service_off"]["matched"],
    )
    rig.service(0)
    rig.advance(1)
    assert rig.rows()[cid]["matched"] == 100                # the same traffic, once service mode is on


def test_fetch_with_pkt_limit_zero_takes_nothing_and_names_the_capture(rig):
    peek = SUMMARY["fetch_peek"]
    cid = rig.start("fixed", 50, rx=[1])
    rig.transmit(0, 100, 1)
    n = rig.rows()[cid]["count"]
    d = rig.cap("fetch", capture_id=cid, pkt_limit=0, snaplen=0).data()
    assert sorted(d) == peek["reply_keys"] and d["pkts"] == [] and d["pending"] == n
    assert rig.rows()[cid]["fetched"] == 0
    d2 = rig.cap("fetch", capture_id=cid, pkt_limit=50, snaplen=0).data()
    assert d2["start_ts"] == d["start_ts"] and len(d2["pkts"]) == n


def test_cyclic_capture_drops_oldest_and_counts_matched(rig):
    cid = rig.start("cyclic", 20, rx=[1])                   # an Rx capture fills with service mode off
    rig.transmit(0, 100, 1)
    row = rig.rows()[cid]
    assert (row["count"], row["matched"]) == (20, 100)
    pkts = rig.cap("fetch", capture_id=cid, pkt_limit=50, snaplen=0).data()["pkts"]
    assert pkts[0]["index"] == 81 and pkts[-1]["index"] == 100


def test_fetch_drains_and_reports_pending(rig):
    cid = rig.start("cyclic", 100, rx=[1])
    rig.transmit(0, 100, 1)
    d = rig.cap("fetch", capture_id=cid, pkt_limit=50, snaplen=0).data()
    assert set(d) == set(SUMMARY["monitor"]["fetch_reply_keys"])
    assert len(d["pkts"]) == 50 and d["pending"] == 50
    assert all(
        set(p) == PKT_KEYS and p["origin"] == "RX" and p["port"] == 1 and p["wirelen"] == 60
        for p in d["pkts"]
    )
    assert d["start_ts"] == 0.0
    row = rig.rows()[cid]
    assert (row["fetched"], row["count"], row["matched"]) == (50, 50, 100)
    d2 = rig.cap("fetch", capture_id=cid, pkt_limit=50, snaplen=0).data()
    assert len(d2["pkts"]) == 50 and d2["pending"] == 0
    assert [p["index"] for p in d["pkts"] + d2["pkts"]] == list(range(1, 101))
    assert rig.cap("fetch", capture_id=cid, pkt_limit=50, snaplen=0).data()["pkts"] == []
    assert rig.rows()[cid]["fetched"] == 100


def test_stop_pkt_count_then_remove(rig):
    cid = rig.start("cyclic", 100, rx=[1])
    rig.transmit(0, 100, 1)
    stop = rig.cap("stop", capture_id=cid)
    assert stop and stop.data() == {"endpoint": "", "pkt_count": 100}
    assert rig.rows()[cid]["state"] == "STOPPED"
    rig.advance(1)
    assert rig.rows()[cid]["matched"] == 100                # a stopped capture records no more
    rm = rig.cap("remove", capture_id=cid)
    assert rm and rm.data() == SUMMARY["remove_active_reply"]["data"] == {}
    assert cid not in rig.rows()


def test_invalid_bpf_returns_recorded_text(rig):
    rc = rig.cap("start", mode="fixed", limit=100, tx=[], rx=[1], filter="not a filter((")
    assert not rc and ERR["bad_bpf"] in rc.err()
    assert rig.rows() == {}
    # the fake evaluates no BPF
    assert rig.start("fixed", 100, rx=[1], bpf="udp and port 53 or (whatever") > 0


def test_eleventh_capture_returns_limit_text(rig):
    for _ in range(10):
        rig.start("fixed", 10, tx=[0])
    rc = rig.cap("start", mode="fixed", limit=10, tx=[0], rx=[], filter="")
    assert not rc and ERR["limit"] in rc.err()
    assert len(rig.rows()) == 10
    rig.cap("stop", capture_id=1)
    rc = rig.cap("start", mode="fixed", limit=10, tx=[0], rx=[], filter="")
    assert not rc and ERR["limit"] in rc.err()                         # a stopped capture still counts
    assert rig.cap("remove", capture_id=1)
    assert rig.start("fixed", 10, tx=[0]) == 11                        # ids are not reused


def test_unknown_capture_id_texts(rig):
    for cmd, extra in (("fetch", {"pkt_limit": 50, "snaplen": 0}), ("stop", {}), ("remove", {})):
        rc = rig.cap(cmd, capture_id=9999, **extra)
        assert not rc and ERR[f"{cmd}_9999"] in rc.err() and "capture ID not found" in rc.err()


def test_remove_active_is_allowed(rig):
    cid = rig.start("cyclic", 100, rx=[1])
    rc = rig.cap("remove", capture_id=cid)
    assert rc and rc.data() == SUMMARY["remove_active_reply"]["data"]
    assert rig.rows() == {}


def test_capture_survives_client_disconnect(rig):
    cid = rig.start("cyclic", 100, rx=[1])
    rig.c.disconnect(stop_traffic=False, release_ports=False)
    c2 = rig.make(rig.fake, user="console-b")
    rc = c2._transmit("capture", params={"command": "status"})
    assert rc and [r["id"] for r in rc.data()] == [cid] and rc.data()[0]["state"] == "ACTIVE"


def test_reset_removes_every_capture(rig):
    a, b = rig.start("cyclic", 100, rx=[1]), rig.start("fixed", 10, tx=[0])
    rig.cap("stop", capture_id=b)
    assert set(rig.rows()) == {a, b}
    # the vendored client's remove_all_captures: status, then remove per id
    rig.c.reset(ports=[0])
    assert rig.rows() == {}


def _fake_refuses(rig, command, **params):
    """Send capture `command` raw, assert the fake never answers it, wait for the fake to exit non-zero and
    return its output. The fake dies with its HarnessError message on purpose instead of answering (a made-up
    success would hide a typo)."""
    assert not rig.cap(command, **params)       # the transport gives up on a server that never answers
    assert rig.fake.proc.wait(timeout=30) != 0
    return rig.fake.proc.communicate()[0]


@pytest.mark.parametrize("params", [{"command": "bogus"}, {}])
def test_unknown_or_missing_capture_command_is_never_answered(fake_trex, foreign_client, params):
    rig = Rig(fake_trex, foreign_client)
    assert not rig.c._transmit("capture", params=params)
    assert rig.fake.proc.wait(timeout=30) != 0
    assert "HARNESS ERROR: capture command" in rig.fake.proc.communicate()[0]


@pytest.mark.parametrize("command, params, missing", [
    ("fetch", {}, "pkt_limit"),
    ("start", {"limit": 10, "tx": [0], "rx": [], "filter": ""}, "mode"),
    ("start", {"mode": "fixed", "tx": [0], "rx": [], "filter": ""}, "limit"),
])
def test_missing_required_capture_parameter_is_refused(fake_trex, foreign_client, command, params, missing):
    rig = Rig(fake_trex, foreign_client)
    cid = rig.start("fixed", 10, tx=[0])
    if command == "fetch":
        params = {"capture_id": cid}
    out = _fake_refuses(rig, command, **params)
    assert f"capture {command} without {missing}" in out


def test_restart_clears_captures_and_restarts_ids(rig):
    rig.start("cyclic", 100, rx=[1])
    rig.start("fixed", 10, tx=[0])
    rig.fake.restart()                                       # SIGUSR2
    c2 = rig.make(rig.fake, user="console-b")
    rc = c2._transmit("capture", params={"command": "status"})
    assert rc and rc.data() == []
    rc = c2._transmit(
        "capture",
        params={"command": "start", "mode": "fixed", "limit": 10, "tx": [0], "rx": [], "filter": ""},
    )
    assert rc and rc.data()["capture_id"] == 1
