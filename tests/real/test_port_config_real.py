"""P3b exit criterion against the real v3.08 guest (make check-real): the session's port configuration,
ping, scan and xstats agree with an independent client playing trex-console (connected and reading,
never acquiring)."""
import asyncio
import functools
import itertools
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from support import FIXTURES, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession
from trex_web_gui.sessions.port_config import LayerRequest

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(FIXTURES / "profiles/flow_latency.py")
LEGACY_REFUSAL = "IPv6 is not supported with current stack"      # recorded: tests/fixtures/p3b/summary.json
L3 = LayerRequest("L3", None, "1.1.1.1", "2.2.2.2", [], None)


def only_unresolved(end: dict[str, Any], start: dict[str, Any]) -> bool:
    """True when `end` differs from `start` only by an unresolved IPv4 destination (ether and ipv4 state)."""
    lc = end["layer_cfg"]
    if (lc.get("ether", {}).get("state"), lc.get("ipv4", {}).get("state")) != ("unconfigured", "unresolved"):
        return False
    fixed = {**end, "layer_cfg": {
        **lc, "ether": {**lc["ether"], "state": start["layer_cfg"]["ether"]["state"]},
        "ipv4": {**lc["ipv4"], "state": start["layer_cfg"]["ipv4"]["state"]}}}
    return fixed == start


def request_of(rec: dict[str, Any], legacy: bool) -> LayerRequest | None:
    """The LayerRequest that puts a port back into the config read_port recorded; None when it
    cannot be expressed."""
    cfg, vlan = rec["layer_cfg"], rec["vlan"]
    ipv4, ether, ipv6 = cfg.get("ipv4"), cfg.get("ether"), cfg.get("ipv6")
    v6 = None if legacy or ipv6 is None else (bool(ipv6.get("enabled")), ipv6.get("src") or None)
    if ipv4 and ipv4.get("state") != "none":
        return LayerRequest("L3", None, ipv4.get("src"), ipv4.get("dst"), vlan, v6)
    if ether and ether.get("state") == "configured":
        return LayerRequest("L2", ether["dst"], None, None, vlan, v6)
    return None                                  # never configured: there is no request that recreates that


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


def read_port(oracle: Any, p: int) -> dict[str, Any]:
    """What the server says about port p, as the independent client reads it."""
    oracle.ports[p].sync()
    attr = oracle.ports[p].get_ts_attr()
    return {"layer_cfg": attr["layer_cfg"], "vlan": list(attr["vlan"]["tags"]),
            "service": bool(oracle.ports[p].service_mode), "multicast": attr["multicast"]["enabled"],
            "rx_filter_mode": attr["rx_filter_mode"]}


async def restore_ports(s: TrexSession, start: dict[int, dict[str, Any]], stack: str) -> None:
    """Cleanup for a test's finally: stop port 0, put each port back to the config read_port recorded, drop
    port 0's streams, close the session. A failing step is printed and the rest still run."""
    legacy = stack == "legacy"
    steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [("stop", lambda: s.stop([0]))]
    for p, rec in start.items():                          # each port goes back to what it was recorded as
        req = request_of(rec, legacy)
        if req is not None:
            steps.append((f"apply {p}", functools.partial(s.apply_layer, p, req)))
        steps.append((f"service {p}", functools.partial(s.set_service, [p], rec["service"])))
    for label, step in steps:
        try:
            await step()
        except Exception as e:                            # noqa: BLE001 - cleanup must go on
            print(f"cleanup {label}: {e}")
    try:
        c = s.require_control()
        await s._run(lambda: c.remove_all_streams(ports=[0]))
    except Exception as e:                                # noqa: BLE001
        print(f"cleanup streams: {e}")
    await s.close()


