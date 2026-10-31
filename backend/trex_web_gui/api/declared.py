"""Declared answers: the helper a route's `responses=` goes through so docs/api.md can list its error codes.

`answers((409, "exists", "the name is taken"), (409, "session"))` builds the `responses=` dict of a route: a
triple is `(status, code, meaning)` (code `""` for a code-less status such as a 304), a pair is
`(status, family)` for a refusal family whose codes live in the shared table of docs/api.md. Each status entry
carries `description` and the extension keys `x-codes` and `x-families`, which FastAPI copies into
`openapi()` unchanged; scripts/gen_api_docs.py renders them."""
from typing import Any, Literal

Family = Literal["session", "daemon", "bundle"]

ARTICLE = {"session": "a session", "daemon": "a daemon", "bundle": "a bundle"}


def family_line(family: str) -> str:
    """The Responses text of a family entry, e.g. `a session refusal, see Errors`."""
    return f"{ARTICLE[family]} refusal, see Errors"


def answers(*items: tuple[int, str, str] | tuple[int, Family]) -> dict[int | str, dict[str, Any]]:
    """The `responses=` dict for `items`; see the module docstring. Codes and families are sorted."""
    codes: dict[int, list[list[str]]] = {}
    families: dict[int, list[str]] = {}
    for item in items:
        if len(item) == 3:
            codes.setdefault(item[0], []).append([item[1], item[2]])
        else:
            families.setdefault(item[0], []).append(item[1])
    out: dict[int | str, dict[str, Any]] = {}
    for status in sorted(codes.keys() | families.keys()):
        sorted_codes = sorted(codes.get(status, []))
        sorted_families = sorted(set(families.get(status, [])))
        meanings = [m for _, m in sorted_codes] + [family_line(f) for f in sorted_families]
        out[status] = {"description": "; ".join(meanings), "x-codes": sorted_codes,
                       "x-families": sorted_families}
    return out
