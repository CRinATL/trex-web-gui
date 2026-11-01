"""Install smoke (P6, D1, D8): the built wheel, installed into a throwaway uv tool dir, serves the SPA, both
workers, the WebSocket and --monitor-only, and touches nothing outside its root.

  install_smoke.py run --pythons "3.12 3.14" [--dist DIR] [--keep DIR]
  install_smoke.py install --root DIR --python 3.12

Run it as `uv run --project backend python -I -B scripts/install_smoke.py ...` (make install-smoke): it needs
httpx and websockets from the dev environment. Each check prints `smoke <python> <check>: ok <figure>`; the
same lines are appended to dist/install-smoke-YYYY-MM-DD-HHMMSSZ.log."""
import argparse
import contextlib
import datetime
import json
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect as ws_connect

ROOT = Path(__file__).resolve().parent.parent
WAIT = 20.0          # seconds to wait for a fake's ready line and a serve's Open line
MODES = (0o644, 0o755)
BAD_PARTS = (".so", ".pyc", "__pycache__/", "/unit_tests/", "/examples/")


class SmokeError(Exception):
    """A check failed, or the smoke could not start."""


def project_version(pyproject: Path) -> str:
    """The `[project] version` of a pyproject.toml."""
    return str(tomllib.loads(pyproject.read_text())["project"]["version"])


def find_wheel(dist: Path, version: str) -> Path:
    """`dist/trex_web_gui-<version>-py3-none-any.whl`; SmokeError naming `make wheel` when it is missing."""
    wheel = dist / f"trex_web_gui-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise SmokeError(f"{wheel} is missing: run `make wheel`")
    return wheel


