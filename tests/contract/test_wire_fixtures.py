"""Recorded wire fixtures: the port-0 PortView the backend publishes in each named scenario.

The frontend tests load these through `wirePort(...)` in frontend/src/testWire.ts, so a fixture can only have
a shape the backend really publishes (the P2.5 final gate found a defect hidden by hand-written fixtures).

`make wire-fixtures` (WRITE_WIRE_FIXTURES=1) rewrites frontend/src/__fixtures__/portViews.json. Without the
variable the test compares what the stateful fake produces now with that file and fails naming the scenario
and the keys that differ.

Normalised before comparing and writing (everything else is exactly as published):
  owner   the local OS user (the session's own name) becomes "<local-user>"

`pgidMsg.json` and `utilMsg.json` hold the `pgid` and `util` messages the stats loop publishes (P3a Task 3),
by scenario. Also normalised there: `ts` becomes 0. The fake runs with --manual-clock and is stepped 10 s
once, so its counters are real and repeatable (rate x 10 s); everything else is exactly as published.

P3b (Task 5). `portViewsP3b.json` holds port 0's PortView per layer-configuration scenario (what `layerView`
reads); `xstats.json` the body of GET /api/ports/0/xstats; `pingMsg.json` the `ping` messages and
`neighbours.json` the POST /api/ports/scan6 body. How each is produced (the Global Constraints):
  l2, vlan_two_tags            the session's own Apply against the fake (the real code).
  l3_resolved, l3_unresolved   raw `set_l3` RPCs from the foreign client (with port 1's recorded hw_mac as
                               resolved_mac, and without), which the session only reads, so the L3 shapes
                               are the server's, not our Apply's.
  ipv6_auto, ipv6_address      the fake started `--stack linux_based`: the recorded legacy guest refuses
                               conf_ipv6 enable ("IPv6 is not supported with current stack"), so these two
                               views cannot come from it. Their shape is modelled from attr_to_json, not
                               recorded (see the fake's header).
  ping, neighbours             the session's real envelope with trexapi.ping_once / scan_neighbours stubbed
                               to return the records Task 1 recorded (tests/fixtures/p3b/summary.json),
                               verbatim in content.

P4a (Task 6). `studioLibrary.json`, `studioStreams.json` and `studioSim.json` hold the GET
/api/studio/profiles body, the first stream page and the simulate reply of the udp_for_benchmarks.py
(--packet_len 128) import. Normalised: the library directory becomes "<library>", the fixtures directory
"<fixtures>", every timestamp STUDIO_TS and the content revision "<rev>" (it hashes the timestamps).

P4b (Task 6). `packetTreeVlan.json` is the decode of the `vlan` frame of tests/fixtures/p3c/summary.json
(pinned since p3c-capture-114). `packet/definitions.json`, `buildUdp.json`, `buildError.json`,
`templates.json` and `decompile.json` hold the GET /api/studio/packet/definitions, POST build (a good and a
failing document), GET templates and POST decompile bodies, recorded through an entered client so the packet
engine closes. The definitions are host-dependent (Scapy reads /etc/ethertypes and /etc/protocols), so only
their SHAPE is compared; the rest is compared exactly. Normalised: the templates directory becomes
"<templates>".

P5 (Task 5). `monitorStl.json` and `monitorStateful.json` hold the `monitor` message the app publishes while
connected to the fake's `--pub-replay` of tests/fixtures/p5/pub-stl.jsonl and pub-stateful.jsonl, taken
once every PUB name the recording holds (summary.json `names`) is present. Normalised: `ts`, `stale_since`
(when set), every `known_ts` value and every `other[...].ts` become MONITOR_TS, and the target port 4500.
The replay loops from the moment the fake starts and the app joins mid-loop, so the counters and the
values inside the frames depend on where it was. The file keeps the values of the run that wrote it; the
comparison is of the SHAPE only (keys, and the type of every leaf; a list counts as a list).
"""
import asyncio
import base64
import getpass
import json
import os
import re
import time
from pathlib import Path

import pytest
from helpers import Recorder, wait_for
from support import FIXTURES, ROOT, TEST_TOKEN, authed
from test_api import WS, client, recv
from test_control import PROFILE, last_ports, loaded, params
from trex_web_gui import trexapi
from trex_web_gui.api.app import create_app
from trex_web_gui.sessions.port_config import LayerRequest
from trex_web_gui.trexapi import decode, scapy_exec

