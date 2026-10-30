import errno
import json
import os
import stat
import struct
from pathlib import Path

import pytest
from trex_web_gui import trexapi
from trex_web_gui.store.spool import CaptureKey, Spool, SpoolBusy

trexapi.load()      # Scapy is importable only after the vendored tree is on the path

KEY = CaptureKey("lab-a:4501", 100.0, 7)
FRAMES = [(0.5, b"\x01" * 60, 60), (1.25, b"\x02" * 64, 100), (2.000001, b"\x03" * 70, 70)]


def _spool(tmp_path: Path) -> Spool:
    """A Spool rooted at tmp_path/captures; Spool.__init__ creates the directory with mode 0700."""
    return Spool(tmp_path / "captures")


def test_writer_produces_readable_pcap(tmp_path: Path) -> None:
    from scapy.utils import rdpcap
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    for ts, raw, wl in FRAMES:
        w.write(ts, raw, wl)
    assert w.count == 3
    info = w.close()
    assert info["name"].endswith(".pcap") and not info["name"].endswith(".partial.pcap")
    assert info["name"].startswith("rec-7-") and info["partial"] is False and info["pkt_count"] == 3
    pkts = rdpcap(str(sp.path(info["name"])))
    assert len(pkts) == 3
    for p, (ts, raw, wl) in zip(pkts, FRAMES, strict=True):
        assert float(p.time) == pytest.approx(ts, abs=1e-6)
        assert bytes(p) == raw and p.wirelen == wl


