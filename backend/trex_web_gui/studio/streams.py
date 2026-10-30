"""The stream schema (v3.08's `STLStream.from_json`), the cross-stream rules and the edit diff.

Pure Python: nothing here imports `trex` or `scapy` (only `trexapi` may). Streams are plain dicts as
`STLProfile.to_json()` writes them; `parse_streams` validates them with strict pydantic models and returns
them unchanged, so no coerced value can reach disk."""
import base64
import binascii
import copy
import json
from typing import Annotated, Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from trex_web_gui.studio.packetdoc import DocError, parse_doc
from trex_web_gui.validation import STREAMS_MAX, code_problem, name_problem

U32 = 4294967295
PKT_MIN, PKT_MAX = 14, 9238
SIDECAR_ENTRY_MAX = 256 * 1024     # bytes of compact JSON in one sidecar entry (D8)
Uint32 = Annotated[int, Field(ge=0, le=U32)]
Positive = Annotated[int, Field(ge=1)]
Micros = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class StreamError(ValueError):
    """A stream or profile that breaks a rule; `str()` is `stream <index> <field>: <reason>` (no index:
    `<field>: <reason>`)."""

    def __init__(self, index: int | None, field: str, reason: str) -> None:
        """Keep the three parts and build the message."""
        super().__init__(f"stream {index} {field}: {reason}" if index is not None else f"{field}: {reason}")
        self.index = index
        self.field = field
        self.reason = reason


class _Strict(BaseModel):
    """Base of every model: strict types, no unknown keys."""

    model_config = ConfigDict(strict=True, extra="forbid")


class Rate(_Strict):
    """`mode.rate`."""

    type: Literal["pps", "bps_L1", "bps_L2", "percentage"]
    value: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def _percentage_cap(self) -> "Rate":
        """A percentage is at most 100."""
        if self.type == "percentage" and self.value > 100:
            raise ValueError("a percentage is at most 100")
        return self


class Mode(_Strict):
    """`mode`: the keys each type requires are those `STLTXMode.from_json` reads."""

    type: Literal["continuous", "single_burst", "multi_burst"]
    rate: Rate
    total_pkts: Positive | None = None
    pkts_per_burst: Positive | None = None
    ibg: Micros | None = None
    count: Positive | None = None

    @model_validator(mode="after")
    def _keys_of_the_type(self) -> "Mode":
        """Exactly the keys of this mode type are present."""
        want = {"continuous": set(), "single_burst": {"total_pkts"},
                "multi_burst": {"pkts_per_burst", "ibg", "count"}}[self.type]
        have = {k for k in ("total_pkts", "pkts_per_burst", "ibg", "count") if getattr(self, k) is not None}
        if have != want:
            raise ValueError(f"a {self.type} mode has keys {sorted(want)}, not {sorted(have)}")
        return self


class Packet(_Strict):
    """`packet`: base64 bytes of 14-9238."""

    binary: str
    meta: str = ""

    @field_validator("binary")
    @classmethod
    def _size(cls, v: str) -> str:
        """The decoded packet is 14-9238 bytes."""
        try:
            n = len(base64.b64decode(v, validate=True))
        except (binascii.Error, ValueError):
            raise ValueError("not base64") from None
        if not PKT_MIN <= n <= PKT_MAX:
            raise ValueError(f"packet of {n} bytes; {PKT_MIN}-{PKT_MAX} bytes")
        return v


class Vm(_Strict):
    """`vm`: instructions are carried verbatim (their values are checked by `code_problem`)."""

    instructions: list[dict[str, Any]]
    cache: Annotated[int, Field(ge=1, le=65535)] | None = None
    split_by_var: str | None = None


class FlowStats(_Strict):
    """`flow_stats`: `{"enabled": false}` alone, or an enabled rule."""

    enabled: bool
    rule_type: Literal["stats", "latency", "tpg"] | None = None
    stream_id: Uint32 | None = None
    vxlan: bool | None = None
    ieee_1588: bool | None = None

    @model_validator(mode="after")
    def _enabled_has_a_rule(self) -> "FlowStats":
        """A disabled rule has no other key; an enabled one has a rule type and a stream id."""
        others = {"rule_type": self.rule_type, "stream_id": self.stream_id, "vxlan": self.vxlan,
                  "ieee_1588": self.ieee_1588}
        if self.enabled and (self.rule_type is None or self.stream_id is None):
            raise ValueError("an enabled rule needs rule_type and stream_id")
        if not self.enabled and any(v is not None for v in others.values()):
            raise ValueError("a disabled rule carries only enabled")
        return self


