"""scripts/check_reflow.py: what a layout-only edit of a Markdown file may change (nothing), and what it
reports."""
import importlib.util
import subprocess
from pathlib import Path

import pytest
from support import ROOT

spec = importlib.util.spec_from_file_location("check_reflow", ROOT / "scripts" / "check_reflow.py")
assert spec and spec.loader
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)
Diff = cr.Diff

SAMPLE = """# Title

| a | b |
|---|---|
| 1 | two |

```sh
echo   hi
```

Press `Start` and read [the guide](guide.md) within 5 s.
"""

PR17_BEFORE = """- **Capture viewer**: a clicked packet is dissected in the packet worker, under its 5 s call timeout and memory
  limit, instead of on the backend's scapy thread; a frame over 9 238 bytes is dissected that far and the rest is
  one Raw layer. A worker that is busy or closed answers 503 `engine_busy` or `engine_closed`. Live capture
  summaries read only a frame's first 2 048 bytes (#14).
"""  # noqa: E501

PR17_AFTER = """- **Capture viewer** (#14):
  - A clicked packet is dissected in the packet worker, under its 5 s call timeout and memory limit, instead of
    on the backend's scapy thread.
  - A frame over 9 238 bytes is dissected that far, and the rest is one Raw layer.
  - A worker that is busy or closed answers 503 `engine_busy` or `engine_closed`.
  - Live capture summaries read only a frame's first 2 048 bytes.
"""  # noqa: E501


def test_identical_text_has_no_difference() -> None:
    """A heading, a table row, a fence, a code span, a link and a number: the same text differs in nothing."""
    assert cr.compare(SAMPLE, SAMPLE) == []
    assert cr.info(SAMPLE, SAMPLE) == []


def test_pr17_capture_viewer_item_keeps_its_facts() -> None:
    """The precedent: PR #17's capture viewer item, before and after, keeps every fact."""
    assert cr.compare(PR17_BEFORE, PR17_AFTER) == []


def test_a_dropped_word_is_reported() -> None:
    """Dropping the only occurrence of a word is a `word -` difference."""
    assert cr.compare("open the browser now\n", "open the now\n") == [Diff("word", "-", "browser")]


def test_case_is_not_a_difference() -> None:
    """Words are case-folded."""
    assert cr.compare("a TRex service\n", "A TRex service\n") == []


def test_code_span_whitespace_is_collapsed_but_a_new_split_is_reported() -> None:
    """Inner whitespace of a span does not count; a span that now crosses a line does."""
    assert cr.compare("see `a  b` here\n", "see `a b` here\n") == []
    one = "run `journalctl --user -u trex-web-gui` now\n"
    two = "run `journalctl --user\n-u trex-web-gui` now\n"
    assert cr.compare(one, two) == [Diff("code-split", "+", "journalctl --user -u trex-web-gui")]
    assert cr.compare(two, one) == []


def test_numbers_are_a_multiset() -> None:
    """Losing one of two equal numbers is a difference."""
    assert cr.compare("wait 5 s then 5 s\n", "wait 5 s then\n") == [Diff("number", "-", "5")]


def test_a_changed_link_target_is_reported() -> None:
    """A link is its text and its target."""
    assert cr.compare("see [x](a.md)\n", "see [x](b.md)\n") == [
        Diff("link", "+", "[x](b.md)"), Diff("link", "-", "[x](a.md)")]


def test_fences_are_compared_byte_for_byte_and_in_order() -> None:
    """One changed space is a `-` and a `+`; swapped fences are a `~ order`."""
    old = "```\na b\n```\n"
    new = "```\na  b\n```\n"
    assert cr.compare(old, new) == [Diff("fence", "+", "```\na  b\n```"), Diff("fence", "-", "```\na b\n```")]
    first, second = "```\none\n```\n", "```\ntwo\n```\n"
    assert cr.compare(first + "\ntext\n\n" + second, second + "\ntext\n\n" + first) == [
        Diff("fence", "~", "order")]


def test_headings_and_table_rows_are_frozen() -> None:
    """A reworded heading or cell is `-`/`+`; swapped headings or rows are `~ order`."""

    def only(category: str, old: str, new: str) -> list:
        """The differences of one category (a reworded line changes its words too)."""
        return [d for d in cr.compare(old, new) if d.category == category]

    assert only("heading", "# One\n", "# Uno\n") == [
        Diff("heading", "+", "# Uno"), Diff("heading", "-", "# One")]
    assert cr.compare("# A\n\ntext\n\n## B\n", "## B\n\ntext\n\n# A\n") == [Diff("heading", "~", "order")]
    assert only("table", "| a | b |\n", "| a | c |\n") == [
        Diff("table", "+", "| a | c |"), Diff("table", "-", "| a | b |")]
    rows = ["| a | 1 |", "| b | 2 |"]
    swapped = "\n".join(reversed(rows)) + "\n"
    assert cr.compare("\n".join(rows) + "\n", swapped) == [Diff("table", "~", "order")]


