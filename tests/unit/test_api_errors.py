"""The `answers` declaration helper (trex_web_gui.api.declared) and its rendering by
scripts/gen_api_docs.py."""
import importlib.util
from types import ModuleType

from fastapi import FastAPI
from support import ROOT
from trex_web_gui.api.declared import answers


def load_generator() -> ModuleType:
    """scripts/gen_api_docs.py imported as a module, as tests/unit/test_api_docs.py does."""
    spec = importlib.util.spec_from_file_location("gen_api_docs", ROOT / "scripts" / "gen_api_docs.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declared() -> dict[int | str, dict[str, object]]:
    """The mixed set of declarations both tests use: codes, families and a code-less 304."""
    return answers((409, "exists", "the name is taken"), (404, "not_found", "no such template"),
                   (409, "session"), (422, "profile_error", "the profile does not load"), (422, "bundle"),
                   (304, "", "the ETag matched"))


def test_answers_builds_responses_with_codes_and_families() -> None:
    r = declared()
    assert r[409]["x-codes"] == [["exists", "the name is taken"]]
    assert r[409]["x-families"] == ["session"]
    assert r[404]["x-codes"] == [["not_found", "no such template"]]
    assert r[404]["x-families"] == []
    assert r[304]["x-codes"] == [["", "the ETag matched"]]
    assert "the name is taken" in str(r[409]["description"])


def test_generator_renders_declared_codes_and_families() -> None:
    app = FastAPI()

    @app.get("/api/x/y", responses=declared())  # type: ignore[arg-type]
    def route() -> dict[str, str]:
        """Does y."""
        return {}

    gen = load_generator()
    spec = app.openapi()
    op = spec["paths"]["/api/x/y"]["get"]
    lines = gen.route_section("/api/x/y", "get", op, spec.get("components", {}).get("schemas", {}))
    assert "- `404` `not_found`: no such template" in lines
    assert "- `409` `exists`: the name is taken" in lines
    assert "- `409`: a session refusal, see Errors" in lines
    assert "- `422` `profile_error`: the profile does not load" in lines
    assert "- `422`: a bundle refusal, see Errors" in lines
    assert "- `304`: the ETag matched" in lines
    default_422 = next(ln for ln in lines if ln.startswith("- `422`") and "bad_request" in ln)
    own_422 = "- `422` `profile_error`: the profile does not load"
    assert lines.index(default_422) < lines.index(own_422)
    not_found, exists = "- `404` `not_found`: no such template", "- `409` `exists`: the name is taken"
    assert lines.index(not_found) < lines.index(exists)
