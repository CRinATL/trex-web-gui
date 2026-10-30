"""Record P3b port configuration on the linux_based stack (P3b follow-up 2) from the real v3.08 guest
(the guest's RPC/PUB ports, 4501/4500 on jammy).
Run by `make record-p3b-v6` (vm/real.sh switches the guest to linux_based first,
vm/stack.sh).
Usage: uv run --project backend python -I -B vm/record_p3b_v6.py <out_dir>
Client "gui-a" acquires ports 0 and 1 and runs the IPv6 configuration the legacy stack refuses (auto, a given
source, off), the L2/L3/VLAN commands and resolve, scan6 and pings (IPv4, IPv6 link-local, IPv6 global);
"gui-b" never acquires and is the oracle (layer_cfg after every step). Every call is timed with
time.monotonic, and every RPC is logged, so summary.json counts the stack's async answers per method:
JSONRPC_V2_ERR_TRY_AGAIN (-32001, the client re-sends every 0.3 s) and the WIP ticket polled with
get_async_results (trex_req_resp_client.py:199-222). `finally` returns both ports to service off, L3 (p0
1.1.1.1 -> 2.2.2.2, p1 the reverse), no VLAN, IPv6 off; after the VLAN cycle port 1's resolve fails on
linux_based, and summary.json's final_state records it unresolved. Structure after vm/record_p3b.py. Fixture
files are written only after every call has succeeded; a UTC-stamped copy of each goes to
/var/tmp (canon:OUT-1)."""
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
from trex.stl.api import STLClient
from trex.utils.zipmsg import ZippedMsg

out = Path(sys.argv[1])
Z, t0, lock = ZippedMsg(), time.time(), threading.Lock()
log: list[dict] = []
TRY_AGAIN, WIP = -32001, -32002


def dec(b):
    """The JSON in a (possibly zipped) RPC message as a Python value; None when `b` is not bytes."""
    if not isinstance(b, (bytes, bytearray)):
        return None
    return json.loads((Z.decompress(b) if Z.is_compressed(b) else b).decode())


orig = JsonRpcClient.send_raw_msg


def rec(self, msg, retry=0):
    """Stands in for JsonRpcClient.send_raw_msg: sends as before, logs the decoded pair, returns the reply."""
    resp = orig(self, msg, retry)
    row = {"t": round(time.time() - t0, 3), "client": self.ctx.username, "req": dec(msg), "resp": dec(resp)}
    with lock:
        log.append(row)
    return resp


