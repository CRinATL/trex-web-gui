# trex-web-gui

trex-web-gui is a web interface for controlling and observing a [Cisco TRex](https://github.com/cisco-system-traffic-generator/trex-core)
traffic generator. It brings traffic control, statistics, packet capture, profile editing and passive monitoring into
one application, replacing the separate `trex-stateless-gui`, `trex-packet-editor` and `trex-qt-gui` tools.

The backend runs on Linux with Python 3.12 or newer. Traffic control targets TRex v3.08 in stateless mode (STL API
5.1); the passive Monitor also shows what stateful (`-f`) runs publish. The backend is Python (FastAPI) and drives a
vendored, patched copy of the TRex Python client; the frontend is React + TypeScript + Vite. **How to use the GUI,
page by page: [`docs/gui-guide.md`](docs/gui-guide.md).**

## What you can do

- **Control**: connect to a TRex server, acquire and release ports, assign profiles (`.py`, YAML, JSON), start, pause,
  update and stop traffic, configure ports (L2/L3, VLAN, IPv6, ARP, service mode, attributes), ping, scan IPv6
  neighbours, read extended counters, and save and re-run multi-port test setups.
- **Dashboard**: global, per-port, per-stream (PG ID) and latency statistics with charts; CPU and mbuf utilization.
- **Capture**: a live capture monitor with a packet viewer, whose packets can also go to Wireshark on the backend
  host; server-side recorders saved as pcap on the backend host; LLDP neighbours.
- **Studio**: a library of stream profiles edited offline (import `.py`, YAML, JSON or pcap; edit streams; simulate;
  export) and a packet editor (layers, fields, payloads, Field Engine, presets, templates).
- **Monitor**: a passive view of what a TRex server publishes on its event port, independent of the control
  connection, also as a wall display.
- **TRex daemon**: write a TRex config, start and stop TRex through its optional daemon (`trex_daemon_server`), run
  stateful profiles, and read its logs.

## Start using it

1. Install trex-web-gui on the machine that can reach the TRex server ([Install](#install)).
2. Start the backend: `trex-web-gui serve`.
3. Open the login URL it prints. For a browser on another machine, the backend also prints the SSH tunnel command
   and the URL to open at the other end.
4. In the GUI, press **Connect…**, enter the TRex server, and connect.
5. Acquire the ports you will use, assign a traffic profile, and start traffic; watch it on the **Dashboard**. Stop
   the traffic and release the ports when you are done.

For a worked example, with the test topology and how to confirm the frames arrive, follow
[`docs/howto-first-stream.md`](docs/howto-first-stream.md).

## Operating model

trex-web-gui is a single-user application. The backend listens on loopback only, and every logged-in browser tab shares
its one TRex session. Each start makes a new login URL.

The browser, the backend and the TRex server can be on three different machines. Paths in the file browser are on
the backend host, `.py` profiles run there as code (as the user who started trex-web-gui), and Wireshark opens there.
Restrict access to the TRex daemon before using daemon control outside a trusted test network
([Daemon exposure](#daemon-exposure)). [`SECURITY.md`](SECURITY.md) says what protects the backend and what does
not.

## Install

trex-web-gui is not on PyPI. From 1.0.1 on, one command installs the latest release with
[`uv`](https://docs.astral.sh/uv/) (until 1.0.1 is published the address answers 404, `curl` reports the 404 and `sh`
installs nothing):

```
curl -fsSL https://github.com/CRinATL/trex-web-gui/releases/latest/download/install.sh | sh
```

On a terminal the script prints the wheel's sha256 and the release page, downloads the wheel, checks it against that
sha256 and stops on a prompt. Compare the sha256 with the one the release page lists next to the wheel, then press Enter
to install. Without a terminal there is no pause: it installs at once and says so. `TREX_WEB_GUI_YES=1` skips the pause
for an unattended install: put it before `sh`, as in `curl -fsSL … | TREX_WEB_GUI_YES=1 sh`.

**Read the script first.** To read the script before running any of it, or on a host with no terminal:

```
curl -fLO https://github.com/CRinATL/trex-web-gui/releases/latest/download/install.sh
```

Read it, and compare its `sha256=` line with the sha256 the release page it names lists next to the wheel. Then run:

```
sh install.sh
```

GitHub computes the release page's sha256 from the uploaded wheel, so a match shows that the script and the wheel
belong to the same published release, and that neither was swapped or damaged on the way. It does not show that the
release itself is good: the signed tag (`git verify-tag`) and the release notes are the record of that.

To install v1.0.0, or to pin a version, install the wheel of a
[GitHub release](https://github.com/CRinATL/trex-web-gui/releases) by its address, where `<tag>` is the release's tag
(for example `v1.0.0`) and `<version>` the same in PEP 440 form (`1.0.0`):

```
uv tool install --python 3.12 https://github.com/CRinATL/trex-web-gui/releases/download/<tag>/trex_web_gui-<version>-py3-none-any.whl
trex-web-gui serve [--server HOST[:SYNC[:ASYNC]]] [--monitor-only] [--monitor HOST[:PORT]] [--daemon-port PORT]
```

With the [GitHub CLI](https://cli.github.com/), `gh release download <tag> -R CRinATL/trex-web-gui --pattern '*.whl'`
fetches the wheel instead, and `uv tool install --python 3.12 ./trex_web_gui-<version>-py3-none-any.whl` installs it;
`make wheel` builds one from a checkout.

A host without Python 3.12 (Ubuntu 22.04 has 3.10) needs nothing more: uv downloads a Python 3.12 from GitHub and the
dependencies from PyPI. The install ends with `Installed 1 executable: trex-web-gui`. The options are described under
[Running it](#running-it). First time? [`docs/howto-first-stream.md`](docs/howto-first-stream.md) sends a first
stream, step by step.

**As a service.** Copy [`docs/trex-web-gui.service`](docs/trex-web-gui.service) to `~/.config/systemd/user/`, then
`systemctl --user enable --now trex-web-gui`. Read the login URL with `journalctl --user -u trex-web-gui`. A user unit has no
display, and Wireshark needs one: see the comments in the unit.

**From another machine** use an SSH tunnel, as in [Access: single user, localhost only](#access-single-user-localhost-only);
a wall display uses the same tunnel ([Monitor and wall displays](#monitor-and-wall-displays)).

### Offline host

A host that cannot reach PyPI installs from a wheelhouse, a directory of the wheel and its dependencies.

1. On a connected machine, build the wheelhouse:

   ```
   uvx pip download --dest wheelhouse --only-binary=:all: --python-version 3.12 --implementation cp \
     --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 ./trex_web_gui-<version>-py3-none-any.whl
   ```

   The wheel ends up inside it, as `wheelhouse/trex_web_gui-<version>-py3-none-any.whl`. Copy `wheelhouse/` to the host.

2. Give the host a Python 3.12, one of:
   - if it reaches GitHub: `uv python install 3.12` on the host, the only step that uses the network;
   - if it does not: on a connected machine, `uv python install 3.12`, then
     `cd "$(uv python dir)" && tar -czf ~/cpython-3.12.tar.gz cpython-3.12.*-linux-x86_64-gnu`; copy the tarball
     to the host and unpack it there with `mkdir -p "$(uv python dir)" && tar -xzf cpython-3.12.tar.gz -C "$(uv python dir)"`
     (uv's Pythons can move, so the two machines' paths and user names may differ);
   - or the distribution's `python3.12` package, where it has one, from local media.

3. On the host, from the directory that holds `wheelhouse/` (both paths below are relative):

   ```
   uv tool install --offline --no-index --find-links wheelhouse --python 3.12 wheelhouse/trex_web_gui-<version>-py3-none-any.whl
   ```

   `wheelhouse/` is not needed after the install.

### Where the install puts things

`uv tool install` installs for the user who runs it, with no `sudo`:

- trex-web-gui and its dependencies go into their own environment, `~/.local/share/uv/tools/trex-web-gui/` (`uv tool dir`;
  `UV_TOOL_DIR` moves it);
- the `trex-web-gui` command is linked into `~/.local/bin` (`uv tool dir --bin`; `UV_TOOL_BIN_DIR` moves it), which must
  be on `PATH`: the install warns when it is not, and `uv tool update-shell` adds it;
- the packages also land in uv's cache, `~/.cache/uv/` (`uv cache dir`);
- the environment runs on the Python 3.12 that `--python 3.12` found, uv's own under `~/.local/share/uv/python/`
  (`uv python dir`) or the system's `python3.12`, so that Python has to stay installed.

`uv tool uninstall trex-web-gui` removes the environment and the command.

More: the REST and WebSocket reference is [`docs/api.md`](docs/api.md); what changed in each release is in its
[release notes](https://github.com/CRinATL/trex-web-gui/releases).

## Access: single user, localhost only

The backend binds `127.0.0.1` and checks the `Host` and `Origin` headers. Each launch makes an access token and prints a
login URL, `Open: http://127.0.0.1:8765/api/auth/login?token=...`. Open that URL: it sets a session cookie, and without
it every `/api` route but `/api/auth/{login,status,logout}` answers 401 and the WebSocket closes, so the page shows only
"Not authorised". The token lives as long as the process, so a restart needs the new URL.

To use the GUI from another machine, tunnel the port over SSH. The banner prints the exact command,
`ssh -N -L 8765:127.0.0.1:8765 <user>@<host>`, and then the URL to open on that machine, `localhost` already in
it. Keep the local port the same as the backend's (`--port`, 8765 by default), because the `Origin` check accepts only the
serving port.

- `TREX_WEB_GUI_TOKEN` sets the token instead (16 or more characters from `A-Za-z0-9_-`; the backend exits with
  status 2 otherwise). It is read once and removed from the environment, so no child process inherits it. The
  kernel's copy of the start-up environment keeps it: `/proc/<pid>/environ` of the backend, and of the `uv`
  process that launched it, is readable by your own user (same uid) only. Do not pass it inline
  (`TREX_WEB_GUI_TOKEN=... trex-web-gui serve`): the value lands in your shell history.
- The banner carries the token. Run as a service, it lands in the journal, readable by members of
  `systemd-journal` and `adm`; treat the journal like the terminal. The uvicorn access log redacts the login
  query.
- The cookie is the token, and a browser sends a cookie to every server on the same host, whatever the port.
  Any HTTP server your browser reaches under the host name you opened the GUI with can read it, so open other
  local web apps under the other name: `http://localhost:<port>` when the GUI is at `127.0.0.1`, and
  `http://127.0.0.1:<port>` when you reach the GUI through a tunnel at `localhost`. The `Origin` check is what
  stops a page on another loopback port from using it.

## Requirements

To run it:

- Linux, with Python 3.12 or later (tested on 3.12–3.14); `uv` fetches one if the host has none.
- A TRex server (v3.08) in stateless mode to connect to. The vendored client is in the wheel; no trex-core checkout
  is needed.
- Optional, on the backend host: Wireshark (the Capture page's **Open in Wireshark**, which needs a display there), and
  the TRex daemon (`trex_daemon_server`) for the daemon dialog.

trex-web-gui does not start TRex or `trex-console`. It takes `trex-console`'s place as a client of a TRex server that is
already running in stateless mode, on its command and event ports (`--server`); only the **TRex daemon…** dialog
starts one, through `trex_daemon_server`. trex-web-gui needs no root: run it as an ordinary user. `.py` profiles run as
code as that user ([Where things are on the backend host](#where-things-are-on-the-backend-host)), so running
trex-web-gui as root would run them as root.

To build it from a checkout:

- [`uv`](https://docs.astral.sh/uv/), and Node.js with npm at a version `frontend/package.json` accepts
  ([`CONTRIBUTING.md`](CONTRIBUTING.md)).
- `npm --prefix frontend install`, then `make build` builds the UI that `trex-web-gui serve` serves.

## Running it

From a checkout, after `make build`:

```
uv run --project backend trex-web-gui serve [options]
```

| Option | What it does |
|---|---|
| `--port PORT` | The port to serve on, on `127.0.0.1` (default 8765). There is no option to listen elsewhere; a tunnel must use the same local port. |
| `--data-dir DIR` | Where the GUI keeps its own files (below). Default `$XDG_DATA_HOME/trex-web-gui`, else `~/.local/share/trex-web-gui`. |
| `--server HOST[:SYNC[:ASYNC]]` | The TRex server the Connect dialog prefills, with its command and event ports (an omitted ASYNC is SYNC-1; an IPv6 host with ports is written `[::1]:4601:4600`). |
| `--daemon-port PORT` | The TRex daemon port the daemon dialog prefills, on the `--server` host (default 8090). |
| `--monitor HOST[:PORT]` | Connects the Monitor to this TRex PUB socket at start-up (default port 4500). Independent of `--monitor-only`; `--server` does not apply to it. |
| `--monitor-only` | Serves the Monitor alone; see [Monitor and wall displays](#monitor-and-wall-displays). |
| `--dev` | API only; the UI comes from the Vite dev server on `:5173` (see `make dev`). |

Ctrl+C closes the TRex session before the backend exits, which can take several seconds against a server that does not
answer; a second Ctrl+C is ignored and says so. Ctrl+\ quits at once, but the ports may stay acquired.

### Where things are on the backend host

Profiles for the Control page's Traffic tab are paths on the backend host, browsed with **Browse…**; `.py` profiles run
as code there when inspected or assigned. Everything the GUI writes goes under the data directory: `settings.toml`
(preferences and saved connections), `port_labels.toml` (port names), `test_setups/`, the Studio library in `profiles/`
(or the **Profile library directory** setting), your packet templates in `templates/` (or **Templates directory**),
saved captures in `captures/`, and files uploaded from a browser in `uploads/` (kept an hour). The Settings page's
**About** section shows the directory in use, and **Back up and restore** moves the settings, saved connections, port
names and test setups between backends as one JSON file.

## Monitor and wall displays

`trex-web-gui serve --monitor-only [--monitor HOST[:PORT]]` serves the Monitor alone: the top bar offers only **Monitor**
and **Settings** (the theme), and the backend answers 409 to every request that changes state and to the capture,
Studio, daemon, profile and file-browser routes. `--monitor` connects it to a TRex PUB socket at start-up. A
`--monitor-only` backend is still bound to `127.0.0.1`, so a wall screen on another machine reaches it through the same
SSH tunnel as above. The Monitor opens no RPC connection and changes nothing on the server, so it also works beside a
stateful (`-f`) run, which has no command port.

## Daemon exposure

The **TRex daemon…** dialog starts and stops TRex through the TRex daemon (`trex_daemon_server`, port 8090). That daemon
is not part of trex-web-gui, and it has no authentication: it runs as root and binds `0.0.0.0` by default
(`--trex-host`, trex-core's `scripts/automation/trex_control_plane/server/trex_server.py:740-741`). Its
`push_file` and `prepare_astf_profile` (`trex_server.py:113-135`) let anyone who can reach port 8090 write a file into the
daemon's files directory and have a Python profile loaded, which is code run as root, whatever trex-web-gui does.

trex-web-gui's own checks are not a boundary against that. The rule on `trex_args` (at most 20 tokens of letters, digits
and `_ . , : = / + -`, with the flags that change what TRex reads refused: `--cfg`, `-i`, `--astf`, `-f`, `-d`, `-m`,
`-l`, `--rx-check`, `--client_cfg`, also abbreviated) is defence in depth on trex-web-gui's own route; flags that take a
path, such as `--rpc-logfile`, still pass it, and port 8090 accepts such arguments from anyone directly. Restrict port
8090 before you use daemon control outside the test VM: firewall it to the GUI host, or start the daemon with
`--trex-host 127.0.0.1` and reach it through an SSH tunnel. The test VM is already safe: QEMU forwards its daemon port
on `127.0.0.1` only (`vm/launch.sh`).

## Development

How to set up a checkout, run the GUI from source, and run the checks: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Help and contact

- Questions, bugs and suggestions: [GitHub issues](https://github.com/CRinATL/trex-web-gui/issues). trex-web-gui has one
  maintainer, who answers on a best-effort basis.
- Questions about TRex itself: upstream, at [trex-core](https://github.com/cisco-system-traffic-generator/trex-core).
- A security problem: privately, as [`SECURITY.md`](SECURITY.md) says, not in a public issue.

## Documentation

- [`docs/howto-first-stream.md`](docs/howto-first-stream.md): a walk-through that sends a first stream and watches it
  arrive.
- [`docs/gui-guide.md`](docs/gui-guide.md): the user's guide to every page, dialog, menu and shortcut.
- [`docs/gui-inventory/README.md`](docs/gui-inventory/README.md): every screen, with the screenshots in
  `docs/gui-inventory/screens/`.
- [`docs/api.md`](docs/api.md): every HTTP route with its parameters, request body and response codes, and the
  WebSocket's message types. trex-web-gui serves no OpenAPI page itself (the token boundary), so the file is generated by
  `make api-docs`.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): building from a checkout, the checks, the conventions.
- [`SECURITY.md`](SECURITY.md): the security model and how to report a problem.

## Layout

A partial map of the repository; every path is relative to its root.

```
backend/
  trex_web_gui/        the app (Python package)
    _vendor/           the vendored, patched TRex client (generated by `make vendor`; committed)
    trexapi/           the only code that imports the vendored client
    sessions/          the TRex session: connection, ports, port configuration, capture, Wireshark
    api/               the HTTP and WebSocket surface, the access-token gate
    store/             files on disk: settings, port labels, test setups, the settings bundle, the capture spool
    studio/            the profile library, import and export, the packet documents, presets, templates, simulate
    monitor/           the Monitor's subscriber to a TRex PUB socket
    daemon/            the TRex daemon client and config model
    install.py         where the running backend finds its UI (the wheel's copy, else the checkout's)
    cli.py             `trex-web-gui serve`
  hatch_build.py       the wheel build hook that puts the built UI into the wheel
  vendor/
    TREX_PIN           the trex-core commit the vendored client is built from
    patches/           the patches `make vendor` applies
    patches.md         what each patch fixes and its upstream status
frontend/
  src/                 the single-page app (React + TypeScript)
    components/        UI components (studio/, packet/, capture/, monitor/, daemon/, setups/, settings/)
    pages/             one component per page
    ui/                shared widgets (tabs, the ⓘ note)
    __fixtures__/      recorded wire messages the frontend tests use
tests/
  unit/                unit tests
  contract/            API tests against the fake TRex
  golden/              stock-profile snapshots (`make check-golden`)
  fake/fake_trex.py    the fake TRex server, seeded from the recordings in tests/fixtures/
  fixtures/            recordings made against real TRex in the test VM
  real/                the `real` tier, against TRex in the test VM
scripts/               the checks `make` runs (citations, docstrings, deferred, api-docs), the install smoke and the release script
vm/                    the test VM scripts (launch.sh, real.sh, demo.sh, ...), the fixture recorders (record_*.py) and shoot_inventory.py
docs/
  gui-guide.md         the user's guide to the GUI
  gui-inventory/       every screen and its screenshot
  api.md               the HTTP API reference (generated by `make api-docs`)
  howto-first-stream.md  a first stream, step by step
```

Licence: GPL-2.0-or-later ([`LICENSE`](LICENSE), [`NOTICE`](NOTICE)).
