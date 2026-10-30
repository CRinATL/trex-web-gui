"""The profile library: `<slug>.json` (exactly `STLProfile.to_json()`) and `<slug>.gui.json` per profile.

Pure Python; imports neither `trex` nor `scapy`. The JSON is always written before the sidecar, both
atomically. A listing reads sidecars only and never writes. A library directory the user also touches by hand
is tolerated: a JSON with no sidecar lists, a bad file goes to the load errors, and an unparsable sidecar is
never overwritten."""
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from trex_web_gui.store.settings import Preferences
from trex_web_gui.store.test_setups import SLUG_RE, slugify
from trex_web_gui.studio.sidecar import Sidecar, Source, new_sidecar, now, parse_sidecar
from trex_web_gui.studio.streams import DiffResult, StreamsDiff, apply_diff

NAME_MAX = 80
LIBRARY_MAX = 1000
SIDE_SUFFIX = ".gui.json"
StreamList = list[dict[str, Any]]      # the `list` method below shadows the builtin inside the class body


class LibraryNotFound(LookupError):
    """No such profile (or a slug outside SLUG_RE)."""


class LibraryExists(Exception):
    """Another profile has the slug of the new name."""


class TooManyProfiles(Exception):
    """The library holds LIBRARY_MAX profiles."""


class StaleRevision(Exception):
    """The profile changed since `rev` was read; `current_rev` is what it is now."""

    def __init__(self, current_rev: str) -> None:
        """Keep the current revision."""
        super().__init__(f"the profile changed (now {current_rev})")
        self.current_rev = current_rev


class LibraryError(ValueError):
    """A bad name: `field` is "name"."""

    def __init__(self, field: str, reason: str) -> None:
        """Keep the field and the reason."""
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


class SidecarUnreadable(Exception):
    """An edit over a sidecar that does not parse; it is left as it is."""

    def __init__(self, file: str, reason: str) -> None:
        """Keep the file name and the parse error."""
        super().__init__(f"{file} does not parse ({reason}); fix or delete it before editing this profile")
        self.file = file
        self.reason = reason


class LibraryEntry(TypedDict):
    """One row of the listing."""

    slug: str
    name: str
    path: str
    stream_count: int
    created: str
    updated: str
    source: Source | None
    source_missing: bool
    load_error: str | None


class LoadError(TypedDict):
    """A file the listing could not use."""

    file: str
    error: str


class Profile(NamedTuple):
    """A profile as read: streams, sidecar (a default one when the file is absent or bad) and `rev`."""

    streams: list[dict[str, Any]]
    sidecar: Sidecar
    rev: str


class _Loaded(NamedTuple):
    """Both files of a profile, parsed."""

    streams: list[dict[str, Any]]
    sidecar: Sidecar | None
    sidecar_error: str | None
    json_bytes: bytes
    sidecar_bytes: bytes
    rev: str


def _read_json(path: Path) -> bytes:
    """The bytes of a library file: the one place a JSON is read."""
    return path.read_bytes()


def rev_of(json_bytes: bytes, sidecar_bytes: bytes) -> str:
    """The content revision: sha256 of the JSON, a NUL and the sidecar (empty when absent), 16 hex digits."""
    return hashlib.sha256(json_bytes + b"\0" + sidecar_bytes).hexdigest()[:16]


def _streams_of(data: bytes) -> list[dict[str, Any]]:
    """The streams in a profile JSON's bytes; ValueError unless it is a list of objects."""
    streams = json.loads(data)
    if not isinstance(streams, list) or not all(isinstance(s, dict) for s in streams):
        raise ValueError("not a list of streams")
    return streams


def _clean_name(name: object) -> str:
    """The name stripped; LibraryError("name", ...) unless 1-NAME_MAX printable characters with a slug."""
    if not isinstance(name, str):
        raise LibraryError("name", "must be text")
    n = name.strip()
    if not n or len(n) > NAME_MAX:
        raise LibraryError("name", f"must be 1-{NAME_MAX} characters")
    if not n.isprintable():
        raise LibraryError("name", "has a character that is not printable")
    if not slugify(n):
        raise LibraryError("name", "needs at least one letter or digit")
    return n


