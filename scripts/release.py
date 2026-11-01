"""Release gate (P6, D10): `release.py check rc|rc<N>|final|patch [--dry-run]`, `release.py build final`,
`release.py publish rc|rc<N>|final|patch [--publish-draft]` and `release.py guard`.

`check` checks the preconditions, runs the gate chain (each step logged to
dist/release-<target>-YYYY-MM-DD-HHMMSSZ.log), records the pass in dist/release-<target>-passed.json and
PRINTS the push, tag and release commands. It never runs `git push`, `git tag` or `gh` (canon:WRK-3) and
writes nothing outside dist/.

`publish` is those commands, run only when the person runs it (make release-rc-publish, make
release-publish, which runs `publish patch`; nothing else calls it): for the commit a passing `check`
recorded it pushes main if needed, tags, pushes the tag, creates the GitHub release and downloads the
released wheel to compare its sha256 with the built one. Each step reads GitHub's state first and is skipped
when already done, a GitHub 5xx or dropped connection is retried with backoff, and a failed write is re-read
before anything else (GitHub can answer 500 and keep the change), so a rerun resumes where a failure
stopped. It never replaces a tag or a release that differs; it refuses.

v1.0.0 is published from the public clone (F1), which has no records, so gate 2's `check final` cannot run
there: `build final` (`release.py build final`; make release-build) runs `make wheel` and `make
install-smoke` in that tree and records the wheel, and `publish final` refuses any tree holding the records.
Its tag is signed (`git tag -s`, checked with `git verify-tag` against ~/.config/git/allowed_signers) and
its release is made as a draft: publish verifies the draft's wheel and stops for the person to look at the
draft; `--publish-draft` publishes it and verifies the wheel again. That is `release.py publish final
[--publish-draft]`; make release-publish and PUBLISH_DRAFT=1 now run `publish patch`.

`check patch` and `publish patch` release 1.0.N, the N that backend/pyproject.toml names (a 1.0.N release
commit sets that version AND commits the backend/uv.lock that uv rewrites for it: CI runs `uv sync --locked`,
and `check patch` refuses the dirty tree otherwise), as a full (not
pre-) release: signed tag, a draft first, and main is never pushed (it reaches GitHub through a
PR).

Run it as `uv run --project backend python -I -B scripts/release.py ...` (make release-rc, make release). Exit
codes: 0 releasable (published, for publish), 1 a precondition or a step failed, 3 the real tier was skipped
(RELEASE_REAL=skip).

`publish patch` also writes dist/install.sh (install_script, a pure function of the target and the wheel's
sha256), uploads it to the draft with the wheel and checks the released copy byte for byte; a published patch
release without it is refused, since a published release cannot take another file."""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Where a published release's files are downloaded from, by anyone: the repo is public, so the host needs no
# GitHub account and no gh.
RELEASES = "https://github.com/CRinATL/trex-web-gui/releases/download"
# A release's own page, where its wheel's sha256 is listed: install.sh names it so the sha256 can be checked.
RELEASE_PAGE = "https://github.com/CRinATL/trex-web-gui/releases/tag"
# Extra `uv tool install` flags. Measured with uv 0.12.5: the plain command replaces an installed older
# version of the same tool, so none are needed.
INSTALL_FLAGS = ""
GUARDED = ("PLAN.md", "DEFERRED.md")
LEDGER_GLOB = ".superpowers/sdd/*/progress.md"
SKIPPED_EXIT = 3


@dataclass(frozen=True)
class Target:
    """One release gate: the version in pyproject, the git tag, the notes file and the release kind."""

    name: str
    version: str
    tag: str
    notes: str
    prerelease: bool


FINAL = Target("final", "1.0.0", "v1.0.0", "docs/releases/v1.0.0.md", False)
RC_NAME = re.compile(r"rc([1-9]\d*)")


def rc_target(n: int) -> Target:
    """Release candidate n of 1.0.0: version 1.0.0rc<n>, tag v1.0.0-rc<n>, its own notes file."""
    return Target(f"rc{n}", f"1.0.0rc{n}", f"v1.0.0-rc{n}", f"docs/releases/v1.0.0-rc{n}.md", True)


PATCH_VERSION = re.compile(r"1\.0\.([1-9]\d*)")
INSTALL_SH = "install.sh"


def patch_target(n: int) -> Target:
    """Patch n of 1.0: version 1.0.<n>, tag v1.0.<n>, its own public notes file; a full release."""
    return Target("patch", f"1.0.{n}", f"v1.0.{n}", f"docs/releases/v1.0.{n}.md", False)


def carries_install_sh(target: Target) -> bool:
    """Whether the release carries install.sh: only a patch does. v1.0.0 is published and immutable, and
    `releases/latest` never resolves to a pre-release, so final and rc keep their one asset."""
    return target.name == "patch"


def resolve_target(name: str, pyproject_text: str) -> Target | str:
    """The Target for `final`, `rc<N>`, `rc` or `patch` (the last two take the version pyproject names, so
    neither needs an edit per release); a str says why there is none."""
    if name == "final":
        return FINAL
    if m := RC_NAME.fullmatch(name):
        return rc_target(int(m.group(1)))
    if name not in ("rc", "patch"):
        return f"unknown target {name!r}: rc, rc<N>, final or patch"
    try:
        version = tomllib.loads(pyproject_text).get("project", {}).get("version")
    except tomllib.TOMLDecodeError as e:
        return f"backend/pyproject.toml is not valid TOML: {e}"
    if name == "patch":
        m = PATCH_VERSION.fullmatch(version) if isinstance(version, str) else None
        if m is None:
            return f"backend/pyproject.toml version is {version!r}, not a 1.0.x patch (1.0.1 or later)"
        return patch_target(int(m.group(1)))
    m = re.fullmatch(r"1\.0\.0rc([1-9]\d*)", version) if isinstance(version, str) else None
    if m is None:
        return f"backend/pyproject.toml version is {version!r}, not a 1.0.0 release candidate"
    return rc_target(int(m.group(1)))


