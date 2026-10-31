"""The `answers` declaration helper (trex_web_gui.api.declared) and its rendering by
scripts/gen_api_docs.py."""
import importlib.util
import re
import textwrap
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import APIRouter, FastAPI
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
        '    raise DaemonRefused(x, "m")\n'
        'def i():\n'
        '    raise BundleError()\n')
    sites = load_checker().refusal_sites(tmp_path)
    assert sites == {("session", "a"): ["m.py:2"], ("session", "b"): ["m.py:4"],
                     ("daemon", "<non-literal>"): ["m.py:6"], ("bundle", "<non-literal>"): ["m.py:8"]}


def test_refusal_sites_skips_vendored_code(tmp_path: Path) -> None:
    (tmp_path / "_vendor").mkdir()
    (tmp_path / "_vendor" / "x.py").write_text('raise CommandRefused("zz", "m")\n')
    assert ("session", "zz") not in load_checker().refusal_sites(tmp_path)


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


# --- the route checker -------------------------------------------------------------------------------------

# The imports every synthetic module starts with, as the real api/ modules spell them.
HEAD = ("import asyncio\n"
        "import functools\n"
        "from fastapi import APIRouter\n"
        "from fastapi.responses import FileResponse, JSONResponse, Response\n"
        "from trex_web_gui import trexapi\n"
        "from trex_web_gui.api.auth import token_matches\n"
        "from trex_web_gui.api.bodies import BodyRefused, err, read_json_capped\n"
        "from trex_web_gui.api.studio import UPLOAD_ID_RE, upload_path\n"
        "from trex_web_gui.studio import bundles\n"
        "from trex_web_gui.daemon import DaemonRefused\n"
        "from trex_web_gui.trexapi import CommandRefused\n")
READ_JSON_CAPPED = {(411, "length_required"), (413, "too_large"), (422, "bad_request")}


def reach(body: str, handler: str = "h") -> object:
    """`reachable` of `handler` in HEAD + `body`, with the checker's own SHARED."""
    checker = load_checker()
    return checker.reachable(HEAD + textwrap.dedent(body), handler, checker.SHARED)


def why(result: object) -> str:
    """The reason of an Unresolved result; fails the test on a set of pairs."""
    assert not isinstance(result, set), result
    return str(result.why)  # type: ignore[attr-defined]


def op(doc: str = "", **declared: list[str]) -> dict[str, object]:
    """An openapi() operation with docstring `doc` and declarations `s<status>=[code or family:<name>, ...]`
    (a code `""` for a code-less status), shaped as `answers` makes them."""
    responses: dict[str, object] = {"200": {"description": "Successful Response"}}
    for key, items in declared.items():
        codes = [[c, "meaning"] for c in items if not c.startswith("family:")]
        families = sorted(c.removeprefix("family:") for c in items if c.startswith("family:"))
        responses[key.removeprefix("s")] = {"description": "x", "x-codes": codes, "x-families": families}
    return {"description": doc, "responses": responses}


def test_literal_err_and_jsonresponse_are_found() -> None:
    assert reach('''
        def h(n: int):
            if n:
                return err(404, "not_found", "no such thing")
            if n > 1:
                return JSONResponse({"error": "stale", "message": "m"}, status_code=409)
            if n > 2:
                return Response(status_code=304)
            return JSONResponse({"ok": True})
        ''') == {(404, "not_found"), (409, "stale"), (304, "")}


def test_nested_helper_and_lambda_are_followed() -> None:
    assert reach('''
        def create_app(other):
            router = APIRouter()

            async def run(coro, ok):
                try:
                    result = await coro
                except trexapi.ProfileError as e:
                    return err(422, "profile_error", e.message)
                return ok(result)

            def unused():
                return err(418, "teapot", "never referenced")

            pick = lambda n: err(410, "gone", "m") if n else None

            @router.post("/x")
            async def h(n: int):
                pick(n)
                return await run(other.go(n), lambda r: r)
        ''') == {(422, "profile_error"), (410, "gone")}


def test_conditional_status_counts_both_branches() -> None:
    session = {(422, "family:session"), (409, "family:session")}
    for clause in ("except CommandRefused as e:", "except trexapi.CommandRefused as e:"):
        assert reach(f'''
            def create_app():
                async def run(coro):
                    try:
                        return await coro
                    {clause}
                        return err(422 if e.code == "bad_request" else 409, e.code, e.message)

                async def h(c):
                    return await run(c)
            ''') == session, clause
    assert reach('''
        async def h(b):
            try:
                return bundles.load(b)
            except bundles.BundleError as e:
                return err(422, e.code, str(e))
        ''') == {(422, "family:bundle")}
    assert reach('''
        async def h(make):
            try:
                return make()
            except DaemonRefused as e:
                return err(409, e.code, e.message)
        ''') == {(409, "family:daemon")}
    assert reach('''
        def h(n):
            return err(400 if n else 404, "missing", "m")
        ''') == {(400, "missing"), (404, "missing")}


