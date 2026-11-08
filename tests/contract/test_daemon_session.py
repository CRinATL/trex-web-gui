"""DaemonSession (backend/trex_web_gui/daemon/session.py) against tests/fake/fake_daemon.py and fake_trex.py:
the refusals that keep it from acting against someone else's TRex, the command preview, the run record,
STF."""
import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import Recorder, wait_for
from support import free_port
from trex_web_gui.daemon import session as session_mod
from trex_web_gui.daemon.client import (
    CALL_TIMEOUT_S,
    DaemonClient,
    DaemonFault,
    DaemonUnreachable,
    StfOptions,
)
from trex_web_gui.daemon.session import DaemonRefused, DaemonSession, StopHooks
from trex_web_gui.sessions import ConnectParams

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CFG = "/tmp/trex_files/a.yaml"
SFR2 = StfOptions("cap2/sfr2.yaml", 20, 1.0, 1000, 100)
SFR2_YAML = (
    "- duration: 10.0\n  generator:\n    distribution: seq\n    clients_start: 16.0.0.1\n"
    "    clients_end: 16.0.0.255\n    servers_start: 48.0.0.1\n    servers_end: 48.0.0.255\n"
    "    clients_per_gb: 201\n    min_clients: 101\n    dual_port_mask: 1.0.0.0\n    tcp_aging: 1\n"
    "    udp_aging: 1\n  cap_info:\n")
STATE_CHANGING = {"push_file", "start_trex", "stop_trex", "force_trex_kill"}
Body = Callable[[DaemonSession, Recorder], Awaitable[None]]


class Hooks:
    """StopHooks that record each call, in order, with expect's arguments; `expect_raises` makes expect
    record its call and then raise RuntimeError."""

    def __init__(self, expect_raises: bool = False) -> None:
        """No calls yet."""
        self.expect_raises = expect_raises
        self.calls: list[str] = []
        self.expected: tuple[str, int, str] | None = None
        self.started_with: tuple[Any, ...] | None = None

    async def started(self, daemon: str, rpc_port: int | None, mode: str, timeout_s: float,
                      ends_at: float | None) -> None:
        """Record the call and its arguments."""
        self.started_with = (daemon, rpc_port, mode, timeout_s, ends_at)

    async def expect(self, host: str, rpc_port: int, daemon: str) -> None:
        """Record the call and its arguments."""
        self.calls.append("expect")
        self.expected = (host, rpc_port, daemon)
        if self.expect_raises:
            raise RuntimeError("expect failed")

    def done(self) -> None:
        """Record the call."""
        self.calls.append("done")

    def cancel(self) -> None:
        """Record the call."""
        self.calls.append("cancel")


def drive(body: Body, owned: Callable[[str, int], set[int] | None] = lambda h, p: None,
          hooks: StopHooks | None = None) -> None:
    """Run `body(session, recorder)` under asyncio.run and close the session in that loop, raised or not."""
    async def wrapped() -> None:
        """The body, then close."""
        rec = Recorder()
        s = DaemonSession(rec, owned, hooks)
        try:
            await body(s, rec)
        finally:
            await s.close()
    asyncio.run(wrapped())


def daemon_msgs(rec: Recorder) -> list[dict[str, Any]]:
    """The `daemon` messages recorded so far."""
    return [m for m in rec.msgs if m["type"] == "daemon"]


def changed(fake: Any) -> set[str]:
    """The state-changing methods the fake daemon has received."""
    return set(fake.methods()) & STATE_CHANGING


def tg_profile(pcaps: list[str]) -> str:
    """A profile in SFR2's shape whose templates are `pcaps`."""
    return SFR2_YAML + "".join(
        f"    - name: {p}\n      cps: 1.0\n      ipg: 10\n      rtt: 10\n      w: 4\n" for p in pcaps)


async def refused(coro: Awaitable[Any], code: str) -> DaemonRefused:
    """Await `coro`, which must raise DaemonRefused with `code`; return the exception."""
    with pytest.raises(DaemonRefused) as e:
        await coro
    assert e.value.code == code, e.value.message
    return e.value


def test_every_destructive_call_needs_confirm(fake_daemon: Any) -> None:
    """Push, Start, Stop and Force stop without confirm are 409 confirm_required and reach no state-changing
    method of the daemon."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Each call without confirm."""
        reply = await s.connect(d.host, d.port)
        await refused(s.push(reply["config_yaml"], None, False), "confirm_required")
        await refused(s.start(CFG, {}, "", 20, False), "confirm_required")
        await refused(s.start(CFG, {}, "", 20, False, SFR2), "confirm_required")
        await refused(s.stop(4501, False), "confirm_required")
        await refused(s.stop(4501, False, force=True, owners_unknown_ack=True), "confirm_required")
        assert changed(d) == set()
    drive(body)


def test_connect_reply_and_push(fake_daemon: Any) -> None:
    """connect returns the daemon's version, config, metadata and devices (plus dummy); a pushed valid config
    lands under files_path and is listed in the daemon message."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, then push the daemon's own config."""
        r = await s.connect(d.host, d.port)
        assert r["version"].startswith("Version : v3.08") and r["files_path"] == "/tmp/trex_files"
        assert r["devices"][-1] == {"id": "dummy"} and len(r["devices"]) > 1
        assert r["status"] == {"state": "Idle", "verbose": "TRex is Idle"}
        assert len(r["metadata_version"]) == 32
        out = await s.push(r["config_yaml"], "my-config.yaml", True)
        assert out == {"path": "/tmp/trex_files/my-config.yaml"}
        assert s.snapshot()["pushed"] == ["/tmp/trex_files/my-config.yaml"]
        for bad in ("tg-0123456789ab-x.yaml", "../x.yaml", "x.txt", "a" * 70 + ".yaml"):
            with pytest.raises(ValueError):
                await s.push(r["config_yaml"], bad, True)
    drive(body)


def test_start_refuses_beside_a_foreign_trex(fake_daemon: Any) -> None:
    """A TRex the daemon lists that this backend did not start is foreign_trex, naming its pid and command."""
    d = fake_daemon("--foreign")

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start refused, with no start_trex."""
        await s.connect(d.host, d.port)
        e = await refused(s.start(CFG, {}, "", 20, True), "foreign_trex")
        assert "2662" in e.message and "_t-rex-64 -i" in e.message
        assert changed(d) == set()
    drive(body)


def test_start_refuses_when_running_or_reserved(fake_daemon: Any) -> None:
    """A second Start is already_running, and a reserved daemon is reserved; neither calls start_trex."""
    d, r = fake_daemon(), fake_daemon("--reserved")

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start twice on one daemon, then once on the reserved one."""
        await s.connect(d.host, d.port)
        out = await s.start(CFG, {"c": 2}, "--no-scapy-server", 20, True)
        assert out["command_preview"].endswith("-c 2 -i --no-scapy-server --no-key")
        assert out["run"]["state"] == "running" and out["run"]["mode"] == "stl"
        await refused(s.start(CFG, {}, "", 20, True), "already_running")
        assert d.methods().count("start_trex") == 1
        await s.connect(r.host, r.port)
        await refused(s.start(CFG, {}, "", 20, True), "reserved")
        assert "start_trex" not in r.methods()
    drive(body)


