"""The profile worker subprocess (plan §4.3): load a profile, import a pcap, export Python, round-trip and
build the template stream, each in a fresh worker; and rebuild a profile's streams here."""
import asyncio
import contextlib
import copy
import ipaddress
import json
import math
import os
import signal
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, TypedDict

from trex_web_gui.validation import NAME_RE, STREAMS_MAX, check_code_safe

from .errors import TrexApiError

PROFILE_SUFFIXES = (".py", ".yaml", ".yml", ".json")
MAX_RESULT = 32 * 1024 * 1024        # bytes of worker stdout accepted; more kills the worker
# The same for a pcap import (a bound on the result, not the upload), and for every op that takes a library
# profile back (round_trip, to_python, load_profile's max_result), so an imported profile stays usable.
MAX_RESULT_PCAP = 96 * 1024 * 1024
WORKER_AS_LIMIT = 4 * 1024**3        # the worker's RLIMIT_AS, set before it reads anything
# Twice the slowest Studio operation at STREAMS_MAX (dump_to_code, 6.1-6.4 s measured by P4a Task 1).
STUDIO_TIMEOUT_S = 13.0
ROUND_TRIP_DIFFS = 20                # difference lines a RoundTrip keeps
DIFF_TEXT = 80                       # characters of a value shown in a difference line
U32 = 4294967295


class ProfileError(TrexApiError):
    pass


class Tunable(TypedDict):
    dest: str
    flag: str
    kind: Literal["int", "float", "str", "bool_true", "bool_false", "choice", "raw"]
    default: object
    choices: list[object] | None
    help: str


class StreamSummary(TypedDict):
    index: int
    name: str | None
    enabled: bool
    self_start: bool
    mode: str
    rate: str
    packet: str
    length: int
    next: str | None
    has_vm: bool


@dataclass(frozen=True)
class LoadedProfile:
    path: str
    kind: Literal["py", "yaml", "json"]
    tunables: list[Tunable]
    streams_by_port: dict[int, list[dict[str, Any]]]
    summary: list[StreamSummary]       # the first SUMMARY_CAP streams (profile_worker.py)
    stream_count: int


