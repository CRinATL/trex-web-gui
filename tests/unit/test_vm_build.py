"""vm/build.sh refusals: every one exits 2 before any file operation or tool, and --rebuild removes only
its listed files. No test reaches a real guest: every subprocess env is built from scratch with stubbed
tools (each logs to calls.log and exits 1), TREX_VM_BASE points into tmp_path, and jammy's overlay is only
stat-ed."""
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
JAMMY_OVERLAY = Path("/var/tmp/trex-vm/trex.qcow2")
STUBBED = ("systemd-run", "qemu-img", "qemu-system-x86_64", "ssh", "ssh-keygen", "xorriso", "curl", "gpgv",
           "socat", "uv")
RESOLUTE_PORTS = (2322, 4600, 4601, 4610, 8190, 8865)


def taken(port: int) -> bool:
    """True when something accepts a TCP connection on 127.0.0.1:port."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(autouse=True)
def _ports_free() -> None:
    """Skips every test here while any of resolute's six ports is in use (check 5 would refuse first)."""
    for port in RESOLUTE_PORTS:
        if taken(port):
            pytest.skip(f"127.0.0.1:{port} is in use")


@pytest.fixture(autouse=True)
def _jammy_overlay_untouched():
    """Fails a test that changes the inode or mtime of jammy's overlay (stat only, never opened)."""
    before = JAMMY_OVERLAY.stat() if JAMMY_OVERLAY.exists() else None
    yield
    if before is not None:
        after = JAMMY_OVERLAY.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    """Makes stub tools that append `name args` to calls.log and exit 1."""
    d = tmp_path / "stubs"
    d.mkdir()
    for tool in STUBBED:
        (d / tool).write_text(f'#!/bin/bash\necho "{tool} $*" >> "{tmp_path}/calls.log"\nexit 1\n')
        (d / tool).chmod(0o755)
    return d


def make_env(tmp_path: Path, stubs: Path, **extra: str) -> dict[str, str]:
    """Builds a scratch env from nothing (never os.environ); checks each stubbed tool resolves to its stub."""
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path),
           "TREX_VM_BASE": str(tmp_path / "base"), **extra}
    for tool in STUBBED:
        assert shutil.which(tool, path=env["PATH"]) == str(stubs / tool)
    return env


def build(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Runs vm/build.sh from the repo root; returns the completed process."""
    return subprocess.run([str(ROOT / "vm" / "build.sh"), *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=60)


def calls(tmp_path: Path) -> str:
    """The stub call log, empty when no stub ran."""
    p = tmp_path / "calls.log"
    return p.read_text() if p.exists() else ""


def keep(path: Path) -> Path:
    """Creates path (and its parents) holding b"keep"; returns it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"keep")
    return path


@pytest.fixture
def row_dir(tmp_path: Path) -> Path:
    """The resolute row dir under the scratch base (not created)."""
    return tmp_path / "base" / "trex-vm-resolute"


