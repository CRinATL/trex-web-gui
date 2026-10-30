"""The settings bundle (P3d): export, preview and apply of connections, preferences, port labels and test
setups as one JSON document.

Shape: {format, version: 1, exported_at, app_version, sections: {preferences, connections, port_labels,
test_setups, theme?}}. `theme` is advisory: it is validated and reported, never stored here (the browser
keeps it). Every section is parsed by its own store's parser; apply parses every section whose mode is not
`skip` before it writes anything, and then writes atomically per file, not across files."""
import copy
import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from trex_web_gui.store.port_labels import LabelError, Labels, PortLabelStore
from trex_web_gui.store.port_labels import _parse as _parse_labels
from trex_web_gui.store.settings import (
    Preferences,
    SavedConnection,
    Settings,
    SettingsError,
    SettingsStore,
    parse_settings,
    validate,
)
from trex_web_gui.store.test_setups import (
    SETUPS_MAX,
    SetupError,
    SetupNotFound,
    TestSetup,
    TestSetupStore,
    TooManySetups,
    parse_setup,
)

FORMAT = "trex-web-gui-settings"
VERSION = 1
THEMES = ("system", "light", "dark", "contrast")
DATA_SECTIONS = ("preferences", "connections", "port_labels", "test_setups")
KNOWN_SECTIONS = (*DATA_SECTIONS, "theme")
Mode = Literal["replace", "merge", "skip"]
MODES = ("replace", "merge", "skip")


class BundleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        """`code` is the API error code: unsupported_bundle, invalid_bundle or bad_request."""
        super().__init__(message)
        self.code = code


class BundleWriteError(Exception):
    def __init__(self, written: list[str], failed: str, reason: str) -> None:
        """A file could not be written. `written` names what already was (sections, and
        `test_setups/<slug>` for each setup); `failed` names what was being written."""
        super().__init__(f"cannot write {failed}: {reason}")
        self.written = written
        self.failed = failed


@dataclass
class Stores:
    settings: SettingsStore
    labels: PortLabelStore
    setups: TestSetupStore


def export(stores: Stores, app_version: str) -> dict[str, Any]:
    """The bundle for the stores' current contents (no theme: the browser holds it)."""
    s = stores.settings.load()
    return {"format": FORMAT, "version": VERSION,
            "exported_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "app_version": app_version,
            "sections": {"preferences": dataclasses.asdict(s.preferences),
                         "connections": [dataclasses.asdict(c) for c in s.connections],
                         "port_labels": stores.labels.load(),
                         "test_setups": stores.setups.list()}}


def _sections(bundle: object) -> dict[str, Any]:
    """The bundle's sections object; BundleError unsupported_bundle for a wrong format or version."""
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise BundleError("unsupported_bundle", f"not a {FORMAT} bundle")
    v = bundle.get("version")
    if isinstance(v, bool) or not isinstance(v, int) or v != VERSION:
        shown = v if isinstance(v, int) and not isinstance(v, bool) else repr(v)
        raise BundleError("unsupported_bundle",
                          f"bundle version {shown} is not supported by this trex-web-gui (expects {VERSION})")
    sections = bundle.get("sections", {})
    if not isinstance(sections, dict):
        raise BundleError("unsupported_bundle", "sections must be an object")
    return sections


def _prefs_of(raw: object) -> Preferences:
    """Preferences from a section (missing keys default); SettingsError."""
    return parse_settings({"preferences": raw}).preferences


def _conns_of(raw: object) -> list[SavedConnection]:
    """Connections from a section, validated; SettingsError."""
    conns = parse_settings({"connections": raw}).connections
    validate(Settings(connections=conns))
    return conns


def _setups_of(raw: object) -> tuple[list[TestSetup], list[str]]:
    """The valid setups of a section (the first of a repeated slug refused) and an error line per bad one."""
    if not isinstance(raw, list):
        return [], ["test_setups: expected a list"]
    good: dict[str, TestSetup] = {}
    errors: list[str] = []
    for i, item in enumerate(raw):
        try:
            s = parse_setup(item)            # the slug is derived from the name, never taken from the bundle
        except SetupError as e:
            errors.append(f"test_setups[{i}]: {e}")
            continue
        if s["slug"] in good:
            errors.append(f"test_setups[{i}]: another setup in the bundle has the slug {s['slug']}")
            continue
        good[s["slug"]] = s
    return list(good.values()), errors


