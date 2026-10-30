"""P3b follow-up 6, Task 2: the session resolves port interface names once at connect and publishes them."""
import asyncio
import socket
from pathlib import Path

import pytest
from helpers import Recorder, wait_for
from test_session import params
from trex_web_gui.sessions import SessionState, trex_session

# get_system_info's hostname in tests/fixtures/rpc.jsonl
FAKE_HOST = "trex-vm"
MACS = {0: "8e:4e:97:11:bf:a8", 1: "52:52:18:89:91:ea"}   # the fake's ports, by id


def write_cfg(tmp_path: Path, text: str) -> Path:
    """Write `text` to trex_cfg.yaml under `tmp_path` and return its path."""
    cfg = tmp_path / "trex_cfg.yaml"
    cfg.write_text(text)
    return cfg


def write_net(root: Path, name: str, mac: str) -> None:
    """Create `sys/class/net/<name>/address` under `root` holding `mac`, making the directories;
    FileExistsError when the interface's directory already exists."""
    d = root / "sys" / "class" / "net" / name
    d.mkdir(parents=True)
    (d / "address").write_text(mac + "\n")


def make(new_session, rec: Recorder, cfg: Path, root: Path):
    """A session from the `new_session` factory publishing to `rec`, reading the trex config at `cfg` and
    host info under `root`; the factory closes it."""
    return new_session(rec, trex_cfg_path=lambda: str(cfg), hostinfo_root=root)


def test_ifaces_snapshot_is_published(fake_trex, new_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)
    root = tmp_path / "root"
    for pid, name in enumerate(("veth0", "veth1")):
        write_net(root, name, MACS[pid])
    cfg = write_cfg(tmp_path, "- interfaces: [veth0, veth1]\n")

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, root)
        await s.connect(params(fake_trex()))
        snaps = [m for m in rec.msgs if m["type"] == "session"]
        assert snaps[-1]["state"] == "connected" and snaps[-1]["ifaces"], snaps[-1]
        assert [(i["port"], i["kernel_name"], i["confirmed"]) for i in snaps[-1]["ifaces"]] == [
            (0, "veth0", True), (1, "veth1", True)]
        assert snaps[-1]["ifaces_note"] is None
        assert s.snapshot()["ifaces"] == snaps[-1]["ifaces"]
        await s.disconnect()
    new_session.run(run())


def test_ifaces_note_when_no_config(fake_trex, new_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)
    cfg = tmp_path / "missing.yaml"

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, tmp_path / "root")
        await s.connect(params(fake_trex()))
        snap = s.snapshot()
        assert len(snap["ifaces"]) == 2
        assert str(cfg) in snap["ifaces_note"]
        await s.disconnect()
    new_session.run(run())


def test_ifaces_cleared_on_disconnect(fake_trex, new_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)
    cfg = tmp_path / "missing.yaml"

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, tmp_path / "root")
        await s.connect(params(fake_trex()))
        assert s.snapshot()["ifaces"] and s.snapshot()["ifaces_note"]
        await s.disconnect()
        assert s.snapshot()["ifaces"] == [] and s.snapshot()["ifaces_note"] is None
    new_session.run(run())


def test_bad_config_does_not_fail_connect(fake_trex, new_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)
    cfg = write_cfg(tmp_path, "- interfaces: [unterminated\n  : : {\n")

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, tmp_path / "root")
        await s.connect(params(fake_trex()))
        snap = s.snapshot()
        assert s._state == SessionState.CONNECTED and snap["state"] == "connected"
        assert snap["ifaces_note"] and len(snap["ifaces"]) == 2
        await s.disconnect()
    new_session.run(run())


def test_host_mismatch_note(fake_trex, new_session, tmp_path) -> None:
    local = socket.gethostname()
    if local == FAKE_HOST:
        pytest.skip("this host is named like the fake's")
    root = tmp_path / "root"
    # present and matching: only the gate can hide them
    for pid, name in enumerate(("veth0", "veth1")):
        write_net(root, name, MACS[pid])
    cfg = write_cfg(tmp_path, "- interfaces: [veth0, veth1]\n")

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, root)
        await s.connect(params(fake_trex()))
        note = s.snapshot()["ifaces_note"]
        assert FAKE_HOST in note and local in note
        ifaces = s.snapshot()["ifaces"]
        assert len(ifaces) == 2
        assert all(
            not i["confirmed"] and i["kernel_name"] is None and i["driver"] is None and i["model"] is None
            for i in ifaces
        )
        await s.disconnect()
    new_session.run(run())


def test_cancel_during_resolve_keeps_connected(fake_trex, new_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)
    cfg = tmp_path / "missing.yaml"

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, tmp_path / "root")
        gate, entered = asyncio.Event(), asyncio.Event()

        async def slow_to_thread(fn, *a):
            entered.set()                                # the cancel below lands inside the resolve await
            await gate.wait()
        monkeypatch.setattr(asyncio, "to_thread", slow_to_thread)
        task = asyncio.create_task(s.connect(params(fake_trex())))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s._state == SessionState.CONNECTED
        assert all(not m.get("ifaces") for m in rec.msgs if m["type"] == "session")
        monkeypatch.undo()
    new_session.run(run())


def test_resolve_failure_becomes_a_note_and_connect_still_succeeds(
    fake_trex, new_session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)

    def boom(*a, **kw):
        raise RuntimeError("sysfs exploded")
    monkeypatch.setattr(trex_session, "resolve", boom)

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, tmp_path / "missing.yaml", tmp_path / "root")
        await s.connect(params(fake_trex()))
        snap = s.snapshot()
        assert s._state == SessionState.CONNECTED and snap["state"] == "connected"
        assert snap["ifaces"] == [] and "sysfs exploded" in snap["ifaces_note"]
        await s.disconnect()
    new_session.run(run())


def test_lost_session_keeps_ifaces(fake_trex, new_session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: FAKE_HOST)
    root = tmp_path / "root"
    for pid, name in enumerate(("veth0", "veth1")):
        write_net(root, name, MACS[pid])
    cfg = write_cfg(tmp_path, "- interfaces: [veth0, veth1]\n")

    async def run() -> None:
        rec = Recorder()
        s = make(new_session, rec, cfg, root)
        f = fake_trex()
        await s.connect(params(f))
        before = s.snapshot()["ifaces"]
        assert before
        f.arm_seq_gap()
        await wait_for(lambda: s.snapshot()["state"] == "lost", 5)
        assert s.snapshot()["ifaces"] == before
        await s.close()
    new_session.run(run())
