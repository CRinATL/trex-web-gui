"""Record the P5 fixtures from the jammy guest: PUB frames of the snapshot TRex (idle, then a 10 kpps UDP
stream), the one stateful attempt, and the daemon's JSON-RPC exchanges.
Usage: uv run --project backend python -I -B vm/record_p5.py <out_dir>   (via `make record-p5`, jammy only)
Phase STL: 10 s idle, then client "gui-rec" acquires port 0, loads
tests/fixtures/profiles/udp_for_benchmarks.py, starts it at 10kpps for 10 s more, stops and releases.
Phase stateful: `vm/mode.sh stateful` (one attempt); on rc 0 30 s of frames, otherwise `stateful =
false` and `stateful_error`. Either way `vm/mode.sh stateless` follows.
Phase daemon: `vm/daemon.sh start`, reads, faults, a foreign start beside the snapshot TRex, two pushed
configs, then a stop/start cycle through the daemon. `finally` stops the daemon and puts TRex back on
the stateless stack.
Frame lines: {"t", "frame" (base64 of the raw ZMQ message), "phase"}; daemon lines: {"t", "method",
"params", "http_status", "body"}. Fixture files are written only after every phase has finished; a
UTC-stamped copy of each goes to /var/tmp (canon:OUT-1). Structure after vm/record_p3c.py. No hardware: the
guest is a QEMU VM."""
import base64
import json
import os
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
from trex_web_gui.trexapi.loader import load

load()
import zmq
from trex.stl.api import STLClient, STLProfile
from trex.utils.zipmsg import ZippedMsg

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BENCH = REPO / "tests" / "fixtures" / "profiles" / "udp_for_benchmarks.py"
GUEST_CFG = (HERE / "guest" / "trex_cfg.yaml").read_text()
DAEMON_PORT = int(os.environ["TREX_VM_DAEMON_PORT"])   # exported by vm/guests.sh (vm/real.sh sources it)
USER = "trex-web-gui-record"
out = Path(sys.argv[1])
Z = ZippedMsg()
frames: list[tuple[float, bytes, str]] = []
phase = "idle"
stop_collect = threading.Event()
daemon_log: list[dict] = []
daemon_t0 = time.monotonic()


def collect() -> None:
    """Thread body: append (monotonic time, raw message, phase) for each PUB message until stopped."""
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.setsockopt(zmq.LINGER, 0)
    sock.connect(f"tcp://127.0.0.1:{ASYNC_PORT}")
    try:
        while not stop_collect.is_set():
            if sock.poll(200):
                frames.append((time.monotonic(), sock.recv(), phase))
    finally:
        sock.close(linger=0)
        ctx.term()


def sh(*args: str) -> tuple[int, str]:
    """Run a vm script from the repo root in the foreground; returns (rc, stdout+stderr)."""
    p = subprocess.run(list(args), cwd=REPO, capture_output=True, text=True, check=False)
    print(f"[sh] {' '.join(args)} rc={p.returncode}\n{p.stdout}{p.stderr}", flush=True)
    return p.returncode, p.stdout + p.stderr


def body_of(raw: bytes):
    """The JSON value in one PUB message (zipped or plain); None when it is not JSON."""
    try:
        return json.loads((Z.decompress(raw) if Z.is_compressed(raw) else raw).decode())
    except (ValueError, zlib.error):   # not JSON, or a corrupt zipped body
        return None


def frame_lines(wanted: set[str]) -> list[dict]:
    """The recorded frames of the given phases as fixture lines, t since the phase's first frame."""
    lines, first = [], {}
    for t, raw, ph in frames:
        if ph in wanted:
            first.setdefault(ph, t)
            lines.append({"t": round(t - first[ph], 4), "frame": base64.b64encode(raw).decode(), "phase": ph})
    return lines


def names_and_triggers(lines: list[dict]) -> tuple[dict, int]:
    """({name: count}, number of `{}` triggers) over fixture frame lines."""
    counts: dict[str, int] = {}
    triggers = 0
    for ln in lines:
        msg = body_of(base64.b64decode(ln["frame"]))
        if msg == {}:
            triggers += 1
        elif isinstance(msg, dict) and isinstance(msg.get("name"), str):
            counts[msg["name"]] = counts.get(msg["name"], 0) + 1
    return counts, triggers


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
        daemon_log.append({"t": t, "method": method, "params": params, "http_status": status, "body": body})
    return body


def result(body):
    """The `result` of a JSON-RPC body; RuntimeError when the body is an error."""
    if not isinstance(body, dict) or "result" not in body:
        raise RuntimeError(f"unexpected daemon reply: {body}")
    return body["result"]


def _false(body) -> bool:
    """True when a daemon reply is a JSON object whose `result` is false."""
    return isinstance(body, dict) and body.get("result") is False


def start_opts(cfg: str, timeout: int = 40) -> dict:
    """start_trex params for a stateless run of `cfg`, as the Java GUI sends them (named)."""
    return {"trex_cmd_options": {"cfg": cfg}, "user": USER, "stateless": True, "block_to_success": True,
            "timeout": timeout, "trex_args": "--no-scapy-server"}


def stl_phase() -> None:
    """Idle 10 s, then 10 kpps from port 0 for 10 s (phase tags `idle`, `traffic`)."""
    global phase
    time.sleep(10)
    c = STLClient(username="gui-rec", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
                  verbose_level="none")
    c.connect()
    try:
        c.acquire(ports=[0], force=True)
        prof = STLProfile.load_py(str(BENCH), direction=0, port_id=0)
        c.remove_all_streams(ports=[0])
        c.add_streams(prof.get_streams(), ports=[0])
        phase = "traffic"
        c.start(ports=[0], mult="10kpps", force=True)
        time.sleep(10)
    finally:
        try:
            c.stop(ports=[0])
            c.remove_all_streams(ports=[0])
            c.release(ports=[0])
        except Exception as e:
            print(f"[restore] stop/release failed: {e}", flush=True)
        c.disconnect()
        phase = "idle"


