"""Imports into the stream schema and the text exports.

Every TRex/Scapy step runs in the trexapi worker; this module only routes a file to it and validates the
result with `parse_streams` and `check_profile`."""
import json
import os
from typing import Any, NamedTuple

import yaml

from trex_web_gui import trexapi
from trex_web_gui.trexapi import PcapOptions

from .streams import check_profile, parse_streams

IMPORT_KINDS = {".py": "py", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
                ".pcap": "pcap", ".cap": "pcap"}
UPLOAD_SUFFIXES = IMPORT_KINDS.keys() - {".py"}       # R4: a .py imports only from a backend path


class ConvertError(ValueError):
    """A file this module cannot import: an unknown suffix, a kind that is not its suffix's, or a pcap
    without its options."""


class Imported(NamedTuple):
    """An import's streams, and whether a .py gave port 1 other streams than port 0 (spec §6.1)."""

    streams: list[dict[str, Any]]
    ports_differ: bool


def kind_of(path: str) -> str:
    """The import kind of `path` from its suffix (case-insensitive); ConvertError for any other suffix."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in IMPORT_KINDS:
        raise ConvertError(f"{os.path.basename(path)}: only {', '.join(IMPORT_KINDS)} files import")
    return IMPORT_KINDS[suffix]


async def import_file(path: str, kind: str, tunables: list[str], pcap: PcapOptions | None) -> Imported:
    """The streams of the file at `path`: a .py loads ports 0 and 1 through `load_profile` and keeps port 0's
    streams (`ports_differ` when port 1's differ); YAML and JSON load port 0; a pcap goes through
    `import_pcap` with `pcap`. ConvertError: `kind` is not the suffix's, or a pcap has no options.
    ProfileError from the worker; StreamError when the result breaks the schema or the profile rules."""
    if kind_of(path) != kind:
        raise ConvertError(f"{os.path.basename(path)} is not a {kind} file")
    ports_differ = False
    if kind == "pcap":
        if pcap is None:
            raise ConvertError("a pcap import needs its options")
        raw = await trexapi.import_pcap(path, pcap)
    elif kind == "py":
        lp = await trexapi.load_profile(path, [0, 1], tunables)
        raw = lp.streams_by_port[0]
        ports_differ = lp.streams_by_port[1] != raw
    else:
        lp = await trexapi.load_profile(path, [0], [])
        raw = lp.streams_by_port[0]
    streams = parse_streams(raw)
    check_profile(streams)
    return Imported(streams, ports_differ)


def to_yaml(streams: list[dict[str, Any]]) -> str:
    """The streams as YAML in the stl-sim form (a list of `to_json` dicts, keys in their order)."""
    return yaml.safe_dump(streams, sort_keys=False)


def to_json_text(streams: list[dict[str, Any]]) -> str:
    """The streams as indented JSON, as `STLProfile.load_json` reads them."""
    return json.dumps(streams, indent=2)
