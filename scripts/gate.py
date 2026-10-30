"""Run a gate's make targets one at a time, saying how long each usually takes and when the gate should end.

  gate.py run GATE   run GATE's steps in order (`make <step>`), stop at the first failure, exit with its code
  gate.py status     one line: the step running now, how long it has run against its usual time, the expected
                     end; or how the last gate ended
  gate.py times      each step's usual time (the median of its last 5 passing runs) and the runs behind it

Each step's time is appended to .superpowers/gate/history.jsonl; until a step has a passing run, its usual
time is the recorded measurement in SEED. .superpowers/gate/status.json is rewritten at every step, so a
reader (a person, or an agent asked "how far along is it?") reads the state instead of guessing it. A run's
last line is `gate <GATE> rc=<N> in <time>`, the line to read in a log."""
import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".superpowers" / "gate"
GATES = {
    "check": ["lint", "typecheck", "test-py", "test-js", "build", "citations", "docstrings"],
    "check-py": ["check-py-3.12", "check-py-3.13", "check-py-3.14"],
    "install-smoke": ["install-smoke-run"],
}
# Seconds, measured on this laptop 2026-10-26 (these numbers are the record): used until a step has
# passing runs of its own. test-py is the 12-worker figure of 2026-10-28.
SEED = {"lint": 17, "typecheck": 27, "test-py": 65, "test-js": 48, "build": 9, "citations": 2,
        "docstrings": 1, "check-py-3.12": 100, "check-py-3.13": 100, "check-py-3.14": 100}
RECENT = 5


def fmt(s: float) -> str:
    """Seconds as 5.8s, 48s or 7m42s."""
    if s < 10:
        return f"{s:.1f}s"
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(round(s), 60)
    return f"{m}m{sec:02d}s"


def load(history: Path) -> list[dict[str, Any]]:
    """The history's records, oldest first; none when the file is missing. A line that is not JSON is
    skipped."""
    if not history.exists():
        return []
    out = []
    for line in history.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # a hand edit or a torn write can leave valid JSON that is not a record: skip it, never fail the gate
        took = rec.get("s") if isinstance(rec, dict) else None
        if isinstance(took, (int, float)) and not isinstance(took, bool):
            out.append(rec)
    return out


def usual(step: str, records: list[dict[str, Any]]) -> tuple[float | None, int]:
    """(the median of the step's last RECENT passing runs, how many runs that is); the SEED figure with 0
    runs before it has any; (None, 0) for a step with neither."""
    runs = [r["s"] for r in records if r.get("step") == step and r.get("rc") == 0][-RECENT:]
    if runs:
        return statistics.median(runs), len(runs)
    seed = SEED.get(step)
    return (float(seed), 0) if seed is not None else (None, 0)


def remaining(steps: list[str], records: list[dict[str, Any]]) -> float | None:
    """The usual time of `steps` together; None when any of them has no estimate."""
    total = 0.0
    for s in steps:
        u, _ = usual(s, records)
        if u is None:
            return None
        total += u
    return total


def clock(t: float) -> str:
    """A wall-clock time, local, HH:MM."""
    return datetime.fromtimestamp(t).strftime("%H:%M")