USE_LOG = "docs/releases/v1.0.0-rc1-use.md"
DEFERRED = "DEFERRED.md"
REQUIRED_SECTIONS = ("## What is in", "## Install", "## Supported", "## Known limitations", "## Deviations",
                     "## Links")
RC_SECTION = "## What to record during the week"   # every candidate says what its users log
PLACEHOLDER = "TBD (Task 9)"

VAR_TMP = re.compile(r"/var/tmp(?:/[^\s`'\"()\[\]<>,;|]*)?")
VM_WORK_DIR = re.compile(r"^/var/tmp/trex-vm(?:-[^/]+)?(?:/|$)")
COMMITTED = re.compile(r"\(committed: ([^)]+)\)")


def wheel_name(version: str) -> str:
    """The wheel file name hatchling gives a pure-Python build of this version."""
    return f"trex_web_gui-{version}-py3-none-any.whl"


# The text of install.sh, with {placeholders} that install_script fills; every other brace is shell syntax.
INSTALL_SCRIPT = (
    "#!/bin/sh\n"
    "# trex-web-gui {version} installer, generated by scripts/release.py for release {tag}.\n"
    "# It downloads this release's wheel, checks its sha256 and installs it with uv.\n"
    "# Check the sha256 below independently: the release page lists it next to the wheel.\n"
    "#   {RELEASE_PAGE}/{tag}\n"
    "# Before installing it pauses on the terminal, so the sha256 can be compared with the release page "
    "first\n"
    "# (TREX_WEB_GUI_YES=1 skips the pause). TREX_WEB_GUI_RELEASES and TREX_WEB_GUI_TTY override the "
    "download\n"
    "# base and the terminal; they exist for tests only.\n"
    "# The whole script is one function, called on the last line: a download cut short runs nothing.\n"
    "set -eu\n"
    "main() {\n"
    "  tag='{tag}'\n"
    "  wheel='{wheel}'\n"
    "  sha256='{sha256}'\n"
    "  page='{RELEASE_PAGE}/{tag}'\n"
    "  base=\"${TREX_WEB_GUI_RELEASES:-{RELEASES}}\"\n"
    "  tty=\"${TREX_WEB_GUI_TTY:-/dev/tty}\"\n"
    "  command -v uv >/dev/null 2>&1 || { echo \"uv is needed: https://docs.astral.sh/uv/\" >&2; exit 1; }\n"
    "  echo \"trex-web-gui {version} ($tag): wheel sha256 $sha256\"\n"
    "  echo \"Check it independently on $page\"\n"
    "  dir=$(mktemp -d)\n"
    "  trap 'rm -f \"$dir/$wheel\"; rmdir \"$dir\"' EXIT\n"
    "  trap 'exit 130' HUP INT TERM\n"
    "  curl -fL -o \"$dir/$wheel\" \"$base/$tag/$wheel\"\n"
    "  echo \"$sha256  $dir/$wheel\" | sha256sum -c -\n"
    "  if [ \"${TREX_WEB_GUI_YES:-}\" = 1 ]; then\n"
    "    :\n"
    "  elif (: <\"$tty\") 2>/dev/null; then\n"
    "    printf 'The wheel matches the sha256 above. Compare it with %s, then press Enter to install "
    "(Ctrl-C stops): ' \"$page\" >>\"$tty\"\n"
    "    read -r _ <\"$tty\" || { echo \"stopped: nothing installed\" >&2; exit 1; }\n"
    "  else\n"
    "    echo \"no terminal to pause on: installing without a pause "
    "(TREX_WEB_GUI_YES=1 says so explicitly)\"\n"
    "  fi\n"
    "  uv tool install --python 3.12 {flags} \"$dir/$wheel\"\n"
    "}\n"
    "main \"$@\"; exit $?\n"
)


def install_script(target: Target, sha256: str) -> str:
    """The text of install.sh for this release: a pure function of the tag, version, wheel name and sha256,
    so a resumed publish regenerates the same bytes. Tokens are replaced, not formatted: the braces are the
    shell's own."""
    text = INSTALL_SCRIPT
    flags = f"{INSTALL_FLAGS} " if INSTALL_FLAGS else ""
    text = text.replace("{flags} ", flags)
    for token, value in (("{version}", target.version), ("{tag}", target.tag),
                         ("{wheel}", wheel_name(target.version)), ("{sha256}", sha256),
                         ("{RELEASE_PAGE}", RELEASE_PAGE), ("{RELEASES}", RELEASES)):
        text = text.replace(token, value)
    return text


def _exempt(path: str) -> bool:
    """True for the bare /var/tmp and anything in a VM work directory (D5): configuration, not evidence."""
    return path == "/var/tmp" or VM_WORK_DIR.match(path) is not None


def _committed_ok(path: str, tracked: set[str]) -> bool:
    """True when path is a tracked file or the prefix of tracked files (a tracked directory)."""
    path = path.strip().rstrip("/")
    return path in tracked or any(t.startswith(path + "/") for t in tracked)


