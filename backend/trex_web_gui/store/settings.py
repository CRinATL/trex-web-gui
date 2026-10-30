"""settings.toml in the data dir: preferences (A11.1) and saved connections (A1.2)."""
import dataclasses
import os
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

PCAP_CAP_MAX_MIB = 1024


class SettingsError(ValueError):
    pass


@dataclass
class SavedConnection:
    name: str
    host: str
    sync_port: int = 4501
    async_port: int = 4500
    timeout_s: float = 3.0


@dataclass
class Preferences:
    templates_dir: str = ""
    wireshark_path: str = "wireshark"
    workspace_dir: str = ""
    release_on_exit: bool = True
    trex_cfg_path: str = "/etc/trex_cfg.yaml"
    library_dir: str = ""
    pcap_size_cap_mib: int = 64


@dataclass
class Settings:
    preferences: Preferences = field(default_factory=Preferences)
    connections: list[SavedConnection] = field(default_factory=list)


def default_data_dir() -> Path:
    """$XDG_DATA_HOME/trex-web-gui, else ~/.local/share/trex-web-gui; creates nothing."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "trex-web-gui"


_TYPES: dict[str, type | tuple[type, ...]] = {
    "name": str, "host": str, "sync_port": int, "async_port": int, "timeout_s": (int, float),
    "templates_dir": str, "wireshark_path": str, "workspace_dir": str,
    "release_on_exit": bool, "trex_cfg_path": str, "library_dir": str,
    "pcap_size_cap_mib": int}


def _typed(cls: type, raw: object) -> Any:
    """A Preferences or SavedConnection from a table, unknown keys ignored; SettingsError on a wrong type."""
    if not isinstance(raw, dict):
        raise SettingsError(f"{cls.__name__}: expected a table, got {type(raw).__name__}")
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name in raw:                               # unknown keys are ignored
            v: Any = raw[f.name]
            want = _TYPES[f.name]
            if isinstance(v, bool) != (want is bool) or not isinstance(v, want):   # bool only where wanted
                raise SettingsError(f"{cls.__name__}.{f.name}: wrong type {type(v).__name__}")
            out[f.name] = float(str(v)) if f.name == "timeout_s" else v
    return cls(**out)


def parse_settings(raw: object) -> Settings:
    """Build Settings from untrusted data (a parsed TOML file or a PUT body) with the same type checks
    for both. Raises SettingsError on any wrong type or shape."""
    if not isinstance(raw, dict):
        raise SettingsError(f"settings: expected a table, got {type(raw).__name__}")
    conns = raw.get("connections", [])
    if not isinstance(conns, list):
        raise SettingsError(f"connections: expected a list, got {type(conns).__name__}")
    try:
        out = Settings(_typed(Preferences, raw.get("preferences", {})),
                       [_typed(SavedConnection, c) for c in conns])
    except TypeError as e:                              # a required key (name, host) is missing
        raise SettingsError(str(e)) from e
    _check_cfg_path(out.preferences)                    # also here: a PUT then gets 422, not save's 400
    _check_library_dir(out.preferences)
    _check_templates_dir(out.preferences)
    _check_pcap_cap(out.preferences)
    return out


def _check_cfg_path(p: Preferences) -> None:
    """Raise SettingsError unless trex_cfg_path is an absolute path without a NUL."""
    if "\x00" in p.trex_cfg_path or not Path(p.trex_cfg_path).is_absolute():
        raise SettingsError(f"trex_cfg_path {p.trex_cfg_path!r} is not an absolute path")


def _check_library_dir(p: Preferences) -> None:
    """Raise SettingsError unless library_dir is empty or, after `~` expansion, absolute and without a NUL."""
    d = p.library_dir
    if d and ("\x00" in d or not os.path.isabs(os.path.expanduser(d))):
        raise SettingsError(f"library_dir {d!r} is not an absolute path")


def _check_templates_dir(p: Preferences) -> None:
    """Raise SettingsError unless templates_dir is empty or, after `~` expansion, absolute without a NUL."""
    d = p.templates_dir
    if d and ("\x00" in d or not os.path.isabs(os.path.expanduser(d))):
        raise SettingsError(f"templates_dir {d!r} is not an absolute path")


def _check_pcap_cap(p: Preferences) -> None:
    """Raise SettingsError unless pcap_size_cap_mib is 1-1024."""
    if not 1 <= p.pcap_size_cap_mib <= PCAP_CAP_MAX_MIB:
        raise SettingsError(f"pcap_size_cap_mib {p.pcap_size_cap_mib} is not 1-{PCAP_CAP_MAX_MIB}")


def validate(s: Settings) -> None:
    """Raise SettingsError: bad cfg path, empty name or host, bad port or timeout, repeated name."""
    _check_cfg_path(s.preferences)
    _check_library_dir(s.preferences)
    _check_templates_dir(s.preferences)
    _check_pcap_cap(s.preferences)
    seen: set[str] = set()
    for c in s.connections:
        if not c.name.strip():
            raise SettingsError("connection name is empty")
        if not c.host.strip():
            raise SettingsError(f"connection {c.name!r}: host is empty")
        for label, p in (("sync port", c.sync_port), ("async port", c.async_port)):
            if not 1 <= p <= 65535:
                raise SettingsError(f"connection {c.name!r}: {label} {p} is not 1-65535")
        if not 0.5 <= c.timeout_s <= 60:
            raise SettingsError(f"connection {c.name!r}: timeout {c.timeout_s} s is not 0.5-60 s")
        key = c.name.strip().lower()
        if key in seen:
            raise SettingsError(f"duplicate connection name {c.name!r}")
        seen.add(key)


class SettingsStore:
    def __init__(self, data_dir: Path) -> None:
        """Point at settings.toml in data_dir; touches no file."""
        self.path = data_dir / "settings.toml"
        self.load_error: str | None = None

    def load(self) -> Settings:
        """The saved settings; defaults when missing, or unreadable or invalid (sets load_error)."""
        self.load_error = None
        try:
            try:
                text = self.path.read_text(encoding="utf-8")   # no exists(): 3.14 says False on EACCES
            except FileNotFoundError:
                return Settings()
            s = parse_settings(tomllib.loads(text))
            validate(s)
            return s
        except (tomllib.TOMLDecodeError, SettingsError, OSError, UnicodeDecodeError) as e:
            self.load_error = f"{self.path}: {e}. Using defaults; the file is kept until you save."
            return Settings()

    def save(self, s: Settings) -> None:
        """Validate, then atomically replace the file, first renaming one that failed to load."""
        validate(s)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.load_error and self.path.exists():
            stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
            self.path.rename(self.path.with_name(f"settings.toml.bad-{stamp}"))
        data = {"preferences": dataclasses.asdict(s.preferences),
                "connections": [dataclasses.asdict(c) for c in s.connections]}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".settings.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.load_error = None
