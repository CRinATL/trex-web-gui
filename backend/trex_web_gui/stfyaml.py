"""Stateful (STF) profile rules: parse, rewrite and name a `-f` YAML (spec §13.4, §13.9), and plan the upload
of a local YAML with its pcaps (spec §13.7). Only `plan_upload` reads files, and it writes nothing.

Only `yaml.safe_load` and `yaml.safe_dump` are used, so a Python tag in a profile is an error, never code.
The shape checked is what `bp_sim.cpp` reads (:2011, :2024-2031, :2181, :2297-2300, :2476-2479).
"""
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

STF_YAML_MAX_BYTES = 65536
MAX_TEMPLATES = 64
PCAP_MAX_BYTES = 4 * 1024 * 1024
MAX_PCAPS = 64
UPLOAD_MAX_BYTES = 32 * 1024 * 1024
# the four classic pcap magics TRex reads (src/common/pcap.cpp:25-29), as the first 4 bytes big-endian
PCAP_MAGICS = frozenset({0xa1b2c3d4, 0xd4c3b2a1, 0xa1b23c4d, 0x4d3cb2a1})
PCAPNG_MAGIC = 0x0a0d0d0a
STF_D_MIN = 10
STF_D_MAX = 3600
STF_M_MAX = 1000.0
STF_L_MAX = 100000
STF_RX_CHECK_MAX = 65535

NAME_RE = r"^[A-Za-z0-9_./-]{1,200}$"
STOCK_PROFILE_RE = r"^(cap2|avl)/[A-Za-z0-9_.-]{1,96}\.yaml$"
TG_PCAP_RE = r"^tg-[0-9a-f]{12}-[A-Za-z0-9_.-]{1,40}\.pcap$"
TG_YAML_RE = r"^tg-[0-9a-f]{12}-[A-Za-z0-9_.-]{1,40}\.yaml$"

_NUMERIC_KEYS = ("cps", "ipg", "rtt", "w")


def _is_number(v: Any) -> bool:
    """True for an int or float that is not a bool."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_finite_number(v: Any) -> bool:
    """True for an int or float that is not a bool, NaN or infinite (YAML spells those `.nan` and `.inf`)."""
    return _is_number(v) and math.isfinite(v)


def parse(text: str) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Check a profile's text; returns (its first entry or None, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    if len(text.encode("utf-8", "replace")) > STF_YAML_MAX_BYTES:
        return None, [f"profile is larger than {STF_YAML_MAX_BYTES} bytes"], warnings
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        return None, [f"not valid YAML{where}: {getattr(e, 'problem', None) or e}"], warnings
    if not isinstance(doc, list) or not doc:
        return None, ["the document must be a non-empty list"], warnings
    entry = doc[0]
    if not isinstance(entry, dict):
        return None, ["the first list entry must be a mapping"], warnings
    if len(doc) > 1:
        warnings.append("TRex reads only the first entry")
    if not _is_number(entry.get("duration")):
        errors.append("duration must be a number")
    cap = entry.get("cap_info")
    if not isinstance(cap, list) or not 1 <= len(cap) <= MAX_TEMPLATES:
        errors.append(f"cap_info must be a list of 1..{MAX_TEMPLATES} templates")
    else:
        for i, t in enumerate(cap):
            if not isinstance(t, dict):
                errors.append(f"cap_info[{i}] must be a mapping")
                continue
            name = t.get("name")
            if not isinstance(name, str) or not re.fullmatch(NAME_RE[1:-1], name):
                errors.append(f"cap_info[{i}].name must match {NAME_RE}")
            for k in _NUMERIC_KEYS:
                if not _is_finite_number(t.get(k)):
                    errors.append(f"cap_info[{i}].{k} must be a finite number")
    return entry, errors, warnings


def templates(entry: dict[str, Any]) -> list[str]:
    """The template names of a parsed entry, in order."""
    return [t["name"] for t in entry.get("cap_info", [])]


def rewrite(text: str, names: dict[str, str]) -> str:
    """The profile with every cap_info name replaced through `names`. ValueError for a name not in it, for
    text over STF_YAML_MAX_BYTES (the cap `parse` has), for YAML that does not load, for an alias (the
    rewrite would change every place that shares it), and for a cap_info that is not a list of mappings."""
    if len(text.encode("utf-8", "replace")) > STF_YAML_MAX_BYTES:
        raise ValueError(f"profile is larger than {STF_YAML_MAX_BYTES} bytes")
    try:
        if any(isinstance(e, yaml.AliasEvent) for e in yaml.parse(text, Loader=yaml.SafeLoader)):
            raise ValueError("YAML aliases are not allowed in a profile")
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"not valid YAML: {getattr(e, 'problem', None) or e}") from e
    if not isinstance(doc, list) or not doc or not isinstance(doc[0], dict):
        raise ValueError("not a profile")
    cap = doc[0].get("cap_info", [])
    if not isinstance(cap, list) or not all(isinstance(t, dict) for t in cap):
        raise ValueError("cap_info must be a list of mappings")
    for t in cap:
        old = t.get("name")
        if old not in names:
            raise ValueError(f"no replacement for template {old!r}")
        t["name"] = names[old]
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def tg_name(data: bytes, stem: str, ext: Literal["pcap", "yaml"]) -> str:
    """The pushed name `tg-<12 hex of sha256>-<safe stem>.<ext>`; callers pass `Path(name).stem`."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:40] or "_"
    return f"tg-{hashlib.sha256(data).hexdigest()[:12]}-{safe}.{ext}"


