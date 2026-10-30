"""The trex_cfg.yaml model, driven by the daemon's own metadata (`get_trex_config_metadata`).

Nothing here knows a field by name except `port_limit` and `interfaces` (their length rule) and `c` (no
default, see NO_DEFAULT): the metadata says what exists, what is mandatory and what type it has. Keys it does
not describe are kept and never an error.
YAML is read with `yaml.safe_load` and written with `yaml.safe_dump` only."""
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Any

import yaml

FIELD_TYPES = frozenset({"NUMBER", "FLOAT", "STRING", "BOOLEAN", "LIST", "OBJECT", "IP", "MAC"})
MAX_YAML_BYTES = 65536
_MAC = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
# Fields the defaults leave unset although the metadata gives one: `c` (4) asks TRex for 2 + 4 x pairs
# CPUs, which a small host lacks (E0 on the 4-vCPU guest, 2026-10-26: "EAL: core 4 unavailable"); unset,
# TRex uses 1.
NO_DEFAULT = frozenset({"c"})


@dataclass(frozen=True)
class ConfigError:
    """One problem: a dotted path (`port_info.0.default_gw`), the YAML line when known, and what is wrong."""
    path: str
    line: int | None
    message: str


def metadata_version(metadata: list[dict[str, Any]]) -> str:
    """The md5 of the metadata's canonical JSON (sorted keys, no spaces): what a ConfigDoc is tied to."""
    canon = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.md5(canon.encode("utf-8"), usedforsecurity=False).hexdigest()


def _default_of(field: dict[str, Any]) -> tuple[bool, Any]:
    """(has a default, the default) of one field: a mandatory OBJECT's is the map of its attributes'
    defaults; an optional OBJECT has none, so TRex's own defaults apply (a `platform` built from
    `master_thread_id`'s default alone is refused: `key not found: rx_thread_id`, E0 on the guest,
    2026-10-26)."""
    if "default" in field:
        return True, field["default"]
    if field.get("type") == "OBJECT" and field.get("mandatory"):
        sub = _defaults_of(field.get("attributes") or [])
        return (True, sub) if sub else (False, None)
    return False, None


def _defaults_of(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """The defaults of a field list, in metadata order; a NO_DEFAULT field is left out."""
    out: dict[str, Any] = {}
    for f in fields:
        if "id" not in f or f["id"] in NO_DEFAULT:
            continue
        has, val = _default_of(f)
        if has:
            out[f["id"]] = val
    return out


def defaults(metadata: list[dict[str, Any]]) -> dict[str, Any]:
    """The values every field with a default starts at (`version` 2, `zmq_pub_port` 4500...), less the
    optional sections and NO_DEFAULT."""
    return _defaults_of(metadata)


def _type_error(ftype: str | None, value: Any) -> str | None:
    """Why value is not of the metadata type, or None when it is."""
    if ftype == "NUMBER":
        return None if isinstance(value, int) and not isinstance(value, bool) else "must be an integer"
    if ftype == "FLOAT":
        return None if isinstance(value, (int, float)) and not isinstance(value, bool) else "must be a number"
    if ftype == "STRING":
        return None if isinstance(value, str) else "must be a string"
    if ftype == "BOOLEAN":
        return None if isinstance(value, bool) else "must be true or false"
    if ftype == "LIST":
        return None if isinstance(value, list) else "must be a list"
    if ftype == "OBJECT":
        return None if isinstance(value, dict) else "must be a mapping"
    if ftype == "IP":
        if isinstance(value, str):
            try:
                ipaddress.ip_address(value)
                return None
            except ValueError:
                pass
        return "must be an IP address"
    if ftype == "MAC":
        return None if isinstance(value, str) and _MAC.match(value) else "must be a MAC address (aa:bb:..)"
    return None  # a type this version does not know: nothing to check


def _check_value(path: str, spec: dict[str, Any], value: Any, errors: list[ConfigError]) -> None:
    """Type-check one present value, recursing into OBJECT attributes and LIST items."""
    ftype = spec.get("type")
    msg = _type_error(ftype, value)
    if msg:
        errors.append(ConfigError(path, None, msg))
        return
    if ftype == "OBJECT":
        _check_fields(path, spec.get("attributes") or [], value, errors)
    elif ftype == "LIST" and isinstance(spec.get("item"), dict):
        item = spec["item"]  # an item has no `id`
        for i, v in enumerate(value):
            _check_value(f"{path}.{i}", item, v, errors)


def _check_fields(prefix: str, fields: list[dict[str, Any]], values: dict[str, Any],
                  errors: list[ConfigError]) -> None:
    """Mandatory, mandatory_if_not_set and type checks of one mapping against its field list."""
    for f in fields:
        fid = f.get("id")
        if fid is None:
            continue
        path = f"{prefix}.{fid}" if prefix else fid
        present = fid in values and values[fid] is not None
        if not present:
            other = f.get("mandatory_if_not_set")
            if other is not None:
                if values.get(other) is None:
                    errors.append(ConfigError(path, None, f"set {fid} or {other}"))
            elif f.get("mandatory"):
                errors.append(ConfigError(path, None, "is required"))
            continue
        _check_value(path, f, values[fid], errors)


def validate(values: dict[str, Any], metadata: list[dict[str, Any]]) -> list[ConfigError]:
    """Every problem of `values` against `metadata`; keys it does not describe are kept, not errors."""
    errors: list[ConfigError] = []
    _check_fields("", metadata, values, errors)
    ifaces, limit = values.get("interfaces"), values.get("port_limit")
    if (isinstance(ifaces, list) and isinstance(limit, int) and not isinstance(limit, bool)
            and len(ifaces) != limit):
        errors.append(ConfigError("interfaces", None,
                                  f"has {len(ifaces)} entries but port_limit is {limit}"))
    return errors


def render_yaml(values: dict[str, Any]) -> str:
    """The trex_cfg.yaml text: a one-element list holding the mapping, in the given key order."""
    return yaml.safe_dump([values], sort_keys=False, default_flow_style=None)


def parse_yaml(text: str) -> tuple[dict[str, Any] | None, list[ConfigError]]:
    """(values, []) for a document that is a list of exactly one mapping, else (None, errors)."""
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        return None, [ConfigError("", None, f"YAML is larger than {MAX_YAML_BYTES} bytes")]
    try:
        doc = yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        return None, [ConfigError("", mark.line + 1 if mark is not None else None,
                                  exc.problem or "invalid YAML")]
    except yaml.YAMLError as exc:
        return None, [ConfigError("", None, f"invalid YAML: {exc}")]
    if not (isinstance(doc, list) and len(doc) == 1 and isinstance(doc[0], dict)):
        return None, [ConfigError("", None, "the document must be a list of exactly one mapping")]
    return doc[0], []