def daemon_phase(summary: dict) -> None:
    """Reads, faults, the foreign start, pushes and the stop/start cycle; fills summary and daemon_log."""
    rc, text = sh("vm/daemon.sh", "start")
    if rc != 0:
        raise RuntimeError(f"daemon.sh start failed: {text}")
    faults = summary["faults"]
    for m in ("connectivity_check", "get_trex_version", "get_trex_config_metadata", "get_devices_info",
              "get_trex_config", "get_files_path", "is_running", "get_running_status", "is_reserved",
              "get_trex_cmds", "get_trex_daemon_log"):
        body = rpc(m)
        if m == "get_files_path":
            summary["files_path"] = result(body)
    files = summary["files_path"]
    faults["get_file"] = rpc("get_file", {"filepath": "/etc/shadow"})
    faults["stop_wrong_seq_idle"] = rpc("stop_trex", {"seq": 999})
    faults["unknown_method"] = rpc("no_such_method")
    faults["foreign_start"] = rpc("start_trex", start_opts("/etc/trex_cfg.yaml", 20))
    for name, text in (("trex-web-gui-record.yaml", GUEST_CFG),
                       ("trex-web-gui-bad.yaml",
                        GUEST_CFG.replace("['veth0', 'veth1']", "['nope0', 'nope1']"))):
        rpc("push_file", {"filename": name, "bin_data": base64.b64encode(text.encode()).decode()})
    # the cycle
    sh("vm/mode.sh", "stop")
    for _ in range(20):   # the daemon notices its TRex is gone
        if _false(rpc("is_running", log=False)):
            break
        time.sleep(0.5)
    faults["start_bad_cfg"] = rpc("start_trex", start_opts(f"{files}/trex-web-gui-bad.yaml"))
    seq = result(rpc("start_trex", start_opts(f"{files}/trex-web-gui-record.yaml")))
    summary["seq"] = seq
    rpc("is_running")
    rpc("get_running_status")
    rpc("get_trex_cmds")
    c = STLClient(username="gui-rec", server="127.0.0.1", sync_port=SYNC_PORT, async_port=ASYNC_PORT,
                  verbose_level="none")
    c.connect()
    try:
        summary["started_version"] = c.get_server_version()["version"]
    finally:
        c.disconnect()
    faults["start_twice"] = rpc("start_trex", start_opts(f"{files}/trex-web-gui-record.yaml"))
    faults["stop_wrong_seq_running"] = rpc("stop_trex", {"seq": seq + 1})
    rpc("get_trex_log")
    rpc("stop_trex", {"seq": seq})
    t = time.monotonic()
    while time.monotonic() - t < 10:
        if _false(rpc("is_running")):
            break
        time.sleep(0.5)
    summary["stop_s"] = round(time.monotonic() - t, 2)
    rpc("force_trex_kill")


summary: dict = {"recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "guest": "jammy",
                 "stateful": False, "stateful_error": None, "stateful_source": "samples",
                 "template_names": [],
                 "files_path": None, "started_version": None, "stop_s": None, "seq": None,
                 "faults": dict.fromkeys(("get_file", "stop_wrong_seq_idle", "stop_wrong_seq_running",
                                          "start_twice",
                                          "start_bad_cfg", "unknown_method", "foreign_start"))}
th = threading.Thread(target=collect, daemon=True)
th.start()
try:
    stl_phase()
    phase = "off"   # the switch window: the STL TRex's last frames belong to no fixture
    rc, text = sh("vm/mode.sh", "stateful")
    if rc == 0:
        phase = "stateful"
        summary["stateful"], summary["stateful_source"] = True, "recording"
        time.sleep(30)
    else:
        summary["stateful_error"] = text
    phase = "off"
    sh("vm/mode.sh", "stateless")
    time.sleep(3)
    daemon_phase(summary)
finally:
    stop_collect.set()
    th.join(5)
    sh("vm/daemon.sh", "stop")
    sh("vm/mode.sh", "stateless")

stl_lines = frame_lines({"idle", "traffic"})
st_lines = frame_lines({"stateful"})
stl_names, stl_trig = names_and_triggers(stl_lines)
st_names, st_trig = names_and_triggers(st_lines)
for ln in st_lines:
    msg = body_of(base64.b64decode(ln["frame"]))
    if isinstance(msg, dict) and msg.get("name") == "template_info":
        summary["template_names"] = msg["data"]
summary.update({"names": {"stl": stl_names, "stateful": st_names},
                "triggers": {"stl": stl_trig, "stateful": st_trig}})
payloads = {"pub-stl.jsonl": "".join(json.dumps(r) + "\n" for r in stl_lines),
            "daemon.jsonl": "".join(json.dumps(r) + "\n" for r in daemon_log),
            "summary.json": json.dumps(summary, indent=1, default=str) + "\n"}
if summary["stateful"]:
    payloads["pub-stateful.jsonl"] = "".join(json.dumps(r) + "\n" for r in st_lines)
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%Y-%m-%d-%H%M%SZ", time.gmtime())
for name, text in payloads.items():
    (out / name).write_text(text)
    (Path("/var/tmp") / f"p5-{Path(name).stem}-{stamp}{Path(name).suffix}").write_text(text)
print(json.dumps({k: summary[k] for k in ("stateful", "stateful_source", "started_version", "stop_s", "seq")}
                 | {"faults": summary["faults"], "names": summary["names"]}, indent=1, default=str))
