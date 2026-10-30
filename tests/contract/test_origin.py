"""Cross-site request refusal: CSRF on simple POSTs and cross-site WebSocket hijacking."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from support import authed
from trex_web_gui.api.app import create_app
from trex_web_gui.api.origin import origin_allowed

# a full URL: the cookie jar picks the cookie by the URL host, not the Host header
WS = "ws://127.0.0.1/api/ws"
HOST = {"Host": "127.0.0.1:8090"}


@pytest.mark.parametrize("origin, ok", [
    ("http://127.0.0.1:8090", True),
    ("http://localhost:8090", True),
    ("http://localhost:5173", False),
    ("http://127.0.0.1:9999", False),
    ("http://127.0.0.1", False),
    ("http://localhost", False),
    ("http://evil.example", False),
    ("http://127.0.0.1.evil.example", False),
    ("http://localhost.evil.example:8090", False),
    ("https://127.0.0.1:8090", False),
    ("null", False),
    ("http://127.0.0.1:notaport", False),
    ("", False),
])
def test_origin_allowed(origin: str, ok: bool) -> None:
    assert origin_allowed(origin, frozenset({8090})) is ok


@pytest.fixture
def client(tmp_path: Path):
    """Yield a logged-in TestClient, entered (the lifespan runs), on an app over `tmp_path` that allows
    origin port 8090. Teardown exits the client context."""
    with authed(create_app(tmp_path, origin_ports=frozenset({8090}))) as c:
        yield c


def test_cross_site_websocket_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as e, \
            client.websocket_connect(WS, headers={**HOST, "Origin": "http://evil.example"}) as ws:
        ws.receive_json()
    assert e.value.code == 1008


@pytest.mark.parametrize("headers", [HOST, {**HOST, "Origin": "http://localhost:8090"}])
def test_same_site_and_originless_websocket_are_accepted(client: TestClient, headers: dict[str, str]) -> None:
    with client.websocket_connect(WS, headers=headers) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_cross_site_simple_post_is_refused(client: TestClient) -> None:
    r = client.post("/api/session/disconnect", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden_origin", "message": "Cross-site request refused."}


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://127.0.0.1:8090"}])
def test_same_site_and_originless_post_are_accepted(client: TestClient, headers: dict[str, str]) -> None:
    assert client.post("/api/session/disconnect", headers=headers).status_code == 200


def test_origin_ports_are_exactly_the_configured_ones(tmp_path: Path) -> None:
    app = create_app(tmp_path, origin_ports=frozenset({8765}))
    with authed(app) as c:
        for ok in ("http://127.0.0.1:8765", "http://localhost:8765"):
            assert c.post("/api/session/disconnect", headers={"Origin": ok}).status_code == 200
            with c.websocket_connect(WS, headers={"Host": "127.0.0.1:8765", "Origin": ok}) as ws:
                assert ws.receive_json()["type"] == "hello"
        for bad in ("http://127.0.0.1:9999", "http://127.0.0.1"):
            assert c.post("/api/session/disconnect", headers={"Origin": bad}).status_code == 403
            with (
                pytest.raises(WebSocketDisconnect) as e,
                c.websocket_connect(WS, headers={"Host": "127.0.0.1:8765", "Origin": bad}) as ws,
            ):
                ws.receive_json()
            assert e.value.code == 1008


@pytest.mark.parametrize("origin, ok", [
    ("http://localhost", True),            # no explicit port means port 80
    ("http://127.0.0.1", True),
    ("https://localhost", False),          # still refused: only http origins are served
    ("http://localhost:8090", False),
])
def test_origin_without_a_port_uses_the_scheme_default(origin: str, ok: bool) -> None:
    assert origin_allowed(origin, frozenset({80})) is ok