FIXTURE_DIR = ROOT / "frontend" / "src" / "__fixtures__"
FILE = FIXTURE_DIR / "portViews.json"
PGID_FILE = FIXTURE_DIR / "pgidMsg.json"
UTIL_FILE = FIXTURE_DIR / "utilMsg.json"
P3B_FILE = FIXTURE_DIR / "portViewsP3b.json"
XSTATS_FILE = FIXTURE_DIR / "xstats.json"
PING_FILE = FIXTURE_DIR / "pingMsg.json"
NEIGHBOURS_FILE = FIXTURE_DIR / "neighbours.json"
TEST_SETUPS_FILE = FIXTURE_DIR / "testSetups.json"
BUNDLE_PREVIEW_FILE = FIXTURE_DIR / "bundlePreview.json"
STUDIO_LIBRARY_FILE = FIXTURE_DIR / "studioLibrary.json"
STUDIO_STREAMS_FILE = FIXTURE_DIR / "studioStreams.json"
STUDIO_SIM_FILE = FIXTURE_DIR / "studioSim.json"
STUDIO_TS = "2026-01-01T00:00:00.000Z"
TREE_VLAN_FILE = FIXTURE_DIR / "packetTreeVlan.json"
PACKET_DIR = FIXTURE_DIR / "packet"
MONITOR_STL_FILE = FIXTURE_DIR / "monitorStl.json"
MONITOR_STATEFUL_FILE = FIXTURE_DIR / "monitorStateful.json"
MONITOR_TS = 1700000000.0
P5 = FIXTURES / "p5"
FLOW_LATENCY = str(ROOT / "tests" / "fixtures" / "profiles" / "flow_latency.py")
STATS_S = 0.1
LOCAL_USER = "<local-user>"
# the session's safety poll interval here; ports are published only when they change, so wait for several
# polls rather than for a message: _drop_idle_profiles has then run
POLL_S = 0.3
SETTLE_S = 4 * POLL_S


def normalise(view: dict) -> dict:
    """A deep copy of port view `view` (through JSON, so it is JSON-clean) with an `owner` equal to the local
    OS user replaced by "<local-user>"."""
    out = json.loads(json.dumps(view))                       # a deep copy that is also JSON-clean
    if out.get("owner") == getpass.getuser():
        out["owner"] = LOCAL_USER
    return out


def _start_bench(b) -> None:
    """Have foreign client `b` acquire port 0 (unforced), add the udp_for_benchmarks streams as profile
    "bench" and start it; `b` must come from foreign_client, which has loaded the vendored client."""
    from trex.stl.api import STLProfile
    b.acquire(ports=[0])
    b.add_streams(STLProfile.load_py(PROFILE).get_streams(), ports=["0.bench"])
    b.start(ports=["0.bench"])


def capture(fake_trex, foreign_client, new_session) -> dict[str, dict]:
    """Run each port-0 scenario (a fresh fake and session apiece; some with a foreign client already running
    "bench") and return {scenario name: the port 0 view the session last published, normalised}."""
    got: dict[str, dict] = {}

    def scenario(name, body, foreign=False):
        """Run `body(session, recorder)` on a fresh fake (a foreign client running "bench" there first when
        `foreign`), wait SETTLE_S, then store port 0's last published view, normalised, as got[name]. The
        session is closed by new_session.run."""
        f, rec = fake_trex(), Recorder()
        # run(): the optional foreign bench, connect, the scenario body, the settle wait, then record port 0's
        # view
        async def run():
            if foreign:
                _start_bench(foreign_client(f))
            s = new_session(rec, poll_s=POLL_S)
            await s.connect(params(f))
            await body(s, rec)
            await asyncio.sleep(SETTLE_S)
            got[name] = normalise(last_ports(rec)[0])
        new_session.run(run())

    # scenario body: connect only, acquire nothing
    async def idle_unowned(s, rec): pass
    # scenario body: acquire port 0
    async def idle_owned(s, rec): await s.acquire([0])
    # scenario body: acquire port 0 and assign the profile
    async def streams_owned(s, rec):
        await s.acquire([0])
        await s.assign_profile([0], await loaded([0]))
    # scenario body: the streams_owned steps, then start at 10kpps
    async def tx_owned(s, rec):
        await streams_owned(s, rec)
        await s.start([0], "10kpps")
    # scenario body: the tx_owned steps, then pause
    async def paused_owned(s, rec):
        await tx_owned(s, rec)
        await s.pause([0])
    # scenario body: force-acquire port 0 (the foreign client's "bench" is running on it)
    async def foreign_bench_tx(s, rec): await s.acquire([0], force=True)
    # scenario body: acquire port 0 and turn service mode on
    async def service_on(s, rec):
        await s.acquire([0])
        await s.set_service([0], True)
    # scenario body: acquire port 0 and set service filtered with mask 1
    async def service_filtered(s, rec):
        await s.acquire([0])
        await s.set_service([0], False, filtered=True, mask=1)

    scenario("idle_unowned", idle_unowned)
    scenario("idle_owned", idle_owned)
    scenario("streams_owned", streams_owned)
    scenario("tx_owned", tx_owned)
    scenario("paused_owned", paused_owned)
    scenario("foreign_bench_tx", foreign_bench_tx, foreign=True)
    scenario("foreign_bench_tx_unowned", idle_unowned, foreign=True)
    scenario("service_on", service_on)
    scenario("service_filtered", service_filtered)
    return got


