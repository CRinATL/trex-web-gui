"""Record the P5 STF fixtures from the jammy guest: a daemon-started stateful (`-f`) run, the daemon's file
listings and the upload suitability gate (spec §13.9, D14).
Usage: uv run --project backend python -I -B vm/record_p5_stf.py <out_dir>   (via `make record-p5-stf`)
Phases: `vm/daemon.sh start` and `vm/mode.sh stop`; the listings; a stock run of cap2/sfr2.yaml (d 20) to
its end; a stopped run (d 60, stopped after 8 s Running); a start without `d`; the gate (G1 push/pull sha256
of the sfr2 pcaps and a 4 MiB pcap, G2 that push's seconds, G3 the listing, G4 a run of sfr2 rewritten to
the pushed absolute names, G5 the same with bare names). `finally` stops the daemon and puts TRex back on
the stateless stack.
Daemon lines: {"t", "method", "params", "http_status", "body"}; a push_file `bin_data` or a get_file result
over 64 KiB is logged as {"elided": true, "bytes": N, "sha256": hex} (no pcap bytes in the fixture). The
gate's verdict is not made here. Fixture files are written only after every phase has finished; a
UTC-stamped copy of each goes to /var/tmp (canon:OUT-1). Structure after vm/record_p5.py, whose
rpc/result/sh are copied (importing it would record). The guest is never saved, so the pushed files are
gone at the next boot. No hardware: the guest is a QEMU VM."""
import base64
import binascii
import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guestenv

SYNC_PORT, ASYNC_PORT = guestenv.ports()     # refuses before connecting if the guest is not selected
from trex_web_gui import stfyaml
from trex_web_gui.trexapi.loader import load

load()
import zmq
from trex.utils.zipmsg import ZippedMsg

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DAEMON_PORT = int(os.environ["TREX_VM_DAEMON_PORT"])   # exported by vm/guests.sh (vm/real.sh sources it)
USER = "trex-web-gui-record"
CFG = "/etc/trex_cfg.yaml"
STOCK = "cap2/sfr2.yaml"
ELIDE_OVER = 64 * 1024
IDLE, RUNNING = 1, 3   # trex_status_e.py
out = Path(sys.argv[1])
Z = ZippedMsg()
daemon_log: list[dict] = []
daemon_t0 = time.monotonic()
pub_msgs: list[dict] = []
phase = "off"
stop_collect = threading.Event()


def collect() -> None:
    """Thread body: keep the decoded PUB messages seen while phase is `g4`, until stopped."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://127.0.0.1:{ASYNC_PORT}")
    try:
        while not stop_collect.is_set():
            if sock.poll(200):
                raw = sock.recv()
                if phase != "g4":
                    continue
                try:
                    msg = json.loads((Z.decompress(raw) if Z.is_compressed(raw) else raw).decode())
                except (ValueError, zlib.error):
                    continue
                if isinstance(msg, dict):
                    pub_msgs.append(msg)
    finally:
        sock.close(linger=0)
        ctx.term()


# sh, rpc and result are copied from vm/record_p5.py (importing that module runs its recording); rpc differs
# only in the log-time elision of large file bodies.
def sh(*args: str) -> tuple[int, str]:
    """Run a vm script from the repo root in the foreground; returns (rc, stdout+stderr)."""
    p = subprocess.run(list(args), cwd=REPO, capture_output=True, text=True, check=False)
    print(f"[sh] {' '.join(args)} rc={p.returncode}\n{p.stdout}{p.stderr}", flush=True)
    return p.returncode, p.stdout + p.stderr


def elided(b64: object) -> object:
    """`b64` itself, or {"elided", "bytes", "sha256"} of its decoded bytes when it is a string over 64 KiB."""
    if not isinstance(b64, str) or len(b64) <= ELIDE_OVER:
        return b64
    try:
        data = binascii.a2b_base64(b64)
    except binascii.Error:
        return b64
    return {"elided": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def log_form(method: str, params: dict, body: object) -> tuple[dict, object]:
    """The (params, body) to log: push_file bin_data and get_file results over 64 KiB elided."""
    if method == "push_file" and "bin_data" in params:
        params = dict(params, bin_data=elided(params["bin_data"]))
    if method == "get_file" and isinstance(body, dict) and "result" in body:
        body = dict(body, result=elided(body["result"]))
    return params, body


def rpc(method: str, params=None, *, log: bool = True, timeout: float = 60):
    """One JSON-RPC 2.0 POST to the daemon with named params; logs and returns the parsed body."""
    params = {} if params is None else params
    req = urllib.request.Request(f"http://127.0.0.1:{DAEMON_PORT}/", method="POST",
                                 data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                  "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t = round(time.monotonic() - daemon_t0, 3)
    try:
        with opener.open(req, timeout=timeout) as r:
            status, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    try:
        body = json.loads(text)
    except ValueError:
        body = text
    print(f"[rpc] {method} {status} {str(body)[:150]}", flush=True)
    if log:
        lp, lb = log_form(method, params, body)
        daemon_log.append({"t": t, "method": method, "params": lp, "http_status": status, "body": lb})
    return body


def result(body):
    """The `result` of a JSON-RPC body; RuntimeError when the body is an error."""
    if not isinstance(body, dict) or "result" not in body:
        raise RuntimeError(f"unexpected daemon reply: {body}")
    return body["result"]


def file_bytes(path: str) -> bytes:
    """The bytes of a guest file read through the daemon's get_file."""
    return base64.b64decode(result(rpc("get_file", {"filepath": path})))


