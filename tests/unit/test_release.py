"""scripts/release.py: the /var/tmp citation guard, the version and notes checks, the gate chain, that `check`
pushes, tags and releases nothing (canon:WRK-3), and `publish`, which the person runs to do those, resuming
after a failure."""
import hashlib
import importlib.util
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support import ROOT

SCRIPT = ROOT / "scripts" / "release.py"


class Lazy:
    """Loads the script on first attribute access, so a missing script fails each test, not the collection."""

    def __init__(self, pick: Callable[[Any], Any] = lambda module: module) -> None:
        """Remembers how to pick the object out of the loaded module."""
        self.pick = pick

    def __getattr__(self, name: str) -> Any:
        """Delegates to the loaded module (or the picked part of it)."""
        spec = importlib.util.spec_from_file_location("release", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(self.pick(module), name)


release = Lazy()
RC1 = Lazy(lambda module: module.rc_target(1))
FINAL = Lazy(lambda module: module.FINAL)


def problems(line: str, tracked: set[str] | None = None) -> list[str]:
    """Guard problems for the text of one guarded file."""
    return release.guard_problems({"plan": line}, tracked or set())


def test_guard_refuses_a_bare_var_tmp_citation() -> None:
    got = problems("x\nshots are in `/var/tmp/trex-web-gui-p1-demo-2026/`, outside git.")
    assert got == ['plan:2: /var/tmp/trex-web-gui-p1-demo-2026/ has no committed copy '
                   '(add "(committed: <path>)" or "(aged out; …)")']


def test_guard_passes_a_committed_copy() -> None:
    tracked = {"docs/evidence/a.txt", "tests/fixtures/netem/summary.json"}
    assert problems("/var/tmp/x/a.txt (committed: docs/evidence/a.txt)", tracked) == []
    assert problems("/var/tmp/x (committed: tests/fixtures/netem)", tracked) == []


def test_guard_refuses_a_committed_path_that_is_not_tracked() -> None:
    got = problems("/var/tmp/x/a.txt (committed: docs/evidence/a.txt)", {"docs/evidence/b.txt"})
    assert len(got) == 1 and "plan:1: /var/tmp/x/a.txt has no committed copy" in got[0]


def test_guard_passes_an_aged_out_citation() -> None:
    assert problems("/var/tmp/x/a.txt (aged out; the 30-day cleanup took it)") == []


def test_guard_passes_vm_work_dirs_and_bare_var_tmp() -> None:
    for path in ("/var/tmp/trex-vm", "/var/tmp/trex-vm-noble/x", "/var/tmp/trex-vm-dl", "/var/tmp"):
        assert problems(f"the work dir is `{path}`.") == [], path
    assert problems("moved from `/var/tmp` (30-day cleanup) to home") == []


def test_guard_refuses_a_lookalike_work_dir() -> None:
    assert len(problems("see /var/tmp/trex-vmx/log")) == 1


def test_guard_passes_on_the_repo() -> None:
    """Every /var/tmp citation in PLAN.md, DEFERRED.md and the ledgers names a committed copy or is exempt."""
    texts, tracked = release._guard_inputs(ROOT)
    assert release.guard_problems(texts, tracked) == []


def test_guard_scans_plan_deferred_and_ledgers_only(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for name in ("PLAN.md", "DEFERRED.md", ".superpowers/sdd/a/progress.md", "README.md", "notes.md",
                 ".superpowers/sdd/a/task-1-report.txt"):
        (repo / name).write_text("see /var/tmp/gone/x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "citations")
    texts, _ = release._guard_inputs(repo)
    assert sorted(texts) == [".superpowers/sdd/a/progress.md", "DEFERRED.md", "PLAN.md"]


def test_version_must_match_the_target() -> None:
    assert release.version_problems('[project]\nversion = "1.0.0"\n', RC1)
    assert release.version_problems('[project]\nversion = "1.0.0rc1"\n', RC1) == []
    assert release.version_problems('[project]\nversion = "1.0.0rc1"\n', FINAL)


def test_wheel_name_and_tag_follow_the_version() -> None:
    assert release.wheel_name("1.0.0rc1") == "trex_web_gui-1.0.0rc1-py3-none-any.whl"
    assert (RC1.version, RC1.tag, RC1.prerelease) == ("1.0.0rc1", "v1.0.0-rc1", True)
    assert (FINAL.version, FINAL.tag, FINAL.prerelease) == ("1.0.0", "v1.0.0", False)
    assert release.wheel_name(FINAL.version) == "trex_web_gui-1.0.0-py3-none-any.whl"
    assert release.wheel_name("1.0.0rc5") == "trex_web_gui-1.0.0rc5-py3-none-any.whl"


def test_notes_file_must_exist_with_required_sections() -> None:
    assert release.notes_problems(None, RC1) == ["docs/releases/v1.0.0-rc1.md is missing"]
    full = NOTES
    assert release.notes_problems(full, RC1) == []
    got = release.notes_problems(full.replace("## What to record during the week", "## Other"), RC1)
    assert got == ["docs/releases/v1.0.0-rc1.md lacks the section '## What to record during the week'"]
    # the week's section is rc1-only
    assert release.notes_problems(full.replace("## What to record during the week", "## Other"), FINAL) == []
    assert release.notes_problems(full + "\nTBD (Task 9)\n", RC1)


def test_final_refuses_an_open_rc1_row() -> None:
    text = ("| ID | Origin | Text | Status |\n|---|---|---|---|\n"
            "| RC1-1 | x | a | fixed in abc1234 |\n| RC1-2 | x | b \\| c | wontfix: not a defect |\n"
            "| RC1-3 | x | d | open |\n| P1-4 | x | e | open |\n")
    assert release.rc1_open_rows(text) == ["RC1-3: open"]


def test_recorded_and_fixed_earlier_close_an_rc1_row() -> None:
    text = ("| ID | Origin | Text | Status |\n|---|---|---|---|\n"
            "| RC1-1 | x | a | recorded: RED evidence kept in the ledger |\n"
            "| RC1-2 | x | b | fixed earlier: a commit that changed the code |\n"
            "| RC1-3 | x | c | open |\n| RC1-4 | x | d | records pending |\n")
    assert release.rc1_open_rows(text) == ["RC1-3: open", "RC1-4: records pending"]


def test_final_needs_the_use_log() -> None:
    assert release.use_log_problems(None)
    assert release.use_log_problems("notes only\n")
    assert release.use_log_problems("2026-10-26 connect: ok\n") == []


def test_rc1_runs_parity_in_report_mode_and_final_in_gate_mode() -> None:
    assert ["make", "parity"] in release.steps(RC1)
    assert ["make", "parity", "PARITY_GATE=1"] in release.steps(FINAL)
    assert ["make", "check", "VENDOR_TESTS=run"] == release.steps(RC1)[0]


def test_check_real_is_the_last_step() -> None:
    for t in (RC1, FINAL):
        chain = release.steps(t)
        assert chain[-1] == ["make", "check-real"] and len(chain) == 7
        names = [s[1] for s in chain[:6]]
        assert names == ["check", "check-py", "deferred", "parity", "wheel", "install-smoke"]


NOTES = "\n".join(["# notes", "## What is in", "## Install", "## Supported", "## Known limitations",
                   "## Deviations", "## Links", "## What to record during the week", ""])


def git(repo: Path, *args: str) -> None:
    """Runs git in repo."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path: Path, branch: str = "main") -> Path:
    """A committed temp repo that passes every rc1 precondition."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "docs" / "releases").mkdir(parents=True)
    (repo / ".superpowers" / "sdd" / "a").mkdir(parents=True)
    (repo / "backend" / "pyproject.toml").write_text('[project]\nversion = "1.0.0rc1"\n')
    (repo / "docs" / "releases" / "v1.0.0-rc1.md").write_text(NOTES)
    (repo / "PLAN.md").write_text("plan\n")
    (repo / "DEFERRED.md").write_text("register\n")
    (repo / ".superpowers" / "sdd" / "a" / "progress.md").write_text("ledger\n")
    (repo / ".gitignore").write_text("/dist/\n")
    git(repo, "init", "-q", "-b", branch)
    # repo-local, so the script's own `git tag` has an identity too: a CI runner has no global one
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


class Recorder:
    """An injected `run`: records argv, appends to the log like run_logged, returns a canned code."""

    def __init__(self, repo: Path, fail_on: tuple[str, ...] = (), code: int = 2,
                 wheel: str = "trex_web_gui-1.0.0rc1-py3-none-any.whl") -> None:
        """Steps whose make target is in fail_on return code; the `wheel` step writes repo/dist/<wheel>."""
        self.wheel = repo / "dist" / wheel
        self.calls: list[list[str]] = []
        self.fail_on = fail_on
        self.code = code

    def __call__(self, argv: list[str], log: Path) -> int:
        """Records the call and writes one log line."""
        self.calls.append(argv)
        if argv[1] == "wheel":                    # the real step builds the wheel under dist/
            self.wheel.parent.mkdir(parents=True, exist_ok=True)
            self.wheel.write_bytes(b"wheel")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write("$ " + " ".join(argv) + "\n")
        return self.code if argv[1] in self.fail_on else 0


def test_dirty_tree_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    (repo / "untracked.txt").write_text("x")
    rec = Recorder(repo)
    assert release.main(["check", "rc1"], run=rec, env={}, root=repo) == 1
    assert "working tree is not clean" in capsys.readouterr().out
    assert rec.calls == []


def test_not_main_is_refused_unless_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path, branch="p6-packaging")
    rec = Recorder(repo)
    assert release.main(["check", "rc1"], run=rec, env={}, root=repo) == 1
    assert "not main" in capsys.readouterr().out and rec.calls == []
    assert release.main(["check", "rc1", "--dry-run"], run=rec, env={}, root=repo) == 0
    assert "DRY RUN (tag from main after the merge):" in capsys.readouterr().out


def test_release_never_runs_push_tag_or_gh(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    rec = Recorder(repo)
    assert release.main(["check", "rc1"], run=rec, env={}, root=repo) == 0
    out = capsys.readouterr().out
    assert len(rec.calls) == 7
    for argv in rec.calls:
        assert argv[:2] not in (["git", "push"], ["git", "tag"]) and argv[0] != "gh"
    assert "git tag -a v1.0.0-rc1" in out and "git push origin v1.0.0-rc1" in out
    gh = [ln for ln in out.splitlines() if ln.startswith("gh release create")]
    assert gh and gh[0].startswith("gh release create v1.0.0-rc1 dist/trex_web_gui-1.0.0rc1-py3-none-any.whl")
    assert gh[0].endswith("--prerelease")
    assert not [ln for ln in out.splitlines() if ln.startswith("sha256")]   # once printed as if a command
    digest = [ln for ln in out.splitlines() if ln.startswith("# wheel sha256 ")]
    assert digest and "sha256sum dist/trex_web_gui-1.0.0rc1-py3-none-any.whl" in digest[0]


def test_a_missing_wheel_after_the_chain_fails_instead_of_printing_commands(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    rec = Recorder(repo, wheel="other-1.0-py3-none-any.whl")      # the chain passes but builds the wrong file
    assert release.main(["check", "rc1"], run=rec, env={}, root=repo) == 1
    out = capsys.readouterr().out
    assert "FAIL: dist/trex_web_gui-1.0.0rc1-py3-none-any.whl not found after the chain" in out
    assert "git tag" not in out and "gh release create" not in out


def test_skipped_real_tier_is_not_releasable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    rec = Recorder(repo)
    assert release.main(["check", "rc1"], run=rec, env={"RELEASE_REAL": "skip"}, root=repo) == 3
    out = capsys.readouterr().out
    assert "NOT RELEASABLE: make check-real was skipped" in out and "git tag" not in out
    assert ["make", "check-real"] not in rec.calls and len(rec.calls) == 6


def test_failed_step_stops_the_chain(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    rec = Recorder(repo, fail_on=("deferred",))
    assert release.main(["check", "rc1"], run=rec, env={}, root=repo) == 1
    assert "FAIL make deferred" in capsys.readouterr().out
    assert [c[1] for c in rec.calls] == ["check", "check-py", "deferred"]


def test_release_writes_only_under_dist(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    before = {p for p in repo.rglob("*") if ".git" not in p.parts}
    assert release.main(["check", "rc1"], run=Recorder(repo), env={}, root=repo) == 0
    new = {p for p in repo.rglob("*") if ".git" not in p.parts} - before
    assert new and all(p == repo / "dist" or (repo / "dist") in p.parents for p in new)
    assert any(p.name.startswith("release-rc1-") and p.suffix == ".log" for p in new)


def test_a_failing_git_call_is_refused_not_read_as_clean(tmp_path: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    for name in ("backend", "docs", "PLAN.md", "DEFERRED.md"):
        (plain / name).symlink_to(repo / name)
    rec = Recorder(repo)
    assert release.main(["check", "rc1", "--dry-run"], run=rec, env={}, root=plain) == 1
    out = capsys.readouterr().out
    assert "git status --porcelain failed" in out and "refusing" in out and rec.calls == []
    assert release.main(["guard"], run=rec, env={}, root=plain) == 1
    assert "guard: ok" not in capsys.readouterr().out


def test_targets_resolve_rc_n_and_rc_from_the_pyproject_version() -> None:
    rc2 = release.resolve_target("rc2", "")
    assert (rc2.name, rc2.version, rc2.tag, rc2.notes, rc2.prerelease) == (
        "rc2", "1.0.0rc2", "v1.0.0-rc2", "docs/releases/v1.0.0-rc2.md", True)
    assert release.resolve_target("rc", '[project]\nversion = "1.0.0rc3"\n').tag == "v1.0.0-rc3"
    assert release.resolve_target("final", "").tag == "v1.0.0"
    assert "not a 1.0.0 release candidate" in release.resolve_target("rc", '[project]\nversion = "1.0.0"\n')
    assert "unknown target" in release.resolve_target("rc0", "")
    # every candidate's notes say what to record during its week, not only rc1's
    got = release.notes_problems("\n".join(release.REQUIRED_SECTIONS), release.rc_target(2))
    assert got == ["docs/releases/v1.0.0-rc2.md lacks the section '## What to record during the week'"]


def test_check_rc_takes_the_candidate_from_pyproject(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    rec = Recorder(repo)
    assert release.main(["check", "rc"], run=rec, env={}, root=repo) == 0
    assert release.main(["check", "rc2"], run=Recorder(repo), env={}, root=repo) == 1   # pyproject says rc1


def test_final_refuses_without_deferred_md(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The public tree has no records: the final gate says so instead of finding no open rows."""
    repo = make_repo(tmp_path)
    git(repo, "rm", "-q", "DEFERRED.md")
    git(repo, "commit", "-qm", "no records")
    rec = Recorder(repo)
    assert release.main(["check", "final"], run=rec, env={}, root=repo) == 1
    assert "DEFERRED.md not found: the final gate runs in the private repo" in capsys.readouterr().out
    assert rec.calls == []


def test_guard_refuses_without_plan_md(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    git(repo, "rm", "-q", "PLAN.md")
    git(repo, "commit", "-qm", "no records")
    assert release.main(["guard"], root=repo) == 1
    assert "PLAN.md not found" in capsys.readouterr().out


# `publish`: the person runs it after a passing `check`; it tags, pushes, releases and checks the uploaded
# wheel, resuming after a failure (rc5's publication hit three GitHub 500s on 2026-10-30).

HTTP500 = "remote: Internal Server Error\n! [remote rejected] (Internal Server Error)"


class FakeGitHub:
    """An injected `sh` for publish: `git ls-remote`, `git push` and `gh release …` against an in-memory
    remote, every other git call run for real in the repo. `fail[key]` is a list of outputs the next calls of
    that kind fail with; a key in `lands` still takes effect before failing (a request GitHub answered 500 but
    kept)."""

    def __init__(self, repo: Path) -> None:
        """An empty remote whose main is the repo's HEAD (pushed already) and no release."""
        self.repo = repo
        self.refs = {"refs/heads/main": self.rev("HEAD")}
        self.release: dict[str, Any] | None = None
        self.calls: list[list[str]] = []
        self.fail: dict[str, list[str]] = {}
        self.lands: set[str] = set()
        self.signed: set[str] = set()     # tags made with -s (made here with -a: the test has no key)
        self.good_signature = True

    def rev(self, ref: str) -> str:
        """The repo's object name for ref."""
        return subprocess.run(["git", "rev-parse", ref], cwd=self.repo, capture_output=True, text=True,
                              check=True).stdout.strip()

    def writes(self) -> list[list[str]]:
        """The calls that change the remote: pushes and gh create/upload/edit."""
        return [c for c in self.calls if c[:2] == ["git", "push"] or (c[0] == "gh" and c[2] != "view"
                                                                       and c[2] != "download")]

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        """Answers one command: (exit code, combined output)."""
        if "verify-tag" in argv:
            self.calls.append(argv)
            ok = self.good_signature and argv[-1] in self.signed
            return (0, 'Good "git" signature') if ok else (1, f"error: no signature found in {argv[-1]}")
        if argv[:2] == ["git", "tag"] and "-s" in argv:
            self.calls.append(argv)
            self.signed.add(argv[argv.index("-s") + 1])
            argv = [("-a" if a == "-s" else a) for a in argv]
        if argv[0] == "git" and argv[1] not in ("ls-remote", "push"):
            r = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, check=False)
            return r.returncode, r.stdout + r.stderr
        self.calls.append(argv)
        key = {"ls-remote": "ls-remote", "push": "push"}.get(argv[1]) if argv[0] == "git" else argv[2]
        if key == "push":
            key = "push-tag" if argv[3].startswith("refs/tags/") else "push-main"
        failing = self.fail.get(key)
        if failing:
            if key in self.lands:
                self.apply(key, argv)
            return 1, failing.pop(0)
        return self.apply(key, argv)

    def apply(self, key: str, argv: list[str]) -> tuple[int, str]:
        """Carries out one command on the fake remote."""
        if key == "ls-remote":
            sha = self.refs.get(argv[3])
            return 0, f"{sha}\t{argv[3]}\n" if sha else ""
        if key == "push-main":
            self.refs["refs/heads/main"] = self.rev(argv[3].split(":")[0])
            return 0, ""
        if key == "push-tag":
            self.refs[argv[3]] = self.rev(argv[3])
            return 0, ""
        rel = self.release
        if key == "view":
            if rel is None:
                return 1, "release not found"
            assets = [{"name": n, "size": len(b)} for n, b in rel["assets"].items()]
            return 0, json.dumps({"isDraft": rel["draft"], "isPrerelease": rel["prerelease"],
                                  "assets": assets})
        if key == "create":
            if rel is not None:
                return 1, f"a release with the same tag name already exists: {argv[3]}"
            partial = "create" in self.lands and self.fail.get("create") is not None
            wheel = self.repo / argv[4]
            self.release = {"draft": partial or "--draft" in argv, "prerelease": "--prerelease" in argv,
                            "assets": {} if partial else {wheel.name: wheel.read_bytes()}}
            return 0, f"https://github.com/o/r/releases/tag/{argv[3]}\n"
        assert rel is not None, argv
        if key == "upload":
            wheel = self.repo / argv[4]
            rel["assets"][wheel.name] = wheel.read_bytes()
        elif key == "edit":
            rel["draft"] = argv[4] != "--draft=false"
        elif key == "download":
            name, out = argv[argv.index("--pattern") + 1], Path(argv[argv.index("--dir") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / name).write_bytes(rel["assets"][name])
        return 0, ""


def checked_repo(tmp_path: Path) -> Path:
    """A repo whose `check rc1` passed: the wheel and the pass record are in dist/."""
    repo = make_repo(tmp_path)
    assert release.main(["check", "rc1"], run=Recorder(repo), env={}, root=repo) == 0
    return repo


def publish(repo: Path, gh: FakeGitHub, sleeps: list[float] | None = None) -> int:
    """`publish rc1` against the fake remote, recording the backoff sleeps instead of sleeping."""
    waits = [] if sleeps is None else sleeps
    return release.main(["publish", "rc1"], root=repo, sh=gh, sleep=waits.append)


def test_check_records_its_pass_for_publish(tmp_path: Path) -> None:
    repo = checked_repo(tmp_path)
    record = json.loads((repo / "dist" / "release-rc1-passed.json").read_text())
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True).stdout.strip()
    assert record == {"gate": "check", "sha": head, "tag": "v1.0.0-rc1",
                      "wheel": "trex_web_gui-1.0.0rc1-py3-none-any.whl",
                      "sha256": hashlib.sha256(b"wheel").hexdigest()}


def test_a_dry_run_check_records_no_pass(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, branch="p6-packaging")
    assert release.main(["check", "rc1", "--dry-run"], run=Recorder(repo), env={}, root=repo) == 0
    assert not (repo / "dist" / "release-rc1-passed.json").exists()


def test_publish_tags_pushes_releases_and_checks_the_wheel(tmp_path: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 0
    out = capsys.readouterr().out
    tag = gh.rev("refs/tags/v1.0.0-rc1")
    assert gh.rev("v1.0.0-rc1^{commit}") == gh.rev("HEAD") and gh.refs["refs/tags/v1.0.0-rc1"] == tag
    assert gh.release == {"draft": False, "prerelease": True,
                          "assets": {"trex_web_gui-1.0.0rc1-py3-none-any.whl": b"wheel"}}
    create = [c for c in gh.calls if c[:3] == ["gh", "release", "create"]]
    assert create == [["gh", "release", "create", "v1.0.0-rc1", "dist/trex_web_gui-1.0.0rc1-py3-none-any.whl",
                       "--title", "trex-web-gui 1.0.0rc1", "--notes-file", "docs/releases/v1.0.0-rc1.md",
                       "--prerelease", "--verify-tag"]]
    assert f"the released wheel's sha256 is {hashlib.sha256(b'wheel').hexdigest()}, as built" in out
    assert not list((repo / "dist").glob("publish-check-*"))        # the downloaded copy is removed


def test_publish_pushes_main_when_it_is_not_pushed(tmp_path: Path) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    head = gh.refs.pop("refs/heads/main")
    assert publish(repo, gh) == 0
    assert gh.refs["refs/heads/main"] == head
    assert ["git", "push", "origin", f"{head}:refs/heads/main"] in gh.calls


def test_a_tag_push_failing_with_500_is_retried_with_backoff(tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.fail["push-tag"] = [HTTP500, HTTP500]
    sleeps: list[float] = []
    assert publish(repo, gh, sleeps) == 0
    assert sleeps == [5, 15]
    assert len([c for c in gh.calls if c[:2] == ["git", "push"]]) == 3
    assert "GitHub error, retrying in 5 s (2/4)" in capsys.readouterr().out


def test_a_push_that_failed_but_landed_is_not_repeated(tmp_path: Path) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.fail["push-tag"], gh.lands = [HTTP500], {"push-tag"}
    assert publish(repo, gh) == 0
    assert len([c for c in gh.calls if c[:2] == ["git", "push"]]) == 1


def test_a_half_made_draft_release_is_finished_not_duplicated(tmp_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """gh creates a draft, uploads, then publishes: a 500 between leaves a draft with no wheel."""
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.fail["create"], gh.lands = ["HTTP 500 (https://api.github.com/repos/o/r/releases)"], {"create"}
    assert publish(repo, gh) == 0
    out = capsys.readouterr().out
    assert [ln for ln in out.splitlines() if ln.startswith("release: ")][:3] == [
        "release: creating the release", "release: uploading the wheel",
        "release: publishing the draft release"]
    kinds = [c[2] for c in gh.calls if c[0] == "gh" and c[2] in ("create", "upload", "edit")]
    assert kinds == ["create", "upload", "edit"]
    assert gh.release is not None and gh.release["draft"] is False
    assert gh.release["assets"] == {"trex_web_gui-1.0.0rc1-py3-none-any.whl": b"wheel"}


def test_a_second_publish_changes_nothing(tmp_path: Path) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 0
    gh.calls.clear()
    assert publish(repo, gh) == 0
    assert gh.writes() == []


def test_an_error_that_is_not_transient_stops_at_once(tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.fail["push-tag"] = ["remote: Permission to o/r.git denied to someone."]
    sleeps: list[float] = []
    assert publish(repo, gh, sleeps) == 1
    out = capsys.readouterr().out
    assert sleeps == [] and "Permission to o/r.git denied" in out
    assert "rerun `make release-rc-publish`" in out
    assert gh.release is None


def test_retries_end_after_four_attempts(tmp_path: Path) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.fail["push-tag"] = [HTTP500] * 9
    sleeps: list[float] = []
    assert publish(repo, gh, sleeps) == 1
    assert sleeps == [5, 15, 45]
    assert len([c for c in gh.calls if c[:2] == ["git", "push"]]) == 4


def test_publish_refuses_without_a_passing_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 1
    assert "no passing `check rc1` recorded" in capsys.readouterr().out
    assert gh.calls == []


def test_publish_refuses_a_record_for_another_commit(tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    git(repo, "commit", "-q", "--allow-empty", "-m", "later")
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 1
    assert "the passing check was for" in capsys.readouterr().out and gh.calls == []


def test_publish_refuses_a_wheel_changed_since_the_check(tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    (repo / "dist" / "trex_web_gui-1.0.0rc1-py3-none-any.whl").write_bytes(b"rebuilt")
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 1
    assert "not the wheel the check built" in capsys.readouterr().out and gh.calls == []


def test_publish_refuses_a_tag_on_another_commit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    git(repo, "tag", "-a", "v1.0.0-rc1", "-m", "old", "HEAD")
    git(repo, "commit", "-q", "--allow-empty", "-m", "later")
    assert release.main(["check", "rc1"], run=Recorder(repo), env={}, root=repo) == 0   # the record moves on
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 1
    assert "tag v1.0.0-rc1 points at" in capsys.readouterr().out and gh.writes() == []


def test_publish_refuses_a_remote_tag_that_differs(tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.refs["refs/tags/v1.0.0-rc1"] = "0" * 40
    assert publish(repo, gh) == 1
    assert "GitHub's tag v1.0.0-rc1 is" in capsys.readouterr().out and gh.release is None


def test_publish_refuses_a_released_wheel_that_differs(tmp_path: Path,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.release = {"draft": False, "prerelease": True,
                  "assets": {"trex_web_gui-1.0.0rc1-py3-none-any.whl": b"x"}}
    assert publish(repo, gh) == 1
    want, got = hashlib.sha256(b"wheel").hexdigest(), hashlib.sha256(b"x").hexdigest()
    assert f"the released wheel's sha256 is {got}, not the built {want}" in capsys.readouterr().out
    assert [c for c in gh.writes() if c[0] == "gh"] == []     # a release already there is never replaced


# v1.0.0 (F1): built and published in the public clone, with a signed tag and a draft the person reviews.

FINAL_WHEEL = "trex_web_gui-1.0.0-py3-none-any.whl"


def public_repo(tmp_path: Path) -> Path:
    """The public clone at 1.0.0: no records (PLAN.md, DEFERRED.md, ledgers), the v1.0.0 notes."""
    repo = make_repo(tmp_path)
    (repo / "backend" / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (repo / "docs" / "releases" / "v1.0.0.md").write_text(NOTES)
    git(repo, "rm", "-q", "PLAN.md", "DEFERRED.md", ".superpowers/sdd/a/progress.md")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "trex-web-gui 1.0.0")
    return repo


def built_public_repo(tmp_path: Path) -> Path:
    """public_repo after `build final` passed."""
    repo = public_repo(tmp_path)
    assert release.main(["build", "final"], run=Recorder(repo, wheel=FINAL_WHEEL), env={}, root=repo) == 0
    return repo


def publish_final(repo: Path, gh: FakeGitHub, *extra: str) -> int:
    """`publish final` against the fake remote, with a home whose allowed_signers the verify names."""
    return release.main(["publish", "final", *extra], root=repo, sh=gh, sleep=lambda s: None,
                        env={"HOME": "/h"})


def test_build_final_runs_the_wheel_and_the_smoke_and_records_them(tmp_path: Path) -> None:
    repo = public_repo(tmp_path)
    rec = Recorder(repo, wheel=FINAL_WHEEL)
    assert release.main(["build", "final"], run=rec, env={}, root=repo) == 0
    assert rec.calls == [["make", "wheel"], ["make", "install-smoke"]]
    record = json.loads((repo / "dist" / "release-final-passed.json").read_text())
    assert (record["gate"], record["tag"], record["wheel"]) == ("build", "v1.0.0", FINAL_WHEEL)


def test_build_is_for_final_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = make_repo(tmp_path)
    assert release.main(["build", "rc1"], run=Recorder(repo), env={}, root=repo) == 1
    assert "build is for final" in capsys.readouterr().out


def test_publish_final_signs_the_tag_and_stops_at_a_draft(tmp_path: Path,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    repo = built_public_repo(tmp_path)
    gh = FakeGitHub(repo)
    assert publish_final(repo, gh) == 0
    out = capsys.readouterr().out
    assert gh.signed == {"v1.0.0"}
    assert ["git", "-c", "gpg.ssh.allowedSignersFile=/h/.config/git/allowed_signers", "verify-tag",
            "v1.0.0"] in gh.calls
    assert gh.release == {"draft": True, "prerelease": False, "assets": {FINAL_WHEEL: b"wheel"}}
    create = [c for c in gh.calls if c[:3] == ["gh", "release", "create"]][0]
    assert "--draft" in create and "--prerelease" not in create
    assert "verify: the released wheel's sha256" in out                       # the draft's wheel is checked
    assert "make release-publish PUBLISH_DRAFT=1" in out and "is released" not in out


def test_publish_draft_publishes_it_and_checks_the_wheel_again(tmp_path: Path,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    repo = built_public_repo(tmp_path)
    gh = FakeGitHub(repo)
    assert publish_final(repo, gh) == 0
    capsys.readouterr()
    assert publish_final(repo, gh, "--publish-draft") == 0
    out = capsys.readouterr().out
    assert gh.release is not None and gh.release["draft"] is False
    assert len([c for c in gh.calls if c[:3] == ["gh", "release", "edit"]]) == 1
    assert "verify: the released wheel's sha256" in out and "v1.0.0 is released" in out
    # the host needs no GitHub account or gh: a plain download from the public release, checked, installed
    digest = json.loads((repo / "dist" / "release-final-passed.json").read_text())["sha256"]
    host = out.split("On the host:\n", 1)[1].splitlines()
    assert host == [
        f"curl -fLO https://github.com/CRinATL/trex-web-gui/releases/download/v1.0.0/{FINAL_WHEEL}",
        f"echo '{digest}  {FINAL_WHEEL}' | sha256sum -c -",
        f"uv tool install --python 3.12 ./{FINAL_WHEEL}",
    ]
    assert "gh release download" not in out


def test_a_tag_whose_signature_does_not_verify_is_not_pushed(tmp_path: Path,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    repo = built_public_repo(tmp_path)
    gh = FakeGitHub(repo)
    gh.good_signature = False
    assert publish_final(repo, gh) == 1
    assert "the signature of tag v1.0.0 does not verify" in capsys.readouterr().out
    assert gh.writes() == []


def test_publish_final_refuses_the_private_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Gate 2 (`check final`) runs in the private repo; v1.0.0 is published from the public clone (F1)."""
    repo = make_repo(tmp_path)
    (repo / "backend" / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (repo / "docs" / "releases" / "v1.0.0.md").write_text(NOTES)
    (repo / "docs" / "releases" / "v1.0.0-rc1-use.md").write_text("2026-10-27 used\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "1.0.0")
    assert release.main(["check", "final"], run=Recorder(repo, wheel=FINAL_WHEEL), env={}, root=repo) == 0
    gh = FakeGitHub(repo)
    assert publish_final(repo, gh) == 1
    assert "publish final runs in the public clone" in capsys.readouterr().out and gh.calls == []


def test_publish_rc_needs_the_full_check_not_a_build(tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    repo = checked_repo(tmp_path)
    path = repo / "dist" / "release-rc1-passed.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "gate": "build"}))
    gh = FakeGitHub(repo)
    assert publish(repo, gh) == 1
    assert "recorded by build, not by check" in capsys.readouterr().out and gh.calls == []


def test_gate_2_hands_over_to_f1_instead_of_printing_private_release_commands(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """v1.0.0 is tagged and released from the public clone (F1), never from the private repo gate 2 runs
    in."""
    repo = make_repo(tmp_path)
    (repo / "backend" / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n')
    (repo / "docs" / "releases" / "v1.0.0.md").write_text(NOTES)
    (repo / "docs" / "releases" / "v1.0.0-rc1-use.md").write_text("2026-10-27 used\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "1.0.0")
    assert release.main(["check", "final"], run=Recorder(repo, wheel=FINAL_WHEEL), env={}, root=repo) == 0
    out = capsys.readouterr().out
    assert "git tag" not in out and "gh release create" not in out and "git push" not in out
    assert "nothing is tagged or released from this repo" in out
    assert "make release-build" in out and "make release-publish" in out
    assert "# wheel sha256 (a record" in out
