"""The /api/emu routes (spec §4.4) over EmuSession: the snapshot, connect and disconnect, the three reads
(namespaces, a namespace's clients, counters) and the profile routes (inspect, load, remove).

The session's own refusals (`not_connected`, `busy`, `unsupported`, `unsupported_server`, and the writes'
`confirm_required` and `foreign_namespaces`) pass through
`run_emu` as 409. `no_namespace` and `no_client` come from the error path only (ruling R19): when a read
scoped to a namespace or client fails with a TrexApiError from the server, the route lists what the server
holds and refuses with the missing key's code, or lets the error through as 400 `emu_error`. The server's
message text is never matched. Every route sits behind the token gate and `require_full_mode`."""
import inspect
import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator

from trex_web_gui.api.bodies import err
from trex_web_gui.api.declared import Item, answers
from trex_web_gui.sessions import EmuRefused, EmuSession, TrexSession
from trex_web_gui.trexapi import ProfileError, ServerRestarted, ServerUnreachable, TrexApiError
from trex_web_gui.trexapi.emu import CHUNK, RATE_MAX, RATE_MIN, Mac, NsKey
from trex_web_gui.trexapi.profiles import load_emu_profile
from trex_web_gui.validation import check_host

Tunables = list[Annotated[str, Field(max_length=1000)]]     # the STL load routes' limits (api/app.py)
_PAIR_RE = re.compile(r"(\d{1,5}),(\d{1,5})")
_MAC_RE = re.compile(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}")


class EmuConnectBody(BaseModel):
    """POST /api/emu/connect."""
    model_config = ConfigDict(extra="forbid")
    host: str
    port: StrictInt = Field(4510, ge=1, le=65535)
    timeout_s: StrictFloat | StrictInt = Field(3.0, gt=0, le=30)

    @field_validator("host")
    @classmethod
    def valid_host(cls, v: str) -> str:
        """Field validator: a hostname, IPv4 address or bare IPv6 literal (check_host)."""
        return check_host(v)


