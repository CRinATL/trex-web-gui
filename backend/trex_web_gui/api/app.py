"""HTTP + WebSocket API (plan §4). Single user, loopback only (Revision 2)."""
import asyncio
import base64
import contextlib
import dataclasses
import logging
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from trex_web_gui import __version__, trexapi
from trex_web_gui.api.auth import TokenMiddleware, cookie_name, new_token, token_matches
from trex_web_gui.api.bodies import err
from trex_web_gui.api.bundle import bundle_router
from trex_web_gui.api.capture import MonitorBody, PacketBody, RecorderBody
from trex_web_gui.api.daemon import daemon_router
from trex_web_gui.api.declared import answers
from trex_web_gui.api.hub import Hub
from trex_web_gui.api.monitor import MONITOR_ONLY_MESSAGE, MonitorOnly, monitor_router, require_full_mode
from trex_web_gui.api.origin import LoopbackOriginMiddleware
from trex_web_gui.api.packet import packet_router
from trex_web_gui.api.port_config import LayerBody, PingBody, ScanBody
from trex_web_gui.api.studio import remove_expired_uploads, studio_router
from trex_web_gui.api.test_setups import test_setups_router
from trex_web_gui.daemon.session import START_TIMEOUT_MAX_S, DaemonSession
from trex_web_gui.install import install_kind
from trex_web_gui.monitor.session import MonitorSession
from trex_web_gui.sessions import ConnectParams, SessionBusy, TrexSession, wireshark
from trex_web_gui.store.bundle import Stores
from trex_web_gui.store.port_labels import LabelError, PortLabelStore
from trex_web_gui.store.settings import SettingsError, SettingsStore, parse_settings
from trex_web_gui.store.spool import SpoolBusy
from trex_web_gui.store.test_setups import TestSetupStore
from trex_web_gui.studio.library import ProfileLibrary, library_dir
from trex_web_gui.studio.templates import TemplateStore, templates_dir
from trex_web_gui.trexapi import decode, lldp, scapy_exec
from trex_web_gui.trexapi.packet import PacketEngine
from trex_web_gui.trexapi.profiles import MAX_RESULT
from trex_web_gui.validation import MULT, check_duration, check_ports

log = logging.getLogger(__name__)
SUPPORTED = "STL API 5.1 (TRex v3.08)"


class ConnectBody(BaseModel):
    host: str = Field(min_length=1)
    sync_port: int = Field(4501, ge=1, le=65535)
    async_port: int = Field(4500, ge=1, le=65535)
    timeout_s: float = Field(3.0, ge=0.5, le=60)
    read_only: bool = False


FS_LIST_MAX = 2000           # entries per /api/fs/list response
PCAP_SUFFIXES = (".pcap", ".cap")    # listed by /api/fs/list with include_pcap (the Studio's pcap import)
PROFILE_TIMEOUT_S = 10.0 # .py profile load budget (plan §4.3); fixtures load in ~0.2 s (measured 2026-10-16)


class Ports(BaseModel):
    ports: list[int] = Field(min_length=1, max_length=64)

    @field_validator("ports")
    @classmethod
    def unique(cls, v: list[int]) -> list[int]:
        """Field validator: rejects a duplicate or negative port id (check_ports)."""
        return check_ports(v)


class AcquireBody(Ports):
    force: bool = False


class ServiceBody(Ports):
    """Three states, as the vendored client has them (trex_client.py set_service_mode_base): off, on, and
    filtered (which needs enabled=false and a mask). The console's --no-tcp-udp is mask 1, the default."""
    enabled: bool
    filtered: bool = False
    mask: int | None = Field(default=None, ge=1, le=255)

    @model_validator(mode="after")
    def consistent(self) -> "ServiceBody":
        """Rejects `filtered` together with `enabled`, and `mask` without `filtered`; a filtered request
        with no mask gets mask 1 (mutates `self.mask`)."""
        if self.filtered and self.enabled:
            raise ValueError("filtered service mode needs enabled to be false")
        if not self.filtered and self.mask is not None:
            raise ValueError("mask applies only to filtered service mode")
        if self.filtered and self.mask is None:
            self.mask = 1
        return self


class AttrBody(Ports):
    promiscuous: bool | None = None
    multicast: bool | None = None
    link_up: bool | None = None
    led_on: bool | None = None
    flow_ctrl: int | None = Field(None, ge=0, le=3)


Tunables = list[Annotated[str, Field(max_length=1000)]]

class PortLabelBody(BaseModel):
    server: str
    key: str
    name: str
    description: str


class InspectBody(BaseModel):
    path: str = Field(min_length=1)
    ports: list[int] = Field(default_factory=lambda: [0], min_length=1, max_length=64)

    @field_validator("ports")
    @classmethod
    def unique(cls, v: list[int]) -> list[int]:
        """Field validator: rejects a duplicate or negative port id (check_ports)."""
        return check_ports(v)
    tunables: Tunables = Field(default_factory=list, max_length=100)


class AssignBody(Ports):
    path: str = Field(min_length=1)
    tunables: Tunables = Field(default_factory=list, max_length=100)
    stop_first: bool = False


class StartBody(Ports):
    mult: str = Field(pattern=MULT)
    duration: float = -1
    force: bool = False

    @field_validator("duration")
    @classmethod
    def dur(cls, v: float) -> float:
        """Accepts -1 (no limit) or 0 < seconds <= 604800 (one week); ValueError otherwise."""
        return check_duration(v)


class UpdateBody(Ports):
    mult: str = Field(pattern=MULT)


class ConsoleBody(BaseModel):
    enabled: bool
    include_polling: bool | None = None      # omitted keeps the session's current value


