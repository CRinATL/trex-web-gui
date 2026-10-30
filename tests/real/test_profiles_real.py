"""P4a exit criteria against the real v3.08 guest (make check-real): the Studio's imports, exports and
simulate, in-process, assigned through TrexSession, with a foreign STLClient as the oracle.

Each test prints its measured figures (vm/real.sh runs pytest with -s)."""
import asyncio
import math
import os
import re
import struct
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from support import SRC, real_ports
from trex_web_gui import trexapi
from trex_web_gui.sessions import ConnectParams, TrexSession
from trex_web_gui.studio import convert, simulate
from trex_web_gui.studio.library import ProfileLibrary

pytestmark = pytest.mark.real
HOST = os.environ.get("TREX_REAL_HOST", "")
STL = SRC / "scripts/stl"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "stl"
REAL_REFUSED = GOLDEN / "REAL_REFUSED.md"
SKIPPED = GOLDEN / "SKIPPED.md"
GOLDEN_ALL = os.environ.get("TREX_REAL_GOLDEN") == "all"
GOLDEN_BUDGET_S = 300.0         # spec §8 budget rule: above this extrapolation, run a sample that fits
GOLDEN_FIRST = 10               # profiles timed before extrapolating
TX_MAX_S = 60.0                 # bound on waiting for a burst to leave TX
SETTLE_S = 0.5                  # gap between two equal oracle reads that end a burst's count
BURST = str(STL / "burst_3st_1000pkt.py")
BENCH = str(STL / "udp_for_benchmarks.py")


@pytest.fixture(autouse=True)
def need_vm() -> None:
    """Autouse: skips every test in the module unless TREX_REAL_HOST is set (vm/real.sh sets it for make
    check-real)."""
    if not HOST:
        pytest.skip("run via make check-real")
    real_ports()


@asynccontextmanager
async def guest() -> AsyncIterator[tuple[TrexSession, Any]]:
    """A connected TrexSession owning port 0 (taken by force) and a foreign STLClient oracle that only reads;
    both closed on exit, the oracle without stopping traffic or releasing ports."""
    trexapi.load()
    from trex.stl.api import STLClient
    oracle = STLClient(username="oracle", server=HOST, sync_port=real_ports()[0],
                       async_port=real_ports()[1], verbose_level="none")
    await asyncio.to_thread(oracle.connect)
    s = TrexSession(lambda m: None)
    try:
        await s.connect(ConnectParams(HOST, *real_ports(), 3.0))
        await s.acquire([0], force=True)
        await s.stop([0])
        yield s, oracle
    finally:
        await s.close()
        await asyncio.to_thread(oracle.disconnect, stop_traffic=False, release_ports=False)


def server_state(oracle: Any) -> str:
    """Profile "_"'s state on port 0 as the server reports it (IDLE when absent)."""
    req = {"port_id": 0, "profile_id": "*", "block": False}
    data = oracle.ports[0].transmit("get_port_status", req).data()
    return str(data["state_profile"].get("_", "IDLE"))


def opackets(oracle: Any) -> int:
    """Port 0's opackets as the oracle reads them from the server."""
    return int(oracle.get_stats(ports=[0])[0]["opackets"])


async def settled_opackets(oracle: Any) -> int:
    """Port 0's opackets once two reads SETTLE_S apart agree (the counters trail the end of a burst)."""
    last = await asyncio.to_thread(opackets, oracle)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        await asyncio.sleep(SETTLE_S)
        now = await asyncio.to_thread(opackets, oracle)
        if now == last:
            return now
        last = now
    return last


async def burst_total(s: TrexSession, oracle: Any, lp: trexapi.LoadedProfile, mult: str) -> int:
    """Assign `lp` to port 0, start it at `mult`, wait until the server leaves TX, and return the oracle's
    opackets delta over the run."""
    await s.assign_profile([0], lp, stop_first=True)
    before = await settled_opackets(oracle)
    await s.start([0], mult)
    deadline = time.monotonic() + TX_MAX_S
    while await asyncio.to_thread(server_state, oracle) == "TX":
        assert time.monotonic() < deadline, f"port 0 still in TX after {TX_MAX_S:g} s"
        await asyncio.sleep(0.1)
    return await settled_opackets(oracle) - before


