"""P5 stateful Monitor against the real v3.08 guest (make check-daemon-real, marker restarts_trex, D8): TRex
restarted on `-f cap2/sfr2.yaml` (vm/mode.sh stateful) publishes its template names and a latency histogram
per port. Skips when the recorder found that stateful did not run on this guest."""
import asyncio
import json
import os
import time
from collections.abc import Iterator

import pytest
from daemon_vm import TEMPLATES, sh
from support import FIXTURES, real_ports
from trex_web_gui.monitor.session import MonitorSession

pytestmark = [pytest.mark.real, pytest.mark.restarts_trex]
HOST = os.environ.get("TREX_REAL_HOST", "")


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it), or when the
    P5 recording says stateful did not run on this guest."""
    if not HOST:
        pytest.skip("run via make check-daemon-real")
    real_ports()
    if not json.loads((FIXTURES / "p5" / "summary.json").read_text())["stateful"]:
        pytest.skip("stateful did not run on this guest (summary.stateful false)")


@pytest.fixture
def stateful_guest() -> Iterator[None]:
    """TRex on the stateful stack (vm/mode.sh stateful); vm/mode.sh stateless after."""
    try:
        sh("vm/mode.sh", "stateful")
        yield
    finally:
        sh("vm/mode.sh", "stateless")


def latency_ports_ready(snap: dict) -> bool:
    """True when the Monitor holds latency_v2 with at least one port-N whose hist.cnt is positive on every
    port-N it lists."""
    lat = snap.get("latency_v2")
    ports = {k: v for k, v in lat.items() if k.startswith("port-")} if isinstance(lat, dict) else {}
    return bool(ports) and all(isinstance(v, dict) and v.get("hist", {}).get("cnt", 0) > 0
                               for v in ports.values())


@pytest.mark.timeout(180)
def test_stateful_monitor_templates_and_latency(stateful_guest: None) -> None:
    """With TRex stateful (the fixture), a MonitorSession on the PUB port: the three template names, and a
    positive latency histogram count on every port within 30 s. Teardown: vm/mode.sh stateless."""
    async def run() -> None:
        snaps: list[dict] = []
        mon = MonitorSession(lambda m: snaps.append(m) if m.get("type") == "monitor" else None)
        try:
            await mon.connect(HOST, real_ports()[1])
            t = time.monotonic()
            while time.monotonic() - t < 30:
                snap = mon.snapshot()
                if snap["templates"] == TEMPLATES and latency_ports_ready(snap):
                    return
                await asyncio.sleep(0.5)
            snap = mon.snapshot()
            raise AssertionError(f"after 30 s: templates={snap['templates']!r}, "
                                 f"latency_v2={snap['latency_v2']!r}")
        finally:
            await mon.close()
    asyncio.run(run())
