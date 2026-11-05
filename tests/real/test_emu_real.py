"""P7 against the real v3.08 guest running TRex with its EMU server (make check-emu-real, jammy only, markers
real, restarts_trex and emu_real; spec §5.3 layer 2 and ruling R24, which moved layer 1's assertions here).
The module fixture restarts the guest's TRex with `vm/mode.sh emu` (the Task 0 command line) and, whatever
happens, puts it back on stateless afterwards. The snapshot is never saved. Every test starts and ends with an
empty EMU server."""
import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from daemon_vm import sh
from support import FIXTURES, ROOT, guestenv, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, EmuRefused, EmuSession, TrexSession
from trex_web_gui.sessions.port_config import LayerRequest
from trex_web_gui.trexapi.emu import EmuInfo, EmuRpc, NsKey, translate_emu
from trex_web_gui.trexapi.errors import UnsupportedServerVersion
from trex_web_gui.trexapi.rawrpc import RawRpc, RawRpcError

pytestmark = [pytest.mark.real, pytest.mark.restarts_trex, pytest.mark.emu_real]
HOST = os.environ.get("TREX_REAL_HOST", "")
PROFILE = str(FIXTURES / "profiles" / "emu_two_ns.py")
STL_PROFILE = str(FIXTURES / "profiles" / "udp_1pkt_simple.py")
RECORDED = json.loads((FIXTURES / "p7" / "summary.json").read_text())
NEGATIVES = json.loads((FIXTURES / "p7" / "negatives.json").read_text())
WIRE = json.loads((FIXTURES / "p7" / "profile_wire.json").read_text())["requests"]
CONFIRMED: dict[str, Any] = {"confirm": True, "foreign_ack": True}
NS0, NS1 = NsKey(0), NsKey(1)
PING_S, TIMEOUT_S = 1.0, 1.0       # the loss test's session: lost within ping_s + timeout_s + 1 s


def emu_port() -> int:
    """The host port forwarded to the guest's EMU server (vm/guestenv.py)."""
    return int(guestenv.emu_port())


@pytest.fixture(scope="module", autouse=True)
def emu_guest() -> Iterator[None]:
    """Module fixture: skip unless run through make check-emu-real; restart the guest's TRex with EMU
    (`vm/mode.sh emu`); always, at teardown, `vm/mode.sh stateless`, then assert vm/ready.py passes."""
    if not HOST:
        pytest.skip("run via make check-emu-real")
    real_ports()
    try:
        sh("vm/mode.sh", "emu")
        yield
    finally:
        restored = subprocess.run(["vm/mode.sh", "stateless"], cwd=ROOT, check=False).returncode
        t0 = Path(os.environ.get("TMPDIR", "/tmp")) / f"emu-real-ready-{os.getpid()}"
        t0.write_text(str(time.time()))
        try:
            ready = subprocess.run([sys.executable, str(ROOT / "vm" / "ready.py"), str(t0)], cwd=ROOT,
                                   check=False, capture_output=True, text=True)
        finally:
            t0.unlink()
        print(f"[teardown] mode stateless rc={restored}, ready rc={ready.returncode}")
        assert restored == 0 and ready.returncode == 0, \
            f"the guest is not back on stateless: {ready.stdout}{ready.stderr}"


async def emptied(s: EmuSession) -> None:
    """Remove everything the EMU server holds (the foreign acknowledgement included)."""
    await s.remove_all(**CONFIRMED)


async def connect_emu(ping_s: float = 60.0, timeout_s: float = 3.0) -> EmuSession:
    """A session connected to the guest's EMU server through the forwarded port, with the server emptied."""
    s = EmuSession(lambda _m: None, ping_s=ping_s)
    await s.connect("127.0.0.1", emu_port(), timeout_s)
    await emptied(s)
    return s


async def finish(s: EmuSession) -> None:
    """Empty the server again (when the session still reaches it) and close the session."""
    try:
        if s.snapshot()["state"] == "connected":
            await emptied(s)
    finally:
        await s.close()


def counter(res: dict[str, Any], table: str, name: str) -> int:
    """The value of counter `name` in `table` of a session.counters() answer."""
    rows = {r["name"]: r["value"] for r in res["tables"][table]}
    assert isinstance(rows[name], int), rows[name]
    return int(rows[name])


