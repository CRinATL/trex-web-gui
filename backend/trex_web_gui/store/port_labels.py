"""port_labels.toml in the data dir: user names and descriptions for ports (P3a).

Layout: { "<host>:<sync_port>" = { "<mac or #N>" = { name = "...", description = "..." } } }.
Same load/save discipline as SettingsStore: atomic replace; a corrupt file reports a load_error, defaults
are used, and the bad file is renamed only on the next save."""
import os
import re
import tempfile
import time
import tomllib
import unicodedata
from pathlib import Path

import tomli_w

NAME_MAX = 40
DESCRIPTION_MAX = 500
SERVER_MAX = 300
_KEY = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}|#[0-9]{1,3}")

# Bidi overrides and isolates can reorder the text around a label; other Cf (ZWJ in emoji) stay allowed.
_BIDI = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

Labels = dict[str, dict[str, dict[str, str]]]


class LabelError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        """Keep the field and the reason apart from the combined "field: reason" message."""
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


def _has_control(s: str) -> bool:
    """True when s holds a control, line or paragraph separator, or bidi override or isolate."""
    return any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") or ch in _BIDI for ch in s)


def check_server(server: object) -> str:
    """The server when it is 1-SERVER_MAX printable characters; else raises LabelError (server)."""
    if not isinstance(server, str) or not server or len(server) > SERVER_MAX or not server.isprintable():
        raise LabelError("server", f"must be 1-{SERVER_MAX} printable characters")
    return server


def check_key(key: object) -> str:
    """The key when it is a lower-case MAC or #<port id>; else raises LabelError (key)."""
    # fullmatch, not match: "$" would accept a trailing newline
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise LabelError("key", "must be a lower-case MAC (aa:bb:cc:dd:ee:ff) or #<port id>")
    return key


def _text(field: str, value: object, limit: int) -> str:
    """The value stripped; raises LabelError for a non-str, a control or bidi character, or too long."""
    if not isinstance(value, str):
        raise LabelError(field, "must be text")
    if _has_control(value):
        raise LabelError(field, "must not contain a control character")
    v = value.strip()
    if len(v) > limit:
        raise LabelError(field, f"is {len(v)} characters; the limit is {limit}")
    return v


def _parse(raw: object) -> Labels:
    """Untrusted data (a parsed file) to Labels, with the rules set() applies. Raises LabelError."""
    if not isinstance(raw, dict):
        raise LabelError("file", "expected a table")
    out: Labels = {}
    for server, keys in raw.items():
        check_server(server)
        if not isinstance(keys, dict):
            raise LabelError("server", f"{server!r}: expected a table")
        for key, entry in keys.items():
            check_key(key)
            if not isinstance(entry, dict):
                raise LabelError("key", f"{key!r}: expected a table")
            name = _text("name", entry.get("name", ""), NAME_MAX)
            desc = _text("description", entry.get("description", ""), DESCRIPTION_MAX)
            if name or desc:
                out.setdefault(server, {})[key] = {"name": name, "description": desc}
    return out


class PortLabelStore:
    def __init__(self, data_dir: Path) -> None:
        """Point at port_labels.toml in data_dir; touches no file."""
        self.path = data_dir / "port_labels.toml"
        self.load_error: str | None = None

    def load(self) -> Labels:
        """The labels in the file; {} when missing, or unreadable or invalid (sets load_error, file kept)."""
        self.load_error = None
        try:
            try:
                text = self.path.read_text(encoding="utf-8")   # no exists(): 3.14 says False on EACCES
            except FileNotFoundError:
                return {}
            return _parse(tomllib.loads(text))
        except (tomllib.TOMLDecodeError, LabelError, OSError, UnicodeDecodeError) as e:
            self.load_error = f"{self.path}: {e}. Using no labels; the file is kept until you save."
            return {}

    def save(self, labels: Labels) -> None:
        """Atomically replace the file, first renaming one that failed to load to .bad-<stamp>."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.load_error and self.path.exists():
            stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
            self.path.rename(self.path.with_name(f"port_labels.toml.bad-{stamp}"))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".port_labels.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(labels, f)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.load_error = None

    def set(self, server: str, key: str, name: str, description: str) -> Labels:
        """Validate, then load-modify-save. An empty name and description delete the entry. Returns the
        whole map. Raises LabelError (nothing written) or OSError (the write failed)."""
        server = check_server(server)
        key = check_key(key)
        name = _text("name", name, NAME_MAX)
        description = _text("description", description, DESCRIPTION_MAX)
        labels = self.load()
        if name or description:
            labels.setdefault(server, {})[key] = {"name": name, "description": description}
        else:
            labels.get(server, {}).pop(key, None)
            if not labels.get(server, True):
                del labels[server]
        self.save(labels)
        return labels
