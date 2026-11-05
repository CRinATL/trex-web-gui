"""EmuSession's profile load and removal against tests/fake/fake_emu.py: the exact RPC order, what is never
sent, rate pacing on a fake clock, partial failures, the refusals made before any RPC, `busy`, the
generation, and the cleanup after a cancelled write or connect. Profiles run in the real worker
(tests/fixtures/profiles/emu_two_ns.py: 2 namespaces by default, 3 clients each)."""
import asyncio
import json
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from support import FIXTURES
from trex_web_gui.sessions import EmuRefused, EmuSession
from trex_web_gui.sessions.emu_session import _Progress
from trex_web_gui.trexapi import ServerUnreachable, TrexApiError
from trex_web_gui.trexapi.emu import EmuRpc, Listing, NsKey

Factory = Callable[..., Any]
PROFILE = str(FIXTURES / "profiles" / "emu_two_ns.py")
WIRE = json.loads((FIXTURES / "p7" / "profile_wire.json").read_text())["requests"]
WRITES = {"ctx_set_def_plugins", "ctx_add", "ctx_client_set_def_plugins", "ctx_client_add",
          "ctx_client_remove", "ctx_remove"}
CONFIRMED: dict[str, Any] = {"confirm": True}


def run(coro: Awaitable[Any]) -> Any:
    """Run `coro` to completion on a fresh loop."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def writes(fake: Any) -> list[dict[str, Any]]:
    """The fake's logged write requests, in order."""
    return [r for r in fake.requests() if r["method"] in WRITES]


def methods(fake: Any) -> list[str]:
    """Every method the fake has seen, in order."""
    return [r["method"] for r in fake.requests()]


async def connected(f: Any, **kw: Any) -> EmuSession:
    """A session connected to the fake, pinging rarely."""
    s = EmuSession(kw.pop("publish", lambda _m: None), ping_s=kw.pop("ping_s", 60), **kw)
    await s.connect(f.host, f.port, 2.0)
    return s


def test_load_rpc_order(fake_emu: Factory) -> None:
    """On an empty server a load sends exactly the recorded wire form of the profile, in the vendored client's
    order; with foreign namespaces (acknowledged) each one is first emptied and removed."""
    f = fake_emu()

    async def body() -> None:
        s = await connected(f)
        try:
            snap = await s.load_profile(PROFILE, [], **CONFIRMED)
            assert snap["loaded"]["namespaces"] == 2 and snap["loaded"]["clients"] == 6
            assert snap["loaded"]["partial"] is False and snap["busy"] is None
        finally:
            await s.close()
    run(body())
    assert writes(f) == WIRE
    g = fake_emu("--foreign-ns", "2", "--clients", "3")

    async def replace() -> None:
        s = await connected(g)
        try:
            await s.load_profile(PROFILE, [], foreign_ack=True, **CONFIRMED)
            listed = await s.namespaces()
            assert [(n["vport"], n["active_clients"], n["ours"]) for n in listed["namespaces"]] == [
                (0, 3, True), (1, 3, True)]
        finally:
            await s.close()
    run(replace())
    got = writes(g)
    assert [r["method"] for r in got[:4]] == ["ctx_client_remove", "ctx_remove"] * 2
    assert [r["params"]["macs"] for r in got[:4:2]] == [
        [[0, 0, 0, 0, 0, c] for c in range(3)], [[0, 0, 0, 1, 0, c] for c in range(3)]]
    assert got[4:] == WIRE


def test_load_never_sends_stop_autotriggers(fake_emu: Factory) -> None:
    """Neither a load over foreign namespaces nor a removal sends ctx_stop_autotriggers (the shipped server
    lacks it), shutdown, or a request that clears."""
    f = fake_emu("--foreign-ns", "1", "--clients", "2")

    async def body() -> None:
        s = await connected(f)
        try:
            await s.load_profile(PROFILE, [], foreign_ack=True, **CONFIRMED)
            await s.remove_all(**CONFIRMED)
        finally:
            await s.close()
    run(body())
    seen = f.requests()
    assert {"ctx_remove", "ctx_client_remove", "ctx_add"} <= {r["method"] for r in seen}
    assert not {"ctx_stop_autotriggers", "shutdown"} & {r["method"] for r in seen}
    assert not any(r["params"].get("clear") for r in seen)