def raw_load() -> None:
    """Load the fixture profile with raw RPCs, the vendored client's own sequence (profile_wire.json)."""
    rpc = EmuRpc("127.0.0.1", emu_port(), 3.0)
    try:
        rpc.connect()
        for req in WIRE:
            rpc.call(req["method"], req["params"])
    finally:
        rpc.close()


@pytest.mark.timeout(60)
def test_handshake_and_version_match_the_recorded_shape() -> None:
    """Item 1: the shipped binary accepts EMU 1.1 through the host port and its get_version equals the
    fixture's (tests/fixtures/p7/summary.json); the reply carries every EmuInfo field."""
    rpc = EmuRpc("127.0.0.1", emu_port(), 3.0)
    try:
        info = rpc.connect()
        assert isinstance(info, EmuInfo)
        assert {k: getattr(info, k) for k in EmuInfo.__annotations__} == RECORDED["version"]
        assert rpc.call("get_version") == RECORDED["version"]
        assert rpc.ping() >= 0
    finally:
        rpc.close()


@pytest.mark.timeout(60)
def test_handshake_with_emu_2_0_is_refused_with_the_recorded_text() -> None:
    """Item 6, the negative control (canon:VER-2): {EMU, 2, 0} is refused with the text recorded in
    tests/fixtures/p7/negatives.json, and translate_emu reads it as UnsupportedServerVersion."""
    recorded = NEGATIVES["api_sync_v2_major2"]["resp"]["error"]
    rpc = RawRpc("127.0.0.1", emu_port(), 3.0)
    try:
        with pytest.raises(RawRpcError) as ei:
            rpc.api_sync("EMU", 2, 0)
        print(f"refused: code={ei.value.code} text={ei.value.message!r}")
        assert (ei.value.code, ei.value.message) == (recorded["code"], recorded["message"])
    finally:
        rpc.close()
    err = translate_emu(ei.value, "api_sync_v2")
    assert isinstance(err, UnsupportedServerVersion)


@pytest.mark.timeout(90)
def test_the_server_applies_mask_to_a_value_request() -> None:
    """The recorded behaviour (tests/fixtures/p7/counters.json, "masked"): ctx_cnt with mask ['ctx'] answers
    only the ctx table's values."""
    rpc = EmuRpc("127.0.0.1", emu_port(), 3.0)
    try:
        rpc.connect()
        full = rpc.counters("ctx_cnt", zero=True)
        masked = rpc.counters("ctx_cnt", zero=True, mask=["ctx"])
        print(f"value tables without mask: {sorted(full)}; with mask ['ctx']: {sorted(masked)}")
        assert set(masked) == {"ctx"}
        assert set(full) > {"ctx"}
    finally:
        rpc.close()


@pytest.mark.timeout(90)
def test_the_server_ignores_mask_on_a_meta_counter_request() -> None:
    """R29, measured: ctx_cnt with meta:true and a mask describes the same tables as the request without one
    (the server honours mask only on value requests). EmuSession never sends mask with meta."""
    rpc = EmuRpc("127.0.0.1", emu_port(), 3.0)
    try:
        rpc.connect()
        full = rpc.counters("ctx_cnt", meta=True)
        masked = rpc.counters("ctx_cnt", meta=True, mask=["ctx"])
        print(f"meta tables without mask: {sorted(full)}; with mask ['ctx']: {sorted(masked)}")
        assert set(masked) == set(full) and set(full) > {"ctx"}
        assert masked["ctx"] == full["ctx"]
    finally:
        rpc.close()


