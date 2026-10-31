"""The /api/daemon routes (spec §6.4, D1-D3, D5, D13) over DaemonSession.

The body models apply the session's own rules (cfg_path, options, trex_args, the STF bounds, the push file
name), so a bad body is a 422 before any daemon call; the session checks them again where a refusal is a
409. Every route sits behind the token gate and `require_full_mode`."""
import inspect
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from trex_web_gui.api.bodies import err
from trex_web_gui.api.declared import Item, answers
from trex_web_gui.daemon.client import DaemonFault, DaemonUnreachable, StfOptions
from trex_web_gui.daemon.config import MAX_YAML_BYTES
from trex_web_gui.daemon.session import (
    _FILENAME_RE,
    DaemonRefused,
    DaemonSession,
    _check_cfg_and_options,
    _check_stf,
    _check_trex_args,
)
from trex_web_gui.stfyaml import TG_YAML_RE
from trex_web_gui.validation import check_host

PROFILE_MAX_CHARS = 300
UPLOAD_PATH_MAX_CHARS = 4096


class DaemonConnectBody(BaseModel):
    """POST /api/daemon/connect."""
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = Field(ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def valid_host(cls, v: str) -> str:
        """Field validator: a hostname, IPv4 address or bare IPv6 literal (check_host)."""
        return check_host(v)


class ConfigPreviewBody(BaseModel):
    """POST /api/daemon/config/preview: form `values` or YAML `text`, exactly one."""
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any] | None = None
    text: str | None = Field(None, max_length=MAX_YAML_BYTES)

    @model_validator(mode="after")
    def exactly_one(self) -> "ConfigPreviewBody":
        """ValueError unless exactly one of values and text is given."""
        if (self.values is None) == (self.text is None):
            raise ValueError("give values or text, exactly one")
        return self


class ConfigPushBody(BaseModel):
    """POST /api/daemon/config/push."""
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(max_length=MAX_YAML_BYTES)
    filename: str | None = None
    confirm: StrictBool = False

    @field_validator("filename")
    @classmethod
    def valid_filename(cls, v: str | None) -> str | None:
        """The name must match the session's rule: ^[A-Za-z0-9_.-]{1,64}\\.ya?ml$, not starting with `tg-`."""
        if v is not None and (not _FILENAME_RE.fullmatch(v) or v.startswith("tg-")):
            raise ValueError("filename must match ^[A-Za-z0-9_.-]{1,64}\\.ya?ml$ and not start with tg-")
        return v


class StfBody(BaseModel):
    """The typed fields of a stateful run: d 10..3600, m 0 < m <= 1000, l 0..100000, rx_check 0..65535."""
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(max_length=PROFILE_MAX_CHARS)      # its path rule is the session's (409)
    d: Annotated[int, Field(strict=True, ge=10, le=3600)]
    m: Annotated[float, Field(strict=True, gt=0, le=1000)] = 1.0
    l: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0  # noqa: E741 - TRex's own flag name
    rx_check: Annotated[int, Field(strict=True, ge=0, le=65535)] = 0

    def options(self) -> StfOptions:
        """The same fields as the session's StfOptions."""
        return StfOptions(self.profile, self.d, self.m, self.l, self.rx_check)


class StartPreviewBody(BaseModel):
    """POST /api/daemon/start/preview. The rules are DaemonSession's own."""
    model_config = ConfigDict(extra="forbid")
    cfg_path: str
    options: dict[str, StrictInt] = Field(default_factory=dict)
    trex_args: str = ""
    mode: Literal["stl", "stf"] = "stl"
    stf: StfBody | None = None

    @model_validator(mode="after")
    def session_rules(self) -> "StartPreviewBody":
        """ValueError when cfg_path, options or trex_args break the session's rule; `stf` is required with
        mode stf and refused with mode stl; a stateful run takes no trex_args."""
        _check_cfg_and_options(self.cfg_path, self.options)
        _check_trex_args(self.trex_args)
        if (self.mode == "stf") != (self.stf is not None):
            raise ValueError("stf is required with mode stf, and refused with mode stl")
        if self.stf is not None:
            _check_stf(self.stf.options(), self.trex_args)
        return self

    def stf_options(self) -> StfOptions | None:
        """The typed STF fields, or None for a stateless run."""
        return self.stf.options() if self.stf is not None else None


