"""/api/studio/packet routes (P4b Task 4): the packet editor's engine, server and user templates, and the pcap
picker.

Every route reads its JSON through `read_json_capped` (PACKET_BODY_MAX). Every `{doc}` goes through
`parse_doc`, then `check_ids` against the cached definitions, before the worker is asked to build it (the
worker evaluates EXPRESSION values, so an unknown id never reaches it). R1: the engine is called only from
the event-loop thread, as a coroutine; nothing here passes an engine call to `asyncio.to_thread` or an
executor, because the worker dies with the thread that forked it (PR_SET_PDEATHSIG). File work (stat, the
pcap scan, template files) runs in threads and never touches the engine.

This module never imports `trex` or `scapy`: the worker does."""
import asyncio
import base64
import binascii
import json
import os
import re
import stat
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trex_web_gui.api.bodies import BODY_ANSWERS, BodyRefused, err, read_json_capped
from trex_web_gui.api.declared import Item, answers
from trex_web_gui.api.studio import UPLOAD_ID_RE
from trex_web_gui.store.settings import Preferences
from trex_web_gui.studio.packetdoc import DocError, PacketDoc, check_ids, model_of, parse_doc
from trex_web_gui.studio.presets import NAMES, PRESETS, PresetError, PresetNotFound, defaults, preset
from trex_web_gui.studio.streams import Packet, Vm
from trex_web_gui.studio.templates import (
    PACKET_BODY_MAX,
    TemplateExists,
    TemplateNameError,
    TemplateNotFound,
    TemplateStore,
    TemplateTooLarge,
    check_name,
)
from trex_web_gui.trexapi.packet import PacketBusy, PacketEngine, PacketError
from trex_web_gui.validation import code_problem

PCAP_PAGE_MAX = 100                      # rows in one picker page (R3)
SCAN_NOTES_MAX = 10                      # capture warnings listed for one page before "and N more"
RECORD_MAX = 65535                       # a record longer than this is cut when the packet is loaded
UNBUILDABLE_SERVER = {                   # R4: server templates the service cannot build (R9 tests it)
    "IGMP-JOIN": "two Scapy classes are named IGMP, and its fixture carries an IP Router Alert option"}
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "studio" / "templates"       # `trex-web-gui/*` ids
BUNDLED_PREFIX = "trex-web-gui/"
BUNDLED_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}")
T = TypeVar("T")
B = TypeVar("B", bound=BaseModel)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocBody(_Body):
    doc: Any


class ParamBody(DocBody):
    param_id: str = Field(min_length=1, max_length=200)


def _base64(v: str) -> str:
    """`v` when it is strict base64 of at least one byte; ValueError otherwise."""
    try:
        if not base64.b64decode(v, validate=True):
            raise ValueError("binary is empty")
    except (binascii.Error, ValueError):
        raise ValueError("binary is not valid base64") from None
    return v


class FromBinaryBody(_Body):
    binary: str
    vm: dict[str, Any] | None = None

    @field_validator("binary")
    @classmethod
    def _b64(cls, v: str) -> str:
        """Strict base64, not empty."""
        return _base64(v)


class DecompileBody(_Body):
    binary: str
    vm: dict[str, Any]

    @field_validator("binary")
    @classmethod
    def _b64(cls, v: str) -> str:
        """Strict base64, not empty."""
        return _base64(v)


class Edited(_Body):
    layer: int = Field(ge=0)
    field: str = Field(min_length=1)


class ReconstructBody(DocBody):
    binary: str
    edited: Edited | None

    @field_validator("binary")
    @classmethod
    def _b64(cls, v: str) -> str:
        """Strict base64, not empty."""
        return _base64(v)


class TemplateBody(DocBody):
    overwrite: bool = False


class PcapSource(_Body):
    """A capture on the backend host (`path`, absolute after `~`) or an upload (`upload_id`): exactly one."""

    path: str | None = Field(None, min_length=1, max_length=4096)
    upload_id: str | None = Field(None, pattern=UPLOAD_ID_RE.pattern)

    @model_validator(mode="after")
    def _one_source(self) -> "PcapSource":
        """Exactly one of `path` and `upload_id`."""
        if (self.path is None) == (self.upload_id is None):
            raise ValueError("give exactly one of path and upload_id")
        return self


class PcapListBody(PcapSource):
    offset: int = 0
    limit: int = PCAP_PAGE_MAX


