"""Measurements of upstream v3.08 behaviour on the real guest: filtered service mode, and issue #1207."""
import asyncio
import os
import time

import pytest
from support import SRC, real_ports
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(SRC / "scripts/stl/udp_for_benchmarks.py")


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@pytest.mark.timeout(60)
def test_filtered_service_mode_on_real_guest() -> None:
    async def run() -> None:
        s = TrexSession(lambda m: None)
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await s.acquire([0], force=True)
            try:
                await s.set_service([0], False, filtered=True, mask=1)
            except Exception as e:  # noqa: BLE001                      # the observation is the point
                print(f"FILTERED_RESULT=refused type={type(e).__name__} text={e!r}")
            else:
                v = s.ports()[0]
                print(f"FILTERED_RESULT=accepted service={v['service']} "
                      f"service_filtered={v['service_filtered']}")
            finally:
                try:
                    await s.set_service([0], False)
                except Exception as e:  # noqa: BLE001
                    print(f"FILTERED_RESET_FAILED {e!r}")
        finally:
            await s.close()
    asyncio.run(run())


@pytest.mark.timeout(120)
def test_stop_after_link_down_issue_1207() -> None:
    from trex_web_gui import trexapi

    async def run() -> None:
        s = TrexSession(lambda m: None)
        hung = False
        try:
            await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await s.acquire([0], force=True)
            lp = await trexapi.load_profile(PROFILE, [0], [])
            await s.assign_profile([0], lp)
            await s.start([0], "10kpps")
            await asyncio.sleep(1.0)
            print(f"1207 link before={s.ports()[0]['link_up']} state={s.ports()[0]['state']}")
            try:
                try:
                    await s.set_port_attr([0], link_up=False)
                except Exception as e:  # noqa: BLE001           # observed on the AF_PACKET guest: the NIC refuses it
                    print(f"1207 set_port_attr link_up=False REFUSED: {type(e).__name__} {e!r}")
                else:
                    await asyncio.sleep(1.0)
                    v = s.ports()[0]
                    print(f"1207 link after link_up=False: link_up={v['link_up']} state={v['state']}")
                t = time.monotonic()
                try:
                    await asyncio.wait_for(s.stop([0]), 15)
                    print(f"1207 stop returned in {time.monotonic() - t:.2f}s")
                except TimeoutError:
                    hung = True
                    print(f"1207 stop HUNG (>{time.monotonic() - t:.1f}s)")
                except Exception as e:  # noqa: BLE001
                    print(f"1207 stop raised after {time.monotonic() - t:.2f}s: {type(e).__name__} {e!r}")
                t = time.monotonic()
                try:
                    c = s.require_control(False)
                    await asyncio.wait_for(s._refresh_now(c), 10)
                    v = s.ports()[0]
                    print(f"1207 refresh answered in {time.monotonic() - t:.2f}s: "
                          f"link_up={v['link_up']} state={v['state']}")
                except Exception as e:  # noqa: BLE001
                    print(f"1207 refresh did NOT answer ({type(e).__name__} {e!r}) after "
                          f"{time.monotonic() - t:.1f}s")
            finally:
                try:
                    await asyncio.wait_for(s.set_port_attr([0], link_up=True), 10)
                    print(f"1207 link restored: link_up={s.ports()[0]['link_up']}")
                except Exception as e:  # noqa: BLE001
                    print(f"1207 restore link_up=True not applied: {type(e).__name__} {e!r}")
        finally:
            try:
                await asyncio.wait_for(s.close(), 20)
            except BaseException as e:  # noqa: BLE001
                print(f"1207 close problem: {type(e).__name__} {e!r}")
        if hung:
            pytest.xfail("stop hung after link down (#1207 reproduced)")
    asyncio.run(run())
