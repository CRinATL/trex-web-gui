"""P0 spike 5b: fake TRex (ZMQ ROUTER + PUB) replaying recorded fixtures. Skeleton: enough for
STLClient.connect() and the post-connect queries.

P2 adds a stateful port model (ownership, handlers, streams, traffic state, service, attrs), per-session
event readers and SIGUSR2 "server restart", all mirrored from tests/fixtures/p2/ (recorded from a real
v3.08 guest): error texts from negatives.json, emitted events from emits.json keyed by method name
(a key with no RPC behind it, such as "late_connect", is a recorder mark and is never looked up),
seq scheme from summary.json (per_session_seq: each reader counts from 0).

Network delay (P3b follow-up 4): --delay-ms D and --jitter-ms J (ints, default 0) hold every reply and every
PUB message D + U(-J, J) ms (floored at 0) after it was produced, one FIFO per peer (the RPC socket is a
ROUTER, one queue per identity; PUB is one queue), so jitter never reorders a peer's stream and two clients'
delays overlap instead of adding up. U comes from random.Random(--seed), which defaults to the bound
rpc port; the ready line prints delay_ms, jitter_ms and seed. With D = J = 0 a reply goes out in the same
loop iteration, as before.

Known divergences from the real server:
- link, LED and flow-control changes are accepted; the measured net_af_packet port reports them
  unsupported (is_link_supported, is_led_supported, is_fc_supported are false).
- rx_filter_mode is not mapped to a status key (P2 never sends it).
- the class name in the api_h_mismatch text is always 'common' (recorded on a common-class method);
  the fake answers the same text on any method.
- validate_result and the stream shapes come from an inline 1 pps UDP stream; rates are not representative.
- the injected seq gap (--seq-gap-at-start, SIGUSR1) is served to every get_async_events session_id: each one
  seen since start or restart when SIGUSR1 arms it, plus any that subscribes later while it stays armed, so
  the control client and the reader both see seqs 5 then 7. A restart (SIGUSR2) clears and disarms it. A dead
  session's share is never consumed. An armed gap stays armed until a restart: a reconnect to the same fake (a
  new session_id) is served 5 then 7 again and so is LOST at once. A second SIGUSR1 does not refill a
  subscriber that was already served (its deque exists, even if emptied).
- SIGUSR2 restart is applied by the request loop before the next message, not instantly.
- start_traffic/update_traffic echo the requested mul value (only pps/abs was recorded) and ts is wall-clock.
- state preconditions the real server refuses are accepted: start while TX, pause unless TX, resume unless
  PAUSE, update while not active. No error text was recorded for them, so none is invented.
- filtered service mode (service_filtered true, with a mask) is accepted; the real server refuses it unless it
  runs with --software, and that error text was not recorded, so none is invented.
- stats (P3, mirrors tests/fixtures/p3/): the batch shape is the recorded idle answer; counters and rates come
  from the fake's port state. The recorded server's rates ramp over several seconds (tx_pps 5787, 7790, 8855
  at 10kpps); the fake's rates jump to the started pps at once and fall to 0 on stop or pause, with no ramp,
  no decay and no idle noise. Counters integrate pps over wall time while the port is TX (accumulated across
  pause and resume), frozen otherwise. Port 0's Tx shows as port 1's Rx and the reverse (recorded
  rx_advancing_ports_when_port0_transmits), with no loss and no latency; bytes per packet is the stream's
  frame length plus 4 (the recorded profile counts 64). Burst and multi-burst streams, a start duration, and a
  "stop after N packets" end are not modelled: a started profile transmits until stopped. A multiplier other
  than pps/kpps/mpps (wire type pps) or x (wire type raw), or an update op other than abs, is accepted by
  start/update (echoed as before) but makes the fake raise when stats are asked while that profile transmits
  (a test bug, not a protocol text).
- cpu_util, bw_per_core and rx_cpu_util stay at the recorded idle values in every state. The reverse direction
  (port 1 to port 0 Rx) is assumed by pid ^ 1; only port 0 transmitting was recorded.
- PGID and utilization stats (P3a, mirrors tests/fixtures/p3a/): get_active_pgids lists the pg_ids of the
  streams the fake holds (each stream's flow_stats.stream_id, by rule_type; a "tpg" rule is a test bug and
  raises). get_pgid_stats answers in the server's wire shape (flow_stats rb/rbs/rp/rps/tb/tbs/tp/tps per port,
  latency lat/jit, "g": null). A stream's rate is the profile's started pps times its own pps over the
  profile's own total (profile_rate's rule); counters integrate it over wall time while the port is TX and
  freeze otherwise; rates jump with no ramp. Port 0's Tx is port 1's Rx (pid ^ 1), with no loss. A latency
  pg_id appears in flow_stats too, and in the latency map only once its port has transmitted, with the one
  recorded latency block (average, histogram, jitter, totals) copied unchanged: the fake invents no latency
  values. ver_id comes from one fake-global counter taken when a pg_id is first added and dropped when its
  last stream goes (the server's m_ver_id_pool++). A requested id that is not active is left out; if no
  requested id is active the answer is the recorded empty one (the server's dump_json skips ids it does not
  hold, `if (user_id_info == NULL) continue`, and sends null flow_stats and ver_id). More than 1152 ids
  requested, or [] with more than 1152 active, answers the server's error text (trex_stl_fs.cpp, dump_json).
  The fake accepts any number of streams.
- get_utilization replays one recorded answer in every state: the first sample taken while port 0 transmitted,
  shape and history as recorded (history[0] is the newest CPU sample). It does not follow the fake's state.
- --manual-clock (test hook): counters integrate a clock that moves only on the fake_advance_clock RPC (params
  {"seconds": n}), so a wire fixture holds real, repeatable counters. Not a TRex method.
- --server-version overrides get_version's version string (test hook for the version refusal).
- link changes are accepted but the link-change event (type 8) is never emitted; the real server sends it
  only on a polled link change, never on a set_port_attr RPC (main_dpdk.cpp:5078-5108).
- stop_traffic, pause_traffic and resume_traffic of an UNKNOWN named profile answer {} (no state change, no
  event); the real server throws via valide_profile (trex_stl_port.cpp:1024-1025). No error text was recorded,
  so none is invented. Unreachable through the client: "N.*" expands only to profiles it has cached.
- port-level events (types 0-3) use the method's type id from emits.json; the real server picks the type from
  the (old, new) aggregate-state transition map (trex_stl_port.cpp:1482-1493).

- port configuration (P3b, mirrors tests/fixtures/p3b/, recorded from a v3.08 guest on the "legacy" stack):
  each port keeps its own recorded attr. set_l2, set_l3, set_vlan and conf_ipv6 change that port's attr as
  CStackBase::attr_to_json builds it (trex_stack_base.cpp:304-345) and answer {} at once, with no async ticket
  (the legacy stack answers synchronously). The refusals are the server's verify_state text
  (trex_port.cpp:377-413, 482-484) outside IDLE, STREAMS and ASTF_LOADED, and "Maximal number of stacked VLANs
  is 2, got: N". Only the set_l2_mode text was recorded; the other three commands' texts imitate the same
  source line with their own command names (set_l3_mode, conf_ipv6, set_vlan_cfg). The other validation errors
  of those handlers (a malformed MAC or address, "invalid VLAN tag") are not modelled: the fake accepts what
  it is sent. A set_l3 without resolved_mac leaves ether.dst and sets ether.state "unconfigured" (recorded).
  conf_ipv6 enabled is refused on the legacy stack with the recorded text; --stack linux_based makes
  get_system_info report that stack and conf_ipv6 set ipv6.enabled and ipv6.src (the given address, "" for
  auto), the shapes tests/fixtures/p3b-v6 recorded from a linux_based guest. Not modelled: on linux_based the
  real server answers set_l2, set_l3, set_vlan and conf_ipv6 with a WIP ticket that the client polls once with
  get_async_results (about 0.3 s, recorded); the fake answers them at once. `push_pkts` answers the
  unknown-method error (not run against the fake); resolve, ping and scan are not run either.

- capture (P3c, mirrors tests/fixtures/p3c/): `capture` status, start, fetch, stop and remove answer the
  recorded shapes and texts ("capture limit has reached" at 10 captures, stopped ones counted; "capture ID not
  found"; the one recorded invalid-filter error). A capture needs no port ownership and belongs to no client:
  it survives a disconnect and a restart (SIGUSR2) clears it. `remove` has no state check. Ids count up from 1
  and are not reused. The fake evaluates no BPF: every filter text except the recorded invalid one ("not a
  filter((") is accepted and matches everything. Packets are synthesised, not captured: while a profile
  transmits, each of its packets is `packet.binary` of the profile's streams taken round-robin (per-stream
  rate ratios are ignored), at the profile's started pps on the fake's clock (--manual-clock aware, folded in
  by Port.settle), `origin` "TX" on a captured Tx port and "RX" on the peer (pid ^ 1) when its Rx is captured;
  `ts` is the fake's clock, `wirelen` the length of `binary`. `index` is 1-based and bumped for every match,
  dropped or not. A fixed capture drops the newest past `limit`, a cyclic one the oldest; `matched` counts
  every match, `fetched` every packet handed out; `fetch` drains up to `pkt_limit` (required, as are `mode`
  and `limit` of a start; `snaplen` is ignored; a `pkt_limit` of 0 takes nothing and reports `pending` = count
  and the start_ts, as recorded in summary.json `fetch_peek`) and reports `pending` and the capture's
  `start_ts`; a status row has no `start_ts` and no `snaplen`. A stopped capture records nothing more. Service
  mode follows what af_packet measured on the guest (tx_capture_probe, summary.json: the arms differ only in
  service mode, port 0 transmitted the same in both): a Tx capture of stream traffic records nothing while the
  Tx port's service mode is off and an Rx capture records without it. That is an af_packet measurement only;
  hardware is unverified in both directions. The filter is checked before the limit, as the server does
  (bpf_verify, trex_rpc_cmds_common.cpp:1416-1418, then the manager's limit check, trex_capture.cpp:875). An
  unknown or missing `command`, or a missing required parameter, raises. Not modelled: endpoint captures (an
  `endpoint` start parameter is a test bug and raises), the global unset_capture_feature() on stop. The
  recorded P3b status row is never replayed. xstats follow the port counters (tx_good_*/tx_q0_* the port's
  own, rx_good_*/rx_q0_* the peer's, pid ^ 1); the recorded values count 60 bytes per 64-byte-counted packet,
  so bytes are obytes minus the fake's 4 per packet. The other names stay as recorded.

Ports: a port of 0 means "bind any free port"; the fake binds it itself and the ready line reports the bound
ports (rpc=<port> pub=<port>), so a caller never chooses a port and loses it to another process before the
fake binds. A non-zero port is bound exactly as given.

Usage: python fake_trex.py <fixtures_dir> <rpc_port> <pub_port> [--reject-api] [--seq-gap-at-start]
    [--server-version V] [--stack S] [--drop METHOD]... [--delay-ms D] [--jitter-ms J] [--seed S]"""
