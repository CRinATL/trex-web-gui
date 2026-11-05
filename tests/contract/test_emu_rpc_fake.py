"""EmuRpc against tests/fake/fake_emu.py, whose handshake and error texts are the recorded ones."""
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from trex_web_gui.trexapi import emu
from trex_web_gui.trexapi.emu import EmuRpc
from trex_web_gui.trexapi.errors import (
    EmuUnsupported,
    ServerRestarted,
    ServerUnreachable,
    TrexApiError,
    UnsupportedServerVersion,
)
from trex_web_gui.trexapi.rawrpc import RawRpc, RawRpcError


@pytest.fixture
def emu_rpc(fake_emu: Callable[..., object]) -> Iterator[Callable[..., EmuRpc]]:
    """A factory for an EmuRpc against a fresh fake started with `flags`; each is closed at teardown."""
    made: list[EmuRpc] = []

    def make(*flags: str, timeout_s: float = 2.0) -> EmuRpc:
        """Start the fake and connect a client object to it (no handshake yet)."""
        f = fake_emu(*flags)
        rpc = EmuRpc(f.host, f.port, timeout_s)    # type: ignore[attr-defined]
        rpc.fake = f                               # type: ignore[attr-defined]
        made.append(rpc)
        return rpc
    yield make
    for r in made:
        r.close()


def test_connect_and_version(emu_rpc: Callable[..., EmuRpc]) -> None:
    """connect() syncs EMU 1.1 and reads the recorded version; ping answers a non-negative time."""
    rpc = emu_rpc()
    info = rpc.connect()
    assert (info.version, info.build_date, info.built_by, info.mode) == (
        "v0.1", "05.05.2019", "hhaim", "emulation")
    assert rpc.ping() >= 0


def test_handshake_refused_names_the_server_api(emu_rpc: Callable[..., EmuRpc],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A client asking for another EMU API is refused with the server's own version in the message."""
    monkeypatch.setattr(emu, "EMU_API", ("EMU", 2, 0))
    with pytest.raises(UnsupportedServerVersion) as e:
        emu_rpc().connect()
    assert e.value.server_version == "1.1" and "EMU API 1.1" in e.value.message


def test_restart_is_server_restarted(emu_rpc: Callable[..., EmuRpc]) -> None:
    """After the fake restarts (SIGUSR2) the old api_h is refused, which is ServerRestarted; a new connect
    works. The signal is asynchronous, so the ping repeats until it lands."""
    rpc = emu_rpc()
    rpc.connect()
    rpc.fake.restart()      # type: ignore[attr-defined]
    deadline = time.monotonic() + 5
    with pytest.raises(ServerRestarted):
        while time.monotonic() < deadline:
            rpc.ping()
            time.sleep(0.05)
    rpc.connect()
    rpc.ping()


def test_hang_times_out_and_next_call_works(emu_rpc: Callable[..., EmuRpc]) -> None:
    """A ping the server never answers is ServerUnreachable after the timeout; the next call (get_version,
    which the fake answers) works on the rebuilt socket."""
    rpc = emu_rpc("--hang", "ping", timeout_s=0.4)
    rpc.connect()
    with pytest.raises(ServerUnreachable):
        rpc.ping()
    assert rpc.call("get_version")["mode"] == "emulation"


def test_huge_plain_reply_is_unreachable(emu_rpc: Callable[..., EmuRpc]) -> None:
    """A 9 MiB plain reply is over the socket's MAXMSGSIZE and libzmq drops it, so the call times out."""
    rpc = emu_rpc("--huge-plain", "get_version", timeout_s=1.0)
    rpc._rpc.api_sync(*emu.EMU_API)    # handshake alone: get_version is the huge one
    with pytest.raises(ServerUnreachable):
        rpc.call("get_version")


def test_huge_zipped_reply_refused_with_bounded_memory(emu_rpc: Callable[..., EmuRpc]) -> None:
    """A small zlib frame claiming 9 MiB is refused by inflate on its length field alone."""
    rpc = emu_rpc("--huge-zip", "get_version")
    rpc._rpc.api_sync(*emu.EMU_API)
    with pytest.raises(RawRpcError, match="refused: oversize") as e:
        rpc.call("get_version")
    assert e.value.code == 0


def test_fixture_fails_the_test_when_shutdown_was_sent(fake_emu: Callable[..., Any]) -> None:
    """A `shutdown` request is never answered and is printed as FORBIDDEN, which assert_clean (the fixture's
    teardown check) turns into a failure; a fake that saw only allowed methods passes it."""
    f = fake_emu()
    clean = fake_emu()
    raw = RawRpc(f.host, f.port, 0.4)
    try:
        with pytest.raises(ServerUnreachable):
            raw.call("shutdown")
    finally:
        raw.close()
    f.stop()
    clean.stop()
    clean.assert_clean()
    with pytest.raises(AssertionError, match="sent shutdown"):
        f.assert_clean()
    f._tail = ""        # the fixture's own teardown check must now pass


CNT = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "p7" / "counters.json").read_text())


