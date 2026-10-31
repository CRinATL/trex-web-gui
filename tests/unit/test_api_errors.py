"""The `answers` declaration helper (trex_web_gui.api.declared) and its rendering by
scripts/gen_api_docs.py."""
import importlib.util
import re
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI
from support import ROOT
from trex_web_gui.api.declared import answers, family_line
from trex_web_gui.api.refusals import REFUSALS


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


# --- the refusal table -------------------------------------------------------------------------------------

PKG = ROOT / "backend" / "trex_web_gui"
# A refusal raised with a computed code, `file:line -> reason`. None exist; add an entry only with a reason.
NON_LITERAL_ALLOWED: dict[str, str] = {}


def load_checker() -> ModuleType:
    """scripts/check_api_errors.py imported as a module (standard library only at module level)."""
    path = ROOT / "scripts" / "check_api_errors.py"
    spec = importlib.util.spec_from_file_location("check_api_errors", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refusal_table_matches_every_raise_site() -> None:
    raised = {k for k in load_checker().refusal_sites(PKG) if k[1] != "<non-literal>"}
    table = set(REFUSALS)
    assert table == raised, (f"raised but not in REFUSALS: {sorted(raised - table)}; "
                             f"in REFUSALS but never raised: {sorted(table - raised)}")


def test_no_refusal_has_a_computed_code() -> None:
    found = load_checker().refusal_sites(PKG)
    sites = [s for (_, code), where in found.items() if code == "<non-literal>" for s in where]
    loose = [s for s in sites if s not in NON_LITERAL_ALLOWED]
    assert not loose, f"refusals raised with a computed code (allow-list them with a reason): {loose}"


def test_refusal_sites_reads_positional_keyword_and_attribute_calls(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        'def f():\n'
        '    raise CommandRefused("a", "m")\n'
        'def g():\n'
        '    raise trexapi.CommandRefused(code="b", message="m")\n'
        'def h():\n'
        '    raise DaemonRefused(x, "m")\n')
    sites = load_checker().refusal_sites(tmp_path)
    assert sites == {("session", "a"): ["m.py:2"], ("session", "b"): ["m.py:4"],
                     ("daemon", "<non-literal>"): ["m.py:6"]}


def test_refusal_table_statuses_follow_the_mapping_sites() -> None:
    for (family, code), (status, meaning) in REFUSALS.items():
        assert family in ("session", "daemon", "bundle") and meaning, code
        want = 422 if family == "bundle" or code == "bad_request" else 409
        assert status == want, code


def test_api_md_errors_section_lists_every_refusal() -> None:
    text = (ROOT / "docs" / "api.md").read_text()
    errors = text.split("## Errors", 1)[1].split("## WebSocket", 1)[0]
    for (family, code), (status, _) in REFUSALS.items():
        assert re.search(rf"^\| `{code}` \| {family} \| {status} \| ", errors, re.M), code


def test_generator_family_text_matches_declared_family_line() -> None:
    gen = load_generator()
    for family in ("session", "daemon", "bundle"):
        assert gen.FAMILY_TEXT[family] == family_line(family)


def test_errors_lead_sentence_admits_the_plain_text_400() -> None:
    errors = (ROOT / "docs" / "api.md").read_text().split("## Errors", 1)[1]
    assert "Every error except the trusted-host 400 is JSON" in errors
