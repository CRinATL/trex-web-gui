"""Pause, Resume and Update sync the ports before they check the cached profile state (stop-race-out-1): a
port whose cache still says idle (or TX) while the server has moved on is controlled after the sync, not
refused from the stale cache. The stub client logs sync and the RPC in one list to pin their order. Every
acquire of ours is marked first (stop-race-flake-1), and the ARP resolve is short attempts, one job each
(FU4-2)."""
import asyncio
import time
from typing import Any

import pytest
from session_rig import IDLE, PAUSE, PEER_MAC, TX, Rig, StubClient, StubPort
from trex_web_gui import trexapi
from trex_web_gui.sessions import SessionState, TrexSession
from trex_web_gui.sessions.port_config import LayerRequest
from trex_web_gui.trexapi import CommandRefused


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Rig:
    """A Rig on this test's monkeypatch."""
    return Rig(monkeypatch)


class SyncPort(StubPort):
    STATE_TX = TX
    STATE_PAUSE = PAUSE

    def __init__(self, log: list[str], cached: int, synced: int, bad: bool = False) -> None:
        """A port whose cached default profile is `cached`; sync() logs, sets it to `synced` and answers an rc
        that is bad when `bad` (then the cache is left alone)."""
        super().__init__()
        self.log, self.synced, self.bad = log, synced, bad
        self.profile_state_list: dict[str, int] = {"_": cached}
        self.status: dict[str, Any] = {}

    def sync(self) -> Any:
        """Logs "sync"; unless bad, sets the cached state to the synced one."""
        self.log.append("sync")
        if not self.bad:
            self.profile_state_list["_"] = self.synced
        return type("RC", (), {"bad": lambda s: self.bad, "err": lambda s: "sync failed: timeout"})()


class CmdClient(StubClient):
    def __init__(self, log: list[str], cached: int, synced: int, bad: bool = False) -> None:
        """A StubClient whose port 0 is a SyncPort, with pause/resume/update logging into `log`."""
        super().__init__()
        self.log = log
        self.ports[0] = SyncPort(log, cached, synced, bad)

    def pause(self, ports: list[int]) -> None:
        """Logs "pause"."""
        self.log.append("pause")

    def resume(self, ports: list[int]) -> None:
        """Logs "resume"."""
        self.log.append("resume")

    def update(self, ports: list[int], mult: str) -> None:
        """Logs "update"."""
        self.log.append("update")


def with_client(rig: Rig, cached: int, synced: int, bad: bool = False) -> list[str]:
    """Swaps in a CmdClient (cache `cached`, server `synced`) and returns its shared log."""
    log: list[str] = []
    rig.c = CmdClient(log, cached, synced, bad)
    rig.s._client = rig.c
    return log


CASES = [
    ("pause", IDLE, TX, lambda s: s.pause([0])),
    ("update", IDLE, TX, lambda s: s.update([0], "2kpps")),
    ("resume", TX, PAUSE, lambda s: s.resume([0])),
]


@pytest.mark.parametrize(("rpc", "cached", "synced", "call"), CASES, ids=[c[0] for c in CASES])
def test_command_syncs_then_sends(rig: Rig, rpc: str, cached: int, synced: int, call: Any) -> None:
    log = with_client(rig, cached, synced)
    rig.run(call)
    assert log == ["sync", rpc]


@pytest.mark.parametrize(("rpc", "cached", "synced", "call"), CASES, ids=[c[0] for c in CASES])
def test_failed_sync_raises_translated_and_sends_nothing(rig: Rig, rpc: str, cached: int, synced: int,
                                                         call: Any) -> None:
    log = with_client(rig, cached, synced, bad=True)

    async def body(s: TrexSession) -> None:
        with pytest.raises(trexapi.TrexApiError) as e:
            await call(s)
        assert not isinstance(e.value, CommandRefused)
    rig.run(body)
    assert log == ["sync"]


# --- every acquire of ours is marked before its RPC (stop-race-flake-1) ---

class AcquireClient(StubClient):
    """A StubClient whose acquire and reset log into `log`, beside the logged marks."""

    def __init__(self, log: list[str]) -> None:
        """Port 0 owned by this user and not acquired, port 1 owned by someone else."""
        super().__init__()
        self.log = log
        self.ctx = type("Ctx", (), {"username": "me"})()
        self.ports[0].owner = "me"
        self.ports[0].is_acquired = lambda: False          # type: ignore[method-assign]
        self.ports[1].owner = "someone"

    def acquire(self, ports: list[int], force: bool = False) -> None:
        """Logs "acquire <ports> <force>"."""
        self.log.append(f"acquire {ports} {force}")

    def reset(self, ports: list[int]) -> None:
        """Logs "reset <ports>"."""
        self.log.append(f"reset {ports}")


