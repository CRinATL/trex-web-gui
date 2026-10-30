"""Record P3b port-configuration, ping, scan and xstats fixtures from the real v3.08 guest
(the guest's RPC/PUB ports, 4501/4500 on jammy).
Usage: uv run --project backend python -I -B vm/record_p3b.py <out_dir>
Client "gui-a" acquires ports 0 and 1 and drives every command P3b will send: service mode, set_vlan, set_l2,
port-level set_l3_mode + a separate resolve (resolvable and unresolvable destination), conf_ipv6, pings,
scan6, xstats while idle / transmitting flow_latency.py / after Stop, and the five refusals (two raw RPCs,
three client calls). Client "gui-b" never acquires: after every step it syncs the ports and its
layer_cfg/vlan/service/rx_filter_mode/ multicast is the oracle. During pings and scans gui-b also polls the
port every 0.25 s ("during" samples). Every call is timed with time.monotonic (summary.json "timings").
Whatever happens, `finally` returns both ports to service off, L3 resolved (p0 1.1.1.1 -> 2.2.2.2, p1 the
reverse), no VLAN, IPv6 off. Structure after vm/record_p3a.py. Fixture files are written only after every call
has succeeded; a UTC-stamped copy of each goes to /var/tmp (canon:OUT-1)."""
import json
import subprocess
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
from trex.stl.api import STLClient, STLProfile
from trex.utils.zipmsg import ZippedMsg

HERE = Path(__file__).resolve().parent
PROFILE = HERE.parent / "tests" / "fixtures" / "profiles" / "flow_latency.py"
out = Path(sys.argv[1])
Z, t0, lock = ZippedMsg(), time.time(), threading.Lock()
log: list[dict] = []


def dec(b):
    """The JSON in a (possibly zipped) RPC message as a Python value; None when `b` is not bytes."""
    if not isinstance(b, (bytes, bytearray)):
        return None
    return json.loads((Z.decompress(b) if Z.is_compressed(b) else b).decode())


orig = JsonRpcClient.send_raw_msg


def rec(self, msg, retry=0):
    """Stands in for JsonRpcClient.send_raw_msg: sends as before, logs the decoded pair, returns the reply."""
    resp = orig(self, msg, retry)
    row = {"t": round(time.time() - t0, 3), "client": self.ctx.username,
           "thread": threading.current_thread().name, "req": dec(msg), "resp": dec(resp)}
    with lock:
        log.append(row)
    return resp


