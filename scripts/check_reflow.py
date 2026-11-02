#!/usr/bin/env python3
"""Check that a layout-only edit of a Markdown file kept its facts.

The rule. A reflow (re-wrapping, splitting a paragraph into bullets, joining two sentences) changes how the
text is laid out and nothing it says. This script compares the old text, `git show REV:PATH`, with the new
text, `git show REV2:PATH` (`--to`) or the work-tree file, and reports every difference in the facts below.
A change that is meant (a new label, a corrected number) goes in an allow file, so that it is named and
has a reason.

Usage
-----
    python3 -I scripts/check_reflow.py --rev REV [--to REV2] [--allow FILE] PATH [PATH ...]

Run it from the repo top level; PATH is relative to it.

Categories (fenced blocks are removed first, then each is extracted)
--------------------------------------------------------------------
  word        a set of case-folded words, from the text without code spans and link targets, and without
              negations
  code        a multiset of code spans, inner whitespace collapsed
  code-split  a multiset of spans that contain a newline; only `+` is reported (a span that was on one line)
  number      a multiset of numbers, from the same text as `word`
  link        a multiset of `[text](target)` and `![text](target)`
  fence       the fenced blocks, byte for byte
  heading     lines starting `#` to `######` and a space
  table       right-stripped lines that start with `|`, in order
  negation    a multiset of not, no, never, only, without, unless, cannot, except, nor and every n't word

Signs: `-` is in the old text only, `+` in the new text only, `~ order` the same items in another order
(fence, heading and table).

Info only (never a failure): a change in the count of a bold span, and in the count of a content word that
occurs on both sides (function words and negations are left out).

Allow file: one `PATH<TAB>CATEGORY<TAB>SIGN<TAB>TOKEN<TAB>REASON` per line; `#` and blank lines are skipped.
One line covers one occurrence, and a line that matches nothing fails the run. In a printed TOKEN a newline
is `\\n` and a tab is `\\t`, so a printed difference can be copied into the allow file.

Exit codes: 0 every path keeps its facts; 1 a difference not allowed, or an allow line not used; 2 a usage
error (unknown revision, a path missing at either side, an unreadable or malformed allow file).
"""
import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

NEGATIONS = {"not", "no", "never", "only", "without", "unless", "cannot", "except", "nor"}
FUNCTION_WORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it",
                  "its", "of", "on", "so", "that", "the", "then", "this", "to", "with"}
CATEGORIES = ("code", "code-split", "fence", "heading", "link", "negation", "number", "table", "word")
SIGNS = ("-", "+", "~")

WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
# The inner image's '!' is taken by [^\[\]], never by an optional '!?', so the alternation
# is unambiguous and the match is linear (CodeQL py/redos).
LINK_RE = re.compile(
    r"(!?)\[((?:[^\[\]]|\[[^\[\]]*\]\([^)]*\))*)\]\(\s*<?([^)\s>]*)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
HEADING_RE = re.compile(r"^#{1,6} ")
BACKTICKS_RE = re.compile(r"`+")
BLANK_LINE_RE = re.compile(r"(\n[ \t]*\n)")
SPAN_MARK = "\x00"


class Diff(NamedTuple):
    """One difference: the category, the sign (`-`, `+`, `~`) and the token."""

    category: str
    sign: str
    token: str


class Allow(NamedTuple):
    """One allow-file line."""

    path: str
    category: str
    sign: str
    token: str
    reason: str


class Facts(NamedTuple):
    """Everything extracted from one text."""

    words: Counter[str]
    negations: Counter[str]
    code: Counter[str]
    code_split: Counter[str]
    numbers: Counter[str]
    links: Counter[str]
    bold: Counter[str]
    fences: list[str]
    headings: list[str]
    tables: list[str]


def _squash(text: str) -> str:
    """Collapses every whitespace run to one space."""
    return " ".join(text.split())


def _split_fences(text: str) -> tuple[list[str], str]:
    """Returns the fenced blocks (fence lines included, joined by newlines) and the text without them."""
    blocks: list[str] = []
    kept: list[str] = []
    current: list[str] | None = None
    for line in text.split("\n"):
        is_fence = line.lstrip().startswith("```")
        if current is None:
            if is_fence:
                current = [line]
            else:
                kept.append(line)
        else:
            current.append(line)
            if is_fence:
                blocks.append("\n".join(current))
                current = None
    if current is not None:
        blocks.append("\n".join(current))
    return blocks, "\n".join(kept)


def _mask_spans(text: str) -> tuple[str, list[str]]:
    """Replaces each code span (CommonMark pairing, within a paragraph) by SPAN_MARK; returns the masked text
    and the spans' raw inner text, in order."""
    spans: list[str] = []
    out: list[str] = []
    for index, chunk in enumerate(BLANK_LINE_RE.split(text)):
        if index % 2:
            out.append(chunk)
            continue
        runs = []
        for m in BACKTICKS_RE.finditer(chunk):
            start = m.start()
            if start and chunk[start - 1] == "\\":
                start += 1
            if m.end() > start:
                runs.append((start, m.end()))
        pos = 0
        j = 0
        while j < len(runs):
            width = runs[j][1] - runs[j][0]
            k = next((n for n in range(j + 1, len(runs)) if runs[n][1] - runs[n][0] == width), None)
            if k is None:
                j += 1
                continue
            out.append(chunk[pos:runs[j][0]] + SPAN_MARK)
            spans.append(chunk[runs[j][1]:runs[k][0]])
            pos = runs[k][1]
            j = k + 1
        out.append(chunk[pos:])
    return "".join(out), spans


def _restore(fragment: str, spans: list[str], before: int) -> str:
    """Puts the code spans back (as `inner`) into a fragment whose first mark is span number `before`."""
    parts = fragment.split(SPAN_MARK)
    result = parts[0]
    for offset, part in enumerate(parts[1:]):
        result += "`" + _squash(spans[before + offset]) + "`" + part
    return result


def _links(masked: str, spans: list[str]) -> tuple[Counter[str], str]:
    """Returns the link multiset and the text with each link replaced by its link text (target dropped)."""
    found: Counter[str] = Counter()

    def tokenise(m: re.Match[str]) -> str:
        """Counts one link, with its text restored and squashed."""
        before = masked[:m.start()].count(SPAN_MARK)
        text = _squash(_restore(m.group(2), spans, before))
        found[f"{m.group(1)}[{text}]({m.group(3)})"] += 1
        return m.group(2)

    plain = LINK_RE.sub(tokenise, masked)
    # a link inside a link's text: only the outer one was counted above, so strip the inner targets too
    while True:
        stripped = LINK_RE.sub(lambda m: m.group(2), plain)
        if stripped == plain:
            return found, plain
        plain = stripped


def facts(text: str) -> Facts:
    """Extracts every category's facts from one Markdown text."""
    fences, prose = _split_fences(text)
    masked, spans = _mask_spans(prose)
    code = Counter(_squash(s) for s in spans)
    code_split = Counter(_squash(s) for s in spans if "\n" in s)
    links, plain = _links(masked, spans)
    bold: Counter[str] = Counter()
    for m in BOLD_RE.finditer(masked):
        before = masked[:m.start()].count(SPAN_MARK)
        bold["**" + _squash(_restore(m.group(1), spans, before)) + "**"] += 1
    plain = plain.replace(SPAN_MARK, " ")
    tokens = [w.casefold() for w in WORD_RE.findall(plain)]
    negations: Counter[str] = Counter()
    words: Counter[str] = Counter()
    for token in tokens:
        norm = token.replace("’", "'")
        if norm in NEGATIONS or norm.endswith("n't"):
            negations[norm] += 1
        else:
            words[token] += 1
    lines = prose.split("\n")
    return Facts(words, negations, code, code_split, Counter(NUMBER_RE.findall(plain)), links, bold, fences,
                 [ln for ln in lines if HEADING_RE.match(ln)],
                 [ln.rstrip() for ln in lines if ln.lstrip().startswith("|")])


def _multiset(category: str, old: Counter[str], new: Counter[str]) -> list[Diff]:
    """One Diff per occurrence that is in only one of the two multisets."""
    out = [Diff(category, "-", tok) for tok, n in (old - new).items() for _ in range(n)]
    out += [Diff(category, "+", tok) for tok, n in (new - old).items() for _ in range(n)]
    return out


def _sequence(category: str, old: list[str], new: list[str]) -> list[Diff]:
    """Multiset difference of two ordered lists, or `~ order` when only the order differs."""
    out = _multiset(category, Counter(old), Counter(new))
    if not out and old != new:
        out.append(Diff(category, "~", "order"))
    return out


def compare(old: str, new: str) -> list[Diff]:
    """Every fact difference between two texts, sorted by category, sign and token."""
    a, b = facts(old), facts(new)
    out = [Diff("word", "-", w) for w in sorted(a.words.keys() - b.words.keys())]
    out += [Diff("word", "+", w) for w in sorted(b.words.keys() - a.words.keys())]
    out += _multiset("negation", a.negations, b.negations)
    out += _multiset("code", a.code, b.code)
    out += [d for d in _multiset("code-split", a.code_split, b.code_split) if d.sign == "+"]
    out += _multiset("number", a.numbers, b.numbers)
    out += _multiset("link", a.links, b.links)
    out += _sequence("fence", a.fences, b.fences)
    out += _sequence("heading", a.headings, b.headings)
    out += _sequence("table", a.tables, b.tables)
    return sorted(out)


def info(old: str, new: str) -> list[str]:
    """The `info:` lines (no path): bold spans added or dropped, and content words whose count changed."""
    a, b = facts(old), facts(new)
    lines = [f"info: bold - {t}" for t in sorted((a.bold - b.bold).elements())]
    lines += [f"info: bold + {t}" for t in sorted((b.bold - a.bold).elements())]
    for word in sorted(a.words.keys() & b.words.keys()):
        if word in FUNCTION_WORDS or word.isdigit() or a.words[word] == b.words[word]:
            continue
        lines.append(f"info: word {word} {a.words[word]}->{b.words[word]}")
    return lines


def _printable(token: str) -> str:
    """A token as printed and as written in an allow file: newline as `\\n`, tab as `\\t`."""
    return token.replace("\n", "\\n").replace("\t", "\\t")


def parse_allow(text: str) -> list[Allow]:
    """Parses an allow file; raises ValueError naming the line number of a malformed line."""
    out = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t", 4)
        if len(fields) != 5:
            raise ValueError(f"line {number}: want PATH, CATEGORY, SIGN, TOKEN and REASON separated by tabs")
        path, category, sign, token, reason = fields
        if category not in CATEGORIES:
            raise ValueError(f"line {number}: unknown category {category!r}")
        if sign not in SIGNS:
            raise ValueError(f"line {number}: unknown sign {sign!r}")
        if not reason.strip():
            raise ValueError(f"line {number}: the reason is empty")
        out.append(Allow(path, category, sign, token, reason.strip()))
    return out


class UsageError(Exception):
    """A revision, path or allow file that cannot be read: exit 2."""


def _git(*args: str, cwd: Path | None = None) -> str:
    """Runs git and returns stdout; raises UsageError on a non-zero exit."""
    proc = subprocess.run(["git", *args], capture_output=True, cwd=cwd)
    if proc.returncode:
        raise UsageError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    try:
        return proc.stdout.decode()
    except UnicodeDecodeError as exc:
        raise UsageError(f"git {' '.join(args)}: not UTF-8 ({exc})") from exc


def _check_path(path: str, args: argparse.Namespace, top: Path, allows: list[Allow]) -> int:
    """Compares one path, prints its lines, returns its problem count."""
    old = _git("show", f"{args.rev}:{path}", cwd=top)
    if args.to:
        new = _git("show", f"{args.to}:{path}", cwd=top)
    else:
        try:
            new = (top / path).read_text()
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError(f"{path}: {exc}") from exc
    mine = Counter((a.category, a.sign, a.token) for a in allows if a.path == path)
    reasons = {(a.category, a.sign, a.token): a.reason for a in allows if a.path == path}
    problems = 0
    for diff in compare(old, new):
        key = (diff.category, diff.sign, _printable(diff.token))
        text = f"{diff.category} {diff.sign} {key[2]}"
        if mine[key] > 0:
            mine[key] -= 1
            print(f"{path}: allowed {text} ({reasons[key]})")
        else:
            problems += 1
            print(f"{path}: {text}")
    for (category, sign, token), left in sorted(mine.items()):
        for _ in range(left):
            problems += 1
            print(f"{path}: unused allow: {category} {sign} {token}")
    for line in info(old, new):
        print(f"{path}: {line}")
    print(f"check_reflow: {path} " + (f"{problems} problem(s)" if problems else "ok"))
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI: print each difference; returns 0 if every path keeps its facts, 1 if not, 2 on a usage error."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rev", required=True, help="the revision holding the old text")
    ap.add_argument("--to", help="the revision holding the new text (default: the work-tree file)")
    ap.add_argument("--allow", help="allow file: PATH, CATEGORY, SIGN, TOKEN, REASON separated by tabs")
    ap.add_argument("paths", nargs="+", metavar="PATH", help="Markdown files, relative to the repo top level")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        allows: list[Allow] = []
        if args.allow:
            try:
                allows = parse_allow(Path(args.allow).read_text())
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise UsageError(f"{args.allow}: {exc}") from exc
        top = Path(_git("rev-parse", "--show-toplevel").strip())
        problems = sum(_check_path(p, args, top, allows) for p in args.paths)
    except UsageError as exc:
        print(f"check_reflow: {exc}", file=sys.stderr)
        return 2
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