JsonRpcClient.send_raw_msg = rec
a = STLClient(username="gui-a", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
b = STLClient(username="gui-b", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
timings: list[dict] = []
steps: list[dict] = []
pings: dict = {}
extra: dict = {}
L3 = {0: ("1.1.1.1", "2.2.2.2"), 1: ("2.2.2.2", "1.1.1.1")}
SRC6 = {0: "2001:db8::1", 1: "2001:db8::2"}   # documentation prefix (RFC 3849)


def timed(name, fn, *, port=None, expect_error=False):
    """Run fn(), measure it, and note the RPC rows it sent; returns (result, error_text).
    An error not marked expected is raised as a RuntimeError after the timing is recorded."""
    first = len(log)
    start = time.monotonic()
    res, err = None, None
    try:
        res = fn()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    secs = round(time.monotonic() - start, 3)
    rows = log[first:]
    timings.append({"call": name, "port": port, "secs": secs, "error": err, "rpcs": len(rows),
                    "try_again": sum(1 for r in rows if code_of(r) == TRY_AGAIN),
                    "wip": sum(1 for r in rows if code_of(r) == WIP),
                    "async_polls": sum(1 for r in rows if method_of(r) == "get_async_results")})
    print(f"[timed] {name} {secs}s rpcs={len(rows)} {err or ''}", flush=True)
    if err and not expect_error:
        raise RuntimeError(f"{name}: {err}")
    return res, err


def method_of(row):
    """The JSON-RPC method of a logged row's request; None when the request is not a single call."""
    req = row["req"]
    return req.get("method") if isinstance(req, dict) else None


def code_of(row):
    """The error code of a logged row's response; None when it is not an error dict."""
    resp = row["resp"]
    return resp.get("error", {}).get("code") if isinstance(resp, dict) else None


def snapshot(p):
    """The oracle: gui-b syncs port p and returns its layer_cfg, VLAN tags and service mode."""
    b.ports[p].sync()
    attr = b.ports[p].get_ts_attr()
    return {"layer_cfg": attr["layer_cfg"], "vlan": attr["vlan"]["tags"], "service": b.ports[p].service_mode}


def step(name, fn, *, port=0, expect_error=False):
    """Time fn through `timed`, then record the oracle snapshot of the port; returns (result, error_text)."""
    res, err = timed(name, fn, port=port, expect_error=expect_error)
    steps.append({"step": name, "port": port, "error": err, "oracle": snapshot(port)})
    return res, err


def call_ok(rc):
    """`rc` when the RPC succeeded, else a RuntimeError carrying its error text."""
    if not rc:
        raise RuntimeError(rc.err())
    return rc


def prec(r):
    """A ping result (a dict, or a record whose state 2/0/1 is success/timeout/unreachable) as a plain row."""
    if isinstance(r, dict):
        return {"text": r["formatted_string"], "status": r["status"], "rtt": r.get("rtt")}
    return {"text": str(r),
            "status": {2: "success", 0: "timeout", 1: "unreachable"}.get(r.state, str(r.state)),
            "rtt": r.rtt if isinstance(r.rtt, (int, float)) else None}


def arp_check(p):
    """Flush port p's namespace neighbour cache (linux_based keeps one per port), then resolve port p."""
    r = subprocess.run([str(Path(__file__).resolve().parent / "ssh.sh"),
                        f"sudo ip netns exec trex-a-{p}-0 ip neigh flush all"],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    _, err = timed(f"resolve_{p}_after_flush", lambda: a.resolve(ports=[p]), port=p, expect_error=True)
    return {"flush_rc": r.returncode, "flush_stderr": r.stderr.strip(), "resolves": err is None, "error": err}


def restore():
    """Ports back to the start: service off, no VLAN, IPv6 off, L3 resolved; a failing step is printed
    (port 1's resolve does fail after the VLAN cycle)."""
    for label, fn in (
            ("service_on", lambda: a.set_service_mode(ports=[0, 1], enabled=True)),
            ("vlan_clear", lambda: a.set_vlan(ports=[0, 1], vlan=[])),
            ("ipv6_off_0", lambda: a.conf_ipv6(0, False)),
            ("ipv6_off_1", lambda: a.conf_ipv6(1, False)),
            ("l3_0", lambda: call_ok(a.ports[0].set_l3_mode(*L3[0]))),
            ("l3_1", lambda: call_ok(a.ports[1].set_l3_mode(*L3[1]))),
            ("resolve_0", lambda: a.resolve(ports=[0])),
            ("resolve_1", lambda: a.resolve(ports=[1])),
            ("service_off", lambda: a.set_service_mode(ports=[0, 1], enabled=False))):
        try:
            fn()
        except Exception as e:
            print(f"[restore] {label} failed: {type(e).__name__}: {e}", flush=True)


a.connect()
b.connect()
a.acquire(ports=[0, 1], force=True)
try:
    restore()
    info = {p: {k: a.ports[p].info.get(k) for k in ("hw_mac", "stack", "driver")} for p in (0, 1)}
    if any(info[p]["stack"] != "linux_based" for p in (0, 1)):
        raise SystemExit(f"the guest is not on linux_based: {info} (run via make record-p3b-v6)")
    hw1 = a.ports[1].info["hw_mac"]
    extra["start_state"] = {p: snapshot(p) for p in (0, 1)}

    # --- an Apply as the GUI runs it, service off at the start: L3 resolved, then IPv6 auto, then back
    for name, fn, port, exp in (
            ("service_on_0", lambda: a.set_service_mode(ports=[0], enabled=True), 0, False),
            ("set_vlan_none", lambda: a.set_vlan(ports=[0], vlan=[]), 0, False),
            ("set_l3_resolvable", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "2.2.2.2")), 0, False),
            ("resolve_resolvable", lambda: a.resolve(ports=[0]), 0, False),
            ("conf_ipv6_auto", lambda: a.conf_ipv6(0, True), 0, False),
            ("service_off_0", lambda: a.set_service_mode(ports=[0], enabled=False), 0, False)):
        step(name, fn, port=port, expect_error=exp)
    extra["ipv6_auto_layer_cfg"] = steps[-2]["oracle"]["layer_cfg"]

    # --- the rest with service on, on both ports
    step("service_on_both", lambda: a.set_service_mode(ports=[0, 1], enabled=True))
    step("conf_ipv6_auto_1", lambda: a.conf_ipv6(1, True), port=1)
    for p in (0, 1):
        step(f"conf_ipv6_source_{p}", lambda p=p: a.conf_ipv6(p, True, SRC6[p]), port=p)
    extra["ipv6_source_layer_cfg"] = {p: snapshot(p)["layer_cfg"] for p in (0, 1)}
    step("set_l3_nobody", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "1.1.1.99")))
    step("resolve_nobody", lambda: a.resolve(ports=[0]), expect_error=True)
    step("set_l3_back", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "2.2.2.2")))
    step("resolve_back", lambda: a.resolve(ports=[0]))
    step("set_l2", lambda: a.set_l2_mode(0, hw1))
    step("set_l3_after_l2", lambda: call_ok(a.ports[0].set_l3_mode("1.1.1.1", "2.2.2.2")))
    step("resolve_after_l2", lambda: a.resolve(ports=[0]))
    # unchanged: what every GUI Apply sends
    step("set_vlan_none_noop", lambda: a.set_vlan(ports=[0], vlan=[]))
    extra["port1_resolves_after_noop_vlan"] = arp_check(1)

    # --- scan and pings
    scan, scan_err = timed("scan6", lambda: a.scan6(ports=[0], timeout=3), port=0, expect_error=True)
    scan_p0 = (scan[0] if isinstance(scan, dict) else scan) or []
    peer = [n for n in scan_p0 if n["mac"].lower() == hw1.lower()]
    extra["scan"] = {"raw": scan, "error": scan_err, "port1_entries": peer}
    for label, dst in (("v4", "2.2.2.2"), ("v6_link_local", peer[0]["ipv6"] if peer else None),
                       ("v6_global", SRC6[1]), ("v4_timeout", "1.1.1.99")):
        if dst is None:
            pings[label] = {"skipped": "scan6 found no entry for port 1"}
            continue
        rows = []
        for _ in range(3):
            res, perr = timed(f"ping_{label}", lambda d=dst: a.ping_ip(0, d, count=1), port=0,
                              expect_error=True)
            rows.append({"error": perr, "records": [prec(r) for r in res or []], "secs": timings[-1]["secs"]})
        pings[label] = {"dst": dst, "runs": rows}

    step("conf_ipv6_off_0", lambda: a.conf_ipv6(0, False))
    step("conf_ipv6_off_1", lambda: a.conf_ipv6(1, False), port=1)
    extra["ipv6_off_layer_cfg"] = steps[-1]["oracle"]["layer_cfg"]

    # --- last, because it cannot be undone without a TRex restart: a VLAN set and cleared on port 0 leaves
    # port 0's namespace unanswered for ARP on this stack (found 2026-10-18; legacy keeps answering). Port 1's
    # neighbour cache is flushed first, so its resolve has to send an ARP request.
    for tags in ([10], [10, 20], []):
        step(f"set_vlan_{'_'.join(map(str, tags)) or 'none'}", lambda t=tags: a.set_vlan(ports=[0], vlan=t))
    extra["port1_resolves_after_vlan_cycle"] = arp_check(1)
    step("service_off_both", lambda: a.set_service_mode(ports=[0, 1], enabled=False))
