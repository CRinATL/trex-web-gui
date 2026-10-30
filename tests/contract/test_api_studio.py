"""/api/studio routes (P4a Task 6): the library, stream pages and diffs, uploads, imports, exports, simulate,
and the bridge to Control's assign. Pcaps are written with struct, so the test process never imports Scapy
for them."""
import asyncio
import base64
import json
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from support import FIXTURES
from test_api import client
from test_api_control import connect
from trex_web_gui import trexapi
from trex_web_gui.studio.library import ProfileLibrary

PROFILE = str(FIXTURES / "profiles" / "udp_for_benchmarks.py")
TUNABLES = ["--packet_len", "128"]
RECORDED = json.loads((FIXTURES / "profiles" / "two_streams.json").read_text())[0]


def udp_frame(src: int = 0x10000001, dst: int = 0x30000001, size: int = 60) -> bytes:
    """An Ethernet/IPv4/UDP frame of `size` bytes (checksums left zero), src and dst as integers."""
    payload = b"x" * (size - 42)
    ip = struct.pack("!BBHHHBBHII", 0x45, 0, 20 + 8 + len(payload), 1, 0, 64, 17, 0, src, dst)
    udp = struct.pack("!HHHH", 1025, 12, 8 + len(payload), 0)
    return b"\x00\x11\x22\x33\x44\x55" + b"\x00\x66\x77\x88\x99\xaa" + b"\x08\x00" + ip + udp + payload