async def library_load(lib: ProfileLibrary, name: str, streams: list[dict[str, Any]]) -> tuple[str, Any]:
    """Create library entry `name` with `streams` and load its JSON as Control does (max_result
    MAX_RESULT_PCAP for one port); the entry's slug and the LoadedProfile."""
    entry = lib.create(name, streams)
    lp = await trexapi.load_profile(entry["path"], [0], [], max_result=trexapi.MAX_RESULT_PCAP)
    return entry["slug"], lp


async def sim(streams: list[dict[str, Any]]) -> simulate.SimReport:
    """Simulate's report of `streams`, with the worker's round trip."""
    return simulate.report(streams, await trexapi.round_trip(streams))


# -- golden profiles validate --------------------------------------------------------------------------------

def read_table(path: Path, columns: int) -> dict[str, list[str]]:
    """The body rows of a markdown table with `columns` columns, by first cell; {} when the file is absent."""
    if not path.exists():
        return {}
    cell = r" (.+?) \|"
    rows = re.findall(r"^\| (\S+) \|" + cell * (columns - 1) + "$", path.read_text(), re.M)
    return {r[0]: list(r[1:]) for r in rows if r[0] not in ("file", "---")}


def golden_profiles() -> list[Path]:
    """The loadable golden profiles: every .py, .yaml and .json under SRC/scripts/stl not in SKIPPED.md."""
    if not STL.is_dir():
        return []
    skipped = read_table(SKIPPED, 2)
    return sorted(p for p in STL.iterdir()
                  if p.is_file() and p.suffix in (".py", ".yaml", ".json") and p.name not in skipped)


def evenly_spaced(items: list[Path], n: int) -> list[Path]:
    """`n` items spread evenly over `items` (all of them when n >= len)."""
    if n >= len(items):
        return list(items)
    return [items[math.floor(i * len(items) / n)] for i in range(n)] if n > 0 else []


def golden_problem(name: str, listed: str | None, refusal: str | None) -> str | None:
    """Why profile `name`'s outcome (refusal: the full message, None when it validated) disagrees with its
    REAL_REFUSED.md listing (listed: the server message, None when unlisted)."""
    if refusal is None:
        return None if listed is None else f"{name} now validates: remove it from REAL_REFUSED.md"
    if listed is None:
        return f"{name} was refused and is not listed in REAL_REFUSED.md; full server message:\n{refusal}"
    if listed not in refusal:
        return (f"{name} was refused, but its message does not contain the listed {listed!r}; full server "
                f"message:\n{refusal}")
    return None


def busy_profiles(oracle: Any, ports: list[int]) -> dict[int, dict[str, str]]:
    """The profiles the server reports with streams (any state but IDLE), by port; {} when every port is
    empty."""
    out: dict[int, dict[str, str]] = {}
    for p in ports:
        req = {"port_id": p, "profile_id": "*", "block": False}
        states = oracle.ports[p].transmit("get_port_status", req).data()["state_profile"]
        held = {str(k): str(v) for k, v in states.items() if v != "IDLE"}
        if held:
            out[p] = held
    return out


async def validate_one(s: TrexSession, path: Path, ports: list[int]) -> str | None:
    """Remove every profile's streams from all `ports` (pg_ids are server-global, so a stream left on any port
    can make a profile's flow stats conflict), then load `path` for port 0 and assign it (which validates);
    None when it validates, else the full message of the refusal or load failure."""
    await s.reset(ports)
    try:
        lp = await trexapi.load_profile(str(path), [0], [])
        await s.assign_profile([0], lp, stop_first=True)
    except trexapi.TrexApiError as e:
        return e.message
    return None