@pytest.mark.timeout(120)
def test_raw_loaded_profile_is_listed_and_a_second_load_replaces_it() -> None:
    """R24 (layer 1's assertions): a profile loaded through raw RPCs is listed by EmuSession (namespaces,
    client pages, counters with meta); a second load, through the session, removes then adds (no
    ctx_stop_autotriggers exists on this server) and leaves the same two namespaces."""
    async def run() -> None:
        s = await connect_emu()
        try:
            await asyncio.to_thread(raw_load)
            nss = await s.namespaces()
            assert {(n["vport"], n["active_clients"]) for n in nss["namespaces"]} == {(0, 3), (1, 3)}
            assert all(set(n["plug_names"]) >= {"arp", "icmp"} and not n["ours"] for n in nss["namespaces"])
            page = await s.clients(NS0, 0, 2)
            assert page["total"] == 3 and len(page["clients"]) == 2
            rest = await s.clients(NS0, 2, 100)
            assert len(rest["clients"]) == 1
            ctx = await s.counters("ctx", meta=True)
            assert "ctx" in ctx["tables"] and all(r["value"] is None for r in ctx["tables"]["ctx"])
            assert counter(await s.counters("ctx"), "ctx", "activeNs") == 2
            icmp = await s.counters("ns", NS0, plugin="icmp", meta=True)
            assert icmp["tables"]["icmp"], icmp

            # foreign to this backend: refused without the acknowledgement, replaced with it
            with pytest.raises(EmuRefused) as ei:
                await s.load_profile(PROFILE, [], confirm=True)
            print(f"without the ack: {ei.value}")
            assert ei.value.code == "foreign_namespaces"
            snap = await s.load_profile(PROFILE, [], **CONFIRMED)
            assert snap["state"] == "connected"
            after = await s.namespaces()
            assert {(n["vport"], n["active_clients"], n["ours"]) for n in after["namespaces"]} \
                == {(0, 3, True), (1, 3, True)}
        finally:
            await finish(s)
    asyncio.run(run())


@pytest.mark.timeout(180)
def test_profile_round_trip_reload_and_remove() -> None:
    """Item 3: load the fixture profile (2 namespaces, 3 clients each, arp+icmp) through EmuSession;
    namespaces and clients list as loaded; a reload (remove-then-add, headline 1) gives the same; Remove
    leaves the server empty."""
    async def run() -> None:
        s = await connect_emu()
        try:
            for round_ in ("load", "reload"):
                await s.load_profile(PROFILE, [], **CONFIRMED)
                nss = await s.namespaces()
                assert not nss["interrupted"], round_
                assert {(n["vport"], n["active_clients"], n["ours"]) for n in nss["namespaces"]} \
                    == {(0, 3, True), (1, 3, True)}, round_
                for key in (NS0, NS1):
                    page = await s.clients(key, 0, 100)
                    assert page["total"] == 3 and len(page["clients"]) == 3, (round_, key)
                    assert counter(await s.counters("ctx"), "ctx", "activeNs") == 2, round_
            await s.remove_all(**CONFIRMED)
            assert (await s.namespaces())["namespaces"] == []
            assert counter(await s.counters("ctx"), "ctx", "activeNs") == 0
        finally:
            await finish(s)
    asyncio.run(run())


@pytest.mark.timeout(180)
def test_stl_traffic_runs_while_emu_is_connected_with_a_profile() -> None:
    """Item 2: the Control session connects, acquires, starts and stops 1 kpps on port 0 while an EMU session
    is connected with the profile loaded; port 0's packet count advances and the EMU session stays
    connected with its namespaces."""
    async def run() -> None:
        trexapi.load()
        stats: list[dict[str, Any]] = []
        ctl = TrexSession(lambda m: stats.append(m) if m.get("type") == "stats" else None)
        emu = await connect_emu(ping_s=1.0)
        try:
            await emu.load_profile(PROFILE, [], **CONFIRMED)
            await ctl.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await ctl.acquire([0], force=True)
            lp = await trexapi.load_profile(STL_PROFILE, [0], [])
            await ctl.assign_profile([0], lp)

            def sent() -> float:
                """Port 0's packets sent since connect, from the newest stats message."""
                return float((stats[-1]["ports"]["0"].get("opackets") or 0.0) if stats else 0.0)

            async def settle(until: Any, what: str) -> None:
                """Wait up to 10 s for `until()`."""
                t = time.monotonic()
                while not until():
                    assert time.monotonic() - t < 10.0, what
                    await asyncio.sleep(0.1)

            await settle(lambda: bool(stats), "no stats message")
            before = sent()
            await ctl.start([0], "1kpps")
            await settle(lambda: sent() >= before + 200, "port 0 sent under 200 packets in 10 s at 1 kpps")
            await ctl.stop([0])
            await asyncio.sleep(1.5)
            stopped = sent()
            await asyncio.sleep(1.5)
            print(f"opackets: before={before} stopped={stopped} later={sent()}")
            assert sent() == stopped, "port 0 still sending after Stop"
            assert emu.snapshot()["state"] == "connected"
            assert len((await emu.namespaces())["namespaces"]) == 2
        finally:
            try:
                await ctl.close()
            finally:
                await finish(emu)
    asyncio.run(run())


