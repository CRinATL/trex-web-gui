"""The per-launch access token (P3c Task 0).

`trex-web-gui serve` makes one token per process (or takes it from TREX_WEB_GUI_TOKEN) and prints a login URL.
The login route sets a session cookie named for the token; every /api route and every WebSocket then needs
that cookie, so a browser that was never given the URL (another user on the host, a tab left open across a
restart) is refused. The token has no persistent copy: it lives as long as the process.

What this does not do: the cookie value is the token, and a browser sends a cookie to every server on the
same host, whatever the port. Any HTTP server the browser reaches at 127.0.0.1 can therefore read it, so
open other local web apps as http://localhost:<port>, not http://127.0.0.1:<port>. The Origin gate
(origin.py) is what stops a page on another loopback port from using it against this backend."""
import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
from collections.abc import MutableMapping
from urllib.parse import unquote_plus

from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

MIN_ENV_TOKEN = 16
ENV_VAR = "TREX_WEB_GUI_TOKEN"
URLSAFE = re.compile(r"[A-Za-z0-9_-]+")
LOGIN_PATH = "/api/auth/login"
EXEMPT = frozenset({LOGIN_PATH, "/api/auth/status", "/api/auth/logout"})
UNAUTHENTICATED_BODY = (b'{"error":"unauthenticated",'
                        b'"message":"Open the URL printed by trex-web-gui serve."}')


def new_token() -> str:
    """A fresh URL-safe token from 32 random bytes."""
    return secrets.token_urlsafe(32)


def token_from_env_or_new(env: MutableMapping[str, str] = os.environ) -> str:
    """The token from TREX_WEB_GUI_TOKEN, or a new one when it is unset or empty; exits with status 2 when it
    is too short or has characters outside letters, digits, `-` and `_`. Pops the variable, so that no child
    process (the profile worker, Wireshark) inherits it."""
    given = env.pop(ENV_VAR, "")
    if not given:
        return new_token()
    if len(given) < MIN_ENV_TOKEN:
        print(f"{ENV_VAR} is too short (minimum {MIN_ENV_TOKEN} characters)", file=sys.stderr)
        raise SystemExit(2)
    if not URLSAFE.fullmatch(given):
        print(f"{ENV_VAR} may contain only letters, digits, '-' and '_'", file=sys.stderr)
        raise SystemExit(2)
    return given


def cookie_name(token: str) -> str:
    """Per token, so two backends reached at one host (cookies ignore ports) keep separate cookies."""
    return "trex_web_gui_" + hashlib.sha256(token.encode()).hexdigest()[:8]


def token_matches(token: str, given: str | None) -> bool:
    """True when `given` equals `token`, compared in constant time; False for None."""
    if given is None:
        return False
    return hmac.compare_digest(token.encode(), given.encode("utf-8", "replace"))


def login_url(host: str, port: int, token: str) -> str:
    """The login route's URL for this host and port, with the token as its query parameter."""
    return f"http://{host}:{port}{LOGIN_PATH}?token={token}"


def _cookie_value(scope: Scope, name: str) -> str | None:
    """The value of cookie `name` in the request's Cookie headers, or None."""
    header = "; ".join(v.decode("latin-1") for k, v in scope["headers"] if k == b"cookie")
    return cookie_parser(header).get(name)


class TokenMiddleware:
    """Innermost gate: Host and Origin are checked before it (see create_app)."""
    def __init__(self, app: ASGIApp, token: str) -> None:
        """Keeps the token and derives its cookie name."""
        self.app, self.token = app, token
        self.cookie = cookie_name(token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Passes an http or websocket request that `_allowed` accepts; otherwise answers 401
        `unauthenticated` JSON (http) or closes with 1008 (websocket). Other scope types pass untouched."""
        kind = scope["type"]
        if kind in ("http", "websocket") and not self._allowed(scope):
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(UNAUTHENTICATED_BODY)).encode())]})
            await send({"type": "http.response.body", "body": UNAUTHENTICATED_BODY})
            return
        await self.app(scope, receive, send)

    def _allowed(self, scope: Scope) -> bool:
        """True for http paths outside /api (the page must load to log in) and the EXEMPT auth paths; every
        other request, WebSocket included, needs the token cookie."""
        path: str = scope["path"]
        if scope["type"] == "http" and not (path == "/api" or path.startswith("/api/")):
            return True                      # static files and the SPA fallback: the page must load to log in
        if scope["type"] == "http" and path in EXEMPT:
            return True
        return token_matches(self.token, _cookie_value(scope, self.cookie))


class RedactLoginToken(logging.Filter):
    """uvicorn's access line carries the request path with its query as the third %s argument
    (uvicorn/protocols/http/h11_impl.py, get_path_with_query_string). For the login path, every `token`
    parameter, at any position, is rewritten to <redacted> before the line is formatted."""
    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrites the access-log record's request-target argument through `_redact`; always returns True,
        so no record is dropped."""
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], self._redact(args[2]), *args[3:])
        return True

    @staticmethod
    def _redact(target: str) -> str:
        """`target` with the value of every `token` query parameter replaced by <redacted> when its path is
        the login path and it has a query; any other target comes back unchanged."""
        path, sep, query = target.partition("?")
        if path != LOGIN_PATH or not sep:
            return target
        parts = []
        for part in query.split("&"):
            key = part.partition("=")[0]
            parts.append(f"{key}=<redacted>" if unquote_plus(key) == "token" else part)
        return path + "?" + "&".join(parts)