class PcapPacketBody(PcapSource):
    index: int = Field(ge=0)


class _Notes:
    """The warnings of one scan: record warnings (the first SCAN_NOTES_MAX for a whole-file scan, or just
    those of record `index`), a count of the rest, and the tail warning when the file ends mid-block."""

    def __init__(self, index: int | None) -> None:
        """A scan of the whole file (`index` None) or of one record."""
        self.index = index
        self.notes: list[str] = []
        self.more = 0
        self.tail: str | None = None

    def record(self, i: int, captured: int, original: int) -> None:
        """Warn about record `i` when `captured` < `original` or `captured` is over RECORD_MAX."""
        found = []
        if captured < original:
            found.append(f"record {i} holds {captured} of its {original} bytes (the capture was cut short)")
        if captured > RECORD_MAX:
            found.append(f"record {i} is {captured} bytes; the packet is cut at {RECORD_MAX} when loaded")
        for text in found:
            if self.index is None and len(self.notes) >= SCAN_NOTES_MAX:
                self.more += 1
            elif self.index is None or self.index == i:
                self.notes.append(text)

    def cut_header(self, i: int) -> None:
        """The file ends inside the header of record `i`."""
        self.tail = (f"the capture ends inside the header of record {i}; it and any later records are "
                     "not shown")

    def cut_record(self, i: int, missing: int) -> None:
        """The file ends inside record `i`, `missing` bytes short."""
        self.tail = (f"the capture ends inside record {i} ({missing} bytes missing); "
                     "it and any later records are not shown")

    def result(self) -> list[str]:
        """The warnings in order: records, the count of more, then the tail."""
        out = list(self.notes)
        if self.more:
            out.append(f"and {self.more} more records are cut")
        return [*out, self.tail] if self.tail else out


def _scan_pcap(f: BinaryIO, head: bytes, size: int, notes: _Notes) -> None:
    """Walk the records of a classic pcap whose 24-byte header is `head`."""
    fmt = "<IIII" if struct.unpack("<I", head[:4])[0] in (0xA1B2C3D4, 0xA1B23C4D) else ">IIII"
    pos, i = 24, 0
    while pos < size:
        raw = f.read(16)
        if len(raw) < 16:
            notes.cut_header(i)
            return
        incl, orig = struct.unpack(fmt, raw)[2:]
        if pos + 16 + incl > size:
            notes.cut_record(i, pos + 16 + incl - size)
            return
        f.seek(incl, os.SEEK_CUR)
        pos += 16 + incl
        notes.record(i, incl, orig)
        i += 1


def _scan_pcapng(f: BinaryIO, size: int, notes: _Notes) -> None:
    """Walk the blocks of a pcapng file (`f` at its start). An Enhanced Packet Block (type 6) is a record
    with a captured and an original length; a Simple Packet Block (type 3) has the original length and
    whatever its block holds. A malformed block length ends the scan without a warning (the worker
    reports what it cannot read)."""
    end = "<"
    pos, i = 0, 0
    while pos < size:
        raw = f.read(8)
        if len(raw) < 8:
            notes.cut_header(i)
            return
        if raw[:4] == b"\x0a\x0d\x0d\x0a":                       # a section header: it sets the byte order
            peek = f.read(4)
            end = ">" if peek == b"\x1a\x2b\x3c\x4d" else "<"
            f.seek(-4, os.SEEK_CUR)
        kind, total = struct.unpack(f"{end}II", raw)
        if total < 12 or total % 4:
            return
        if pos + total > size:
            if kind in (3, 6):
                notes.cut_record(i, pos + total - size)
            else:
                notes.tail = (f"the capture ends inside a block of type {kind}; it and any later records "
                              "are not shown")
            return
        if kind == 6 and total >= 32:                             # EPB: interface, timestamp, caplen, origlen
            captured, original = struct.unpack(f"{end}II", f.read(20)[12:20])
            f.seek(total - 8 - 20, os.SEEK_CUR)
            notes.record(i, captured, original)
            i += 1
        elif kind == 3 and total >= 16:                           # SPB: origlen, then the data
            original = struct.unpack(f"{end}I", f.read(4))[0]
            f.seek(total - 8 - 4, os.SEEK_CUR)
            notes.record(i, min(original, total - 16), original)
            i += 1
        else:
            f.seek(total - 8, os.SEEK_CUR)
        pos += total


