"""/api/studio routes (P4a): the profile library, stream pages and diffs, uploads, imports, exports and the
offline simulation.

Library I/O runs in `asyncio.to_thread` under one `asyncio.Lock`, so writers never interleave and a large
profile never blocks the loop. Worker calls (import, append, export to Python, simulate, the template) run
outside that lock: read under it, call the worker, then take it again to write, where `rev` is checked again.
Every `{slug}` goes through `library.path_of` before any other work, so a bad slug is a 404 before any file
access or body read."""
import asyncio
import base64
import binascii
import os
import re
import secrets
import stat
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.requests import ClientDisconnect

from trex_web_gui import trexapi
from trex_web_gui.api.bodies import BODY_ANSWERS, BodyRefused, err, read_json_capped
from trex_web_gui.api.declared import Item, answers
from trex_web_gui.studio import convert, simulate
from trex_web_gui.studio.library import (
    LibraryEntry,
    LibraryError,
    LibraryExists,
    LibraryNotFound,
    ProfileLibrary,
    SidecarUnreadable,
    StaleRevision,
    TooManyProfiles,
)
from trex_web_gui.studio.sidecar import Source, now
from trex_web_gui.studio.streams import DiffResult, StreamError, StreamsDiff, check_profile, parse_streams
from trex_web_gui.trexapi import decode, scapy_exec

STREAMS_PUT_LIMIT = 16 * 1024 * 1024     # bytes in a PUT .../streams body
PAGE_MAX = 100                           # rows in one stream page
SOURCE_MAX = 1024 * 1024                 # bytes of a .py source the source view reads
UPLOAD_MAX = 64 * 1024 * 1024            # bytes in one upload (R8)
UPLOADS_MAX = 8                          # uploads kept at once
UPLOAD_TTL_S = 3600                      # an upload older than this is removed
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
UPLOAD_NAME_MAX = 255                    # characters in an upload's `name` (echoed in the reply)
EXPORTS = {"json": ("json", "application/json"), "yaml": ("yaml", "application/yaml"),
           "py": ("py", "text/x-python")}
T = TypeVar("T")


class NameBody(BaseModel):
    name: str


class RenameBody(BaseModel):
    name: str
    rev: str


class TunableForm(BaseModel):
    """The Control tunables form as the GUI fills it (stored in the sidecar's `source.tunables`)."""

    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]
    extra: str


class AppendTo(BaseModel):
    slug: str
    rev: str


class ImportBody(BaseModel):
    """An import: exactly one of `path` (on the backend host) or `upload_id`."""

    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(None, min_length=1)
    upload_id: str | None = Field(None, pattern=UPLOAD_ID_RE.pattern)
    kind: Literal["py", "yaml", "json", "pcap"] | None = None
    tunables: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=100)
    tunable_form: TunableForm | None = None
    pcap: dict[str, Any] | None = None
    name: str | None = None
    append_to: AppendTo | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "ImportBody":
        """Exactly one of `path` and `upload_id`."""
        if (self.path is None) == (self.upload_id is None):
            raise ValueError("give exactly one of path and upload_id")
        return self


def remove_expired_uploads(data_dir: Path) -> None:
    """Remove every upload older than UPLOAD_TTL_S; a missing directory or an unremovable file is skipped."""
    d = data_dir / "uploads"
    cutoff = time.time() - UPLOAD_TTL_S
    try:
        entries = list(os.scandir(d))
    except FileNotFoundError:
        return
    for e in entries:
        try:
            if e.is_file(follow_symlinks=False) and e.stat(follow_symlinks=False).st_mtime < cutoff:
                os.unlink(e.path)
        except OSError:
            pass


def _packet_of(stream: dict[str, Any]) -> bytes | str:
    """The stream's packet bytes, or why a stored (perhaps hand-edited) stream has none usable."""
    try:
        return base64.b64decode(stream["packet"]["binary"], validate=True)
    except (KeyError, TypeError, binascii.Error, ValueError) as e:
        return f"no usable packet ({type(e).__name__}: {e})"


def _summaries(packets: list[bytes | str]) -> list[dict[str, str]]:
    """The protocol and info line of each packet; proto "?" with the reason for a packet that is a reason
    (`_packet_of`). Scapy executor only."""
    out = []
    for raw in packets:
        if isinstance(raw, str):
            out.append({"proto": "?", "info": raw})
            continue
        s = decode.summary(raw)
        out.append({"proto": s.proto, "info": s.info})
    return out


