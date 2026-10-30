import json
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.store import test_setups as ts
from trex_web_gui.store.test_setups import (
    SetupError,
    SetupExists,
    SetupNotFound,
    TooManySetups,
    parse_setup,
    slugify,
)


def port(**kw: Any) -> dict[str, Any]:
    """A valid port entry with `kw` overrides."""
    return {"key": "aa:bb:cc:dd:ee:ff", "port": 0, "profile": {"path": "/p/udp.py", "kind": "py"},
            "tunables": {"values": {"pps": 10, "name": "x", "on": True}, "extra": "--foo"},
            "mult": "10kpps", "duration": -1, "force": False, **kw}


def setup(name: str = "Lab A", **kw: Any) -> dict[str, Any]:
    """A valid raw setup with `kw` overrides."""
    return {"name": name, "server": "lab1:4501", "ports": [port()], "dashboard": None, **kw}


def test_slugify() -> None:
    assert slugify("Lab A: 10 kpps") == "lab-a-10-kpps"
    assert slugify("  x--y  ") == "x-y"


def test_round_trip_one_port_with_layer(tmp_path: Path) -> None:
    layer = {"mode": "L3", "src_ipv4": "10.0.0.1", "dst_ipv4": "10.0.0.2", "vlan": [5], "ipv6": None,
             "dst_mac": None}
    dash = {"tab": "latency", "streams_pgids": [1], "latency_pgids": [2, 3], "window_s": 120, "layout": 2}
    store = ts.TestSetupStore(tmp_path)
    saved = store.save(parse_setup(setup(ports=[port(layer=layer)], dashboard=dash)))
    got = store.get("lab-a")
    assert got == saved and got["slug"] == "lab-a" and got["server"] == "lab1:4501"
    assert got["ports"][0]["layer"] == layer and got["dashboard"] == dash
    assert got["created"].endswith("Z") and got["updated"].endswith("Z")
    assert store.list() == [saved] and store.load_errors == []


def test_acquire_force_defaults_false_and_is_independent_of_force() -> None:
    p = parse_setup(setup(ports=[port(force=True)]))["ports"][0]
    assert p["force"] is True and p["acquire_force"] is False and p["stop_first"] is False
    p = parse_setup(setup(ports=[port(acquire_force=True)]))["ports"][0]
    assert p["force"] is False and p["acquire_force"] is True


@pytest.mark.parametrize("mult", ["fast", "10kpps\n", "", 5])
def test_invalid_mult_names_the_field(mult: Any) -> None:
    with pytest.raises(SetupError) as e:
        parse_setup(setup(ports=[port(), port(port=1, mult=mult)]))
    assert e.value.field == "ports[1].mult"


@pytest.mark.parametrize("layer", [
    "L2", {"port": 0, "mode": "L2", "dst_mac": "aa:bb:cc:dd:ee:ff"}, {"mode": "L9"}, {"mode": "L2"},
    ])
def test_invalid_layer_names_the_field(layer: Any) -> None:
    with pytest.raises(SetupError) as e:
        parse_setup(setup(ports=[port(layer=layer)]))
    assert e.value.field == "ports[0].layer"


def test_layer_is_stored_normalised() -> None:
    raw = {"mode": "L2", "dst_mac": "AA:BB:CC:DD:EE:FF", "extra": "dropped"}
    got = parse_setup(setup(ports=[port(layer=raw)]))["ports"][0]["layer"]
    assert got is not None and got["dst_mac"] == "aa:bb:cc:dd:ee:ff" and "extra" not in got
    assert "port" not in got


def test_slug_is_derived_not_taken(tmp_path: Path) -> None:
    s = parse_setup(setup("Safe", slug="../../x"))
    assert s["slug"] == "safe"
    store = ts.TestSetupStore(tmp_path)
    store.save(s)
    assert [p.name for p in (tmp_path / "test_setups").iterdir()] == ["safe.json"]
    assert not (tmp_path.parent / "x.json").exists()


@pytest.mark.parametrize("name", ["Тест", "!!!"])
def test_empty_slug_name_is_refused(name: str) -> None:
    with pytest.raises(SetupError) as e:
        parse_setup(setup(name))
    assert (e.value.field, e.value.reason) == ("name", "needs at least one letter or digit a-z 0-9")