def test_suspend_keeps_partial_and_resume_appends(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.write(*FRAMES[1])
    s = w.suspend()
    assert s["partial"] is True and s["name"].endswith(".partial.pcap") and s["pkt_count"] == 2
    w2 = sp.resume(KEY)
    assert w2 is not None and w2.count == 2
    w2.write(*FRAMES[2])
    done = w2.close()
    assert done["pkt_count"] == 3 and not done["partial"]
    names = [f["name"] for f in sp.list()]
    assert names == [done["name"]]
    assert not [p for p in sp.dir.iterdir() if "partial" in p.name]


def test_resume_ignores_a_partial_from_another_capture_with_the_same_id(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    old = w.suspend()
    assert sp.resume(CaptureKey("lab-a:4501", 200.0, 7)) is None
    assert sp.resume(CaptureKey("lab-b:4501", 100.0, 7)) is None
    listed = sp.list()
    assert [(f["name"], f["partial"]) for f in listed] == [(old["name"], True)]
    w2 = sp.writer(CaptureKey("lab-a:4501", 200.0, 7))
    w2.write(*FRAMES[1])
    w2.close()
    assert len(sp.list()) == 2


def test_resume_drops_a_torn_trailing_record(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    s = w.suspend()
    with open(sp.path(s["name"]), "ab") as f:
        f.write(struct.pack("<IIII", 1, 2, 50, 50) + b"x" * 10)      # a crash mid-record
    w2 = sp.resume(KEY)
    assert w2 is not None and w2.count == 1
    w2.write(*FRAMES[1])
    assert w2.close()["pkt_count"] == 2


def test_abort_leaves_nothing(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.abort()
    assert list(sp.dir.iterdir()) == [] and sp.list() == []


@pytest.mark.parametrize("name", ["../x.pcap", "a b.pcap", "x.txt", "x.pcap\n", "", "/etc/x.pcap",
                                  "a/b.pcap"])
def test_bad_name_rejected(tmp_path: Path, name: str) -> None:
    sp = _spool(tmp_path)
    with pytest.raises(ValueError):
        sp.path(name)
    with pytest.raises(ValueError):
        sp.delete(name)
    assert sp.path("ok-1.pcap") == sp.dir / "ok-1.pcap"


def test_list_reads_sidecar_and_counts_without_it(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.write(*FRAMES[1])
    done = w.close()
    (row,) = sp.list()
    assert (row["capture_id"], row["server"], row["start_ts"], row["pkt_count"]) == (
        7, "lab-a:4501", 100.0, 2)
    assert row["size"] == sp.path(done["name"]).stat().st_size
    (sp.dir / (done["name"] + ".json")).unlink()
    (row,) = sp.list()
    assert (row["capture_id"], row["server"], row["start_ts"], row["pkt_count"]) == (7, None, None, 2)
    # a corrupt sidecar behaves as a missing one
    (sp.dir / (done["name"] + ".json")).write_text("{not json")
    assert sp.list()[0]["server"] is None


def test_list_is_newest_first(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    a = sp.writer(CaptureKey("s:1", 1.0, 1)).close()
    b = sp.writer(CaptureKey("s:1", 2.0, 2)).close()
    os.utime(sp.path(a["name"]), (1000, 1000))
    os.utime(sp.path(b["name"]), (2000, 2000))
    assert [f["name"] for f in sp.list()] == [b["name"], a["name"]]


def test_dir_and_files_are_private(tmp_path: Path) -> None:
    d = tmp_path / "captures"
    d.mkdir(mode=0o755)
    sp = Spool(d)                       # a pre-existing directory is tightened too
    assert stat.S_IMODE(sp.dir.stat().st_mode) == 0o700
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    partial = next(p for p in sp.dir.iterdir())
    assert stat.S_IMODE(partial.stat().st_mode) == 0o600
    done = w.close()
    for p in sp.dir.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
    assert sp.path(done["name"]).exists()


def test_same_name_gets_a_numeric_suffix_not_an_overwrite(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trex_web_gui.store.spool.time.time", lambda: 1_700_000_000.0)
    sp = _spool(tmp_path)
    w1 = sp.writer(KEY)
    w1.write(*FRAMES[0])
    w2 = sp.writer(KEY)                 # same id, same second, while the first is open
    w2.write(*FRAMES[1])
    a, b = w1.close(), w2.close()
    assert a["name"] != b["name"]
    assert a["pkt_count"] == 1 and b["pkt_count"] == 1
    w3 = sp.writer(KEY)                 # the final name is taken too: close must not replace it
    w3.write(*FRAMES[2])
    c = w3.close()
    assert len({a["name"], b["name"], c["name"]}) == 3
    assert next(f for f in sp.list() if f["name"] == a["name"])["pkt_count"] == 1


def test_a_held_name_cannot_be_deleted(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    (held,) = sp.held()
    assert sp.is_held(held) and held.endswith(".partial.pcap")
    with pytest.raises(SpoolBusy):
        sp.delete(held)
    s = w.suspend()
    assert not sp.is_held(s["name"]) and sp.held() == frozenset()
    w2 = sp.resume(KEY)
    assert w2 is not None and sp.is_held(s["name"])
    done = w2.close()
    assert sp.held() == frozenset()
    sp.delete(done["name"])
    assert list(sp.dir.iterdir()) == []


def test_list_reports_an_unreadable_file_count_as_unknown(tmp_path: Path) -> None:
    # _scan's None ("not a pcap") is not "no records": the row says unknown, not 0
    sp = _spool(tmp_path)
    (sp.dir / "junk.pcap").write_bytes(b"not a pcap header")
    (row,) = sp.list()
    assert row["pkt_count"] is None


def test_list_ignores_booleans_in_a_sidecar(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    done = w.close()
    side = sp.dir / (done["name"] + ".json")
    side.write_text(json.dumps({**json.loads(side.read_text()), "capture_id": True, "pkt_count": True}))
    (row,) = sp.list()
    # bool is an int subclass: fall back, don't use it
    assert row["capture_id"] == 7 and row["pkt_count"] == 1


def test_sidecar_is_replaced_atomically(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    s = w.suspend()
    side = sp.dir / (s["name"] + ".json")
    assert json.loads(side.read_text()) == {"server": "lab-a:4501", "start_ts": 100.0, "capture_id": 7,
                                            "pkt_count": 0, "partial": True}
    assert [p.name for p in sp.dir.iterdir() if p.name.endswith(".tmp")] == []


class _FakeOs:
    """os.write that accepts at most `step` bytes per call, keeps them in `got`, and raises ENOSPC once
    more than `fail_after` calls have been made (never, when None)."""

    def __init__(self, step: int, fail_after: int | None = None) -> None:
        """Starts with no calls and nothing written; step and fail_after are described on the class."""
        self.step, self.fail_after, self.calls, self.got = step, fail_after, 0, b""

    def __call__(self, fd: int, data: bytes) -> int:
        """Counts the call; raises ENOSPC past fail_after, else keeps and returns the first step bytes."""
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise OSError(28, "No space left on device")
        n = min(self.step, len(data))
        self.got += bytes(data[:n])
        return n


def test_a_short_write_is_completed_before_the_count_rises(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    w = _spool(tmp_path).writer(KEY)
    fake = _FakeOs(step=7)
    monkeypatch.setattr(os, "write", fake)
    w.write(*FRAMES[0])
    assert w.count == 1 and len(fake.got) == 16 + 60 and fake.calls > 1
    assert fake.got.endswith(b"\x01" * 60)


def test_a_failed_write_does_not_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = _spool(tmp_path).writer(KEY)
    w.write(*FRAMES[0])
    monkeypatch.setattr(os, "write", _FakeOs(step=10, fail_after=2))
    with pytest.raises(OSError) as e:
        w.write(*FRAMES[1])
    assert e.value.errno == 28 and w.count == 1


def _suspended(sp: Spool) -> Path:
    """Writes the first two FRAMES through a new writer for KEY, suspends it; returns the suspended path."""
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.write(*FRAMES[1])
    return sp.path(w.suspend()["name"])


def test_resume_leaves_an_unreadable_partial_untouched(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)
    p = _suspended(sp)
    before = p.read_bytes()
    real = Path.read_bytes

    def broken(self: Path) -> bytes:
        if self == p:
            raise OSError(5, "Input/output error")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", broken)
    assert sp.resume(KEY) is None
    assert sp.skipped == [p.name] and not sp.held()
    monkeypatch.undo()
    assert p.read_bytes() == before


def test_resume_leaves_a_bad_header_untouched(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    p = _suspended(sp)
    raw = bytearray(p.read_bytes())
    raw[0:4] = b"\x00\x00\x00\x00"
    p.write_bytes(bytes(raw))
    assert sp.resume(KEY) is None
    assert sp.skipped == [p.name]
    assert p.read_bytes() == bytes(raw)


def test_resume_truncates_a_torn_tail_to_the_last_whole_record(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    p = _suspended(sp)
    whole = p.read_bytes()
    with open(p, "ab") as f:
        f.write(struct.pack("<IIII", 1, 2, 50, 50) + b"x" * 10)
    w = sp.resume(KEY)
    assert w is not None and w.count == 2 and sp.skipped == []
    assert p.read_bytes() == whole


def test_a_write_that_returns_zero_raises_instead_of_looping(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    w = _spool(tmp_path).writer(KEY)
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    with pytest.raises(OSError) as e:
        w.write(*FRAMES[0])
    assert e.value.errno == errno.EIO and w.count == 0


def test_writer_writes_its_sidecar_at_once_so_a_failed_suspend_still_resumes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.write(*FRAMES[1])

    def boom(*a: object, **k: object) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Spool, "_write_sidecar", boom)
    with pytest.raises(OSError):
        w.suspend()
    monkeypatch.undo()
    assert not sp.held()                                    # the hold went with the fd, before the sidecar
    w2 = sp.resume(KEY)
    # counted from the file, not from the stale sidecar
    assert w2 is not None and w2.count == 2


def test_a_partial_left_by_a_crash_lists_the_records_on_disk(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    for ts, raw, wl in FRAMES:
        w.write(ts, raw, wl)
    os.close(w._fd)                                         # the process dies: no suspend(), sidecar says 0
    (row,) = Spool(sp.dir).list()
    assert row["partial"] is True and row["pkt_count"] == 3


def test_a_partial_whose_suspend_sidecar_failed_lists_the_records_on_disk(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.write(*FRAMES[1])

    def boom(*a: object, **k: object) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Spool, "_write_sidecar", boom)
    with pytest.raises(OSError):
        w.suspend()
    monkeypatch.undo()
    (row,) = sp.list()
    assert row["partial"] is True and row["pkt_count"] == 2


def test_a_complete_file_lists_its_sidecar_count(tmp_path: Path) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    done = w.close()
    side = json.loads((sp.dir / (done["name"] + ".json")).read_text())
    side["pkt_count"] = 99                                  # differs from the one record on purpose
    (sp.dir / (done["name"] + ".json")).write_text(json.dumps(side))
    (row,) = sp.list()
    assert row["partial"] is False and row["pkt_count"] == 99


def test_a_header_write_failure_unlinks_the_new_file_and_raises(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)

    def full(fd: int, data: bytes) -> int:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "write", full)
    with pytest.raises(OSError):
        sp.writer(KEY)
    assert list(sp.dir.iterdir()) == [] and not sp.held()


def test_a_sidecar_failure_in_writer_unlinks_the_new_file_and_raises(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)

    def boom(*a: object, **k: object) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(Spool, "_write_sidecar", boom)
    with pytest.raises(OSError):
        sp.writer(KEY)
    assert list(sp.dir.iterdir()) == [] and not sp.held()


def test_release_drops_the_hold_even_when_close_raises(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    real = os.close

    def bad_close(fd: int) -> None:
        real(fd)
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(os, "close", bad_close)
    with pytest.raises(OSError):
        w.abort()
    monkeypatch.undo()
    assert not sp.held()


def test_resume_closes_the_fd_when_ftruncate_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sp = _spool(tmp_path)
    w = sp.writer(KEY)
    w.write(*FRAMES[0])
    w.suspend()
    closed: list[int] = []
    real = os.close

    def spy(fd: int) -> None:
        closed.append(fd)
        real(fd)

    def boom(fd: int, n: int) -> None:
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(os, "ftruncate", boom)
    monkeypatch.setattr(os, "close", spy)
    with pytest.raises(OSError):
        sp.resume(KEY)
    assert len(closed) == 1 and not sp.held()