def _diff_keys(a: dict, b: dict) -> list[str]:
    """The sorted keys, over both dicts, whose values differ (a key missing from one side reads as
    "<missing>")."""
    return sorted(k for k in set(a) | set(b) if a.get(k, "<missing>") != b.get(k, "<missing>"))


def _compare_or_write(file: Path, got: dict[str, dict]) -> None:
    """With WRITE_WIRE_FIXTURES=1, rewrite `file` with `got` (sorted keys, indent 2) and return, making the
    directory if needed. Otherwise compare `got` with `file` scenario by scenario and assert, naming each
    missing scenario and each differing key; FileNotFoundError when `file` is absent."""
    if os.environ.get("WRITE_WIRE_FIXTURES") == "1":
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(got, sort_keys=True, indent=2) + "\n")
        return
    want = json.loads(Path(file).read_text())
    problems = []
    for name in sorted(set(got) | set(want)):
        if name not in want or name not in got:
            problems.append(
                f"{name}: scenario missing from {'the file' if name not in want else 'the capture'}"
            )
        elif got[name] != want[name]:
            keys = _diff_keys(got[name], want[name])
            problems.append(
                f"{name}: differing keys {keys}; "
                + "; ".join(
                    f"{k}: published {got[name].get(k, '<missing>')!r} "
                    f"vs file {want[name].get(k, '<missing>')!r}"
                    for k in keys
                )
            )
    assert not problems, f"{file.name} drifted (run `make wire-fixtures` if intended):\n" + "\n".join(
        problems
    )


def test_wire_fixtures_match_what_the_backend_publishes(fake_trex, foreign_client, new_session):
    _compare_or_write(FILE, capture(fake_trex, foreign_client, new_session))


def normalise_msg(msg: dict) -> dict:
    """A deep copy of message `msg` (through JSON) with `ts` set to 0; KeyError when it has no `ts`."""
    out = json.loads(json.dumps(msg))
    out["ts"] = 0
    return out


def capture_pgid_util(fake_trex, new_session) -> tuple[dict[str, dict], dict[str, dict]]:
    """The (pgid, util) answer dicts from the fake at TX with flow_latency.py (pg_id 5 flow stats, pg_id 7
    flow latency), then stopped, then removed and re-added."""
    pgid: dict[str, dict] = {}
    util: dict[str, dict] = {}
    f, rec = fake_trex("--manual-clock"), Recorder()

    async def fresh(kind):
        """The second `kind` message published after now: a read in flight on the reader can be published
        after the command returns, and was read before it."""
        n = len([m for m in rec.msgs if m["type"] == kind])
        await wait_for(lambda: len([m for m in rec.msgs if m["type"] == kind]) > n + 1, 3)
        return [m for m in rec.msgs if m["type"] == kind][n + 1]

    async def run():
        """Connect, acquire port 0, assign flow_latency.py and start it at 10kpps, step the fake's clock 10 s,
        then record a fresh pgid and util message at tx, a pgid after stop, and a pgid after reset and a new
        assign_profile; fills `pgid` and `util`."""
        s = new_session(rec, stats_s=STATS_S)
        await s.connect(params(f))
        await s.acquire([0])
        loaded_ = await trexapi.load_profile(FLOW_LATENCY, [0], [])
        await s.assign_profile([0], loaded_)
        await s.start([0], "10kpps")
        # counters = rate x 10 s
        await s._run(lambda: s._client._transmit("fake_advance_clock", params={"seconds": 10}))
        # the latency id shows after Tx
        await wait_for(lambda: any(m["type"] == "pgid" and m["latency"] for m in rec.msgs), 3)
        await asyncio.sleep(2 * STATS_S)
        pgid["tx"] = normalise_msg(await fresh("pgid"))
        util["tx"] = normalise_msg(await fresh("util"))
        await s.stop([0])
        pgid["stopped"] = normalise_msg(await fresh("pgid"))
        await s.reset([0])                                   # removes the streams: the ids go
        await s.assign_profile([0], await trexapi.load_profile(FLOW_LATENCY, [0], []))
        pgid["readded"] = normalise_msg(await fresh("pgid"))
    new_session.run(run())
    return pgid, util


def test_pgid_and_util_wire_fixtures_match_what_the_backend_publishes(fake_trex, new_session):
    pgid, util = capture_pgid_util(fake_trex, new_session)
    _compare_or_write(PGID_FILE, pgid)
    _compare_or_write(UTIL_FILE, util)


SUMMARY = json.loads((FIXTURES / "p3b" / "summary.json").read_text())
PEER_MAC = SUMMARY["resolved_mac"]                    # port 1's hw_mac
# the recorded neighbour; only recorded addresses are used here
V6_ADDRESS = SUMMARY["scan"]["raw"]["0"][0]["ipv6"]
_RECORD_STATUS = {"success": "reply", "timeout": "timeout"}


