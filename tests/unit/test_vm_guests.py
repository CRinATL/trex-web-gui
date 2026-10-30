"""vm/guests.sh: guest selection, the stale-env refusals, trex_vm_running, and launch.sh/ssh.sh reading it.
No test reaches a real guest: every subprocess env is built from scratch with stubbed tools and
TREX_VM_BASE pointing into tmp_path."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
GUESTS = ROOT / "vm" / "guests.sh"
STUBBED = ("systemd-run", "qemu-img", "qemu-system-x86_64", "ssh", "ssh-keygen", "xorriso", "curl", "gpgv",
           "socat", "uv")
KEYS = ("TREX_VM_DIR", "TREX_VM_NAME", "TREX_VM_SSH_PORT", "TREX_REAL_SYNC_PORT", "TREX_REAL_ASYNC_PORT",
        "TREX_VM_GUI_PORT", "TREX_VM_DL", "TREX_VM_TLS", "TREX_VM_DL_TREX", "TREX_VM_HOSTNAME",
        "TREX_VM_INSTANCE_ID", "TREX_VM_EMU_PORT", "TREX_VM_DAEMON_PORT")


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    """Makes stub tools that log `name args` to calls.log and exit 1; systemd-run and ssh print argv."""
    d = tmp_path / "stubs"
    d.mkdir()
    for tool in STUBBED:
        body = f'echo "{tool} $*" >> "{tmp_path}/calls.log"\n'
        if tool in ("systemd-run", "ssh"):
            body = 'printf "%s\\n" "$@"\nexit 0\n'
        else:
            body += "exit 1\n"
        (d / tool).write_text("#!/bin/bash\n" + body)
        (d / tool).chmod(0o755)
    return d


def make_env(tmp_path: Path, stubs: Path, base: bool = True, **extra: str) -> dict[str, str]:
    """Builds a scratch env from nothing (never os.environ); checks each stubbed tool resolves to its stub."""
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)}
    if base:
        env["TREX_VM_BASE"] = str(tmp_path / "base")
    env.update(extra)
    for tool in STUBBED:
        assert shutil.which(tool, path=env["PATH"]) == str(stubs / tool)
    return env


def sourced(env: dict[str, str], *keys: str, tail: str = "") -> tuple[dict[str, str], int, str]:
    """Sources vm/guests.sh, prints KEY=value lines for keys; returns (values, rc, stderr)."""
    script = ". vm/guests.sh && " + "".join(f'printf "%s=%s\\n" {k} "${k}"; ' for k in keys) + "true; " + tail
    r = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return vals, r.returncode, r.stderr


def test_jammy_is_the_default(tmp_path: Path, stubs: Path) -> None:
    """No selection and an empty TREX_VM_GUEST both give jammy's historical paths and ports."""
    for extra in ({}, {"TREX_VM_GUEST": ""}):
        vals, rc, err = sourced(make_env(tmp_path, stubs, base=False, **extra), *KEYS)
        assert rc == 0 and err == ""
        assert vals["TREX_VM_DIR"] == "/var/tmp/trex-vm"
        assert vals["TREX_VM_NAME"] == "trex-vm"
        assert (vals["TREX_VM_SSH_PORT"], vals["TREX_REAL_SYNC_PORT"], vals["TREX_REAL_ASYNC_PORT"],
                vals["TREX_VM_GUI_PORT"]) == ("2222", "4501", "4500", "8765")
        assert vals["TREX_VM_DL"] == "/var/tmp/trex-vm/dl"
        assert vals["TREX_VM_TLS"] == "/var/tmp/trex-vm/tls/bundle.pem"


def test_resolute_row(tmp_path: Path, stubs: Path) -> None:
    """The resolute row has its own dir, name, shared download dirs and ports 100 above jammy's."""
    vals, rc, _ = sourced(make_env(tmp_path, stubs, base=False, TREX_VM_GUEST="resolute"), *KEYS)
    assert rc == 0
    assert vals == {
        "TREX_VM_DIR": "/var/tmp/trex-vm-resolute", "TREX_VM_NAME": "trex-vm-resolute",
        "TREX_VM_SSH_PORT": "2322", "TREX_REAL_SYNC_PORT": "4601", "TREX_REAL_ASYNC_PORT": "4600",
        "TREX_VM_GUI_PORT": "8865", "TREX_VM_DL": "/var/tmp/trex-vm-dl/resolute",
        "TREX_VM_TLS": "/var/tmp/trex-vm-dl/tls/bundle.pem", "TREX_VM_DL_TREX": "/var/tmp/trex-vm-dl",
        "TREX_VM_HOSTNAME": "trex-vm-resolute", "TREX_VM_INSTANCE_ID": "trex-vm-resolute-1",
        "TREX_VM_EMU_PORT": "4610", "TREX_VM_DAEMON_PORT": "8190"}


