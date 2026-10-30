"""The capture spool: pcap files under the data dir's `captures/` (P3c).

Layout: `rec-<id>-<UTC stamp>[-<n>].partial.pcap` while a Save is incomplete, renamed to `.pcap` at close.
Next to each file, `<name>.json` is a sidecar `{server, start_ts, capture_id, pkt_count, partial}` written
to a temp file and `os.replace`d, so a reader never sees half of it. A partial's `pkt_count` is never read
back: `list()` and `resume()` count its whole records on disk. A capture's identity is
`CaptureKey(server, start_ts, capture_id)`, not the id: ids restart at 1 with every TRex process. A partial
file resumes only for the key in its sidecar. The directory is 0700 (also when it pre-existed), files 0600.
Names are opened with O_EXCL and take a numeric suffix on collision; nothing is ever overwritten.
`held()` names the files an open Writer owns; `delete` refuses them (`SpoolBusy`). The path of a file is
never taken from a request: `path()` accepts only `^[\\w.-]+\\.pcap$` (fullmatch: a trailing newline fails).
pcap is written in the one format of store/pcap.py (linktype 1, microsecond timestamps); no Scapy. Not
thread-safe: call it from one thread (the loop)."""
import contextlib
import json
import os
import re
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, TypeGuard

from trex_web_gui.store import pcap

_NAME = re.compile(r"[\w.-]+\.pcap")
_ID = re.compile(r"rec-(\d+)-")
_PARTIAL = ".partial.pcap"


class SpoolFile(TypedDict):
    name: str
    size: int
    mtime: float
    capture_id: int | None
    server: str | None
    start_ts: float | None
    pkt_count: int | None
    partial: bool


@dataclass(frozen=True)
class CaptureKey:
    server: str
    start_ts: float
    capture_id: int


class SpoolBusy(Exception):
    """The file is open in a Writer; the caller answers 409 capture_busy."""


def _is_int(v: object) -> TypeGuard[int]:
    """An int that is not a bool (a sidecar's `true` is not a count or an id)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _count(path: Path) -> int | None:
    """The whole records in `path`, or None when `_scan` cannot read it as a pcap (unknown, not 0)."""
    scanned = _scan(path)
    return scanned[0] if scanned else None


def _scan(path: Path) -> tuple[int, int] | None:
    """(whole records, byte offset after the last whole one); a torn tail after valid records is dropped.
    None when the file cannot be read or does not start with a pcap header: that is not "no records", and
    the caller must leave such a file as it is."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < pcap.GLOBAL.size or struct.unpack_from("<I", data)[0] != pcap.MAGIC:
        return None
    pos, n = pcap.GLOBAL.size, 0
    while pos + pcap.RECORD.size <= len(data):
        incl = pcap.RECORD.unpack_from(data, pos)[2]
        if pos + pcap.RECORD.size + incl > len(data):
            break
        pos += pcap.RECORD.size + incl
        n += 1
    return n, pos


class Writer:
    def __init__(self, spool: "Spool", key: CaptureKey, name: str, fd: int, count: int) -> None:
        """Spool.writer() and resume() build it around an open fd; the spool already lists name as held."""
        self._spool = spool
        self._key = key
        self._name = name
        self._fd: int | None = fd
        self.count = count

    def write(self, ts_rel: float, raw: bytes, wirelen: int) -> None:
        """Append one record; ValueError once closed, OSError on a failed write (a torn tail uncounted)."""
        if self._fd is None:
            raise ValueError("writer is closed")
        pcap.write_all(self._fd, pcap.pack_record(ts_rel, raw, wirelen))
        self.count += 1             # only once the whole record is out

    def _release(self) -> None:
        """Close the fd and unhold the file; repeating is a no-op, and a close error still unholds."""
        try:
            if self._fd is not None:
                fd, self._fd = self._fd, None
                os.close(fd)
        finally:
            self._spool._held.discard(self._name)

    def suspend(self) -> SpoolFile:
        """Keep the partial file for a later resume(); the sidecar records the key."""
        self._release()
        side = self._spool._write_sidecar(self._name, self._key, self.count, True)
        return self._spool._describe(self._name, side)

    def close(self) -> SpoolFile:
        """Complete the file: partial -> final name (never replacing one), sidecar for the final name."""
        self._release()
        sp = self._spool
        final = sp._link_final(self._name)
        sp._unlink(self._name)
        sp._unlink(self._name + ".json")
        return sp._describe(final, sp._write_sidecar(final, self._key, self.count, False))

    def abort(self) -> None:
        """Release the file and delete it and its sidecar; nothing is kept."""
        self._release()
        self._spool._unlink(self._name)
        self._spool._unlink(self._name + ".json")