def marked(rig: Rig, monkeypatch: pytest.MonkeyPatch, reset_raises: bool = False) -> list[str]:
    """Swaps in an AcquireClient and logs mark_pending / unmark_pending beside its RPCs; acquire_marked is
    left real, over the patched marks."""
    log: list[str] = []
    rig.c = AcquireClient(log)
    rig.s._client = rig.c
    for mod in (trexapi, trexapi.client):                   # acquire_marked calls the client module's own
        monkeypatch.setattr(mod, "mark_pending", lambda c, ports: log.append(f"mark {ports}"))
        monkeypatch.setattr(mod, "unmark_pending", lambda c, ports: log.append(f"unmark {ports}"))
    monkeypatch.setattr(trexapi, "check_not_foreign", lambda c, ports: None)
    if reset_raises:
        # Logs "reset", then raises.
        def boom(ports: list[int]) -> None:
            log.append("reset")
            raise RuntimeError("reset failed")
        rig.c.reset = boom                                  # type: ignore[method-assign]
    return log


@pytest.mark.parametrize("force", [False, True])
def test_acquire_marks_before_the_rpc(rig: Rig, monkeypatch: pytest.MonkeyPatch, force: bool) -> None:
    log = marked(rig, monkeypatch)
    rig.run(lambda s: s.acquire([0], force))
    assert log == ["mark [0]", f"acquire [0] {force}"]