def test_path_and_extra_refuse_nul_and_overlength() -> None:
    cases = [("profile", {"path": "a\x00b", "kind": "py"}, "ports[0].profile.path"),
             ("profile", {"path": "a" * 4097, "kind": "py"}, "ports[0].profile.path"),
             ("profile", {"path": "", "kind": "py"}, "ports[0].profile.path"),
             ("profile", {"path": "/p", "kind": "exe"}, "ports[0].profile.kind"),
             ("tunables", {"values": {}, "extra": "a\x00"}, "ports[0].tunables.extra"),
             ("tunables", {"values": {}, "extra": "a" * 4097}, "ports[0].tunables.extra"),
             ("tunables", {"values": {"k": [1]}, "extra": ""}, "ports[0].tunables.values.k"),
             ("tunables", {"values": {"k" * 65: 1}, "extra": ""}, "ports[0].tunables.values"),
             ("tunables", {"values": {"k": "v" * 1025}, "extra": ""}, "ports[0].tunables.values.k"),
             ("tunables", {"values": {str(i): i for i in range(101)}, "extra": ""},
              "ports[0].tunables.values")]
    for k, v, field in cases:
        with pytest.raises(SetupError) as e:
            parse_setup(setup(ports=[port(**{k: v})]))
        assert e.value.field == field, (k, v)
    parse_setup(setup(ports=[port(profile={"path": "a" * 4096, "kind": "yaml"})]))


def test_port_list_and_dashboard_limits() -> None:
    bad = [({"ports": []}, "ports"), ({"ports": [port(port=i) for i in range(65)]}, "ports"),
           ({"ports": [port(), port()]}, "ports"), ({"ports": [port(port=-1)]}, "ports[0].port"),
           ({"ports": [port(duration=0)]}, "ports[0].duration"),
           ({"ports": [port(duration=604801)]}, "ports[0].duration"),
           ({"dashboard": {"tab": "x", "streams_pgids": [], "latency_pgids": [],
                           "window_s": 60, "layout": 1}}, "dashboard.tab"),
           ({"dashboard": {"tab": "streams", "streams_pgids": list(range(9)), "latency_pgids": [],
                           "window_s": 60, "layout": 1}}, "dashboard.streams_pgids"),
           ({"dashboard": {"tab": "streams", "streams_pgids": [], "latency_pgids": [-1],
                           "window_s": 60, "layout": 1}}, "dashboard.latency_pgids"),
           ({"dashboard": {"tab": "streams", "streams_pgids": [], "latency_pgids": [],
                           "window_s": 61, "layout": 1}}, "dashboard.window_s"),
           ({"dashboard": {"tab": "streams", "streams_pgids": [], "latency_pgids": [],
                           "window_s": 60, "layout": 3}}, "dashboard.layout")]
    for kw, field in bad:
        with pytest.raises(SetupError) as e:
            parse_setup(setup(**kw))
        assert e.value.field == field, kw
    parse_setup(setup(ports=[port(port=i) for i in range(64)]))


def test_name_limits_and_control_characters() -> None:
    parse_setup(setup("a" * 60))
    for name in ("a" * 61, "", "   ", "bad\nname", "bad\x00name", "x‮y", 5):
        with pytest.raises(SetupError) as e:
            parse_setup(setup(name))
        assert e.value.field == "name", repr(name)


def test_too_many_setups_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts, "SETUPS_MAX", 2)
    store = ts.TestSetupStore(tmp_path)
    store.save(parse_setup(setup("a")))
    store.save(parse_setup(setup("b")))
    store.save(parse_setup(setup("b")))                 # an existing slug is still saved
    with pytest.raises(TooManySetups):
        store.save(parse_setup(setup("c")))
    with pytest.raises(TooManySetups):
        store.duplicate("a", "c")
    assert len(store.list()) == 2