class Stream(_Strict):
    """One stream as `STLStream.to_json()` writes it."""

    name: Any = None
    next: Any = None
    enabled: bool
    self_start: bool
    start_paused: bool = False
    isg: Micros
    action_count: Uint32
    core_id: Annotated[int, Field(ge=-1, le=255)] = -1
    flags: Annotated[int, Field(ge=0, le=15)]
    random_seed: Uint32 | None = None
    mode: Mode
    packet: Packet
    vm: Vm
    flow_stats: FlowStats

    @field_validator("name", "next")
    @classmethod
    def _name_shape(cls, v: Any) -> Any:
        """A name (and so a `next`) is a safe string or an int >= 1; bools and floats never are."""
        if (why := name_problem(v)) is not None:
            raise ValueError(why)
        return v

    @field_validator("flags")
    @classmethod
    def _dst_mode(cls, v: int) -> int:
        """The destination-MAC mode `(flags >> 1) & 3` is at most 2 (ARP)."""
        if (v >> 1) & 3 > 2:
            raise ValueError(f"destination MAC mode {(v >> 1) & 3} does not exist (at most 2)")
        return v

    @model_validator(mode="after")
    def _core_id_with_rule(self) -> "Stream":
        """A latency or tpg stream is sequenced, so it takes no core id."""
        if self.flow_stats.rule_type in ("latency", "tpg") and self.core_id >= 0 and self.flow_stats.enabled:
            raise ValueError("core_id is not supported for latency or tpg streams")
        return self


