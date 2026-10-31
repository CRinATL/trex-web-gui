#!/usr/bin/env python3
"""Build docs/api.md, the HTTP API reference, from the app's own OpenAPI description.

Usage (from the repo root, in the backend's environment):
    uv run --project backend python -I -B scripts/gen_api_docs.py          # write docs/api.md
    uv run --project backend python -I -B scripts/gen_api_docs.py --check  # write nothing; exit 1 and print a
                                                                           # unified diff (at most 40 lines)
                                                                           # when docs/api.md is stale
`make api-docs` is the first form; `make check` runs the second through tests/unit/test_api_docs.py.

trex-web-gui serves no OpenAPI page on purpose (create_app passes docs_url, redoc_url and openapi_url None:
the token boundary, P3c spec §6.0), so this script reads `create_app(<a temporary data dir>).openapi()` at
build time. Nothing but the standard library is imported at module level, so the script can start outside the
backend's environment and say so; the app is imported inside `build`. The output is deterministic: sorted,
with no timestamp and no version string, so a release bump does not stale it.

The preamble below is hand-written, but every list in it (the WebSocket's message types, the routes
`--monitor-only` refuses) is read from the code it describes."""
import argparse
import difflib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "api.md"
DIFF_MAX_LINES = 40
METHOD_ORDER = ("get", "post", "put", "patch", "delete")
# FastAPI's own 422 body, which the app never sends (it answers bad_request)
HIDDEN_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})
CONSTRAINT_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
                   "pattern", "minItems", "maxItems", "multipleOf")


def undescribed(spec: dict[str, Any]) -> list[str]:
    """`METHOD /path` of each operation in the OpenAPI `spec` whose description (the route docstring) is
    empty, in path then method order."""
    return [f"{m.upper()} {p}" for p, ops in sorted(spec["paths"].items()) for m in METHOD_ORDER
            if m in ops and not ops[m].get("description", "").strip()]


def schema_name(key: str) -> str:
    """The name shown for a component schema: `trex_web_gui__api__app__ConnectBody` (FastAPI's disambiguation
    of two models called ConnectBody) becomes `app.ConnectBody`."""
    prefix = "trex_web_gui__api__"
    if key.startswith(prefix):
        module, _, cls = key[len(prefix):].partition("__")
        return f"{module}.{cls}"
    return key


def type_of(s: dict[str, Any]) -> str:
    """A compact type for a JSON-schema fragment: refs by name, unions with ` | `, arrays as `array<T>`,
    maps as `object<string, T>`, an enum as its quoted members."""
    if "$ref" in s:
        return schema_name(s["$ref"].rsplit("/", 1)[-1])
    if "anyOf" in s or "oneOf" in s:
        return " | ".join(type_of(x) for x in s.get("anyOf", s.get("oneOf", [])))
    if "const" in s:
        return json.dumps(s["const"])
    if "enum" in s:
        return " | ".join(json.dumps(x) for x in s["enum"])
    t = s.get("type")
    if t == "array":
        return f"array<{type_of(s['items'])}>" if "items" in s else "array"
    if t == "object":
        extra = s.get("additionalProperties")
        if isinstance(extra, dict) and extra:
            return f"object<string, {type_of(extra)}>"
        return "object"
    if isinstance(t, list):
        return " | ".join(t)
    return str(t) if t else "any"


def bound(v: Any) -> str:
    """A constraint's value as JSON, a whole-number float (pydantic's `1.0`) as the integer."""
    return json.dumps(int(v) if isinstance(v, float) and v.is_integer() else v)


def constraints_of(s: dict[str, Any]) -> list[str]:
    """`key=value` for each bound or pattern in a schema fragment, its union branches and its array items."""
    out = [f"{k}={bound(s[k])}" for k in CONSTRAINT_KEYS if k in s]
    for branch in s.get("anyOf", []) + s.get("oneOf", []):
        out += [c for c in constraints_of(branch) if c not in out]
    if isinstance(s.get("items"), dict):
        out += [f"items {c}" for c in constraints_of(s["items"])]
    if "enum" in s and len(s["enum"]) > 1:
        out.append("one of " + ", ".join(json.dumps(x) for x in s["enum"]))
    return out


def cell(text: str) -> str:
    """Text safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ") or "-"


def code(text: str) -> str:
    """`text` as inline code safe inside a table cell (a pipe is escaped; a backtick is replaced)."""
    return "`" + text.replace("`", "'").replace("|", "\\|") + "`"


def table(rows: list[tuple[str, str, str, str, str]], first: str) -> list[str]:
    """A five-column Markdown table: `first`, type, required, default, constraints."""
    out = [f"| {first} | type | required | default | constraints |", "|---|---|---|---|---|"]
    out += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    return out


def field_rows(schema: dict[str, Any]) -> list[tuple[str, str, str, str, str]]:
    """The rows of a model's field table, in the model's own field order."""
    required = set(schema.get("required", []))
    rows = []
    for name, f in schema.get("properties", {}).items():
        default = code(json.dumps(f["default"])) if "default" in f else "-"
        rows.append((code(name), code(type_of(f)), "yes" if name in required else "no", default,
                     cell(", ".join(constraints_of(f)))))
    return rows


def parameter_rows(params: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str]]:
    """The rows of a route's parameter table."""
    rows = []
    for p in params:
        s = p.get("schema", {})
        default = code(json.dumps(s["default"])) if "default" in s else "-"
        rows.append((code(p["name"]), code(type_of(s)), "yes" if p.get("required") else "no", default,
                     cell(", ".join(constraints_of(s)))))
    return rows