class StartBody(StartPreviewBody):
    """POST /api/daemon/start."""
    timeout: Annotated[float, Field(strict=True, ge=5, le=300)] = 40
    confirm: StrictBool = False


class StopBody(BaseModel):
    """POST /api/daemon/stop."""
    model_config = ConfigDict(extra="forbid")
    rpc_port: int = Field(ge=1, le=65535)
    confirm: StrictBool = False
    force: StrictBool = False
    owners_unknown_ack: StrictBool = False


class ProfileBody(BaseModel):
    """POST /api/daemon/stf/profile."""
    model_config = ConfigDict(extra="forbid")
    path: str


class UploadPreviewBody(BaseModel):
    """POST /api/daemon/stf/upload/preview: `path` is an absolute path on the backend's host."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=UPLOAD_PATH_MAX_CHARS)

    @field_validator("path")
    @classmethod
    def absolute(cls, v: str) -> str:
        """The path must be absolute."""
        if not v.startswith("/"):
            raise ValueError("path must be absolute")
        return v


class UploadBody(UploadPreviewBody):
    """POST /api/daemon/stf/upload. `expected_yaml_remote` is the preview's `yaml_remote`: the upload is
    refused (409 plan_changed) when the files now plan to a different profile, or the connected daemon is not
    `expected_target` (host:port, as the confirm named it)."""
    expected_yaml_remote: str = Field(pattern=TG_YAML_RE)
    expected_target: str = Field(min_length=3, max_length=300)
    confirm: StrictBool = False


async def _answer(make: Callable[[], Any]) -> Any:
    """Run `make()` (a plain call or a coroutine) and map the session's errors: DaemonRefused 409 with its
    code, DaemonUnreachable 502 daemon_unreachable, DaemonFault 502 daemon_fault (the daemon's code leads the
    message), a rule the body model missed 422 bad_request."""
    try:
        out = make()
        return await out if inspect.isawaitable(out) else out
    except DaemonRefused as e:
        return err(409, e.code, e.message)
    except DaemonUnreachable as e:
        return err(502, "daemon_unreachable", e.message)
    except DaemonFault as e:
        return err(502, "daemon_fault", f"{e.code}: {e.message}")
    except ValueError as e:
        return err(422, "bad_request", str(e))


# What a route through `_answer` declares.
DAEMON_ANSWERS: tuple[Item, ...] = (
    (409, "daemon"),
    (422, "bad_request", "a rule the body model missed"),
    (502, "daemon_fault", "the daemon answered with an error"),
    (502, "daemon_unreachable", "the daemon cannot be reached"),
)


def daemon_router(daemon: Callable[[], DaemonSession]) -> APIRouter:
    """The /api/daemon routes over the lifespan's DaemonSession."""
    router = APIRouter()

    @router.post("/api/daemon/connect", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def connect(b: DaemonConnectBody) -> Any:
        """Connect to the daemon at host:port; returns its version, config, metadata, devices, files dir. 409
        with `busy` (another daemon operation is in flight), or `not_connected` (a disconnect arrived while
        connecting). 502 when the daemon cannot be reached or faults."""
        return await _answer(lambda: daemon().connect(b.host, b.port))

    @router.post("/api/daemon/disconnect")
    async def disconnect() -> dict[str, Any]:
        """Drop the connection; returns the snapshot."""
        await daemon().disconnect()
        return daemon().snapshot()

    @router.get("/api/daemon")
    async def get_daemon() -> dict[str, Any]:
        """The daemon session's snapshot (the `daemon` hub message)."""
        return daemon().snapshot()

    @router.post("/api/daemon/config/preview", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def config_preview(b: ConfigPreviewBody) -> Any:
        """{yaml, values, errors} for form values or YAML text; makes no daemon call."""
        return await _answer(lambda: daemon().preview(b.values, b.text))

    @router.post("/api/daemon/config/push", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def config_push(b: ConfigPushBody) -> Any:
        """Push the config as `filename` into the daemon's files dir; needs confirm; returns {path}."""
        return await _answer(lambda: daemon().push(b.yaml, b.filename, b.confirm))

    @router.post("/api/daemon/start/preview", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def start_preview(b: StartPreviewBody) -> Any:
        """{command_preview}: the exact command a Start would run."""
        return await _answer(lambda: {"command_preview": daemon().command_preview(
            b.cfg_path, dict(b.options), b.trex_args, b.stf_options())})

    @router.post("/api/daemon/start", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def start(b: StartBody) -> Any:
        """Start TRex; needs `confirm`; returns {seq, command_preview, run}. 422 bad_request when `cfg_path`
        is not absolute or has `..` or characters outside A-Z a-z 0-9 _ . / -, when `options` holds
        anything but `c` (1-64), when `trex_args` has more than 20 tokens, an unbalanced quote, a token
        with other characters or a forbidden flag, when `stf` is sent with `mode` stl or left out with
        `mode` stf, when `timeout` is outside 5..300, or for a bad stateful `stf` (out-of-range values, or
        any `trex_args`). 409 with
        `not_connected` (no daemon connection), `confirm_required`, `busy` (another daemon operation is in
        flight), `already_running`, `reserved`, or `foreign_trex` (a TRex this daemon did not start is
        running), and `invalid_profile` for a stateful profile that is refused. 502 when the daemon cannot
        be reached or faults."""
        return await _answer(lambda: daemon().start(
            b.cfg_path, dict(b.options), b.trex_args, b.timeout, b.confirm, b.stf_options()))

    @router.post("/api/daemon/stop", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def stop(b: StopBody) -> Any:
        """Stop (or, with `force`, Force stop) TRex; needs confirm; returns {running, stop_s}. 409 with
        `confirm_required`, `not_connected` (no daemon connection), `busy` (another daemon operation is in
        flight), `not_ours` (Stop when this backend holds no run, or a stateful run it did not start),
        `owners_unknown` (the port owners cannot be read; Force stop lifts it with `owners_unknown_ack`), or
        `in_use` (another user owns a port; never lifted). 502 when the daemon cannot be reached or faults."""
        return await _answer(lambda: daemon().stop(b.rpc_port, b.confirm, b.force, b.owners_unknown_ack))

    @router.get("/api/daemon/stf/profiles", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def stf_profiles(source: Literal["stock", "uploaded"]) -> Any:
        """{source, files}: the stock or the uploaded STF profiles the daemon lists."""
        return await _answer(lambda: daemon().stf_profiles(source))

    @router.post("/api/daemon/stf/profile", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def stf_profile(b: ProfileBody) -> Any:
        """{path, duration, templates, errors, warnings} for one profile; 409 invalid_profile for a path
        outside the path rule."""
        return await _answer(lambda: daemon().stf_profile(b.path))

    @router.post("/api/daemon/stf/upload/preview", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def stf_upload_preview(b: UploadPreviewBody) -> Any:
        """What an upload of the local profile `path` would write: {profile, files, total_bytes, yaml,
        yaml_remote, yaml_bytes, yaml_exists, warnings}; 409 invalid_profile when the plan is refused. Writes
        nothing."""
        return await _answer(lambda: daemon().stf_upload_preview(b.path))

    @router.post("/api/daemon/stf/upload", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def stf_upload(b: UploadBody) -> Any:
        """Push the local profile `path` and its pcaps to the daemon's files dir; needs confirm; 409
        plan_changed when the files differ from the preview's `expected_yaml_remote` or the connected
        daemon is not `expected_target`; returns {profile, pushed, skipped}."""
        return await _answer(lambda: daemon().stf_upload(
            b.path, b.confirm, b.expected_yaml_remote, b.expected_target))

    @router.get("/api/daemon/log", response_model=None, responses=answers(*DAEMON_ANSWERS))
    async def log(which: Literal["trex", "daemon"]) -> Any:
        """{which, text}: the tail of TRex's log or the daemon's own."""
        out = await _answer(lambda: daemon().log(which))
        return out if isinstance(out, JSONResponse) else {"which": which, "text": out}

    return router
