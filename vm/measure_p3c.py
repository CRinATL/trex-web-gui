"""P3c Task 10 Step 3: the capture monitor at 1500 B frames, 100 and 1000 pps, 30 s each,
against the real guest.

The backend is a child process running the real app (create_app + uvicorn, the built frontend), with one
addition: the RPC tap the session installs (trexapi/client.py _install_rpc_tap -> verbose_tap) is wrapped with
a per-call thread-CPU counter, and every `capture fetch` request it sees is time-stamped. The child writes
those counters to a JSON file every 0.2 s. This parent drives the API over HTTP with the login cookie,
subscribes to /api/ws like a second browser tab, reads the child's CPU from /proc/<pid>/stat and
`ps -o times=`, and drives headless Chrome over CDP on the Capture page with the P3a method (cdp_measure.py of
the P3a workspace: busy fraction from Performance.getMetrics TaskDuration, rAF frames, long tasks, per-message
handling time).

Usage: backend/.venv/bin/python -I -B vm/measure_p3c.py <scratch dir>   (guest up; ports 4501/4500 on jammy)
       for a guest other than jammy first run eval "$(TREX_VM_GUEST=<guest> vm/guests.sh)"
"""
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import websockets

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guestenv

SYNC_PORT, ASYNC_PORT = guestenv.ports()
BENCH = REPO / "tests/fixtures/profiles/udp_for_benchmarks.py"
PORT, CDP_PORT = 18791, 9341
GUI = f"http://127.0.0.1:{PORT}"
SCRATCH = Path(sys.argv[1])
SECONDS = 30
WARM_S = 3
MON_LIMIT = 100                    # the monitor's default limit (spec bounds), what a user gets
TICK = os.sysconf("SC_CLK_TCK")

CHILD = r"""
import json, os, sys, threading, time
sys.path.insert(0, %(backend)r)
from trex_web_gui.trexapi import client
STATS = {"tap_calls": 0, "tap_cpu_s": 0.0, "tap_wall_s": 0.0, "fetch_req_ts": [], "fetch_reply_max_chars": 0}
lock = threading.Lock()
orig = client._install_rpc_tap
def wrapped(c, on_rpc):
    orig(c, on_rpc)
    rpc = c.conn.rpc
    inner = rpc.verbose_msg
    pending = {"fetch": False}
    def timed(msg):
        t, w = time.thread_time(), time.perf_counter()
        inner(msg)
        dt, dw = time.thread_time() - t, time.perf_counter() - w
        with lock:
            STATS["tap_calls"] += 1; STATS["tap_cpu_s"] += dt; STATS["tap_wall_s"] += dw
            if msg.startswith("Sending Request") and '"fetch"' in msg:
                STATS["fetch_req_ts"].append(time.time())
                pending["fetch"] = True
            elif msg.startswith("Server Response") and pending["fetch"] and '"pkts"' in msg:
                STATS["fetch_reply_max_chars"] = max(STATS["fetch_reply_max_chars"], len(msg))
                pending["fetch"] = False
    rpc.verbose_msg = timed
client._install_rpc_tap = wrapped
def dump():
    while True:
        with lock:
            text = json.dumps(STATS)
        tmp = %(stats)r + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, %(stats)r)
        time.sleep(0.2)
threading.Thread(target=dump, daemon=True).start()
import uvicorn
from pathlib import Path
from trex_web_gui.api.app import create_app
token = sys.stdin.readline().strip()
app = create_app(Path(%(data)r), "127.0.0.1", Path(%(static)r), origin_ports=frozenset({%(port)d}), token=token)
uvicorn.run(app, host="127.0.0.1", port=%(port)d, log_level="warning")
"""  # noqa: E501  (Python source embedded as data)