import argparse
import base64
import collections
import heapq
import ipaddress
import json
import math
import os
import random
import re
import signal
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import zmq

MAGIC = 0xABE85CEA
def unzip(b):
    """(payload, True) for a body that starts with the TRex magic (0xABE85CEA), decompressed, else (body,
    False)."""
    if len(b) >= 8 and struct.unpack(">I", b[:4])[0] == MAGIC:
        return zlib.decompress(b[8:]), True
    return b, False
def zipb(b):
    """The body in the TRex wire framing: magic, big-endian length, then the zlib-compressed payload."""
    return struct.pack(">II", MAGIC, len(b)) + zlib.compress(b)

ap = argparse.ArgumentParser()
ap.add_argument("fixtures")
ap.add_argument("rpc_port", type=int, help="0 binds any free port; the ready line reports the bound one")
ap.add_argument("pub_port", type=int, help="0 binds any free port; the ready line reports the bound one")
ap.add_argument("--reject-api", action="store_true",
                help="answer api_sync_v2 with the recorded 'too new' error")
ap.add_argument("--seq-gap-at-start", action="store_true",
                help="arm the seq gap immediately (loss during connect)")
ap.add_argument("--server-version",
                help="report this string as get_version.version (the recorded one is v3.08)")
ap.add_argument("--stack", default="legacy", choices=["legacy", "linux_based"],
                help="the ports' info.stack (the recorded server runs legacy); "
                     "linux_based lets conf_ipv6 enable IPv6")
ap.add_argument("--manual-clock", action="store_true",
                help="the counters' clock moves only on the fake_advance_clock RPC "
                     "(deterministic wire fixtures)")
ap.add_argument("--delay-ms", type=int, default=0,
                help="hold every reply and PUB message this long (a remote backend)")
ap.add_argument("--jitter-ms", type=int, default=0,
                help="uniform +/- jitter on --delay-ms, order kept per peer")
ap.add_argument("--seed", type=int, help="seed for the jitter draws (default: the bound rpc port)")
ap.add_argument("--pub-replay", help="JSONL of recorded PUB frames ({t, frame, phase}): replay the raw "
                "frames at their recorded pace, looping, instead of the synthetic trex-global")
ap.add_argument("--pub-garbage", action="store_true",
                help="every 2 s send hostile frames: a 1 GiB length claim, a zlib bomb, not json, [1,2], "
                "{data:1}")
ap.add_argument("--pub-names", type=int, default=0, help="every 2 s send N extra frames named x-<i>")
ap.add_argument("--pub-big", type=int, default=0,
                help="every 2 s send one raw (uncompressed) frame named big, BYTES long on the wire")
ap.add_argument("--drop", action="append", default=[], help="answer METHOD as unknown (negative control)")
a = ap.parse_args()

def _due(now, last_due, d, j, rng):
    """When a message produced at `now` goes out: now + D + U(-J, J) ms (floored at 0), never before the
    peer's previous message (`last_due`), so the stream keeps its order."""
    return max(now + max(0.0, d + rng.uniform(-j, j)) / 1000.0, last_due)

# --- load fixtures: (method, port_id) -> first recorded response object ----------------------
table, pub_global = {}, []
def key(req):
    """The replay-table key of a request: (method, params.port_id), with port_id None when absent."""
    p = req.get("params") or {}
    return (req["method"], p.get("port_id"))
def load_rpc(path):
    """Add the first recorded answer of every request in the jsonl file at `path` to the replay table; an
    existing key stays."""
    for line in Path(path).read_text().splitlines():
        r = json.loads(line)
        reqs = r["req"] if isinstance(r["req"], list) else [r["req"]]
        # real TRex answers a 1-element batch with a bare object
        resps = r["resp"] if isinstance(r["resp"], list) else [r["resp"]]
        by_id = {x["id"]: x for x in resps}
        for q in reqs:
            table.setdefault(key(q), by_id[q["id"]])
load_rpc(f"{a.fixtures}/rpc.jsonl")
load_rpc(f"{a.fixtures}/p2/rpc.jsonl")      # after P1: setdefault keeps P1's recorded answers
# the stats batch is answered by stateful() whatever the file order
load_rpc(f"{a.fixtures}/p3/rpc.jsonl")
for line in Path(f"{a.fixtures}/pub.jsonl").read_text().splitlines():
    m = json.loads(line)["msg"]
    if isinstance(m, dict) and m.get("name") == "trex-global":
        pub_global.append(m)
neg = json.loads(Path(f"{a.fixtures}/negatives.json").read_text())
API_H = table[("api_sync_v2", None)]["result"]["api_h"]

# --- P2: stateful ports, per-session readers, restart (mirrors tests/fixtures/p2/, recorded in Task 1) ---
NEG2 = json.loads(Path(f"{a.fixtures}/p2/negatives.json").read_text())
EMITS = json.loads(Path(f"{a.fixtures}/p2/emits.json").read_text())
SUMMARY2 = json.loads(Path(f"{a.fixtures}/p2/summary.json").read_text())
SYSINFO = table[("get_system_info", None)]["result"]
BASE_STATUS = table[("get_port_status", 0)]["result"]
PORT_STATUS = {
    i: (table.get(("get_port_status", i)) or table[("get_port_status", 0)])["result"]
    for i in range(SYSINFO["port_count"])
}
# the states a configuration RPC is allowed in (trex_port.cpp:378-380 ...)
ACTIVE_OK = ("IDLE", "STREAMS", "ASTF_LOADED")
STATE_EVENT = {"start_traffic": "TX", "stop_traffic": "STREAMS", "pause_traffic": "PAUSE",
               "resume_traffic": "TX", "update_traffic": None}
# set_port_attr request keys -> get_port_status attr keys (trex_port.py:500-507 vs the recorded status)
ATTR_KEYS = {"link_status": "link", "led_status": "led", "flow_ctrl_mode": "fc"}
# set by SIGUSR2; applied by the request loop (single-threaded, no lock)
restart_pending = False
assert SUMMARY2["per_session_seq"], "the fake implements the per-reader seq scheme only"


manual_now = 0.0


def clock():
    """The time counters integrate over: wall time, or with --manual-clock only what fake_advance_clock has
    added."""
    return manual_now if a.manual_clock else time.monotonic()


