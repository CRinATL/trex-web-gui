"""vm/real.sh and vm/demo.sh on the guest selection: validation before boot, the "already running" test,
the shared frontend/dist rule and `status --all`. No test reaches a real guest: every subprocess env is built
from scratch with stubbed tools (each logs to calls.log and exits 1) and TREX_VM_BASE points into tmp_path."""
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from support import demo_ports_lock

ROOT = Path(__file__).resolve().parent.parent.parent
STUBBED = ("systemd-run", "qemu-img", "qemu-system-x86_64", "ssh", "ssh-keygen", "xorriso", "curl", "gpgv",
           "socat", "uv", "npm")


def taken(port: int) -> bool:
    """True when something accepts a TCP connection on 127.0.0.1:port."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


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
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "TREX_VM_BASE": str(tmp_path / "base"),
           "TREX_VM_GUEST": "resolute", **extra}
    for tool in STUBBED:
        assert shutil.which(tool, path=env["PATH"]) == str(stubs / tool)
    return env


@pytest.fixture
def row(tmp_path: Path) -> Path:
    """The resolute row dir, holding an empty trex.qcow2."""
    d = tmp_path / "base" / "trex-vm-resolute"
    d.mkdir(parents=True)
    (d / "trex.qcow2").write_bytes(b"")
    return d


def calls(tmp_path: Path) -> str:
    """The stub call log, empty when no stub ran."""
    p = tmp_path / "calls.log"
    return p.read_text() if p.exists() else ""


def run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Runs a vm script from the repo root; returns the completed process."""
    return subprocess.run([str(ROOT / "vm" / script), *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=60)


@pytest.fixture(autouse=True)
def _guest_ports_free() -> None:
    """Skips every test here while the resolute ssh port is in use."""
    if taken(2322):
        pytest.skip("127.0.0.1:2322 is in use")


@pytest.fixture
def demo_ports() -> Iterator[None]:
    """Holds the demo-ports lock, so no other test binds or probes the guests' ports meanwhile."""
    with demo_ports_lock():
        yield


def spawn(tail: list[str]) -> subprocess.Popen[bytes]:
    """Starts a sleeping python whose argv ends with tail; returns it once /proc shows the full argv."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *tail])
    for _ in range(100):
        if tail[-1].encode() in Path(f"/proc/{p.pid}/cmdline").read_bytes():
            break
        time.sleep(0.02)
    return p


def test_real_refuses_a_bad_stack_before_boot(tmp_path: Path, stubs: Path, row: Path) -> None:
    """A bad TREX_VM_STACK is refused before any boot."""
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_STACK="bogus"))
    assert r.returncode == 2
    assert r.stdout.strip() == "UNAVAILABLE: TREX_VM_STACK must be legacy or linux_based"
    assert "systemd-run" not in calls(tmp_path)


def test_real_records_from_jammy_only(tmp_path: Path, stubs: Path, row: Path) -> None:
    """A recorder on another guest is refused before any boot."""
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_RECORD="p2"))
    assert r.returncode == 2
    assert r.stdout.strip() == "UNAVAILABLE: TREX_VM_RECORD records from jammy only"
    assert "systemd-run" not in calls(tmp_path)


def test_real_refuses_while_this_guest_runs(tmp_path: Path, stubs: Path, row: Path) -> None:
    """A live QEMU named for this guest refuses the run and is not quit."""
    p = spawn(["-name", "trex-vm-resolute"])
    try:
        (row / "qemu.pid").write_text(f"{p.pid}\n")
        r = run("real.sh", make_env(tmp_path, stubs))
    finally:
        p.kill()
        p.wait()
    assert r.returncode == 2
    assert r.stdout.strip() == "UNAVAILABLE: a trex VM is already running"
    log = calls(tmp_path)
    assert "systemd-run" not in log and "socat" not in log


def test_real_ignores_a_pidfile_naming_another_process(tmp_path: Path, stubs: Path, row: Path) -> None:
    """A pidfile naming an unrelated live process is not a running VM, and the exit trap leaves it alone."""
    (row / "qemu.pid").write_text(f"{os.getpid()}\n")
    r = run("real.sh", make_env(tmp_path, stubs))
    assert r.returncode == 2
    assert "already running" not in r.stdout and "VM did not come up" in r.stdout
    assert "socat" not in calls(tmp_path)


def test_demo_refuses_when_this_guests_gui_port_is_taken(
        tmp_path: Path, stubs: Path, row: Path, demo_ports: None) -> None:
    """demo.sh up checks the selected guest's GUI port (8865), not 8765."""
    srv = socket.socket()
    try:
        srv.bind(("127.0.0.1", 8865))
    except OSError:
        srv.close()
        pytest.skip("127.0.0.1:8865 is in use")
    srv.listen(1)
    try:
        r = run("demo.sh", make_env(tmp_path, stubs), "up")
    finally:
        srv.close()
    assert r.returncode == 2
    assert "port 8865 is already in use" in r.stdout
    assert "systemd-run" not in calls(tmp_path)


def other_serving(tmp_path: Path, stubs: Path, dirs: str) -> subprocess.CompletedProcess[str]:
    """Runs other_demo_serving from a DEMO_SH_LIB=1 source of demo.sh on the resolute guest."""
    env = make_env(tmp_path, stubs, DEMO_SH_LIB="1", TREX_VM_DEMO_DIRS=dirs)
    return subprocess.run(["bash", "-c", ". vm/demo.sh; other_demo_serving"], cwd=ROOT, env=env,
                          capture_output=True, text=True)


def test_demo_skips_the_build_while_another_guests_demo_serves(tmp_path: Path, stubs: Path) -> None:
    """other_demo_serving names a row whose pidfile is a live trex-web-gui serve process."""
    other = tmp_path / "other"
    other.mkdir()
    dirs = f"jammy={other}"
    assert other_serving(tmp_path, stubs, dirs).returncode == 1
    p = spawn(["trex-web-gui", "serve"])
    try:
        (other / "demo-gui.pid").write_text(f"{p.pid}\n")
        r = other_serving(tmp_path, stubs, dirs)
        assert (r.returncode, r.stdout) == (0, "jammy\n")
    finally:
        p.kill()
        p.wait()
    (other / "demo-gui.pid").write_text(f"{os.getpid()}\n")
    assert other_serving(tmp_path, stubs, dirs).returncode == 1


def test_demo_status_all_names_every_row(tmp_path: Path, stubs: Path, demo_ports: None) -> None:
    """status --all prints one headed block per guest row, each from its own dir and ports."""
    for port in (2222, 2322, 2422, 8765, 8865, 8965):
        if taken(port):
            pytest.skip(f"127.0.0.1:{port} is in use")
    dirs = f"jammy={tmp_path}/j resolute={tmp_path}/r noble={tmp_path}/n"
    r = run("demo.sh", make_env(tmp_path, stubs, TREX_VM_DEMO_DIRS=dirs), "status", "--all")
    assert r.returncode == 0
    assert r.stdout == ("jammy:\nVM: stopped\nGUI: stopped\nresolute:\nVM: stopped\nGUI: stopped\n"
                        "noble:\nVM: stopped\nGUI: stopped\n")



def test_demo_usage_shows_the_whole_header(tmp_path: Path, stubs: Path) -> None:
    """An unknown command prints the header comment (rc 2), also under DEMO_SH_LIB=1 when executed."""
    for extra in ({}, {"DEMO_SH_LIB": "1"}):
        r = run("demo.sh", make_env(tmp_path, stubs, **extra), "bogus")
        assert r.returncode == 2 and r.stderr == ""
        assert "status --all" in r.stdout and "DEMO_SH_LIB" in r.stdout


def test_demo_status_all_flags_an_unresolved_row(tmp_path: Path, stubs: Path, demo_ports: None) -> None:
    """A row with no resolvable work dir (seam: an empty TREX_VM_DEMO_DIRS entry) reads unknown, rc 1."""
    for port in (2222, 2322, 2422, 8765, 8865, 8965):
        if taken(port):
            pytest.skip(f"127.0.0.1:{port} is in use")
    dirs = f"jammy= resolute={tmp_path}/r noble={tmp_path}/n"
    r = run("demo.sh", make_env(tmp_path, stubs, TREX_VM_DEMO_DIRS=dirs), "status", "--all")
    assert r.returncode == 1
    assert r.stdout == ("jammy:\nVM: unknown (could not resolve the work dir)\n"
                        "resolute:\nVM: stopped\nGUI: stopped\nnoble:\nVM: stopped\nGUI: stopped\n")


def test_real_accepts_p5_and_refuses_a_bad_mark(tmp_path: Path, stubs: Path, row: Path) -> None:
    """TREX_VM_MARK takes only restarts_trex, and only on jammy; both refusals come before any boot."""
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_MARK="bogus"))
    assert r.returncode == 2
    assert r.stdout.strip().startswith("UNAVAILABLE: TREX_VM_MARK")
    assert "systemd-run" not in calls(tmp_path)
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_MARK="restarts_trex"))   # the guest is resolute
    assert r.returncode == 2
    assert r.stdout.strip().startswith("UNAVAILABLE: TREX_VM_MARK=restarts_trex")
    assert "systemd-run" not in calls(tmp_path)
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_RECORD="p5"))
    assert r.stdout.strip() == "UNAVAILABLE: TREX_VM_RECORD records from jammy only"   # p5 is known


