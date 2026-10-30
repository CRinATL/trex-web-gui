"""TrexSession control surface against the stateful fake (P2 Tasks 5-6)."""
import asyncio
import json
import logging
import os
import signal

import pytest
from helpers import Recorder, wait_for
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams
from trex_web_gui.trexapi import CommandRefused


def params(f, **kw):
    """ConnectParams for fake `f` with a 2.0 s timeout; `kw` passes through to ConnectParams."""
    return ConnectParams(f.host, f.rpc_port, f.pub_port, 2.0, **kw)


def last_ports(rec):
    """The `ports` list of the newest "ports" message `rec` recorded; StopIteration when there is none."""
    return next(m["ports"] for m in reversed(rec.msgs) if m["type"] == "ports")


def test_connect_publishes_ports(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        ports = last_ports(rec)
        assert [p["id"] for p in ports] == [0, 1]
        assert {"state", "owner", "owned", "service", "info", "profiles"} <= set(ports[0])
    new_session.run(run())


def test_acquire_release_and_log(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        assert last_ports(rec)[0]["owned"] is True
        await s.release([0])
        assert last_ports(rec)[0]["owned"] is False
        assert [m["level"] for m in rec.msgs if m["type"] == "log"][-2:] == ["info", "info"]
    new_session.run(run())


def test_foreign_force_acquire_shows_owner_and_refuses(fake_trex, foreign_client, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        b = foreign_client(f)
        await asyncio.to_thread(b.acquire, ports=[0], force=True)
        # one event or one poll
        await wait_for(lambda: last_ports(rec)[0]["owner"] == "console-b", 2.0 + 2.5)
        assert last_ports(rec)[0]["owned"] is False
        with pytest.raises(CommandRefused) as ei:
            await s.release([0])
        assert ei.value.code == "not_owned" and "console-b" in ei.value.message
    new_session.run(run())


def test_reacquire_takes_back_ports_owned_by_my_username(fake_trex, foreign_client, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        import getpass
        me = foreign_client(f, user=getpass.getuser())       # "another session of you" holds port 1
        await asyncio.to_thread(me.acquire, ports=[1])
        await wait_for(lambda: last_ports(rec)[1]["owner"] == getpass.getuser(), 4.5)
        assert await s.reacquire() == [1]
        assert last_ports(rec)[1]["owned"] is True
    new_session.run(run())


def test_read_only_refuses_mutations(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f, read_only=True))
        with pytest.raises(CommandRefused) as ei:
            await s.acquire([0])
        assert ei.value.code == "read_only"
        assert s.snapshot()["params"]["read_only"] is True
    new_session.run(run())


def test_server_restart_marks_lost(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        f.restart()
        # subscriber poll + RPC tap (or the safety poll, if its tick is first) + margin
        await wait_for(lambda: rec.states()[-1] == "lost", 2.0 + 3.0)
        assert "restarted" in s.snapshot()["error"]
    new_session.run(run())


def test_reconnect_to_the_same_server_rebroadcasts_ports(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        # no acquire: ownership would change the ports and publish anyway
        await s.connect(params(f))
        f.restart()                                      # SIGUSR2: new api_h, same process and ports
        await wait_for(lambda: rec.states()[-1] == "lost", 2.0 + 3.0)
        await s.connect(params(f))
        last_connecting = max(
            i for i, m in enumerate(rec.msgs) if m["type"] == "session" and m["state"] == "connecting"
        )
        assert any(m["type"] == "ports" for m in rec.msgs[last_connecting:])
    new_session.run(run())


def test_rpc_console_toggle(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await asyncio.sleep(0.5)
        assert not any(m["type"] == "rpc" for m in rec.msgs)
        assert s.snapshot()["rpc_console"] is False
        s.set_rpc_console(True, include_polling=True)   # only polls arrive unprompted
        assert s.snapshot()["rpc_console"] is True
        # published, not polled
        assert [m for m in rec.msgs if m["type"] == "session"][-1]["rpc_console"] is True
        await wait_for(lambda: any(m["type"] == "rpc" for m in rec.msgs), 3.0)
    new_session.run(run())


def test_disconnect_releases_by_default_and_keeps_when_told(fake_trex, foreign_client, new_session):
    f = fake_trex()
    watcher = foreign_client(f)
    async def run():
        s = new_session(Recorder())
        await s.connect(params(f))
        await s.acquire([0])
        await s.disconnect()
        await asyncio.sleep(0.3)
        watcher.ports[0].sync()
        assert watcher.ports[0].get_owner() == ""
        keep = new_session(Recorder())
        keep._release_on_exit = lambda: False
        await keep.connect(params(f))
        await keep.acquire([0])
        await keep.close()
        watcher.ports[0].sync()
        assert watcher.ports[0].get_owner() != ""
    new_session.run(run())


def test_close_releases_by_default(fake_trex, foreign_client, new_session):
    """The exit path: close() (fail-fast clients, then disconnect) releases the ports when release_on_exit."""
    f = fake_trex()
    watcher = foreign_client(f)
    async def run():
        s = new_session(Recorder())
        await s.connect(params(f))
        await s.acquire([0])
        await s.close()
        await asyncio.sleep(0.3)
        watcher.ports[0].sync()
        assert watcher.ports[0].get_owner() == ""
    new_session.run(run())


def test_close_logs_the_release_and_a_failed_one(fake_trex, new_session, caplog):
    """RC1-25: the exit release says what happened; the client's own swallowed a failure silently."""
    f = fake_trex()
    async def run():
        ok = new_session(Recorder())
        await ok.connect(params(f))
        await ok.acquire([0])
        with caplog.at_level(logging.INFO, logger="trex_web_gui"):
            await ok.close()
        assert "Released port(s) [0]" in caplog.text
        caplog.clear()
        bad = new_session(Recorder())
        await bad.connect(params(f))
        await bad.acquire([1])
        def refuse(ports):
            raise RuntimeError("server said no")
        bad._client.release = refuse
        with caplog.at_level(logging.INFO, logger="trex_web_gui"):
            await bad.close()
        warn = [r for r in caplog.records if r.levelno == logging.WARNING]
        text = "Releasing port(s) [1] failed: server said no; they stay owned by"
        assert any(text in r.getMessage() for r in warn)
    new_session.run(run())


def test_a_port_left_by_an_earlier_session_of_mine_is_named_and_reacquired(fake_trex, new_session):
    """RC1-25, seen on the rc1 host. A session exits holding port 0 under this user's name; the next session
    sees it as mine_elsewhere, is refused with the way back (not "force-acquire"), and Re-acquire my ports
    takes it."""
    f = fake_trex()
    async def run():
        old = new_session(Recorder())
        old._release_on_exit = lambda: False
        await old.connect(params(f))
        await old.acquire([0])
        await old.close()
        s = new_session(Recorder())
        await s.connect(params(f))
        await wait_for(lambda: s.ports()[0]["mine_elsewhere"], 3.0)
        assert s.ports()[1]["mine_elsewhere"] is False
        for attempt in (lambda: s.acquire([0]), lambda: s.set_service([0], True)):
            with pytest.raises(trexapi.CommandRefused) as e:
                await attempt()
            assert "is held by another session of yours" in str(e.value)
            assert "Re-acquire my ports takes it back" in str(e.value)
        assert await s.reacquire() == [0]
        assert s.ports()[0]["owned"] is True and s.ports()[0]["mine_elsewhere"] is False
        await s.close()
    new_session.run(run())


PROFILE = str(FIXTURES / "profiles" / "udp_for_benchmarks.py")


async def loaded(ports):
    """The udp_for_benchmarks profile (packet_len 128) loaded for `ports` by trexapi.load_profile."""
    return await trexapi.load_profile(PROFILE, ports, ["--packet_len", "128"])


def test_assign_start_update_pause_resume_stop(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        assert last_ports(rec)[0]["state"] == "STREAMS"
        await s.start([0], "10kpps")
        assert last_ports(rec)[0]["state"] == "TX"
        await s.update([0], "20kpps")
        await s.pause([0])
        assert last_ports(rec)[0]["state"] == "PAUSE"
        await s.resume([0])
        assert last_ports(rec)[0]["state"] == "TX"
        await s.stop([0])
        assert last_ports(rec)[0]["state"] == "STREAMS"
    new_session.run(run())


def test_assigned_records_the_last_assign_until_release(fake_trex, new_session):
    """RC1-30: assigned() reports this connection's last Assign to a port while the port holds streams and is
    owned; Release forgets it, and so does a new connection."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        assert await s.assigned(0) == {"streams": False, "assigned": None}
        await s.assign_profile([0], await loaded([0]), tunables=["--packet_len", "128"])
        r = await s.assigned(0)
        assert r["streams"] is True and r["assigned"] is not None
        assert r["assigned"]["name"] == "udp_for_benchmarks.py"
        assert r["assigned"]["tunables"] == ["--packet_len", "128"] and r["assigned"]["stream_count"] == 1
        await s.release([0])
        assert (await s.assigned(0))["assigned"] is None
        with pytest.raises(CommandRefused):
            await s.assigned(9)
    new_session.run(run())


def test_start_refused_for_dest_mac_says_why_and_what_to_do(fake_trex, new_session):
    """RC1-30: the server's dest-MAC refusal (trex_stl_port.cpp:275-277; the fake does not model it, so
    the client's start raises its text here) comes back naming the port's state and the stream that takes
    the port's MAC: the benchmark profile's stream has the default destination, so its mode is not "from
    packet"."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        def refuse(**_kw):
            raise Exception("start_traffic: Port 0 dest MAC is invalid and there are streams without "
                            "explicit dest MAC.")
        s._client.start = refuse
        with pytest.raises(trexapi.TrexApiError) as ei:
            await s.start([0], "1kpps")
        assert ei.value.message.startswith("Port 0 cannot start: ")
        assert "stream 0 takes the destination MAC from the port" in ei.value.message
        assert "Studio > Properties" in ei.value.message
    new_session.run(run())


def test_assign_to_tx_port_refused_without_stop_first(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        lp = await loaded([0])
        await s.assign_profile([0], lp)
        await s.start([0], "1kpps")
        with pytest.raises(CommandRefused) as ei:
            await s.assign_profile([0], lp)
        assert ei.value.code == "port_active"
        assert "stop-and-replace" in ei.value.message
        await s.assign_profile([0], lp, stop_first=True)
        assert last_ports(rec)[0]["state"] == "STREAMS"
    new_session.run(run())


def test_assign_partial_failure_says_port_is_empty(fake_trex, new_session, monkeypatch):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        lp = await loaded([0])
        def boom(_streams):
            raise RuntimeError("stream rebuild failed")
        monkeypatch.setattr(trexapi, "streams_from_json", boom)
        with pytest.raises(trexapi.TrexApiError) as ei:
            await s.assign_profile([0], lp)
        assert "now has no streams" in ei.value.message and "stream rebuild failed" in ei.value.message
    new_session.run(run())


class _BadRc:
    """Stands in for trex RC_ERR (tests cannot import trex): the two methods assign_profile calls."""
    def __init__(self, msg):
        """Keep `msg` as the text err() returns."""
        self._msg = msg
    def bad(self):
        """Always True: the result is an error."""
        return True
    def err(self):
        """The message given at construction."""
        return self._msg


def test_validate_failure_leaves_no_streams(fake_trex, new_session, monkeypatch):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        lp = await loaded([0])
        monkeypatch.setattr(type(s._client.ports[0]), "validate", lambda self: _BadRc("boom"))
        with pytest.raises(trexapi.TrexApiError) as ei:
            await s.assign_profile([0], lp)
        assert "boom" in ei.value.message and "now has no streams" in ei.value.message
        assert last_ports(rec)[0]["state"] == "IDLE"
    new_session.run(run())


def test_assign_partial_failure_names_every_emptied_port(fake_trex, new_session, monkeypatch):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0, 1])
        lp = await loaded([0, 1])
        def boom(_streams):
            raise RuntimeError("stream rebuild failed")
        monkeypatch.setattr(trexapi, "streams_from_json", boom)
        with pytest.raises(trexapi.TrexApiError) as ei:
            await s.assign_profile([0, 1], lp)
        assert "Ports [0, 1] now have no streams" in ei.value.message
    new_session.run(run())


def test_start_on_unowned_port_is_refused(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        with pytest.raises(CommandRefused) as ei:
            await s.start([0], "1kpps")
        assert ei.value.code == "not_owned" and "acquire it first" in ei.value.message
    new_session.run(run())


def test_service_and_attrs_and_reset(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        s._poll_s = 3600                     # no safety poll; the command's own refresh must show the change
        await s.connect(params(f))
        await s.acquire([0])
        await s.set_service([0], True)
        assert last_ports(rec)[0]["service"] is True
        await s.set_port_attr([0], promiscuous=True)
        assert last_ports(rec)[0]["promiscuous"] is True
        await s.reset([0])
        port = last_ports(rec)[0]
        assert port["owned"] is True and port["state"] == "IDLE"
        assert port["service"] is False and port["promiscuous"] is False
    new_session.run(run())


def test_service_filtered_with_mask_then_off(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        s._poll_s = 3600
        await s.connect(params(f))
        await s.acquire([0])
        # filtered wins: the client refuses enabled and filtered together
        await s.set_service([0], True, filtered=True, mask=1)
        port = last_ports(rec)[0]
        assert port["service_filtered"] is True and port["service"] is False
        await s.set_service([0], True)
        port = last_ports(rec)[0]
        assert port["service"] is True and port["service_filtered"] is False
        await s.set_service([0], False, filtered=True, mask=3)
        assert last_ports(rec)[0]["service_filtered"] is True
        await s.set_service([0], False)
        port = last_ports(rec)[0]
        assert port["service"] is False and port["service_filtered"] is False
    new_session.run(run())


def test_service_mask_reaches_the_wire(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        s.set_rpc_console(True)
        await s.set_service([0], False, filtered=True, mask=6)       # 6, not the default 1
        await wait_for(
            lambda: any(_is_service_request(m) and _service_params(m)["mask"] == 6 for m in rec.msgs), 2
        )
        sent = [_service_params(m) for m in rec.msgs if _is_service_request(m)]
        assert [(p["enabled"], p["filtered"], p["mask"]) for p in sent] == [(False, True, 6)]
    new_session.run(run())


def test_stop_of_a_flow_stats_profile_removes_rx_filters_on_port_1(fake_trex, new_session):
    """A3.5: stopping a profile with flow_stats sends remove_rx_filters for that port."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([1])
        await s.assign_profile(
            [1], await trexapi.load_profile(str(FIXTURES / "profiles" / "gui_example.yaml"), [1], [])
        )
        await s.start([1], "1kpps")
        s.set_rpc_console(True)
        await s.stop([1])
        def removals():
            return [b for m in rec.msgs if m.get("type") == "rpc" and m["direction"] == "request"
                    for b in _bodies(m) if b.get("method") == "remove_rx_filters"]
        await wait_for(lambda: removals(), 2)
        req = removals()
        assert [r["params"]["port_id"] for r in req] == [1]
        replies = [b for m in rec.msgs if m.get("type") == "rpc" and m["direction"] == "response"
                   for b in _bodies(m) if b.get("id") == req[0]["id"]]
        assert len(replies) == 1 and not replies[0].get("error")
    new_session.run(run())


def _bodies(m):
    """The JSON-RPC calls in a tapped message: a stats batch is one request whose body is a list."""
    b = json.loads(m["body"]) if isinstance(m["body"], str) else m["body"]
    return b if isinstance(b, list) else [b]


def _service_params(m):
    """The params of the first "service" call in tapped message `m`; StopIteration when it holds none."""
    return next(b["params"] for b in _bodies(m) if b.get("method") == "service")


def _is_service_request(m):
    """True for a tapped RPC request message that carries a "service" call (alone or in a batch)."""
    return (
        m.get("type") == "rpc"
        and m["direction"] == "request"
        and any(b.get("method") == "service" for b in _bodies(m))
    )


def test_assign_validates_and_returns_rate_per_port(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0, 1])
        rates = await s.assign_profile([0, 1], await loaded([0, 1]))
        assert sorted(rates) == [0, 1] and all(isinstance(r, dict) and r for r in rates.values())
    new_session.run(run())


def test_profiles_follow_commands_without_a_poll(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        s._poll_s = 3600                     # no safety poll: only command refreshes update the view
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        assert last_ports(rec)[0]["profiles"] == {"_": "STREAMS"}
        await s.start([0], "1kpps")
        assert last_ports(rec)[0]["profiles"] == {"_": "TX"}
    new_session.run(run())


def test_foreign_reset_clears_the_default_profile_after_a_poll(fake_trex, foreign_client, new_session):
    """STLPort.__set_profile_state skips state 0 (trex_stl_port.py:67-68), so after a foreign reset the
    client cache kept "_": STREAMS; sync_ports drops profiles the server reports IDLE or omits."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        assert last_ports(rec)[0]["profiles"] == {"_": "STREAMS"}
        b = foreign_client(f)
        await asyncio.to_thread(b.reset, ports=[0])
        await wait_for(lambda: "_" not in last_ports(rec)[0]["profiles"], 6)       # the next poll
        assert last_ports(rec)[0]["state"] == "IDLE"
    new_session.run(run())


def test_reassign_after_a_foreign_reset_and_a_poll(fake_trex, foreign_client, new_session):
    """The poll drops "_" from profile_state_list; its stream bookkeeping must go too, or the next
    remove_all_streams hits the client's unguarded `del profile_state_list[...]` (trex_stl_port.py:88-89)."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        b = foreign_client(f)
        await asyncio.to_thread(b.reset, ports=[0])
        await wait_for(lambda: "_" not in last_ports(rec)[0]["profiles"], 6)       # the next poll
        await s.acquire([0], force=True)
        await s.assign_profile([0], await loaded([0]))
        assert last_ports(rec)[0]["profiles"] == {"_": "STREAMS"}
    new_session.run(run())


def test_rate_info(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        info = await s.rate_info(0)
        assert isinstance(info, dict) and info                 # shape = Task 1's recorded validate_result
    new_session.run(run())


def test_command_to_a_stopped_server_is_unreachable_and_marks_lost(fake_trex, new_session):
    """The vendored client answers a command to a silent server with 'Not connected to server'
    (trex_req_resp_client.py:36/180/389); that must read as unreachable, not as a TRex error."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        os.kill(f.proc.pid, signal.SIGSTOP)
        with pytest.raises(trexapi.ServerUnreachable):
            await s.release([0])
        assert s.snapshot()["state"] == "lost"
        os.kill(f.proc.pid, signal.SIGCONT)
    new_session.run(run())


def test_vendored_not_connected_text_translates_to_unreachable(fake_trex, new_session):
    """The text comes from the real vendored client after it dropped a silent server, not from a literal."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        c = s._client
        os.kill(f.proc.pid, signal.SIGSTOP)
        with pytest.raises(Exception, match="Failed to get server response"):
            await s._run(lambda: c.acquire(ports=[0]))             # the first RPC fails and drops the link
        # the next one answers 'Not connected to server'
        rc = await s._run(lambda: c.ports[0].sync())
        os.kill(f.proc.pid, signal.SIGCONT)
        assert rc.bad() and "Not connected to server" in str(rc.err())
        assert isinstance(trexapi.translate(Exception(str(rc.err()))), trexapi.ServerUnreachable)
    new_session.run(run())


# -- P2.5 Task 3: port-wide commands act on every profile; per-profile commands check "_" ------------
def _bench_running(fake_trex, foreign_client):
    """Starts a fake and returns (fake, foreign client), the client running the named profile `bench` on
    port 0 (the default profile `_` stays idle)."""
    f = fake_trex()
    b = foreign_client(f)                 # loads the vendored client; only then is `trex` importable
    from trex.stl.api import STLProfile
    b.acquire(ports=[0])
    b.add_streams(STLProfile.load_py(str(FIXTURES / "profiles" / "udp_for_benchmarks.py")).get_streams(),
                  ports=["0.bench"])
    b.start(ports=["0.bench"])
    return f, b


def _raw_status(c):
    """Port 0's status for every profile, read straight from the vendored client `c` (a non-blocking
    get_port_status RPC, not through the session)."""
    return c.ports[0].transmit("get_port_status", {"port_id": 0, "profile_id": "*", "block": False}).data()


def _requests(rec, *methods):
    """The method names, in order, of the RPC calls in the requests `rec` recorded, keeping only those
    named in `methods` (batched calls count one by one)."""
    return [b["method"] for m in rec.msgs if m.get("type") == "rpc" and m["direction"] == "request"
            for b in _bodies(m) if b.get("method") in methods]


def test_stop_stops_a_named_profile_started_by_someone_else(fake_trex, foreign_client, new_session):
    f, b = _bench_running(fake_trex, foreign_client)
    rec = Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0], force=True)
        await s.stop([0])
        assert last_ports(rec)[0]["state"] != "TX"
    new_session.run(run())
    status = _raw_status(b)
    assert status["state_profile"]["bench"] == "STREAMS" and status["state"] != "TX"


def test_published_port_view_for_a_foreign_profile_only(fake_trex, foreign_client, new_session):
    """The wire shape the frontend tests use: with only `bench` in TX, the client never records `_` IDLE
    (trex_stl_port.py:67-68) and _drop_idle_profiles removes it, so the PortView has no "_" entry."""
    f, _b = _bench_running(fake_trex, foreign_client)
    rec = Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0], force=True)
        await wait_for(lambda: last_ports(rec)[0]["profiles"] == {"bench": "TX"}, 6)       # the next poll
        assert last_ports(rec)[0]["state"] == "TX"
        assert "_" not in last_ports(rec)[0]["profiles"]
    new_session.run(run())


def test_stop_when_idle_is_still_a_noop(fake_trex, new_session):
    """Guard: stop on an idle owned port returns without error."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([1])
        await s.stop([1])
        assert last_ports(rec)[1]["state"] == "IDLE"
    new_session.run(run())


def test_stop_leaves_no_transient_streams_ghost(fake_trex, foreign_client, new_session):
    f, _ = _bench_running(fake_trex, foreign_client)
    rec = Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0], force=True)
        await s.stop([0])
        # The client records no "_" at all for an idle default profile (state_from_name_dynamic skips
        # STATE_IDLE = 0, trex_stl_port.py:70); the ghost this guards against is "_": "STREAMS".
        assert last_ports(rec)[0]["profiles"].get("_", "IDLE") == "IDLE"
    new_session.run(run())


def test_assign_with_stop_first_stops_a_named_profile(fake_trex, foreign_client, new_session):
    f, b = _bench_running(fake_trex, foreign_client)
    rec = Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0], force=True)
        loaded = await trexapi.load_profile(str(FIXTURES / "profiles" / "gui_example.yaml"), [0], [])
        await s.assign_profile([0], loaded, stop_first=True)
        assert last_ports(rec)[0]["state"] != "TX"
    new_session.run(run())
    assert _raw_status(b)["state_profile"]["bench"] != "TX"


def test_pause_update_resume_refuse_when_only_a_named_profile_runs(fake_trex, foreign_client, new_session):
    f, _ = _bench_running(fake_trex, foreign_client)
    rec = Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0], force=True)
        s.set_rpc_console(True)
        for call, cause in ((lambda: s.pause([0]), "is not transmitting"),
                            (lambda: s.update([0], "2x"), "is not transmitting"),
                            (lambda: s.resume([0]), "is not paused")):
            with pytest.raises(CommandRefused) as e:
                await call()
            assert e.value.code == "not_active"
            assert "profile '_'" in e.value.message and cause in e.value.message
        assert _requests(rec, "pause_traffic", "update_traffic", "resume_traffic") == []
    new_session.run(run())


def _poll_record(i, direction="request", body=None):
    """A poll-flagged RpcRecord (timestamp 1.0, thread "t"); `body` defaults to a get_global_stats call
    with id `i`."""
    return trexapi.RpcRecord(direction, body if body is not None else {"id": i, "method": "get_global_stats"},
                             1.0, "t", poll=True)


def test_console_hides_polling_but_shows_the_users_commands(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([1])
        await s.assign_profile(
            [1], await trexapi.load_profile(str(FIXTURES / "profiles" / "gui_example.yaml"), [1], [])
        )
        await s.start([1], "1kpps")
        s.set_rpc_console(True)
        assert s.snapshot()["include_polling"] is False
        for i in range(20):
            s._on_rpc(_poll_record(i))
            s._on_rpc(_poll_record(i, "response", {"id": i, "result": {}}))
        await s.stop([1])
        await asyncio.sleep(0.1)
        rpcs = [m for m in rec.msgs if m.get("type") == "rpc"]
        assert not any(b.get("method") in ("get_global_stats", "get_async_events", "get_port_status")
                       for m in rpcs for b in _bodies(m))
        stop = [
            b
            for m in rpcs
            if m["direction"] == "request"
            for b in _bodies(m)
            if b.get("method") == "stop_traffic"
        ]
        assert len(stop) == 1
        assert any(
            b.get("id") == stop[0]["id"] for m in rpcs if m["direction"] == "response" for b in _bodies(m)
        )
    new_session.run(run())


def test_console_with_polling_on_publishes_polls(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        s.set_rpc_console(True, include_polling=True)
        before = len([m for m in rec.msgs if m.get("type") == "rpc"])
        for i in range(20):
            s._on_rpc(_poll_record(i))
        await wait_for(lambda: len([m for m in rec.msgs if m.get("type") == "rpc"]) >= before + 20, 2.0)
    new_session.run(run())


def test_include_polling_is_snapshot_state_and_capture_toggle_keeps_it(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        s.set_rpc_console(True, include_polling=True)
        # a reload sees it
        assert [m for m in rec.msgs if m["type"] == "session"][-1]["include_polling"] is True
        s.set_rpc_console(False)
        assert s.snapshot()["include_polling"] is True and s.snapshot()["rpc_console"] is False
        s.set_rpc_console(True, include_polling=False)
        assert s.snapshot()["include_polling"] is False
    new_session.run(run())


def test_restart_error_in_a_list_response_is_noted_with_the_console_off(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        assert s.snapshot()["rpc_console"] is False
        # a stats batch answered after a restart: the error rides inside a list body
        s._on_rpc(
            trexapi.RpcRecord(
                "response",
                [
                    {"id": 1, "result": {}},
                    {"id": 2, "error": {"code": -32000, "message": "API handler provided mismatch"}},
                ],
                1.0,
                "t",
                poll=True,
            )
        )
        await asyncio.sleep(0.05)
        assert s._restart_cause is not None and "restarted" in s._restart_cause
    new_session.run(run())


def _foreign_flow_stats_stream():
    """A continuous 1 kpps UDP stream with flow stats (pg_id 12), built from the vendored API."""
    from trex.stl import api as t
    return t.STLStream(packet=t.STLPktBuilder(pkt=t.Ether() / t.IP() / t.UDP() / ("x" * 18)),
                       flow_stats=t.STLFlowStats(pg_id=12), mode=t.STLTXCont(pps=1000))


def test_stop_stops_a_foreign_profile_the_cache_missed(fake_trex, foreign_client, new_session):
    """Another client started profile "bench"; the session's cache holds it as STREAMS (its port idle),
    so "0.*" would not stop it. Stop syncs the port first and stops what the server reports."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        s._poll_s = 3600                       # the poll cannot heal the cache
        await s.connect(params(f))
        b = foreign_client(f)
        await asyncio.to_thread(b.acquire, ports=[0], force=True)
        # b's "port acquired" event reaches s on the async SUB socket, apart from the REQ replies. Handled
        # after s.acquire it would clear s's handler (async_event_port_acquired), so s.stop would be refused
        # as "not owned". Wait until s has seen it (it sets the port's owner) before s takes the port.
        await wait_for(lambda: s._client.ports[0].owner == b.ctx.username, 5)
        await asyncio.to_thread(b.add_streams, [_foreign_flow_stats_stream()], ports=["0.bench"])
        await asyncio.to_thread(b.start, ports=["0.bench"])
        await s.acquire([0], force=True)
        port = s._client.ports[0]
        port.profile_state_list["bench"] = port.STATE_STREAMS  # the stale cache: profile and port
        port.state = port.STATE_STREAMS
        await s.stop([0])
        state = b.ports[0].transmit(
            "get_port_status", {"port_id": 0, "profile_id": "*", "block": False}).data()["state_profile"]
        assert state["bench"] == "STREAMS"
    new_session.run(run())


def test_foreign_profile_events_reach_the_log_and_the_port_view(fake_trex, foreign_client, new_session):
    """A3.11: another client starts, pauses, resumes and stops profile "bench". The session publishes the four
    profile events with their messages, and the start events (no poll) put bench in TX."""
    f, rec = fake_trex(), Recorder()
    def profile_events():
        return [m for m in rec.msgs if m["type"] == "event" and m["name"].startswith("profile ")]
    async def run():
        s = new_session(rec)
        s._poll_s = 3600
        await s.connect(params(f))
        b = foreign_client(f)
        await asyncio.to_thread(b.acquire, ports=[0], force=True)
        await wait_for(lambda: s._client.ports[0].owner == b.ctx.username, 5)
        await asyncio.to_thread(b.add_streams, [_foreign_flow_stats_stream()], ports=["0.bench"])
        await asyncio.to_thread(b.start, ports=["0.bench"])
        await wait_for(lambda: len(profile_events()) >= 1, 5)
        await wait_for(lambda: last_ports(rec)[0]["profiles"].get("bench") == "TX", 5)
        await asyncio.to_thread(b.pause, ports=["0.bench"])
        await asyncio.to_thread(b.resume, ports=["0.bench"])
        await asyncio.to_thread(b.stop, ports=["0.bench"])
        await wait_for(lambda: len(profile_events()) >= 4, 5)
        evs = profile_events()
        assert [m["name"] for m in evs] == ["profile started", "profile paused", "profile resumed",
                                            "profile stopped"]
        assert [m["message"] for m in evs] == [f"Profile 0.bench has {w}" for w in
                                               ("started", "paused", "resumed", "stopped")]
    new_session.run(run())


def test_stop_drops_a_ghost_profile_before_expanding(fake_trex, new_session):
    """The cache holds a profile the server does not have: after the sync it is gone, so "0.*" never
    expands to it and no stop_traffic is sent for it."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        s._poll_s = 3600
        await s.connect(params(f))
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
        await s.start([0], "1kpps")
        port = s._client.ports[0]
        port.profile_state_list["ghost"] = port.STATE_TX
        port.profile_stream_list["ghost"] = []
        s.set_rpc_console(True)
        await s.stop([0])
        sent = [b for m in rec.msgs if m.get("type") == "rpc" and m["direction"] == "request"
                for b in _bodies(m) if b.get("method") == "stop_traffic"]
        assert sent and all(b["params"].get("profile_id") != "ghost" for b in sent)
        assert "ghost" not in port.profile_state_list
    new_session.run(run())


def test_stop_on_an_idle_port_succeeds_and_sends_nothing(fake_trex, new_session):
    """An idle owned port: Stop succeeds after the sync and sends no stop_traffic and no remove_rx_filters."""
    f, rec = fake_trex(), Recorder()
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        s.set_rpc_console(True)
        await s.stop([0])
        sent = {b.get("method") for m in rec.msgs if m.get("type") == "rpc" and m["direction"] == "request"
                for b in _bodies(m)}
        assert not sent & {"stop_traffic", "remove_rx_filters"}
    new_session.run(run())