def _mtime(path: Path) -> str:
    """A file's modification time in the sidecar timestamp format ("" when unreadable)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except OSError:
        return ""


def library_dir(prefs: Preferences, data_dir: Path) -> Path:
    """The library directory: `prefs.library_dir` with `~` expanded when set, else `data_dir / "profiles"`.
    Creates nothing; the first write does. Call it per use: `ProfileLibrary(lambda: library_dir(...))`."""
    return Path(prefs.library_dir).expanduser() if prefs.library_dir else data_dir / "profiles"


class ProfileLibrary:
    """The library directory `dir_fn()` names, resolved on every call."""

    def __init__(self, dir_fn: Callable[[], Path]) -> None:
        """Remember how to find the directory; touches nothing."""
        self._dir_fn = dir_fn

    def _dir(self) -> Path:
        """The library directory now."""
        return self._dir_fn()

    def path_of(self, slug: str) -> Path:
        """The JSON file for `slug`; LibraryNotFound for a slug outside SLUG_RE, before any file access."""
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise LibraryNotFound(slug)
        return self._dir() / f"{slug}.json"

    def _side_path(self, slug: str) -> Path:
        """The sidecar file for `slug`."""
        return self.path_of(slug).with_name(f"{slug}{SIDE_SUFFIX}")

    def _slugs(self) -> list[str]:
        """The slug of every `<slug>.json` in the directory, sorted; empty when it does not exist."""
        try:
            names = os.listdir(self._dir())
        except FileNotFoundError:
            return []
        return sorted(n[:-5] for n in names if n.endswith(".json") and not n.endswith(SIDE_SUFFIX)
                      and not n.startswith(".") and SLUG_RE.fullmatch(n[:-5]))

    def _taken(self, slug: str) -> bool:
        """Whether either file of `slug` exists."""
        return self.path_of(slug).exists() or self._side_path(slug).exists()

    def _atomic(self, path: Path, data: bytes) -> None:
        """Replace `path` with `data` through a temp file in its directory; the temp file goes on failure."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".profile.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _write(self, slug: str, json_bytes: bytes, sidecar: Sidecar) -> str:
        """Write the JSON, then the sidecar; the new rev."""
        side = json.dumps(sidecar, indent=2).encode()
        self._atomic(self.path_of(slug), json_bytes)
        self._atomic(self._side_path(slug), side)
        return rev_of(json_bytes, side)

    def _load(self, slug: str) -> _Loaded:
        """Both files of `slug`; LibraryNotFound if the JSON is absent, ValueError if it does not parse."""
        path = self.path_of(slug)
        try:
            jb = _read_json(path)
        except FileNotFoundError:
            raise LibraryNotFound(slug) from None
        present = True
        try:
            sb = _read_json(self._side_path(slug))
        except FileNotFoundError:
            sb, present = b"", False
        side: Sidecar | None = None
        err: str | None = None
        if present:                                      # an empty file is present, and does not parse
            try:
                side = parse_sidecar(json.loads(sb))
            except ValueError as e:                      # JSON errors and SidecarError are ValueErrors
                err = str(e)
        return _Loaded(_streams_of(jb), side, err, jb, sb, rev_of(jb, sb))

    def _editable(self, slug: str, expect_rev: str) -> tuple[_Loaded, Sidecar]:
        """The profile for an edit based on `expect_rev` and the sidecar to edit (a fresh one when there is no
        sidecar file); SidecarUnreadable over a bad sidecar, StaleRevision on a changed profile."""
        got = self._load(slug)
        if got.sidecar_error is not None:
            raise SidecarUnreadable(f"{slug}{SIDE_SUFFIX}", got.sidecar_error)
        if got.rev != expect_rev:
            raise StaleRevision(got.rev)
        return got, got.sidecar or new_sidecar(slug, None, len(got.streams))

    def get(self, slug: str) -> Profile:
        """The profile; a profile whose sidecar is absent or bad still loads, with a default sidecar."""
        got = self._load(slug)
        return Profile(got.streams, got.sidecar or new_sidecar(slug, None, len(got.streams)), got.rev)

    def _entry(self, slug: str, side: Sidecar, load_error: str | None = None) -> LibraryEntry:
        """The listing row for `slug` and its sidecar."""
        src = side["source"]
        missing = (src is not None and src["kind"] == "py"
                   and (src["path"] is None or not os.path.isfile(src["path"])))
        return {"slug": slug, "name": side["name"], "path": str(self.path_of(slug)),
                "stream_count": side["stream_count"], "created": side["created"], "updated": side["updated"],
                "source": src, "source_missing": missing, "load_error": load_error}

    def list(self) -> tuple[list[LibraryEntry], list[LoadError]]:
        """Every usable profile sorted by slug, and the files that were not. Reads sidecars; a profile JSON is
        read only when there is no usable sidecar to give its count. Writes nothing."""
        d = self._dir()
        try:
            names = sorted(os.listdir(d))
        except FileNotFoundError:
            return [], []
        entries: list[LibraryEntry] = []
        errors: list[LoadError] = []
        for n in names:
            if not n.endswith(".json") or n.endswith(SIDE_SUFFIX) or n.startswith("."):
                continue
            slug = n[:-5]
            if not SLUG_RE.fullmatch(slug):
                errors.append({"file": n, "error": "file name is not a library slug"})
                continue
            load_error: str | None = None
            side: Sidecar | None = None
            try:
                side = parse_sidecar(json.loads(_read_json(d / f"{slug}{SIDE_SUFFIX}")))
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                load_error = str(e)
            if side is None:
                try:
                    count = len(_streams_of(_read_json(d / n)))
                except (OSError, ValueError) as e:
                    errors.append({"file": n, "error": str(e)})
                    continue
                side = new_sidecar(slug, None, count)
                side["created"] = side["updated"] = _mtime(d / n)
            entries.append(self._entry(slug, side, load_error))
        return entries, errors

    def _check_room(self) -> None:
        """TooManyProfiles when the library is full."""
        if len(self._slugs()) >= LIBRARY_MAX:
            raise TooManyProfiles(f"at most {LIBRARY_MAX} profiles")

    def create(self, name: str, streams: StreamList | None = None, source: Source | None = None,
               pcap_wizard: dict[str, Any] | None = None) -> LibraryEntry:
        """A new profile named `name`. LibraryError, LibraryExists, TooManyProfiles, OSError."""
        n = _clean_name(name)
        slug = slugify(n)
        if self._taken(slug):
            raise LibraryExists(slug)
        self._check_room()
        streams = streams or []
        side = new_sidecar(n, source, len(streams))
        side["pcap_wizard"] = pcap_wizard
        self._write(slug, json.dumps(streams, indent=2).encode(), side)
        return self._entry(slug, side)

    def save(self, slug: str, streams: StreamList, sidecar: Sidecar, expect_rev: str) -> str:
        """Replace both files with `streams` and `sidecar` (its count and `updated` set) when the profile is
        still at `expect_rev`; the new rev. StaleRevision, SidecarUnreadable, LibraryNotFound."""
        self._editable(slug, expect_rev)
        side: Sidecar = {**sidecar, "stream_count": len(streams), "updated": now()}
        return self._write(slug, json.dumps(streams, indent=2).encode(), side)

    def apply(self, slug: str, diff: StreamsDiff) -> tuple[DiffResult, str]:
        """Apply `diff` (its `rev` is the base) and write the result; the result and the new rev. StreamError
        for a refused diff as well as the errors of `save`."""
        got, side = self._editable(slug, diff.rev)
        res = apply_diff(got.streams, side["streams"], diff)
        new: Sidecar = {**side, "streams": res.sidecar_streams, "stream_count": len(res.streams),
                        "updated": now()}
        return res, self._write(slug, json.dumps(res.streams, indent=2).encode(), new)

    def append(self, slug: str, streams: StreamList, expect_rev: str) -> tuple[DiffResult, str]:
        """Add `streams` at the end (colliding names renamed); an `added`-only diff."""
        return self.apply(slug, StreamsDiff(rev=expect_rev, added=streams))

    def rename(self, slug: str, new_name: str, expect_rev: str) -> LibraryEntry:
        """Give the profile a new name and the slug it derives. The same slug renames in the sidecar only;
        otherwise the new JSON, the new sidecar, then the old files are written/removed in that order, so a
        crash leaves two listed entries and never one that lost its source."""
        got, side = self._editable(slug, expect_rev)
        n = _clean_name(new_name)
        new_slug = slugify(n)
        if new_slug != slug and self._taken(new_slug):
            raise LibraryExists(new_slug)
        new: Sidecar = {**side, "name": n, "stream_count": len(got.streams), "updated": now()}
        if new_slug == slug:
            self._atomic(self._side_path(slug), json.dumps(new, indent=2).encode())
        else:
            self._write(new_slug, got.json_bytes, new)
            self.path_of(slug).unlink(missing_ok=True)
            self._side_path(slug).unlink(missing_ok=True)
        return self._entry(new_slug, new)

    def duplicate(self, slug: str, new_name: str) -> LibraryEntry:
        """A copy under a new name, keeping `source` and `pcap_wizard`, with new timestamps. LibraryNotFound,
        LibraryError, LibraryExists, TooManyProfiles."""
        got = self._load(slug)
        if got.sidecar_error is not None:
            raise SidecarUnreadable(f"{slug}{SIDE_SUFFIX}", got.sidecar_error)
        n = _clean_name(new_name)
        new_slug = slugify(n)
        if self._taken(new_slug):
            raise LibraryExists(new_slug)
        self._check_room()
        old = got.sidecar or new_sidecar(slug, None, len(got.streams))
        new = new_sidecar(n, old["source"], len(got.streams))
        new["streams"] = (list(old["streams"]) + [{} for _ in got.streams])[:len(got.streams)]
        new["pcap_wizard"] = old["pcap_wizard"]
        self._write(new_slug, got.json_bytes, new)
        return self._entry(new_slug, new)

    def delete(self, slug: str) -> None:
        """Remove both files; LibraryNotFound when the JSON is absent."""
        try:
            self.path_of(slug).unlink()
        except FileNotFoundError:
            raise LibraryNotFound(slug) from None
        self._side_path(slug).unlink(missing_ok=True)