def spawn(tail: list[str]) -> subprocess.Popen[bytes]:
    """Starts a sleeping python whose argv ends with tail; returns it once /proc shows the full argv."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *tail])
    for _ in range(100):
        if not tail or tail[-1].encode() in Path(f"/proc/{p.pid}/cmdline").read_bytes():
            break
        time.sleep(0.02)
    return p


def test_build_needs_a_known_guest(tmp_path: Path, stubs: Path) -> None:
    """No guest, or one not in the table, is a usage error."""
    env = make_env(tmp_path, stubs)
    assert build(env).returncode == 2
    assert build(env, "bionic").returncode == 2
    assert not (tmp_path / "calls.log").exists()


def test_build_never_builds_jammy(tmp_path: Path, stubs: Path) -> None:
    """jammy is built by hand: refused, with or without --rebuild, and its overlay is untouched."""
    overlay = keep(tmp_path / "base" / "trex-vm" / "trex.qcow2")
    env = make_env(tmp_path, stubs)
    for args in (("jammy",), ("jammy", "--rebuild")):
        r = build(env, *args)
        assert r.returncode == 2 and "built by hand" in r.stderr
    assert overlay.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_build_refuses_an_existing_overlay(tmp_path: Path, stubs: Path, row_dir: Path) -> None:
    """An existing trex.qcow2 without --rebuild is refused and left alone."""
    overlay = keep(row_dir / "trex.qcow2")
    r = build(make_env(tmp_path, stubs), "resolute")
    assert r.returncode == 2 and "trex.qcow2" in r.stderr and "--rebuild" in r.stderr
    assert overlay.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


@pytest.mark.parametrize("var", ["TREX_VM_DIR", "TREX_VM_DL", "TREX_VM_SHARED"])
def test_build_refuses_a_hand_set_dir(tmp_path: Path, stubs: Path, row_dir: Path, var: str) -> None:
    """A dir variable set by hand and differing from the row's is refused even with --rebuild."""
    other = keep(tmp_path / "other" / "trex.qcow2")
    own = keep(row_dir / "trex.qcow2")
    r = build(make_env(tmp_path, stubs, **{var: str(other.parent)}), "resolute", "--rebuild")
    assert r.returncode == 2 and var in r.stderr and "unset" in r.stderr
    assert other.read_bytes() == b"keep" and own.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_build_refuses_an_added_protected_dir_even_with_rebuild(
        tmp_path: Path, stubs: Path, row_dir: Path) -> None:
    """TREX_VM_PROTECT_ALSO naming the row dir, or a symlink to it, refuses --rebuild."""
    overlay = keep(row_dir / "trex.qcow2")
    link = tmp_path / "link"
    link.symlink_to(row_dir)
    for protect in (str(row_dir), str(link)):
        r = build(make_env(tmp_path, stubs, TREX_VM_PROTECT_ALSO=protect), "resolute", "--rebuild")
        assert r.returncode == 2 and "protected" in r.stderr
        assert overlay.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_hard_protected_dirs_cannot_be_removed(tmp_path: Path, stubs: Path) -> None:
    """--is-protected: jammy's dir and what lies under it are protected, string-prefix siblings are not."""
    def code(path: str, **extra: str) -> int:
        """Exit code of `build.sh --is-protected path`."""
        return build(make_env(tmp_path, stubs, **extra), "--is-protected", path).returncode

    for path in ("/var/tmp/trex-vm", "/var/tmp/trex-vm/", "/var/tmp/trex-vm/dl"):
        assert code(path) == 0
        assert code(path, TREX_VM_PROTECT_ALSO="") == 0
    siblings = ("/var/tmp/trex-vm-resolute", "/var/tmp/trex-vm-noble", "/var/tmp/trex-vm-dl")
    for path in (*siblings, str(tmp_path / "x")):
        assert code(path) == 1
    assert code(str(tmp_path / "x"), TREX_VM_PROTECT_ALSO=str(tmp_path / "x")) == 0
    assert not (tmp_path / "calls.log").exists()


@pytest.mark.parametrize("how", ["pidfile", "cmdline"])
def test_build_refuses_while_the_dir_is_in_use(tmp_path: Path, stubs: Path, row_dir: Path, how: str) -> None:
    """A live process in qemu.pid, or one whose argv holds this dir's drive, refuses the build."""
    row_dir.mkdir(parents=True)
    p = spawn([] if how == "pidfile" else [f"file={row_dir}/trex.qcow2,if=virtio"])
    try:
        if how == "pidfile":
            (row_dir / "qemu.pid").write_text(f"{p.pid}\n")
        r = build(make_env(tmp_path, stubs), "resolute")
    finally:
        p.kill()
        p.wait()
    assert r.returncode == 2 and "running" in r.stderr
    assert not (tmp_path / "calls.log").exists()


def test_rebuild_removes_only_the_listed_files(tmp_path: Path, stubs: Path, row_dir: Path) -> None:
    """--rebuild removes the overlay, seed and known_hosts, keeps the key pair, and runs no tool first."""
    for name in ("trex.qcow2", "seed.iso", "known_hosts", "id_ed25519", "id_ed25519.pub", "keep.txt"):
        keep(row_dir / name)
    r = build(make_env(tmp_path, stubs), "resolute", "--rebuild")
    assert r.returncode == 2 and "TLS bundle" in r.stderr
    for gone in ("trex.qcow2", "seed.iso", "known_hosts"):
        assert not (row_dir / gone).exists()
    for kept in ("id_ed25519", "id_ed25519.pub", "keep.txt"):
        assert (row_dir / kept).read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_build_refuses_a_symlinked_row_dir(tmp_path: Path, stubs: Path, row_dir: Path) -> None:
    """A row dir that is a symlink (to a dir holding an overlay) is refused even with --rebuild."""
    target = keep(tmp_path / "target" / "trex.qcow2")
    row_dir.parent.mkdir(parents=True)
    row_dir.symlink_to(target.parent)
    r = build(make_env(tmp_path, stubs), "resolute", "--rebuild")
    assert r.returncode == 2 and "symlink" in r.stderr
    assert target.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


