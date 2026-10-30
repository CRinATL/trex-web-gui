"""Reject cross-site browser requests (CSRF and cross-site WebSocket hijacking).

TrustedHostMiddleware stops DNS rebinding, but a page on any site can still aim a
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

from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
DEFAULT_PORTS = {"http": 80, "https": 443}


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
        `forbidden_origin` JSON, or a WebSocket close with 1008. A request without Origin, and other scope
        types, pass."""
        if scope["type"] in ("http", "websocket"):
            origin = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"origin"), None)
            if origin is not None and not origin_allowed(origin, self.ports):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                body = b'{"error":"forbidden_origin","message":"Cross-site request refused."}'
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
