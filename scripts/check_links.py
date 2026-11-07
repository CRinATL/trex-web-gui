#!/usr/bin/env python3
"""Check that every internal link points at a file that exists and, for a Markdown target, at its heading.

Two kinds of link are checked, in every file git tracks (not symlinks, whose target is checked as itself,
and not `backend/trex_web_gui/_vendor/`, which is generated):

  - In a Markdown file, a relative link `[text](path)`, `[text](path#anchor)`, `[text](#anchor)`,
    `[text](<path>)` or `[text](/path)` (from the repo root, as GitHub resolves it), an image
    `![alt](path)`, a badge `[![alt](img)](path)` (both targets) and a reference definition
    `[name]: path` (a footnote `[^1]: …` is not one). A link with a scheme (`https:`,
    `mailto:`) is external and skipped, as is anything inside a fenced code block or inline code.
  - In any file, a URL into this repository on GitHub: REPO_URL + `#anchor` (the README as GitHub shows it)
    and REPO_URL + `/blob/main/<path>[#anchor]` or `/tree/main/<path>`. Release notes and shipped code carry
    these, and a reader of an installed wheel cannot be sent a corrected link. These are checked wherever
    they appear, inline code included: a reader copies a URL out of a code span too.

An anchor is checked against the target's headings, slugged as GitHub does (the rendered text: link text
kept, HTML tags dropped; lower case; punctuation other than `-` and `_` dropped; spaces to `-`; a repeat
gets the first free `-1`, `-2`), plus any `<a id="…">` or `<a name="…">`, and `#readme` on README.md.
Anchors are compared case-sensitively, the conservative reading. A line anchor (`#L10`, `#L10C5-L12C3`) on
a GitHub URL is not a heading and is not checked; a query string (`?plain=1`) is dropped. A ``` or ~~~
line at any indent opens a fence (so a fence inside a list item is one). Not handled, none of them in the
repo: setext headings (`Title` over `===`), `_emphasis_` or combining marks in a heading, indented code
blocks, HTML `<a href>`, a path holding `)`, and a link whose text wraps onto the next line.
A URL built in code from parts (`PROJECT_URL + "#…"`) is not seen here; the test that owns it checks it.

Exit 0 when every link resolves, 1 otherwise, one `file:line: link: why` line per broken link.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

REPO_URL = "https://github.com/CRinATL/trex-web-gui"
SKIP_PREFIXES = ("backend/trex_web_gui/_vendor/",)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$")
HTML_ANCHOR = re.compile(r'<a\s+(?:name|id)="([^"]+)"')
INLINE_CODE = re.compile(r"`[^`]*`")
# Link text may hold one level of brackets (a badge's image); the target is `<…>` or has no space; an
# optional "title" or 'title' follows.
MD_LINK = re.compile(r"\[(?:[^\[\]]|\[[^\]]*\])*\]\((?:<([^>]+)>|([^)\s]+))(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)")
# An image inside a badge's link text: MD_LINK takes the outer link whole, so its images get their own pass.
INNER_IMAGE = re.compile(r"!\[[^\]]*\]\((?:<([^>]+)>|([^)\s]+))")
# CommonMark: the label is not a footnote (`[^1]:`), and only a title may follow the destination, so a prose
# line such as `[ERROR]: something failed` is not a definition.
REF_DEF = re.compile(r"^ {0,3}\[[^\]^][^\]]*\]:\s*(?:<([^>]+)>|(\S+))"
                     r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*$")
# The lookahead keeps a sibling repo (`trex-web-gui-records`) out. The path stops at whitespace, quotes,
# brackets, `#`, `?` and a backslash (a string literal's `\n`); a trailing full stop or comma is trimmed.
GH_URL = re.compile(re.escape(REPO_URL)
                    + r"(?![\w-])(?:/(?:blob|tree)/main/([^\s#?)\"'`>\]\\]+))?(?:\?[^\s#\"'`)>\]]*)?"
                    + r"(?:#([A-Za-z0-9_-]+))?")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
LINE_ANCHOR = re.compile(r"^L\d+(?:C\d+)?(?:-L\d+(?:C\d+)?)?$")


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading's source text (before the -1, -2 of a repeat): it slugs the
    rendered text, so a link keeps its text and an HTML tag goes."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


class _Fences:
    """Fenced-code state for one file: a fence closes only on the same character, at least as long."""

    def __init__(self) -> None:
        """Outside any fence."""
        self.open: str | None = None

    def step(self, line: str) -> bool:
        """Feed one line; True when it is a fence line or inside a fence (so it holds no links)."""
        m = FENCE.match(line)
        if self.open is None:
            if m:
                self.open = m.group(1)
            return bool(m)
        # A closing fence is the opening character only, at least as many: `~~~` never closes on ```.
        if m and len(m.group(1)) >= len(self.open) and not line.strip().lstrip(self.open[0]):
            self.open = None
        return True


def anchors(text: str, readme: bool = False) -> set[str]:
    """Every anchor a Markdown text offers: its headings' slugs (outside code fences), its HTML anchors,
    and `readme` when it is the README GitHub shows on the repo page."""
    found: set[str] = {"readme"} if readme else set()
    slugs: set[str] = set()  # headings only: repeats are numbered among headings
    fences = _Fences()
    for line in text.splitlines():
        if fences.step(line):
            continue
        m = HEADING.match(line)
        if m:
            base = s = slug(m.group(1))
            n = 0
            while s in slugs:  # github-slugger: the first free -n
                n += 1
                s = f"{base}-{n}"
            slugs.add(s)
            found.add(s)
        found.update(HTML_ANCHOR.findall(line))
    return found


def links(rel: str, text: str) -> list[tuple[int, str, str, str]]:
    """The internal links of one file: (line number, the link as written, target path relative to the
    repo root, anchor or ""). A relative Markdown link resolves against the file's directory, one starting
    `/` against the repo root; a bare `#anchor` targets the file itself."""
    out: list[tuple[int, str, str, str]] = []
    is_md = rel.endswith(".md")
    here = Path(rel).parent
    fences = _Fences()
    for i, line in enumerate(text.splitlines(), 1):
        if is_md and not fences.step(line):
            prose = INLINE_CODE.sub("", line)
            raws = []
            for m in MD_LINK.finditer(prose):
                raws.append(m.group(1) or m.group(2))
                text = m.group(0)[:m.group(0).rindex("](")]
                raws += [i.group(1) or i.group(2) for i in INNER_IMAGE.finditer(text)]
            ref = REF_DEF.match(prose)
            if ref:
                raws.append(ref.group(1) or ref.group(2))
            for raw in raws:
                if SCHEME.match(raw):
                    continue  # external: https:, mailto:, ...
                path, _, anchor = raw.partition("#")
                path = unquote(path)
                if not path:
                    target = rel
                elif path.startswith("/"):
                    target = _normalise(Path(path.lstrip("/")))
                else:
                    target = _normalise(here / path)
                out.append((i, raw, target, anchor))
        for m in GH_URL.finditer(line):
            path, anchor = m.group(1), m.group(2) or ""
            if anchor and LINE_ANCHOR.match(anchor):
                anchor = ""
            if path:
                out.append((i, m.group(0), unquote(path.rstrip(".,;:")).rstrip("/"), anchor))
            elif anchor:
                out.append((i, m.group(0), "README.md", anchor))
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
            continue  # generated, checked as its target, or tracked but deleted in the work tree
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
                    cache[target] = anchors(tpath.read_text(encoding="utf-8"), readme=target == "README.md")
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
