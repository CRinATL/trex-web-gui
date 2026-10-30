"""Test setups (P3d): one JSON file per setup in data_dir/test_setups/<slug>.json.

A setup names a server, a profile with its tunables, a start mix and an optional L2/L3 layer for each port,
and the Dashboard view. The slug is derived from the name (never taken from input) and is the file name, so
no input chooses a path. Writes are an atomic replace; a file that fails to load is listed in `load_errors`
and left alone. This module imports nothing from `api` at import time (LayerBody is imported inside
parse_setup)."""
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from trex_web_gui.store.port_labels import LabelError, _text, check_server
from trex_web_gui.validation import (
    DASHBOARD_LAYOUTS,
    DASHBOARD_TABS,
    DASHBOARD_WINDOWS_S,
    MULT,
    check_duration,
)

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 60
KEY_MAX = 64
PATH_MAX = 4096
EXTRA_MAX = 4096
TUNABLES_MAX = 100
TUNABLE_KEY_MAX = 64
TUNABLE_STR_MAX = 1024
PORTS_MAX = 64
PGIDS_MAX = 8
SETUPS_MAX = 500
KINDS = ("py", "yaml", "json")


class SetupError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        """Keep the field and the reason apart from the combined "field: reason" message."""
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class SetupNotFound(LookupError):
    """No setup has this slug (or the slug is not a valid one)."""


class SetupExists(Exception):
    """A setup with the target slug already exists."""


class TooManySetups(Exception):
    """The directory already holds SETUPS_MAX setups."""


class SetupPort(TypedDict):
    key: str
    port: int
    profile: dict[str, str]          # {"path", "kind"}
    tunables: dict[str, Any]         # {"values", "extra"}
    mult: str
    duration: float
    force: bool                      # the start force
    acquire_force: bool
    stop_first: bool
    layer: dict[str, Any] | None


class TestSetup(TypedDict):
    name: str
    slug: str
    created: str
    updated: str
    server: str | None
    ports: list[SetupPort]
    dashboard: dict[str, Any] | None


def slugify(name: str) -> str:
    """Lower-case; each run of characters outside [a-z0-9] becomes one "-"; leading and trailing "-" go."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _now() -> str:
    """The current UTC time as ISO-8601 with milliseconds and a trailing Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _text_of(field: str, value: object, limit: int) -> str:
    """store.port_labels._text under SetupError, and non-empty."""
    try:
        v = _text(field, value, limit)
    except LabelError as e:
        raise SetupError(field, e.reason) from None
    if not v:
        raise SetupError(field, "must not be empty")
    return v


def _plain(field: str, value: object, limit: int, *, empty: bool = False) -> str:
    """A str of at most `limit` characters without NUL (not stripped); non-empty unless `empty`."""
    if not isinstance(value, str):
        raise SetupError(field, "must be text")
    if "\x00" in value:
        raise SetupError(field, "must not contain NUL")
    if len(value) > limit or (not value and not empty):
        raise SetupError(field, f"must be {0 if empty else 1}-{limit} characters")
    return value


def _flag(field: str, value: object) -> bool:
    """`value` when it is a bool."""
    if not isinstance(value, bool):
        raise SetupError(field, "must be true or false")
    return value


def _int(field: str, value: object) -> int:
    """`value` when it is an int and not a bool."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise SetupError(field, "must be an integer")
    return value


def _dict(field: str, value: object) -> dict[str, Any]:
    """`value` when it is a dict."""
    if not isinstance(value, dict):
        raise SetupError(field, "must be an object")
    return value


def _layer(field: str, raw: object, port: int) -> dict[str, Any] | None:
    """The layer in LayerBody's normalised form without `port`; SetupError(field) for anything it refuses."""
    if raw is None:
        return None
    from pydantic import ValidationError

    from trex_web_gui.api.port_config import LayerBody  # lazily: api imports this module's package
    if not isinstance(raw, dict):
        raise SetupError(field, "must be an object")
    if "port" in raw:
        raise SetupError(field, "must not contain port")
    try:
        return LayerBody(port=port, **raw).model_dump(exclude={"port"})
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        raise SetupError(field, f"{where}: {first['msg']}" if where else str(first["msg"])) from None
    except TypeError as e:
        raise SetupError(field, str(e)) from None


def _tunables(field: str, raw: object) -> dict[str, Any]:
    """{"values", "extra"} validated."""
    t = _dict(field, raw)
    values = _dict(f"{field}.values", t.get("values", {}))
    if len(values) > TUNABLES_MAX:
        raise SetupError(f"{field}.values", f"at most {TUNABLES_MAX} entries")
    for k, v in values.items():
        _plain(f"{field}.values", k, TUNABLE_KEY_MAX)
        if isinstance(v, str):
            _plain(f"{field}.values.{k}", v, TUNABLE_STR_MAX, empty=True)
        elif isinstance(v, bool):
            pass
        elif isinstance(v, int | float):
            if v != v or v in (float("inf"), float("-inf")):
                raise SetupError(f"{field}.values.{k}", "must be a finite number")
        else:
            raise SetupError(f"{field}.values.{k}", "must be text, a number or true/false")
    extra = _plain(f"{field}.extra", t.get("extra", ""), EXTRA_MAX, empty=True)
    return {"values": dict(values), "extra": extra}


