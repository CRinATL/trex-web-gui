"""The profile library on disk (P4a Task 3)."""
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES
from trex_web_gui.store.settings import Settings, SettingsStore
from trex_web_gui.studio import library as lib
from trex_web_gui.studio.library import (
    LibraryError,
    LibraryExists,
    LibraryNotFound,
    ProfileLibrary,
    SidecarUnreadable,
    StaleRevision,
    TooManyProfiles,
    library_dir,
)
from trex_web_gui.studio.sidecar import new_sidecar
from trex_web_gui.studio.streams import StreamsDiff


def stream(name: Any = None) -> dict[str, Any]:
    """A valid stream (the recorded one), named when `name` is given."""
    s = json.loads((FIXTURES / "profiles/two_streams.json").read_text())[0]
    if name is not None:
        s["name"] = name
    return s


def src_of(kind: str, path: Path) -> dict[str, Any]:
    """A Source of `kind` at `path`."""
    return {"kind": kind, "path": str(path), "args": [], "tunables": None, "ports_differ": False,
            "imported": "2026-10-23T10:00:00.000Z"}


def mklib(tmp_path: Path) -> ProfileLibrary:
    """A library in tmp_path/profiles (not created yet)."""
    return ProfileLibrary(lambda: tmp_path / "profiles")


def files(tmp_path: Path) -> dict[str, bytes]:
    """Every file of the library dir, name to bytes."""
    d = tmp_path / "profiles"
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())}