@pytest.mark.timeout(180)
def test_session_port_config_matches_the_client_oracle() -> None:
    trexapi.load()
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    oracle.connect()                                     # reading only: no acquire, reset, start or stop
    start: dict[int, dict[str, Any]] = {}

    async def run() -> None:
        s = TrexSession(lambda m: None)

        async def apply_and_compare(label: str, req: LayerRequest) -> None:
            res = await s.apply_layer(0, req)
            assert res.service_restore_error is None, (label, res)
            truth = await asyncio.to_thread(read_port, oracle, 0)
            print(f"{label}: oracle layer_cfg={truth['layer_cfg']} vlan={truth['vlan']} "
                  f"service={truth['service']}")
            assert s.ports()[0]["layer_cfg"] == truth["layer_cfg"], label
            assert s.ports()[0]["vlan"] == truth["vlan"] == req.vlan, label
            assert truth["service"] is start[0]["service"], label            # service mode restored

        stack = ""                           # read once connected; the cleanup below needs it either way
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            for p in (0, 1):
                start[p] = await asyncio.to_thread(read_port, oracle, p)
            print(f"starting config: {start}")
            hw1 = s.ports()[1]["info"]["hw_mac"]
            stack = s.ports()[0]["info"]["stack"]
            await s.acquire([0, 1], force=True)

            await apply_and_compare("L2", LayerRequest("L2", hw1, None, None, [], None))
            l2 = await asyncio.to_thread(read_port, oracle, 0)
            # the oracle sees the L2 destination
            assert l2["layer_cfg"]["ether"]["dst"].lower() == hw1.lower()
            await apply_and_compare("L3", L3)
            truth = await asyncio.to_thread(read_port, oracle, 0)
            assert truth["layer_cfg"]["ipv4"]["state"] == "resolved"
            assert truth["layer_cfg"]["ether"]["dst"].lower() == hw1.lower()   # resolved to port 1's hw_mac
            await apply_and_compare("VLAN 10", LayerRequest("L3", None, "1.1.1.1", "2.2.2.2", [10], None))
            await apply_and_compare("VLAN none", L3)
            if stack == "legacy":
                # the GUI never sends conf_ipv6 enabled on legacy; a raw one shows the server's refusal
                with pytest.raises(trexapi.CommandRefused) as gui:
                    await s.apply_layer(0, LayerRequest("L3", None, "1.1.1.1", "2.2.2.2", [], (True, None)))
                assert gui.value.code == "ipv6_unsupported"
                print(f"GUI refusal: {gui.value}")
                c = s.require_control()
                await s.set_service([0], True)
                try:
                    with pytest.raises(Exception, match=LEGACY_REFUSAL) as raw:
                        await s._run(lambda: trexapi.conf_ipv6(c, 0, True, None))
                    print(f"raw conf_ipv6 refusal: {raw.value}")
                finally:
                    await s.set_service([0], False)
            else:
                await apply_and_compare("IPv6 auto",
                                        LayerRequest("L3", None, "1.1.1.1", "2.2.2.2", [], (True, None)))
                await apply_and_compare("IPv6 off",
                                        LayerRequest("L3", None, "1.1.1.1", "2.2.2.2", [], (False, None)))

            ping = await s.ping(0, "2.2.2.2", 5, 64)
            print("ping: " + " | ".join(f"{r.status} {r.text}" for r in ping.lines))
            assert ping.service_restore_error is None
            assert [r.status for r in ping.lines] == ["reply"] * 5
            scan = await s.scan(0)
            print(f"scan: {scan.neighbours}")
            assert scan.service_restore_error is None
            assert hw1.lower() in {n.mac.lower() for n in scan.neighbours}

            async def tx_pair() -> tuple[int, int]:
                x = await s.xstats(0)
                st = await asyncio.to_thread(oracle.get_stats, ports=[0])
                return x.values["tx_good_packets"], st[0]["opackets"]
            lp = await trexapi.load_profile(PROFILE, [0], [])
            await s.assign_profile([0], lp)
            x0, o0 = await tx_pair()
            await s.start([0], "1")
            await asyncio.sleep(2.0)
            await s.stop([0])
            await asyncio.sleep(0.5)
            x1, o1 = await tx_pair()
            print(f"xstats tx_good_packets delta={x1 - x0} oracle opackets delta={o1 - o0}")
            assert o1 - o0 > 0 and x1 - x0 == o1 - o0
        finally:
            await restore_ports(s, start, stack)

    try:
        asyncio.run(run())
        linux = oracle.ports[0].info.get("stack") == "linux_based"
        unresolved_only: list[int] = []
        for p in (0, 1):
            end = read_port(oracle, p)
            print(f"ending config port {p}: {end}")
            if linux and end != start[p] and only_unresolved(end, start[p]):
                unresolved_only.append(p)
                continue
            assert end == start[p], f"port {p} is not back in its starting config"
        if unresolved_only:
            # Recorded limit of the linux_based stack (tests/fixtures/p3b-v6/summary.json,
            # port1_resolves_after_vlan_cycle): once a VLAN is set and cleared on port 0, port 0 stops
            # answering ARP until TRex restarts, so the cleanup cannot re-resolve the other port. Everything
            # else above passed.
            pytest.xfail(f"linux_based: ports {unresolved_only} end unresolved after the VLAN step "
                         "(recorded limit)")
    finally:
        oracle.disconnect(stop_traffic=False, release_ports=False)


