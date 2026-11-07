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
    for bad in ("doc.md\tword\t+\tstatus\n", "doc.md\tcolour\t+\tx\twhy\n", "doc.md\tword\t+\tx\t \n",
                "doc.md\tword\t*\tx\twhy\n"):
        with pytest.raises(ValueError, match="line 1"):
            cr.parse_allow(bad)


def test_symbols_are_a_multiset() -> None:
    """docs-cleanup-26: a dropped or added symbol that carries meaning is a difference; punctuation a reflow
    may change (a semicolon to a full stop) is not, and neither is a symbol inside a code span."""
    assert cr.compare("Load is 85 % now.\n", "Load is 85 now.\n") == [Diff("symbol", "-", "%")]
    assert cr.compare("4 × 2 and 1 ≤ 3\n", "4 × 2 and 1 ≤ 3 ≤ 5\n") == [Diff("number", "+", "5"),
                                                                   Diff("symbol", "+", "≤")]
    assert cr.compare("a; b\n", "a. B\n") == []
    assert cr.compare("run `x % y` now\n", "run `x % y` now\n") == []
    assert cr.compare("Tx − Rx\n", "Rx − Tx\n") == []          # order is out of scope, by design


def test_a_digit_only_token_is_a_number_not_a_word() -> None:
    """docs-cleanup-28: a changed number is one number pair, not also a word pair."""
    assert cr.compare("Wait 10 s.\n", "Wait 12 s.\n") == [Diff("number", "+", "12"),
                                                         Diff("number", "-", "10")]
    assert cr.compare("use v1 now\n", "use now\n") == [Diff("number", "-", "1"), Diff("word", "-", "v1")]


def test_a_backslash_escapes_only_an_opening_backtick() -> None:
    """docs-cleanup-97: inside a code span a backslash is literal, so `x\\` closes at its backtick;
    before an opening backtick it escapes it."""
    assert cr.facts("a `x\\` b\n").code == {"x\\": 1}
    assert cr.facts("a `x\\` b `y`\n").code == {"x\\": 1, "y": 1}
    assert cr.facts("a \\`x` b\n").code == {}


def test_not_listed_constructs_are_named_in_the_docstring() -> None:
    """docs-cleanup-27 and -29: the constructs the check does not see, and its two judgment calls."""
    doc = cr.__doc__
    for name in ("~~~", "link titles", "reference-style", "indented code", "setext"):
        assert name in doc
    assert "Lines for another path" in doc and "ignored for this path" in doc and "digit-only" in doc


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

    allow.write_text("other.md\tnegation\t-\tnot\tfor another file\n")       # another path: neither used
    rc, out = run("--rev", "HEAD", "--allow", str(allow), "doc.md")            # nor reported unused
    assert rc == 1 and "unused allow" not in out and "doc.md: negation - not\n" in out

    doc.write_text("The browser shows the port.\nIt is kept and is shown.\n")
    allow.write_text("doc.md\tnegation\t-\tnot\tfirst reason\ndoc.md\tnegation\t-\tnot\tsecond reason\n")
    rc, out = run("--rev", "HEAD", "--allow", str(allow), "doc.md")            # docs-cleanup-31: each its own
    assert rc == 0 and "(first reason)\n" in out and "(second reason)\n" in out

    # docs-cleanup-30: both sides are read the same way, and a line ending is not a fact. A CRLF file compared
    # with itself (git's raw bytes against the work tree), and a file only converted to CRLF, keep their
    # facts.
    crlf = b"# Title\r\n\n| a | b |\r\n\nThe browser shows the port.\r\nIt is not kept and is not shown.\r\n"
    doc.write_bytes(crlf)
    git(repo, "commit", "-qam", "crlf")
    assert run("--rev", "HEAD", "doc.md")[0] == 0
    doc.write_bytes(crlf.replace(b"\r\n", b"\n"))
    assert run("--rev", "HEAD", "doc.md")[0] == 0
    doc.write_text("The browser shows the port.\nIt is kept and is shown.\n")
    git(repo, "commit", "-qam", "back")

    assert run("--rev", "nosuchrev", "doc.md")[0] == 2
    assert run("--rev", "HEAD", "missing.md")[0] == 2
    allow.write_text("bad line\n")
    assert run("--rev", "HEAD", "--allow", str(allow), "doc.md")[0] == 2
    assert run("--rev", "HEAD", "--to", "HEAD", "doc.md")[0] == 0