def test_unpaired_operation_is_reported() -> None:
    spec = {"paths": {"/api/x/{name}": {"get": op(), "delete": op()}}}
    routes = {"GET /api/x/{name}": ("m.py", "h")}
    sources = {"m.py": HEAD + "def h(name):\n    return {}\n"}
    assert load_checker().problems(spec, routes, sources, REFUSALS, {}) == [
        "DELETE /api/x/{name}: cannot be followed: no handler"]


def test_routes_of_matches_openapi() -> None:
    app = FastAPI()
    router = APIRouter(prefix="/api/packet")

    @app.get("/api/top")
    def top() -> dict[str, str]:
        return {}

    @router.post("/templates")
    def make_template() -> dict[str, str]:
        return {}

    @router.get("/files/{rest:path}")
    def files(rest: str) -> dict[str, str]:
        return {}

    app.include_router(router, prefix="/v")
    found = load_checker().routes_of(app)
    assert set(found) == {f"{m.upper()} {p}" for p, ops in app.openapi()["paths"].items() for m in ops}
    assert found["GET /api/top"] == ("test_api_errors.py", "top")
    assert found["GET /v/api/packet/files/{rest}"] == ("test_api_errors.py", "files")


def test_helper_passed_by_reference_is_followed() -> None:
    src = '''
        def make():
            async def guarded(work):
                return await work()

            async def h(slug: str):
                async def go():
                    return err(404, "not_found", "m")
                return await guarded(go)

            async def h2(slug: str):
                def read(n):
                    return err(413, "too_large", "m")
                return await asyncio.to_thread(read, 1)
        '''
    assert reach(src) == {(404, "not_found")}
    assert reach(src, "h2") == {(413, "too_large")}


def test_nested_names_resolve_innermost_first() -> None:
    src = '''
        def go():
            return err(500, "outer", "m")

        def make():
            async def guarded(work):
                return await work()

            async def h():
                async def go():
                    return err(404, "first", "m")
                return await guarded(go)

            async def h2():
                async def go():
                    return err(409, "second", "m")
                return await guarded(go)

            async def h3():
                return await guarded(go)
        '''
    assert reach(src) == {(404, "first")}
    assert reach(src, "h2") == {(409, "second")}
    assert reach(src, "h3") == {(500, "outer")}


def test_decorator_and_shared_names_are_not_unresolved() -> None:
    src = '''
        router = APIRouter()

        @router.post("/login", responses=answers((401, "bad_token", "m")))
        async def h(request, token: str, extra=mystery()):
            body = await read_json_capped(request, 10)
            if token_matches(token):
                return {"ok": body}
            return err(401, "bad_token", "m")
        '''
    assert reach(src) == {(401, "bad_token")} | READ_JSON_CAPPED
    spec = {"paths": {"/login": {"post": op(s401=["bad_token"], s411=["length_required"], s413=["too_large"],
                                             s422=["bad_request"])}}}
    routes = {"POST /login": ("m.py", "h")}
    assert load_checker().problems(spec, routes, {"m.py": HEAD + textwrap.dedent(src)}, REFUSALS, {}) == []


def test_method_calls_and_parameters_are_data() -> None:
    assert reach('''
        def make(session):
            async def run(coro, ok):
                result = await coro
                return ok(result)

            async def h(p, e, result):
                if p:
                    return session().snapshot()
                if e:
                    return str(e)
                if result:
                    return await run(p, len)
                return FileResponse(p)
        ''') == set()


def test_body_refused_response_is_read_json_capped() -> None:
    src = '''
        def make():
            async def guarded(work):
                try:
                    return await work()
                except BodyRefused as e:
                    return e.response

            async def h():
                async def go():
                    return {"ok": True}
                return await guarded(go)

            async def h2(request):
                async def go():
                    return await read_json_capped(request, 10)
                return await guarded(go)

            async def h3():
                async def go():
                    raise BodyRefused(err(404, "not_found", "m"))
                return await guarded(go)

            async def h4(request):
                try:
                    return await request.json()
                except (BodyRefused, ValueError) as e:
                    return e.response

            async def h5(request):
                try:
                    return await read_json_capped(request, 10)
                except (ValueError, BodyRefused) as e:
                    return e.response
        '''
    assert reach(src) == set()
    assert reach(src, "h2") == READ_JSON_CAPPED
    assert reach(src, "h3") == {(404, "not_found")} | READ_JSON_CAPPED
    assert reach(src, "h4") == set()
    assert reach(src, "h5") == READ_JSON_CAPPED