def _port(i: int, raw: object) -> SetupPort:
    """One validated port entry."""
    f = f"ports[{i}]"
    p = _dict(f, raw)
    port = _int(f"{f}.port", p.get("port"))
    if port < 0:
        raise SetupError(f"{f}.port", "must not be negative")
    prof = _dict(f"{f}.profile", p.get("profile"))
    kind = prof.get("kind")
    if kind not in KINDS:
        raise SetupError(f"{f}.profile.kind", "must be py, yaml or json")
    mult = p.get("mult")
    if not isinstance(mult, str) or not re.fullmatch(MULT, mult):
        raise SetupError(f"{f}.mult", "is not a rate such as 10kpps, 100mbps or 50%")
    duration = p.get("duration")
    if not isinstance(duration, int | float) or isinstance(duration, bool):
        raise SetupError(f"{f}.duration", "must be a number")
    try:
        duration = check_duration(float(duration))
    except ValueError as e:
        raise SetupError(f"{f}.duration", str(e)) from None
    return {"key": _text_of(f"{f}.key", p.get("key"), KEY_MAX), "port": port,
            "profile": {"path": _plain(f"{f}.profile.path", prof.get("path"), PATH_MAX), "kind": kind},
            "tunables": _tunables(f"{f}.tunables", p.get("tunables", {})),
            "mult": mult, "duration": duration, "force": _flag(f"{f}.force", p.get("force", False)),
            "acquire_force": _flag(f"{f}.acquire_force", p.get("acquire_force", False)),
            "stop_first": _flag(f"{f}.stop_first", p.get("stop_first", False)),
            "layer": _layer(f"{f}.layer", p.get("layer"), port)}


def _dashboard(raw: object) -> dict[str, Any] | None:
    """The Dashboard view validated, or None."""
    if raw is None:
        return None
    d = _dict("dashboard", raw)
    if d.get("tab") not in DASHBOARD_TABS:
        raise SetupError("dashboard.tab", f"must be one of {', '.join(DASHBOARD_TABS)}")
    out: dict[str, Any] = {"tab": d["tab"]}
    for k in ("streams_pgids", "latency_pgids"):
        v = d.get(k)
        if not isinstance(v, list) or len(v) > PGIDS_MAX:
            raise SetupError(f"dashboard.{k}", f"must be a list of at most {PGIDS_MAX} integers")
        if any(_int(f"dashboard.{k}", x) < 0 for x in v):
            raise SetupError(f"dashboard.{k}", "must not hold a negative id")
        out[k] = list(v)
    for k, choices in (("window_s", DASHBOARD_WINDOWS_S), ("layout", DASHBOARD_LAYOUTS)):
        if d.get(k) not in choices or isinstance(d.get(k), bool):
            raise SetupError(f"dashboard.{k}", f"must be one of {', '.join(map(str, choices))}")
        out[k] = d[k]
    return out


def parse_setup(raw: object) -> TestSetup:
    """Untrusted data to a TestSetup. The slug is derived from the name and any `slug` in `raw` is ignored;
    `created`/`updated` are kept when they are text (the store sets them on save). Raises SetupError."""
    r = _dict("setup", raw)
    name = _text_of("name", r.get("name"), NAME_MAX)
    slug = slugify(name)
    if not slug:
        raise SetupError("name", "needs at least one letter or digit a-z 0-9")
    server = r.get("server")
    if server is not None:
        try:
            server = check_server(server)
        except LabelError as e:
            raise SetupError("server", e.reason) from None
    ports = r.get("ports")
    if not isinstance(ports, list) or not 1 <= len(ports) <= PORTS_MAX:
        raise SetupError("ports", f"must hold 1-{PORTS_MAX} entries")
    parsed = [_port(i, p) for i, p in enumerate(ports)]
    ids = [p["port"] for p in parsed]
    if len(set(ids)) != len(ids):
        raise SetupError("ports", "port ids must be unique")
    created, updated = (v if isinstance(v, str) and len(v) <= 40 else ""
                        for v in (r.get("created"), r.get("updated")))
    return {"name": name, "slug": slug, "created": created, "updated": updated, "server": server,
            "ports": parsed, "dashboard": _dashboard(r.get("dashboard"))}


