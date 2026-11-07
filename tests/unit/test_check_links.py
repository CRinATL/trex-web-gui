"""scripts/check_links.py: GitHub's heading slugs, which links are internal, and what counts as broken."""
import subprocess
import sys
from pathlib import Path

import pytest
from support import ROOT, load_script

SCRIPT = ROOT / "scripts" / "check_links.py"
# This repo's GitHub URL, split so the checker (which scans this file) never reads the examples as links.
GH = "https://github.com/CRinATL/" + "trex-web-gui"


@pytest.fixture(scope="module")
def cl():
    """The checker module, loaded by path on first use so a missing script fails each test, not collection."""
    return load_script("check_links")


def make_repo(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    """A git repo in tmp_path holding `files` (path -> text or bytes), all tracked."""
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", *files], check=True)
    return tmp_path


def at(rel: str, line: int, rest: str) -> str:
    """One broken-link line as the checker prints it (built here so the citation check does not read
    the expected text as a citation)."""
    return f"{rel}:{line}: {rest}"


def broken(cl, tmp_path: Path, files: dict[str, str | bytes]) -> list[str]:
    """The checker's broken-link lines for a repo holding `files`."""
    repo = make_repo(tmp_path, files)
    return cl.check(repo, cl.tracked(repo))


def test_the_url_split_above_is_the_checkers(cl) -> None:
    assert cl.REPO_URL == GH


@pytest.mark.parametrize(("heading", "anchor"), [
    ("Access: single user, localhost only", "access-single-user-localhost-only"),
    ("The `trex-web-gui` command and `PATH`", "the-trex-web-gui-command-and-path"),
    ("Offline host", "offline-host"),
    ("11a. EMU", "11a-emu"),
    ("snake_case and dash-ed", "snake_case-and-dash-ed"),
    ("See [the guide](docs/g.md) now", "see-the-guide-now"),
    ("A <code>tag</code> here", "a-tag-here"),
    ("Launch 🚀 now", "launch--now"),
])
def test_slug_matches_github(cl, heading: str, anchor: str) -> None:
    assert cl.slug(heading) == anchor


def test_anchors_number_repeats_and_skip_fenced_headings(cl) -> None:
    text = "# A\n## Notes\n```\n# not a heading\n```\n## Notes\n<a id=\"custom\"></a>\n<a name=\"old\"></a>\n"
    assert cl.anchors(text) == {"a", "notes", "notes-1", "custom", "old"}


def test_a_repeat_takes_the_first_free_number(cl) -> None:
    # github-slugger: "A 1", "A", "A" -> a-1, a, a-2 (a-1 is taken by the first heading).
    assert cl.anchors("# A 1\n# A\n# A\n") == {"a-1", "a", "a-2"}


def test_closing_hashes_and_indented_headings(cl) -> None:
    assert cl.anchors("## Foo ##\n   ### Bar\n    #### code, not a heading\n") == {"foo", "bar"}


def test_readme_offers_githubs_readme_anchor(cl) -> None:
    assert "readme" in cl.anchors("# T\n", readme=True)
    assert "readme" not in cl.anchors("# T\n")


def test_good_links_pass(cl, tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "README.md": "# Top\n## Offline host\n"
                     "See [guide](docs/guide.md#part-two) and [here](#offline-host).\n"
                     "![pic](docs/pic.svg) [ext](https://example.com/x#y) [mail](mailto:a@b)\n"
                     "[t](docs/guide.md \"title\") [t2](docs/guide.md 'title') [a](<docs/guide.md>)\n"
                     "[![badge](docs/pic.svg)](docs/guide.md) [root](/docs/guide.md)\n"
                     "[sp](docs/my%20file.md)\n"
                     "[ref]: docs/guide.md#part-two\n",
        "docs/guide.md": "# Guide\n## Part two\n[back](../README.md#top)\n",
        "docs/my file.md": "# M\n",
        "docs/pic.svg": "<svg/>",
        "src/x.py": f'URL = "{GH}#offline-host"\n'
                    f'DOC = "{GH}/blob/main/docs/guide.md#part-two"\n'
                    f'DIR = "{GH}/tree/main/docs/"\n'
                    f'LINE = "{GH}/blob/main/README.md#L10"\n'
                    f'PLAIN = "{GH}/blob/main/README.md?plain=1"\n'
                    f'HOME = "{GH}#readme"\n'
                    f'ISSUES = "{GH}/issues"\n'
                    f'NL = "{GH}/blob/main/README.md\\n"\n',
        "notes.md": f"See {GH}/blob/main/README.md, then {GH}/blob/main/docs/guide.md; "
                    f"and {GH}/blob/main/README.md:\n",
    }
    assert broken(cl, tmp_path, files) == []


def test_a_bare_repo_url_is_not_a_link_to_the_readme(cl, tmp_path: Path) -> None:
    # The URL with no path and no anchor (the project page, /issues) names no file: a repo without a README
    # has nothing broken.
    assert broken(cl, tmp_path, {"x.py": f'U = "{GH}"\nI = "{GH}/issues"\n'}) == []


def test_a_missing_file_is_broken(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": "# T\n[gone](docs/gone.md)\n"})
    assert out == [at("README.md", 2, "docs/gone.md: no such file docs/gone.md")]


def test_a_missing_anchor_is_broken(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": "# T\n## Install\n",
                                "docs/a.md": "[x](../README.md#offline-host)\n"})
    assert out == [at("docs/a.md", 1, "../README.md#offline-host: README.md has no heading #offline-host")]


def test_a_same_file_anchor_is_checked(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": "# T\n[x](#nowhere)\n"})
    assert out == [at("README.md", 2, "#nowhere: README.md has no heading #nowhere")]


@pytest.mark.parametrize(("line", "raw", "target"), [
    ("[![b](docs/p.svg)](docs/gone.md)", "docs/gone.md", "docs/gone.md"),
    ("[x](docs/gone.md 'title')", "docs/gone.md", "docs/gone.md"),
    ("[x](<docs/gone.md>)", "docs/gone.md", "docs/gone.md"),
    ("[x](/docs/gone.md)", "/docs/gone.md", "docs/gone.md"),
    ("[r]: docs/gone.md", "docs/gone.md", "docs/gone.md"),
])
def test_every_link_form_is_checked(cl, tmp_path: Path, line: str, raw: str, target: str) -> None:
    out = broken(cl, tmp_path, {"README.md": f"# T\n{line}\n", "docs/p.svg": "<svg/>"})
    assert out == [at("README.md", 2, f"{raw}: no such file {target}")]


def test_a_github_url_anchor_into_the_readme_is_checked_in_any_file(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n", "src/pages.py": f'U = "{GH}#offline-host"\n'}
    out = broken(cl, tmp_path, files)
    assert out == [at("src/pages.py", 1, f"{GH}#offline-host: README.md has no heading #offline-host")]


def test_a_github_url_ending_a_sentence_is_still_checked(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n## Install\n",
             "notes.md": f"See {GH}#offline-host. Or {GH}/blob/main/README.md.\n"
                         f"Not ours: {GH}-records#offline-host\n"}
    out = broken(cl, tmp_path, files)
    assert out == [at("notes.md", 1, f"{GH}#offline-host: README.md has no heading #offline-host")]


def test_a_github_url_in_inline_code_is_still_checked(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": f"# T\nCopy `{GH}#nope` from here.\n"})
    assert out == [at("README.md", 2, f"{GH}#nope: README.md has no heading #nope")]


def test_a_github_blob_url_to_a_missing_file_is_broken(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n", "notes.md": f"<{GH}/blob/main/docs/api.md>\n"}
    out = broken(cl, tmp_path, files)
    assert out == [at("notes.md", 1, f"{GH}/blob/main/docs/api.md: no such file docs/api.md")]


def test_a_github_tree_url_to_a_missing_directory_is_broken(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": "# T\n", "x.py": f'D = "{GH}/tree/main/gone/"\n'})
    assert out == [at("x.py", 1, f"{GH}/tree/main/gone/: no such file gone")]


def test_a_link_above_the_repo_root_is_broken(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": "[x](../outside.md)\n"})
    assert out == [at("README.md", 1, "../outside.md: no such file ../outside.md")]


def test_code_fences_and_inline_code_are_not_links(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n```\n[x](gone.md)\n```\nUse `[x](gone.md)` literally.\n"
                          "~~~\n```\n[x](gone.md)\n~~~\n````\n```\n[x](gone.md)\n````\n"}
    assert broken(cl, tmp_path, files) == []


def test_a_fence_closes_only_on_its_own_kind(cl, tmp_path: Path) -> None:
    # The ``` inside ~~~ does not close it; the ~~~ does, and the link after it is checked.
    out = broken(cl, tmp_path, {"README.md": "~~~\n```\n~~~\n[x](gone.md)\n"})
    assert out == [at("README.md", 4, "gone.md: no such file gone.md")]


def test_symlinks_the_vendored_tree_binaries_and_deleted_files_are_skipped(cl, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {
        "README.md": "# T\n[g](docs/g.md)\n", "docs/g.md": "# G\n",
        "backend/trex_web_gui/_vendor/lib/README.md": "[x](CONTRIBUTING.md)\n",
        "img.md": b"\x89PNG\xff\xfe[x](gone.md)",
        "deleted.md": "[x](gone.md)\n",
    })
    (repo / "backend" / "README.md").symlink_to("../README.md")
    subprocess.run(["git", "-C", str(repo), "add", "backend/README.md"], check=True)
    (repo / "deleted.md").unlink()
    assert cl.check(repo, cl.tracked(repo)) == []


def test_cli_exit_status(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"README.md": "# T\n[x](#nowhere)\n"})
    run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--repo", str(repo)],
                         capture_output=True, text=True)
    assert run.returncode == 1
    assert "README.md has no heading #nowhere" in run.stdout
    (repo / "README.md").write_text("# T\n[x](#t)\n")
    run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--repo", str(repo)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stdout


def test_this_repo_has_no_broken_links(cl) -> None:
    assert cl.check(ROOT, cl.tracked(ROOT)) == []