def tracked_files() -> list[str]:
    """`git ls-files backend/trex_web_gui` mapped to wheel member names (`trex_web_gui/...`)."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "backend/trex_web_gui"], check=True,
                         capture_output=True, text=True).stdout
    return [line.removeprefix("backend/") for line in out.splitlines() if line]


def check_wheel(wheel: Path, tracked: list[str]) -> list[str]:
    """The problems with a wheel (empty when it is good): untracked or missing package files, no SPA,
    forbidden members, a mode other than 0644 or 0755, and no licence files."""
    with zipfile.ZipFile(wheel) as z:
        infos = [i for i in z.infolist() if not i.filename.endswith("/")]
    names = {i.filename for i in infos}
    found: list[str] = []
    want = set(tracked)
    for n in sorted(names):
        if n.startswith("trex_web_gui/") and not n.startswith("trex_web_gui/static/") and n not in want:
            found.append(f"untracked file in the wheel: {n}")
    for n in sorted(want - names):
        found.append(f"tracked file missing from the wheel: {n}")
    index = "trex_web_gui/static/index.html"
    if index not in names:
        found.append(f"no {index}")
    else:
        with zipfile.ZipFile(wheel) as z:
            if b"trex-web-gui.theme" not in z.read(index):
                found.append(f"{index} lacks trex-web-gui.theme")
    if not any(n.startswith("trex_web_gui/static/assets/") for n in names):
        found.append("no trex_web_gui/static/assets/ member")
    for n in sorted(names):
        versioned_so = ".so." in n.rsplit("/", 1)[-1]
        if n.endswith((".so", ".pyc")) or versioned_so or any(p in "/" + n for p in BAD_PARTS[2:]):
            found.append(f"forbidden member: {n}")
    for bundled in ("LLDP", "DHCP-DISCOVER"):
        if f"trex_web_gui/studio/templates/{bundled}.trp" not in names:
            found.append(f"no trex_web_gui/studio/templates/{bundled}.trp")
    for i in infos:
        mode = (i.external_attr >> 16) & 0o777
        if mode not in MODES:
            found.append(f"mode {mode:o} on {i.filename}")
    for lic in ("LICENSE", "NOTICE"):
        if not any(n.endswith(f".dist-info/licenses/{lic}") for n in names):
            found.append(f"no dist-info/licenses/{lic}")
    return found


def tool_env(root: Path, base_path: str) -> dict[str, str]:
    """The environment of every uv call: the tool and bin directories under root, no Python downloads,
    and the real HOME (uv's managed Pythons and its shared cache live there)."""
    return {"PATH": base_path, "HOME": os.environ.get("HOME", str(Path.home())),
            "UV_TOOL_DIR": str(root / "tools"), "UV_TOOL_BIN_DIR": str(root / "bin"),
            "UV_PYTHON_DOWNLOADS": "never"}


def serve_env(root: Path, base_path: str, token: str) -> dict[str, str]:
    """The environment of an installed `trex-web-gui serve`: HOME and the XDG directories under root."""
    return {"PATH": base_path, "HOME": str(root / "home"), "XDG_DATA_HOME": str(root / "data"),
            "XDG_CONFIG_HOME": str(root / "config"), "TREX_WEB_GUI_TOKEN": token}


def outside_snapshot(paths: list[Path]) -> dict[str, tuple[str, ...]]:
    """Per path a sorted tuple of `name mtime_ns` over its tree (the file itself); () if missing."""
    snap: dict[str, tuple[str, ...]] = {}
    for p in paths:
        if not p.exists():
            snap[str(p)] = ()
            continue
        entries = [f"{p.name} {p.lstat().st_mtime_ns}"]
        if p.is_dir():
            for dirpath, dirs, files in os.walk(p):
                for n in dirs + files:
                    f = Path(dirpath) / n
                    with contextlib.suppress(OSError):
                        entries.append(f"{f.relative_to(p)} {f.lstat().st_mtime_ns}")
        snap[str(p)] = tuple(sorted(entries))
    return snap


def new_token() -> str:
    """A fresh 32-character token (letters and digits)."""
    return secrets.token_hex(16)


def free_port() -> int:
    """A TCP port that was free on 127.0.0.1 a moment ago."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Proc:
    """A child in its own process group, with a thread collecting its stdout lines."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path, graceful: bool = False) -> None:
        """Starts argv (stderr merged into stdout) in a new session; `graceful` marks a serve, which teardown
        interrupts before it kills."""
        self.graceful = graceful
        self.p = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.lines: list[str] = []
        self.q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        """Copies stdout lines to the list and the queue until EOF."""
        assert self.p.stdout
        for line in self.p.stdout:
            self.lines.append(line.rstrip("\n"))
            self.q.put(line.rstrip("\n"))

    def wait_line(self, prefix: str, timeout: float) -> str:
        """The first output line containing `prefix` within `timeout` seconds, else SmokeError."""
        end = time.monotonic() + timeout
        seen = [ln for ln in self.lines if prefix in ln]
        if seen:
            return seen[0]
        while (left := end - time.monotonic()) > 0:
            try:
                line = self.q.get(timeout=left)
            except queue.Empty:
                break
            if prefix in line:
                return line
        tail = "\n".join(self.lines[-15:])
        raise SmokeError(f"no {prefix!r} within {timeout:g}s (exit {self.p.poll()}); output:\n{tail}")

    def stop(self, sig: int = signal.SIGINT, grace: float = 15.0) -> float:
        """Signals the process, waits `grace` seconds, then kills its group; returns the seconds waited.
        SmokeError if it needed the kill."""
        t0 = time.monotonic()
        if self.p.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.p.pid, sig)
            try:
                self.p.wait(grace)
            except subprocess.TimeoutExpired:
                self.kill()
                raise SmokeError(f"still running {grace:g}s after the signal; killed its group") from None
        return time.monotonic() - t0

    def kill(self) -> None:
        """SIGKILL to the whole group, then reap."""
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.p.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.p.wait(5)


def uv_install(root: Path, python: str, wheel: Path) -> float:
    """`uv tool install --python <python> <wheel>` under tool_env; returns the seconds it took."""
    t0 = time.monotonic()
    r = subprocess.run(["uv", "tool", "install", "--python", python, str(wheel)],
                       env=tool_env(root, os.environ["PATH"]), cwd=root, capture_output=True, text=True,
                       check=False)
    if r.returncode != 0:
        raise SmokeError(f"uv tool install failed ({r.returncode}):\n{r.stdout}{r.stderr}")
    return time.monotonic() - t0


def install_only(root: Path, python: str) -> Path:
    """Installs the current wheel into root (Task 9's demo) and returns `root/bin/trex-web-gui`."""
    wheel = find_wheel(ROOT / "dist", project_version(ROOT / "backend" / "pyproject.toml"))
    root.mkdir(parents=True, exist_ok=True)
    uv_install(root, python, wheel)
    exe = root / "bin" / "trex-web-gui"
    if not exe.exists():
        raise SmokeError(f"{exe} was not installed")
    return exe


