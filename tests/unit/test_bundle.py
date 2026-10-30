import dataclasses
import os
import re
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.store import bundle as b
from trex_web_gui.store.bundle import BundleError, BundleWriteError, Stores
from trex_web_gui.store.port_labels import PortLabelStore
from trex_web_gui.store.settings import Preferences, SavedConnection, Settings, SettingsError, SettingsStore
from trex_web_gui.store.test_setups import TestSetupStore, parse_setup, slugify


def stores(d: Path) -> Stores:
    """Stores over the data dir `d`."""
    return Stores(SettingsStore(d), PortLabelStore(d), TestSetupStore(d))


def port(path: str = "/p/udp.py", n: int = 0) -> dict[str, Any]:
    """A valid raw setup port."""
    return {"key": "#0", "port": n, "profile": {"path": path, "kind": "py"},
            "tunables": {"values": {}, "extra": ""}, "mult": "10kpps", "duration": -1, "force": False}


def setup(name: str, path: str = "/p/udp.py") -> dict[str, Any]:
    """A valid raw setup."""
    return {"name": name, "server": None, "ports": [port(path)], "dashboard": None}


def make(sections: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """A version-1 bundle with `sections`."""
    return {"format": b.FORMAT, "version": 1, "exported_at": "x", "app_version": "t",
            "sections": sections, **kw}


def seed(s: Stores) -> None:
    """Local data: two connections, one label, two setups, non-default preferences."""
    s.settings.save(Settings(Preferences(wireshark_path="/usr/bin/ws"), [
        SavedConnection("lab", "10.0.0.1"), SavedConnection("only-local", "10.0.0.9")]))
    s.labels.set("10.0.0.1:4501", "#0", "up", "")
    s.labels.set("10.0.0.1:4501", "#1", "down", "")
    s.setups.save(parse_setup(setup("Lab A")))
    s.setups.save(parse_setup(setup("Local Only")))


def test_export_shape_and_version(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    j = b.export(s, "9.9")
    assert j["format"] == "trex-web-gui-settings" and j["version"] == 1 and j["app_version"] == "9.9"
    assert j["exported_at"].endswith("Z")
    assert set(j["sections"]) == {"preferences", "connections", "port_labels", "test_setups"}
    assert [c["name"] for c in j["sections"]["connections"]] == ["lab", "only-local"]
    assert [x["slug"] for x in j["sections"]["test_setups"]] == ["lab-a", "local-only"]


def test_preview_counts_adds_changes_removes(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    incoming = make({
        "connections": [{"name": "LAB", "host": "10.0.0.2"}, {"name": "new", "host": "h"}],
        "port_labels": {"10.0.0.1:4501": {"#0": {"name": "up", "description": ""},
                                          "#2": {"name": "x", "description": ""}}},
        "test_setups": [setup("Lab A", "/q/other.py"), setup("Brand New")]})
    sec = b.preview(incoming, s)["sections"]
    assert (sec["connections"]["adds"], sec["connections"]["changes"], sec["connections"]["removes"]) == (
        ["new"], ["LAB"], ["only-local"])
    assert (sec["port_labels"]["adds"], sec["port_labels"]["changes"], sec["port_labels"]["removes"]) == (
        ["10.0.0.1:4501 #2"], [], ["10.0.0.1:4501 #1"])
    assert (sec["test_setups"]["adds"], sec["test_setups"]["changes"], sec["test_setups"]["removes"]) == (
        ["brand-new"], ["lab-a"], ["local-only"])
    assert sec["test_setups"]["valid"] and sec["preferences"]["present"] is False


def test_preview_of_own_export_has_no_changes(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    sec = b.preview(b.export(s, "t"), s)["sections"]
    for name in b.DATA_SECTIONS:
        assert sec[name]["adds"] == sec[name]["changes"] == sec[name]["removes"] == [], name
        assert sec[name]["flags"] == [] and sec[name]["valid"]


def test_preview_flags_wireshark_path_change(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    prefs = {**dataclasses.asdict(s.settings.load().preferences), "wireshark_path": "/tmp/evil"}
    flags = b.preview(make({"preferences": prefs}), s)["sections"]["preferences"]["flags"]
    assert flags == ["`wireshark_path` changes from `/usr/bin/ws` to `/tmp/evil`"]


def test_preview_flags_every_changed_preference(tmp_path: Path) -> None:
    s = stores(tmp_path)
    new = {"templates_dir": "/t", "wireshark_path": "w", "workspace_dir": "/w", "release_on_exit": False,
           "trex_cfg_path": "/c.yaml", "library_dir": "/lib", "pcap_size_cap_mib": 128}
    assert set(new) == {f.name for f in dataclasses.fields(Preferences)}
    flags = b.preview(make({"preferences": new}), s)["sections"]["preferences"]["flags"]
    assert len(flags) == len(new)
    for k, v in new.items():
        assert any(f.startswith(f"`{k}` changes from `") and f.endswith(f"to `{v}`") for f in flags), k


def test_preview_lists_profile_paths(tmp_path: Path) -> None:
    s = stores(tmp_path)
    incoming = make({"test_setups": [setup("A", "/x/a.py"), setup("B", "/x/b.py"), setup("C", "/x/a.py")]})
    assert b.preview(incoming, s)["profile_paths"] == ["/x/a.py", "/x/b.py"]


def test_preview_lists_per_setup_path_changes(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    incoming = make({"test_setups": [setup("Lab A", "/evil.py"), setup("Brand New", "/n.py")]})
    assert b.preview(incoming, s)["path_changes"] == [
        {"slug": "lab-a", "port": 0, "from": "/p/udp.py", "to": "/evil.py"}]


def test_bundle_setup_slug_cannot_choose_the_file(tmp_path: Path) -> None:
    s = stores(tmp_path)
    evil = {**setup("Lab A"), "slug": "../../x"}
    b.apply(make({"test_setups": [evil]}), {"test_setups": "replace"}, s)
    assert [p.name for p in (tmp_path / "test_setups").iterdir()] == [f"{slugify('Lab A')}.json"]
    assert [p.name for p in tmp_path.iterdir()] == ["test_setups"]
    assert not (tmp_path.parent / "x.json").exists()


def test_skipped_invalid_section_does_not_block(tmp_path: Path) -> None:
    s = stores(tmp_path)
    incoming = make({"preferences": {"wireshark_path": 5}, "connections": [{"name": "n", "host": "h"}]})
    pv = b.preview(incoming, s)["sections"]
    assert pv["preferences"]["valid"] is False and pv["connections"]["valid"]
    r = b.apply(incoming, {"preferences": "skip", "connections": "replace"}, s)
    assert r["written"] == ["connections"] and "preferences" in r["skipped"]
    assert [c.name for c in s.settings.load().connections] == ["n"]


def test_invalid_theme_is_invalid_not_applied(tmp_path: Path) -> None:
    s = stores(tmp_path)
    pv = b.preview(make({"theme": "neon"}), s)["sections"]["theme"]
    assert pv["present"] and not pv["valid"] and "neon" in pv["errors"][0]
    assert b.preview(make({"theme": "dark"}), s)["sections"]["theme"]["valid"]
    r = b.apply(make({"theme": "dark"}), {"theme": "replace"}, s)
    assert r["written"] == [] and not (tmp_path / "settings.toml").exists()


def test_replace_setups_writes_before_deleting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = stores(tmp_path)
    seed(s)
    order: list[str] = []
    real_replace, real_unlink = os.replace, Path.unlink

    def replace(src: Any, dst: Any) -> None:
        order.append("write " + Path(dst).name)
        real_replace(src, dst)

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.suffix == ".json":
            order.append("delete " + self.name)
        real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    b.apply(make({"test_setups": [setup("New One")]}), {"test_setups": "replace"}, s)
    assert order == ["write new-one.json", "delete lab-a.json", "delete local-only.json"]


def test_write_error_names_what_was_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = stores(tmp_path)
    seed(s)
    real = os.replace

    def replace(src: Any, dst: Any) -> None:
        if Path(dst).name == "two.json":
            raise OSError("disk full")
        real(src, dst)
    monkeypatch.setattr(os, "replace", replace)
    incoming = make({"connections": [{"name": "n", "host": "h"}],
                     "test_setups": [setup("One"), setup("Two"), setup("Three")]})
    with pytest.raises(BundleWriteError) as e:
        b.apply(incoming, {"connections": "replace", "test_setups": "replace"}, s)
    assert e.value.written == ["connections", "test_setups/one"] and e.value.failed == "test_setups/two"
    monkeypatch.undo()
    assert {x["slug"] for x in s.setups.list()} == {"lab-a", "local-only", "one"}     # nothing deleted yet


def test_settings_store_refusal_is_invalid_bundle(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A SettingsError from the settings store's save is a refusal of the content (422), not a storage
    failure (500); it is the first write, so nothing else has been written."""
    s = stores(tmp_path)
    seed(s)

    def refuse(self: SettingsStore, new: Settings) -> None:
        raise SettingsError("trex_cfg_path 'x' is not an absolute path")
    monkeypatch.setattr(SettingsStore, "save", refuse)
    incoming = make({"preferences": {"trex_cfg_path": "/ok"}, "test_setups": [setup("Brand New")]})
    with pytest.raises(BundleError) as e:
        b.apply(incoming, {"preferences": "replace", "test_setups": "replace"}, s)
    assert e.value.code == "invalid_bundle" and "preferences" in str(e.value)
    assert {x["slug"] for x in s.setups.list()} == {"lab-a", "local-only"}


def test_unsupported_version_names_it(tmp_path: Path) -> None:
    for v in (2, 0, "1", True):
        with pytest.raises(BundleError) as e:
            b.preview(make({}, version=v), stores(tmp_path))
        assert e.value.code == "unsupported_bundle"
        assert "not supported by this trex-web-gui (expects 1)" in str(e.value)
    with pytest.raises(BundleError, match="bundle version 2 is not supported"):
        b.apply(make({}, version=2), {}, stores(tmp_path))


@pytest.mark.parametrize("bad", [{"version": 1}, {"format": "other", "version": 1, "sections": {}}, [], None])
def test_wrong_format_is_unsupported(tmp_path: Path, bad: object) -> None:
    with pytest.raises(BundleError) as e:
        b.preview(bad, stores(tmp_path))
    assert e.value.code == "unsupported_bundle"


def test_old_name_format_is_refused(tmp_path: Path) -> None:
    old = {"format": "trex-gui-settings", "version": 1, "exported_at": "x", "app_version": "t",
           "sections": {}}
    for call in (lambda: b.preview(old, stores(tmp_path)), lambda: b.apply(old, {}, stores(tmp_path))):
        with pytest.raises(BundleError) as e:
            call()
        assert e.value.code == "unsupported_bundle"


def test_apply_replace_then_reload_equals_bundle(tmp_path: Path) -> None:
    src = stores(tmp_path / "src")
    seed(src)
    exported = b.export(src, "t")
    dst = stores(tmp_path / "dst")
    dst.settings.save(Settings(connections=[SavedConnection("gone", "h")]))
    dst.labels.set("a:1", "#0", "gone", "")
    dst.setups.save(parse_setup(setup("Gone")))
    r = b.apply(exported, {n: "replace" for n in b.DATA_SECTIONS}, dst)
    assert r["written"] == list(b.DATA_SECTIONS)
    again = b.export(dst, "t")
    assert again["sections"] == exported["sections"]


def test_apply_merge_keeps_local_only_connection_and_merges_setups_by_slug(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    incoming = make({"connections": [{"name": "LAB", "host": "10.9.9.9"}],
                     "port_labels": {"10.0.0.1:4501": {"#0": {"name": "NEW", "description": "d"}}},
                     "test_setups": [setup("lab a", "/new.py"), setup("Added")]})
    b.apply(incoming, {n: "merge" for n in b.DATA_SECTIONS if n != "preferences"}, s)
    conns = {c.name: c.host for c in s.settings.load().connections}
    assert conns == {"LAB": "10.9.9.9", "only-local": "10.0.0.9"}
    labels = s.labels.load()["10.0.0.1:4501"]
    assert labels["#0"] == {"name": "NEW", "description": "d"} and labels["#1"]["name"] == "down"
    got = {x["slug"]: x for x in s.setups.list()}
    assert set(got) == {"lab-a", "local-only", "added"}
    assert got["lab-a"]["ports"][0]["profile"]["path"] == "/new.py"


def test_invalid_section_blocks_the_whole_apply(tmp_path: Path) -> None:
    s = stores(tmp_path)
    seed(s)
    before = b.export(s, "t")["sections"]
    bad = make({"connections": [{"name": "n", "host": "h"}], "port_labels": {"s": {"bad key": {}}},
                "test_setups": [setup("Fine")]})
    with pytest.raises(BundleError) as e:
        b.apply(bad, {n: "replace" for n in b.DATA_SECTIONS}, s)
    assert e.value.code == "invalid_bundle" and "key" in str(e.value)
    assert b.export(s, "t")["sections"] == before
    bad = make({"test_setups": [setup("Fine"), {"name": "x"}]})
    with pytest.raises(BundleError, match=r"test_setups\[1\]"):
        b.apply(bad, {"test_setups": "replace"}, s)
    assert b.export(s, "t")["sections"] == before


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(Preferences)])
def test_bundle_round_trips_every_preference(tmp_path: Path, field: str) -> None:
    s = stores(tmp_path)
    default = getattr(Preferences(), field)
    changed = (not default) if isinstance(default, bool) else default + 1 if isinstance(default, int) \
        else "/changed/path"
    prefs = {**dataclasses.asdict(Preferences()), field: changed}
    b.apply(make({"preferences": prefs}), {"preferences": "replace"}, s)
    assert getattr(s.settings.load().preferences, field) == changed
    assert b.export(s, "t")["sections"]["preferences"] == prefs


def test_bundle_missing_preference_key_gets_default(tmp_path: Path) -> None:
    s = stores(tmp_path)
    s.settings.save(Settings(Preferences(trex_cfg_path="/other.yaml", wireshark_path="w")))
    pre_fu6 = make({"preferences": {"wireshark_path": "ws2"}})
    b.apply(pre_fu6, {"preferences": "replace"}, s)
    p = s.settings.load().preferences
    assert p.trex_cfg_path == Preferences().trex_cfg_path and p.wireshark_path == "ws2"


def test_unknown_section_is_ignored_and_listed(tmp_path: Path) -> None:
    s = stores(tmp_path)
    incoming = make({"connections": [], "future": {"x": 1}, "another": 2})
    assert b.preview(incoming, s)["ignored"] == ["another", "future"]
    assert b.apply(incoming, {"future": "replace", "connections": "skip"}, s) == {
        "written": [], "skipped": list(b.KNOWN_SECTIONS)}


def stored(s: Stores, slug: str) -> dict[str, Any]:
    """The stored setup with this slug."""
    return dict(s.setups.get(slug))


def test_import_never_keeps_an_old_updated_for_changed_content(tmp_path: Path) -> None:
    s = stores(tmp_path)
    s.setups.import_setup(parse_setup({**setup("Lab A"), "created": "2020-01-01T00:00:00.000Z",
                                       "updated": "2020-01-02T00:00:00.000Z"}))
    local = stored(s, "lab-a")
    edited = {**local, "ports": [port("/evil.py")]}           # same slug, same `updated`, other content
    b.apply(make({"test_setups": [edited]}), {"test_setups": "merge"}, s)
    got = stored(s, "lab-a")
    assert got["ports"][0]["profile"]["path"] == "/evil.py"
    assert got["updated"] != local["updated"] and got["created"] == local["created"]


def test_import_of_a_new_slug_keeps_the_bundles_timestamps(tmp_path: Path) -> None:
    s = stores(tmp_path)
    new = {**setup("Fresh"), "created": "2020-01-01T00:00:00.000Z", "updated": "2021-01-01T00:00:00.000Z"}
    b.apply(make({"test_setups": [new]}), {"test_setups": "merge"}, s)
    got = stored(s, "fresh")
    assert (got["created"], got["updated"]) == (new["created"], new["updated"])


def test_import_of_identical_content_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = stores(tmp_path)
    s.setups.save(parse_setup(setup("Lab A")))
    local = stored(s, "lab-a")
    monkeypatch.setattr(os, "replace", lambda *a: pytest.fail("rewrote an identical setup"))
    other_stamps = {**local, "created": "2001-01-01T00:00:00.000Z", "updated": "2002-01-01T00:00:00.000Z"}
    b.apply(make({"test_setups": [other_stamps]}), {"test_setups": "replace"}, s)
    assert stored(s, "lab-a") == local


def test_setups_cap_counts_corrupt_files_and_preview_reports_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trex_web_gui.store import test_setups as ts
    monkeypatch.setattr(ts, "SETUPS_MAX", 2)
    monkeypatch.setattr(b, "SETUPS_MAX", 2)
    s = stores(tmp_path)
    s.setups.save(parse_setup(setup("One")))
    (tmp_path / "test_setups" / "broken.json").write_text("{nope")
    incoming = make({"test_setups": [setup("Two")]})
    sec = b.preview(incoming, s)["sections"]["test_setups"]
    assert sec["valid"] is False and "merge" in sec["errors"][0] and "at most 2" in sec["errors"][0]
    with pytest.raises(BundleError, match="at most 2"):
        b.apply(incoming, {"test_setups": "merge"}, s)
    assert {x["slug"] for x in s.setups.list()} == {"one"}


def test_preferences_merge_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="preferences") as e:
        b.apply(make({"preferences": {}}), {"preferences": "merge"}, stores(tmp_path))
    assert e.value.code == "bad_request"


def test_preview_flags_what_a_setup_runs_later(tmp_path: Path) -> None:
    s = stores(tmp_path)
    layer = {"mode": "L2", "src_mac": None, "dst_mac": "aa:bb:cc:dd:ee:ff", "vlan": [], "src_ipv4": None,
             "dst_ipv4": None, "ipv6": None}
    p0 = {**port(n=0), "acquire_force": True, "stop_first": True}
    p1 = {**port(n=1), "layer": layer}
    plain = setup("Plain")
    incoming = make({"test_setups": [{**setup("Risky"), "ports": [p0, p1]}, plain]})
    sec = b.preview(incoming, s)["sections"]["test_setups"]
    assert sec["valid"], sec["errors"]
    assert sec["flags"] == ["risky: port 0 force-acquires on Run",
                            "risky: port 0 stops a transmitting port before assigning",
                            "risky: port 1 applies a port configuration"]
    assert re.fullmatch(r"[a-z-]+: port \d .*", sec["flags"][0])