class Port:
    def __init__(self, pid):
        """A port in its start state: IDLE, unowned, no streams, its own copy of the recorded attr,
        counters at zero from clock()."""
        self.id, self.handler, self.owner, self.session = pid, "", "", None
        self.default_state, self.streams, self.service, self.service_filtered = "IDLE", {}, False, False
        # named profiles: {"streams": {...}, "state": "STREAMS"|"TX"|"PAUSE"}
        self.profiles: dict[str, dict] = {}
        # each port's own recorded attr
        self.attr = json.loads(json.dumps(PORT_STATUS.get(pid, BASE_STATUS)["attr"]))
        # profile -> (pps, mean frame bytes), set by start/update
        self.rates: dict[str, tuple[float, float]] = {}
        self.mults: dict[str, dict] = {}                  # profile -> the multiplier as given
        self.unmodelled: dict[str, str] = {}              # profile -> why its rate cannot be modelled
        self.opackets = self.obytes = 0.0
        self.pg_counts: dict[int, list[float]] = {}       # pg_id -> [packets, bytes] sent from this port
        self.counted_at = clock()
        # profile -> fraction of a packet not yet sent (capture synthesis)
        self.pkt_carry: dict[str, float] = {}
        self.pkt_rr: dict[str, int] = {}                  # profile -> next stream index (round-robin)

    def _tx_profiles_all(self):
        """Names of the profiles in TX ("_" first), whether or not a rate was derived for them."""
        return [pr for pr in ["_"] + list(self.profiles) if self.get_state(pr) == "TX"]

    def _tx_profiles(self):
        """Names of the profiles in TX that have a modelled rate; a TX profile with an unmodelled rate is
        left out."""
        return [pr for pr in ["_"] + list(self.profiles) if self.get_state(pr) == "TX" and pr in self.rates]

    @property
    def pps(self):
        """Packets per second now being sent: the started rate of every profile in TX, 0 otherwise."""
        return sum(self.rates[pr][0] for pr in self._tx_profiles())

    @property
    def bps(self):
        """Bits per second now being sent: started pps times mean frame bytes times 8, summed over the TX
        profiles."""
        return sum(self.rates[pr][0] * self.rates[pr][1] * 8 for pr in self._tx_profiles())

    def pg_rates(self):
        """pg_id -> (pps, frame bytes) now being sent: each stream's share of its profile's started pps, own
        pps over the profile's own total."""
        out: dict[int, list[float]] = {}
        for pr in self._tx_profiles():
            streams = list(self.streams_of(pr).values())
            own_total = sum(st["mode"]["rate"]["value"] for st in streams)
            for st in streams:
                pg = pg_of(st)
                if pg is not None and own_total:
                    share = self.rates[pr][0] * st["mode"]["rate"]["value"] / own_total
                    cur = out.setdefault(pg[0], [0.0, float(frame_bytes(st))])
                    cur[0] += share
        return {k: (v[0], v[1]) for k, v in out.items()}

    def settle(self):
        """Fold the time since the last call into the counters, and offer the packets sent in it to the active
        captures. Call before any change of state or rate."""
        now = clock()
        dt = now - self.counted_at
        t0, self.counted_at = self.counted_at, now
        capture_feed(self, t0, dt)
        for pg, (pps, fb) in self.pg_rates().items():
            c = self.pg_counts.setdefault(pg, [0.0, 0.0])
            c[0] += pps * dt
            c[1] += pps * fb * dt
        self.opackets += self.pps * dt
        self.obytes += self.bps / 8 * dt

    @property
    def state(self):
        """The port's aggregate state: TX over PAUSE over STREAMS over IDLE (mirrors
        STLPort.__sync_port_state_from_profile, trex_stl_port.py:109-120)."""
        states = [self.default_state] + [pr["state"] for pr in self.profiles.values()]
        return next(s for s in ("TX", "PAUSE", "STREAMS", "IDLE") if s in states)

    def get_state(self, profile):
        """The state of one profile ("_" is the default profile); KeyError for an unknown named profile."""
        return self.default_state if profile == "_" else self.profiles[profile]["state"]

    def set_state(self, profile, state):
        """Set one profile's state (KeyError for an unknown named profile); does not settle the counters or
        emit an event."""
        if profile == "_":
            self.default_state = state
        else:
            self.profiles[profile]["state"] = state

    def streams_of(self, profile):
        """The live stream dict (stream_id -> stream) of one profile, not a copy; KeyError for an unknown
        named profile."""
        return self.streams if profile == "_" else self.profiles[profile]["streams"]

    def status(self):
        """A get_port_status result: the recorded status of this port with the live state, owner, service
        flags, attr (the live dict), max_stream_id, profile_count and state_profile."""
        s = json.loads(json.dumps(PORT_STATUS.get(self.id, BASE_STATUS)))
        all_ids = [i for st in [self.streams] + [pr["streams"] for pr in self.profiles.values()] for i in st]
        s.update(state=self.state, owner=self.owner, service=self.service,
                 service_filtered=self.service_filtered, attr=self.attr,
                 max_stream_id=max(all_ids, default=0),
                 # the recorded statuses show 1: only "_"
                 profile_count=1 + len(self.profiles),
                 state_profile={"_": self.default_state,
                                **{k: pr["state"] for k, pr in self.profiles.items()}})
        return s


ports = [Port(i) for i in range(SYSINFO["port_count"])]


class HarnessError(RuntimeError):
    """A test asked the fake for something it does not model. Uncaught on purpose: the fake dies loudly."""


# --- P3: stats shape (mirrors tests/fixtures/p3/rpc.jsonl, the first recorded stats batch) ---
def _recorded_stats_batch():
    """The first recorded [global, port 0, port 1] stats batch of p3/rpc.jsonl as {"global", 0, 1} results;
    HarnessError if none."""
    for line in Path(f"{a.fixtures}/p3/rpc.jsonl").read_text().splitlines():
        r = json.loads(line)
        if isinstance(r["req"], list) and [q["method"] for q in r["req"]] == [
                "get_global_stats", "get_port_stats", "get_port_stats"]:
            by_id = {x["id"]: x["result"] for x in r["resp"]}
            return {"global": by_id[r["req"][0]["id"]], 0: by_id[r["req"][1]["id"]],
                    1: by_id[r["req"][2]["id"]]}
    raise HarnessError("tests/fixtures/p3/rpc.jsonl holds no stats batch")
STATS_SHAPE = _recorded_stats_batch()


def _recorded_p3a():
    """From tests/fixtures/p3a/rpc.jsonl: the first recorded latency block (the 'lat' of a latency pg_id) and
    the first get_utilization answer taken while port 0 transmitted (cpu ports[0] == 0)."""
    lat = util = None
    for line in Path(f"{a.fixtures}/p3a/rpc.jsonl").read_text().splitlines():
        r = json.loads(line)
        reqs = r["req"] if isinstance(r["req"], list) else [r["req"]]
        resps = r["resp"] if isinstance(r["resp"], list) else [r["resp"]]
        by_id = {x["id"]: x for x in resps}
        for q in reqs:
            res = by_id[q["id"]].get("result")
            if q["method"] == "get_pgid_stats" and lat is None:
                for k, v in (res["latency"] or {}).items():
                    if k != "g" and lat is None:
                        lat = v["lat"]
            if q["method"] == "get_utilization" and util is None and res["cpu"][0]["ports"][0] == 0:
                util = res
    if lat is None or util is None:
        raise HarnessError(
            "tests/fixtures/p3a/rpc.jsonl holds no latency block or no transmitting utilization sample"
        )
    return lat, util
REC_LATENCY, REC_UTIL = _recorded_p3a()
MAX_PGIDS_QUERY = 1024 + 128       # MAX_ALLOWED_PGID_LIST_LEN, src/stx/stl/trex_stl_fs.cpp:74


def _recorded_p3b_errors():
    """The error objects of tests/fixtures/p3b/rpc.jsonl by (method, text fragment). Only errors are read from
    that file: its service-context answers (capture, push_pkts) are never replayed."""
    out = {}
    for line in Path(f"{a.fixtures}/p3b/rpc.jsonl").read_text().splitlines():
        r = json.loads(line)
        reqs = r["req"] if isinstance(r["req"], list) else [r["req"]]
        resps = r["resp"] if isinstance(r["resp"], list) else [r["resp"]]
        by_id = {x["id"]: x for x in resps}
        for q in reqs:
            e = by_id[q["id"]].get("error")
            if e:
                out.setdefault((q["method"], e["specific_err"]), e)
    return out
P3B_ERRORS = _recorded_p3b_errors()


def p3b_error(method, fragment, rid, **rewrite):
    """A recorded error of `method` whose specific_err contains `fragment`, for request `rid`, with `rewrite`
    applied to the text."""
    for (m, text), e in P3B_ERRORS.items():
        if m == method and fragment in text:
            e = json.loads(json.dumps(e))
            for old, new in rewrite.items():
                e["specific_err"] = e["specific_err"].replace(old, new)
            return {"id": rid, "jsonrpc": "2.0", "error": e}
    raise HarnessError(f"tests/fixtures/p3b/rpc.jsonl holds no {method} error containing {fragment!r}")