def test_a_dropped_not_is_a_difference() -> None:
    """A negation is a multiset of its own, and never a `word`."""
    assert cr.compare("it is not shown and is not kept\n", "it is shown and is not kept\n") == [
        Diff("negation", "-", "not")]
    assert cr.compare("it can't open; it can close\n", "it can open; it can close\n") == [
        Diff("negation", "-", "can't")]
    assert cr.compare("it can open here\n", "it can open except here\n") == [Diff("negation", "+", "except")]
    assert cr.compare("it isn’t shown\n", "it isn't shown\n") == []


def test_bold_and_word_count_drops_are_info_only() -> None:
    """Bold changes and word-count changes print info lines and never fail."""
    assert cr.compare("a b\n", "a **Control** b\n") == [Diff("word", "+", "control")]
    assert cr.compare("use Control\n", "use **Control**\n") == []
    assert "info: bold + **Control**" in cr.info("use Control\n", "use **Control**\n")
    assert cr.info("port a port b port\n", "port a port b\n") == ["info: word port 3->2"]
    assert cr.compare("port a port b port\n", "port a port b\n") == []
    assert cr.info("port a port b\n", "port a port b port\n") == ["info: word port 2->3"]
    assert cr.info("the x the y\n", "the x y\n") == []
    assert cr.info("not x not y\n", "not x y\n") == []
    assert cr.info("x or y or z\n", "x or y z\n") == ["info: word or 2->1"]


def test_parse_allow() -> None:
    """A valid line is one Allow; a short line, an unknown category or an empty reason names line 1."""
    got = cr.parse_allow("# note\n\ndoc.md\tword\t+\tstatus\tnew label\n")
    assert got == [cr.Allow("doc.md", "word", "+", "status", "new label")]
    for bad in ("doc.md\tword\t+\tstatus\n", "doc.md\tcolour\t+\tx\twhy\n", "doc.md\tword\t+\tx\t \n"):
        with pytest.raises(ValueError, match="line 1"):
            cr.parse_allow(bad)


def git(repo: Path, *args: str) -> None:
    """Runs git in repo."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp git repo with doc.md committed, the current directory."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "doc.md").write_text("The browser shows the port.\nIt is not kept and is not shown.\n")
    git(tmp_path, "add", "doc.md")
    git(tmp_path, "commit", "-qm", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The exit codes and the printed lines, on a real git repo."""
    doc = repo / "doc.md"

    def run(*argv: str) -> tuple[int, str]:
        """Runs main and returns (exit code, stdout)."""
        rc = cr.main(list(argv))
        return rc, capsys.readouterr().out

    doc.write_text("The browser\nshows the port.\nIt is not kept\nand is not shown.\n")
    rc, out = run("--rev", "HEAD", "doc.md")
    assert rc == 0 and out.endswith("check_reflow: doc.md ok\n")

    doc.write_text("The shows the port.\nIt is not kept and is not shown.\n")
    rc, out = run("--rev", "HEAD", "doc.md")
    assert rc == 1
    assert "doc.md: word - browser\n" in out and out.endswith("check_reflow: doc.md 1 problem(s)\n")

    allow = repo / "allow.tsv"
    allow.write_text("doc.md\tword\t-\tbrowser\tdropped on purpose\n")
    rc, out = run("--rev", "HEAD", "--allow", str(allow), "doc.md")
    assert rc == 0 and "doc.md: allowed word - browser (dropped on purpose)\n" in out

    allow.write_text("doc.md\tword\t-\tbrowser\tr\ndoc.md\tword\t-\tother\tr\n")
    rc, out = run("--rev", "HEAD", "--allow", str(allow), "doc.md")
    assert rc == 1 and "doc.md: unused allow: word - other\n" in out

    doc.write_text("The browser shows the port.\nIt is not kept and is shown.\n")
    rc, out = run("--rev", "HEAD", "doc.md")
    assert rc == 1 and "doc.md: negation - not\n" in out
    allow.write_text("doc.md\tnegation\t-\tnot\tshown now\n")
    rc, out = run("--rev", "HEAD", "--allow", str(allow), "doc.md")
    assert rc == 0 and "doc.md: allowed negation - not (shown now)\n" in out

    assert run("--rev", "nosuchrev", "doc.md")[0] == 2
    assert run("--rev", "HEAD", "missing.md")[0] == 2
    allow.write_text("bad line\n")
    assert run("--rev", "HEAD", "--allow", str(allow), "doc.md")[0] == 2
    assert run("--rev", "HEAD", "--to", "HEAD", "doc.md")[0] == 0