def stock_profile_ok(path: str) -> bool:
    """True when `path` is a stock profile path (`cap2/x.yaml` or `avl/x.yaml`)."""
    return re.fullmatch(STOCK_PROFILE_RE, path) is not None and ".." not in path


def uploaded_profile_ok(path: str, files_path: str) -> bool:
    """True when `path` is `<files_path>/<name>` with `name` a content-addressed `tg-*.yaml`. TG_YAML_RE has
    no `/`, so nothing below or beside files_path matches. An empty or non-absolute files_path is never
    acceptable. The template rule (R16) is enforced in `DaemonSession.stf_profile`, not here."""
    if not files_path.startswith("/"):
        return False
    prefix = files_path.rstrip("/") + "/"
    return path.startswith(prefix) and re.fullmatch(TG_YAML_RE, path[len(prefix):]) is not None


def check_stf_bounds(d: Any, m: Any, l: Any, rx_check: Any) -> None:  # noqa: E741 - the field is named l
    """ValueError naming the first of d, m, l, rx_check that is out of its bounds."""
    if isinstance(d, bool) or not isinstance(d, int) or not STF_D_MIN <= d <= STF_D_MAX:
        raise ValueError(f"d must be an int in {STF_D_MIN}..{STF_D_MAX}")
    if not _is_number(m) or not 0 < m <= STF_M_MAX:
        raise ValueError(f"m must be a number above 0 and at most {STF_M_MAX}")
    if isinstance(l, bool) or not isinstance(l, int) or not 0 <= l <= STF_L_MAX:
        raise ValueError(f"l must be an int in 0..{STF_L_MAX}")
    if isinstance(rx_check, bool) or not isinstance(rx_check, int) or not 0 <= rx_check <= STF_RX_CHECK_MAX:
        raise ValueError(f"rx_check must be an int in 0..{STF_RX_CHECK_MAX}")


@dataclass(frozen=True)
class UploadFile:
    """One pcap to push: its local file, the remote name (`tg_name`), its size and the template name the
    profile gave it."""
    local: Path
    remote: str
    size: int
    template: str


@dataclass(frozen=True)
class UploadPlan:
    """What an upload writes: the rewritten profile (`yaml_text`, pushed as `yaml_remote`), the pcaps, the
    bytes in all (pcaps and profile) and notes for the user."""
    yaml_remote: str
    yaml_text: str
    pcaps: list[UploadFile]
    total_bytes: int
    warnings: list[str]


def read_regular(path: Path, limit: int, what: str) -> bytes:
    """The bytes of `path`, which must resolve (strict) to a regular file of at most `limit` bytes (a FIFO or
    a device is refused, and the read stops at limit + 1). ValueError naming `what` otherwise."""
    try:
        real = path.resolve(strict=True)
        if not real.is_file():
            raise ValueError(f"{what} {str(path)!r} is not a regular file")
        if real.stat().st_size > limit:
            raise ValueError(f"{what} {str(path)!r} is larger than {limit} bytes")
        with real.open("rb") as f:
            data = f.read(limit + 1)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"{what} {str(path)!r} cannot be read: {e}") from e
    if len(data) > limit:
        raise ValueError(f"{what} {str(path)!r} is larger than {limit} bytes")
    return data