def test_push_refuses_an_invalid_config_without_calling(fake_daemon: Any) -> None:
    """A config that is not YAML, or fails the metadata, is invalid_config and makes no push_file call."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """A broken document and a well-formed one that lacks mandatory fields."""
        await s.connect(d.host, d.port)
        await refused(s.push("- [unclosed", None, True), "invalid_config")
        await refused(s.push("- port_limit: 2\n", None, True), "invalid_config")
        assert changed(d) == set()
    drive(body)


def test_not_connected_is_refused(fake_daemon: Any) -> None:
    """Every call that needs a daemon says not_connected when there is none."""
    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Push, start, stop, profiles and log on a disconnected session."""
        await refused(s.push("x", None, True), "not_connected")
        await refused(s.start(CFG, {}, "", 20, True), "not_connected")
        await refused(s.stop(4501, True), "not_connected")
        await refused(s.stf_profiles("stock"), "not_connected")
        await refused(s.log("trex"), "not_connected")
    drive(body)


def test_command_preview_matches_the_daemon_format() -> None:
    """The stateless preview is exactly the daemon's command with runs of spaces collapsed."""
    s = DaemonSession(Recorder(), lambda h, p: None)
    assert s.command_preview(CFG, {"c": 2}, "--no-scapy-server") == (
        "nice -n -19 ./t-rex-64 --iom 0 --cfg /tmp/trex_files/a.yaml -c 2 -i --no-scapy-server --no-key")
    assert s.command_preview(CFG, {}, "") == (
        "nice -n -19 ./t-rex-64 --iom 0 --cfg /tmp/trex_files/a.yaml -i --no-key")
    faults = json.loads((FIXTURES / "p5/summary.json").read_text())["faults"]
    recorded = " ".join(faults["start_bad_cfg"]["error"]["message"].split())
    cfg = re.search(r"--cfg (\S+)", recorded)    # the recorded file name, whatever the recorder named it
    assert cfg is not None
    assert s.command_preview(cfg[1], {}, "--no-scapy-server") == \
        f"nice -n -19 ./t-rex-64 --iom 0 --cfg {cfg[1]} -i --no-scapy-server --no-key"
    assert s.command_preview(cfg[1], {}, "--no-scapy-server") in recorded


def test_stf_command_preview_matches_the_recorded_command() -> None:
    """The STF preview is the recorded -f command (Task 14's full_command_stf, spaces collapsed)."""
    s = DaemonSession(Recorder(), lambda h, p: None)
    got = s.command_preview("/etc/trex_cfg.yaml", {"c": 1}, "", SFR2)
    assert got == ("nice -n -19 ./t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -f cap2/sfr2.yaml -d 20 -m 1.0 "
                   "-l 1000 --rx-check 100 -c 1 --no-key")
    recorded = json.loads((FIXTURES / "p5-stf/summary.json").read_text())["full_command_stf"]
    assert got == " ".join(recorded.split())
    assert " -l " not in s.command_preview("/etc/trex_cfg.yaml", {}, "", StfOptions("cap2/sfr2.yaml", 10, 2))


@pytest.mark.parametrize("args", [
    "--cfg", "--cfg=/etc/x", "-i", "--astf", "-f", "-f=x", "-d", "-m", "-l", "--rx-check", "--client_cfg",
    "--client_cfg=/etc/x", "--rx-check=5", "$(id)", "a;b", "a b;c", "`x`", "a|b", "'a b'", "a>b",
    " ".join(["--no-key"] * 21),
    "--cf /etc/x", "--cf=/etc/x", "--client-cfg /etc/x", "--client_ /etc/x", "--rx 5", "--as",
])
def test_trex_args_rule(args: str) -> None:
    """Each of these is refused as a ValueError, by the preview and so by start."""
    s = DaemonSession(Recorder(), lambda h, p: None)
    with pytest.raises(ValueError):
        s.command_preview(CFG, {}, args)


@pytest.mark.parametrize("cfg,options", [
    ("relative.yaml", {}), ("/tmp/../etc/x.yaml", {}), ("/tmp/a b.yaml", {}), ("/tmp/x;y.yaml", {}),
    (CFG, {"c": 0}), (CFG, {"c": 65}), (CFG, {"c": True}), (CFG, {"x": 1}),
])
def test_cfg_and_options_rules(cfg: str, options: dict[str, Any]) -> None:
    """A cfg path outside the rule or an option other than c in 1..64 is a ValueError."""
    with pytest.raises(ValueError):
        DaemonSession(Recorder(), lambda h, p: None).command_preview(cfg, options, "")


def test_trex_args_prefix_rule_spares_unrelated_flags() -> None:
    """`--no-key` is no prefix of a forbidden flag, so it still passes."""
    s = DaemonSession(Recorder(), lambda h, p: None)
    assert s.command_preview(CFG, {}, "--no-key").endswith("-i --no-key --no-key")


def test_trex_args_accepts_ordinary_flags() -> None:
    """Twenty plain tokens pass, and the preview carries them in order."""
    s = DaemonSession(Recorder(), lambda h, p: None)
    out = s.command_preview(CFG, {}, "--no-scapy-server --prom -v=2 --cores 4")
    assert out.endswith("-i --no-scapy-server --prom -v=2 --cores 4 --no-key")


def started(s: DaemonSession) -> Awaitable[Any]:
    """Start a stateless run on the connected daemon."""
    return s.start(CFG, {}, "", 20, True)


