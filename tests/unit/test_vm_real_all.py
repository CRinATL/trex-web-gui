"""vm/real_all.sh: runs every guest in turn with the per-guest env cleared, never stops early, and refuses a
recording. The real.sh it runs is a stub (the TREX_REAL_SH seam); no guest is touched: the env is built from
scratch with stubbed tools on PATH and TREX_VM_BASE inside tmp_path."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
STUBBED = ("systemd-run", "qemu-img", "qemu-system-x86_64", "ssh", "ssh-keygen", "xorriso", "curl", "gpgv",
           "socat", "uv")
CLEARED = ("TREX_VM_DIR", "TREX_VM_DL", "TREX_VM_SHARED", "TREX_VM_CODENAME", "TREX_VM_DIR_WARNED",
           "TREX_VM_SNAPSHOT")
STUB_REAL = ('#!/bin/bash\n'
             'echo "ran $TREX_VM_GUEST ' + " ".join("${" + v + ":-unset}" for v in CLEARED) +
             ' stack=${TREX_VM_STACK:-unset} base=${TREX_VM_BASE:-unset}"\n'
             '[ "$TREX_VM_GUEST" = resolute ] && exit 3\nexit 0\n')


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    """Makes stub tools that append `name args` to calls.log and exit 1."""
    d = tmp_path / "stubs"
    d.mkdir()
    for tool in STUBBED:
        (d / tool).write_text(f'#!/bin/bash\necho "{tool} $*" >> "{tmp_path}/calls.log"\nexit 1\n')
        (d / tool).chmod(0o755)
    return d


def guest_names() -> list[str]:
    """The guest names from vm/guests.sh --list, in table order."""
    out = subprocess.run([str(ROOT / "vm" / "guests.sh"), "--list"], cwd=ROOT, env={"PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, check=True, timeout=30)
    return out.stdout.split()


def run_all(tmp_path: Path, stubs: Path, *args: str, body: str = STUB_REAL,
            **extra: str) -> subprocess.CompletedProcess[str]:
    """Runs vm/real_all.sh in a from-scratch env whose real.sh is a stub script with the given body."""
    real = tmp_path / "real_stub.sh"
    real.write_text(body)
    real.chmod(0o755)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "TREX_VM_BASE": str(tmp_path / "base"),
           "TREX_REAL_SH": str(real), **extra}
    for tool in STUBBED:
        assert shutil.which(tool, path=env["PATH"]) == str(stubs / tool)
    r = subprocess.run([str(ROOT / "vm" / "real_all.sh"), *args], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=60)
    assert not (tmp_path / "calls.log").exists(), "a stubbed tool was called"
    return r


def test_real_all_runs_every_guest_and_clears_the_guest_env(tmp_path: Path, stubs: Path) -> None:
    """Each guest runs in list order with all six per-guest vars cleared; the stack and base pass through."""
    caller = {v: f"/{v.lower()}" for v in CLEARED}
    r = run_all(tmp_path, stubs, TREX_VM_STACK="linux_based", **caller)
    base = str(tmp_path / "base")
    assert r.returncode == 1, r.stderr
    assert f"check-real-all: ignoring {' '.join(CLEARED)} for per-guest runs" in r.stdout
    lines = r.stdout.splitlines()
    pos = 0
    for g in guest_names():
        rc = 3 if g == "resolute" else 0
        for want in (f"ran {g} {' '.join(['unset'] * 6)} stack=linux_based base={base}",
                     f"check-real-all: {g} rc={rc}"):
            pos = lines.index(want, pos) + 1


def test_real_all_passes_when_all_pass(tmp_path: Path, stubs: Path) -> None:
    """With every guest passing, the exit code is 0 and nothing is ignored."""
    r = run_all(tmp_path, stubs, body="#!/bin/bash\nexit 0\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ignoring" not in r.stdout
    for g in guest_names():
        assert f"check-real-all: {g} rc=0" in r.stdout


def test_real_all_refuses_a_recording(tmp_path: Path, stubs: Path) -> None:
    """TREX_VM_RECORD set gives rc 2 and the stub never runs."""
    r = run_all(tmp_path, stubs, TREX_VM_RECORD="p2")
    assert r.returncode == 2
    assert "check-real-all runs the tests, not a recorder" in r.stderr + r.stdout
    assert "ran " not in r.stdout


def test_real_all_rejects_arguments(tmp_path: Path, stubs: Path) -> None:
    """Any argument gives the usage line and rc 2, and no guest runs."""
    r = run_all(tmp_path, stubs, "resolute")
    assert r.returncode == 2
    assert "usage: real_all.sh (no arguments)" in r.stderr
    assert "ran " not in r.stdout
