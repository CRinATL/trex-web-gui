"""A fake TRex daemon (trex_server.py, JSON-RPC over HTTP): `fake_daemon.py <port> [flags]`.

It is a ThreadingHTTPServer so that one hung or dripping request does not stop the others. The real daemon
is a single-threaded SimpleJSONRPCServer (trex_server.py), which would serialise them: a test that needs to
see the Control session survive a hung daemon must not rely on this fake's concurrency being the daemon's.

Every body it answers is a recorded one: tests/fixtures/p5/daemon.jsonl and summary.json (faults) and
tests/fixtures/p5-stf/ (STF run, listings, outcomes). It keeps the state Idle -> Starting -> Running, a seq
counter (stop_trex answers False while idle and -33 for a wrong seq while running) and a temp dir that
push_file writes into. It prints one `[fake-daemon] method <name>` line per request, so a test can read the
log of what was called, `push <name>` for each push_file (before it is judged), and `hang <method>` when a
--hang request starts to stall.

Flags: --foreign (lists a recorded `_t-rex-64 -i` process), --foreign-stf (lists a recorded STF command this
fake did not start), --reserved, --start-fails, --stf-fails, --stf-scale F (an STF run lasts d*F seconds),
--push-false (push_file answers false and writes nothing), --list-hides-pushed (get_files_list omits what
push_file wrote),
--hang METHOD (sleep 30 s), --drip METHOD (one byte per second), --huge METHOD (a 9 MiB reply), --redirect
(302 to every call), --stop-keeps-running (stop_trex says True but the run goes on)."""
import argparse
import base64
import json
import shlex
import signal
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIX = Path(__file__).resolve().parent.parent / "fixtures"
IDLE, STARTING, RUNNING = 1, 2, 3
HUGE_BYTES = 9 * 1024 * 1024
HANG_S = 30
START_S = 0.05


def rows(fixture: str) -> list[dict[str, Any]]:
    """The recorded request/response rows of one fixture dir's daemon.jsonl."""
    text = (FIX / fixture / "daemon.jsonl").read_text()
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def summary(fixture: str) -> dict[str, Any]:
    """One fixture dir's summary.json."""
    parsed: dict[str, Any] = json.loads((FIX / fixture / "summary.json").read_text())
    return parsed


P5, STF = summary("p5"), summary("p5-stf")
FILES_PATH = STF["files_path"]
STATIC: dict[str, Any] = {}
for _r in rows("p5") + rows("p5-stf"):
    if not _r["params"] and _r["method"] not in STATIC and "result" in _r["body"]:
        STATIC[_r["method"]] = _r["body"]["result"]
FOREIGN_CMD = next(r["body"]["result"] for r in rows("p5") if r["method"] == "get_trex_cmds"
                   and "-i" in r["body"]["result"][0][1].split() and "legacy" in r["body"]["result"][0][1])
SFR2_B64 = next(r["body"]["result"] for r in rows("p5-stf")
                if r["method"] == "get_file" and r["params"] == {"filepath": "cap2/sfr2.yaml"})
LISTINGS = {k: v["result"] for k, v in STF["listings"].items() if "result" in v}
STATELESS_CMD = "./_t-rex-64 --iom 0 --cfg {cfg}{c} -i{args} --no-key"


def fault(body: dict[str, Any]) -> dict[str, Any]:
    """The JSON-RPC error object of a recorded error body."""
    err: dict[str, Any] = body["error"]
    return err