@pytest.mark.timeout(240)
def test_stl_port_pings_an_emu_client_and_its_counters_advance() -> None:
    """Item 4 (R0.5: no port prerequisite on the guest): STL port 1 takes L3 1.1.1.1 (the profile's gateway)
    and pings the EMU client 1.1.1.3 on vport 0 with the app's own apply_layer and ping; all replies arrive
    and the namespace's icmp and arp counters advance."""
    async def run() -> None:
        trexapi.load()
        ctl = TrexSession(lambda _m: None)
        emu = await connect_emu()
        try:
            await emu.load_profile(PROFILE, [], **CONFIRMED)
            await ctl.connect(ConnectParams(HOST, *real_ports(), 3.0))
            await ctl.acquire([1], force=True)

            async def counters() -> dict[str, int]:
                """The four counters the run moves, in namespace vport 0."""
                icmp = await emu.counters("ns", NS0, plugin="icmp")
                arp = await emu.counters("ns", NS0, plugin="arp")
                return {"icmp_q": counter(icmp, "icmp", "pktRxIcmpQuery"),
                        "icmp_r": counter(icmp, "icmp", "pktTxIcmpResponse"),
                        "arp_q": counter(arp, "arp", "pktRxArpQuery"),
                        "arp_r": counter(arp, "arp", "pktTxReply")}
            before = await counters()
            applied = await ctl.apply_layer(1, LayerRequest("L3", None, "1.1.1.1", "1.1.1.3", [], None))
            assert applied.service_restore_error is None, applied
            ping = await ctl.ping(1, "1.1.1.3", 3, 64)
            print("ping: " + " | ".join(f"{r.status} {r.text}" for r in ping.lines))
            assert ping.service_restore_error is None
            assert [r.status for r in ping.lines] == ["reply"] * 3
            after = await counters()
            delta = {k: after[k] - before[k] for k in after}
            print(f"counter deltas: {delta}")
            assert delta["icmp_q"] >= 3 and delta["icmp_r"] >= 3
            assert delta["arp_q"] >= 1 and delta["arp_r"] >= 1
        finally:
            try:
                await ctl.close()
            finally:
                await finish(emu)
    asyncio.run(run())


@pytest.mark.timeout(240)
def test_restart_of_trex_is_detected_and_a_new_connection_sees_an_empty_server() -> None:
    """Item 5 (last in the file: it restarts TRex): with a profile loaded, stop TRex in the guest; the EMU
    session goes `lost` with a restart or unreachable text within ping_s + timeout_s + 1 s of the stop
    returning; after `vm/mode.sh emu` a new connection works and the server holds nothing."""
    bound = PING_S + TIMEOUT_S + 1.0

    async def run() -> None:
        s = EmuSession(lambda _m: None, ping_s=PING_S)
        try:
            await s.connect("127.0.0.1", emu_port(), TIMEOUT_S)
            await emptied(s)
            await s.load_profile(PROFILE, [], **CONFIRMED)
            await asyncio.to_thread(sh, "vm/mode.sh", "stop")
            t = time.monotonic()
            while s.snapshot()["state"] != "lost":
                assert time.monotonic() - t < bound, f"not lost within {bound} s: {s.snapshot()}"
                await asyncio.sleep(0.05)
            took = time.monotonic() - t
            error = s.snapshot()["error"] or ""
            print(f"lost_after_s={took:.2f} (bound {bound}) error={error!r}")
            assert "restarted" in error or "no answer" in error or "Cannot reach" in error, error
        finally:
            await s.close()
        await asyncio.to_thread(sh, "vm/mode.sh", "emu")
        again = EmuSession(lambda _m: None, ping_s=60)
        try:
            snap = await again.connect("127.0.0.1", emu_port(), 3.0)
            assert snap["state"] == "connected"
            assert (await again.namespaces())["namespaces"] == []
        finally:
            await again.close()
    asyncio.run(run())