def _find_pcap(name: str, yaml_dir: Path) -> Path:
    """The local file for a template name: an absolute one as is, a relative one against the YAML's dir, then
    its parent (spec §13.7). ValueError naming the template when none is a file."""
    if name.startswith("/"):
        tries = [Path(name)]
    else:
        tries = [yaml_dir / name, yaml_dir.parent / name]
    for t in tries:
        try:
            if t.resolve(strict=True).is_file():
                return t
        except (OSError, RuntimeError):
            continue
    raise ValueError(f"template {name!r}: no such file (tried {', '.join(str(t) for t in tries)})")


def _check_pcap_magic(data: bytes, name: str) -> None:
    """ValueError unless `data` starts with a classic pcap magic; pcapng gets its own message."""
    magic = int.from_bytes(data[:4], "big") if len(data) >= 4 else -1
    if magic == PCAPNG_MAGIC:
        raise ValueError(f"template {name!r} is pcapng, which TRex does not read; "
                         "convert it with editcap -F pcap")
    if magic not in PCAP_MAGICS:
        raise ValueError(f"template {name!r} is not a pcap file (its first 4 bytes are not a pcap magic)")


def plan_upload(yaml_path: Path, files_path: str) -> UploadPlan:
    """Read a local profile and its pcaps and plan their upload into `files_path` (absolute), writing nothing.

    ValueError, naming the file, when: the YAML is not absolute, is not a regular file of at most
    STF_YAML_MAX_BYTES, or fails `parse`; a template cannot be found (absolute as is, relative against the
    YAML's dir then its parent), is over PCAP_MAX_BYTES or lacks a classic pcap magic (pcapng is named);
    there are more than UPLOAD_MAX_BYTES in all (at most MAX_PCAPS distinct pcaps follows from `parse` capping
    the templates at MAX_TEMPLATES, which is 64 as well). Remote names are
    `tg_name`s; the rewritten profile points at `<files_path>/<remote>`."""
    if not yaml_path.is_absolute():
        raise ValueError(f"profile path {str(yaml_path)!r} must be absolute")
    if not files_path.startswith("/"):
        raise ValueError("the daemon's files path must be absolute")
    who = f"profile {str(yaml_path)!r}"
    text_bytes = read_regular(yaml_path, STF_YAML_MAX_BYTES, "profile")
    try:
        text = text_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{who} is not UTF-8 text: {e}") from e
    entry, errors, parse_warnings = parse(text)
    if entry is None or errors:
        raise ValueError(f"{who}: {(errors or ['not a profile'])[0]}")
    yaml_dir = yaml_path.resolve(strict=True).parent
    by_remote: dict[str, UploadFile] = {}
    mapping: dict[str, str] = {}
    base = files_path.rstrip("/")
    for name in dict.fromkeys(templates(entry)):
        local = _find_pcap(name, yaml_dir)
        data = read_regular(local, PCAP_MAX_BYTES, f"template {name!r} file")
        _check_pcap_magic(data, name)
        remote = tg_name(data, local.stem, "pcap")
        mapping[name] = f"{base}/{remote}"
        by_remote.setdefault(remote, UploadFile(local, remote, len(data), name))
    # distinct files <= distinct template names <= MAX_TEMPLATES (= MAX_PCAPS), which `parse` enforced above
    try:
        yaml_text = rewrite(text, mapping)
    except ValueError as e:
        raise ValueError(f"{who}: {e}") from e
    yaml_bytes = yaml_text.encode("utf-8")
    if len(yaml_bytes) > STF_YAML_MAX_BYTES:
        raise ValueError(f"{who} is larger than {STF_YAML_MAX_BYTES} bytes once its names are rewritten")
    pcaps = list(by_remote.values())
    total = sum(p.size for p in pcaps) + len(yaml_bytes)
    if total > UPLOAD_MAX_BYTES:
        raise ValueError(f"{who} and its pcaps are {total} bytes; the most is {UPLOAD_MAX_BYTES}")
    warnings = list(parse_warnings)
    if "#" in text:
        warnings.append("comments are not kept")
    return UploadPlan(tg_name(yaml_bytes, yaml_path.stem, "yaml"), yaml_text, pcaps, total, warnings)
