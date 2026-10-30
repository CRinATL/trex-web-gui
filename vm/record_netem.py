"""Record how the session behaves under network delay (P3b follow-up 4), on the real v3.08 guest.
Usage (through vm/real.sh): TREX_NETEM_BEFORE=<4de803a worktree> make record-netem
For (D, J) in (0,0) (netem off, the default qdisc P3b was measured on), (50,10), (100,20), (200,40) ms it
sets the delay (vm/netem.sh), runs vm/record_p3b.py into /var/tmp/trex-netem/<stamp>/d<D>/p3b and keeps its
composites_secs, max_secs_by_call and slowest_call, then runs vm/measure_netem.py on TREX_NETEM_BEFORE/backend
and on this repo's backend. A failing step is recorded as {"error": ...} and the loop goes on; the delay is
always taken off in `finally`. Writes <out_dir>/summary.json (tests/fixtures/netem) and a UTC-stamped copy to
/var/tmp (canon:OUT-1).
Smoke-only environment: TREX_NETEM_DELAYS (comma list restricting the delays), TREX_NETEM_RUNS (--runs),
TREX_NETEM_OUT (replaces <out_dir>)."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DELAYS = [(0, 0), (50, 10), (100, 20), (200, 40)]


def summarize(p3b_summary: dict[str, Any], before: dict[str, Any], after: dict[str, Any], delay_ms: int,
              jitter_ms: int, qdisc: str = "") -> dict[str, Any]:
    """One summary row. An {"error": ...} step is carried through unchanged."""
    if "error" in p3b_summary:
        p3b = p3b_summary
    else:
        p3b = {k: p3b_summary[k] for k in ("composites_secs", "max_secs_by_call", "slowest_call")}
    return {"delay_ms": delay_ms, "jitter_ms": jitter_ms, "qdisc": qdisc, "p3b": p3b, "before": before,
            "after": after}


RECORD_TIMEOUT_S = 1800      # record_p3b.py and measure_netem.py
NETEM_TIMEOUT_S = 60         # netem.sh (one ssh round trip, two at most)


def step(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run argv; subprocess.TimeoutExpired (after `timeout` s) or OSError propagate to the caller."""
    print("[run] " + " ".join(argv), flush=True)
    r = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r


def error(what: str, r: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """The {"error": ...} row for a step that exited nonzero: exit code and the last 400 chars of output."""
    return {"error": f"{what} exited {r.returncode}: {(r.stderr or r.stdout).strip()[-400:]}"}


def failure(what: str, e: Exception) -> dict[str, str]:
    """The {"error": ...} row for a step that raised instead of exiting."""
    if isinstance(e, subprocess.TimeoutExpired):
        return {"error": f"{what} timed out after {e.timeout:g} s"}
    return {"error": f"{what}: {type(e).__name__}: {e}"}


def measure(backend: str, out: Path, runs: str | None) -> dict[str, Any]:
    """Run vm/measure_netem.py on `backend` into `out`; its JSON, or an {"error": ...} row if it fails."""
    argv = [sys.executable, "-I", "-B", str(HERE / "measure_netem.py"), backend, str(out)]
    if runs:
        argv += ["--runs", runs]
    try:
        r = step(argv, RECORD_TIMEOUT_S)
        return json.loads(out.read_text()) if r.returncode == 0 else error("measure_netem", r)
    except Exception as e:                               # noqa: BLE001 - recorded, the loop goes on
        return failure("measure_netem", e)


def qdisc_ok(qdisc: str, delay_ms: int, jitter_ms: int) -> bool:
    """True when the `tc qdisc show` text is the one (delay_ms, jitter_ms) asked for: no netem at delay 0,
    else netem with `delay <D>ms` followed by `<J>ms`. Assumed: iproute2 prints `delay 50ms  10ms` and omits
    a zero jitter (`delay 50ms`), so a missing jitter counts as 0 ms."""
    if delay_ms == 0:
        return "netem" not in qdisc
    m = re.search(rf"delay {delay_ms}ms(?:\s+(\d+(?:\.\d+)?)ms)?(?!\S)", qdisc)
    return "netem" in qdisc and m is not None and float(m.group(1) or 0) == jitter_ms


def record_delay(d: int, j: int, before_tree: str, work: Path, runs: str | None) -> dict[str, Any]:
    """One row. When the delay was not applied (netem.sh failed or shows another qdisc), p3b, before and after
    are all the same {"error": ...} and nothing is measured."""
    try:
        r = step([str(HERE / "netem.sh"), *(["off"] if d == 0 else [str(d), str(j)])], NETEM_TIMEOUT_S)
    except Exception as e:                               # noqa: BLE001 - recorded, the loop goes on
        err = failure("netem.sh", e)
        return summarize(err, err, err, d, j)
    qdisc = r.stdout.strip()
    if r.returncode != 0:
        err = error("netem.sh", r)
    elif not qdisc_ok(qdisc, d, j):
        err = {"error": f"qdisc does not match {d} ms / {j} ms: {qdisc!r}"}
    else:
        err = None
    if err is not None:
        return summarize(err, err, err, d, j, qdisc)
    (work / f"d{d}").mkdir(parents=True, exist_ok=True)
    p3b: dict[str, Any]
    try:
        p3b_dir = work / f"d{d}" / "p3b"
        r = step([sys.executable, "-I", "-B", str(HERE / "record_p3b.py"), str(p3b_dir)], RECORD_TIMEOUT_S)
        p3b = error("record_p3b", r) if r.returncode else json.loads((p3b_dir / "summary.json").read_text())
    except Exception as e:                               # noqa: BLE001 - recorded, the loop goes on
        p3b = failure("record_p3b", e)
    before = measure(str(Path(before_tree) / "backend"), work / f"d{d}" / "before.json", runs)
    after = measure(str(REPO / "backend"), work / f"d{d}" / "after.json", runs)
    return summarize(p3b, before, after, d, j, qdisc)


def main() -> int:
    """Record each delay (netem set on the guest, always taken off after); summary.json goes in two places."""
    before_tree = os.environ["TREX_NETEM_BEFORE"]
    out_dir = Path(os.environ.get("TREX_NETEM_OUT") or sys.argv[1])
    runs = os.environ.get("TREX_NETEM_RUNS")
    only = os.environ.get("TREX_NETEM_DELAYS")
    delays = [dj for dj in DELAYS if not only or str(dj[0]) in only.split(",")]
    stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
    work = Path("/var/tmp/trex-netem") / stamp
    rows: list[dict[str, Any]] = []
    try:
        for d, j in delays:
            rows.append(record_delay(d, j, before_tree, work, runs))
    finally:
        try:
            step([str(HERE / "netem.sh"), "off"], NETEM_TIMEOUT_S)
        except Exception as e:                           # noqa: BLE001 - the summary is still written
            print(f"[netem.sh off failed] {failure('netem.sh', e)['error']}", file=sys.stderr)
    summary = {"recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "delays": rows}
    text = json.dumps(summary, indent=2) + "\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(text)
    work.mkdir(parents=True, exist_ok=True)
    (work / "summary.json").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