WRAP = r"""
(() => {
  const W = window.WebSocket;
  window.__msg = [];
  window.WebSocket = function (...a) {
    const ws = new W(...a);
    const add = ws.addEventListener.bind(ws);
    const time = (fn) => function (ev) {
      const t0 = performance.now();
      const r = fn.call(this, ev);
      const ch = new MessageChannel();
      ch.port1.onmessage = () => { window.__msg.push([performance.now() - t0, ev.data.length]); };
      ch.port2.postMessage(0);
      return r;
    };
    ws.addEventListener = (type, fn, opts) => add(type, type === 'message' ? time(fn) : fn, opts);
    let om = null;
    Object.defineProperty(ws, 'onmessage', { get: () => om, set: (fn) => { om = fn; add('message', time(fn)); } });
    return ws;
  };
  window.WebSocket.prototype = W.prototype;
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
})();
"""  # noqa: E501  (JS source embedded as data)

MEASURE = r"""
(async (secs) => {
  const lt = []; const po = new PerformanceObserver((l) => l.getEntries().forEach((e) => lt.push(e.duration)));
  po.observe({ type: 'longtask', buffered: false });
  window.__msg = [];
  let frames = 0, run = true; const raf = () => { frames++; if (run) requestAnimationFrame(raf); }; requestAnimationFrame(raf);
  const h0 = performance.memory.usedJSHeapSize;
  await new Promise((r) => setTimeout(r, secs * 1000));
  run = false; po.disconnect();
  const m = window.__msg.map((x) => x[0]).sort((a, b) => a - b);
  const big = window.__msg.filter((x) => x[1] > 50000).map((x) => x[0]).sort((a, b) => a - b);
  const q = (a, p) => a.length ? +a[Math.min(a.length - 1, Math.floor(p * a.length))].toFixed(1) : null;
  return JSON.stringify({ fps: +(frames / secs).toFixed(1), longtasks: lt.length, longtask_max_ms: lt.length ? +Math.max(...lt).toFixed(1) : 0,
    msgs: m.length, msg_p50_ms: q(m, 0.5), msg_p95_ms: q(m, 0.95), msg_max_ms: q(m, 1),
    big_msgs: big.length, big_p50_ms: q(big, 0.5), big_p95_ms: q(big, 0.95), big_max_ms: q(big, 1),
    heap_mb: [+(h0 / 1e6).toFixed(1), +(performance.memory.usedJSHeapSize / 1e6).toFixed(1)] });
})
"""  # noqa: E501  (JS source embedded as data)


class Page:
    def __init__(self, ws):
        """Wrap a CDP websocket: `n` numbers commands, `waiters` maps a command id to its pending future."""
        self.ws, self.n, self.waiters = ws, 0, {}

    async def reader(self):
        """Read replies until the socket closes, resolving each id's waiter; id-less events are dropped."""
        async for raw in self.ws:
            msg = json.loads(raw)
            fut = self.waiters.pop(msg.get("id"), None)
            if fut is not None:
                fut.set_result(msg)

    async def call(self, method, **params):
        """Send a CDP command and wait for its reply (`reader` must be running); RuntimeError on error."""
        self.n += 1
        mid = self.n
        fut = asyncio.get_running_loop().create_future()
        self.waiters[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        msg = await fut
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def js(self, expr, timeout_s=30):
        """Evaluate `expr` in the page, awaiting a promise; its value, RuntimeError if it threw."""
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True,
                            timeout=timeout_s * 1000)
        if "exceptionDetails" in r:
            raise RuntimeError(str(r["exceptionDetails"]))
        return r["result"].get("value")

    async def task_seconds(self):
        """(TaskDuration, Timestamp) in seconds from Performance.getMetrics, for the busy share."""
        d = {m["name"]: m["value"] for m in (await self.call("Performance.getMetrics"))["metrics"]}
        return d["TaskDuration"], d["Timestamp"]


def proc_cpu_s(pid):
    """CPU seconds (user + system, not children) the process has used, from /proc/<pid>/stat."""
    f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TICK          # utime + stime (fields 14, 15)


def ps_times(pid):
    """Cumulative CPU time of the process as `ps -o times=` reports it, in whole seconds."""
    return int(subprocess.run(["ps", "-o", "times=", "-p", str(pid)], capture_output=True, text=True).stdout)


def child_stats(path):
    """The counters the child last wrote to its JSON stats file."""
    return json.loads(path.read_text())


def q(a, p):
    """The value at index floor(p * n) of the sorted `a` (clamped to the last), 4 places; None when empty."""
    a = sorted(a)
    return round(a[min(len(a) - 1, int(p * len(a)))], 4) if a else None


