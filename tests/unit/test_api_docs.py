"""docs/api.md, the API reference built from the app's own OpenAPI description by scripts/gen_api_docs.py
(`make api-docs`). trex-web-gui serves no OpenAPI page on purpose (P3c spec §6.0, the token boundary:
tests/contract/test_auth.py::test_docs_and_openapi_are_absent pins /docs, /redoc and /openapi.json at 404 for
an authenticated client, so this file does not repeat that test); the reference is generated at build time
instead, and these tests keep it from drifting from the routes."""
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from fastapi.routing import APIRoute
from support import ROOT
from trex_web_gui.api.app import create_app

SCRIPT = ROOT / "scripts" / "gen_api_docs.py"
DOC = ROOT / "docs" / "api.md"


def load_script() -> ModuleType:
    """The generator imported as a module (it imports nothing but the standard library at module level)."""
    spec = importlib.util.spec_from_file_location("gen_api_docs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_docs_are_current() -> None:
    """`gen_api_docs.py --check` exits 0 against the committed docs/api.md."""
    p = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--check"], cwd=ROOT, capture_output=True,
                       text=True, check=False)
    assert p.returncode == 0, p.stdout + p.stderr


def test_api_docs_cover_every_route(tmp_path: Path) -> None:
    """Every APIRoute path and method of create_app(tmp_path) is a `### METHOD /path` heading, and the
    WebSocket (which OpenAPI does not describe) is named in the preamble."""
    text = DOC.read_text()
    headings = {ln[4:] for ln in text.splitlines() if ln.startswith("### ")}
    want = {f"{m} {r.path}" for r in create_app(tmp_path).routes if isinstance(r, APIRoute)
            for m in r.methods}
    assert want <= headings, sorted(want - headings)
    assert "/api/ws" in text.split("\n## /api/", 1)[0]


def test_every_route_has_a_description(tmp_path: Path) -> None:
    """The generator refuses a route whose docstring is empty, naming it (the reference is built from route
    docstrings); and none of the app's routes is one."""
    gen = load_script()
    app = create_app(tmp_path)
    assert gen.undescribed(app.openapi()) == []
    spec = {"paths": {"/api/x": {"get": {"description": " \n"}, "post": {"description": "Does x."}}}}
    assert gen.undescribed(spec) == ["GET /api/x"]


def test_check_reports_a_stale_file(tmp_path: Path) -> None:
    """`check` returns 0 for the current file, and 1 with a diff of at most 40 lines for a stale one."""
    gen = load_script()
    current = tmp_path / "api.md"
    current.write_text(DOC.read_text())
    assert gen.check(current) == (0, "")
    current.write_text(DOC.read_text().replace("# ", "## ", 1) + "\n".join(f"extra {i}" for i in range(100)))
    rc, diff = gen.check(current)
    assert rc == 1 and diff.startswith("--- ") and len(diff.splitlines()) <= 40