def guard_problems(texts: dict[str, str], tracked: set[str]) -> list[str]:
    """One problem per /var/tmp mention that is neither exempt, nor on a line marked `(aged out`, nor on a
    line naming a tracked `(committed: <path>)`. Texts are keyed by file name; lines count from 1."""
    problems = []
    for name, text in texts.items():
        for n, line in enumerate(text.splitlines(), 1):
            mentions = [m.rstrip(".:") for m in VAR_TMP.findall(line)]
            mentions = [m for m in mentions if not _exempt(m)]
            if not mentions or "(aged out" in line:
                continue
            if any(_committed_ok(p, tracked) for p in COMMITTED.findall(line)):
                continue
            hint = 'has no committed copy (add "(committed: <path>)" or "(aged out; …)")'
            problems += [f"{name}:{n}: {m} {hint}" for m in mentions]
    return problems


def version_problems(pyproject_text: str, target: Target) -> list[str]:
    """The pyproject version must be the target's."""
    try:
        version = tomllib.loads(pyproject_text).get("project", {}).get("version")
    except tomllib.TOMLDecodeError as e:
        return [f"backend/pyproject.toml is not valid TOML: {e}"]
    if version != target.version:
        return [f"backend/pyproject.toml version is {version!r}; {target.name} needs {target.version!r}"]
    return []


def notes_problems(text: str | None, target: Target) -> list[str]:
    """The notes file must exist, hold every required section, and carry no unfilled placeholder."""
    if text is None:
        return [f"{target.notes} is missing"]
    sections = REQUIRED_SECTIONS + ((RC_SECTION,) if target.prerelease else ())
    lines = text.splitlines()
    problems = [f"{target.notes} lacks the section {s!r}" for s in sections
                if not any(ln.startswith(s) for ln in lines)]
    if PLACEHOLDER in text:
        problems.append(f"{target.notes} still holds {PLACEHOLDER!r}")
    return problems


def rc1_open_rows(deferred_text: str) -> list[str]:
    """`RC1-<n>: <status>` for each register row whose status does not start `fixed` (which covers
    `fixed earlier`), `recorded` or `wontfix`."""
    open_rows = []
    for line in deferred_text.splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)]
        if len(cells) < 4 or not re.fullmatch(r"RC1-\d+", cells[1]):
            continue
        status = cells[-2]
        if not status.startswith(("fixed", "recorded", "wontfix")):
            open_rows.append(f"{cells[1]}: {status}")
    return open_rows


def use_log_problems(text: str | None, path: str = USE_LOG) -> list[str]:
    """A week's use log must exist and hold at least one line starting with a date."""
    if text is None:
        return [f"{path} is missing"]
    if not re.search(r"^\d{4}-\d{2}-\d{2}", text, re.M):
        return [f"{path} has no line starting with a date (YYYY-MM-DD)"]
    return []


HOST_USE_LOG = "docs/releases/host-use-1.0.x.md"
HOST_SECTION = "## Host use (1.0.x)"
CONF = ".sdd.conf"


def host_rows_problems(deferred_text: str) -> list[str]:
    """The register must hold the host-use section, and every `H-<n>` row anywhere in it must be closed
    (status starts `fixed`, `wontfix` or `recorded`); the status is the last cell, never the finding text."""
    problems = []
    if HOST_SECTION not in deferred_text.splitlines():
        problems.append(f"{DEFERRED} lacks the section {HOST_SECTION!r}")
    for line in deferred_text.splitlines():
        opened = re.match(r"\s*\|\s*(H-\d+)\s*\|", line)
        if opened and not re.search(r"(?<!\\)\|$", line.rstrip()):
            problems.append(f"malformed host row {opened.group(1)}: no closing |")
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)]
        if len(cells) < 4 or not re.fullmatch(r"H-\d+", cells[1]):
            continue
        status = cells[-2]
        if not status.startswith(("fixed", "wontfix", "recorded")):
            problems.append(f"open host row {cells[1]}: {status}")
    return problems


def steps(target: Target) -> list[list[str]]:
    """The gate chain, in order; check-real is last (the longest, and it holds the VM)."""
    parity = ["make", "parity"] + (["PARITY_GATE=1"] if target.name in ("final", "patch") else [])
    return [["make", "check", "VENDOR_TESTS=run"], ["make", "check-py"], ["make", "deferred"], parity,
            ["make", "wheel"], ["make", "install-smoke"], ["make", "check-real"]]


def release_commands(target: Target, sha: str, wheel: Path, sha256: str) -> list[str]:
    """The commands the person runs after the gate: push main, tag, push the tag, create the release.

    The wheel's digest closes the list as a shell comment, so pasting the whole block runs nothing extra."""
    gh = f'gh release create {target.tag} dist/{wheel.name} --title "trex-web-gui {target.version}"'
    gh += f" --notes-file {target.notes}" + (" --prerelease" if target.prerelease else "")
    return ["git push origin main    # if main is not pushed yet",
            f'git tag -a {target.tag} -m "trex-web-gui {target.version}" {sha}',
            f"git push origin {target.tag}",
            gh,
            f"# wheel sha256 (a record, not a command; `sha256sum dist/{wheel.name}` reprints it): {sha256}"]


def run_logged(argv: list[str], log: Path) -> int:
    """Runs argv in the repo root, appending its output to log after a `$ argv` line; returns its code."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write("$ " + " ".join(argv) + "\n")
        f.flush()
        return subprocess.run(argv, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, check=False).returncode


class GitError(RuntimeError):
    """A git call failed; the gate must refuse, never read the failure as empty output."""


def _git(root: Path, *args: str) -> str:
    """Stdout of `git <args>` in root; raises GitError when git cannot run or exits nonzero."""
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    except OSError as e:
        raise GitError(f"git {' '.join(args)} could not run: {e}") from e
    if r.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed (exit {r.returncode}): {r.stderr.strip()}")
    return r.stdout


def _read(path: Path) -> str | None:
    """The file's text, or None when it does not exist."""
    return path.read_text() if path.is_file() else None