@pytest.mark.parametrize("which", ["row", "shared"])
def test_build_refuses_a_group_writable_dir(tmp_path: Path, stubs: Path, row_dir: Path, which: str) -> None:
    """A group-writable row dir or shared download dir is refused even with --rebuild."""
    overlay = keep(row_dir / "trex.qcow2")
    shared = tmp_path / "base" / "trex-vm-dl"
    shared.mkdir()
    (row_dir if which == "row" else shared).chmod(0o770)
    r = build(make_env(tmp_path, stubs), "resolute", "--rebuild")
    assert r.returncode == 2 and "writable" in r.stderr
    assert overlay.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_build_refuses_a_symlinked_seed_dir(tmp_path: Path, stubs: Path, row_dir: Path) -> None:
    """--rebuild does not delete through a seed dir that links into another guest's seed."""
    victim = keep(tmp_path / "base" / "trex-vm" / "seed" / "user-data")
    row_dir.mkdir(parents=True)
    (row_dir / "seed").symlink_to(victim.parent)
    r = build(make_env(tmp_path, stubs), "resolute", "--rebuild")
    assert r.returncode == 2 and "symlink" in r.stderr
    assert victim.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_build_refuses_a_relative_base(tmp_path: Path, stubs: Path) -> None:
    """A relative TREX_VM_BASE is refused before anything runs."""
    r = build(make_env(tmp_path, stubs, TREX_VM_BASE="rel/base"), "resolute")
    assert r.returncode == 2 and "absolute" in r.stderr
    assert not (tmp_path / "calls.log").exists()


def mkdir700(tmp_path: Path, stubs: Path, target: Path) -> subprocess.CompletedProcess[str]:
    """Sources vm/guests.sh in bash and runs trex_mkdir700 on target."""
    script = '. "$1/vm/guests.sh" && trex_mkdir700 "$2"'
    return subprocess.run(["bash", "-c", script, "x", str(ROOT), str(target)], env=make_env(tmp_path, stubs),
                          capture_output=True, text=True, timeout=30)


def test_mkdir700_makes_a_missing_path_private(tmp_path: Path, stubs: Path) -> None:
    """A missing path is created with every new component mode 700."""
    target = tmp_path / "a" / "b"
    assert mkdir700(tmp_path, stubs, target).returncode == 0
    assert (target.stat().st_mode & 0o777, target.parent.stat().st_mode & 0o777) == (0o700, 0o700)


def test_mkdir700_refuses_an_unsafe_existing_leaf(tmp_path: Path, stubs: Path) -> None:
    """A group-writable leaf is refused and keeps its mode; a symlinked leaf is refused."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o770)
    r = mkdir700(tmp_path, stubs, shared)
    assert r.returncode != 0 and "writable" in r.stderr and shared.stat().st_mode & 0o777 == 0o770
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    r = mkdir700(tmp_path, stubs, link)
    assert r.returncode != 0 and "symlink" in r.stderr


def test_build_refuses_an_unsafe_download_dir(tmp_path: Path, stubs: Path, row_dir: Path) -> None:
    """A group-writable image dir (TREX_VM_DL's row value) is refused with --rebuild."""
    overlay = keep(row_dir / "trex.qcow2")
    dl = tmp_path / "base" / "trex-vm-dl" / "resolute"
    dl.mkdir(parents=True)
    dl.chmod(0o770)
    r = build(make_env(tmp_path, stubs), "resolute", "--rebuild")
    assert r.returncode == 2 and "writable" in r.stderr and overlay.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_download_refuses_an_unsafe_dir_for_another_guest(tmp_path: Path, stubs: Path) -> None:
    """download.sh on a non-jammy guest exits 2 for a group-writable TREX_VM_DL, before any tool."""
    dl = tmp_path / "dl"
    dl.mkdir()
    dl.chmod(0o770)
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="resolute", TREX_VM_DL=str(dl))
    r = subprocess.run([str(ROOT / "vm" / "download.sh"), "--verify-only"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "writable" in r.stderr
    assert not (tmp_path / "calls.log").exists()


def run_download(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Runs vm/download.sh from the repo root; returns the completed process."""
    return subprocess.run([str(ROOT / "vm" / "download.sh"), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


def test_download_refuses_a_protected_dir_for_another_guest(tmp_path: Path, stubs: Path) -> None:
    """download.sh on a non-jammy guest exits 2 for a TREX_VM_DL that is protected, touching nothing."""
    dl = tmp_path / "dl"
    dl.mkdir(mode=0o700)
    kept = keep(dl / "ubuntu.img")
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="noble", TREX_VM_DL=str(dl), TREX_VM_PROTECT_ALSO=str(dl))
    r = run_download(env)
    assert r.returncode == 2 and f"FAILED: {dl} is a protected dir" in r.stderr
    assert sorted(p.name for p in dl.iterdir()) == ["ubuntu.img"] and kept.read_bytes() == b"keep"
    assert not (tmp_path / "calls.log").exists()


def test_verify_only_creates_nothing_for_another_guest(tmp_path: Path, stubs: Path) -> None:
    """download.sh --verify-only with a missing TREX_VM_DL exits 1 and creates no directory."""
    dl = tmp_path / "base" / "dl-missing"
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="noble", TREX_VM_DL=str(dl))
    r = run_download(env, "--verify-only")
    assert r.returncode == 1 and f"FAILED: nothing downloaded in {dl}" in r.stderr
    assert not (tmp_path / "base").exists() and not (tmp_path / "calls.log").exists()
