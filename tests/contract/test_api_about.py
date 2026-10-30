"""GET /api/about."""
from pathlib import Path

from test_api import client


def test_about_names_host(tmp_path: Path) -> None:
    with client(tmp_path) as c:
        host = c.get("/api/about").json()["host"]
    assert isinstance(host, str) and host.strip()
