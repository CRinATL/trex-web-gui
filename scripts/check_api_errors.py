"""Checks on the error codes docs/api.md lists. `refusal_sites` finds every refusal code raised under
backend/trex_web_gui/ so the shared table (trex_web_gui.api.refusals) cannot drift from the raise sites.
`routes_of` pairs each operation of the app with its handler, `reachable` follows a handler's body to the
(status, code) pairs it can answer, and `problems` compares those with what each route declares and names in
its docstring. Standard library only at module level."""
import ast
import builtins
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FAMILIES = {"CommandRefused": "session", "DaemonRefused": "daemon", "BundleError": "bundle"}
NON_LITERAL = "<non-literal>"


def _class_name(func: ast.expr) -> str | None:
    """The called name of `X(...)` or `mod.X(...)`, else None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _code_arg(call: ast.Call) -> ast.expr | None:
    """The `code` argument of a refusal call: the first positional, else the `code=` keyword."""
    if call.args:
        return call.args[0]
    return next((k.value for k in call.keywords if k.arg == "code"), None)


def refusal_sites(root: Path) -> dict[tuple[str, str], list[str]]:
    """Every code raised as the first argument of `CommandRefused(...)`, `DaemonRefused(...)` or
    `BundleError(...)` (positional or `code=`, bare or as an attribute) in the `.py` files under `root`, as
    (family, code) -> `file:line` sites (the file relative to `root`). The family is session, daemon or
    bundle, after the class. A code that is not a string literal is reported under the code
    `"<non-literal>"` of its family."""
    sites: dict[tuple[str, str], list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if "_vendor" in rel.parts:              # vendored third-party code is not ours to scan
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _class_name(node.func)
            if name not in FAMILIES:
                continue
            arg = _code_arg(node)
            literal = isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            key = arg.value if isinstance(arg, ast.Constant) and literal else NON_LITERAL
            sites.setdefault((FAMILIES[name], str(key)), []).append(f"{rel}:{node.lineno}")
    return sites


# --- the route checker -------------------------------------------------------------------------------------

Pair = tuple[int, str]          # (status, code); code "" for a code-less status, "family:<name>" for a family

# Names imported from other api/ modules that a handler may call, with the pairs a call can answer.
# `problems` passes this to `reachable`; any other such name (except `err`) cannot be followed.
SHARED: dict[str, set[Pair]] = {
    # api/bodies.py: raises BodyRefused 411 without a Content-Length, 413 over the cap, 422 on bad JSON
    "read_json_capped": {(411, "length_required"), (413, "too_large"), (422, "bad_request")},
    # api/auth.py: compares a token, returns a bool
    "token_matches": set(),
}
RESPONSES = ("err", "JSONResponse", "Response")      # the calls that make an error response
BODY_REFUSED = "BodyRefused"     # api/bodies.py's carrier; its argument is walked, `e.response` is SHARED's
API_MODULE = re.compile(r"^(trex_web_gui\.api\.|\.)\w+$")   # `from <this> import ...` is another api/ module
DOC_PAIR = re.compile(r"\b([1-5]\d\d)\s+`?([a-z][a-z0-9_]*)`?")

Function = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
Scope = Function | ast.Module


@dataclass(frozen=True)
class Unresolved:
    """A handler whose answers cannot be told from its source; `why` names the line and the reason."""
    why: str


class _Stop(Exception):
    """Raised inside the walk to give up with a reason (becomes Unresolved)."""


def _literal_int(node: ast.expr) -> list[int] | None:
    """The statuses `node` can be: an int literal, or both branches of an IfExp of them; None otherwise."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return [node.value]
    if isinstance(node, ast.IfExp):
        body, orelse = _literal_int(node.body), _literal_int(node.orelse)
        return None if body is None or orelse is None else body + orelse
    return None