def failure(e: Exception) -> JSONResponse:
    """The error response for a library, stream, import or worker exception."""
    if isinstance(e, LibraryNotFound):
        return err(404, "not_found", f"No profile {e}")
    if isinstance(e, LibraryExists):
        return err(409, "exists", f"A profile {e} already exists")
    if isinstance(e, TooManyProfiles):
        return err(409, "too_many", str(e))
    if isinstance(e, StaleRevision):
        return JSONResponse({"error": "stale", "message": "The profile changed since it was read; reload it.",
                             "rev": e.current_rev}, status_code=409)
    if isinstance(e, SidecarUnreadable):
        return err(409, "sidecar_unreadable", str(e))
    if isinstance(e, LibraryError):
        return err(422, "invalid_name", e.reason)
    if isinstance(e, StreamError):
        return JSONResponse({"error": "invalid_stream", "message": str(e), "index": e.index,
                             "field": e.field}, status_code=422)
    if isinstance(e, trexapi.ProfileError):
        return err(422, "profile_error", e.message)
    if isinstance(e, convert.ConvertError):
        return err(422, "profile_error", str(e))
    if isinstance(e, OSError):
        return err(500, "storage_error", f"Cannot use the profile library files: {e}")
    if isinstance(e, ValueError):                 # a profile JSON that does not parse
        return err(422, "profile_error", f"The profile file does not parse: {e}")
    raise e


# What a route through `guarded` declares from `failure`, apart from its 404 (what is missing differs).
LIBRARY_ANSWERS: tuple[Item, ...] = (
    (409, "exists", "a profile of that name already exists"),
    (409, "sidecar_unreadable", "a stream's sidecar file does not parse; it is left as it is"),
    (409, "stale", "the profile changed since it was read; the body carries `rev`"),
    (409, "too_many", "the profile library is full"),
    (422, "invalid_name", "the profile name is not valid"),
    (422, "invalid_stream", "a stream is invalid; `index` and `field` name it"),
    (422, "profile_error", "the profile cannot be loaded or converted"),
    (500, "storage_error", "the profile library files cannot be read or written"),
)


