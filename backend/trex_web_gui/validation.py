"""Rules shared by the API bodies and the stores. Imports nothing from `api` or `store`."""
import ipaddress
import math
import re
from typing import Any

# Mirrors trex/utils/parsing_opts.py:88 without the update "+"/"-" suffix (canon:CMT-8).
MULT = r"^\d+(\.\d+)?(([kmg]?(bpsl1|pps|bps))|%)?$"

# The Dashboard's choices, restated from the frontend (the only values its UI can produce):
# tab = DashTabId, frontend/src/dashboardPrefs.ts; window_s = INTERVALS and layout = LAYOUTS,
# frontend/src/chartSeries.ts. A new choice in the UI must be added here too.
DASHBOARD_TABS = ("overview", "streams", "latency", "utilization")
DASHBOARD_WINDOWS_S = (60, 90, 120, 300)
DASHBOARD_LAYOUTS = (1, 2, 4)


def check_ports(v: list[int]) -> list[int]:
    """`v` itself when the ids are unique and non-negative; ValueError otherwise (a 422 bad_request to the
    client)."""
    if len(set(v)) != len(v) or min(v) < 0:
        raise ValueError("ports must be unique, non-negative port ids")
    return v


def check_duration(v: float) -> float:
    """`v` when it is -1 (no limit) or 0 < seconds <= 604800 (one week); ValueError otherwise."""
    if v != -1 and not 0 < v <= 604800:
        raise ValueError("duration is -1 (no limit) or 0 < seconds <= 604800")
    return v


HOST_RE = re.compile(r"[A-Za-z0-9_.-]{1,253}")


def check_host(v: str) -> str:
    """`v` when it is a hostname or IPv4 address (A-Z a-z 0-9 _ . -, 1-253 characters) or a bare IPv6 literal
    (no brackets, no zone); ValueError otherwise, so whitespace, `/`, `@` and brackets never get through."""
    if ":" in v:
        try:
            if "%" in v or len(v) > 45:
                raise ValueError(v)
            ipaddress.IPv6Address(v)
        except ValueError:
            raise ValueError("host must be a hostname, an IPv4 address or a bare IPv6 address") from None
        return v
    if not HOST_RE.fullmatch(v):
        raise ValueError("host must be a hostname, an IPv4 address or a bare IPv6 address")
    return v


# Stream text that reaches an exported .py (R14): `dump_to_code` quotes names and VM strings without escaping
# (trex_stl_streams.py:949-952 "__add_quotes"), so these are refused, never escaped. Printable ASCII 0x20-0x7e
# except `"` 0x22, `'` 0x27 and `\` 0x5c. Match with `fullmatch`.
NAME_RE = re.compile(r"[\x20-\x21\x23-\x26\x28-\x5b\x5d-\x7e]{1,80}")
VM_TEXT_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
STREAMS_MAX = 25_000  # streams per profile (R16)


def name_problem(name: object) -> str | None:
    """Why `name` is not a valid stream name (a string matching NAME_RE, or an int >= 1), or None."""
    if type(name) is str:
        ok = NAME_RE.fullmatch(name)
        return None if ok else "must be 1-80 printable ASCII characters without quote or backslash"
    if type(name) is int:
        return None if name >= 1 else "an integer name must be at least 1"
    return "must be a string or an integer"


def _vm_value_problem(value: object) -> str | None:
    """Why `value` may not appear in a VM instruction (numbers, bools, null, lists of numbers, safe text)."""
    if value is None or type(value) is bool or type(value) is int:
        return None
    if type(value) is float:
        return None if math.isfinite(value) else "must be a finite number"
    if type(value) is str:
        return None if VM_TEXT_RE.fullmatch(value) else "must match [A-Za-z0-9_.-]{1,64}"
    if type(value) is list:
        ok = all(type(v) is int or (type(v) is float and math.isfinite(v)) for v in value)
        return None if ok else "a list must hold finite numbers only"
    return "must be a number, a bool, null, a list of numbers or safe text"


