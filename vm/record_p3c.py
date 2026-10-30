"""Record the P3c capture RPC family, packets and Scapy trees from the real v3.08 guest
(the guest's RPC/PUB ports, 4501/4500 on jammy).
Usage: uv run --project backend python -I -B vm/record_p3c.py <out_dir>
Client "gui-a" acquires ports 0 and 1 and owns every capture below; client "gui-b" never acquires and is the
non-acquiring oracle (it lists captures, and runs the second 5 s monitor). Port 0 transmits
tests/fixtures/profiles/udp_1pkt_simple.py at 100pps for the capture steps and udp_for_benchmarks.py
--packet_len 1500 at 100pps and 1000pps for the size/rate steps. Every raw capture call goes through
gui-a._transmit("capture", ...); every call is timed with time.monotonic (summary.json "timings").
Whatever happens, `finally` removes every capture and returns both ports to service off, L3 resolved
(p0 1.1.1.1 -> 2.2.2.2, p1 the reverse), no VLAN, IPv6 off, as vm/record_p3b.py does.
Fixture files (rpc.jsonl, summary.json, trees.json) are written only after every call has succeeded; a
UTC-stamped copy of each goes to /var/tmp (canon:OUT-1). Structure after vm/record_p3b.py."""
import base64
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guestenv

SYNC_PORT, ASYNC_PORT = guestenv.ports()     # refuses before connecting if the guest is not selected
from trex_web_gui.trexapi.loader import load

load()
from trex.common.trex_req_resp_client import JsonRpcClient
from trex.scapy_server.scapy_service import Scapy_service
from trex.stl.api import STLClient, STLProfile
from trex.utils.zipmsg import ZippedMsg

HERE = Path(__file__).resolve().parent
PROFILES = HERE.parent / "tests" / "fixtures" / "profiles"
UDP1, BENCH = PROFILES / "udp_1pkt_simple.py", PROFILES / "udp_for_benchmarks.py"
out = Path(sys.argv[1])
Z, t0, lock = ZippedMsg(), time.time(), threading.Lock()
log: list[dict] = []
last_wire: dict = {}


def dec(b):
    """The JSON in a (possibly zipped) RPC message as a Python value; None when `b` is not bytes."""
    if not isinstance(b, (bytes, bytearray)):
        return None
    return json.loads((Z.decompress(b) if Z.is_compressed(b) else b).decode())


orig = JsonRpcClient.send_raw_msg


def rec(self, msg, retry=0):
    """Stands in for JsonRpcClient.send_raw_msg: logs the decoded pair and reply size; re-raises."""
    try:
        resp = orig(self, msg, retry)
    except Exception as e:
        print(f"[rec] send_raw_msg raised {type(e).__name__}: {e}; request={dec(msg)}", flush=True)
        raise
    row = {"t": round(time.time() - t0, 3), "client": self.ctx.username,
           "thread": threading.current_thread().name, "req": dec(msg), "resp": dec(resp)}
    with lock:
        log.append(row)
        if isinstance(resp, (bytes, bytearray)):
            last_wire.update(client=self.ctx.username, wire_bytes=len(resp),
                             json_bytes=len(json.dumps(row["resp"], separators=(",", ":"))))
    return resp