def _raw(c, method: str, **params) -> None:
    """Send raw `method` to port 0 through client `c` (its handler, non-blocking, plus `params`); asserts the
    call succeeded."""
    rc = c._transmit(method, params={"handler": c.ports[0].handler, "port_id": 0, "block": False, **params})
    assert rc, rc.err()


def capture_p3b(fake_trex, foreign_client, new_session) -> dict[str, dict]:
    """Run each layer scenario (a fresh fake and session apiece) and return {scenario name: the port 0 view
    the session last published, normalised}."""
    got: dict[str, dict] = {}

    def scenario(name, body, *flags, foreign=False):
        """Run one scenario on a fake started with `flags`. With `foreign`, a foreign client force-acquires
        port 0 and `body(c)` (a plain call) acts on it before the session connects, and the session only
        reads it; otherwise the session acquires port 0 and awaits `body(session)`. Then wait SETTLE_S and
        store port 0's last published view, normalised, as got[name]."""
        f, rec = fake_trex(*flags), Recorder()

        # run(): the foreign or session path above, then the settle wait and recording port 0's view
        async def run():
            if foreign:
                c = foreign_client(f)
                c.acquire(ports=[0], force=True)
                body(c)
            s = new_session(rec, poll_s=POLL_S)
            await s.connect(params(f))
            if not foreign:
                await s.acquire([0])
                await body(s)
            await asyncio.sleep(SETTLE_S)
            got[name] = normalise(last_ports(rec)[0])
        new_session.run(run())

    def l2(vlan, ipv6=None):
        """The scenario body that applies an L2 layer on port 0 toward PEER_MAC with `vlan` and `ipv6` through
        the session."""
        # body(): apply an L2 layer request toward PEER_MAC on port 0 through session `s`
        async def body(s):
            await s.apply_layer(0, LayerRequest("L2", PEER_MAC, None, None, vlan, ipv6))
        return body

    scenario("l2", l2([]))
    scenario("vlan_two_tags", l2([10, 20]))
    scenario(
        "l3_resolved",
        lambda c: _raw(c, "set_l3", src_addr="1.1.1.1", dst_addr="2.2.2.2", resolved_mac=PEER_MAC),
        foreign=True,
    )
    scenario(
        "l3_unresolved", lambda c: _raw(c, "set_l3", src_addr="1.1.1.1", dst_addr="1.1.1.99"), foreign=True
    )
    scenario("ipv6_auto", l2([], (True, None)), "--stack", "linux_based")
    scenario("ipv6_address", l2([], (True, V6_ADDRESS)), "--stack", "linux_based")
    return got


def test_p3b_port_views_match_what_the_backend_publishes(fake_trex, foreign_client, new_session):
    _compare_or_write(P3B_FILE, capture_p3b(fake_trex, foreign_client, new_session))


def test_xstats_wire_fixture_matches_the_route(fake_trex, new_session):
    f, rec = fake_trex(), Recorder()
    out: dict[str, dict] = {}

    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        v = await s.xstats(0)
        out["port0"] = {"port": v.port, "values": v.values}      # the route's body (api/app.py xstats)
    new_session.run(run())
    _compare_or_write(XSTATS_FILE, out)


def _recorded_line(rec: dict) -> trexapi.PingLine:
    """What ping_once would return for a recorded record: the record's text and rtt, its status in our
    words."""
    return trexapi.PingLine(_RECORD_STATUS[rec["status"]], rec["text"], rec["rtt"])


def _stub_ping(line: trexapi.PingLine):
    """A stand-in for trexapi.ping_once that ignores its arguments and returns `line`."""
    return lambda c, port, dst, pkt_size: line


