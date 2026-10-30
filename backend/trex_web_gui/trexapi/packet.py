"""The packet engine (P4b plan D1): one persistent packet worker subprocess, started on first use, kept up,
and killed with the backend. Calls are serialised over its stdin/stdout pipes.

Lifecycle rules (plan Global Constraints, "The worker"):
(a) the worker exits on stdin EOF; (b) it dies with its parent; (c) `close()` kills the group at once and
nothing respawns after it; (d) a cancelled call, or a reply that does not echo the request id, kills the
group; (e) `timeout_s` runs from when the request is written, and waiting for the lock is bounded
separately by PACKET_QUEUE_S and kills nothing; (f) a start is bounded by PACKET_START_S and a call
attempts at most one; (g) each worker has a private working directory."""
import asyncio
import base64
import contextlib
import json
import os
import signal
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, TypedDict

from trex_web_gui.studio.packetdoc import DocError, PacketDoc, parse_doc
from trex_web_gui.validation import code_problem

from .errors import TrexApiError
from .profiles import MAX_RESULT, STUDIO_TIMEOUT_S, WORKER_AS_LIMIT, _boot, _env, _reap

PACKET_TIMEOUT_S = 5.0        # one editor op, from the request being written
PACKET_QUEUE_S = 15.0         # waiting for an earlier call to finish
PACKET_START_S = 30.0         # spawn to the ready line
CLOSE_WAIT_S = 5.0            # close() waiting for the call in flight to release the lock
MODULE = "trex_web_gui.trexapi.packet_worker"


class PacketError(TrexApiError):
    """A packet worker failure. `restarted` is True when the worker was killed (the next call starts a
    fresh one), False when it is still the same worker (a refusal, a busy engine, a closed engine)."""

    def __init__(self, message: str, restarted: bool = False) -> None:
        """Keep `restarted` next to the message."""
        super().__init__(message)
        self.restarted = restarted


class PacketBusy(PacketError):
    """The queue wait passed PACKET_QUEUE_S: an earlier call is still running. Nothing was killed."""


class Definitions(TypedDict):
    etag: str
    layerIds: list[str]
    protocols: list[dict[str, Any]]
    feInstructionParameters: list[dict[str, Any]]
    feInstructions: list[dict[str, Any]]
    feParameters: list[dict[str, Any]]
    feTemplates: list[dict[str, Any]]


class FieldEngineResult(TypedDict):
    instructions: dict[str, Any]
    error: str | None


class BuildResult(TypedDict):
    binary: str | None
    data: list[dict[str, Any]]
    field_engine: FieldEngineResult | None
    error: str | None


class DocFromBinary(TypedDict):
    doc: PacketDoc
    warnings: list[str]


class ReconstructEdit(TypedDict):
    doc: PacketDoc
    warnings: list[str]


class Decompiled(TypedDict):
    instructions: list[dict[str, Any]]
    global_parameters: dict[str, Any]
    warnings: list[str]


class PcapRow(TypedDict):
    index: int
    length: int
    summary: str


class PcapPage(TypedDict):
    total: int
    rows: list[PcapRow]


class StreamParts(TypedDict):
    packet: dict[str, str]
    vm: dict[str, Any]


@dataclass
class _Worker:
    """One worker process, its private working directory and its ready line."""
    proc: asyncio.subprocess.Process
    tmp: tempfile.TemporaryDirectory[str]
    ready: dict[str, Any] | None = None
    gone: bool = False


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the worker's whole process group; nothing if it is already gone."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


