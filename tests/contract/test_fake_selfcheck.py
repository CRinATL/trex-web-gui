import pytest
from trex_web_gui import trexapi


def _client(f, timeout_s=2.0):
    """A new unconnected client for fake `f` (host, rpc_port, pub_port) that ignores events, from
    trexapi.make_client."""
    return trexapi.make_client(f.host, f.rpc_port, f.pub_port, timeout_s, lambda ev: None)


def test_connect_and_info_match_recording(fake_trex) -> None:
    f = fake_trex()
    c = _client(f)
    c.connect()
    assert c.get_server_version()["version"] == "v3.08"
    assert c.get_server_system_info()["port_count"] == 2
    c.disconnect()
    f.stop()
    assert f.served().get("get_async_events", 0) > 0


@pytest.mark.parametrize("flags, cause", [
    (("--reject-api",), "server: '5.0', client: '5.1'"),
    (("--drop", "get_system_info"), "Method get_system_info not registered"),
])
def test_negative_controls_fail_for_their_cause(fake_trex, flags, cause) -> None:
    c = _client(fake_trex(*flags))
    with pytest.raises(Exception) as ei:
        c.connect()
        c.get_server_system_info()
    assert cause in str(ei.value)


def test_session_id_is_random_nonzero() -> None:  # plan §4.1 Ownership; upstream trex_ctx.py:18-20
    ids = {_client(type("F", (), {"host": "127.0.0.1", "rpc_port": 1, "pub_port": 2})()).ctx.session_id
           for _ in range(5)}
    assert 0 not in ids and len(ids) == 5
