"""P7 guest control: vm/mode.sh emu, vm/emu.sh and vm/emu_ready.py against stubs (ssh, emu_ready).
No test reaches a guest."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from support import guestenv

ROOT = Path(__file__).resolve().parent.parent.parent
EMU_START = ("sudo sh -c 'rm -f /tmp/trex-emu-mode.log; cd /opt/trex/v3.08 && "
             "setsid nohup ./t-rex-64 -i --iom 0 --no-scapy-server --emu --cfg /etc/trex_cfg.yaml "
             ">/tmp/trex-emu-mode.log 2>&1 </dev/null &'")


def _script(path: Path, body: str) -> Path:
    """Writes an executable bash script; returns its path."""
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Path]:
    """Stubs for ssh and emu_ready, each logging its argv to calls.txt and exiting 0; returns the paths."""
    calls = tmp_path / "calls.txt"
    return {
        "calls": calls,
        "ssh": _script(tmp_path / "ssh", f'printf "ssh %s\\n" "$*" >> {calls}\nexit 0\n'),
        "ready": _script(tmp_path / "ready", f'printf "ready %s\\n" "$*" >> {calls}\nexit 0\n'),
    }


def run(rig: dict[str, Path], script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Runs vm/<script> with the stubs wired through the seams; returns the result even on a nonzero exit."""
    env = {**os.environ, "TREX_VM_SSH": str(rig["ssh"]), "TREX_VM_EMU_READY": str(rig["ready"]),
           "TREX_VM_GUEST": "jammy"}
    return subprocess.run(["bash", str(ROOT / "vm" / script), *args], capture_output=True, text=True, env=env,
                          check=False, timeout=60)


def _no_emu_running(rig: dict[str, Path]) -> None:
    """Makes the ssh stub answer the pgrep probe with failure (no trex-emu in the guest)."""
    calls = rig["calls"]
    _script(rig["ssh"], f'printf "ssh %s\\n" "$*" >> {calls}\ncase "$*" in *pgrep*) exit 1;; esac\nexit 0\n')


def log(rig: dict[str, Path]) -> str:
    """The stub call log, empty when no stub ran."""
    return rig["calls"].read_text() if rig["calls"].exists() else ""


def test_mode_emu_starts_the_r01_command(rig: dict[str, Path]) -> None:
    """emu stops TRex, starts the R0.1 command detached (no --software, no library path), waits."""
    r = run(rig, "mode.sh", "emu")
    assert r.returncode == 0, r
    text = log(rig)
    assert text.index("pkill -INT -x _t-rex-64") < text.index(EMU_START) < text.index("ready --timeout 60")
    assert "--software" not in text and "LD_LIBRARY_PATH" not in text
    assert "pkill -f" not in text


def test_mode_emu_fails_when_emu_never_answers(rig: dict[str, Path]) -> None:
    """A failing readiness seam gives rc 1, names the guest log and tails it."""
    _script(rig["ready"], f'printf "ready %s\\n" "$*" >> {rig["calls"]}\nexit 1\n')
    r = run(rig, "mode.sh", "emu")
    assert r.returncode == 1, r
    assert "/tmp/trex-emu-mode.log" in r.stdout
    assert "tail -n 20 /tmp/trex-emu-mode.log" in log(rig)


def test_emu_sh_start_refuses_while_a_trex_emu_runs(rig: dict[str, Path]) -> None:
    """The ssh stub answers the pgrep probe with success (EMU running): no start command is sent."""
    r = run(rig, "emu.sh", "start")
    assert r.returncode == 1, r
    assert "already runs" in r.stdout
    assert "setsid" not in log(rig)


def test_emu_sh_start_runs_standalone_without_d(rig: dict[str, Path]) -> None:
    """With no trex-emu running, start runs the R5 command (library path, -p 4510, no -d), then waits."""
    _no_emu_running(rig)
    r = run(rig, "emu.sh", "start")
    assert r.returncode == 0, r
    text = log(rig)
    assert ("cd /opt/trex/v3.08 && LD_LIBRARY_PATH=/opt/trex/v3.08/so setsid nohup "
            "./trex_emu/trex-emu -p 4510 >/tmp/trex-emu.log 2>&1 </dev/null & "
            "echo $! >/tmp/trex-emu.pid") in text
    assert " -d " not in text
    assert text.index("setsid") < text.index("ready --timeout 60")


def test_emu_sh_stop_kills_by_pidfile(rig: dict[str, Path]) -> None:
    """stop signals the pid from the pidfile, TERM then KILL, each only while its cmdline names trex-emu."""
    _no_emu_running(rig)
    r = run(rig, "emu.sh", "stop")
    assert r.returncode == 0, r
    lines = log(rig).splitlines()
    term = next(i for i, ln in enumerate(lines) if "kill -TERM $p" in ln)
    kill = next(i for i, ln in enumerate(lines) if "kill -KILL $p" in ln)
    assert term < kill
    for i in (term, kill):
        assert "cat /tmp/trex-emu.pid" in lines[i] and "grep -qa trex-emu /proc/$p/cmdline" in lines[i]
    assert "rm -f /tmp/trex-emu.pid" in log(rig)
    assert "pkill -f" not in log(rig)