def test_noble_row(tmp_path: Path, stubs: Path) -> None:
    """The noble row has its own dir, name, shared download dirs and ports 200 above jammy's."""
    keys = (*KEYS, "TREX_VM_IMAGE", "TREX_VM_IMAGE_URL")
    vals, rc, _ = sourced(make_env(tmp_path, stubs, base=False, TREX_VM_GUEST="noble"), *keys)
    assert rc == 0
    assert vals == {
        "TREX_VM_DIR": "/var/tmp/trex-vm-noble", "TREX_VM_NAME": "trex-vm-noble",
        "TREX_VM_SSH_PORT": "2422", "TREX_REAL_SYNC_PORT": "4701", "TREX_REAL_ASYNC_PORT": "4700",
        "TREX_VM_GUI_PORT": "8965", "TREX_VM_DL": "/var/tmp/trex-vm-dl/noble",
        "TREX_VM_TLS": "/var/tmp/trex-vm-dl/tls/bundle.pem", "TREX_VM_DL_TREX": "/var/tmp/trex-vm-dl",
        "TREX_VM_HOSTNAME": "trex-vm-noble", "TREX_VM_INSTANCE_ID": "trex-vm-noble-1",
        "TREX_VM_EMU_PORT": "4710", "TREX_VM_DAEMON_PORT": "8290",
        "TREX_VM_IMAGE": "ubuntu-24.04-server-cloudimg-amd64.img",
        "TREX_VM_IMAGE_URL": "https://cloud-images.ubuntu.com/releases/noble/release"}


def test_base_redirects_every_row(tmp_path: Path, stubs: Path) -> None:
    """TREX_VM_BASE is the parent of the row dir and of the shared download dir."""
    vals, _, _ = sourced(make_env(tmp_path, stubs, TREX_VM_GUEST="resolute"), "TREX_VM_DIR", "TREX_VM_DL")
    assert vals == {"TREX_VM_DIR": f"{tmp_path}/base/trex-vm-resolute",
                    "TREX_VM_DL": f"{tmp_path}/base/trex-vm-dl/resolute"}


def test_unknown_guest_exits_2(tmp_path: Path, stubs: Path) -> None:
    """An unknown guest name is refused with rc 2 and a message listing the rows."""
    _, rc, err = sourced(make_env(tmp_path, stubs, TREX_VM_GUEST="bionic"))
    assert rc == 2 and "bionic" in err and "jammy" in err and "resolute" in err and "noble" in err


def test_list(tmp_path: Path, stubs: Path) -> None:
    """--list prints the row names in table order."""
    r = subprocess.run([str(GUESTS), "--list"], env=make_env(tmp_path, stubs), capture_output=True, text=True)
    assert r.stdout == "jammy\nresolute\nnoble\n"


def test_hand_set_dir_without_guest_is_silent(tmp_path: Path, stubs: Path) -> None:
    """A hand-set TREX_VM_DIR with no guest selected is honoured without a warning (byte-compat)."""
    vals, rc, err = sourced(make_env(tmp_path, stubs, TREX_VM_DIR=str(tmp_path)), "TREX_VM_DIR")
    assert rc == 0 and vals["TREX_VM_DIR"] == str(tmp_path) and err == ""


def test_hand_set_dir_with_guest_warns_once(tmp_path: Path, stubs: Path) -> None:
    """A guest plus a different hand-set dir warns once; TREX_VM_DIR_WARNED silences children."""
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="resolute", TREX_VM_DIR=str(tmp_path))
    _, _, err = sourced(env)
    assert len(err.splitlines()) == 1 and str(tmp_path) in err and f"{tmp_path}/base/trex-vm-resolute" in err
    # The warning marks the shell's environment, so a second source in the same run stays quiet.
    _, _, err = sourced(env, tail=". vm/guests.sh; bash -c '. vm/guests.sh'")
    assert len(err.splitlines()) == 1
    _, _, err = sourced({**env, "TREX_VM_DIR_WARNED": "1"})
    assert err == ""