class EmuProfileBody(BaseModel):
    """POST /api/emu/profile/inspect: a `.py` EMU profile and the tunables to run it with."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    tunables: Tunables = Field(default_factory=list, max_length=100)


class EmuWriteBody(BaseModel):
    """The body of the writes: `confirm`, `foreign_ack` and `crash_ack` default to false (and a write refuses
    without `confirm`), `max_rate` is clients per second."""
    model_config = ConfigDict(extra="forbid")
    max_rate: StrictInt | StrictFloat | None = Field(None, ge=RATE_MIN, le=RATE_MAX)
    confirm: StrictBool = False
    foreign_ack: StrictBool = False
    crash_ack: StrictBool = False


class EmuLoadBody(EmuWriteBody):
    """POST /api/emu/profile/load: the write body and the profile."""
    path: str = Field(min_length=1)
    tunables: Tunables = Field(default_factory=list, max_length=100)


def _pair(text: str, name: str) -> tuple[int, int]:
    """A `tci` or `tpid` query value `a,b` (each 0..65535) as a pair; ValueError otherwise."""
    m = _PAIR_RE.fullmatch(text)
    if m is None or int(m[1]) > 65535 or int(m[2]) > 65535:
        raise ValueError(f"{name} must be two numbers 0..65535 separated by a comma, such as 0,0")
    return int(m[1]), int(m[2])


def _ns_key(vport: int | None, tci: str, tpid: str) -> NsKey | None:
    """The namespace key a request names, or None without a vport; ValueError for a bad tci or tpid."""
    pair = (_pair(tci, "tci"), _pair(tpid, "tpid"))
    if vport is None:
        if (tci, tpid) != ("0,0", "0,0"):
            raise ValueError("tci and tpid need a vport")
        return None
    return NsKey(vport, *pair)


def _mac(text: str) -> Mac:
    """A `mac` query value `aa:bb:cc:dd:ee:ff` as six bytes; ValueError otherwise."""
    if not _MAC_RE.fullmatch(text):
        raise ValueError("mac must be six hex bytes separated by colons, such as 00:00:00:70:00:01")
    b = [int(x, 16) for x in text.split(":")]
    return b[0], b[1], b[2], b[3], b[4], b[5]


async def run_emu(make: Callable[[], Any]) -> Any:
    """Run `make()` (a plain call or a coroutine) and map the session's errors: EmuRefused 409 with its code,
    ServerRestarted or ServerUnreachable 502 unreachable, any other TrexApiError 400 emu_error, a rule the
    query or body model missed (ValueError) 422 bad_request."""
    try:
        out = make()
        return await out if inspect.isawaitable(out) else out
    except EmuRefused as e:
        return err(409, e.code, e.message)
    except (ServerRestarted, ServerUnreachable) as e:
        return err(502, "unreachable", e.message)
    except TrexApiError as e:
        return err(400, "emu_error", e.message)
    except ValueError as e:
        return err(422, "bad_request", str(e))


async def run_emu_profile(make: Callable[[], Any]) -> Any:
    """run_emu for the routes that run a profile: a ProfileError from `make()` is 422 profile_error."""
    async def guarded() -> Any:
        """`make()`, with a ProfileError turned into its 422 answer."""
        try:
            out = make()
            return await out if inspect.isawaitable(out) else out
        except ProfileError as e:
            return err(422, "profile_error", e.message)
    return await run_emu(guarded)


# What a route through `run_emu` declares.
EMU_ANSWERS: tuple[Item, ...] = (
    (409, "emu"),
    (422, "bad_request", "an argument breaks a rule; the message names it"),
    (400, "emu_error", "the EMU server answered with an error"),
    (502, "unreachable", "the EMU server cannot be reached"),
)
PROFILE_ANSWERS: tuple[Item, ...] = (
    (422, "profile_error", "the profile cannot be loaded"),
)


async def _ns_present(emu: EmuSession, ns: NsKey) -> bool | None:
    """Whether the server's namespace listing holds `ns`; None when the listing was interrupted and `ns` is
    not in it, which proves nothing."""
    listing = await emu.namespaces()
    if any((r["vport"], tuple(r["tci"]), tuple(r["tpid"])) == (ns.vport, ns.tci, ns.tpid)
           for r in listing["namespaces"]):
        return True
    return None if listing["interrupted"] else False


async def _refuse_if_gone(emu: EmuSession, exc: TrexApiError, ns: NsKey | None, mac: Mac | None) -> None:
    """After a read named a namespace (and perhaps a client) and failed with `exc`: list what the server
    holds and raise EmuRefused no_namespace or no_client when the key is absent, else raise `exc` again.
    The session's own refusals and a lost connection are not asked about; they pass through."""
    if ns is None or isinstance(exc, EmuRefused | ServerRestarted | ServerUnreachable):
        raise exc
    try:
        ns_there = await _ns_present(emu, ns)
        client_there = True if mac is None or ns_there is not True else await emu.has_client(ns, mac)
    except (EmuRefused, ServerRestarted, ServerUnreachable):
        raise                       # a lost or dropped connection is the answer, not the read's error
    except TrexApiError:
        raise exc from None         # the read's own error is the one to show, not the re-listing's
    if ns_there is False:
        raise EmuRefused("no_namespace", f"The EMU server has no namespace vport {ns.vport}, "
                                         f"tci {list(ns.tci)}, tpid {list(ns.tpid)}.") from exc
    if client_there is False and mac is not None:
        raise EmuRefused("no_client", "The namespace has no client "
                                      f"{':'.join(f'{b:02x}' for b in mac)}.") from exc
    raise exc