def sent(rpc: EmuRpc, method: str) -> list[dict[str, Any]]:
    """The params of every `method` request the fake behind `rpc` has received."""
    return [r["params"] for r in rpc.fake.requests() if r["method"] == method]     # type: ignore[attr-defined]


def test_list_600_namespaces_in_chunks(emu_rpc: Callable[..., EmuRpc]) -> None:
    """600 namespaces come back whole, unique and in order from pages of at most 255, the first a reset."""
    rpc = emu_rpc("--foreign-ns", "600")
    rpc.connect()
    got = rpc.list_namespaces()
    assert not got.interrupted
    assert [k.vport for k in got.items] == list(range(600))
    reqs = sent(rpc, "ctx_iter")
    assert [r["reset"] for r in reqs] == [True, False, False, False]       # 255 + 255 + 90, then 'stopped'
    assert all(r["count"] <= emu.CHUNK for r in reqs)


def test_interrupted_listing_retries_then_flags(emu_rpc: Callable[..., EmuRpc]) -> None:
    """An interruption mid-listing restarts it, and a listing that completes on the retry is not flagged; one
    the server keeps interrupting is tried three times (the first plus two retries) and returned flagged,
    with the last pass's items."""
    once = emu_rpc("--foreign-ns", "600", "--interrupt-iter", "3", "--interrupt-limit", "1")
    once.connect()
    got = once.list_namespaces()
    assert not got.interrupted and len(got.items) == 600
    assert [r["reset"] for r in sent(once, "ctx_iter")].count(True) == 2
    always = emu_rpc("--foreign-ns", "600", "--interrupt-iter", "3")
    always.connect()
    got = always.list_namespaces()
    assert got.interrupted and 0 < len(got.items) < 600
    assert [r["reset"] for r in sent(always, "ctx_iter")].count(True) == 1 + emu.LISTING_RETRIES


def test_repeated_item_restarts_the_listing(emu_rpc: Callable[..., EmuRpc]) -> None:
    """A page that repeats an item already listed restarts the walk; the retry's listing is whole, each
    namespace once, and not flagged."""
    rpc = emu_rpc("--foreign-ns", "600", "--repeat-iter", "1")
    rpc.connect()
    got = rpc.list_namespaces()
    assert not got.interrupted
    assert [k.vport for k in got.items] == list(range(600))
    assert [r["reset"] for r in sent(rpc, "ctx_iter")].count(True) == 2


def test_endless_repeats_are_flagged(emu_rpc: Callable[..., EmuRpc]) -> None:
    """A server that repeats an item on every walk is tried 1 + LISTING_RETRIES times, then the listing is
    flagged interrupted with no item twice."""
    rpc = emu_rpc("--foreign-ns", "600", "--repeat-iter", "-1")
    rpc.connect()
    got = rpc.list_namespaces()
    assert got.interrupted
    assert 0 < len(got.items) < 600 and len({k.vport for k in got.items}) == len(got.items)
    assert [r["reset"] for r in sent(rpc, "ctx_iter")].count(True) == 1 + emu.LISTING_RETRIES


def test_listing_cap(emu_rpc: Callable[..., EmuRpc], monkeypatch: pytest.MonkeyPatch) -> None:
    """A server with more namespaces, or more clients in one, than the cap is refused rather than followed."""
    monkeypatch.setattr(emu, "MAX_NAMESPACES", 300)
    monkeypatch.setattr(emu, "MAX_CLIENT_KEYS", 300)
    rpc = emu_rpc("--foreign-ns", "400", "--clients", "350")
    rpc.connect()
    with pytest.raises(TrexApiError, match="more than 300 namespaces"):
        rpc.list_namespaces()
    with pytest.raises(TrexApiError, match="more than 300 clients"):
        rpc.list_clients(emu.NsKey(0))


def test_client_info_page(emu_rpc: Callable[..., EmuRpc]) -> None:
    """A namespace's info gives its active_clients and plugins; a client page is the MACs listed then their
    infos, each info carrying its own MAC."""
    rpc = emu_rpc("--foreign-ns", "2", "--clients", "5")
    rpc.connect()
    keys = rpc.list_namespaces().items
    infos = rpc.ns_info(keys)
    assert [(i["vport"], i["active_clients"], i["plug_names"]) for i in infos] == [
        (0, 5, ["arp", "icmp"]), (1, 5, ["arp", "icmp"])]
    macs = rpc.list_clients(keys[1]).items
    assert len(macs) == 5
    page = rpc.client_info(keys[1], macs[1:3])
    assert [tuple(c["mac"]) for c in page] == macs[1:3]
    with pytest.raises(ValueError):
        rpc.client_info(keys[1], macs * 52)
    with pytest.raises(ValueError):
        rpc.ns_info(keys * 128)