JsonRpcClient.send_raw_msg = rec
a = STLClient(username="gui-a", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
b = STLClient(username="gui-b", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
timings: list[dict] = []
marks: list[dict] = []
L3 = {0: ("1.1.1.1", "2.2.2.2"), 1: ("2.2.2.2", "1.1.1.1")}
summary_extra: dict = {}


def mark(name):
    """Record the time of the named call in `marks` (written to summary.json) and print it."""
    marks.append({"t": round(time.time() - t0, 3), "call": name})
    print(f"[call] {name}", flush=True)


def timed(name, fn, *, port=None, expect_error=False):
    """Run fn(), measure it; returns (result, error_text). An unexpected error propagates."""
    start = time.monotonic()
    res, err = None, None
    try:
        res = fn()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        if not expect_error:
            timings.append({"call": name, "port": port, "secs": round(time.monotonic() - start, 3),
                            "error": err})
            raise
    timings.append({"call": name, "port": port, "secs": round(time.monotonic() - start, 3), "error": err})
    print(f"[timed] {name} {timings[-1]['secs']}s {err or ''}", flush=True)
    return res, err


def call_ok(rc):
    """`rc` when the RPC succeeded, else a RuntimeError carrying its error text."""
    if not rc:
        raise RuntimeError(rc.err())
    return rc


def raw(name, client=None, **params):
    """One raw `capture` RPC through client._transmit, timed; returns {ok, data, error}."""
    c = client or a
    rc, _ = timed(name, lambda: c._transmit("capture", params=params))
    return {"ok": bool(rc), "data": rc.data() if rc else None, "error": None if rc else rc.err()}


def status(client=None, name="get_capture_status"):
    """Timed get_capture_status through `client` (default gui-a): the rows keyed by capture id."""
    rows, _ = timed(name, (client or a).get_capture_status)
    return rows


def fetch(cid, name="capture_fetch", client=None):
    """Raw `fetch` of up to 50 packets; destructive: the server drops what it returns from the capture."""
    return raw(name, client, command="fetch", capture_id=cid, pkt_limit=50, snaplen=0)


def fetch_all(cid, name="capture_fetch_all"):
    """`(pkts, first)`: every fetched packet and the first reply's data, fetching until none are pending or a
    reply is empty (at most 40); RuntimeError if a fetch fails."""
    pkts: list[dict] = []
    first = None
    for _ in range(40):
        r = fetch(cid, name)
        if not r["ok"]:
            raise RuntimeError(r["error"])
        first = first or r["data"]
        pkts += r["data"]["pkts"]
        if r["data"]["pending"] == 0 or not r["data"]["pkts"]:
            break
    return pkts, first


def remove_capture(cid, name="capture_remove", client=None):
    """Raw `remove` of the capture; a failure is in the returned dict."""
    return raw(name, client, command="remove", capture_id=cid)


def stop_capture(cid, name="capture_stop", client=None):
    """Raw `stop` of the capture; a failure is in the returned dict."""
    return raw(name, client, command="stop", capture_id=cid)


def start_raw(name, client=None, tx=(), rx=(), limit=100, mode="fixed", bpf=""):
    """Raw `start` of a capture on the given Tx and Rx ports; the reply dict carries the new capture id."""
    return raw(name, client, command="start", limit=limit, mode=mode, tx=list(tx), rx=list(rx), filter=bpf)


def clear_captures():
    """Remove every capture through gui-a; a failure is printed, not raised."""
    try:
        a.remove_all_captures()
    except Exception as e:
        print(f"[restore] remove_all_captures failed: {type(e).__name__}: {e}", flush=True)


def restore():
    """Ports back to the start: stop, no streams, no captures, service off, L3 resolved, no VLAN, IPv6 off."""
    mark("restore")
    clear_captures()
    for label, fn in (
            ("stop", lambda: a.stop(ports=[0, 1])),
            ("remove_streams", lambda: a.remove_all_streams(ports=[0, 1])),
            ("service_on", lambda: a.set_service_mode(ports=[0, 1], enabled=True)),
            ("vlan_clear", lambda: a.set_vlan(ports=[0, 1], vlan=[])),
            ("ipv6_off_0", lambda: a.conf_ipv6(0, False)),
            ("ipv6_off_1", lambda: a.conf_ipv6(1, False)),
            ("l3_0", lambda: call_ok(a.ports[0].set_l3_mode(*L3[0]))),
            ("l3_1", lambda: call_ok(a.ports[1].set_l3_mode(*L3[1]))),
            ("resolve_0", lambda: a.resolve(ports=[0])),
            ("resolve_1", lambda: a.resolve(ports=[1])),
            ("service_off", lambda: a.set_service_mode(ports=[0, 1], enabled=False)),
            ("remove_captures_again", lambda: a.remove_all_captures())):
        try:
            fn()
        except Exception as e:
            print(f"[restore] {label} failed: {type(e).__name__}: {e}", flush=True)


# --- traffic ------------------------------------------------------------------------------------------------
traffic_force = False     # set by probe_traffic(): True when captures only see traffic with service mode on


def load_profile(path, tunables):
    """Replace port 0's streams with those of the profile file loaded with `tunables`."""
    prof = STLProfile.load_py(str(path), direction=0, port_id=0, tunables=tunables)
    a.remove_all_streams(ports=[0])
    a.add_streams(prof.get_streams(), ports=[0])


def traffic_on(mult):
    """Start port 0 at `mult`, forced when `traffic_force` says captures need service mode on."""
    timed("start_traffic", lambda: a.start(ports=[0], mult=mult, force=traffic_force), port=0)


def traffic_off():
    """Stop port 0."""
    timed("stop_traffic", lambda: a.stop(ports=[0]), port=0)


def probe_traffic():
    """Does a capture on Rx port 1 see port 0's stream with service mode off? If not, with service on + force.

    RuntimeError when neither does."""
    global traffic_force
    load_profile(UDP1, [])
    tried = []
    for svc in (False, True):
        a.set_service_mode(ports=[0, 1], enabled=svc)
        traffic_force = svc
        try:
            traffic_on("100pps")
        except Exception as e:
            tried.append({"service": svc, "start_error": f"{type(e).__name__}: {e}"})
            continue
        cid = start_raw("probe_start", rx=[1], limit=20, bpf="udp")["data"]["capture_id"]
        time.sleep(1.5)
        count = status()[cid]["count"]
        traffic_off()
        remove_capture(cid, "probe_remove")
        tried.append({"service": svc, "count": count})
        if count > 0:
            summary_extra["traffic_mode"] = {"service_on": svc, "force": svc, "tried": tried}
            return
    raise RuntimeError(f"no capture saw the stream: {tried}")


def first_frame(name, bpf, action, *, wait=0.6):
    """A fixed capture on Rx port 1 with `bpf`, run `action`; the first fetched packet (base64) and its row.

    RuntimeError when no frame matched."""
    cid = start_raw(f"{name}_start", rx=[1], limit=20, bpf=bpf)["data"]["capture_id"]
    try:
        action()
        pkts: list[dict] = []
        for _ in range(8):
            time.sleep(wait)
            pkts, _ = fetch_all(cid, f"{name}_fetch")
            if pkts:
                break
        if not pkts:
            raise RuntimeError(f"no {name} frame matched {bpf!r}")
        return pkts[0]["binary"], {k: v for k, v in pkts[0].items() if k != "binary"}
    finally:
        stop_capture(cid, f"{name}_stop")
        remove_capture(cid, f"{name}_remove")


# --- the recording ------------------------------------------------------------------------------------------
a.connect()
b.connect()
a.acquire(ports=[0, 1], force=True)
rows: list[dict] = []       # every buffer-capture status row seen, for the key set
errors: dict = {}
summary: dict = {}
frames: dict = {}
frame_meta: dict = {}
try:
    restore()
    mark("probe_traffic")
    probe_traffic()
    svc_traffic = summary_extra["traffic_mode"]["service_on"]

    # --- the monitor: cyclic, BPF udp, status x3 at 1 s, three fetches, stop, drain, remove
    mark("monitor")
    load_profile(UDP1, [])
    a.set_service_mode(ports=[0, 1], enabled=svc_traffic)
    traffic_on("100pps")
    started, _ = timed("start_capture", lambda: a.start_capture(rx_ports=[1], limit=100, mode="cyclic",
                                                                bpf_filter="udp"), port=1)
    mon_id = started["id"]
    mon_status = []
    for _ in range(3):
        time.sleep(1.0)
        row = status()[mon_id]
        rows.append(row)
        mon_status.append(row)
    mon_fetches = [fetch(mon_id) for _ in range(3)]
    mon_stop = stop_capture(mon_id)
    mon_drain = fetch(mon_id, "capture_fetch_drain")
    mon_remove = remove_capture(mon_id)
    traffic_off()
    pk0 = mon_fetches[0]["data"]["pkts"][0]
    summary["monitor"] = {
        "start": started, "status_samples": mon_status, "final": mon_status[-1],
        "fetch_pkt_counts": [len(f["data"]["pkts"]) for f in mon_fetches],
        "fetch_pending": [f["data"]["pending"] for f in mon_fetches],
        "fetch_reply_keys": sorted(mon_fetches[0]["data"]), "stop": mon_stop,
        "drain_pkts": len(mon_drain["data"]["pkts"]),
        "drain_pending": mon_drain["data"]["pending"], "remove": mon_remove,
        "first_packet": {k: v for k, v in pk0.items() if k != "binary"},
        "start_ts": mon_fetches[0]["data"]["start_ts"]}
    frames["udp"] = pk0["binary"]

    # --- fixed recorder on the transmit side: 50 packets, status until count == 50, stop, fetch all, remove
    # Tx capture probe (re-measured with one variable, audit fix item 7): both arms start port 0's traffic
    # with force=True at the same rate and capture with the same mode and limit for the same wait; they differ
    # only in service mode (both ports). Per arm: the capture's status row and port 0's opackets delta over
    # the window.
    mark("fixed_recorder")
    probe = []
    for svc in (False, True):
        a.set_service_mode(ports=[0, 1], enabled=svc)
        a.start(ports=[0], mult="100pps", force=True)
        before = a.get_stats(ports=[0])[0]["opackets"]
        arm_id = start_raw(f"tx_probe_service_{'on' if svc else 'off'}_start", tx=[0], limit=50,
                           mode="fixed")["data"]["capture_id"]
        time.sleep(1.5)
        arm_row = status()[arm_id]
        after = a.get_stats(ports=[0])[0]["opackets"]
        traffic_off()
        remove_capture(arm_id, f"tx_probe_service_{'on' if svc else 'off'}_remove")
        probe.append({"service": svc, "force": True, "mode": "fixed", "limit": 50, "count": arm_row["count"],
                      "matched": arm_row["matched"], "opackets_delta": after - before, "row": arm_row})
    summary_extra["tx_capture_probe"] = probe
    summary_extra["tx_capture_service_off"] = probe[0]["row"]      # the fake's Tx-needs-service test reads it
    a.set_service_mode(ports=[0, 1], enabled=True)
    timed("start_traffic", lambda: a.start(ports=[0], mult="100pps", force=True), port=0)
    rec_start = start_raw("capture_start_fixed", tx=[0], limit=50, mode="fixed")
    rec_id = rec_start["data"]["capture_id"]
    deadline, polls = time.monotonic() + 10, 0
    while True:
        row = status()[rec_id]
        polls += 1
        if row["count"] == 50 or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    rows.append(row)
    rec_stop = stop_capture(rec_id)
    # the peek: a fetch with pkt_limit 0 on a stopped capture that holds packets (audit fix, item 4)
    peek_before = status()[rec_id]
    peek = raw("capture_fetch_peek", command="fetch", capture_id=rec_id, pkt_limit=0, snaplen=0)
    peek_after = status()[rec_id]
    rec_pkts, rec_first_reply = fetch_all(rec_id, "capture_fetch_recorder")
    summary_extra["fetch_peek"] = {
        "ok": peek["ok"], "pkts": len(peek["data"]["pkts"]) if peek["ok"] else None,
        "pending": peek["data"]["pending"] if peek["ok"] else None,
        "reply_keys": sorted(peek["data"]) if peek["ok"] else None,
        "start_ts": peek["data"]["start_ts"] if peek["ok"] else None,
        "status_count_before": peek_before["count"], "status_count_after": peek_after["count"],
        "fetched_before": peek_before["fetched"], "fetched_after": peek_after["fetched"],
        "next_fetch_start_ts": rec_first_reply["start_ts"], "error": None if peek["ok"] else peek["error"]}
    after_fetch = status()[rec_id]
    rec_remove = remove_capture(rec_id)
    traffic_off()
    a.set_service_mode(ports=[0, 1], enabled=svc_traffic)
    p1 = rec_pkts[0]
    summary["fixed_recorder"] = {
        "start": rec_start["data"], "polls": polls, "final": row, "stop": rec_stop["data"],
        "fetched": len(rec_pkts), "status_after_fetch": after_fetch, "remove": rec_remove,
        "first_packet": {k: v for k, v in p1.items() if k != "binary"},
        "start_ts": rec_first_reply["start_ts"],
        "fetch_reply_keys": sorted(rec_first_reply)}
    summary["first_packet"] = {"keys": sorted(p1), "index": p1["index"], "origin": p1["origin"],
                               "ts": p1["ts"], "start_ts": rec_first_reply["start_ts"], "port": p1["port"],
                               "wirelen": p1["wirelen"]}

    # --- the two 5 s counts: gui-b's own monitor next to a fresh gui-a monitor,
    # same traffic, listed in one status
    mark("oracle_counts")
    traffic_on("100pps")
    ca = start_raw("capture_start_oracle_a", rx=[1], limit=1000, mode="cyclic")["data"]["capture_id"]
    cb = start_raw("capture_start_oracle_b", b, rx=[1], limit=1000, mode="cyclic")["data"]["capture_id"]
    time.sleep(5)
    both = status(b, "get_capture_status_oracle")
    rows += [both[ca], both[cb]]
    for cid, c in ((ca, a), (cb, b)):
        stop_capture(cid, "capture_stop_oracle", c)
        remove_capture(cid, "capture_remove_oracle", c)
    traffic_off()
    summary["five_second_counts"] = {"a": both[ca]["matched"], "b": both[cb]["matched"],
                                     "a_row": both[ca], "b_row": both[cb]}

    # --- errors, raw
    mark("errors")
    summary["errors"] = {}
    bad = start_raw("capture_start_bad_bpf", rx=[1], bpf="not a filter((")
    summary["errors"]["bad_bpf"] = bad["error"]
    ids = []
    for _ in range(10):
        r = start_raw("capture_start_limit_fill", tx=[0], limit=10)
        ids.append(r["data"]["capture_id"])
    eleventh = start_raw("capture_start_eleventh", tx=[0], limit=10)
    summary["errors"]["limit"] = eleventh["error"]
    summary["errors"]["limit_ok_flag"] = eleventh["ok"]
    summary["capture_ids_at_limit"] = ids
    clear_captures()
    for cmd, name in (("fetch", "fetch_9999"), ("stop", "stop_9999"), ("remove", "remove_9999")):
        params = {"command": cmd, "capture_id": 9999}
        if cmd == "fetch":
            params.update(pkt_limit=50, snaplen=0)
        r = raw(f"capture_{cmd}_9999", **params)
        summary["errors"][name] = r["error"]
    active_id = start_raw("capture_start_active", rx=[1], limit=10, mode="cyclic")["data"]["capture_id"]
    r = remove_capture(active_id, "capture_remove_active")
    summary["remove_active_allowed"] = bool(r["ok"]) and active_id not in status()
    summary["remove_active_reply"] = r
    clear_captures()

    # --- Reset with one ACTIVE monitor and one STOPPED recorder
    mark("reset")
    load_profile(UDP1, [])
    traffic_on("100pps")
    m_id = start_raw("capture_start_reset_monitor", rx=[1], limit=100, mode="cyclic")["data"]["capture_id"]
    r_id = start_raw("capture_start_reset_recorder", tx=[0], limit=50)["data"]["capture_id"]
    time.sleep(1.0)
    stop_capture(r_id, "capture_stop_reset_recorder")
    before = status()
    timed("reset", lambda: a.reset(ports=[0]), port=0)
    after = status()
    summary["reset"] = {"before_ids": sorted(before),
                        "before_states": {i: r["state"] for i, r in before.items()}, "after": after}
    summary["reset_removes_captures"] = bool(before) and not after and m_id in before and r_id in before
    a.set_service_mode(ports=[0, 1], enabled=True)

    # --- the service capture a ping creates: gui-b samples the status list every 0.1 s
    mark("service_capture")
    seen: dict[int, dict] = {}
    stop_sampling = threading.Event()

    # thread body: gui-b lists the captures every 0.1 s until `stop_sampling`, keeping each id's first row
    def sample():
        while not stop_sampling.is_set():
            try:
                for i, r in b.get_capture_status().items():
                    seen.setdefault(i, r)
            except Exception as e:
                seen.setdefault(-1, {"error": str(e)})
            time.sleep(0.1)

    th = threading.Thread(target=sample, name="sample-service-capture")
    th.start()
    ping, perr = timed("service_ping", lambda: a.ping_ip(0, "2.2.2.2", count=1), port=0, expect_error=True)
    stop_sampling.set()
    th.join()
    rows_seen = [r for r in seen.values() if "filter" in r]
    summary["service_capture"] = {"row": rows_seen[0] if rows_seen else None, "all_rows": rows_seen,
                                  "ping_error": perr,
                                  "ping_status": [r["status"] if isinstance(r, dict) else r.state
                                                  for r in ping or []]}

    # --- persistence across a client disconnect
    mark("persistence")
    p_id = start_raw("capture_start_persist", rx=[1], limit=100, mode="cyclic")["data"]["capture_id"]
    timed("disconnect", a.disconnect)
    timed("reconnect", a.connect)
    a.acquire(ports=[0, 1], force=True)
    survived = status()
    summary["capture_survives_disconnect"] = p_id in survived
    summary["after_reconnect_status"] = survived
    clear_captures()

    # --- packet kinds: arp, vlan, ipv6 neighbour discovery
    mark("frames_arp")
    a.set_service_mode(ports=[0, 1], enabled=True)
    call_ok(a.ports[0].set_l3_mode(*L3[0]))
    frames["arp"], frame_meta["arp"] = first_frame("arp", "arp", lambda: a.resolve(ports=[0]))
    mark("frames_vlan")
    a.set_vlan(ports=[0, 1], vlan=[10])
    try:
        frames["vlan"], frame_meta["vlan"] = first_frame(
            "vlan", "vlan and icmp", lambda: a.ping_ip(0, "2.2.2.2", count=1))
    finally:
        a.set_vlan(ports=[0, 1], vlan=[])
        a.resolve(ports=[0])
    mark("frames_ipv6_nd")
    try:
        a.conf_ipv6(0, True)
    except Exception as e:
        summary["conf_ipv6_error"] = f"{type(e).__name__}: {e}"    # legacy stack: scan6 still sends the NS
    frames["ipv6_nd"], frame_meta["ipv6_nd"] = first_frame(
        "ipv6_nd", "icmp6 and ip6[40] == 135",
        lambda: timed("scan6", lambda: a.scan6(ports=[0], timeout=3), port=0, expect_error=True))
    frames["truncated"] = base64.b64encode(bytes(30)).decode()
    frame_meta["truncated"] = {"built_by_hand": True, "len": 30}

    # --- rates: 1500 B frames at 100 and 1000 pps, three fetches of 50, each timed and sized
    mark("rates")
    summary["fetch_sizes"] = {}
    load_profile(BENCH, ["--packet_len", "1500"])
    a.set_service_mode(ports=[0, 1], enabled=svc_traffic)
    for pps in (100, 1000):
        traffic_on(f"{pps}pps")
        cid = start_raw(f"capture_start_{pps}pps", rx=[1], limit=1000, mode="cyclic",
                        bpf="udp")["data"]["capture_id"]
        time.sleep(2.5)
        res = []
        for _ in range(3):
            r = fetch(cid, f"capture_fetch_1500B_{pps}pps")
            res.append({"secs": timings[-1]["secs"], "pkts": len(r["data"]["pkts"]),
                        "pending": r["data"]["pending"],
                        "wirelen": r["data"]["pkts"][0]["wirelen"] if r["data"]["pkts"] else None,
                        "json_bytes": last_wire.get("json_bytes"), "wire_bytes": last_wire.get("wire_bytes")})
        res_status = status()[cid]
        stop_capture(cid, f"capture_stop_{pps}pps")
        remove_capture(cid, f"capture_remove_{pps}pps")
        traffic_off()
        summary["fetch_sizes"][f"{pps}pps"] = {"fetches": res, "status": res_status}
        rows.append(res_status)

    # --- the Scapy trees, in process
    mark("scapy")
    t = time.monotonic()
    svc = Scapy_service()
    construct = round(time.monotonic() - t, 3)
    trees, per_call = {}, {}
    for kind, b64 in frames.items():
        t = time.monotonic()
        trees[kind] = svc.reconstruct_pkt(None, b64, [])
        per_call[kind] = round(time.monotonic() - t, 4)
    summary["scapy"] = {"construct_secs": construct, "reconstruct_secs": per_call,
                        "reconstruct_max_secs": max(per_call.values())}
finally:
    restore()
    try:
        a.release(ports=[0, 1])
    except Exception as e:
        print(f"[restore] release failed: {e}", flush=True)
    for client in (a, b):
        try:
            client.disconnect()
        except Exception as e:
            print(f"[restore] disconnect of {client.ctx.username} failed: {e}", flush=True)

kinds: dict[str, float] = {}
for tm in timings:
    kinds[tm["call"]] = max(kinds.get(tm["call"], 0), tm["secs"])
buffer_rows = [r for r in rows if "snaplen" not in r]
summary.update({
    "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "marks": marks,
    "status_row": rows[0], "status_row_keys": sorted({k for r in buffer_rows for k in r}),
    "filter_keys": sorted({k for r in rows for k in r["filter"]}),
    "fetch_packet_keys": summary["first_packet"]["keys"], "frames": frames, "frame_meta": frame_meta,
    "timings": timings, "max_secs_by_call": kinds, "slowest_call": max(timings, key=lambda x: x["secs"]),
    **summary_extra})
payloads = {"rpc.jsonl": "".join(json.dumps(r) + "\n" for r in log),
            "summary.json": json.dumps(summary, indent=1, default=str) + "\n",
            "trees.json": json.dumps(trees, indent=1, default=str) + "\n"}
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
for name, text in payloads.items():
    (out / name).write_text(text)
    (Path("/var/tmp") / f"p3c-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
print(json.dumps({"rows": len(log), "slowest": summary["slowest_call"], "errors": summary["errors"],
                  "reset_removes_captures": summary["reset_removes_captures"],
                  "capture_survives_disconnect": summary["capture_survives_disconnect"],
                  "scapy": summary["scapy"], "five_second_counts": {k: summary["five_second_counts"][k]
                                                                    for k in "ab"}}, indent=1))
