"""The HTML error page for a browser navigation (api/pages.py): each refusal a person can reach by opening
an address in a tab answers the page when the request negotiates HTML, and the unchanged JSON (or, for the
Host gate, plain text) otherwise. The token never reaches the page."""
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from support import anon, authed
from trex_web_gui.api import pages
from trex_web_gui.api.app import create_app
from trex_web_gui.api.origin import host_name

# What Chrome and Firefox send for an address-bar, bookmark or link navigation.
BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"}
# A fetch() from a page that happens to ask for HTML: still a script, still JSON.
FETCH_HTML = {"Accept": "text/html", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"}
JSON = {"Accept": "application/json"}
UNAUTHENTICATED = {"error": "unauthenticated", "message": "Open the URL printed by trex-web-gui serve."}
STALE = "STALE-TOKEN-from-an-earlier-run-0123456789"


def page_of(r: httpx.Response) -> str:
    """The response's text after checking it is the HTML page: content type, lang, the project name and
    the GitHub link."""
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.startswith("<!doctype html>\n<html lang=\"en\">")
    assert "trex-web-gui" in r.text and pages.PROJECT_URL in r.text and pages.ISSUES_URL in r.text
    return r.text


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """An app over tmp_path without a built UI (so the framework's own 404 and 405 answer) and with a route
    that raises."""
    a = create_app(tmp_path / "data", origin_ports=frozenset({8765}))

    @a.get("/api/boom")
    def boom() -> None:
        """Raises, so the 500 handler answers."""
        raise RuntimeError("<b>kaput</b>")
    return a


def test_stale_login_url_in_a_tab_is_a_page_without_the_token(app: FastAPI) -> None:
    r = anon(app).get(f"/api/auth/login?token={STALE}", headers=BROWSER)
    assert r.status_code == 403 and "set-cookie" not in r.headers
    text = page_of(r)
    assert STALE not in text and "token=" + STALE[:5] not in text
    assert "403 bad_token" in text and "TREX_WEB_GUI_TOKEN" in text and pages.ACCESS_URL in text
    assert "This login link doesn" in text


@pytest.mark.parametrize("headers", [JSON, {}, FETCH_HTML])
def test_stale_login_url_from_a_script_is_the_same_json(app: FastAPI, headers: dict[str, str]) -> None:
    r = anon(app).get(f"/api/auth/login?token={STALE}", headers=headers)
    assert r.status_code == 403 and r.headers["content-type"] == "application/json"
    assert r.json() == {"error": "bad_token", "message": "That token does not match this backend."}


def test_bookmarked_api_path_without_a_session_is_a_401_page(app: FastAPI) -> None:
    r = anon(app).get("/api/about", headers=BROWSER)
    assert r.status_code == 401 and "401 unauthenticated" in page_of(r)
    assert "isn't signed in" in r.text and 'href="/"' in r.text


@pytest.mark.parametrize("headers", [JSON, {}, FETCH_HTML])
def test_401_json_is_unchanged(app: FastAPI, headers: dict[str, str]) -> None:
    r = anon(app).get("/api/about", headers=headers)
    assert r.status_code == 401 and r.headers["content-type"] == "application/json"
    assert r.json() == UNAUTHENTICATED and r.headers["content-length"] == str(len(r.content))


def test_foreign_origin_navigation_is_a_403_page(app: FastAPI) -> None:
    r = anon(app).get("/api/about", headers={**BROWSER, "Origin": "http://evil.example"})
    assert r.status_code == 403 and "403 forbidden_origin" in page_of(r)
    r = anon(app).get("/api/about", headers={**JSON, "Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden_origin", "message": "Cross-site request refused."}


def test_wrong_host_navigation_is_a_400_page_and_a_script_gets_plain_text(app: FastAPI) -> None:
    with authed(app) as c:
        r = c.get("/api/about", headers={**BROWSER, "Host": "0.0.0.0:8765"})
        assert r.status_code == 400 and "400 bad_host" in page_of(r) and "127.0.0.1" in r.text
        r = c.get("/api/about", headers={"Host": "[::1]:8765"})
        assert r.status_code == 400 and r.text == "Invalid host header"
        assert r.headers["content-type"].startswith("text/plain")
        assert c.get("/api/about", headers={"Host": "localhost:9999"}).status_code == 200
        assert c.get("/api/about", headers={"Host": "127.0.0.1"}).status_code == 200


def test_404_and_405_navigations_are_pages_and_keep_the_allow_header(app: FastAPI) -> None:
    with authed(app) as c:
        r = c.get("/api/nope", headers=BROWSER)                 # the framework's 404
        assert r.status_code == 404 and "404 not_found" in page_of(r)
        r = c.get("/api/session/connect", headers=BROWSER)      # POST-only: the framework's 405
        assert r.status_code == 405 and "405 method_not_allowed" in page_of(r)
        assert "POST" in r.headers["allow"]
        r = c.get("/api/session/connect")
        assert r.status_code == 405 and r.json()["error"] == "method_not_allowed"
        assert "POST" in r.headers["allow"]
        assert c.get("/api/nope").json()["error"] == "not_found"


def test_404_under_api_with_a_built_ui_is_a_page_too(tmp_path: Path) -> None:
    """With a built UI the catch-all route answers every GET; its /api branch negotiates as well."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>INDEX</html>")
    with authed(create_app(tmp_path / "data", static_dir=static)) as c:
        r = c.get("/api/nope", headers=BROWSER)
        assert r.status_code == 404 and "404 not_found" in page_of(r) and "/api/nope" in r.text
        assert c.get("/api/nope").json() == {"error": "not_found", "message": "No such API route: /api/nope"}
        assert "INDEX" in c.get("/some/client/route", headers=BROWSER).text


def test_unhandled_exception_navigation_is_a_500_page_with_the_message_escaped(app: FastAPI) -> None:
    with authed(app, raise_server_exceptions=False) as c:
        r = c.get("/api/boom", headers=BROWSER)
        assert r.status_code == 500 and "500 internal" in page_of(r)
        assert "RuntimeError: &lt;b&gt;kaput&lt;/b&gt;" in r.text and "<b>kaput</b>" not in r.text
        r = c.get("/api/boom")
        assert r.status_code == 500
        assert r.json() == {"error": "internal", "message": "RuntimeError: <b>kaput</b>"}


def test_validation_failure_navigation_is_the_generic_page(app: FastAPI) -> None:
    with authed(app) as c:
        r = c.get("/api/studio/profiles/x/export", headers=BROWSER)     # `format` is required
        assert r.status_code == 422 and "422 bad_request" in page_of(r) and "couldn" in r.text
        r = c.get("/api/studio/profiles/x/export")
        assert r.status_code == 422 and r.json()["error"] == "bad_request"


def test_the_page_is_self_contained_and_themed() -> None:
    text = pages.render(403, "bad_token", "msg")
    assert "<link" not in text and "@import" not in text and "url(" not in text
    assert text.count("<script>") == 1 and "trex-web-gui.theme" in text      # the GUI's theme pre-paint only
    assert "prefers-color-scheme:dark" in text and 'data-theme=contrast' in text
    assert '<meta name="viewport"' in text and "<h1>" in text and "<main>" in text


def test_an_unknown_code_gets_the_generic_page_and_the_message_escaped() -> None:
    text = pages.render(418, "teapot", "a <script> in the message & more")
    assert "418 teapot" in text and "a &lt;script&gt; in the message &amp; more" in text
    assert pages.GENERIC[0] in text


@pytest.mark.parametrize("headers, html", [
    ([(b"accept", b"text/html,application/xhtml+xml,*/*;q=0.8"), (b"sec-fetch-dest", b"document")], True),
    ([(b"accept", b"text/html")], True),                                 # no fetch metadata (curl, older)
    ([(b"accept", b"TEXT/HTML; q=0.9")], True),
    ([(b"accept", b"text/html"), (b"sec-fetch-dest", b"empty")], False),   # a fetch() asking for HTML
    ([(b"accept", b"text/html"), (b"sec-fetch-dest", b"iframe")], False),
    ([(b"accept", b"*/*")], False),
    ([(b"accept", b"application/json")], False),
    ([], False),
])
def test_wants_html(headers: list[tuple[bytes, bytes]], html: bool) -> None:
    assert pages.wants_html({"type": "http", "headers": headers}) is html


@pytest.mark.parametrize("header, name", [
    ("127.0.0.1:8765", "127.0.0.1"), ("localhost", "localhost"), ("[::1]:8765", "[::1]"), ("[::1]", "[::1]"),
    ("", ""), ("evil.example:80", "evil.example"),
])
def test_host_name(header: str, name: str) -> None:
    assert host_name(header) == name


def test_every_api_route_still_answers_json_to_the_test_client(app: FastAPI) -> None:
    """The negotiation never fires on `*/*`: the contract tests' client sees no page."""
    c: TestClient = anon(app)
    for path in ("/api/about", "/api/auth/login?token=x", "/api/nope"):
        assert c.get(path).headers["content-type"] == "application/json", path
