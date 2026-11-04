"""Reject a foreign Host (DNS rebinding) and cross-site browser requests (CSRF and cross-site WebSocket
hijacking).

LoopbackHostMiddleware stops DNS rebinding, but a page on any site can still aim a
simple POST or a WebSocket at http://127.0.0.1:<port>: the Host header is then
loopback, browsers apply no CORS preflight to WebSockets or to body-less POSTs,
and the WebSocket handshake carries no CORS check at all. Browsers always send
Origin on those requests, so anything whose Origin is not a loopback http origin on an allowed port
is refused (see origin_allowed for which). Requests without Origin (curl, scripts) are not browser cross-site
requests and pass.

The port allow-list is load-bearing, not defence in depth. The access token (auth.py) travels in a
SameSite=Strict cookie, and SameSite does not split loopback ports: a page served by another local web server
at http://127.0.0.1:<other port> is same-site, so the browser sends it this backend's cookie. Only this Origin
check refuses it. Cookies are not scoped by port either, so any HTTP server the browser reaches at 127.0.0.1
can read the cookie: open other local web apps as http://localhost:<port>.
"""
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from trex_web_gui.api.bodies import err
from trex_web_gui.api.pages import for_browser, html_error, wants_html

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
DEFAULT_PORTS = {"http": 80, "https": 443}
BAD_HOST_MESSAGE = "Invalid host header"


def host_name(header: str) -> str:
    """The host of a Host header value without its port: `localhost:8765` is `localhost`, a bracketed IPv6
    address keeps its brackets (`[::1]:8765` is `[::1]`), and an empty header is the empty string."""
    if header.startswith("["):
        return header.partition("]")[0] + "]"
    return header.partition(":")[0]


class LoopbackHostMiddleware:
    """DNS-rebinding guard: the Host header must name a loopback host (any port). It replaces Starlette's
    TrustedHostMiddleware so that a browser navigation gets the HTML page of pages.py (400 `bad_host`) where
    a script still gets the plain-text 400 Starlette's sent."""
    def __init__(self, app: ASGIApp, hosts: frozenset[str] = LOOPBACK_HOSTS) -> None:
        """Keeps the set of host names accepted."""
        self.app, self.hosts = app, hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Passes an http or websocket request whose Host (port stripped) is in `hosts`; otherwise answers 400
        with the plain text "Invalid host header" (the HTML page for a browser navigation), or closes a
        WebSocket with 1008. A missing Host is refused. Other scope types pass."""
        if scope["type"] in ("http", "websocket"):
            header = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"host"), "")
            if host_name(header) not in self.hosts:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                if wants_html(scope):
                    await html_error(400, "bad_host", BAD_HOST_MESSAGE)(scope, receive, send)
                else:
                    await PlainTextResponse(BAD_HOST_MESSAGE, status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def origin_allowed(origin: str, ports: frozenset[int]) -> bool:
    """True for an http origin on 127.0.0.1 or localhost, with no path, whose port (the scheme's default
    when omitted) is in `ports`; False for anything else, a malformed port included."""
    try:
        u = urlsplit(origin)
        port = u.port                   # raises ValueError on a malformed port
    except ValueError:
        return False
    if port is None:                    # "http://localhost": the browser means the scheme's default port
        port = DEFAULT_PORTS.get(u.scheme)
    return u.scheme == "http" and u.hostname in LOOPBACK_HOSTS and not u.path and port in ports


class LoopbackOriginMiddleware:
    """Only the serving port, and the Vite dev port with --dev, are accepted (P1 parked item: any
    loopback port let another local web server drive this API). An empty set refuses every browser
    origin; only tests use that."""
    def __init__(self, app: ASGIApp, ports: frozenset[int]) -> None:
        """Keeps the set of ports whose origins are accepted."""
        self.app, self.ports = app, ports

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuses an http or websocket request that carries an Origin header `origin_allowed` rejects: 403
        `forbidden_origin` JSON (the HTML page of pages.py for a browser navigation), or a WebSocket close
        with 1008. A request without Origin, and other scope types, pass."""
        if scope["type"] in ("http", "websocket"):
            origin = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"origin"), None)
            if origin is not None and not origin_allowed(origin, self.ports):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                refusal = for_browser(scope, err(403, "forbidden_origin", "Cross-site request refused."))
                await refusal(scope, receive, send)
                return
        await self.app(scope, receive, send)
