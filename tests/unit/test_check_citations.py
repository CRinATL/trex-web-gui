"""scripts/check_citations.py on a temp tree: what resolves, what is broken, what a sibling and the
allowlist do."""
import importlib.util
import subprocess
from pathlib import Path

import pytest
from support import ROOT

spec = importlib.util.spec_from_file_location("check_citations", ROOT / "scripts" / "check_citations.py")
assert spec and spec.loader
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


@pytest.fixture
def tree(tmp_path: Path):
    """A tmp git repo (pkg/real.py, pkg/guide.md, .citation-siblings) plus a plain-directory sibling holding
    only_in_sibling.cpp; returns run(text, capsys, allow). tmp_path does the cleanup."""
    repo, sib = tmp_path / "repo", tmp_path / "sib"
    (repo / "pkg").mkdir(parents=True)
    sib.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "pkg" / "real.py").write_text("one\ntwo\nthree\n")
    (repo / "pkg" / "guide.md").write_text("# Guide\nrule one\n")
    (sib / "only_in_sibling.cpp").write_text("a\nb\nc\nd\ne\n")
    (repo / ".citation-siblings").write_text(f"{sib}\n")

    # Writes docs/note.md and .citation-allow, runs the checker on docs, returns (exit code, stdout).
    def run(citing_text: str, capsys, allow: str = "") -> tuple[int, str]:
        (repo / "docs").mkdir(exist_ok=True)
        (repo / "docs" / "note.md").write_text(citing_text)
        (repo / ".citation-allow").write_text(allow)
        rc = cc.main(["--repo", str(repo), "docs"])
        return rc, capsys.readouterr().out
    return run


def test_a_good_citation_resolves(tree, capsys):
    rc, out = tree("see pkg/real.py:2-3 and real.py:1\n", capsys)
    assert rc == 0 and "2 resolve, 0 broken" in out


def test_a_line_past_eof_is_broken(tree, capsys):
    rc, out = tree("see pkg/real.py:4\n", capsys)
    assert rc == 1 and "docs/note.md:1  cites pkg/real.py:4" in out and "has 3 lines" in out


def test_a_missing_file_with_a_directory_part_is_broken(tree, capsys):
    rc, out = tree("see pkg/nope.py:1\n", capsys)
    assert rc == 1 and "cites pkg/nope.py:1  -- no such file" in out


def test_docs_evidence_is_not_scanned(tree, capsys, tmp_path):
    """docs/evidence/ holds frozen copies whose file:line citations are stale by design: never scanned."""
    cite = "see pkg/real" + ".py:9\n"                                          # real.py has 3 lines
    rc, out = tree(cite, capsys)
    assert rc == 1 and "docs/note.md:1  cites pkg/real" + ".py:9" in out         # negative control
    repo = tmp_path / "repo"
    (repo / "docs" / "note.md").write_text("")
    (repo / "docs" / "evidence" / "x").mkdir(parents=True)
    (repo / "docs" / "evidence" / "x" / "review.md").write_text(cite)
    rc = cc.main(["--repo", str(repo), "docs"])
    out = capsys.readouterr().out
    assert rc == 0 and "cites" not in out


def test_an_evidence_directory_elsewhere_is_scanned(tree, capsys, tmp_path):
    """Only docs/evidence is frozen; a directory named evidence anywhere else is a living document."""
    cite = "see pkg/real" + ".py:9\n"                                          # real.py has 3 lines
    tree("", capsys)
    repo = tmp_path / "repo"
    (repo / "docs" / "sub" / "evidence").mkdir(parents=True)
    (repo / "docs" / "sub" / "evidence" / "review.md").write_text(cite)
    rc = cc.main(["--repo", str(repo), "docs"])
    out = capsys.readouterr().out
    assert rc == 1 and "docs/sub/evidence/review" + ".md:1  cites pkg/real" + ".py:9" in out


def test_a_bare_basename_found_only_in_a_sibling_resolves_there(tree, capsys):
    rc, out = tree("see only_in_sibling.cpp:5\n", capsys)
    assert rc == 0 and "1 resolve, 0 broken" in out
    rc, out = tree("see only_in_sibling.cpp:6\n", capsys)           # the sibling file has 5 lines
    assert rc == 1 and "no local candidate" in out


def test_an_allowlisted_fake_is_skipped(tree, capsys):
    rc, out = tree("see pkg/fake.py:9\n", capsys, allow="docs/note.md pkg/fake.py:9   # worked example\n")
    assert rc == 0 and "1 allowlisted" in out


def test_a_local_allow_file_is_read_beside_the_tracked_one(tree, capsys, tmp_path):
    """`.citation-allow.local` (untracked, the maintainer's records) adds lines; without it the fake fails."""
    other = "pkg/other" + ".py:2"                         # split, so this file's own scan does not see it
    cite = f"see pkg/fake.py:9 and {other}\n"
    rc, out = tree(cite, capsys, allow="docs/note.md pkg/fake.py:9\n")
    assert rc == 1 and f"cites {other}" in out                                # negative control
    (tmp_path / "repo" / ".citation-allow.local").write_text(f"docs/note.md {other}   # a record\n")
    rc, out = tree(cite, capsys, allow="docs/note.md pkg/fake.py:9\n")
    assert rc == 0 and "2 allowlisted" in out