class FakeClock:
    """A clock that only a sleep advances; records each sleep."""

    def __init__(self) -> None:
        """Start at 100 s with nothing slept."""
        self.now, self.slept = 100.0, []

    def clock(self) -> float:
        """The fake time."""
        return self.now

    async def sleep(self, s: float) -> None:
        """Advance the fake time by `s` and remember it."""
        self.slept.append(s)
        self.now += s


def test_rate_pacing_on_fake_clock(fake_emu: Factory) -> None:
    """1,000 clients at 500 a second take 2 s of (fake) sleeping, spread over the chunks; unpaced loads sleep
    not at all."""
    f = fake_emu()
    fc = FakeClock()

    async def body(rate: float | None) -> None:
        s = await connected(f, clock=fc.clock, sleep=fc.sleep)
        try:
            await s.load_profile(PROFILE, ["--clients", "500"], max_rate=rate, foreign_ack=True, **CONFIRMED)
            assert (await s.namespaces())["namespaces"][1]["active_clients"] == 500
        finally:
            await s.close()
    run(body(500))
    assert 1.9 <= sum(fc.slept) <= 2.1 and len(fc.slept) >= 4
    fc.slept.clear()
    run(body(None))
    assert fc.slept == []


def test_partial_failure_reports_progress(fake_emu: Factory) -> None:
    """A failure in the second namespace's clients names how far the load got, in the error and in the
    snapshot's `loaded` (partial); the busy flag is gone and the generation moved."""
    f = fake_emu("--foreign-ns", "1", "--fail", "ctx_client_add=2")
    out: list[dict[str, Any]] = []

    async def body() -> None:
        s = await connected(f, publish=out.append)
        try:
            with pytest.raises(TrexApiError) as ei:
                await s.load_profile(PROFILE, [], foreign_ack=True, **CONFIRMED)
            assert "injected failure" in ei.value.message
            assert ("removed all; added 2 of 2 namespaces and 3 of 6 clients; "
                    "the server now holds a partial profile") in ei.value.message
            snap = s.snapshot()
            assert snap["busy"] is None and snap["generation"] == 1 and snap["state"] == "connected"
            assert snap["loaded"]["partial"] is True
            assert (snap["loaded"]["namespaces"], snap["loaded"]["clients"]) == (2, 3)
        finally:
            await s.close()
    run(body())
    assert any(m["busy"] and m["busy"]["kind"] == "loading" and m["busy"]["total"] == 6 for m in out)


def test_failure_while_removing_names_the_removal(fake_emu: Factory) -> None:
    """A failure removing the second foreign namespace says how many namespaces were removed."""
    f = fake_emu("--foreign-ns", "3", "--fail", "ctx_remove=2")

    async def body() -> None:
        s = await connected(f)
        try:
            with pytest.raises(TrexApiError) as ei:
                await s.remove_all(foreign_ack=True, **CONFIRMED)
            assert "removed 1 of 3 namespaces; the server holds the rest" in ei.value.message
            assert s.snapshot()["generation"] == 1 and s.snapshot()["loaded"] is None
        finally:
            await s.close()
    run(body())


def test_ipfix_removal_loss_names_the_cause(fake_emu: Factory) -> None:
    """Removing a namespace that loaded ipfix leaves ctx_remove unanswered on the real server (it panics): the
    session is lost, with text that names the ipfix plugin."""
    f = fake_emu("--foreign-ns", "1", "--plugins", "arp,ipfix", "--hang", "ctx_remove")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        await s.connect(f.host, f.port, 0.5)
        try:
            with pytest.raises(ServerUnreachable) as ei:
                await s.remove_all(foreign_ack=True, crash_ack=True, **CONFIRMED)
            assert "ipfix" in ei.value.message
            snap = s.snapshot()
            assert snap["state"] == "lost" and "ipfix" in snap["error"] and snap["busy"] is None
        finally:
            await s.close()
    run(body())


@pytest.mark.parametrize(("plugins", "named"), [("arp,tdl", "tdl"), ("arp,icmp", None)])
def test_loss_label_names_only_the_plugins_the_namespace_held(fake_emu: Factory, plugins: str,
                                                              named: str | None) -> None:
    """The lost-connection text names ipfix or tdl only when that namespace's plug_names held them: a
    namespace that held neither is not blamed."""
    f = fake_emu("--foreign-ns", "1", "--plugins", plugins, "--hang", "ctx_remove")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        await s.connect(f.host, f.port, 0.5)
        try:
            with pytest.raises(ServerUnreachable) as ei:
                await s.remove_all(foreign_ack=True, crash_ack=True, **CONFIRMED)
            text = ei.value.message
            assert "ipfix" not in text
            assert ("tdl plugin was being removed" in text) == (named == "tdl")
            assert ("tdl" in text) == (named == "tdl")
        finally:
            await s.close()
    run(body())