class Daemon:
    """The fake's state, guarded by a lock: the run, the seq counter, the pushed files."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Idle, with no pushed files."""
        self.args = args
        self.lock = threading.Lock()
        self.seq = 0
        self.run_seq: int | None = None
        self.run_cmd = ""
        self.starting_until = 0.0
        self.end_at: float | None = None        # an STF run's end; None for a stateless run
        self.outcome = "TRex is Idle"
        self.stf_run = False
        self.tmp = tempfile.TemporaryDirectory(prefix="fake-daemon-")

    def _settle(self) -> None:
        """An STF run whose d*scale has passed is Idle with the recorded 'finished' outcome. Lock held."""
        if self.run_seq is not None and self.end_at is not None and time.monotonic() >= self.end_at:
            self.run_seq, self.end_at = None, None
            self.outcome = STF["outcomes"]["finished"]

    def state(self) -> int:
        """IDLE, STARTING or RUNNING now."""
        with self.lock:
            self._settle()
            if self.run_seq is None:
                return IDLE
            return STARTING if time.monotonic() < self.starting_until else RUNNING

    def status(self) -> dict[str, Any]:
        """The get_running_status result: the recorded shape, {state, verbose}."""
        st = self.state()
        verbose = {IDLE: self.outcome, STARTING: "TRex is Starting", RUNNING: "TRex is Running"}[st]
        return {"state": st, "verbose": verbose}

    def cmds(self) -> list[list[str]]:
        """The get_trex_cmds result: the foreign processes the flags ask for, then this fake's run."""
        out: list[list[str]] = []
        if self.args.foreign:
            out.append(list(FOREIGN_CMD[0]))
        if self.args.foreign_stf:
            out.append(list(STF["stf_cmd"]))
        with self.lock:
            self._settle()
            if self.run_seq is not None:
                out.append(["4242", self.run_cmd])
        return out

    def start(self, params: dict[str, Any]) -> Any:
        """start_trex: a recorded fault or the new run's seq."""
        opts = params.get("trex_cmd_options") or {}
        stateless = params.get("stateless", True)
        if self.args.start_fails or (self.args.stf_fails and not stateless):
            return ("error", fault(P5["faults"]["start_bad_cfg"]))
        if self.state() != IDLE:
            return ("error", fault(P5["faults"]["start_twice"]))
        if not stateless and ("f" not in opts or "d" not in opts):
            return ("error", fault(STF["start_stf_no_d"]))
        with self.lock:
            self.seq += 1
            seq = self.seq
            self.run_seq, self.stf_run = seq, not stateless
            self.starting_until = time.monotonic() + START_S
            self.end_at = None
            if stateless:
                c = f" -c {opts['c']}" if "c" in opts else ""
                args = " " + params["trex_args"] if params.get("trex_args") else ""
                self.run_cmd = STATELESS_CMD.format(cfg=opts.get("cfg", "/etc/trex_cfg.yaml"), c=c, args=args)
                self.outcome = "TRex is Idle"
            else:
                toks = shlex.split(STF["stf_cmd"][1])
                toks[toks.index("-f") + 1] = str(opts["f"])
                self.run_cmd = " ".join(toks)
                self.end_at = time.monotonic() + float(opts["d"]) * self.args.stf_scale
        time.sleep(START_S)             # block_to_success: the call returns once TRex is Running
        return ("result", seq)

    def stop(self, seq: Any) -> Any:
        """stop_trex: False while idle, the recorded -33 for another seq, True once the run is ended."""
        if self.state() == IDLE:
            return ("result", STF["stop_after_end"]["result"])
        with self.lock:
            if seq != self.run_seq:
                return ("error", fault(P5["faults"]["stop_wrong_seq_running"]))
            if self.args.stop_keeps_running:
                return ("result", True)
            self._end("stopped")
        return ("result", True)

    def _end(self, how: str) -> None:
        """End the run now; an STF run reports the recorded outcome for `how`. Lock held."""
        self.outcome = STF["outcomes"][how] if self.stf_run else "TRex is Idle"
        self.run_seq, self.end_at = None, None

    def kill(self) -> bool:
        """force_trex_kill: whatever this fake runs ends at once."""
        with self.lock:
            if self.run_seq is not None:
                self._end("stopped")
        return True

    def pushed(self) -> list[str]:
        """The names of the files push_file has written."""
        return sorted(p.name for p in Path(self.tmp.name).iterdir())

    def push(self, params: dict[str, Any]) -> Any:
        """push_file: base64-decode into the temp dir; a name with a path in it is refused."""
        name = params.get("filename", "")
        print(f"[fake-daemon] push {name}", flush=True)
        if self.args.push_false:
            return ("result", False)
        if not name or "/" in name or name in (".", ".."):
            return ("error", fault(P5["faults"]["get_file"]))
        (Path(self.tmp.name) / name).write_bytes(base64.b64decode(params.get("bin_data", "")))
        return ("result", True)

    def files_list(self, path: str) -> Any:
        """get_files_list: the recorded cap2 and avl listings, files_path plus the pushed names, else -33."""
        if path in ("cap2", "avl"):
            return ("result", LISTINGS[path])
        if path == FILES_PATH:
            return ("result", [[], [] if self.args.list_hides_pushed else self.pushed()])
        return ("error", fault(STF["listings"]["etc"]))

    def get_file(self, path: str) -> Any:
        """get_file: the recorded cap2/sfr2.yaml, a pushed file, else the recorded -33 naming the path."""
        if path == "cap2/sfr2.yaml":
            return ("result", SFR2_B64)
        prefix = FILES_PATH + "/"
        if path.startswith(prefix) and path[len(prefix):] in self.pushed():
            data = (Path(self.tmp.name) / path[len(prefix):]).read_bytes()
            return ("result", base64.b64encode(data).decode())
        err = dict(fault(P5["faults"]["get_file"]))
        err["message"] = err["message"].replace("/etc/shadow", path)
        return ("error", err)

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """('result', value) or ('error', object) for one request."""
        if method == "is_running":
            return ("result", self.state() != IDLE)
        if method == "get_running_status":
            return ("result", self.status())
        if method == "is_reserved":
            return ("result", bool(self.args.reserved))
        if method == "get_trex_cmds":
            return ("result", self.cmds())
        if method == "get_files_path":
            return ("result", FILES_PATH)
        if method == "start_trex":
            return self.start(params)
        if method == "stop_trex":
            return self.stop(params.get("seq"))
        if method == "force_trex_kill":
            return ("result", self.kill())
        if method == "push_file":
            return self.push(params)
        if method == "get_files_list":
            return self.files_list(params.get("path", ""))
        if method == "get_file":
            return self.get_file(params.get("filepath", ""))
        if method in STATIC:
            return ("result", STATIC[method])
        err = dict(fault(P5["faults"]["unknown_method"]))
        err["message"] = f"Method {method} not supported."
        return ("error", err)