@pytest.mark.timeout(7200 if GOLDEN_ALL else 900)
def test_golden_profiles_validate_on_the_guest() -> None:
    """Every listed profile is refused with a message containing its listed server message; every other one
    validates. Times GOLDEN_FIRST, extrapolates, and above GOLDEN_BUDGET_S runs an evenly spaced sample of the
    rest that fits (TREX_REAL_GOLDEN=all runs all); listed profiles always run. The session acquires every
    port and the run starts only once the oracle sees no streams on any of them."""
    allp = golden_profiles()
    assert allp, f"no golden profiles under {STL}"
    listed = {f: row[0] for f, row in read_table(REAL_REFUSED, 3).items()}
    outcome: dict[str, str | None] = {}

    async def run() -> None:
        """Validate the first GOLDEN_FIRST, then the rest or a sample of it, recording each outcome."""
        async with guest() as (s, oracle):
            ports = sorted(await asyncio.to_thread(oracle.get_all_ports))
            await s.acquire(ports, force=True)
            await s.reset(ports)                   # earlier tests leave streams (and their pg_ids) behind
            left = await asyncio.to_thread(busy_profiles, oracle, ports)
            print(f"golden: ports {ports}; profiles holding streams after the reset: {left}")
            assert not left, f"the guest still holds streams before the golden run: {left}"
            t0 = time.monotonic()
            first, rest = allp[:GOLDEN_FIRST], allp[GOLDEN_FIRST:]
            for p in first:
                outcome[p.name] = await validate_one(s, p, ports)
            t_first = time.monotonic() - t0
            per = t_first / len(first)
            extrap = per * len(allp)
            print(f"golden: {len(allp)} profiles; first {len(first)} took {t_first:.1f} s "
                  f"({per:.2f} s each); extrapolated total {extrap:.1f} s")
            if GOLDEN_ALL or extrap <= GOLDEN_BUDGET_S:
                todo = rest
                print(f"golden: running all {len(allp)}" + (" (TREX_REAL_GOLDEN=all)" if GOLDEN_ALL else ""))
            else:
                n = max(0, math.floor((GOLDEN_BUDGET_S - t_first) / per))
                todo = evenly_spaced(rest, n)
                extra = [p for p in rest if p.name in listed and p not in todo]
                print(f"golden: extrapolation exceeds {GOLDEN_BUDGET_S:g} s; sample of {len(todo)} of the "
                      f"remaining {len(rest)}: {', '.join(p.name for p in todo)}")
                if extra:
                    print(f"golden: plus the listed {', '.join(p.name for p in extra)}")
                todo = todo + extra
            for p in todo:
                outcome[p.name] = await validate_one(s, p, ports)
            print(f"golden: {len(outcome)} profiles in {time.monotonic() - t0:.1f} s")
    asyncio.run(run())
    refused = {n: m for n, m in outcome.items() if m is not None}
    print(f"golden: {len(outcome) - len(refused)} validated, {len(refused)} refused")
    for n, m in sorted(refused.items()):
        first_line = (m.splitlines() or [""])[0].replace("|", "/")
        print(f"golden refused: | {n} | {first_line} | ? |")
    problems = [p for n in sorted(outcome) if (p := golden_problem(n, listed.get(n), outcome[n])) is not None]
    assert not problems, "\n\n".join(problems)


def test_golden_problem_decisions() -> None:
    """The listing rules: unlisted validated passes; listed validated, unlisted refused and a refusal whose
    message lacks the listed text are problems; a listed substring of the full message passes."""
    assert golden_problem("a.py", None, None) is None
    assert "now validates: remove it from REAL_REFUSED.md" in (golden_problem("a.py", "x", None) or "")
    assert "not listed" in (golden_problem("a.py", None, "boom on core 3") or "")
    assert "boom on core 3" in (golden_problem("a.py", None, "boom on core 3") or "")
    assert golden_problem("a.py", "boom on core", "Port 0: boom on core 3") is None
    assert "does not contain" in (golden_problem("a.py", "other", "boom") or "")


