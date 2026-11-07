"""TrexSession's planned-stop record: a daemon stop of the server it is connected to ends the session
as DISCONNECTED with `ended`, never as LOST, and sends no RPC to the gone server."""
import asyncio
import dataclasses
import socket
import time

import pytest
from helpers import Recorder, wait_for
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, trex_session
from trex_web_gui.trexapi import ClientEvent, ServerUnreachable, TrexApiError
from trex_web_gui.trexproc import silence_message

DAEMON = "h:8090"
GONE_RPCS = ("capture_stop", "capture_remove", "release")


def params(f, timeout_s: float = 2.0) -> ConnectParams:
    """ConnectParams for fake `f` with the given timeout."""
    return ConnectParams(f.host, f.rpc_port, f.pub_port, timeout_s)


def timeout_event() -> ClientEvent:
    """A silence loss event, as either client's subscriber reports it."""
    return ClientEvent("subscriber timeout", [], "subscriber timeout", 1.0, level="warning")


def lose_control(s) -> None:
    """Inject a loss on the control client's path."""
    s._handle_event(timeout_event())


def lose_reader(s) -> None:
    """Inject a loss on the reader's path."""
    s._handle_reader_event(timeout_event())


def sessions(rec: Recorder) -> list[dict]:
    """Every `session` message `rec` recorded."""
    return [m for m in rec.msgs if m["type"] == "session"]


def held_loss_text(rec: Recorder) -> list[dict]:
    """The `event` messages and the reader's `log` messages `rec` recorded: the text a loss shows."""
    return [m for m in rec.msgs if m["type"] == "event" or (m["type"] == "log" and "Reader" in m["message"])]


def no_gone_rpcs(f) -> None:
    """The fake (stopped to read its counts) was sent none of the RPCs a planned stop must not send."""
    f.stop()
    served = f.served()
    assert {m: served.get(m, 0) for m in GONE_RPCS} == dict.fromkeys(GONE_RPCS, 0), served


def test_planned_stop_goes_disconnected_with_ended(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0, 1])
        await s.set_service([1], True)
        assert (await s.monitor_start([], [1], "", 100)).id
        assert s.capture_snapshot()["monitor"] is not None
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON) is True
        n = len(rec.msgs)
        s.planned_stop_done()
        captures = [m for m in rec.msgs[n:] if m["type"] == "capture"]
        assert len(captures) == 1 and captures[0]["monitor"] is None
        snap = s.snapshot()
        cap = s.capture_snapshot()
        assert cap["monitor"] is None and cap["captures"] == []
        snap = s.snapshot()
        assert snap["state"] == "disconnected" and snap["error"] is None
        ended = snap["ended"]
        assert ended["by"] == "daemon_stop" and ended["daemon"] == DAEMON
        assert ended["recovery"] == {"kind": "none"} and isinstance(ended["at"], float)
        assert s.ports() == [] and rec.msgs[-2:] == [{"type": "ports", "ports": []}, sessions(rec)[-1]]
        assert [m["type"] for m in rec.msgs[n:]] == ["capture", "ports", "session"]
        assert "lost" not in rec.states()
        await s.disconnect()
        f.stop()
    new_session.run(run())
    no_gone_rpcs(f)


@pytest.mark.parametrize("lose", [lose_control, lose_reader])
def test_loss_while_stop_expected_is_held(fake_trex, new_session, lose) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        lose(s)
        assert s.snapshot()["state"] == "connected" and "lost" not in rec.states()
        assert held_loss_text(rec) == []                 # neither the warning event nor the reader's log
        s.planned_stop_done()
        assert s.snapshot()["state"] == "disconnected" and s.snapshot()["ended"] is not None
        assert "lost" not in rec.states() and held_loss_text(rec) == []
    new_session.run(run())


