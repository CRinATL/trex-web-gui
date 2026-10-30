"""scripts/gate.py on a fake clock and fake steps: estimates, the status file, the history and the summary."""
import importlib.util
import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from support import ROOT

SCRIPT = ROOT / "scripts" / "gate.py"
spec = importlib.util.spec_from_file_location("gate", SCRIPT)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class Clock:
    """A clock that only moves when a fake step advances it."""
    def __init__(self) -> None:
        """Starts at an arbitrary fixed time."""
        self.t = 1_000_000.0

    def __call__(self) -> float:
        """The current fake time."""
        return self.t


def fake(clock: Clock, took: dict[str, float], rc: dict[str, int] | None = None,
         ran: list[str] | None = None) -> Callable[[str], int]:
    """An execute() that advances the clock by took[step], returns rc[step] (default 0) and appends the step
    to `ran`."""
    def execute(step: str) -> int:
        """One fake step."""
        if ran is not None:
            ran.append(step)
        clock.t += took[step]
        return (rc or {}).get(step, 0)
    return execute


def seed(monkeypatch: pytest.MonkeyPatch, **s: float) -> None:
    """Give fake steps a recorded figure, for this test only."""
    monkeypatch.setattr(gate, "SEED", {**gate.SEED, **s})


def test_fmt() -> None:
    assert [gate.fmt(s) for s in (5.84, 48, 462.85, 60)] == ["5.8s", "48s", "7m43s", "1m00s"]


def test_seed_then_median_of_the_last_five_passing_runs() -> None:
    assert gate.usual("test-py", []) == (65.0, 0)               # the recorded figure
    assert gate.usual("nothing-known", []) == (None, 0)
    passes = [{"step": "lint", "s": s, "rc": 0} for s in (100, 1, 2, 3, 4, 5)]
    recs = passes + [{"step": "lint", "s": 99, "rc": 2}]
    assert gate.usual("lint", recs) == (3.0, 5)      # the oldest pass and the failure are left out