# -- R15: every Studio path transmits the same packet total --------------------------------------------------

@pytest.mark.timeout(180)
def test_studio_round_trips_transmit_the_same_packet_totals(tmp_path: Path) -> None:
    """burst_3st_1000pkt.py as loaded, as imported into the library, as the library's YAML export and as its
    Python export: each sends exactly 30000 packets at mult "1000", which is simulate's burst total."""
    lib = ProfileLibrary(lambda: tmp_path / "lib")

    async def run() -> None:
        """Build the four LoadedProfiles, run each and compare the oracle's deltas with simulate."""
        imp = await convert.import_file(BURST, "py", [], None)
        slug, lp_lib = await library_load(lib, "burst 3st", imp.streams)
        streams = lib.get(slug).streams
        report = await sim(streams)
        yaml_path = tmp_path / "burst_export.yaml"
        yaml_path.write_text(convert.to_yaml(streams))
        py_path = tmp_path / "burst_export.py"
        py_path.write_text(await trexapi.to_python(streams))
        paths = {
            "loaded": await trexapi.load_profile(BURST, [0], []),
            "library": lp_lib,
            "yaml export": await trexapi.load_profile(str(yaml_path), [0], []),
            "python export": await trexapi.load_profile(str(py_path), [0], []),
        }
        totals: dict[str, int] = {}
        async with guest() as (s, oracle):
            for what, lp in paths.items():
                totals[what] = await burst_total(s, oracle, lp, "1000")
                print(f"R15 {what}: opackets delta {totals[what]}")
        print(f"R15 simulate totals.burst_pkts {report['totals']['burst_pkts']}")
        assert report["totals"]["burst_pkts"] == 30000
        assert totals == dict.fromkeys(paths, 30000)
    asyncio.run(run())


# -- simulate against the server's validate ------------------------------------------------------------------

@pytest.mark.timeout(90)
def test_simulate_matches_the_server_validate(tmp_path: Path) -> None:
    """udp_for_benchmarks.py --packet_len 128 --stream_count 3 imported, simulated and assigned: simulate's
    pps 3, bps_l2 3072, bps_l1 3552 each equal the validate reply's max_pps, max_bps_l2, max_bps_l1."""
    lib = ProfileLibrary(lambda: tmp_path / "lib")

    async def run() -> None:
        """Import, simulate, assign the library entry and compare."""
        imp = await convert.import_file(BENCH, "py", ["--packet_len", "128", "--stream_count", "3"], None)
        slug, lp = await library_load(lib, "bench 3", imp.streams)
        totals = (await sim(lib.get(slug).streams))["totals"]
        async with guest() as (s, _oracle):
            rates = await s.assign_profile([0], lp, stop_first=True)
        rate = rates[0]["rate"]
        assert isinstance(rate, dict)
        print(f"simulate totals: pps {totals['pps']} bps_l2 {totals['bps_l2']} bps_l1 {totals['bps_l1']}")
        print(f"server validate rate: max_pps {rate['max_pps']} max_bps_l2 {rate['max_bps_l2']} "
              f"max_bps_l1 {rate['max_bps_l1']}")
        assert (totals["pps"], totals["bps_l2"], totals["bps_l1"]) == (3, 3072, 3552)
        assert totals["pps"] == pytest.approx(rate["max_pps"], rel=1e-9)
        assert totals["bps_l2"] == pytest.approx(rate["max_bps_l2"], rel=1e-9)
        assert totals["bps_l1"] == pytest.approx(rate["max_bps_l1"], rel=1e-9)
    asyncio.run(run())


# -- pcap loop total -----------------------------------------------------------------------------------------

