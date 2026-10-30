"""Measure TrexSession against the real guest (the guest's RPC/PUB ports, 4501/4500 on jammy)
under whatever delay is on the NIC.
Usage: uv run --project backend python -I -B vm/measure_netem.py <backend_dir> <out_json> [--runs N]
    (N default 3)
<backend_dir> goes first on sys.path, so that tree's trex_web_gui is the one measured (P3b follow-up 4 runs it
on a 4de803a worktree and on this repo). Records {"backend": trex_web_gui.__file__, "commit": git rev-parse
HEAD of the tree, "connect_s": [...], "apply_fail": [{apply_s, max_gap_s, stop_s}],
"ping": [{ping_s, max_gap_s, stop_s}]}. apply_fail follows
tests/real/test_port_config_real.py::test_stats_flow_during_a_failing_apply (port 0 transmits, an UNRESOLVED
Apply on port 1, Stop 1.0 s in); ping is a 5-echo ping from port 1 under the same load and Stop. Each run
records the start config and puts it back with that file's restore_ports."""
import argparse
import asyncio
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guestenv

HOST = os.environ.get("TREX_REAL_HOST", "127.0.0.1")
SYNC_PORT, ASYNC_PORT = guestenv.ports()


def commit_of(backend_dir: str | Path) -> str:
    """The commit of the tree that holds `backend_dir` (a repo or a git worktree)."""
    tree = Path(backend_dir).resolve().parent
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def dirty_of(backend_dir: str | Path) -> bool:
    """True when the tree's backend/ has uncommitted changes, so `commit` alone would not
    identify what ran."""
    tree = Path(backend_dir).resolve().parent
    return bool(subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--", "backend"],
                               capture_output=True, text=True, check=True).stdout.strip())


def max_gap(arrivals: list[float], lo: float, hi: float) -> float | None:
    """Longest gap in seconds (3 places) between consecutive arrivals within [lo, hi]; None under two."""
    window = [t for t in arrivals if lo <= t <= hi]
    gaps = [b - a for a, b in itertools.pairwise(window)]
    return round(max(gaps), 3) if gaps else None


async def one_run(kind: str, oracle: Any, trexapi: Any, mod: Any, session_cls: Any, profile: str,
                  conn_params: Any) -> dict[str, Any]:
    """One timed run, kind "apply_fail" or "ping": force-acquire ports 0 and 1, transmit on 0, work on 1, Stop
    1.0 s in; the timing row. The ports are restored afterwards, or the session closed if connect raised."""
    arrivals: list[float] = []

    # the session's hub: records the arrival time of each stats message and drops the rest
    def publish(m: dict[str, Any]) -> None:
        if m.get("type") == "stats":
            arrivals.append(time.monotonic())

    start: dict[int, dict[str, Any]] = {}
    s = session_cls(publish, stats_s=1.0)
    stack = ""
    connected = False
    try:
        await s.connect(conn_params)
        connected = True
        for p in (0, 1):
            start[p] = await asyncio.to_thread(mod.read_port, oracle, p)
        stack = s.ports()[0]["info"]["stack"]
        await s.acquire([0, 1], force=True)
        lp = await trexapi.load_profile(profile, [0], [])
        await s.assign_profile([0], lp)
        await s.start([0], "1")
        await asyncio.sleep(2.0)
        t0 = time.monotonic()
        if kind == "apply_fail":
            work = asyncio.create_task(s.apply_layer(1, mod.UNRESOLVED))
        else:
            work = asyncio.create_task(s.ping(1, "1.1.1.1", 5, 64))
        await asyncio.sleep(1.0)
        done_at_stop = work.done()                       # recorded, not asserted
        t_stop = time.monotonic()
        await s.stop([0])
        stop_s = time.monotonic() - t_stop
        res = await work
        t1 = time.monotonic()
        await asyncio.sleep(1.2)
        row: dict[str, Any] = {f"{'apply' if kind == 'apply_fail' else 'ping'}_s": round(t1 - t0, 3),
                               "max_gap_s": max_gap(arrivals, t0 - 1.2, t1 + 1.2), "stop_s": round(stop_s, 3)}
        if kind == "apply_fail":
            row["apply_done_at_stop"] = done_at_stop
            row["arp_state"] = res.arp.state if res.arp is not None else None
        else:
            row["ping_done_at_stop"] = done_at_stop
        return row
    finally:
        if connected:
            await mod.restore_ports(s, start, stack)
        else:
            # connect raised: that exception must reach the caller
            try:
                await s.close()
            except Exception:                            # noqa: BLE001
                pass


async def connect_once(session_cls: Any, conn_params: Any) -> float:
    """Seconds one fresh session takes to connect (3 places); it is closed again afterwards."""
    s = session_cls(lambda m: None, stats_s=1.0)
    t = time.monotonic()
    await s.connect(conn_params)
    took = time.monotonic() - t
    await s.close()
    return round(took, 3)


def main() -> int:
    """CLI: measure the trex_web_gui under <backend_dir> on the guest; <out_json> is always written
    (finally)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("backend_dir")
    ap.add_argument("out_json")
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    backend = Path(a.backend_dir).resolve()
    repo = Path(__file__).resolve().parent.parent
    # backend under test first; this repo's tests/ only for the shared helpers (their imports resolve there)
    sys.path[:0] = [str(backend)]
    sys.path.extend([str(repo / "tests"), str(repo / "tests" / "real")])
    import trex_web_gui
    from trex_web_gui import trexapi
    from trex_web_gui.sessions import ConnectParams, TrexSession
    assert str(Path(trex_web_gui.__file__).resolve()).startswith(str(backend)), trex_web_gui.__file__
    import test_port_config_real as mod  # read_port, restore_ports, request_of, UNRESOLVED, PROFILE
    trexapi.load()
    from trex.stl.api import STLClient
    params = ConnectParams(HOST, SYNC_PORT, ASYNC_PORT, 3.0)
    out: dict[str, Any] = {"backend": trex_web_gui.__file__, "commit": commit_of(backend),
                           "dirty": dirty_of(backend), "connect_s": [],
                           "apply_fail": [], "ping": []}
    print(f"backend {out['backend']} commit {out['commit']}", flush=True)
    oracle = STLClient(username="oracle", server=HOST, sync_port=SYNC_PORT, async_port=ASYNC_PORT,
                       verbose_level="none")
    oracle.connect()
    try:
        for _ in range(a.runs):
            out["connect_s"].append(asyncio.run(connect_once(TrexSession, params)))
        for kind in ("apply_fail", "ping"):
            for _ in range(a.runs):
                out[kind].append(asyncio.run(
                    one_run(kind, oracle, trexapi, mod, TrexSession, mod.PROFILE, params)))
                print(f"[{kind}] {out[kind][-1]}", flush=True)
    finally:
        oracle.disconnect(stop_traffic=False, release_ports=False)
        Path(a.out_json).write_text(json.dumps(out, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
