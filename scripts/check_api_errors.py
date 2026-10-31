"""Checks on the error codes docs/api.md lists. `refusal_sites` finds every refusal code raised under
backend/trex_web_gui/ so the shared table (trex_web_gui.api.refusals) cannot drift from the raise sites.
Standard library only at module level."""
import ast
from pathlib import Path

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
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _class_name(node.func) not in FAMILIES:
                continue
            arg = _code_arg(node)
            if arg is None:
                continue
            literal = isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            key = arg.value if literal else NON_LITERAL  # type: ignore[union-attr]
            family = FAMILIES[_class_name(node.func)]  # type: ignore[index]
            sites.setdefault((family, key), []).append(f"{path.relative_to(root)}:{node.lineno}")
    return sites