@pytest.mark.parametrize("record,script", [("p5", "record_p5.py"), ("p5-stf", "record_p5_stf.py")])
def test_real_restores_stateless_after_the_p5_recorder(tmp_path: Path, stubs: Path, record: str,
                                                       script: str) -> None:
    """A failing p5 or p5-stf recorder (rc 3) still stops the daemon, restarts legacy, keeps rc 3."""
    if taken(2222):
        pytest.skip("127.0.0.1:2222 is in use")
    jammy = tmp_path / "base" / "trex-vm"
    jammy.mkdir(parents=True)
    (jammy / "trex.qcow2").write_bytes(b"")
    log = tmp_path / "calls.log"
    sysrun = stubs / "systemd-run"
    pidfile = tmp_path / "sleeper.pid"
    sysrun.write_text(f'#!/bin/bash\necho "systemd-run $*" >> "{log}"\n'
                      f'case "$*" in *qemu-system-x86_64*) echo $$ > "{pidfile}"; exec sleep 30;; esac\n'
                      'exit 3\n')
    (stubs / "socat").write_text(f'#!/bin/bash\necho "socat $*" >> "{log}"\n'
                                 f'kill $(cat "{pidfile}")\nexit 0\n')
    for tool in ("ssh", "uv", "curl"):
        (stubs / tool).write_text(f'#!/bin/bash\necho "{tool} $*" >> "{log}"\nexit 0\n')
    env = make_env(tmp_path, stubs, TREX_VM_GUEST="jammy", TREX_VM_RECORD=record)
    t0 = time.monotonic()
    r = run("real.sh", env)
    assert time.monotonic() - t0 < 10
    assert r.returncode == 3, r
    text = calls(tmp_path)
    rec = text.index(script)
    stop = text.index("kill -TERM $p", rec)
    legacy = text.index("--cfg /etc/trex_cfg_legacy.yaml", stop)
    assert rec < stop < legacy


