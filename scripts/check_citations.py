#!/usr/bin/env python3
"""Resolve every `file:line` citation in the source and say which ones point at nothing.

Ported from another of the maintainer's projects (same CLI and semantics), adapted to this repo. This
codebase cites itself, the vendored TRex client and trex-core constantly (`trex_client.py:480`,
`ports.py _drop_idle_profiles`). A line moves, the citation does not, and the next reader is sent somewhere
confidently wrong; reviewers flagged stale citations on almost every task of P2 and P2.5.

What this can and cannot do. It checks the mechanical half: does the file exist, and does it have that many
lines. `--verbose` prints the cited line's text so the semantic half (is that still the thing meant?) is a
glance rather than a hunt. It cannot tell that a citation now points at a plausible-looking wrong line, so
`--verbose` is the mode that matches the real risk; the exit code only catches the crude cases.

Adaptations for this repo
-------------------------
  - The repo root is the git top level (`pyproject.toml` lives in `backend/`); `--repo` overrides.
  - Default roots: backend/trex_web_gui (without `_vendor`: the vendored client is cited, not scanned), tests,
    frontend/src, docs, vm, scripts, and PLAN.md. `.superpowers/` is never scanned (SKIP_DIRS).
  - Citing files: .py .ts .tsx .sh .md (and .toml .rst). Cited files also include .cpp .h .jsonl .json .yaml
    .asciidoc: a JSON file has no comments, so it can be cited but never cites.
  - A bare basename resolves in this repo first, the vendored client (backend/trex_web_gui/_vendor/trex)
    counting as this repo, then in the siblings. When the local file is too SHORT for the cited line and a
    sibling's same-named file is not, the citation almost certainly meant the sibling and must be
    path-qualified; that is reported as broken, naming both.
  - `.citation-siblings` lists other trees one per line (`~` allowed; a missing path is skipped and named in
    the summary, because sibling paths are machine facts; while one is missing, a path citation that
    matches nothing and whose directory is not this repo's counts as external, so a clone without the
    siblings passes, and one under this repo's own directories still fails).
    Words after the path restrict what is INDEXED for bare-basename lookup to those subdirectories
    (e.g. `/var/tmp/trex-core-... src scripts`); an exact root-relative path still resolves against the whole
    tree. Siblings are in PRIORITY order: when a bare
    name or partial path has no local file and matches in several siblings, the earliest sibling that has
    it wins (trex-core exists both as the /var/tmp history clone and inside trex-rework; they are the same
    code).
  - `.citation-renames`: one `<old_prefix> <new_prefix>   # why` per line. A cited path that exists nowhere
    is retried with the first matching old prefix replaced, so records citing a path from before a move keep
    resolving unedited; a path that exists as cited is never rewritten, and a bad line number still fails.
  - `.citation-allow`: one `<citing-relpath> <raw>:<span>` per line (copy it from a failing run), `#`
    comments. Keyed on the (citing file, cited text) pair, not a line number, so a citing line moving does
    not stale it. It is for a known-fake citation (a document quoting a bad citation) or a genuinely
    historical one.

Snippets and living documents (added 2026-10-18, project review I7)
-------------------------------------------------------------------
  - A citation may be followed by a quoted snippet: `trex_port.py:176-178 "def is_writeable"`. The snippet
    must occur in the cited lines (whitespace-normalised), so a number that drifted onto other text fails
    instead of resolving quietly. With a continuation list, the snippet checks the last span.
  - A line citation into a `.md` file names a living document whose lines move with every edit: it fails
    unless it carries a snippet. Prefer citing the section heading
    (`plan-merged.md §8 "Capture and service-mode limits"`); keep bare `file:line` for frozen trees (the
    vendored client, trex-core at the pin).

Limits worth knowing before trusting a clean run
------------------------------------------------
  - Silent-by-luck: the cross-repo rule fires only when the local file is too SHORT. A bare `<name>.py`
    citation meaning a sibling's file resolves quietly against a longer local file of the same name.
    Qualify cross-repo paths.
  - A document ABOUT a bad citation reports that citation; that is what `.citation-allow` is for.

Usage
-----
    uv run python scripts/check_citations.py                  # the default roots
    uv run python scripts/check_citations.py --verbose        # every citation + its line
    uv run python scripts/check_citations.py --changed HEAD~1 # only files a diff touched
    uv run python scripts/check_citations.py backend/trex_web_gui # explicit roots

Exit 0 when every citation resolves, 1 when any names a missing file, a line past end-of-file, or a path this
checker cannot pin to one file, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# A path-ish token followed by :NNN or :NNN-MMM. The leading char class
# refuses a bare `:123` and the extension list keeps `10:00Z` and `{x:3d}`
# out; URLs are excluded by span, below, not by this pattern.
CITATION = re.compile(
    # The leading class admits `.` and `/` so that `.claude/skills/...` and an
    # absolute `/Users/...` are captured WHOLE. Without them the match starts
    # one character in, and a dotfile path silently becomes a different,
    # non-existent path — reported as broken while the real file sits right
    # there. Three such false positives in this repo's own docs.
    r"(?P<path>(?:~/|\./|/)?[A-Za-z0-9_.][A-Za-z0-9_./+-]*"
    r"\.(?:py|md|toml|txt|sql|jsonl|json|ya?ml|cfg|ini|sh|rst|tsx|ts|cpp|h|asciidoc))"
    r":(?P<lo>\d+)(?:\s*[-–]\s*(?P<hi>\d+))?")

# `<file>:51,232-234`: a list of lines after one path. Checking only the first element would leave the rest
# unverified.
CONTINUATION = re.compile(r"\A,(?P<lo>\d+)(?:\s*[-–]\s*(?P<hi>\d+))?")

# `<citation> "text"`: a quoted snippet the cited lines must contain.
SNIPPET = re.compile(r'\A[ \t]*"(?P<snip>[^"\n]{3,})"')

# Any whitespace-delimited token containing `://`. Citations inside one are
# URLs, not file references. Matching the SPAN rather than looking backwards
# a fixed number of characters is what catches `http://host:8080/stats.py:1`,
# where the citation match begins after the port's colon and a look-behind
# sees only `st:`.
URL_SPAN = re.compile(r"\S*://\S*")

# Directory-qualified path references with no line number, for --include-paths.
PATH_ONLY = re.compile(
    r"(?P<path>(?:backend|tests|docs|scripts|frontend|vm)/[A-Za-z0-9_./+-]*"
    r"\.(?:py|md|toml|txt|sql|jsonl|json|ya?ml|cfg|ini|sh|rst|tsx|ts|cpp|h|asciidoc))(?![\w:])")

SKIP_DIRS = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", ".uv-cache", "node_modules", ".superpowers", "dist"}
# The vendored client is never SCANNED for citations (it is upstream code) but is INDEXED, so a bare
# `trex_client.py:480` resolves to it as part of this repo.
# docs/evidence/ (that path only, not any directory named evidence) holds frozen copies of reviews and logs
# (their file:line citations date from when they were written), so it is indexed but never scanned.
SCAN_SKIP_DIRS = SKIP_DIRS | {"_vendor"}
FROZEN_DIR = ("docs", "evidence")
# Files that can CITE (they have comments) and files that can be CITED.
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".md", ".toml", ".sh", ".rst"}
CITED_SUFFIXES = TEXT_SUFFIXES | {".cpp", ".h", ".jsonl", ".json", ".yaml", ".yml", ".asciidoc", ".txt"}
DEFAULT_ROOTS = ["backend/trex_web_gui", "tests", "frontend/src", "docs", "vm", "scripts", "PLAN.md",
                 "README.md", "SECURITY.md", "CONTRIBUTING.md"]
SIBLING_FILE = ".citation-siblings"
ALLOW_FILE = ".citation-allow"
RENAME_FILE = ".citation-renames"


def _walk(root: Path, suffixes: set[str] = TEXT_SUFFIXES, skip: set[str] = SCAN_SKIP_DIRS):
    """`root` itself if a file, else each file under it with a wanted suffix and no skipped directory."""
    if root.is_file():
        yield root
        return
    for p in root.rglob("*"):
        # parts relative to the root: a skip name in the path ABOVE it (e.g. a clone under /var/tmp)
        # is not a skip
        if p.is_file() and p.suffix in suffixes and not skip & set(p.relative_to(root).parts):
            yield p


def _line_count(path: Path) -> int:
    """Number of lines in the file, counted as bytes so an undecodable file still counts."""
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def _nth_line(path: Path, n: int) -> str:
    """Line `n` (1-based) without its newline; the empty string when the file is shorter."""
    with path.open(errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            if i == n:
                return line.rstrip("\n")
    return ""


def _lines(path: Path, lo: int, hi: int) -> str:
    """Lines `lo` to `hi` inclusive, each stripped and joined by single spaces."""
    with path.open(errors="replace") as fh:
        return " ".join(line.strip() for i, line in enumerate(fh, start=1) if lo <= i <= hi)


def _norm(text: str) -> str:
    """The text with every whitespace run collapsed to one space, for snippet matching."""
    return " ".join(text.split())


def _index(roots: list[Path], restrict: dict[Path, list[str]]) -> dict[str, list[Path]]:
    """Basename -> paths of every citable file; a root in `restrict` is indexed only under its subdirs."""
    out: dict[str, list[Path]] = {}
    for root in roots:
        for sub in restrict.get(root, [""]):
            if (root / sub).exists():
                for p in _walk(root / sub, CITED_SUFFIXES, SKIP_DIRS):
                    out.setdefault(p.name, []).append(p)
    return out


def _by_suffix(raw: str, index: dict[str, list[Path]], roots: list[Path]) -> list[Path]:
    """Files whose path ends with `raw` — this project's partial-path style.

    `analysis/candidates.py` and `scrub_import/importer.py` name enough to be
    unambiguous without pinning the package layout into every comment, and
    they are the house style. Treating them as broken made the check 90% false
    positives on the real docs tree, and a check people learn to ignore is
    worse than no check.
    """
    name = Path(raw).name
    hits = [q for q in index.get(name, []) if str(q).endswith("/" + raw)]
    # Preserve the caller's root ORDER (own repo first) so the preference
    # below is decided by where a file is, not by walk order.
    return sorted(hits, key=lambda q: next(
        (i for i, r in enumerate(roots) if q.is_relative_to(r)), len(roots)))


def _resolve(raw: str, citing: Path, own: Path, siblings: list[Path],
             index: dict[str, list[Path]], missing: tuple[Path, ...] | list[Path] = (),
             renames: tuple[tuple[str, str], ...] | list[tuple[str, str]] = ()
             ) -> tuple[Path | None, list[Path], str]:
    """A citation's path as written -> (target, other candidates, why-not).

    Returns the preferred target plus every OTHER file the same text could
    name, because the caller needs those to tell "cited the wrong repo" from
    "cited a line that does not exist".
    """
    if raw.startswith(("/", "~")):
        # An absolute path names a filesystem this check cannot see — the
        # MacBook's `/Users/...`, or a `~/` sibling that may not be cloned
        # here. Unverifiable is not the same as wrong, and reporting it as
        # broken would put permanent noise in the output.
        expanded = Path(raw).expanduser()
        return ((expanded, [], "") if expanded.is_file()
                else (None, [], "absolute path, not on this machine"))

    roots = [own, *siblings]

    # 0. A path that moved (`.citation-renames`): only when the path as cited exists nowhere, so a file still
    #    at its old path is never rewritten. The first matching old prefix wins.
    if not any((root / raw).is_file() for root in roots):
        for old, new in renames:
            if raw.startswith(old):
                raw = new + raw[len(old):]
                break

    # 1. Exact, root-relative — the citing repo first, then siblings. A
    #    deliberately path-qualified citation (`src/` plus a file) has
    #    to resolve HERE, before any basename index can drag it into an
    #    ambiguity pool; qualifying the path is the fix this check recommends,
    #    so punishing it would be perverse.
    for root in roots:
        if (root / raw).is_file():
            return root / raw, [], ""

    # 2. `trex-rework/<path>` — prefixed with a repo's own NAME, which
    #    reads naturally in a document spanning two repos and resolves nowhere
    #    by itself. Strip and retry; the remainder is usually a partial path.
    for root in roots:
        if raw.startswith(root.name + "/"):
            rest = raw[len(root.name) + 1:]
            if (root / rest).is_file():
                return root / rest, [], ""
            hits = _by_suffix(rest, index, [root])
            if hits:
                return hits[0], hits[1:], ""
            raw = rest
            break

    # 3. Relative to the citing file's own directory.
    if (citing.parent / raw).is_file():
        return citing.parent / raw, [], ""

    # 4. Partial or bare, resolved against the index with the CITING repo
    #    preferred. Local preference is what keeps a shared basename from
    #    reading as ambiguous 312 times over; the caller re-opens the question
    #    when the local file turns out to be too short for the cited line.
    hits = _by_suffix(raw, index, roots) if "/" in raw else index.get(raw, [])
    if "/" not in raw:
        hits = sorted(hits, key=lambda q: next(
            (i for i, r in enumerate(roots) if q.is_relative_to(r)), len(roots)))
    local = [q for q in hits if q.is_relative_to(own)]
    if len(local) == 1:
        return local[0], [q for q in hits if q != local[0]], ""
    if len(local) > 1:
        return None, local, f"ambiguous within this repo ({len(local)} matches)"
    if len(hits) == 1:
        return hits[0], [], ""
    if len(hits) > 1:
        # No local file: the earliest sibling that has the name wins, provided it has exactly one.
        for root in siblings:
            first = [q for q in hits if q.is_relative_to(root)]
            if first:
                if len(first) == 1:
                    return first[0], [q for q in hits if q != first[0]], ""
                break
        return None, hits, f"ambiguous across repos ({len(hits)} matches)"

    if "/" not in raw:
        # A bare name was always context-dependent, and one legitimate use
        # here is cross-project provenance: a bare name that lives in a tree
        # that is not a configured sibling (or no longer exists, like the
        # /var/tmp clone after 30 days). Reporting that as broken would train readers to ignore
        # the output, so it is surfaced separately and does not fail the run.
        return None, [], "external or unresolvable basename"
    if missing and not (own / raw).parent.is_dir():
        # A clone without its siblings (the public tree): a path whose directory is not this repo's most
        # likely points into the missing tree. A path under one of this repo's own directories still fails.
        return None, [], "external: unverifiable here, a listed sibling is missing"
    return None, [], "no such file"


def _allow_key(rel: Path, raw: str, span: str) -> str:
    """The exact `.citation-allow` line text for one citation - shared by
    the loader and the checker so a copy-pasted failure line always matches
    what it was copied from."""
    return f"{rel} {raw}:{span}"


def _citation_allow(repo: Path) -> set[str]:
    """Deliberately-fake citations to skip, keyed on `_allow_key`. Missing
    file means no exceptions yet - same convention as `.citation-siblings`,
    and the same reason: committed, and absence is a fact, not a mistake."""
    path = repo / ALLOW_FILE
    if not path.is_file():
        return set()
    return {stripped for ln in path.read_text().splitlines()
            if (stripped := ln.split("#", 1)[0].strip())}


def _renames(repo: Path) -> list[tuple[str, str]]:
    """`(old_prefix, new_prefix)` pairs from `.citation-renames`, in file order. A line is
    `<old_prefix> <new_prefix>   # why`; `#` comments and blank lines are ignored. Missing file means no
    aliases. It lets historical records that cite a path from before a move keep resolving unedited."""
    path = repo / RENAME_FILE
    if not path.is_file():
        return []
    out: list[tuple[str, str]] = []
    for ln in path.read_text().splitlines():
        parts = ln.split("#", 1)[0].split()
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


def _siblings(
    repo: Path, flags: list[str]
) -> tuple[list[Path], dict[Path, list[str]], str | None, list[Path]]:
    """(sibling roots, per-root index subdirectories, error, listed siblings missing here). A
    `.citation-siblings` line is `<path> [subdir ...]`; the subdirs only narrow bare-basename INDEXING, not
    exact resolution."""
    out: list[Path] = []
    missing: list[Path] = []
    restrict: dict[Path, list[str]] = {}
    for x in flags:
        p = Path(x).expanduser().resolve()
        if not p.is_dir():
            return [], {}, f"--sibling {p} is not a directory", []
        out.append(p)
    listed = repo / SIBLING_FILE
    if listed.is_file():
        for ln in listed.read_text().splitlines():
            ln = ln.split("#", 1)[0].strip()
            if not ln:
                continue
            ln, *subdirs = ln.split()
            # Silently skipped when absent: this file is committed and the
            # two repos sit at different paths on the two machines, so a
            # missing sibling is a machine fact, not a mistake.
            p = Path(ln).expanduser().resolve()
            if not p.is_dir():
                missing.append(p)   # named in the summary: its citations silently stop being checked
            elif p != repo and p not in out:
                out.append(p)
                if subdirs:
                    restrict[p] = subdirs
    return out, restrict, None, missing


def _citations(line: str):
    """Every (raw_path, lo, hi, snippet) on one line, continuations included; a quoted snippet after the
    citation rides on its last span, the others carry None."""
    spans = [m.span() for m in URL_SPAN.finditer(line)]
    for m in CITATION.finditer(line):
        if any(a <= m.start() < b for a, b in spans):
            continue
        lo = int(m.group("lo"))
        found = [(lo, int(m.group("hi")) if m.group("hi") else lo)]
        pos = m.end()
        while (c := CONTINUATION.match(line[pos:])):
            lo2 = int(c.group("lo"))
            found.append((lo2, int(c.group("hi") or lo2)))
            pos += c.end()
        s = SNIPPET.match(line[pos:])
        for i, (a, b) in enumerate(found):
            yield m.group("path"), a, b, (s.group("snip") if s and i == len(found) - 1 else None)


def main(argv: list[str] | None = None) -> int:
    """CLI: print each failing citation; returns 1 if any fails (broken, ambiguous, a missing path or a line
    past the end of the file; external and allowlisted ones pass), 2 on a usage or git error, else 0."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("roots", nargs="*", default=None,
                    help="files or directories to scan (default: " + " ".join(DEFAULT_ROOTS) + ")")
    ap.add_argument("--verbose", action="store_true",
                    help="print every resolved citation with the line it names — the "
                         "mode that catches content drift, which the exit code cannot")
    ap.add_argument("--changed", metavar="REF",
                    help="only scan files changed against REF (e.g. HEAD~1, main)")
    ap.add_argument("--sibling", action="append", default=[], metavar="PATH",
                    help=f"another repo whose files may legitimately be cited from here "
                         f"(repeatable). Also read from {SIBLING_FILE} in the repo root, "
                         f"so the default invocation already knows about the sibling.")
    ap.add_argument("--include-paths", action="store_true",
                    help="also check directory-qualified path references that carry no "
                         "line number (docs/foo.md, src/pkg/bar.py) — this repo's "
                         "CLAUDE.md records doc pointers drifting too")
    ap.add_argument("--repo", default=None, help="repo root (default: the git top level)")
    args = ap.parse_args(argv)

    start = Path(args.repo).resolve() if args.repo else Path.cwd()
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=False, capture_output=True, text=True,
                         cwd=start)
    if top.returncode != 0:
        print(f"{start} is not inside a git work tree (use --repo)", file=sys.stderr)
        return 2
    repo = Path(top.stdout.strip()).resolve()

    siblings, restrict, err, missing = _siblings(repo, args.sibling)
    if err:
        print(err, file=sys.stderr)
        return 2

    if args.changed:
        out = subprocess.run(["git", "diff", "--name-only", args.changed],
                             check=False, capture_output=True, text=True, cwd=repo)
        if out.returncode != 0:
            # A bad ref must not read as "0 citations scanned, all clear".
            print(f"git diff --name-only {args.changed} failed:\n"
                  f"{out.stderr.strip()}", file=sys.stderr)
            return 2
        scanned = f"files changed vs {args.changed}"
        files = [repo / f for f in out.stdout.split()
                 if (repo / f).is_file() and (repo / f).suffix in TEXT_SUFFIXES
                 and not SCAN_SKIP_DIRS & set(Path(f).parts) and Path(f).parts[:2] != FROZEN_DIR]
    else:
        names = args.roots or DEFAULT_ROOTS
        roots = [repo / r for r in names if (repo / r).exists()]
        scanned = " ".join(names)
        files = [f for r in roots for f in _walk(r) if f.relative_to(repo).parts[:2] != FROZEN_DIR]

    index = _index([repo, *siblings], restrict)
    allow = _citation_allow(repo)
    renames = _renames(repo)

    total = bad = ambiguous = external = allowed = 0
    failures: list[str] = []
    for f in sorted(set(files)):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = f.relative_to(repo) if f.is_relative_to(repo) else f
        for ln, line in enumerate(text.splitlines(), start=1):
            for raw, lo, hi, snip in _citations(line):
                total += 1
                target, others, why = _resolve(raw, f, repo, siblings, index, missing, renames)
                here = f"{rel}:{ln}"
                span = f"{lo}" + (f"-{hi}" if hi != lo else "")
                key = _allow_key(rel, raw, span)
                if target is None:
                    if why.startswith(("external", "absolute")):
                        external += 1
                        if args.verbose:
                            print(f"  ~  {here} -> {raw}:{span}  ({why})")
                        continue
                    if key in allow:
                        allowed += 1
                        if args.verbose:
                            print(f"  ~  {here} -> {raw}:{span}  (allowlisted: {why})")
                        continue
                    # Ambiguous is a FAILURE, and printed by default. As a
                    # non-failing verbose-only line it hid the one citation
                    # class this wave actually logged: rc 0, nothing on
                    # screen, wrong file.
                    ambiguous += 1
                    cands = ", ".join(str(q) for q in others[:4])
                    failures.append(f"{here}  cites {raw}:{span}  -- {why}: {cands}")
                    continue
                n = _line_count(target)
                # Check BOTH bounds: a swapped or typo'd range such as 500-9
                # has hi <= n and lo far past EOF, and line 0 names a line
                # that does not exist — both sailed through an hi-only check.
                if max(lo, hi) > n or lo == 0:
                    if key in allow:
                        allowed += 1
                        if args.verbose:
                            print(f"  ~  {here} -> {raw}:{span}  (allowlisted: "
                                  f"{target} has {n} lines)")
                        continue
                    bad += 1
                    rescued = [q for q in others if _line_count(q) >= max(lo, hi)]
                    if rescued:
                        # The recorded defect, stated precisely: the citation
                        # is satisfiable in the sibling and not here, so it
                        # named the wrong repo. Qualify the path.
                        failures.append(
                            f"{here}  cites {raw}:{span}  -- {target} has {n} lines, "
                            f"but {rescued[0]} would satisfy it: QUALIFY THE PATH")
                    else:
                        # Say when the only candidate was in a SIBLING: a bare
                        # a bare citation with no local file resolves there
                        # by elimination, and naming the sibling as if it were
                        # the intended target would be a confident wrong
                        # attribution.
                        via = ("" if target.is_relative_to(repo)
                               else f" (no local candidate; nearest was {target})")
                        failures.append(
                            f"{here}  cites {raw}:{span}  -- {target} has {n} lines"
                            if not via else
                            f"{here}  cites {raw}:{span}  -- unresolved here{via}, "
                            f"which has {n} lines")
                    continue
                if snip is not None and _norm(snip) not in _norm(_lines(target, lo, hi)):
                    problem = f'snippet "{snip}" not in those lines: {_nth_line(target, lo).strip()[:60]!r}'
                elif snip is None and target.suffix == ".md":
                    problem = ("a line citation into a living document; cite its section heading, or add a "
                               "quoted snippet")
                else:
                    problem = ""
                if problem:
                    if key in allow:
                        allowed += 1
                        if args.verbose:
                            print(f"  ~  {here} -> {raw}:{span}  (allowlisted: {problem})")
                        continue
                    bad += 1
                    failures.append(f"{here}  cites {raw}:{span}  -- {problem}")
                    continue
                if args.verbose:
                    body = _nth_line(target, lo).strip()
                    print(f"  ok {here} -> {raw}:{span}  | {body[:88]}")

    if args.include_paths:
        for f in sorted(set(files)):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            rel = f.relative_to(repo) if f.is_relative_to(repo) else f
            for ln, line in enumerate(text.splitlines(), start=1):
                spans = [m.span() for m in URL_SPAN.finditer(line)]
                for m in PATH_ONLY.finditer(line):
                    if any(a <= m.start() < b for a, b in spans):
                        continue
                    raw = m.group("path")
                    total += 1
                    if (repo / raw).exists():
                        if args.verbose:
                            print(f"  ok {rel}:{ln} -> {raw}")
                        continue
                    if any((sib / raw).exists() for sib in siblings):
                        external += 1
                        if args.verbose:
                            print(f"  ~  {rel}:{ln} -> {raw} (in a sibling repo)")
                        continue
                    # A `src/`/`docs/`-prefixed path is a claim about THIS
                    # repo's layout. The flag says "validate"; observing it
                    # and exiting 0 was the weaker thing the help promised.
                    bad += 1
                    failures.append(f"{rel}:{ln}  cites {raw}  -- no such path")

    print(f"\n{total} citation(s) scanned across {len(set(files))} file(s) "
          f"in [{scanned}]: {total - bad - ambiguous - external - allowed} resolve, "
          f"{bad} broken, {ambiguous} unresolvable, {external} external"
          + (f", {allowed} allowlisted" if allowed else "")
          + (f"; siblings: {', '.join(s.name for s in siblings)}" if siblings else ""))
    for p in missing:
        # Not a failure (a sibling's path is a machine fact), but never silent: a citation into it that has
        # no local candidate now counts as external.
        print(f"missing sibling: {p} (listed in {SIBLING_FILE}; its citations are not checked here)")
    if failures:
        print("\nBROKEN:")
        for x in failures:
            print(f"  {x}")
        print("\nA citation that resolves is not thereby CORRECT — the line may have "
              "changed\nunder a number that did not. Re-read with --verbose after any "
              "edit that\nmoves lines in a cited file.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