def test_create_get_list_delete(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    assert not (tmp_path / "profiles").exists() and pl.list() == ([], [])   # listing creates nothing
    e = pl.create("Lab A", [stream("a"), stream("b")])
    assert e["slug"] == "lab-a" and e["name"] == "Lab A" and e["stream_count"] == 2
    assert e["path"] == str(tmp_path / "profiles/lab-a.json") and e["source"] is None
    assert e["source_missing"] is False and e["load_error"] is None
    p = pl.get("lab-a")
    assert [s["name"] for s in p.streams] == ["a", "b"] and p.sidecar["name"] == "Lab A"
    assert p.sidecar["streams"] == [{}, {}] and len(p.rev) == 16
    assert json.loads((tmp_path / "profiles/lab-a.json").read_text()) == p.streams
    assert pl.list() == ([e], [])
    pl.delete("lab-a")
    assert files(tmp_path) == {}
    with pytest.raises(LibraryNotFound):
        pl.get("lab-a")
    with pytest.raises(LibraryNotFound):
        pl.delete("lab-a")


def test_names_collide_by_slug(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("Lab A")
    with pytest.raises(LibraryExists):
        pl.create("lab-a")


def test_name_limits(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    for bad in ("", "   ", "x" * 81, "!!!", "tab\there"):
        with pytest.raises(LibraryError) as e:
            pl.create(bad)
        assert e.value.field == "name"
    assert pl.create("x" * 80)["slug"] == "x" * 80
    assert not [p for p in (tmp_path / "profiles").iterdir() if p.name.startswith("!")]


def test_path_of_refuses_a_bad_slug_before_any_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pl = mklib(tmp_path)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("io")
    monkeypatch.setattr(lib, "_read_json", boom)
    for bad in ("../x", "A", "", "a/b", "a.gui"):
        with pytest.raises(LibraryNotFound):
            pl.path_of(bad)
        with pytest.raises(LibraryNotFound):
            pl.get(bad)


def test_duplicate_checks_the_new_name_and_keeps_source(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    src = src_of("py", tmp_path / "p.py")
    pl.create("A", [stream("a")], source=src)
    pl.create("B")
    with pytest.raises(LibraryExists):
        pl.duplicate("a", "b")                           # the legacy crashed here (A5.1)
    with pytest.raises(LibraryError):
        pl.duplicate("a", "")
    with pytest.raises(LibraryNotFound):
        pl.duplicate("zzz", "C")
    e = pl.duplicate("a", "A copy")
    assert e["slug"] == "a-copy" and e["source"] == pl.get("a").sidecar["source"] and e["stream_count"] == 1
    assert pl.get("a-copy").streams == pl.get("a").streams


def test_rename_moves_both_files_and_keeps_created(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    old = pl.create("Old", [stream("a")])
    rev = pl.get("old").rev
    time.sleep(0.01)
    e = pl.rename("old", "New Name", rev)
    assert e["slug"] == "new-name" and e["created"] == old["created"] and e["updated"] > old["updated"]
    assert sorted(files(tmp_path)) == ["new-name.gui.json", "new-name.json"]
    assert pl.get("new-name").sidecar["name"] == "New Name"
    with pytest.raises(LibraryNotFound):
        pl.get("old")


def test_rename_to_the_same_slug_is_not_exists(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("Lab A", [stream("a")])
    before = (tmp_path / "profiles/lab-a.json").read_bytes()
    e = pl.rename("lab-a", "LAB  a", pl.get("lab-a").rev)
    assert e["slug"] == "lab-a" and e["name"] == "LAB  a"
    assert (tmp_path / "profiles/lab-a.json").read_bytes() == before
    pl.create("Other")
    with pytest.raises(LibraryExists):
        pl.rename("lab-a", "other", pl.get("lab-a").rev)
    with pytest.raises(LibraryError):
        pl.rename("lab-a", "", pl.get("lab-a").rev)


def test_a_crash_between_rename_writes_leaves_two_entries_never_a_lost_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pl = mklib(tmp_path)
    src = src_of("yaml", tmp_path / "p.yaml")
    pl.create("Old", [stream("a")], source=src)
    rev = pl.get("old").rev
    real = os.replace
    calls: list[str] = []

    def crash(a: Any, b: Any) -> None:
        calls.append(os.path.basename(b))
        if len(calls) == 2:                              # the new sidecar
            raise OSError("disk")
        real(a, b)
    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError):
        pl.rename("old", "New", rev)
    monkeypatch.undo()
    assert calls == ["new.json", "new.gui.json"]
    entries, errors = pl.list()
    assert {e["slug"] for e in entries} == {"old", "new"} and errors == []
    assert pl.get("old").sidecar["source"] == src


def test_save_with_an_old_rev_is_stale_and_writes_nothing(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")])
    p = pl.get("a")
    new_rev = pl.save("a", [stream("a"), stream("b")], p.sidecar, p.rev)
    assert new_rev != p.rev and pl.get("a").rev == new_rev and pl.get("a").sidecar["stream_count"] == 2
    before = files(tmp_path)
    with pytest.raises(StaleRevision) as e:
        pl.save("a", [stream("z")], p.sidecar, p.rev)
    assert e.value.current_rev == new_rev
    with pytest.raises(StaleRevision):
        pl.rename("a", "B", p.rev)
    with pytest.raises(StaleRevision):
        pl.apply("a", StreamsDiff(rev=p.rev, removed=[0]))
    with pytest.raises(StaleRevision):
        pl.append("a", [stream("q")], p.rev)
    assert files(tmp_path) == before


def test_sidecar_only_change_changes_rev(tmp_path: Path) -> None:
    jb, sb = b"[]", b'{"a": 1}'
    assert lib.rev_of(jb, sb) != lib.rev_of(jb, b'{"a": 2}') and lib.rev_of(jb, b"") != lib.rev_of(jb, sb)
    assert len(lib.rev_of(jb, sb)) == 16
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")])
    p = pl.get("a")
    sc = json.loads(json.dumps(p.sidecar))
    sc["pcap_wizard"] = {"k": 1}
    rev2 = pl.save("a", p.streams, sc, p.rev)
    assert rev2 != p.rev


def test_apply_writes_json_before_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a"), stream("b")])
    p = pl.get("a")
    real = os.replace
    order: list[str] = []

    def rec(a: Any, b: Any) -> None:
        order.append(os.path.basename(b))
        real(a, b)
    monkeypatch.setattr(os, "replace", rec)
    res, rev = pl.apply("a", StreamsDiff(rev=p.rev, changed={"0": {"name": "z"}}, added=[stream("c")]))
    assert order == ["a.json", "a.gui.json"]
    assert [s["name"] for s in res.streams] == ["z", "b", "c"] and rev == pl.get("a").rev
    assert pl.get("a").sidecar["stream_count"] == 3 and len(pl.get("a").sidecar["streams"]) == 3
    res2, _ = pl.append("a", [stream("c")], rev)
    assert res2.renamed == {"c": "c_2"} and pl.get("a").sidecar["stream_count"] == 4


def test_json_without_sidecar_lists_and_loads(tmp_path: Path) -> None:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles/hand.json").write_text(json.dumps([stream("a"), stream("b")]))
    pl = mklib(tmp_path)
    entries, errors = pl.list()
    assert errors == [] and len(entries) == 1
    e = entries[0]
    assert (e["slug"], e["name"], e["stream_count"]) == ("hand", "hand", 2)
    assert e["source"] is None and e["load_error"] is None
    assert len(pl.get("hand").streams) == 2 and pl.get("hand").sidecar["name"] == "hand"
    assert files(tmp_path).keys() == {"hand.json"}       # a read creates nothing
    p = pl.get("hand")
    pl.apply("hand", StreamsDiff(rev=p.rev, removed=[1]))   # the first edit writes a sidecar
    assert pl.get("hand").sidecar["stream_count"] == 1 and "hand.gui.json" in files(tmp_path)


def test_corrupt_sidecar_sets_load_error_and_keeps_the_json(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")])
    (tmp_path / "profiles/a.gui.json").write_text("{nope")
    e = pl.list()[0][0]
    assert e["load_error"] and e["stream_count"] == 1 and e["slug"] == "a"
    assert len(pl.get("a").streams) == 1                 # still assignable
    assert (tmp_path / "profiles/a.gui.json").read_text() == "{nope"


def test_edit_repairs_a_short_sidecar_list_and_keeps_source(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    src = src_of("json", tmp_path / "p.json")
    pl.create("A", [stream("a"), stream("b"), stream("c")], source=src)
    side = tmp_path / "profiles/a.gui.json"
    sc = json.loads(side.read_text())
    sc["streams"] = [{"x": 1}]                           # short: 3 streams
    sc["pcap_wizard"] = {"keep": True}
    side.write_text(json.dumps(sc))
    p = pl.get("a")
    pl.apply("a", StreamsDiff(rev=p.rev, changed={"1": {"name": "bb"}}))
    got = pl.get("a").sidecar
    assert got["streams"] == [{"x": 1}, {}, {}] and got["source"] == src
    assert got["pcap_wizard"] == {"keep": True}
    assert got["stream_count"] == 3 and got["name"] == "A" and got["created"] == sc["created"]


def test_edit_refuses_over_an_unparsable_sidecar(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")])
    (tmp_path / "profiles/a.gui.json").write_text('{"version": 9}')
    before = files(tmp_path)
    p = pl.get("a")
    with pytest.raises(SidecarUnreadable, match="a.gui.json"):
        pl.apply("a", StreamsDiff(rev=p.rev, removed=[0]))
    with pytest.raises(SidecarUnreadable):
        pl.append("a", [stream("q")], p.rev)
    with pytest.raises(SidecarUnreadable):
        pl.rename("a", "B", p.rev)
    with pytest.raises(SidecarUnreadable):
        pl.save("a", [], new_sidecar("A", None, 0), p.rev)
    assert files(tmp_path) == before


def test_bad_files_go_to_load_errors_untouched(tmp_path: Path) -> None:
    d = tmp_path / "profiles"
    d.mkdir()
    (d / "My Profile.json").write_text("[]")
    (d / "bad.json").write_text("{")
    (d / "notlist.json").write_text("{}")
    (d / "orphan.gui.json").write_text("{}")
    (d / ".profile.x.tmp").write_text("")
    mt = {p.name: p.stat().st_mtime_ns for p in d.iterdir()}
    entries, errors = mklib(tmp_path).list()
    assert entries == []
    got = {e["file"]: e["error"] for e in errors}
    assert got["My Profile.json"] == "file name is not a library slug"
    assert set(got) == {"My Profile.json", "bad.json", "notlist.json"} and got["bad.json"]
    assert {p.name: p.stat().st_mtime_ns for p in d.iterdir()} == mt


def test_list_reads_only_sidecars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")])
    pl.create("B", [stream("a"), stream("b")])
    real = lib._read_json

    def only_sidecars(path: Path) -> bytes:
        if not path.name.endswith(".gui.json"):
            raise AssertionError(f"read a profile JSON: {path}")
        return real(path)
    monkeypatch.setattr(lib, "_read_json", only_sidecars)
    entries, errors = pl.list()
    assert [(e["slug"], e["stream_count"]) for e in entries] == [("a", 1), ("b", 2)] and errors == []


def test_source_missing_is_reported(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    py = tmp_path / "p.py"
    py.write_text("x")
    src = src_of("py", py)
    pl.create("A", source=src)
    pl.create("B", source=src_of("yaml", py))
    assert [e["source_missing"] for e in pl.list()[0]] == [False, False]
    py.unlink()
    assert [e["source_missing"] for e in pl.list()[0]] == [True, False]   # only a .py is assigned from it


def test_too_many_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "LIBRARY_MAX", 2)
    pl = mklib(tmp_path)
    pl.create("A")
    pl.create("B")
    with pytest.raises(TooManyProfiles):
        pl.create("C")
    with pytest.raises(TooManyProfiles):
        pl.duplicate("a", "C")
    pl.rename("a", "Z", pl.get("a").rev)                 # a rename does not grow the count


def test_library_dir_preference_is_read_per_call(tmp_path: Path) -> None:
    here = {"d": tmp_path / "one"}
    pl = ProfileLibrary(lambda: here["d"])
    pl.create("A")
    here["d"] = tmp_path / "two"
    assert pl.list() == ([], [])
    pl.create("B")
    assert sorted(p.name for p in (tmp_path / "one").iterdir()) == ["a.gui.json", "a.json"]
    assert sorted(p.name for p in (tmp_path / "two").iterdir()) == ["b.gui.json", "b.json"]


def test_a_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pl = mklib(tmp_path)
    pl.create("A")

    def boom(a: Any, b: Any) -> None:
        raise OSError("disk")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        pl.create("B")
    monkeypatch.undo()
    assert sorted(files(tmp_path)) == ["a.gui.json", "a.json"]


def test_library_dir_follows_the_stored_preference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    store = SettingsStore(data)
    pl = ProfileLibrary(lambda: library_dir(store.load().preferences, data))
    pl.create("A")                                       # no setting: data_dir/profiles
    assert (data / "profiles/a.json").is_file()
    s = Settings()
    s.preferences.library_dir = str(tmp_path / "chosen")
    store.save(s)
    assert not (tmp_path / "chosen").exists() and pl.list() == ([], [])   # nothing created by a read
    pl.create("B")
    assert (tmp_path / "chosen/b.json").is_file()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    s.preferences.library_dir = "~/x"
    store.save(s)
    pl.create("C")
    assert (tmp_path / "home/x/c.json").is_file()
    s.preferences.library_dir = ""
    store.save(s)
    assert [e["slug"] for e in pl.list()[0]] == ["a"]


def test_an_empty_sidecar_is_unreadable_for_edits_and_a_load_error_in_the_listing(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")])
    (tmp_path / "profiles/a.gui.json").write_bytes(b"")
    before = files(tmp_path)
    assert pl.list()[0][0]["load_error"]
    p = pl.get("a")
    with pytest.raises(SidecarUnreadable):
        pl.apply("a", StreamsDiff(rev=p.rev, removed=[0]))
    with pytest.raises(SidecarUnreadable):
        pl.rename("a", "B", p.rev)
    assert files(tmp_path) == before


def test_duplicate_over_an_unparsable_sidecar_is_refused(tmp_path: Path) -> None:
    pl = mklib(tmp_path)
    pl.create("A", [stream("a")], source=src_of("py", tmp_path / "p.py"))
    (tmp_path / "profiles/a.gui.json").write_text("{nope")
    before = files(tmp_path)
    with pytest.raises(SidecarUnreadable):
        pl.duplicate("a", "B")
    assert files(tmp_path) == before
