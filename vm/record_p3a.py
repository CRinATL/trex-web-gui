"""Record P3a stats fixtures from the real v3.08 guest
(the guest's RPC/PUB ports, 4501/4500 on jammy, forwarded by vm/launch.sh).
Usage: uv run --project backend python -I -B vm/record_p3a.py <out_dir>
Client "gui-a" acquires ports 0 and 1 and adds tests/fixtures/profiles/flow_latency.py (pg_id 5 flow stats at
1000 pps, pg_id 7 latency stats at 100 pps) on port 0. Per sample it sends get_active_pgids,
get_pgid_stats and get_utilization (all of which land in rpc.jsonl) and records the client's own
pgid_stats.get_stats(ids, relative=False) and get_util_stats() output (the oracle) in summary.json.
Conditions: idle; load (3 samples, 1 s apart); after_stop; re_add (remove_all_streams, same profile again).
During load it also measures the util `history` order and whether `last_max` depends on the reader, and a
second client "gui-b" that connects without acquiring reads the same RPCs (read-only case).
Structure after vm/record_p3.py. Fixture files are written only after every call has succeeded; a UTC-stamped
copy of each goes to /var/tmp (canon:OUT-1)."""
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
from trex.stl.api import STLClient, STLProfile
from trex.utils.zipmsg import ZippedMsg

PROFILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "profiles" / "flow_latency.py"
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
samples: list[dict] = []
marks: list[dict] = []


def mark(name):
    """Record the time of the named call in `marks` (written to summary.json) and print it."""
    marks.append({"t": round(time.time() - t0, 3), "call": name})
    print(f"[call] {name}", flush=True)


def read(c):
    """The three polled reads, in the order the GUI will send them;
    returns the oracle for the same instant."""
    active = c.get_active_pgids()
    ids = sorted(active["flow_stats"] + active["latency"])
    return {"active_pgids": active, "pgid_stats": c.pgid_stats.get_stats(ids, relative=False),
            "util_stats": c.get_util_stats()}


def ks(d):
    """Key list; the oracle mixes int pg_ids with str keys."""
    return sorted(d, key=str)


def sample(condition):
    """Append one `read(a)` oracle to `samples` with the times that bracket its RPCs in rpc.jsonl."""
    t_a = round(time.time() - t0, 3)
    o = read(a)
    samples.append({"condition": condition, "t_before": t_a, "t_after": round(time.time() - t0, 3),
                    "oracle": o})
    print(f"[sample] {condition}", flush=True)


def add_profile():
    """Load the flow_latency profile and add its streams to port 0 through client a."""
    prof = STLProfile.load_py(str(PROFILE), direction=0, port_id=0, tunables=[])
    a.add_streams(prof.get_streams(), ports=[0])


a.connect()
b.connect()
a.acquire(ports=[0, 1], force=True)
a.reset(ports=[0, 1])
time.sleep(1)
add_profile()
time.sleep(1)

mark("idle")
sample("idle")
time.sleep(1)
sample("idle")
mark("start_traffic")
a.start(ports=[0])
time.sleep(3.0)
for _ in range(3):
    sample("load")
    time.sleep(1.0)

mark("history_order")
h1 = a.get_util_stats()
time.sleep(1.0)
h2 = a.get_util_stats()
c1, c2 = h1["cpu"][0]["history"], h2["cpu"][0]["history"]
history = {"first": h1, "second_1s_later": h2,
           "old_head_at_new_index_1": c1[0] == c2[1],
           "old_tail_shifted_down": c1[:-1] == c2[1:], "old_head_shifted_up": c1[1:] == c2[:-1]}

mark("last_max_readers")
ids = [5, 7]
series = {"a_with_b": [], "b": [], "a_alone": []}
stop_b = threading.Event()


# thread body: client b reads the pg_id stats every 0.1 s until `stop_b` is set, logging pg_id 7's last_max
def b_loop():
    while not stop_b.is_set():
        r = b.pgid_stats.get_stats(ids, relative=False)
        series["b"].append(
            {"t": round(time.time() - t0, 3), "last_max": r["latency"][7]["latency"]["last_max"]})
        time.sleep(0.1)