# 1.1.1.99 does not exist: the Apply takes about 3.2 s (3.196 s measured 2026-10-31; the resolve is six
# 0.5 s attempts)
UNRESOLVED = LayerRequest("L3", None, "2.2.2.2", "1.1.1.99", [], None)
MAX_STATS_GAP_S = 2.0
MAX_STOP_S = 3.2


@pytest.mark.timeout(180)
def test_stats_flow_during_a_failing_apply() -> None:
    """P3b follow-up 3: port 0 transmits while port 1's Apply, mostly a failing resolve on the control client,
    takes about 3.2 s (3.196 s measured 2026-10-31). The reader client keeps `stats` flowing (before, the
    largest gap was 3.05-3.16 s) and Stop, issued 1.0 s into the Apply, still returns within MAX_STOP_S."""
    trexapi.load()
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    oracle.connect()                          # reading only: used to record and compare the end config
    start: dict[int, dict[str, Any]] = {}

    async def run() -> None:
        arrivals: list[float] = []

        def publish(m: dict[str, Any]) -> None:
            if m.get("type") == "stats":
                arrivals.append(time.monotonic())

        s = TrexSession(publish)
        stack = ""
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            for p in (0, 1):
                start[p] = await asyncio.to_thread(read_port, oracle, p)
            stack = s.ports()[0]["info"]["stack"]
            await s.acquire([0, 1], force=True)
            lp = await trexapi.load_profile(PROFILE, [0], [])
            await s.assign_profile([0], lp)
            await s.start([0], "1")
            await asyncio.sleep(2.0)
            assert arrivals, "no stats message before the Apply"

            t0 = time.monotonic()
            apply = asyncio.create_task(s.apply_layer(1, UNRESOLVED))
            await asyncio.sleep(1.0)                     # inside the resolve
            assert not apply.done(), "the Apply had already finished when Stop was sent"
            t_stop = time.monotonic()
            await s.stop([0])
            stop_s = time.monotonic() - t_stop
            res = await apply              # the resolve fails; the Apply reports it, it does not raise
            assert res.arp is not None and res.arp.state == "unresolved", res
            t1 = time.monotonic()
            await asyncio.sleep(1.2)                     # one more tick, so a late last message is counted
            window = [t for t in arrivals if t0 - 1.2 <= t <= t1 + 1.2]
            assert len(window) >= 2, f"{len(window)} stats message(s) around the Apply"
            gaps = [b - a for a, b in itertools.pairwise(window)]
            print(f"apply took {t1 - t0:.3f} s, result={res}")
            print(f"stats max gap during the Apply: {max(gaps):.3f} s (n={len(window)}, "
                  f"gaps={[round(g, 3) for g in gaps]})")
            print(f"Stop issued 1.0 s into the Apply returned in {stop_s:.3f} s")
            assert max(gaps) < MAX_STATS_GAP_S
            assert stop_s < MAX_STOP_S
        finally:
            await restore_ports(s, start, stack)

    try:
        asyncio.run(run())
    finally:
        oracle.disconnect(stop_traffic=False, release_ports=False)
