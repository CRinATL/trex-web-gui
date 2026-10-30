"""P3b follow-up 4 tooling: vm/netem.sh argument checks and commands, the recorder's row shape, and the commit
that vm/measure_netem.py attributes a run to. None of this touches a VM: vm/ssh.sh is replaced by a stub."""
import importlib
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
NETEM = ROOT / "vm" / "netem.sh"


@pytest.fixture
def vm_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Puts vm/ on sys.path for this test only (monkeypatch undoes it); returns load(name) for vm/ modules."""
    monkeypatch.syspath_prepend(str(ROOT / "vm"))

    # Imports the named vm/ module; vm/ is on sys.path while the fixture is active.
    def load(name: str) -> ModuleType:
        return importlib.import_module(name)
    return load


@pytest.fixture
def stub(tmp_path: Path) -> dict[str, Path]:
    """Writes an ssh stub script that logs its arguments to calls.txt; returns {"script", "calls"} paths."""
    calls = tmp_path / "calls.txt"
    script = tmp_path / "ssh_stub.sh"
    script.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> {calls}\necho "qdisc stub"\n')
    script.chmod(0o755)
    return {"script": script, "calls": calls}


def run_netem(stub: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    """Runs vm/netem.sh with NETEM_SSH pointing at the stub; returns the result even on a nonzero exit."""
    env = {**os.environ, "NETEM_SSH": str(stub["script"])}
    return subprocess.run(["bash", str(NETEM), *args], capture_output=True, text=True, env=env, check=False)


@pytest.mark.parametrize("args", [("x", "1"), ("50", "60"), ("-1", "0"), ("2000", "0"), ()])
def test_netem_rejects_bad_arguments(stub: dict[str, Path], args: tuple[str, ...]) -> None:
    r = run_netem(stub, *args)
    assert r.returncode == 2, r
    assert not stub["calls"].exists(), "validation must happen before any ssh"


def test_netem_commands(stub: dict[str, Path]) -> None:
    r = run_netem(stub, "50", "10")
    assert r.returncode == 0, r
    set_calls = stub["calls"].read_text()
    stub["calls"].unlink()
    assert "tc qdisc replace dev ens3 root netem delay 50ms 10ms" in set_calls
    assert "qdisc add" not in set_calls
    assert "tc qdisc show dev ens3" in set_calls
    for args in (("off",), ("0", "0")):
        stub["calls"].unlink(missing_ok=True)
        r = run_netem(stub, *args)
        assert r.returncode == 0, r
        off_calls = stub["calls"].read_text()
        assert "tc qdisc del dev ens3 root" in off_calls, args
        assert "netem delay" not in off_calls, args
        assert "qdisc stub" in r.stdout


def test_record_netem_summary_shape(vm_module: Any) -> None:
    record_netem = vm_module("record_netem")
    p3b = json.loads((ROOT / "tests" / "fixtures" / "p3b" / "summary.json").read_text())
    before = {"commit": "4de803a", "connect_s": [0.1]}
    row = record_netem.summarize(p3b, before, {"error": "x"}, 50, 10, "qdisc netem 8001: delay 50ms")
    assert set(row) == {"delay_ms", "jitter_ms", "qdisc", "p3b", "before", "after"}
    assert (row["delay_ms"], row["jitter_ms"]) == (50, 10)
    assert row["qdisc"] == "qdisc netem 8001: delay 50ms"
    assert set(row["p3b"]) == {"composites_secs", "max_secs_by_call", "slowest_call"}
    assert row["p3b"]["composites_secs"] == p3b["composites_secs"]
    assert row["before"] == before and row["after"] == {"error": "x"}
    assert record_netem.summarize({"error": "boom"}, before, before, 0, 0)["p3b"] == {"error": "boom"}


def test_measure_records_its_commit(vm_module: Any) -> None:
    measure_netem = vm_module("measure_netem")
    want = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    assert measure_netem.commit_of(ROOT / "backend") == want


NETEM_50 = "qdisc netem 8001: root refcnt 2 limit 1000 delay 50ms  10ms"
FQ = "qdisc fq_codel 0: root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5ms interval 100ms"


def test_qdisc_ok(vm_module: Any) -> None:
    ok = vm_module("record_netem").qdisc_ok
    assert ok(NETEM_50, 50, 10) and not ok(NETEM_50, 100, 20) and not ok(NETEM_50, 0, 0)
    assert ok(FQ, 0, 0) and not ok(FQ, 50, 10)
    assert not ok(NETEM_50, 50, 20) and not ok(NETEM_50, 50, 0) and not ok(NETEM_50, 50, 1)
    # iproute2 omits a zero jitter
    assert ok("qdisc netem 8001: root refcnt 2 limit 1000 delay 50ms", 50, 0)
    assert not ok("qdisc netem 8001: root refcnt 2 limit 1000 delay 50ms", 50, 10)


@pytest.mark.parametrize("rc,out", [(1, ""), (0, NETEM_50)])
def test_unapplied_delay_gives_error_rows_and_no_measurement(vm_module: Any, monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path, rc: int, out: str) -> None:
    rn = vm_module("record_netem")
    monkeypatch.setattr(rn, "step", lambda argv, timeout: subprocess.CompletedProcess(argv, rc, out, "boom"))
    measured: list[str] = []
    monkeypatch.setattr(rn, "measure", lambda *a, **k: measured.append("x") or {})
    row = rn.record_delay(100, 20, "/nonexistent", tmp_path, None)
    assert not measured
    assert "error" in row["p3b"] and row["p3b"] == row["before"] == row["after"]
    assert (row["delay_ms"], row["jitter_ms"]) == (100, 20)


def assert_all_error(row: dict[str, Any], needle: str) -> None:
    """Asserts the p3b, before and after entries are the same error mapping, whose message holds needle."""
    assert row["p3b"] == row["before"] == row["after"]
    assert needle in row["p3b"]["error"], row


def test_step_that_cannot_launch_gives_error_rows(vm_module: Any, monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    rn = vm_module("record_netem")

    def boom(argv: list[str], timeout: float) -> None:
        raise OSError("no such file")
    monkeypatch.setattr(rn, "step", boom)
    monkeypatch.setattr(rn, "measure", lambda *a, **k: pytest.fail("measured"))
    assert_all_error(rn.record_delay(100, 20, "/nonexistent", tmp_path, None), "OSError")


def test_netem_step_timeout_gives_error_rows(vm_module: Any, monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
    rn = vm_module("record_netem")
    seen: list[float] = []

    def run(argv: list[str], **kw: Any) -> None:
        seen.append(kw["timeout"])
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(rn.subprocess, "run", run)
    monkeypatch.setattr(rn, "measure", lambda *a, **k: pytest.fail("measured"))
    row = rn.record_delay(100, 20, "/nonexistent", tmp_path, None)
    assert_all_error(row, "netem.sh timed out after 60 s")
    assert seen == [60]


def test_p3b_and_measure_timeouts_are_recorded_and_the_loop_goes_on(
        vm_module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rn = vm_module("record_netem")
    seen: list[tuple[str, float]] = []

    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append((Path(argv[-1] if "netem.sh" in argv[0] else argv[2]).name, kw["timeout"]))
        if "netem.sh" in argv[0]:
            return subprocess.CompletedProcess(argv, 0, NETEM_50, "")
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(rn.subprocess, "run", run)
    row = rn.record_delay(50, 10, "/nonexistent", tmp_path, None)
    assert "record_p3b timed out after 1800 s" in row["p3b"]["error"]
    assert "measure_netem timed out after 1800 s" in row["before"]["error"]
    assert "measure_netem timed out after 1800 s" in row["after"]["error"]
    assert [t for _, t in seen] == [60, 1800, 1800, 1800]