def test_stop_refuses_while_another_user_owns_a_port(fake_daemon: Any, fake_trex: Any,
                                                      foreign_client: Any) -> None:
    """A port owned by trex-console is in_use for Stop and Force stop, with or without the ack; no stop."""
    d, f = fake_daemon(), fake_trex()
    foreign_client(f, "console-b").acquire(ports=[0], force=False)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start a run, then try every way of stopping it."""
        await s.connect(d.host, d.port)
        await started(s)
        e = await refused(s.stop(f.rpc_port, True), "in_use")
        assert "port 0: console-b" in e.message
        await refused(s.stop(f.rpc_port, True, force=True), "in_use")
        await refused(s.stop(f.rpc_port, True, force=True, owners_unknown_ack=True), "in_use")
        assert changed(d) == {"start_trex"}
    drive(body)


def test_stop_allows_ports_our_session_owns(fake_daemon: Any, fake_trex: Any, foreign_client: Any) -> None:
    """A port the Control session owns is not 'another user': Stop proceeds."""
    d, f = fake_daemon(), fake_trex()
    foreign_client(f, "console-b").acquire(ports=[0], force=False)
    seen: list[tuple[str, int]] = []

    def owned(host: str, port: int) -> set[int] | None:
        """This backend's session owns port 0 on f."""
        seen.append((host, port))
        return {0}

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start, Stop."""
        await s.connect(d.host, d.port)
        await started(s)
        out = await s.stop(f.rpc_port, True)
        assert out["running"] is False and isinstance(out["stop_s"], float)
        assert "stop_trex" in d.methods() and seen == [(f.host, f.rpc_port)]
    drive(body, owned)


def test_stop_refuses_when_owners_cannot_be_read(fake_daemon: Any) -> None:
    """Nothing on the command port: Stop is owners_unknown with or without the ack, and calls no stop."""
    d = fake_daemon()
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Stop twice."""
        await s.connect(d.host, d.port)
        await started(s)
        e = await refused(s.stop(dead, True), "owners_unknown")
        assert "Stop needs the TRex command port to see who owns its ports" in e.message
        assert str(dead) in e.message and "Force stop can proceed" not in e.message
        await refused(s.stop(dead, True, owners_unknown_ack=True), "owners_unknown")
        assert changed(d) == {"start_trex"}
    drive(body)


def test_force_stop_refuses_unknown_owners_without_the_ack(fake_daemon: Any) -> None:
    """Force stop on an unreadable port, no ack: owners_unknown, with the hint, and no force_trex_kill."""
    d = fake_daemon("--daemon-run")
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Force stop without the ack."""
        await s.connect(d.host, d.port)
        e = await refused(s.stop(dead, True, force=True), "owners_unknown")
        assert "Force stop can proceed if you acknowledge that the owners are unknown" in e.message
        assert "force_trex_kill" not in d.methods()
    drive(body)


def test_force_stop_with_the_ack_kills_once(fake_daemon: Any) -> None:
    """Force stop with the ack on an unreadable port kills the daemon's run once and publishes a warning
    naming the ack."""
    d = fake_daemon("--daemon-run")
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Force stop with the ack."""
        await s.connect(d.host, d.port)
        out = await s.stop(dead, True, force=True, owners_unknown_ack=True)
        assert out["running"] is False
        assert d.methods().count("force_trex_kill") == 1
        logs = [m for m in rec.msgs if m["type"] == "log"]
        assert len(logs) == 1 and logs[0]["level"] == "warning" and "owners_unknown_ack" in logs[0]["message"]
        assert f"{d.host}:{dead}" in logs[0]["message"]
    drive(body)


def test_owners_unknown_ack_does_not_override_in_use(fake_daemon: Any, fake_trex: Any,
                                                     foreign_client: Any) -> None:
    """Another user owns port 0: Force stop with the ack is in_use and kills nothing."""
    d, f = fake_daemon("--daemon-run"), fake_trex()
    foreign_client(f, "console-b").acquire(ports=[0], force=False)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Force stop with the ack."""
        await s.connect(d.host, d.port)
        await refused(s.stop(f.rpc_port, True, force=True, owners_unknown_ack=True), "in_use")
        assert "force_trex_kill" not in d.methods()
    drive(body)


def test_stop_without_our_seq_needs_force(fake_daemon: Any, fake_trex: Any) -> None:
    """With no run of ours held, Stop is not_ours; Force stop (owners readable and free) kills the daemon's
    run once."""
    d, f = fake_daemon("--daemon-run"), fake_trex()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Stop, then Force stop."""
        await s.connect(d.host, d.port)
        await refused(s.stop(f.rpc_port, True), "not_ours")
        assert changed(d) == set()
        await s.stop(f.rpc_port, True, force=True)
        assert d.methods().count("force_trex_kill") == 1
        assert [m for m in rec.msgs if m["type"] == "log"] == []       # the ack was not used
    drive(body)


