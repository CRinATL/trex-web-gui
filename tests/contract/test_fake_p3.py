"""The stateful fake's stats follow its port state (P3-min Task 2). Shapes and the Tx-to-Rx port mapping come
from tests/fixtures/p3/; the values come from the fake's state. The recorded server's rates ramp over
seconds and the fake's do not (declared in the fake's docstring), so rates are asserted only against the
started pps."""
import json
import time

import pytest
from support import FIXTURES

SUMMARY = json.loads((FIXTURES / "p3" / "summary.json").read_text())


def client_with_stream(fake_trex, foreign_client, pps=100, **kw):
    """Start a fake; connect a client "gui-a" to it, acquire port 0 (forced) and add one continuous UDP
    stream at `pps` to it (`kw` goes to STLStream); returns (fake, client). The stream is added, not
    started."""
    f = fake_trex()
    # loads the vendored client; trex is importable from here on
    c = foreign_client(f, user="gui-a")
    from trex.stl.api import IP, UDP, Ether, STLPktBuilder, STLStream, STLTXCont
    c.acquire(ports=[0], force=True)
    c.add_streams(
        STLStream(
            packet=STLPktBuilder(pkt=Ether() / IP() / UDP() / ("x" * 18)), mode=STLTXCont(pps=pps), **kw
        ),
        ports=[0],
    )
    return f, c


def test_counters_advance_only_while_tx_at_the_started_rate(fake_trex, foreign_client):
    _f, c = client_with_stream(fake_trex, foreign_client)
    a1 = c.get_stats(ports=[0])[0]
    time.sleep(0.3)
    a2 = c.get_stats(ports=[0])[0]
    assert a1["opackets"] == a2["opackets"] == 0 and a2["tx_pps"] == 0          # idle: equal
    base = c.get_stats(ports=[0])[0]["opackets"]
    c.start(ports=[0], mult="10kpps")
    first = c.get_stats(ports=[0])[0]
    time.sleep(1.0)
    second = c.get_stats(ports=[0])[0]
    delta = second["opackets"] - first["opackets"]
    # within 10% of 10 000 per second
    assert 9_000 <= delta <= 11_000, delta
    assert second["opackets"] > base and second["tx_pps"] == pytest.approx(10_000)
    c.stop(ports=[0])
    frozen = c.get_stats(ports=[0])[0]
    time.sleep(0.3)
    after = c.get_stats(ports=[0])[0]
    assert after["opackets"] == frozen["opackets"] and after["obytes"] == frozen["obytes"]
    assert after["tx_pps"] == 0 and after["tx_bps"] == 0 and c.get_stats()["global"]["tx_pps"] == 0


def test_counters_freeze_on_pause_and_resume_from_there(fake_trex, foreign_client):
    _f, c = client_with_stream(fake_trex, foreign_client)
    c.start(ports=[0], mult="10kpps")
    time.sleep(0.2)
    c.pause(ports=[0])
    p1 = c.get_stats(ports=[0])[0]
    time.sleep(0.3)
    p2 = c.get_stats(ports=[0])[0]
    assert p1["opackets"] == p2["opackets"] > 0 and p2["tx_pps"] == 0
    c.resume(ports=[0])
    time.sleep(0.3)
    assert c.get_stats(ports=[0])[0]["opackets"] > p2["opackets"]


def test_rx_counters_follow_the_recorded_port_mapping(fake_trex, foreign_client):
    advancing = SUMMARY["rx_advancing_ports_when_port0_transmits"]
    _f, c = client_with_stream(fake_trex, foreign_client)
    before = c.get_stats()
    c.start(ports=[0], mult="10kpps")
    time.sleep(0.5)
    after = c.get_stats()
    assert [i for i in (0, 1) if after[i]["ipackets"] > before[i]["ipackets"]] == advancing
    # no loss; sampled a moment apart
    assert after[1]["ipackets"] == pytest.approx(after[0]["opackets"], abs=1500)
    assert after[1]["ibytes"] > 0 and after[0]["ibytes"] == 0
    assert after[1]["rx_pps"] == pytest.approx(10_000) and after[0]["rx_pps"] == 0
    g = after["global"]
    assert g["tx_pps"] == pytest.approx(10_000) and g["rx_pps"] == pytest.approx(10_000)


def test_stats_have_every_recorded_key(fake_trex, foreign_client):
    _f, c = client_with_stream(fake_trex, foreign_client)
    # idle, then under load
    for load in (False, True):
        if load:
            c.start(ports=[0], mult="10kpps")
        s = c.get_stats()
        assert set(s["global"]) == set(SUMMARY["global_keys"])
        assert set(s[0]) == set(SUMMARY["port_keys"]) == set(s[1])
        assert not [k for k, v in s["global"].items() if v == "N/A"]


def test_x_multiplier_uses_the_stream_rate(fake_trex, foreign_client):
    _f, c = client_with_stream(fake_trex, foreign_client, pps=200)
    c.start(ports=[0], mult="3")   # no unit = raw factor, the "x" of the brief
    assert c.get_stats(ports=[0])[0]["tx_pps"] == pytest.approx(600)
    c.update(ports=[0], mult="2mpps")
    assert c.get_stats(ports=[0])[0]["tx_pps"] == pytest.approx(2_000_000)
    c.update(ports=[0], mult="5kpps")
    assert c.get_stats(ports=[0])[0]["tx_pps"] == pytest.approx(5_000)


def test_a_multiplier_the_fake_does_not_model_fails_loudly_when_stats_are_asked(fake_trex, foreign_client):
    f, c = client_with_stream(fake_trex, foreign_client)
    c.start(ports=[0], mult="1kbps")                                # start is accepted exactly as before
    c.update(ports=[0], mult="50%")                                 # so is every unit on update
    from trex.common.trex_exceptions import TRexError
    # the stats request kills the fake; the client times out
    with pytest.raises(TRexError):
        c.get_stats(ports=[0])
    out = f.proc.communicate(timeout=5)[0]                          # the traceback, read after the death
    assert f.proc.returncode != 0
    assert "multiplier type 'percentage' is not modelled" in out


def test_named_profiles_count_by_their_own_state(fake_trex, foreign_client):
    """Aggregate and per-profile state differ here: with `_` paused the port is still TX through `bench`."""
    from trex.stl.api import IP, UDP, Ether, STLPktBuilder, STLStream, STLTXCont
    _f, c = client_with_stream(fake_trex, foreign_client)                       # `_` holds a 100 pps stream
    c.add_streams(STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()/("x" * 18)), mode=STLTXCont(pps=100)),
                  ports=["0.bench"])
    c.start(ports=["0"], mult="10kpps")
    c.start(ports=["0.bench"], mult="5kpps")
    time.sleep(0.1)
    assert c.get_stats(ports=[0])[0]["tx_pps"] == pytest.approx(15_000)
    c.pause(ports=["0"])
    # only the named profile transmits
    assert c.get_stats(ports=[0])[0]["tx_pps"] == pytest.approx(5_000)
    c.resume(ports=["0"])
    c.pause(ports=["0.bench"])
    assert c.get_stats(ports=[0])[0]["tx_pps"] == pytest.approx(10_000)
    c.resume(ports=["0.bench"])
    c.stop(ports=["0", "0.bench"])
    frozen = c.get_stats(ports=[0])[0]
    time.sleep(0.3)
    after = c.get_stats(ports=[0])[0]
    assert frozen["opackets"] > 0 and after["opackets"] == frozen["opackets"] and after["tx_pps"] == 0
