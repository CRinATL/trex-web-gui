"""Shoot the GUI inventory (docs/gui-inventory/screens/*.png) against the real TRex guest.

What it does: starts the real backend (`trex-web-gui serve` through vm/shoot_serve.py, which names the TRex
session `demo` instead of the shooter's login; the built frontend in frontend/dist) as a child on another port
with a throw-away data dir, starts headless Chrome, and drives the GUI over the Chrome DevTools protocol with
real mouse and keyboard events, the way 021e1fa's reshoot was made. Every shot is a
1400x900 CSS pixel viewport at device scale 2 (2800x1800 PNGs) in the Light theme; the exceptions are named
where they are
taken (the theme shots; 82 and 84 are 540 px tall windows, 2800x1080; 85-87 are crops of one element, the
Control tab's status chips). Ports 0 and 1 of the guest are looped back to each other; the script starts
live traffic on both while it shoots. Two steps near the end change the guest: "daemon" runs
`vm/daemon.sh start` and `vm/mode.sh stop` (the guest's TRex is stopped for a stateful run) and always runs
`vm/daemon.sh stop` afterwards; "monitor_stateful" starts tests/fake/fake_trex.py on ports 18501/18500 and
stops it again. The VM is left with no TRex running, so finish with `vm/demo.sh down`.

Usage (the guest, RPC/PUB ports 4501/4500 on jammy, must be up, `vm/demo.sh up`, and frontend/dist built;
for a guest other than jammy first run eval "$(TREX_VM_GUEST=<guest> vm/guests.sh)"):

    systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- \\
        backend/.venv/bin/python -I -B vm/shoot_inventory.py <scratch dir> [--out DIR] [--until STEP]

<scratch dir> holds the backend's data dir, Chrome's profile, the logs and the staged shots. It is emptied
first, but only when it is empty already or carries the marker file (.shoot_inventory_scratch) this tool
wrote; it is refused when it is the repo, contains or lies inside it, or is $HOME or /. The shots are
written to <scratch dir>/shots; only after a complete run (no --until) are the old PNGs of --out (default
docs/gui-inventory/screens) replaced by them. With --until the shots stay in <scratch dir>/shots and the
path is printed. STEPS below lists the steps in order; --until stops after the named one (a fast way to test
a late step). Ports 8766 (backend) and 9344 (Chrome) must be free; the tool refuses to start otherwise.
Needs only what backend/.venv has (httpx, websockets) and google-chrome on the PATH.
"""
import argparse
import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from base64 import b64decode
from pathlib import Path

import httpx
import websockets

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import guestenv

SYNC_PORT, ASYNC_PORT = guestenv.ports()
TREE_ROOT = f"TRex-{guestenv.hostname()}"          # the tree item carries the guest's hostname
FIXTURES = REPO / "tests/fixtures/profiles"
FAKE_TREX = REPO / "tests/fake/fake_trex.py"
FAKE_FIXTURES = REPO / "tests/fixtures"
STATEFUL_REPLAY = REPO / "tests/fixtures/p5/pub-stateful.jsonl"     # a recorded stateful TRex's PUB frames
FAKE_RPC_PORT, FAKE_PUB_PORT = 18501, 18500
# The fixture profiles the shoot imports or assigns. Each is copied under the scratch dir, so the screens show
# the scratch path, not the repo's. None imports a sibling file (only argparse and trex_stl_lib), so copying
# the file itself is enough.
SHOT_PROFILES = ("flow_latency.py", "udp_for_benchmarks.py", "gui_example.yaml")
GUI_PORT, CDP_PORT = 8766, 9344
GUI = f"http://127.0.0.1:{GUI_PORT}"
WIDTH, HEIGHT, SCALE = 1400, 900, 2

# Page-side helpers, installed on every new document. `name` approximates the accessible name; `find` returns
# the visible elements of a selector whose name equals `text` (a leading "~" means "contains"), searched
# inside `within` (a selector; the last match, so an open dialog wins) or the whole document.
LIB = r"""
window.S = {
  norm: (t) => (t || '').replace(/\s+/g, ' ').trim(),
  name(e) {
    const al = e.getAttribute('aria-label'); if (al) return this.norm(al);
    const lb = e.getAttribute('aria-labelledby');
    if (lb) { const x = document.getElementById(lb.split(' ')[0]); if (x) return this.norm(x.textContent); }
    if (e.labels && e.labels.length) return this.norm(e.labels[0].textContent);
    const ph = e.getAttribute('placeholder'); if (ph && !e.textContent.trim()) return this.norm(ph);
    return this.norm(this.text(e));
  },
  text(e) {   // textContent without aria-hidden parts (a run button's glyph and width-reserving labels, RC1-20)
    if (!e.querySelector('[aria-hidden=true]')) return e.textContent;
    const c = e.cloneNode(true); c.querySelectorAll('[aria-hidden=true]').forEach((x) => x.remove()); return c.textContent;
  },
  vis(e) {
    if (e.checkVisibility && !e.checkVisibility({ contentVisibilityAuto: true, visibilityProperty: true })) return false;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  },
  root(within) {
    if (!within) return document;
    const all = [...document.querySelectorAll(within)].filter((e) => this.vis(e));
    return all.length ? all[all.length - 1] : document;
  },
  find(sel, text, within) {
    const contains = text.startsWith('~'), want = contains ? text.slice(1) : text;
    return [...this.root(within).querySelectorAll(sel)].filter((e) => this.vis(e)
      && (contains ? this.name(e).includes(want) : this.name(e) === want));
  },
  rect(e) { e.scrollIntoView({ block: 'center', inline: 'center' }); const r = e.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2]; },
};
"""  # noqa: E501  (JS source embedded as data)

js = json.dumps    # a Python value as a JS literal
CLICKABLE = ("button, a, [role=tab], [role=treeitem], [role=menuitem], summary, label, "
             "input[type=checkbox], input[type=radio]")
FIELD = "input:not([type=checkbox]):not([type=radio]), textarea"