class Spool:
    def __init__(self, dir: Path) -> None:
        """Create the directory if needed and force it to mode 0700, also when it already existed."""
        self.dir = dir
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self._held: set[str] = set()
        self.skipped: list[str] = []        # partials resume() matched but could not read: left untouched

    def path(self, name: str) -> Path:
        """The file's path in the spool; ValueError unless name is a plain .pcap name. No existence check."""
        if not _NAME.fullmatch(name):
            raise ValueError(f"bad capture file name: {name!r}")
        return self.dir / name

    def held(self) -> frozenset[str]:
        """Names of the files an open Writer owns."""
        return frozenset(self._held)

    def is_held(self, name: str) -> bool:
        """True when an open Writer owns name."""
        return name in self._held

    def delete(self, name: str) -> None:
        """Delete the file and sidecar; ValueError: bad name; SpoolBusy: held; FileNotFoundError: no file."""
        p = self.path(name)
        if name in self._held:
            raise SpoolBusy(name)
        p.unlink()
        self._unlink(name + ".json")

    def list(self) -> list[SpoolFile]:
        """Every well-named .pcap, newest first; skips a file that vanishes mid-scan."""
        rows: list[SpoolFile] = []
        for p in self.dir.glob("*.pcap"):
            if _NAME.fullmatch(p.name):
                with contextlib.suppress(FileNotFoundError):
                    rows.append(self._describe(p.name, self._read_sidecar(p.name)))
        rows.sort(key=lambda r: (r["mtime"], r["name"]), reverse=True)
        return rows

    def writer(self, key: CaptureKey) -> Writer:
        """Create a partial under a free name with header and sidecar, and hold it; OSError if it cannot."""
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time()))
        base = f"rec-{key.capture_id}-{stamp}"
        for n in range(10_000):
            name = f"{base}{'' if n == 0 else f'-{n}'}{_PARTIAL}"
            if name in self._held or (self.dir / (base + ("" if n == 0 else f"-{n}") + ".pcap")).exists():
                continue                    # the final name is taken: close() would have to suffix anyway
            try:
                fd = os.open(self.dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                pcap.write_all(fd, pcap.HEADER)
                # the key is on disk from the start: a later failed suspend() leaves a partial resume() still
                # matches (its pkt_count is stale; resume() and _describe count a partial's records on disk)
                self._write_sidecar(name, key, 0, True)
            except OSError:
                os.close(fd)
                self._unlink(name)          # a file with no whole header would only be an unreadable partial
                self._unlink(name + ".json")
                raise
            self._held.add(name)
            return Writer(self, key, name, fd, 0)
        raise OSError("no free capture file name")

    def resume(self, key: CaptureKey) -> Writer | None:
        """Reopen the partial for `key` and drop a torn tail. A matched file that cannot be read or has a bad
        header is never touched: not resumed, named in `skipped`, and the caller opens a new one."""
        self.skipped = []
        for p in sorted(self.dir.glob(f"rec-{key.capture_id}-*{_PARTIAL}")):
            if p.name in self._held or not _NAME.fullmatch(p.name):
                continue
            side = self._read_sidecar(p.name)
            if side is None or (side.get("server"), side.get("start_ts"), side.get("capture_id")) != (
                    key.server, key.start_ts, key.capture_id):
                continue
            scanned = _scan(p)
            if scanned is None:
                self.skipped.append(p.name)
                continue
            count, end = scanned
            fd = os.open(p, os.O_WRONLY | os.O_APPEND)
            try:
                os.ftruncate(fd, end)       # drop a torn tail from a crash
            except OSError:
                os.close(fd)
                raise
            self._held.add(p.name)
            return Writer(self, key, p.name, fd, count)
        return None

    def _unlink(self, name: str) -> None:
        """Delete name under the spool dir; a missing file is not an error."""
        with contextlib.suppress(FileNotFoundError):
            (self.dir / name).unlink()

    def _link_final(self, partial: str) -> str:
        """Give the partial its final name with link(), which fails rather than replace."""
        base = partial[:-len(_PARTIAL)]
        for n in range(10_000):
            final = f"{base}{'' if n == 0 else f'-{n}'}.pcap"
            try:
                os.link(self.dir / partial, self.dir / final)
            except FileExistsError:
                continue
            return final
        raise OSError("no free capture file name")

    def _write_sidecar(self, name: str, key: CaptureKey, count: int, partial: bool) -> dict[str, object]:
        """Write name's sidecar via a temp file and os.replace; returns the dict written."""
        side: dict[str, object] = {"server": key.server, "start_ts": key.start_ts,
                                   "capture_id": key.capture_id, "pkt_count": count, "partial": partial}
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".sidecar-", suffix=".tmp")     # 0600
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(side, f)
            os.replace(tmp, self.dir / (name + ".json"))
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        return side

    def _read_sidecar(self, name: str) -> dict[str, object] | None:
        """name's sidecar, or None when it is missing, unreadable, not JSON or not an object."""
        try:
            side = json.loads((self.dir / (name + ".json")).read_text())
        except (OSError, ValueError):
            return None
        return side if isinstance(side, dict) else None

    def _describe(self, name: str, side: dict[str, object] | None) -> SpoolFile:
        """The SpoolFile row for name from its stat and sidecar; FileNotFoundError if the file is gone."""
        st = (self.dir / name).stat()
        partial = name.endswith(_PARTIAL)
        if side is not None and name in self._held:
            side = None                     # a sidecar of an open file is stale: count the records
        m = _ID.match(name)
        cid = side.get("capture_id") if side else None
        # a partial's count is always the whole records on disk: its sidecar was written at open with 0 and
        # a crash or a failed suspend() leaves that 0 behind. A complete file's sidecar count is trusted.
        count = side.get("pkt_count") if side and not partial else None
        server = side.get("server") if side else None
        start = side.get("start_ts") if side else None
        return SpoolFile(
            name=name, size=st.st_size, mtime=st.st_mtime,
            capture_id=cid if _is_int(cid) else (int(m.group(1)) if m else None),
            server=server if isinstance(server, str) else None,
            start_ts=float(start) if _is_int(start) or isinstance(start, float) else None,
            pkt_count=count if _is_int(count) else _count(self.dir / name),
            partial=partial)
