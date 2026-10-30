"""Shared by the real-tier daemon modules (make check-daemon-real): the guest's daemon as a module fixture
body, a logged-in client over a fresh app, and the polling the runs need. Not a test module."""
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from support import ROOT, authed, real_ports
from trex_web_gui.api.app import create_app
from trex_web_gui.daemon.client import DaemonClient

HOST = os.environ.get("TREX_REAL_HOST", "")
CFG = "/etc/trex_cfg.yaml"
STOCK_PROFILE = "cap2/sfr2.yaml"
TEMPLATES = ["cap2/Oracle.pcap", "cap2/smtp.pcap", "cap2/dns.pcap"]    # sfr2.yaml's, in its order (R4)


def daemon_port() -> int:
    """The forwarded daemon port that vm/guests.sh exports (TREX_VM_DAEMON_PORT)."""
    return int(os.environ["TREX_VM_DAEMON_PORT"])


def sh(*args: str) -> str:
    """Run a vm script from the repo root in the foreground; AssertionError (with its output)
    unless rc is 0."""
    p = subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True, check=False)
    print(f"[sh] {' '.join(args)} rc={p.returncode}\n{p.stdout}{p.stderr}", flush=True)
    assert p.returncode == 0, f"{' '.join(args)} failed: {p.stdout}{p.stderr}"
    return p.stdout + p.stderr


def daemon_running_fixture(*, stop_trex: bool = False) -> Iterator[None]:
    """The body of a module fixture: skip unless TREX_REAL_HOST is set, run `vm/daemon.sh start` (and
    `vm/mode.sh stop` when `stop_trex`), yield, and always, at teardown, `vm/daemon.sh stop`, then
    `vm/mode.sh stateless`, then assert vm/ready.py passes."""
    if not HOST:
        pytest.skip("run via make check-daemon-real")
    real_ports()
    try:
        sh("vm/daemon.sh", "start")
        if stop_trex:
            sh("vm/mode.sh", "stop")
        yield
    finally:
        stopped = subprocess.run(["vm/daemon.sh", "stop"], cwd=ROOT, check=False).returncode
        restored = subprocess.run(["vm/mode.sh", "stateless"], cwd=ROOT, check=False).returncode
        t0 = Path(os.environ.get("TMPDIR", "/tmp")) / f"daemon-vm-ready-{os.getpid()}"
        t0.write_text(str(time.time()))
        try:
            ready = subprocess.run([sys.executable, str(ROOT / "vm" / "ready.py"), str(t0)], cwd=ROOT,
                                   check=False, capture_output=True, text=True)
        finally:
            t0.unlink()
        print(f"[teardown] daemon stop rc={stopped}, mode stateless rc={restored}, "
              f"ready rc={ready.returncode}")
        assert restored == 0 and ready.returncode == 0, \
            f"the guest is not back on stateless: {ready.stdout}{ready.stderr}"


def direct() -> DaemonClient:
    """A DaemonClient on the guest's daemon, for reads and polling that bypass the app."""
    return DaemonClient("127.0.0.1", daemon_port())


def wait_daemon_idle(limit_s: float) -> None:
    """Block until the daemon's is_running is false, or fail after `limit_s` (the daemon notices a TRex that
    vm/mode.sh stopped behind its back after a moment)."""
    d = direct()
    t = time.monotonic()
    while d.is_running():
        assert time.monotonic() - t < limit_s, f"the daemon still says running after {limit_s} s"
        time.sleep(0.5)


def connect_daemon(c: TestClient) -> dict[str, Any]:
    """POST /api/daemon/connect to the guest's daemon; asserts 200 and returns the body."""
    r = c.post("/api/daemon/connect", json={"host": "127.0.0.1", "port": daemon_port()})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def app_client(tmp_path: Path) -> TestClient:
    """A logged-in, un-entered client of a fresh app (`with app_client(tmp_path) as c:` runs its lifespan)."""
    return authed(create_app(tmp_path))


def start_stf(c: TestClient, profile: str, d: int) -> dict[str, Any]:
    """POST /api/daemon/start for a stateful run of `profile` (l 1000, rx_check 100, c 1) with confirm;
    asserts 200 and returns the body."""
    r = c.post("/api/daemon/start", json={
        "mode": "stf", "cfg_path": CFG, "options": {"c": 1}, "confirm": True,
        "stf": {"profile": profile, "d": d, "l": 1000, "rx_check": 100}})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def wait_templates(c: TestClient, want: list[str], limit_s: float) -> None:
    """Point the app's Monitor at the guest's PUB port and wait for `templates` to equal `want`. The Monitor
    is disconnected again afterwards."""
    r = c.post("/api/monitor/connect", json={"host": "127.0.0.1", "port": real_ports()[1]})
    assert r.status_code == 200, r.text
    try:
        t = time.monotonic()
        seen = None
        while time.monotonic() - t < limit_s:
            seen = c.get("/api/monitor").json().get("templates")
            if seen == want:
                return
            time.sleep(0.5)
        raise AssertionError(f"templates {seen!r} are not {want!r} after {limit_s} s")
    finally:
        c.post("/api/monitor/disconnect")


def wait_run_ended(c: TestClient, limit_s: float) -> dict[str, Any]:
    """Poll GET /api/daemon until the run record's state is not `running`; returns the record. Fails after
    `limit_s`."""
    t = time.monotonic()
    run: dict[str, Any] | None = None
    while time.monotonic() - t < limit_s:
        run = c.get("/api/daemon").json()["run"]
        if run is not None and run["state"] != "running":
            return run
        time.sleep(1.0)
    raise AssertionError(f"the run did not end within {limit_s} s: {run}")
