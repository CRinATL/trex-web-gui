"""P3-min exit criterion against the real v3.08 guest (make check-real): the session's `stats` message
agrees with an independent client playing trex-console (connected and reading, never acquiring)."""
import asyncio
import itertools
import os
import time

import pytest
from support import SRC, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(SRC / "scripts/stl/udp_for_benchmarks.py")
MAX_GAP_S = 0.05            # brief: the oracle reads within 50 ms of the session's message
SETTLE_MAX_S = 10.0
SETTLE_REL = 0.02


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@pytest.mark.timeout(120)
def test_session_stats_match_the_client_oracle_under_load() -> None:
    """Rates are server-computed and ramp for several seconds after a start, so: settle first, compare each
    session sample with the oracle read at the same instant, never with the nominal 10 kpps, and compare
    counter DELTAS over a common window (the two clients have different counter baselines)."""
    trexapi.load()
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    oracle.connect()                                     # reading only: no acquire, reset, start or stop

    async def run() -> None:
        stats: list[dict] = []
        s = TrexSession(lambda m: stats.append(m) if m.get("type") == "stats" else None)

        async def sample() -> tuple[dict, dict, float]:
            """Wait for the next session stats message, then read the oracle at once."""
            n = len(stats)
            deadline = time.monotonic() + 5.0
            while len(stats) == n:
                assert time.monotonic() < deadline, "no stats message within 5 s"
                await asyncio.sleep(0.005)
            msg = stats[n]
            seen = time.monotonic()
            ref = await asyncio.to_thread(oracle.get_stats, ports=sorted(oracle.ports))
            return msg, ref, time.monotonic() - seen

        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await s.acquire([0], force=True)
            lp = await trexapi.load_profile(PROFILE, [0], [])
            await s.assign_profile([0], lp)
            await s.start([0], "10kpps")

            prev = -1.0
            settle_t = time.monotonic()
            while True:
                msg, _, _ = await sample()
                cur = msg["global"]["tx_pps"] or 0.0
                if cur > 0 and prev > 0 and abs(cur - prev) < SETTLE_REL * prev:
                    break
                prev = cur
                if time.monotonic() - settle_t > SETTLE_MAX_S:
                    break
            settle_s = time.monotonic() - settle_t
            print(f"settle_s={settle_s:.1f}")

            rows = []
            for _ in range(6):                           # 6 samples: five 1 s windows between them
                rows.append(await sample())
            gaps = [g for _, _, g in rows]
            print(f"oracle_gap_s max={max(gaps):.4f} all={[round(g, 4) for g in gaps]}")
            assert max(gaps) <= MAX_GAP_S

            ref_g = [r["global"]["tx_pps"] for _, r, _ in rows]
            one_sample = max(abs(b - a) for a, b in itertools.pairwise(ref_g))
            print(f"one_sample_swing_pps={one_sample:.1f}")
            for i, (msg, ref, _) in enumerate(rows[:5]):
                for label, got, want in (("global", msg["global"]["tx_pps"], ref["global"]["tx_pps"]),
                                         ("port0", msg["ports"]["0"]["tx_pps"], ref[0]["tx_pps"])):
                    tol = max(0.05 * want, one_sample)
                    print(f"sample {i} {label} session={got:.1f} oracle={want:.1f} tol={tol:.1f}")
                    assert got is not None and abs(got - want) <= tol
            for i in range(5):
                (m0, r0, g0), (m1, r1, g1) = rows[i], rows[i + 1]
                assert m1["interval_s"] == 1.0
                assert 0.9 <= m1["ts"] - m0["ts"] <= 1.2
                d_s = m1["ports"]["0"]["opackets"] - m0["ports"]["0"]["opackets"]
                d_o = r1[0]["opackets"] - r0[0]["opackets"]
                # the oracle is read up to `gap` after each message; at the line rate that is
                # rate * gap packets
                skew = r1[0]["tx_pps"] * (g0 + g1)
                tol = max(0.05 * d_o, skew)
                print(f"window {i} dt={m1['ts'] - m0['ts']:.3f}s session_d={d_s:.0f} "
                      f"oracle_d={d_o:.0f} tol={tol:.0f}")
                assert d_o > 0 and abs(d_s - d_o) <= tol
        finally:
            try:
                await s.stop([0])
            finally:
                await s.close()

    try:
        asyncio.run(run())
    finally:
        oracle.disconnect(stop_traffic=False, release_ports=False)