JsonRpcClient.send_raw_msg = rec
a = STLClient(username="gui-a", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
b = STLClient(username="gui-b", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
timings: list[dict] = []
composites: dict[str, float] = {}
steps: list[dict] = []
marks: list[dict] = []
last_snap: dict[int, dict] = {}
L3 = {0: ("1.1.1.1", "2.2.2.2"), 1: ("2.2.2.2", "1.1.1.1")}


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


def snapshot(p):
    """The oracle: gui-b (independent, not an owner) asks the server for the port and reads its own cache."""
    b.ports[p].sync()
    attr = b.ports[p].get_ts_attr()
    return {"layer_cfg": attr["layer_cfg"], "vlan": attr["vlan"]["tags"],
            "rx_filter_mode": attr["rx_filter_mode"],
            "multicast": attr["multicast"]["enabled"], "service": b.ports[p].service_mode,
            "service_filtered": b.ports[p].service_mode_filtered, "state": b.ports[p].state_name
            if hasattr(b.ports[p], "state_name") else b.ports[p].state}


def flat(d, pre=""):
    """Nested dicts flattened to one level with dotted keys."""
    if isinstance(d, dict):
        return {k2: v for k, v in d.items() for k2, v in flat(v, f"{pre}{k}.").items()}
    return {pre.rstrip("."): d}


def step(name, fn, *, port=0, expect_error=False):
    """Time fn, then snapshot via the oracle and record which keys changed since the port's last snapshot."""
    mark(name)
    res, err = timed(name, fn, port=port, expect_error=expect_error)
    snap = snapshot(port)
    before = flat(last_snap.get(port, {}))
    after = flat(snap)
    changed = {k: {"from": before.get(k), "to": after[k]} for k in after if before.get(k) != after[k]}
    last_snap[port] = snap
    steps.append({"step": name, "port": port, "error": err, "oracle": snap,
                  "changed": changed if before else None})
    return res, err


def call_ok(rc):
    """`rc` when the RPC succeeded, else a RuntimeError carrying its error text."""
    if not rc:
        raise RuntimeError(rc.err())
    return rc


def prec(r):
    """A ping result (a dict, or a record whose state 2/0/1 is success/timeout/unreachable) as a plain row."""
    if isinstance(r, dict):
        return {"text": r["formatted_string"], "status": r["status"], "rtt": r.get("rtt"),
                "src_ip": r.get("src_ip"), "ttl": r.get("ttl")}
    return {"text": str(r),
            "status": {2: "success", 0: "timeout", 1: "unreachable"}.get(r.state, str(r.state)),
            "rtt": r.rtt if isinstance(r.rtt, (int, float)) else None, "src_ip": r.responder_ip, "ttl": r.ttl}


class Watch:
    """Poll the oracle every 0.25 s while a service call runs; summary gets before / during / after."""

    def __init__(self, label, port=0):
        """Name the watch and the port; `rows` collects the polled samples."""
        self.label, self.port, self.rows = label, port, []

    def __enter__(self):
        """Snapshot the port as the before state, then start the polling thread."""
        self.before = snapshot(self.port)
        self.stop = threading.Event()
        self.th = threading.Thread(target=self.run, name=f"watch-{self.label}")
        self.t = time.monotonic()
        self.th.start()
        return self

    def run(self):
        """Polling thread body: a snapshot every 0.25 s until `stop` is set, kept in `rows`."""
        while not self.stop.is_set():
            s = snapshot(self.port)
            self.rows.append({"dt": round(time.monotonic() - self.t, 2),
                              "rx_filter_mode": s["rx_filter_mode"],
                              "multicast": s["multicast"], "service": s["service"]})
            time.sleep(0.25)

    def __exit__(self, *exc):
        """Stop the polling thread, snapshot the after state, file all three in `watches`; never swallows."""
        self.stop.set()
        self.th.join()
        after = snapshot(self.port)
        watches[self.label] = {
            "before": {k: self.before[k] for k in ("rx_filter_mode", "multicast", "service")},
            "during": self.rows,
            "after": {k: after[k] for k in ("rx_filter_mode", "multicast", "service")}}
        return False


watches: dict[str, dict] = {}


def restore():
    """Ports back to the start: stop, no streams, service off, L3 resolved,
    no VLAN, IPv6 off, multicast off."""
    mark("restore")
    for label, fn in (
            ("stop", lambda: a.stop(ports=[0, 1])),
            ("remove_streams", lambda: a.remove_all_streams(ports=[0, 1])),
            ("service_on", lambda: a.set_service_mode(ports=[0, 1], enabled=True)),
            ("vlan_clear", lambda: a.set_vlan(ports=[0, 1], vlan=[])),
            ("ipv6_off_0", lambda: a.conf_ipv6(0, False)),
            ("ipv6_off_1", lambda: a.conf_ipv6(1, False)),
            ("multicast_off", lambda: a.set_port_attr(ports=[0, 1], multicast=False)),
            ("l3_0", lambda: call_ok(a.ports[0].set_l3_mode(*L3[0]))),
            ("l3_1", lambda: call_ok(a.ports[1].set_l3_mode(*L3[1]))),
            ("resolve_0", lambda: a.resolve(ports=[0])),
            ("resolve_1", lambda: a.resolve(ports=[1])),
            ("service_off", lambda: a.set_service_mode(ports=[0, 1], enabled=False))):
        try:
            fn()
        except Exception as e:
            print(f"[restore] {label} failed: {type(e).__name__}: {e}", flush=True)


def xstats_sample(condition):
    """Append one sample: both ports' xstats from client a, plus gui-b's packet counters as the oracle."""
    t_a = round(time.time() - t0, 3)
    vals = {p: a.get_xstats(p) for p in (0, 1)}
    st = b.get_stats(ports=[0, 1])
    oracle = {p: {"opackets": st[p]["opackets"], "ipackets": st[p]["ipackets"]} for p in (0, 1)}
    xstats_samples.append({"condition": condition, "t_before": t_a, "t_after": round(time.time() - t0, 3),
                           "xstats": vals, "oracle": oracle})
    print(f"[xstats] {condition}", flush=True)


xstats_samples: list[dict] = []
xstats_names: dict = {}
refusals: dict = {}
pings: dict = {}
extra: dict = {}

a.connect()
b.connect()
a.acquire(ports=[0, 1], force=True)
try:
    restore()
    info = {p: {k: a.ports[p].info.get(k) for k in ("hw_mac", "stack", "driver", "description")}
            for p in (0, 1)}
    hw1 = a.ports[1].info["hw_mac"]
    for p in (0, 1):
        snapshot_p = snapshot(p)
        last_snap[p] = snapshot_p
    extra["start_state"] = {p: last_snap[p] for p in (0, 1)}
    # --- the guest kernel's view of IPv6 on the veth pair
    # (the legacy stack does not answer IPv6, only the kernel does)
    sysctl = {}
    for dev in ("veth0", "veth1"):
        r = subprocess.run([str(HERE / "ssh.sh"), f"sysctl -n net.ipv6.conf.{dev}.disable_ipv6"],
                           capture_output=True, text=True, timeout=30)
        sysctl[dev] = r.stdout.strip() if r.returncode == 0 else f"rc={r.returncode} {r.stderr.strip()}"

    # --- multicast on net_af_packet, service off
    _, err = step("set_port_attr_multicast_true", lambda: a.set_port_attr(ports=[0], multicast=True))
    extra["multicast_true"] = {"ok": err is None, "error": err, "oracle_multicast": last_snap[0]["multicast"]}
    step("set_port_attr_multicast_false", lambda: a.set_port_attr(ports=[0], multicast=False))

    # --- composites: a whole Apply, starting with service off, as the GUI will run it
    # (one executor job per call)
    def apply(dst):
        """One whole Apply to `dst` as the GUI will run it, each part a recorded step; (total secs, parts)."""
        t = time.monotonic()
        parts = {}
        for name, fn, exp in (
                ("service_on", lambda: a.set_service_mode(ports=[0], enabled=True), False),
                ("set_l3", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", dst)), False),
                ("resolve", lambda: a.resolve(ports=[0]), dst != "2.2.2.2"),
                ("service_off", lambda: a.set_service_mode(ports=[0], enabled=False), False)):
            _, err = step(f"apply_{dst}_{name}", fn, expect_error=exp)
            parts[name] = {"secs": timings[-1]["secs"], "error": err}
        return round(time.monotonic() - t, 3), parts

    composites["apply_resolved"], extra["apply_resolved_parts"] = apply("2.2.2.2")
    extra["resolved_mac"] = last_snap[0]["layer_cfg"]["ether"]["dst"]
    extra["resolved_layer_cfg"] = last_snap[0]["layer_cfg"]
    composites["apply_unresolved"], extra["apply_unresolved_parts"] = apply("1.1.1.99")
    extra["unresolved_layer_cfg"] = last_snap[0]["layer_cfg"]
    composites["apply_resolved_again"], _ = apply("2.2.2.2")

    # --- the rest with service on
    step("service_on_0", lambda: a.set_service_mode(ports=[0], enabled=True))
    step("service_on_1", lambda: a.set_service_mode(ports=[1], enabled=True), port=1)
    for tags in ([10], [10, 20], []):
        step(f"set_vlan_{'_'.join(map(str, tags)) or 'none'}", lambda t=tags: a.set_vlan(ports=[0], vlan=t))
    step("set_l2", lambda: a.set_l2_mode(0, hw1))
    step("set_l3_resolvable", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "2.2.2.2")))
    step("resolve_resolvable", lambda: a.resolve(ports=[0]))
    step("set_l3_nobody", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "1.1.1.99")))
    step("resolve_nobody", lambda: a.resolve(ports=[0]), expect_error=True)
    step("set_l3_back", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "2.2.2.2")))
    step("resolve_back", lambda: a.resolve(ports=[0]))
    extra["resolved_mac_service_on"] = last_snap[0]["layer_cfg"]["ether"]["dst"]
    step("conf_ipv6_auto", lambda: a.conf_ipv6(0, True), expect_error=True)
    step("conf_ipv6_source", lambda: a.conf_ipv6(0, True, "2001:db8::1"), expect_error=True)
    step("conf_ipv6_off", lambda: a.conf_ipv6(0, False), expect_error=True)

    # --- VLAN 10 on both ports: one resolve and one ping
    step("vlan10_both", lambda: a.set_vlan(ports=[0, 1], vlan=[10]))
    _, err = step("vlan10_resolve", lambda: a.resolve(ports=[0]), expect_error=True)
    res, perr = timed("vlan10_ping", lambda: a.ping_ip(0, "2.2.2.2", count=1), port=0, expect_error=True)
    extra["vlan10"] = {"resolve_error": err, "resolve_layer_cfg": last_snap[0]["layer_cfg"],
                       "ping_error": perr, "ping": [prec(r) for r in res or []]}
    step("vlan_clear_both", lambda: a.set_vlan(ports=[0, 1], vlan=[]))
    step("vlan_clear_resolve", lambda: a.resolve(ports=[0]))

    # --- pings
    pings["ok"] = []
    for i in range(5):
        mark(f"ping_{i}")
        if i == 0:
            with Watch("ping_v4"):
                res, _ = timed("ping_v4_ok", lambda: a.ping_ip(0, "2.2.2.2", count=1), port=0)
        else:
            res, _ = timed("ping_v4_ok", lambda: a.ping_ip(0, "2.2.2.2", count=1), port=0)
        pings["ok"].append({"records": [prec(r) for r in res], "secs": timings[-1]["secs"]})
    mark("ping_timeout")
    with Watch("ping_v4_timeout"):
        res, _ = timed("ping_v4_timeout", lambda: a.ping_ip(0, "1.1.1.99", count=1), port=0)
    pings["timeout"] = {"records": [prec(r) for r in res], "secs": timings[-1]["secs"]}

    step("ipv6_on_for_scan", lambda: a.conf_ipv6(0, True), expect_error=True)
    mark("scan6")
    with Watch("scan6"):
        scan, scan_err = timed("scan6", lambda: a.scan6(ports=[0], timeout=3), port=0, expect_error=True)
    scan_p0 = (scan[0] if isinstance(scan, dict) else scan) or []
    peer = [n for n in scan_p0 if n["mac"].lower() == hw1.lower()]
    extra["scan"] = {"raw": scan, "error": scan_err, "port1_entries": peer}
    m = [int(x, 16) for x in hw1.split(":")]
    eui = f"fe80::{(m[0] ^ 2) << 8 | m[1]:x}:{m[2] << 8 | 0xff:x}:fe{m[3]:02x}:{m[4] << 8 | m[5]:x}"
    dst6 = peer[0]["ipv6"] if peer else eui
    mark("ping_v6")
    with Watch("ping_v6"):
        res, perr = timed("ping_v6", lambda: a.ping_ip(0, dst6, count=1), port=0, expect_error=True)
    pings["v6"] = {"dst": dst6, "dst_from_scan": bool(peer), "error": perr,
                   "records": [prec(r) for r in res or []], "secs": timings[-1]["secs"]}
    step("ipv6_off_after_scan", lambda: a.conf_ipv6(0, False), expect_error=True)
    step("service_off_0", lambda: a.set_service_mode(ports=[0], enabled=False))
    step("service_off_1", lambda: a.set_service_mode(ports=[1], enabled=False), port=1)

    # --- raw three-tag refusal (idle, service on so only the tag count can refuse)
    step("service_on_for_raw", lambda: a.set_service_mode(ports=[0], enabled=True))
    mark("raw_three_tags")
    rc, _ = timed("raw_set_vlan_three", lambda: a.ports[0].transmit(
        "set_vlan", {"handler": a.ports[0].handler, "port_id": 0, "vlan": [10, 20, 30], "block": False}),
        port=0)
    refusals["raw_set_vlan_three_tags"] = {"ok": bool(rc), "error": None if rc else rc.err(),
                                            "data": rc.data() if rc else None}
    step("after_raw_three_tags", lambda: None)
    step("service_off_after_raw", lambda: a.set_service_mode(ports=[0], enabled=False))

    # --- xstats names and values: idle, while port 0 transmits, after Stop
    for p in (0, 1):
        rc = a.ports[p].transmit("get_port_xstats_names", {"port_id": p})
        xstats_names[p] = rc.data()["xstats_names"]
    prof = STLProfile.load_py(str(PROFILE), direction=0, port_id=0, tunables=[])
    a.add_streams(prof.get_streams(), ports=[0])
    time.sleep(1)
    mark("xstats_idle")
    xstats_sample("idle")
    time.sleep(1)
    xstats_sample("idle")
    mark("start_traffic")
    a.start(ports=[0])
    time.sleep(2.0)
    for _ in range(3):
        xstats_sample("load")
        time.sleep(1.0)
    svc_while_tx, svc_err = timed("service_on_while_tx", lambda: a.set_service_mode(ports=[0], enabled=True),
                                  port=0, expect_error=True)
    refusals["service_on_while_tx"] = {"error": svc_err}
    extra["client_view_during_tx"] = {"state": a.ports[0].state, "is_writeable": a.ports[0].is_writeable(),
                                      "is_dynamic": a.ports[0].is_dynamic,
                                      "is_active": a.ports[0].is_active(),
                                      "is_transmitting": a.ports[0].is_transmitting(),
                                      "service_mode": a.ports[0].is_service_mode_on()}
    mark("refusal_set_l2_raw")
    rc, _ = timed("raw_set_l2_while_tx", lambda: a.ports[0].transmit(
        "set_l2", {"handler": a.ports[0].handler, "port_id": 0, "dst_mac": hw1, "block": False}), port=0)
    refusals["raw_set_l2_while_tx"] = {"ok": bool(rc), "error": None if rc else rc.err()}
    mark("refusal_set_l2_client")
    _, err = timed("client_set_l2_while_tx", lambda: a.set_l2_mode(0, hw1), port=0, expect_error=True)
    # STLPort.is_writeable() is True in TX when is_dynamic
    refusals["client_set_l2_while_tx"] = {"error": err}
    mark("refusal_set_l2_client_static")
    # only so that common/trex_port.py:41 (the base class check) speaks; restored below
    a.ports[0].is_dynamic = False
    _, err = timed("client_set_l2_while_tx_base_check", lambda: a.set_l2_mode(0, hw1), port=0,
                   expect_error=True)
    a.ports[0].is_dynamic = True
    refusals["client_set_l2_while_tx_base_check"] = {"error": err}
    snap_tx = snapshot(0)
    extra["during_tx_snapshot"] = snap_tx
    mark("stop_traffic")
    timed("stop", lambda: a.stop(ports=[0]), port=0)
    time.sleep(1)
    xstats_sample("after_stop")
    time.sleep(1)
    xstats_sample("after_stop")
    a.remove_all_streams(ports=[0])