def studio_router(library: ProfileLibrary, data_dir: Path) -> APIRouter:
    """The /api/studio routes over `library`; uploads live in `data_dir / "uploads"`."""
    router = APIRouter()
    lock = asyncio.Lock()            # the library: every read and write
    upload_lock = asyncio.Lock()     # the upload count check and the file creation
    upload_dir = data_dir / "uploads"

    async def io(fn: Callable[..., T], *args: Any) -> T:
        """`fn(*args)` in a thread, under the library lock."""
        async with lock:
            return await asyncio.to_thread(fn, *args)

    async def guarded(work: Callable[[], Awaitable[T]]) -> T | JSONResponse:
        """`work()`'s result, or the error response for what it raised."""
        try:
            return await work()
        except BodyRefused as e:
            return e.response
        except (LibraryNotFound, LibraryExists, TooManyProfiles, StaleRevision, SidecarUnreadable,
                trexapi.ProfileError, OSError, ValueError) as e:
            return failure(e)

    def listing() -> dict[str, Any]:
        """The GET /api/studio/profiles body (library thread)."""
        entries, errors = library.list()
        return {"profiles": entries, "load_errors": errors, "dir": str(library.path_of("profile").parent)}

    def entry_of(slug: str) -> LibraryEntry:
        """The listing row of `slug` (library thread); LibraryNotFound when it is not listed."""
        for e in library.list()[0]:
            if e["slug"] == slug:
                return e
        raise LibraryNotFound(slug)

    def diff_reply(res: DiffResult, rev: str) -> dict[str, Any]:
        """The reply to a write that edits streams; `warnings` are the enabled streams whose next names a
        disabled one (saved, but the server refuses the profile at assign)."""
        return {"rev": rev, "stream_count": len(res.streams), "renamed": res.renamed, "cleared": res.cleared,
                "warnings": simulate.disabled_next_pairs(res.streams)}

    def new_source() -> Source:
        """The source of a profile made from nothing."""
        return {"kind": "new", "path": None, "args": [], "tunables": None, "ports_differ": False,
                "imported": now()}

    @router.get("/api/studio/profiles", response_model=None,
                responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def list_profiles() -> dict[str, Any] | JSONResponse:
        """Every profile, the files that failed to load and the library directory."""
        return await guarded(lambda: io(listing))

    @router.post("/api/studio/profiles", response_model=None,
                 responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def create_profile(b: NameBody) -> LibraryEntry | JSONResponse:
        """An empty profile named `name` (source kind `new`); 409 exists or too_many, 422 invalid_name."""
        return await guarded(lambda: io(lambda: library.create(b.name, [], new_source())))

    @router.get("/api/studio/profiles/{slug}", response_model=None,
                responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def get_profile(slug: str) -> dict[str, Any] | JSONResponse:
        """The entry, the rev and the sidecar (a default one when the file is absent or bad)."""
        def read() -> dict[str, Any]:
            """Load the profile and its listing row."""
            p = library.get(slug)
            return {"entry": entry_of(slug), "rev": p.rev, "sidecar": p.sidecar}

        async def go() -> dict[str, Any]:
            """404 first, then the read."""
            library.path_of(slug)
            return await io(read)
        return await guarded(go)

    @router.delete("/api/studio/profiles/{slug}", response_model=None,
                   responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def delete_profile(slug: str) -> dict[str, Any] | JSONResponse:
        """Remove both files; the listing."""
        def delete() -> dict[str, Any]:
            """Delete, then list."""
            library.delete(slug)
            return listing()

        async def go() -> dict[str, Any]:
            """404 first, then the delete."""
            library.path_of(slug)
            return await io(delete)
        return await guarded(go)

    @router.post("/api/studio/profiles/{slug}/rename", response_model=None,
                 responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def rename_profile(slug: str, b: RenameBody) -> LibraryEntry | JSONResponse:
        """A new name (and the slug it derives) when the profile is still at `rev`; 409 stale or exists, 422
        invalid_name."""
        async def go() -> LibraryEntry:
            """404 first, then the rename."""
            library.path_of(slug)
            return await io(library.rename, slug, b.name, b.rev)
        return await guarded(go)

    @router.post("/api/studio/profiles/{slug}/duplicate", response_model=None,
                 responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def duplicate_profile(slug: str, b: NameBody) -> LibraryEntry | JSONResponse:
        """A copy under a new name; 409 exists or too_many, 422 invalid_name."""
        async def go() -> LibraryEntry:
            """404 first, then the copy."""
            library.path_of(slug)
            return await io(library.duplicate, slug, b.name)
        return await guarded(go)

    @router.get("/api/studio/profiles/{slug}/streams", response_model=None,
                responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile or stream")))
    async def stream_page(slug: str, offset: Annotated[int, Query(ge=0)] = 0,
                          limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = PAGE_MAX
                          ) -> dict[str, Any] | JSONResponse:
        """`limit` streams from `offset`, each with its packet length and a Scapy summary, plus every
        stream's name. A stored stream without a usable packet gets length null and summary proto "?" with
        the reason, so one bad stream in a hand-edited file does not hide the page."""
        async def go() -> dict[str, Any]:
            """404 first, then the read, then the summaries on the Scapy executor (outside the lock)."""
            library.path_of(slug)
            p = await io(library.get, slug)
            page = p.streams[offset:offset + limit]
            packets = [_packet_of(s) for s in page]
            summaries = await scapy_exec.arun(_summaries, packets)
            side = p.sidecar["streams"]
            rows = [{"index": offset + i, "stream": s, "length": None if isinstance(raw, str) else len(raw),
                     "summary": summary,
                     "has_editor": offset + i < len(side) and bool(side[offset + i].get("editor"))}
                    for i, (s, raw, summary) in enumerate(zip(page, packets, summaries, strict=True))]
            return {"rev": p.rev, "stream_count": len(p.streams), "names": [s.get("name") for s in p.streams],
                    "offset": offset, "rows": rows, "warnings": simulate.disabled_next_pairs(p.streams)}
        return await guarded(go)

    @router.get("/api/studio/profiles/{slug}/streams/{index}/sidecar", response_model=None,
                responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile or stream")))
    async def stream_sidecar(slug: str, index: int) -> dict[str, Any] | JSONResponse:
        """`{rev, entry}`: the sidecar entry of stream `index` (`{}` when it has none); 404 for an index out
        of range. Served per row because an entry can hold a 256 KiB editor document."""
        async def go() -> dict[str, Any] | JSONResponse:
            """404 first, then the read."""
            library.path_of(slug)
            p = await io(library.get, slug)
            if not 0 <= index < len(p.streams):
                return err(404, "not_found", f"No stream {index} (0-{len(p.streams) - 1})")
            side = p.sidecar["streams"]
            return {"rev": p.rev, "entry": side[index] if index < len(side) else {}}
        return await guarded(go)

    @router.get("/api/studio/profiles/{slug}/source", response_model=None,
                responses=answers(
                    *LIBRARY_ANSWERS,
                    (403, "source_unreadable", "the imported source file cannot be read (permission denied)"),
                    (404, "not_found", "no such profile"),
                    (404, "source_missing", "the imported source file no longer exists"),
                    (409, "no_source", "the profile was not imported from a .py file"),
                    (413, "too_large", "the source file is over 1 MiB"),
                    (422, "bad_request", "the source is not a .py file, or cannot be read as one")))
    async def profile_source(slug: str) -> dict[str, Any] | JSONResponse:
        """`{path, text}` of the .py a profile was imported from. Reads only the path its sidecar names:
        409 no_source unless the source kind is py, 404 source_missing for an absent file, 422 bad_request
        unless the resolved path ends in .py and is a regular file, 403 source_unreadable for a file the
        backend may not read, 413 too_large over 1 MiB. The text is UTF-8, undecodable bytes replaced."""
        def read(path: str) -> dict[str, Any] | JSONResponse:
            """The file's text, or the refusal (a plain thread). The file is opened without following a link
            or waiting on a pipe, and checked as a regular file on the open descriptor."""
            real = os.path.realpath(path)
            if not os.path.exists(real):
                return err(404, "source_missing", f"The source {path} no longer exists")
            if not real.endswith(".py"):
                return err(422, "bad_request", f"{path} is not a .py file")
            try:
                fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return err(404, "source_missing", f"The source {path} no longer exists")
            except PermissionError:
                return err(403, "source_unreadable", f"The source {path} cannot be read: permission denied")
            except OSError as e:                      # a link swapped in (ELOOP) or another open failure
                return err(422, "bad_request", f"{path} cannot be read as a .py file: {e.strerror}")
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                return err(422, "bad_request", f"{path} is not a .py file")
            with os.fdopen(fd, "rb") as f:
                data = f.read(SOURCE_MAX + 1)
            if len(data) > SOURCE_MAX:
                return err(413, "too_large", f"The source is over {SOURCE_MAX // (1024 * 1024)} MiB")
            return {"path": path, "text": data.decode("utf-8", errors="replace")}

        async def go() -> dict[str, Any] | JSONResponse:
            """404 first, then the sidecar's source, then the file."""
            library.path_of(slug)
            src = (await io(library.get, slug)).sidecar["source"]
            if src is None or src["kind"] != "py" or src["path"] is None:
                return err(409, "no_source", "The profile was not imported from a .py file")
            return await asyncio.to_thread(read, src["path"])
        return await guarded(go)

    @router.put("/api/studio/profiles/{slug}/streams", response_model=None,
                responses=answers(
                    *BODY_ANSWERS,
                    *LIBRARY_ANSWERS,
                    (404, "not_found", "no such profile or stream")))
    async def put_streams(slug: str, request: Request) -> dict[str, Any] | JSONResponse:
        """Apply a StreamsDiff (body at most 16 MiB); 409 stale, 422 invalid_stream naming
        the index and field, 411/413/422 bad_request from the body."""
        async def go() -> dict[str, Any] | JSONResponse:
            """404 first, then the body, then the write."""
            library.path_of(slug)
            raw = await read_json_capped(request, STREAMS_PUT_LIMIT)
            try:
                diff = StreamsDiff.model_validate(raw)
            except ValidationError as e:
                first = e.errors()[0]
                where = ".".join(str(x) for x in first["loc"]) or "body"
                return err(422, "bad_request", f"{where}: {first['msg']}")
            res, rev = await io(library.apply, slug, diff)
            return diff_reply(res, rev)
        return await guarded(go)

    @router.get("/api/studio/template", response_model=None,
                responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def template() -> dict[str, Any] | JSONResponse:
        """The default new stream, built in the worker."""
        return await guarded(trexapi.template_stream)

    @router.put("/api/studio/uploads", response_model=None,
                responses=answers(
                    (400, "bad_request", "the client disconnected during the upload"),
                    (411, "length_required", "the request has no valid Content-Length"),
                    (413, "too_large", "the upload is over 64 MiB"),
                    (422, "bad_request", "the name is over 255 characters or has an unusable suffix"),
                    (409, "too_many", "8 uploads are already kept; import or wait for one to expire"),
                    (500, "storage_error", "the upload cannot be stored")))
    async def put_upload(request: Request, name: Annotated[str, Query(max_length=UPLOAD_NAME_MAX)]
                         ) -> dict[str, Any] | JSONResponse:
        """Store the raw body as an upload. 422 bad_request for a `name` over 255 characters or with a suffix
        other than .yaml .yml .json .pcap .cap (both before the body is read), 411 without Content-Length,
        413 over 64 MiB, 409 too_many when 8 uploads are already kept (an upload is removed after an hour).
        The partial file is removed on a refusal and on a client disconnect."""
        suffix = Path(name).suffix.lower()
        if suffix not in convert.UPLOAD_SUFFIXES:
            return err(422, "bad_request", f"{name}: only {', '.join(sorted(convert.UPLOAD_SUFFIXES))} files "
                                           "upload (a .py imports from a path on the backend host)")
        declared = request.headers.get("content-length", "")
        if not declared.isascii() or not declared.isdigit():
            return err(411, "length_required", "Content-Length is required")
        too_large = err(413, "too_large", f"an upload is at most {UPLOAD_MAX // (1024 * 1024)} MiB")
        if int(declared) > UPLOAD_MAX:
            return too_large
        upload_id = secrets.token_hex(16)
        path = upload_dir / f"{upload_id}{suffix}"

        def create() -> int:
            """Remove expired uploads, count the rest and create the file when there is room; the count
            before the creation."""
            remove_expired_uploads(data_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            count = sum(1 for n in os.listdir(upload_dir) if not n.startswith("."))
            if count < UPLOADS_MAX:
                path.touch(exist_ok=False)
            return count

        try:
            async with upload_lock:
                count = await asyncio.to_thread(create)
        except OSError as e:
            return err(500, "storage_error", f"Cannot store the upload: {e}")
        if count >= UPLOADS_MAX:
            return err(409, "too_many", f"at most {UPLOADS_MAX} uploads are kept; import or wait for one "
                                        f"to expire ({UPLOAD_TTL_S // 60} min)")
        size = 0
        done = False
        try:
            f = await asyncio.to_thread(path.open, "wb")
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > UPLOAD_MAX:
                        return too_large
                    await asyncio.to_thread(f.write, chunk)
            finally:
                await asyncio.to_thread(f.close)
            done = True
        except ClientDisconnect:
            return err(400, "bad_request", "the client disconnected during the upload")
        except OSError as e:
            return err(500, "storage_error", f"Cannot store the upload: {e}")
        finally:
            if not done:
                path.unlink(missing_ok=True)
        return {"upload_id": upload_id, "name": name, "size": size}

    @router.post("/api/studio/import", response_model=None,
                 responses=answers(
                     *LIBRARY_ANSWERS,
                     (404, "not_found", "no such upload (it may have expired)"),
                     (422, "bad_request", "the import options (tunables, pcap) or the file are wrong")))
    async def import_profile(b: ImportBody) -> dict[str, Any] | JSONResponse:
        """Import a file into a new profile, or append it to `append_to` (409 stale on a changed profile).
        The file is checked and converted in the worker outside the library lock; a successful import of an
        upload deletes it, a failed one keeps it for a retry."""
        async def go() -> dict[str, Any] | JSONResponse:
            """Resolve and check the request, run the worker, then write."""
            if b.append_to is not None:
                library.path_of(b.append_to.slug)
            if b.upload_id is not None:
                found = sorted(upload_dir.glob(f"{b.upload_id}.*"))
                if not found:
                    return err(404, "not_found", f"No upload {b.upload_id} (it may have expired)")
                file = found[0]
            else:
                assert b.path is not None
                file = Path(os.path.abspath(os.path.expanduser(b.path)))
            kind = b.kind or convert.kind_of(str(file))
            if kind != "py" and (b.tunables or b.tunable_form is not None):
                return err(422, "bad_request", "tunables apply to a .py import only")
            if kind == "pcap":
                if b.pcap is None:
                    return err(422, "bad_request", "a pcap import needs its options (pcap)")
                try:
                    trexapi.check_pcap_options(b.pcap)  # type: ignore[arg-type]
                except ValueError as e:
                    return err(422, "bad_request", f"pcap: {e}")
            elif b.pcap is not None:
                return err(422, "bad_request", "pcap options apply to a pcap import only")
            pcap: trexapi.PcapOptions | None = b.pcap  # type: ignore[assignment]
            imp = await convert.import_file(str(file), kind, b.tunables, pcap)
            if b.append_to is not None:
                res, rev = await io(library.append, b.append_to.slug, imp.streams, b.append_to.rev)
                reply: dict[str, Any] = diff_reply(res, rev)
            else:
                source: Source = {
                    "kind": kind,  # type: ignore[typeddict-item]
                    "path": str(file) if b.upload_id is None else None, "args": list(b.tunables),
                    "tunables": None if b.tunable_form is None else b.tunable_form.model_dump(),
                    "ports_differ": imp.ports_differ, "imported": now()}
                name = b.name if b.name is not None else file.stem
                reply = dict(await io(library.create, name, imp.streams, source))
            if b.upload_id is not None:
                file.unlink(missing_ok=True)
            return reply
        return await guarded(go)

    @router.get("/api/studio/profiles/{slug}/export", response_model=None,
                responses=answers(
                    *LIBRARY_ANSWERS,
                    (404, "not_found", "no such profile"),
                    (422, "bad_request", "the format or stream query is wrong")))
    async def export(slug: str, format: str, stream: int | None = None) -> Response:
        """The profile as an attachment `<slug>.<ext>` (`<slug>-<i>.<ext>` for one stream): json is the
        library file itself, yaml the stl-sim form, py written by the worker (whole profiles only). Every
        format but the whole-profile json is a 422 invalid_stream when a stored stream breaks the rules."""
        async def go() -> Response:
            """404 first, then the checks, the read and the conversion."""
            library.path_of(slug)
            if format not in EXPORTS:
                return err(422, "bad_request", f"format: one of {', '.join(EXPORTS)}")
            if stream is not None and format == "py":
                return err(422, "bad_request", "stream: a Python export is of the whole profile")
            ext, media = EXPORTS[format]
            if format == "json" and stream is None:
                def read() -> bytes:
                    """The library file's bytes; LibraryNotFound when it is gone."""
                    try:
                        return library.path_of(slug).read_bytes()
                    except FileNotFoundError:
                        raise LibraryNotFound(slug) from None
                body = await io(read)
                return Response(body, media_type=media, headers=disposition(f"{slug}.{ext}"))
            # Every conversion reads the stored streams, which a hand-dropped file may hold unvalidated:
            # check them as simulate does, so no unchecked value reaches a converter (final review C1).
            # The raw JSON export above is the file's own bytes, not code.
            streams = parse_streams((await io(library.get, slug)).streams)
            check_profile(streams)
            if stream is not None:
                if not 0 <= stream < len(streams):
                    return err(422, "bad_request", f"stream: {stream} is not an index of a stream "
                                                   f"(0-{len(streams) - 1})")
                streams = [streams[stream]]
            if format == "yaml":
                text = convert.to_yaml(streams)
            elif format == "json":
                text = convert.to_json_text(streams)
            else:
                text = await trexapi.to_python(streams)
            fname = f"{slug}.{ext}" if stream is None else f"{slug}-{stream}.{ext}"
            return Response(text, media_type=media, headers=disposition(fname))
        return await guarded(go)

    def disposition(fname: str) -> dict[str, str]:
        """The attachment header for `fname` (a slug and a suffix, so it needs no quoting)."""
        return {"Content-Disposition": f'attachment; filename="{fname}"'}

    @router.post("/api/studio/profiles/{slug}/simulate", response_model=None,
                 responses=answers(*LIBRARY_ANSWERS, (404, "not_found", "no such profile")))
    async def simulate_profile(slug: str) -> simulate.SimReport | JSONResponse:
        """The offline simulation: the profile checked, round-tripped in the worker and reported."""
        async def go() -> simulate.SimReport:
            """404 first, then the read, the checks and the worker."""
            library.path_of(slug)
            streams = parse_streams((await io(library.get, slug)).streams)
            check_profile(streams)
            return simulate.report(streams, await trexapi.round_trip(streams))
        return await guarded(go)

    return router