def _literal_str(node: ast.expr | None) -> str | None:
    """The value of a string literal, else None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    """The value of keyword `name` of `call`, else None."""
    return next((k.value for k in call.keywords if k.arg == name), None)


def _children(node: ast.AST) -> list[ast.AST]:
    """The child nodes of a function or module that belong to its own scope: its body statements (a
    function's decorators and argument defaults are not part of it)."""
    if isinstance(node, ast.Lambda):
        return [node.body]
    return list(node.body)


def _bindings(scope: Scope) -> dict[str, tuple[str, Any]]:
    """Name -> (kind, detail) for each name bound in `scope` itself (nested functions and classes are bound,
    not entered). Kinds: def (a function or a lambda assigned to a name; detail is its node), param, import
    (detail is the module, "" for `import x`), local (any other binding)."""
    found: dict[str, tuple[str, Any]] = {}

    def bind(name: str, kind: str, detail: Any = None) -> None:
        """Record a binding; a def outranks a param, which outranks any other kind."""
        rank = {"def": 0, "param": 1}
        old = found.get(name)
        if old is None or rank.get(kind, 2) < rank.get(old[0], 2):
            found[name] = (kind, detail)

    if not isinstance(scope, ast.Module):
        a = scope.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
            if arg is not None:
                bind(arg.arg, "param")
    stack = _children(scope)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bind(node.name, "def", node)
            continue
        if isinstance(node, ast.ClassDef):
            bind(node.name, "local")
            continue
        if isinstance(node, ast.Lambda):
            continue
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            bind(node.targets[0].id, "def", node.value)
            continue
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            for alias in node.names:
                bind(alias.asname or alias.name, "import", module)
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                bind((alias.asname or alias.name).split(".")[0], "import", "")
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bind(node.id, "local")
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bind(node.name, "local")
        stack.extend(ast.iter_child_nodes(node))
    return found


class _Walk:
    """The pairs reachable from one handler of one module; see `reachable`."""

    def __init__(self, tree: ast.Module, shared: dict[str, set[Pair]]) -> None:
        """Index every function's enclosing scope and start with no pairs."""
        self.shared = shared
        self.parent: dict[int, Scope] = {}
        self.scopes: dict[int, dict[str, tuple[str, Any]]] = {}
        self.pairs: set[Pair] = set()
        self.seen: set[int] = set()
        self.functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for node in tree.body:
            self._index(node, tree)

    def _index(self, node: ast.AST, scope: Scope) -> None:
        """Record the enclosing scope of each function and lambda at or under `node`, which is in `scope`. A
        function's decorators and argument defaults are in the enclosing scope, its body in its own."""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for child in ast.iter_child_nodes(node):
                self._index(child, scope)
            return
        self.parent[id(node)] = scope
        a = node.args
        outer: list[ast.AST] = [*a.defaults, *(d for d in a.kw_defaults if d is not None)]
        if not isinstance(node, ast.Lambda):
            self.functions.append(node)
            outer += node.decorator_list
        for part in outer:
            self._index(part, scope)
        for part in _children(node):
            self._index(part, node)

    def chain(self, scope: Scope) -> list[Scope]:
        """`scope`, then each enclosing function outward, then the module."""
        out = [scope]
        while not isinstance(out[-1], ast.Module):
            out.append(self.parent[id(out[-1])])
        return out

    def resolve(self, name: str, chain: list[Scope]) -> tuple[str, Any]:
        """The binding of `name`, innermost scope first; ("builtin", None) or ("unbound", None) when no scope
        binds it."""
        for scope in chain:
            if id(scope) not in self.scopes:
                self.scopes[id(scope)] = _bindings(scope)
            if name in self.scopes[id(scope)]:
                return self.scopes[id(scope)][name]
        return ("builtin", None) if hasattr(builtins, name) else ("unbound", None)

    def follow(self, fn: Function) -> None:
        """Walk the body of `fn` once, in its own lexical scope."""
        if id(fn) in self.seen:
            return
        self.seen.add(id(fn))
        chain = self.chain(fn)
        for node in _children(fn):
            self.visit(node, chain, {})

    def visit(self, node: ast.AST, chain: list[Scope], caught: dict[str, str | None]) -> None:
        """Collect the pairs under `node`. `caught` maps each name bound by an enclosing `except X as name` to
        the class name of X (None for a tuple)."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return                                   # followed only where it is referenced
        if isinstance(node, ast.Lambda):
            self.seen.add(id(node))
            self.visit(node.body, [node, *chain], caught)
            return
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            return                                   # a lambda definition: followed where it is referenced
        if isinstance(node, ast.Try | ast.TryStar):
            for part in (*node.body, *node.orelse, *node.finalbody):
                self.visit(part, chain, caught)
            for handler in node.handlers:
                inner = caught
                if handler.name:
                    cls = _class_name(handler.type) if handler.type is not None else None
                    inner = {**caught, handler.name: cls}
                for part in handler.body:
                    self.visit(part, chain, inner)
            return
        if isinstance(node, ast.Call):
            self.call(node, chain, caught)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            kind, detail = self.resolve(node.id, chain)
            if kind == "def":
                self.follow(detail)
        elif (isinstance(node, ast.Attribute) and node.attr == "response" and isinstance(node.value, ast.Name)
              and caught.get(node.value.id, "") == BODY_REFUSED):
            if "read_json_capped" not in self.shared:
                raise _Stop(f"line {node.lineno}: e.response of BodyRefused needs read_json_capped in shared")
            self.pairs |= self.shared["read_json_capped"]
        for child in ast.iter_child_nodes(node):
            if isinstance(node, ast.Call) and child is node.func and isinstance(child, ast.Name):
                continue                             # the called name was classified by `call`
            self.visit(child, chain, caught)

    def call(self, node: ast.Call, chain: list[Scope], caught: dict[str, str | None]) -> None:
        """Classify a call: a response, a followed function, a SHARED name, data, or a reason to give up."""
        if not isinstance(node.func, ast.Name):
            return                                   # a method call or a call of a call: data
        name, line = node.func.id, node.lineno
        kind, detail = self.resolve(name, chain)
        if kind == "def":
            self.follow(detail)
        elif kind in ("param", "local", "builtin"):
            return
        elif name in RESPONSES:
            self.response(node, caught)
        elif kind == "unbound":
            raise _Stop(f"line {line}: call to {name}, which is not defined, imported or a builtin")
        elif API_MODULE.match(detail):
            if name in self.shared:
                self.pairs |= self.shared[name]
            elif name != BODY_REFUSED:
                raise _Stop(f"line {line}: call to {name}, imported from {detail} and not in SHARED")

    def response(self, node: ast.Call, caught: dict[str, str | None]) -> None:
        """The pairs of an `err`, `JSONResponse` or `Response` call (see `reachable`)."""
        name, line = node.func.id, node.lineno            # type: ignore[attr-defined]
        if name == "err":
            status = node.args[0] if node.args else _keyword(node, "status")
            code = node.args[1] if len(node.args) > 1 else _keyword(node, "code")
        else:
            status = _keyword(node, "status_code")
            body = node.args[0] if node.args else _keyword(node, "content")
            code = None
            if isinstance(body, ast.Dict):
                pairs = zip(body.keys, body.values, strict=True)
                code = next((v for k, v in pairs if _literal_str(k) == "error"), None)
            if code is None:
                if status is None:
                    return                           # a success body with the default status
                statuses = _literal_int(status)
                if statuses is None:
                    raise _Stop(f"line {line}: {name} with a status that is not a literal")
                self.pairs |= {(s, "") for s in statuses if s != 200}
                return
        statuses = _literal_int(status) if status is not None else None
        if statuses is None:
            raise _Stop(f"line {line}: {name} with a status that is not a literal")
        literal = _literal_str(code)
        if literal is not None:
            self.pairs |= {(s, literal) for s in statuses}
            return
        if (name == "err" and isinstance(code, ast.Attribute) and code.attr == "code"
                and isinstance(code.value, ast.Name) and caught.get(code.value.id) in FAMILIES):
            family = FAMILIES[str(caught[code.value.id])]
            self.pairs |= {(s, f"family:{family}") for s in statuses}
            return
        raise _Stop(f"line {line}: {name} with a code that is not a literal or a family's e.code")


def reachable(module_src: str, handler: str, shared: dict[str, set[Pair]]) -> set[Pair] | Unresolved:
    """The (status, code) pairs the function named `handler` in `module_src` can answer, or Unresolved.

    Handler names are unique within a module, not across the app (`connect` is in app.py and daemon.py), so
    the module is part of the key. The walk covers the handler's body (not its decorators or argument
    defaults) and the body of each function it reaches. A name resolves innermost first: the function's own
    scope, each enclosing function outward, then the module. A same-module function or lambda is followed
    when it is called or named bare (`guarded(go)`, `asyncio.to_thread(read, ...)`); a call through a
    parameter (`ok(result)`) is data, since what was passed in is followed where it was named.

    Pairs: `err(<status>, "<code>", ...)`; `JSONResponse({"error": "<code>", ...}, status_code=<status>)`;
    `Response(status_code=<status>)` or a code-less `JSONResponse(..., status_code=<status>)` as
    `(status, "")` (status 200 is a success, not a pair); an IfExp status gives both branches;
    `err(<status>, e.code, ...)` under `except CommandRefused|DaemonRefused|BundleError as e` (bare or
    qualified) gives `(status, "family:<session|daemon|bundle>")`; `e.response` under
    `except BodyRefused as e` gives `shared["read_json_capped"]`; a call to a key of `shared` gives its pairs.
    Method calls, calls into other packages, builtins, locals, parameters, classes and `BodyRefused(...)` (its
    argument is walked) are data.

    Unresolved: an `err`/`JSONResponse`/`Response` whose status or code is not a literal (outside the family
    sites above), a call to a name nothing binds that is not a builtin, or a call to a name imported from
    another api/ module other than `err`, `BodyRefused` and the keys of `shared`."""
    tree = ast.parse(module_src)
    walk = _Walk(tree, shared)
    found = [f for f in walk.functions if f.name == handler]
    if len(found) != 1:
        return Unresolved(f"{len(found)} functions named {handler} in the module")
    try:
        walk.follow(found[0])
    except _Stop as e:
        return Unresolved(str(e))
    return walk.pairs


def routes_of(app: Any, prefix: str = "") -> dict[str, tuple[str, str]]:
    """`"METHOD /path"` -> (module file name, handler name) for each schema route of `app` (duck-typed, no
    FastAPI import). An entry with `endpoint`, `path_format`, `methods` and a true `include_in_schema` is a
    route; one with `original_router` and `include_context.prefix` (FastAPI's wrapper of an
    `include_router`) is recursed into with its prefix prepended. `path_format` already drops `{name:path}`
    converters and holds the router's own prefix."""
    out: dict[str, tuple[str, str]] = {}
    for route in app.routes:
        if hasattr(route, "original_router"):
            out |= routes_of(route.original_router, prefix + route.include_context.prefix)
            continue
        if not getattr(route, "include_in_schema", False) or not getattr(route, "methods", None):
            continue
        if not hasattr(route, "endpoint") or not hasattr(route, "path_format"):
            continue
        where = (Path(inspect.getfile(route.endpoint)).name, route.endpoint.__name__)
        for method in route.methods:
            out[f"{method.upper()} {prefix}{route.path_format}"] = where
    return out