def start_fake(procs: list[Proc], rpc: int, pub: int, *extra: str) -> Proc:
    """Starts tests/fake/fake_trex.py on the two ports and waits for its ready line. The process is added
    to `procs` before the wait, so teardown reaps it even when the wait fails."""
    fake = Proc([sys.executable, "-I", "-B", "tests/fake/fake_trex.py", "tests/fixtures", str(rpc), str(pub),
                 *extra], {"PATH": os.environ["PATH"]}, ROOT)
    procs.append(fake)
    fake.wait_line("[fake] ready", WAIT)
    return fake


def start_serve(procs: list[Proc], root: Path, exe: Path, token: str, port: int, *args: str) -> Proc:
    """Starts the installed `trex-web-gui serve` with the scratch environment and waits for its Open line. The
    process is added to `procs` before the wait, so teardown reaps it even when the wait fails."""
    for d in ("home", "data", "config"):
        (root / d).mkdir(exist_ok=True)
    argv = [str(exe), "serve", "--port", str(port), "--data-dir", str(root / "data" / "trex-web-gui"), *args]
    serve = Proc(argv, serve_env(root, os.environ["PATH"], token), root, graceful=True)
    procs.append(serve)
    line = serve.wait_line("Open: ", WAIT)
    want = f"Open: http://127.0.0.1:{port}/api/auth/login?token={token}"
    if want not in line:
        raise SmokeError(f"unexpected Open line: {line}")
    serve.wait_line("Uvicorn running", WAIT)      # the Open line is printed before the socket is bound
    return serve


def cookie_header(r: httpx.Response) -> str:
    """The `name=value` Cookie header for the cookie a login response set."""
    jar = r.headers.get_list("set-cookie")
    if not jar:
        raise SmokeError("login set no cookie")
    return jar[0].split(";")[0]


