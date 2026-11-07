"""TrexSession's planned-stop record: a daemon stop of the server it is connected to ends the session
as DISCONNECTED with `ended`, never as LOST, and sends no RPC to the gone server."""
import time

import pytest
from helpers import Recorder
from trex_web_gui.sessions import ConnectParams
from trex_web_gui.trexapi import ClientEvent, TrexApiError
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
        await s.acquire([0])
        assert await s.expect_stop(f.host, f.rpc_port, DAEMON) is True
        s.planned_stop_done()
        snap = s.snapshot()
        assert snap["state"] == "disconnected" and snap["error"] is None
        ended = snap["ended"]
        assert ended["by"] == "daemon_stop" and ended["daemon"] == DAEMON
        assert ended["recovery"] == {"kind": "none"} and isinstance(ended["at"], float)
        assert s.ports() == [] and rec.msgs[-2:] == [{"type": "ports", "ports": []}, sessions(rec)[-1]]
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
        s.planned_stop_done()
        assert s.snapshot()["state"] == "disconnected" and s.snapshot()["ended"] is not None
        assert "lost" not in rec.states()
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
