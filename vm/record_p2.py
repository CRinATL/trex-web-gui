"""Record P2 fixtures from the real v3.08 guest
(the guest's RPC/PUB ports, 4501/4500 on jammy, forwarded by vm/launch.sh).
Usage: uv run --project backend python -I -B vm/record_p2.py <out_dir>
Two clients: A ("gui-a") is the GUI's role, B ("console-b") is trex-console's. Every JsonRpcClient
request/response pair is logged with its client's username (pattern from an earlier recording spike).
Fixture files are written only after every call has succeeded, so a failed run leaves no partial output."""
import json
import random
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
from trex.stl.api import IP, UDP, Ether, STLClient, STLPktBuilder, STLStream, STLTXCont
from trex.utils.zipmsg import ZippedMsg

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
b = STLClient(username="console-b", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
marks: list[dict] = []


def mark(name):
    """Record the time of the named call in `marks` (written to summary.json) and print it."""
    marks.append({"t": round(time.time() - t0, 3), "call": name})
    print(f"[call] {name}", flush=True)


def raw(c, method, params):
    """Send one raw RPC through c's own connection (so it is logged); returns its request id."""
    p = dict(params)
    p["api_h"] = c.conn.rpc.api_h
    rid = random.getrandbits(24)
    c.conn.rpc.send_msg(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": p}))
    return rid


negatives: dict[str, dict] = {}


def neg(key, c, method, params):
    """Send a raw request through `c` that must fail; its logged row goes in negatives[key]."""
    rid = raw(c, method, params)
    with lock:      # subscriber threads append concurrently, so select by the request id, not by position
        row = next(r for r in reversed(log) if isinstance(r["req"], dict) and r["req"].get("id") == rid)
    assert isinstance(row["resp"], dict) and "error" in row["resp"], (
        f"negative {key} did not fail: {row['resp']}")
    negatives[key] = row


a.connect()
b.connect()
a.reset(ports=[0, 1])
a.release(ports=[0, 1])
time.sleep(1)
mark("acquire")
b.acquire(ports=[0])
time.sleep(1)
neg("acquire_owned_no_force", a, "acquire",
    {"port_id": 0, "user": "gui-a", "session_id": a.ctx.session_id, "force": False})
mark("acquire_force")
a.acquire(ports=[0], force=True)
time.sleep(1)
a.acquire(ports=[1])
# late reader: subscribes after events have already fired, to tell per-session from global seq counters
c = STLClient(username="late-c", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
               verbose_level="none")
mark("late_connect")
c.connect()
time.sleep(1)
neg("release_wrong_handler", a, "release", {"port_id": 0, "handler": "bogus000"})
h0 = a.ports[0].handler
neg("start_no_streams", a, "start_traffic",
    {"handler": h0, "port_id": 0, "profile_id": "_", "mul": {"type": "raw", "value": 1.0, "op": "abs"},
     "duration": -1, "force": False, "core_mask": 1})
neg("command_wrong_handler", a, "start_traffic",
    {"handler": "bogus000", "port_id": 0, "profile_id": "_",
     "mul": {"type": "raw", "value": 1.0, "op": "abs"},
     "duration": -1, "force": False, "core_mask": 1})
# built inline: the stock profile files import trex_stl_lib, which is not on the vendored path
streams_to_add = [STLStream(packet=STLPktBuilder(pkt=Ether() / IP(src="16.0.0.1", dst="48.0.0.1")
                                                 / UDP(dport=12, sport=1025) / (b"x" * 86)),
                            mode=STLTXCont())]
mark("add_stream")
a.add_streams(streams_to_add, ports=[0])
time.sleep(1)
b.ports[0].sync_streams()     # a populated get_all_streams: every acquire above ran before any stream existed
a.ports[0].validate()
validate_result = a.ports[0].get_profile()
mark("start_traffic")
a.start(ports=[0], mult="10kpps")
time.sleep(1)
neg("remove_streams_while_tx", a, "remove_all_streams", {"handler": h0, "port_id": 0, "profile_id": "_"})
mark("update_traffic")
a.update(ports=[0], mult="20kpps")
time.sleep(1)
mark("pause_traffic")
a.pause(ports=[0])
time.sleep(1)
mark("resume_traffic")
a.resume(ports=[0])
time.sleep(1)
mark("stop_traffic")
a.stop(ports=[0])
time.sleep(1)
mark("set_port_attr")
a.set_port_attr(ports=[0], promiscuous=True)
time.sleep(1)
a.set_port_attr(ports=[0], promiscuous=False)
mark("service")
a.set_service_mode(ports=[0], enabled=True)
time.sleep(1)
a.set_service_mode(ports=[0], enabled=False)
mark("remove_all_streams")
a.remove_all_streams(ports=[0])
time.sleep(1)
real_h = a.conn.rpc.api_h
a.conn.rpc.api_h = "zzzzzzzz"
neg("api_h_mismatch", a, "get_port_status", {"port_id": 0, "block": False})
a.conn.rpc.api_h = real_h
mark("release")
a.release(ports=[0])
time.sleep(1)
a.release(ports=[1])
time.sleep(1)
a.disconnect()
b.disconnect()
c.disconnect()

def items(x):
    """`x` as a list: a batch stays as it is, a single message is wrapped."""
    return x if isinstance(x, list) else [x]


def pairs(r):
    """(request, response) items of a row, matched by JSON-RPC id (batches may reorder responses)."""
    by_id = {x.get("id"): x for x in items(r["resp"]) if isinstance(x, dict)}
    return [(q, by_id.get(q.get("id"))) for q in items(r["req"]) if isinstance(q, dict)]


events = [(r["t"], r["client"], e) for r in log
          for q, rs in pairs(r)
          if q.get("method") == "get_async_events"
          for e in ((rs or {}).get("result") or {}).get("data", [])]
# emits: event types the watching client received within 1 s after each marked call (gui-a for "acquire",
# else console-b)
emits = {}
for m in marks:
    watcher = "gui-a" if m["call"] == "acquire" else "console-b"
    emits[m["call"]] = [e["type"] for t, who, e in events
                        if who == watcher and m["t"] <= t <= m["t"] + 1.0 and e.get("name") == "trex-event"]
seqs = {}
for _, who, e in events:
    seqs.setdefault(who, []).append(e["seq"])
streams = next((rs for r in reversed(log) for q, rs in pairs(r) if q.get("method") == "get_all_streams"),
               None)
first_seq = {k: v[0] for k, v in seqs.items()}
assert "late-c" in first_seq, "late reader saw no events"
payloads = {
    "rpc.jsonl": "".join(json.dumps(r) + "\n" for r in log),
    "negatives.json": json.dumps(negatives, indent=1) + "\n",
    "emits.json": json.dumps(emits, indent=1) + "\n",
    "summary.json": json.dumps({
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "marks": marks,
        "seq_by_reader": {k: v[:20] for k, v in seqs.items()},
        "first_seq": first_seq,
        # a reader that joined after events fired still starts at 0
        "per_session_seq": first_seq["late-c"] == 0,
        "validate_result": validate_result, "get_all_streams_example": streams}, indent=1) + "\n",
}
out.mkdir(parents=True, exist_ok=True)
for name, text in payloads.items():     # every payload is built above, so a failure there leaves nothing
    (out / name).write_text(text)
print(json.dumps({"rows": len(log), "negatives": sorted(negatives), "emits": emits}, indent=1))
