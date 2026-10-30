"""P3a exit criterion against the real v3.08 guest (make check-real): the session's `pgid` and `util` messages
agree with an independent client playing trex-console (connected and reading, never acquiring)."""
import asyncio
import itertools
import os
import time

import pytest
from support import FIXTURES, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(FIXTURES / "profiles/flow_latency.py")
SETTLE_S = 8.0              # rates and latency ramp for several seconds after a start
MAX_GAP_S = 0.25            # the oracle is read this soon after the session's messages


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@pytest.mark.timeout(120)
def test_session_pgid_matches_the_client_oracle_under_load() -> None:
    """Compare each session sample with the oracle read right after it, never with the nominal 1000/100 pps,
    and compare counter DELTAS over a common window (the two clients read absolute server counters, but the
    oracle is read a little later than the session)."""
    trexapi.load()
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    oracle.connect()                                     # reading only: no acquire, reset, start or stop

    async def run() -> None:
        pgids: list[dict] = []
        utils: list[dict] = []

        def publish(m: dict) -> None:
            if m.get("type") == "pgid":
                pgids.append(m)
            elif m.get("type") == "util":
                utils.append(m)

        s = TrexSession(publish)

        async def sample() -> tuple[dict, dict, dict, dict, float]:
            """Wait for the next pgid message and the util message after it, then read the oracle at once."""
            n, u = len(pgids), len(utils)
            deadline = time.monotonic() + 5.0
            while len(pgids) == n or len(utils) == u:
                assert time.monotonic() < deadline, "no pgid and util message within 5 s"
                await asyncio.sleep(0.005)
            msg, umsg = pgids[n], utils[u]
            seen = time.monotonic()
            # util first: the session's util read is the one the oracle must trail most closely
            uref = await asyncio.to_thread(oracle.get_util_stats)
            ref = await asyncio.to_thread(oracle.pgid_stats.get_stats, [5, 7], False)
            return msg, ref, umsg, uref, time.monotonic() - seen

        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await s.acquire([0], force=True)
            lp = await trexapi.load_profile(PROFILE, [0], [])
            await s.assign_profile([0], lp)
            await s.start([0], "1")
            await asyncio.sleep(SETTLE_S)

            rows = [await sample() for _ in range(6)]   # 6 samples: five 1 s windows between them
            gaps = [r[4] for r in rows]
            print(f"oracle_gap_s max={max(gaps):.4f} all={[round(g, 4) for g in gaps]}")
            assert max(gaps) <= MAX_GAP_S

            def flow(m: dict, key: str) -> float:
                return float(m["flow"]["5"][key]["total"])

            def lat(m: dict, key: str) -> float:
                return float(m["latency"]["7"]["latency"][key])

            def rlat(r: dict, key: str) -> float:
                return float(r["latency"][7]["latency"][key])

            def rflow(r: dict, key: str) -> float:
                return float(r["flow_stats"][5][key]["total"])

            for key in ("tx_pps", "rx_pps"):
                ref = [rflow(r, key) for _, r, _, _, _ in rows]
                one_sample = max(abs(b - a) for a, b in itertools.pairwise(ref))
                for i, (m, r, _, _, _) in enumerate(rows):
                    got, want = flow(m, key), rflow(r, key)
                    tol = max(0.05 * want, one_sample)
                    print(f"sample {i} {key} session={got:.1f} oracle={want:.1f} tol={tol:.1f}")
                    assert want > 0 and abs(got - want) <= tol

            for i in range(5):
                (m0, r0, _, _, g0), (m1, r1, _, _, g1) = rows[i], rows[i + 1]
                d_s = flow(m1, "tx_pkts") - flow(m0, "tx_pkts")
                d_o = rflow(r1, "tx_pkts") - rflow(r0, "tx_pkts")
                tol = max(0.05 * d_o, rflow(r1, "tx_pps") * (g0 + g1))
                print(f"window {i} dt={m1['ts'] - m0['ts']:.3f}s tx_pkts session_d={d_s:.0f} "
                      f"oracle_d={d_o:.0f} tol={tol:.0f}")
                assert d_o > 0 and abs(d_s - d_o) <= tol
                d_s = flow(m1, "rx_pkts") - flow(m0, "rx_pkts")
                d_o = rflow(r1, "rx_pkts") - rflow(r0, "rx_pkts")
                print(f"window {i} rx_pkts session_d={d_s:.0f} oracle_d={d_o:.0f} tol={tol:.0f}")
                assert d_o > 0 and abs(d_s - d_o) <= tol

            for key in ("average", "jitter"):
                ref = [rlat(r, key) for _, r, _, _, _ in rows]
                one_poll = max(abs(b - a) for a, b in itertools.pairwise(ref))
                for i, (m, r, _, _, _) in enumerate(rows):
                    got, want = lat(m, key), rlat(r, key)
                    print(f"sample {i} latency {key} session={got:.1f} oracle={want:.1f} "
                          f"one_poll={one_poll:.1f}")
                    assert abs(got - want) <= one_poll

            for i, (_, _, um, uref, _) in enumerate(rows):
                # The server appends one history entry about every 1 s on its own schedule, so the oracle,
                # read a few ms later, may be exactly one sample ahead: its list is then the session's
                # shifted by one.
                for core, (cm, cr) in enumerate(zip(um["cpu"], uref["cpu"], strict=True)):
                    got, want = cm["history"], cr["history"]
                    print(f"sample {i} core {core} util history[:3] session={got[:3]} oracle={want[:3]}")
                    assert got == want or got[:-1] == want[1:]
        finally:
            try:
                await s.stop([0])
            finally:
                await s.close()

    try:
        asyncio.run(run())
    finally:
        oracle.disconnect(stop_traffic=False, release_ports=False)