finally:
    restore()
    final = {p: snapshot(p) for p in (0, 1)}
    try:
        a.release(ports=[0, 1])
    except Exception as e:
        print(f"[restore] release failed: {e}", flush=True)
    a.disconnect()
    b.disconnect()

by_call: dict[str, dict] = {}
for t in timings:
    k = by_call.setdefault(t["call"], {"n": 0, "max_secs": 0.0, "try_again": 0, "wip": 0, "async_polls": 0})
    k["n"] += 1
    k["max_secs"] = max(k["max_secs"], t["secs"])
    for f in ("try_again", "wip", "async_polls"):
        k[f] += t[f]
async_by_method: dict[str, dict] = {}
for r in log:
    m, c = method_of(r), code_of(r)
    if c in (TRY_AGAIN, WIP) or m == "get_async_results":
        e = async_by_method.setdefault(m or "?", {"try_again": 0, "wip": 0, "rows": 0})
        e["rows"] += 1
        e["try_again"] += c == TRY_AGAIN
        e["wip"] += c == WIP
summary = {
    "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "port_info": info,
    "hw_mac_port1": hw1,
    "timings": timings, "by_call": by_call, "async_by_method": async_by_method,
    "slowest_call": max(timings, key=lambda t: t["secs"]), "steps": steps, "pings": pings,
    "final_state": final,
    **extra,
}
payloads = {"rpc.jsonl": "".join(json.dumps(r) + "\n" for r in log),
            "summary.json": json.dumps(summary, indent=1, default=str) + "\n"}
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
for name, text in payloads.items():
    (out / name).write_text(text)
    (Path("/var/tmp") / f"p3b-v6-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
print(json.dumps({"rows": len(log), "slowest": summary["slowest_call"], "async_by_method": async_by_method},
                 indent=1))
