"""make_client's event tap must publish the handler's Event.msg (upstream EventsHandler.on_event returns
None, so the message has to come from the handler itself). Builds a real STLClient; nothing connects."""
import json
import threading
import time
from typing import Any

import pytest
from trex_web_gui.trexapi import (
    PENDING_ACQUIRE_S,
    ClientEvent,
    RpcRecord,
    acquire_marked,
    make_client,
    mark_pending,
    unmark_pending,
)


def test_tap_passes_handler_message_through() -> None:
    got: list[ClientEvent] = []
    c = make_client("127.0.0.1", 1, 2, 1.0, got.append)
    eh = c.ctx.event_handler
    ev = eh.on_event("subscriber crashed", RuntimeError("SEQ ERROR 7 6"))
    assert [e.name for e in got] == ["subscriber crashed"]
    assert got[0].message == "subscriber thread crashed: RuntimeError: SEQ ERROR 7 6"
    assert ev is not None and ev.msg == got[0].message      # tap returns the handler's Event
    assert eh.events[-1] is ev                              # and records it like upstream does


def test_tap_keeps_upstream_error_for_unhandled_events() -> None:
    c = make_client("127.0.0.1", 1, 2, 1.0, lambda e: None)
    with pytest.raises(Exception, match="unhandled event 'no such event'"):
        c.ctx.event_handler.on_event("no such event")


def test_rpc_tap_sees_request_and_response(fake_trex) -> None:  # type: ignore[no-untyped-def]
    f = fake_trex()
    recs: list[RpcRecord] = []
    c = make_client(f.host, f.rpc_port, f.pub_port, 2.0, lambda ev: None, on_rpc=recs.append)
    c.connect()
    try:
        c.get_server_version()
    finally:
        c.disconnect()
    reqs = [r for r in recs if r.direction == "request" and isinstance(r.body, dict)]
    assert any(r.body.get("method") == "get_version" for r in reqs)
    resp_ids = {r.body.get("id") for r in recs if r.direction == "response" and isinstance(r.body, dict)}
    assert any(r.body.get("id") in resp_ids for r in reqs if r.body.get("method") == "get_version")


def test_event_carries_level(fake_trex) -> None:  # type: ignore[no-untyped-def]
    f = fake_trex()
    evs: list[ClientEvent] = []
    c = make_client(f.host, f.rpc_port, f.pub_port, 2.0, evs.append)
    c.connect()
    try:
        c.ctx.event_handler.on_event("port acquired", 0, "someone", 12345, True)   # foreign force-take
    finally:
        c.disconnect()
    ev = next(e for e in evs if e.name == "port acquired")
    assert ev.level in ("info", "warning") and "someone" in (ev.message or "")


def test_event_level_comes_from_upstream_ev_type(fake_trex) -> None:  # type: ignore[no-untyped-def]
    f = fake_trex()
    evs: list[ClientEvent] = []
    c = make_client(f.host, f.rpc_port, f.pub_port, 2.0, evs.append)
    c.connect()
    try:
        # upstream builds Event('server', 'warning', ...)
        c.ctx.event_handler.on_event("server stopped", "test")
    finally:
        c.disconnect()
    ev = next(e for e in evs if e.name == "server stopped")
    assert ev.level == "warning"


def test_rpc_tap_not_installed_without_on_rpc() -> None:
    c = make_client("127.0.0.1", 1, 2, 1.0, lambda ev: None)
    rpc = c.conn.rpc
    assert "verbose_msg" not in vars(rpc) and "pretty_json" not in vars(rpc)


def _tapped(msgs: list[tuple[str, object]]) -> list[RpcRecord]:
    """The RpcRecords a client that never connects taps from (prefix, body) pairs fed to its verbose_msg."""
    recs: list = []  # type: ignore[type-arg]
    c = make_client("127.0.0.1", 1, 2, 1.0, lambda ev: None, on_rpc=recs.append)
    for prefix, body in msgs:
        c.conn.rpc.verbose_msg(f"{prefix}\n\n{json.dumps(body)}")
    return recs


REQ, RESP = "Sending Request To Server:", "Server Response:"


def _call(i: int, method: str) -> dict[str, object]:
    """A JSON-RPC 2.0 request dict with id i, the method and empty params."""
    return {"jsonrpc": "2.0", "id": i, "method": method, "params": {}}