def test_unresolvable_call_is_reported_not_skipped() -> None:
    assert "line" in why(reach('''
        def h(status_var):
            return err(status_var, "x", "m")
        '''))
    for body in ('def h(code):\n    return err(409, code, "m")\n',
                 'def h(c):\n    return JSONResponse({"error": c, "message": "m"}, status_code=409)\n',
                 'def h(s):\n    return Response(status_code=s)\n',
                 'def h():\n    try:\n        return 1\n    except ValueError as e:\n'
                 '        return err(409, e.code, "m")\n',
                 'def h():\n    return mystery()\n',
                 'def h(p):\n    return upload_path(p)\n',
                 'def h():\n    respond = err\n    return respond(404, "x", "m")\n',
                 'def h():\n    f = functools.partial(err, 404)\n    return f("x", "m")\n',
                 'def h(body):\n    return JSONResponse(body, status)\n',
                 'def h():\n    try:\n        return 1\n    except OSError as e:\n'
                 '        return e.response\n'):
        assert why(reach(body)), body
    spec = {"paths": {"/api/x": {"get": op()}}}
    sources = {"m.py": HEAD + 'def h(status_var):\n    return err(status_var, "x", "m")\n'}
    [line] = load_checker().problems(spec, {"GET /api/x": ("m.py", "h")}, sources, REFUSALS, {})
    assert line.startswith("GET /api/x: cannot be followed: "), line


def test_docstring_codes() -> None:
    src = HEAD + textwrap.dedent('''
        def a():
            return err(409, "exists", "m")

        def b():
            return {}

        def s(c):
            try:
                return c()
            except CommandRefused as e:
                return err(409, e.code, e.message)

        def d(c):
            try:
                return c()
            except DaemonRefused as e:
                return err(409, e.code, e.message)
        ''')
    spec = {"paths": {
        "/a": {"post": op("Creates it; 409 exists without overwrite.", s409=["exists"])},
        "/b": {"post": op("Creates it; 409 exists without overwrite.")},
        "/c": {"get": op("Reads port 4500, the first 200 bytes, or a 404.")},
        "/s": {"get": op("409 spool_unavailable without a capture directory.", s409=["family:session"])},
        "/d": {"get": op("409 not_connected when no daemon is attached.", s409=["family:daemon"])},
        "/e": {"get": op("409 spool_unavailable without a capture directory.", s409=["family:daemon"])}}}
    routes = {"POST /a": ("m.py", "a"), "POST /b": ("m.py", "b"), "GET /c": ("m.py", "b"),
              "GET /s": ("m.py", "s"), "GET /d": ("m.py", "d"), "GET /e": ("m.py", "d")}
    assert load_checker().problems(spec, routes, {"m.py": src}, REFUSALS, {}) == [
        "GET /e: 409 spool_unavailable named in the docstring but not declared",
        "POST /b: 409 exists named in the docstring but not declared"]


def test_problems_names_route_and_pair() -> None:
    src = HEAD + textwrap.dedent('''
        def h(n):
            if n:
                return err(404, "not_found", "m")
            return Response(status_code=304)

        def bad(s):
            return err(s, "x", "m")
        ''')
    spec = {"paths": {
        "/api/h": {"get": op("409 exists when taken.", s304=[""], s409=["exists"])},
        "/api/bad": {"get": op()},
        "/api/quiet": {"get": op()}}}
    routes = {"GET /api/h": ("m.py", "h"), "GET /api/bad": ("m.py", "bad"), "GET /api/quiet": ("m.py", "bad")}
    checker = load_checker()
    found = checker.problems(spec, routes, {"m.py": src}, REFUSALS, {"GET /api/quiet": "a test reason"})
    bad_line = src.splitlines().index('    return err(s, "x", "m")') + 1
    assert found[0].startswith(f"GET /api/bad: cannot be followed: line {bad_line}: "), found
    assert found[1:] == ["GET /api/h: 404 not_found answered but not declared",
                         "GET /api/h: 409 exists declared but never answered"]
    spec["paths"]["/api/h"]["get"] = op("409 not_owned when taken.", s304=[""], s404=["not_found"])
    allowed = {"GET /api/bad": "a test reason", "GET /api/quiet": "a test reason"}
    assert checker.problems(spec, routes, {"m.py": src}, REFUSALS, allowed) == [
        "GET /api/h: 409 not_owned named in the docstring but not declared"]


def test_classes_are_data_and_positional_statuses_are_read() -> None:
    assert reach('''
        class Notes:
            pass

        def h(body, n):
            Notes()
            if n:
                return JSONResponse({"error": "stale", "message": "m"}, 409)
            if body:
                return JSONResponse(body, 422)
            return Response(None, 304)
        ''') == {(409, "stale"), (422, ""), (304, "")}


def test_routes_of_refuses_a_duplicate_route() -> None:
    app = FastAPI()

    @app.get("/api/x")
    def first() -> dict[str, str]:
        return {}

    @app.get("/api/x")
    def second() -> dict[str, str]:
        return {}

    with pytest.raises(ValueError, match="GET /api/x"):
        load_checker().routes_of(app)


def test_declared_reads_real_answers_through_openapi() -> None:
    app = FastAPI()

    @app.get("/api/x/y", responses=declared())  # type: ignore[arg-type]
    def route() -> dict[str, str]:
        return {}

    operation = app.openapi()["paths"]["/api/x/y"]["get"]
    assert load_checker()._declared(operation) == {
        (409, "exists"), (404, "not_found"), (409, "family:session"), (422, "profile_error"),
        (422, "family:bundle"), (304, "")}
