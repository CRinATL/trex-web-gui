"""P5 STF runs against the real v3.08 guest's daemon (make check-daemon-real, jammy only, marker
restarts_trex, spec §13.10): the stock cap2/sfr2.yaml run end to end through the app, and the upload of a
local copy of it. The module fixture starts the daemon and stops the snapshot's TRex; its teardown always
stops the daemon and puts TRex back on the stateless stack."""
from collections.abc import Iterator
from pathlib import Path

import pytest
from daemon_vm import (
    HOST,
    STOCK_PROFILE,
    TEMPLATES,
    app_client,
    connect_daemon,
    daemon_port,
    daemon_running_fixture,
    direct,
    start_stf,
    wait_run_ended,
    wait_templates,
)
from support import real_ports
from trex_web_gui import stfyaml

pytestmark = [pytest.mark.real, pytest.mark.restarts_trex]


@pytest.fixture(scope="module", autouse=True)
def daemon() -> Iterator[None]:
    """Module fixture: the daemon is running and the snapshot's TRex is stopped; always cleaned up after."""
    yield from daemon_running_fixture(stop_trex=True)


@pytest.mark.timeout(300)
def test_daemon_stf_stock_run(tmp_path: Path) -> None:
    """The stock sfr2 profile, d 30: listed, parsed to three templates, started, seen by the Monitor, no
    command port for a session, ended with TRex's own words, and a non-empty TRex log."""
    with app_client(tmp_path) as c:
        connect_daemon(c)
        r = c.get("/api/daemon/stf/profiles", params={"source": "stock"})
        assert r.status_code == 200 and STOCK_PROFILE in r.json()["files"], r.text
        r = c.post("/api/daemon/stf/profile", json={"path": STOCK_PROFILE})
        assert r.status_code == 200 and r.json()["templates"] == TEMPLATES, r.text
        started = start_stf(c, STOCK_PROFILE, 30)
        assert isinstance(started["seq"], int)
        assert "-f cap2/sfr2.yaml -d 30 -m 1.0" in started["command_preview"], started["command_preview"]
        wait_templates(c, TEMPLATES, 30)
        r = c.post("/api/session/connect", json={"host": HOST, "sync_port": real_ports()[0],
                                                 "async_port": real_ports()[1]})
        assert r.status_code == 502 and r.json()["error"] == "unreachable", r.text
        run = wait_run_ended(c, 30 + 40)
        assert run["state"] == "ended" and run["outcome"] == "TRex finished.", run
        r = c.get("/api/daemon/log", params={"which": "trex"})
        assert r.status_code == 200 and r.json()["text"].strip(), r.text


@pytest.mark.timeout(300)
def test_daemon_stf_upload_run(tmp_path: Path) -> None:
    """A local copy of sfr2 with bare pcap names, fetched from the guest: previewed, uploaded (tg- names),
    listed as an uploaded profile, then run with d 20 under the absolute uploaded names."""
    caps = tmp_path / "caps"
    caps.mkdir()
    d = direct()
    text = d.get_file(STOCK_PROFILE).decode()
    for name in TEMPLATES:
        (caps / Path(name).name).write_bytes(d.get_file(name))
    local = caps / "gate.yaml"
    local.write_text(stfyaml.rewrite(text, {n: Path(n).name for n in TEMPLATES}))
    (tmp_path / "app").mkdir()
    with app_client(tmp_path / "app") as c:
        info = connect_daemon(c)
        r = c.post("/api/daemon/stf/upload/preview", json={"path": str(local)})
        assert r.status_code == 200, r.text
        plan = r.json()
        assert len(plan["files"]) == 3 and all(f["remote"].startswith("tg-") for f in plan["files"]), plan
        assert plan["yaml_remote"].startswith("tg-"), plan
        r = c.post("/api/daemon/stf/upload", json={"path": str(local), "expected_yaml_remote":
                                                   plan["yaml_remote"], "confirm": True,
                                                   "expected_target": f"127.0.0.1:{daemon_port()}"})
        assert r.status_code == 200, r.text
        profile = r.json()["profile"]
        r = c.get("/api/daemon/stf/profiles", params={"source": "uploaded"})
        assert r.status_code == 200 and profile in r.json()["files"], r.text
        start_stf(c, profile, 20)
        want = [f"{info['files_path']}/{f['remote']}" for f in plan["files"]]
        wait_templates(c, want, 30)
        run = wait_run_ended(c, 20 + 40)
        assert run["state"] == "ended" and run["outcome"] == "TRex finished.", run