def scan_capture(path: str, index: int | None) -> list[str]:
    """Warnings about the capture `path`, classic pcap or pcapng, read without loading any record: the
    capture ends inside a record (the reader then stops without a word or shows a short packet), a record
    holds fewer bytes than it had on the wire (captured < original length), or a record is over RECORD_MAX
    bytes (R8). With `index` None, the first SCAN_NOTES_MAX record warnings and a count of the rest; with
    an index, only that record's. Any other file has no warnings here (the worker says "not a pcap")."""
    notes = _Notes(index)
    with open(path, "rb") as f:
        head = f.read(24)
        size = os.fstat(f.fileno()).st_size
        magic = struct.unpack("<I", head[:4])[0] if len(head) >= 4 else 0
        if len(head) >= 24 and magic in (0xA1B2C3D4, 0xA1B23C4D, 0xD4C3B2A1, 0x4D3CB2A1):
            _scan_pcap(f, head, size, notes)
        elif len(head) >= 12 and head[:4] == b"\x0a\x0d\x0d\x0a" and head[8:12] in (
                b"\x4d\x3c\x2b\x1a", b"\x1a\x2b\x3c\x4d"):
            f.seek(0)
            _scan_pcapng(f, size, notes)
    return notes.result()


def bundled_ids() -> list[str]:
    """The ids `trex-web-gui/<name>` of the `.trp` files shipped in `studio/templates/`, sorted. Read from
    disk; the worker is never asked for them."""
    return sorted(BUNDLED_PREFIX + p.stem for p in BUNDLED_DIR.glob("*.trp")
                  if BUNDLED_NAME.fullmatch(p.stem) and p.is_file())


def bundled_doc(template_id: str) -> PacketDoc | None:
    """The shipped template `template_id` (an id from `bundled_ids`) parsed, or None when there is no such
    template. DocError or ValueError when the shipped file itself is bad."""
    if template_id not in bundled_ids():
        return None
    path = BUNDLED_DIR / (template_id.removeprefix(BUNDLED_PREFIX) + ".trp")
    return parse_doc(json.loads(path.read_text(encoding="utf-8")))


# What a route through `guarded` declares from `engine_failure`.
ENGINE_ANSWERS: tuple[Item, ...] = (
    (422, "packet_error", "the packet worker refused the request"),
    (503, "engine_busy", "the packet worker is busy"),
    (503, "engine_closed", "the packet worker is closed"),
    (503, "engine_restarted", "the packet worker restarted; the next call starts a fresh one"),
)