@pytest.mark.parametrize("lose", [lose_control, lose_reader])
def test_held_loss_text_is_published_on_cancel(fake_trex, new_session, lose) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        lose(s)
        assert held_loss_text(rec) == []
        s.expect_stop_cancel()
        assert held_loss_text(rec) != [] and s.snapshot()["state"] == "lost"
        assert rec.msgs[-1]["type"] == "session"         # the held text first, then LOST
    new_session.run(run())


def test_late_loss_after_done_is_ignored(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        s.planned_stop_done()
        before, n = s.snapshot(), len(rec.msgs)
        lose_control(s)
        lose_reader(s)
        assert s.snapshot() == before and rec.msgs[n:] == []
    new_session.run(run())


def test_connect_after_planned_stop_does_not_release(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        s.planned_stop_done()
        t0 = time.monotonic()
        await s.connect(params(f))
        assert time.monotonic() - t0 < 2 * 2.0
        assert s.snapshot()["state"] == "connected"
        f.stop()
    new_session.run(run())
    assert f.served().get("release", 0) == 0, f.served()


def test_cancel_after_held_loss_goes_lost(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        lose_control(s)
        s.expect_stop_cancel()
        snap = s.snapshot()
        assert snap["state"] == "lost" and snap["ended"] is None
        assert snap["error"] == silence_message(f.host, f.rpc_port, 2.0, None), snap["error"]
    new_session.run(run())


def test_cancel_without_loss_changes_nothing(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        n = len(rec.msgs)
        s.expect_stop_cancel()
        assert s.snapshot()["state"] == "connected" and s.snapshot()["ended"] is None
        assert rec.msgs[n:] == []
        s.planned_stop_done()                            # nothing armed any more
        assert s.snapshot()["state"] == "connected"
    new_session.run(run())


def test_expect_stop_needs_an_exact_match(fake_trex, new_session) -> None:
    f = fake_trex()

    async def run() -> None:
        s = new_session(Recorder())
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON) is False      # disconnected
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port + 1, DAEMON) is False
        s.planned_stop_done()
        assert s.snapshot()["state"] == "connected"      # nothing was armed
        await s.disconnect()
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON) is False      # params kept, not connected
        await s.connect(ConnectParams("localhost", f.rpc_port, f.pub_port, 2.0))
        assert await s.expect_stop("127.0.0.1", f.rpc_port, DAEMON) is False
        s.planned_stop_done()
        assert s.snapshot()["state"] == "connected" and s.snapshot()["ended"] is None
    new_session.run(run())


def test_failed_connect_keeps_ended_success_clears_it(fake_trex, new_session) -> None:
    f = fake_trex()

    async def run() -> None:
        s = new_session(Recorder())
        await s.connect(params(f))
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        s.planned_stop_done()
        ended = s.snapshot()["ended"]
        with pytest.raises(TrexApiError):
            await s.connect(ConnectParams(f.host, 1, 2, 0.5))
        assert s.snapshot()["ended"] == ended
        await s.connect(params(f))
        assert s.snapshot()["ended"] is None
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        s.planned_stop_done()
        assert s.snapshot()["ended"] is not None
        await s.disconnect()
        assert s.snapshot()["ended"] is None
    new_session.run(run())


def test_set_recovery_publishes(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        n = len(rec.msgs)
        s.set_recovery({"kind": "reconnecting", "deadline": 5.0})
        assert rec.msgs[n:] == []
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
        s.planned_stop_done()
        s.set_recovery({"kind": "reconnecting", "deadline": 5.0})
        last = sessions(rec)[-1]
        assert rec.msgs[-1] is last
        assert last["ended"]["recovery"] == {"kind": "reconnecting", "deadline": 5.0}
    new_session.run(run())


def test_expect_stop_arms_from_lost(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await s.connect(params(f))
        lose_control(s)
        assert s.snapshot()["state"] == "lost"
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON) is True
        s.planned_stop_done()
        snap = s.snapshot()
        assert snap["state"] == "disconnected" and snap["error"] is None and snap["ended"]["daemon"] == DAEMON
    new_session.run(run())


# -- the reconnect after a daemon start ----------------------------------------------------------


def connecting(rec: Recorder) -> int:
    """How many `connecting` session messages `rec` recorded."""
    return rec.states().count("connecting")


async def planned_stop(s, f) -> None:
    """Connect `s` to `f`, then end the session as a planned stop (the fake keeps running)."""
    await s.connect(params(f))
    assert await s.expect_stop(f.host, f.rpc_port, DAEMON)
    s.planned_stop_done()


def test_reconnect_after_start_succeeds(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        seq = s.snapshot()["connect_seq"]
        n = len(rec.msgs)
        s.reconnect_after_start(5)
        first = sessions(rec)[-1]
        assert rec.msgs[n] is first and first["ended"]["recovery"]["kind"] == "reconnecting"
        assert first["ended"]["recovery"]["deadline"] > time.time() + 20
        await wait_for(lambda: s.snapshot()["state"] == "connected", 10)
        snap = s.snapshot()
        assert snap["ended"] is None and snap["connect_seq"] != seq
        assert snap["connect_seq"].split(":")[1] == str(int(seq.split(":")[1]) + 1)
        await s._reconnect_task
    new_session.run(run())


def test_reconnect_times_out(fake_trex, new_session, monkeypatch) -> None:
    f, rec = fake_trex(), Recorder()
    monkeypatch.setattr(trex_session, "RECONNECT_WINDOW_S", 0.5)

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        f.stop()
        before = connecting(rec)
        s.reconnect_after_start(0.1)
        await s._reconnect_task
        snap = s.snapshot()
        rcv = snap["ended"]["recovery"]
        assert rcv["kind"] == "reconnect_failed" and rcv["error"], rcv
        assert snap["state"] == "disconnected" and connecting(rec) == before
    new_session.run(run())


def test_unreachable_attempt_goes_back_to_probing(fake_trex, new_session, monkeypatch) -> None:
    f, rec = fake_trex(), Recorder()
    monkeypatch.setattr(trex_session, "RECONNECT_RETRY_S", 0.05)

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        real, calls = s.connect, []

        async def flaky(p):
            """The first attempt finds the server unreachable; later ones are real."""
            calls.append(p)
            if len(calls) == 1:
                raise ServerUnreachable("not yet")
            return await real(p)
        monkeypatch.setattr(s, "connect", flaky)
        s.reconnect_after_start(5)
        await s._reconnect_task
        assert len(calls) == 2 and s.snapshot()["state"] == "connected"
    new_session.run(run())


def test_loss_during_attempt_goes_back_to_probing(fake_trex, new_session, monkeypatch) -> None:
    f, rec = fake_trex(), Recorder()
    monkeypatch.setattr(trex_session, "RECONNECT_RETRY_S", 0.05)

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        real, calls = s.connect, []

        async def lossy(p):
            """The first attempt ends LOST; later ones are real."""
            calls.append(p)
            if len(calls) == 1:
                s._state = trex_session.SessionState.LOST
                raise TrexApiError("Connection lost during connect: x")
            return await real(p)
        monkeypatch.setattr(s, "connect", lossy)
        s.reconnect_after_start(5)
        await s._reconnect_task
        assert len(calls) == 2 and s.snapshot()["state"] == "connected"
    new_session.run(run())


def test_cancel_during_an_unreachable_attempt_ends_the_task(fake_trex, new_session, monkeypatch) -> None:
    f, rec = fake_trex(), Recorder()
    monkeypatch.setattr(trex_session, "RECONNECT_RETRY_S", 0.05)
    mute = socket.socket()
    mute.bind(("127.0.0.1", 0))
    mute.listen()                                        # accepts the TCP connect and never answers

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        s._params = dataclasses.replace(s._params, sync_port=mute.getsockname()[1], timeout_s=1.0)
        s.reconnect_after_start(30)
        await wait_for(lambda: connecting(rec) == 1, 5)
        t0 = time.monotonic()
        await s.cancel_reconnect()
        assert time.monotonic() - t0 < 1.0 + 2
        assert s._reconnect_task.done()
        await asyncio.sleep(0.5)                         # ten probe periods: a task not ended would try again
        assert connecting(rec) == 1
        assert s.snapshot()["ended"]["recovery"] == {"kind": "none"}
    try:
        new_session.run(run())
    finally:
        mute.close()


def test_a_later_stop_or_start_cancels_the_reconnect(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        f.stop()                                         # nothing to reconnect to: the task keeps waiting
        s.reconnect_after_start(30)
        await asyncio.sleep(0.1)
        task = s._reconnect_task
        assert not task.done() and s.snapshot()["ended"]["recovery"]["kind"] == "reconnecting"
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON) is False
        assert task.done() and s.snapshot()["ended"]["recovery"] == {"kind": "none"}
        s.reconnect_after_start(30)
        await asyncio.sleep(0.1)
        task, ended = s._reconnect_task, s.snapshot()["ended"]
        assert not task.done()
        await s.daemon_started("other:8090", f.rpc_port, "stl", 30, None)
        assert task.done()
        assert s.snapshot()["ended"] == {**ended, "recovery": {"kind": "none"}}
    new_session.run(run())


def test_a_refusal_ends_the_reconnect_at_once(fake_trex, new_session, monkeypatch) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        before = connecting(rec)
        monkeypatch.setattr(trexapi, "SUPPORTED_VERSION", "v0.0")
        s.reconnect_after_start(30)
        await asyncio.wait_for(s._reconnect_task, 5)
        rcv = s.snapshot()["ended"]["recovery"]
        assert rcv["kind"] == "reconnect_failed" and rcv["error"], rcv
        assert connecting(rec) == before + 1
    new_session.run(run())


def test_started_needs_a_record_and_a_match(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        n = len(rec.msgs)
        await s.daemon_started(DAEMON, f.rpc_port, "stl", 5, None)       # no record
        assert s._reconnect_task is None and rec.msgs[n:] == []
        await planned_stop(s, f)
        await s.daemon_started(DAEMON, f.rpc_port + 1, "stl", 5, None)   # another command port
        assert s._reconnect_task is None
        assert s.snapshot()["ended"]["recovery"] == {"kind": "none"}
        await s.daemon_started(DAEMON, f.rpc_port, "stl", 5, None)       # the match
        assert s._reconnect_task is not None
        await wait_for(lambda: s.snapshot()["state"] == "connected", 10)
    new_session.run(run())


def test_stf_start_sets_stf_run(fake_trex, new_session) -> None:
    f = fake_trex()

    async def run() -> None:
        s = new_session(Recorder())
        await planned_stop(s, f)
        await s.daemon_started("h:8090", None, "stf", 40, ends_at=123.0)
        assert s.snapshot()["ended"]["recovery"] == {"kind": "stf_run", "ends_at": 123.0}
        assert s._reconnect_task is None
    new_session.run(run())


def test_close_awaits_the_reconnect(fake_trex, new_session) -> None:
    f = fake_trex()

    async def run() -> None:
        s = new_session(Recorder())
        await planned_stop(s, f)
        f.stop()
        s.reconnect_after_start(30)
        await asyncio.sleep(0.1)
        task = s._reconnect_task
        assert not task.done()
        await s.close()
        assert task.done()
    new_session.run(run())


def test_forget_ended_publishes_only_when_there_was_one(fake_trex, new_session) -> None:
    f, rec = fake_trex(), Recorder()

    async def run() -> None:
        s = new_session(rec)
        await planned_stop(s, f)
        n = len(rec.msgs)
        s.forget_ended()
        assert s.snapshot()["ended"] is None and rec.msgs[n:] == [s.snapshot()]
        s.forget_ended()
        assert len(rec.msgs) == n + 1
    new_session.run(run())
