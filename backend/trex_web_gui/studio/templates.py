"""User packet templates (P4b plan Task 4): `.trp` files under the templates directory, by a name of one to
eight `/`-separated segments.

A name is checked lexically before any I/O (`check_name`), and again after `os.path.realpath` (`path_of`), so
a symlink out of the directory is refused. Files are written atomically and hold at most PACKET_BODY_MAX
bytes. Pure Python: nothing here imports `trex` or `scapy`."""
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from trex_web_gui.store.settings import Preferences
from trex_web_gui.studio.packetdoc import PacketDoc, parse_doc

TEMPLATE_SEGMENTS_MAX = 8
TEMPLATE_NAME_MAX = 200
PACKET_BODY_MAX = 4 * 1024 * 1024      # a packet route's body, and a stored template file
SEGMENT_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
SUFFIX = ".trp"


class TemplateNameError(ValueError):
    """A template name the rules refuse (lexically, or because it resolves outside the directory)."""


class TemplateExists(Exception):
    """A template of that name exists and `overwrite` was not given."""


class TemplateNotFound(Exception):
    """No template of that name."""


class TemplateTooLarge(Exception):
    """The template's file would be, or is, over PACKET_BODY_MAX bytes."""


def check_name(name: object) -> list[str]:
    """The segments of the template name `name`; TemplateNameError unless it is 1-8 `/`-separated segments
    of `[A-Za-z0-9_.-]`, none empty, `.` or `..`, with no `\\` or NUL and at most 200 characters. No I/O."""
    if not isinstance(name, str) or not name:
        raise TemplateNameError("a template name is a non-empty string")
    if len(name) > TEMPLATE_NAME_MAX:
        raise TemplateNameError(f"a template name is at most {TEMPLATE_NAME_MAX} characters")
    if "\\" in name or "\x00" in name:
        raise TemplateNameError("a template name has no backslash or NUL")
    segments = name.split("/")
    if len(segments) > TEMPLATE_SEGMENTS_MAX:
        raise TemplateNameError(f"a template name has at most {TEMPLATE_SEGMENTS_MAX} segments")
    for seg in segments:
        if seg in ("", ".", "..") or not SEGMENT_RE.fullmatch(seg):
            raise TemplateNameError(f"segment {seg!r}: use 1-100 of letters, digits, '_', '.', '-'; "
                                    "not empty, '.' or '..'")
    return segments


def templates_dir(prefs: Preferences, data_dir: Path) -> Path:
    """The templates directory: `prefs.templates_dir` with `~` expanded when set, else `data_dir /
    "templates"`. Creates nothing; the first write does. Call it per use."""
    return Path(prefs.templates_dir).expanduser() if prefs.templates_dir else data_dir / "templates"


class TemplateStore:
    """The user templates under `dir_fn()`, re-read per call so a changed preference takes effect."""

    def __init__(self, dir_fn: Callable[[], Path]) -> None:
        """Remember `dir_fn`; touches no file."""
        self._dir_fn = dir_fn

    def directory(self) -> Path:
        """The templates directory now."""
        return self._dir_fn()

    def path_of(self, name: str) -> Path:
        """The file for `name`: `check_name` first (no I/O), then the resolved path must lie inside the
        resolved directory, else TemplateNameError."""
        segments = check_name(name)
        root = self._dir_fn()
        target = root.joinpath(*segments[:-1], segments[-1] + SUFFIX)
        real_root = os.path.realpath(root)
        real = os.path.realpath(target)
        if os.path.commonpath([real_root, real]) != real_root or real == real_root:
            raise TemplateNameError(f"{name!r} resolves outside the templates directory")
        return target

    def list(self) -> list[str]:
        """The names of the templates present, sorted; [] when the directory does not exist. Symlinked
        directories are not followed, and a file that resolves outside the directory is skipped."""
        root = self._dir_fn()
        names: list[str] = []
        for here, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.startswith(".") or not f.endswith(SUFFIX):
                    continue
                name = (Path(here) / f[:-len(SUFFIX)]).relative_to(root).as_posix()
                try:
                    self.path_of(name)
                except TemplateNameError:
                    continue
                names.append(name)
        return sorted(names)

    def read(self, name: str) -> PacketDoc:
        """The template `name`. TemplateNotFound, TemplateTooLarge, or ValueError/DocError when the file does
        not parse (a hand-edited file)."""
        path = self.path_of(name)
        try:
            if path.stat().st_size > PACKET_BODY_MAX:
                raise TemplateTooLarge(f"{name} is over {PACKET_BODY_MAX} bytes")
            data = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            raise TemplateNotFound(name) from None
        return parse_doc(json.loads(data))

    def write(self, name: str, doc: PacketDoc, overwrite: bool) -> None:
        """Write `doc` as the template `name`, atomically, creating the directories. TemplateExists when it
        is present and `overwrite` is false; TemplateTooLarge past PACKET_BODY_MAX bytes."""
        data = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
        if len(data) > PACKET_BODY_MAX:
            raise TemplateTooLarge(f"the template is {len(data)} bytes; at most {PACKET_BODY_MAX}")
        path = self.path_of(name)
        if not overwrite and path.exists():
            raise TemplateExists(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path_of(name)                                  # again: the directories now exist
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".template.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            if not overwrite and path.exists():
                raise TemplateExists(name)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, name: str) -> None:
        """Remove the template `name`; TemplateNotFound when it is not there."""
        try:
            self.path_of(name).unlink()
        except FileNotFoundError:
            raise TemplateNotFound(name) from None