class PacketEngine:
    """Owns the packet worker. `call` runs one op; `definitions` and `build` are the typed ones."""

    def __init__(self) -> None:
        """No worker yet: the first call starts it."""
        self._worker: _Worker | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._starts = 0
        self._next_id = 0
        self._defs: Definitions | None = None
        self._defs_start = 0

    @property
    def pid(self) -> int | None:
        """The current worker's pid, or None when none is running."""
        return self._worker.proc.pid if self._worker else None

    @property
    def starts(self) -> int:
        """How many workers have been started."""
        return self._starts

    @property
    def closed(self) -> bool:
        """True once close() has run; every later call raises."""
        return self._closed

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        """The current worker process (the test seam for its stdin and pid), or None."""
        return self._worker.proc if self._worker else None

    @property
    def ready(self) -> dict[str, Any] | None:
        """The current worker's ready line, or None when none is running or it has not sent one yet."""
        return self._worker.ready if self._worker else None

    async def call(self, op: str, args: dict[str, Any], timeout_s: float = PACKET_TIMEOUT_S
                   ) -> dict[str, Any]:
        """Run `op` in the worker and return its ok result (`id` removed). Starts the worker when none is
        up. PacketError: `restarted=True` after a timeout, overflow, EOF, malformed or mismatched reply,
        memory failure or start failure (the worker was killed); `restarted=False` for the worker's own
        refusal, a busy engine, or a closed engine."""
        if self._closed:
            raise _closed()
        try:
            async with asyncio.timeout(PACKET_QUEUE_S):
                await self._lock.acquire()
        except TimeoutError:
            raise PacketBusy("the packet engine is busy") from None
        try:
            if self._closed:
                raise _closed()
            if self._worker is None or self._worker.proc.returncode is not None:
                await self._discard()
                await self._start()
                if self._closed:                       # close() ran while the worker was starting
                    await self._discard()
                    raise _closed()
            return await self._exchange(op, args, timeout_s)
        finally:
            self._lock.release()

    async def definitions(self) -> Definitions:
        """The protocol and field-engine definitions, with `etag` and `layerIds`; fetched once per worker
        start."""
        if self._defs is not None and self._defs_start == self._starts and self._worker is not None:
            return self._defs
        res = await self.call("definitions", {})
        res.pop("ok", None)
        self._defs = res                                    # type: ignore[assignment]
        self._defs_start = self._starts
        return self._defs                                   # type: ignore[return-value]

    async def build(self, packet: list[dict[str, Any]], fe: dict[str, Any] | None) -> BuildResult:
        """Build the packet model `packet`, compiling the field-engine model `fe` when it has
        instructions."""
        res = await self.call("build", {"packet": packet, "fe": fe})
        res.pop("ok", None)
        return res                                          # type: ignore[return-value]

    async def payload_classes(self, packet: list[dict[str, Any]]) -> list[str]:
        """The protocols the last layer of `packet` may carry."""
        res = await self.call("payload_classes", {"packet": packet})
        return list(res["classes"])

    async def param_values(self, packet: list[dict[str, Any]], fe: dict[str, Any] | None, param_id: str
                           ) -> dict[str, str]:
        """The values the Field Engine parameter `param_id` may take for this packet and instructions."""
        res = await self.call("param_values", {"packet": packet, "fe": fe, "param_id": param_id})
        return dict(res["map"])

    async def doc_from_binary(self, binary: str, vm: dict[str, Any] | None) -> DocFromBinary:
        """A document for the base64 packet bytes `binary`, with the Field Engine decompiled from `vm`.
        It holds literals only (plan D13)."""
        res = await self.call("doc_from_binary", {"binary": binary, "vm": vm})
        return {"doc": _parse_reply(res["doc"]), "warnings": list(res["warnings"])}

    async def reconstruct_edit(self, doc: PacketDoc, binary: str, edited: dict[str, Any] | None
                               ) -> ReconstructEdit:
        """The document after a byte edit (plan D12): `edited` `{layer, field}` names the field the byte
        belongs to, or is None. Falls back to re-deriving the packet from `binary`, with a warning."""
        res = await self.call("reconstruct_edit", {"doc": doc, "binary": binary, "edited": edited})
        return {"doc": _parse_reply(res["doc"]), "warnings": list(res["warnings"])}

    async def decompile(self, binary: str, vm: dict[str, Any]) -> Decompiled:
        """The Field Engine instructions of `vm` as editor parameters (string values), with warnings."""
        res = await self.call("decompile", {"binary": binary, "vm": vm})
        res.pop("ok", None)
        return res                                          # type: ignore[return-value]

    async def templates(self) -> list[dict[str, str]]:
        """The server's templates as `{id, name}`."""
        res = await self.call("templates", {})
        return list(res["templates"])

    async def template(self, template_id: str) -> PacketDoc:
        """The server template `template_id` as a document."""
        res = await self.call("template", {"id": template_id})
        try:
            return parse_doc(json.loads(base64.b64decode(res["data"]).decode("utf-8")))
        except (ValueError, DocError) as e:
            raise PacketError(f"template {template_id} is not a packet document: {e}") from None

    async def pcap_list(self, path: str, offset: int, limit: int) -> PcapPage:
        """Rows `offset` to `offset + limit` of the capture at `path` and its packet count. A file that is
        not a capture is PacketError("not a pcap: ...")."""
        res = await self.call("pcap_list", {"path": path, "offset": offset, "limit": limit},
                              timeout_s=STUDIO_TIMEOUT_S)
        return {"total": res["total"], "rows": res["rows"]}

    async def pcap_packet(self, path: str, index: int) -> str:
        """Packet `index` of the capture at `path`, base64."""
        res = await self.call("pcap_packet", {"path": path, "index": index}, timeout_s=STUDIO_TIMEOUT_S)
        return str(res["binary"])

    async def to_stream(self, packet: list[dict[str, Any]], fe: dict[str, Any] | None) -> StreamParts:
        """`{packet: {binary, meta}, vm}` for a stream. A packet or Field Engine error is
        PacketError(restarted=False) carrying the message."""
        res = await self.call("to_stream", {"packet": packet, "fe": fe})
        return {"packet": res["packet"], "vm": res["vm"]}

    async def vm_python(self, packet: list[dict[str, Any]], fe: dict[str, Any] | None) -> str:
        """The Python that builds this document's Field Engine (upstream `to_code`, from `vm = STLVM()` to
        the line before `stream = STLStream(`). The compiled VM must pass `code_problem` first."""
        parts = await self.to_stream(packet, fe)
        if (problem := code_problem({"vm": parts["vm"]})) is not None:
            raise PacketError(f"unsafe for code: {problem[0]}: {problem[1]}")
        res = await self.call("to_code", {"binary": parts["packet"]["binary"], "vm": parts["vm"]})
        return str(res["code"])

    async def close(self) -> None:
        """Mark the engine closed and kill the worker's group at once, without waiting for the lock. A call
        in flight raises PacketError("the packet engine was closed"); so does every later call."""
        self._closed = True
        w = self._worker
        if w is not None:
            _kill_group(w.proc)
        try:
            async with asyncio.timeout(CLOSE_WAIT_S):
                await self._lock.acquire()
        except TimeoutError:
            return
        try:
            await self._discard()
        finally:
            self._lock.release()

    async def _discard(self) -> None:
        """Kill, reap and forget the current worker, and remove its working directory. Idempotent. Only
        called with the lock held (or by close() once the call in flight has released it)."""
        w, self._worker = self._worker, None
        if w is None or w.gone:
            return
        w.gone = True
        _kill_group(w.proc)
        try:
            await _reap(w.proc)
        finally:
            w.tmp.cleanup()

    async def _start(self) -> None:
        """Spawn a worker and wait for its ready line, at most PACKET_START_S. One attempt: a failure kills
        the worker and raises PacketError(restarted=True)."""
        tmp = tempfile.TemporaryDirectory(prefix="trex-web-gui-packet-")
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-B", "-c", _boot(WORKER_AS_LIMIT, MODULE),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=tmp.name, limit=MAX_RESULT + 1,
                env={**_env(), "TREX_WEB_GUI_PARENT_PID": str(os.getpid())}, start_new_session=True)
        except BaseException:
            tmp.cleanup()
            raise
        self._starts += 1
        worker = self._worker = _Worker(proc, tmp)
        assert proc.stdout is not None
        if self._closed:                               # close() ran during the spawn and saw no worker
            await self._discard()
            raise _closed()
        try:
            async with asyncio.timeout(PACKET_START_S):
                raw = await proc.stdout.readline()
            ready = json.loads(raw) if raw else None
            if not isinstance(ready, dict) or ready.get("ready") is not True:
                raise ValueError("no ready line")
            worker.ready = ready
        except BaseException as e:
            await self._discard()
            if self._closed:
                raise _closed() from None
            if isinstance(e, asyncio.CancelledError):
                raise
            why = (f"did not start within {PACKET_START_S:g} s" if isinstance(e, TimeoutError)
                   else "exited before it was ready")
            raise PacketError(f"the packet worker {why}", restarted=True) from None

    async def _exchange(self, op: str, args: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        """Write one request and read its reply. Every failure kills the worker; a cancellation does too, so
        the next call cannot read a stale reply."""
        worker = self._worker
        assert worker is not None and worker.proc.stdin is not None and worker.proc.stdout is not None
        self._next_id += 1
        rid = self._next_id
        line = json.dumps({"id": rid, "op": op, "args": args}).encode() + b"\n"
        problem: str | None = None
        raw = b""
        try:
            async with asyncio.timeout(timeout_s):
                try:
                    worker.proc.stdin.write(line)
                    await worker.proc.stdin.drain()
                    raw = await worker.proc.stdout.readline()
                except (BrokenPipeError, ConnectionResetError):
                    raw = b""
        except TimeoutError:
            problem = f"did not answer within {timeout_s:g} s"
        except ValueError:                       # StreamReader.readline: the line passed its limit
            problem = f"produced more than {MAX_RESULT // (1024 * 1024)} MiB in one reply"
        except BaseException:                    # CancelledError and anything unforeseen
            await self._discard()
            raise
        reply: Any = None
        if problem is None:
            if not raw:
                problem = "exited without a reply"
            else:
                try:
                    reply = json.loads(raw)
                except ValueError:
                    problem = "sent a malformed reply"
        if problem is None and (not isinstance(reply, dict) or reply.get("id") != rid):
            problem = "sent a reply for another request"
        if problem is None and reply.get("error") == "MemoryError" and not reply.get("ok"):
            problem = f"ran out of memory (limit {WORKER_AS_LIMIT / 1024**3:g} GiB)"
        if problem is not None:
            await self._discard()
            if self._closed:
                raise _closed()
            raise PacketError(f"the packet worker {problem}; it was restarted", restarted=True)
        reply.pop("id")
        if not reply.get("ok"):
            raise PacketError(str(reply.get("error") or "the packet worker refused the request"))
        return dict(reply)


def _parse_reply(raw: Any) -> PacketDoc:
    """The worker's document, checked again here: PacketError when it does not parse."""
    try:
        return parse_doc(raw)
    except DocError as e:
        raise PacketError(f"the packet worker returned a document that does not parse: {e}") from None


def _closed() -> PacketError:
    """The error for a call on, or in flight during, a closed engine."""
    return PacketError("the packet engine was closed", restarted=False)