def xstats_values(port):
    """get_port_xstats_values: the recorded row of this port with the counters that move following the
    fake's. Names from the recorded get_port_xstats_names. Bytes carry no FCS (the recorded server's 60
    per packet), the fake's obytes carry 4 per packet. Settles every port first (so it advances their
    counters and captures); HarnessError for an unmodelled TX rate."""
    peer = ports[port.id ^ 1] if (port.id ^ 1) < len(ports) else None
    names = (table.get(("get_port_xstats_names", port.id))
             or table[("get_port_xstats_names", 0)])["result"]["xstats_names"]
    vals = dict(zip(names,
                    (table.get(("get_port_xstats_values", port.id))
                     or table[("get_port_xstats_values", 0)])["result"]["xstats_values"],
                    strict=True))
    for pt in ports:
        for prof in pt._tx_profiles_all():
            if prof in pt.unmodelled:
                raise HarnessError(pt.unmodelled[prof])
        pt.settle()
    # counts(pt): (packets, bytes without the fake's 4 FCS bytes per packet) that port `pt` sent; (0, 0) for
    # no port
    def counts(pt):
        return (int(pt.opackets), int(pt.obytes) - 4 * int(pt.opackets)) if pt else (0, 0)
    for side, pt in (("tx", port), ("rx", peer)):
        pk, by = counts(pt)
        for q in ("good", "q0"):
            vals[f"{side}_{q}_packets"], vals[f"{side}_{q}_bytes"] = pk, by
    return [vals[n] for n in names]


def frame_bytes(stream):
    """Wire bytes of one packet of a stored stream: the binary plus the 4-byte FCS (the recorded 64-byte
    profile counts 64)."""
    return len(base64.b64decode(stream["packet"]["binary"])) + 4


def pg_of(stream):
    """(pg_id, kind) of a stored stream with flow stats on, kind "flow_stats" or "latency"; None for a stream
    without. HarnessError for any other rule_type."""
    fs = stream.get("flow_stats") or {}
    if not fs.get("enabled"):
        return None
    if fs.get("rule_type") not in ("stats", "latency"):
        raise HarnessError(
            f"HARNESS ERROR: flow_stats rule_type {fs.get('rule_type')!r} is not modelled "
            "(stats, latency only)"
        )
    return fs["stream_id"], ("latency" if fs["rule_type"] == "latency" else "flow_stats")


def active_pgids():
    """pg_id -> kind over every stream the fake holds, on every port and profile."""
    out = {}
    for pt in ports:
        for st in [pt.streams] + [pr["streams"] for pr in pt.profiles.values()]:
            for s_ in st.values():
                pg = pg_of(s_)
                if pg:
                    out[pg[0]] = pg[1]
    return out


ver_ids: dict[int, int] = {}      # pg_id -> ver_id, taken from one global pool when the pg_id is first added
ver_pool = 0


def sync_pgids():
    """Give a ver_id to every newly active pg_id and forget the ones no stream holds any more (counters
    too)."""
    global ver_pool
    active = active_pgids()
    for pg in sorted(set(ver_ids) - set(active)):
        del ver_ids[pg]
        for pt in ports:
            pt.pg_counts.pop(pg, None)
    for pg in active:
        if pg not in ver_ids:
            ver_ids[pg] = ver_pool
            ver_pool += 1


def profile_rate(port, prof, mul):
    """(pps, mean frame bytes) of a started or updated profile. Mirrors the grammar of
    trex/utils/parsing_opts.py:85-130 for the types the GUI sends; anything else is a test bug."""
    if mul.get("op", "abs") != "abs":
        raise HarnessError(f"HARNESS ERROR: multiplier op {mul.get('op')!r} is not modelled (only abs)")
    streams = list(port.streams_of(prof).values())
    own = [st["mode"]["rate"]["value"] if st["mode"]["rate"]["type"] == "pps" else None for st in streams]
    if not streams or None in own:
        raise HarnessError(
            "HARNESS ERROR: a stream whose mode.rate is not type pps (percentage, bps, bpsl1) is not modelled"
        )
    own_total = sum(own)
    if mul["type"] == "pps":
        pps = float(mul["value"])
    elif mul["type"] == "raw":
        pps = own_total * float(mul["value"])
    else:
        raise HarnessError(
            f"HARNESS ERROR: multiplier type {mul['type']!r} is not modelled (pps, kpps, mpps, x only)"
        )
    mean_bytes = (
        sum(w * frame_bytes(st) for w, st in zip(own, streams, strict=True)) / own_total if own_total else 0.0
    )
    return pps, mean_bytes

readers: dict[int, collections.deque] = {}
next_seq: dict[int, int] = {}


def recorded_error(key, rid, **rewrite):
    """The recorded P2 negative `key` as an error response to request `rid`, each `rewrite` old -> new
    applied to its texts."""
    e = json.loads(json.dumps(NEG2[key]["resp"]))
    e["id"] = rid
    for old, new in rewrite.items():
        for f in ("message", "specific_err"):
            if f in e["error"]:
                e["error"][f] = e["error"][f].replace(old, new)
    return e


def emit(method, data, port_level=True):
    """Queue the recorded events of `method` onto every session reader, each with its next per-session seq,
    and wake the publisher. Port events (type < 10) are skipped unless `port_level`, and carry no
    profile_id."""
    for type_id in EMITS.get(method, []):
        if type_id < 10 and not port_level:    # port events fire only when the aggregate state changed
            continue
        ev = {"name": "trex-event", "type": type_id, "data": dict(data)}
        # recorded port events 0-3 carry only port_id; profile_id starts at 10
        if type_id < 10:
            ev["data"].pop("profile_id", None)
        if type_id == 5:
            ev["data"].setdefault("force", False)
        for sid, q in readers.items():
            q.append(dict(ev, seq=next_seq[sid]))
            next_seq[sid] += 1
    kick.set()


def stats_of(port):
    """A get_port_stats result: the recorded shape, values from the port's state; Rx follows the peer port
    (pid ^ 1)."""
    peer = ports[port.id ^ 1] if (port.id ^ 1) < len(ports) else None
    r = dict(STATS_SHAPE[port.id if port.id in (0, 1) else 0])
    r.update(opackets=int(port.opackets), obytes=int(port.obytes), oerrors=0, ierrors=0,
             ipackets=int(peer.opackets) if peer else 0, ibytes=int(peer.obytes) if peer else 0,
             m_total_tx_pps=port.pps, m_total_tx_bps=port.bps,
             m_total_rx_pps=peer.pps if peer else 0.0, m_total_rx_bps=peer.bps if peer else 0.0)
    return r


def global_stats():
    """A get_global_stats result: the recorded shape with rates and counters summed over the ports; Rx is
    each paired port's peer Tx. Reads the counters as they stand (the caller settles the ports first)."""
    r = dict(STATS_SHAPE["global"])
    tx_pps, tx_bps = sum(pt.pps for pt in ports), sum(pt.bps for pt in ports)
    # a port with a peer receives that peer's Tx
    paired = [pt for pt in ports if (pt.id ^ 1) < len(ports)]
    rx_pps, rx_bps = sum(ports[pt.id ^ 1].pps for pt in paired), sum(ports[pt.id ^ 1].bps for pt in paired)
    r.update(
        m_tx_pps=tx_pps,
        m_tx_bps=tx_bps,
        m_rx_pps=rx_pps,
        m_rx_bps=rx_bps,
        m_rx_core_pps=rx_pps,
        m_total_tx_pkts=sum(int(pt.opackets) for pt in ports),
        m_total_tx_bytes=sum(int(pt.obytes) for pt in ports),
        m_total_rx_pkts=sum(int(ports[pt.id ^ 1].opackets) for pt in paired),
        m_total_rx_bytes=sum(int(ports[pt.id ^ 1].obytes) for pt in paired),
    )
    return r


def pgid_stats_answer(pgids):
    """The result of get_pgid_stats in the server's wire shape (recorded in tests/fixtures/p3a/rpc.jsonl).
    Raises ValueError with the server's text where the server throws (trex_stl_fs.cpp, dump_json),
    HarnessError for an unmodelled TX rate. Unless it returns early with nothing to report, it settles
    every port first."""
    active = active_pgids()
    if pgids:
        if len(pgids) > MAX_PGIDS_QUERY:
            raise ValueError(
                f"Asking information for too many packet group IDs. Max allowed is {MAX_PGIDS_QUERY}"
            )
        wanted = [pg for pg in pgids if pg in active]
    else:
        if len(active) > MAX_PGIDS_QUERY:
            raise ValueError(
                "Asking information for all packet group IDs. "
                f"There are currently {len(active)} active IDs . "
                f"Max allowed is {MAX_PGIDS_QUERY}. Need to limit query size."
            )
        wanted = sorted(active)
    # dump_json skips a missing id (`if (user_id_info == NULL) continue`); nothing left sends null
    # flow_stats/ver_id
    if not wanted:
        return {"flow_stats": None, "latency": {"g": None}, "ver_id": None}
    for pt in ports:
        for prof in pt._tx_profiles_all():
            if prof in pt.unmodelled:
                raise HarnessError(pt.unmodelled[prof])
        pt.settle()
    rates = [pt.pg_rates() for pt in ports]
    flow, lat = {}, {}
    for pg in wanted:
        f = {k: {} for k in ("rb", "rbs", "rp", "rps", "tb", "tbs", "tp", "tps")}
        for pt in ports:
            peer = pt.id ^ 1 if (pt.id ^ 1) < len(ports) else None
            tp, tb = (int(v) for v in pt.pg_counts.get(pg, [0, 0]))
            tps, fb = rates[pt.id].get(pg, (0.0, 0.0))
            rp, rb = (int(v) for v in ports[peer].pg_counts.get(pg, [0, 0])) if peer is not None else (0, 0)
            rps, rfb = rates[peer].get(pg, (0.0, 0.0)) if peer is not None else (0.0, 0.0)
            i = str(pt.id)
            # the server prints the four rates with two decimals (snprintf "%.2f", trex_stl_fs.cpp:1393-1401)
            f["tp"][i], f["tb"][i], f["tps"][i], f["tbs"][i] = tp, tb, round(tps, 2), round(tps * fb * 8, 2)
            f["rp"][i], f["rb"][i], f["rps"][i], f["rbs"][i] = rp, rb, round(rps, 2), round(rps * rfb * 8, 2)
        flow[str(pg)] = f
        if active[pg] == "latency" and any(int(pt.pg_counts.get(pg, [0])[0]) > 0 for pt in ports):
            lat[str(pg)] = {"lat": json.loads(json.dumps(REC_LATENCY))}
    lat["g"] = None
    return {"flow_stats": flow, "latency": lat, "ver_id": {str(pg): ver_ids[pg] for pg in wanted}}