def test_foreign_ipfix_or_tdl_namespaces_need_the_crash_ack(fake_emu: Factory) -> None:
    """Removing a foreign namespace whose plugins include ipfix or tdl (trex-emu v3.08 crashes, R33) is
    refused `crash_risk` before any write, for a load and a removal, even with `foreign_ack`; the message
    names the namespace and the restart. With `crash_ack` it goes ahead. Our own namespaces never need it."""
    f = fake_emu("--foreign-ns", "2", "--clients", "1", "--plugins", "arp,ipfix")

    async def body() -> None:
        s = await connected(f)
        try:
            for call in (lambda: s.load_profile(PROFILE, [], foreign_ack=True, **CONFIRMED),
                         lambda: s.remove_all(foreign_ack=True, **CONFIRMED)):
                with pytest.raises(EmuRefused) as ei:
                    await call()
                assert ei.value.code == "crash_risk" and "restart" in ei.value.message
                assert "2 namespace(s)" in ei.value.message and "vport 0" in ei.value.message
            with pytest.raises(EmuRefused) as ei:           # no foreign_ack: that refusal comes first
                await s.remove_all(**CONFIRMED)
            assert ei.value.code == "foreign_namespaces"
            assert writes(f) == [] and s.snapshot()["generation"] == 0
            await s.load_profile(PROFILE, [], foreign_ack=True, crash_ack=True, **CONFIRMED)
            assert len(writes(f)) > 0
            await s.remove_all(**CONFIRMED)                             # ours now: nothing foreign, no ack
        finally:
            await s.close()
    run(body())


def test_crash_ack_is_only_needed_for_namespace_level_plugins(fake_emu: Factory) -> None:
    """A foreign namespace without ipfix or tdl needs only `foreign_ack`."""
    f = fake_emu("--foreign-ns", "1", "--plugins", "arp,mdns")

    async def body() -> None:
        s = await connected(f)
        try:
            await s.remove_all(foreign_ack=True, **CONFIRMED)
        finally:
            await s.close()
    run(body())


def test_load_refuses_without_confirm_before_any_rpc(fake_emu: Factory) -> None:
    """No confirm, a bad rate, or no connection refuses with nothing sent beyond the handshake."""
    f = fake_emu("--foreign-ns", "2")

    async def body() -> None:
        s = await connected(f)
        try:
            for call in (lambda: s.load_profile(PROFILE, []), lambda: s.remove_all(),
                         lambda: s.load_profile(PROFILE, [], confirm=False, foreign_ack=True)):
                with pytest.raises(EmuRefused) as ei:
                    await call()
                assert ei.value.code == "confirm_required"
            for rate in (0, 100001, -5, float("nan"), True):
                with pytest.raises(ValueError, match="max_rate"):
                    await s.remove_all(max_rate=rate, **CONFIRMED)    # type: ignore[arg-type]
            assert s.snapshot()["generation"] == 0
        finally:
            await s.close()
        with pytest.raises(EmuRefused) as ei:
            await s.remove_all(**CONFIRMED)
        assert ei.value.code == "not_connected"
    run(body())
    assert methods(f) == ["api_sync_v2", "get_version"]


def test_foreign_namespaces_need_the_ack(fake_emu: Factory) -> None:
    """Namespaces this backend did not add refuse a load or removal until `foreign_ack`, after one listing and
    before any write; namespaces it added itself do not."""
    f = fake_emu("--foreign-ns", "2", "--clients", "2")

    async def body() -> None:
        s = await connected(f)
        try:
            for call in (lambda: s.load_profile(PROFILE, [], **CONFIRMED), lambda: s.remove_all(**CONFIRMED)):
                with pytest.raises(EmuRefused) as ei:
                    await call()
                assert ei.value.code == "foreign_namespaces" and "2 namespace(s)" in ei.value.message
                assert "4 client(s)" in ei.value.message
            assert writes(f) == [] and s.snapshot()["generation"] == 0
            await s.load_profile(PROFILE, [], foreign_ack=True, **CONFIRMED)
            await s.load_profile(PROFILE, [], **CONFIRMED)          # all of it is ours now: no ack needed
            await s.remove_all(**CONFIRMED)
            assert (await s.namespaces())["namespaces"] == []
        finally:
            await s.close()
    run(body())