def push(name: str, data: bytes, timeout: float = 60) -> tuple[object, float]:
    """push_file `data` as `name`; returns (the reply body, seconds the call took)."""
    t = time.monotonic()
    try:
        params = {"filename": name, "bin_data": base64.b64encode(data).decode()}
        body = rpc("push_file", params, timeout=timeout)
    except OSError as e:   # a timeout or a dropped connection is a gate result, not a recorder failure
        body = {"exception": f"{type(e).__name__}: {e}"}
        print(f"[rpc] push_file {name} failed: {body['exception']}", flush=True)
    return body, round(time.monotonic() - t, 2)


def state_of() -> dict:
    """The daemon's {state, verbose} (not logged: the timeline carries it)."""
    return result(rpc("get_running_status", log=False))


def wait_idle(limit: float) -> float:
    """Seconds until get_running_status says Idle, or `limit`."""
    t = time.monotonic()
    while time.monotonic() - t < limit and state_of()["state"] != IDLE:
        time.sleep(0.5)
    return round(time.monotonic() - t, 2)


def stf_opts(profile: str, d: int | None) -> dict:
    """start_trex params for a stock-style STF run of `profile` (no `d` key when d is None)."""
    o: dict = {"cfg": CFG, "f": profile}
    if d is not None:
        o["d"] = d
    o.update({"m": 1.0, "l": 1000, "rx_check": 100, "c": 1})
    return {"trex_cmd_options": o, "user": USER, "stateless": False, "block_to_success": True, "timeout": 40}


def daemon_failure(why: str) -> RuntimeError:
    """Print the daemon's log tail, then the error that stops the recorder."""
    try:
        log = base64.b64decode(result(rpc("get_trex_daemon_log", log=False))).decode(errors="replace")
        print(f"[daemon log tail]\n{log[-4000:]}", flush=True)
    except Exception as e:
        print(f"[daemon log unreadable: {e}]", flush=True)
    return RuntimeError(why)


def run_stf(profile: str, d: int, *, stop_after: float | None = None) -> dict:
    """Start an STF run and follow it to Idle (or stop it after `stop_after` s Running); returns
    what was seen.
    RuntimeError when start_trex returns no seq or the run never reaches Running."""
    t0 = time.monotonic()
    body = rpc("start_trex", stf_opts(profile, d), timeout=45)
    start_s = round(time.monotonic() - t0, 2)
    if not isinstance(body, dict) or not isinstance(body.get("result"), int):
        raise daemon_failure(f"start_trex gave no seq: {body}")
    seq = body["result"]
    r: dict = {"seq": seq, "start_s": start_s, "timeline": [], "cmd": None}
    tr = time.monotonic()
    running_since = None
    stopped_at = None
    while time.monotonic() - tr < d + 40:
        st = state_of()
        now = round(time.monotonic() - tr, 2)
        r["timeline"].append({"t": now, "state": st["state"], "verbose": st["verbose"]})
        if st["state"] == RUNNING and running_since is None:
            running_since = time.monotonic()
            for pid, cmd in result(rpc("get_trex_cmds")):
                if "_t-rex-64" in cmd:
                    r["cmd"] = [pid, cmd]
        if st["state"] == IDLE:
            if running_since is None:
                raise daemon_failure(f"run {seq} is Idle and was never Running: {st['verbose']}")
            break
        if stop_after is not None and stopped_at is None and time.monotonic() - running_since >= stop_after:
            stopped_at = time.monotonic()
            r["stop_result"] = rpc("stop_trex", {"seq": seq})
        time.sleep(1)
    else:
        raise daemon_failure(f"run {seq} did not finish within {d + 40} s")
    assert running_since is not None   # the loop raises or breaks only after Running
    r["run_s"] = round(time.monotonic() - running_since, 2)
    if stopped_at is not None:
        r["stop_s"] = round(time.monotonic() - stopped_at, 2)
    r["verbose"] = state_of()["verbose"]
    return r