class Browser:
    """One headless Chrome page driven over the DevTools protocol, with the helpers the shots need."""

    def __init__(self, ws, out):
        """`ws` is the page's DevTools websocket; PNGs are written into the directory `out`."""
        self.ws, self.out, self.n, self.waiters, self.shots = ws, out, 0, {}, []

    async def reader(self):
        """Resolve each command's waiter from its reply until the socket closes; events are dropped."""
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                fut = self.waiters.pop(msg.get("id"), None)
                if fut is not None:
                    fut.set_result(msg)
        except websockets.ConnectionClosed:
            pass

    async def call(self, method, **params):
        """Send one DevTools command and return its result; RuntimeError if Chrome answers with an error."""
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiters[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        msg = await fut
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    async def js(self, expr):
        """Evaluate `expr` in the page (promises awaited) and return its value; RuntimeError if it threw."""
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", str(r)))
        return r["result"].get("value")

    async def viewport(self, height=HEIGHT, width=WIDTH):
        """Set the CSS viewport size (device scale stays 2)."""
        await self.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                        deviceScaleFactor=SCALE, mobile=False)

    async def goto(self, path):
        """Navigate to a path of the GUI and give the page a moment to render."""
        await self.call("Page.navigate", url=GUI + path)
        await asyncio.sleep(1.2)

    async def wait(self, expr, timeout=20, what=None):
        """Poll the JS expression until it is truthy; TimeoutError (naming `what`) after `timeout` seconds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if await self.js(expr):
                return
            await asyncio.sleep(0.15)
        r = await self.call("Page.captureScreenshot", format="png")      # what the page looked like
        (self.out / "zz-timeout.png").write_bytes(b64decode(r["data"]))
        raise TimeoutError(what or expr)

    async def exists(self, text, sel=CLICKABLE, within=None):
        """True when a visible element of `sel` is named `text` (see LIB)."""
        return await self.js(f"S.find({js(sel)}, {js(text)}, {js(within)}).length > 0")

    async def wait_for(self, text, sel=CLICKABLE, within=None, timeout=20):
        """Wait until an element named `text` is visible."""
        await self.wait(f"S.find({js(sel)}, {js(text)}, {js(within)}).length > 0", timeout, f"{sel} {text!r}")

    async def wait_gone(self, text, sel=CLICKABLE, within=None, timeout=20):
        """Wait until no element named `text` is visible."""
        await self.wait(f"S.find({js(sel)}, {js(text)}, {js(within)}).length == 0", timeout, f"gone {text!r}")

    async def wait_text(self, text, timeout=20):
        """Wait until the visible page text contains `text`."""
        await self.wait(f"document.body.innerText.includes({js(text)})", timeout, f"text {text!r}")

    async def outline(self, within=None):
        """The visible controls as `tag[type] name`, one per line: what a step can click or fill."""
        return await self.js(f"""[...S.root({js(within)}).querySelectorAll('button, a, input, select, textarea, [role=tab], [role=treeitem], summary')]
          .filter((e) => S.vis(e)).map((e) => e.tagName.toLowerCase() + (e.type ? '[' + e.type + ']' : '') + ' ' + S.name(e)).join('\\n')""")  # noqa: E501

    async def mouse(self, x, y, button="left", clicks=1):
        """Move to (x, y) and click there (CSS pixels) with real mouse events."""
        await self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            await self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, clickCount=clicks,
                            buttons=1 if button == "left" else 2)

    async def click(self, text, sel=CLICKABLE, within=None, nth=0, button="left", clicks=1, wait=True):
        """Click the `nth` visible element of `sel` named `text`, after waiting for it to show up."""
        if wait:
            await self.wait_for(text, sel, within)
        x, y = await self.js(f"S.rect(S.find({js(sel)}, {js(text)}, {js(within)})[{nth}])")
        await self.mouse(x, y, button, clicks)
        await asyncio.sleep(0.25)

    async def click_css(self, css, nth=0, clicks=1):
        """Click the `nth` visible element matching a CSS selector; `clicks` 2 is a double click."""
        shown = f"[...document.querySelectorAll({js(css)})].filter((e) => S.vis(e))"
        await self.wait(f"{shown}.length > {nth}", 20, css)
        x, y = await self.js(f"S.rect({shown}[{nth}])")
        for n in range(1, clicks + 1):
            await self.mouse(x, y, "left", n)
        await asyncio.sleep(0.3)

    async def hover(self, text, sel=CLICKABLE, within=None):
        """Move the pointer over an element (for tooltips and hover states)."""
        x, y = await self.js(f"S.rect(S.find({js(sel)}, {js(text)}, {js(within)})[0])")
        await self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    async def key(self, key, modifiers=0, text=None):
        """Press and release a key. `key` is a DOM key name; modifiers: 2 ctrl, 8 shift, 1 alt."""
        codes = {"Enter": 13, "Escape": 27, "Backspace": 8, "Tab": 9, "ArrowDown": 40, "ArrowUp": 38}
        base = {"key": key, "windowsVirtualKeyCode": codes.get(key, ord(key.upper()) if len(key) == 1 else 0),
                "modifiers": modifiers}
        await self.call("Input.dispatchKeyEvent", type="keyDown", **({"text": text} if text else {}), **base)
        await self.call("Input.dispatchKeyEvent", type="keyUp", **base)
        await asyncio.sleep(0.2)

    async def fill(self, text, value, within=None, sel=FIELD):
        """Focus the field named `text`, replace its content with `value`, as typing would."""
        await self.click(text, sel, within)
        await self.js("document.activeElement.select && document.activeElement.select()")
        if value == "":
            await self.key("Backspace")
        else:
            await self.call("Input.insertText", text=value)
        await asyncio.sleep(0.2)

    async def choose(self, text, option, within=None):
        """Set the <select> named `text` to the option whose label (or value) is `option`."""
        await self.wait_for(text, "select", within)
        ok = await self.js(f"""(() => {{
          const s = S.find('select', {js(text)}, {js(within)})[0];
          const o = [...s.options].find((x) => x.label === {js(option)} || x.value === {js(option)});
          if (!o) return false;
          Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(s, o.value);
          s.dispatchEvent(new Event('change', {{ bubbles: true }})); return true; }})()""")
        if not ok:
            raise RuntimeError(f"select {text!r} has no option {option!r}")
        await asyncio.sleep(0.25)

    async def scroll(self, sel, top):
        """Scroll the first element matching the CSS selector `sel` to `top` (px, or 'end')."""
        pos = "el.scrollHeight" if top == "end" else str(top)
        await self.js(f"(() => {{ const el = document.querySelector({js(sel)}); el.scrollTop = {pos}; }})()")
        await asyncio.sleep(0.3)

    async def shot(self, name):
        """Write the viewport as <out>/<name>.png."""
        await asyncio.sleep(0.4)
        r = await self.call("Page.captureScreenshot", format="png")
        (self.out / f"{name}.png").write_bytes(b64decode(r["data"]))
        self.shots.append(name)
        print(f"shot {name}", flush=True)

    async def clip_shot(self, name, css, pad=8):
        """Write just the first element matching `css` (plus `pad` px) as <out>/<name>.png."""
        await asyncio.sleep(0.4)
        r = await self.js(f"(() => {{ const r = document.querySelector({js(css)}).getBoundingClientRect();"
                          f" return [r.left, r.top, r.width, r.height]; }})()")
        x, y, w, h = r
        clip = {"x": max(x - pad, 0), "y": max(y - pad, 0), "width": w + 2 * pad, "height": h + 2 * pad,
                "scale": 1}
        shot = await self.call("Page.captureScreenshot", format="png", clip=clip)
        (self.out / f"{name}.png").write_bytes(b64decode(shot["data"]))
        self.shots.append(name)
        print(f"shot {name}", flush=True)

    async def tall_shot(self, name, height):
        """A shot at another viewport height, then back to the standard one."""
        await self.viewport(height)
        await self.shot(name)
        await self.viewport()


class Backend:
    """The `trex-web-gui serve` child: a data dir of its own, a known token, restartable."""

    def __init__(self, scratch):
        """Choose the data dir under `scratch` and a fresh access token."""
        self.data, self.log = scratch / "data", scratch / "backend.log"
        self.token, self.proc, self.logf = secrets.token_urlsafe(32), None, None

    def start(self):
        """Start the child and wait until it answers; fail at once if it exits or the port is taken."""
        if port_in_use(GUI_PORT):
            raise RuntimeError(f"port {GUI_PORT} is already in use; stop what serves it first")
        env = {**os.environ, "TREX_WEB_GUI_TOKEN": self.token}
        self.logf = open(self.log, "a")
        self.proc = subprocess.Popen(
            [str(REPO / "backend/.venv/bin/python"), "-I", "-B", str(REPO / "vm/shoot_serve.py"), "serve",
             "--port", str(GUI_PORT), "--data-dir", str(self.data),
             "--server", f"127.0.0.1:{SYNC_PORT}:{ASYNC_PORT}"],
            stdout=self.logf, stderr=subprocess.STDOUT, env=env, cwd=str(REPO))
        for _ in range(100):
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited with {self.proc.returncode}; see {self.log}")
            try:
                if urllib.request.urlopen(f"{GUI}/api/auth/status").status == 200:
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"backend did not answer; see {self.log}")

    def stop(self):
        """SIGINT the child (it closes its TRex session first) and wait for it."""
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        if self.logf is not None:
            self.logf.close()
            self.logf = None


def check_status(r, what, ok=(200,)):
    """Raise RuntimeError unless the reply's status is in ok (a real error: `python -O` strips asserts)."""
    if r.status_code not in ok:
        raise RuntimeError(f"{what}: {r.status_code} {r.text}")


