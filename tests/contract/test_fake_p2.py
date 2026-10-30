"""The stateful fake against the vendored client, two sessions at once (P2 Task 2). Every negative
asserts the recorded cause text from tests/fixtures/p2/negatives.json."""
import json
import time

import pytest
from support import FIXTURES
from trex_web_gui.trexapi import loader

NEG = json.loads((FIXTURES / "p2" / "negatives.json").read_text())


def recorded_text(key: str) -> str:
    """The error text of the recorded negative `key` in NEG: its specific_err when set, else its message."""
    e = NEG[key]["resp"]["error"]
    return e.get("specific_err") or e["message"]


def wait(pred, timeout=5.0):
    """Poll `pred` every 0.05 s, sleeping the calling thread; asserts if it is still false after `timeout`
    seconds."""
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "condition not met in time"
        time.sleep(0.05)


def test_foreign_acquire_is_seen_as_event_and_owner(fake_trex, foreign_client):
    f = fake_trex()
    loader.load()
    from trex.stl.api import STLClient
    a = STLClient(username="gui-a", server=f.host, sync_port=f.rpc_port, async_port=f.pub_port,
                  verbose_level="none", sync_timeout=2, async_timeout=2)
    a.connect()
    try:
        b = foreign_client(f)
        b.acquire(ports=[0])
        wait(lambda: a.ports[0].get_owner() == "console-b")
        assert any("taken by 'console-b'" in str(ev.msg) for ev in a.get_events())
    finally:
        a.disconnect()


def test_acquire_owned_without_force_gives_recorded_error(fake_trex, foreign_client):
    f = fake_trex()
    b = foreign_client(f)
    b.acquire(ports=[0])
    a = foreign_client(f, user="gui-a")
    from trex.common.trex_exceptions import TRexError
    with pytest.raises(TRexError) as ei:
        a.acquire(ports=[0])
    first_line = recorded_text("acquire_owned_no_force").splitlines()[0]
    assert first_line.split("'")[0] in str(ei.value)      # owner name is rewritten per request


def test_traffic_cycle_and_events(fake_trex, foreign_client):
    f = fake_trex()
    a = foreign_client(f, user="gui-a")
    b = foreign_client(f)
    from trex.stl.api import IP, UDP, Ether, STLPktBuilder, STLStream, STLTXCont
    a.acquire(ports=[0], force=True)
    a.add_streams(STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()), mode=STLTXCont(pps=10)), ports=[0])
    a.start(ports=[0])
    wait(lambda: b.ports[0].is_active())
    a.pause(ports=[0])
    wait(lambda: b.ports[0].is_paused())
    a.resume(ports=[0])
    a.stop(ports=[0])
    wait(lambda: not b.ports[0].is_active())


def test_restart_invalidates_api_h(fake_trex, foreign_client):
    f = fake_trex()
    a = foreign_client(f, user="gui-a")
    f.restart()
    rc = a.ports[0].sync()
    assert rc.bad() and recorded_text("api_h_mismatch").splitlines()[0] in rc.err()


def test_force_acquire_syncs_loaded_streams(fake_trex, foreign_client):
    """Every acquire runs sync_streams -> get_all_streams; v3.08 clients read the dynamic-profile shape."""
    recorded = json.loads((FIXTURES / "p2" / "summary.json").read_text())["get_all_streams_example"]
    assert set(recorded["result"]) == {"profiles"}          # the fake below must answer in this shape
    f = fake_trex()
    a = foreign_client(f, user="gui-a")
    from trex.stl.api import IP, UDP, Ether, STLPktBuilder, STLStream, STLTXCont
    a.acquire(ports=[0])
    a.add_streams([STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()), mode=STLTXCont(pps=10))
                   for _ in range(2)], ports=[0])
    b = foreign_client(f)
    b.acquire(ports=[0], force=True)
    assert len(b.ports[0].streams) == 2


def test_set_port_attr_lands_under_status_keys(fake_trex, foreign_client):
    f = fake_trex()
    a = foreign_client(f, user="gui-a")
    b = foreign_client(f)
    a.acquire(ports=[0])
    a.set_port_attr(ports=[0], flow_ctrl=1, promiscuous=True)   # flow_ctrl reaches the wire via patch 08
    assert a.ports[0].get_ts_attr()["fc"]["mode"] == 1          # set_attr re-syncs from get_port_status
    b.ports[0].sync()
    attr = b.ports[0].get_ts_attr()
    assert attr["fc"]["mode"] == 1 and attr["promiscuous"]["enabled"] is True
    assert "flow_ctrl_mode" not in attr


def test_acquire_refusal_names_the_current_owner(fake_trex, foreign_client):
    f = fake_trex()
    a = foreign_client(f, user="gui-a")
    a.acquire(ports=[0])
    b = foreign_client(f)
    from trex.common.trex_exceptions import TRexError
    with pytest.raises(TRexError) as ei:
        b.acquire(ports=[0])
    assert "gui-a" in str(ei.value) and "console-b" not in str(ei.value)


