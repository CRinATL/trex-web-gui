"""make_client's event tap must publish the handler's Event.msg (upstream EventsHandler.on_event returns
None, so the message has to come from the handler itself). Builds a real STLClient; nothing connects."""
import json

import pytest
from trex_web_gui.trexapi import ClientEvent, RpcRecord, make_client


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
