#!/usr/bin/env python3
"""Check that every internal link points at a file that exists and, for a Markdown target, at its heading.

Two kinds of link are checked, in every file git tracks (not symlinks, whose target is checked as itself,
and not `backend/trex_web_gui/_vendor/`, which is generated):

  - In a Markdown file, a relative link `[text](path)`, `[text](path#anchor)` or `[text](#anchor)`, and an
    image `![alt](path)`. A link with a scheme (`https:`, `mailto:`) is external and skipped, as is anything
    inside a fenced code block or inline code.
  - In any file, a URL into this repository on GitHub: `https://github.com/CRinATL/trex-web-gui#anchor` (the
    README as GitHub shows it) and `.../blob/main/<path>[#anchor]` or `.../tree/main/<path>`. Release notes
    and shipped code carry these, and a reader of an installed wheel cannot be sent a corrected link.

An anchor is checked against the target's headings, slugged as GitHub does (lower case, punctuation other
than `-` and `_` dropped, spaces to `-`, a repeated heading gets `-1`, `-2`), plus any `<a id="…">` or
`<a name="…">`. A URL built in code from parts (`PROJECT_URL + "#…"`) is not seen here; the test that owns
it checks it.

Exit 0 when every link resolves, 1 otherwise, one `file:line: link: why` line per broken link.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/CRinATL/trex-web-gui"
SKIP_PREFIXES = ("backend/trex_web_gui/_vendor/",)
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
HTML_ANCHOR = re.compile(r'<a\s+(?:name|id)="([^"]+)"')
INLINE_CODE = re.compile(r"`[^`]*`")
MD_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# The lookahead keeps a sibling repo (`trex-web-gui-records`) out; a full stop after a path is trimmed.
GH_URL = re.compile(re.escape(REPO_URL)
                    + r"(?![\w-])(?:/(?:blob|tree)/main/([^\s#)\"'`>\]]+))?(?:#([A-Za-z0-9_-]+))?")


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading's text (before the -1, -2 of a repeat)."""
    text = heading.strip().lower().replace("`", "")
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every anchor a Markdown text offers: its headings' slugs (outside code fences) and its HTML anchors."""
    found: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = HEADING.match(line)
        if m:
            s = slug(m.group(1))
            n = seen.get(s, 0)
            found.add(s if n == 0 else f"{s}-{n}")
            seen[s] = n + 1
        found.update(HTML_ANCHOR.findall(line))
    return found


def links(rel: str, text: str) -> list[tuple[int, str, str, str]]:
    """The internal links of one file: (line number, the link as written, target path relative to the
    repo root, anchor or ""). A relative Markdown link resolves against the file's directory; a bare
    `#anchor` targets the file itself."""
    out: list[tuple[int, str, str, str]] = []
    is_md = rel.endswith(".md")
    here = Path(rel).parent
    fenced = False
    for i, line in enumerate(text.splitlines(), 1):
        if is_md and FENCE.match(line):
            fenced = not fenced
            continue
        if is_md and not fenced:
            for m in MD_LINK.finditer(INLINE_CODE.sub("", line)):
                raw = m.group(1)
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw):
                    continue  # external: https:, mailto:, ...
                path, _, anchor = raw.partition("#")
                target = _normalise(here / path) if path else rel
                out.append((i, raw, target, anchor))
        for m in GH_URL.finditer(line):
            path, anchor = m.group(1), m.group(2)
            if path or anchor:
                path = path.rstrip(".,;:") if path else None
                out.append((i, m.group(0), path.rstrip("/") if path else "README.md", anchor or ""))
    return out


def _normalise(p: Path) -> str:
    """A repo-relative path with `.` and `..` folded, without touching the disk (a `..` above the root
    stays, so the caller can refuse it)."""
    parts: list[str] = []
    for part in p.parts:
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
        elif part != ".":
            parts.append(part)
    return "/".join(parts) or "."


def check(repo: Path, files: list[str]) -> list[str]:
    """The broken links among `files` (repo-relative), as `file:line: link: why`."""
    broken: list[str] = []
    cache: dict[str, set[str]] = {}
    for rel in files:
        path = repo / rel
        if rel.startswith(SKIP_PREFIXES) or path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary
        for line, raw, target, anchor in links(rel, text):
            tpath = repo / target
            if target.startswith("..") or not tpath.exists():
                broken.append(f"{rel}:{line}: {raw}: no such file {target}")
                continue
            if anchor and tpath.is_file() and tpath.suffix == ".md":
                if target not in cache:
                    cache[target] = anchors(tpath.read_text(encoding="utf-8"))
                if anchor not in cache[target]:
                    broken.append(f"{rel}:{line}: {raw}: {target} has no heading #{anchor}")
    return broken


def tracked(repo: Path) -> list[str]:
    """The files git tracks in `repo`."""
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                         capture_output=True, text=True, check=True)
    return [f for f in out.stdout.split("\0") if f]


def main(argv: list[str] | None = None) -> int:
    """Check the repo's tracked files; print each broken link and a summary."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    files = tracked(args.repo)
    broken = check(args.repo, files)
    for b in broken:
        print(b)
    print(f"links: {len(files)} files, {len(broken)} broken")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
