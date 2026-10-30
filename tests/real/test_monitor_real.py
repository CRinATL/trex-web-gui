"""P5 exit criterion against the real v3.08 guest (make check-real, spec §8): under load the Monitor's
`global.m_tx_pps` (the PUB `trex-global` message) agrees with the TrexSession's `stats` global `tx_pps`."""
import asyncio
import os
import time

import pytest
from support import SRC, real_ports
from trex_web_gui import trexapi
from trex_web_gui.monitor.session import MonitorSession
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(SRC / "scripts/stl/udp_for_benchmarks.py")
SETTLE_MAX_S = 15.0
SETTLE_REL = 0.02
MAX_GAP_S = 1.0             # a Monitor sample and the session sample it is compared with
TOLERANCE = 0.02
SAMPLES = 5


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@pytest.mark.timeout(120)
def test_monitor_tx_pps_matches_the_session_under_load() -> None:
    """Rates ramp for several seconds after a start, so: settle on the session's own rate first, then
    compare each of SAMPLES Monitor samples with the session sample nearest in time (arrival times on one
    monotonic clock), never with the nominal 10 kpps."""
    trexapi.load()

    async def run() -> None:
        stats: list[tuple[float, float]] = []       # (arrival, global tx_pps)
        mons: list[tuple[float, float]] = []
        seen_ts: list[float] = []                   # the Monitor's trex-global stamps already taken

        def on_stats(m: dict) -> None:
            """Keep the arrival time and the global tx_pps of each session stats message."""
            if m.get("type") == "stats" and m["global"]["tx_pps"] is not None:
                stats.append((time.monotonic(), float(m["global"]["tx_pps"])))

        def on_monitor(m: dict) -> None:
            """Keep each new trex-global sample once: the Monitor republishes unchanged slots."""
            ts = m.get("known_ts", {}).get("trex-global")
            if m.get("type") == "monitor" and m.get("global") and ts is not None and ts not in seen_ts:
                seen_ts.append(ts)
                mons.append((time.monotonic(), float(m["global"]["m_tx_pps"])))

        s = TrexSession(on_stats)
        mon = MonitorSession(on_monitor)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await mon.connect(HOST, real_ports()[1])
            await s.acquire([0], force=True)
            lp = await trexapi.load_profile(PROFILE, [0], [])
            await s.assign_profile([0], lp)
            await s.start([0], "10kpps")

            t0 = time.monotonic()
            while True:                              # settle: two consecutive session samples within 2 %
                n = len(stats)
                while len(stats) == n:
                    assert time.monotonic() - t0 < SETTLE_MAX_S + 5, "no stats message"
                    await asyncio.sleep(0.01)
                if len(stats) >= 2 and stats[-2][1] > 0 \
                        and abs(stats[-1][1] - stats[-2][1]) < SETTLE_REL * stats[-2][1]:
                    break
                assert time.monotonic() - t0 < SETTLE_MAX_S, f"the session rate did not settle: {stats[-3:]}"
            print(f"settle_s={time.monotonic() - t0:.1f}")
            settled = time.monotonic()
            while len([m for m in mons if m[0] > settled]) < SAMPLES:
                assert time.monotonic() - settled < 30, f"only {len(mons)} Monitor samples"
                await asyncio.sleep(0.05)
            await asyncio.sleep(1.2)                 # a session sample after the last Monitor sample

            after = [m for m in mons if m[0] > settled][:SAMPLES]
            for i, (t_m, v_m) in enumerate(after):
                t_s, v_s = min(stats, key=lambda x: abs(x[0] - t_m))
                gap = abs(t_s - t_m)
                print(f"sample {i} monitor={v_m:.1f} session={v_s:.1f} gap_s={gap:.3f}")
                assert gap <= MAX_GAP_S
                assert v_s > 0 and abs(v_m - v_s) <= TOLERANCE * v_s
        finally:
            try:
                await s.stop([0])
            finally:
                await mon.close()
                await s.close()

    asyncio.run(run())