def capture_ping_and_neighbours(
    fake_trex, new_session, monkeypatch
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Publish a ping for each recorded record (reply, timeout, reply_v6) with trexapi.ping_once stubbed, then
    a scan with scan_neighbours stubbed to the recorded neighbour; returns ({name: the `ping` message,
    normalised}, {"scan": the neighbours and service_restore_error}). Replaces both on trexapi through
    `monkeypatch`, which undoes it at test end."""
    pings = SUMMARY["pings"]
    recorded = {"reply": pings["ok"][0]["records"][0], "timeout": pings["timeout"]["records"][0],
                "reply_v6": pings["v6"]["records"][0]}
    msgs: dict[str, dict] = {}
    neigh: dict[str, dict] = {}
    f, rec = fake_trex(), Recorder()

    # run(): per recorded record, ping with ping_once stubbed and keep the published ping message; then scan
    async def run():
        s = new_session(rec)
        await s.connect(params(f))
        await s.acquire([0])
        for name, record in recorded.items():
            monkeypatch.setattr(trexapi, "ping_once", _stub_ping(_recorded_line(record)))
            before = len(rec.msgs)
            r = await s.ping(0, "2.2.2.2" if name != "reply_v6" else V6_ADDRESS, 1, 64)
            assert len(r.lines) == 1
            msgs[name] = normalise_msg(next(m for m in rec.msgs[before:] if m["type"] == "ping"))
        monkeypatch.setattr(
            trexapi,
            "scan_neighbours",
            lambda c, p, timeout_s=3: [trexapi.Neighbour(**SUMMARY["scan"]["raw"]["0"][0])],
        )
        r6 = await s.scan(0)
        neigh["scan"] = {
            "neighbours": [x._asdict() for x in r6.neighbours],
            "service_restore_error": r6.service_restore_error,
        }
    new_session.run(run())
    return msgs, neigh


def test_ping_and_neighbours_wire_fixtures(fake_trex, new_session, monkeypatch: pytest.MonkeyPatch):
    msgs, neigh = capture_ping_and_neighbours(fake_trex, new_session, monkeypatch)
    _compare_or_write(PING_FILE, msgs)
    _compare_or_write(NEIGHBOURS_FILE, neigh)


# P3c (Task 5). `captureMsg.json`, `capturePkts.json`, `captureSave.json`, `packetTree.json` and
# `captureFiles.json`: the `capture`, `capture_pkts` and `capture_save` messages the session publishes, the
# POST /api/capture/packet/tree body and the GET /api/capture/files body, from the fake started --manual-clock
# (counters and packets are real and repeatable: rate x seconds). Normalised: `ts` becomes 0 and a spool
# file's `mtime` too; the Spool's directory becomes "<spool>", the server string in a sidecar "<server>" (the
# fake's port is random), and the UTC stamp in a file name "<stamp>".
CAPTURE_FILE = FIXTURE_DIR / "captureMsg.json"
CAPTURE_PKTS_FILE = FIXTURE_DIR / "capturePkts.json"
CAPTURE_SAVE_FILE = FIXTURE_DIR / "captureSave.json"
TREE_FILE = FIXTURE_DIR / "packetTree.json"
CAPTURE_FILES_FILE = FIXTURE_DIR / "captureFiles.json"
STAMP = re.compile(r"\d{8}T\d{6}Z|\d{8}-\d{6}")


def normalise_capture(msg: dict) -> dict:
    """A deep copy of message `msg` (through JSON) with `ts` set to 0 when it has one."""
    out = json.loads(json.dumps(msg))
    if "ts" in out:
        out["ts"] = 0
    return out


def normalise_file(f: dict) -> dict:
    """A copy of spool listing entry `f` with `mtime` 0, a set `server` replaced by "<server>" (a missing
    one added as None) and every STAMP match in `name` replaced by "<stamp>"."""
    out = dict(f)
    out["mtime"] = 0
    out["server"] = "<server>" if out.get("server") else out.get("server")
    out["name"] = STAMP.sub("<stamp>", out["name"])
    return out


def capture_p3c(fake_trex, new_session, tmp_path) -> tuple[dict, dict, dict, dict, dict]:
    """Drive the session through the capture scenario on a manual-clock fake, with its spool under
    `tmp_path`/captures, and return (cap, pkts, save, files, tree): the normalised capture, capture_pkts and
    capture_save messages, the spool listing and the decoded UDP packet tree. Saves one recorder to a file in
    the spool."""
    f, rec = fake_trex("--manual-clock"), Recorder()
    cap: dict[str, dict] = {}
    pkts: dict[str, dict] = {}
    save: dict[str, dict] = {}
    files: dict[str, dict] = {}
    tree: dict[str, dict] = {}

    # of(): the messages of type `kind` the recorder has so far
    def of(kind: str) -> list[dict]:
        return [m for m in rec.msgs if m["type"] == kind]

    # advance(): step the fake's clock by `seconds` from the control client, on the session's executor
    async def advance(s, seconds):
        await s._run(lambda: s._client._transmit("fake_advance_clock", params={"seconds": seconds}))

    # run(): acquire both ports, assign the profile, put port 1 in service, start a recorder and a monitor on
    # it, transmit from port 0, fill cap, pkts, save, files and tree from what the session publishes (saving
    # the recorder to the spool) and from decoding the recorded UDP frame
    async def run():
        s = new_session(rec, spool_dir=tmp_path / "captures")
        await s.connect(params(f))
        await s.acquire([0, 1])
        await s.assign_profile([0, 1], await loaded([0, 1]))
        await s.set_service([1], True)
        cap["idle"] = normalise_capture(s.capture_snapshot())
        rid = await s.recorder_start([], [1], "", 120)
        mon = await s.monitor_start([], [1], "", 100)
        await s.start([0], "1kpps")
        await advance(s, 1.0)
        await wait_for(lambda: any(m["rows"] for m in of("capture_pkts")), 5)
        pkts["batch"] = normalise_capture(next(m for m in of("capture_pkts") if m["rows"]))
        # both(): True when the message has a monitor and exactly two captures, and all of them have matched
        # packets
        def both(m):
            return bool(m["monitor"] and m["monitor"]["matched"] > 0 and len(m["captures"]) == 2
                        and all(c["matched"] > 0 for c in m["captures"]))
        await wait_for(lambda: any(both(m) for m in of("capture")), 5)
        cap["monitor_and_recorder"] = normalise_capture(next(m for m in of("capture") if both(m)))
        await s.monitor_stop()
        n = len(of("capture_save"))
        saved = await s.recorder_save(rid)
        save["messages"] = {"all": [normalise_capture(m) for m in of("capture_save")[n:]]}
        for m in save["messages"]["all"]:
            if m["file"]:
                m["file"] = normalise_file(m["file"])
        listing = s.spool().list()
        files["listing"] = {
            "dir": "<spool>",
            "files": [normalise_file(x) for x in listing],
            "total": len(listing),
        }
        assert saved["name"] == listing[0]["name"] and mon.id != rid
        tree["udp"] = await scapy_exec.arun(decode.tree, base64.b64decode(SUMMARY_P3C["frames"]["udp"]))
    new_session.run(run())
    return cap, pkts, save, files, tree


SUMMARY_P3C = json.loads((FIXTURES / "p3c" / "summary.json").read_text())


def test_p3c_capture_wire_fixtures(fake_trex, new_session, tmp_path):
    cap, pkts, save, files, tree = capture_p3c(fake_trex, new_session, tmp_path)
    _compare_or_write(CAPTURE_FILE, cap)
    _compare_or_write(CAPTURE_PKTS_FILE, pkts)
    _compare_or_write(CAPTURE_SAVE_FILE, save)
    _compare_or_write(CAPTURE_FILES_FILE, files)
    _compare_or_write(TREE_FILE, tree)


def _setup_body(name: str, path: str, port_extra: dict) -> dict:
    """A PUT /api/test-setups body of one port whose profile is `path`; `port_extra` joins the port."""
    return {"name": name, "server": "h:4501", "dashboard": None,
            "ports": [{"key": "#0", "port": 0, "profile": {"path": path, "kind": "py"},
                       "tunables": {"values": {}, "extra": ""}, "mult": "10kpps", "duration": -1,
                       "force": False, **port_extra}]}


def _no_ts(msg: dict) -> dict:
    """`msg` (a test_setups message) with each setup's created and updated set to a fixed string."""
    out = json.loads(json.dumps(msg))
    for s in out["setups"]:
        s["created"] = s["updated"] = "2026-01-01T00:00:00Z"
    return out


def test_p3d_setups_and_bundle_preview_wire_fixtures(tmp_path):
    """testSetups.json: the test_setups message after two PUTs (one with a layer and the Run flags).
    bundlePreview.json: the preview of that machine's bundle on another one that has an older lab-a pointing
    elsewhere, a local-only setup, other preferences and a connection of the same name."""
    layer = {"mode": "L2", "dst_mac": "aa:bb:cc:dd:ee:ff", "vlan": [100]}
    with client(tmp_path / "a") as c, c.websocket_connect(WS) as ws:
        for _ in range(8):
            recv(ws)
        c.put("/api/settings", json={"preferences": {"wireshark_path": "/opt/ws"},
                                     "connections": [{"name": "lab", "host": "10.0.0.5"}]})
        recv(ws)
        c.put("/api/test-setups/lab-a", json=_setup_body("Lab A", "/p/udp.py", {
            "acquire_force": True, "stop_first": True, "layer": layer}))
        recv(ws)
        c.put("/api/test-setups/lab-b", json=_setup_body("Lab B", "/p/udp.py", {}))
        setups_msg = recv(ws)
        assert setups_msg["type"] == "test_setups"
        exported = c.get("/api/settings/bundle").json()
    with client(tmp_path / "b") as c:
        c.put("/api/test-setups/lab-a", json=_setup_body("Lab A", "/old/other.py", {}))
        c.put("/api/test-setups/old", json=_setup_body("Old", "/old/other.py", {}))
        c.put("/api/settings", json={"connections": [{"name": "lab", "host": "10.9.9.9"}]})
        r = c.post("/api/settings/bundle/preview", json=exported)
        assert r.status_code == 200
        preview = r.json()
    assert preview["path_changes"] and preview["sections"]["test_setups"]["flags"]
    _compare_or_write(TEST_SETUPS_FILE, {"two_setups": _no_ts(setups_msg)})
    _compare_or_write(BUNDLE_PREVIEW_FILE, {"preview": preview})


def _studio_normalise(body: object, library: Path) -> object:
    """A deep copy of a Studio reply with the library and fixtures directories, the timestamps and the
    revision replaced by fixed strings."""
    text = json.dumps(body).replace(str(library), "<library>").replace(str(FIXTURES), "<fixtures>")
    out = json.loads(text)

    def walk(v: object) -> None:
        """Replace every timestamp and rev in place."""
        if isinstance(v, dict):
            for k in v:
                if k in ("created", "updated", "imported") and isinstance(v[k], str):
                    v[k] = STUDIO_TS
                elif k == "rev":
                    v[k] = "<rev>"
                else:
                    walk(v[k])
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(out)
    return out


def test_studio_wire_fixtures_match_what_the_backend_answers(tmp_path):
    """The listing, the first stream page and the simulate reply of the udp_for_benchmarks import."""
    with client(tmp_path) as c:
        r = c.post("/api/studio/import", json={"path": PROFILE, "tunables": ["--packet_len", "128"],
                                                "tunable_form": {"values": {"packet_len": 128}, "extra": ""}})
        assert r.status_code == 200, r.text
        slug = r.json()["slug"]
        listing = c.get("/api/studio/profiles").json()
        page = c.get(f"/api/studio/profiles/{slug}/streams").json()
        sim = c.post(f"/api/studio/profiles/{slug}/simulate").json()
    library = tmp_path / "profiles"
    assert listing["dir"] == str(library) and page["stream_count"] == 1 and sim["round_trip"]["equal"]
    _compare_or_write(STUDIO_LIBRARY_FILE, {"udp_for_benchmarks": _studio_normalise(listing, library)})
    _compare_or_write(STUDIO_STREAMS_FILE, {"udp_for_benchmarks": _studio_normalise(page, library)})
    _compare_or_write(STUDIO_SIM_FILE, {"udp_for_benchmarks": _studio_normalise(sim, library)})


def _shape(x: object) -> object:
    """`x` with every leaf replaced by its type name and every list by "list"."""
    if isinstance(x, dict):
        return {k: _shape(v) for k, v in x.items()}
    return "list" if isinstance(x, list) else type(x).__name__


def normalise_monitor(msg: dict) -> dict:
    """The `monitor` message with ts, stale_since (when set), known_ts and each other[...].ts fixed, and the
    fake's random port replaced by 4500."""
    out = dict(msg)
    out["ts"] = MONITOR_TS
    out["target"] = {**msg["target"], "port": 4500}
    out["stale_since"] = None if msg["stale_since"] is None else MONITOR_TS
    out["known_ts"] = {k: MONITOR_TS for k in msg["known_ts"]}
    out["other"] = {k: {**v, "ts": MONITOR_TS} for k, v in msg["other"].items()}
    return out


def capture_monitor(fake_trex, tmp_path, replay: str, names: list[str]) -> dict:
    """Connect an app to a fake replaying `replay` and return the first `monitor` message on /api/ws that
    holds every name in `names`, normalised."""
    fake = fake_trex("--pub-replay", str(P5 / replay))
    app = create_app(tmp_path, token=TEST_TOKEN, monitor_target=(fake.host, fake.pub_port))
    with authed(app) as c, c.websocket_connect(WS) as ws:
        end = time.monotonic() + 40.0          # a full replay loop (30 s) in case the join missed the names
        while True:
            m = recv(ws, timeout=max(0.5, end - time.monotonic()))
            if m["type"] == "monitor" and all(n in m["known_ts"] or n in m["other"] for n in names):
                return normalise_monitor(m)


def _compare_shape_or_write(file: Path, got: dict, check_monitor: bool = True) -> None:
    """With WRITE_WIRE_FIXTURES=1 write `got` to `file` (sorted keys, indent 2, making the directory);
    otherwise assert that its shape equals the file's, and (unless `check_monitor` is False) that the file
    is a connected monitor message at MONITOR_TS."""
    if os.environ.get("WRITE_WIRE_FIXTURES") == "1":
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(got, sort_keys=True, indent=2) + "\n")
        return
    want = json.loads(file.read_text())
    assert _shape(got) == _shape(want), f"{file.name} drifted in shape (run `make wire-fixtures` if intended)"
    if check_monitor:
        assert want["state"] == "connected" and want["ts"] == MONITOR_TS