def test_emu_sh_stop_fails_while_emu_still_runs(rig: dict[str, Path]) -> None:
    """The pgrep stub keeps answering success after the kill: stop reports rc 1."""
    assert run(rig, "emu.sh", "stop").returncode == 1


def test_emu_sh_refuses_an_unknown_command(rig: dict[str, Path]) -> None:
    """An unknown command is rc 2 before any ssh."""
    assert run(rig, "emu.sh", "bogus").returncode == 2
    assert log(rig) == ""


def test_emu_port_defaults_to_jammy_and_refuses_other_guests() -> None:
    """guestenv.emu_port: 4510 for jammy, the exported value otherwise, a named error without it."""
    assert guestenv.emu_port({}) == 4510
    assert guestenv.emu_port({"TREX_VM_EMU_PORT": "4610", "TREX_VM_GUEST": "resolute"}) == 4610
    with pytest.raises(guestenv.GuestEnvError, match=r"vm/guests\.sh"):
        guestenv.emu_port({"TREX_VM_GUEST": "resolute"})
    with pytest.raises(guestenv.GuestEnvError, match="port number"):
        guestenv.emu_port({"TREX_VM_EMU_PORT": "abc"})


def test_emu_ready_handshakes_then_pings(monkeypatch: pytest.MonkeyPatch) -> None:
    """emu_ready retries until api_sync_v2 EMU 1.1 and ping both succeed; a refusal is a failed attempt."""
    spec = importlib.util.spec_from_file_location("emu_ready_under_test", ROOT / "vm" / "emu_ready.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen: list[tuple[str, ...]] = []

    class Fake:
        """A RawRpc stand-in recording the calls made on it."""
        def __init__(self, host: str, port: int, timeout_s: float) -> None:
            """Records the endpoint."""
            seen.append(("open", host, str(port)))

        def api_sync(self, name: str, major: int, minor: int) -> str:
            """Records the handshake; the first attempt is refused."""
            seen.append(("sync", name, str(major), str(minor)))
            if sum(1 for s in seen if s[0] == "sync") == 1:
                raise mod.TrexApiError("refused")
            return "h"

        def call(self, method: str) -> None:
            """Records the call."""
            seen.append(("call", method))

        def close(self) -> None:
            """Records the close."""
            seen.append(("close",))

    monkeypatch.setattr(mod, "RawRpc", Fake)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod.sys, "argv", ["emu_ready.py", "--timeout", "5"])
    assert mod.main() == 0
    assert ("sync", "EMU", "1", "1") in seen and seen[-2:] == [("call", "ping"), ("close",)]


def _ssh_fails_on(rig: dict[str, Path], pattern: str) -> None:
    """Makes the ssh stub fail for a command containing pattern (glob) and succeed for the rest."""
    calls = rig["calls"]
    body = f'printf "ssh %s\\n" "$*" >> {calls}\ncase "$*" in *"{pattern}"*) exit 1;; esac\nexit 0\n'
    _script(rig["ssh"], body)


def test_mode_emu_stops_a_leftover_trex_emu_before_starting(rig: dict[str, Path]) -> None:
    """emu stops trex-emu (TERM, KILL) after TRex and before the --emu start: the answer is then TRex's."""
    r = run(rig, "mode.sh", "emu")
    assert r.returncode == 0, r
    text = log(rig)
    assert (text.index("pkill -INT -x _t-rex-64") < text.index("pkill -TERM -x trex-emu")
            < text.index("pkill -KILL -x trex-emu") < text.index(EMU_START))


def test_mode_emu_fails_when_a_trex_emu_will_not_stop(rig: dict[str, Path]) -> None:
    """A trex-emu that survives the KILL gives rc 1 and no start command."""
    _ssh_fails_on(rig, "-x trex-emu")
    r = run(rig, "mode.sh", "emu")
    assert r.returncode == 1, r
    assert "trex-emu did not stop" in r.stdout
    assert "setsid" not in log(rig) and "ready" not in log(rig)


def test_mode_stop_also_stops_trex_emu(rig: dict[str, Path]) -> None:
    """stop stops _t-rex-64 and then trex-emu; a trex-emu that stays makes it fail."""
    assert run(rig, "mode.sh", "stop").returncode == 0
    text = log(rig)
    assert text.index("pkill -INT -x _t-rex-64") < text.index("pkill -TERM -x trex-emu")
    _ssh_fails_on(rig, "-x trex-emu")
    assert run(rig, "mode.sh", "stop").returncode == 1


def test_emu_sh_start_refuses_while_trex_runs(rig: dict[str, Path]) -> None:
    """Standalone EMU is not started next to a running _t-rex-64 (R5: no TRex running)."""
    _ssh_fails_on(rig, "pgrep -x trex-emu")
    r = run(rig, "emu.sh", "start")
    assert r.returncode == 1, r
    assert "TRex runs" in r.stdout
    assert "setsid" not in log(rig)