def emu_router(emu: Callable[[], EmuSession], control: Callable[[], TrexSession]) -> APIRouter:
    """The /api/emu routes over the lifespan's EmuSession; `control` is the Control session, whose port count
    the profile inspection checks vports against (read only)."""
    router = APIRouter()

    @router.get("/api/emu")
    async def get_emu() -> dict[str, Any]:
        """The EMU session's snapshot (the `emu` hub message)."""
        return emu().snapshot()

    @router.post("/api/emu/connect", response_model=None, responses=answers(*EMU_ANSWERS))
    async def connect(b: EmuConnectBody) -> Any:
        """Connect to the EMU server at host:port; returns the snapshot. 409 with `busy` (a connect or write
        is in flight), `unsupported_server` (the server's API is not 1.x), or `not_connected` (a disconnect
        arrived while connecting). 502 `unreachable` when the server cannot be reached, 400 `emu_error` when
        it answers an error."""
        return await run_emu(lambda: emu().connect(b.host, b.port, float(b.timeout_s)))

    @router.post("/api/emu/disconnect", response_model=None, responses=answers(*EMU_ANSWERS))
    async def disconnect() -> Any:
        """Drop the connection; returns the snapshot. 409 `busy` during a write."""
        async def go() -> dict[str, Any]:
            """Disconnect, then the snapshot."""
            await emu().disconnect()
            return emu().snapshot()
        return await run_emu(go)

    @router.get("/api/emu/namespaces", response_model=None, responses=answers(*EMU_ANSWERS))
    async def namespaces() -> Any:
        """{generation, namespaces: [{vport, tci, tpid, active_clients, plug_names, ours}], interrupted}.
        409 `not_connected`. 502 `unreachable` when the server cannot be reached, 400 `emu_error` when it
        answers an error."""
        return await run_emu(lambda: emu().namespaces())

    @router.get("/api/emu/clients", response_model=None, responses=answers(*EMU_ANSWERS))
    async def clients(vport: Annotated[int, Query(ge=0, le=65535)], tci: str = "0,0", tpid: str = "0,0",
                      offset: Annotated[int, Query(ge=0)] = 0,
                      limit: Annotated[int, Query(ge=1, le=CHUNK)] = 100) -> Any:
        """{total, clients, interrupted}: clients `offset`..`offset+limit` of the namespace named by `vport`,
        `tci` and `tpid` (pairs `a,b`; omitted = `0,0`). 409 `not_connected`, or `no_namespace` when the
        server answered an error and no longer lists the namespace. 422 `bad_request` for a bad `tci` or
        `tpid`. 502 `unreachable` when the server cannot be reached, 400 `emu_error` when it answers an
        error."""
        async def go() -> dict[str, Any]:
            """The page; after a server error, `no_namespace` when the namespace is gone."""
            ns = NsKey(vport, _pair(tci, "tci"), _pair(tpid, "tpid"))
            try:
                return await emu().clients(ns, offset, limit)
            except TrexApiError as exc:
                await _refuse_if_gone(emu(), exc, ns, None)
                raise
        return await run_emu(go)

    @router.get("/api/emu/counters", response_model=None, responses=answers(*EMU_ANSWERS))
    async def counters(scope: Literal["ctx", "ns", "client"], plugin: str | None = None,
                       vport: Annotated[int | None, Query(ge=0, le=65535)] = None, tci: str = "0,0",
                       tpid: str = "0,0", mac: str | None = None, meta: bool = False) -> Any:
        """{tables: {table: [{name, value, unit, help, info, zero}]}, unsupported: [plugin]}. `scope` ctx
        takes no key, ns takes `vport` (with `tci`, `tpid`), client also `mac` (`aa:bb:cc:dd:ee:ff`). `plugin`
        limits the read to one plugin; left out, every plugin loaded in the namespace or client that has
        counters in the scope is read and those the server lacks are listed in `unsupported`. `meta=true`
        answers every counter with value null. 409 `not_connected`; `unsupported` when the named plugin's
        counters are not available; `no_namespace` or `no_client` when the server answered an error and no
        longer lists the key. 422 `bad_request` for a key that does not fit the scope or an unknown plugin.
        502 `unreachable` when the server cannot be reached, 400 `emu_error` when it answers an error."""
        async def go() -> dict[str, Any]:
            """The counters; after a server error, `no_namespace` or `no_client` when the key is gone."""
            ns = _ns_key(vport, tci, tpid)
            addr = _mac(mac) if mac is not None else None
            try:
                return await emu().counters(scope, ns, addr, plugin, meta)
            except TrexApiError as exc:
                await _refuse_if_gone(emu(), exc, ns, addr)
                raise
        return await run_emu(go)

    @router.post("/api/emu/profile/inspect", response_model=None,
                 responses=answers(*PROFILE_ANSWERS))
    async def inspect_profile(b: EmuProfileBody) -> Any:
        """Run the `.py` EMU profile at `path` with `tunables` in the profile worker and summarise it:
        {tunables, counts: {namespaces, clients}, plugins, vports, warnings}. Touches no server. A `warnings`
        entry names a vport the connected Control session does not have as a port (a warning, never a
        refusal). 422 `profile_error` when the profile does not load or breaks a limit."""
        async def go() -> dict[str, Any]:
            """The summary of the loaded profile."""
            loaded = await load_emu_profile(b.path, b.tunables)
            snap = control().snapshot()
            ports = len(control().ports()) if snap["state"] == "connected" else None
            warnings = [] if ports is None else [
                f"vport {v} is not a port of the connected TRex, which has {ports}" for v in loaded.vports
                if v >= ports]
            return {"tunables": loaded.tunables, "counts": {"namespaces": loaded.namespaces,
                                                           "clients": loaded.clients},
                    "plugins": loaded.plugins, "vports": loaded.vports, "warnings": warnings}
        try:
            return await go()
        except ProfileError as e:
            return err(422, "profile_error", e.message)

    @router.post("/api/emu/profile/load", response_model=None,
                 responses=answers(*EMU_ANSWERS, *PROFILE_ANSWERS))
    async def load_profile(b: EmuLoadBody) -> Any:
        """Replace everything on the EMU server with the `.py` profile at `path` (run with `tunables`): every
        namespace there is removed, then the profile's are added, at most `max_rate` clients per second.
        Returns the snapshot. 409 `confirm_required` without `confirm: true`, `foreign_namespaces` when the
        server holds namespaces this backend did not add and `foreign_ack` is not true (checked after one
        listing), `crash_risk` when one of those has the ipfix or tdl plugin at namespace level (removing it
        crashes trex-emu v3.08) and `crash_ack` is not true, `busy`, `not_connected`. 422 `profile_error`, or
        `bad_request` for a `max_rate` outside 1..100000. 502 `unreachable` (a lost connection names how far
        the load got), 400 `emu_error` with the
        server's text and how far it got."""
        return await run_emu_profile(lambda: emu().load_profile(b.path, b.tunables, max_rate=b.max_rate,
                                                                confirm=b.confirm, foreign_ack=b.foreign_ack,
                                                                crash_ack=b.crash_ack))

    @router.post("/api/emu/profile/remove", response_model=None, responses=answers(*EMU_ANSWERS))
    async def remove_profile(b: EmuWriteBody) -> Any:
        """Remove every namespace and client from the EMU server, at most `max_rate` clients per second.
        Returns the snapshot. 409 `confirm_required`, `foreign_namespaces`, `crash_risk`, `busy`,
        `not_connected` as for the load; `bad_request` for a `max_rate` outside 1..100000. 502 `unreachable`
        (trex-emu v3.08 crashes when a namespace with the ipfix or tdl plugin at namespace level is removed,
        which is reported as a lost connection; a client-level ipfix removes cleanly), 400 `emu_error`."""
        return await run_emu(lambda: emu().remove_all(max_rate=b.max_rate, confirm=b.confirm,
                                                      foreign_ack=b.foreign_ack, crash_ack=b.crash_ack))

    return router