def test_p5_monitor_wire_fixtures(fake_trex, tmp_path):
    """The monitor message for the STL recording (trex-global only) and the stateful one (every name)."""
    names = json.loads((P5 / "summary.json").read_text())["names"]
    stl = capture_monitor(fake_trex, tmp_path / "stl", "pub-stl.jsonl", list(names["stl"]))
    stateful = capture_monitor(fake_trex, tmp_path / "stateful", "pub-stateful.jsonl",
                               list(names["stateful"]))
    assert stl["global"] is not None and stl["latency_v2"] is None and stl["templates"] is None
    assert stateful["latency_v2"] is not None and stateful["templates"] is not None
    assert stateful["latency"] is not None and stateful["rx_check"] is not None
    _compare_shape_or_write(MONITOR_STL_FILE, stl)
    _compare_shape_or_write(MONITOR_STATEFUL_FILE, stateful)


def test_packet_tree_vlan_fixture_is_pinned():
    """packetTreeVlan.json is the decode of the recorded `vlan` frame (p3c-capture-114)."""
    tree = {"vlan": decode.tree(base64.b64decode(SUMMARY_P3C["frames"]["vlan"]))}
    _compare_or_write(TREE_VLAN_FILE, tree)


PACKET_UDP_DOC = {"packet": [
    {"id": "Ether", "fields": [{"id": "dst", "value": "52:52:18:89:91:ea"},
                               {"id": "src", "value": "8e:4e:97:11:bf:a8"}]},
    {"id": "IP", "fields": [{"id": "src", "value": "16.0.0.1"}, {"id": "dst", "value": "48.0.0.1"}]},
    {"id": "UDP", "fields": [{"id": "sport", "value": 1025}, {"id": "dport", "value": 12}]},
    {"id": "Raw", "fields": [{"id": "load", "value": {"vtype": "BYTES", "generate": "template_code",
                                                      "template_code": "78", "size": 16}}]}]}