class TestSetupStore:
    __test__ = False                 # not a pytest class

    def __init__(self, data_dir: Path) -> None:
        """Point at data_dir/test_setups; touches no file."""
        self.dir = data_dir / "test_setups"
        self.load_errors: list[dict[str, str]] = []

    def path_of(self, slug: str) -> Path:
        """The file for `slug`; SetupNotFound for a slug outside SLUG_RE, before any filesystem access."""
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise SetupNotFound(slug)
        return self.dir / f"{slug}.json"

    def _read(self, path: Path) -> TestSetup:
        """The setup in `path`, which must be named after its own slug; raises OSError, ValueError."""
        s = parse_setup(json.loads(path.read_text(encoding="utf-8")))
        if path.stem != s["slug"]:
            raise SetupError("name", f"file {path.name} does not match its name's slug {s['slug']}")
        return s

    def _files(self) -> list[Path]:
        """Every setup file, sorted; empty when the directory does not exist."""
        try:
            return sorted(p for p in self.dir.iterdir() if p.suffix == ".json" and not p.name.startswith("."))
        except FileNotFoundError:
            return []

    def list(self) -> list[TestSetup]:
        """Every loadable setup, sorted by slug. Files that fail to load go to `load_errors` as
        {"file", "error"} and are left in place."""
        out: list[TestSetup] = []
        errors: list[dict[str, str]] = []
        for p in self._files():
            try:
                out.append(self._read(p))
            except (OSError, ValueError) as e:        # JSONDecodeError and UnicodeDecodeError are ValueErrors
                errors.append({"file": p.name, "error": str(e)})
        self.load_errors = errors
        return out

    def get(self, slug: str) -> TestSetup:
        """The setup; SetupNotFound when absent; OSError/ValueError when its file is unreadable."""
        try:
            return self._read(self.path_of(slug))
        except FileNotFoundError:
            raise SetupNotFound(slug) from None

    def _write(self, s: TestSetup, *, count_new: bool = True) -> None:
        """Atomically replace the setup's file; a new slug past SETUPS_MAX raises TooManySetups
        unless `count_new` is false (a rename: the count does not change)."""
        path = self.path_of(s["slug"])
        if count_new and not path.exists() and len(self._files()) >= SETUPS_MAX:
            raise TooManySetups(f"at most {SETUPS_MAX} setups")
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".setup.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _created_of(self, slug: str) -> str | None:
        """The stored `created` of an existing setup, None when there is none or it cannot be read."""
        try:
            return self._read(self.path_of(slug))["created"] or None
        except (OSError, ValueError):
            return None

    def save(self, setup: TestSetup) -> TestSetup:
        """Validate, stamp (`created` kept when the slug exists, `updated` always set) and write. Returns what
        was stored. SetupError, TooManySetups, OSError."""
        s = parse_setup(setup)
        now = _now()
        s["created"] = self._created_of(s["slug"]) or now
        s["updated"] = now
        self._write(s)
        return s

    def file_slugs(self) -> set[str]:
        """The slug of every setup file on disk, corrupt ones included: what the SETUPS_MAX count in
        `_write` counts."""
        return {p.stem for p in self._files()}

    def import_setup(self, setup: TestSetup) -> TestSetup:
        """Validate and write an imported `setup`. Its `created` and `updated` are kept (a blank one gets
        the current time) only when the slug is new here. When the local setup has the same content (all but
        the timestamps) nothing is written and the local one is returned. Otherwise the local `created` is
        kept and `updated` is now, so changed content never keeps an old `updated` (Run compares it).
        SetupError, TooManySetups, OSError."""
        s = parse_setup(setup)
        now = _now()
        local: TestSetup | None = None
        unreadable = False
        try:
            local = self._read(self.path_of(s["slug"]))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            unreadable = True
        if local is None and not unreadable:
            s["created"] = s["created"] or now
            s["updated"] = s["updated"] or now
        else:
            if local is not None:
                same = {k: v for k, v in local.items() if k not in ("created", "updated")}
                if same == {k: v for k, v in s.items() if k not in ("created", "updated")}:
                    return local
            s["created"] = (local["created"] if local else "") or s["created"] or now
            s["updated"] = now
        self._write(s)
        return s

    def rename(self, slug: str, new_name: str) -> TestSetup:
        """Give the setup a new name and the slug that name derives. SetupNotFound, SetupExists (another
        setup has the new slug), SetupError."""
        s = self.get(slug)
        new = parse_setup({**s, "name": new_name})
        if new["slug"] != slug and self.path_of(new["slug"]).exists():
            raise SetupExists(new["slug"])
        new["created"] = s["created"]
        new["updated"] = _now()
        self._write(new, count_new=False)
        if new["slug"] != slug:
            self.path_of(slug).unlink(missing_ok=True)
        return new

    def duplicate(self, slug: str, new_name: str) -> TestSetup:
        """A copy under a new name, with new timestamps. SetupNotFound, SetupExists, TooManySetups,
        SetupError."""
        s = self.get(slug)
        new = parse_setup({**s, "name": new_name})
        if self.path_of(new["slug"]).exists():
            raise SetupExists(new["slug"])
        new["created"] = new["updated"] = _now()
        self._write(new)
        return new

    def delete(self, slug: str) -> None:
        """Remove the setup's file; SetupNotFound when absent."""
        try:
            self.path_of(slug).unlink()
        except FileNotFoundError:
            raise SetupNotFound(slug) from None
