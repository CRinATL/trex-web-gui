"""HTTP responses are gzip-compressed when the client accepts it (P3b follow-up 5: the browser demo found the
WebSocket negotiating permessage-deflate while HTTP went uncompressed)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support import authed
from trex_web_gui.api.app import create_app

BUNDLE = "export const rows = [" + ", ".join(f'"row {i}"' for i in range(2000)) + "];\n"


@pytest.fixture
def client(tmp_path: Path):
    """Yield a logged-in TestClient, entered (the lifespan runs), on an app serving a static dir under
    `tmp_path` with a large assets/app.js (BUNDLE). Teardown exits the client context (lifespan shutdown)."""
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>trex-web-gui</title>")
    (static / "assets" / "app.js").write_text(BUNDLE)
    with authed(create_app(tmp_path / "data", static_dir=static)) as c:
        yield c


def test_static_asset_is_gzipped_when_accepted(client: TestClient) -> None:
    r = client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    assert r.text == BUNDLE                                   # httpx decoded it
    assert int(r.headers["content-length"]) < len(BUNDLE) // 4


def test_not_compressed_without_accept_encoding(client: TestClient) -> None:
    r = client.get("/assets/app.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers
    assert r.text == BUNDLE
