"""P3d exit criterion against the real v3.08 guest (make check-real): a setup saved through the store, with
both ports' layers inside it, runs in the phased order (acquire, layers, assign, start) and the session's
`stats` agree with an independent client playing trex-console (connected and reading, never acquiring)."""
import asyncio
import itertools
import os
import time
from pathlib import Path
from typing import Any, cast

import pytest
from support import SRC, real_ports
from test_port_config_real import read_port, restore_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession
from trex_web_gui.sessions.port_config import LayerRequest
from trex_web_gui.store import test_setups

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(SRC / "scripts/stl/udp_for_benchmarks.py")
SETTLE_S = 5.8              # rates ramp for several seconds after a start (tests/real/test_stats_real.py)
TOLERANCE = 0.05
SAMPLES = 5


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


def two_port_setup() -> test_setups.TestSetup:
    """Port 0 transmits and port 1 only answers: each port's L3 gateway is the other's address, and both
    layers are in the setup so the phased Run applies them both before it assigns or starts anything."""
    def layer(src: str, dst: str) -> dict[str, Any]:
        """An L3 layer body without VLAN."""
        return {"mode": "L3", "src_ipv4": src, "dst_ipv4": dst, "vlan": []}
    tx = {"key": "tx", "port": 0, "profile": {"path": PROFILE, "kind": "py"},
          "tunables": {"values": {"packet_len": 128}, "extra": ""}, "mult": "10kpps", "duration": -1,
          "force": False, "acquire_force": False, "stop_first": False,
          "layer": layer("1.1.1.1", "2.2.2.2")}
    # port 1 has the same profile and rate fields; this test assigns and starts port 0 only (a browser Run
    # would assign and start port 1 too)
    rx = {**tx, "key": "rx", "port": 1, "layer": layer("2.2.2.2", "1.1.1.1")}
    return cast(test_setups.TestSetup, {"name": "P3d real", "slug": "", "created": "", "updated": "",
                                        "server": f"{HOST}:{real_ports()[0]}",
                                        "ports": [tx, rx], "dashboard": None})


@pytest.mark.timeout(180)
def test_saved_setup_runs_and_matches_the_oracle(tmp_path: Path) -> None:
    """The stored setup's fields (not literals in this test) drive the Run: acquire both ports, apply both
    layers, assign port 0, start port 0. After the settle the session's global tx_pps agrees with the
    oracle's within max(TOLERANCE, the one-sample swing) in SAMPLES of SAMPLES samples, and the oracle sees
    port 0's IPv4 resolved."""
    trexapi.load()
    from trex.stl.api import STLClient
    store = test_setups.TestSetupStore(tmp_path)
    store.save(two_port_setup())
    setup = store.get("p3d-real")
    by_port = {p["port"]: p for p in setup["ports"]}
    assert by_port[0]["layer"] is not None and by_port[1]["layer"] is not None
    from trex.common.trex_exceptions import TRexError
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    # Reading only: no acquire, reset, start or stop. The connect is retried: right after
    # test_stats_flow_during_a_failing_apply it once got no RPC reply ("Failed to get server response", 1 of 5
    # make check-real runs on 2026-10-21); the next test's oracle connected at once.
    for attempt in range(3):
        try:
            oracle.connect()
            break
        except TRexError:
            if attempt == 2:
                raise
            time.sleep(2.0)
    start: dict[int, dict[str, Any]] = {}

    async def run() -> None:
        stats: list[dict[str, Any]] = []
        s = TrexSession(lambda m: stats.append(m) if m.get("type") == "stats" else None)
        stack = ""
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            for p in (0, 1):
                start[p] = await asyncio.to_thread(read_port, oracle, p)
            stack = s.ports()[0]["info"]["stack"]

            # phase 1: acquire every port (acquire_force from the setup, false)
            await s.acquire(sorted(by_port), force=any(p["acquire_force"] for p in setup["ports"]))
            # phase 3: apply every layer before any assign
            for pid, sp in sorted(by_port.items()):
                lay = sp["layer"]
                assert lay is not None
                res = await s.apply_layer(pid, LayerRequest(lay["mode"], lay.get("dst_mac"),
                                                            lay.get("src_ipv4"), lay.get("dst_ipv4"),
                                                            lay["vlan"], None))
                assert res.service_restore_error is None, (pid, res)
                print(f"apply layer port {pid}: arp={res.arp}")
            # phase 4: assign port 0's profile with the stored tunables
            sp0 = by_port[0]
            probe = await trexapi.load_profile(sp0["profile"]["path"], [0], [])
            args: list[str] = []
            for spec in probe.tunables:
                v = sp0["tunables"]["values"].get(spec["dest"])
                if v is not None and str(v) != str(spec["default"]):
                    args += [spec["flag"], str(v)]
            assert args, "packet_len must reach the profile as a non-default tunable"
            lp = await trexapi.load_profile(sp0["profile"]["path"], [0], args)
            await s.assign_profile([0], lp, stop_first=sp0["stop_first"])
            # phase 5: start port 0 only
            await s.start([0], sp0["mult"], sp0["duration"], sp0["force"])

            truth = await asyncio.to_thread(read_port, oracle, 0)
            print(f"oracle port 0 layer_cfg={truth['layer_cfg']}")
            assert truth["layer_cfg"]["ipv4"]["state"] == "resolved"
            await asyncio.sleep(SETTLE_S)

            rows: list[tuple[float | None, float]] = []
            for _ in range(SAMPLES + 1):                     # one extra sample: SAMPLES windows between them
                n = len(stats)
                deadline = time.monotonic() + 5.0
                while len(stats) == n:
                    assert time.monotonic() < deadline, "no stats message within 5 s"
                    await asyncio.sleep(0.005)
                ref = await asyncio.to_thread(oracle.get_stats, ports=sorted(oracle.ports))
                rows.append((stats[n]["global"]["tx_pps"], ref["global"]["tx_pps"]))
            # test_stats_real.py's tolerance: 5 % of the oracle or its one-sample swing, whichever is larger
            one_sample = max(abs(b - a) for (_, a), (_, b) in itertools.pairwise(rows))
            print(f"one_sample_swing_pps={one_sample:.1f}")
            ok = 0
            for i, (got, want) in enumerate(rows[:SAMPLES]):
                tol = max(TOLERANCE * want, one_sample)
                print(f"sample {i} session={got} oracle={want:.1f} tol={tol:.1f}")
                if got is not None and want > 0 and abs(got - want) <= tol:
                    ok += 1
            assert ok == SAMPLES, f"{ok} of {SAMPLES} samples within max({TOLERANCE:.0%}, one-sample swing)"
        finally:
            await restore_ports(s, start, stack)

    try:
        asyncio.run(run())
    finally:
        oracle.disconnect(stop_traffic=False, release_ports=False)