def test_inherited_keys_are_recomputed(tmp_path: Path, stubs: Path) -> None:
    """Keys other than the dirs are always recomputed from the row, whatever the caller exported."""
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="resolute", TREX_REAL_SYNC_PORT="9", TREX_VM_NAME="x")
    vals, _, _ = sourced(env, "TREX_REAL_SYNC_PORT", "TREX_VM_NAME")
    assert vals == {"TREX_REAL_SYNC_PORT": "4601", "TREX_VM_NAME": "trex-vm-resolute"}


def test_env_prepared_for_another_guest_is_refused(tmp_path: Path, stubs: Path) -> None:
    """A TREX_VM_CODENAME left by another guest's eval is refused, not mixed in."""
    _, rc, err = sourced(make_env(tmp_path, stubs, TREX_VM_GUEST="resolute", TREX_VM_CODENAME="jammy"))
    assert rc == 2 and "prepared for jammy" in err


def test_executed_mode_prints_a_clean_row(tmp_path: Path, stubs: Path) -> None:
    """Executed, guests.sh ignores inherited per-guest keys and never prints TREX_VM_DIR_WARNED."""
    env = make_env(tmp_path, stubs, base=False, TREX_VM_GUEST="resolute", TREX_VM_CODENAME="jammy",
                   TREX_VM_DIR="/x", TREX_VM_DIR_WARNED="1")
    r = subprocess.run([str(GUESTS)], env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "export TREX_VM_GUEST=resolute\n" in r.stdout
    assert "export TREX_VM_DIR=/var/tmp/trex-vm-resolute\n" in r.stdout
    assert "TREX_VM_DIR_WARNED" not in r.stdout


def spawn(argv_tail: list[str]) -> subprocess.Popen[bytes]:
    """Starts a sleeping python whose argv ends with argv_tail; returns it once /proc shows the full argv."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *argv_tail])
    for _ in range(100):
        if argv_tail[-1].encode() in Path(f"/proc/{p.pid}/cmdline").read_bytes():
            break
        time.sleep(0.02)
    return p


def running(env: dict[str, str]) -> tuple[int, str]:
    """Runs trex_vm_running under env; returns (rc, stderr)."""
    r = subprocess.run(["bash", "-c", ". vm/guests.sh; trex_vm_running"], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    return r.returncode, r.stderr


def test_vm_running_needs_this_guests_qemu(tmp_path: Path, stubs: Path) -> None:
    """Only a live process that is this guest's QEMU (by -name or by disk) counts as running."""
    res = make_env(tmp_path, stubs, TREX_VM_GUEST="resolute")
    jam = make_env(tmp_path, stubs)
    rdir = tmp_path / "base" / "trex-vm-resolute"
    jdir = tmp_path / "base" / "trex-vm"
    rdir.mkdir(parents=True)
    jdir.mkdir(parents=True)
    pidfile = rdir / "qemu.pid"
    assert running(res) == (1, "")
    pidfile.write_text("")
    assert running(res) == (1, "")
    pidfile.write_text("abc\n")
    assert running(res) == (1, "")
    pidfile.write_text(f"{os.getpid()}\n")
    assert running(res) == (1, "")
    cases = [
        (res, pidfile, ["-name", "trex-vm"], 1),
        (jam, jdir / "qemu.pid", ["-name", "trex-vm-resolute"], 1),
        (res, pidfile, ["-name", "trex-vm-resolute"], 0),
        (res, pidfile, ["-name", "trex-vm", "-drive", f"file={rdir}/trex.qcow2,if=virtio"], 0),
    ]
    for env, pf, tail, want in cases:
        p = spawn(tail)
        try:
            pf.write_text(f"{p.pid}\n")
            assert running(env) == (want, ""), tail
        finally:
            p.kill()
            p.wait()


def test_launch_jammy_command_line_unchanged(tmp_path: Path, stubs: Path) -> None:
    """With no guest selected, launch.sh builds today's qemu command line."""
    env = make_env(tmp_path, stubs, base=False)
    launch = str(ROOT / "vm" / "launch.sh")
    r = subprocess.run([launch, "trex_ready"], env=env, capture_output=True, text=True)
    w = "/var/tmp/trex-vm"
    assert r.returncode == 0 and r.stdout.splitlines() == [
        "--user", "--scope", "-q", "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0", "--",
        "qemu-system-x86_64", "-name", "trex-vm", "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "4096",
        "-drive", f"file={w}/trex.qcow2,if=virtio,format=qcow2",
        "-drive", f"file={w}/seed.iso,if=virtio,format=raw,readonly=on",
        "-nic", "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22,hostfwd=tcp:127.0.0.1:4500-:4500,"
                "hostfwd=tcp:127.0.0.1:4501-:4501,hostfwd=tcp:127.0.0.1:4510-:4510,hostfwd=tcp:127.0.0.1:8090-:8090",
        "-display", "none", "-serial", f"file:{w}/console.log",
        "-monitor", f"unix:{w}/monitor.sock,server,nowait", "-pidfile", f"{w}/qemu.pid",
        "-loadvm", "trex_ready"]


def test_launch_resolute_forwards(tmp_path: Path, stubs: Path) -> None:
    """The resolute guest gets its own -name and host ports, with the guest side unchanged."""
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="resolute")
    r = subprocess.run([str(ROOT / "vm" / "launch.sh")], env=env, capture_output=True, text=True)
    argv = r.stdout.splitlines()
    assert argv[argv.index("-name") + 1] == "trex-vm-resolute"
    assert argv[argv.index("-nic") + 1] == (
        "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2322-:22,hostfwd=tcp:127.0.0.1:4600-:4500,"
        "hostfwd=tcp:127.0.0.1:4601-:4501,hostfwd=tcp:127.0.0.1:4610-:4510,hostfwd=tcp:127.0.0.1:8190-:8090")