def test_busy_refuses_second_write_and_disconnect(fake_emu: Factory) -> None:
    """While a load runs, a second write, a disconnect and a connect are refused `busy`, reads continue, and
    the load finishes once released."""
    f = fake_emu()
    fc = FakeClock()

    async def body() -> None:
        gate = asyncio.Event()

        async def sleep(s: float) -> None:
            """Hold the paced load until the test opens the gate."""
            await gate.wait()
            await fc.sleep(s)
        s = await connected(f, clock=fc.clock, sleep=sleep)
        try:
            task = asyncio.create_task(s.load_profile(PROFILE, [], max_rate=1, **CONFIRMED))
            for _ in range(500):
                if s.snapshot()["busy"] and s.snapshot()["busy"]["done"] >= 1:
                    break
                await asyncio.sleep(0.02)
            assert s.snapshot()["busy"] == {"kind": "loading", "done": 1, "total": 6}
            for call in (lambda: s.load_profile(PROFILE, [], **CONFIRMED), lambda: s.remove_all(**CONFIRMED),
                         s.disconnect, lambda: s.connect(f.host, f.port)):
                with pytest.raises(EmuRefused) as ei:
                    await call()
                assert ei.value.code == "busy"
            assert len((await s.namespaces())["namespaces"]) == 2          # reads continue
            gate.set()
            snap = await task
            assert snap["busy"] is None and snap["loaded"]["partial"] is False
        finally:
            gate.set()
            await s.close()
    run(body())


def test_generation_bumps(fake_emu: Factory) -> None:
    """The generation is 0, then 1 after a load and 2 after a removal, in the snapshot and in the namespace
    listing (which therefore changes between them)."""
    f = fake_emu()
    out: list[dict[str, Any]] = []

    async def body() -> None:
        s = await connected(f, publish=out.append)
        try:
            assert (await s.namespaces())["generation"] == 0
            await s.load_profile(PROFILE, [], **CONFIRMED)
            assert s.snapshot()["generation"] == 1 and (await s.namespaces())["generation"] == 1
            snap = await s.remove_all(**CONFIRMED)
            assert snap["generation"] == 2 and snap["loaded"] is None
            assert (await s.namespaces())["generation"] == 2
        finally:
            await s.close()
    run(body())
    assert [m["generation"] for m in out if m["busy"] is None][-1] == 2


def test_namespaces_read_a_snapshot_of_ours(fake_emu: Factory) -> None:
    """The executor job reads `ours` and the generation as arguments taken on the loop, not from the session
    (Task 5 M5): the listing is built from what it was given."""
    f = fake_emu("--foreign-ns", "1")

    async def body() -> None:
        s = await connected(f)
        try:
            conn = s._need_conn()
            out = s._read_namespaces(conn, frozenset(), 7)
            assert out["generation"] == 7 and out["namespaces"][0]["ours"] is False
        finally:
            await s.close()
    run(body())


def test_cancelled_write_cleans_up(fake_emu: Factory) -> None:
    """Cancelling a load in flight clears `busy`, bumps the generation and records what was added as partial;
    a later write is accepted."""
    f = fake_emu()
    fc = FakeClock()

    async def body() -> None:
        gate = asyncio.Event()

        async def sleep(s: float) -> None:
            """Hold the paced load until the test opens the gate."""
            await gate.wait()
            await fc.sleep(s)
        s = await connected(f, clock=fc.clock, sleep=sleep)
        try:
            task = asyncio.create_task(s.load_profile(PROFILE, [], max_rate=1, **CONFIRMED))
            for _ in range(500):
                if s.snapshot()["busy"] and s.snapshot()["busy"]["done"] >= 1:
                    break
                await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            snap = s.snapshot()
            assert snap["busy"] is None and snap["generation"] == 1 and snap["loaded"]["partial"] is True
            gate.set()
            await s.remove_all(**CONFIRMED)         # not busy: accepted
            assert (await s.namespaces())["namespaces"] == []
        finally:
            gate.set()
            await s.close()
    run(body())