FAMILY_TEXT = {"session": "a session refusal, see Errors", "daemon": "a daemon refusal, see Errors",
               "bundle": "a bundle refusal, see Errors"}
# The descriptions FastAPI writes itself; a route that declares none of its own prints the generic line.
DEFAULT_DESCRIPTIONS = {"Successful Response", "Validation Error"}


def route_section(path: str, method: str, op: dict[str, Any], schemas: dict[str, Any]) -> list[str]:
    """The `###` entry for one route: description, parameters, request body and declared responses."""
    out = [f"### {method.upper()} {path}", "", op["description"].strip(), ""]
    for where, title in (("path", "Path parameters"), ("query", "Query parameters")):
        params = sorted((p for p in op.get("parameters", []) if p["in"] == where), key=lambda p: p["name"])
        if params:
            out += [f"**{title}**", "", *table(parameter_rows(params), "name"), ""]
    for ctype, media in sorted(op.get("requestBody", {}).get("content", {}).items()):
        s = media.get("schema", {})
        if "$ref" in s:
            name = s["$ref"].rsplit("/", 1)[-1]
            out += [f"**Request body** (`{ctype}`, schema `{schema_name(name)}`)", "",
                    *table(field_rows(schemas[name]), "field"), ""]
        else:
            out += [f"**Request body** (`{ctype}`): {code(type_of(s))}", ""]
    out += ["**Responses**", ""]
    for status in sorted(op["responses"], key=int):
        entry = op["responses"][status]
        if status == "422":
            out.append("- `422` `bad_request`: the body or query failed validation "
                       "(`{\"error\": \"bad_request\", \"message\": <text>}`)")
        codes = entry.get("x-codes", [])
        families = entry.get("x-families", [])
        out += [f"- `{status}` `{c}`: {m}" if c else f"- `{status}`: {m}" for c, m in codes]
        out += [f"- `{status}`: {FAMILY_TEXT[f]}" for f in families]
        if status == "422" or codes or families:
            continue
        s = entry.get("content", {}).get("application/json", {}).get("schema")
        declared = entry.get("description", "")
        if s:
            out.append(f"- `{status}`: {code(type_of(s))}")
        elif declared and declared not in DEFAULT_DESCRIPTIONS:
            out.append(f"- `{status}`: {declared}")
        else:
            out.append(f"- `{status}`: the body is described above")
    return [*out, ""]


def schema_section(key: str, schema: dict[str, Any]) -> list[str]:
    """The appendix entry for one component schema."""
    out = [f"### {schema_name(key)}", ""]
    if schema.get("description"):
        out += [schema["description"].strip(), ""]
    if schema.get("properties"):
        out += [*table(field_rows(schema), "field"), ""]
    else:
        out += [f"Type: {code(type_of(schema))}", ""]
    return out


PREAMBLE = """# trex-web-gui HTTP API

Generated by `scripts/gen_api_docs.py` (`make api-docs`) from the routes' own docstrings and models; do not
edit it by hand. `make check` fails when it is stale. trex-web-gui serves no OpenAPI description or
documentation page itself (`/docs`, `/redoc` and `/openapi.json` are switched off, so none of them serves one;
a built UI's fallback may answer those paths with its `index.html`): this file is the reference.

## Access

- **Token and cookie.** `trex-web-gui serve` makes one access token per process and prints a login URL.
  `GET {login}?token=<token>` with the right token answers 303 to `/` and sets the session cookie
  (`HttpOnly`, `SameSite=Strict`, named `trex_web_gui_` plus eight hex characters derived from the token); a
  wrong one is 403 `bad_token`. Every other `/api` route, and the WebSocket, needs that cookie: without it a
  request is 401 `unauthenticated` and the WebSocket closes with 1008. Reachable without the cookie:
  {exempt}.
- **Loopback only.** The `Host` header must be `127.0.0.1` or `localhost` (any port); any other is a 400 with
  a plain-text body. A request that carries an `Origin` header must name an `http` origin on `127.0.0.1` or
  `localhost` at the backend's own port (and the Vite dev port with `--dev`); any other is 403
  `forbidden_origin`. A request without `Origin` (curl, a script) passes that check.
- **`--monitor-only`.** A backend started with `--monitor-only` answers 409 `monitor_only` to every request,
  of any method, under {families}, and to every {writes} outside {exempt_writes}. Reads of the other routes
  stay live.

## Errors

Every error is JSON, `{"error": <code>, "message": <text>}`, with the HTTP status of the refusal.

- 422 `bad_request`: the body or a query parameter failed validation (the message lists the first three
  problems). This replaces FastAPI's own 422 body, which is never sent.
- 401 `unauthenticated`: no valid session cookie (see Access).
- 403 `forbidden_origin`: the request's `Origin` header is not an allowed origin (see Access).
- 400: the `Host` header is not `127.0.0.1` or `localhost` (see Access), with a plain-text body.
- 404 `not_found` and 405 `method_not_allowed`: an unknown route or a wrong method.
- 500 `internal`: an unhandled exception (the message is its type and text).
- 500 `storage_error`: a file the backend keeps could not be read or written (settings, port labels, test
  setups, the Studio library, packet templates, uploads, bundle imports); the message says which.
- Any other code is the route's own; each route's text names the ones it can answer.

## WebSocket

`/api/ws` is a WebSocket, which OpenAPI does not describe. It needs the cookie, sends a `hello` first and then
the current state, then forwards the hub's JSON messages, each with a `type`. State messages are coalesced (a
newer message replaces the queued older one of its type): {state}. Bulk messages are never coalesced but are
capped: {bulk}. For a subscriber whose queue overflows, the hub drops the oldest queued message whose type
is not a state type (the list above); state messages are never dropped.
"""