def records_git_dir(root: Path, home: str) -> Path | str | None:
    """The records' git dir, read from root's .sdd.conf like the sdd scripts' ledger_git_dir(): the last
    `LEDGER_GIT_DIR=` line, no quote stripping, a leading `~` is home, a relative path is relative to root.
    None when no records are configured; a str reason when the key is set but unusable (inside the repo, or
    not a git dir), so the caller refuses rather than scan nothing."""
    conf = root / CONF
    if not conf.is_file():
        return None
    values = re.findall(r"^LEDGER_GIT_DIR=(.*)$", conf.read_text(), re.M)
    if not values or not values[-1]:
        return None
    value = values[-1]
    raw = home + value[1:] if value.startswith("~") else value
    path = Path(os.path.realpath(raw if raw.startswith("/") else str(root / raw)))
    top = Path(os.path.realpath(root))
    if path == top or top in path.parents:
        return (f"{CONF} sets LEDGER_GIT_DIR={value}, which is inside the repo ({top}); "
                "only a git dir outside it is used")
    try:
        _git(root, f"--git-dir={path}", "rev-parse", "--git-dir")
    except GitError:
        return f"{CONF} sets LEDGER_GIT_DIR={value}, which is not a git dir"
    return path


def _rec(root: Path, gd: Path, *args: str) -> str:
    """`git <args>` against the records' git dir, whose work tree is the public clone."""
    return _git(root, f"--git-dir={gd}", f"--work-tree={root}", *args)