def write_status(path: Path, data: dict[str, Any]) -> None:
    """Replace the status file in one rename, so a reader never sees half of it. The temp file is this
    process's own, so two gates at once never rename each other's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data) + "\n")
    os.replace(tmp, path)


def rc_text(rc: int) -> str:
    """An exit code as the trailer shows it: a signal death (negative from subprocess) as 128 + signal, named,
    so an OOM kill under the cgroup cap reads as one."""
    if rc < 0:
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = f"signal {-rc}"
        return f"{128 - rc} (killed by {name})"
    return str(rc)


def exit_code(rc: int) -> int:
    """The code to exit with: 128 + signal for a signal death, else rc."""
    return 128 - rc if rc < 0 else rc


def run_gate(gate: str, steps: list[str], *, execute: Callable[[str], int],
             now: Callable[[], float] = time.time, state: Path = STATE, out: TextIO = sys.stdout) -> int:
    """Run `steps` in order through `execute` (a step name -> its exit code), printing each step's usual
    time and the expected end, recording each step's time, and keeping the status file current. Stops at
    the first failure and returns its code; 0 when every step passed."""
    history, status = state / "history.jsonl", state / "status.json"
    records = load(history)
    start = now()
    rest = remaining(steps, records)
    head = f"gate {gate}: {len(steps)} steps"
    if rest is not None:
        head += f", usually {fmt(rest)}; done ≈ {clock(start + rest)}"
    print(head, file=out, flush=True)
    times: list[tuple[str, float, float | None]] = []
    rc = 0
    try:
        rc = _steps(gate, steps, records, start, execute, now, history, status, times, out)
    except KeyboardInterrupt:
        rc = 130                    # Ctrl-C: record the gate as ended, not as running forever
        print(f"\ninterrupted during {times[-1][0] if times else steps[0]}", file=out, flush=True)
    total = now() - start
    last = times[-1][0] if times else steps[0]
    write_status(status, {"gate": gate, "step": last, "index": max(len(times), 1), "of": len(steps),
                          "pid": os.getpid(), "started": start, "ended": start + total, "rc": rc,
                          "done": True})
    print(f"\n{'step':<16}{'took':>9}{'usual':>9}", file=out)
    for step, took, u in times:
        print(f"{step:<16}{fmt(took):>9}{fmt(u) if u is not None else '-':>9}", file=out)
    print(f"gate {gate} rc={rc_text(rc)} in {fmt(total)}", file=out, flush=True)
    return exit_code(rc)


def _steps(gate: str, steps: list[str], records: list[dict[str, Any]], start: float,
           execute: Callable[[str], int], now: Callable[[], float], history: Path, status: Path,
           times: list[tuple[str, float, float | None]], out: TextIO) -> int:
    """The step loop of run_gate: each step's line, status, run, history record and result; `times` gets
    each finished step. Returns the first failing step's code, or 0."""
    rc = 0
    for i, step in enumerate(steps, 1):
        u, n = usual(step, records)
        left = remaining(steps[i - 1:], records)
        t0 = now()
        eta = t0 + left if left is not None else None
        write_status(status, {"gate": gate, "step": step, "index": i, "of": len(steps), "pid": os.getpid(),
                              "started": start, "step_started": t0, "step_usual": u, "eta": eta,
                              "done": False})
        if u is None:
            said = "no estimate yet"
        else:
            said = f"usually {fmt(u)}" + ("" if n else " (recorded figure)")
        if eta is not None:
            said += f"; gate done ≈ {clock(eta)}"
        print(f"\n[{i}/{len(steps)}] {step}: {said}", file=out, flush=True)
        rc = execute(step)
        took = now() - t0
        times.append((step, took, u))
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a") as f:
            f.write(json.dumps({"gate": gate, "step": step, "s": round(took, 1), "rc": rc,
                                "at": round(t0)}) + "\n")
        result = "ok" if rc == 0 else f"FAILED rc={rc_text(rc)}"
        print(f"[{i}/{len(steps)}] {step}: {result} in {fmt(took)}", file=out, flush=True)
        if rc != 0:
            break
    return rc


def alive(pid: int) -> bool:
    """Whether a process with this pid exists (one owned by another user counts as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def status_line(state: Path = STATE, now: Callable[[], float] = time.time,
                is_alive: Callable[[int], bool] = alive) -> str:
    """The status file as one line: what runs now and the expected end, or how the last gate ended. A gate
    whose process is gone without saying so (killed, or the machine went down) reads as interrupted."""
    path = state / "status.json"
    if not path.exists():
        return "no gate has run yet"
    s = json.loads(path.read_text())
    if not s["done"] and not is_alive(s["pid"]):
        return (f"gate {s['gate']} was interrupted at step {s['index']}/{s['of']} {s['step']} "
                f"(pid {s['pid']} is gone)")
    if s["done"]:
        took = fmt(s["ended"] - s["started"])
        return f"gate {s['gate']} ended {clock(s['ended'])}, rc={rc_text(s['rc'])}, after {took}"
    line = f"gate {s['gate']}: step {s['index']}/{s['of']} {s['step']}, {fmt(now() - s['step_started'])}"
    if s.get("step_usual") is not None:
        line += f" of usually {fmt(s['step_usual'])}"
    if s.get("eta") is not None:
        line += f"; done ≈ {clock(s['eta'])}"
        if now() > s["eta"]:
            line += " (running past its estimate)"
    return line


def make(step: str) -> int:
    """Run one step as `make <step>` in the repo, its output going straight through. Variables given on
    the outer make's command line (VENDOR_TESTS=run) reach it through MAKEFLAGS."""
    return subprocess.run(["make", "--no-print-directory", step], cwd=ROOT).returncode


def main(argv: list[str] | None = None) -> int:
    """The command line: run, status or times."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("gate", choices=sorted(GATES))
    sub.add_parser("status")
    sub.add_parser("times")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run_gate(a.gate, GATES[a.gate], execute=make)
    if a.cmd == "status":
        print(status_line())
        return 0
    records = load(STATE / "history.jsonl")
    for gate, steps in GATES.items():
        for step in steps:
            u, n = usual(step, records)
            basis = f"{n} run{'s' if n != 1 else ''}" if n else "recorded figure" if u is not None else "-"
            print(f"{gate:<14}{step:<18}{fmt(u) if u is not None else '-':>9}  {basis}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