# --- P3c: the capture family (mirrors tests/fixtures/p3c/, recorded from a v3.08 guest) ---
MAX_CAPTURES = 10                  # MAX_CAPTURE_SIZE, trex_capture.h:690; stopped captures count
captures: dict[int, dict] = {}
capture_next_id = 1


def _recorded_p3c_errors():
    """The recorded `capture` errors of p3c/rpc.jsonl by specific_err ("BPF" for any invalid-filter text);
    HarnessError if the invalid-filter, limit or unknown-ID error was not recorded."""
    out = {}
    for line in Path(f"{a.fixtures}/p3c/rpc.jsonl").read_text().splitlines():
        r = json.loads(line)
        reqs = r["req"] if isinstance(r["req"], list) else [r["req"]]
        resps = r["resp"] if isinstance(r["resp"], list) else [r["resp"]]
        by_id = {x["id"]: x for x in resps}
        for q in reqs:
            e = by_id[q["id"]].get("error")
            if q["method"] == "capture" and e:
                out.setdefault(e["specific_err"] if "BPF" not in e["specific_err"] else "BPF", e)
    for need in ("BPF", "capture limit has reached", "capture ID not found"):
        if need not in out:
            raise HarnessError(f"tests/fixtures/p3c/rpc.jsonl holds no capture error {need!r}")
    return out
P3C_ERRORS = _recorded_p3c_errors()
BAD_BPF = "not a filter(("          # the one filter text the recorded server refused


def capture_feed(port, t0, dt):
    """Called by Port.settle before the counters move: the packets `port` sent in (t0, t0 + dt] at its current
    rates, offered to every ACTIVE capture that watches them. The sub-packet carry advances whether or not
    anyone watches."""
    if dt <= 0:
        return
    peer = port.id ^ 1 if (port.id ^ 1) < len(ports) else None
    watch = [
        c
        for c in captures.values()
        if c["state"] == "ACTIVE"
        and (
            (c["filter"]["tx"] >> port.id) & 1
            and port.service
            or (peer is not None and (c["filter"]["rx"] >> peer) & 1)
        )
    ]
    for pr in port._tx_profiles():
        pps = port.rates[pr][0]
        streams = list(port.streams_of(pr).values())
        if pps <= 0 or not streams:
            continue
        carry = port.pkt_carry.get(pr, 0.0)
        total = carry + pps * dt
        n = math.floor(total + 1e-9)
        port.pkt_carry[pr] = max(0.0, total - n)
        if not watch:
            continue
        for k in range(1, n + 1):
            i = port.pkt_rr.get(pr, 0)
            port.pkt_rr[pr] = i + 1
            binary = streams[i % len(streams)]["packet"]["binary"]
            ts = t0 + (k - carry) / pps
            for c in watch:
                if ts < c["start_ts"]:
                    continue
                if (c["filter"]["tx"] >> port.id) & 1 and port.service:
                    capture_match(c, binary, "TX", port.id, ts)
                if peer is not None and (c["filter"]["rx"] >> peer) & 1:
                    capture_match(c, binary, "RX", peer, ts)


def capture_match(c, binary, origin, pid, ts):
    """Record one matched packet in capture `c`: count it, give it the next index, and keep it unless the
    capture is full (fixed drops the new packet, cyclic drops the oldest). The index is taken even for a
    dropped packet."""
    c["matched"] += 1
    # pre-incremented for every match (trex_capture.cpp:715-720)
    c["next_index"] += 1
    pkt = {"binary": binary, "index": c["next_index"], "origin": origin, "port": pid, "ts": ts,
           "wirelen": len(base64.b64decode(binary))}
    if len(c["pkts"]) >= c["limit"]:
        if c["mode"] == "fixed":
            return                                        # fixed drops the newest
        c["pkts"].popleft()                               # cyclic drops the oldest
    c["pkts"].append(pkt)


def capture_row(c):
    """A `capture status` row for capture `c`: its settings and counts, with the bytes of the packets it
    holds."""
    return {
        "id": c["id"],
        "mode": c["mode"],
        "limit": c["limit"],
        "state": c["state"],
        "matched": c["matched"],
        "fetched": c["fetched"],
        "count": len(c["pkts"]),
        "bytes": sum(len(base64.b64decode(p["binary"])) for p in c["pkts"]),
        "filter": dict(c["filter"]),
    }


def capture_rpc(p, rid):
    """The `capture` method. A capture needs no ownership (trex_rpc_cmds_common.cpp:142). Once the command
    and parameters check out it settles every port, then status lists, start adds, fetch drains, stop
    marks STOPPED and remove deletes a capture. The recorded refusals (invalid filter, limit reached,
    unknown ID) are error responses. HarnessError for a command or parameter the server would refuse with
    an unrecorded text, an endpoint start (not modelled), and an unmodelled TX rate met while settling."""
    global capture_next_id
    cmd = p.get("command")
    # ok(result): the success response to this request
    def ok(result):
        return {"id": rid, "jsonrpc": "2.0", "result": result}
    # err(key): the recorded error P3C_ERRORS[key] as the response to this request
    def err(key):
        return {"error": json.loads(json.dumps(P3C_ERRORS[key])), "id": rid, "jsonrpc": "2.0"}
    if cmd not in ("status", "start", "fetch", "stop", "remove"):
        # the server refuses at parse_choice(params, "command", ...) (trex_rpc_cmds_common.cpp:1373) with a
        # text that is not recorded
        raise HarnessError(
            f"HARNESS ERROR: capture command {cmd!r} is not one of status, start, fetch, stop, remove"
        )
    need = {"start": ("mode", "limit"), "fetch": ("pkt_limit",)}.get(cmd, ())
    if missing := [k for k in need if k not in p]:      # required, no default (trex_rpc_cmds_common.cpp:1546)
        raise HarnessError(
            f"HARNESS ERROR: capture {cmd} without {', '.join(missing)}: the server requires it"
        )
    # fold the time so far into the captures before anything reads or changes them
    for pt in ports:
        for prof in pt._tx_profiles_all():
            if prof in pt.unmodelled:
                raise HarnessError(pt.unmodelled[prof])
        pt.settle()
    if cmd == "status":
        return ok([capture_row(c) for c in captures.values()])
    if cmd == "start":
        if "endpoint" in p:
            raise HarnessError("HARNESS ERROR: endpoint captures are not modelled")
        if p.get("filter") == BAD_BPF:
            return err("BPF")
        if len(captures) >= MAX_CAPTURES:
            return err("capture limit has reached")
        def mask(ids):
            """The bitmask of the given port ids."""
            return sum(1 << i for i in set(ids or []))

        cid, capture_next_id = capture_next_id, capture_next_id + 1
        captures[cid] = {
            "id": cid,
            "mode": p["mode"],
            "limit": p["limit"],
            "state": "ACTIVE",
            "pkts": collections.deque(),
            "matched": 0,
            "fetched": 0,
            "next_index": 0,
            "start_ts": clock(),
            "filter": {"tx": mask(p.get("tx")), "rx": mask(p.get("rx")), "bpf": p.get("filter", "")},
        }
        return ok({"capture_id": cid, "start_ts": captures[cid]["start_ts"]})
    c = captures.get(p.get("capture_id"))
    if c is None:
        return err("capture ID not found")
    if cmd == "fetch":
        n = p["pkt_limit"]
        out = []
        while c["pkts"] and len(out) < n:
            out.append(c["pkts"].popleft())
        c["fetched"] += len(out)
        return ok({"pkts": out, "pending": len(c["pkts"]), "start_ts": c["start_ts"]})
    if cmd == "stop":
        c["state"] = "STOPPED"
        return ok({"endpoint": "", "pkt_count": len(c["pkts"])})
    del captures[c["id"]]                 # remove: no state check (trex_capture.cpp:960-985)
    return ok({})