def test_rpc_tap_marks_polling_and_a_response_inherits_it_by_id() -> None:
    recs = _tapped([
        (REQ, [_call(1, "get_global_stats"), _call(2, "get_port_stats")]),
        (RESP, [{"id": 1, "result": {}}, {"id": 2, "result": {}}]),
        (REQ, _call(3, "get_async_events")), (RESP, {"id": 3, "result": []}),
        (REQ, _call(4, "get_port_status")), (RESP, {"id": 4, "result": {}}),
        (REQ, _call(5, "start_traffic")), (RESP, {"id": 5, "result": {}}),
    ])
    assert [r.poll for r in recs] == [True, True, True, True, True, True, False, False]


def test_new_poll_methods_are_hidden() -> None:
    methods = ("get_active_pgids", "get_pgid_stats", "get_utilization")
    recs = _tapped([(REQ, _call(i, m)) for i, m in enumerate(methods, 1)]
                   + [(RESP, {"id": i, "result": {}}) for i in range(1, 4)])
    assert [r.poll for r in recs] == [True] * 6


def test_rpc_tap_batch_is_a_poll_only_when_every_method_is() -> None:
    recs = _tapped([
        (REQ, [_call(1, "get_global_stats"), _call(2, "start_traffic")]),
        (RESP, [{"id": 1, "result": {}}, {"id": 2, "result": {}}]),
    ])
    assert [r.poll for r in recs] == [False, False]


def test_rpc_tap_unanswered_ids_are_bounded() -> None:
    from trex_web_gui.trexapi.client import _MAX_PENDING_POLL_IDS
    recs = _tapped([(REQ, _call(i, "get_async_events")) for i in range(_MAX_PENDING_POLL_IDS + 50)]
                   + [(RESP, {"id": 0, "result": []}),
                      (RESP, {"id": _MAX_PENDING_POLL_IDS + 49, "result": []})])
    assert recs[-2].poll is False       # id 0 was dropped, the oldest out; an unknown id is shown
    assert recs[-1].poll is True


def test_capture_status_and_fetch_are_polls_start_is_not() -> None:
    def cap(i: int, command: str) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": i, "method": "capture", "params": {"command": command}}
    commands = ("status", "fetch", "start", "stop", "remove")
    recs = _tapped([(REQ, cap(i, cmd)) for i, cmd in enumerate(commands, 1)]
                   + [(RESP, {"id": i, "result": {}}) for i in range(1, 6)])
    assert [r.poll for r in recs] == [True, True, False, False, False] * 2


@pytest.mark.parametrize("event_id,name,message,level", [
    (10, "profile started", "Profile 0.bench has started", "info"),
    (11, "profile stopped", "Profile 0.bench has stopped", "info"),
    (12, "profile paused", "Profile 0.bench has paused", "info"),
    (13, "profile resumed", "Profile 0.bench has resumed", "info"),
    (14, "profile finished tx", "Profile 0.bench job done", "info"),
    (17, "profile error", "Profile 0.bench job failed", "warning"),
])
def test_every_profile_event_id_reaches_the_tap(event_id: int, name: str, message: str, level: str) -> None:
    """A3.11: each server profile event id (10-14, 17) arrives at the tap with its name, message, level and
    [port, profile] args. Nothing connects; the id goes straight into the client's async handler."""
    got: list[ClientEvent] = []
    c = make_client("127.0.0.1", 1, 2, 1.0, got.append)
    c.conn.async_.handle_event(event_id, {"port_id": 0, "profile_id": "bench"})
    assert got[-1].name == name
    assert got[-1].message == message
    assert got[-1].level == level
    assert got[-1].args == [0, "bench"]


# --- pending-acquire marks (stop-race-flake-1) ---

OTHER = "another-session-id"


class HandlerPort:
    """A port with a handler and owner, and the two methods the vendor's "port acquired" handler calls."""

    def __init__(self) -> None:
        """Handler "h1", owner "me"."""
        self.handler, self.owner = "h1", "me"

    def is_acquired(self) -> bool:
        """True while a handler is set."""
        return bool(self.handler)

    def async_event_port_acquired(self, who: str) -> None:
        """As the vendor port: clears the handler and records the new owner."""
        self.handler, self.owner = "", who