def preamble() -> list[str]:
    """The hand-written head of the reference; its lists are read from the modules they describe."""
    from trex_web_gui.api.auth import EXEMPT, LOGIN_PATH
    from trex_web_gui.api.hub import BULK_TYPES, STATE_TYPES
    from trex_web_gui.api.monitor import FAMILIES, WRITE_EXEMPT, WRITE_METHODS
    fill = {
        "login": LOGIN_PATH,
        "exempt": ", ".join(f"`{p}`" for p in sorted(EXEMPT)),
        "families": ", ".join(f"`{f}`" for f in FAMILIES),
        "writes": ", ".join(sorted(WRITE_METHODS)),
        "exempt_writes": ", ".join(f"`{p}`" for p in WRITE_EXEMPT),
        "state": ", ".join(f"`{t}`" for t in sorted(STATE_TYPES)),
        "bulk": ", ".join(f"`{t}` (at most {n} queued per subscriber)"
                          for t, n in sorted(BULK_TYPES.items())),
    }
    text = PREAMBLE
    for key, value in fill.items():
        text = text.replace("{" + key + "}", value)
    return text.splitlines() + [""]


def build() -> str:
    """The text of docs/api.md for the app as it is now. SystemExit when a route has no docstring."""
    from trex_web_gui.api.app import create_app
    with tempfile.TemporaryDirectory() as tmp:
        spec = create_app(Path(tmp)).openapi()
    missing = undescribed(spec)
    if missing:
        raise SystemExit("routes without a docstring (the reference is built from them): "
                         + ", ".join(missing))
    schemas = spec["components"]["schemas"]
    families: dict[str, list[tuple[str, str]]] = {}
    for path, ops in spec["paths"].items():
        families.setdefault("/".join(path.split("/")[:3]), []).extend((path, m) for m in ops)
    out = preamble()
    for fam in sorted(families):
        out += [f"## {fam}", ""]
        for path, method in sorted(families[fam], key=lambda pm: (pm[0], METHOD_ORDER.index(pm[1]))):
            out += route_section(path, method, spec["paths"][path][method], schemas)
    out += ["## Schemas", "", "The models that request bodies are made of, in name order.", ""]
    for key in sorted((k for k in schemas if k not in HIDDEN_SCHEMAS), key=schema_name):
        out += schema_section(key, schemas[key])
    return "\n".join(out).rstrip("\n") + "\n"


def check(path: Path = DOC) -> tuple[int, str]:
    """(0, "") when `path` holds exactly what `build` would write; otherwise (1, a unified diff of at most
    DIFF_MAX_LINES lines, the last one saying so when it was cut). A missing file counts as empty."""
    want = build()
    have = path.read_text() if path.exists() else ""
    if have == want:
        return 0, ""
    diff = list(difflib.unified_diff(have.splitlines(), want.splitlines(), f"{path.name} (committed)",
                                     f"{path.name} (generated)", lineterm=""))
    if len(diff) > DIFF_MAX_LINES:
        diff = diff[:DIFF_MAX_LINES - 1] + [f"... (diff cut at {DIFF_MAX_LINES} lines)"]
    return 1, "\n".join(diff)


def main(argv: list[str]) -> int:
    """Write docs/api.md, or with --check compare it; the exit status."""
    ap = argparse.ArgumentParser(description="Build docs/api.md from the app's OpenAPI description.")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 with a diff when it is stale")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        import trex_web_gui.api.app  # noqa: F401  # only to fail early with a clear message
    except ImportError as e:
        print(f"gen_api_docs.py needs the backend's environment ({e}); run it as "
              "`uv run --project backend python -I -B scripts/gen_api_docs.py`", file=sys.stderr)
        return 2
    if args.check:
        rc, diff = check()
        if rc:
            print(diff)
            print("docs/api.md is stale: run `make api-docs`", file=sys.stderr)
        return rc
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(build())
    print(f"wrote {DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