finally:
    restore()
    final = {p: snapshot(p) for p in (0, 1)}
    try:
        a.release(ports=[0, 1])
    except Exception as e:
        print(f"[restore] release failed: {e}", flush=True)
    a.disconnect()
    b.disconnect()

by = {c: [s for s in xstats_samples if s["condition"] == c] for c in ("idle", "load", "after_stop")}
moving = {}
for p in (0, 1):
    names = xstats_names[p]
    i0, i1 = by["idle"][-1]["xstats"][p], by["load"][-1]["xstats"][p]
    moving[p] = sorted(n for n in i1 if i1[n] != i0.get(n))
kinds: dict[str, float] = {}
for t in timings:
    kinds[t["call"]] = max(kinds.get(t["call"], 0), t["secs"])
summary = {
    "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "profile": PROFILE.name,
    "marks": marks, "port_info": info, "hw_mac_port1": hw1, "info_stack": info[0]["stack"],
    "sysctl_disable_ipv6": sysctl, "timings": timings, "max_secs_by_call": kinds,
    "composites_secs": composites,
    "slowest_call": max(timings, key=lambda t: t["secs"]), "steps": steps, "watches": watches, "pings": pings,
    "refusals": refusals, "xstats_names": xstats_names, "xstats_names_that_move_under_load": moving,
    "xstats_samples": xstats_samples, "final_state": final, **extra,
}
payloads = {"rpc.jsonl": "".join(json.dumps(r) + "\n" for r in log),
            "summary.json": json.dumps(summary, indent=1, default=str) + "\n"}
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
for name, text in payloads.items():
    (out / name).write_text(text)
    (Path("/var/tmp") / f"p3b-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
print(json.dumps({"rows": len(log), "slowest": summary["slowest_call"], "composites": composites,
                  "resolved_mac": extra.get("resolved_mac"), "refusals": refusals}, indent=1))
