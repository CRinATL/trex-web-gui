"""P5 against the real v3.08 guest's daemon (make check-daemon-real, jammy only, marker restarts_trex): the
foreign-TRex refusal and the push, start, foreign-owner refusal and stop cycle (spec §8, D1-D3). The module
fixture starts the guest's daemon; its teardown always stops it and puts TRex back on the stateless stack."""
import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from daemon_vm import (
    HOST,
    app_client,
    connect_daemon,
    daemon_running_fixture,
    sh,
    wait_daemon_idle,
)
from support import ROOT, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession

pytestmark = [pytest.mark.real, pytest.mark.restarts_trex]


@pytest.fixture(scope="module", autouse=True)
def daemon() -> Iterator[None]:
    """Module fixture: the guest's daemon is running; always stopped and TRex put back on stateless after."""
    yield from daemon_running_fixture()


async def trex_version() -> str:
    """Connect a standalone TrexSession to the guest's TRex, read its version and close it."""
    s = TrexSession(lambda _m: None)
    try:
        return (await s.connect(ConnectParams(HOST, *real_ports(), 3.0))).version
    finally:
        await s.disconnect()
        await s.close()


@pytest.mark.timeout(90)
def test_daemon_refuses_to_start_beside_the_snapshot_trex(tmp_path: Path) -> None:
    """The snapshot's TRex (_t-rex-64) was not started by this daemon: Start with confirm is 409
    foreign_trex naming it, and that TRex still answers v3.08."""
    with app_client(tmp_path) as c:
        connect_daemon(c)
        r = c.post("/api/daemon/start", json={"cfg_path": "/etc/trex_cfg.yaml", "confirm": True})
        assert r.status_code == 409, r.text
        assert r.json()["error"] == "foreign_trex" and "_t-rex-64" in r.json()["message"], r.text
    assert asyncio.run(trex_version()) == "v3.08"


@pytest.mark.timeout(300)
def test_daemon_push_start_stop_cycle(tmp_path: Path) -> None:
    """Last in the file so the fixture teardown counts against its budget (about 130 s, doubled): stop the
    snapshot's TRex, push the guest's config, start through the daemon, see a foreign owner refuse Stop,
    stop, and find TRex gone."""
    trexapi.load()
    from trex.stl.api import STLClient
    cfg_text = (ROOT / "vm" / "guest" / "trex_cfg.yaml").read_text()
    with app_client(tmp_path) as c:
        info = connect_daemon(c)
        sh("vm/mode.sh", "stop")
        wait_daemon_idle(10)
        r = c.post("/api/daemon/config/push", json={"yaml": cfg_text, "filename": "trex-web-gui-real.yaml",
                                                   "confirm": True})
        assert r.status_code == 200, r.text
        cfg_path = r.json()["path"]
        r = c.post("/api/daemon/start", json={"cfg_path": cfg_path, "trex_args": "--no-scapy-server",
                                              "confirm": True})
        assert r.status_code == 200, r.text
        preview = r.json()["command_preview"]
        assert f"--cfg {info['files_path']}/" in preview, preview
        assert "-i --no-scapy-server --no-key" in preview, preview

        assert asyncio.run(trex_version()) == "v3.08"
        foreign = STLClient(username="console-b", server=HOST, sync_port=real_ports()[0],
                            async_port=real_ports()[1], verbose_level="none")
        foreign.connect()
        try:
            foreign.acquire(ports=[0], force=True)
            r = c.post("/api/daemon/stop", json={"rpc_port": real_ports()[0], "confirm": True})
            assert r.status_code == 409, r.text
            assert r.json()["error"] == "in_use" and "console-b" in r.json()["message"], r.text
        finally:
            try:
                foreign.release(ports=[0])
            finally:
                foreign.disconnect()
        r = c.post("/api/daemon/stop", json={"rpc_port": real_ports()[0], "confirm": True})
        assert r.status_code == 200, r.text
        if r.json()["running"]:                 # not within the route's own wait: allow the 10 s here
            wait_daemon_idle(10)
        with pytest.raises(trexapi.ServerUnreachable):
            asyncio.run(trex_version())
        for which in ("trex", "daemon"):
            r = c.get("/api/daemon/log", params={"which": which})
            assert r.status_code == 200 and r.json()["which"] == which and r.json()["text"].strip(), r.text