PACKET_ERROR_DOC = {"packet": [{"id": "Ether"}, {"id": "IP", "fields": [
    {"id": "id", "value": {"vtype": "EXPRESSION", "expr": "1/0"}}]}]}
PACKET_FE_DOC = {"packet": [{"id": "Ether"}, {"id": "IP"}, {"id": "UDP"}], "feInstructions": [
    {"id": "STLVmFlowVar", "parameters": {"name": "ip_src", "min_value": "16.0.0.1", "max_value": "16.0.0.10",
                                          "size": "4", "op": "inc"}},
    {"id": "STLVmWrFlowVar", "parameters": {"fv_name": "ip_src", "pkt_offset": "IP.src"}}],
    "fePrarameters": {"cache_size": "10"}}


# Fields whose display text (hvalue) Scapy takes from the host's /etc/ethertypes and /etc/protocols (the
# TRex build stubs /etc/services out, so ports are never named). R12, Task 6 M1: the file holds this
# host's names; another host may show the number.
_TABLE_NAMED = {("Ether", "type"), ("IP", "proto")}


def _host_named(built: dict, want_file: Path) -> dict:
    """`built` with each _TABLE_NAMED hvalue that differs from the recorded file's replaced by the file's,
    but only when it is the field's own number (decimal or hex). A different name still fails the compare;
    no table is patched and no other field is touched."""
    if os.environ.get("WRITE_WIRE_FIXTURES") == "1" or not want_file.exists():
        return built
    want = json.loads(want_file.read_text())["udp"]["data"]
    out = json.loads(json.dumps(built))
    for layer, recorded in zip(out["data"], want, strict=False):
        for field, rec in zip(layer["fields"], recorded["fields"], strict=False):
            if (layer["id"], field["id"]) in _TABLE_NAMED and field["hvalue"] != rec["hvalue"]:
                number = field["value"]
                if field["hvalue"] in (str(number), hex(number)):
                    field["hvalue"] = rec["hvalue"]
    return out