def _diff(local: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """adds, changes, removes: the keys only in `incoming`, in both with different values, only in `local`."""
    return {"adds": [k for k in incoming if k not in local],
            "changes": [k for k in incoming if k in local and incoming[k] != local[k]],
            "removes": [k for k in local if k not in incoming]}


def _section(present: bool = False, errors: list[str] | None = None, diff: dict[str, Any] | None = None,
             flags: list[str] | None = None) -> dict[str, Any]:
    """A preview section."""
    return {"present": present, "valid": not errors, "errors": errors or [],
            "adds": [], "changes": [], "removes": [], "flags": flags or [], **(diff or {})}


def _flatten_labels(labels: Labels) -> dict[str, Any]:
    """{"<server> <key>": entry}."""
    return {f"{srv} {key}": e for srv, keys in labels.items() for key, e in keys.items()}


def _no_ts(s: TestSetup) -> dict[str, Any]:
    """A setup without its timestamps, for comparing content."""
    return {k: v for k, v in s.items() if k not in ("created", "updated")}


def _cap_error(slugs_here: set[str], incoming: list[str], mode: str) -> list[str]:
    """An error line when the setups here plus the incoming slugs would pass SETUPS_MAX, else []."""
    n = len(slugs_here | set(incoming))
    if n <= SETUPS_MAX:
        return []
    return [f"test_setups: {mode} would leave {n} setups here; at most {SETUPS_MAX}"]


def _setup_flags(setups: list[TestSetup]) -> list[str]:
    """What an imported setup does later without a further prompt: force-acquire, stop, a port config."""
    out: list[str] = []
    for s in setups:
        for p in s["ports"]:
            if p["acquire_force"]:
                out.append(f"{s['slug']}: port {p['port']} force-acquires on Run")
            if p["stop_first"]:
                out.append(f"{s['slug']}: port {p['port']} stops a transmitting port before assigning")
            if p["layer"] is not None:
                out.append(f"{s['slug']}: port {p['port']} applies a port configuration")
    return out


def preview(bundle: object, stores: Stores) -> dict[str, Any]:
    """What applying `bundle` would do, per section, assuming replace (`removes` are what replace deletes).
    Raises BundleError unsupported_bundle for a wrong format or version."""
    raw = _sections(bundle)
    local = stores.settings.load()
    out: dict[str, Any] = {name: _section() for name in KNOWN_SECTIONS}
    profile_paths: list[str] = []
    path_changes: list[dict[str, Any]] = []
    for name in DATA_SECTIONS:
        if name not in raw:
            continue
        r = raw[name]
        sec = out[name] = _section(present=True)
        try:
            if name == "preferences":
                p = _prefs_of(r)
                sec["flags"] = [f"`{f.name}` changes from `{getattr(local.preferences, f.name)}` to "
                                f"`{getattr(p, f.name)}`" for f in dataclasses.fields(Preferences)
                                if getattr(p, f.name) != getattr(local.preferences, f.name)]
            elif name == "connections":
                conns = _conns_of(r)
                sec.update(_diff({c.name.strip().lower(): c for c in local.connections},
                                 {c.name.strip().lower(): c for c in conns}))
                names = {c.name.strip().lower(): c.name for c in [*local.connections, *conns]}
                for k in ("adds", "changes", "removes"):
                    sec[k] = [names[x] for x in sec[k]]
            elif name == "port_labels":
                sec.update(_diff(_flatten_labels(stores.labels.load()), _flatten_labels(_parse_labels(r))))
            else:
                setups, errors = _setups_of(r)
                # preview has no mode: the cap is checked for merge, the mode that adds files
                errors += _cap_error(stores.setups.file_slugs(), [s["slug"] for s in setups], "merge")
                sec["errors"] = errors
                sec["valid"] = not errors
                sec["flags"] = _setup_flags(setups)
                have = {s["slug"]: s for s in stores.setups.list()}
                sec.update(_diff({k: _no_ts(v) for k, v in have.items()},
                                 {s["slug"]: _no_ts(s) for s in setups}))
                profile_paths = list(dict.fromkeys(
                    [*profile_paths, *(sp["profile"]["path"] for s in setups for sp in s["ports"])]))
                for s in setups:
                    old = have.get(s["slug"])
                    if old:
                        before = {op["port"]: op["profile"]["path"] for op in old["ports"]}
                        path_changes += [
                            {"slug": s["slug"], "port": sp["port"], "from": before[sp["port"]],
                             "to": sp["profile"]["path"]}
                            for sp in s["ports"]
                            if sp["port"] in before and before[sp["port"]] != sp["profile"]["path"]]
        except (SettingsError, LabelError) as e:
            sec.update(_section(present=True, errors=[f"{name}: {e}"]))
    if "theme" in raw:
        ok = raw["theme"] in THEMES
        out["theme"] = _section(present=True, errors=[] if ok else [
            f"theme: {raw['theme']!r} is not one of {', '.join(THEMES)}"])
    return {"version": VERSION, "sections": out,
            "ignored": sorted(k for k in raw if k not in KNOWN_SECTIONS),
            "profile_paths": profile_paths, "path_changes": path_changes}


def _merge_labels(local: Labels, incoming: Labels) -> Labels:
    """`local` with every entry of `incoming` set over it."""
    out = copy.deepcopy(local)
    for srv, keys in incoming.items():
        out.setdefault(srv, {}).update(keys)
    return out


def apply(bundle: object, modes: dict[str, str], stores: Stores) -> dict[str, list[str]]:
    """Apply the bundle's sections by mode (a section without a mode is skipped). Every section whose mode
    is not `skip` is parsed first: BundleError invalid_bundle naming it, nothing written. Then
    preferences+connections, port_labels and test_setups are written; BundleWriteError names what was
    written when a file fails. `replace` of test_setups writes the new files before deleting local-only ones.
    Returns {"written": [section], "skipped": [section]}."""
    raw = _sections(bundle)
    for name, m in modes.items():
        if m not in MODES:
            raise BundleError("bad_request", f"mode for {name} must be one of {', '.join(MODES)}")
    if modes.get("preferences") == "merge":
        raise BundleError("bad_request", "preferences: mode must be replace or skip")
    active = [n for n in DATA_SECTIONS if n in raw and modes.get(n, "skip") != "skip"]
    new_settings: Settings | None = None
    new_labels: Labels | None = None
    to_write: list[TestSetup] = []
    to_delete: list[str] = []
    try:
        if "preferences" in active or "connections" in active:
            cur = stores.settings.load()
            prefs, conns = cur.preferences, cur.connections
            if "preferences" in active:
                p = _prefs_of(raw["preferences"])
                prefs = p
            if "connections" in active:
                incoming = _conns_of(raw["connections"])
                if modes["connections"] == "replace":
                    conns = incoming
                else:
                    conns = list(conns)
                    for c in incoming:
                        k = c.name.strip().lower()
                        at = next((i for i, x in enumerate(conns) if x.name.strip().lower() == k), None)
                        if at is None:
                            conns.append(c)
                        else:
                            conns[at] = c
            new_settings = Settings(prefs, conns)
            validate(new_settings)
        if "port_labels" in active:
            incoming_labels = _parse_labels(raw["port_labels"])
            new_labels = incoming_labels if modes["port_labels"] == "replace" else _merge_labels(
                stores.labels.load(), incoming_labels)
    except (SettingsError, LabelError) as e:
        raise BundleError("invalid_bundle", str(e)) from None
    if "test_setups" in active:
        to_write, errors = _setups_of(raw["test_setups"])
        if errors:
            raise BundleError("invalid_bundle", "; ".join(errors))
        have = [s["slug"] for s in stores.setups.list()]
        incoming_slugs = [s["slug"] for s in to_write]
        if over := _cap_error(stores.setups.file_slugs(), incoming_slugs, modes["test_setups"]):
            raise BundleError("invalid_bundle", over[0])
        if modes["test_setups"] == "replace":
            to_delete = [s for s in have if s not in incoming_slugs]

    written: list[str] = []
    done: list[str] = []
    if new_settings is not None:
        names = [n for n in ("preferences", "connections") if n in active]
        try:
            stores.settings.save(new_settings)
        except SettingsError as e:
            # the store refused the content; this is the first write, so nothing else is on disk yet
            raise BundleError("invalid_bundle", f"preferences: {e}") from None
        except OSError as e:
            raise BundleWriteError(written, "+".join(names), str(e)) from e
        written += names
        done += names
    if new_labels is not None:
        try:
            stores.labels.save(new_labels)
        except OSError as e:
            raise BundleWriteError(written, "port_labels", str(e)) from e
        written.append("port_labels")
        done.append("port_labels")
    if "test_setups" in active:
        for s in to_write:
            try:
                stores.setups.import_setup(s)
            except (OSError, TooManySetups) as e:
                raise BundleWriteError(written, f"test_setups/{s['slug']}", str(e)) from e
            written.append(f"test_setups/{s['slug']}")
        for slug in to_delete:
            try:
                stores.setups.delete(slug)
            except SetupNotFound:
                pass
            except OSError as e:
                raise BundleWriteError(written, f"test_setups/{slug}", str(e)) from e
        done.append("test_setups")
    return {"written": done, "skipped": [n for n in KNOWN_SECTIONS if n not in done]}