async def main():
    """Start the child backend and headless Chrome and measure at each pps on the real guest: connects,
    force-acquires ports 0 and 1, sets port 1 service mode, starts and stops traffic on port 0, disconnects.
    Writes measure-results.json under SCRATCH; both processes are always stopped."""
    data, stats = SCRATCH / "data", SCRATCH / "child-stats.json"
    data.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    code = CHILD % {"backend": str(REPO / "backend"), "stats": str(stats), "data": str(data),
                    "static": str(REPO / "frontend/dist"), "port": PORT}
    log = open(SCRATCH / "child.log", "w")
    child = subprocess.Popen([sys.executable, "-B", "-c", code], stdin=subprocess.PIPE, stdout=log,
                             stderr=subprocess.STDOUT, text=True, cwd=str(REPO))
    child.stdin.write(token + "\n")
    child.stdin.flush()
    chrome = None
    try:
        async with httpx.AsyncClient(base_url=GUI, timeout=60) as h:
            for _ in range(100):
                try:
                    if (await h.get("/api/auth/status")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            r = await h.get("/api/auth/login", params={"token": token}, follow_redirects=False)
            assert r.status_code in (302, 303, 307), r.status_code

            # one API call: prints it, requires HTTP 200, returns the JSON body
            async def post(path, body=None, method="POST"):
                rr = await h.request(method, path, json=body)
                print(f"{method} {path} -> {rr.status_code} {rr.text[:100]}", flush=True)
                assert rr.status_code == 200, rr.text
                return rr.json()

            await post("/api/session/connect",
                       {"host": "127.0.0.1", "sync_port": SYNC_PORT, "async_port": ASYNC_PORT})
            await post("/api/ports/acquire", {"ports": [0, 1], "force": True})
            await post("/api/ports/service", {"ports": [1], "enabled": True})
            await post("/api/ports/assign",
                       {"ports": [0], "path": str(BENCH), "tunables": ["--packet_len", "1500"]})

            prof = SCRATCH / "chrome"
            shutil.rmtree(prof, ignore_errors=True)
            chrome = subprocess.Popen(["google-chrome", "--headless=new",
                                       f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={prof}",
                                       "--no-first-run", "--no-default-browser-check", "about:blank"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            target = None
            for _ in range(100):
                try:
                    target = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
                                  if t["type"] == "page")
                    break
                except Exception:
                    await asyncio.sleep(0.2)
            version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version"))["Browser"]
            cookie = h.cookies
            hdr = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookie.items())}
            ws_msgs = []

            # a second tab: appends (time, type, size, row count, monitor) per /api/ws message until cancelled
            async def subscribe():
                async with websockets.connect(f"ws://127.0.0.1:{PORT}/api/ws", additional_headers=hdr,
                                              max_size=None) as ws:
                    async for raw in ws:
                        m = json.loads(raw)
                        ws_msgs.append((time.time(), m.get("type"), len(raw),
                                        len(m.get("rows", [])), m.get("monitor")))
            sub = asyncio.create_task(subscribe())

            async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as cws:
                p = Page(cws)
                rd = asyncio.create_task(p.reader())
                await p.call("Page.enable")
                await p.call("Performance.enable")
                await p.call("Page.addScriptToEvaluateOnNewDocument", source=WRAP)
                await p.call("Page.navigate", url=f"{GUI}/api/auth/login?token={token}")
                await asyncio.sleep(3)
                await p.call("Page.navigate", url=f"{GUI}/capture")
                await asyncio.sleep(4)
                results = {"chrome": version, "monitor_limit": MON_LIMIT, "seconds": SECONDS, "pps": {}}
                for pps in (100, 1000):
                    await post("/api/ports/start", {"ports": [0], "mult": f"{pps}pps"})
                    await post("/api/capture/monitor",
                               {"tx": [], "rx": [1], "bpf": "udp", "limit": MON_LIMIT})
                    await asyncio.sleep(WARM_S)
                    rows_visible = await p.js("document.querySelectorAll('table tbody tr').length")
                    t0 = time.time()
                    c0, ps0, s0 = proc_cpu_s(child.pid), ps_times(child.pid), child_stats(stats)
                    k0, w0 = await p.task_seconds()
                    browser = json.loads(await p.js(f"({MEASURE})({SECONDS})", timeout_s=SECONDS + 30))
                    k1, w1 = await p.task_seconds()
                    t1 = time.time()
                    c1, ps1, s1 = proc_cpu_s(child.pid), ps_times(child.pid), child_stats(stats)
                    browser["main_thread_busy_pct"] = round(100 * (k1 - k0) / (w1 - w0), 1)
                    browser["table_rows_at_start"] = rows_visible
                    win = [m for m in ws_msgs if t0 <= m[0] <= t1]
                    pk = [m for m in win if m[1] == "capture_pkts"]
                    st = [m for m in win if m[1] == "capture" and m[4]]
                    fts = [t for t in s1["fetch_req_ts"] if t0 <= t <= t1]
                    gaps = [b - a for a, b in zip(fts, fts[1:], strict=False)]
                    pgaps = [b[0] - a[0] for a, b in zip(pk, pk[1:], strict=False)]
                    m0, m1 = st[0][4], st[-1][4]
                    span = st[-1][0] - st[0][0]
                    cpu = c1 - c0
                    tap = s1["tap_cpu_s"] - s0["tap_cpu_s"]
                    res = {
                        "window_s": round(t1 - t0, 2),
                        "fetch_rpcs": len(fts), "fetch_hz": round(len(fts) / (t1 - t0), 3),
                        "fetch_gap_s": {"p50": q(gaps, .5), "p95": q(gaps, .95), "max": q(gaps, 1),
                                        "min": q(gaps, 0)},
                        "capture_pkts_msgs": len(pk), "rows": sum(m[3] for m in pk),
                        "capture_pkts_gap_s": {"p50": q(pgaps, .5), "p95": q(pgaps, .95), "max": q(pgaps, 1)},
                        "capture_pkts_json_bytes_max": max((m[2] for m in pk), default=0),
                        "capture_pkts_json_bytes_p50": q([m[2] for m in pk], .5),
                        "fetch_reply_chars_max_whole_run": s1["fetch_reply_max_chars"],
                        "monitor_first": {k: m0[k] for k in ("id", "matched", "fetched", "count")},
                        "monitor_last": {k: m1[k] for k in ("id", "matched", "fetched", "count")},
                        "matched_minus_fetched": [m0["matched"] - m0["fetched"],
                                                  m1["matched"] - m1["fetched"]],
                        "matched_minus_fetched_growth_per_s": round(
                            ((m1["matched"] - m1["fetched"]) - (m0["matched"] - m0["fetched"])) / span, 1)
                        if span > 0 else None,
                        "backend_cpu_s": round(cpu, 2),
                        "backend_cpu_pct_of_one_core": round(100 * cpu / (t1 - t0), 1),
                        "ps_times_s": [ps0, ps1],
                        "tap_calls": s1["tap_calls"] - s0["tap_calls"], "tap_cpu_s": round(tap, 3),
                        "tap_wall_s": round(s1["tap_wall_s"] - s0["tap_wall_s"], 3),
                        "tap_share_of_backend_cpu_pct": round(100 * tap / cpu, 1) if cpu else None,
                        "browser": browser}
                    results["pps"][str(pps)] = res
                    print(f"{pps}pps {json.dumps(res)}", flush=True)
                    await post("/api/capture/monitor", method="DELETE")
                    await post("/api/ports/stop", {"ports": [0]})
                    await asyncio.sleep(1)
                rd.cancel()
            sub.cancel()
            await post("/api/ports/service", {"ports": [1], "enabled": False})
            await post("/api/session/disconnect", {})
            out = SCRATCH / "measure-results.json"
            out.write_text(json.dumps(results, indent=1))
            print(f"RESULTS {json.dumps(results)}", flush=True)
    finally:
        if chrome is not None:
            chrome.terminate()
            try:
                chrome.wait(10)
            except subprocess.TimeoutExpired:
                chrome.kill()
        child.terminate()
        try:
            child.wait(20)
        except subprocess.TimeoutExpired:
            child.kill()
        log.close()


asyncio.run(main())
