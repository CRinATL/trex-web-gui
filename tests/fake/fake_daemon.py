"""A fake TRex daemon (trex_server.py, JSON-RPC over HTTP): `fake_daemon.py <port> [flags]`.

It is a ThreadingHTTPServer so that one hung or dripping request does not stop the others. The real daemon
is a single-threaded SimpleJSONRPCServer (trex_server.py), which would serialise them: a test that needs to
see the Control session survive a hung daemon must not rely on this fake's concurrency being the daemon's.

Most bodies it answers are recorded ones: tests/fixtures/p5/daemon.jsonl and summary.json (faults),
tests/fixtures/p5-stf/ (STF run, listings, outcomes) and tests/fixtures/daemon-ui/ (the missing TRex log). Not
all are: the register in tests/fake/unrecorded.py lists every answer no recording backs (the divergences below
and each hook flag, except --foreign and --foreign-stf, which list recorded processes). It
keeps the state Idle -> Starting -> Running, a seq counter (stop_trex answers False while idle and -33 for a
wrong seq while running) and a temp dir that push_file writes into. It prints one `[fake-daemon] method
<name>` line per request, so a test can read the log of what was called, `push <name>` for each push_file
(before it is judged), and `hang <method>` when a --hang request starts to stall.

Flags: --foreign (lists a recorded `_t-rex-64 -i` process), --foreign-stf (lists a recorded STF command this
fake did not start), --reserved, --start-fails, --stf-fails, --stf-scale F (an STF run lasts d*F seconds),
--push-false (push_file answers false and writes nothing), --list-hides-pushed (get_files_list omits what
push_file wrote), --hang METHOD (sleep 30 s), --drip METHOD (one byte per second), --huge METHOD (a 9 MiB
reply), --redirect (302 to every call), --stop-keeps-running (stop_trex says True but the run goes on),
--daemon-run (the fake starts Running a stateless run of its own whose seq this backend never saw, as when
another client started it through the daemon; it counts as a start_trex already accepted, so --no-trex-log
reads the log and --foreign-after-start lists its process at once), --run-ends-before-kill (the run ends on
its own just as force_trex_kill arrives, so the kill finds the daemon Idle), --exit-lag S (after a stop or
kill ends the fake's run, its process stays listed by get_trex_cmds for S seconds while get_running_status
already says Idle: trex_launch_thread.py `run` sends SIGUSR1 to the TRex process group and sets the status
Idle at once, without waiting for `_t-rex-64` to exit), --no-trex-log (get_trex_log answers the recorded
missing-file -33 of tests/fixtures/daemon-ui until a start_trex has been accepted, as a fresh daemon does),
--foreign-after-start (lists the --foreign process only once a start_trex has been accepted, as when a TRex is
started by hand beside this backend's run), --foreign-ends-with-run (lists the --foreign process only while
the fake's own run is going, so a stop empties the list at once), --trex-log-fault (get_trex_log answers the
recorded get_file refusal, a -33 that is not the missing file: the fault `daemon/session.py` `log` must
re-raise).

Known divergences from the real server:
- --exit-lag keeps the ended run's process listed for S seconds after the status is Idle: read from
  trex_launch_thread.py, not recorded. [U:daemon-exit-lag-listing]
- --foreign-after-start lists a hand-started TRex only once a start_trex has been accepted: modelled, not
  recorded. [U:daemon-foreign-after-start]
- --foreign-ends-with-run drops the foreign process from the list at the moment the run ends: modelled, not
  recorded; the real list lags (see --exit-lag). [U:daemon-foreign-ends-with-run]
- --no-trex-log answers the recorded missing-file -33 only until a start_trex has been accepted; the recorded
  daemon-ui body is the answer of a daemon that has never started TRex, and the log's reading normally after
  a start is the assumption. [U:daemon-trex-log-after-start]
- --trex-log-fault answers get_trex_log with the recorded get_file refusal; the daemon's own answer to a
  get_trex_log it cannot serve for another reason is not recorded. [U:daemon-hook-trex-log-fault]
- The status text while Starting is read from trex_server.py `start`, not recorded: start_trex blocks until
  Running, so no recording caught it. [U:daemon-starting-status-text]
- Without --no-trex-log, get_trex_log answers the recorded p5 body even before any start_trex; a daemon that
  never started TRex answers the missing-file -33. [U:daemon-trex-log-before-start]
- push_file stores the name's basename and answers False for a name it cannot open: read from trex_server.py
  `push_file`, not recorded. [U:daemon-push-file-basename]
- force_trex_kill answers True when it ends the fake's run and the idle stop_trex reply while Idle: the one
  recording is a -11. [U:daemon-force-kill-results]

force_trex_kill stops only the daemon's own run: the real daemon's force_trex_kill and stop_trex both call
CTRex.stop_trex (trex_server.py), which answers False while Idle and never touches a TRex the daemon did not
start. So an idle kill answers the recorded idle stop_trex reply (p5-stf `stop_after_end`), and the --foreign
processes stay listed."""
import argparse
import base64
import json
import os
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
# the text trex_server.py `start` sets (read from its source; the register entry says so)
STARTING_VERBOSE = "TRex is starting (data is not available yet)"  # [U:daemon-starting-status-text]


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
NO_TREX_LOG = next(r["body"]["error"] for r in rows("daemon-ui") if r["method"] == "get_trex_log")
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
        # a start_trex was accepted (--foreign-after-start, --no-trex-log); a --daemon-run run was started
        # through the daemon, so the log exists
        self.started = bool(args.daemon_run)
        self.lingering: list[tuple[str, str, float]] = []   # (pid, cmd, until): ended runs still listed
        self.tmp = tempfile.TemporaryDirectory(prefix="fake-daemon-")
        if args.daemon_run:
            self.seq = self.run_seq = 1
            self.run_cmd = STATELESS_CMD.format(cfg="/etc/trex_cfg.yaml", c="", args="")

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
        verbose = {IDLE: self.outcome, STARTING: STARTING_VERBOSE, RUNNING: "TRex is Running"}[st]
        return {"state": st, "verbose": verbose}

    def cmds(self) -> list[list[str]]:
        """The get_trex_cmds result: the foreign processes the flags ask for, then this fake's run."""
        out: list[list[str]] = []
        if self.args.foreign_stf:
            out.append(list(STF["stf_cmd"]))
        with self.lock:
            self._settle()
            foreign_late = self.args.foreign_after_start and self.started  # [U:daemon-foreign-after-start]
            foreign_scoped = (  # [U:daemon-foreign-ends-with-run]
                self.args.foreign_ends_with_run and self.run_seq is not None
            )
            if self.args.foreign or foreign_late or foreign_scoped:
                out.insert(0, list(FOREIGN_CMD[0]))
            if self.run_seq is not None:
                out.append(["4242", self.run_cmd])
            now = time.monotonic()
            self.lingering = [e for e in self.lingering if e[2] > now]
            out.extend([pid, cmd] for pid, cmd, _until in self.lingering)
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
            self.run_seq, self.stf_run, self.started = seq, not stateless, True
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
        """End the run now; an STF run reports the recorded outcome for `how`, and with --exit-lag its process
        stays listed for that long. Lock held."""
        self.outcome = STF["outcomes"][how] if self.stf_run else "TRex is Idle"
        if self.args.exit_lag > 0 and self.run_seq is not None:
            linger_until = time.monotonic() + self.args.exit_lag  # [U:daemon-exit-lag-listing]
            self.lingering.append(("4242", self.run_cmd, linger_until))
        self.run_seq, self.end_at = None, None

    def kill(self) -> Any:
        """force_trex_kill: the fake's own run ends at once (True); while Idle it answers what stop_trex
        answers while Idle (CTRex.stop_trex serves both), and the --foreign processes go on."""
        with self.lock:
            if self.args.run_ends_before_kill and self.run_seq is not None:
                self._end("stopped")
        if self.state() == IDLE:
            return STF["stop_after_end"]["result"]    # [U:daemon-force-kill-results]
        with self.lock:
            self._end("stopped")
        return True

    def pushed(self) -> list[str]:
        """The names of the files push_file has written."""
        return sorted(p.name for p in Path(self.tmp.name).iterdir())

    def push(self, params: dict[str, Any]) -> Any:
        """push_file: base64-decode into the temp dir under the name's basename, as trex_server.py does; a
        name that cannot be opened (empty, `.`, `..`, a trailing slash) answers False, as its IOError does."""
        name = params.get("filename", "")
        print(f"[fake-daemon] push {name}", flush=True)
        if self.args.push_false:
            return ("result", False)
        try:
            target = Path(self.tmp.name) / os.path.basename(name)    # [U:daemon-push-file-basename]
            target.write_bytes(base64.b64decode(params.get("bin_data", "")))
        except OSError:
            return ("result", False)
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
        if method == "get_trex_log" and self.args.trex_log_fault:   # [U:daemon-hook-trex-log-fault]
            return ("error", fault(P5["faults"]["get_file"]))
        log_unstarted = self.args.no_trex_log and not self.started  # [U:daemon-trex-log-after-start]
        if method == "get_trex_log" and log_unstarted:
            return ("error", NO_TREX_LOG)
        if method in STATIC:      # [U:daemon-trex-log-before-start] get_trex_log reads before any start
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


