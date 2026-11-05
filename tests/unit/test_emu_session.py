"""EmuSession against tests/fake/fake_emu.py: the state machine, liveness, coalesced reads, the meta cache
and the isolation from TrexSession's executors."""
import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from trex_web_gui.sessions import EmuRefused, EmuSession, TrexSession
from trex_web_gui.sessions.emu_session import _rows
from trex_web_gui.trexapi import emu
from trex_web_gui.trexapi.emu import EmuRpc, NsKey
from trex_web_gui.trexapi.errors import ServerUnreachable

Factory = Callable[..., Any]
KEY0 = NsKey(0)


def run(coro: Awaitable[Any]) -> Any:
    """Run `coro` to completion on a fresh loop."""
    return asyncio.run(coro)  # type: ignore[arg-type]


async def until(pred: Callable[[], bool], within: float) -> bool:
    """True once `pred()` holds, polling for at most `within` seconds."""
    end = time.monotonic() + within
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


def methods(fake: Any, name: str) -> list[dict[str, Any]]:
    """The fake's logged requests of one method."""
    return [r for r in fake.requests() if r["method"] == name]


def test_connect_publishes_connecting_then_connected(fake_emu: Factory) -> None:
    """connect() publishes `connecting`, then `connected` with the server's version; disconnect publishes
    `disconnected`; a second connect builds a new connection."""
    f = fake_emu()
    out: list[dict[str, Any]] = []

    async def body() -> None:
        s = EmuSession(out.append, ping_s=60)
        try:
            snap = await s.connect(f.host, f.port, 2.0)
            assert snap["state"] == "connected" and snap["target"] == {"host": f.host, "port": f.port}
            assert snap["info"]["version"] == "v0.1"
            await s.disconnect()
            assert s.snapshot()["state"] == "disconnected" and s.snapshot()["info"] is None
        finally:
            await s.close()
    run(body())
    assert [m["state"] for m in out] == ["connecting", "connected", "disconnected"]
    assert all(m["type"] == "emu" for m in out)
    assert len(methods(f, "api_sync_v2")) == 1