@pytest.fixture
def two_siblings(tmp_path: Path):
    """A repo with two siblings, in priority order, that both hold dup.cpp (trex-core: the /var/tmp clone and
    the copy inside trex-rework); returns (run, s1, s2)."""
    repo, s1, s2 = tmp_path / "repo", tmp_path / "s1", tmp_path / "s2"
    for d in (repo, s1, s2):
        d.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # In subdirectories: a file at a sibling's ROOT is an exact root-relative hit (step 1 of _resolve) and
    # never reaches the sibling-order rule; trex-core's files are all in subdirectories.
    (s1 / "a").mkdir()
    (s2 / "b").mkdir()
    (s1 / "a" / "dup.cpp").write_text("1\n2\n3\n")                       # 3 lines
    (s2 / "b" / "dup.cpp").write_text("".join(f"{i}\n" for i in range(10)))   # 10 lines
    (repo / ".citation-siblings").write_text(f"{s1}\n{s2}\n")

    # Writes docs/note.md, runs the checker over docs, returns (exit code, captured stdout).
    def run(text: str, capsys) -> tuple[int, str]:
        (repo / "docs").mkdir(exist_ok=True)
        (repo / "docs" / "note.md").write_text(text)
        rc = cc.main(["--repo", str(repo), "docs"])
        return rc, capsys.readouterr().out
    return run, s1, s2


def test_the_earliest_sibling_wins_a_bare_name_found_in_both(two_siblings, capsys):
    run, _s1, _s2 = two_siblings
    rc, out = run("see dup.cpp:2\n", capsys)
    assert rc == 0 and "1 resolve, 0 broken" in out


def test_a_line_only_the_later_sibling_has_says_qualify_the_path(two_siblings, capsys):
    run, s1, s2 = two_siblings
    rc, out = run("see dup.cpp:8\n", capsys)                       # s1 has 3 lines, s2 has 10
    assert (rc == 1 and f"{s1 / 'a' / 'dup.cpp'} has 3 lines" in out
            and f"{s2 / 'b' / 'dup.cpp'} would satisfy it" in out)
    assert "QUALIFY THE PATH" in out


def test_two_matches_in_the_earliest_sibling_are_ambiguous(two_siblings, capsys):
    run, s1, _s2 = two_siblings
    (s1 / "other").mkdir()
    (s1 / "other" / "dup.cpp").write_text("x\n")
    rc, out = run("see dup.cpp:1\n", capsys)
    assert rc == 1 and "ambiguous across repos (3 matches)" in out


def test_with_the_earliest_sibling_gone_the_later_one_resolves(two_siblings, capsys):
    run, s1, _s2 = two_siblings
    (s1 / "a" / "dup.cpp").unlink()
    (s1 / "a").rmdir()
    s1.rmdir()                                                      # the /var/tmp clone aged out
    rc, out = run("see dup.cpp:8\n", capsys)
    assert rc == 0 and "1 resolve, 0 broken" in out


def test_the_earliest_sibling_wins_a_partial_path_too(two_siblings, capsys):
    run, s1, s2 = two_siblings
    for d, n in ((s1, 3), (s2, 10)):
        (d / "x" / "deep").mkdir(parents=True)
        (d / "x" / "deep" / "p.cpp").write_text("l\n" * n)
    rc, out = run("see deep/p.cpp:2\n", capsys)
    assert rc == 0 and "1 resolve, 0 broken" in out
    rc, out = run("see deep/p.cpp:8\n", capsys)
    assert rc == 1 and "QUALIFY THE PATH" in out


def test_a_quoted_snippet_after_a_citation_is_checked(tree, capsys):
    rc, out = tree('see pkg/real.py:2-3 "two three"\n', capsys)                  # whitespace-normalised
    assert rc == 0 and "1 resolve, 0 broken" in out
    rc, out = tree('see pkg/real.py:2 "three"\n', capsys)
    assert rc == 1 and "snippet \"three\" not in" in out


def test_a_line_citation_into_markdown_needs_a_snippet(tree, capsys):
    rc, out = tree("see pkg/guide.md:2\n", capsys)            # a living document: lines move under the number
    assert rc == 1 and "living document" in out
    rc, out = tree('see pkg/guide.md:2 "rule one"\n', capsys)
    assert rc == 0 and "1 resolve, 0 broken" in out


def test_a_missing_sibling_is_named_not_silently_skipped(tree, capsys, tmp_path):
    (tmp_path / "repo" / ".citation-siblings").write_text(f"{tmp_path / 'gone'}\n")
    rc, out = tree("see pkg/real.py:1\n", capsys)
    assert rc == 0 and f"missing sibling: {tmp_path / 'gone'}" in out


