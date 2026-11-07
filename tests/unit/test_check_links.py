"""scripts/check_links.py: GitHub's heading slugs, which links are internal, and what counts as broken."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from support import ROOT

SCRIPT = ROOT / "scripts" / "check_links.py"


@pytest.fixture(scope="module")
def cl():
    """The checker module, loaded by path on first use so a missing script fails each test, not collection."""
    spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A git repo in tmp_path holding `files` (path -> text), all tracked."""
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", *files], check=True)
    return tmp_path


def at(rel: str, line: int, rest: str) -> str:
    """One broken-link line as the checker prints it (built here so the citation check does not read
    the expected text as a citation)."""
    return f"{rel}:{line}: {rest}"


def broken(cl, tmp_path: Path, files: dict[str, str]) -> list[str]:
    """The checker's broken-link lines for a repo holding `files`."""
    repo = make_repo(tmp_path, files)
    return cl.check(repo, cl.tracked(repo))


@pytest.mark.parametrize(("heading", "anchor"), [
    ("Access: single user, localhost only", "access-single-user-localhost-only"),
    ("The `trex-web-gui` command and `PATH`", "the-trex-web-gui-command-and-path"),
    ("Offline host", "offline-host"),
    ("11a. EMU", "11a-emu"),
    ("snake_case and dash-ed", "snake_case-and-dash-ed"),
])
def test_slug_matches_github(cl, heading: str, anchor: str) -> None:
    assert cl.slug(heading) == anchor


def test_anchors_number_repeats_and_skip_fenced_headings(cl) -> None:
    text = "# A\n## Notes\n```\n# not a heading\n```\n## Notes\n<a id=\"custom\"></a>\n"
    assert cl.anchors(text) == {"a", "notes", "notes-1", "custom"}


def test_good_links_pass(cl, tmp_path: Path) -> None:
    files = {
        "README.md": "# Top\n## Offline host\n"
                     "See [guide](docs/guide.md#part-two) and [here](#offline-host).\n"
                     "![pic](docs/pic.svg) [ext](https://example.com/x#y) [mail](mailto:a@b)\n",
        "docs/guide.md": "# Guide\n## Part two\n[back](../README.md#top)\n",
        "docs/pic.svg": "<svg/>",
        "src/x.py": 'URL = "https://github.com/CRinATL/trex-web-gui#offline-host"\n'
                    'DOC = "https://github.com/CRinATL/trex-web-gui/blob/main/docs/guide.md#part-two"\n'
                    'ISSUES = "https://github.com/CRinATL/trex-web-gui/issues"\n',
    }
    assert broken(cl, tmp_path, files) == []


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


def test_a_github_url_anchor_into_the_readme_is_checked_in_any_file(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n", "src/pages.py": 'U = "https://github.com/CRinATL/trex-web-gui#offline-host"\n'}
    out = broken(cl, tmp_path, files)
    assert out == [at("src/pages.py", 1, "https://github.com/CRinATL/trex-web-gui#offline-host: "
                   "README.md has no heading #offline-host")]


def test_a_github_url_ending_a_sentence_is_still_checked(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n## Install\n",
             "notes.md": "See https://github.com/CRinATL/trex-web-gui#offline-host. Or "
                         "https://github.com/CRinATL/trex-web-gui/blob/main/README.md.\n"
                         "Not ours: https://github.com/CRinATL/trex-web-gui-records#offline-host\n"}
    out = broken(cl, tmp_path, files)
    assert out == [at("notes.md", 1, "https://github.com/CRinATL/trex-web-gui#offline-host: "
                   "README.md has no heading #offline-host")]


def test_a_github_blob_url_to_a_missing_file_is_broken(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n",
             "notes.md": "<https://github.com/CRinATL/trex-web-gui/blob/main/docs/api.md>\n"}
    out = broken(cl, tmp_path, files)
    assert out == [at("notes.md", 1, "https://github.com/CRinATL/trex-web-gui/blob/main/docs/api.md: "
                   "no such file docs/api.md")]


def test_a_link_above_the_repo_root_is_broken(cl, tmp_path: Path) -> None:
    out = broken(cl, tmp_path, {"README.md": "[x](../outside.md)\n"})
    assert out == [at("README.md", 1, "../outside.md: no such file ../outside.md")]


def test_code_fences_and_inline_code_are_not_links(cl, tmp_path: Path) -> None:
    files = {"README.md": "# T\n```\n[x](gone.md)\n```\nUse `[x](gone.md)` literally.\n"}
    assert broken(cl, tmp_path, files) == []


def test_symlinks_and_the_vendored_tree_are_skipped(cl, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {
        "README.md": "# T\n[g](docs/g.md)\n", "docs/g.md": "# G\n",
        "backend/trex_web_gui/_vendor/lib/README.md": "[x](CONTRIBUTING.md)\n",
    })
    (repo / "backend" / "README.md").symlink_to("../README.md")
    subprocess.run(["git", "-C", str(repo), "add", "backend/README.md"], check=True)
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