def test_each_connect_builds_a_new_rpc(fake_emu: Factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reconnect gets a new EmuRpc, so what the old one remembered as unsupported does not carry over."""
    f = fake_emu()
    made: list[EmuRpc] = []
    orig = EmuRpc.__init__

    def spy(self: EmuRpc, *a: Any, **k: Any) -> None:
        """Record every EmuRpc built."""
        orig(self, *a, **k)
        made.append(self)
    monkeypatch.setattr(EmuRpc, "__init__", spy)

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            await s.connect(f.host, f.port, 2.0)
        finally:
            await s.close()
    run(body())
    assert len(made) == 2 and made[0] is not made[1]


def test_connect_refused_by_api_version_is_unsupported_server(fake_emu: Factory,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """A server on another EMU API is EmuRefused unsupported_server naming its version, and the session
    stays disconnected with the error."""
    f = fake_emu()
    monkeypatch.setattr(emu, "EMU_API", ("EMU", 2, 0))

    async def body() -> None:
        s = EmuSession(lambda _m: None)
        try:
            with pytest.raises(EmuRefused) as e:
                await s.connect(f.host, f.port, 2.0)
            assert e.value.code == "unsupported_server" and "1.1" in str(e.value)
            snap = s.snapshot()
            assert snap["state"] == "disconnected" and snap["error"]
        finally:
            await s.close()
    run(body())


def test_unreachable_server_leaves_the_session_disconnected() -> None:
    """Nothing listening: connect raises the transport error (not a refusal) and the state is disconnected."""
    from trex_web_gui.trexapi.errors import ServerUnreachable

    async def body() -> None:
        s = EmuSession(lambda _m: None)
        try:
            with pytest.raises(ServerUnreachable):
                await s.connect("127.0.0.1", 9, 0.3)
            assert s.snapshot()["state"] == "disconnected" and s.snapshot()["error"]
        finally:
            await s.close()
    run(body())


def test_reads_refuse_when_not_connected() -> None:
    """Every read is EmuRefused not_connected before connect."""
    async def body() -> None:
        s = EmuSession(lambda _m: None)
        try:
            for call in (s.namespaces(), s.clients(KEY0), s.counters("ctx")):
                with pytest.raises(EmuRefused) as e:
                    await call
                assert e.value.code == "not_connected"
        finally:
            await s.close()
    run(body())


def test_a_second_concurrent_connect_is_busy(fake_emu: Factory) -> None:
    """While one connect is in flight another is EmuRefused busy."""
    f = fake_emu("--hang", "get_version")

    async def body() -> None:
        s = EmuSession(lambda _m: None)
        try:
            first = asyncio.ensure_future(s.connect(f.host, f.port, 0.5))
            await asyncio.sleep(0.1)
            with pytest.raises(EmuRefused) as e:
                await s.connect(f.host, f.port, 0.5)
            assert e.value.code == "busy"
            with pytest.raises(ServerUnreachable):   # the first one times out against the hung method
                await first
        finally:
            await s.close()
    run(body())


def test_ping_loss_marks_lost(fake_emu: Factory) -> None:
    """A server that stops answering pings makes the session `lost` within ping_s + timeout, with the cause;
    reads then answer not_connected and the ping loop is over."""
    f = fake_emu("--hang", "ping")
    out: list[dict[str, Any]] = []

    async def body() -> None:
        s = EmuSession(out.append, ping_s=0.1)
        try:
            await s.connect(f.host, f.port, 0.5)
            t0 = time.monotonic()
            assert await until(lambda: s.snapshot()["state"] == "lost", 0.1 + 0.5 + 1.5)
            assert time.monotonic() - t0 < 0.1 + 0.5 + 1.5
            assert s.snapshot()["error"]
            with pytest.raises(EmuRefused) as e:
                await s.namespaces()
            assert e.value.code == "not_connected" and "lost" in str(e.value)
            n = len(methods(f, "ping"))
            await asyncio.sleep(0.8)
            assert len(methods(f, "ping")) == n
        finally:
            await s.close()
    run(body())
    assert [m["state"] for m in out][-2:] == ["lost", "disconnected"]


def test_restart_marks_lost_with_restart_text(fake_emu: Factory) -> None:
    """After the fake restarts, the next ping is refused for its old api_h and the session is `lost` with
    the restart text."""
    f = fake_emu()

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=0.1)
        try:
            await s.connect(f.host, f.port, 2.0)
            f.restart()
            assert await until(lambda: s.snapshot()["state"] == "lost", 5)
            assert s.snapshot()["error"] == emu.RESTARTED
        finally:
            await s.close()
    run(body())


def test_concurrent_identical_reads_coalesce(fake_emu: Factory) -> None:
    """Three identical namespaces() calls started together share one listing on the wire; a different request
    (a client page) is not coalesced with it."""
    f = fake_emu("--foreign-ns", "3", "--clients", "2")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            one = await s.namespaces()
            single = len(methods(f, "ctx_iter"))
            assert len(one["namespaces"]) == 3 and single >= 1
            a, b, c = await asyncio.gather(s.namespaces(), s.namespaces(), s.namespaces())
            assert a == b == c
            assert len(methods(f, "ctx_iter")) == 2 * single
            assert len(methods(f, "ctx_get_info")) == 2
        finally:
            await s.close()
    run(body())


def test_namespaces_listing_shape(fake_emu: Factory) -> None:
    """Each namespace carries its key, active_clients, plug_names and `ours`; the body has the generation."""
    f = fake_emu("--foreign-ns", "2", "--clients", "4")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            res = await s.namespaces()
            assert res["generation"] == 0 and res["interrupted"] is False
            assert res["namespaces"][1] == {"vport": 1, "tci": [0, 0], "tpid": [0, 0], "active_clients": 4,
                                            "plug_names": ["arp", "icmp"], "ours": False}
        finally:
            await s.close()
    run(body())


def test_client_page_walks_only_to_the_page_and_totals_from_ns_info(fake_emu: Factory) -> None:
    """A page of 10 of 600 clients is one cursor request of 10, `total` is the namespace's active_clients,
    and an offset past the total walks nothing."""
    f = fake_emu("--foreign-ns", "1", "--clients", "600")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            page = await s.clients(KEY0, 0, 10)
            assert page["total"] == 600 and len(page["clients"]) == 10 and page["interrupted"] is False
            its = methods(f, "ctx_client_iter")
            assert len(its) == 1 and its[0]["params"]["count"] == 10
            deep = await s.clients(KEY0, 590, 100)
            assert deep["total"] == 600 and len(deep["clients"]) == 10
            n = len(methods(f, "ctx_client_iter"))
            assert (await s.clients(KEY0, 600, 5))["clients"] == []
            assert len(methods(f, "ctx_client_iter")) == n
            with pytest.raises(ValueError):
                await s.clients(KEY0, 0, 256)
        finally:
            await s.close()
    run(body())


def test_meta_fetched_once_per_key(fake_emu: Factory) -> None:
    """Counters of one key fetch the tables' meta once and then only values; another key fetches its own
    meta; `meta=True` answers the described counters without a values request."""
    f = fake_emu("--foreign-ns", "2", "--clients", "1")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            for _ in range(3):
                res = await s.counters("ctx")
                assert res["unsupported"] == []
            reqs = methods(f, "ctx_cnt")
            assert [bool(r["params"]["meta"]) for r in reqs] == [True, False, False, False][:len(reqs)]
            assert sum(bool(r["params"]["meta"]) for r in reqs) == 1 and len(reqs) == 4
            described = await s.counters("ctx", meta=True)
            assert described["tables"] and all(row["value"] is None for rows in described["tables"].values()
                                               for row in rows)
            assert len(methods(f, "ctx_cnt")) == 4
            await s.counters("ns", NsKey(0), plugin="arp")
            await s.counters("ns", NsKey(0), plugin="arp")
            await s.counters("ns", NsKey(1), plugin="arp")
            arp = methods(f, "arp_ns_cnt")
            assert sum(bool(r["params"]["meta"]) for r in arp) == 2 and len(arp) == 5
            assert not any(r["params"].get("clear") for r in f.requests() if "params" in r)
        finally:
            await s.close()
    run(body())


def test_meta_cache_is_dropped_on_reconnect(fake_emu: Factory) -> None:
    """A new connection fetches the meta again."""
    f = fake_emu()

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            await s.counters("ctx")
            await s.connect(f.host, f.port, 2.0)
            await s.counters("ctx")
        finally:
            await s.close()
    run(body())
    assert sum(bool(r["params"]["meta"]) for r in methods(f, "ctx_cnt")) == 2


def test_named_unsupported_plugin_is_refused_and_omitted_ones_are_listed(fake_emu: Factory) -> None:
    """ipfix's namespace counters are method-not-found on the shipped server. Named, the request is
    EmuRefused unsupported (and the next one sends nothing); a plugin with no namespace counters is refused
    without any request. With plugin omitted they are skipped and listed in `unsupported`."""
    f = fake_emu("--foreign-ns", "1", "--clients", "1")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            with pytest.raises(EmuRefused) as e:
                await s.counters("ns", KEY0, plugin="ipfix")
            assert e.value.code == "unsupported"
            n = len(methods(f, "ipfix_ns_cnt"))
            assert n >= 1
            with pytest.raises(EmuRefused):
                await s.counters("ns", KEY0, plugin="ipfix")
            with pytest.raises(EmuRefused) as e2:
                await s.counters("ns", KEY0, plugin="dhcp")
            assert e2.value.code == "unsupported"
            assert len(methods(f, "ipfix_ns_cnt")) == n and not methods(f, "dhcp_client_cnt")
            allp = await s.counters("ns", KEY0)
            assert "ipfix" in allp["unsupported"] and "dhcp" not in allp["unsupported"]
            assert methods(f, "arp_ns_cnt")        # the loaded plugin was read, whatever its sparse values
            assert len(methods(f, "ipfix_ns_cnt")) == n
        finally:
            await s.close()
    run(body())


def test_counters_check_their_keys(fake_emu: Factory) -> None:
    """ValueError for an unknown scope or plugin, a missing or surplus key."""
    f = fake_emu()

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 2.0)
            for args in (("bogus",), ("ns",), ("ctx", KEY0), ("client", KEY0), ("ns", KEY0, None, "nope")):
                with pytest.raises(ValueError):
                    await s.counters(*args)
        finally:
            await s.close()
    run(body())


def test_disconnect_during_read(fake_emu: Factory) -> None:
    """A disconnect while a read is out ends that read with not_connected (not `lost`), leaves the session
    disconnected and the session usable for a new connect."""
    f = fake_emu("--foreign-ns", "1", "--hang", "ctx_get_info")

    async def body() -> None:
        s = EmuSession(lambda _m: None, ping_s=60)
        try:
            await s.connect(f.host, f.port, 0.6)
            read = asyncio.ensure_future(s.namespaces())
            assert await until(lambda: bool(methods(f, "ctx_get_info")), 3)
            await s.disconnect()
            with pytest.raises(EmuRefused) as e:
                await read
            assert e.value.code == "not_connected"
            assert s.snapshot()["state"] == "disconnected" and s.snapshot()["error"] is None
            await s.connect(f.host, f.port, 0.6)
            assert s.snapshot()["state"] == "connected"
        finally:
            await s.close()
    run(body())


def test_emu_session_never_touches_trex_executors(fake_emu: Factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every EMU RPC runs on a thread named `emu*`, and a TrexSession built alongside, with spies for its
    two executors, receives no submit during EMU connect, reads and close."""
    f = fake_emu("--foreign-ns", "2", "--clients", "3")
    threads: list[str] = []
    orig = EmuRpc.call

    def spy(self: EmuRpc, method: str, params: dict[str, Any] | None = None) -> Any:
        """Record the calling thread's name, then call through."""
        threads.append(threading.current_thread().name)
        return orig(self, method, params)
    monkeypatch.setattr(EmuRpc, "call", spy)

    async def body() -> None:
        trex = TrexSession(lambda _m: None)
        trex._executor, trex._reader_executor = MagicMock(), MagicMock()
        s = EmuSession(lambda _m: None, ping_s=0.05)
        try:
            await s.connect(f.host, f.port, 2.0)
            await s.namespaces()
            await s.clients(KEY0, 0, 2)
            await s.counters("ctx")
            await asyncio.sleep(0.3)        # pings
        finally:
            await s.close()
        assert not trex._executor.method_calls and not trex._reader_executor.method_calls
    run(body())
    assert len(threads) > 8 and all(t.startswith("emu") for t in threads)
    assert methods(f, "ping")


def test_close_is_idempotent(fake_emu: Factory) -> None:
    """close() twice is fine, leaves the session disconnected, and the second one does nothing."""
    f = fake_emu()
    out: list[dict[str, Any]] = []

    async def body() -> None:
        s = EmuSession(out.append, ping_s=60)
        await s.connect(f.host, f.port, 2.0)
        await s.close()
        n = len(out)
        await s.close()
        assert len(out) == n and s.snapshot()["state"] == "disconnected"
        with pytest.raises(EmuRefused):
            await s.namespaces()
    run(body())


def test_connect_after_close_is_refused_not_connected() -> None:
    """A closed session refuses connect with not_connected before submitting to its shut-down executor (which
    would raise RuntimeError)."""
    async def body() -> None:
        s = EmuSession(lambda _m: None)
        await s.close()
        with pytest.raises(EmuRefused) as e:
            await s.connect("127.0.0.1", 9, 0.3)
        assert e.value.code == "not_connected" and s.snapshot()["state"] == "disconnected"
    run(body())


def test_close_while_a_connect_is_queued_refuses_it_not_connected() -> None:
    """A connect whose job is still queued behind another when close() shuts the executor down (its queued
    jobs cancelled) ends in EmuRefused not_connected, not a CancelledError of the caller's task."""
    async def body() -> None:
        s = EmuSession(lambda _m: None)
        gate = threading.Event()
        s._executor.submit(gate.wait, 5)                 # the one executor thread is busy
        task = asyncio.create_task(s.connect("127.0.0.1", 9, 0.3))
        assert await until(lambda: s.snapshot()["state"] == "connecting", 2)
        asyncio.get_running_loop().call_later(0.2, gate.set)
        await s.close()                                  # cancels the queued connect job, waits for the gate
        with pytest.raises(EmuRefused) as e:
            await task
        assert e.value.code == "not_connected" and not task.cancelled()
        assert s.snapshot()["state"] == "disconnected"
    run(body())


def test_the_app_builds_and_closes_the_emu_session(tmp_path: Any) -> None:
    """The lifespan builds app.state.emu and closes it on shutdown."""
    from support import authed
    from trex_web_gui.api.app import create_app
    app = create_app(tmp_path, token="t" * 32)
    with authed(app):
        assert isinstance(app.state.emu, EmuSession)
        assert app.state.emu.snapshot()["state"] == "disconnected"
    assert app.state.emu._closed


_META = {"t1": {"name": "t1", "meta": [{"name": "a", "unit": "pkts", "help": "A", "info": 18, "zero": False},
                                      {"name": "b", "unit": "ops", "help": "B", "info": 19, "zero": False}]},
         "t2": {"name": "t2", "meta": [{"name": "c", "unit": "u", "help": "C", "info": 20, "zero": False}]}}


def test_rows_fill_a_counter_the_sparse_values_omit_with_zero() -> None:
    """The server leaves zero counters out; the rows carry every described counter, in meta order."""
    rows = _rows(_META, {"t1": {"b": 5}, "t2": {"c": 1}})
    assert [(r["name"], r["value"]) for r in rows["t1"]] == [("a", 0), ("b", 5)]
    assert rows["t1"][0]["info"] == 18 and rows["t1"][0]["unit"] == "pkts" and rows["t1"][1]["info"] == 19


def test_rows_give_a_table_absent_from_values_all_zeros() -> None:
    """A meta table the values lack entirely appears, every counter 0."""
    rows = _rows(_META, {"t1": {"a": 2}})
    assert list(rows) == ["t1", "t2"]
    assert [(r["name"], r["value"], r["info"]) for r in rows["t2"]] == [("c", 0, 20)]


def test_rows_keep_a_value_the_meta_does_not_describe() -> None:
    """An undescribed value is kept after the described ones, with info None and empty unit and help."""
    rows = _rows(_META, {"t1": {"zzz": 3, "a": 1}, "t3": {"q": 9}})
    assert [(r["name"], r["value"]) for r in rows["t1"]] == [("a", 1), ("b", 0), ("zzz", 3)]
    assert rows["t1"][2]["info"] is None and rows["t1"][2]["unit"] == "" and rows["t1"][2]["help"] == ""
    assert [(r["name"], r["info"]) for r in rows["t3"]] == [("q", None)]


def test_rows_without_values_describe_everything_with_none() -> None:
    """meta-only reads (values None) keep value None."""
    assert [r["value"] for rs in _rows(_META, None).values() for r in rs] == [None, None, None]