def pcap_bytes(frames: list[bytes], gap_usec: int = 1000) -> bytes:
    """A classic libpcap file (Ethernet link type) holding `frames`, `gap_usec` apart."""
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    for i, f in enumerate(frames):
        t = 1_700_000_000 * 1_000_000 + i * gap_usec
        out.append(struct.pack("<IIII", t // 1_000_000, t % 1_000_000, len(f), len(f)) + f)
    return b"".join(out)


def pcap_opts(**kw: Any) -> dict[str, Any]:
    """PcapOptions with the defaults (timestamps, speedup 1, one loop, no prefix, no rewrite) and `kw`."""
    return {"ipg_usec": None, "speedup": 1.0, "loop_count": 1, "name_prefix": "", "rewrite_src": None,
            "rewrite_dst": None, **kw}


def lib_dir(tmp_path: Path) -> Path:
    """The library directory of an app over `tmp_path` with default preferences."""
    return tmp_path / "profiles"


def uploads(tmp_path: Path) -> list[str]:
    """The file names in the uploads directory (empty when it does not exist)."""
    d = tmp_path / "uploads"
    return sorted(os.listdir(d)) if d.is_dir() else []


def import_bench(c: Any, **kw: Any) -> dict[str, Any]:
    """Import udp_for_benchmarks.py --packet_len 128 by path; asserts 200 and returns the entry."""
    r = c.post("/api/studio/import", json={"path": PROFILE, "tunables": TUNABLES, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def get_profile(c: Any, slug: str) -> dict[str, Any]:
    """GET one profile; asserts 200."""
    r = c.get(f"/api/studio/profiles/{slug}")
    assert r.status_code == 200, r.text
    return r.json()


def upload(c: Any, name: str, data: bytes) -> Any:
    """PUT `data` as an upload named `name`; the response."""
    return c.put("/api/studio/uploads", params={"name": name}, content=data)


def burst(name: Any, nxt: Any = None) -> dict[str, Any]:
    """The recorded stream as a named single burst of 10 packets, with `next` when given."""
    s = json.loads(json.dumps(RECORDED))
    s["name"] = name
    s["mode"] = {"type": "single_burst", "rate": {"type": "pps", "value": 10.0}, "total_pkts": 10}
    if nxt is not None:
        s["next"] = nxt
    return s


# ---- CRUD


def test_create_list_get_rename_duplicate_delete(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.post("/api/studio/profiles", json={"name": "My Profile"})
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["slug"] == "my-profile" and e["stream_count"] == 0 and e["source"]["kind"] == "new"
        assert c.post("/api/studio/profiles", json={"name": "my profile"}).json()["error"] == "exists"
        listing = c.get("/api/studio/profiles").json()
        assert [p["slug"] for p in listing["profiles"]] == ["my-profile"]
        assert listing["load_errors"] == [] and listing["dir"] == str(lib_dir(tmp_path))
        got = get_profile(c, "my-profile")
        assert got["entry"]["name"] == "My Profile" and got["sidecar"]["name"] == "My Profile"
        r = c.post("/api/studio/profiles/my-profile/rename", json={"name": "Other", "rev": got["rev"]})
        assert r.status_code == 200 and r.json()["slug"] == "other", r.text
        assert c.get("/api/studio/profiles/my-profile").json()["error"] == "not_found"
        r = c.post("/api/studio/profiles/other/duplicate", json={"name": "Copy"})
        assert r.status_code == 200 and r.json()["slug"] == "copy", r.text
        r = c.delete("/api/studio/profiles/other")
        assert r.status_code == 200 and [p["slug"] for p in r.json()["profiles"]] == ["copy"]
        assert c.delete("/api/studio/profiles/other").json()["error"] == "not_found"
        r = c.post("/api/studio/profiles", json={"name": "!!!"})
        assert r.status_code == 422 and r.json()["error"] == "invalid_name"


def test_rename_with_an_old_rev_is_409_stale(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.post("/api/studio/profiles", json={"name": "a"})
        old = get_profile(c, "a")["rev"]
        r = c.put("/api/studio/profiles/a/streams", json={"rev": old, "added": [RECORDED]})
        assert r.status_code == 200, r.text
        r = c.post("/api/studio/profiles/a/rename", json={"name": "b", "rev": old})
        assert r.status_code == 409 and r.json()["error"] == "stale"
        assert r.json()["rev"] == get_profile(c, "a")["rev"] != old
        assert (lib_dir(tmp_path) / "a.json").exists() and not (lib_dir(tmp_path) / "b.json").exists()


def test_bad_slug_is_404_before_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_io(self: ProfileLibrary) -> Path:
        raise AssertionError("the library directory was resolved for a bad slug")
    with client(tmp_path) as c:
        monkeypatch.setattr(ProfileLibrary, "_dir", no_io)
        for method, url, body in [
            ("GET", "/api/studio/profiles/Bad_Slug", None),
            ("DELETE", "/api/studio/profiles/a.b", None),
            ("POST", "/api/studio/profiles/a%20b/rename", {"name": "x", "rev": "0"}),
            ("POST", "/api/studio/profiles/-a/duplicate", {"name": "x"}),
            ("GET", "/api/studio/profiles/A/streams", None),
            ("PUT", "/api/studio/profiles/A/streams", {"rev": "0"}),
            ("GET", "/api/studio/profiles/A/export?format=json", None),
            ("POST", "/api/studio/profiles/A/simulate", None),
        ]:
            r = c.request(method, url, json=body)
            assert r.status_code == 404 and r.json()["error"] == "not_found", (url, r.text)
            assert r.json()["message"].startswith("No profile "), (url, r.text)     # the route's, not a 404


# ---- edits


def test_put_streams_toggles_enabled_and_returns_new_rev(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        import_bench(c)
        page = c.get("/api/studio/profiles/udp-for-benchmarks/streams").json()
        assert page["rows"][0]["stream"]["enabled"] is True
        r = c.put("/api/studio/profiles/udp-for-benchmarks/streams",
                  json={"rev": page["rev"], "changed": {"0": {"enabled": False}}})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["rev"] != page["rev"] and j["stream_count"] == 1
        assert j["renamed"] == {} and j["cleared"] == []
        after = c.get("/api/studio/profiles/udp-for-benchmarks/streams").json()
        assert after["rev"] == j["rev"] and after["rows"][0]["stream"]["enabled"] is False
        before = dict(page["rows"][0]["stream"], enabled=False)
        assert after["rows"][0]["stream"] == before          # only the patched field changed


def editor_entry(tag: str = "x") -> dict[str, Any]:
    """A valid sidecar entry for the recorded stream."""
    doc = {"fileType": "trex-packet-editor", "version": "1.0.0", "metadata": {"caption": tag},
           "packet": [{"id": "Ether", "fields": []}], "fePrarameters": {}, "feInstructions": []}
    return {"editor": {"doc": doc, "binary": RECORDED["packet"]["binary"], "vm": RECORDED["vm"]}}


def test_page_rows_carry_has_editor_only(tmp_path: Path) -> None:
    """A row says whether its stream has an editor entry; the entry itself is not in the page."""
    with client(tmp_path) as c:
        import_bench(c)
        page = c.get("/api/studio/profiles/udp-for-benchmarks/streams").json()
        assert page["rows"][0]["has_editor"] is False
        r = c.put("/api/studio/profiles/udp-for-benchmarks/streams",
                  json={"rev": page["rev"], "sidecar": {"0": editor_entry("secret")}})
        assert r.status_code == 200, r.text
        r2 = c.get("/api/studio/profiles/udp-for-benchmarks/streams")
        assert r2.json()["rows"][0]["has_editor"] is True and "secret" not in r2.text


def test_sidecar_entry_route(tmp_path: Path) -> None:
    """GET .../streams/{i}/sidecar gives {rev, entry}; an index out of range is 404."""
    with client(tmp_path) as c:
        import_bench(c)
        base = "/api/studio/profiles/udp-for-benchmarks/streams"
        rev = c.get(base).json()["rev"]
        assert c.get(f"{base}/0/sidecar").json() == {"rev": rev, "entry": {}}
        j = c.put(base, json={"rev": rev, "sidecar": {"0": editor_entry("e")}}).json()
        assert c.get(f"{base}/0/sidecar").json() == {"rev": j["rev"], "entry": editor_entry("e")}
        assert c.get(f"{base}/1/sidecar").status_code == 404
        assert c.get(f"{base}/-1/sidecar").status_code == 404
        r = c.put(base, json={"rev": j["rev"], "added": [RECORDED], "added_sidecar": []})
        assert r.status_code == 422 and r.json()["field"] == "added_sidecar"


def test_source_of_a_py_entry(tmp_path: Path) -> None:
    """The source route returns the imported .py's path and text."""
    with client(tmp_path) as c:
        import_bench(c)
        r = c.get("/api/studio/profiles/udp-for-benchmarks/source")
    assert r.status_code == 200, r.text
    assert r.json() == {"path": PROFILE, "text": Path(PROFILE).read_text()}


def set_source(tmp_path: Path, slug: str, **over: Any) -> None:
    """Rewrite the `source` of a profile's sidecar on disk with `over`."""
    side = lib_dir(tmp_path) / f"{slug}.gui.json"
    j = json.loads(side.read_text())
    j["source"] = {**j["source"], **over}
    side.write_text(json.dumps(j))


def test_source_refused_for_json_entry_and_missing_file(tmp_path: Path) -> None:
    """409 no_source for a non-.py entry, 404 source_missing for a file that is gone, 404 for a bad slug."""
    with client(tmp_path) as c:
        c.post("/api/studio/profiles", json={"name": "fresh"})
        r = c.get("/api/studio/profiles/fresh/source")
        assert r.status_code == 409 and r.json()["error"] == "no_source"
        import_bench(c)
        set_source(tmp_path, "udp-for-benchmarks", path=str(tmp_path / "gone.py"))
        r = c.get("/api/studio/profiles/udp-for-benchmarks/source")
        assert r.status_code == 404 and r.json()["error"] == "source_missing"
        assert c.get("/api/studio/profiles/nothing/source").status_code == 404


def test_source_over_cap_is_413(tmp_path: Path) -> None:
    """A source over SOURCE_MAX is 413; one of exactly SOURCE_MAX reads."""
    from trex_web_gui.api.studio import SOURCE_MAX
    big = tmp_path / "big.py"
    with client(tmp_path) as c:
        import_bench(c)
        big.write_bytes(b"#" * (SOURCE_MAX + 1))
        set_source(tmp_path, "udp-for-benchmarks", path=str(big))
        r = c.get("/api/studio/profiles/udp-for-benchmarks/source")
        assert r.status_code == 413 and r.json()["error"] == "too_large"
        big.write_bytes(b"#" * SOURCE_MAX)
        assert c.get("/api/studio/profiles/udp-for-benchmarks/source").status_code == 200


def test_source_refuses_a_non_py_target(tmp_path: Path) -> None:
    """A sidecar that names a non-.py file, a directory, or a symlink that resolves to a non-.py file is 422,
    and the file's text is never read (R17)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    link = tmp_path / "innocent.py"
    link.symlink_to(secret)
    (tmp_path / "dir.py").mkdir()
    with client(tmp_path) as c:
        import_bench(c)
        for target in (secret, link, tmp_path / "dir.py"):
            set_source(tmp_path, "udp-for-benchmarks", path=str(target))
            r = c.get("/api/studio/profiles/udp-for-benchmarks/source")
            assert r.status_code == 422 and r.json()["error"] == "bad_request", target
            assert "TOP-SECRET" not in r.text


def test_lone_surrogate_in_a_sidecar_entry_is_422_naming_the_field(tmp_path: Path) -> None:
    """A JSON "\\ud800" in an entry answers 422 invalid_stream on the sidecar, not "does not parse"."""
    with client(tmp_path) as c:
        import_bench(c)
        base = "/api/studio/profiles/udp-for-benchmarks/streams"
        rev = c.get(base).json()["rev"]
        body = json.dumps({"rev": rev, "sidecar": {"0": editor_entry("\ud800")}})
        r = c.put(base, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "invalid_stream" and r.json()["field"] == "sidecar" and r.json()["index"] == 0


def test_source_that_is_a_fifo_or_unreadable_is_refused(tmp_path: Path) -> None:
    """A FIFO named .py is 422 without blocking; a file the backend may not read is 403 naming the path."""
    fifo = tmp_path / "pipe.py"
    os.mkfifo(fifo)
    locked = tmp_path / "locked.py"
    locked.write_text("x = 1\n")
    locked.chmod(0)
    try:
        with client(tmp_path) as c:
            import_bench(c)
            set_source(tmp_path, "udp-for-benchmarks", path=str(fifo))
            r = c.get("/api/studio/profiles/udp-for-benchmarks/source")
            assert r.status_code == 422 and r.json()["error"] == "bad_request"
            set_source(tmp_path, "udp-for-benchmarks", path=str(locked))
            r = c.get("/api/studio/profiles/udp-for-benchmarks/source")
            if os.geteuid() != 0:
                assert r.status_code == 403 and r.json()["error"] == "source_unreadable"
                assert str(locked) in r.json()["message"]
    finally:
        locked.chmod(0o600)


def test_a_next_to_a_disabled_stream_is_saved_and_warned(tmp_path: Path) -> None:
    """Disabling S1 under S0 -> S1 saves (200) and warns in the reply and the page; re-enabling clears it."""
    with client(tmp_path) as c:
        slug = c.post("/api/studio/profiles", json={"name": "w"}).json()["slug"]
        url = f"/api/studio/profiles/{slug}/streams"
        rev = c.get(url).json()["rev"]
        r = c.put(url, json={"rev": rev, "added": [burst("S0", "S1"), burst("S1")]})
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == []
        r = c.put(url, json={"rev": r.json()["rev"], "changed": {"1": {"enabled": False}}})
        assert r.status_code == 200, r.text
        want = [{"index": 0, "name": "S0", "next": "S1"}]
        assert r.json()["warnings"] == want
        page = c.get(url, params={"offset": 1}).json()
        assert [x["index"] for x in page["rows"]] == [1] and page["warnings"] == want
        r = c.put(url, json={"rev": r.json()["rev"], "changed": {"1": {"enabled": True}}})
        assert r.status_code == 200 and r.json()["warnings"] == []
        assert c.get(url).json()["warnings"] == []


def test_put_streams_with_old_rev_is_409_and_writes_nothing(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        import_bench(c)
        old = get_profile(c, "udp-for-benchmarks")["rev"]
        c.put("/api/studio/profiles/udp-for-benchmarks/streams",
              json={"rev": old, "changed": {"0": {"enabled": False}}})
        files = {p.name: p.read_bytes() for p in lib_dir(tmp_path).iterdir()}
        r = c.put("/api/studio/profiles/udp-for-benchmarks/streams",
                  json={"rev": old, "changed": {"0": {"isg": 5.0}}})
        assert r.status_code == 409 and r.json()["error"] == "stale"
        assert r.json()["rev"] == get_profile(c, "udp-for-benchmarks")["rev"]
        assert {p.name: p.read_bytes() for p in lib_dir(tmp_path).iterdir()} == files


def test_invalid_stream_refused_before_any_write(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.post("/api/studio/profiles", json={"name": "p"})
        rev = get_profile(c, "p")["rev"]
        rev = c.put("/api/studio/profiles/p/streams",
                    json={"rev": rev, "added": [burst("a"), burst("b", "a")]}).json()["rev"]
        files = {p.name: p.read_bytes() for p in lib_dir(tmp_path).iterdir()}
        rate = {"type": "percentage", "value": 101}
        for patch, field in [({"flags": 16}, "flags"), ({"flags": "3"}, "flags"),
                             ({"mode": {**burst("a")["mode"], "rate": rate}}, "mode.rate")]:
            r = c.put("/api/studio/profiles/p/streams", json={"rev": rev, "changed": {"1": patch}})
            j = r.json()
            assert r.status_code == 422 and j["error"] == "invalid_stream", r.text
            assert j["index"] == 1 and j["field"] == field, j
            assert {p.name: p.read_bytes() for p in lib_dir(tmp_path).iterdir()} == files
        # an added stream is checked too (no index: the field names its place in `added`)
        r = c.put("/api/studio/profiles/p/streams", json={"rev": rev, "added": [{**burst("c"), "flags": 16}]})
        assert r.status_code == 422 and r.json()["error"] == "invalid_stream" and r.json()["index"] is None
        assert r.json()["field"] == "added[0].flags"
        assert {p.name: p.read_bytes() for p in lib_dir(tmp_path).iterdir()} == files
        # ruling Task 2 M3 (revised): a `next` the caller wrote to a missing name is refused
        r = c.put("/api/studio/profiles/p/streams", json={"rev": rev, "changed": {"1": {"next": "nobody"}}})
        j = r.json()
        assert r.status_code == 422 and j["error"] == "invalid_stream", r.text
        assert j["index"] == 1 and j["field"] == "next", j
        assert {p.name: p.read_bytes() for p in lib_dir(tmp_path).iterdir()} == files
        # ...while an added (pasted) stream's dangling `next` is cleared and reported (ruling Task 2 M3)
        r = c.put("/api/studio/profiles/p/streams", json={"rev": rev, "added": [burst("c", "nobody")]})
        assert r.status_code == 200 and r.json()["cleared"] == ["c"], r.text
        added = c.get("/api/studio/profiles/p/streams").json()["rows"][2]["stream"]
        assert added["name"] == "c" and "next" not in added
        rev = r.json()["rev"]
        # ...and so is a `next` a removal leaves dangling
        r = c.put("/api/studio/profiles/p/streams", json={"rev": rev, "removed": [0]})
        assert r.status_code == 200 and r.json()["cleared"] == ["b"] and r.json()["stream_count"] == 2, r.text
        row = c.get("/api/studio/profiles/p/streams").json()["rows"][0]
        assert row["stream"]["name"] == "b" and "next" not in row["stream"]


def test_page_and_reorder_of_a_22000_stream_profile(tmp_path: Path) -> None:
    n = 22000
    streams = [dict(json.loads(json.dumps(RECORDED)), name=f"s{i}") for i in range(n)]
    ProfileLibrary(lambda: lib_dir(tmp_path)).create("Big", streams)
    with client(tmp_path) as c:
        page = c.get("/api/studio/profiles/big/streams", params={"offset": 21950}).json()
        assert page["stream_count"] == n and len(page["names"]) == n and page["offset"] == 21950
        assert len(page["rows"]) == 50 and page["rows"][0]["index"] == 21950
        page = c.get("/api/studio/profiles/big/streams").json()
        assert len(page["rows"]) == 100 and page["names"][:2] == ["s0", "s1"]
        row = page["rows"][0]
        assert row["index"] == 0 and row["stream"]["name"] == "s0"
        assert row["length"] == len(base64.b64decode(RECORDED["packet"]["binary"]))
        assert row["summary"]["proto"] and isinstance(row["summary"]["info"], str)
        body = {"rev": page["rev"], "order": list(range(n - 1, -1, -1))}
        assert len(json.dumps(body)) < 1024 * 1024
        r = c.put("/api/studio/profiles/big/streams", json=body)
        assert r.status_code == 200 and r.json()["stream_count"] == n, r.text
        after = c.get("/api/studio/profiles/big/streams", params={"limit": 1}).json()
        assert after["names"][0] == f"s{n - 1}" and len(after["rows"]) == 1
        for q in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            assert c.get("/api/studio/profiles/big/streams", params=q).status_code == 422


def test_page_flags_a_stored_stream_without_a_usable_packet(tmp_path: Path) -> None:
    lib_dir(tmp_path).mkdir()
    no_packet = {k: v for k, v in RECORDED.items() if k != "packet"}
    bad_b64 = dict(RECORDED, packet={"binary": "%%%", "meta": ""})
    (lib_dir(tmp_path) / "hand.json").write_text(json.dumps([RECORDED, no_packet, bad_b64, dict(RECORDED,
                                                                                              packet=7)]))
    with client(tmp_path) as c:
        r = c.get("/api/studio/profiles/hand/streams")
        assert r.status_code == 200, r.text
        rows = r.json()["rows"]
    assert rows[0]["summary"]["proto"] != "?" and rows[0]["length"] > 0
    for row in rows[1:]:
        assert row["length"] is None and row["summary"]["proto"] == "?", row
        assert row["summary"]["info"].startswith("no usable packet"), row


def test_page_warnings_skip_stored_streams_they_cannot_read(tmp_path: Path) -> None:
    """A hand-written profile with a stream lacking `enabled`, one whose `enabled` is not a bool, and
    list-valued names and nexts: the page is 200 and warns only for the readable pair (final review I1)."""
    lib_dir(tmp_path).mkdir()
    no_enabled = {k: v for k, v in burst("x", "off").items() if k != "enabled"}
    streams = [burst("a", "off"), dict(burst("off"), enabled=False), no_enabled,
               dict(burst("y", "off"), enabled=1),
               burst(["l"], "off"), burst("z", ["off"]), dict(burst("w", ["l"]), enabled=False),
               dict(burst(["off"]), enabled=False)]
    (lib_dir(tmp_path) / "hand.json").write_text(json.dumps(streams))
    with client(tmp_path) as c:
        r = c.get("/api/studio/profiles/hand/streams")
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == [{"index": 0, "name": "a", "next": "off"}]


# ---- imports


def test_import_py_with_tunables_records_the_source(tmp_path: Path) -> None:
    form = {"values": {"packet_len": 128}, "extra": ""}
    with client(tmp_path) as c:
        e = import_bench(c, tunable_form=form)
        assert e["slug"] == "udp-for-benchmarks" and e["stream_count"] == 1
        src = get_profile(c, e["slug"])["sidecar"]["source"]
    assert src["kind"] == "py" and os.path.isabs(src["path"]) and src["path"] == PROFILE
    assert src["args"] == TUNABLES and src["tunables"] == form


def test_import_legacy_yaml(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        e = import_bench(c, path=str(FIXTURES / "profiles" / "gui_example.yaml"), tunables=[], name="Legacy")
        assert e["slug"] == "legacy" and e["stream_count"] == 1 and e["source"]["kind"] == "yaml"
        row = c.get("/api/studio/profiles/legacy/streams").json()["rows"][0]
        assert row["stream"]["name"] == "l1" and row["stream"]["flow_stats"]["rule_type"] == "latency"
        r = c.post("/api/studio/import", json={"path": str(FIXTURES / "profiles" / "gui_example.yaml"),
                                                "tunables": ["--x"]})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"      # tunables are py only


def test_upload_then_import_pcap_deletes_the_upload(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = upload(c, "trace.pcap", pcap_bytes([udp_frame()] * 3))
        assert r.status_code == 200, r.text
        up = r.json()
        assert up["name"] == "trace.pcap" and up["size"] > 0 and len(up["upload_id"]) == 32
        assert uploads(tmp_path) == [up["upload_id"] + ".pcap"]
        r = c.post("/api/studio/import",
                   json={"upload_id": up["upload_id"], "pcap": pcap_opts(), "name": "Trace"})
        assert r.status_code == 200, r.text
        assert r.json()["stream_count"] == 3 and r.json()["source"]["kind"] == "pcap"
        assert uploads(tmp_path) == []
        r = c.post("/api/studio/import", json={"upload_id": up["upload_id"], "pcap": pcap_opts()})
        assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_failed_import_keeps_the_upload(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        up = upload(c, "junk.pcap", b"not a pcap at all, just bytes" * 10).json()
        r = c.post("/api/studio/import", json={"upload_id": up["upload_id"], "pcap": pcap_opts()})
        assert r.status_code == 422 and r.json()["error"] == "profile_error", r.text
        assert uploads(tmp_path) == [up["upload_id"] + ".pcap"]
        (tmp_path / "uploads" / (up["upload_id"] + ".pcap")).write_bytes(pcap_bytes([udp_frame()]))
        r = c.post("/api/studio/import",
                   json={"upload_id": up["upload_id"], "pcap": pcap_opts(), "name": "x"})
        assert r.status_code == 200, r.text


def test_expired_uploads_are_removed(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        old = upload(c, "a.json", b"[]").json()
        fresh = upload(c, "b.json", b"[]").json()
        stale = tmp_path / "uploads" / (old["upload_id"] + ".json")
        t = time.time() - 3601
        os.utime(stale, (t, t))
        third = upload(c, "c.json", b"[]").json()
        assert uploads(tmp_path) == sorted([fresh["upload_id"] + ".json", third["upload_id"] + ".json"])


def test_expired_uploads_are_removed_at_startup(tmp_path: Path) -> None:
    d = tmp_path / "uploads"
    d.mkdir()
    (d / ("a" * 32 + ".pcap")).write_bytes(b"x")
    (d / ("b" * 32 + ".pcap")).write_bytes(b"x")
    t = time.time() - 3601
    os.utime(d / ("a" * 32 + ".pcap"), (t, t))
    with client(tmp_path):
        assert uploads(tmp_path) == ["b" * 32 + ".pcap"]


@pytest.mark.parametrize("name", ["x.py", "x.exe", "x"])
def test_upload_suffix_refused(tmp_path: Path, name: str) -> None:
    with client(tmp_path) as c:
        r = upload(c, name, b"print(1)")
        assert r.status_code == 422 and r.json()["error"] == "bad_request", r.text
    assert uploads(tmp_path) == []


def test_upload_name_over_255_characters_refused(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = upload(c, "x" * 251 + ".pcap", b"\0" * 100)
        assert r.status_code == 422 and r.json()["error"] == "bad_request", r.text
        assert uploads(tmp_path) == []
        assert upload(c, "x" * 250 + ".pcap", b"\0" * 100).status_code == 200


def test_upload_over_the_cap_is_413_and_leaves_nothing(tmp_path: Path) -> None:
    cap = 64 * 1024 * 1024
    with client(tmp_path) as c:
        r = c.put("/api/studio/uploads", params={"name": "big.pcap"}, content=b"",
                  headers={"content-length": str(cap + 1)})
        assert r.status_code == 413 and r.json()["error"] == "too_large", r.text
        assert uploads(tmp_path) == []
        # a body longer than its declared length is stopped at the cap and the partial file removed
        r = c.put("/api/studio/uploads", params={"name": "big.pcap"}, content=b"\0" * (cap + 1024),
                  headers={"content-length": "100"})
        assert r.status_code == 413 and r.json()["error"] == "too_large", r.text
        assert uploads(tmp_path) == []
        assert upload(c, "ok.pcap", b"\0" * 100).status_code == 200       # the slot was released


def test_upload_without_length_is_411(tmp_path: Path) -> None:
    def chunks() -> Any:
        yield b"abc"
    with client(tmp_path) as c:
        r = c.put("/api/studio/uploads", params={"name": "x.pcap"}, content=chunks())
        assert r.status_code == 411 and r.json()["error"] == "length_required", r.text
    assert uploads(tmp_path) == []


def test_uploads_max(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        for i in range(8):
            assert upload(c, f"{i}.json", b"[]").status_code == 200
        r = upload(c, "9.json", b"[]")
        assert r.status_code == 409 and r.json()["error"] == "too_many", r.text
    assert len(uploads(tmp_path)) == 8


def test_pcap_options_refused_before_the_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_worker(*a: Any, **kw: Any) -> Any:
        raise AssertionError("the worker ran")
    with client(tmp_path) as c:
        up = upload(c, "t.pcap", pcap_bytes([udp_frame()])).json()
        monkeypatch.setattr(trexapi, "import_pcap", no_worker)
        monkeypatch.setattr(trexapi, "load_profile", no_worker)
        for body in [{"pcap": pcap_opts(speedup=0)}, {"pcap": pcap_opts(loop_count=-1)}, {"pcap": {}}, {}]:
            r = c.post("/api/studio/import", json={"upload_id": up["upload_id"], **body})
            assert r.status_code == 422 and r.json()["error"] == "bad_request", r.text
        r = c.post("/api/studio/import", json={"path": PROFILE, "pcap": pcap_opts()})      # pcap only
        assert r.status_code == 422 and r.json()["error"] == "bad_request", r.text
        r = c.post("/api/studio/import", json={"path": PROFILE, "upload_id": up["upload_id"]})
        assert r.status_code == 422 and r.json()["error"] == "bad_request", r.text      # exactly one
    assert uploads(tmp_path) == [up["upload_id"] + ".pcap"]


def test_append_pcap_renames_on_collision(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        up = upload(c, "t.pcap", pcap_bytes([udp_frame()] * 2)).json()
        e = c.post("/api/studio/import", json={"upload_id": up["upload_id"], "name": "Trace",
                                               "pcap": pcap_opts(name_prefix="p")}).json()
        rev = get_profile(c, e["slug"])["rev"]
        up = upload(c, "t.pcap", pcap_bytes([udp_frame()] * 2)).json()
        r = c.post("/api/studio/import", json={"upload_id": up["upload_id"],
                                               "pcap": pcap_opts(name_prefix="p"),
                                               "append_to": {"slug": e["slug"], "rev": rev}})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["stream_count"] == 4 and j["rev"] != rev and j["cleared"] == []
        assert j["renamed"] == {"p1": "p1_2", "p2": "p2_2"}, j
        names = c.get(f"/api/studio/profiles/{e['slug']}/streams").json()["names"]
        assert names == ["p1", "p2", "p1_2", "p2_2"]
        rows = c.get(f"/api/studio/profiles/{e['slug']}/streams").json()["rows"]
        assert rows[3]["stream"]["next"] == "p1_2"          # the appended loop follows its own rename
        assert uploads(tmp_path) == []
        up = upload(c, "t.pcap", pcap_bytes([udp_frame()])).json()
        r = c.post("/api/studio/import", json={"upload_id": up["upload_id"], "pcap": pcap_opts(),
                                               "append_to": {"slug": e["slug"], "rev": rev}})
        assert r.status_code == 409 and r.json()["error"] == "stale" and r.json()["rev"] == j["rev"]
        assert uploads(tmp_path) == [up["upload_id"] + ".pcap"]


# ---- exports


def test_export_json_is_the_library_file(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        import_bench(c)
        r = c.get("/api/studio/profiles/udp-for-benchmarks/export", params={"format": "json"})
        assert r.status_code == 200
        assert r.content == (lib_dir(tmp_path) / "udp-for-benchmarks.json").read_bytes()
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["content-disposition"] == 'attachment; filename="udp-for-benchmarks.json"'


def test_export_yaml_and_py_attachments(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        import_bench(c)
        streams = json.loads((lib_dir(tmp_path) / "udp-for-benchmarks.json").read_text())
        r = c.get("/api/studio/profiles/udp-for-benchmarks/export", params={"format": "yaml"})
        assert r.status_code == 200 and yaml.safe_load(r.text) == streams
        assert r.headers["content-type"].startswith("application/yaml")
        assert r.headers["content-disposition"] == 'attachment; filename="udp-for-benchmarks.yaml"'
        r = c.get("/api/studio/profiles/udp-for-benchmarks/export", params={"format": "py"})
        assert r.status_code == 200 and "STLStream" in r.text, r.text
        assert r.headers["content-type"].startswith("text/x-python")
        assert r.headers["content-disposition"] == 'attachment; filename="udp-for-benchmarks.py"'
        r = c.get("/api/studio/profiles/udp-for-benchmarks/export", params={"format": "py", "stream": 0})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
        r = c.get("/api/studio/profiles/udp-for-benchmarks/export", params={"format": "txt"})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"


def test_export_of_a_profile_whose_file_is_gone_is_404(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.post("/api/studio/profiles", json={"name": "p"})
        c.post("/api/studio/profiles", json={"name": "q"})       # the library directory stays
        c.delete("/api/studio/profiles/p")
        for fmt in ("json", "yaml", "py"):
            r = c.get("/api/studio/profiles/p/export", params={"format": fmt})
            assert r.status_code == 404 and r.json()["error"] == "not_found", (fmt, r.text)


def test_export_one_stream_yaml(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.post("/api/studio/profiles", json={"name": "p"})
        rev = get_profile(c, "p")["rev"]
        c.put("/api/studio/profiles/p/streams", json={"rev": rev, "added": [burst("a"), burst("b", "a")]})
        r = c.get("/api/studio/profiles/p/export", params={"format": "yaml", "stream": 1})
        assert r.status_code == 200 and yaml.safe_load(r.text) == [burst("b", "a")], r.text
        assert r.headers["content-disposition"] == 'attachment; filename="p-1.yaml"'
        r = c.get("/api/studio/profiles/p/export", params={"format": "json", "stream": 0})
        assert r.status_code == 200 and json.loads(r.text) == [burst("a")]
        assert r.headers["content-disposition"] == 'attachment; filename="p-0.json"'
        r = c.get("/api/studio/profiles/p/export", params={"format": "yaml", "stream": 2})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"


def test_export_refuses_a_hand_written_stream_with_code_in_it(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """A library JSON dropped in by hand with code as `action_count` (written unquoted by `dump_to_code`) is
    refused by every export that converts it, before any worker; the raw JSON export is the file's own bytes
    (final review C1)."""
    lib_dir(tmp_path).mkdir()
    bad = dict(RECORDED, action_count="__import__('os').getpid()")
    (lib_dir(tmp_path) / "hand.json").write_text(json.dumps([RECORDED, bad]))

    async def no_export(streams: Any, timeout_s: float = 0) -> str:
        """Fails the test: the export reached the worker."""
        raise AssertionError("to_python was called")
    with client(tmp_path) as c:
        monkeypatch.setattr(trexapi, "to_python", no_export)
        url = "/api/studio/profiles/hand/export"
        for params in ({"format": "py"}, {"format": "yaml"}, {"format": "yaml", "stream": 0},
                       {"format": "json", "stream": 1}):
            r = c.get(url, params=params)
            assert r.status_code == 422, (params, r.text)
            assert r.json() == {"error": "invalid_stream", "message": r.json()["message"], "index": 1,
                                "field": "action_count"}, params
        r = c.get(url, params={"format": "json"})
        assert r.status_code == 200 and r.content == (lib_dir(tmp_path) / "hand.json").read_bytes()


# ---- simulate, template


def test_simulate_reply_shape(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        import_bench(c)
        r = c.post("/api/studio/profiles/udp-for-benchmarks/simulate")
        assert r.status_code == 200, r.text
        j = r.json()
    assert set(j) == {"round_trip", "streams", "totals", "chains", "unreachable", "notes"}
    assert j["round_trip"] == {"equal": True, "differences": []}
    assert j["streams"][0]["length"] == 128 and j["streams"][0]["index"] == 0


def test_template_is_a_valid_stream(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.get("/api/studio/template")
        assert r.status_code == 200 and len(base64.b64decode(r.json()["packet"]["binary"])) == 60, r.text
        c.post("/api/studio/profiles", json={"name": "p"})
        rev = get_profile(c, "p")["rev"]
        r = c.put("/api/studio/profiles/p/streams", json={"rev": rev, "added": [r.json()]})
        assert r.status_code == 200, r.text


def test_worker_calls_do_not_hold_the_library_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(streams: Any, timeout_s: float = 0) -> str:
        await asyncio.sleep(1.0)
        return "# code\n"
    with client(tmp_path) as c:
        import_bench(c)
        monkeypatch.setattr(trexapi, "to_python", slow)
        with ThreadPoolExecutor(max_workers=2) as ex:
            export = ex.submit(c.get, "/api/studio/profiles/udp-for-benchmarks/export",
                               params={"format": "py"})
            time.sleep(0.2)                       # the export is inside the worker call by now
            t0 = time.monotonic()
            r = c.get("/api/studio/profiles")
            took = time.monotonic() - t0
            assert r.status_code == 200 and not export.done()
            assert export.result().text == "# code\n"
    assert took < 0.5, took


# ---- the file browser


def test_fs_list_include_pcap(tmp_path: Path) -> None:
    for n in ("a.py", "b.pcap", "c.CAP", "d.txt"):
        (tmp_path / n).write_text("")
    with client(tmp_path) as c:
        plain = c.get("/api/fs/list", params={"path": str(tmp_path)}).json()
        assert [e["name"] for e in plain["entries"] if not e["dir"]] == ["a.py"]
        both = c.get("/api/fs/list", params={"path": str(tmp_path), "include_pcap": True}).json()
        assert [e["name"] for e in both["entries"] if not e["dir"]] == ["a.py", "b.pcap", "c.CAP"]


# ---- the bridge to Control (spec §8)


def _streams_on_port_0(foreign_client: Any, f: Any) -> list[dict[str, Any]]:
    """The streams the fake holds on port 0's default profile, read by a second client."""
    b = foreign_client(f)
    data = b.ports[0].transmit("get_all_streams", {"port_id": 0, "profile_id": "*"}).data()
    return list(data["profiles"].get("_", {}).values())


def test_library_json_assigns_on_the_fake(tmp_path: Path, fake_trex: Any, foreign_client: Any) -> None:
    f = fake_trex()
    with client(tmp_path) as c:
        e = import_bench(c)
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        r = c.post("/api/ports/assign", json={"ports": [0], "path": e["path"]})
        assert r.status_code == 200 and r.json()["stream_count"] == 1, r.text
        streams = _streams_on_port_0(foreign_client, f)
    assert len(streams) == 1 and len(base64.b64decode(streams[0]["packet"]["binary"])) == 124


def test_library_chain_assigns_on_the_fake(tmp_path: Path, fake_trex: Any, foreign_client: Any) -> None:
    f = fake_trex()
    with client(tmp_path) as c:
        e = c.post("/api/studio/profiles", json={"name": "Chain"}).json()
        rev = get_profile(c, "chain")["rev"]
        second = dict(burst("b"), self_start=False)
        r = c.put("/api/studio/profiles/chain/streams", json={"rev": rev, "added": [burst("a", "b"), second]})
        assert r.status_code == 200, r.text
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0]}).status_code == 200
        r = c.post("/api/ports/assign", json={"ports": [0], "path": e["path"]})
        assert r.status_code == 200 and r.json()["stream_count"] == 2, r.text
        assert len(_streams_on_port_0(foreign_client, f)) == 2


def test_assign_of_a_library_json_takes_the_larger_result_cap(tmp_path: Path, fake_trex: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    caps: list[tuple[str, int]] = []

    async def record(path: str, ports: list[int], tunables: list[str], timeout_s: float = 10.0, *,
                     max_result: int = trexapi.profiles.MAX_RESULT) -> Any:
        caps.append((os.path.basename(path), max_result))
        raise trexapi.ProfileError("recorded")
    f = fake_trex()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.json").write_text("[]")
    with client(tmp_path) as c:
        e = c.post("/api/studio/profiles", json={"name": "Lib"}).json()
        connect(c, f)
        assert c.post("/api/ports/acquire", json={"ports": [0, 1]}).status_code == 200
        monkeypatch.setattr(trexapi, "load_profile", record)
        # the library entry, also through a path that resolves to it by another spelling
        via = str(lib_dir(tmp_path) / ".." / "profiles" / "lib.json")
        for path in (e["path"], via, str(outside / "x.json"), PROFILE):
            r = c.post("/api/ports/assign", json={"ports": [0, 1], "path": path})
            assert r.status_code == 422 and "recorded" in r.json()["message"], r.text
            r = c.post("/api/profiles/inspect", json={"path": path, "ports": [0, 1]})
            assert r.status_code == 422 and "recorded" in r.json()["message"], r.text
    big = trexapi.MAX_RESULT_PCAP * 2
    small = trexapi.profiles.MAX_RESULT
    assert caps == [("lib.json", big)] * 4 + [("x.json", small)] * 2 + [("udp_for_benchmarks.py", small)] * 2
