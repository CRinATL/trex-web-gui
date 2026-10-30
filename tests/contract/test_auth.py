"""The per-launch access token (P3c Task 0): every unauthenticated client here is anon(app), every
logged-in one authed(app)."""
import re
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.websockets import WebSocketDisconnect
from support import anon, authed
from trex_web_gui.api.app import create_app
from trex_web_gui.api.auth import cookie_name

WS = "ws://127.0.0.1/api/ws"
AUTH_ROUTES = {"/api/auth/login", "/api/auth/status", "/api/auth/logout"}
UNAUTHENTICATED = {"error": "unauthenticated", "message": "Open the URL printed by trex-web-gui serve."}

# Built at collection time so the routes can parametrise the test. Its own static dir, with an index.html
# as test_compression.py has, so that GET / is a real answer. The middleware answers before any handler,
# so no lifespan runs.
_DIR = tempfile.TemporaryDirectory()
_STATIC = Path(_DIR.name) / "dist"
(_STATIC / "assets").mkdir(parents=True)
(_STATIC / "index.html").write_text("<!doctype html><title>trex-web-gui</title>")
APP = create_app(Path(_DIR.name) / "data", static_dir=_STATIC)
ROUTES = sorted((m, re.sub(r"\{[^}]+\}", "1", r.path)) for r in APP.routes
                if isinstance(r, APIRoute) and r.path.startswith("/api/") and r.path not in AUTH_ROUTES
                for m in r.methods)


def test_the_route_list_is_not_empty() -> None:
    assert len(ROUTES) > 20 and ("GET", "/api/about") in ROUTES and ("POST", "/api/session/connect") in ROUTES


@pytest.mark.parametrize("method, path", ROUTES)
def test_no_cookie_is_401_on_every_api_route(method: str, path: str) -> None:
    r = anon(APP).request(method, path)
    assert r.status_code == 401
    if method != "HEAD":
        assert r.json() == UNAUTHENTICATED


def test_unknown_api_path_is_401_too_and_the_spa_needs_no_token() -> None:
    c = anon(APP)
    assert c.get("/api/no-such-route").status_code == 401
    r = c.get("/")
    assert r.status_code == 200 and "<title>trex-web-gui</title>" in r.text


def test_login_sets_httponly_samesite_strict_cookie_and_redirects(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    r = anon(app).get("/api/auth/login?token=" + app.state.token, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{cookie_name(app.state.token)}={app.state.token};")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert "Secure" not in cookie and "Path=/" in cookie and "Max-Age" not in cookie


@pytest.mark.parametrize("nxt", ["//evil.example", "/\\evil.example", "/%09/evil.example"])
def test_login_ignores_next(tmp_path: Path, nxt: str) -> None:
    app = create_app(tmp_path)
    r = anon(app).get(
        "/api/auth/login", params={"token": app.state.token, "next": nxt}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_bad_token_is_403(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    r = anon(app).get("/api/auth/login?token=wrong")
    assert r.status_code == 403 and r.json()["error"] == "bad_token" and "set-cookie" not in r.headers
    assert anon(app).get("/api/auth/login").status_code == 403           # no token at all


def test_non_ascii_token_is_403_not_500(tmp_path: Path) -> None:
    r = anon(create_app(tmp_path), raise_server_exceptions=False).get("/api/auth/login?token=%C3%A9")
    assert r.status_code == 403


def test_ws_without_cookie_is_refused_before_accept(tmp_path: Path) -> None:
    c = anon(create_app(tmp_path))
    with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(WS) as ws:
        ws.receive_json()                  # would be the hello, had the socket been accepted
    assert e.value.code == 1008


def test_ws_with_cookie_gets_hello(tmp_path: Path) -> None:
    with authed(create_app(tmp_path)) as c, c.websocket_connect(WS) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_logout_clears_cookie_then_401(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with authed(app) as c:
        assert c.get("/api/about").status_code == 200
        r = c.post("/api/auth/logout")
        assert r.status_code == 200 and "Max-Age=0" in r.headers["set-cookie"]
        assert c.get("/api/about").status_code == 401
        assert c.get("/api/auth/status").json() == {"authenticated": False}


@pytest.mark.timeout(10)                        # a close that never comes must fail, not hang
def test_logout_closes_open_sockets(tmp_path: Path) -> None:
    with authed(create_app(tmp_path)) as c, c.websocket_connect(WS) as ws:
        assert ws.receive_json()["type"] == "hello"
        c.post("/api/auth/logout")
        with pytest.raises(WebSocketDisconnect):
            for _ in range(20):
                ws.receive_json()


@pytest.mark.timeout(10)
def test_logout_without_cookie_leaves_open_sockets_open(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    with authed(app) as c, c.websocket_connect(WS) as ws:
        while ws.receive_json()["type"] != "daemon":      # drain the greeting (it ends with daemon)
            pass
        assert anon(app).post("/api/auth/logout").status_code == 200
        body = {"server": "10.0.0.5:4501", "key": "aa:bb:cc:00:11:22", "name": "n", "description": ""}
        assert c.put("/api/port-labels", json=body).status_code == 200
        assert ws.receive_json()["type"] == "port_labels"      # still open and still receiving


def test_foreign_origin_without_cookie_is_403_not_401(tmp_path: Path) -> None:
    r = anon(create_app(tmp_path)).get("/api/about", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "forbidden_origin"


def test_cross_site_page_with_cookie_is_refused_by_origin(tmp_path: Path) -> None:
    with authed(create_app(tmp_path)) as c:
        r = c.get("/api/about", headers={"Origin": "http://evil.example"})
        assert r.status_code == 403 and r.json()["error"] == "forbidden_origin"


def test_two_backends_cookies_coexist(tmp_path: Path) -> None:
    a, b = create_app(tmp_path / "a", token="a" * 20), create_app(tmp_path / "b", token="b" * 20)
    ca = authed(a)
    cb = authed(b, cookies=ca.cookies)          # one jar; logging into b must not evict a's cookie
    assert {c.name for c in cb.cookies.jar} == {cookie_name("a" * 20), cookie_name("b" * 20)}
    jar = httpx.Cookies(cb.cookies)
    for app in (a, b):
        c = anon(app, cookies=jar)
        assert c.get("/api/about").status_code == 200
    # b's cookie is not a's
    assert anon(a, cookies={cookie_name("b" * 20): "b" * 20}).get("/api/about").status_code == 401


def test_cookie_of_another_backend_value_is_refused(tmp_path: Path) -> None:
    app = create_app(tmp_path, token="a" * 20)
    wrong = anon(app, cookies={cookie_name("a" * 20): "b" * 20})
    assert wrong.get("/api/about").status_code == 401


def test_create_app_without_token_is_closed(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    assert len(app.state.token) == 43
    assert anon(app).get("/api/about").status_code == 401


def test_docs_and_openapi_are_absent(tmp_path: Path) -> None:
    c = authed(create_app(tmp_path))
    assert [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [404, 404, 404]


def test_status_route_is_exempt(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    assert anon(app).get("/api/auth/status").json() == {"authenticated": False}
    with authed(app) as c:
        assert c.get("/api/auth/status").json() == {"authenticated": True}