def test_reacquire_marks_before_the_rpc(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    log = marked(rig, monkeypatch)
    rig.run(lambda s: s.reacquire())
    assert log == ["mark [0]", "acquire [0] True"]


def test_reset_marks_before_the_rpc(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    log = marked(rig, monkeypatch)
    rig.run(lambda s: s.reset([0]))
    assert log == ["mark [0]", "reset [0]"]


def test_failed_reset_unmarks(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    log = marked(rig, monkeypatch, reset_raises=True)

    async def body(s: TrexSession) -> None:
        with pytest.raises(Exception, match="reset failed"):
            await s.reset([0])
    rig.run(body)
    assert log == ["mark [0]", "reset", "unmark [0]"]


# --- the ARP resolve is short attempts, one control-executor job each (FU4-2) ---

L3 = LayerRequest("L3", None, "1.1.1.1", "1.1.1.99", [], None)
ARP_TEXT = "port(s) [1]: link is DOWN"


def attempts(rig: Rig, monkeypatch: pytest.MonkeyPatch,
             answers: dict[int, Any] | None = None) -> list[tuple[float, bool]]:
    """Stubs arp_attempt: each call sleeps 0.05 s, logs "arp_attempt" into rig.log, records (listen_s, first)
    in the returned list and answers answers[n] for attempt n (1-based; an exception is raised), else None."""
    seen: list[tuple[float, bool]] = []

    # one stubbed attempt
    def attempt(c: Any, port: int, listen_s: float, first: bool) -> Any:
        seen.append((listen_s, first))
        n = len(seen)
        t0 = time.monotonic()
        time.sleep(0.05)
        rig.log.append(("arp_attempt", rig.jobs, t0, time.monotonic()))
        a = (answers or {}).get(n)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(trexapi, "arp_attempt", attempt)
    return seen


def names(rig: Rig) -> list[str]:
    """The stubbed calls in the order they ran."""
    return [n for n, *_ in rig.log]


def test_a_job_queued_after_the_first_attempt_runs_before_the_last(rig: Rig,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    attempts(rig, monkeypatch)

    async def body(s: TrexSession) -> None:
        apply = asyncio.ensure_future(s.apply_layer(1, L3))
        while not any(n == "arp_attempt" for n, *_ in rig.log):    # the first attempt has run
            await asyncio.sleep(0.005)
        await s._run(rig.stub("other", None))
        await apply
    rig.run(body)
    order = names(rig)
    assert order.index("other") < len(order) - 1 - order[::-1].index("arp_attempt")


def test_six_attempts_then_arp_unresolved(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = attempts(rig, monkeypatch)

    async def body(s: TrexSession) -> None:
        r = await s.apply_layer(1, L3)
        assert r.arp == ("unresolved", None, "no reply")
    rig.run(body)
    assert names(rig) == ["set_vlan", "set_l3", *["arp_attempt"] * 6, "arp_unresolved"]
    assert seen == [(0.5, True)] + [(0.5, False)] * 5


def test_a_reply_on_attempt_2_stops_the_loop(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts(rig, monkeypatch, {2: trexapi.ArpResult("resolved", PEER_MAC, None)})

    async def body(s: TrexSession) -> None:
        assert (await s.apply_layer(1, L3)).arp == ("resolved", PEER_MAC, None)
    rig.run(body)
    assert names(rig) == ["set_vlan", "set_l3", "arp_attempt", "arp_attempt"]


def test_an_unresolved_failure_on_attempt_2_stops_the_loop(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts(rig, monkeypatch, {2: trexapi.ArpResult("unresolved", None, ARP_TEXT)})

    async def body(s: TrexSession) -> None:
        assert (await s.apply_layer(1, L3)).arp == ("unresolved", None, ARP_TEXT)
    rig.run(body)
    assert names(rig) == ["set_vlan", "set_l3", "arp_attempt", "arp_attempt"]
    assert rig.logs("warning") == [f"Apply L3 configuration to port 1: L3 set, ARP unresolved: {ARP_TEXT}"]


def test_service_turned_off_between_attempts_ends_unresolved_and_restores(
        rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    text = "port(s) [1]: must be under service mode"
    attempts(rig, monkeypatch, {2: trexapi.ArpResult("unresolved", None, text)})

    async def body(s: TrexSession) -> None:
        assert (await s.apply_layer(1, L3)).arp == ("unresolved", None, text)
    rig.run(body)
    assert rig.c.calls == [("set_service_mode", 1, True), ("set_service_mode", 1, False)]


def test_a_loss_in_an_attempt_raises_marks_lost_and_stops(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = attempts(rig, monkeypatch, {1: trexapi.ServerUnreachable("gone")})

    async def body(s: TrexSession) -> None:
        with pytest.raises(trexapi.ServerUnreachable):
            await s.apply_layer(1, L3)
        assert s._state == SessionState.LOST
    rig.run(body)
    assert len(seen) == 1 and "arp_unresolved" not in names(rig)


def test_stop_on_the_applys_port_runs_between_two_attempts(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    attempts(rig, monkeypatch)
    monkeypatch.setattr(trexapi, "check_ports", lambda c, ports, need_owned=True: None)
    rig.s._stop_all_profiles = lambda c, ports: rig.stub("stop", None)()      # type: ignore[method-assign]

    async def body(s: TrexSession) -> None:
        apply = asyncio.ensure_future(s.apply_layer(1, L3))
        while not any(n == "arp_attempt" for n, *_ in rig.log):
            await asyncio.sleep(0.005)
        await s.stop([1])                                       # accepted: the Apply's port is not refused
        assert (await apply).arp == ("unresolved", None, "no reply")
    rig.run(body)
    order = names(rig)
    i = order.index("stop")
    assert order[i - 1] == "arp_attempt" and order[i + 1] == "arp_attempt"
    assert order.count("arp_attempt") == 6


def test_the_connection_ending_between_attempts_stops_the_resolve(rig: Rig,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    n = 0

    # attempt 2 ends the connection, as a loss noticed elsewhere would
    def attempt(c: Any, port: int, listen_s: float, first: bool) -> None:
        nonlocal n
        n += 1
        rig.log.append(("arp_attempt", rig.jobs, 0.0, 0.0))
        if n == 2:
            rig.s._state = SessionState.LOST
    monkeypatch.setattr(trexapi, "arp_attempt", attempt)
    req = LayerRequest("L3", None, "1.1.1.1", "1.1.1.99", [], (False, None))

    async def body(s: TrexSession) -> None:
        r = await s.apply_layer(1, req)
        assert r.arp == ("unresolved", None, "the connection ended; the resolve stopped")
        jobs.append(rig.jobs)                           # before close() runs its own disconnect job
    jobs: list[int] = []
    rig.run(body)
    order = names(rig)
    assert order.count("arp_attempt") == 2 and order[-1] == "arp_attempt"
    assert jobs == [rig.log[-1][1]]                  # no job of any kind after the last attempt
    assert ("Apply L3 configuration to port 1: L3 set, ARP unresolved: the connection ended; the resolve "
            "stopped") in rig.logs("warning")
    assert not [m for m in rig.logs("info") if m.endswith(": done")] and rig.logs("error") == []
