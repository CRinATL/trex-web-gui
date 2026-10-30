"""The `<slug>.gui.json` sidecar beside a library profile: its name, where it came from, and the per-stream
GUI state (a list parallel to the profile's streams). Pure Python; imports neither `trex` nor `scapy`."""
import os
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

SIDECAR_VERSION = 1
SOURCE_KINDS = ("py", "yaml", "json", "pcap", "new")


class SidecarError(ValueError):
    """A sidecar that does not parse."""


class Source(TypedDict):
    """Where a library profile was imported from."""

    kind: Literal["py", "yaml", "json", "pcap", "new"]
    path: str | None
    args: list[str]
    tunables: dict[str, Any] | None
    ports_differ: bool
    imported: str


class Sidecar(TypedDict):
    """The sidecar document."""

    version: int
    name: str
    created: str
    updated: str
    source: Source | None
    streams: list[dict[str, Any]]
    pcap_wizard: dict[str, Any] | None
    stream_count: int


def now() -> str:
    """The current UTC time in the setups format (ISO-8601, milliseconds, trailing Z)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_sidecar(name: str, source: Source | None, count: int) -> Sidecar:
    """A sidecar for `count` streams, `created` and `updated` now; a source path is stored absolute."""
    src: Source | None = None
    if source is not None:
        src = {**source}
        if src["path"] is not None:
            src["path"] = os.path.abspath(os.path.expanduser(src["path"]))
    stamp = now()
    return {"version": SIDECAR_VERSION, "name": name, "created": stamp, "updated": stamp, "source": src,
            "streams": [{} for _ in range(count)], "pcap_wizard": None, "stream_count": count}


def _is_int(v: object) -> bool:
    """An int that is not a bool."""
    return isinstance(v, int) and not isinstance(v, bool)


def _check_source(src: object) -> None:
    """SidecarError unless `src` has the shape of a Source."""
    if not isinstance(src, dict):
        raise SidecarError("source: must be an object or null")
    if src.get("kind") not in SOURCE_KINDS:
        raise SidecarError(f"source.kind: must be one of {', '.join(SOURCE_KINDS)}")
    if src.get("path") is not None and not isinstance(src.get("path"), str):
        raise SidecarError("source.path: must be a string or null")
    args = src.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise SidecarError("source.args: must be a list of strings")
    if src.get("tunables") is not None and not isinstance(src.get("tunables"), dict):
        raise SidecarError("source.tunables: must be an object or null")
    if not isinstance(src.get("ports_differ"), bool):
        raise SidecarError("source.ports_differ: must be a boolean")
    if not isinstance(src.get("imported"), str):
        raise SidecarError("source.imported: must be a string")


def parse_sidecar(raw: object) -> Sidecar:
    """`raw` as a Sidecar (a shallow copy); SidecarError when it is not one, naming an unknown version."""
    if not isinstance(raw, dict):
        raise SidecarError("sidecar: must be an object")
    version = raw.get("version")
    if not _is_int(version):
        raise SidecarError("version: missing or not an integer")
    if version != SIDECAR_VERSION:
        raise SidecarError(f"sidecar version {version} is not supported (this build reads {SIDECAR_VERSION})")
    for key in ("name", "created", "updated"):
        if not isinstance(raw.get(key), str):
            raise SidecarError(f"{key}: must be a string")
    if raw.get("source") is not None:
        _check_source(raw["source"])
    streams = raw.get("streams")
    if not isinstance(streams, list) or not all(isinstance(s, dict) for s in streams):
        raise SidecarError("streams: must be a list of objects")
    if raw.get("pcap_wizard") is not None and not isinstance(raw.get("pcap_wizard"), dict):
        raise SidecarError("pcap_wizard: must be an object or null")
    count = raw.get("stream_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise SidecarError("stream_count: must be a non-negative integer")
    return {"version": version, "name": raw["name"], "created": raw["created"], "updated": raw["updated"],
            "source": raw.get("source"), "streams": streams, "pcap_wizard": raw.get("pcap_wizard"),
            "stream_count": count}