# RPC -> server verify_state name
CONFIG_CMDS = {"set_l2": "set_l2_mode", "set_l3": "set_l3_mode", "set_vlan": "set_vlan_cfg",
               "conf_ipv6": "conf_ipv6"}


def stateful(m, p, rid):
    """The response for a method the fake models with state, or None to fall back to the replay table (an
    unmodelled method then gets the recorded unknown-method error). Mutates the ports, captures, readers and
    pg_id table and emits the recorded events. A stats, xstats, pgid, capture or fake_advance_clock call
    that gets past its own checks settles every port (feeding the active captures), and add_stream,
    traffic state changes and service settle one before changing it. HarnessError for parameters the fake does
    not model (an unmodelled TX rate, a tpg rule), so the fake dies loudly. Per method, what it models:
    - get_global_stats, get_port_stats: counters and rates from the port state; the server's rates ramp.
    - fake_advance_clock (--manual-clock only): a test hook, not a TRex method.
    - get_async_events: the session's reader queue, seq from 0 per reader; None when empty.
    - get_active_pgids, get_pgid_stats: the pg_id table; any number of streams is accepted, a tpg rule raises.
    - get_utilization: one recorded answer in every state; it does not follow the fake's state.
    - capture: capture_rpc (every filter but BAD_BPF matches; packets are synthesised; snaplen is ignored).
    - get_port_status, acquire, release: ownership by session_id and handler, with the recorded refusals.
    - remove_rx_filters: answers {} in every state and removes nothing; the server requires STREAMS.
    - get_all_streams, add_stream: the dynamic-profile shape; add_stream checks the port state nowhere,
      where the server requires IDLE or STREAMS.
    - remove_all_streams: the recorded refusal while the profile is TX or PAUSE; unknown profile: no-op.
    - validate: the recorded answer of an inline 1 pps stream, whatever stream was sent.
    - start, stop, pause, resume, update_traffic: state moves by method. Start with no streams gets the
      recorded start_no_streams refusal. Not modelled: the server's state refusals (start while TX, pause
      unless TX, resume unless PAUSE, update while not active). Start and update echo the requested
      multiplier (ts is wall-clock); an unmodelled multiplier is accepted and raises only at the next
      stats read. An unknown profile on pause, resume or stop answers {} where the server throws. Port
      events use the method's type id, not the server's transition map.
    - get_port_xstats_values: the recorded row, its packet and byte counters following the port counters.
    - set_l2, set_l3, set_vlan, conf_ipv6: synchronous answers (the linux_based stack answers with a WIP
      ticket on the server; --stack linux_based only lets conf_ipv6 enable IPv6). Modelled refusals:
      verify_state outside IDLE, STREAMS and ASTF_LOADED, more than 2 VLANs, conf_ipv6 enabled on legacy.
      Only the set_l2_mode refusal text was recorded; the other three imitate it. Other validation errors
      are not modelled.
    - service: filtered mode is accepted, which the real server refuses without --software.
    - set_port_attr: link, LED and flow control are accepted and the link-change event is never emitted."""
    global ver_pool, manual_now
    pid = p.get("port_id")
    port = ports[pid] if isinstance(pid, int) and 0 <= pid < len(ports) else None
    if m in ("get_global_stats", "get_port_stats") and (m == "get_global_stats" or port is not None):
        for pt in ports:
            for prof in pt._tx_profiles_all():
                if prof in pt.unmodelled:
                    raise HarnessError(pt.unmodelled[prof])
            pt.settle()
        return {
            "id": rid,
            "jsonrpc": "2.0",
            "result": global_stats() if m == "get_global_stats" else stats_of(port),
        }
    # test hook, not a TRex method: settle at the old rates, then step
    if m == "fake_advance_clock" and a.manual_clock:
        for pt in ports:
            pt.settle()
        manual_now += float(p["seconds"])
        for pt in ports:
            pt.settle()
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "get_async_events":
        sid = p.get("session_id")
        if sid not in readers:
            readers[sid] = collections.deque()
            # measured: every reader's first seq is 0 (summary.json first_seq)
            next_seq[sid] = 0
        data = []
        while readers[sid]:
            data.append(readers[sid].popleft())
        return {"id": rid, "jsonrpc": "2.0", "result": {"data": data}} if data else None
    if m == "get_active_pgids":          # a getter: no ownership needed
        act = active_pgids()
        return {
            "id": rid,
            "jsonrpc": "2.0",
            "result": {
                "ids": {
                    k: sorted(pg for pg, kind in act.items() if kind == k) for k in ("flow_stats", "latency")
                }
            },
        }
    if m == "get_pgid_stats":
        try:
            result = pgid_stats_answer(p["pgids"])
        except ValueError as e:
            # generate_execute_err: code -32000, "Failed To Execute Method" (trex_stl_rpc_cmds.cpp)
            return {"id": rid, "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Failed To Execute Method", "specific_err": str(e)}}
        return {"id": rid, "jsonrpc": "2.0", "result": result}
    if m == "get_utilization":
        return {"id": rid, "jsonrpc": "2.0", "result": REC_UTIL}
    if m == "capture":
        return capture_rpc(p, rid)
    if port is None:
        return None
    if m == "get_port_status":
        return {"id": rid, "jsonrpc": "2.0", "result": port.status()}
    if m == "acquire":
        if port.owner and port.session != p["session_id"] and not p.get("force"):
            e = recorded_error("acquire_owned_no_force", rid)
            e["error"]["specific_err"] = re.sub(
                r"taken by '[^']*'", f"taken by '{port.owner}'", e["error"]["specific_err"]
            )
            return e
        port.handler = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8))
        port.owner, port.session = p["user"], p["session_id"]
        emit("acquire_force" if p.get("force") else "acquire",
             {"port_id": pid, "who": p["user"], "session_id": p["session_id"], "force": bool(p.get("force"))})
        return {"id": rid, "jsonrpc": "2.0", "result": port.handler}
    if "handler" in p and p["handler"] != port.handler:
        return recorded_error("release_wrong_handler" if m == "release" else "command_wrong_handler", rid)
    if m == "release":
        who, sid = port.owner, port.session
        port.handler, port.owner, port.session = "", "", None
        emit("release", {"port_id": pid, "who": who, "session_id": sid})
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "remove_rx_filters":         # A3.5: sent by the client on stop; a recorded server answers {}
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "get_all_streams":       # dynamic-profile shape (v3.08); an empty port lists no profile
        profiles = {
            k: {str(i): v for i, v in st.items()}
            for k, st in [("_", port.streams)] + [(n, pr["streams"]) for n, pr in port.profiles.items()]
            if st
        }
        return {"id": rid, "jsonrpc": "2.0", "result": {"profiles": profiles}}
    if m == "add_stream":
        # fold the time so far into the counters before the stream set (and so the shares) change
        port.settle()
        prof = p.get("profile_id", "_")
        if prof != "_":
            port.profiles.setdefault(prof, {"streams": {}, "state": "IDLE"})
        prior = port.streams_of(prof).get(p["stream_id"])
        port.streams_of(prof)[p["stream_id"]] = p["stream"]
        new_pg = pg_of(p["stream"])
        if prior is not None and pg_of(prior) != new_pg:
            sync_pgids()
        elif new_pg and new_pg[0] not in ver_ids:
            # O(1) for the common add; a full scan per add would be quadratic
            ver_ids[new_pg[0]] = ver_pool
            ver_pool += 1
        if port.get_state(prof) == "IDLE":
            port.set_state(prof, "STREAMS")
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "remove_all_streams":
        prof = p.get("profile_id", "_")
        # an unknown profile is a no-op (trex_stl_rpc_cmds.cpp:1085)
        if prof != "_" and prof not in port.profiles:
            return {"id": rid, "jsonrpc": "2.0", "result": {}}
        # the target profile's state, not the port's (trex_stl_port.cpp:900-903)
        if port.get_state(prof) in ("TX", "PAUSE"):
            # text built as "... current state: '<state> on profile_id(<id>)." (trex_stl_port.cpp:719)
            return recorded_error(
                "remove_streams_while_tx",
                rid,
                **{"'TX ": f"'{port.get_state(prof)} ", "profile_id(_)": f"profile_id({prof})"},
            )
        before = port.state
        port.streams_of(prof).clear()
        sync_pgids()
        port.set_state(prof, "IDLE")
        emit("remove_all_streams", {"port_id": pid, "profile_id": prof}, port.state != before)
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "validate":
        return {"id": rid, "jsonrpc": "2.0", "result": SUMMARY2["validate_result"]}
    if m in STATE_EVENT:
        prof = p.get("profile_id", "_")
        if m == "start_traffic" and not (
            port.streams_of(prof) if prof == "_" or prof in port.profiles else None
        ):
            return recorded_error("start_no_streams", rid)
        if m in ("pause_traffic", "resume_traffic") and prof != "_" and prof not in port.profiles:
            # an unknown profile: declared divergence (module docstring)
            return {"id": rid, "jsonrpc": "2.0", "result": {}}
        if m == "stop_traffic" and prof != "_" and prof not in port.profiles:
            # an unknown profile: declared divergence (module docstring)
            return {"id": rid, "jsonrpc": "2.0", "result": {}}
        if m == "stop_traffic" and port.get_state(prof) not in ("TX", "PAUSE"):
            # a known but inactive profile: no state change, no event, result {} (trex_stl_port.cpp:377-381,
            # trex_stl_rpc_cmds.cpp:1213-1228)
            return {"id": rid, "jsonrpc": "2.0", "result": {}}
        known = prof == "_" or prof in port.profiles
        port.settle()                          # count the time spent in the old state at the old rate
        if m in ("start_traffic", "update_traffic") and known and port.streams_of(prof):
            port.mults[prof] = p["mul"]        # stored as given; rates are derived when stats are asked for
            try:
                port.rates[prof] = profile_rate(port, prof, p["mul"])
                port.unmodelled.pop(prof, None)
            except HarnessError as e:          # accepted as before; the stats request fails loudly
                port.rates.pop(prof, None)
                port.unmodelled[prof] = str(e)
        before = port.state
        if STATE_EVENT[m]:
            port.set_state(prof, STATE_EVENT[m])
        # port events 0-3: aggregate change only (trex_stl_port.cpp:1473-1494)
        emit(m, {"port_id": pid, "profile_id": prof}, port.state != before)
        result = {"multiplier": p["mul"]["value"], "ts": time.time()} if m == "start_traffic" else (
            {"multiplier": p["mul"]["value"]} if m == "update_traffic" else {})
        return {"id": rid, "jsonrpc": "2.0", "result": result}
    if m == "get_port_xstats_values":
        return {"id": rid, "jsonrpc": "2.0", "result": {"xstats_values": xstats_values(port)}}
    if m in CONFIG_CMDS:
        cmd = CONFIG_CMDS[m]
        # verify_state(IDLE | STREAMS | ASTF_LOADED, cmd) (trex_port.cpp:378, 394, 410, 482)
        if port.state not in ACTIVE_OK:
            return p3b_error(
                "set_l2",
                "cannot be executed on current state",
                rid,
                **{"'set_l2_mode'": f"'{cmd}'", "'TX'": f"'{port.state}'"},
            )
        lc = port.attr["layer_cfg"]
        # ether.dst valid, IPv4 forgotten (recorded); IPv6 too (recorded, p3b-v6)
        if m == "set_l2":
            lc["ether"].update(dst=p["dst_mac"], state="configured")
            lc["ipv4"] = {"state": "none"}
            lc["ipv6"] = {"enabled": False}
        elif m == "set_l3":
            # a given resolved_mac makes the destination valid; without it the old dst stays, invalid
            # (recorded)
            if p.get("resolved_mac"):
                lc["ether"].update(dst=p["resolved_mac"], state="configured")
            else:
                lc["ether"]["state"] = "unconfigured"
            lc["ipv4"] = {
                "src": p["src_addr"],
                "dst": p["dst_addr"],
                "state": "resolved" if p.get("resolved_mac") else "unresolved",
            }
        elif m == "set_vlan":
            if len(p["vlan"]) > 2:
                return p3b_error(
                    "set_vlan", "Maximal number of stacked VLANs", rid, **{"got: 3": f"got: {len(p['vlan'])}"}
                )
            port.attr["vlan"] = {"tags": list(p["vlan"])}
        else:                                 # conf_ipv6
            if p["enabled"] and a.stack == "legacy":
                return p3b_error("conf_ipv6", "IPv6 is not supported with current stack", rid)
            lc["ipv6"] = (
                {
                    "enabled": True,
                    "src": str(ipaddress.IPv6Address(p["src_ipv6"])) if p.get("src_ipv6") else "",
                }
                if p["enabled"]
                else {"enabled": False}
            )
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "service":
        port.settle()                    # a capture's Tx rule depends on the service mode over time
        port.service, port.service_filtered = bool(p["enabled"]), bool(p.get("filtered"))
        emit("service", {"port_id": pid, "attr": port.attr})
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    if m == "set_port_attr":
        for k, v in p["attr"].items():
            port.attr[ATTR_KEYS.get(k, k)] = v
        emit("set_port_attr", {"port_id": pid, "attr": json.loads(json.dumps(port.attr))})
        return {"id": rid, "jsonrpc": "2.0", "result": {}}
    return None