def _field_of(loc: tuple[int | str, ...]) -> str:
    """A dotted field path from a pydantic error location."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else part)
    return out or "stream"


def _check_one(index: int | None, stream: object) -> None:
    """Validate one stream with the models and `code_problem`; StreamError on the first failure."""
    try:
        Stream.model_validate(stream)
    except ValidationError as e:
        first = e.errors()[0]
        reason = first["msg"].removeprefix("Value error, ")
        raise StreamError(index, _field_of(first["loc"]), reason) from None
    if (p := code_problem(stream)) is not None:
        raise StreamError(index, p[0], p[1])


def parse_streams(raw: object) -> list[dict[str, Any]]:
    """Deep copies of `raw`'s streams, unchanged, when every one passes the schema and the code-safety rules;
    StreamError otherwise. A list over STREAMS_MAX is refused before any stream is looked at."""
    if not isinstance(raw, list):
        raise StreamError(None, "streams", "must be a list")
    if len(raw) > STREAMS_MAX:
        raise StreamError(None, "streams", f"{len(raw)} streams; at most {STREAMS_MAX}")
    for i, s in enumerate(raw):
        _check_one(i, s)
    return copy.deepcopy(raw)


def _key(name: Any) -> tuple[type, Any]:
    """A name as a lookup key: value and type, so `1` and `"1"` differ."""
    return (type(name), name)


def check_profile(streams: list[dict[str, Any]]) -> None:
    """The cross-stream rules: names unique by value and type, every `next` names a stream, no `next` on a
    continuous stream. StreamError on the first break."""
    names: set[tuple[type, Any]] = set()
    for i, s in enumerate(streams):
        if (n := s.get("name")) is not None:
            if _key(n) in names:
                raise StreamError(i, "name", f"{n!r} is used by another stream")
            names.add(_key(n))
    for i, s in enumerate(streams):
        if (nx := s.get("next")) is None:
            continue
        if s["mode"]["type"] == "continuous":
            raise StreamError(i, "next", "a continuous stream has no next")
        if _key(nx) not in names:
            raise StreamError(i, "next", f"{nx!r} names no stream")


def check_sidecar_entry(index: int | None, entry: object) -> None:
    """`entry` is `{}` or `{"editor": {"doc": a PacketDoc, "binary": str, "vm": dict}}` (D7) of at most
    SIDECAR_ENTRY_MAX bytes of compact JSON; StreamError(index, "sidecar", ...) otherwise."""
    if not isinstance(entry, dict):
        raise StreamError(index, "sidecar", "an entry must be an object")
    if not entry:
        return
    if set(entry) != {"editor"} or not isinstance(entry["editor"], dict):
        raise StreamError(index, "sidecar", "an entry is {} or has the one key editor")
    ed = entry["editor"]
    if set(ed) != {"doc", "binary", "vm"} or not isinstance(ed["binary"], str) \
            or not isinstance(ed["vm"], dict):
        raise StreamError(index, "sidecar", "editor holds doc, binary (a string) and vm (an object)")
    try:
        size = len(json.dumps(entry, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except UnicodeEncodeError:
        raise StreamError(index, "sidecar", "editor document is not valid UTF-8") from None
    if size > SIDECAR_ENTRY_MAX:
        raise StreamError(index, "sidecar", "editor document over 256 KiB")
    try:
        parse_doc(ed["doc"])
    except DocError as e:
        raise StreamError(index, "sidecar", f"editor document {e}") from None


class StreamsDiff(BaseModel):
    """An edit: field-level patches by old index, added streams (with their sidecar entries), removed indices,
    sidecar entries replaced by old index and a new order."""

    model_config = ConfigDict(strict=True, extra="forbid")

    rev: str
    changed: dict[str, dict[str, Any]] = {}
    added: list[dict[str, Any]] = []
    removed: list[int] = []
    order: list[int | str] | None = None
    sidecar: dict[str, dict[str, Any]] = {}
    added_sidecar: list[dict[str, Any]] | None = None


class DiffResult(NamedTuple):
    """What `apply_diff` produces."""

    streams: list[dict[str, Any]]
    sidecar_streams: list[dict[str, Any]]
    renamed: dict[str, str]
    cleared: list[str]


def free_name(name: str | int, taken: set[tuple[type, str | int]]) -> str:
    """`<name>_<k>` for the smallest k >= 2 not in `taken` (which holds (type, value) pairs)."""
    k = 2
    while (str, f"{name}_{k}") in taken:
        k += 1
    return f"{name}_{k}"


def _index_of(text: str, n: int, where: str) -> int:
    """`text` as an index into n streams (canonical decimal, no leading zeros); StreamError otherwise."""
    if not (text.isascii() and text.isdigit() and text == str(int(text))) or int(text) >= n:
        raise StreamError(None, where, f"{text!r} is not an index of a stream (0-{n - 1})")
    return int(text)


def _plan_order(n: int, removed: list[int], n_added: int, order: list[int | str] | None) -> list[int | str]:
    """The final order as old indices and `"+k"` tokens; StreamError(None, "order", ...) when `order` is not a
    permutation of the surviving streams and the added ones."""
    if len(set(removed)) != len(removed):
        raise StreamError(None, "removed", "an index is repeated")
    for r in removed:
        if not 0 <= r < n:
            raise StreamError(None, "removed", f"{r} is not an index of a stream (0-{n - 1})")
    gone = set(removed)
    expected: list[int | str] = [i for i in range(n) if i not in gone] + [f"+{k}" for k in range(n_added)]
    if order is None:
        return expected
    if len(order) != len(expected) or len(set(map(repr, order))) != len(order) \
            or sorted(map(repr, order)) != sorted(map(repr, expected)):
        raise StreamError(None, "order", "must list every surviving stream and every added one exactly once")
    return list(order)


def _rename_map(streams: list[dict[str, Any]],
                patches: dict[int, dict[str, Any]]) -> dict[tuple[type, Any], Any]:
    """Old name key -> new name for every patch that changes a stored name to another non-null one (a deleted
    name is not followed: the dangling `next` is cleared and reported)."""
    out: dict[tuple[type, Any], Any] = {}
    for i, patch in patches.items():
        old = streams[i].get("name")
        if patch.get("name") is not None and old is not None and _key(patch["name"]) != _key(old):
            out[_key(old)] = patch["name"]
    return out


def _text(name: Any, index: int) -> str:
    """A stream's name as text, or `#<index>` when it has none."""
    return f"#{index}" if name is None else str(name)