def test_ssh_uses_the_guest_port_and_dir(tmp_path: Path, stubs: Path) -> None:
    """ssh.sh passes the selected guest's port and key path."""
    ssh = str(ROOT / "vm" / "ssh.sh")
    argv = subprocess.run([ssh, "true"], env=make_env(tmp_path, stubs, TREX_VM_GUEST="resolute"),
                          capture_output=True, text=True).stdout.splitlines()
    assert argv[argv.index("-p") + 1] == "2322"
    assert argv[argv.index("-i") + 1] == f"{tmp_path}/base/trex-vm-resolute/id_ed25519"
    argv = subprocess.run([ssh, "true"], env=make_env(tmp_path, stubs), capture_output=True,
                          text=True).stdout.splitlines()
    assert argv[argv.index("-p") + 1] == "2222"


def test_executed_output_evals_to_the_sourced_values(tmp_path: Path, stubs: Path) -> None:
    """eval of the executed output (base dir with a space) sets all 18 keys as sourcing does."""
    base = tmp_path / "a base"
    keys = KEYS + ("TREX_VM_CODENAME", "TREX_VM_IMAGE", "TREX_VM_IMAGE_URL", "TREX_VM_SHARED",
                   "TREX_VM_GUEST")
    assert len(keys) == 18
    env = make_env(tmp_path, stubs, base=False, TREX_VM_BASE=str(base))
    r = subprocess.run(["bash", "-c", 'eval "$(TREX_VM_GUEST=resolute vm/guests.sh)"; '
                        + "".join(f'printf "%s=%s\n" {k} "${k}"; ' for k in keys)],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stderr == ""
    got = dict(line.split("=", 1) for line in r.stdout.splitlines())
    want, _, _ = sourced({**env, "TREX_VM_GUEST": "resolute"}, *keys)
    assert got == want and got["TREX_VM_DIR"] == f"{base}/trex-vm-resolute"


def test_executed_mode_rejects_unknown_arguments(tmp_path: Path, stubs: Path) -> None:
    """Any argument but a lone --list prints usage on stderr and exits 2."""
    for args in (["--lsit"], ["--list", "x"]):
        env = make_env(tmp_path, stubs)
        r = subprocess.run([str(GUESTS), *args], env=env, capture_output=True, text=True)
        assert (r.returncode, r.stdout, r.stderr) == (2, "", "usage: guests.sh [--list]\n")