def smoke_python(py: str, root: Path, wheel: Path, version: str, say: Callable[[str], None],
                 outside_paths: list[Path], before: dict[str, tuple[str, ...]]) -> None:
    """The fifteen checks for one Python; `say` prints a line. Raises SmokeError at the first failure."""
    def step(name: str, fn: Callable[[], str]) -> None:
        """Runs one check and prints its ok line; any failure is a SmokeError naming the check."""
        try:
            figure = fn()
        except Exception as e:
            raise SmokeError(f"smoke {py} {name}: FAIL {e}") from e
        say(f"smoke {py} {name}: ok {figure}".rstrip())

    root.mkdir(parents=True, exist_ok=True)
    exe = root / "bin" / "trex-web-gui"
    token = new_token()
    rpc, pub, port, daemon = free_port(), free_port(), free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    state: dict[str, Proc | str] = {}
    procs: list[Proc] = []

    def need(cond: bool, msg: str) -> None:
        """Raises AssertionError(msg) unless cond."""
        if not cond:
            raise AssertionError(msg)

    def c_wheel() -> str:
        """Check 1."""
        bad = check_wheel(wheel, tracked_files())
        need(not bad, "; ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""))
        return wheel.name

    def c_install() -> str:
        """Check 2."""
        secs = uv_install(root, py, wheel)
        need(exe.exists(), f"{exe} does not exist")
        du = subprocess.run(["du", "-sb", str(root / "tools")], capture_output=True, text=True, check=True)
        return f"{secs:.1f}s tools={du.stdout.split()[0]}B"

    def c_serve() -> str:
        """Check 3."""
        start_fake(procs, rpc, pub)
        state["serve"] = start_serve(procs, root, exe, token, port, "--server", f"127.0.0.1:{rpc}",
                                     "--daemon-port", str(daemon))
        return f"port={port}"

    def c_auth() -> str:
        """Check 4."""
        with httpx.Client(base_url=base, follow_redirects=False) as anon:
            r = anon.get("/api/auth/login", params={"token": token})
            need(r.status_code == 303, f"login gave {r.status_code}")
            state["cookie"] = cookie_header(r)
            a = anon.get("/api/about", headers={"Cookie": ""})
            need(a.status_code == 401, f"/api/about without the cookie gave {a.status_code}")
        return "303 and 401"

    def http() -> httpx.Client:
        """A client holding the session cookie."""
        return httpx.Client(base_url=base, headers={"Cookie": str(state["cookie"])}, timeout=60)

    def c_about() -> str:
        """Check 5."""
        with http() as c:
            j = c.get("/api/about").json()
        need(j["app_version"] == version, f"app_version {j['app_version']!r} != {version!r}")
        need(j["install"] == "wheel", f"install {j['install']!r}")
        sf = str(j["static_from"])
        need(sf.startswith(str(root / "tools")) and sf.endswith("trex_web_gui/static"), f"static_from {sf}")
        return f"{version} wheel"

    def c_spa() -> str:
        """Check 6."""
        with http() as c:
            r = c.get("/")
            need(r.status_code == 200 and "trex-web-gui.theme" in r.text, f"/ gave {r.status_code}")
            m = re.search(r'/assets/[^"\']+', r.text)
            need(m is not None, "no /assets/ link in /")
            assert m
            a = c.get(m.group(0))
            need(a.status_code == 200, f"{m.group(0)} gave {a.status_code}")
        return m.group(0)

    def c_openapi() -> str:
        """Check 7."""
        with http() as c:
            r = c.get("/openapi.json")
        try:
            body = r.json()
        except ValueError:
            body = None
        need(not (isinstance(body, dict) and "openapi" in body), "/openapi.json serves an OpenAPI document")
        return str(r.status_code)

    def c_connect() -> str:
        """Check 8."""
        with http() as c:
            r = c.post("/api/session/connect",
                       json={"host": "127.0.0.1", "sync_port": rpc, "async_port": pub})
        need(r.status_code == 200, f"connect gave {r.status_code}: {r.text[:200]}")
        return "200"

    def timed_get(path: str) -> tuple[httpx.Response, float]:
        """GET path with the session; returns the response and the seconds taken."""
        with http() as c:
            t0 = time.monotonic()
            r = c.get(path)
        return r, time.monotonic() - t0

    def c_profile() -> str:
        """Check 9."""
        r, secs = timed_get("/api/studio/template")
        need(r.status_code == 200, f"gave {r.status_code}: {r.text[:200]}")
        return f"{secs:.2f}s"

    def c_packet() -> str:
        """Check 10."""
        r, secs = timed_get("/api/studio/packet/definitions")
        need(r.status_code == 200, f"gave {r.status_code}: {r.text[:200]}")
        n = len(r.json()["protocols"])
        need(n > 300, f"{n} protocols")
        return f"{secs:.2f}s protocols={n}"

    def c_templates() -> str:
        """Check 11."""
        r, _ = timed_get("/api/studio/packet/templates")
        need(r.status_code == 200, f"gave {r.status_code}")
        ids = [t["id"] for t in r.json()["server"]]
        for bundled in ("trex-web-gui/LLDP", "trex-web-gui/DHCP-DISCOVER"):
            need(bundled in ids, f"{bundled} not listed")
        return f"{len(ids)} server templates"

    def c_hello() -> str:
        """Check 12."""
        with ws_connect(f"ws://127.0.0.1:{port}/api/ws", additional_headers={"Cookie": str(state["cookie"])},
                        open_timeout=10) as ws:
            hello = json.loads(ws.recv(timeout=10))
        need(hello.get("type") == "hello", f"first message {hello.get('type')!r}")
        need(hello["app_version"] == version, f"app_version {hello['app_version']!r}")
        need(hello["daemon_hint"]["port"] == daemon, f"daemon_hint {hello['daemon_hint']}")
        return f"daemon_hint.port={daemon}"

    def c_stop() -> str:
        """Check 13."""
        serve = state["serve"]
        assert isinstance(serve, Proc)
        secs = serve.stop()
        return f"{secs:.1f}s"

    def c_monitor() -> str:
        """Check 14."""
        pub2, rpc2, port2, daemon2 = free_port(), free_port(), free_port(), free_port()
        token2 = new_token()
        start_fake(procs, rpc2, pub2, "--pub-replay", "tests/fixtures/p5/pub-stl.jsonl")
        serve2 = start_serve(procs, root, exe, token2, port2, "--monitor-only", "--monitor",
                             f"127.0.0.1:{pub2}", "--daemon-port", str(daemon2))
        with httpx.Client(base_url=f"http://127.0.0.1:{port2}", timeout=30) as c:
            r = c.get("/api/auth/login", params={"token": token2})
            c.headers["Cookie"] = cookie_header(r)
            k = c.post("/api/session/connect",
                       json={"host": "127.0.0.1", "sync_port": rpc2, "async_port": pub2})
            need(k.status_code == 409 and k.json().get("error") == "monitor_only",
                 f"connect gave {k.status_code}")
            end = time.monotonic() + 10
            snap: dict[str, Any] = {}
            while time.monotonic() < end:
                snap = c.get("/api/monitor").json()
                on_target = snap.get("target") == {"host": "127.0.0.1", "port": pub2}
                if on_target and (snap.get("frames") or 0) > 0:
                    break
                time.sleep(0.25)
            else:
                raise AssertionError(f"no frames from the replay in 10s: {snap}")
        serve2.stop()
        return f"frames={snap['frames']}"

    def c_outside() -> str:
        """Check 15."""
        after = outside_snapshot(outside_paths)
        changed = [p for p in before if before[p] != after[p]]
        need(not changed, f"changed outside the root: {', '.join(changed)}")
        return f"{len(outside_paths)} paths unchanged"

    try:
        for name, fn in (("wheel", c_wheel), ("install", c_install), ("serve", c_serve), ("auth", c_auth),
                         ("about", c_about), ("spa", c_spa), ("openapi", c_openapi), ("connect", c_connect),
                         ("profile worker", c_profile), ("packet worker", c_packet),
                         ("templates", c_templates), ("hello", c_hello), ("stop", c_stop),
                         ("monitor-only", c_monitor), ("outside", c_outside)):
            step(name, fn)
    finally:
        for p in reversed(procs):       # serves get SIGINT and 15 s; every group is then killed outright
            if p.graceful and p.p.poll() is None:
                with contextlib.suppress(SmokeError):
                    p.stop()
            p.kill()
        subprocess.run(["uv", "tool", "uninstall", "trex-web-gui"], env=tool_env(root, os.environ["PATH"]),
                       cwd=root, capture_output=True, check=False)


def run(pythons: list[str], dist: Path, keep: Path | None = None) -> int:
    """Smokes the wheel on each Python; prints and logs one line per check. Returns 0 after
    `install-smoke: ok`, else 1 after a FAIL line. Tears down in `finally` and removes the temp root
    (a `keep` directory is used as the root and left in place)."""
    dist.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M%SZ")
    log = dist / f"install-smoke-{stamp}.log"

    def say(line: str) -> None:
        """Prints the line and appends it to the log."""
        print(line, flush=True)
        with log.open("a") as f:
            f.write(line + "\n")

    root = keep if keep is not None else Path(tempfile.mkdtemp(prefix="trex-web-gui-smoke-"))
    root.mkdir(parents=True, exist_ok=True)
    try:
        version = project_version(ROOT / "backend" / "pyproject.toml")
        wheel = find_wheel(dist, version)
        tooldir = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True,
                                 check=True).stdout.strip()
        local = Path.home() / ".local"
        outside = [local / "bin", Path(tooldir), local / "share" / "trex-web-gui"]
        for py in pythons:
            before = outside_snapshot(outside)
            smoke_python(py, root / f"py{py}", wheel, version, say, outside, before)
        say("install-smoke: ok")
        return 0
    except SmokeError as e:
        msg = str(e)
        say(msg if "FAIL" in msg else f"install-smoke: FAIL {msg}")
        return 1
    finally:
        if keep is None:
            shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """CLI: `run --pythons "3.12 3.14" [--dist DIR] [--keep DIR]` or `install --root DIR --python 3.12`."""
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="smoke the wheel on each Python")
    r.add_argument("--pythons", default="3.12 3.14", help="space-separated uv Python requests")
    r.add_argument("--dist", type=Path, default=ROOT / "dist")
    r.add_argument("--keep", type=Path, default=None, help="use this directory as the root and keep it")
    i = sub.add_parser("install", help="install the wheel into --root and print its trex-web-gui")
    i.add_argument("--root", type=Path, required=True)
    i.add_argument("--python", default="3.12")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.pythons.split(), a.dist, a.keep)
    try:
        print(install_only(a.root, a.python))
    except SmokeError as e:
        print(f"install-smoke: FAIL {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