def full_command() -> str | None:
    """The last `TREX FULL COMMAND: ` line of the daemon's log, after the marker."""
    log = base64.b64decode(result(rpc("get_trex_daemon_log"))).decode(errors="replace")
    lines = [ln for ln in log.splitlines() if "TREX FULL COMMAND: " in ln]
    return lines[-1].split("TREX FULL COMMAND: ", 1)[1] if lines else None


def synth_pcap(min_bytes: int) -> bytes:
    """A classic pcap (magic 0xa1b2c3d4, linktype 1) of 1514-byte zero-payload Ethernet records,
    at least min_bytes long."""
    head = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    rec = struct.pack("<IIII", 0, 0, 1514, 1514) + bytes(1514)
    return head + rec * (-(-(min_bytes - len(head)) // len(rec)))


def sha(b: bytes) -> str:
    """Hex sha256."""
    return hashlib.sha256(b).hexdigest()


def stop_if_started(body: object) -> None:
    """If a start_trex reply carries a seq (the run began), stop it and wait for Idle."""
    if isinstance(body, dict) and isinstance(body.get("result"), int):
        rpc("stop_trex", {"seq": body["result"]})
        wait_idle(15)


def gate(summary: dict, sfr2_text: str, files_path: str) -> None:
    """G1-G5 (spec §13.9); each experiment's failure is recorded in gate.errors, never raised."""
    global phase
    g = summary["gate"]
    pushed: dict[str, str] = {}   # stock name -> pushed tg name
    rendered_names: dict[str, str] = {}
    try:   # G1, G2, G3
        items = []
        for old in stfyaml.templates(stfyaml.parse(sfr2_text)[0] or {}):
            data = file_bytes(old)
            items.append((old, data, stfyaml.tg_name(data, Path(old).stem, "pcap")))
        big = synth_pcap(4 * 1024 * 1024)
        items.append(("synth-4m", big, stfyaml.tg_name(big, "synth4m", "pcap")))
        for old, data, tg in items:
            body, secs = push(tg, data, timeout=30 if old == "synth-4m" else 60)
            ok = isinstance(body, dict) and body.get("result") is True
            if old == "synth-4m":
                g["g2_push_4m_s"] = secs if ok else None
            else:
                rendered_names[old] = f"{files_path}/{tg}"
            g["g1_detail"][tg] = {"pushed": ok, "sha_ok": False}
            if ok:
                g["g1_detail"][tg]["sha_ok"] = sha(file_bytes(f"{files_path}/{tg}")) == sha(data)
            pushed[old] = tg
        details = g["g1_detail"].values()
        g["g1_round_trip"] = bool(details) and all(d["pushed"] and d["sha_ok"] for d in details)
        listing = result(rpc("get_files_list", {"path": files_path}))
        g["g3_listed"] = all(tg in listing[1] for tg in pushed.values())
    except Exception as e:
        g["errors"].append(f"g1-g3: {type(e).__name__}: {e}")
    if len(rendered_names) != 3:
        g["errors"].append("g4/g5 skipped: the sfr2 pcaps were not all pushed")
        return
    try:   # G4
        text = stfyaml.rewrite(sfr2_text, rendered_names)
        ytg = stfyaml.tg_name(text.encode(), "sfr2", "yaml")
        body, _ = push(ytg, text.encode())
        if not (isinstance(body, dict) and body.get("result") is True):
            raise RuntimeError(f"push of {ytg} gave {body}")
        pub_msgs.clear()
        phase = "g4"
        try:
            r = run_stf(f"{files_path}/{ytg}", 20)
        finally:
            phase = "off"
        seen = [m["data"] for m in pub_msgs if m.get("name") == "template_info"]
        g["g4"] = {"seq": r["seq"], "templates": seen[-1] if seen else [], "outcome": r["verbose"],
                   "run_s": r["run_s"]}
    except Exception as e:
        g["errors"].append(f"g4: {type(e).__name__}: {e}")
        wait_idle(15)
    try:   # G5
        # bare basenames of the pushed names: relative to nothing TRex resolves from
        text = stfyaml.rewrite(sfr2_text, {old: pushed[old] for old in rendered_names})
        ytg = stfyaml.tg_name(text.encode(), "sfr2rel", "yaml")
        body, _ = push(ytg, text.encode())
        if not (isinstance(body, dict) and body.get("result") is True):
            raise RuntimeError(f"push of {ytg} gave {body}")
        reply = rpc("start_trex", stf_opts(f"{files_path}/{ytg}", 20), timeout=45)
        g["g5_relative"] = reply
        stop_if_started(reply)
    except Exception as e:
        g["errors"].append(f"g5: {type(e).__name__}: {e}")
        wait_idle(15)


summary: dict = {"recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "guest": "jammy",
                 "files_path": None, "listings": {}, "stf_cmd": None, "full_command_stf": None,
                 "start_s": None, "run_s": None, "timeline": [], "outcomes": {}, "stop_after_end": None,
                 "stop_s": None, "stop_result": None, "start_stf_no_d": None,
                 "gate": {"g1_round_trip": False, "g1_detail": {}, "g2_push_4m_s": None, "g3_listed": False,
                          "g4": {"seq": None, "templates": [], "outcome": None, "run_s": None},
                          "g5_relative": None, "errors": []}}
th = threading.Thread(target=collect, daemon=True)
th.start()
try:
    rc, text = sh("vm/daemon.sh", "start")
    if rc != 0:
        raise RuntimeError(f"daemon.sh start failed: {text}")
    rc, text = sh("vm/mode.sh", "stop")
    if rc != 0:
        raise RuntimeError(f"mode.sh stop failed: {text}")
    files = summary["files_path"] = result(rpc("get_files_path"))
    for key, path in (("cap2", "cap2"), ("avl", "avl"), ("files_path", files), ("etc", "/etc")):
        summary["listings"][key] = rpc("get_files_list", {"path": path})
    sfr2_text = base64.b64decode(result(rpc("get_file", {"filepath": STOCK}))).decode()
    # stock run to its end
    r = run_stf(STOCK, 20)
    summary.update(stf_cmd=r["cmd"], start_s=r["start_s"], run_s=r["run_s"], timeline=r["timeline"])
    summary["outcomes"]["finished"] = r["verbose"]
    summary["stop_after_end"] = rpc("stop_trex", {"seq": r["seq"]})
    rpc("get_trex_log")
    summary["full_command_stf"] = full_command()
    # stopped run
    r = run_stf(STOCK, 60, stop_after=8)
    summary["outcomes"]["stopped"] = r["verbose"]
    summary["stop_s"] = r["stop_s"]
    summary["stop_result"] = r["stop_result"]
    # start without d
    body = rpc("start_trex", stf_opts(STOCK, None), timeout=45)
    summary["start_stf_no_d"] = body
    stop_if_started(body)
    gate(summary, sfr2_text, files)
finally:
    stop_collect.set()
    th.join(5)
    sh("vm/daemon.sh", "stop")
    sh("vm/mode.sh", "stateless")

payloads = {"daemon.jsonl": "".join(json.dumps(r) + "\n" for r in daemon_log),
            "summary.json": json.dumps(summary, indent=1, default=str) + "\n"}
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
for name, text in payloads.items():
    (out / name).write_text(text)
    (Path("/var/tmp") / f"p5-stf-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
shown = ("start_s", "run_s", "stop_s", "full_command_stf", "start_stf_no_d", "gate")
print(json.dumps({k: summary[k] for k in shown}, indent=1, default=str))
