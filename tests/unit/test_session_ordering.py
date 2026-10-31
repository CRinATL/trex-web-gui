"""Pause, Resume and Update sync the ports before they check the cached profile state (stop-race-out-1): a
port whose cache still says idle (or TX) while the server has moved on is controlled after the sync, not
refused from the stale cache. The stub client logs sync and the RPC in one list to pin their order."""
from typing import Any

import pytest
from session_rig import IDLE, PAUSE, TX, Rig, StubClient, StubPort
from trex_web_gui import trexapi
from trex_web_gui.sessions import TrexSession
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