def build_parser() -> argparse.ArgumentParser:
    """The flags of the fake: the port and the behaviour hooks."""
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("--foreign", action="store_true")
    ap.add_argument("--foreign-stf", action="store_true")
    ap.add_argument("--reserved", action="store_true")  # [U:daemon-hook-reserved]
    ap.add_argument("--start-fails", action="store_true")  # [U:daemon-hook-start-fails]
    ap.add_argument("--stf-fails", action="store_true")  # [U:daemon-hook-stf-fails]
    ap.add_argument("--push-false", action="store_true")  # [U:daemon-hook-push-false]
    ap.add_argument("--list-hides-pushed", action="store_true")  # [U:daemon-hook-list-hides-pushed]
    ap.add_argument("--stf-scale", type=float, default=1.0)  # [U:daemon-hook-stf-scale]
    ap.add_argument("--hang")  # [U:daemon-hook-hang]
    ap.add_argument("--drip")  # [U:daemon-hook-drip]
    ap.add_argument("--huge")  # [U:daemon-hook-huge]
    ap.add_argument("--redirect", action="store_true")  # [U:daemon-hook-redirect]
    ap.add_argument("--stop-keeps-running", action="store_true")  # [U:daemon-hook-stop-keeps-running]
    ap.add_argument("--daemon-run", action="store_true")  # [U:daemon-hook-daemon-run]
    ap.add_argument("--run-ends-before-kill", action="store_true")  # [U:daemon-hook-run-ends-before-kill]
    ap.add_argument("--exit-lag", type=float, default=0.0)
    ap.add_argument("--foreign-after-start", action="store_true")
    ap.add_argument("--no-trex-log", action="store_true")
    ap.add_argument("--trex-log-fault", action="store_true")
    ap.add_argument("--foreign-ends-with-run", action="store_true")
    return ap


def main() -> None:
    """Parse the flags, serve on 127.0.0.1:<port> until SIGTERM."""
    args = build_parser().parse_args()
    daemon = Daemon(args)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(daemon, args))
    httpd.daemon_threads = True
    args.port = httpd.server_address[1]          # port 0: the one bound, for the ready line and --redirect
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"[fake-daemon] ready port={args.port}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        daemon.tmp.cleanup()


if __name__ == "__main__":
    main()