# The scalars `dump_to_code` writes into the .py unquoted, with the type each must have (final review C1),
# as the `Stream` schema (studio/streams.py) allows: "bool", "int" (never a bool) or "num" (an int or a
# finite float), and whether null is allowed. A string there is code, so only these types are written.
# All in `STLStream.to_code` (backend/trex_web_gui/_vendor/trex/stl/trex_stl_streams.py:771 "def to_code"):
# vm.cache at :847 "vm.set_cached(%s)"; enabled, self_start, start_paused and isg at :856-862
# "'isg = %s'"; flow_stats.stream_id at :871 "flow_stats = %s(%s)"; stream_id, action_count,
# random_seed and core_id at :875-881 "'core_id = %s'"; flags at :883 "mac_dst_override_mode"; the
# mode keys and the rate value at :890-891 "mode_args +=". Names, `next` and VM values are quoted, and
# checked below.
CODE_SCALARS: tuple[tuple[str, str, bool], ...] = (
    ("enabled", "bool", False), ("self_start", "bool", False), ("start_paused", "bool", False),
    ("isg", "num", False), ("action_count", "int", False), ("core_id", "int", False), ("flags", "int", False),
    ("random_seed", "int", True), ("stream_id", "int", True),
    ("flow_stats.enabled", "bool", False), ("flow_stats.stream_id", "int", True),
    ("mode.total_pkts", "int", True), ("mode.pkts_per_burst", "int", True), ("mode.ibg", "num", True),
    ("mode.count", "int", True), ("mode.rate.value", "num", False), ("vm.cache", "int", True),
)
_KIND_TEXT = {"bool": "a bool", "int": "an integer", "num": "a finite number"}


def _scalar_ok(value: object, kind: str, nullable: bool) -> bool:
    """`value` has the type `kind` names (bool, int but never bool, or an int or finite float), or is None
    when `nullable`."""
    if value is None:
        return nullable
    if kind == "bool":
        return type(value) is bool
    if kind == "int":
        return type(value) is int
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _scalar_problem(stream: dict[str, Any]) -> tuple[str, str] | None:
    """The first CODE_SCALARS field present in `stream` with the wrong type, or None. A field under a key
    that is not an object is skipped: the worker cannot build that stream at all."""
    for path, kind, nullable in CODE_SCALARS:
        *parents, key = path.split(".")
        at: object = stream
        for p in parents:
            at = at.get(p) if isinstance(at, dict) else None
        if isinstance(at, dict) and key in at and not _scalar_ok(at[key], kind, nullable):
            return (path, f"must be {_KIND_TEXT[kind]}" + (" or null" if nullable else ""))
    return None


def code_problem(stream: object) -> tuple[str, str] | None:
    """The first (field, reason) in `stream` that is unsafe to write into Python, or None."""
    if not isinstance(stream, dict):
        return ("stream", "must be an object")
    for key in ("name", "next"):
        if key in stream and (why := name_problem(stream[key])):
            return (key, why)
    if (p := _scalar_problem(stream)) is not None:
        return p
    vm = stream.get("vm")
    if not isinstance(vm, dict):
        return None
    split = vm.get("split_by_var")
    if split is not None and split != "" and (type(split) is not str or not VM_TEXT_RE.fullmatch(split)):
        return ("vm.split_by_var", "must be empty or match [A-Za-z0-9_.-]{1,64}")
    instructions = vm.get("instructions")
    if not isinstance(instructions, list):
        return None
    for j, ins in enumerate(instructions):
        if not isinstance(ins, dict):
            return (f"vm.instructions[{j}]", "must be an object")
        for k, v in ins.items():
            if (why := _vm_value_problem(v)) is not None:
                return (f"vm.instructions[{j}].{k}", why)
    return None


def check_code_safe(streams: list[dict[str, Any]]) -> None:
    """None when every name, `next` and VM string in `streams` is safe to quote into an exported .py and
    every scalar written unquoted (CODE_SCALARS) has its type; ValueError("stream <i> <field>: <reason>")
    for the first that is not."""
    for i, s in enumerate(streams):
        if (p := code_problem(s)) is not None:
            raise ValueError(f"stream {i} {p[0]}: {p[1]}")