def do_restart():
    """The SIGUSR2 restart, run by the request loop: a new api_h, fresh ports, and ver_ids, captures,
    readers and the seq gap (disarmed) cleared."""
    global API_H, restart_pending, ver_pool, gap_armed, capture_next_id
    restart_pending = False
    API_H = "".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(8))
    ports[:] = [Port(i) for i in range(len(ports))]
    ver_ids.clear()
    ver_pool = 0
    captures.clear()
    capture_next_id = 1
    readers.clear()
    next_seq.clear()
    gap.clear()
    gap_seen.clear()
    gap_armed = False


# SIGUSR2 handler: only flags the restart; the request loop applies it before the next message.
def on_restart(*_):
    global restart_pending
    restart_pending = True


signal.signal(signal.SIGUSR2, on_restart)

served = collections.Counter()
def err_like(name, req_id, method=None, client_ver=None, server_ver=None):
    """The recorded negative `name` as a response to `req_id`; the method name and the client and server
    versions in its text are rewritten when given."""
    e = json.loads(json.dumps(neg[name]["resp"]))
    e["id"] = req_id
    err = e["error"]
    # recorded text names the recorded method/versions: rewrite it for this request
    if method:
        err["message"] = err["message"].replace("no_such_method", method)
    if client_ver and "specific_err" in err:
        err["specific_err"] = re.sub(r"client: '[^']*'", f"client: '{client_ver}'", err["specific_err"])
    if server_ver and "specific_err" in err:
        err["specific_err"] = re.sub(r"server: '[^']*'", f"server: '{server_ver}'", err["specific_err"])
    return e

def answer(req):
    """The response to one request: the api_sync_v2 or api_h check, then --drop and push_pkts as unknown
    methods, the seq-gap path, stateful(), and the replay table; an unrecorded method gets the recorded
    unknown-method error (printed as "[fake] unrecorded method"). Replayed answers are rewritten: get_version
    under --server-version, the get_system_info stack, the api_sync_v2 api_h (the current one after restart).
    Counts the method in `served` once it passes the api check, wakes the PUB thread on publish_now, and
    registers a get_async_events session for the seq gap."""
    m, p, rid = req.get("method"), req.get("params") or {}, req.get("id")
    if m == "api_sync_v2":
        cv = f"{p.get('major')}.{p.get('minor')}"
        if p.get("major", 5) < 5:
            return err_like("api_sync_v2_major4", rid, client_ver=cv)
        if a.reject_api:                       # pretend to be a 5.0 server
            return err_like("api_sync_v2_minor2_too_new", rid, client_ver=cv, server_ver="5.0")
        if p.get("name") != "STL" or p.get("major") != 5 or p.get("minor", 0) > 1:
            return err_like("api_sync_v2_minor2_too_new", rid, client_ver=cv)
    elif "api_h" not in p:
        return err_like("get_version_without_api_h", rid)
    elif p["api_h"] != API_H:
        return recorded_error("api_h_mismatch", rid)
    served[m] += 1
    if m in a.drop:
        return err_like("unknown_method", rid, method=m)
    if m == "push_pkts":
        return err_like("unknown_method", rid, method=m)
    if m == "publish_now":
        kick.set()               # real TRex publishes immediately; client barrier() depends on it
    if m == "get_async_events":
        sid = p.get("session_id")
        gap_seen.add(sid)                              # every subscriber since start or restart
        if gap_armed and sid not in gap:
            gap[sid] = collections.deque(GAP_SEQS)     # armed after it subscribed, or it subscribes late
    # the seq-gap path stays first: P1's gap tests depend on it
    if m == "get_async_events" and gap.get(p.get("session_id")):
        data = [{"name": "fake-noop", "type": 0, "data": {}, "seq": gap[p.get("session_id")].popleft()}]
        kick.set()                              # wake the subscriber again so it fetches the next one
        return {"id": rid, "jsonrpc": "2.0", "result": {"data": data}}
    r = stateful(m, p, rid)
    if r is not None:
        return r
    if m == "get_async_events":
        return {"id": rid, "jsonrpc": "2.0", "result": {"data": []}}
    r = table.get((m, p.get("port_id"))) or table.get((m, None))
    if m == "get_version" and a.server_version and r is not None:
        r = {**r, "result": {**r["result"], "version": a.server_version}}
    if m == "get_system_info" and r is not None:
        r = {
            **r,
            "result": {**r["result"], "ports": [{**pt, "stack": a.stack} for pt in r["result"]["ports"]]},
        }
    # a server hands every new sync its current api_h; after SIGUSR2 that is the new one
    if m == "api_sync_v2" and r is not None:
        r = {**r, "result": {**r["result"], "api_h": API_H}}
    if r is None:
        print(f"[fake] unrecorded method {m} port={p.get('port_id')}", flush=True)
        return err_like("unknown_method", rid, method=m)
    r = dict(r)
    r["id"] = rid
    return r