def udp_frame(size: int = 60) -> bytes:
    """An Ethernet/IPv4/UDP frame of `size` bytes, 16.0.0.1 to 48.0.0.1 (checksums left zero)."""
    payload = b"x" * (size - 42)
    ip = struct.pack("!BBHHHBBHII", 0x45, 0, 20 + 8 + len(payload), 1, 0, 64, 17, 0, 0x10000001, 0x30000001)
    udp = struct.pack("!HHHH", 1025, 12, 8 + len(payload), 0)
    return b"\x00\x11\x22\x33\x44\x55" + b"\x00\x66\x77\x88\x99\xaa" + b"\x08\x00" + ip + udp + payload


def write_pcap(path: Path, frames: list[bytes], gap_usec: int = 1000) -> Path:
    """A classic libpcap file (Ethernet link type) holding `frames`, `gap_usec` apart."""
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    for i, f in enumerate(frames):
        t = 1_700_000_000 * 1_000_000 + i * gap_usec
        out.append(struct.pack("<IIII", t // 1_000_000, t % 1_000_000, len(f), len(f)) + f)
    path.write_bytes(b"".join(out))
    return path


@pytest.mark.timeout(90)
def test_pcap_loop_total_on_the_guest(tmp_path: Path) -> None:
    """A two-packet pcap imported with loop_count 3, assigned and started at mult "1": simulate's loop pkts
    equals the oracle's opackets delta. On a mismatch the measured total is reported: the formula in
    simulate.py is corrected under a controller ruling, never edited to pass."""
    lib = ProfileLibrary(lambda: tmp_path / "lib")
    pcap = write_pcap(tmp_path / "two.pcap", [udp_frame(), udp_frame()])
    opts: trexapi.PcapOptions = {"ipg_usec": None, "speedup": 1.0, "loop_count": 3, "name_prefix": "",
                                 "rewrite_src": None, "rewrite_dst": None}

    async def run() -> None:
        """Import, simulate, assign, run and compare."""
        imp = await convert.import_file(str(pcap), "pcap", [], opts)
        slug, lp = await library_load(lib, "two loop 3", imp.streams)
        report = await sim(lib.get(slug).streams)
        [chain] = report["chains"]
        async with guest() as (s, oracle):
            measured = await burst_total(s, oracle, lp, "1")
        print(f"pcap loop: simulate chain {chain}; oracle opackets delta {measured}")
        assert chain["end"] == "loop" and chain["action_count"] == 3
        assert chain["pkts"] == measured, (
            f"the guest sent {measured} packets, simulate's loop formula says {chain['pkts']}: stop and "
            "report the measured total; simulate.py's loop formula is corrected under a controller ruling")
    asyncio.run(run())


# -- a next that names a disabled stream (ledger ruling, Task 5 review) --------------------------------------

@pytest.mark.timeout(90)
def test_next_to_a_disabled_stream_on_the_guest(tmp_path: Path) -> None:
    """burst_3st_1000pkt.py with its middle stream S1 disabled: the server refuses it on validate ("stream
    <id> is pointing to disabled stream <id>", jammy 2026-10-24), and simulate's notes say so."""
    lib = ProfileLibrary(lambda: tmp_path / "lib")

    async def run() -> None:
        """Import, disable S1, simulate, assign and compare."""
        imp = await convert.import_file(BURST, "py", [], None)
        streams = [dict(st) for st in imp.streams]
        streams[1]["enabled"] = False
        slug, lp = await library_load(lib, "burst S1 off", streams)
        report = await sim(lib.get(slug).streams)
        async with guest() as (s, _oracle):
            with pytest.raises(trexapi.TrexApiError) as refused:
                await s.assign_profile([0], lp, stop_first=True)
        print(f"disabled next: assign refused with {refused.value.message!r}; "
              f"simulate notes {report['notes']}")
        assert "pointing to disabled stream" in refused.value.message
        assert "the server refuses this profile on validate: S0 goes to disabled S1" in report["notes"]
    asyncio.run(run())