def packet_router(engine: PacketEngine, store: TemplateStore, prefs_fn: Callable[[], Preferences],
                  data_dir: Path) -> APIRouter:
    """The /api/studio/packet routes over `engine`, the user templates in `store`, and the uploads in
    `data_dir / "uploads"`. `prefs_fn` gives the current preferences (the pcap size cap)."""
    router = APIRouter(prefix="/api/studio/packet")
    upload_dir = data_dir / "uploads"

    def engine_failure(e: PacketError) -> JSONResponse:
        """The error response for a worker failure: 503 engine_busy, engine_closed or engine_restarted (the
        next call starts a fresh worker), else 422 packet_error (the worker's own refusal)."""
        if isinstance(e, PacketBusy):
            return err(503, "engine_busy", str(e))
        if engine.closed:
            return err(503, "engine_closed", str(e))
        if e.restarted:
            return err(503, "engine_restarted", str(e))
        return err(422, "packet_error", str(e))

    def doc_failure(e: DocError) -> JSONResponse:
        """422 invalid_doc naming the position: `layer <i>` / `instruction <i>` from check_ids, else the key
        path from parse_doc."""
        return JSONResponse({"error": "invalid_doc", "message": e.message, "where": e.label or e.where},
                            status_code=422)

    async def guarded(work: Callable[[], Awaitable[T]]) -> T | JSONResponse | Response:
        """`work()`'s result, or the response for a refused body or a worker failure."""
        try:
            return await work()
        except BodyRefused as e:
            return e.response
        except PacketError as e:
            return engine_failure(e)

    async def body_of(request: Request, model: type[B]) -> B:
        """The capped JSON body as `model`; BodyRefused (413, 411, 422 bad_request) otherwise."""
        raw = await read_json_capped(request, PACKET_BODY_MAX)
        try:
            return model.model_validate(raw)
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(x) for x in first["loc"]) or "body"
            raise BodyRefused(err(422, "bad_request", f"{where}: {first['msg']}")) from None

    async def checked(raw: object) -> PacketDoc:
        """The document `raw`: parse_doc, then check_ids against the definitions; BodyRefused 422 invalid_doc.
        The worker has not seen it yet (it is asked for the definitions only after parse_doc)."""
        try:
            doc = parse_doc(raw)
            check_ids(doc, await engine.definitions())
        except DocError as e:
            raise BodyRefused(doc_failure(e)) from None
        return doc

    @router.get("/definitions", response_model=None,
                responses=answers(
                    *ENGINE_ANSWERS,
                    (304, "", "the `If-None-Match` ETag is current; nothing is sent")))
    async def definitions(if_none_match: str | None = Header(None)) -> Response:
        """The protocol and Field Engine definitions with an ETag; 304 when `If-None-Match` has it."""
        async def go() -> Response:
            """Fetch (cached per worker start) and answer."""
            defs = await engine.definitions()
            tag = f'"{defs["etag"]}"'
            offered = {t.strip().removeprefix("W/") for t in (if_none_match or "").split(",")}
            if tag in offered:
                return Response(status_code=304, headers={"ETag": tag})
            return JSONResponse(defs, headers={"ETag": tag})
        return await guarded(go)

    @router.post("/build", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_doc", "the document or file is invalid; `where` is the position")))
    async def build(request: Request) -> Any:
        """Build the document; a build error is a 200 with `error` set."""
        async def go() -> Any:
            """Parse, check ids, build."""
            b = await body_of(request, DocBody)
            packet, fe = model_of(await checked(b.doc))
            return await engine.build(packet, fe)
        return await guarded(go)

    @router.post("/payload-classes", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_doc", "the document or file is invalid; `where` is the position")))
    async def payload_classes(request: Request) -> Any:
        """The protocols the document's last layer may carry."""
        async def go() -> Any:
            """Parse, check ids, ask."""
            b = await body_of(request, DocBody)
            return {"classes": await engine.payload_classes(model_of(await checked(b.doc))[0])}
        return await guarded(go)

    @router.post("/param-values", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_doc", "the document or file is invalid; `where` is the position")))
    async def param_values(request: Request) -> Any:
        """The values a Field Engine parameter may take for the document."""
        async def go() -> Any:
            """Parse, check ids, ask."""
            b = await body_of(request, ParamBody)
            packet, fe = model_of(await checked(b.doc))
            return {"map": await engine.param_values(packet, fe, b.param_id)}
        return await guarded(go)

    @router.post("/doc-from-binary", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_stream", "a stream is invalid; `index` and `field` name it")))
    async def doc_from_binary(request: Request) -> Any:
        """A literal-only document for packet bytes and, optionally, a stream's VM."""
        async def go() -> Any:
            """Check the VM's shape, then derive."""
            b = await body_of(request, FromBinaryBody)
            if b.vm is not None:
                _vm_checked(b.vm)
            return await engine.doc_from_binary(b.binary, b.vm)
        return await guarded(go)

    @router.post("/reconstruct", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_doc", "the document or file is invalid; `where` is the position")))
    async def reconstruct(request: Request) -> Any:
        """The document after a byte edit."""
        async def go() -> Any:
            """Parse, check ids, reconstruct."""
            b = await body_of(request, ReconstructBody)
            doc = await checked(b.doc)
            edited = None if b.edited is None else b.edited.model_dump()
            return await engine.reconstruct_edit(doc, b.binary, edited)
        return await guarded(go)

    @router.post("/decompile", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_stream", "a stream is invalid; `index` and `field` name it")))
    async def decompile(request: Request) -> Any:
        """A VM's instructions as editor parameters."""
        async def go() -> Any:
            """Check the VM's shape, then decompile."""
            b = await body_of(request, DecompileBody)
            _vm_checked(b.vm)
            return await engine.decompile(b.binary, b.vm)
        return await guarded(go)

    def _vm_checked(vm: dict[str, Any]) -> None:
        """BodyRefused 422 invalid_stream unless `vm` is a P4a Vm whose strings pass `code_problem`."""
        try:
            Vm.model_validate(vm)
        except ValidationError as e:
            first = e.errors()[0]
            field = "vm." + ".".join(str(x) for x in first["loc"])
            raise BodyRefused(_invalid_stream(field, first["msg"])) from None
        if (problem := code_problem({"vm": vm})) is not None:
            raise BodyRefused(_invalid_stream(problem[0], problem[1]))

    def _invalid_stream(field: str, message: str) -> JSONResponse:
        """422 invalid_stream naming `field`."""
        return JSONResponse({"error": "invalid_stream", "message": f"{field}: {message}", "field": field},
                            status_code=422)

    @router.post("/to-stream", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_doc", "the document or file is invalid; `where` is the position"),
                     (422, "invalid_stream", "a stream is invalid; `index` and `field` name it")))
    async def to_stream(request: Request) -> Any:
        """`{packet, vm}` for a stream, each half validated as P4a validates a stream's."""
        async def go() -> Any:
            """Parse, check ids, build, then validate the two halves."""
            b = await body_of(request, DocBody)
            packet, fe = model_of(await checked(b.doc))
            parts = await engine.to_stream(packet, fe)
            try:
                Packet.model_validate(parts["packet"])
            except ValidationError as e:
                first = e.errors()[0]
                raise BodyRefused(_invalid_stream("packet." + ".".join(str(x) for x in first["loc"]),
                                                  first["msg"].removeprefix("Value error, "))) from None
            _vm_checked(parts["vm"])
            return parts
        return await guarded(go)

    @router.post("/vm-python", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (422, "invalid_doc", "the document or file is invalid; `where` is the position")))
    async def vm_python(request: Request) -> Any:
        """The Python that builds the document's Field Engine."""
        async def go() -> Any:
            """Parse, check ids, generate."""
            b = await body_of(request, DocBody)
            packet, fe = model_of(await checked(b.doc))
            return {"code": await engine.vm_python(packet, fe)}
        return await guarded(go)

    @router.get("/templates", response_model=None, responses=answers(*ENGINE_ANSWERS))
    async def templates() -> Any:
        """`{server, user, dir}`: the server's templates (`buildable` false with a `reason` for one the
        service cannot build), the user's template names, and the user directory."""
        async def go() -> Any:
            """List both kinds."""
            server = [{**t, "buildable": t["id"] not in UNBUILDABLE_SERVER,
                       "reason": UNBUILDABLE_SERVER.get(t["id"])} for t in await engine.templates()]
            server += [{"id": i, "name": i.removeprefix(BUNDLED_PREFIX), "buildable": True, "reason": None}
                       for i in await asyncio.to_thread(bundled_ids)]
            names, directory = await asyncio.to_thread(lambda: (store.list(), str(store.directory())))
            return {"server": server, "user": names, "dir": directory}
        return await guarded(go)

    @router.get("/templates/server/{template_id:path}", response_model=None,
                responses=answers(
                    (404, "not_found", "no such server template"),
                    (422, "invalid_doc", "the document or file is invalid; `where` is the position"),
                    (422, "packet_error", "the template cannot be built, or the packet worker refused"),
                    (500, "storage_error", "the shipped template file cannot be read"),
                    (503, "engine_busy", "the packet worker is busy"),
                    (503, "engine_closed", "the packet worker is closed"),
                    (503, "engine_restarted", "the worker restarted; the next call starts a fresh one")))
    async def server_template(template_id: str) -> Any:
        """The server template `template_id` as `{doc}`; 404 unless the listing gave that id."""
        async def go() -> Any:
            """Look the id up in the listing before asking the worker for it. A `trex-web-gui/` id is read
            from the shipped files instead, and the worker is not asked."""
            if template_id.startswith(BUNDLED_PREFIX):
                try:
                    doc = await asyncio.to_thread(bundled_doc, template_id)
                except DocError as e:
                    return doc_failure(e)
                except (OSError, ValueError) as e:
                    return err(500, "storage_error", f"Cannot read {template_id}: {e}")
                if doc is None:
                    return err(404, "not_found", f"No server template {template_id}")
                return {"doc": doc}
            if template_id not in {t["id"] for t in await engine.templates()}:
                return err(404, "not_found", f"No server template {template_id}")
            if template_id in UNBUILDABLE_SERVER:
                return err(422, "packet_error",
                           f"{template_id} cannot be built: {UNBUILDABLE_SERVER[template_id]}")
            return {"doc": await engine.template(template_id)}
        return await guarded(go)

    def template_failure(e: Exception) -> JSONResponse:
        """The response for a template-store exception."""
        if isinstance(e, TemplateNotFound):
            return err(404, "not_found", f"No template {e}")
        if isinstance(e, TemplateExists):
            return err(409, "exists", f"A template {e} already exists")
        if isinstance(e, TemplateTooLarge):
            return err(413, "too_large", str(e))
        if isinstance(e, TemplateNameError):
            return err(404, "not_found", str(e))
        if isinstance(e, DocError):
            return doc_failure(e)
        if isinstance(e, ValueError):                 # a stored file that is not JSON
            return err(422, "invalid_doc", f"The template file does not parse: {e}")
        return err(500, "storage_error", f"Cannot use the templates directory: {e}")

    store_errors = (TemplateNotFound, TemplateExists, TemplateTooLarge, TemplateNameError, DocError,
                    ValueError, OSError)

    @router.get("/templates/user/{name:path}", response_model=None,
                responses=answers(
                    (404, "not_found", "no such template"),
                    (409, "exists", "a template of that name already exists"),
                    (413, "too_large", "the template file is over its size cap"),
                    (422, "invalid_doc", "the document or file is invalid; `where` is the position"),
                    (500, "storage_error", "the templates directory cannot be used")))
    async def get_user_template(name: str) -> Any:
        """The user template `name` as `{doc}`; a name the rules refuse is a 404."""
        try:
            return {"doc": await asyncio.to_thread(store.read, name)}
        except TemplateNameError as e:
            return err(404, "not_found", str(e))
        except store_errors as e:
            return template_failure(e)

    @router.put("/templates/user/{name:path}", response_model=None,
                responses=answers(
                    (411, "length_required", "the request has no valid Content-Length"),
                    (413, "too_large", "the body, or the serialised template, is over its size cap"),
                    (422, "bad_request", "the body is not valid JSON, or a field is wrong"),
                    *ENGINE_ANSWERS,
                    (404, "not_found", "no such template"),
                    (409, "exists", "a template of that name already exists"),
                    (422, "invalid_doc", "the document or file is invalid; `where` is the position"),
                    (500, "storage_error", "the templates directory cannot be used")))
    async def put_user_template(name: str, request: Request) -> Any:
        """Save the document as the user template `name` (409 exists without `overwrite`); `{doc}`. A name
        the rules refuse is a 404, answered before the body is read or the worker is asked for anything."""
        try:
            check_name(name)
        except TemplateNameError as e:
            return err(404, "not_found", str(e))

        async def go() -> Any:
            """Parse, check ids, write."""
            b = await body_of(request, TemplateBody)
            doc = await checked(b.doc)
            try:
                await asyncio.to_thread(store.write, name, doc, b.overwrite)
            except store_errors as e:
                return template_failure(e)
            return {"doc": doc}
        return await guarded(go)

    @router.delete("/templates/user/{name:path}", response_model=None,
                   responses=answers(
                       (404, "not_found", "no such template"),
                       (409, "exists", "a template of that name already exists"),
                       (413, "too_large", "the template file is over its size cap"),
                       (422, "invalid_doc", "the document or file is invalid; `where` is the position"),
                       (500, "storage_error", "the templates directory cannot be used")))
    async def delete_user_template(name: str) -> Any:
        """Remove the user template `name`; `{}`."""
        try:
            await asyncio.to_thread(store.delete, name)
        except TemplateNameError as e:
            return err(404, "not_found", str(e))
        except store_errors as e:
            return template_failure(e)
        return {}

    def inspect(src: PcapSource, index: int | None) -> tuple[str, list[str]]:
        """The capture's path and its warnings, in a thread. BodyRefused: 404 not_found (no upload, no
        file), 422 bad_request (a path that is relative, or not a regular file), 413 too_large (over
        `pcap_size_cap_mib`, before the worker opens it)."""
        if src.upload_id is not None:
            found = sorted(upload_dir.glob(f"{src.upload_id}.*"))
            if not found:
                raise BodyRefused(err(404, "not_found", f"No upload {src.upload_id} (it may have expired)"))
            path = str(found[0])
        else:
            assert src.path is not None
            path = os.path.expanduser(src.path)
            if "\x00" in path or not os.path.isabs(path):
                raise BodyRefused(err(422, "bad_request", "path must be an absolute path"))
        try:
            st = os.stat(path)
        except FileNotFoundError:
            raise BodyRefused(err(404, "not_found", f"No file {path}")) from None
        except OSError as e:
            raise BodyRefused(err(422, "bad_request", f"Cannot read {path}: {e.strerror}")) from None
        if not stat.S_ISREG(st.st_mode):
            raise BodyRefused(err(422, "bad_request", f"{path} is not a regular file"))
        cap = prefs_fn().pcap_size_cap_mib
        if st.st_size > cap * 1024 * 1024:
            raise BodyRefused(err(413, "too_large", f"{path} is {st.st_size} bytes; the pcap size cap is "
                                                    f"{cap} MiB (Settings, pcap_size_cap_mib)"))
        try:
            return path, scan_capture(path, index)
        except OSError as e:
            raise BodyRefused(err(422, "bad_request", f"Cannot read {path}: {e.strerror}")) from None

    @router.get("/presets", response_model=None)
    async def presets() -> Any:
        """The presets as `{presets: [{id, name, defaults}]}`; `defaults` are what a form starts from."""
        return {"presets": [{"id": i, "name": NAMES[i], "defaults": defaults(i)} for i in PRESETS]}

    @router.post("/presets/{preset_id}", response_model=None,
                 responses=answers(
                     *BODY_ANSWERS,
                     *ENGINE_ANSWERS,
                     (404, "not_found", "no such preset"),
                     (422, "invalid_params", "the preset parameters are invalid; `where` names the field")))
    async def build_preset(preset_id: str, request: Request) -> Any:
        """`{doc, flags}` for preset `preset_id` over the JSON object in the body (parameters left out take
        their defaults); 404 for an unknown preset, 422 invalid_params naming the parameter. `flags` is the
        stream's MAC-mode bits, or null when the preset leaves them alone. The worker is not involved."""
        async def go() -> Any:
            """Read the parameters, build."""
            raw = await read_json_capped(request, PACKET_BODY_MAX)
            try:
                return preset(preset_id, raw)
            except PresetNotFound:
                return err(404, "not_found", f"No preset {preset_id}")
            except PresetError as e:
                return JSONResponse({"error": "invalid_params", "message": str(e), "where": e.where},
                                    status_code=422)
        return await guarded(go)

    @router.post("/pcap/list", response_model=None,
                 responses=answers(
                     (411, "length_required", "the request has no valid Content-Length"),
                     (413, "too_large", "the request body, or the capture file, is over its size cap"),
                     *ENGINE_ANSWERS,
                     (404, "not_found", "no such upload or file"),
                     (422, "bad_request", "the body is invalid, or `path` is relative or not a file")))
    async def pcap_list(request: Request) -> Any:
        """One page of a capture, `{total, rows, warnings}`: the offset clamps to >= 0 and the limit to 1-100;
        `warnings` are the capture's cut or truncated records. The capture is an upload (`upload_id`) or an
        absolute backend `path`. 404 not_found for an expired upload or a missing file, 422 bad_request for
        a relative path or a file that is not a regular file, 413 too_large above the Settings
        `pcap_size_cap_mib` (default 64)."""
        async def go() -> Any:
            """Locate and size-check the file, then page it in the worker."""
            b = await body_of(request, PcapListBody)
            path, notes = await asyncio.to_thread(inspect, b, None)
            page = await engine.pcap_list(path, max(0, b.offset), min(max(1, b.limit), PCAP_PAGE_MAX))
            return {**page, "warnings": notes}
        return await guarded(go)

    @router.post("/pcap/packet", response_model=None,
                 responses=answers(
                     (411, "length_required", "the request has no valid Content-Length"),
                     (413, "too_large", "the request body, or the capture file, is over its size cap"),
                     *ENGINE_ANSWERS,
                     (404, "not_found", "no such upload or file"),
                     (422, "bad_request", "the body is invalid, or `path` is relative or not a file")))
    async def pcap_packet(request: Request) -> Any:
        """One packet of a capture as `{binary, doc, warnings}`: the document holds literals only. The
        capture and its refusals (404 not_found, 422 bad_request, 413 too_large) are those of
        `/pcap/list`."""
        async def go() -> Any:
            """Locate and size-check the file, read the record, derive the document."""
            b = await body_of(request, PcapPacketBody)
            path, notes = await asyncio.to_thread(inspect, b, b.index)
            binary = await engine.pcap_packet(path, b.index)
            derived = await engine.doc_from_binary(binary, None)
            return {"binary": binary, "doc": derived["doc"], "warnings": [*notes, *derived["warnings"]]}
        return await guarded(go)

    return router


