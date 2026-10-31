"""scripts/deferred.py mark on a temp register: the status column changes, nothing else does."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from support import ROOT

SCRIPT = ROOT / "scripts" / "deferred.py"
spec = importlib.util.spec_from_file_location("deferred", SCRIPT)
assert spec and spec.loader
deferred = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deferred)

REGISTER = """# Deferred

| ID | Ledger | Entry | Status |
|---|---|---|---|
| p3c-capture-18 | [p3c-capture ledger :18](x/progress.md) | Task 0: minor (deferred): a \\| b | open (untriaged) |
| p3c-capture-29 | [p3c-capture ledger :29](x/progress.md) | Task 1: minor (deferred): c | fixed in 6fa33f0 |
"""  # noqa: E501  (a table row recorded as data)


def run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Runs the deferred script isolated (-I -B) on --file path; returns the result even on a nonzero exit."""
    return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), *args, "--file", str(path)],
                          check=False, capture_output=True, text=True)


def test_mark_sets_only_the_status_column(tmp_path: Path) -> None:
    f = tmp_path / "DEFERRED.md"
    f.write_text(REGISTER)
    r = run(f, "mark", "p3c-capture-18", "--status", "fixed in 5aa0724")
    assert r.returncode == 0, r.stderr
    want = REGISTER.replace("| open (untriaged) |", "| fixed in 5aa0724 |")
    assert f.read_text() == want
    assert deferred.ROW.match(want.splitlines()[4])["status"] == "fixed in 5aa0724"


def test_mark_several_rows(tmp_path: Path) -> None:
    f = tmp_path / "DEFERRED.md"
    f.write_text(REGISTER)
    assert run(f, "mark", "p3c-capture-18", "p3c-capture-29", "--status", "fixed in SQUASH").returncode == 0
    assert f.read_text().count("| fixed in SQUASH |") == 2


def test_an_unknown_id_writes_nothing(tmp_path: Path) -> None:
    f = tmp_path / "DEFERRED.md"
    f.write_text(REGISTER)
    r = run(f, "mark", "p3c-capture-18", "p3c-capture-999", "--status", "fixed in 5aa0724")
    assert r.returncode == 1
    assert "p3c-capture-999" in r.stderr
    assert f.read_text() == REGISTER


def test_a_status_with_a_pipe_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "DEFERRED.md"
    f.write_text(REGISTER)
    assert run(f, "mark", "p3c-capture-18", "--status", "a | b").returncode == 1
    assert f.read_text() == REGISTER


def test_host_rows_are_not_ledger_rows() -> None:
    """H-* rows (the 1.0.x host findings) have no ledger link; deferred.py leaves them to release.py."""
    row = "| H-1 | host use, 2026-10-31 | a finding | open |"
    assert deferred.ROW.match(row) is None


def test_mark_needs_ids_and_a_status(tmp_path: Path) -> None:
    f = tmp_path / "DEFERRED.md"
    f.write_text(REGISTER)
    assert run(f, "mark", "--status", "x").returncode == 2
    assert run(f, "mark", "p3c-capture-18").returncode == 2


def test_deferred_check_without_register_names_it(tmp_path: Path) -> None:
    """A public clone has neither the register nor the ledger tool: the missing register is what it names."""
    env = {**os.environ, "SDD_LEDGER": str(tmp_path / "no-ledger-tool")}
    argv = [sys.executable, "-I", "-B", str(SCRIPT), "check", "--file", str(tmp_path / "DEFERRED.md")]
    r = subprocess.run(argv, check=False, capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "DEFERRED.md not found (records are in the private repo)" in r.stderr + r.stdout
    assert "no ledger tool" not in r.stderr + r.stdout