def on_term(*_):
    """SIGTERM and SIGINT handler: print the `[fake] served` method counts and exit 0."""
    print("[fake] served " + json.dumps(dict(served)), flush=True)
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
signal.signal(signal.SIGINT, on_term)

# Die with the process that started us (pytest SIGKILLed -> fixture teardown never runs, and we would be
# orphaned holding two ports). On Linux: prctl(PR_SET_PDEATHSIG, SIGKILL). The kernel delivers it even
# while we are SIGSTOPped, which the stalled-server test does; a ppid-polling thread cannot run then and
# was measured leaving a stopped orphan for 20+ s. Caveat: the "parent" is the *thread* that forked us,
# so a fake started from a short-lived worker thread would die with that thread (all callers today start
# it from the main thread). Elsewhere: a ppid-polling daemon thread, which covers the unstopped case.
PARENT = os.getppid()
if sys.platform == "linux":
    import ctypes
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:   # 1 = PR_SET_PDEATHSIG
        sys.exit(f"[fake] prctl(PR_SET_PDEATHSIG) failed: errno {ctypes.get_errno()}")
    if os.getppid() != PARENT:                     # parent died before prctl took effect
        os._exit(0)
else:
    def watchdog():
        """Non-Linux stand-in for PR_SET_PDEATHSIG: polls the parent pid and exits the process within 0.5 s
        of its death."""
        while os.getppid() == PARENT:
            time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=watchdog, daemon=True).start()

ctx = zmq.Context()
rep = ctx.socket(zmq.ROUTER)
pub = ctx.socket(zmq.PUB)


def bind_port(sock, port):
    """Bind `sock` on loopback port `port` (0: any free port) and return the port it is bound to."""
    sock.bind(f"tcp://127.0.0.1:{port or '*'}")
    return int(sock.getsockopt_string(zmq.LAST_ENDPOINT).rsplit(":", 1)[1])


a.rpc_port = bind_port(rep, a.rpc_port)
a.pub_port = bind_port(pub, a.pub_port)
if a.seed is None:
    a.seed = a.rpc_port

def replay_schedule(path):
    """[(offset_s, raw_frame)] from a --pub-replay JSONL, plus the loop length in seconds. Each phase's `t`
    restarts at 0 (also detected as t going backwards), so a phase starts 0.5 s after the previous
    phase's last frame."""
    sched, base, prev_phase, prev_t, last = [], 0.0, None, 0.0, 0.0
    for line in Path(path).read_text().splitlines():
        rec = json.loads(line) if line.strip() else {}
        if not rec:
            continue
        if prev_phase is not None and (rec["phase"] != prev_phase or rec["t"] < prev_t):
            base = last + 0.5     # a phase boundary (t went back to 0): never a negative or huge delta
        prev_phase, prev_t = rec["phase"], rec["t"]
        last = base + rec["t"]
        sched.append((last, base64.b64decode(rec["frame"])))
    return sched, last + 0.5
replay, replay_len = replay_schedule(a.pub_replay) if a.pub_replay else ([], 0.0)
# claims 100 B, inflates to 16 MiB
BOMB_CLAIM = struct.pack(">II", MAGIC, 100) + zlib.compress(b"\0" * (16 << 20))
HUGE_CLAIM = struct.pack(">II", MAGIC, 1 << 30) + zlib.compress(b"x")


def synthetic_frames():
    """The frames of the --pub-garbage, --pub-names and --pub-big flags, one round (sent every 2 s)."""
    out = []
    if a.pub_garbage:
        out += [HUGE_CLAIM, BOMB_CLAIM, b"not json", b"[1,2]", b'{"data":1}']
    out += [zipb(json.dumps({"name": f"x-{i}", "data": {"i": i}}).encode()) for i in range(a.pub_names)]
    if a.pub_big:
        head, tail = b'{"name":"big","data":"', b'"}'
        out.append(head + b"A" * (a.pub_big - len(head) - len(tail)) + tail)
    return out


kick = threading.Event()        # set by the request loop on publish_now; PUB socket stays on one thread
def publisher():
    """The PUB thread: a recorded trex-global message every 0.5 s (or, with --pub-replay, the recorded frames
    at their pace, looping) and a b"{}" trigger after each global and whenever kicked, plus the synthetic
    --pub-* frames every 2 s; each frame held per --delay-ms and --jitter-ms in one FIFO. The only thread that
    touches the PUB socket."""
    i, next_global = 0, 0.0
    q, last_due = collections.deque(), 0.0       # (due, frame), FIFO: the one PUB peer
    rng = random.Random(a.seed + 1)              # its own stream: the request loop draws from another thread
    # put(frame): queue `frame` with its due time, never earlier than the previous frame (one FIFO, the one
    # PUB peer)
    def put(frame):
        nonlocal last_due
        last_due = _due(time.monotonic(), last_due, a.delay_ms, a.jitter_ms, rng)
        q.append((last_due, frame))
    started, ri, next_synth = time.monotonic(), 0, 0.0
    while True:
        kicked = kick.wait(min(0.05, max(0.0, q[0][0] - time.monotonic())) if q else 0.05)
        kick.clear()
        now = time.monotonic()
        if now >= next_synth:       # its own 2 s timer, independent of the replay loop
            next_synth = now + 2.0
            for fr in synthetic_frames():
                put(fr)
        if replay:
            if now - started - replay[ri][0] > 1.0:     # fell behind (SIGSTOP, a slow host): no burst of
                started = now - replay[ri][0]           # every overdue frame, carry on from this one
            while now - started >= replay[ri][0]:
                put(replay[ri][1])
                ri += 1
                if ri == len(replay):
                    ri, started = 0, started + replay_len
                    break
        elif now >= next_global:
            put(zipb(json.dumps(pub_global[i % len(pub_global)]).encode()))
            i += 1
            next_global = time.monotonic() + 0.5
            kicked = True
        if kicked:
            put(b"{}")           # trigger: wakes subscribers to poll get_async_events now
        while q and q[0][0] <= time.monotonic():
            pub.send(q.popleft()[1])
GAP_SEQS = (5, 7)
gap = {}                                   # session_id -> deque of pending gap seqs, one per subscriber
gap_seen = set()                           # every get_async_events session_id since start or restart
gap_armed = False                          # armed (SIGUSR1 or --seq-gap-at-start) until a restart
def arm(*_):
    """SIGUSR1 handler: arm the seq gap, give every get_async_events session seen so far its seqs 5 then 7,
    wake the publisher."""
    global gap_armed
    gap_armed = True
    for sid in gap_seen:
        gap.setdefault(sid, collections.deque(GAP_SEQS))
    kick.set()
signal.signal(signal.SIGUSR1, arm)
if a.seq_gap_at_start:
    gap_armed = True
threading.Thread(target=publisher, daemon=True).start()
print(
    f"[fake] ready rpc={a.rpc_port} pub={a.pub_port} methods={len(table)} globals={len(pub_global)} "
    f"reject_api={a.reject_api} drop={a.drop} delay_ms={a.delay_ms} jitter_ms={a.jitter_ms} seed={a.seed}",
    flush=True,
)
outq, out_seq = [], 0                     # heap of (due, seq, frames); seq keeps equal dues in arrival order
peer_due, rep_rng = {}, random.Random(a.seed)
while True:
    now = time.monotonic()
    # timeout so SIGTERM is handled promptly (a blocking recv defers it); shorter when a reply falls due
    # sooner
    wait_ms = min(200, math.ceil(max(0.0, outq[0][0] - now) * 1000)) if outq else 200
    if rep.poll(wait_ms):
        frames = rep.recv_multipart()           # [identity, b"", body]
        raw, zipped = unzip(frames[-1])
        req = json.loads(raw)
        if restart_pending:        # once per incoming message, before a single request or a whole batch
            do_restart()
        if isinstance(req, list):
            resp = [answer(q) for q in req]
            if len(resp) == 1:
                resp = resp[0]          # mimic TRex: a 1-element batch gets a bare object, not an array
        else:
            resp = answer(req)
        out = json.dumps(resp).encode()
        who = frames[0]
        peer_due[who] = due = _due(time.monotonic(), peer_due.get(who, 0.0), a.delay_ms, a.jitter_ms, rep_rng)
        heapq.heappush(outq, (due, out_seq, [*frames[:-1], zipb(out) if zipped else out]))
        out_seq += 1
    while outq and outq[0][0] <= time.monotonic():
        rep.send_multipart(heapq.heappop(outq)[2])