def test_a_run_records_each_step_and_ends_with_the_rc_line(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    clock, out, ran = Clock(), io.StringIO(), []
    seed(monkeypatch, a=4, b=60)
    rc = gate.run_gate("g", ["a", "b"], execute=fake(clock, {"a": 5, "b": 50}, ran=ran), now=clock,
                       state=tmp_path, out=out)
    assert rc == 0 and ran == ["a", "b"]
    text = out.getvalue()
    assert "gate g: 2 steps, usually 1m04s" in text
    assert "[1/2] a: usually 4.0s (recorded figure)" in text and "[2/2] b: ok in 50s" in text
    assert text.rstrip().endswith("gate g rc=0 in 55s")
    hist = [json.loads(ln) for ln in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert [(h["step"], h["s"], h["rc"]) for h in hist] == [("a", 5.0, 0), ("b", 50.0, 0)]
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["done"] is True and status["rc"] == 0
    # the next run's estimate is the measured time, not the seed
    out2 = io.StringIO()
    gate.run_gate("g", ["a"], execute=fake(clock, {"a": 5}), now=clock, state=tmp_path, out=out2)
    assert "[1/1] a: usually 5.0s;" in out2.getvalue()


def test_a_failure_stops_the_gate_with_its_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock, out, ran = Clock(), io.StringIO(), []
    seed(monkeypatch, a=1, b=1, c=1)
    ex = fake(clock, {"a": 1, "b": 1, "c": 1}, rc={"b": 2}, ran=ran)
    assert gate.run_gate("g", ["a", "b", "c"], execute=ex, now=clock, state=tmp_path, out=out) == 2
    assert ran == ["a", "b"]
    assert "[2/3] b: FAILED rc=2" in out.getvalue()
    assert out.getvalue().rstrip().endswith("gate g rc=2 in 2.0s")


def test_status_line_while_running_and_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    assert gate.status_line(tmp_path, clock) == "no gate has run yet"
    seen: list[str] = []
    seed(monkeypatch, a=100, b=20)

    def execute(step: str) -> int:
        """30 s in, read the status as a person asking would; then finish the step."""
        clock.t += 30
        seen.append(gate.status_line(tmp_path, clock))
        clock.t += {"a": 70, "b": 0}[step]
        return 0
    gate.run_gate("g", ["a", "b"], execute=execute, now=clock, state=tmp_path, out=io.StringIO())
    assert seen[0].startswith("gate g: step 1/2 a, 30s of usually 1m40s; done ≈ ")
    assert "running past" not in seen[0]
    assert seen[1].startswith("gate g: step 2/2 b, 30s of usually 20s")
    assert seen[1].endswith("(running past its estimate)")
    after = gate.status_line(tmp_path, clock)
    assert after.startswith("gate g ended ") and ", rc=0, after 2m10s" in after


def test_every_gate_step_is_a_make_target() -> None:
    makefile = (ROOT / "Makefile").read_text()
    for steps in gate.GATES.values():
        for step in steps:
            target = "check-py-%" if step.startswith("check-py-") else step
            assert f"\n{target}:" in makefile, step


def test_ctrl_c_ends_the_gate_as_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review S5: Ctrl-C mid-step records the gate as ended with 130, prints the trailer, and status says
    so."""
    clock, out = Clock(), io.StringIO()
    seed(monkeypatch, a=1, b=1)

    def execute(step: str) -> int:
        """Step b is interrupted."""
        clock.t += 1
        if step == "b":
            raise KeyboardInterrupt
        return 0
    assert gate.run_gate("g", ["a", "b"], execute=execute, now=clock, state=tmp_path, out=out) == 130
    assert "interrupted during a" in out.getvalue()        # the last step that finished; b never recorded
    assert out.getvalue().rstrip().endswith("gate g rc=130 in 2.0s")
    assert json.loads((tmp_path / "status.json").read_text())["done"] is True
    assert ", rc=130, " in gate.status_line(tmp_path, clock)


def test_a_gate_whose_process_is_gone_reads_as_interrupted(tmp_path: Path) -> None:
    """Review S5: a status left `done: False` by a killed gate does not claim to be running."""
    gate.write_status(tmp_path / "status.json", {"gate": "check", "step": "test-py", "index": 3, "of": 7,
                                                  "pid": 4242, "started": 0, "step_started": 0,
                                                  "step_usual": 60, "eta": 100, "done": False})
    line = gate.status_line(tmp_path, Clock(), is_alive=lambda pid: False)
    assert line == "gate check was interrupted at step 3/7 test-py (pid 4242 is gone)"
    assert gate.status_line(tmp_path, Clock(), is_alive=lambda pid: True).startswith("gate check: step 3/7")


def test_history_lines_that_are_not_records_are_skipped(tmp_path: Path) -> None:
    """Review S6: valid JSON that is not a record, or a record with no numeric time, never fails a gate."""
    h = tmp_path / "history.jsonl"
    h.write_text("[]\n3\n\"x\"\nnot json\n" + json.dumps({"step": "lint", "s": "7", "rc": 0}) + "\n"
                 + json.dumps({"step": "lint", "s": True, "rc": 0}) + "\n"
                 + json.dumps({"step": "lint", "s": 12.5, "rc": 0}) + "\n")
    recs = gate.load(h)
    assert recs == [{"step": "lint", "s": 12.5, "rc": 0}]
    assert gate.usual("lint", recs) == (12.5, 1)


def test_a_signal_death_reads_as_one() -> None:
    """Review N4: subprocess's -9 is shown and exited as 137, named, so an OOM kill is recognisable."""
    assert gate.rc_text(-9) == "137 (killed by SIGKILL)" and gate.exit_code(-9) == 137
    assert gate.rc_text(2) == "2" and gate.exit_code(2) == 2