def test_real_accepts_p5_stf_and_refuses_it_off_jammy(tmp_path: Path, stubs: Path, row: Path) -> None:
    """p5-stf is a known recorder, and like every recorder it is refused before any boot off jammy."""
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_RECORD="p5-stf"))   # the guest is resolute
    assert r.stdout.strip() == "UNAVAILABLE: TREX_VM_RECORD records from jammy only"
    assert r.returncode == 2
    assert "systemd-run" not in calls(tmp_path)
    r = run("real.sh", make_env(tmp_path, stubs, TREX_VM_RECORD="bogus"))
    assert r.stdout.strip().startswith("UNAVAILABLE: TREX_VM_RECORD must be")
    assert "p5-stf" in r.stdout


def test_demo_passes_the_daemon_port_for_non_jammy_rows(tmp_path: Path, stubs: Path) -> None:
    """demo.sh serves with --daemon-port $TREX_VM_DAEMON_PORT on every row but jammy, whose command line has
    no such flag (the daemon is forwarded at the client default 8090)."""
    got: dict[str, str] = {}
    for guest in ("jammy", "resolute"):
        env = make_env(tmp_path, stubs, DEMO_SH_LIB="1", TREX_VM_GUEST=guest)
        r = subprocess.run(["bash", "-c", ". vm/demo.sh; daemon_flag; echo \"port=$TREX_VM_DAEMON_PORT\""],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        got[guest] = r.stdout
    assert got["jammy"] == "port=8090\n"
    assert got["resolute"].startswith("--daemon-port ") and "port=8090" not in got["resolute"]
    port = got["resolute"].split()[1]
    assert got["resolute"] == f"--daemon-port {port}\nport={port}\n" and int(port) > 8090
    assert "serve --port $PORT --server $server $daemon >" in (ROOT / "vm" / "demo.sh").read_text()


def test_demo_ports_lock_excludes_another_process(tmp_path: Path) -> None:
    """While the lock is held a second process cannot flock the file; once released it can."""
    lock = tmp_path / "ports.lock"
    probe = ("import fcntl, os, sys\n"
             "fd = os.open(sys.argv[1], os.O_RDWR)\n"
             "try:\n"
             "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
             "except BlockingIOError:\n"
             "    sys.exit(1)\n")

    def rc() -> int:
        return subprocess.run([sys.executable, "-I", "-c", probe, str(lock)], timeout=30).returncode

    with demo_ports_lock(lock):
        assert rc() == 1
    assert rc() == 0


def test_demo_ports_lock_is_freed_when_its_holder_dies(tmp_path: Path) -> None:
    """A held lock makes a waiter fail naming the path after wait_s; killing the holder frees it."""
    lock = tmp_path / "ports.lock"
    holder = ("import fcntl, os, sys, time\n"
              "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR)\n"
              "fcntl.flock(fd, fcntl.LOCK_EX)\n"
              "print('held', flush=True)\n"
              "time.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-I", "-c", holder, str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout is not None and p.stdout.readline().strip() == "held"
        with pytest.raises(TimeoutError, match=str(lock)):
            with demo_ports_lock(lock, wait_s=0.3):
                pytest.fail("entered while another process held the lock")
    finally:
        p.kill()
        p.wait()
        if p.stdout is not None:
            p.stdout.close()
    with demo_ports_lock(lock, wait_s=5):
        pass