def make_handler(daemon: Daemon, args: argparse.Namespace) -> type[BaseHTTPRequestHandler]:
    """The request handler class bound to `daemon` and the flags."""

    class Handler(BaseHTTPRequestHandler):
        """One JSON-RPC POST per connection."""
        protocol_version = "HTTP/1.0"

        def do_POST(self) -> None:  # noqa: N802
            """Log the method, apply the hostile flags, answer."""
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method, params = req.get("method", ""), req.get("params") or {}
            print(f"[fake-daemon] method {method}", flush=True)
            if args.redirect:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{args.port}/")
                self.end_headers()
                return
            if method == args.hang:
                print(f"hang {method}", flush=True)
                time.sleep(HANG_S)
            kind, value = daemon.dispatch(method, params)
            if method == args.huge:
                kind, value = "result", "A" * HUGE_BYTES
            body = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), kind: value}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if method == args.drip:
                    for i in range(len(body)):
                        self.wfile.write(body[i:i + 1])
                        self.wfile.flush()
                        time.sleep(1)
                else:
                    self.wfile.write(body)
            except OSError:
                pass            # the client gave up (a deadline test)

        def log_message(self, *a: Any) -> None:
            """Silence the access log; the method lines are the log."""

    return Handler


def main() -> None:
    """Parse the flags, serve on 127.0.0.1:<port> until SIGTERM."""
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("--foreign", action="store_true")
    ap.add_argument("--foreign-stf", action="store_true")
    ap.add_argument("--reserved", action="store_true")
    ap.add_argument("--start-fails", action="store_true")
    ap.add_argument("--stf-fails", action="store_true")
    ap.add_argument("--push-false", action="store_true")
    ap.add_argument("--list-hides-pushed", action="store_true")
    ap.add_argument("--stf-scale", type=float, default=1.0)
    ap.add_argument("--hang")
    ap.add_argument("--drip")
    ap.add_argument("--huge")
    ap.add_argument("--redirect", action="store_true")
    ap.add_argument("--stop-keeps-running", action="store_true")
    args = ap.parse_args()
    daemon = Daemon(args)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(daemon, args))
    httpd.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"[fake-daemon] ready port={args.port}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        daemon.tmp.cleanup()


if __name__ == "__main__":
    main()
