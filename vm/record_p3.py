"""Record P3-min stats fixtures from the real v3.08 guest
(the guest's RPC/PUB ports, 4501/4500 on jammy, forwarded by vm/launch.sh).
Usage: uv run --project backend python -I -B vm/record_p3.py <out_dir>
One client ("gui-a") acquires ports 0 and 1, then samples STLClient.get_stats() (its own
[get_global_stats, get_port_stats x2] batch is what lands in rpc.jsonl) in three conditions: idle; port 0
transmitting udp_for_benchmarks.py at 10kpps for 5 s; after stop. summary.json holds the client's own
to_dict() output for the same instant (the `tui` source), the to_dict() key lists, and the Rx port mapping.
Structure after vm/record_p2.py. Fixture files are written only after every call has succeeded."""
import json
import os
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

PROFILE = (os.environ.get("TREX_SRC", os.path.expanduser("~/Projects/trex-rework/trex-core"))
           + "/scripts/stl/udp_for_benchmarks.py")
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
samples: list[dict] = []
marks: list[dict] = []


def mark(name):
    """Record the time of the named call in `marks` (written to summary.json) and print it."""
    marks.append({"t": round(time.time() - t0, 3), "call": name})
    print(f"[call] {name}", flush=True)


def sample(condition):
    """One get_stats() (sync_now: it sends the stats batch itself); logged t brackets
    the batch in rpc.jsonl."""
    t_a = round(time.time() - t0, 3)
    st = a.get_stats(ports=[0, 1])
    samples.append({"condition": condition, "t_before": t_a, "t_after": round(time.time() - t0, 3),
                    "stats": {str(k): v for k, v in st.items() if k in ("global", 0, 1)}})
    print(f"[sample] {condition}", flush=True)


a.connect()
a.acquire(ports=[0, 1], force=True)
a.reset(ports=[0, 1])
time.sleep(1)
prof = STLProfile.load_py(PROFILE, direction=0, port_id=0, tunables=[])
a.add_streams(prof.get_streams(), ports=[0])
time.sleep(1)

mark("idle")
sample("idle")
time.sleep(1)
sample("idle")
mark("start_traffic")
a.start(ports=[0], mult="10kpps")
time.sleep(2.0)
for _ in range(3):
    sample("load")
    time.sleep(1.0)
mark("stop_traffic")
a.stop(ports=[0])
time.sleep(1)
sample("after_stop")
time.sleep(2)
sample("after_stop")
a.remove_all_streams(ports=[0])
a.release(ports=[0, 1])
a.disconnect()

idle, load = ([s for s in samples if s["condition"] == "idle"],
              [s for s in samples if s["condition"] == "load"])
rx_delta = {p: load[-1]["stats"][str(p)]["ipackets"] - idle[-1]["stats"][str(p)]["ipackets"] for p in (0, 1)}
tx_delta = {p: load[-1]["stats"][str(p)]["opackets"] - idle[-1]["stats"][str(p)]["opackets"] for p in (0, 1)}
rx_advancing = sorted(p for p, d in rx_delta.items() if d > 0)
summary = {
    "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "profile": PROFILE, "mult": "10kpps",
    "marks": marks, "samples": samples,
    "global_keys": sorted(samples[0]["stats"]["global"]), "port_keys": sorted(samples[0]["stats"]["0"]),
    "port_key_order": list(samples[0]["stats"]["0"]), "global_key_order": list(samples[0]["stats"]["global"]),
    "tx_delta_idle_to_last_load": tx_delta, "rx_delta_idle_to_last_load": rx_delta,
    "rx_advancing_ports_when_port0_transmits": rx_advancing,
}
assert rx_advancing, f"no port's Rx advanced under load: {rx_delta}"
payloads = {"rpc.jsonl": "".join(json.dumps(r) + "\n" for r in log),
            "summary.json": json.dumps(summary, indent=1) + "\n"}
out.mkdir(parents=True, exist_ok=True)
for name, text in payloads.items():
    (out / name).write_text(text)
print(json.dumps({"rows": len(log), "rx_delta": rx_delta, "tx_delta": tx_delta,
                  "global_keys": summary["global_keys"], "port_keys": summary["port_keys"]}, indent=1))
