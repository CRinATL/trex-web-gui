"""P5 guest control: vm/mode.sh and vm/daemon.sh against stubs (ssh, stack.sh, pub_ready, curl), and the rule
that only vm/build.sh ever saves a snapshot. No test reaches a guest."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
STATEFUL = ("sudo sh -c 'rm -f /tmp/trex-stateful.log; cd /opt/trex/v3.08 && "
            "setsid nohup ./t-rex-64 -f cap2/sfr2.yaml -d 120 -l 1000 --rx-check 100 -c 1 "
            ">/tmp/trex-stateful.log 2>&1 </dev/null &'")


def _script(path: Path, body: str) -> Path:
    """Writes an executable bash script; returns its path."""
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Path]:
    """Stubs for ssh, stack.sh, pub_ready and curl, each logging its argv to calls.txt; returns the paths."""
    calls = tmp_path / "calls.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return {
        "calls": calls, "bin": bin_dir,
        "ssh": _script(tmp_path / "ssh", f'printf "ssh %s\\n" "$*" >> {calls}\nexit 0\n'),
        "stack": _script(tmp_path / "stack", f'printf "stack %s\\n" "$*" >> {calls}\nexit 0\n'),
        "ready": _script(tmp_path / "ready", f'printf "ready %s\\n" "$*" >> {calls}\nexit 0\n'),
        "curl": _script(bin_dir / "curl",
                        f'printf "curl %s\\n" "$*" >> {calls}\necho \'{{"id": 1, "result": true}}\'\n'),
    }


def run(rig: dict[str, Path], script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Runs vm/<script> with the stubs wired through the seams; returns the result even on a nonzero exit."""
    env = {**os.environ, "PATH": f"{rig['bin']}:{os.environ['PATH']}", "TREX_VM_SSH": str(rig["ssh"]),
           "TREX_VM_STACK_SH": str(rig["stack"]), "TREX_VM_PUB_READY": str(rig["ready"]),
           "TREX_VM_GUEST": "jammy"}
    return subprocess.run(["bash", str(ROOT / "vm" / script), *args], capture_output=True, text=True, env=env,
                          check=False, timeout=60)


def log(rig: dict[str, Path]) -> str:
    """The stub call log, empty when no stub ran."""
    return rig["calls"].read_text() if rig["calls"].exists() else ""


def test_mode_refuses_an_unknown_mode_before_ssh(rig: dict[str, Path]) -> None:
    r = run(rig, "mode.sh", "bogus")
    assert r.returncode == 2, r
    assert log(rig) == ""


def test_mode_stateful_starts_the_spec_command_detached(rig: dict[str, Path]) -> None:
    r = run(rig, "mode.sh", "stateful")
    assert r.returncode == 0, r
    text = log(rig)
    assert "pkill -INT -x _t-rex-64" in text
    assert STATEFUL in text
    assert "ready --timeout 60" in text
    assert "pkill -f" not in text


def test_mode_stateless_delegates_to_stack_legacy(rig: dict[str, Path]) -> None:
    r = run(rig, "mode.sh", "stateless")
    assert r.returncode == 0, r
    assert log(rig).strip() == "stack legacy"


def test_mode_reports_the_guest_log_when_trex_does_not_publish(rig: dict[str, Path]) -> None:
    calls = rig["calls"]
    _script(rig["ready"], f'printf "ready %s\\n" "$*" >> {calls}\nexit 1\n')
    r = run(rig, "mode.sh", "stateful")
    assert r.returncode == 1, r
    assert "/tmp/trex-stateful.log" in r.stdout
    assert "tail -n 20 /tmp/trex-stateful.log" in log(rig)


def test_daemon_start_sets_the_log_and_records_its_pid(rig: dict[str, Path]) -> None:
    r = run(rig, "daemon.sh", "start")
    assert r.returncode == 0, r
    text = log(rig)
    assert "TREX_DAEMON_LOG=/tmp/trex-daemon.log setsid nohup ./trex_daemon_server start-live -p 8090" in text
    assert "echo $! >/tmp/trex-daemon.pid" in text
    assert "connectivity_check" in text and "127.0.0.1:8090" in text


def test_daemon_stop_escalates_to_kill(rig: dict[str, Path]) -> None:
    _script(rig["curl"], "exit 7\n")   # the port is closed after the kill
    r = run(rig, "daemon.sh", "stop")
    assert r.returncode == 0, r
    lines = log(rig).splitlines()
    term = next(i for i, ln in enumerate(lines) if "kill -TERM $p" in ln)
    kill = next(i for i, ln in enumerate(lines) if "kill -KILL $p" in ln)
    assert term < kill
    for i in (term, kill):
        assert "grep -qa trex_daemon_server /proc/$p/cmdline" in lines[i]
    assert "pkill -f" not in log(rig)


def test_daemon_stop_fails_while_the_port_still_answers(rig: dict[str, Path]) -> None:
    r = run(rig, "daemon.sh", "stop")   # the curl stub keeps answering true
    assert r.returncode == 1, r


def test_daemon_status_probes_the_port(rig: dict[str, Path]) -> None:
    assert run(rig, "daemon.sh", "status").stdout.strip() == "daemon: running"
    _script(rig["curl"], "exit 7\n")
    assert run(rig, "daemon.sh", "status").stdout.strip() == "daemon: stopped"


def test_no_vm_script_but_build_saves_a_snapshot() -> None:
    files = [*(ROOT / "vm").glob("*.sh"), *(ROOT / "vm").glob("*.py")]
    assert files
    offenders = sorted(p.name for p in files if "savevm" in p.read_text() and p.name != "build.sh")
    assert offenders == []
    assert "savevm" in (ROOT / "vm" / "build.sh").read_text()


def test_daemon_probe_ignores_any_proxy(rig: dict[str, Path]) -> None:
    run(rig, "daemon.sh", "status")
    assert "--noproxy *" in log(rig)


def test_pub_ready_reads_a_corrupt_zipped_frame_as_no_name() -> None:
    import importlib.util
    import struct
    import zlib
    spec = importlib.util.spec_from_file_location("pub_ready_under_test", ROOT / "vm" / "pub_ready.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    magic = struct.pack(">II", 0xABE85CEA, 10)
    assert mod.frame_name(magic + b"not a zlib stream") is None
    assert mod.frame_name(b'{"name": "trex-global"}') == "trex-global"
    good = magic[:4] + struct.pack(">I", 23) + zlib.compress(b'{"name": "trex-global"}')
    assert mod.frame_name(good) == "trex-global"