def test_one_release_gives_each_reader_exactly_one_release_event(fake_trex, foreign_client):
    """emits.json says one type-6 event per release RPC (the recorder once released two ports under one
    mark)."""
    f = fake_trex()
    a = foreign_client(f, user="gui-a")
    b = foreign_client(f)
    a.acquire(ports=[0])
    wait(lambda: b.ports[0].get_owner() == "gui-a")
    b.clear_events()
    a.release(ports=[0])
    wait(lambda: b.ports[0].get_owner() == "")
    time.sleep(0.5)
    assert sum("released" in str(ev.msg) for ev in b.get_events()) == 1


def test_empty_port_reports_the_default_profile_idle(fake_trex, foreign_client):
    """Every recorded v3.08 get_port_status carries state_profile {"_": ...} and profile_count 1."""
    c = foreign_client(fake_trex())
    status = c.ports[0].transmit("get_port_status", {"port_id": 0, "profile_id": "*", "block": False}).data()
    assert status["state_profile"] == {"_": "IDLE"}
    assert status["profile_count"] == 1


def test_remove_rx_filters_is_answered_for_every_port(fake_trex, foreign_client):
    c = foreign_client(fake_trex())
    for pid in (0, 1):
        c.acquire(ports=[pid])
        rc = c.ports[pid].transmit("remove_rx_filters", {"port_id": pid, "handler": c.ports[pid].handler})
        assert rc.good(), rc.err()


def _bench_in_tx(fake_trex, foreign_client):
    """Starts a fake and returns (fake, foreign client), the client owning port 0 with the benchmark
    streams of profile `bench` transmitting."""
    from trex.stl.api import STLProfile
    f = fake_trex()
    c = foreign_client(f)
    c.acquire(ports=[0])
    prof = STLProfile.load_py(str(FIXTURES / "profiles" / "udp_for_benchmarks.py"))
    c.add_streams(prof.get_streams(), ports=["0.bench"])
    c.start(ports=["0.bench"])
    return f, c


def _status(c):
    """Port 0's status for every profile, read straight from the vendored client `c` (a non-blocking
    get_port_status RPC)."""
    return c.ports[0].transmit("get_port_status", {"port_id": 0, "profile_id": "*", "block": False}).data()


def test_named_profile_runs_beside_the_default(fake_trex, foreign_client):
    _, c = _bench_in_tx(fake_trex, foreign_client)
    status = _status(c)
    assert status["state_profile"] == {"_": "IDLE", "bench": "TX"}
    assert status["state"] == "TX"
    assert status["profile_count"] == 2


def test_stop_of_the_default_profile_leaves_the_named_one_running(fake_trex, foreign_client):
    """TrexStatelessProfile::stop_traffic returns at once when the profile is not active
    (trex_stl_port.cpp:377-381)
    and the RPC still answers {} (trex_stl_rpc_cmds.cpp:1213-1228): a no-op, not an error."""
    _, c = _bench_in_tx(fake_trex, foreign_client)
    rc = c.ports[0].transmit("stop_traffic", {"port_id": 0, "handler": c.ports[0].handler, "profile_id": "_"})
    assert rc.good(), rc.err()
    assert _status(c)["state_profile"] == {"_": "IDLE", "bench": "TX"}


def test_stop_of_the_named_profile_leaves_streams(fake_trex, foreign_client):
    _, c = _bench_in_tx(fake_trex, foreign_client)
    c.stop(ports=["0.bench"])
    status = _status(c)
    assert status["state_profile"] == {"_": "IDLE", "bench": "STREAMS"}
    assert status["state"] == "STREAMS"


def test_stop_of_an_inactive_profile_emits_no_event(fake_trex, foreign_client):
    f, c = _bench_in_tx(fake_trex, foreign_client)
    reader = foreign_client(f, user="reader")
    reader.ports[0].sync()
    reader.clear_events()
    rc = c.ports[0].transmit("stop_traffic", {"port_id": 0, "handler": c.ports[0].handler, "profile_id": "_"})
    assert rc.good(), rc.err()
    reader.ports[0].sync()
    time.sleep(0.5)
    assert reader.get_events() == []


def test_assign_of_the_default_profile_is_not_refused_while_bench_transmits(fake_trex, foreign_client):
    """remove_all_streams checks the target profile's state (trex_stl_port.cpp:900-903), not the port's."""
    _, c = _bench_in_tx(fake_trex, foreign_client)
    rc = c.ports[0].transmit(
        "remove_all_streams", {"port_id": 0, "handler": c.ports[0].handler, "profile_id": "_"}
    )
    assert rc.good(), rc.err()
    rc = c.ports[0].transmit(
        "remove_all_streams", {"port_id": 0, "handler": c.ports[0].handler, "profile_id": "bench"}
    )
    assert not rc.good()
    # trex_stl_port.cpp:719 names the target profile
    assert "current state: 'TX on profile_id(bench)" in rc.err()
    c.stop(ports=["0.bench"])
    rc = c.ports[0].transmit(
        "remove_all_streams", {"port_id": 0, "handler": c.ports[0].handler, "profile_id": "bench"}
    )
    assert rc.good(), rc.err()
    status = _status(c)       # the server keeps the profile, IDLE (trex_stl_port.cpp:895-897)
    assert status["state_profile"] == {"_": "IDLE", "bench": "IDLE"}
    assert status["profile_count"] == 2