def apply_diff(streams: list[dict[str, Any]], sidecar_streams: list[dict[str, Any]],
               diff: StreamsDiff) -> DiffResult:
    """Apply `diff` to copies of `streams` and `sidecar_streams`; the inputs are not touched.

    Order of work: index and order checks; rename-following `next` rewrite; patches (a key replaces the stored
    key, null deletes `name`, `next` or `random_seed`); removals; placement; renaming of colliding added
    names; for every `next` that now names nothing, a refusal when the caller wrote it (a `next` key in a
    patch; StreamError at the stream's new index, field "next"), otherwise a clear that `cleared` reports
    (left dangling by a rename to null or a removal, or carried by an added (pasted) stream after the
    collision renames are followed; ruling Task 2 M3, revised again); the size cap; `check_profile`. The size
    cap is checked from the counts first, before any stream is validated. A `sidecar` entry replaces the entry
    of its old index and an `added_sidecar` entry goes with its added stream; entries move with their streams.
    StreamError on any refusal."""
    n = len(streams)
    patches = {_index_of(k, n, "changed"): v for k, v in diff.changed.items()}
    entries = {_index_of(k, n, "sidecar"): v for k, v in diff.sidecar.items()}
    if (diff.added_sidecar is not None) and len(diff.added_sidecar) != len(diff.added):
        raise StreamError(None, "added_sidecar", f"{len(diff.added_sidecar)} entries for {len(diff.added)} "
                                                 "added streams")
    if gone_entry := sorted(set(entries) & set(diff.removed)):
        raise StreamError(None, "sidecar", f"index {gone_entry[0]} is also removed")
    for i, e in entries.items():
        check_sidecar_entry(i, e)
    for pos, e in enumerate(diff.added_sidecar or []):
        try:
            check_sidecar_entry(None, e)
        except StreamError as err:
            raise StreamError(None, f"added_sidecar[{pos}]", err.reason) from None
    order = _plan_order(n, diff.removed, len(diff.added), diff.order)
    if both := sorted(set(patches) & set(diff.removed)):
        raise StreamError(None, "changed", f"index {both[0]} is also removed")
    total = len(order)
    if total > STREAMS_MAX:
        raise StreamError(None, "streams", f"{total} streams; at most {STREAMS_MAX}")

    side = (list(sidecar_streams) + [{} for _ in range(n)])[:n]
    work = copy.deepcopy(streams)
    for i, e in entries.items():
        side[i] = copy.deepcopy(e)
    renames = _rename_map(work, patches)
    if renames:
        for s in work:
            if (nx := s.get("next")) is not None and _key(nx) in renames:
                s["next"] = renames[_key(nx)]
    written: set[int] = set()           # id() of each stream whose `next` the caller wrote
    for i, patch in patches.items():
        if patch.get("next") is not None:
            written.add(id(work[i]))
        for k, v in copy.deepcopy(patch).items():
            if v is None and k in ("name", "next", "random_seed"):
                work[i].pop(k, None)
            else:
                work[i][k] = v
        _check_one(i, work[i])
    added = [copy.deepcopy(a) for a in diff.added]
    for pos, a in enumerate(added):
        try:
            _check_one(None, a)
        except StreamError as e:
            raise StreamError(None, f"added[{pos}].{e.field}", e.reason) from None

    kept_names = {_key(work[i]["name"]) for i in range(n)
                  if i not in set(diff.removed) and work[i].get("name") is not None}
    taken = set(kept_names) | {_key(a["name"]) for a in added if a.get("name") is not None}
    renamed: dict[str, str] = {}
    follow: dict[tuple[type, Any], str] = {}
    for a in added:
        if a.get("name") is not None and _key(a["name"]) in kept_names:
            new = free_name(a["name"], taken)
            taken.add((str, new))
            renamed[str(a["name"])] = new
            follow[_key(a["name"])] = new
            a["name"] = new
    for a in added:
        if (nx := a.get("next")) is not None and _key(nx) in follow:
            a["next"] = follow[_key(nx)]

    out: list[dict[str, Any]] = []
    out_side: list[dict[str, Any]] = []
    for tok in order:
        if isinstance(tok, int) and not isinstance(tok, bool):
            out.append(work[tok])
            out_side.append(copy.deepcopy(side[tok]))
        else:
            out.append(added[int(str(tok)[1:])])
            out_side.append(copy.deepcopy((diff.added_sidecar or [{} for _ in added])[int(str(tok)[1:])]))

    names = {_key(s["name"]) for s in out if s.get("name") is not None}
    cleared: list[str] = []
    for i, s in enumerate(out):
        if (nx := s.get("next")) is not None and _key(nx) not in names:
            if id(s) in written:
                raise StreamError(i, "next", f"{nx!r} names no stream")
            del s["next"]
            cleared.append(_text(s.get("name"), i))
    check_profile(out)
    return DiffResult(out, out_side, renamed, cleared)