def records_problems(root: Path, gd: Path) -> list[str]:
    """The records must track the register, the host-use log and .sdd.conf, hold no uncommitted change to a
    tracked record, sit on main, and have main equal to their remote's (ls-remote only; a failure raises)."""
    problems = []
    tracked = set(_rec(root, gd, "ls-files").splitlines())
    problems += [f"{name} is not tracked by the records" for name in (DEFERRED, HOST_USE_LOG, CONF)
                 if name not in tracked]
    dirty = _rec(root, gd, "status", "--porcelain", "--untracked-files=no").splitlines()
    problems += [f"records have an uncommitted change: {line.rstrip()}" for line in dirty if line.strip()]
    branch = _rec(root, gd, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main":
        problems.append(f"records are on {branch}, not main")
    local = _rec(root, gd, "rev-parse", "main").strip()
    remote = _rec(root, gd, "ls-remote", "origin", "refs/heads/main").split()
    remote_sha = remote[0] if remote else "nothing"
    if local != remote_sha:
        problems.append(f"records main is {local}, its remote has {remote_sha}: commit and push the records "
                        "(git rec push)")
    return problems


def _guard_inputs(root: Path, gd: Path | None = None) -> tuple[dict[str, str], set[str]]:
    """The guarded texts (PLAN.md, DEFERRED.md, tracked ledgers) and the set of tracked paths; with the
    records' git dir, the paths the records track count and name the guarded files."""
    public = {p for p in _git(root, "ls-files").splitlines() if p}
    if gd is None:
        tracked = names_from = public
    else:
        names_from = {p for p in _rec(root, gd, "ls-files").splitlines() if p}
        tracked = public | names_from
    names = [n for n in GUARDED if n in names_from]
    names += sorted(p for p in names_from if re.fullmatch(r"\.superpowers/sdd/[^/]+/progress\.md", p))
    return {n: (root / n).read_text() for n in names if (root / n).is_file()}, tracked


def records_gate_problems(root: Path, home: str) -> list[str]:
    """What the records must show before a patch: configured and usable, pushed, every host row closed, every
    RC1 row closed, a dated use log, and the use log not tracked by the public repo. The publish step runs it
    again."""
    gd = records_git_dir(root, home)
    if not isinstance(gd, Path):
        reason = gd or f"no LEDGER_GIT_DIR in {CONF}"
        return [f"no records: {reason}; the patch gate runs in the clone that holds the records"]
    register = _read(root / DEFERRED)
    problems = records_problems(root, gd)
    if register is None:
        problems.append(f"{DEFERRED} not found")
    problems += host_rows_problems(register or "")
    problems += [f"open rc1 row {r}" for r in rc1_open_rows(register or "")]
    problems += use_log_problems(_read(root / HOST_USE_LOG), HOST_USE_LOG)
    if _git(root, "ls-files", "--", HOST_USE_LOG).strip():
        problems.append(f"{HOST_USE_LOG} is tracked by the public repo: host details stay private")
    return problems


def _check(target: Target, dry_run: bool, run: Callable[[list[str], Path], int], env: Mapping[str, str],
           root: Path) -> int:
    """Preconditions, then the chain, then the printed commands."""
    problems = [f"working tree is not clean: {ln}" for ln in _git(root, "status", "--porcelain").splitlines()]
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main" and not dry_run:
        problems.append(f"HEAD is on {branch!r}, not main (--dry-run relaxes this)")
    problems += version_problems(_read(root / "backend" / "pyproject.toml") or "", target)
    problems += notes_problems(_read(root / target.notes), target)
    home = env.get("HOME", "")
    gd = records_git_dir(root, home) if target.name == "patch" else None
    texts, tracked = _guard_inputs(root, gd if isinstance(gd, Path) else None)
    problems += guard_problems(texts, tracked)
    if target.name == "patch":
        problems += records_gate_problems(root, home)
        head = _git(root, "rev-parse", "HEAD").strip()
        if not dry_run:
            main_ref = _git(root, "ls-remote", "origin", "refs/heads/main").split()
            main_sha = main_ref[0] if main_ref else "nothing"
            if head != main_sha:
                problems.append(f"HEAD {head} is not GitHub's main ({main_sha}): merge the release commit "
                                "through a PR and pull")
        if _git(root, "tag", "--list", target.tag).strip():
            problems.append(f"tag {target.tag} already exists locally")
        if _git(root, "ls-remote", "origin", f"refs/tags/{target.tag}").strip():
            problems.append(f"tag {target.tag} already exists on origin")
        if not _git(root, "ls-files", "--", target.notes).strip():
            problems.append(f"{target.notes} is not tracked by the public repo")
    if target.name == "final":
        register = _read(root / DEFERRED)
        if register is None:          # a public clone: without the register, no open row would read as none
            problems.append(f"{DEFERRED} not found: the final gate runs in the private repo")
        problems += [f"open rc1 row {r}" for r in rc1_open_rows(register or "")]
        problems += use_log_problems(_read(root / USE_LOG))
    if problems:
        print("\n".join(problems))
        print(f"release {target.name}: {len(problems)} precondition problem(s); no step was run")
        return 1
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M%SZ")
    log = root / "dist" / f"release-{target.name}-{stamp}.log"
    skip_real = env.get("RELEASE_REAL") == "skip"
    chain = steps(target)
    for i, argv in enumerate(chain, 1):
        if skip_real and i == len(chain):
            print(f"NOT RELEASABLE: {' '.join(argv)} was skipped (RELEASE_REAL=skip); log {log}")
            return SKIPPED_EXIT
        print(f"step {i}/{len(chain)}: {' '.join(argv)}", flush=True)
        if run(argv, log) != 0:
            print(f"FAIL {' '.join(argv)} (log {log})")
            return 1
    wheel = root / "dist" / wheel_name(target.version)
    if not wheel.is_file():
        print(f"FAIL: dist/{wheel.name} not found after the chain (log {log})")
        return 1
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    print(f"release {target.name}: every step passed (log {log})")
    sha = _git(root, "rev-parse", "HEAD").strip()
    if not dry_run:
        record = {"gate": "check", "sha": sha, "tag": target.tag, "wheel": wheel.name, "sha256": digest}
        _passed(root, target).write_text(json.dumps(record, indent=1) + "\n")
    if target.name == "final":     # v1.0.0 is tagged and released from the public clone, not from here
        print(f"Gate 2 passed at {sha}; nothing is tagged or released from this repo.\n"
              "v1.0.0 is published by F1 (docs/superpowers/plans/2026-10-26-p12-public-split.md):\n"
              "export this commit to the public clone, then run release.py build final and\n"
              "release.py publish final there.")
        print(release_commands(target, sha, wheel, digest)[-1])
        return 0
    if target.name == "patch":
        if dry_run:
            print(f"DRY RUN: gate passed at {sha} for {target.tag}; nothing recorded, so "
                  "make release-publish would refuse")
        else:
            print(f"Gate passed at {sha} for {target.tag}. Run make release-publish: it signs the tag and "
                  "stops at a draft for you to look at; make release-publish PUBLISH_DRAFT=1 publishes it.")
        print(release_commands(target, sha, wheel, digest)[-1])
        return 0
    print("DRY RUN (tag from main after the merge):" if dry_run else
          f"Run these yourself, none is run here ({_publish_make(target)} runs them, and resumes after a "
          "failure):")
    print("\n".join(release_commands(target, sha, wheel, digest)))
    return 0


def _build(target: Target, run: Callable[[list[str], Path], int], root: Path) -> int:
    """`build final`: in the public clone, the wheel and the install smoke, then the record publish reads."""
    if target.name != "final":
        print(f"release build is for final (v1.0.0's wheel in the public clone); {target.name}'s gate is "
              "check")
        return 1
    problems = [f"working tree is not clean: {ln}" for ln in _git(root, "status", "--porcelain").splitlines()]
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main":
        problems.append(f"HEAD is on {branch!r}, not main")
    problems += version_problems(_read(root / "backend" / "pyproject.toml") or "", target)
    if problems:
        print("\n".join(problems))
        print(f"release build: {len(problems)} precondition problem(s); no step was run")
        return 1
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M%SZ")
    log = root / "dist" / f"release-build-{target.name}-{stamp}.log"
    for argv in (["make", "wheel"], ["make", "install-smoke"]):
        print(f"build: {' '.join(argv)}", flush=True)
        if run(argv, log) != 0:
            print(f"FAIL {' '.join(argv)} (log {log})")
            return 1
    wheel = root / "dist" / wheel_name(target.version)
    if not wheel.is_file():
        print(f"FAIL: dist/{wheel.name} not found after make wheel (log {log})")
        return 1
    record = {"gate": "build", "sha": _git(root, "rev-parse", "HEAD").strip(), "tag": target.tag,
              "wheel": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    _passed(root, target).write_text(json.dumps(record, indent=1) + "\n")
    print(f"release build: {wheel.name} built and smoke-tested (log {log}); next: {_publish_make(target)}")
    return 0


# publish: the person's own command (see the module docstring)

Sh = Callable[[list[str]], tuple[int, str]]
TRANSIENT = re.compile(r"\b5\d\d\b|Internal Server Error|Bad Gateway|Service Unavailable|"
                       r"Gateway Time-?out|remote end hung up|Connection reset|Connection refused|timed out|"
                       r"TLS handshake", re.I)
BACKOFF = (5, 15, 45)
ATTEMPTS = len(BACKOFF) + 1


class PublishError(RuntimeError):
    """A publish step cannot go on; the message says why and what is already done stays done."""


def _passed(root: Path, target: Target) -> Path:
    """Where `check` records its pass for `publish`."""
    return root / "dist" / f"release-{target.name}-passed.json"


def _publish_make(target: Target) -> str:
    """The make target that runs publish for this target."""
    if target.name == "final":     # make release-publish now runs publish patch
        return "uv run --project backend python -I -B scripts/release.py publish final"
    return "make release-publish" if target.name == "patch" else "make release-rc-publish"


def run_captured(root: Path) -> Sh:
    """The real `sh`: runs argv in root and returns (exit code, stdout and stderr together)."""
    def sh(argv: list[str]) -> tuple[int, str]:
        """One command; a program that cannot start is exit 127 with the reason, like a shell."""
        try:
            r = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
        except OSError as e:
            return 127, f"{argv[0]} could not run: {e}"
        return r.returncode, r.stdout + r.stderr
    return sh


class Publisher:
    """One publish run: the target, the recorded pass, and the injected command runner and sleep."""

    def __init__(self, target: Target, root: Path, sh: Sh, sleep: Callable[[float], None],
                 env: Mapping[str, str], publish_draft: bool) -> None:
        """Nothing runs until run(). publish_draft: publish the reviewed draft (final and patch)."""
        self.target, self.root, self.sh, self.sleep = target, root, sh, sleep
        self.env, self.publish_draft = env, publish_draft
        self.final = target.name == "final"
        self.patch = target.name == "patch"
        self.public = self.final or self.patch   # the tag is signed and the release stops at a draft

    def backoff(self, what: str, i: int, rc: int, out: str) -> None:
        """After failure i (0-based) of what: wait before the next try, or PublishError when the error is
        not a transient GitHub or network one, or the tries are used up."""
        if not TRANSIENT.search(out) or i == ATTEMPTS - 1:
            raise PublishError(f"{what} failed (exit {rc}): {out.strip()}")
        first = next((ln for ln in out.splitlines() if TRANSIENT.search(ln)), out.strip())
        print(f"{what}: GitHub error, retrying in {BACKOFF[i]} s ({i + 2}/{ATTEMPTS}): {first.strip()}")
        self.sleep(BACKOFF[i])

    def call(self, what: str, argv: list[str]) -> tuple[int, str]:
        """A read: argv, retried with backoff while it fails with a transient error; the last result."""
        for i in range(ATTEMPTS):
            rc, out = self.sh(argv)
            if rc == 0 or not TRANSIENT.search(out) or i == ATTEMPTS - 1:
                return rc, out
            self.backoff(what, i, rc, out)
        raise AssertionError("unreachable")

    def must(self, what: str, argv: list[str]) -> str:
        """call(), and its output; PublishError when it still fails."""
        rc, out = self.call(what, argv)
        if rc != 0:
            raise PublishError(f"{what} failed (exit {rc}): {out.strip()}")
        return out

    def write(self, what: str, argv: list[str], done: Callable[[], bool]) -> None:
        """A change on GitHub. After a failure done() is asked before anything else, since a request GitHub
        answered with an error may still have been carried out; only then is a transient error retried."""
        for i in range(ATTEMPTS):
            rc, out = self.sh(argv)
            if rc == 0:
                if not done():
                    raise PublishError(f"{what} reported success, but GitHub does not show it")
                return
            if done():
                print(f"{what}: GitHub reported an error, but it is done")
                return
            self.backoff(what, i, rc, out)

    def remote(self, ref: str) -> str | None:
        """The object name GitHub has for ref, or None."""
        out = self.must(f"reading {ref} on GitHub", ["git", "ls-remote", "origin", ref])
        names = [ln.split("\t")[0] for ln in out.splitlines() if ln.endswith("\t" + ref)]
        return names[0] if names else None

    def tag_object(self, ref: str, sha: str) -> str | None:
        """The local tag object of ref when it points at sha, else None."""
        rc, _ = self.sh(["git", "rev-parse", "-q", "--verify", ref])
        if rc != 0 or _git(self.root, "rev-parse", f"{ref}^{{commit}}").strip() != sha:
            return None
        return _git(self.root, "rev-parse", ref).strip()

    def view(self) -> dict | None:
        """The GitHub release of the tag (isDraft, isPrerelease, assets), or None when there is none."""
        rc, out = self.call("reading the release", ["gh", "release", "view", self.target.tag, "--json",
                                                   "isDraft,isPrerelease,assets"])
        if rc != 0 and "release not found" in out:
            return None
        if rc != 0:
            raise PublishError(f"reading the release failed (exit {rc}): {out.strip()}")
        return json.loads(out)

    def record(self) -> dict:
        """The pass `check` recorded, after checking it matches HEAD, a clean main and the wheel on disk."""
        t, root = self.target, self.root
        again = "make release-build" if self.final else "make release" if self.patch else "make release-rc"
        if self.final and ((root / "PLAN.md").is_file() or (root / DEFERRED).is_file()):
            raise PublishError("publish final runs in the public clone (F1); this tree holds the private "
                               "records")
        text = _read(_passed(root, t))
        if text is None:
            raise PublishError(f"no passing `{'build' if self.final else 'check'} {t.name}` recorded "
                               f"({_passed(root, t).relative_to(root)}); run {again} first")
        rec = json.loads(text)
        want = "build" if self.final else "check"
        if rec.get("gate") != want:
            raise PublishError(f"the pass was recorded by {rec.get('gate')}, not by {want}; run {again}")
        dirty = _git(root, "status", "--porcelain").splitlines()
        problems = [f"working tree is not clean: {ln}" for ln in dirty]
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch != "main":
            problems.append(f"HEAD is on {branch!r}, not main")
        head = _git(root, "rev-parse", "HEAD").strip()
        if rec.get("sha") != head or rec.get("tag") != t.tag:
            problems.append(f"the passing check was for {rec.get('tag')} at {rec.get('sha')}, not {t.tag} at "
                            f"HEAD {head}; run the check again")
        wheel = root / "dist" / rec.get("wheel", "")
        if not wheel.is_file() or hashlib.sha256(wheel.read_bytes()).hexdigest() != rec.get("sha256"):
            problems.append(f"dist/{rec.get('wheel')} is missing or not the wheel the check built; run the "
                            "check again")
        if problems:
            raise PublishError("\n".join(problems))
        return rec

    def run(self) -> str:
        """Every step in order, each skipped when GitHub already has it; "draft" when the final or patch
        release waits for the person's review, else "released"."""
        t, rec = self.target, self.record()
        sha, wheel = rec["sha"], f"dist/{rec['wheel']}"
        main_ref = "refs/heads/main"
        if self.patch:   # the records may have changed since the check; nothing is written until they hold
            found = records_gate_problems(self.root, self.env.get("HOME", ""))
            if found:
                raise PublishError("\n".join(found) + "\nnothing was tagged or released")
        there_main = self.remote(main_ref)
        if there_main == sha:
            print(f"main: GitHub has {sha[:12]}")
        elif self.patch:   # a patch never pushes main: it reaches main through a PR
            has = there_main or "no main"
            tag_there = self.remote(f"refs/tags/{t.tag}")
            if tag_there is None or tag_there != self.tag_object(f"refs/tags/{t.tag}", sha):
                raise PublishError(f"main moved since the check (GitHub has {has}, the check was for "
                                   f"{sha}); rerun make release")
            # an earlier run pushed the tag, which pins the commit: a later main changes nothing that ships
            print(f"main: GitHub has {has[:12]}, moved since the check; the tag {t.tag} already pins "
                  f"{sha[:12]}")
        else:
            self.write("pushing main", ["git", "push", "origin", f"{sha}:{main_ref}"],
                       lambda: self.remote(main_ref) == sha)
            print(f"main: pushed {sha[:12]}")
        ref = f"refs/tags/{t.tag}"
        rc, _ = self.sh(["git", "rev-parse", "-q", "--verify", ref])
        if rc == 0:
            on = _git(self.root, "rev-parse", f"{ref}^{{commit}}").strip()
            if on != sha:
                raise PublishError(f"tag {t.tag} points at {on}, not {sha}; it is never moved here")
            print(f"tag: {t.tag} exists on {sha[:12]}")
        else:
            argv = ["git", "tag", "-s" if self.public else "-a", t.tag, "-m", f"trex-web-gui {t.version}",
                    sha]
            rc, out = self.sh(argv)
            if rc != 0:
                raise PublishError(f"making tag {t.tag} failed (exit {rc}): {out.strip()}")
            print(f"tag: made {t.tag} on {sha[:12]}{', signed' if self.public else ''}")
        if self.public:
            signers = f"{self.env.get('HOME', '~')}/.config/git/allowed_signers"
            rc, out = self.sh(["git", "-c", f"gpg.ssh.allowedSignersFile={signers}", "verify-tag", t.tag])
            if rc != 0:
                raise PublishError(f"the signature of tag {t.tag} does not verify: {out.strip()}")
            print(f"tag: {t.tag}'s signature verifies")
        tag_obj = _git(self.root, "rev-parse", ref).strip()
        there = self.remote(ref)
        if there is not None and there != tag_obj:
            raise PublishError(f"GitHub's tag {t.tag} is {there}, not this tag ({tag_obj}); it is never "
                               "replaced")
        if there is None:
            self.write("pushing the tag", ["git", "push", "origin", ref], lambda: self.remote(ref) == tag_obj)
            print(f"tag: pushed {t.tag}")
        else:
            print(f"tag: GitHub has {t.tag}")
        if carries_install_sh(t):
            script = self.root / "dist" / INSTALL_SH
            script.write_bytes(install_script(t, rec["sha256"]).encode())
            script.chmod(0o644)
        state = self.release(rec, wheel)
        self.verify(rec)
        return state

    def release(self, rec: dict, wheel: str) -> str:
        """Create the release, or finish one a failure left as a draft or without the wheel: each round reads
        the release and takes the one step it lacks, so a failed step is never repeated blindly."""
        t, fails = self.target, 0
        files = {rec["wheel"]: wheel} | ({INSTALL_SH: f"dist/{INSTALL_SH}"} if carries_install_sh(t) else {})
        for _ in range(3 * ATTEMPTS):
            rel = self.view()
            if rel is not None and rel["isPrerelease"] != t.prerelease:
                kind = "a pre-release" if rel["isPrerelease"] else "not a pre-release"
                raise PublishError(f"the release {t.tag} is {kind}; fix it on GitHub, it is never changed "
                                   "here")
            have = {a["name"] for a in rel["assets"]} if rel is not None else set()
            missing = [path for name, path in files.items() if name not in have]
            if rel is not None and not rel["isDraft"] and INSTALL_SH in files and INSTALL_SH not in have:
                raise PublishError(f"the release {t.tag} is published without {INSTALL_SH}; it cannot be "
                                   "added")
            if rel is None:
                argv = ["gh", "release", "create", t.tag, *files.values(), "--title",
                        f"trex-web-gui {t.version}", "--notes-file", t.notes]
                argv += (["--prerelease"] if t.prerelease else []) + ["--verify-tag"]
                argv += ["--draft"] if self.public else []
                what = "creating the release"
            elif missing:
                argv = ["gh", "release", "upload", t.tag, *missing, "--clobber"]
                what = "uploading " + " and ".join("the wheel" if m == wheel else INSTALL_SH for m in missing)
            elif rel["isDraft"] and self.public and not self.publish_draft:
                print(f"release: {t.tag} is a draft with {' and '.join(files)}")
                return "draft"
            elif rel["isDraft"]:
                argv, what = ["gh", "release", "edit", t.tag, "--draft=false"], "publishing the draft release"
            else:
                print(f"release: {t.tag} is published with {' and '.join(files)}")
                return "released"
            print(f"release: {what}")
            rc, out = self.sh(argv)
            if rc != 0:
                self.backoff(what, fails, rc, out)
                fails += 1
        raise PublishError(f"the release {t.tag} is still not complete after {3 * ATTEMPTS} rounds")

    def verify(self, rec: dict) -> None:
        """Download the released wheel and compare its sha256 with the one the check built; for a patch also
        download install.sh and compare its bytes with the generated text."""
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M%SZ")
        where = self.root / "dist" / f"publish-check-{stamp}"
        names = [rec["wheel"]] + ([INSTALL_SH] if carries_install_sh(self.target) else [])
        script = None
        try:
            patterns = [x for n in names for x in ("--pattern", n)]
            self.must("downloading the released wheel", ["gh", "release", "download", self.target.tag,
                                                         *patterns, "--dir", str(where), "--clobber"])
            got = hashlib.sha256((where / rec["wheel"]).read_bytes()).hexdigest()
            if INSTALL_SH in names:
                script = (where / INSTALL_SH).read_bytes()
        finally:
            for f in where.glob("*"):
                f.unlink()
            if where.is_dir():
                where.rmdir()
        if got != rec["sha256"]:
            raise PublishError(f"the released wheel's sha256 is {got}, not the built {rec['sha256']}")
        if script is not None and script != install_script(self.target, rec["sha256"]).encode():
            raise PublishError("the released install.sh differs from the generated one")
        print(f"verify: the released wheel's sha256 is {got}, as built")
        if script is not None:
            print(f"verify: the released {INSTALL_SH} is the generated one")


def _publish(target: Target, root: Path, sh: Sh, sleep: Callable[[float], None], env: Mapping[str, str],
             publish_draft: bool) -> int:
    """Run publish and say how it ended; what is done stays done, so a rerun resumes."""
    try:
        state = Publisher(target, root, sh, sleep, env, publish_draft).run()
    except PublishError as e:
        print(f"{e}\npublish {target.name}: stopped; what is done stays done, rerun "
              f"`{_publish_make(target)}` after fixing the cause")
        return 1
    if state == "draft":
        flag = "PUBLISH_DRAFT=1" if target.name == "patch" else "--publish-draft"
        print(f"publish {target.name}: {target.tag} is a draft. Check it on the Releases page, then run "
              f"`{_publish_make(target)} {flag}` to publish it.")
        return 0
    wheel, digest = wheel_name(target.version), json.loads(_read(_passed(root, target)) or "{}").get("sha256")
    pinned = (f"curl -fLO {RELEASES}/{target.tag}/{wheel}\n"
              f"echo '{digest}  {wheel}' | sha256sum -c -\n"
              f"uv tool install --python 3.12 ./{wheel}")
    if carries_install_sh(target):
        latest = f"curl -fsSL {RELEASES.removesuffix('/download')}/latest/download/{INSTALL_SH} | sh"
        pinned = f"{latest}\nor, pinned to this release:\n{pinned}"
    print(f"publish {target.name}: {target.tag} is released. On the host:\n{pinned}")
    return 0


def main(argv: list[str] | None = None, run: Callable[[list[str], Path], int] = run_logged,
         env: Mapping[str, str] = os.environ, root: Path = ROOT, sh: Sh | None = None,
         sleep: Callable[[float], None] = time.sleep) -> int:
    """Entry point: `guard`, `check <target> [--dry-run]` or `publish <target>` (rc, rc<N>, final, patch)."""
    ap = argparse.ArgumentParser(prog="release.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("guard", help="refuse /var/tmp citations without a committed copy")
    chk = sub.add_parser("check", help="preconditions, the gate chain, then the printed commands")
    chk.add_argument("target", help="rc (the candidate pyproject names), rc<N>, final or patch")
    chk.add_argument("--dry-run", action="store_true", help="allow a branch other than main")
    pub = sub.add_parser("publish", help="tag, push, release and verify what a passing check recorded")
    pub.add_argument("target", help="rc (the candidate pyproject names), rc<N>, final or patch")
    pub.add_argument("--publish-draft", action="store_true",
                     help="final, patch: publish the reviewed draft release")
    bld = sub.add_parser("build", help="final, in the public clone: make wheel and install-smoke, then the "
                         "record")
    bld.add_argument("target", help="final")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "guard":
            gd = records_git_dir(root, env.get("HOME", ""))
            if isinstance(gd, str):   # a set but unusable key must not fall back to a scan that reads nothing
                print(f"records: {gd}")
                return 1
            if gd is None and not (root / "PLAN.md").is_file():   # a public clone: nothing would be scanned
                print("PLAN.md not found: the guard scans the records, which are in the private repo")
                return 1
            texts, tracked = _guard_inputs(root, gd)
            found = guard_problems(texts, tracked)
            print("\n".join(found) if found else "guard: ok")
            return 1 if found else 0
        target = resolve_target(args.target, _read(root / "backend" / "pyproject.toml") or "")
        if isinstance(target, str):
            print(f"{target}\nrelease: no target; no step was run")
            return 1
        if args.cmd == "publish":
            return _publish(target, root, sh or run_captured(root), sleep, env, args.publish_draft)
        if args.cmd == "build":
            return _build(target, run, root)
        return _check(target, args.dry_run, run, env, root)
    except GitError as e:
        print(f"{e}\nrelease {getattr(args, 'target', 'guard')}: git failed; refusing (no step was run)")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