def test_rename_collision_is_refused(tmp_path: Path) -> None:
    store = ts.TestSetupStore(tmp_path)
    a = store.save(parse_setup(setup("A")))
    store.save(parse_setup(setup("B")))
    with pytest.raises(SetupExists):
        store.rename("a", "b!")
    r = store.rename("a", "Alpha")
    assert r["slug"] == "alpha" and r["created"] == a["created"]
    assert [s["slug"] for s in store.list()] == ["alpha", "b"]
    assert store.rename("alpha", "ALPHA")["name"] == "ALPHA"       # same slug: a rename in place
    with pytest.raises(SetupNotFound):
        store.rename("gone", "x")


def test_duplicate_gets_new_slug_and_timestamps(tmp_path: Path) -> None:
    store = ts.TestSetupStore(tmp_path)
    a = store.save(parse_setup(setup("A")))
    d = store.duplicate("a", "A copy")
    assert d["slug"] == "a-copy" and d["ports"] == a["ports"]
    assert d["created"] == d["updated"] and d["created"] >= a["created"]
    assert store.get("a") == a
    with pytest.raises(SetupExists):
        store.duplicate("a", "a copy")


def test_save_keeps_created_and_moves_updated(tmp_path: Path) -> None:
    store = ts.TestSetupStore(tmp_path)
    first = store.save(parse_setup(setup("A")))
    second = store.save(parse_setup({**setup("A"), "created": "1999-01-01T00:00:00.000Z"}))
    assert second["created"] == first["created"] and second["updated"] >= first["updated"]


def test_corrupt_file_is_listed_with_load_error(tmp_path: Path) -> None:
    store = ts.TestSetupStore(tmp_path)
    store.save(parse_setup(setup("Good")))
    d = tmp_path / "test_setups"
    (d / "bad.json").write_text("{not json")
    (d / "wrong-name.json").write_text(json.dumps(setup("Other")))
    (d / "invalid.json").write_text(json.dumps(setup("Invalid", ports=[])))
    got = store.list()
    assert [s["slug"] for s in got] == ["good"]
    assert sorted(e["file"] for e in store.load_errors) == ["bad.json", "invalid.json", "wrong-name.json"]
    assert all(e["error"] for e in store.load_errors)
    assert (d / "bad.json").read_text() == "{not json"            # left alone


def test_save_is_atomic_and_no_temp_left(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ts.TestSetupStore(tmp_path)
    store.save(parse_setup(setup("A")))
    before = (tmp_path / "test_setups" / "a.json").read_text()

    def boom(*_: Any) -> None:
        raise OSError("disk")
    monkeypatch.setattr(ts.os, "replace", boom)
    with pytest.raises(OSError):
        store.save(parse_setup(setup("A", server="other")))
    assert sorted(p.name for p in (tmp_path / "test_setups").iterdir()) == ["a.json"]
    assert (tmp_path / "test_setups" / "a.json").read_text() == before


def test_rename_at_the_cap_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts, "SETUPS_MAX", 2)
    store = ts.TestSetupStore(tmp_path)
    store.save(parse_setup(setup("a")))
    store.save(parse_setup(setup("b")))
    assert store.rename("a", "c")["slug"] == "c"
    assert [s["slug"] for s in store.list()] == ["b", "c"]


def test_bad_slug_never_touches_the_filesystem(tmp_path: Path) -> None:
    store = ts.TestSetupStore(tmp_path / "data")                  # the directory does not exist
    for slug in ("../x", "A", "a--b", "", "a/b", "-a"):
        with pytest.raises(SetupNotFound):
            store.path_of(slug)
        for call in (store.get, store.delete):
            with pytest.raises(SetupNotFound):
                call(slug)
    assert not (tmp_path / "data").exists()


def test_bad_slug_does_not_reach_a_real_file(tmp_path: Path) -> None:
    store = ts.TestSetupStore(tmp_path)
    store.save(parse_setup(setup("A")))                           # a.json
    d = tmp_path / "test_setups"
    (d / "A.json").write_text(json.dumps(setup("A")))             # what the slug "A" would reach
    (tmp_path / "x.json").write_text(json.dumps(setup("x")))      # what "../x" would reach
    for slug in ("A", "../x"):
        for call in (store.get, store.delete):
            with pytest.raises(SetupNotFound):
                call(slug)
        for call2 in (store.rename, store.duplicate):
            with pytest.raises(SetupNotFound):
                call2(slug, "new")
    assert (d / "A.json").exists() and (tmp_path / "x.json").exists()