async def close_to_completion(s: TrexSession | MonitorSession | DaemonSession) -> None:
    """Run s.close() to completion even if we are cancelled meanwhile. TrexSession.close() sets _closed
    first, so a cancelled close() cannot be retried, and a cancelled disconnect can leave the client
    connected (an unclosed LOST client then blocks interpreter exit in ZMQ's context.term). So: shield,
    and keep waiting through cancellation; re-raise it afterwards."""
    task = asyncio.ensure_future(s.close())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
    task.result()
    if cancelled:
        raise asyncio.CancelledError


def create_app(data_dir: Path, server_hint: str | None = None, static_dir: Path | None = None, *,
               server_ports: tuple[int, int] | None = None,
               origin_ports: frozenset[int] = frozenset(), token: str | None = None,
               monitor_only: bool = False, monitor_target: tuple[str, int] | None = None,
               daemon_port: int = 8090) -> FastAPI:
    """Build the app. `token` None means generate one (fail closed: there is no "no gate" mode); the result is
    app.state.token. The CLI passes an env-or-new token (tests pass their own).

    `monitor_only` makes every route that changes state, and the daemon, profile, capture and Studio
    families, answer 409 monitor_only (require_full_mode). `monitor_target` (host, port) makes the lifespan
    connect the Monitor at startup, in either mode; hello carries both. `daemon_port` is the port hello's
    `daemon_hint` gives the Daemon dialog for the --server host (127.0.0.1 without one).

    Known limitation: shutdown waits for the session's executor, so shutting down
    while a connect is in flight waits up to timeout_s. Against a server that has gone silent, close()
    first makes new RPCs single-attempt (trexapi.fail_fast), but an RPC already in its retry loop (the
    subscriber thread's get_async_events, joined in disconnect) cannot be cut short without killing
    threads. Measured 2026-10-16 at timeout_s = 3 with SIGSTOP on the fake: 3.4 s when nothing was in
    flight, 8.5-11.6 s with an RPC in flight. That figure is measured after SIGINT, i.e. about
    12.6 s after the server went silent (P1 measured 12.3 s). It is
    logged at shutdown. A second Ctrl+C does not shorten either wait."""
    hub = Hub()
    store = SettingsStore(data_dir)
    label_store = PortLabelStore(data_dir)
    setup_store = TestSetupStore(data_dir)

    def profiles_dir() -> Path:
        """The profile library directory under the current preferences."""
        return library_dir(store.load().preferences, data_dir)

    library = ProfileLibrary(profiles_dir)
    # One packet engine per app (app.state.packet_engine, the test seam). Its worker starts on the first call,
    # which is always made from the event-loop thread (never an executor: the worker dies with the thread
    # that forked it), and the lifespan's shutdown closes it.
    packet_engine = PacketEngine()
    template_store = TemplateStore(lambda: templates_dir(store.load().preferences, data_dir))
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Startup builds the TrexSession (app.state.session), loads the settings and warms up the scapy
        executor, and removes expired Studio uploads; it builds the MonitorSession (app.state.monitor) and
        connects it when there is a monitor_target, and builds the DaemonSession (app.state.daemon). Shutdown,
        also after a cancelled lifespan, closes the Daemon session, then the Monitor, then logs the wait if a
        session is live, closes the session to completion (kills every Wireshark it launched, releases ports
        per the release_on_exit preference) and shuts the scapy executor down."""
        state["session"] = app.state.session = TrexSession(
            hub.publish, release_on_exit=lambda: store.load().preferences.release_on_exit,
            trex_cfg_path=lambda: store.load().preferences.trex_cfg_path, spool_dir=data_dir / "captures")
        state["monitor"] = app.state.monitor = MonitorSession(hub.publish)
        state["daemon"] = app.state.daemon = DaemonSession(hub.publish, session_owned)
        store.load()
        scapy_exec.warm_up()      # before any session connects: construction swaps sys.stdout
        remove_expired_uploads(data_dir)
        try:
            if monitor_target:
                try:
                    await state["monitor"].connect(*monitor_target)
                except ValueError as e:      # the CLI rejects these first; a caller of create_app may not
                    log.error("the Monitor target %s:%s was refused: %s", *monitor_target, e)
            yield
        except asyncio.CancelledError:
            # Double Ctrl+C: uvicorn skips lifespan shutdown on the second SIGINT, then re-raises SIGINT, so
            # asyncio.run cancels this task while Starlette waits for the shutdown message. Letting the
            # cancel out makes Starlette send lifespan.shutdown.failed with a CancelledError traceback, which
            # uvicorn logs as an error. This is a normal shutdown: clean up below, then end quietly.
            log.debug("lifespan cancelled before the shutdown message; closing the session")
        finally:
            try:
                await packet_engine.close()      # kills the packet worker's process group; never respawns
            except asyncio.CancelledError:
                log.debug("lifespan shutdown was cancelled; the packet engine was closed first")
            except Exception:       # the Daemon, Monitor and session below must still close
                log.exception("closing the packet engine failed; closing the rest anyway")
            if state["daemon"].snapshot()["busy"]:
                # canon:TSK-2: the wait below is silent otherwise. A Start in flight holds the close for up to
                # its timeout plus 5 s (305 s at the longest); a push or a stop for its own call deadline.
                log.warning("Waiting for an in-flight daemon operation to finish before exiting "
                            "(a Start can take up to %g s)", START_TIMEOUT_MAX_S + 5)
            try:
                await close_to_completion(state["daemon"])
            except asyncio.CancelledError:
                log.debug("lifespan shutdown was cancelled; daemon close had completed")
            except Exception:       # the Monitor and the session below must still close
                log.exception("closing the daemon session failed; closing the rest anyway")
            try:
                await close_to_completion(state["monitor"])
            except asyncio.CancelledError:
                log.debug("lifespan shutdown was cancelled; monitor close had completed")
            except Exception:       # the session below must still close
                log.exception("closing the Monitor failed; closing the session anyway")
            snap = state["session"].snapshot()
            if isinstance(snap["params"], dict) and snap["state"] in ("connecting", "connected", "lost"):
                # canon:TSK-2: the wait below is silent otherwise. An in-flight connect waits up to its
                # timeout_s; otherwise loss_bound is the nominal bound plus margin (trexapi/client.py),
                # measured 2026-10-16: 11.6 s at timeout_s = 3 against 14.5 s.
                t = float(snap["params"]["timeout_s"])
                log.warning("Waiting up to %g s for the TRex server to answer before exiting",
                            t if snap["state"] == "connecting" else trexapi.loss_bound(t))
            try:
                await close_to_completion(state["session"])
            except asyncio.CancelledError:
                # Guards an external cancel of the lifespan during the close (uvicorn 0.54's force-exit
                # skips lifespan shutdown instead, so it does not reach here). The close has completed
                # and this is the last thing the lifespan does. Re-raising would make Starlette/uvicorn
                # log "Application shutdown failed" with a traceback for a clean shutdown, so end
                # quietly. (close_to_completion itself still re-raises, for any other caller.)
                log.debug("lifespan shutdown was cancelled; session close had completed")
            scapy_exec.shutdown()

    token = token if token is not None else new_token()
    sockets: set[WebSocket] = set()      # accepted /api/ws sockets, closed by logout
    app = FastAPI(title="trex-web-gui", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(require_full_mode)])
    app.state.monitor_only = monitor_only
    app.state.hub = hub
    app.state.packet_engine = packet_engine
    app.state.token = token
    cookie = cookie_name(token)
    # Gate order (Starlette: the last add_middleware call is outermost): GZip, Origin, TrustedHost, Token.
    # The token gate is added first, so it is innermost and Origin runs before it.
    app.add_middleware(TokenMiddleware, token=token)
    # DNS-rebinding guard: only loopback names (the middleware ignores the port).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    # CSRF / cross-site WebSocket guard: browsers' cross-site requests carry a foreign Origin. Only the
    # serving port (and the Vite port with --dev) are accepted.
    app.add_middleware(LoopbackOriginMiddleware, ports=origin_ports)
    # HTTP only (the WebSocket negotiates permessage-deflate itself); bodies under 500 bytes go uncompressed.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.exception_handler(RequestValidationError)
    async def bad_request(_: Request, e: RequestValidationError) -> JSONResponse:
        """422 bad_request for a body or query that failed validation: the first three errors as `field:
        message`, with a count of the rest."""
        found = e.errors()
        where = [".".join(str(x) for x in d["loc"][1:]) or str(d["loc"][0]) for d in found]
        shown = "; ".join(f"{w}: {d['msg']}" for w, d in zip(where[:3], found[:3], strict=False))
        return err(422, "bad_request", shown + (f" (and {len(found) - 3} more)" if len(found) > 3 else ""))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
        """Framework HTTP errors (unknown route, wrong method, others) as `{error, message}` JSON, with the
        exception's status and headers."""
        # Framework 404/405 (unknown route, wrong method) would otherwise answer {"detail": ...}.
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return JSONResponse({"error": code, "message": str(e.detail)}, status_code=e.status_code,
                            headers=getattr(e, "headers", None))

    @app.exception_handler(MonitorOnly)
    async def monitor_only_refused(_: Request, e: MonitorOnly) -> JSONResponse:
        """409 monitor_only for a request that require_full_mode refused."""
        return err(409, "monitor_only", MONITOR_ONLY_MESSAGE)

    @app.exception_handler(Exception)
    async def internal(_: Request, e: Exception) -> JSONResponse:
        """500 `internal` carrying the exception's type and message for any unhandled exception; it does
        not log."""
        # No log call: ServerErrorMiddleware re-raises after this response and uvicorn logs the traceback.
        return err(500, "internal", f"{type(e).__name__}: {e}")

    def session() -> TrexSession:
        """The TrexSession that lifespan built; valid only once startup has run."""
        s: TrexSession = state["session"]
        return s

    def session_owned(host: str, sync_port: int) -> set[int] | None:
        """The ids of the ports the Control session owns when it is connected to host:sync_port, else None
        (what DaemonSession's port-owner check treats as this backend's own)."""
        snap = session().snapshot()
        params = snap["params"]
        if snap["state"] != "connected" or not isinstance(params, dict) \
                or params["host"] != host or params["sync_port"] != sync_port:
            return None
        return {p["id"] for p in session().ports() if p["owned"]}

    app.state.session_owned = session_owned

    def settings_json() -> dict[str, Any]:
        """The current settings, the store's load_error and the data dir: the body of GET and PUT
        /api/settings, and the payload of the `settings` WebSocket message."""
        s = store.load()
        return {"settings": dataclasses.asdict(s), "load_error": store.load_error, "data_dir": str(data_dir)}

    @app.get("/api/auth/login", response_model=None, status_code=303,
             responses=answers((403, "bad_token", "the token does not match")))
    def login(token: str = "") -> Response:
        """GET: with a matching token, a 303 to `/` that sets the session cookie (HttpOnly,
        SameSite=Strict); otherwise 403 bad_token. Reachable without the cookie (auth.EXEMPT)."""
        # Any `next` query is ignored: the redirect target is always "/".
        if not token_matches(app.state.token, token):
            return err(403, "bad_token", "That token does not match this backend.")
        r = RedirectResponse("/", status_code=303)
        r.set_cookie(cookie, token, path="/", httponly=True, samesite="strict")
        return r

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, bool]:
        """Whether the request's cookie holds this backend's token; always 200. Reachable without the
        cookie (auth.EXEMPT)."""
        return {"authenticated": token_matches(app.state.token, request.cookies.get(cookie))}

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> Response:
        """Deletes the cookie; when the request carried a valid cookie it also closes every accepted
        /api/ws socket with code 1008. Reachable without the cookie (auth.EXEMPT)."""
        r = JSONResponse({"authenticated": False})
        r.delete_cookie(cookie, path="/", httponly=True, samesite="strict")
        # a tab that logged out must not keep receiving; a cookieless caller cuts no one off
        if token_matches(app.state.token, request.cookies.get(cookie)):
            for sock in list(sockets):
                with contextlib.suppress(Exception):
                    await sock.close(code=1008)
        return r

    static_from = str(static_dir.resolve()) if static_dir and (static_dir / "index.html").exists() else None

    @app.get("/api/about")
    def about() -> dict[str, Any]:
        """The app version, the vendored TRex pin, the supported-server string, how this backend is installed
        (`install`: "wheel" or "source"), the directory the SPA is served from (`static_from`, or null)
        and the name of the computer running trex-web-gui (`host`)."""
        return {"app_version": __version__, "trex_pin": trexapi.pin(), "supported": SUPPORTED,
                "install": install_kind(), "static_from": static_from, "host": socket.gethostname()}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        """`{settings, load_error, data_dir}`: the current settings, the error from loading the settings file
        (null when it loaded), and the backend's data directory."""
        return settings_json()

    @app.put("/api/settings", response_model=None)
    async def put_settings(body: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        """Validates the body (parse_settings) and saves it; returns the saved settings and publishes
        `settings` to the hub. A save after a load error renames the bad file to
        settings.toml.bad-<stamp>. 422 invalid_settings for a bad body, 400
        invalid_settings when the store refuses it, 500 storage_error when the file cannot be written."""
        try:
            s = parse_settings(body)                    # same type checks as loading the file
        except SettingsError as e:
            return err(422, "invalid_settings", str(e))
        try:
            store.save(s)
        except SettingsError as e:
            return err(400, "invalid_settings", str(e))
        except OSError as e:
            return err(500, "storage_error", f"Cannot write settings: {e}")
        # async def on purpose, like put_port_labels: hub.publish is loop-thread only
        msg = settings_json()
        hub.publish({"type": "settings", **msg})
        return msg

    def labels_json() -> dict[str, Any]:
        """The port labels and the store's load_error: the body of GET /api/port-labels, the payload of the
        `port_labels` WebSocket message, and what PUT returns."""
        return {"labels": label_store.load(), "load_error": label_store.load_error}

    @app.get("/api/port-labels")
    async def get_port_labels() -> dict[str, Any]:
        """The port labels and any load error."""
        return labels_json()

    @app.put("/api/port-labels", response_model=None)
    async def put_port_labels(b: PortLabelBody) -> dict[str, Any] | JSONResponse:
        """Sets one port label (an empty name and description delete it) and publishes `port_labels` to the
        hub. 422 invalid_label for a rejected label, 500 storage_error when the file cannot be written."""
        # async def on purpose: the file is a few KB, so the load-modify-save runs on the loop thread, which
        # serialises two PUTs, and hub.publish (loop-thread only) is called from the right thread.
        try:
            labels = label_store.set(b.server, b.key, b.name, b.description)
        except LabelError as e:
            return err(422, "invalid_label", str(e))
        except OSError as e:
            return err(500, "storage_error", f"Cannot write port labels: {e}")
        hub.publish({"type": "port_labels", "labels": labels, "load_error": label_store.load_error})
        return {"labels": labels, "load_error": label_store.load_error}

    def setups_json() -> dict[str, Any]:
        """The `test_setups` WebSocket message: every setup and the files that failed to load."""
        setups = setup_store.list()
        return {"type": "test_setups", "setups": setups, "load_errors": setup_store.load_errors}

    app.include_router(test_setups_router(setup_store, hub.publish, setups_json))
    app.include_router(bundle_router(Stores(store, label_store, setup_store), __version__, hub.publish,
                                     settings_json, labels_json, setups_json))
    app.include_router(studio_router(library, data_dir))
    app.include_router(packet_router(packet_engine, template_store, lambda: store.load().preferences,
                                     data_dir))
    app.include_router(monitor_router(lambda: state["monitor"]))
    app.include_router(daemon_router(lambda: state["daemon"]))

    @app.get("/api/session")
    def get_session() -> dict[str, object]:
        """The session's snapshot."""
        return session().snapshot()

    @app.post("/api/session/connect", response_model=None)
    async def connect(body: ConnectBody) -> dict[str, Any] | JSONResponse:
        """Connects the session to a TRex server; returns {info: ServerInfo}. 409 busy while a session is
        connecting, connected or changing, 422 unsupported_version, 502 unreachable or trex_error."""
        try:
            params = ConnectParams(body.host, body.sync_port, body.async_port, body.timeout_s, body.read_only)
            info = await session().connect(params)
        except SessionBusy as e:
            return err(409, "busy", e.message)
        except trexapi.UnsupportedServerVersion as e:
            return err(422, "unsupported_version", e.message)
        except trexapi.ServerUnreachable as e:
            return err(502, "unreachable", e.message)
        except trexapi.TrexApiError as e:
            return err(502, "trex_error", e.message)
        return {"info": dataclasses.asdict(info)}

    @app.post("/api/session/disconnect")
    async def disconnect() -> dict[str, object]:
        """Disconnects the session: stops and removes its own monitor capture, releases its ports
        (TrexSession.disconnect default) and returns the snapshot."""
        await session().disconnect()
        return session().snapshot()

    async def run(coro: Awaitable[Any], ok: Callable[[Any], dict[str, Any]]) -> dict[str, Any] | JSONResponse:
        """Awaits a session command and answers `ok(result)`, or maps its error to a status: CommandRefused
        409 (422 for bad_request), ProfileError 422, ServerRestarted or ServerUnreachable 502, any other
        TrexApiError 400. The shared mapping for commands; connect and the capture-file routes map their
        own."""
        try:
            result = await coro
        except trexapi.CommandRefused as e:
            return err(422 if e.code == "bad_request" else 409, e.code, e.message)
        except trexapi.ProfileError as e:
            return err(422, "profile_error", e.message)
        except (trexapi.ServerRestarted, trexapi.ServerUnreachable) as e:
            return err(502, "unreachable", e.message)
        except trexapi.TrexApiError as e:
            return err(400, "trex_error", e.message)
        return ok(result)

    def with_ports(_: Any = None) -> dict[str, Any]:
        """The body every port command returns: the session's current port views. The argument is ignored,
        so this can serve as `run`'s `ok`."""
        return {"ports": session().ports()}

    def result_cap(path: str, ports: list[int]) -> int:
        """The worker result cap for loading `path` onto `ports`: MAX_RESULT_PCAP per port for a `.json`
        directly inside the current library directory (a pcap import can make one that large), else the
        load default (both paths resolved with realpath before comparing)."""
        real = os.path.realpath(os.path.expanduser(path))
        in_library = os.path.dirname(real) == os.path.realpath(profiles_dir())
        if in_library and real.lower().endswith(".json"):
            return trexapi.MAX_RESULT_PCAP * len(ports)
        return MAX_RESULT

    @app.get("/api/ports")
    def get_ports() -> dict[str, Any]:
        """The session's current port views (empty while disconnected)."""
        return with_ports()

    @app.post("/api/ports/acquire", response_model=None)
    async def acquire(b: AcquireBody) -> dict[str, Any] | JSONResponse:
        """Acquires the ports for this session, taking them from another owner when `force`. Refused (409)
        when not connected, read-only or for an unknown port."""
        return await run(session().acquire(b.ports, b.force), with_ports)

    @app.post("/api/ports/release", response_model=None)
    async def release(b: Ports) -> dict[str, Any] | JSONResponse:
        """Releases the ports. Refused (409) when not connected, read-only, for an unknown or unowned port,
        or while a ping or scan holds one (port_busy)."""
        return await run(session().release(b.ports), with_ports)

    @app.post("/api/ports/reacquire", response_model=None)
    async def reacquire() -> dict[str, Any] | JSONResponse:
        """Force-acquires the ports the server lists as this OS user's but held by no handler of this
        session; returns them as `reacquired` with the port views. Refused (409) when not connected or
        read-only."""
        return await run(session().reacquire(), lambda r: {"reacquired": r, **with_ports()})

    @app.post("/api/ports/service", response_model=None)
    async def service(b: ServiceBody) -> dict[str, Any] | JSONResponse:
        """Sets service mode off, on or filtered (with a mask) on the ports. Refused (409) when not
        connected, read-only, for an unknown or unowned port, or while a ping or scan holds one."""
        return await run(session().set_service(b.ports, b.enabled, b.filtered, b.mask), with_ports)

    @app.post("/api/ports/attr", response_model=None)
    async def attr(b: AttrBody) -> dict[str, Any] | JSONResponse:
        """Sets the port attributes given (promiscuous, multicast, link_up, led_on, flow_ctrl). 422
        bad_request when none is given; otherwise refused as `service` is."""
        return await run(session().set_port_attr(
            b.ports, promiscuous=b.promiscuous, multicast=b.multicast, link_up=b.link_up, led_on=b.led_on,
            flow_ctrl=b.flow_ctrl), with_ports)

    @app.post("/api/ports/reset", response_model=None)
    async def reset(b: Ports) -> dict[str, Any] | JSONResponse:
        """Resets the ports through the client: force-acquire, stop traffic, remove streams, clear stats,
        promiscuous (where supported), rx queue and service mode off, and remove EVERY capture on the
        server (the live monitor, unsaved recorders, other clients' captures), whatever ports are named.
        Also zeroes the reader's counters; the ports stay acquired. Refused (409) when not connected,
        read-only, for an unknown port, or while a ping or scan holds one."""
        return await run(session().reset(b.ports), with_ports)

    @app.post("/api/ports/layer", response_model=None)
    async def layer(b: LayerBody) -> dict[str, Any] | JSONResponse:
        """Applies an L2 or L3 configuration to one port inside a service-mode bracket (VLAN, MAC or IPv4,
        the ARP resolve for L3, optional IPv6); returns the ports, the ARP result and any
        service_restore_error. Holds the port busy meanwhile. Refused (409) when not connected, read-only,
        the port is unowned or transmitting, busy, or IPv6 is asked of a legacy stack."""
        return await run(session().apply_layer(b.port, b.request()), lambda r: {
            **with_ports(), "arp": None if r.arp is None else r.arp._asdict(),
            "service_restore_error": r.service_restore_error})

    @app.post("/api/ports/ping", response_model=None)
    async def ping(b: PingBody) -> dict[str, Any] | JSONResponse:
        """Pings `dst` from one port inside a service-mode bracket, publishing each echo as a `ping`
        message and the port as busy; returns the run id, the lines and any service_restore_error. An echo
        that is refused or fails ends the ping with its own line; a lost connection mid-ping is 502
        unreachable with no final line. Refused (409) up front as `layer` is, bar IPv6."""
        return await run(session().ping(b.port, b.dst, b.count, b.pkt_size), lambda r: {
            "run": r.run, "lines": [x._asdict() for x in r.lines],
            "service_restore_error": r.service_restore_error})

    @app.post("/api/ports/scan6", response_model=None)
    async def scan6(b: ScanBody) -> dict[str, Any] | JSONResponse:
        """Scans one port for IPv6 neighbours inside a service-mode bracket, holding it busy; returns the
        neighbours and any service_restore_error. Refused (409) as `layer` is, bar IPv6."""
        return await run(session().scan(b.port), lambda r: {
            "neighbours": [x._asdict() for x in r.neighbours],
            "service_restore_error": r.service_restore_error})

    @app.get("/api/ports/{pid}/xstats", response_model=None)
    async def xstats(pid: int) -> dict[str, Any] | JSONResponse:
        """Reads one port's extended stats: no ownership needed and no refresh. Refused (409) when not
        connected or for an unknown port."""
        return await run(session().xstats(pid), lambda v: {"port": v.port, "values": v.values})

    @app.post("/api/profiles/inspect", response_model=None)
    async def inspect(b: InspectBody) -> dict[str, Any] | JSONResponse:
        """Loads a profile in the profile worker subprocess for these ports and tunables and returns its
        summary; touches no server and no session. 422 profile_error when it cannot be loaded or exceeds
        10 seconds."""
        return await run(
            trexapi.load_profile(b.path, b.ports, b.tunables, timeout_s=PROFILE_TIMEOUT_S,
                                 max_result=result_cap(b.path, b.ports)),
            lambda lp: {"path": lp.path, "kind": lp.kind, "tunables": lp.tunables, "summary": lp.summary,
                        "stream_count": lp.stream_count})

    @app.post("/api/ports/assign", response_model=None)
    async def assign(b: AssignBody) -> dict[str, Any] | JSONResponse:
        """Replaces the streams on the ports with a profile's: loads it in the worker, adds the streams,
        validates and returns the summary, per-port rates and the port views. `stop_first` stops
        transmitting ports; otherwise they are refused. Refused (409) before the worker runs when not
        connected, read-only, for an unowned port or one still transmitting; 422 profile_error for a bad
        profile."""
        async def go() -> Any:
            """Refuses first (precheck), then loads the profile and assigns it; returns the loaded profile
            and the per-port rates."""
            # refuse (not_owned, port_active, read_only) before running the profile subprocess
            await session().precheck_assign(b.ports, b.stop_first)
            lp = await trexapi.load_profile(b.path, b.ports, b.tunables, timeout_s=PROFILE_TIMEOUT_S,
                                            max_result=result_cap(b.path, b.ports))
            rates = await session().assign_profile(b.ports, lp, b.stop_first, b.tunables)
            return lp, rates
        return await run(go(), lambda r: {"summary": r[0].summary, "stream_count": r[0].stream_count,
                                          "rate": {str(p): v for p, v in r[1].items()}, **with_ports()})

    @app.get("/api/ports/{pid}/assigned", response_model=None)
    async def assigned(pid: int) -> dict[str, Any] | JSONResponse:
        """What is on the port: `streams`, whether its default profile holds streams, and `assigned`, this
        connection's last Assign to it (path, name, tunables, summary, stream_count), or null when there is
        none or the port no longer holds streams or is no longer owned. Start sends what is on the port, so
        the Traffic tab shows this, not the path being edited. Refused (409) when not connected or for an
        unknown port."""
        return await run(session().assigned(pid), lambda r: r)

    @app.post("/api/ports/start", response_model=None)
    async def start(b: StartBody) -> dict[str, Any] | JSONResponse:
        """Starts traffic on the ports at `mult`, for `duration` seconds or until stopped (-1). Refused
        (409) when not connected, read-only, for an unowned port, or while a ping or scan holds one."""
        return await run(session().start(b.ports, b.mult, b.duration, b.force), with_ports)

    @app.post("/api/ports/update", response_model=None)
    async def update(b: UpdateBody) -> dict[str, Any] | JSONResponse:
        """Changes the multiplier of ports that are transmitting. Refused (409) when not connected,
        read-only, for an unowned port, or one whose default profile is not transmitting."""
        return await run(session().update(b.ports, b.mult), with_ports)

    @app.post("/api/ports/pause", response_model=None)
    async def pause(b: Ports) -> dict[str, Any] | JSONResponse:
        """Pauses transmitting ports. Refused (409) when not connected, read-only, for an unowned port, or
        one whose default profile is not transmitting."""
        return await run(session().pause(b.ports), with_ports)

    @app.post("/api/ports/resume", response_model=None)
    async def resume(b: Ports) -> dict[str, Any] | JSONResponse:
        """Resumes paused ports. Refused (409) when not connected, read-only, for an unowned port, or one
        whose default profile is not paused."""
        return await run(session().resume(b.ports), with_ports)

    @app.post("/api/ports/stop", response_model=None)
    async def stop(b: Ports) -> dict[str, Any] | JSONResponse:
        """Stops every profile the client knows on the ports. Refused (409) when not connected, read-only,
        or for an unowned port."""
        return await run(session().stop(b.ports), with_ports)

    @app.get("/api/ports/{pid}/rate", response_model=None)
    async def rate(pid: int) -> dict[str, Any] | JSONResponse:
        """Validates the port's default profile on the server and returns its rate info. The port must
        be owned (409 not_owned, so always in a read-only session). 400 trex_error when the server rejects
        the streams."""
        return await run(session().rate_info(pid), lambda r: {"rate": r})

    @app.get("/api/capture", response_model=None)
    async def get_capture() -> dict[str, Any] | JSONResponse:
        """The capture snapshot, after a refresh from the server while connected."""
        async def go() -> None:
            """Refreshes the capture status from the server; no RPC unless the session is connected."""
            await session().capture_refresh()            # only while CONNECTED; otherwise no RPC
        return await run(go(), lambda _: session().capture_snapshot())

    @app.post("/api/capture/monitor", response_model=None)
    async def monitor_start(b: MonitorBody) -> dict[str, Any] | JSONResponse:
        """Starts the live capture monitor (a cyclic capture of `limit` packets) and the capture loop, and
        publishes the capture state; returns the monitor. Refused (409) when one is running, an Rx port is
        not in service mode, server capture slots are full, or not connected or read-only; 409 bad_port
        for a port the server does not have. Starting it also ends any Wireshark pipe and empties the LLDP
        table."""
        return await run(session().monitor_start(b.tx, b.rx, b.bpf, b.limit),
                         lambda m: {"monitor": dataclasses.asdict(m)})

    @app.delete("/api/capture/monitor", response_model=None)
    async def monitor_stop() -> dict[str, Any] | JSONResponse:
        """Stops the live monitor with a capture-stop RPC, drains its remaining packets with destructive
        fetches (published as `capture_pkts`), removes the capture from the server, ends its Wireshark pipe
        and LLDP table, and publishes the capture state. Refused (409) when no monitor runs, not connected
        or read-only."""
        return await run(session().monitor_stop(), lambda _: {})

    @app.post("/api/capture/recorders", response_model=None)
    async def recorder_start(b: RecorderBody) -> dict[str, Any] | JSONResponse:
        """Starts a fixed-size recorder capture of `limit` packets; returns its id. Refused (409) as
        `monitor_start` is, bar a running monitor."""
        return await run(session().recorder_start(b.tx, b.rx, b.bpf, b.limit), lambda i: {"id": i})

    @app.post("/api/capture/recorders/{cid}/stop", response_model=None)
    async def recorder_stop(cid: int) -> dict[str, Any] | JSONResponse:
        """Stops one recorder capture; returns its packet count. Refused (409) for the monitor's id, a
        capture being saved, an endpoint capture, or when not connected or read-only."""
        return await run(session().recorder_stop(cid), lambda n: {"pkt_count": n})

    @app.post("/api/capture/recorders/{cid}/save", response_model=None)
    async def recorder_save(cid: int) -> dict[str, Any] | JSONResponse:
        """Stops the recorder, then fetches its packets (a destructive fetch) into a pcap file in the
        capture directory, publishing `capture_save` progress, and removes the capture from the server
        after the file is written; returns the file. A failed save keeps any packets already written.
        Refused (409) for the monitor's id, a capture already being saved, an endpoint capture, no capture
        directory, a file write failure (spool_error), or when not connected or read-only."""
        return await run(session().recorder_save(cid), lambda f: {"file": f})

    @app.delete("/api/capture/recorders/{cid}", response_model=None)
    async def recorder_remove(cid: int) -> dict[str, Any] | JSONResponse:
        """Removes one recorder capture from the server. Refused (409) for the monitor's id, a capture
        being saved, or when not connected or read-only."""
        return await run(session().recorder_remove(cid), lambda _: {})

    # async on purpose: the table is the session's, read and pruned on the loop thread only.
    @app.get("/api/capture/lldp")
    async def capture_lldp() -> dict[str, Any]:
        """The current capture monitor's LLDP neighbours whose TTL has not run out, with the module's note
        on what they show: `{neighbours, note}`."""
        return {"neighbours": session().lldp_neighbours(), "note": lldp.NOTE}

    @app.post("/api/capture/packet/tree", response_model=None)
    async def packet_tree(b: PacketBody) -> dict[str, Any] | JSONResponse:
        """Dissects one base64 frame with Scapy on the scapy executor and returns the tree. 400 bad_packet
        when dissection raises."""
        try:      # tree() can raise on odd bytes (Scapy_service)
            tree: dict[str, Any] = await scapy_exec.arun(decode.tree, base64.b64decode(b.binary))
        except Exception as e:
            return err(400, "bad_packet", f"The packet could not be dissected: {type(e).__name__}: {e}")
        return tree

    # The Wireshark path is the saved preference, read here; never a request's (a body is ignored).
    @app.get("/api/capture/wireshark/preflight")
    def wireshark_preflight() -> dict[str, Any]:
        """Whether the saved Wireshark path can be launched: `{ok, reason}`. The request supplies nothing."""
        ok, reason = wireshark.preflight(store.load().preferences.wireshark_path)
        return {"ok": ok, "reason": reason}

    @app.post("/api/capture/wireshark", response_model=None)
    async def wireshark_start() -> dict[str, Any] | JSONResponse:
        """Launches Wireshark (a child process) on the monitor's packets, using the saved path; a pipe
        already running is kept. Returns the capture snapshot. Refused (409) when no monitor runs, not
        connected, or Wireshark cannot be launched."""
        return await run(session().wireshark_start(store.load().preferences.wireshark_path),
                         lambda _: session().capture_snapshot())

    @app.delete("/api/capture/wireshark", response_model=None)
    async def wireshark_stop() -> dict[str, Any] | JSONResponse:
        """Closes the Wireshark pipe, leaving its window open; returns the capture snapshot."""
        return await run(session().wireshark_stop(), lambda _: session().capture_snapshot())

    @app.get("/api/capture/files", response_model=None)
    async def capture_files() -> dict[str, Any] | JSONResponse:
        """Lists the files in the capture directory. 409 spool_unavailable when the backend has none."""
        try:
            spool = session().spool()
        except trexapi.CommandRefused as e:
            return err(409, e.code, e.message)
        files = spool.list()
        return {"dir": str(spool.dir), "files": files, "total": len(files)}

    @app.get("/api/capture/files/{name}", response_model=None)
    async def capture_file(name: str) -> FileResponse | JSONResponse:
        """Serves one capture file as a pcap download. 409 spool_unavailable, 400 bad_name for an unusable
        name, 404 when there is no such file."""
        try:
            path = session().spool().path(name)
        except trexapi.CommandRefused as e:
            return err(409, e.code, e.message)
        except ValueError as e:
            return err(400, "bad_name", str(e))
        if not path.is_file():
            return err(404, "not_found", f"No capture file {name}")
        return FileResponse(path, media_type="application/vnd.tcpdump.pcap", filename=name)

    @app.delete("/api/capture/files/{name}", response_model=None)
    async def capture_file_delete(name: str) -> dict[str, Any] | JSONResponse:
        """Deletes one capture file. 409 spool_unavailable, or capture_busy while a Save is writing it; 400
        bad_name; 404 when there is no such file."""
        try:
            session().spool().delete(name)
        except trexapi.CommandRefused as e:
            return err(409, e.code, e.message)
        except ValueError as e:
            return err(400, "bad_name", str(e))
        except SpoolBusy:
            return err(409, "capture_busy", f"{name} is being written by a Save.")
        except FileNotFoundError:
            return err(404, "not_found", f"No capture file {name}")
        return {}

    @app.post("/api/console")
    def console(b: ConsoleBody) -> dict[str, Any]:
        """Turns the raw JSON-RPC console on or off (`include_polling` None keeps its value), logs the
        change and publishes the session snapshot. Needs no connection."""
        s = session()
        s.set_rpc_console(b.enabled, b.include_polling)
        return {"enabled": b.enabled, "include_polling": s.snapshot()["include_polling"]}

    @app.get("/api/fs/list", response_model=None)
    def fs_list(path: str | None = None, include_pcap: bool = False) -> dict[str, Any] | JSONResponse:
        """Localhost single-user file browser for profile paths. Lists the directory `path` (when empty, the
        Settings workspace directory, else the backend user's home): subdirectories first, then files with
        the suffixes .py .yaml .yml .json, plus .pcap .cap with `include_pcap`; dotfiles are hidden and at
        most 2000 entries are listed. Returns `{path, parent, entries: [{name, dir, size}], truncated}`
        (`parent` is null at the root; `size` is null for a directory or an unreadable file). 404 not_found
        when `path` is not a directory, 403 forbidden for a directory the backend may not read, 400
        bad_path for a path that cannot be resolved."""
        suffixes = trexapi.PROFILE_SUFFIXES + (PCAP_SUFFIXES if include_pcap else ())
        base = path or store.load().preferences.workspace_dir or str(Path.home())
        try:
            d = Path(base).expanduser().resolve()
            if not d.is_dir():
                return err(404, "not_found", f"{d} is not a directory")
            entries: list[dict[str, Any]] = []
            truncated = False
            for e in sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
                if e.name.startswith("."):
                    continue
                if e.is_dir():
                    item: dict[str, Any] = {"name": e.name, "dir": True, "size": None}
                elif e.suffix.lower() in suffixes:
                    try:
                        size: int | None = e.stat().st_size
                    except OSError:                  # e.g. a broken symlink: keep the entry, size unknown
                        size = None
                    item = {"name": e.name, "dir": False, "size": size}
                else:
                    continue
                if len(entries) >= FS_LIST_MAX:      # cap the response; the UI shows "truncated"
                    truncated = True
                    break
                entries.append(item)
        except PermissionError as e:
            return err(403, "forbidden", str(e))
        except (OSError, ValueError) as e:
            return err(400, "bad_path", str(e))
        return {"path": str(d), "parent": str(d.parent) if d.parent != d else None, "entries": entries,
                "truncated": truncated}

    @app.websocket("/api/ws")
    async def ws(sock: WebSocket) -> None:
        """The /api/ws socket: sends hello, the session snapshot, the ports when there are any, busy,
        port_labels, capture, monitor, test_setups and daemon, then forwards hub messages until either side
        closes.
        Registered in `sockets` so logout can close it; a failed task closes it with 1011."""
        await sock.accept()
        sockets.add(sock)
        q = hub.subscribe()
        ready = asyncio.Event()          # pump waits, so hello + snapshot always go out first

        async def pump() -> None:
            """Forwards hub messages to the socket once `ready` is set; ends quietly when a logout closed
            the socket mid-send."""
            await ready.wait()
            while True:
                msg = await q.get()
                try:
                    await sock.send_json(msg)
                except RuntimeError:       # a logout closed the socket mid-send: not a failure to log
                    return

        async def watch_close() -> None:      # notice a closed tab now, not at the next send
            while True:
                if (await sock.receive())["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(pump()), asyncio.create_task(watch_close())]
        hello_target = {"host": monitor_target[0], "port": monitor_target[1]} if monitor_target else None
        daemon_hint = {"host": server_hint or "127.0.0.1", "port": daemon_port}
        hello_ports = {"sync_port": server_ports[0], "async_port": server_ports[1]} if server_ports else None
        try:
            await sock.send_json({"type": "hello", "app_version": __version__, "server_hint": server_hint,
                                  "server_ports": hello_ports, "monitor_only": monitor_only,
                                  "monitor_target": hello_target, "daemon_hint": daemon_hint})
            await sock.send_json(session().snapshot())
            if ports := session().ports():       # empty while disconnected; P1's order is unchanged
                await sock.send_json({"type": "ports", "ports": ports})
            # busy and port_labels are always sent, even when empty: a reconnecting tab must learn
            # that a ping finished (busy) or a label was deleted (port_labels) while it was away
            await sock.send_json({"type": "busy", "ports": session().busy()})
            await sock.send_json({"type": "port_labels", **labels_json()})
            await sock.send_json(session().capture_snapshot())
            await sock.send_json(state["monitor"].snapshot())
            await sock.send_json(setups_json())
            await sock.send_json(state["daemon"].snapshot())
            ready.set()
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            sockets.discard(sock)
            hub.unsubscribe(q)
            for t in tasks:
                t.cancel()
            # asyncio.wait, not gather: a gather cancelled from outside (Starlette's TestClient cancels the
            # app right after the client closes) can end the app task as cancelled instead of letting its
            # scope absorb it, so the test failed at teardown with CancelledError (~0.3% of runs under load).
            await asyncio.wait(tasks)
            for t in tasks:
                r = None if t.cancelled() else t.exception()
                expected = r is None or isinstance(r, WebSocketDisconnect)
                if r is not None and not expected:
                    log.error("WebSocket task failed; closing the socket", exc_info=r)
                    with contextlib.suppress(Exception):      # the socket may already be gone
                        await sock.close(code=1011)

    if static_dir and (static_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", response_model=None)
        def spa(path: str) -> FileResponse | JSONResponse:      # client-side routes fall back to index.html
            if path == "api" or path.startswith("api/"):
                return err(404, "not_found", f"No such API route: /{path}")
            root = static_dir.resolve()
            try:
                f = (root / path).resolve()
                served = bool(path) and f.is_relative_to(root) and f.is_file()
            except (OSError, ValueError):       # NUL byte (ValueError) or an over-long segment (ENAMETOOLONG)
                served = False
            return FileResponse(f if served else root / "index.html")

    return app