def _declared(operation: dict[str, Any]) -> set[Pair]:
    """The pairs an openapi() operation declares through `answers` (its x-codes and x-families)."""
    out: set[Pair] = set()
    for status, entry in operation.get("responses", {}).items():
        if not str(status).isdigit() or not isinstance(entry, dict):
            continue
        out |= {(int(status), code) for code, _ in entry.get("x-codes", [])}
        out |= {(int(status), f"family:{f}") for f in entry.get("x-families", [])}
    return out


def _label(pair: Pair) -> str:
    """`<status> <code>`, or the status alone for a code-less pair."""
    return f"{pair[0]} {pair[1]}" if pair[1] else str(pair[0])


def problems(spec: dict[str, Any], routes: dict[str, tuple[str, str]], sources: dict[str, str],
             table: dict[tuple[str, str], tuple[int, str]], allowed: dict[str, str]) -> list[str]:
    """Each problem of each operation of `spec` (an openapi() dict), sorted, as
    `<METHOD /path>: <status> <code> answered but not declared`, `... declared but never answered`,
    `... named in the docstring but not declared` or `... cannot be followed: <why>` (`no handler` when
    `routes` has no entry). `routes` is `routes_of(app)`, `sources` maps a module file name to its source,
    `table` is the shared refusal table keyed (family, code), and `allowed` maps a route to the reason its
    problems are suppressed. A family's pair prints as `family:<name>`.

    A docstring names a pair only as `<status> <code>` where the code is known (in `table`, declared by some
    route, or answered by some route), so `port 4500` or `200 bytes` names nothing. The pair is satisfied when
    the route declares it, or declares a family at that status whose row in `table` holds the code."""
    out: list[str] = []
    operations = [(f"{m.upper()} {p}", o) for p, ops in spec.get("paths", {}).items() for m, o in ops.items()]
    answered: dict[str, set[Pair] | Unresolved] = {}
    for key, _ in operations:
        if key in routes and key not in allowed:
            file, handler = routes[key]
            if file not in sources:
                answered[key] = Unresolved(f"no source for {file}")
            else:
                answered[key] = reachable(sources[file], handler, SHARED)
    known = {code for _, code in table}
    known |= {c for _, o in operations for _, c in _declared(o)}
    known |= {c for r in answered.values() if isinstance(r, set) for _, c in r}
    for key, operation in operations:
        if key in allowed:
            continue
        if key not in routes:
            out.append(f"{key}: cannot be followed: no handler")
            continue
        declared = _declared(operation)
        result = answered[key]
        if isinstance(result, Unresolved):
            out.append(f"{key}: cannot be followed: {result.why}")
        else:
            out += [f"{key}: {_label(p)} answered but not declared" for p in sorted(result - declared)]
            out += [f"{key}: {_label(p)} declared but never answered" for p in sorted(declared - result)]
        families = {(s, c.removeprefix("family:")) for s, c in declared if c.startswith("family:")}
        named = {(int(s), c) for s, c in DOC_PAIR.findall(operation.get("description", ""))}
        for status, code in sorted(named):
            if code not in known or (status, code) in declared:
                continue
            if any(table.get((f, code), (None,))[0] == status for s, f in families if s == status):
                continue
            out.append(f"{key}: {status} {code} named in the docstring but not declared")
    return sorted(out)
