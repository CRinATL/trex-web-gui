"""Fail `make check` when a project function has no docstring and no real comment (optional baseline).

  check_docstrings.py [--repo DIR] [--baseline FILE] [--write-baseline FILE] [ROOT ...]

A function is documented by a docstring, by a comment-only `#` line directly above its `def` or its first
decorator and sitting at or left of the def's column, or by a trailing `#` comment on the line of the `def`
keyword. These do not count: an empty `#`, a section banner (`# --- x ---`, `# ===`), a pragma (`# noqa`,
`# type:`, `# pragma:`, `# fmt:`), a trailing comment on the statement before the `def`, a comment on the
closing line of a multi-line signature, and a comment indented deeper than the def (the end of the previous
body).
Comments come from `tokenize`, so a `#` inside a string is not one. Lambdas are not checked, and neither are
`test_*` functions (and everything nested in them) in files named `test_*.py`: the test name states the
behaviour.

The baseline lists `<repo-relative path>::<qualified name>` for each function that is undocumented today,
compared as a multiset (a property getter and setter share a name). A miss not in the baseline is new; a
baseline entry that is now documented, or no longer exists, is stale; either exits 1. The baseline only
shrinks: documenting a function means deleting its line. ROOT defaults to backend/trex_web_gui backend/scripts
tests vm scripts under --repo (relative ROOTs are read against --repo; one outside it is a usage error), and
`_vendor` and `__pycache__` are skipped. --baseline and --write-baseline are read against the working
directory. A missing --repo or --baseline is a usage error (exit 2)."""
import argparse
import ast
import io
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = ("backend/trex_web_gui", "backend/scripts", "tests", "vm", "scripts")
SKIPPED_DIRS = {"_vendor", "__pycache__"}
BANNER = re.compile(r"^#\s*[-=]{2,}|[-=]{3,}$")
PRAGMA = re.compile(r"^#\s*(noqa|type:|pragma:|fmt:)")


def _comments(source: str) -> dict[int, tuple[bool, int]]:
    """Row -> (comment is alone on its line, its column) for each comment that can document a def."""
    lines = source.splitlines()
    found: dict[int, tuple[bool, int]] = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        text = tok.string.rstrip()
        if text == "#" or BANNER.search(text) or PRAGMA.match(text):
            continue
        row, col = tok.start
        found[row] = (not lines[row - 1][:col].strip(), col)
    return found


def _analyze(path: Path, source: str) -> tuple[int, list[tuple[int, str]]]:
    """(functions checked, [(line, qualname)] undocumented) for one file, in source order."""
    comments = _comments(source)
    in_test_file = path.name.startswith("test_") and path.suffix == ".py"
    total = 0
    misses: list[tuple[int, str]] = []

    def documented(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """True for a docstring, a comment-only line directly above the `def` or the first decorator, or a
        trailing one on the `def` line."""
        if (ast.get_docstring(node, clean=False) or "").strip():
            return True
        for anchor in {node.decorator_list[0] if node.decorator_list else node, node}:
            above = comments.get(anchor.lineno - 1)
            if above and above[0] and above[1] <= node.col_offset:
                return True
        trailing = comments.get(node.lineno)
        return bool(trailing and not trailing[0])

    def visit(node: ast.AST, stack: list[str], skipping: bool) -> None:
        """Count and check each function under `node`; `skipping` is set inside a skipped test function."""
        nonlocal total
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, stack + [child.name], skipping)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                skip = skipping or (in_test_file and child.name.startswith("test_"))
                if not skip:
                    total += 1
                    if not documented(child):
                        misses.append((child.lineno, ".".join(stack + [child.name])))
                visit(child, stack + [child.name], skip)
            else:
                visit(child, stack, skipping)

    visit(ast.parse(source, filename=str(path)), [], False)
    return total, sorted(misses, key=lambda m: m[0])


def undocumented(path: Path, source: str) -> list[tuple[int, str]]:
    """Line and qualified name of each undocumented function in one file, in source order."""
    return _analyze(path, source)[1]


def _files(repo: Path, roots: list[Path]) -> list[Path]:
    """Sorted .py files under the roots (a root may be a file), minus `_vendor` and `__pycache__`."""
    found: set[Path] = set()
    for root in roots:
        root = root if root.is_absolute() else repo / root
        candidates = [root] if root.is_file() else root.rglob("*.py")
        found.update(p for p in candidates
                     if p.suffix == ".py" and not SKIPPED_DIRS & set(p.relative_to(repo).parts))
    return sorted(found)


def _scan_counted(repo: Path, roots: list[Path]) -> tuple[int, list[tuple[str, int, str]]]:
    """(functions checked, `scan`'s misses) over the roots."""
    total = 0
    misses: list[tuple[str, int, str]] = []
    for path in _files(repo, roots):
        n, found = _analyze(path, path.read_text(encoding="utf-8"))
        total += n
        rel = path.relative_to(repo).as_posix()
        misses.extend((rel, line, name) for line, name in found)
    return total, misses


def scan(repo: Path, roots: list[Path]) -> list[tuple[str, int, str]]:
    """(path relative to repo, line, qualname) of each undocumented function under the roots."""
    return _scan_counted(repo, roots)[1]


def main(argv: list[str]) -> int:
    """Run the CLI: 0 clean or baseline written, 1 for a new miss or stale entry; a usage error exits 2."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO, help="repo root; paths are relative to it")
    ap.add_argument("--baseline", type=Path,
                    help="known misses, one path::qualname per line, relative to the working directory"
                    " (default: none)")
    ap.add_argument("--write-baseline", type=Path, metavar="FILE",
                    help="write the current misses to FILE (relative to the working directory), exit 0")
    ap.add_argument("roots", nargs="*", metavar="ROOT", type=Path, help="default: " + " ".join(DEFAULT_ROOTS))
    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        ap.error(f"repo {args.repo} is not a directory")
    roots = []
    for r in args.roots or [Path(d) for d in DEFAULT_ROOTS]:
        full = (r if r.is_absolute() else repo / r).resolve()
        if not full.is_relative_to(repo):
            ap.error(f"root {r} is outside the repo {repo}")
        if not full.exists():
            if args.roots:
                ap.error(f"root {r} does not exist")
            continue
        roots.append(full)
    total, misses = _scan_counted(repo, roots)

    if args.write_baseline:
        lines = sorted(f"{p}::{q}" for p, _, q in misses)
        args.write_baseline.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
        print(f"docstrings: {total} functions, {len(misses)} undocumented, {len(lines)} in baseline")
        return 0

    baseline: Counter[str] = Counter()
    if args.baseline:
        if not args.baseline.is_file():
            ap.error(f"baseline {args.baseline} does not exist")
        baseline.update(ln for ln in args.baseline.read_text(encoding="utf-8").splitlines() if ln.strip())
    in_baseline = sum(baseline.values())
    bad = 0
    for path, line, name in misses:
        key = f"{path}::{name}"
        if baseline[key] > 0:
            baseline[key] -= 1
            continue
        print(f"{path}:{line}: {name} has no docstring or comment")
        bad += 1
    for key in sorted(baseline.elements()):
        print(f"{key} is documented now; remove it from the baseline")
        bad += 1
    print(f"docstrings: {total} functions, {len(misses)} undocumented, {in_baseline} in baseline")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
