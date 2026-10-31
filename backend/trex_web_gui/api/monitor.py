"""The Monitor's routes (spec §6.1) and the `--monitor-only` guard (spec §6.2)."""
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.requests import HTTPConnection
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from trex_web_gui.api.bodies import err
from trex_web_gui.api.declared import answers
from trex_web_gui.monitor.host import check_monitor_host
from trex_web_gui.monitor.session import MonitorHostRefused, MonitorSession
from trex_web_gui.validation import check_host

# Refused whole, with any method (spec §6.2: daemon, profiles, "captures, Wireshark, downloads", Studio;
# and the host file browser, which no Monitor-only page uses).
FAMILIES = ("/api/daemon", "/api/profiles", "/api/capture", "/api/studio", "/api/fs")
WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})
# A write under these answers for itself: the Monitor's own routes, and the token gate's.
WRITE_EXEMPT = ("/api/monitor", "/api/auth")
MONITOR_ONLY_MESSAGE = "This backend runs with --monitor-only"


class MonitorOnly(Exception):
    """Raised by `require_full_mode`; app.py answers it with 409 `monitor_only`."""


def under(path: str, prefix: str) -> bool:
    """Whether `path` equals `prefix` or lies below it (`/api/capture` yes, `/api/capture-x` no)."""
    return path == prefix or path.startswith(prefix + "/")


def refused_in_monitor_only(method: str, path: str) -> bool:
    """Whether a backend run with --monitor-only refuses this request: every method on a whole family,
    and any write outside the Monitor's and the token gate's routes. Reads of session, ports, settings and
    test setups stay live."""
    if any(under(path, f) for f in FAMILIES):
        return True
    return method in WRITE_METHODS and not any(under(path, p) for p in WRITE_EXEMPT)


async def require_full_mode(conn: HTTPConnection) -> None:
    """App-wide dependency (declared in `dependencies=[…]`, so it runs before body validation): raises
    MonitorOnly when the backend runs with --monitor-only and the request is one it refuses. A WebSocket
    carries no method and is never refused."""
    method = conn.scope.get("method")
    if conn.app.state.monitor_only and isinstance(method, str) \
            and refused_in_monitor_only(method, conn.url.path):
        raise MonitorOnly


class ConnectBody(BaseModel):
    host: str
    port: int = Field(4500, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def valid_host(cls, v: str) -> str:
        """Field validator: a hostname, IPv4 address or bare IPv6 literal (check_host), whose dotted labels
        start and end with a letter or digit (check_monitor_host)."""
        return check_monitor_host(check_host(v))


def monitor_router(monitor: Callable[[], MonitorSession]) -> APIRouter:
    """The three /api/monitor routes over the lifespan's MonitorSession; each answers its snapshot."""
    router = APIRouter()

    @router.get("/api/monitor")
    async def get_monitor() -> dict[str, Any]:
        """The Monitor's snapshot."""
        return monitor().snapshot()

    @router.post("/api/monitor/connect", response_model=None,
                 responses=answers((422, "invalid", "libzmq refuses the endpoint")))
    async def connect(b: ConnectBody) -> dict[str, Any] | JSONResponse:
        """Point the Monitor at host:port (replacing any earlier target); returns the snapshot. 422 invalid
        when libzmq refuses the endpoint."""
        try:
            return await monitor().connect(b.host, b.port)
        except MonitorHostRefused as e:
            return err(422, "invalid", str(e))

    @router.post("/api/monitor/disconnect")
    async def disconnect() -> dict[str, Any]:
        """Stop the Monitor and clear its data; returns the snapshot."""
        return await monitor().disconnect()

    return router