def test_client_page_walks_only_to_offset_plus_limit(emu_rpc: Callable[..., EmuRpc]) -> None:
    """Page offset 100, limit 50 of a 600-client namespace walks 150 MACs in one request, not 600; the total
    for the page is active_clients from ctx_get_info (review M16). A walk to 300 asks 255 then 45."""
    rpc = emu_rpc("--foreign-ns", "1", "--clients", "600")
    rpc.connect()
    ns = rpc.list_namespaces().items[0]
    macs = rpc.list_clients(ns, upto=150).items
    assert len(macs) == 150
    assert [r["count"] for r in sent(rpc, "ctx_client_iter")] == [150]
    assert rpc.ns_info([ns])[0]["active_clients"] == 600
    assert len(rpc.client_info(ns, macs[100:150])) == 50
    rpc.list_clients(ns, upto=300)
    assert [r["count"] for r in sent(rpc, "ctx_client_iter")] == [150, 255, 45]
    assert rpc.list_clients(ns, upto=0).items == []
    assert len(sent(rpc, "ctx_client_iter")) == 3


def test_counters_meta_and_sparse_values(emu_rpc: Callable[..., EmuRpc]) -> None:
    """ctx_cnt meta describes every table; the value request without zero is sparse (fewer counters than the
    meta lists), with zero it is complete; a namespace counter (arp) answers the same way."""
    rpc = emu_rpc("--foreign-ns", "1")
    rpc.connect()
    ns = rpc.list_namespaces().items[0]
    meta = rpc.counters("ctx_cnt", meta=True)
    sparse = rpc.counters("ctx_cnt")
    full = rpc.counters("ctx_cnt", zero=True)
    assert meta == CNT["ctx"]["meta"] and sparse == CNT["ctx"]["values"] and full == CNT["ctx"]["values_zero"]
    names = {t: {m["name"] for m in v["meta"]} for t, v in meta.items()}
    assert all(set(sparse[t]) <= names[t] for t in sparse)
    assert sum(map(len, sparse.values())) < sum(map(len, full.values()))
    assert rpc.counters("arp_ns_cnt", ns, meta=True) == CNT["plugins"]["arp"]["ns"]["meta"]
    assert "arp" in rpc.counters("arp_ns_cnt", ns, zero=True)
    assert all(r["params"]["clear"] is False for r in rpc.fake.requests() if "mask" in r["params"])  # type: ignore[attr-defined]


def test_mask_honoured(emu_rpc: Callable[..., EmuRpc]) -> None:
    """On a value request a mask limits the tables to the names in it and a name no table has gives none; a
    meta request ignores it, as the shipped server does (ruling R29)."""
    rpc = emu_rpc()
    rpc.connect()
    assert set(rpc.counters("ctx_cnt", zero=True, mask=["ctx"])) == {"ctx"}
    # the shipped server ignores mask on a meta request (R29); the fake follows it
    assert set(rpc.counters("ctx_cnt", meta=True, mask=["ctx", "veth"])) == set(CNT["ctx"]["meta"])
    assert rpc.counters("ctx_cnt", zero=True, mask=["no_such_table"]) == {}


def test_unsupported_plugin_counter_is_reported(emu_rpc: Callable[..., EmuRpc]) -> None:
    """ipfix_ns_cnt, which the shipped binary lacks, is EmuUnsupported and asked once; a plugin not loaded in
    the namespace or the client (the 'no valid plugin' text, both scopes) is EmuUnsupported too, asked each
    time; one loaded answers."""
    rpc = emu_rpc("--foreign-ns", "1", "--clients", "1")
    rpc.connect()
    ns = rpc.list_namespaces().items[0]
    mac = rpc.list_clients(ns).items[0]
    for _ in range(2):
        with pytest.raises(EmuUnsupported) as e:
            rpc.counters("ipfix_ns_cnt", ns, meta=True)
        assert e.value.method == "ipfix_ns_cnt"
    assert len(sent(rpc, "ipfix_ns_cnt")) == 1
    for method, args in (("igmp_ns_cnt", (ns,)), ("dns_c_cnt", (ns, mac))):
        for _ in range(2):
            with pytest.raises(EmuUnsupported):
                rpc.counters(method, *args, meta=True)
        assert len(sent(rpc, method)) == 2
    assert "icmp" in rpc.counters("icmp_ns_cnt", ns, meta=True)