def test_link_regex_does_not_backtrack_exponentially() -> None:
    """A '[' followed by many '![]()' with no closing '](…)' must fail fast (CodeQL py/redos on LINK_RE)."""
    probe = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('cr', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "m.LINK_RE.search('[' + '![]()' * 40)\n"
    )
    script = str(ROOT / "scripts" / "check_reflow.py")
    try:
        subprocess.run(["python3", "-I", "-c", probe, script], check=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail("LINK_RE backtracks exponentially on '[' + '![]()' * 40")


def test_link_regex_keeps_an_image_inside_link_text() -> None:
    """The inner '![alt](src)' stays part of the outer link's text, as before the ReDoS fix."""
    found = [m.groups() for m in cr.LINK_RE.finditer("[a ![i](x.png) b](y) and ![alt](p.png)")]
    assert found == [("", "a ![i](x.png) b", "y"), ("!", "alt", "p.png")]


def test_merge_sums_counters_and_concatenates_lists() -> None:
    """`merge` adds the Counters and joins the ordered lists in the order given."""
    merged = cr.merge([cr.facts("# A\n\nx `c` 5\n"), cr.facts("# B\n\nx `c`\n")])
    assert merged.code["c"] == 2 and merged.numbers["5"] == 1
    assert merged.headings == ["# A", "# B"]


@pytest.fixture
def pool_run(repo: Path, capsys: pytest.CaptureFixture[str]):  # noqa: ANN201
    """A `run(*argv)` returning (exit code, stdout) for `main`, over the `repo` fixture."""

    def run(*argv: str) -> tuple[int, str]:
        """Runs main and returns (exit code, stdout)."""
        rc = cr.main(list(argv))
        return rc, capsys.readouterr().out

    return run


def test_pool_extracts_each_file_on_its_own(repo: Path, pool_run) -> None:  # noqa: ANN001
    """Backticks do not pair across files: a stray one in each old file is no code span."""
    (repo / "a.md").write_text("Run `x\n")
    (repo / "b.md").write_text("` now\n")
    git(repo, "add", "a.md", "b.md")
    git(repo, "commit", "-qm", "ab")
    (repo / "a.md").write_text("Run `x` now\n")
    (repo / "b.md").write_text("")
    rc, out = pool_run("--pool", "--rev", "HEAD", "a.md", "b.md")
    assert rc == 1 and "*: code + x\n" in out and "*: word - x\n" in out


def test_pool_a_paragraph_moved_to_a_new_file_cancels_out(repo: Path, pool_run) -> None:  # noqa: ANN001
    """A paragraph moved to a new, untracked file keeps its facts; an absent side reads as empty."""
    (repo / "a.md").write_text("Open the browser now.\n\nThe browser shows ports.\n")
    git(repo, "add", "a.md")
    git(repo, "commit", "-qm", "a")
    (repo / "a.md").write_text("Open the browser now.\n")
    (repo / "b.md").write_text("The browser browser shows ports.\n")
    rc, out = pool_run("--pool", "--rev", "HEAD", "a.md", "b.md")
    assert rc == 0 and "b.md: absent at HEAD, read as empty\n" in out
    assert "*: info: word browser 2->3\n" in out
    assert out.endswith("check_reflow: pool(a.md b.md) ok\n")
    assert pool_run("--rev", "HEAD", "a.md", "b.md")[0] == 2


def test_pool_a_file_deleted_on_the_new_side_reads_as_empty(repo: Path, pool_run) -> None:  # noqa: ANN001
    """A path absent from the work tree, or at --to, reads as empty."""
    (repo / "a.md").write_text("Open the browser now.\n")
    (repo / "b.md").write_text("The browser shows ports.\n")
    git(repo, "add", "a.md", "b.md")
    git(repo, "commit", "-qm", "ab")
    (repo / "a.md").write_text("Open the browser now.\n\nThe browser shows ports.\n")
    (repo / "b.md").unlink()
    rc, out = pool_run("--pool", "--rev", "HEAD", "a.md", "b.md")
    assert rc == 0 and "b.md: absent from the work tree, read as empty\n" in out
    git(repo, "commit", "-qam", "merged")
    rc, out = pool_run("--pool", "--rev", "HEAD~1", "--to", "HEAD", "a.md", "b.md")
    assert rc == 0 and "b.md: absent at HEAD, read as empty\n" in out


def _moved_without_browser(repo: Path) -> None:
    """Commits a.md with two paragraphs, then moves the second, minus `browser`, to a new b.md."""
    (repo / "a.md").write_text("Open it now.\n\nThe browser shows ports.\n")
    git(repo, "add", "a.md")
    git(repo, "commit", "-qm", "a")
    (repo / "a.md").write_text("Open it now.\n")
    (repo / "b.md").write_text("The shows ports.\n")


def test_pool_reports_a_fact_lost_between_files(repo: Path, pool_run) -> None:  # noqa: ANN001
    """A fact the moved text lost is reported once, for the pool."""
    _moved_without_browser(repo)
    rc, out = pool_run("--pool", "--rev", "HEAD", "a.md", "b.md")
    assert rc == 1 and "*: word - browser\n" in out
    assert out.endswith("check_reflow: pool(a.md b.md) 1 problem(s)\n")


def test_pool_allow_lines(repo: Path, pool_run) -> None:  # noqa: ANN001
    """Pool mode reads the `*` allow lines only; another path there is a usage error."""
    _moved_without_browser(repo)
    allow = repo / "allow.tsv"
    args = ("--pool", "--rev", "HEAD", "--allow", str(allow), "a.md", "b.md")
    allow.write_text("*\tword\t-\tbrowser\tmoved on purpose\n")
    rc, out = pool_run(*args)
    assert rc == 0 and "*: allowed word - browser (moved on purpose)\n" in out
    allow.write_text("*\tword\t-\tbrowser\tr\n*\tword\t-\tother\tr\n")
    rc, out = pool_run(*args)
    assert rc == 1 and "*: unused allow: word - other\n" in out
    allow.write_text("a.md\tword\t-\tbrowser\tr\n")
    assert pool_run(*args)[0] == 2
    allow.write_text("*\tword\t-\tbrowser\tr\n")           # outside pool mode a `*` line is neither used
    (repo / "a.md").write_text("Open it now.\n\nThe shows ports.\n")
    rc, out = pool_run("--rev", "HEAD", "--allow", str(allow), "a.md")
    assert rc == 1 and "allowed" not in out and "unused" not in out and "a.md: word - browser\n" in out


def test_pool_usage_errors(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown revision (either), and a path absent on both sides, are usage errors in pool mode."""
    (repo / "a.md").write_text("Open it now.\n")
    for argv, why in ((["--rev", "nosuchrev", "a.md"], "unknown revision 'nosuchrev'"),
                      (["--rev", "HEAD", "--to", "nosuchrev", "doc.md"], "unknown revision 'nosuchrev'"),
                      (["--rev", "HEAD", "nowhere.md", "a.md"], "nowhere.md: absent on both sides")):
        assert cr.main(["--pool", *argv]) == 2
        assert why in capsys.readouterr().err