def tap_client(got: list[ClientEvent] | None = None) -> Any:
    """An unconnected client from make_client whose port 0 is a HandlerPort."""
    c = make_client("127.0.0.1", 1, 2, 1.0, got.append if got is not None else (lambda e: None))
    c.ports = {0: HandlerPort()}
    return c


def foreign(c: Any, port: int = 0) -> Any:
    """Fires a "port acquired" event from another session at `port`."""
    return c.ctx.event_handler.on_event("port acquired", port, "x", OTHER, True)


def own(c: Any, port: int = 0) -> Any:
    """Fires the "port acquired" event of this client's own session at `port`."""
    return c.ctx.event_handler.on_event("port acquired", port, "me", c.ctx.session_id, True)


def test_marked_port_keeps_handler_on_foreign_event() -> None:
    got: list[ClientEvent] = []
    c = tap_client(got)
    mark_pending(c, [0])
    assert foreign(c) is None
    assert c.ports[0].handler == "h1"
    assert len(got) == 1 and got[0].level == "info"
    assert got[0].message == "Port 0 was taken by 'x' before our acquire; ignored"
    assert got[0].name == "port acquired" and got[0].args == [0, "x", OTHER, True]
    assert not c.ctx.event_handler.events


def test_unmarked_port_loses_handler_on_foreign_event() -> None:
    c = tap_client()
    foreign(c)
    assert c.ports[0].handler == ""


def test_own_event_clears_one_mark() -> None:
    c = tap_client()
    mark_pending(c, [0])
    own(c)
    foreign(c)
    assert c.ports[0].handler == ""


def test_two_marks_one_own_event_still_protect() -> None:
    c = tap_client()
    mark_pending(c, [0])
    mark_pending(c, [0])
    own(c)
    foreign(c)
    assert c.ports[0].handler == "h1"


def test_unmark_after_own_event_is_a_noop() -> None:
    c = tap_client()
    mark_pending(c, [0])
    own(c)
    unmark_pending(c, [0])
    assert 0 not in c.pending_acquire


def test_own_event_for_unmarked_port_is_fine() -> None:
    c = tap_client()
    own(c)
    assert c.pending_acquire == {}


def test_expired_mark_does_not_protect(monkeypatch: pytest.MonkeyPatch) -> None:
    c = tap_client()
    now = time.monotonic()
    mark_pending(c, [0])
    monkeypatch.setattr(time, "monotonic", lambda: now + PENDING_ACQUIRE_S + 1)
    foreign(c)
    assert c.ports[0].handler == ""


def test_raising_on_event_is_logged_not_raised_for_an_ignored_event() -> None:
    def boom(e: ClientEvent) -> None:
        raise RuntimeError("sink")
    c = make_client("127.0.0.1", 1, 2, 1.0, boom)
    c.ports = {0: HandlerPort()}
    mark_pending(c, [0])
    foreign(c)
    assert c.ports[0].handler == "h1"


def test_failed_acquire_leaves_no_mark() -> None:
    c = tap_client()

    def refuse(**kw: Any) -> None:
        raise trexapi_error()
    c.acquire = refuse
    with pytest.raises(RuntimeError):
        acquire_marked(c, [0], False)
    assert c.pending_acquire == {}


def trexapi_error() -> Exception:
    """The refusal the stub acquire raises."""
    return RuntimeError("refused")


def test_mark_and_unmark_from_two_threads_leave_nothing() -> None:
    c = tap_client()

    def loop() -> None:
        for _ in range(10_000):
            mark_pending(c, [0])
            unmark_pending(c, [0])
    threads = [threading.Thread(target=loop) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 0 not in c.pending_acquire


def test_acquire_marked_then_stale_foreign_event_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    c = tap_client()
    monkeypatch.setattr(c, "acquire", lambda ports, force: "h2", raising=False)
    assert acquire_marked(c, [0], True) == "h2"
    assert c.pending_acquire[0][0] == 1
    c.ctx.event_handler.on_event("port acquired", 0, "x", OTHER, True)
    assert c.ports[0].handler == "h1"
    c.ctx.event_handler.on_event("port acquired", 0, "me", c.ctx.session_id, True)
    assert 0 not in c.pending_acquire
