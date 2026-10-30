import json
import os
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from test_api import WS, client, recv
from trex_web_gui.api import bodies
from trex_web_gui.api.bundle import BODY_LIMIT

SETUP = {"name": "Lab A", "server": None, "dashboard": None,
         "ports": [{"key": "#0", "port": 0, "profile": {"path": "/p/udp.py", "kind": "py"},
                    "tunables": {"values": {}, "extra": ""}, "mult": "10kpps", "duration": -1,
                    "force": False}]}
MODES = {"preferences": "replace", "connections": "replace", "port_labels": "replace",
         "test_setups": "replace"}


def populate(c: Any) -> None:
    """Settings, a label and a setup through the API."""
    c.put("/api/settings", json={"preferences": {"wireshark_path": "/ws"},
                                 "connections": [{"name": "lab", "host": "h"}]})
    c.put("/api/port-labels", json={"server": "h:4501", "key": "#0", "name": "up", "description": ""})
    assert c.put("/api/test-setups/lab-a", json=SETUP).status_code == 200


def test_export_wipe_import_round_trip(tmp_path: Path) -> None:
    with client(tmp_path / "a") as c:
        populate(c)
        exported = c.get("/api/settings/bundle").json()
        assert exported["version"] == 1
    with client(tmp_path / "b") as c:
        pv = c.post("/api/settings/bundle/preview", json=exported)
        assert pv.status_code == 200 and pv.json()["sections"]["test_setups"]["adds"] == ["lab-a"]
        r = c.post("/api/settings/bundle/apply", json={"bundle": exported, "modes": MODES})
        assert r.status_code == 200 and r.json()["written"] == list(MODES)
        assert c.get("/api/settings/bundle").json()["sections"] == exported["sections"]


def test_preview_and_apply_publish_state(tmp_path: Path) -> None:
    with client(tmp_path / "a") as c:
        populate(c)
        exported = c.get("/api/settings/bundle").json()
    with client(tmp_path / "b") as c, c.websocket_connect(WS) as ws:
        for _ in range(8):
            recv(ws)
        assert c.post("/api/settings/bundle/preview", json=exported).status_code == 200
        applied = c.post("/api/settings/bundle/apply", json={"bundle": exported, "modes": MODES})
        assert applied.status_code == 200
        got = {m["type"]: m for m in (recv(ws) for _ in range(3))}
        assert set(got) == {"settings", "port_labels", "test_setups"}
        assert got["settings"]["settings"]["connections"][0]["name"] == "lab"
        assert "data_dir" in got["settings"]
        assert got["port_labels"]["labels"]["h:4501"]["#0"]["name"] == "up"
        assert [s["slug"] for s in got["test_setups"]["setups"]] == ["lab-a"]


def test_version_2_is_422(tmp_path: Path) -> None:
    bundle = {"format": "trex-web-gui-settings", "version": 2, "sections": {}}
    with client(tmp_path) as c:
        for url, body in (("preview", bundle), ("apply", {"bundle": bundle, "modes": {}})):
            r = c.post(f"/api/settings/bundle/{url}", json=body)
            assert r.status_code == 422 and r.json()["error"] == "unsupported_bundle"
            assert "bundle version 2 is not supported by this trex-web-gui (expects 1)" in r.json()["message"]


def test_invalid_section_is_422_naming_it_and_write_error_is_500(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = {"format": "trex-web-gui-settings", "version": 1,
              "sections": {"connections": [{"name": "n", "host": "h"}], "port_labels": {"s": {"zz": {}}}}}
    with client(tmp_path) as c:
        r = c.post("/api/settings/bundle/apply", json={"bundle": bundle, "modes": {
            "connections": "replace", "port_labels": "replace"}})
        assert r.status_code == 422 and r.json()["error"] == "invalid_bundle"
        assert "key" in r.json()["message"]
        assert not (tmp_path / "settings.toml").exists()
        sideways = {"bundle": bundle, "modes": {"connections": "sideways"}}
        r = c.post("/api/settings/bundle/apply", json=sideways)
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
        r = c.post("/api/settings/bundle/apply", json={"nope": 1})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
        ok = {**bundle, "sections": {"connections": bundle["sections"]["connections"],
                                     "test_setups": [SETUP]}}
        real = os.replace

        def replace(src: Any, dst: Any) -> None:
            if str(dst).endswith("lab-a.json"):
                raise OSError("disk full")
            real(src, dst)
        monkeypatch.setattr(os, "replace", replace)
        r = c.post("/api/settings/bundle/apply", json={"bundle": ok, "modes": {
            "connections": "replace", "test_setups": "replace"}})
        assert r.status_code == 500 and r.json()["error"] == "storage_error"
        assert r.json()["written"] == ["connections"] and r.json()["failed"] == "test_setups/lab-a"


def test_over_4mib_is_413_before_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[int] = []
    yielded: list[int] = []

    class Spy:
        def loads(self, data: bytes) -> object:
            """Record the call, then parse."""
            loads.append(len(data))
            return json.loads(data)
    monkeypatch.setattr(bodies, "json", Spy())

    async def stream(self: Request) -> Any:
        # a declared length under the cap with a longer body: 4 MiB, then 1 byte, then more
        for n in (BODY_LIMIT, 1, BODY_LIMIT):
            yielded.append(n)
            yield b"x" * n
    monkeypatch.setattr(Request, "stream", stream)
    with client(tmp_path) as c:
        for url, body in (("preview", b"{}"), ("apply", b"{}")):
            yielded.clear()
            r = c.post(f"/api/settings/bundle/{url}", content=body,
                       headers={"Content-Type": "application/json"})
            assert r.status_code == 413 and r.json()["error"] == "too_large"
            assert yielded == [BODY_LIMIT, 1]          # the read stopped at 4 MiB + 1 bytes
            assert loads == []
        yielded.clear()
        monkeypatch.undo()                                   # positive control: the spy is the one called
        monkeypatch.setattr(bodies, "json", Spy())
        r = c.post("/api/settings/bundle/preview", json={"format": "x"})
        assert r.status_code == 422 and len(loads) == 1
    monkeypatch.undo()
    with client(tmp_path) as c:                         # a declared length over the cap is refused too
        r = c.post("/api/settings/bundle/preview", content=b"x" * (BODY_LIMIT + 1))
        assert r.status_code == 413 and r.json()["error"] == "too_large"


def test_missing_content_length_is_411(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        for url in ("preview", "apply"):
            r = c.post(f"/api/settings/bundle/{url}", content=iter([b"{}"]))
            assert r.status_code == 411 and r.json()["error"] == "length_required"
