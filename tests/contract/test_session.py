import asyncio
import os
import signal
import time

import pytest
from helpers import Recorder, wait_for
from trex_web_gui.sessions import ConnectParams, SessionBusy
from trex_web_gui.trexapi import (
    ClientEvent,
    ServerUnreachable,
    TrexApiError,
    UnsupportedServerVersion,
    loss_bound,
)


def crash_message(rec: Recorder) -> str | None:
    """The `message` of the last "subscriber crashed" message `rec` recorded; None when it recorded none."""
    crashes = [m for m in rec.msgs if m.get("name") == "subscriber crashed"]
    return crashes[-1]["message"] if crashes else None


def params(f, timeout_s: float = 2.0) -> ConnectParams:
    """ConnectParams for fake `f` with the given timeout."""
    return ConnectParams(f.host, f.rpc_port, f.pub_port, timeout_s)


def test_connect_reports_info_without_a_spurious_resumed(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        info = await s.connect(params(fake_trex()))
        assert (info.version, info.mode, info.port_count) == ("v3.08", "STL", 2)
        await asyncio.sleep(1.0)                         # upstream emits "subscriber resumed" at start
        assert "subscriber resumed" not in rec.events(), rec.events()
        # the last carries the resolved ifaces
        assert rec.states() == ["connecting", "connected", "connected"]
        assert s.snapshot()["state"] == "connected"
        await s.disconnect()
        await s.close()
        assert rec.states()[-1] == "disconnected"
    new_session.run(run())


def test_other_trex_version_refused_and_client_released(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        with pytest.raises(TrexApiError) as ei:
            await s.connect(params(fake_trex("--server-version", "v3.07")))
        assert str(ei.value) == (
            "The server runs TRex v3.07; trex-web-gui supports only TRex v3.08 (STL API 5.1).")
        assert rec.states() == ["connecting", "disconnected"]
        assert s.snapshot()["state"] == "disconnected"
    new_session.run(run())


def test_5_0_server_refused_naming_its_version(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        with pytest.raises(UnsupportedServerVersion) as ei:
            await s.connect(params(fake_trex("--reject-api")))
        assert ei.value.server_version == "5.0" and "5.0" in str(ei.value)
        assert rec.states() == ["connecting", "disconnected"]
        assert "5.0" in (rec.msgs[-1]["error"] or "")
        await s.close()
    new_session.run(run())


def test_no_server_fails_fast_with_cause(fake_trex, new_session) -> None:
    async def run() -> None:
        f = fake_trex()
        f.stop()  # ports known to be free now
        s = new_session(Recorder())
        t = time.monotonic()
        with pytest.raises(ServerUnreachable):
            await s.connect(params(f, timeout_s=1.0))
        assert time.monotonic() - t < 1.0 + 2.0          # Review Focus 4: bounded by timeout + margin
        await s.close()
    new_session.run(run())


def test_connect_while_connecting_is_refused(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        f = fake_trex()
        first = asyncio.create_task(s.connect(params(f)))
        await asyncio.sleep(0)                           # let the first take the lock
        with pytest.raises(SessionBusy):
            await s.connect(params(f))
        await first
        await s.disconnect()
        await s.close()
    new_session.run(run())


def test_seq_gap_marks_lost_and_process_survives(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        f = fake_trex()
        await s.connect(params(f))
        f.arm_seq_gap()
        await wait_for(lambda: s.snapshot()["state"] == "lost", 5)
        assert "subscriber crashed" in rec.events()
        assert "SEQ ERROR 7 6" in str([m for m in rec.msgs if m.get("name") == "subscriber crashed"])
        assert "SEQ ERROR 7 6" in (crash_message(rec) or "")          # the cause, not just the name
        assert "SEQ ERROR 7 6" in str(s.snapshot()["error"])
        await s.close()
    new_session.run(run())
    # Reaching this line (the process is alive) is the check: patch 05 + the handler kept the host
    # unsignalled.


def test_loss_during_connect_never_reports_connected(fake_trex, new_session) -> None:  # Review Focus 5
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        try:
            await s.connect(params(fake_trex("--seq-gap-at-start")))
        except TrexApiError:
            pass                                         # failing the connect is acceptable
        await asyncio.sleep(1.0)                         # let any late event land
        assert s.snapshot()["state"] in ("lost", "disconnected")
        assert rec.states()[-1] != "connected"
        assert "SEQ ERROR 7 6" in (crash_message(rec) or "")          # the cause, not just the name
        assert "SEQ ERROR 7 6" in str(s.snapshot()["error"])
        await s.close()
    new_session.run(run())


def test_stalled_server_marks_lost_then_reconnect_works(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        f = fake_trex()
        await s.connect(params(f, timeout_s=1.0))
        os.kill(f.proc.pid, signal.SIGSTOP)
        t = time.monotonic()
        await wait_for(lambda: s.snapshot()["state"] == "lost", loss_bound(1.0))
        lost_after = time.monotonic() - t
        # the reader's timeout can mark LOST first (its handler publishes no event); the control's follows
        await wait_for(lambda: "subscriber timeout" in rec.events(), 5)
        os.kill(f.proc.pid, signal.SIGCONT)
        g = fake_trex()
        info = await s.connect(params(g))                # Review Focus 5: cleanup must not block reconnect
        assert info.version == "v3.08"
        await s.disconnect()
        await s.close()
        print(f"lost_after_s={lost_after:.2f}")           # recorded in the task report (canon:VER-3)
    new_session.run(run())


def test_get_system_info_failure_is_reported(fake_trex, new_session) -> None:
    async def run() -> None:
        s = new_session(Recorder())
        with pytest.raises(TrexApiError, match="get_system_info not registered"):
            await s.connect(params(fake_trex("--drop", "get_system_info")))
        assert s.snapshot()["state"] == "disconnected"
        await s.close()
    new_session.run(run())


def test_cancelled_connect_leaves_disconnected_and_reusable(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        dead = fake_trex()
        dead.stop()  # nothing answers: connect() blocks for ~timeout_s
        t = asyncio.create_task(s.connect(params(dead, timeout_s=1.0)))
        await asyncio.sleep(0.1)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert s.snapshot()["state"] == "disconnected"
        assert rec.states()[-1] == "disconnected"
        assert s._client is None                         # the client the cancelled job built was released
        info = await s.connect(params(fake_trex()))      # and the session is usable again
        assert info.version == "v3.08"
        await s.disconnect()
        await s.close()
    new_session.run(run())


def test_closed_session_refuses_connect_and_close_is_idempotent(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        await s.close()
        published = len(rec.msgs)
        await s.close()
        assert len(rec.msgs) == published                # a repeated close() publishes nothing
        with pytest.raises(TrexApiError, match="session is closed"):
            await s.connect(params(fake_trex()))
    new_session.run(run())


def test_resumed_is_logged_only_after_a_timeout_in_this_connection(fake_trex, new_session) -> None:
    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        await s.connect(params(fake_trex()))
        resumed = ClientEvent("subscriber resumed", [], "Subscriber resumed", 1.0)
        s._handle_event(resumed)
        assert "subscriber resumed" not in rec.events()
        s._handle_event(ClientEvent("subscriber timeout", [], "Subscriber timeout", 2.0, level="warning"))
        s._handle_event(resumed)
        assert rec.events()[-2:] == ["subscriber timeout", "subscriber resumed"]
        await s.disconnect()
        await s.connect(params(fake_trex()))             # a new connection starts clean
        s._handle_event(resumed)
        assert rec.events().count("subscriber resumed") == 1
    new_session.run(run())


def test_new_session_run_closes_sessions_in_the_loop_they_ran_in(new_session) -> None:
    """Guards the harness: a close() in a later asyncio.run let late events schedule _refresh_soon on a dead
    loop ("Task was destroyed but it is pending")."""
    loops: dict[str, asyncio.AbstractEventLoop] = {}
    async def run() -> None:
        s = new_session(Recorder())
        loops["body"] = asyncio.get_running_loop()
        real_close = s.close
        async def spy() -> None:
            loops["close"] = asyncio.get_running_loop()
            await real_close()
        s.close = spy                                    # type: ignore[method-assign]
    new_session.run(run())
    assert loops["close"] is loops["body"]
    assert all(s._closed for s in new_session.made)


def test_poll_survives_an_unexpected_exception(fake_trex, new_session, monkeypatch, caplog) -> None:
    import trex_web_gui.trexapi as api
    real, calls = api.sync_ports, []

    def flaky(c):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real(c)
    monkeypatch.setattr(api, "sync_ports", flaky)

    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        s._poll_s = 0.05
        await s.connect(params(fake_trex()))
        await wait_for(lambda: len(calls) >= 3, 5)          # the poll kept ticking after the RuntimeError
        assert s._poll_task is not None and not s._poll_task.done()
        assert any(r.exc_info and "boom" in str(r.exc_info[1]) for r in caplog.records)
        await s.disconnect()
        await s.close()
    new_session.run(run())


def test_refresh_failure_is_logged_not_left_unretrieved(fake_trex, new_session, monkeypatch, caplog) -> None:
    import trex_web_gui.trexapi as api

    async def run() -> None:
        rec = Recorder()
        s = new_session(rec)
        s._poll_s = 3600
        await s.connect(params(fake_trex()))
        monkeypatch.setattr(api, "port_views", lambda c: (_ for _ in ()).throw(RuntimeError("refresh-boom")))
        s._schedule_refresh()
        await wait_for(
            lambda: any(
                "refresh-boom" in str(r.exc_info[1])
                for r in caplog.records
                if r.exc_info and r.name.startswith("trex_web_gui")
            ),
            5,
        )
        await s.disconnect()
        await s.close()
    new_session.run(run())