def test_include_paths_never_cuts_an_extension_short(tree, capsys, tmp_path):
    """PATH_ONLY never backtracks to a shorter extension: .jsonl:N to .json, .tsx:N to .ts, .sha256 to .sh."""
    docs = tmp_path / "repo" / "docs"
    docs.mkdir()
    for name in ("rpc.jsonl", "view.tsx", "wheel.sha256"):
        (docs / name).write_text("a\nb\n")
    cite = "see docs/rpc" + ".jsonl:2, docs/view" + ".tsx:1 and docs/wheel.sha256\n"   # split: not a citation
    tree(cite, capsys)                                                                 # writes docs/note.md
    rc = cc.main(["--repo", str(tmp_path / "repo"), "--include-paths", "docs"])
    out = capsys.readouterr().out
    assert rc == 0 and "no such path" not in out
    (docs / "note.md").write_text("see docs/gone.jsonl\n")                           # negative control
    rc = cc.main(["--repo", str(tmp_path / "repo"), "--include-paths", "docs"])
    assert rc == 1 and "cites docs/gone.jsonl  -- no such path" in capsys.readouterr().out


def test_a_path_citation_into_a_missing_sibling_is_unverifiable_not_broken(tree, capsys, tmp_path):
    """A public clone has no siblings: a path whose directory is not in this repo cannot be checked here."""
    (tmp_path / "repo" / ".citation-siblings").write_text(f"{tmp_path / 'gone'}\n")
    rc, out = tree("see src/x.cpp:1\n", capsys)
    assert rc == 0 and "0 broken" in out and "1 external" in out
    assert f"missing sibling: {tmp_path / 'gone'}" in out


def test_a_broken_local_path_citation_still_fails_without_siblings(tree, capsys, tmp_path):
    (tmp_path / "repo" / ".citation-siblings").write_text(f"{tmp_path / 'gone'}\n")
    rc, out = tree("see pkg/nope.py:1\n", capsys)                  # pkg/ is this repo's: a real mistake
    assert rc == 1 and "cites pkg/nope.py:1  -- no such file" in out


def test_a_path_citation_with_all_siblings_present_and_no_match_still_fails(tree, capsys):
    rc, out = tree("see src/x.cpp:1\n", capsys)                    # the sibling is there and lacks it
    assert rc == 1 and "cites src/x.cpp:1  -- no such file" in out


# Assembled so this file does not itself cite a nonexistent path to the repo-wide citation check.
OLD = "old/pkg/a" + ".py"


@pytest.fixture
def renamed(tmp_path: Path):
    """A tmp repo whose code moved from old/pkg/ to new/pkg/; returns run(text, capsys, ...)."""
    repo = tmp_path / "repo"
    (repo / "new" / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "new" / "pkg" / "a.py").write_text("one\ntwo\nthree\n")

    # Writes the rename file, an optional real old file, and docs/note.md; runs the checker on docs.
    def run(text: str, capsys, renames: str | None = "old/pkg/ new/pkg/   # moved\n",
            old_lines: str | None = None, new_lines: str = "one\ntwo\nthree\n") -> tuple[int, str]:
        if renames is not None:
            (repo / ".citation-renames").write_text("# header\n\n" + renames)
        (repo / "new" / "pkg" / "a.py").write_text(new_lines)
        if old_lines is not None:
            (repo / "old" / "pkg").mkdir(parents=True)
            (repo / "old" / "pkg" / "a.py").write_text(old_lines)
        (repo / "docs").mkdir(exist_ok=True)
        (repo / "docs" / "note.md").write_text(text)
        rc = cc.main(["--repo", str(repo), "docs"])
        return rc, capsys.readouterr().out
    return run


def test_rename_alias_resolves_an_old_prefix(renamed, capsys):
    rc, out = renamed(f"see {OLD}:2\n", capsys)
    assert rc == 0 and "1 resolve, 0 broken" in out


def test_rename_alias_does_not_rescue_a_bad_line(renamed, capsys):
    rc, out = renamed(f"see {OLD}:9\n", capsys)
    assert rc == 1 and f"cites {OLD}:9" in out and "has 3 lines" in out


def test_no_rename_file_means_no_aliases(renamed, capsys):
    rc, out = renamed(f"see {OLD}:2\n", capsys, renames=None)
    assert rc == 1 and f"cites {OLD}:2  -- no such file" in out


def test_an_existing_path_is_never_rewritten(renamed, capsys):
    rc, out = renamed(f"see {OLD}:3\n", capsys, old_lines="1\n2\n3\n4\n5\n",
                      new_lines="1\n2\n")
    assert rc == 0 and "1 resolve, 0 broken" in out


def test_renames_loader_keeps_file_order_and_skips_comments(tmp_path):
    (tmp_path / ".citation-renames").write_text("# c\n\na/ b/   # why\nc/ d/\n")
    assert cc._renames(tmp_path) == [("a/", "b/"), ("c/", "d/")]
    assert cc._renames(tmp_path / "none") == []