class Api:
    """The backend's REST API with the login cookie, for set-up that is not the shot's subject."""

    def __init__(self, token):
        """Log in with the token (the cookie stays in the client)."""
        self.h = httpx.Client(base_url=GUI, timeout=60)
        r = self.h.get("/api/auth/login", params={"token": token}, follow_redirects=False)
        check_status(r, "login", (302, 303, 307))

    def post(self, path, body=None):
        """POST JSON and return the parsed reply; RuntimeError (with the body) on a non-200."""
        r = self.h.post(path, json=body)
        check_status(r, path)
        return r.json()

    def get(self, path):
        """GET and return the parsed reply."""
        r = self.h.get(path)
        check_status(r, path)
        return r.json()


def qemu(cmd):
    """Send one command to the guest's QEMU monitor (`stop` pauses the guest, `cont` resumes it)."""
    import socket
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(str(guestenv.vm_dir() / "monitor.sock"))
    sock.settimeout(2)
    sock.sendall((cmd + "\n").encode())
    time.sleep(0.5)
    sock.close()


def vm_script(*args):
    """Run a vm/ script from the repo (it reads the guest from the environment, jammy by default);
    RuntimeError with its output when it fails."""
    r = subprocess.run([str(REPO / "vm" / args[0]), *args[1:]], cwd=REPO, capture_output=True, text=True,
                       timeout=180, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: rc {r.returncode}\n{r.stdout}{r.stderr}")
    return r.stdout


def port_in_use(port):
    """True when something already accepts connections on 127.0.0.1:`port`."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


MARKER = ".shoot_inventory_scratch"


def prepare_scratch(scratch):
    """Refuse a scratch dir that could hold something that matters, then empty it and mark it as ours.
    Refused: the repo, a parent or a child of it, $HOME, a parent of $HOME, and any non-empty directory that
    does not carry the marker this tool writes."""
    home = Path.home().resolve()
    if scratch == REPO or REPO.is_relative_to(scratch) or scratch.is_relative_to(REPO):
        raise SystemExit(f"refusing scratch dir {scratch}: it is, contains or lies inside the repo {REPO}")
    if scratch == home or home.is_relative_to(scratch):
        raise SystemExit(f"refusing scratch dir {scratch}: it is or contains $HOME")
    if scratch.exists() and not scratch.is_dir():
        raise SystemExit(f"refusing scratch dir {scratch}: not a directory")
    if scratch.is_dir() and any(scratch.iterdir()):
        if not (scratch / MARKER).is_file():
            raise SystemExit(f"refusing scratch dir {scratch}: not empty and has no {MARKER} (not ours)")
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / MARKER).write_text("made by vm/shoot_inventory.py; safe to empty\n")


def stage_profiles(scratch):
    """Copy the fixture profiles the shoot uses into <scratch>/profiles and return that directory."""
    dest = scratch / "profiles"
    dest.mkdir()
    for name in SHOT_PROFILES:
        shutil.copy(FIXTURES / name, dest / name)
    return dest


def find_target():
    """The first page target of the Chrome we started."""
    for _ in range(100):
        try:
            for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json")):
                if t["type"] == "page":
                    return t
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Chrome did not start")


# --------------------------------------------------------------------------------------------------------
# The steps. Each takes the context `c` (c.b browser, c.be backend, c.api) and takes its shots in order.
# --------------------------------------------------------------------------------------------------------

class Ctx:
    """What the steps share: browser, backend, API client and the scratch directory."""

    def __init__(self, b, be, scratch):
        """Hold the pieces; `api` is set once the shoot is logged in."""
        self.b, self.be, self.scratch, self.api = b, be, scratch, None
        self.profiles = scratch / "profiles"     # the staged copies of the fixture profiles (stage_profiles)


async def step_shell(c):
    """01-02: the Not authorised page (no cookie yet), then the disconnected Control page."""
    b = c.b
    await b.goto("/")
    await b.wait_text("Not authorised")
    await b.shot("01-not-authorised")
    await b.goto(f"/api/auth/login?token={c.be.token}")
    await b.wait_for("Control", "a")
    await b.js("localStorage.setItem('trex-web-gui.theme', 'light')")
    await b.goto("/control")
    await b.shot("02-control-disconnected")


async def step_disconnected(c):
    """Pages shot before any connect: Dashboard and Capture, the Setups menu and the empty dialog."""
    b = c.b
    await b.click("Dashboard", "a")
    await b.shot("34-dash-disconnected")
    await b.click("Capture", "a")
    await b.shot("43-capture-disconnected")
    await b.click("Control", "a")
    await b.click("Setups ▾", "button")
    await b.shot("64-setups-menu-disconnected")
    await b.click("Manage setups…", "[role=menuitem]")
    await b.shot("66-manage-setups-empty")
    await b.key("Escape")


async def step_connect(c):
    """03-07: the Connect dialog (Advanced shut and open), connecting, a failed connect, Save as…; then a
    good connect."""
    b = c.b
    await b.click("Connect…", "button")
    await b.shot("03-connect-dialog")
    await b.click("~Advanced", "summary")
    await b.shot("04-connect-advanced")
    await b.fill("Host", "10.255.255.1", "[role=dialog]")
    await b.fill("Timeout (s)", "20", "[role=dialog]")
    await b.click("Connect", "button", "[role=dialog]")
    await b.wait_for("Connecting…", "footer [role=status], button")
    await b.shot("05-connect-connecting")
    await b.wait("S.find('[role=alert]', '~', '[role=dialog]').length > 0", 40, "connect error")
    await b.shot("06-connect-error")
    await b.click("Save as…", "button", "[role=dialog]")
    await b.fill("Name", "unreachable", "[role=dialog]")
    await b.click("Save", "button", "[role=group]")
    await b.wait_for("Delete", "button", "[role=dialog]")
    await b.shot("07-connect-saved")
    await b.fill("Host", "127.0.0.1", "[role=dialog]")
    await b.fill("Timeout (s)", "3", "[role=dialog]")
    await b.click("Connect", "button", "[role=dialog]")
    await b.wait_for("Disconnect", "button")
    await b.wait_gone("Connect to TRex", "[role=dialog]")
    c.api = Api(c.be.token)
    # Browse… starts in the workspace dir, else $HOME: the staged profiles, so no screen lists the home dir
    cur = c.api.get("/api/settings")["settings"]
    cur["preferences"]["workspace_dir"] = str(c.profiles)
    check_status(c.api.h.put("/api/settings", json=cur), "PUT /api/settings")


async def step_control(c):
    """08-16: the connected Control page, both context menus, Start all, and the port's Control tab."""
    b, api = c.b, c.api
    # A run starts from clean ports: an earlier shoot may have left traffic running on the guest.
    api.post("/api/ports/acquire", {"ports": [0, 1], "force": True})
    api.post("/api/ports/stop", {"ports": [0, 1]})
    api.post("/api/ports/reset", {"ports": [0, 1]})
    await b.wait_for("~Port 0, IDLE, owned by you", "[role=treeitem]")
    await b.click("Setups ▾", "button")
    await b.click("Save current…", "[role=menuitem]")
    await b.shot("67-save-setup-no-profile")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.click("~Port 1", "[role=treeitem]")
    await b.click("Control", "[role=tab]")
    await b.clip_shot("85-chips-idle", ".chips")
    api.post("/api/ports/assign", {"ports": [0], "path": str(c.profiles / "flow_latency.py"), "tunables": []})
    api.post("/api/ports/assign", {"ports": [1], "path": str(c.profiles / "udp_for_benchmarks.py"),
                                   "tunables": ["--packet_len", "128"]})
    await b.click(TREE_ROOT, "[role=treeitem]")
    await b.shot("08-control-root-connected")
    await b.click(TREE_ROOT, "[role=treeitem]", button="right")
    await b.shot("09-context-menu-root")
    await b.key("Escape")
    await b.click("~Port 0", "[role=treeitem]", button="right")
    await b.shot("10-context-menu-port")
    await b.key("Escape")
    await b.click("Start all", "button", "[role=toolbar]")
    await b.shot("11-start-all-dialog")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.click("Profile _ (STREAMS)", "[role=treeitem]")
    await b.shot("12-control-profile-selected")
    await b.click("~Port 0", "[role=treeitem]")
    await b.fill("Name", "Generator", "[role=tabpanel]")
    await b.fill("Description", "sends to port 1", "[role=tabpanel]")
    await b.click("Save", "button", "[role=tabpanel]")
    await b.wait_for("~Port 0 — Generator", "[role=treeitem], h2, h3")
    await b.shot("13-port-control-top")
    await b.click("~Port details", "summary")
    await b.shot("14-port-control-details")
    await b.click("~Port details", "summary")
    await b.click("Filtered", "input[type=radio]")
    await b.click("Reset…", "button", "[role=tabpanel]")
    await b.scroll("main", "end")
    await b.shot("15-port-control-bottom")
    await b.click("Off", "input[type=radio]")
    await b.scroll("main", 0)


async def step_port_tabs(c):
    """16-27: Config, Tools (Ping, IPv6 neighbours), Traffic (empty, Browse, inspected, transmitting, update)
    and Counters; 115, Traffic's floating ⓘ note."""
    b = c.b
    await b.click("Config", "[role=tab]")
    await b.shot("16-port-config-top")
    await b.click("L2", "input[type=radio]")
    await b.shot("17-port-config-l2")
    await b.click("Revert", "button", "[role=tabpanel]")
    await b.click("Tools", "[role=tab]")
    await b.shot("18-port-tools-ping-empty")
    await b.fill("Destination", "2.2.2.2", "[role=tabpanel]")
    await b.fill("Count", "3", "[role=tabpanel]")
    await b.click("Ping", "button", "[role=tabpanel]")
    await b.wait_text("received")
    await b.shot("19-port-tools-ping")
    await b.click("IPv6 neighbours", "button")
    await b.click("Scan", "button", "[role=tabpanel]")
    await b.wait_for("Scan", "button", "[role=tabpanel]")
    await asyncio.sleep(3)
    await b.shot("20-port-tools-neighbours")
    await b.click("~Port 1", "[role=treeitem]")
    await b.click("Traffic", "[role=tab]")
    await b.shot("21-port-traffic-empty")
    await b.click("Browse…", "button")
    await b.shot("22-browse-dialog")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.fill("Profile path", str(c.profiles / "udp_for_benchmarks.py"))
    await b.click("Inspect", "button", "[role=tabpanel]")
    await b.wait(SOME_TABLE, 20, "streams table")
    await b.fill("Multiplier", "5kpps")
    await b.click("Show rate at ×1", "button")
    await b.click("Assign", "button", "[role=tabpanel]")
    await asyncio.sleep(1.5)
    await b.shot("23-port-traffic-inspected")
    await b.click("About profile paths", "button", "[role=tabpanel]")      # the floating ⓘ note (RC1-34)
    await b.wait("document.querySelector('[role=note]:not([hidden])') !== null", 10, "the profile paths note")
    await b.shot("115-traffic-profile-note")
    await b.click("About profile paths", "button", "[role=tabpanel]")
    await b.click("Start", "button", "[role=tabpanel]")
    await b.wait_text("TX")
    await b.shot("24-port-traffic-transmitting")
    await b.fill("Multiplier", "8kpps")
    await b.key("Enter")               # typing alone never starts the countdown; Enter commits
    await b.wait_text("Applying")
    await b.shot("25-port-traffic-update")
    await b.click("Apply now", "button")
    await b.click("Counters", "[role=tab]")
    await asyncio.sleep(1.5)
    await b.shot("26-port-counters")
    await b.fill("Filter", "q0")
    await b.click("Pin tx_q0_packets", "input[type=checkbox]")
    await b.shot("27-port-counters-filtered")
    await b.fill("Filter", "")


async def step_traffic(c):
    """Live traffic on both ports for the pages that follow: port 0 flow_latency (PG 5 flow, PG 7 latency),
    port 1 keeps the benchmark profile it is already sending. 85-87: the Control tab's chips in each state."""
    b, api = c.b, c.api
    api.post("/api/ports/start", {"ports": [0], "mult": "1.1kpps", "duration": -1})
    await asyncio.sleep(3)
    await b.click("~Port 0", "[role=treeitem]")
    await b.click("Control", "[role=tab]")
    await b.clip_shot("86-chips-tx", ".chips")
    api.post("/api/ports/pause", {"ports": [0]})
    await b.wait_text("State PAUSE")
    await b.clip_shot("87-chips-pause", ".chips")
    api.post("/api/ports/resume", {"ports": [0]})
    await b.wait_text("State TX")


async def step_events(c):
    """28-30: the Events page (filters, MARK rows), filtered, and the RPC view with Capture on."""
    b, api = c.b, c.api
    api.post("/api/ports/pause", {"ports": [1]})
    api.post("/api/ports/resume", {"ports": [1]})
    await b.click("~Events", "a")
    await b.shot("28-events-page")
    await b.click("~Server events", "button")
    await b.shot("29-events-filtered")
    await b.click("~All", "button")
    await b.click("RPC", "[role=tab]")
    await b.click("Capture", "label")
    api.post("/api/ports/pause", {"ports": [1]})
    api.post("/api/ports/resume", {"ports": [1]})
    await asyncio.sleep(1)
    await b.shot("30-events-rpc")
    await b.click("Events", "[role=tab]")


async def step_dashboard(c):
    """31-39: every Dashboard tab with traffic on both ports."""
    b = c.b
    await b.click("Dashboard", "a")
    await asyncio.sleep(8)
    await b.shot("35-dash-overview-top")
    await b.choose("Layout", "4 charts")
    await b.shot("36-dash-overview-4charts")
    await b.choose("Layout", "2 charts")
    await b.click("Streams", "[role=tab]")
    await asyncio.sleep(3)
    await b.shot("37-dash-streams")
    await b.click("Latency", "[role=tab]")
    await asyncio.sleep(2)
    await b.shot("38-dash-latency-top")
    await b.scroll("main", "end")
    await b.shot("39-dash-latency-bottom")
    await b.scroll("main", 0)
    await b.click("Utilization", "[role=tab]")
    await asyncio.sleep(2)
    await b.shot("40-dash-utilization")
    await b.click("Overview", "[role=tab]")


async def ensure_traffic(c):
    """Make sure both ports are transmitting (a lost session may have stopped them): port 0 at 1.1 kpps,
    port 1 at 5."""
    for p in c.api.get("/api/ports")["ports"]:
        if p["state"] not in ("TX", "PAUSE"):
            rate = "1.1kpps" if p["id"] == 0 else "5kpps"
            c.api.post("/api/ports/start", {"ports": [p["id"]], "mult": rate, "duration": -1})
    await asyncio.sleep(1)


async def step_lost(c):
    """41-42, 31-33: the guest is paused (QEMU `stop`): stale, then connection lost on the Dashboard; the
    Control page and status bar in the lost state; a Reconnect that fails (the bar reads `Disconnected:` with
    the error and still offers See Events, Reconnect and Connect…); then the guest resumes, Reconnect
    succeeds, and the Events page shows the divider rows."""
    b, api = c.b, c.api
    qemu("stop")
    try:
        await asyncio.sleep(5.5)
        await b.shot("41-dash-stale")
        await b.wait_text("Connection lost", 40)
        await asyncio.sleep(1)
        await b.shot("42-dash-connection-lost")
        await b.click("Control", "a")
        await b.shot("31-control-connection-lost")
        await b.click("Reconnect", "button")
        # a failed Reconnect leaves the session disconnected with the error; the bar keeps the lost buttons
        await b.wait(f"{FOOTER}.startsWith('Disconnected: ')", 90, "failed reconnect")
        await b.wait_for("Reconnect", "button", "footer")
        await asyncio.sleep(1)
        print("status bar after the failed Reconnect:", await b.js(FOOTER), flush=True)
        await b.shot("32-statusbar-reconnect-failed")
    finally:
        qemu("cont")
    await asyncio.sleep(2)
    await b.click("Reconnect", "button", "footer")      # the guest answers again
    await b.wait_for("Disconnect", "button", "footer")
    api.post("/api/ports/acquire", {"ports": [0, 1], "force": True})
    await asyncio.sleep(1)
    await ensure_traffic(c)
    await b.click("~Events", "a")
    await b.shot("33-events-marks")


async def step_capture(c):
    """44-50: the Capture page tabs: Live (set up, running, the packet viewer), Recorders, Saved files,
    LLDP."""
    b, api = c.b, c.api
    await b.click("Capture", "a")
    await b.click("Live", "[role=tab]")
    await b.click("Port 1", "input[type=checkbox]", '[role=group][aria-label="Rx ports"]')
    await b.fill("BPF filter", "udp")
    await b.shot("44-capture-live-setup")
    await b.click("~Turn service mode on", "button")
    await asyncio.sleep(1)
    await b.click("Start", "button", "[role=tabpanel]")
    await asyncio.sleep(4)
    await b.shot("45-capture-live-running")
    await b.click_css("table tbody tr", 0, clicks=2)
    await b.wait_for("Copy hex", "button")
    await b.click("~src", "button, [role=treeitem], summary, span")
    await b.shot("46-packet-viewer")
    await b.click("Close", "button", "[role=dialog]")
    await b.click("Stop", "button", "[role=tabpanel]")
    await b.click("~Recorders", "[role=tab]")
    await b.click("Add recorder", "button")
    await b.click("Port 1", "input[type=checkbox]", '[role=dialog] [role=group][aria-label="Rx ports"]')
    await b.fill("Limit", "200", "[role=dialog]")
    await b.shot("47-add-recorder-dialog")
    await b.click("OK", "button", "[role=dialog]")
    await asyncio.sleep(3)
    await b.click("~Stop", "button", "[role=tabpanel]")
    await asyncio.sleep(1)
    await b.click("LLDP", "[role=tab]")
    await b.click("Port 1", "input[type=checkbox]", "[role=tabpanel]")
    await b.click("Start LLDP monitor", "button")
    await asyncio.sleep(2)
    await b.shot("50-capture-lldp")
    await b.click("~Recorders", "[role=tab]")
    await b.shot("48-capture-recorders")
    await b.click("Save", "button", "[role=tabpanel]")
    await asyncio.sleep(2)
    await b.click("~Saved files", "[role=tab]")
    await b.shot("49-capture-saved-files")
    api.h.delete("/api/capture/monitor")


async def save_setup(b, name, stop_first=False, shot=None):
    """Setups ▾, Save current…, name it, tick the three options (stop-first only when asked), Save."""
    await b.click("Setups ▾", "button")
    await b.click("Save current…", "[role=menuitem]")
    await b.fill("Name", name, "[role=dialog]")
    for label in ("~include port configuration", "~include dashboard layout"):
        await b.click(label, "label", "[role=dialog]")
    if stop_first:
        await b.click("~stop a transmitting port", "label", "[role=dialog]")
    if shot:
        await b.shot(shot)
    await b.click("Save", "button", "[role=dialog]")
    await b.wait_gone("Save setup", "[role=dialog]")


async def open_manage(b):
    """Setups ▾, Manage setups…"""
    await b.click("Setups ▾", "button")
    await b.click("Manage setups…", "[role=menuitem]")
    await b.wait_for("Close", "button", "[role=dialog]")


async def set_rate(b, mult, unit):
    """Set the Traffic tab's rate (the ports are stopped, so no live-update countdown starts). The Multiplier
    box takes the rate as one text (RC1-36): `5kpps`, or a bare number for a factor (`unit` "x")."""
    await b.fill("Multiplier", mult if unit == "x" else f"{mult}{unit}")


async def pick(b, name):
    """Choose a setup in the Test setups list."""
    await b.click(f"~{name}", "[role=option]", "[role=dialog]")


async def step_setups(c):
    """65-82: the Setups menu, Save current setup (refused port, filled, overwrite), the Test setups dialog
    (list, loaded, edits since Load, Run failed at assign, Run finished, Run failed at start, Stop started
    ports, duplicate, delete, another server, a 540 px tall window)."""
    b, api = c.b, c.api
    api.post("/api/ports/stop", {"ports": [0, 1]})
    api.post("/api/ports/service", {"ports": [1], "enabled": False})      # the capture steps turned it on
    await b.click("Control", "a")
    await b.click("Setups ▾", "button")
    await b.shot("65-setups-menu")
    await b.key("Escape")
    await b.click("~Port 0", "[role=treeitem]")
    await b.click("Traffic", "[role=tab]")
    await b.fill("Profile path", str(c.profiles / "flow_latency.py"))
    await b.click("Inspect", "button", "[role=tabpanel]")
    await b.wait(SOME_TABLE, 20, "streams table")
    await b.click("~Port 1", "[role=treeitem]")
    await b.click("Traffic", "[role=tab]")      # a reconnect empties the forms: port 1 gets its profile again
    await b.fill("Profile path", str(c.profiles / "udp_for_benchmarks.py"))
    await b.click("Inspect", "button", "[role=tabpanel]")
    await b.wait("[...document.querySelectorAll('table')].some((t) => S.vis(t))", 20, "streams table")
    await set_rate(b, "5", "kpps")
    await b.fill("Multiplier", "")
    await b.click("Setups ▾", "button")
    await b.click("Save current…", "[role=menuitem]")
    await b.shot("69-save-setup-port-refused")
    await b.click("Cancel", "button", "[role=dialog]")
    await set_rate(b, "5", "kpps")
    await save_setup(b, "Lab rig", shot="68-save-setup-filled")
    await b.click("Setups ▾", "button")
    await b.click("Save current…", "[role=menuitem]")
    await b.fill("Name", "lab RIG", "[role=dialog]")
    await b.click("Save", "button", "[role=dialog]")
    await b.shot("70-save-setup-overwrite")
    await b.click("Cancel", "button", "[role=dialog]")
    await save_setup(b, "Stop first rig", stop_first=True)
    # a third setup that asks 100 Gbps of port 1, which TRex refuses on a 10 Gbps port
    await set_rate(b, "100", "gbps")
    await save_setup(b, "Too fast", stop_first=True)
    await set_rate(b, "5", "kpps")
    # one saved for another server: the first setup, re-pointed
    first = api.get("/api/test-setups/lab-rig")
    other = {**first, "name": "Other lab", "server": "10.1.2.3:4501"}
    r = api.h.put("/api/test-setups/other-lab", json=other)
    check_status(r, "PUT /api/test-setups/other-lab")
    await open_manage(b)
    await b.shot("71-manage-setups-list")
    await b.click("Load", "button", "[role=dialog]")
    await b.wait_text("ready")
    await b.shot("72-manage-setups-loaded")
    await b.click("Close", "button", "[role=dialog]")
    await set_rate(b, "8", "kpps")      # an edit in the Traffic tab since Load (port 1 is still selected)
    await open_manage(b)
    await b.wait_for("Save and Run", "button", "[role=dialog]")
    await b.shot("73-manage-setups-edits-since-load")
    await b.click("Close", "button", "[role=dialog]")
    api.post("/api/ports/start", {"ports": [0], "mult": "1kpps", "duration": -1})
    api.post("/api/ports/start", {"ports": [1], "mult": "5kpps", "duration": -1})
    await asyncio.sleep(1)
    await open_manage(b)
    # both ports transmit now and this setup does not stop first, so the run fails at its first phase (layer)
    await b.click("Run as saved", "button", "[role=dialog]")
    await b.wait_text("failed at")   # not scrolled: the failed port's row scrolls itself into view
    await b.shot("76-manage-setups-run-failed-assign")
    await set_rate_closing(b, "5", "kpps")
    await open_manage(b)
    await pick(b, "Stop first rig")
    await b.click("Load", "button", "[role=dialog]")
    await b.wait_text("ready")
    await b.click("Run", "button", "[role=dialog]")
    await b.wait_text("Run finished")
    await b.shot("75-manage-setups-run-finished")
    await pick(b, "Too fast")
    await b.click("Load", "button", "[role=dialog]")
    await b.wait_text("ready")
    await b.click("Run", "button", "[role=dialog]")
    await b.wait_for("Stop started ports", "button", "[role=dialog]", timeout=40)
    await b.shot("77-manage-setups-run-failed-start")
    await b.click("Stop started ports", "button", "[role=dialog]")
    await b.wait_gone("Stop started ports", "button", "[role=dialog]")
    await b.shot("78-manage-setups-started-stopped")
    await pick(b, "Lab rig")
    await b.click("Duplicate", "button", "[role=dialog]")
    await b.shot("79-manage-setups-duplicate")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.click("Delete", "button", "[role=dialog]")
    await b.shot("80-manage-setups-delete-confirm")
    await b.click("Cancel", "button", "[role=dialog]")
    await pick(b, "Other lab")
    await b.click("Load", "button", "[role=dialog]")
    await b.wait_text("ready")
    await b.shot("81-manage-setups-other-server")
    await b.tall_shot("82-manage-setups-540px", 540)
    await b.click("Close", "button", "[role=dialog]")


async def set_rate_closing(b, mult, unit):
    """Close the Test setups dialog, restore the Traffic tab's rate, and leave the form as it was."""
    await b.click("Close", "button", "[role=dialog]")
    await set_rate(b, mult, unit)


FOOTER = "document.querySelector('footer').innerText"      # the status bar's text
SOME_TABLE = "[...document.querySelectorAll('table')].some((t) => S.vis(t))"   # a streams table is on screen
# the Studio stream table's visible rows
STREAM_ROWS = "[...document.querySelectorAll('table.stream-table tbody tr')].filter((r) => S.vis(r))"

# The import dialog's size against the viewport: does a body scroll, and is Apply on screen.
_IMPORT_FIT = """(() => { const d = document.querySelector('[role=dialog]');
  const r = d.getBoundingClientRect();
  const body = [...d.querySelectorAll('*')].find((e) => e.scrollHeight > e.clientHeight + 2
    && getComputedStyle(e).overflowY !== 'visible');
  return JSON.stringify({ dialog_h: r.height, viewport_h: innerHeight, scrolling_body: !!body,
    apply_visible: [...d.querySelectorAll('button')].some((x) => x.textContent === 'Apply'
      && x.getBoundingClientRect().bottom <= innerHeight) }) })()"""


async def set_theme(b, label):
    """Settings, Appearance, choose a theme."""
    await b.click("Settings", "a")
    await b.click("Appearance", "[role=tab]")
    await b.click(label, "input[type=radio]")


async def step_themes(c):
    """59-60: the Dashboard in the Dark and High contrast themes, with traffic on both ports; back to
    Light."""
    b, api = c.b, c.api
    api.post("/api/ports/start", {"ports": [0], "mult": "1.1kpps", "duration": -1})
    api.post("/api/ports/start", {"ports": [1], "mult": "5kpps", "duration": -1})
    for label, name in (("Dark", "59-dashboard-dark"), ("High contrast", "60-dashboard-high-contrast")):
        await set_theme(b, label)
        await b.click("Dashboard", "a")
        await asyncio.sleep(10)
        await b.shot(name)
    await set_theme(b, "Light")


async def set_input_file(b, path):
    """Put a file into the page's hidden file input, as the browser's chooser would."""
    doc = await b.call("DOM.getDocument")
    node = await b.call("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[type=file]")
    await b.call("DOM.setFileInputFiles", files=[str(path)], nodeId=node["nodeId"])


async def step_settings(c):
    """53-58: the Settings sections and the changed-on-the-server line; 61-63: the Import settings dialog."""
    b, api = c.b, c.api
    await b.click("Settings", "a")
    tabs = (("Appearance", "53-settings-appearance"), ("Files on the backend host", "54-settings-files"),
                ("Session", "55-settings-session"), ("Back up and restore", "56-settings-backup"),
                ("About", "57-settings-about"))
    for tab, name in tabs:
        await b.click(tab, "[role=tab]")
        await asyncio.sleep(0.5)
        await b.shot(name)
    # an unsaved edit here, then a different Wireshark path saved "by another tab"
    await b.click("Files on the backend host", "[role=tab]")
    await b.fill("Workspace directory", "/srv/trex-workspace")
    cur = api.get("/api/settings")["settings"]
    cur["preferences"]["wireshark_path"] = "/opt/wireshark/bin/wireshark"
    r = api.h.put("/api/settings", json=cur)
    check_status(r, "PUT /api/settings")
    await b.wait_text("Settings changed on the server")
    await b.shot("58-settings-changed-on-server")
    await b.click("Reload", "button")
    # the file to import: exported now, then the data here is changed, and two values in the file are edited
    bundle = api.get("/api/settings/bundle")
    bundle["sections"]["theme"] = "dark"
    api.h.delete("/api/test-setups/stop-first-rig")
    extra = api.get("/api/test-setups/lab-rig")
    api.h.put("/api/test-setups/extra-rig", json={**extra, "name": "Extra rig"})
    cur = api.get("/api/settings")["settings"]
    cur["preferences"]["templates_dir"] = "/srv/templates"
    api.h.put("/api/settings", json=cur)
    sections = bundle["sections"]["test_setups"]
    items = sections if isinstance(sections, list) else list(sections.values())
    for item in items:
        if item.get("name") == "Lab rig":
            item["ports"][0]["force"] = True
            item["ports"][0]["profile"]["path"] = "/srv/profiles/flow_latency.py"
    path = c.scratch / "import.json"
    path.write_text(json.dumps(bundle, indent=2))
    await b.goto("/settings")
    await b.click("Back up and restore", "[role=tab]")
    await set_input_file(b, path)
    await b.wait_for("Import settings", "h2", "[role=dialog]")
    await b.shot("61-import-preview")
    await b.choose("Preferences mode", "replace", "[role=dialog]")
    await b.choose("Test setups mode", "replace", "[role=dialog]")
    await b.click("Also apply theme", "label", "[role=dialog]")
    print("import dialog at 900:", await b.js(_IMPORT_FIT), flush=True)
    await b.shot("62-import-preview-replace")
    await b.viewport(540)
    await b.shot("84-import-preview-540px")
    print("import dialog at 540:", await b.js(_IMPORT_FIT), flush=True)
    await b.viewport()
    await b.choose("Preferences mode", "skip", "[role=dialog]")
    await b.choose("Test setups mode", "merge", "[role=dialog]")
    await b.click("Also apply theme", "label", "[role=dialog]")
    await b.click("Apply", "button", "[role=dialog]")
    await b.wait_gone("Import settings", "h2", "[role=dialog]")
    await b.wait_text("Imported")
    await b.shot("63-import-result")


def udp_frame(src=0x10000001, size=60):
    """An Ethernet/IPv4/UDP frame of `size` bytes from `src` (an integer) to 48.0.0.1, checksums left zero."""
    payload = b"x" * (size - 42)
    ip = struct.pack("!BBHHHBBHII", 0x45, 0, 20 + 8 + len(payload), 1, 0, 64, 17, 0, src, 0x30000001)
    udp = struct.pack("!HHHH", 1025, 12, 8 + len(payload), 0)
    return b"\x00\x11\x22\x33\x44\x55" + b"\x00\x66\x77\x88\x99\xaa" + b"\x08\x00" + ip + udp + payload


def write_pcap(path, frames, gap_usec=1000):
    """A classic libpcap file (Ethernet link type) holding `frames`, `gap_usec` apart; returns `path`."""
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    for i, f in enumerate(frames):
        t = 1_700_000_000 * 1_000_000 + i * gap_usec
        out.append(struct.pack("<IIII", t // 1_000_000, t % 1_000_000, len(f), len(f)) + f)
    path.write_bytes(b"".join(out))
    return path


async def step_studio(c):
    """88-94: Studio: the library with entries and the editor, a selected stream, its Properties and Packet
    tabs, the import dialog with the pcap wizard, the Simulate report; then Control's Library… pick."""
    b, api = c.b, c.api
    api.post("/api/ports/stop", {"ports": [0, 1]})       # the Traffic tab's Library… needs an idle port
    api.post("/api/studio/import", {"path": str(c.profiles / "udp_for_benchmarks.py"), "kind": "py",
                                    "tunables": ["--packet_len", "128", "--stream_count", "3"],
                                    "name": "UDP benchmark x3"})
    api.post("/api/studio/import", {"path": str(c.profiles / "flow_latency.py"), "kind": "py",
                                    "name": "Flow latency"})
    api.post("/api/studio/import", {"path": str(c.profiles / "gui_example.yaml"), "kind": "yaml",
                                    "name": "Legacy GUI example"})
    pcap = write_pcap(c.scratch / "two_udp.pcap", [udp_frame(), udp_frame(0x10000002)])
    await b.click("Studio", "a")
    await b.click("~UDP benchmark x3", "[role=option]")
    await b.wait(STREAM_ROWS + ".length > 0", 20, "stream table rows")
    await b.shot("88-studio-library")
    await b.click_css("table.stream-table tbody tr", 1)
    await b.wait_for("Properties", "[role=tab]")
    await b.shot("89-studio-stream-selected")
    await b.js("document.querySelector('.stream-detail').scrollIntoView({ block: 'start' })")
    await b.shot("90-studio-properties")
    await b.click("Packet", "[role=tab]")
    await b.shot("91-studio-packet")
    await b.scroll("main", 0)
    await b.click("Import…", "button")
    await b.fill("Path", str(pcap), "[role=dialog]")
    await b.fill("Name", "Two UDP packets", "[role=dialog]")
    await b.fill("Loops", "3", "[role=dialog]")
    await b.click("Rewrite source IPv4", "input[type=checkbox]", "[role=dialog]")
    await b.fill("Source start", "16.0.0.1", "[role=dialog]")
    await b.fill("Source count", "2", "[role=dialog]")
    await b.shot("92-studio-import-pcap")
    await b.click("Import", "button", "[role=dialog]")
    await b.wait_gone("Import a profile", "[role=dialog]", timeout=30)
    await b.wait_for("~Two UDP packets", "[role=option]")
    await b.click("Simulate", "button")
    await b.wait_for("Simulate", "[role=dialog]", timeout=30)
    await b.shot("93-studio-simulate")
    await b.click("Close", "button", "[role=dialog]")
    await b.click("Control", "a")
    await b.click("~Port 1", "[role=treeitem]")
    await b.click("Traffic", "[role=tab]")
    await b.click("Library…", "button")
    await b.wait_for("~UDP benchmark x3", "button", "[role=dialog]")
    await b.shot("94-control-library-pick")
    await b.click("Cancel", "button", "[role=dialog]")


async def step_packet(c):
    """107-114: the packet editor (P4b): the standalone page with a template, its Field engine tab, the
    payload editor, the presets, the pcap picker and Add to profile; then Studio's embedded editor and the
    .py profile's source dialog."""
    b = c.b
    await b.click("Studio", "a")
    await b.click("Packet editor", "a")
    await b.wait_for("File ▾", "button")
    await b.click("Templates…", "button")
    await b.wait_for("Templates", "[role=dialog]")
    await b.click("TCP-SYN", "button", "[role=dialog]")
    await b.wait_gone("Templates", "[role=dialog]")
    await b.wait_for("~TCP", "button.layer-title")
    await b.click("Expand all", "button")
    await b.wait_text("Destination")
    await b.shot("107-packet-standalone-template")
    await b.click("Presets…", "button")
    await b.wait_for("Presets", "[role=dialog]")
    await b.shot("108-packet-presets")
    await b.click("Open in editor", "button", "[role=dialog]", nth=1)       # the IP sweep
    await b.wait_gone("Presets", "[role=dialog]")
    await b.click("Field engine", "[role=tab]")
    await b.wait_for("~STLVmFlowVar", "h3")
    await b.shot("109-packet-field-engine")
    await b.click("Packet", "[role=tab]")
    await b.click("Expand all", "button")
    await b.click("Payload…", "button")
    await b.wait_for("Payload", "[role=dialog]")
    await b.shot("110-packet-payload-editor")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.click("File ▾", "button")
    await b.click("Open pcap…", "[role=menuitem]")
    await b.fill("Capture path", str(c.scratch / "two_udp.pcap"), "[role=dialog]")
    await b.click("List packets", "button", "[role=dialog]")
    await b.wait_for("Open packet 1", "button", "[role=dialog]")
    await b.shot("111-packet-pcap-picker")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.click("File ▾", "button")
    await b.click("Add to profile as a new stream…", "[role=menuitem]")
    await b.wait_for("Add to profile", "[role=dialog]")
    await b.shot("112-packet-add-to-profile")
    await b.click("Cancel", "button", "[role=dialog]")
    await b.click("Studio", "a")
    await b.click("~UDP benchmark x3", "[role=option]")
    await b.wait(STREAM_ROWS + ".length > 0", 20, "stream table rows")
    await b.click_css("table.stream-table tbody tr", 0)
    await b.click("Packet", "[role=tab]")
    await b.click("Edit packet", "button")
    await b.wait_for("Save", "button")
    await b.wait_text("Destination")
    await b.shot("113-studio-embedded-packet-editor")
    await b.click("Cancel", "button")
    await b.click("View source…", "button")
    await b.wait_for("Profile source", "[role=dialog]")
    await b.shot("114-studio-py-source")
    await b.click("Close", "button", "[role=dialog]")


async def step_monitor(c):
    """52, 95-96: the Monitor page: disconnected (the old placeholder's number), then connected to the
    guest's PUB port while both ports transmit: the Global and Ports panels of a stateless TRex, then
    Global's charts."""
    b = c.b
    await ensure_traffic(c)
    await b.click("Monitor", "a")
    await b.wait_for("Connect", "button", ".monitor-connect")
    await b.shot("52-monitor-disconnected")
    await b.fill("Host", "127.0.0.1", ".monitor-connect")
    await b.fill("Port", str(ASYNC_PORT), ".monitor-connect")
    await b.click("Connect", "button", ".monitor-connect")
    await b.wait_for("~Global", "button", ".monitor-grid")
    await b.wait_for("~Ports", "button", ".monitor-grid")
    await asyncio.sleep(8)                      # the charts need a few samples
    await b.shot("95-monitor-guest-global")
    await b.scroll("main", 760)                 # the Global panel's charts
    await b.shot("96-monitor-guest-charts")
    await b.scroll("main", 0)
    await b.click("Disconnect", "button", ".monitor-connect")


async def step_monitor_stateful(c):
    """97-99: the Monitor fed by fake_trex --pub-replay with the recorded stateful frames: the whole page
    (every panel) at a tall viewport, then the Inspector."""
    b = c.b
    for port in (FAKE_RPC_PORT, FAKE_PUB_PORT):
        if port_in_use(port):
            raise RuntimeError(f"port {port} is in use; the fake TRex needs 18500 and 18501")
    fake = subprocess.Popen([sys.executable, "-I", "-B", str(FAKE_TREX), str(FAKE_FIXTURES),
                             str(FAKE_RPC_PORT), str(FAKE_PUB_PORT), "--pub-replay", str(STATEFUL_REPLAY)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        ready = fake.stdout.readline() if fake.stdout else ""
        if not ready.startswith("[fake] ready"):
            raise RuntimeError(f"fake_trex did not start: {ready!r}")
        await b.click("Monitor", "a")
        await b.fill("Host", "127.0.0.1", ".monitor-connect")
        await b.fill("Port", str(FAKE_PUB_PORT), ".monitor-connect")
        await b.click("Connect", "button", ".monitor-connect")
        for panel in ("Global", "Ports", "Templates", "Latency", "Rx-check", "Latency v2", "Inspector"):
            await b.wait_for(f"~{panel}", "button", ".monitor-grid", timeout=40)
        await asyncio.sleep(6)
        await b.shot("97-monitor-stateful-top")
        await b.tall_shot("98-monitor-stateful-all", 3600)
        await b.js("document.getElementById('monitor-panel-inspector').scrollIntoView({ block: 'start' })")
        await b.shot("99-monitor-inspector")
        await b.click("Disconnect", "button", ".monitor-connect")
    finally:
        fake.terminate()
        try:
            fake.wait(10)
        except subprocess.TimeoutExpired:
            fake.kill()


async def step_daemon(c):
    """100-106: the TRex daemon dialog against the guest's daemon (`vm/daemon.sh start`): Config, YAML, the
    Run tab with its preview, the Run tab in Stateful mode with cap2/sfr2.yaml picked, the Start
    confirmation, the run mid-countdown (the guest's TRex is stopped first), and the Connect dialog's
    stateful-run message. The daemon is always stopped afterwards."""
    b, api = c.b, c.api
    vm_script("daemon.sh", "start")
    try:
        await b.click("Control", "a")
        await b.click("TRex daemon…", "button")
        await b.wait_for("Connect", "button", ".daemon-connect")
        await b.click("Connect", "button", ".daemon-connect")
        await b.wait_text("State: connected")
        await b.click("Config", "[role=tab]", "[role=dialog]")
        await b.wait_for("~Interfaces", "h3, h4, summary, legend, label", "[role=dialog]", timeout=30)
        await b.shot("100-daemon-config")
        await b.click("YAML", "[role=tab]", "[role=dialog]")
        await b.wait("document.querySelector('.daemon-dialog pre, .daemon-dialog textarea') !== null", 20,
                     "YAML text")
        await b.shot("101-daemon-yaml")
        await b.click("Run", "[role=tab]", "[role=dialog]")
        await b.wait("document.querySelector('.command-preview') !== null", 20, "command preview")
        await b.shot("102-daemon-run")
        await b.click("Stateful run (-f)", "input[type=radio]", "[role=dialog]")
        await b.wait("[...document.querySelectorAll('#stf-profile option')]"
                     ".some((o) => o.value === 'cap2/sfr2.yaml')", 20, "sfr2 in the stock list")
        await b.choose("Profile", "cap2/sfr2.yaml", "[role=dialog]")
        await b.wait_text("Templates: cap2/Oracle.pcap")
        await b.wait("(document.querySelector('.command-preview') || {}).textContent"
                     "?.includes('-f cap2/sfr2.yaml')", 20, "stateful command preview")
        await b.shot("103-daemon-run-stateful")
        # a stateful run needs the guest's own TRex gone: end the Control session, then stop it
        api.post("/api/session/disconnect")
        vm_script("mode.sh", "stop")
        await b.wait("[...document.querySelectorAll('.daemon-run button')]"
                     ".some((x) => S.name(x) === 'Start' && !x.disabled)", 40, "Start enabled")
        await b.fill("Duration -d (s)", "90", "[role=dialog]")
        await b.wait("(document.querySelector('.command-preview') || {}).textContent?.includes('-d 90')", 20,
                     "preview with -d 90")
        await b.click("Start", "button", ".daemon-actions")
        await b.wait_for("Start TRex", "h2, h3", "[role=dialog]")
        await b.shot("104-daemon-start-confirm")
        await b.click("Start", "button", "[role=dialog]")
        await b.wait_for("~ends in about", "[role=timer]", timeout=30)
        await asyncio.sleep(3)
        await b.scroll(".daemon-body", "end")
        await b.shot("105-daemon-stf-running")
        await b.click("Close", "button", "[role=dialog]")
        await b.click("Connect…", "button")
        await b.wait_for("Connect to TRex", "h2", "[role=dialog]")
        await b.click("Connect", "button", "[role=dialog] .dialog-footer")
        await b.wait_text("is running a stateful (-f) run", 60)
        await b.shot("106-connect-stateful-run")
        await b.key("Escape")
    finally:
        vm_script("daemon.sh", "stop")


async def step_pages(c):
    """83: the backend stopped."""
    b = c.b
    await b.goto("/control")
    await asyncio.sleep(1)
    c.be.stop()
    await b.wait_text("Backend offline", 40)
    await b.shot("83-statusbar-backend-offline")


STEPS = [("shell", step_shell), ("disconnected", step_disconnected), ("connect", step_connect),
         ("control", step_control), ("port_tabs", step_port_tabs), ("traffic", step_traffic),
         ("events", step_events), ("dashboard", step_dashboard), ("lost", step_lost),
         ("capture", step_capture), ("setups", step_setups), ("themes", step_themes),
         ("settings", step_settings), ("studio", step_studio), ("packet", step_packet),
         ("monitor", step_monitor),
         ("monitor_stateful", step_monitor_stateful), ("daemon", step_daemon), ("pages", step_pages)]


async def run(scratch, out, until, steps=None, stage=None):
    """Start the backend and Chrome, run the steps (up to `until`), and always stop both. The shots go to
    <scratch>/shots; a complete run then replaces the old PNGs of `out` with them. `steps` (default STEPS)
    is the list of (name, coroutine function) and `stage` (default stage_profiles) the function that fills
    the scratch dir with what the steps need; shoot_howto.py passes its own."""
    steps = STEPS if steps is None else steps
    prepare_scratch(scratch)
    (stage or stage_profiles)(scratch)
    if port_in_use(CDP_PORT):
        raise SystemExit(f"port {CDP_PORT} is already in use (another Chrome?); stop it first")
    staging = scratch / "shots"
    staging.mkdir()
    be = Backend(scratch)
    chrome = None
    complete = False
    try:
        be.start()
        chrome = subprocess.Popen(
            ["google-chrome", "--headless=new", f"--remote-debugging-port={CDP_PORT}",
             f"--user-data-dir={scratch / 'chrome'}", "--no-first-run", "--no-default-browser-check",
             "--hide-scrollbars", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = find_target()
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            b = Browser(ws, staging)
            reader = asyncio.create_task(b.reader())
            await b.call("Page.enable")
            await b.call("Page.addScriptToEvaluateOnNewDocument", source=LIB)
            await b.viewport()
            c = Ctx(b, be, scratch)
            for name, fn in steps:
                print(f"== {name}", flush=True)
                await fn(c)
                if name == until:
                    break
            else:
                complete = until is None
            reader.cancel()
    finally:
        if chrome is not None:
            chrome.terminate()
            try:
                chrome.wait(10)
            except subprocess.TimeoutExpired:
                chrome.kill()
        be.stop()
    if complete:
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.png"):
            old.unlink()
        for new in sorted(staging.glob("*.png")):
            shutil.move(str(new), out / new.name)
        print(f"{len(list(out.glob('*.png')))} shots in {out}", flush=True)
    else:
        print(f"partial run: the shots are in {staging}; {out} is untouched", flush=True)


def main(steps=None, stage=None, out=None, doc=None):
    """Parse the command line and run the shoot. The keyword arguments default to this tool's own STEPS,
    stage_profiles, docs/gui-inventory/screens and docstring; shoot_howto.py passes its own."""
    steps = STEPS if steps is None else steps
    out = REPO / "docs/gui-inventory/screens" if out is None else out
    p = argparse.ArgumentParser(description=(doc or __doc__).split("\n\n")[0])
    p.add_argument("scratch", type=Path)
    p.add_argument("--out", type=Path, default=out)
    p.add_argument("--until", default=None, choices=[name for name, _ in steps], help="stop after this step")
    a = p.parse_args()
    asyncio.run(run(a.scratch.resolve(), a.out.resolve(), a.until, steps, stage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