async def load_profile(path: str, ports: list[int], tunables: list[str],
                       timeout_s: float = 10.0, *, max_result: int = MAX_RESULT) -> LoadedProfile:
    """Load a profile in a worker subprocess. The worker and its process group are killed on every exit
    path. ProfileError: unsupported suffix, missing file, no ports, timeout, a result over `max_result`
    bytes (MAX_RESULT_PCAP for a library profile, which a pcap import can make that large) or none,
    running out of memory, or the profile's own error. The profile is arbitrary code."""
    path = os.path.expanduser(path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in PROFILE_SUFFIXES:
        raise ProfileError(f"{os.path.basename(path)}: only {', '.join(PROFILE_SUFFIXES)} profiles load here "
                           "(a pcap is imported in the Studio).")
    if not os.path.isfile(path):
        raise ProfileError(f"{path} does not exist or is not a file.")
    if not ports:
        raise ProfileError("No target ports given.")
    req = {"op": "load", "path": os.path.abspath(path), "ports": ports, "tunables": tunables}
    res = await _run_worker(req, os.path.dirname(os.path.abspath(path)), max_result, timeout_s,
                            os.path.basename(path))
    return LoadedProfile(path=os.path.abspath(path), kind=res["kind"], tunables=res["tunables"],
                         streams_by_port={int(k): v for k, v in res["streams_by_port"].items()},
                         summary=res["summary"], stream_count=res["stream_count"])


class AddressRange(TypedDict):
    start: str                       # a dotted IPv4 address
    count: int                       # >= 1; start + count - 1 <= 255.255.255.255


class PcapOptions(TypedDict):
    ipg_usec: float | None           # None: the gaps from the pcap timestamps; else >= 0.001 us
    speedup: float                   # > 0; divides the gaps
    loop_count: int                  # 0-65535; 0 loops forever
    name_prefix: str                 # 0-40 characters of NAME_RE; empty keeps load_pcap's integer names
    rewrite_src: AddressRange | None
    rewrite_dst: AddressRange | None


class RoundTrip(NamedTuple):
    equal: bool
    differences: list[str]           # <= ROUND_TRIP_DIFFS "stream <i> <key>: ..." then "streams: n -> m"


def _number(v: object) -> bool:
    """True for an int or a finite float (never a bool)."""
    return (type(v) is int or type(v) is float) and math.isfinite(v)


def check_pcap_options(opts: PcapOptions) -> None:
    """None when `opts` is a valid PcapOptions; ValueError naming the first bad field otherwise."""
    if not isinstance(opts, dict):
        raise ValueError("pcap options must be an object")
    want = set(PcapOptions.__annotations__)
    if set(opts) != want:
        raise ValueError(f"pcap options have the keys {sorted(want)}, not {sorted(opts)}")
    if not (_number(opts["speedup"]) and opts["speedup"] > 0):
        raise ValueError("speedup: a number above 0")
    ipg = opts["ipg_usec"]
    # load_pcap refuses `ipg_usec < 0.001 * speedup` (trex_stl_streams.py:1262-1264 "0.001 * speedup")
    if ipg is not None and not (_number(ipg) and ipg >= 0.001 * opts["speedup"]):
        raise ValueError("ipg_usec: null (from the timestamps) or at least 0.001 us after the speedup")
    if not (type(opts["loop_count"]) is int and 0 <= opts["loop_count"] <= 65535):
        raise ValueError("loop_count: an integer 0-65535 (0 loops forever)")
    prefix = opts["name_prefix"]
    if not (type(prefix) is str and (prefix == "" or (len(prefix) <= 40 and NAME_RE.fullmatch(prefix)))):
        raise ValueError("name_prefix: 0-40 printable ASCII characters without quote or backslash")
    for key in ("rewrite_src", "rewrite_dst"):
        rw = opts[key]
        if rw is None:
            continue
        shape = f"{key}: {{start: a dotted IPv4 address, count: an integer >= 1}}"
        if not (isinstance(rw, dict) and set(rw) == {"start", "count"} and type(rw["start"]) is str
                and type(rw["count"]) is int and rw["count"] >= 1):
            raise ValueError(shape)
        try:
            start = int(ipaddress.IPv4Address(rw["start"]))
        except ValueError:
            raise ValueError(shape) from None
        if start + rw["count"] - 1 > U32:
            raise ValueError(f"{key}: {rw['start']} + {rw['count']} addresses pass 255.255.255.255")


async def import_pcap(path: str, opts: PcapOptions,
                      timeout_s: float = STUDIO_TIMEOUT_S) -> list[dict[str, Any]]:
    """The streams of a pcap, one single-burst stream per packet chained in a loop (`STLProfile.load_pcap`),
    built in the worker with `cwd` the file's own directory. ValueError: bad `opts`. ProfileError: a missing
    file, more than STREAMS_MAX packets (counted before any stream is built), a rewrite over a packet without
    IPv4, a result over MAX_RESULT_PCAP, a timeout, or TRex's or Scapy's own error."""
    check_pcap_options(opts)
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ProfileError(f"{path} does not exist or is not a file.")
    req = {"op": "pcap", "path": path, "opts": opts, "streams_max": STREAMS_MAX}
    res = await _run_worker(req, os.path.dirname(path), MAX_RESULT_PCAP, timeout_s, os.path.basename(path))
    streams: list[dict[str, Any]] = res["streams"]
    return streams


async def to_python(streams: list[dict[str, Any]], timeout_s: float = STUDIO_TIMEOUT_S) -> str:
    """The streams as a Python profile (`STLProfile.dump_to_code`), written in the worker. ProfileError
    before any worker starts when a name, `next` or VM string is unsafe to quote into code (R14), or a scalar
    written unquoted is not of its type (`validation.CODE_SCALARS`); otherwise as `_run_worker`."""
    try:
        check_code_safe(streams)
    except ValueError as e:
        raise ProfileError(f"The Python export refused the profile: {e}") from None
    with tempfile.TemporaryDirectory() as cwd:
        res = await _run_worker({"op": "to_python", "streams": streams}, cwd, MAX_RESULT_PCAP, timeout_s,
                                "The Python export")
    code: str = res["code"]
    return code


def _flat(v: Any, prefix: str = "") -> dict[str, Any]:
    """A stream's values by dotted key: nested objects flattened, empty objects, lists and scalars kept
    whole."""
    if not isinstance(v, dict) or not v:
        return {prefix: v}
    out: dict[str, Any] = {}
    for k, x in v.items():
        out.update(_flat(x, f"{prefix}.{k}" if prefix else str(k)))
    return out


def _shown(v: Any) -> str:
    """A value for a difference line: its JSON, cut to DIFF_TEXT characters."""
    text = json.dumps(v)
    return text if len(text) <= DIFF_TEXT else text[: DIFF_TEXT - 3] + "..."


def _differences(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Up to ROUND_TRIP_DIFFS lines "stream <i> <key>: <before> -> <after>" (`absent` for a missing key; a
    nested key is dotted), then a line for a changed stream count, which the cap never drops."""
    out: list[str] = []
    for i, (b, a) in enumerate(zip(before, after, strict=False)):
        fb, fa = _flat(b), _flat(a)
        for key in list(fb) + [k for k in fa if k not in fb]:
            if key not in fa or key not in fb or fb[key] != fa[key] or type(fb[key]) is not type(fa[key]):
                was = _shown(fb[key]) if key in fb else "absent"
                now = _shown(fa[key]) if key in fa else "absent"
                out.append(f"stream {i} {key}: {was} -> {now}")
    out = out[:ROUND_TRIP_DIFFS]
    if len(before) != len(after):              # kept whatever the cap
        out.append(f"streams: {len(before)} -> {len(after)}")
    return out


async def round_trip(streams: list[dict[str, Any]], timeout_s: float = STUDIO_TIMEOUT_S) -> RoundTrip:
    """The streams through `STLProfile.from_json(...).to_json()` in the worker, compared with the input:
    `equal` when nothing changed, else the first ROUND_TRIP_DIFFS differences. ProfileError as
    `_run_worker`."""
    with tempfile.TemporaryDirectory() as cwd:
        res = await _run_worker({"op": "round_trip", "streams": streams}, cwd, MAX_RESULT_PCAP, timeout_s,
                                "The round trip")
    diffs = _differences(streams, res["streams"])
    return RoundTrip(not diffs, diffs)


_template: dict[str, Any] | None = None


async def template_stream() -> dict[str, Any]:
    """The default new stream (a 60-byte UDP packet, 64 with FCS, continuous at 1 pps, unnamed) in its JSON
    form, built in the worker once per process; each call returns a fresh copy."""
    global _template
    if _template is None:
        with tempfile.TemporaryDirectory() as cwd:
            res = await _run_worker({"op": "template"}, cwd, MAX_RESULT, STUDIO_TIMEOUT_S,
                                    "The template stream")
        _template = res["stream"]
    return copy.deepcopy(_template)


async def _run_worker(req: dict[str, Any], cwd: str, cap: int, timeout_s: float,
                      label: str) -> dict[str, Any]:
    """Run one worker operation: `python -I -B` with the address-space limit WORKER_AS_LIMIT, the PATH/HOME
    environment and its own session, `req` as JSON on stdin and `cwd` as its directory. The worker's whole
    process group is killed and reaped on every exit path. Returns the worker's ok result; ProfileError,
    starting with `label`, on a timeout, more than `cap` bytes of output, no result, running out of memory,
    or the operation's own error."""
    limit = WORKER_AS_LIMIT
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-B", "-c", _boot(limit),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=cwd, env=_env(), start_new_session=True)
    try:
        out = await asyncio.wait_for(_converse(proc, json.dumps(req).encode(), cap), timeout_s)
    except TimeoutError:
        raise ProfileError(f"{label} did not finish within {timeout_s:g} s; "
                           "the loader was stopped.") from None
    finally:
        # The profile is arbitrary code: kill its whole process group (children included), on every exit path.
        # Limit: a child that called setsid()/setpgid() (or double-forked into a new session) leaves the group
        # and survives; only the worker's own group is contained.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await _reap(proc)
    if len(out) > cap:
        hint = (f" A pcap import holds at most {STREAMS_MAX} streams, and a capture of large packets fits "
                "fewer." if req.get("op") == "pcap" else "")
        raise ProfileError(f"{label}: the loader produced more than {cap // (1024 * 1024)} MiB "
                           f"and was stopped.{hint}")
    line = out.decode(errors="replace").strip().splitlines()[-1:] or [""]
    try:
        res = json.loads(line[0])
    except ValueError:
        res = None
    if not isinstance(res, dict):
        raise ProfileError(f"{label}: the loader exited without a result (exit code {proc.returncode}).")
    if res.get("ok"):
        return res
    if res.get("error") == "MemoryError" and "traceback" not in res:
        raise ProfileError(f"{label}: the loader ran out of memory (limit {limit / 1024**3:g} GiB).")
    detail = res.get("traceback") or ""
    printed = "\n".join((res.get("output") or "").strip().splitlines()[-20:])
    raise ProfileError(f"{label}: {res.get('error')}" + (f"\n{detail}" if detail else "")
                       + (f"\nProfile output (last lines):\n{printed}" if printed else ""))


REAP_S = 2.0         # bound on draining the pipe and waiting for the worker after the kill


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Wait for the killed worker without ever hanging.

    Process.wait() also waits for stdout to disconnect, and after an over-cap read the StreamReader still
    holds buffered data, so the transport stays paused and never sees EOF: drain to EOF first. If something
    still holds the pipe (a child that escaped the group), give up after REAP_S and close the transport.
    proc._transport (BaseSubprocessTransport) is private. The public alternative is an os.pipe() read through
    loop.connect_read_pipe(), so wait() never depends on stdout; this fallback runs only when a process
    escaped the group, so the smaller change was kept. close() releases the pipes and the child watcher."""
    assert proc.stdout is not None

    async def drain_and_wait() -> None:
        """Read stdout to EOF, then wait for the exit."""
        while await proc.stdout.read(1 << 20):    # type: ignore[union-attr]
            pass
        await proc.wait()

    try:
        await asyncio.wait_for(drain_and_wait(), REAP_S)
    except TimeoutError:
        proc._transport.close()                    # type: ignore[attr-defined]


async def _converse(proc: asyncio.subprocess.Process, req: bytes, cap: int) -> bytes:
    """Send the request, then read stdout to EOF or past `cap` bytes (overflow)."""
    assert proc.stdin is not None and proc.stdout is not None
    try:
        proc.stdin.write(req)
        await proc.stdin.drain()
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass                       # the worker died early; the read below returns EOF and we report no result
    return await _read_capped(proc.stdout, cap)


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> bytes:
    """Read to EOF, or until more than `cap` bytes are held (up to one 1 MiB chunk past it)."""
    buf = bytearray()
    while len(buf) <= cap:
        chunk = await stream.read(1 << 20)
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _backend_root() -> str:
    """The directory that holds the trex_web_gui package (the one the worker puts on sys.path)."""
    import trex_web_gui
    return os.path.dirname(os.path.dirname(os.path.abspath(trex_web_gui.__file__)))


def _boot(limit: int, module: str = "trex_web_gui.trexapi.profile_worker") -> str:
    """The worker's `-c` program: first the address-space limit (before sys.path is touched or the request
    read), then the backend root on sys.path and the worker `module` run as __main__. -I ignores every
    PYTHON* variable, PYTHONPATH included, and drops the cwd from sys.path, so the bootstrap puts the
    backend root on sys.path itself."""
    return (f"import resource; resource.setrlimit(resource.RLIMIT_AS, ({int(limit)}, {int(limit)})); "
            "import sys, runpy; sys.path.insert(0, " + repr(_backend_root()) + "); "
            f"runpy.run_module({module!r}, run_name='__main__')")


def _env() -> dict[str, str]:
    """The worker's whole environment: PATH and HOME, nothing else."""
    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}


def streams_from_json(streams: list[dict[str, Any]]) -> list[Any]:
    """Executor side: rebuild STLStream objects from the worker's JSON (round-trip measured exact)."""
    from .loader import load
    load()
    from trex.stl.api import STLProfile
    return list(STLProfile.from_json(streams).get_streams())
