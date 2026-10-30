import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from test_api import WS, client, recv
from trex_web_gui.store import test_setups as ts

BODY: dict[str, Any] = {
    "name": "Lab A", "server": None, "dashboard": None,
    "ports": [{"key": "#0", "port": 0, "profile": {"path": "/p/udp.py", "kind": "py"},
               "tunables": {"values": {}, "extra": ""}, "mult": "10kpps", "duration": -1, "force": False}]}


def hello(ws: Any) -> list[str]:
    """The eight hello messages' types."""
    return [recv(ws)["type"] for _ in range(8)]


def test_put_get_list_delete(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        assert c.get("/api/test-setups").json() == {"setups": [], "load_errors": []}
        r = c.put("/api/test-setups/lab-a", json=BODY)
        assert r.status_code == 200 and r.json()["slug"] == "lab-a" and r.json()["updated"].endswith("Z")
        assert c.get("/api/test-setups/lab-a").json() == r.json()
        assert c.get("/api/test-setups").json() == {"setups": [r.json()], "load_errors": []}
        assert c.delete("/api/test-setups/lab-a").json() == {"setups": [], "load_errors": []}
        assert c.get("/api/test-setups/lab-a").json()["error"] == "not_found"
        d = c.delete("/api/test-setups/lab-a")
        assert d.status_code == 404 and d.json()["error"] == "not_found"


def test_put_slug_mismatch_is_422(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        r = c.put("/api/test-setups/other", json=BODY)
        assert r.status_code == 422 and r.json()["error"] == "invalid_setup"
        assert "slug does not match name" in r.json()["message"]
        assert not (tmp_path / "test_setups").exists()


def test_put_invalid_is_422_and_writes_nothing(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        bad = {**BODY, "ports": [{**BODY["ports"][0], "mult": "fast"}]}
        r = c.put("/api/test-setups/lab-a", json=bad)
        assert r.status_code == 422 and r.json()["error"] == "invalid_setup"
        assert "ports[0].mult" in r.json()["message"]
        r = c.put("/api/test-setups/lab-a", content=b"{nope", headers={"Content-Type": "application/json"})
        assert r.status_code == 422 and r.json()["error"] == "bad_request"
        assert not (tmp_path / "test_setups").exists()


def test_rename_and_duplicate(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.put("/api/test-setups/lab-a", json=BODY)
        r = c.post("/api/test-setups/lab-a/duplicate", json={"name": "Lab B"})
        assert r.status_code == 200 and r.json()["slug"] == "lab-b"
        assert c.post("/api/test-setups/lab-a/duplicate", json={"name": "lab b"}).status_code == 409
        r = c.post("/api/test-setups/lab-a/rename", json={"name": "lab b"})
        assert r.status_code == 409 and r.json()["error"] == "exists"
        r = c.post("/api/test-setups/lab-a/rename", json={"name": "Lab C"})
        assert r.status_code == 200 and r.json()["slug"] == "lab-c"
        assert [s["slug"] for s in c.get("/api/test-setups").json()["setups"]] == ["lab-b", "lab-c"]
        assert c.post("/api/test-setups/lab-a/rename", json={"name": "X"}).json()["error"] == "not_found"
        r = c.post("/api/test-setups/lab-c/rename", json={"name": "!!!"})
        assert r.status_code == 422 and r.json()["error"] == "invalid_setup"


@pytest.mark.parametrize("slug", ["%2e%2e", "A", "a--b", "-a"])
def test_route_slug_outside_slug_re_is_404(tmp_path: Path, slug: str) -> None:
    d = tmp_path / "test_setups"
    d.mkdir()
    # the files the bad slugs would reach if the gate were missing
    planted = [d / "A.json", d / "a--b.json", d / "-a.json", tmp_path / "x.json"]
    for p in planted:
        p.write_text(json.dumps(BODY))
    before = {p: p.read_text() for p in planted}
    with client(tmp_path) as c:
        for r in (c.get(f"/api/test-setups/{slug}"), c.put(f"/api/test-setups/{slug}", json=BODY),
                  c.delete(f"/api/test-setups/{slug}"),
                  c.post(f"/api/test-setups/{slug}/rename", json={"name": "x"}),
                  c.post(f"/api/test-setups/{slug}/duplicate", json={"name": "x"})):
            assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert {p: p.read_text() for p in planted} == before
    assert sorted(p.name for p in d.iterdir()) == ["-a.json", "A.json", "a--b.json"]


def test_rename_at_the_cap_is_200(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ts, "SETUPS_MAX", 2)
    with client(tmp_path) as c:
        c.put("/api/test-setups/lab-a", json=BODY)
        c.put("/api/test-setups/lab-b", json={**BODY, "name": "Lab B"})
        assert c.put("/api/test-setups/lab-c", json={**BODY, "name": "Lab C"}).json()["error"] == "too_many"
        r = c.post("/api/test-setups/lab-a/rename", json={"name": "Lab D"})
        assert r.status_code == 200 and r.json()["slug"] == "lab-d"


def test_put_over_256kib_is_413(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        big = {**BODY, "pad": "x" * (256 * 1024)}
        r = c.put("/api/test-setups/lab-a", json=big)
        assert r.status_code == 413 and r.json()["error"] == "too_large"
        # a declared length under the cap does not excuse a longer body
        r = c.put("/api/test-setups/lab-a", content=b"[" + b"0," * (200 * 1024) + b"0]",
                  headers={"Content-Length": "10", "Content-Type": "application/json"})
        assert r.status_code == 413 and r.json()["error"] == "too_large"
        assert not (tmp_path / "test_setups").exists()


def test_put_without_content_length_is_411(tmp_path: Path) -> None:
    def chunks() -> Any:
        yield b'{"name": "x"}'
    with client(tmp_path) as c:
        r = c.put("/api/test-setups/lab-a", content=chunks())
        assert r.status_code == 411 and r.json()["error"] == "length_required"


def test_body_cap_counts_bytes_actually_read(tmp_path: Path) -> None:
    import asyncio

    from starlette.requests import Request
    from trex_web_gui.api import bodies

    async def run() -> None:
        sent = [b"x" * 600, b"y" * 600, b"z"]
        seen: list[int] = []

        async def receive() -> dict[str, Any]:
            seen.append(1)
            return {"type": "http.request", "body": sent[len(seen) - 1], "more_body": len(seen) < 3}
        req = Request({"type": "http", "headers": [(b"content-length", b"5")], "method": "PUT"}, receive)
        with pytest.raises(bodies.BodyRefused) as e:
            await bodies.read_json_capped(req, 1000)
        assert e.value.response.status_code == 413
        assert len(seen) == 2                  # stopped before the third chunk
    asyncio.run(run())


def put_published(c: TestClient, ws: Any) -> None:
    """PUT BODY and check the next message is the full list."""
    r = c.put("/api/test-setups/lab-a", json=BODY)
    m = recv(ws)
    assert m == {"type": "test_setups", "setups": [r.json()], "load_errors": []}


def test_ws_hello_sends_test_setups_after_capture(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        c.put("/api/test-setups/lab-a", json=BODY)
        with c.websocket_connect(WS) as ws:
            types = hello(ws)
            assert types.index("port_labels") < types.index("capture") < types.index("test_setups")
            assert types[-2:] == ["test_setups", "daemon"]


def test_put_is_published_to_an_open_websocket(tmp_path: Path) -> None:
    with client(tmp_path) as c, c.websocket_connect(WS) as ws:
        hello(ws)
        put_published(c, ws)
        c.post("/api/test-setups/lab-a/duplicate", json={"name": "Lab B"})
        assert [s["slug"] for s in recv(ws)["setups"]] == ["lab-a", "lab-b"]
        c.post("/api/test-setups/lab-b/rename", json={"name": "Lab C"})
        assert [s["slug"] for s in recv(ws)["setups"]] == ["lab-a", "lab-c"]
        c.delete("/api/test-setups/lab-c")
        assert [s["slug"] for s in recv(ws)["setups"]] == ["lab-a"]