def test_host_named_accepts_only_the_number(tmp_path, monkeypatch):
    monkeypatch.delenv("WRITE_WIRE_FIXTURES", raising=False)  # make wire-fixtures sets it
    want = tmp_path / "w.json"
    base = {"data": [{"id": "Ether", "fields": [{"id": "type", "value": 2048, "hvalue": "IPv4"}]}]}
    want.write_text(json.dumps({"udp": base}))
    number = {"data": [{"id": "Ether", "fields": [{"id": "type", "value": 2048, "hvalue": "0x800"}]}]}
    other = {"data": [{"id": "Ether", "fields": [{"id": "type", "value": 2048, "hvalue": "ip4"}]}]}
    assert _host_named(number, want) == base
    assert _host_named(other, want) == other


def test_packet_wire_fixtures(tmp_path):
    """The packet group, recorded with an entered client (the lifespan closes the engine)."""
    url = "/api/studio/packet"
    with client(tmp_path) as c:
        defs = c.get(f"{url}/definitions")
        built = c.post(f"{url}/build", json={"doc": PACKET_UDP_DOC})
        failed = c.post(f"{url}/build", json={"doc": PACKET_ERROR_DOC})
        listing = c.get(f"{url}/templates")
        parts = c.post(f"{url}/to-stream", json={"doc": PACKET_FE_DOC})
        assert parts.status_code == 200, parts.text
        decompiled = c.post(f"{url}/decompile", json={"binary": parts.json()["packet"]["binary"],
                                                      "vm": parts.json()["vm"]})
    for r in (defs, built, failed, listing, decompiled):
        assert r.status_code == 200, r.text
    assert built.json()["error"] is None and "ZeroDivisionError" in failed.json()["error"]
    dirs = listing.json()
    assert dirs["dir"] == str(tmp_path / "templates")
    listing_body = {**dirs, "dir": "<templates>"}
    _compare_shape_or_write(PACKET_DIR / "definitions.json", {"definitions": defs.json()},
                            check_monitor=False)
    _compare_or_write(PACKET_DIR / "buildUdp.json",
                      {"udp": _host_named(built.json(), PACKET_DIR / "buildUdp.json")})
    _compare_or_write(PACKET_DIR / "buildError.json", {"expression": failed.json()})
    _compare_or_write(PACKET_DIR / "templates.json", {"listing": listing_body})
    _compare_or_write(PACKET_DIR / "decompile.json", {"flow_var": decompiled.json()})