def test_cancelled_connect_closes_the_socket_and_leaves_connecting(fake_emu: Factory,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A connect cancelled while its handshake is out leaves the session `disconnected`, and the socket the
    handshake opens afterwards is closed (Task 5 M1/M2)."""
    f = fake_emu()
    release = threading.Event()
    entered = threading.Event()
    closed: list[EmuRpc] = []
    orig_connect, orig_close = EmuRpc.connect, EmuRpc.close

    def slow(self: EmuRpc) -> Any:
        """The handshake, held until the test releases it."""
        entered.set()
        release.wait(10)
        return orig_connect(self)

    def spy(self: EmuRpc) -> None:
        """Remember every EmuRpc closed."""
        closed.append(self)
        orig_close(self)
    monkeypatch.setattr(EmuRpc, "connect", slow)
    monkeypatch.setattr(EmuRpc, "close", spy)

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        task = asyncio.create_task(s.connect(f.host, f.port, 2.0))
        await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s.snapshot()["state"] == "disconnected"
        release.set()
        await s.close()                  # waits for the handshake job, whose done-callback closes the socket
    run(body())
    assert len(closed) == 1


def test_close_during_connect_closes_the_socket(fake_emu: Factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """close() while the handshake is out: the connect ends `not_connected` and its socket is closed, though
    the executor is already shut down."""
    f = fake_emu()
    release = threading.Event()
    entered = threading.Event()
    closed: list[EmuRpc] = []
    orig_connect, orig_close = EmuRpc.connect, EmuRpc.close

    def slow(self: EmuRpc) -> Any:
        """The handshake, held until the test releases it."""
        entered.set()
        release.wait(10)
        return orig_connect(self)

    def spy(self: EmuRpc) -> None:
        """Remember every EmuRpc closed."""
        closed.append(self)
        orig_close(self)
    monkeypatch.setattr(EmuRpc, "connect", slow)
    monkeypatch.setattr(EmuRpc, "close", spy)

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        task = asyncio.create_task(s.connect(f.host, f.port, 2.0))
        await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(s.close())
        await asyncio.sleep(0.1)
        release.set()
        await closing
        with pytest.raises(EmuRefused) as ei:
            await task
        assert ei.value.code == "not_connected"
    run(body())
    assert len(closed) == 1


def test_interrupted_first_listing_needs_the_ack(fake_emu: Factory) -> None:
    """A namespace listing another client keeps interrupting proves nothing about who owns what: without the
    ack the write is refused even when every namespace listed is ours, and nothing is removed (review I1)."""
    f = fake_emu("--interrupt-iter", "1")

    async def body() -> None:
        s = await connected(f)
        try:
            await s.load_profile(PROFILE, [], **CONFIRMED)
            before = len(writes(f))
            for call in (lambda: s.load_profile(PROFILE, [], **CONFIRMED), lambda: s.remove_all(**CONFIRMED)):
                with pytest.raises(EmuRefused) as ei:
                    await call()
                assert ei.value.code == "foreign_namespaces"
                assert "changing the namespaces" in ei.value.message
            assert len(writes(f)) == before
            await s.remove_all(foreign_ack=True, **CONFIRMED)       # acknowledged: it goes ahead
            assert [r["method"] for r in writes(f)[before:]].count("ctx_remove") >= 2
        finally:
            await s.close()
    run(body())


def test_foreign_namespace_in_the_relisting_is_not_removed(fake_emu: Factory) -> None:
    """When the first listing was interrupted and the re-listing shows a namespace that was not ours at the
    start, removal stops before it without the ack (having removed what it was sure of), and says how far
    it got."""
    f = fake_emu("--foreign-ns", "2", "--clients", "1")

    async def body() -> None:
        s = await connected(f)
        try:
            conn = s._need_conn()
            first = Listing([NsKey(0)], interrupted=True)
            progress = _Progress(removal_total=1)
            busy = s._reserve("removing")
            with pytest.raises(EmuRefused) as ei:
                await s._remove_all(conn, busy, first, {NsKey(0): (1, [])}, None, progress,
                                    frozenset({NsKey(0)}), False)
            assert ei.value.code == "foreign_namespaces" and progress.removed_ns == 1
            note = s._annotated(ei.value, progress)
            assert "removed 1 of 2 namespaces" in note.message
            s._release(busy)
            assert [n["vport"] for n in (await s.namespaces())["namespaces"]] == [1]
        finally:
            await s.close()
    run(body())
    removed = [r["params"]["tunnels"] for r in writes(f) if r["method"] == "ctx_remove"]
    assert [t[0]["vport"] for t in removed] == [0]


def test_interrupted_walks_are_retried(fake_emu: Factory) -> None:
    """With every cursor read interrupting, an acknowledged removal re-walks a namespace's clients and
    re-lists the namespaces, and still empties the server."""
    f = fake_emu("--foreign-ns", "1", "--clients", "3", "--interrupt-iter", "1")

    async def body() -> None:
        s = await connected(f)
        try:
            await s.remove_all(foreign_ack=True, **CONFIRMED)
            assert (await s.namespaces())["namespaces"] == []
        finally:
            await s.close()
    run(body())
    assert [len(r["params"]["macs"]) for r in writes(f) if r["method"] == "ctx_client_remove"] == [3]
    walks = [r for r in f.requests() if r["method"] == "ctx_client_iter" and r["params"].get("reset")]
    assert len(walks) >= 2


def test_chunks_never_exceed_the_rate(fake_emu: Factory) -> None:
    """With max_rate=10 no client add or removal request carries more than 10 clients (review I2), and the
    fake clock is slept 5 s for 50 clients."""
    f = fake_emu("--foreign-ns", "1", "--clients", "25")
    fc = FakeClock()

    async def body() -> None:
        s = await connected(f, clock=fc.clock, sleep=fc.sleep)
        try:
            await s.load_profile(PROFILE, ["--clients", "25"], max_rate=10, foreign_ack=True, **CONFIRMED)
        finally:
            await s.close()
    run(body())
    adds = [len(r["params"]["clients"]) for r in writes(f) if r["method"] == "ctx_client_add"]
    gone = [len(r["params"]["macs"]) for r in writes(f) if r["method"] == "ctx_client_remove"]
    assert sum(adds) == 50 and max(adds) == 10 and sum(gone) == 25 and max(gone) == 10
    assert 7.4 <= sum(fc.slept) <= 7.6      # 75 clients at 10 a second


def test_connection_lost_between_chunks_is_a_loss_with_progress(fake_emu: Factory) -> None:
    """The ping loop finds the server restarted while a paced load sleeps: the load fails as a lost
    connection (not a bare not_connected) naming how far it got, and `loaded` and the error agree."""
    f = fake_emu()
    fc = FakeClock()
    first = []
    session: list[Any] = []

    async def sleep(_s: float) -> None:
        """The first pacing sleep restarts the fake and waits (at most 5 s) until a ping marked it lost."""
        if not first:
            first.append(1)
            f.restart()
        end = time.monotonic() + 5
        while session[0].snapshot()["state"] != "lost" and time.monotonic() < end:
            await asyncio.sleep(0.01)

    async def body() -> None:
        s = await connected(f, ping_s=0.05, clock=fc.clock, sleep=sleep)
        session.append(s)
        try:
            with pytest.raises(ServerUnreachable) as ei:
                await s.load_profile(PROFILE, [], max_rate=1, **CONFIRMED)
            assert "partial profile" in ei.value.message and "added 2 of 2 namespaces" in ei.value.message
            snap = s.snapshot()
            assert snap["state"] == "lost" and snap["error"] == ei.value.message
            assert snap["loaded"]["partial"] is True and snap["busy"] is None and snap["generation"] == 1
        finally:
            await s.close()
    run(body())


def test_remove_on_an_empty_server_bumps_the_generation(fake_emu: Factory) -> None:
    """Every completed write bumps the generation, also one that had nothing to remove (review M2)."""
    f = fake_emu()

    async def body() -> None:
        s = await connected(f)
        try:
            assert (await s.remove_all(**CONFIRMED))["generation"] == 1
        finally:
            await s.close()
    run(body())
    assert writes(f) == []


def test_busy_is_published_while_the_profile_loads(fake_emu: Factory) -> None:
    """The busy flag is published at once, before the profile worker has run (review M8)."""
    f = fake_emu()
    out: list[dict[str, Any]] = []

    async def body() -> None:
        s = await connected(f, publish=out.append)
        try:
            await s.load_profile(PROFILE, [], **CONFIRMED)
        finally:
            await s.close()
    run(body())
    assert {"kind": "loading", "done": 0, "total": 0} in [m["busy"] for m in out]