tb = threading.Thread(target=b_loop, name="b-reader")
tb.start()
for _ in range(5):
    r = a.pgid_stats.get_stats(ids, relative=False)
    series["a_with_b"].append(
        {"t": round(time.time() - t0, 3), "last_max": r["latency"][7]["latency"]["last_max"]})
    time.sleep(1.0)
stop_b.set()
tb.join()
for _ in range(5):
    r = a.pgid_stats.get_stats(ids, relative=False)
    series["a_alone"].append(
        {"t": round(time.time() - t0, 3), "last_max": r["latency"][7]["latency"]["last_max"]})
    time.sleep(1.0)

mark("read_only_client")
read_only: dict = {"owned_ports": b.get_acquired_ports()}
for name, fn in (("get_active_pgids", lambda: b.get_active_pgids()),
                 ("get_pgid_stats", lambda: b.pgid_stats.get_stats(ids, relative=False)),
                 ("get_util_stats", lambda: b.get_util_stats())):
    try:
        read_only[name] = {"ok": True, "result": fn()}
    except Exception as e:
        read_only[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

mark("stop_traffic")
a.stop(ports=[0])
time.sleep(1)
sample("after_stop")
time.sleep(2)
sample("after_stop")
mark("re_add")
a.remove_all_streams(ports=[0])
add_profile()
time.sleep(1)
sample("re_add")
a.remove_all_streams(ports=[0])
a.release(ports=[0, 1])
a.disconnect()
b.disconnect()

by = {c: [s for s in samples if s["condition"] == c] for c in ("idle", "load", "after_stop", "re_add")}
last = by["load"][-1]["oracle"]["pgid_stats"]["flow_stats"]
rx_ports = sorted(p for p, v in last[5]["rx_pkts"].items() if p != "total" and v > 0)
fs = last[5]
summary = {
    "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "profile": str(PROFILE.name),
    "marks": marks, "samples": samples,
    "pgid_stats_keys": ks(last_o := by["load"][-1]["oracle"]["pgid_stats"]),
    "flow_stats_keys": ks(last_o["flow_stats"]), "flow_stats_pgid_keys": ks(fs),
    "flow_stats_pgid_key_order": list(fs),
    "latency_keys": ks(last_o["latency"]), "latency_pgid_keys": ks(last_o["latency"][7]),
    "latency_stats_keys": ks(last_o["latency"][7]["latency"]),
    "err_cntrs_keys": ks(last_o["latency"][7]["err_cntrs"]),
    "active_pgids_keys": sorted(by["load"][-1]["oracle"]["active_pgids"]),
    "util_keys": sorted(by["load"][-1]["oracle"]["util_stats"]),
    "ver_id_key_to_value_types": (type(next(iter(last_o["ver_id"]))).__name__ + " key -> "
                                  + type(next(iter(last_o["ver_id"].values()))).__name__ + " value"),
    "ver_id_shape": {str(k): v for k, v in last_o["ver_id"].items()},
    "ver_id_before_re_add": by["after_stop"][-1]["oracle"]["pgid_stats"]["ver_id"],
    "ver_id_after_re_add": by["re_add"][-1]["oracle"]["pgid_stats"]["ver_id"],
    "rx_ports_counting_flow_5_when_port0_transmits": rx_ports,
    "history": history, "last_max_series": series, "read_only_client": read_only,
}
assert rx_ports, f"no port's Rx counted pg_id 5 under load: {fs['rx_pkts']}"
payloads = {"rpc.jsonl": "".join(json.dumps(r) + "\n" for r in log),
            "summary.json": json.dumps(summary, indent=1, default=str) + "\n"}
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
for name, text in payloads.items():
    (out / name).write_text(text)
    (Path("/var/tmp") / f"p3a-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
print(json.dumps({"rows": len(log), "rx_ports": rx_ports, "ver_id": summary["ver_id_shape"],
                  "history": {k: v for k, v in history.items() if k.startswith("old_")}}, indent=1))