def test_stop_reply_reports_running(fake_daemon: Any, fake_trex: Any,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal Stop answers {running: false, stop_s}; a TRex still running after the wait answers
    {running: true, stop_s: null}, which is not an error."""
    d, f = fake_daemon(), fake_trex()
    stubborn = fake_daemon("--stop-keeps-running")
    monkeypatch.setattr(session_mod, "STOP_WAIT_S", 0.6)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """One stop on each fake."""
        await s.connect(d.host, d.port)
        await started(s)
        out = await s.stop(f.rpc_port, True)
        assert out["running"] is False and out["stop_s"] is not None and out["stop_s"] < 5
        await s.connect(stubborn.host, stubborn.port)
        await started(s)
        assert await s.stop(f.rpc_port, True) == {"running": True, "stop_s": None, "other_pids": []}
        assert s.snapshot()["run"]["state"] == "running"
    drive(body)


def test_stop_calls_the_hooks_in_order(fake_daemon: Any, fake_trex: Any) -> None:
    """A stop that ends TRex tells the hooks expect, then done; expect carries the TRex command target and the
    daemon's host:port."""
    d, f, hooks = fake_daemon(), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start, stop."""
        await s.connect(d.host, d.port)
        await started(s)
        assert (await s.stop(f.rpc_port, True))["running"] is False
        assert hooks.calls == ["expect", "done"]
        assert hooks.expected == (d.host, f.rpc_port, f"{d.host}:{d.port}")
    drive(body, hooks=hooks)


def test_force_stop_without_a_daemon_run_is_refused(fake_daemon: Any, fake_trex: Any) -> None:
    """A hand-started TRex with the daemon Idle (daemon-force-2): force_trex_kill could not stop it, so Force
    stop is not_daemon_run before any state-changing call, and the hooks hear nothing."""
    d, f, hooks = fake_daemon("--foreign"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop."""
        await s.connect(d.host, d.port)
        e = await refused(s.stop(f.rpc_port, True, force=True), "not_daemon_run")
        assert "did not start" in e.message
        assert changed(d) == set()
        assert hooks.calls == []
    drive(body, hooks=hooks)


def test_force_stop_without_a_daemon_run_skips_the_owner_read(fake_daemon: Any) -> None:
    """Force stop with no run of ours, the daemon Idle, at a dead command port and no ack: not_daemon_run
    (the running_status read comes before the owner read, which would be owners_unknown)."""
    d = fake_daemon("--foreign")

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop without the ack."""
        await s.connect(d.host, d.port)
        await refused(s.stop(free_port(), True, force=True), "not_daemon_run")
        assert changed(d) == set()
    drive(body)


def test_force_stop_of_a_hand_started_stf_run_needs_no_ack_to_refuse(fake_daemon: Any) -> None:
    """A -f run started by hand, the daemon Idle: Force stop without the ack is not_daemon_run, not
    owners_unknown."""
    d = fake_daemon("--foreign-stf")

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop without the ack."""
        await s.connect(d.host, d.port)
        await refused(s.stop(free_port(), True, force=True), "not_daemon_run")
        assert changed(d) == set()
    drive(body)


def test_force_stop_whose_kill_finds_no_run_cancels(fake_daemon: Any, fake_trex: Any) -> None:
    """The daemon's run ends just before force_trex_kill arrives, which answers False: not_daemon_run, and the
    hooks hear expect, then cancel, never done."""
    d, f, hooks = fake_daemon("--daemon-run", "--run-ends-before-kill"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop."""
        await s.connect(d.host, d.port)
        await refused(s.stop(f.rpc_port, True, force=True), "not_daemon_run")
        assert d.methods().count("force_trex_kill") == 1
        assert hooks.calls == ["expect", "cancel"]
    drive(body, hooks=hooks)


def test_force_stop_calls_the_hooks(fake_daemon: Any, fake_trex: Any) -> None:
    """Force stop of a daemon run this backend did not start: force_trex_kill ends it (is_running turns
    false), so the hooks hear expect, then done."""
    d, f, hooks = fake_daemon("--daemon-run"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop."""
        await s.connect(d.host, d.port)
        out = await s.stop(f.rpc_port, True, force=True)
        assert out["running"] is False
        assert hooks.calls == ["expect", "done"]
    drive(body, hooks=hooks)


def warnings_of(rec: Recorder) -> list[dict[str, Any]]:
    """The warning-level `log` messages recorded so far."""
    return [m for m in rec.msgs if m["type"] == "log" and m["level"] == "warning"]


async def other_client(d: Any, *calls: Callable[[DaemonClient], Any]) -> None:
    """Make `calls` from a second DaemonClient, off the loop, as another client of the daemon would."""
    c = DaemonClient(d.host, d.port)
    for call in calls:
        await asyncio.to_thread(call, c)


def test_force_stop_with_our_seq_sends_force_trex_kill(fake_daemon: Any, fake_trex: Any) -> None:
    """Force stop while this backend holds a seq kills with force_trex_kill, never stop_trex(seq)
    (p5-monitor-daemon-174)."""
    d, f = fake_daemon(), fake_trex()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start stateless, Force stop."""
        await s.connect(d.host, d.port)
        await s.start(CFG, {}, "", 20, True, None, f.rpc_port)
        out = await s.stop(f.rpc_port, True, force=True)
        assert out["running"] is False
        assert d.methods().count("force_trex_kill") == 1
        assert "stop_trex" not in d.methods()
    drive(body)


def test_force_stop_with_our_seq_and_the_ack_logs_the_warning(fake_daemon: Any) -> None:
    """Force stop with the ack while a seq is held publishes the owners_unknown_ack warning and kills once."""
    d = fake_daemon()
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start stateless, Force stop with the ack on an unreadable port."""
        await s.connect(d.host, d.port)
        await s.start(CFG, {}, "", 20, True)
        await s.stop(dead, True, force=True, owners_unknown_ack=True)
        logs = warnings_of(rec)
        assert len(logs) == 1 and "owners_unknown_ack" in logs[0]["message"]
        assert f"{d.host}:{dead}" in logs[0]["message"]
        assert d.methods().count("force_trex_kill") == 1
    drive(body)


def test_force_stop_with_a_stale_seq_kills_the_new_run(fake_daemon: Any, fake_trex: Any,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Our run ended and another client started one before a poll cleared our seq: Force stop kills that run
    (stop_trex with the stale seq would be refused -33)."""
    monkeypatch.setattr(session_mod, "POLL_S", 60.0)
    d, f = fake_daemon(), fake_trex()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start, let another client replace the run, Force stop."""
        await s.connect(d.host, d.port)
        await s.start(CFG, {}, "", 20, True)
        await other_client(d, lambda c: c.force_trex_kill(), lambda c: c.start_trex(CFG, {}, "other", 20, ""))
        out = await s.stop(f.rpc_port, True, force=True)
        assert out["running"] is False
        assert "stop_trex" not in d.methods()
        assert DaemonClient(d.host, d.port).is_running() is False
    drive(body)


def test_force_stop_with_a_held_seq_and_the_daemon_idle_is_refused_first(
        fake_daemon: Any, fake_trex: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A held seq whose run already ended (the daemon Idle, no poll since): Force stop is not_daemon_run
    before any stopping call, so the hooks hear nothing."""
    monkeypatch.setattr(session_mod, "POLL_S", 60.0)
    d, f, hooks = fake_daemon(), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start, let another client end the run, Force stop."""
        await s.connect(d.host, d.port)
        await s.start(CFG, {}, "", 20, True)
        await other_client(d, lambda c: c.force_trex_kill())
        await refused(s.stop(f.rpc_port, True, force=True), "not_daemon_run")
        assert "expect" not in hooks.calls
        assert d.methods().count("force_trex_kill") == 1
    drive(body, hooks=hooks)


FOREIGN_PID = next(int(r["body"]["result"][0][0]) for r in
                   map(json.loads, (FIXTURES / "p5" / "daemon.jsonl").read_text().splitlines())
                   if r["method"] == "get_trex_cmds" and "legacy" in r["body"]["result"][0][1])
DAEMON_PID = 4242               # the pid fake_daemon.py lists for its own run
STILL_RUNNING = "a TRex the daemon did not start is still running there"


def still_running_warnings(rec: Recorder) -> list[dict[str, Any]]:
    """The warnings that report a TRex left running beside a stopped daemon run."""
    return [m for m in warnings_of(rec) if STILL_RUNNING in m["message"]]


def test_stop_beside_a_hand_started_trex_cancels(fake_daemon: Any, fake_trex: Any) -> None:
    """Force stop with a hand-started TRex answering on the command port (daemon-force-3): the daemon's run
    ends, but the hooks hear cancel, the reply names the pid still running, and one warning says so."""
    d, f, hooks = fake_daemon("--daemon-run", "--foreign"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop."""
        await s.connect(d.host, d.port)
        out = await s.stop(f.rpc_port, True, force=True)
        assert out["running"] is False
        assert out["other_pids"] == [FOREIGN_PID]
        assert hooks.calls == ["expect", "cancel"]
        logs = still_running_warnings(rec)
        assert len(logs) == 1 and f"{d.host}:{f.rpc_port}" in logs[0]["message"]
        assert str(FOREIGN_PID) in logs[0]["message"]
    drive(body, hooks=hooks)


def test_stop_whose_list_empties_but_the_port_answers_cancels(fake_daemon: Any, fake_trex: Any) -> None:
    """Both listed TRexes leave the process list during the wait, yet a TRex still answers on the command
    port: an empty listing never decides "stopped", so the hooks cancel (other_pids stays [])."""
    d, f, hooks = fake_daemon("--daemon-run", "--foreign-ends-with-run"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop."""
        await s.connect(d.host, d.port)
        out = await s.stop(f.rpc_port, True, force=True)
        assert out["other_pids"] == []
        assert hooks.calls == ["expect", "cancel"]
        assert len(still_running_warnings(rec)) == 1
    drive(body, hooks=hooks)


def test_stop_beside_another_trex_on_a_dead_port_is_done(fake_daemon: Any) -> None:
    """Force stop beside another TRex when nothing answers on the command port: done, no pids, no warning
    about a TRex left running."""
    d, hooks = fake_daemon("--daemon-run", "--foreign"), Hooks()
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop with the ack (the owners of a dead port cannot be read)."""
        await s.connect(d.host, d.port)
        out = await s.stop(dead, True, force=True, owners_unknown_ack=True)
        assert out["other_pids"] == []
        assert hooks.calls == ["expect", "done"]
        assert still_running_warnings(rec) == []
    drive(body, hooks=hooks)


def test_stop_waits_for_the_daemon_run_to_leave_the_list(fake_daemon: Any,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon reports Idle before its TRex exits (--exit-lag): the stop re-reads the process list until
    the daemon's run has left it, and only then probes."""
    monkeypatch.setattr(session_mod, "POLL_S", 60.0)
    d = fake_daemon("--daemon-run", "--foreign", "--exit-lag", "0.6")
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, time a Force stop."""
        await s.connect(d.host, d.port)
        before = d.methods().count("get_trex_cmds")
        t0 = time.monotonic()
        out = await s.stop(dead, True, force=True, owners_unknown_ack=True)
        assert time.monotonic() - t0 >= 0.6
        assert d.methods().count("get_trex_cmds") - before >= 4
        assert out["other_pids"] == []
    drive(body)


def test_stop_check_is_bounded(fake_daemon: Any, fake_trex: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon TRex that never leaves the list: the wait ends at STOP_WAIT_S and the probe still runs, so a
    TRex answering on the command port cancels."""
    monkeypatch.setattr(session_mod, "STOP_WAIT_S", 0.5)
    d, f, hooks = fake_daemon("--daemon-run", "--foreign", "--exit-lag", "30"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, time a Force stop."""
        await s.connect(d.host, d.port)
        t0 = time.monotonic()
        out = await s.stop(f.rpc_port, True, force=True)
        assert time.monotonic() - t0 < 5
        assert out["other_pids"] == sorted([FOREIGN_PID, DAEMON_PID])
        assert hooks.calls[-1] == "cancel"
    drive(body, hooks=hooks)


def test_stop_check_reuses_the_status_read(fake_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two TRexes listed, the daemon's run leaving at once: the check takes its first process list from the
    status fold-in after the wait, so the stop makes no read beyond the two every stop makes."""
    monkeypatch.setattr(session_mod, "POLL_S", 60.0)
    d = fake_daemon("--daemon-run", "--foreign")
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop with the ack on a dead port."""
        await s.connect(d.host, d.port)
        before = d.methods().count("get_trex_cmds")
        out = await s.stop(dead, True, force=True, owners_unknown_ack=True)
        assert d.methods().count("get_trex_cmds") - before == 2
        assert out["other_pids"] == []
    drive(body)


def test_stop_check_survives_a_list_read_fault(fake_daemon: Any, fake_trex: Any,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A process-list read that fails during the post-stop wait, after a kill that worked: the stop still
    answers (no 502), the wait ends, and the probe decides: a TRex answering cancels, naming the last list."""
    monkeypatch.setattr(session_mod, "POLL_S", 60.0)
    d, f, hooks = fake_daemon("--daemon-run", "--foreign", "--exit-lag", "30"), fake_trex(), Hooks()
    real = DaemonClient.trex_cmds
    reads = 0

    def failing(self: DaemonClient) -> Any:
        """The real read for the stop's first two (pre-stop list, status fold-in); then the link drops."""
        nonlocal reads
        reads += 1
        if reads > 2:
            raise DaemonUnreachable("connection reset")
        return real(self)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, make later list reads fail, time a Force stop."""
        await s.connect(d.host, d.port)
        monkeypatch.setattr(DaemonClient, "trex_cmds", failing)
        t0 = time.monotonic()
        out = await s.stop(f.rpc_port, True, force=True)
        assert time.monotonic() - t0 < session_mod.STOP_WAIT_S
        assert out["running"] is False
        assert out["other_pids"] == sorted([FOREIGN_PID, DAEMON_PID])
        assert hooks.calls == ["expect", "cancel"]
    drive(body, hooks=hooks)


def test_stop_with_one_trex_skips_the_check(fake_daemon: Any, fake_trex: Any,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the daemon's run on the host: the two process-list reads a stop always makes (the pre-stop one and
    the status fold-in after the wait), no more, no probe, done."""
    monkeypatch.setattr(session_mod, "POLL_S", 60.0)
    d, f, hooks = fake_daemon("--daemon-run"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, Force stop."""
        await s.connect(d.host, d.port)
        before = d.methods().count("get_trex_cmds")
        out = await s.stop(f.rpc_port, True, force=True)
        assert d.methods().count("get_trex_cmds") - before == 2
        assert out["other_pids"] == []
        assert hooks.calls == ["expect", "done"]
    drive(body, hooks=hooks)


def test_plain_stop_of_our_run_beside_a_hand_started_trex_cancels(fake_daemon: Any, fake_trex: Any) -> None:
    """A TRex started by hand after our Start, answering on the command port: a plain Stop of our run ends
    in cancel and names that TRex."""
    d, f, hooks = fake_daemon("--foreign-after-start"), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start stateless, Stop."""
        await s.connect(d.host, d.port)
        await s.start(CFG, {}, "", 20, True, None, f.rpc_port)
        out = await s.stop(f.rpc_port, True)
        assert "force_trex_kill" not in d.methods()
        assert out["other_pids"] == [FOREIGN_PID]
        assert hooks.calls[-1] == "cancel"
    drive(body, hooks=hooks)


def test_start_tells_the_hooks(fake_daemon: Any) -> None:
    """A stateless start reports the daemon, the command port the caller gave, the mode and the timeout."""
    d, hooks = fake_daemon(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start stateless with a port."""
        await s.connect(d.host, d.port)
        await s.start(CFG, {}, "", 20, True, None, 4701)
        assert hooks.started_with == (f"{d.host}:{d.port}", 4701, "stl", 20, None)
    drive(body, hooks=hooks)


def test_stf_start_tells_the_hooks_when_it_ends(fake_daemon: Any) -> None:
    """A stateful start reports mode stf, no command port and ends_at = started_at + d."""
    d, hooks = fake_daemon("--stf-scale", "0.05"), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start stateful."""
        await s.connect(d.host, d.port)
        out = await s.start(CFG, {}, "", 40, True, SFR2)
        assert hooks.started_with == (f"{d.host}:{d.port}", None, "stf", 40, out["run"]["started_at"] + 20)
    drive(body, hooks=hooks)


def test_stop_still_running_cancels(fake_daemon: Any, fake_trex: Any,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop after which the daemon still reports running ends in cancel, never done."""
    d, f, hooks = fake_daemon("--stop-keeps-running"), fake_trex(), Hooks()
    monkeypatch.setattr(session_mod, "STOP_WAIT_S", 0.5)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start, stop."""
        await s.connect(d.host, d.port)
        await started(s)
        assert (await s.stop(f.rpc_port, True))["running"] is True
        assert hooks.calls == ["expect", "cancel"]
    drive(body, hooks=hooks)


def test_an_expect_that_raises_cancels_and_sends_no_stop(fake_daemon: Any, fake_trex: Any) -> None:
    """An expect that raises still ends in cancel, and the stop is never sent: TRex keeps running."""
    d, f, hooks = fake_daemon(), fake_trex(), Hooks(expect_raises=True)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start, stop into the raising expect."""
        await s.connect(d.host, d.port)
        await started(s)
        with pytest.raises(RuntimeError, match="expect failed"):
            await s.stop(f.rpc_port, True)
        assert hooks.calls == ["expect", "cancel"]
        assert await asyncio.to_thread(DaemonClient(d.host, d.port).is_running) is True
    drive(body, hooks=hooks)


def test_stop_that_raises_after_expect_cancels(fake_daemon: Any, fake_trex: Any,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop_trex that never answers raises after expect; the hooks hear cancel."""
    d, f, hooks = fake_daemon("--hang", "stop_trex"), fake_trex(), Hooks()
    monkeypatch.setattr(session_mod, "CALL_TIMEOUT_S", 1.0)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start, stop into the hang."""
        await s.connect(d.host, d.port)
        await started(s)
        with pytest.raises(DaemonUnreachable):
            await s.stop(f.rpc_port, True)
        assert hooks.calls == ["expect", "cancel"]
    drive(body, hooks=hooks)


def test_stop_refused_never_expects(fake_daemon: Any, fake_trex: Any) -> None:
    """A stop refused before the stopping call (no confirm; no run of ours without force) reaches no hook."""
    d, f, hooks = fake_daemon(), fake_trex(), Hooks()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Both refusals."""
        await s.connect(d.host, d.port)
        await refused(s.stop(f.rpc_port, False), "confirm_required")
        await refused(s.stop(f.rpc_port, True), "not_ours")
        assert hooks.calls == []
    drive(body, hooks=hooks)


def test_daemon_message_is_published_while_connected(fake_daemon: Any) -> None:
    """After connect the 2 s poll publishes what changed: a run started behind the session's back shows as
    Running with its command and seq-less running_seq, then Idle again once it is stopped."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Connect, start a run through the client, watch the messages."""
        await s.connect(d.host, d.port)
        first = daemon_msgs(rec)[-1]
        assert first["state"] == "connected" and first["target"] == {"host": d.host, "port": d.port}
        assert first["status"] == {"state": "Idle", "verbose": "TRex is Idle"} and first["trex_cmds"] == []
        assert first["files_path"] == "/tmp/trex_files" and first["busy"] is False and first["run"] is None
        assert set(first) == {"type", "state", "target", "version", "status", "running_seq", "files_path",
                              "last_error", "trex_cmds", "reserved", "pushed", "busy", "run"}
        seq = DaemonClient(d.host, d.port).start_trex(CFG, {}, "me", 20, "")
        await wait_for(lambda: daemon_msgs(rec)[-1]["status"]["state"] == "Running", 4.5)
        last = daemon_msgs(rec)[-1]
        assert [c["cmd"] for c in last["trex_cmds"]] == [f"./_t-rex-64 --iom 0 --cfg {CFG} -i --no-key"]
        DaemonClient(d.host, d.port).stop_trex(seq)
        await wait_for(lambda: daemon_msgs(rec)[-1]["status"]["state"] == "Idle", 4.5)
        assert daemon_msgs(rec)[-1]["trex_cmds"] == []
    drive(body)


def test_hung_daemon_times_out_without_blocking_the_session(fake_daemon: Any, fake_trex: Any,
                                                            new_session: Any) -> None:
    """With is_running hanging: a Start is busy, the status call is DaemonUnreachable within
    CALL_TIMEOUT_S + 2, and the Control session keeps publishing stats throughout."""
    d, f = fake_daemon("--hang", "is_running"), fake_trex()

    async def body() -> None:
        """Hang the status read, try a Start, watch both sessions."""
        control_rec = Recorder()
        control = new_session(control_rec)
        await control.connect(ConnectParams(f.host, f.rpc_port, f.pub_port, 2.0))
        stats = lambda: len([m for m in control_rec.msgs if m["type"] == "stats"])  # noqa: E731
        daemon_rec = Recorder()
        s = DaemonSession(daemon_rec, lambda h, p: None)
        try:
            await s.connect(d.host, d.port)
            task = asyncio.create_task(s.status())
            await asyncio.to_thread(d.wait_line, "hang is_running")
            t0, before = time.monotonic(), stats()
            await refused(s.start(CFG, {}, "", 20, True), "busy")
            assert "start_trex" not in d.methods()
            with pytest.raises(DaemonUnreachable):
                await task
            assert time.monotonic() - t0 < CALL_TIMEOUT_S + 2
            assert stats() > before + 3, "the Control session stopped publishing stats"
            last = daemon_msgs(daemon_rec)[-1]
            assert last["state"] == "error" and last["last_error"]
        finally:
            await s.close()
    new_session.run(body())


STF_FLAGS = ("--stf-scale", "0.05")


def test_stf_start_runs_then_ends(fake_daemon: Any) -> None:
    """A d=20 run lasts about 1 s; the daemon message then says ended, with the recorded outcome."""
    d = fake_daemon(*STF_FLAGS)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start the stock profile and wait."""
        await s.connect(d.host, d.port)
        out = await s.start("/etc/trex_cfg.yaml", {"c": 1}, "", 20, True, SFR2)
        assert out["run"]["mode"] == "stf" and out["run"]["profile"] == "cap2/sfr2.yaml"
        assert out["run"]["duration_s"] == 20 and out["run"]["state"] == "running"
        assert out["run"]["outcome"] is None
        assert out["command_preview"].startswith(
            "nice -n -19 ./t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -f ")
        assert s.snapshot()["running_seq"] == out["seq"]
        await wait_for(lambda: (daemon_msgs(rec)[-1]["run"] or {}).get("state") == "ended", 4)
        last = daemon_msgs(rec)[-1]
        assert last["run"]["outcome"] == "TRex finished." and last["running_seq"] is None
    drive(body)


async def push_tg(d: Any, name: str, text: str) -> str:
    """Push `text` as `name` straight to the fake daemon; returns its path in files_path."""
    await asyncio.to_thread(DaemonClient(d.host, d.port).push_file, name, text)
    return f"/tmp/trex_files/{name}"


@pytest.mark.parametrize("kind", ["dotdot", "etc", "missing", "relative_pcap", "unlisted_pcap", "nan",
                                  "outside"])
def test_stf_start_refuses_an_invalid_profile(fake_daemon: Any, kind: str) -> None:
    """A path outside the two rules, a missing file, an uploaded profile whose templates are not listed
    absolute tg-*.pcap names, and non-finite numbers: invalid_profile, with no start_trex."""
    d = fake_daemon(*STF_FLAGS)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Set the profile up, then Start."""
        await s.connect(d.host, d.port)
        pcap = "/tmp/trex_files/tg-0123456789ab-a.pcap"      # pushed below, so listed
        gone = "/tmp/trex_files/tg-0123456789ab-gone.pcap"   # never pushed
        paths = {
            "dotdot": "cap2/../x.yaml", "etc": "/etc/x.yaml", "missing": "cap2/missing.yaml",
            "outside": "/tmp/trex_files/x.yaml",
            "relative_pcap": await push_tg(d, "tg-0123456789ab-rel.yaml", tg_profile(["cap2/Oracle.pcap"])),
            "unlisted_pcap": await push_tg(d, "tg-0123456789ab-gone.yaml", tg_profile([gone])),
            "nan": await push_tg(d, "tg-0123456789ab-nan.yaml",
                                 tg_profile([pcap]).replace("cps: 1.0", "cps: .nan")),
        }
        await push_tg(d, "tg-0123456789ab-a.pcap", "x")
        e = await refused(s.start("/etc/trex_cfg.yaml", {}, "", 20, True,
                                  StfOptions(paths[kind], 20, 1.0, 1000, 100)), "invalid_profile")
        assert paths[kind] in e.message or kind in ("unlisted_pcap", "relative_pcap", "nan")
        assert "start_trex" not in d.methods()
    drive(body)


def test_stf_start_accepts_an_uploaded_profile(fake_daemon: Any) -> None:
    """An uploaded tg-*.yaml whose templates are listed absolute tg-*.pcap names starts."""
    d = fake_daemon(*STF_FLAGS)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Push the pcap and the profile, Start."""
        await s.connect(d.host, d.port)
        pcap = await push_tg(d, "tg-0123456789ab-a.pcap", "x")
        path = await push_tg(d, "tg-0123456789ab-p.yaml", tg_profile([pcap]))
        prof = await s.stf_profile(path)
        assert prof["errors"] == [] and prof["templates"] == [pcap] and prof["duration"] == 10.0
        out = await s.start("/etc/trex_cfg.yaml", {}, "", 20, True, StfOptions(path, 20, 1.0, 0, 0))
        assert out["run"]["profile"] == path
    drive(body)


@pytest.mark.parametrize("d_,m,l,rx", [(9, 1.0, 0, 0), (3601, 1.0, 0, 0), (20, 0, 0, 0), (20, 1000.1, 0, 0),
                                       (20, 1.0, -1, 0), (20, 1.0, 100001, 0), (20, 1.0, 0, -1),
                                       (20, 1.0, 0, 65536)])
def test_stf_bounds(fake_daemon: Any, d_: int, m: float, l: int, rx: int) -> None:  # noqa: E741
    """Each out-of-bounds d, m, l or rx_check is a ValueError and reaches no start_trex."""
    d = fake_daemon(*STF_FLAGS)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start with the bad value."""
        await s.connect(d.host, d.port)
        with pytest.raises(ValueError):
            await s.start("/etc/trex_cfg.yaml", {}, "", 20, True, StfOptions("cap2/sfr2.yaml", d_, m, l, rx))
        assert "start_trex" not in d.methods()
    drive(body)


def test_stf_trex_args_must_be_empty(fake_daemon: Any) -> None:
    """Free text beside an STF run is a ValueError (D13), with no start_trex."""
    d = fake_daemon(*STF_FLAGS)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start with --no-scapy-server."""
        await s.connect(d.host, d.port)
        with pytest.raises(ValueError, match="empty"):
            await s.start("/etc/trex_cfg.yaml", {}, "--no-scapy-server", 20, True, SFR2)
        assert "start_trex" not in d.methods()
    drive(body)


def test_stop_of_our_stf_run_skips_the_owner_read(fake_daemon: Any) -> None:
    """Nothing listens on rpc_port, yet Stop of our own STF run works: no owner read, stop_trex with our seq,
    and the run is `stopped`."""
    d = fake_daemon()        # a real-length run: it must still be going when Stop arrives
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start, Stop."""
        await s.connect(d.host, d.port)
        out = await s.start("/etc/trex_cfg.yaml", {}, "", 20, True, SFR2)
        res = await s.stop(dead, True)
        assert res["running"] is False
        assert d.methods().count("stop_trex") == 1 and "force_trex_kill" not in d.methods()
        run = s.snapshot()["run"]
        assert run["state"] == "stopped" and run["seq"] == out["seq"] and run["outcome"] == "TRex finished."
        assert s.snapshot()["running_seq"] is None
    drive(body)


def test_force_stop_of_a_hand_started_stf_run_is_not_daemon_run(fake_daemon: Any) -> None:
    """A -f run started by hand, with the daemon Idle: Force stop with the ack is not_daemon_run, and nothing
    is killed."""
    d = fake_daemon("--foreign-stf")

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Force stop with the ack."""
        await s.connect(d.host, d.port)
        await refused(s.stop(free_port(), True, force=True, owners_unknown_ack=True), "not_daemon_run")
        assert changed(d) == set()
    drive(body)


def test_force_stop_of_a_foreign_stf_run_needs_the_ack(fake_daemon: Any) -> None:
    """A -f run this backend did not start, the daemon running: Stop is not_ours; Force stop without the ack
    is owners_unknown ('stateful', no port read); with the ack it kills once."""
    d = fake_daemon("--foreign-stf", "--daemon-run")
    dead = free_port()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """The three attempts."""
        await s.connect(d.host, d.port)
        await refused(s.stop(dead, True), "not_ours")
        e = await refused(s.stop(dead, True, force=True), "owners_unknown")
        assert "stateful" in e.message and e.extra == {"stf": True}
        assert "force_trex_kill" not in d.methods()
        await s.stop(dead, True, force=True, owners_unknown_ack=True)
        assert d.methods().count("force_trex_kill") == 1
        assert "owners_unknown_ack" in [m for m in rec.msgs if m["type"] == "log"][0]["message"]
    drive(body)


def test_run_record_survives_a_reconnect(fake_daemon: Any) -> None:
    """The record outlives disconnect() and a connect to the same target; a connect elsewhere clears it."""
    d, other = fake_daemon(), fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start, disconnect, reconnect, then go elsewhere."""
        await s.connect(d.host, d.port)
        out = await s.start("/etc/trex_cfg.yaml", {}, "", 20, True, SFR2)
        await s.disconnect()
        last = daemon_msgs(rec)[-1]
        assert last["state"] == "disconnected" and last["run"]["seq"] == out["seq"]
        await s.connect(d.host, d.port)
        assert s.snapshot()["run"]["seq"] == out["seq"] and s.snapshot()["running_seq"] == out["seq"]
        await s.connect(other.host, other.port)
        assert s.snapshot()["run"] is None and s.snapshot()["running_seq"] is None
        assert s.snapshot()["pushed"] == []
    drive(body)


def test_disconnect_during_connect_leaves_the_session_disconnected(fake_daemon: Any) -> None:
    """disconnect() does not take the op slot, so it can run while connect() awaits its reads: the connect is
    then refused (not_connected), and the session has no client, no poll task and state disconnected."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Hold connect inside its `devices` read, disconnect, let the read finish."""
        reached, release = asyncio.Event(), asyncio.Event()
        real_call = s._call

        async def held(fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
            """Pass every call through; park the `devices` read until released."""
            out = await real_call(fn, *args, **kw)
            if getattr(fn, "__name__", "") == "devices":
                reached.set()
                await release.wait()
            return out
        s._call = held                      # type: ignore[method-assign]
        task = asyncio.create_task(s.connect(d.host, d.port))
        await asyncio.wait_for(reached.wait(), 5)
        await s.disconnect()
        release.set()
        with pytest.raises(DaemonRefused) as e:
            await task
        assert e.value.code == "not_connected"
        assert s._poll_task is None and s._client is None and s._op is False
        snap = s.snapshot()
        assert snap["state"] == "disconnected" and snap["target"] is None and snap["busy"] is False
        assert daemon_msgs(rec)[-1]["state"] == "disconnected"
        await asyncio.sleep(0.1)
        assert s._poll_task is None
    drive(body)


def test_stf_profiles_lists_stock_and_uploaded(fake_daemon: Any) -> None:
    """Stock holds cap2/sfr2.yaml (yaml only, matching the rule); uploaded lists only the tg-*.yaml pushed."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """List both sources before and after two pushes."""
        await s.connect(d.host, d.port)
        stock = await s.stf_profiles("stock")
        assert stock["source"] == "stock" and "cap2/sfr2.yaml" in stock["files"]
        assert all(f.endswith(".yaml") and f.split("/")[0] in ("cap2", "avl") for f in stock["files"])
        assert any(f.startswith("avl/") for f in stock["files"])
        assert (await s.stf_profiles("uploaded")) == {"source": "uploaded", "files": []}
        await push_tg(d, "tg-0123456789ab-x.yaml", "a: 1")
        await push_tg(d, "other.yaml", "a: 1")
        assert (await s.stf_profiles("uploaded")) == {
            "source": "uploaded", "files": ["/tmp/trex_files/tg-0123456789ab-x.yaml"]}
        with pytest.raises(ValueError):
            await s.stf_profiles("other")  # type: ignore[arg-type]
    drive(body)


def test_stf_profile_reports_errors_in_the_reply(fake_daemon: Any) -> None:
    """A stock profile parses (duration, three templates); a missing file is an errors entry with its path."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Read a good profile and a missing one."""
        await s.connect(d.host, d.port)
        good = await s.stf_profile("cap2/sfr2.yaml")
        assert good["errors"] == [] and good["duration"] == 10.0
        assert good["templates"] == ["cap2/Oracle.pcap", "cap2/smtp.pcap", "cap2/dns.pcap"]
        gone = await s.stf_profile("cap2/missing.yaml")
        assert len(gone["errors"]) == 1 and "cap2/missing.yaml" in gone["errors"][0]
        await refused(s.stf_profile("/etc/passwd"), "invalid_profile")
    drive(body)


def test_stf_start_failure_is_a_fault(fake_daemon: Any) -> None:
    """The daemon's recorded start_bad_cfg (-11) comes back as a DaemonFault and the run record stays null."""
    d = fake_daemon("--stf-fails")

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Start an STF run that fails."""
        await s.connect(d.host, d.port)
        with pytest.raises(DaemonFault) as e:
            await s.start("/etc/trex_cfg.yaml", {}, "", 20, True, SFR2)
        assert e.value.code == -11
        assert s.snapshot()["run"] is None and s.snapshot()["running_seq"] is None
    drive(body)


def test_log_and_busy_flag(fake_daemon: Any) -> None:
    """log() reads the trex and daemon logs; the busy flag is published true during an operation."""
    d = fake_daemon()

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Read both logs, run a push, look at the busy messages."""
        r = await s.connect(d.host, d.port)
        assert "TRex" in await s.log("trex") and "trex_server" in await s.log("daemon")
        with pytest.raises(ValueError):
            await s.log("other")  # type: ignore[arg-type]
        await s.push(r["config_yaml"], None, True)
        flags = [m["busy"] for m in daemon_msgs(rec)]
        assert True in flags and flags[-1] is False
    drive(body)
