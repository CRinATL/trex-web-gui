# trex-web-gui

trex-web-gui is a web interface for controlling and observing a [Cisco TRex](https://github.com/cisco-system-traffic-generator/trex-core)
traffic generator. It brings traffic control, statistics, packet capture, profile editing and passive monitoring into
one application, replacing the separate `trex-stateless-gui`, `trex-packet-editor` and `trex-qt-gui` tools.

The backend runs on Linux with Python 3.12 or newer. Traffic control targets TRex v3.08 in stateless mode (STL API
5.1). The passive Monitor also shows what stateful (`-f`) runs publish.

[Before you start](#before-you-start) · [Install](#install) · [Quick start](#quick-start) ·
[Access](#access-single-user-localhost-only) · [Command-line options](#command-line-options) ·
[Documentation](#documentation) · [Development](#development)

## What you can do

- **Control**:
  - connect to a TRex server;
  - acquire and release ports;
  - assign profiles (`.py`, YAML, JSON);
  - start, pause, update and stop traffic;
  - configure ports (L2/L3, VLAN, IPv6, ARP, service mode, attributes);
  - ping;
  - scan IPv6 neighbours;
  - read extended counters;
  - save and re-run multi-port test setups.
- **Dashboard**: global, per-port, per-stream (PG ID) and latency statistics with charts; CPU and mbuf utilization.
- **Capture**:
  - a live capture monitor with a packet viewer, whose packets can also go to Wireshark on the backend host;
  - server-side recorders saved as pcap on the backend host;
  - LLDP neighbours.
- **Studio**: a library of stream profiles edited offline (import `.py`, YAML, JSON or pcap; edit streams; simulate;
  export) and a packet editor (layers, fields, payloads, Field Engine, presets, templates).
- **Monitor**: a passive view of what a TRex server publishes on its event port, independent of the control
  connection, also as a wall display.
- **TRex daemon**: write a TRex config, start and stop TRex through its optional daemon (`trex_daemon_server`), run
  stateful profiles, and read its logs.

## Before you start

trex-web-gui is a single-user application. The backend listens on loopback only, and every logged-in browser tab shares
its one TRex session. Each start makes a new login URL.

The browser, the backend and the TRex server can be on three different machines:

- **The backend host** runs trex-web-gui. It needs Linux, with Python 3.12 or later (tested on 3.12–3.14). `uv`
  fetches one if the host has none.
  - Paths in the file browser are on the backend host, `.py` profiles run there as code (as the user who started
    trex-web-gui), and Wireshark opens there.
  - Optional: Wireshark (the Capture page's **Open in Wireshark**, which needs a display there).
- **The TRex server** is a TRex v3.08 in stateless mode, already running
  ([Starting the TRex server](#starting-the-trex-server)).
  - The backend host must be able to reach its command and event ports, 4501 and 4500 by default.
  - The vendored client is in the wheel. No trex-core checkout is needed.
  - Optional, on the TRex host: the TRex daemon (`trex_daemon_server`) for the daemon dialog.
- **The browser** is a current browser, on the backend host or on another machine through an SSH tunnel
  ([Access](#access-single-user-localhost-only)).

Three common layouts, from one host to three:

1. **Local console.** Everything on the TRex host, which needs a display for the browser. No tunnel. TRex still
   listens on 4501, 4500 and the daemon's 8090 on every interface, so on a shared network block them with the
   host's firewall.

   ![One host: the browser, the trex-web-gui backend on 127.0.0.1:8765 and the TRex server on ports 4501 and 4500, all on one Linux host](docs/images/topology-one-host.svg)

2. **Remote browser,** the usual lab shape. The backend runs on the TRex host, and a browser on your workstation
   reaches it through an SSH tunnel. Only SSH needs to face the network.

   ![Two hosts: a workstation browser reaches the backend through an encrypted SSH tunnel to 127.0.0.1:8765, and the backend and the TRex server share the TRex host](docs/images/topology-two-hosts.svg)

3. **Dedicated backend host.** The backend runs on a third machine, for example one with a display for Wireshark.
   It must reach the TRex server's ports 4501 and 4500 over the network, and that ZMQ traffic has no encryption
   or authentication, so use this layout only on a trusted test network.

   ![Three hosts: a browser machine tunnels over SSH to the backend host, which reaches the TRex server on a third host over unencrypted, unauthenticated ZMQ on ports 4501 and 4500](docs/images/topology-three-hosts.svg)

trex-web-gui does not start TRex or `trex-console`. It takes `trex-console`'s place as a client of a TRex server that is
already running in stateless mode, on its command and event ports (`--server`). Only the **TRex daemon…** dialog
starts one, through `trex_daemon_server`. trex-web-gui needs no root: run it as an ordinary user. `.py` profiles run as
code as that user ([Where things are on the backend host](#where-things-are-on-the-backend-host)), so running
trex-web-gui as root would run them as root.

Restrict access to the TRex daemon before using daemon control outside a trusted test network
([Daemon exposure](#daemon-exposure)). [`SECURITY.md`](SECURITY.md) says what protects the backend and what does
not.

## Install

trex-web-gui is not on PyPI. One command installs the latest release with [`uv`](https://docs.astral.sh/uv/).
Other methods, older releases and offline hosts are under [Other ways to install](#other-ways-to-install).

**Install uv first** if `uv --version` finds none. uv's standalone installer needs no `sudo`:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- It puts `uv` in `~/.local/bin` and adds that directory to your shell profile.
- Open a new terminal afterwards, so the new `PATH` takes effect.
- [uv's installation page](https://docs.astral.sh/uv/getting-started/installation/) lists the other ways to install
  it, such as a package manager or `pipx install uv`.

Then install trex-web-gui:

```
curl -fsSL https://github.com/CRinATL/trex-web-gui/releases/latest/download/install.sh | sh
```

On a terminal the script:

- prints the wheel's sha256 and the release page;
- downloads the wheel;
- checks it against that sha256;
- stops on a prompt.

Compare the sha256 with the one the release page lists next to the wheel, then press Enter to install. Without a
terminal there is no pause: it installs at once and says so. `TREX_WEB_GUI_YES=1` skips the pause for an unattended
install: put it before `sh`, as in `curl -fsSL … | TREX_WEB_GUI_YES=1 sh`. The install ends with
`Installed 1 executable: trex-web-gui`.

**To upgrade**, stop the running backend, then run the same command. It replaces the installed version.

The script ends by printing how to start the backend: `trex-web-gui`. When `~/.local/bin` is not on your `PATH`, it
does two more things:

- It gives the command's full path instead.
- It adds the directory to your shell profile with `uv tool update-shell`. To have it print the steps instead and
  leave your profile alone, put `TREX_WEB_GUI_NO_MODIFY_PATH=1` before `sh`. A re-run whose profile already names
  the directory says so instead of asking uv again.

The terminal you installed from keeps its old `PATH`
([The `trex-web-gui` command and `PATH`](#the-trex-web-gui-command-and-path)). Next: the [Quick start](#quick-start).

### Where the install puts things

`uv tool install` installs for the user who runs it, with no `sudo`:

- trex-web-gui and its dependencies go into their own environment,
  `~/.local/share/uv/tools/trex-web-gui/` (`uv tool dir`; `UV_TOOL_DIR` moves it);
- the `trex-web-gui` command is linked into `~/.local/bin` (`uv tool dir --bin`; `UV_TOOL_BIN_DIR` moves it), which must
  be on `PATH` ([The `trex-web-gui` command and `PATH`](#the-trex-web-gui-command-and-path));
- the packages also land in uv's cache, `~/.cache/uv/` (`uv cache dir`);
- the environment runs on the Python 3.12 that `--python 3.12` found, uv's own under `~/.local/share/uv/python/`
  (`uv python dir`) or the system's `python3.12`, so that Python has to stay installed.

`uv tool uninstall trex-web-gui` removes the environment and the command.

### The `trex-web-gui` command and `PATH`

The command is in `~/.local/bin` (`uv tool dir --bin` prints the directory). If `trex-web-gui` answers
`command not found`, that directory is not on your `PATH`.

The installer adds it to your shell profile, but a script run as `curl … | sh` cannot change the `PATH` of the
terminal that started it. If the installer said it updated your profile, open a new terminal, or run the
`export PATH=…` line it printed in the current one.

Otherwise, add the directory to your shell profile yourself, in one of two ways:

- Run `uv tool update-shell`. It adds the directory to your shell's startup files.
- Or add this line yourself, to `~/.profile` for a login shell, `~/.bashrc` for bash, or `~/.zshenv` for zsh:

  ```
  export PATH="$HOME/.local/bin:$PATH"
  ```

Then open a new terminal, or run that line in the current one. `command -v trex-web-gui` prints the command's path
once it is found. Until then, `~/.local/bin/trex-web-gui` runs it by its full path.

## Quick start

After the install, on the machine that can reach the TRex server ([Install](#install)):

1. Start the backend:

   ```
   trex-web-gui
   ```

   1.1.0 and earlier need `trex-web-gui serve` ([Older releases](#older-releases)).
2. **Open the login URL it prints**, `Open: http://127.0.0.1:8765/api/auth/login?token=...`, in a browser on that
   host. Each start makes a new URL.
   - **From another machine**, run the SSH tunnel command the backend prints, and open the URL it prints for the
     other end ([Access: single user, localhost only](#access-single-user-localhost-only)).
   - A wall display uses the same tunnel ([Monitor and wall displays](#monitor-and-wall-displays)).
3. **Connect to TRex.** In the GUI, press **Connect…**, enter the TRex server, and connect. TRex must be running in
   stateless mode ([Starting the TRex server](#starting-the-trex-server)). `trex-web-gui --server HOST` prefills the
   dialog. [Command-line options](#command-line-options) lists every option.
4. Acquire the ports you will use, assign a traffic profile, and start traffic. Watch it on the **Dashboard**. Stop
   the traffic and release the ports when you are done.
5. **Ctrl+C stops it.**

For a worked example, with the test topology and how to confirm the frames arrive, follow
[`docs/howto-first-stream.md`](docs/howto-first-stream.md). **How to use the GUI, page by page:
[`docs/gui-guide.md`](docs/gui-guide.md).**

If the backend does not start:

- **`command not found`** means `~/.local/bin` is not on your `PATH`. Run `~/.local/bin/trex-web-gui` for now, and
  see [The `trex-web-gui` command and `PATH`](#the-trex-web-gui-command-and-path).
- **`ssh <host> trex-web-gui` answers `command not found`** even when a login finds it. A command given to `ssh`
  runs in a shell that is neither a login shell nor interactive, and Ubuntu's `~/.bashrc` stops early for such a
  shell, so no startup file adds the directory. Log in and start it there, or give the full path:
  `ssh <host> '~/.local/bin/trex-web-gui'`.
- **Don't use `uv run --project backend trex-web-gui` here.** That form is for a source checkout only. On an
  installed host it fails with ``Project directory `backend` does not exist``.

**As a service.** Copy [`docs/trex-web-gui.service`](docs/trex-web-gui.service) to `~/.config/systemd/user/`, then
`systemctl --user enable --now trex-web-gui`. Read the login URL with `journalctl --user -u trex-web-gui`. A user unit
has no display, and Wireshark needs one: see the comments in the unit.

### Starting the TRex server

On the TRex host, start TRex in interactive stateless mode (`-i`) from its install directory (here
`/opt/trex/v3.08`), as root:

```
cd /opt/trex/v3.08
sudo ./t-rex-64 -i --cfg /etc/trex_cfg.yaml
```

- **`-i` is the mode trex-web-gui controls.** TRex v3.08's `--help` describes it as "Run TRex in 'stateless'
  mode". Do not add `--astf`: ASTF is a different mode, and trex-web-gui does not control it. Do not start TRex
  with `-f <profile>` either: that is a stateful run, which has no command port, so only the Monitor works beside it
  ([Monitor and wall displays](#monitor-and-wall-displays)).
- **`--cfg`** names the platform configuration: the ports, cores and addresses. TRex reads `/etc/trex_cfg.yaml`
  when the option is left out. To create one, run `sudo ./dpdk_setup_ports.py -i` in the same directory: it asks
  about each interface.
- **Ports.** TRex takes commands on port 4501 and publishes events on 4500 unless its configuration sets
  `zmq_rpc_port` / `zmq_pub_port`. Those are the ports trex-web-gui uses when you give only a host. Otherwise give
  both, as `--server HOST:SYNC:ASYNC` or in the Connect dialog. The backend host must be able to reach both ports.
- **`--no-scapy-server`** is optional. It stops TRex starting its own Scapy server, which trex-web-gui never uses: the
  packet editor builds packets with the backend's own copy of the TRex client.
- **The command stays in the foreground** and shows TRex's own console. To leave it running after you log out, run
  it detached instead:

  ```
  sudo sh -c 'cd /opt/trex/v3.08 && setsid nohup ./t-rex-64 -i --cfg /etc/trex_cfg.yaml >/tmp/trex.log 2>&1 </dev/null &'
  ```

  Stop it with `sudo pkill -INT -x _t-rex-64`.

If the TRex daemon (`trex_daemon_server`) runs on the TRex host, the **TRex daemon…** dialog can start TRex instead.
It starts it in stateless mode, adding `-i` itself ([Daemon exposure](#daemon-exposure)).

## Other ways to install

### Read the script first

To read the script before running any of it, or on a host with no terminal:

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

### Pin a version

To install v1.0.0, or to pin a version, install the wheel of a
[GitHub release](https://github.com/CRinATL/trex-web-gui/releases) by its address. Replace `<tag>` with the release's
tag (for example `v1.0.0`) and `<version>` with the same in PEP 440 form (`1.0.0`):

```
uv tool install --python 3.12 https://github.com/CRinATL/trex-web-gui/releases/download/<tag>/trex_web_gui-<version>-py3-none-any.whl
```

Then start it with `trex-web-gui serve`, which works on every release ([Older releases](#older-releases)), and the
[Command-line options](#command-line-options) you need.

With the [GitHub CLI](https://cli.github.com/), `gh release download <tag> -R CRinATL/trex-web-gui --pattern '*.whl'`
fetches the wheel instead, and `uv tool install --python 3.12 ./trex_web_gui-<version>-py3-none-any.whl` installs it.
`make wheel` builds one from a checkout.

A host without Python 3.12 (Ubuntu 22.04 has 3.10) needs nothing more: uv downloads a Python 3.12 from GitHub and the
dependencies from PyPI.

### Older releases

- **1.1.0 and earlier need the `serve` command:** `trex-web-gui serve [options]`, and `trex-web-gui serve --help`
  for the options. Without it they stop with `the following arguments are required: cmd`. 1.1.1 and later serve by
  default and still accept `serve`, so `trex-web-gui serve` works on every release.
- **The 1.1.0 installer does not add `~/.local/bin` to your `PATH`.** Re-run the installer, which installs the latest
  release and adds the directory, or see
  [The `trex-web-gui` command and `PATH`](#the-trex-web-gui-command-and-path).
- **v1.0.0 has no installer.** Install it by its wheel ([Pin a version](#pin-a-version)).

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
     to the host and unpack it there with
     `mkdir -p "$(uv python dir)" && tar -xzf cpython-3.12.tar.gz -C "$(uv python dir)"`
     (uv's Pythons can move, so the two machines' paths and user names may differ);
   - or the distribution's `python3.12` package, where it has one, from local media.

3. On the host, from the directory that holds `wheelhouse/` (both paths below are relative):

   ```
   uv tool install --offline --no-index --find-links wheelhouse --python 3.12 wheelhouse/trex_web_gui-<version>-py3-none-any.whl
   ```

   `wheelhouse/` is not needed after the install.

## Access: single user, localhost only

The backend binds `127.0.0.1` and checks the `Host` and `Origin` headers. Each launch makes an access token and prints a
login URL, `Open: http://127.0.0.1:8765/api/auth/login?token=...`. Open that URL: it sets a session cookie, and without
it every `/api` route but `/api/auth/{login,status,logout}` answers 401 and the WebSocket closes, so the page shows only
"Not authorised". The token lives as long as the process, so a restart needs the new URL.

To use the GUI from another machine, tunnel the port over SSH. The banner prints the exact command,
`ssh -N -L 8765:127.0.0.1:8765 <user>@<host>`, and then the URL to open on that machine, `localhost` already in
it. Keep the local port the same as the backend's (`--port`, 8765 by default), because the `Origin` check accepts only
the serving port.

- `TREX_WEB_GUI_TOKEN` sets the token instead (16 or more characters from `A-Za-z0-9_-`; the backend exits with
  status 2 otherwise):
  - It is read once and removed from the environment, so no child process inherits it.
  - The kernel's copy of the start-up environment keeps it: `/proc/<pid>/environ` of the backend, and of the `uv`
    process that launched it, is readable by your own user (same uid) only.
  - Do not pass it inline (`TREX_WEB_GUI_TOKEN=... trex-web-gui`): the value lands in your shell history.
- The banner carries the token. Run as a service, it lands in the journal, readable by members of
  `systemd-journal` and `adm`. Treat the journal like the terminal. The uvicorn access log redacts the login
  query.
- The cookie is the token, and a browser sends a cookie to every server on the same host, whatever the port.
  Any HTTP server your browser reaches under the host name you opened the GUI with can read it, so open other
  local web apps under the other name: `http://localhost:<port>` when the GUI is at `127.0.0.1`, and
  `http://127.0.0.1:<port>` when you reach the GUI through a tunnel at `localhost`. The `Origin` check is what
  stops a page on another loopback port from using it.

## Command-line options

Installed with `install.sh` or `uv tool install`:

```
trex-web-gui [options]
```

`trex-web-gui --help` lists the options. From a source checkout, see [Development](#development).

Serving is the default and only command, and `trex-web-gui serve [options]` does the same
([Older releases](#older-releases)).

| Option | What it does |
|---|---|
| `--port PORT` | The port to serve on, on `127.0.0.1` (default 8765). There is no option to listen elsewhere; a tunnel must use the same local port. |
| `--data-dir DIR` | Where the GUI keeps its own files (below). Default `$XDG_DATA_HOME/trex-web-gui`, else `~/.local/share/trex-web-gui`. |
| `--server HOST[:SYNC[:ASYNC]]` | The TRex server the Connect dialog prefills, with its command and event ports (an omitted ASYNC is SYNC-1; an IPv6 host with ports is written `[::1]:4601:4600`). |
| `--daemon-port PORT` | The TRex daemon port the daemon dialog prefills, on the `--server` host (default 8090). |
| `--monitor HOST[:PORT]` | Connects the Monitor to this TRex PUB socket at start-up (default port 4500). Independent of `--monitor-only`; `--server` does not apply to it. |
| `--monitor-only` | Serves the Monitor alone; see [Monitor and wall displays](#monitor-and-wall-displays). |

Ctrl+C closes the TRex session before the backend exits, which can take several seconds against a server that does not
answer. A second Ctrl+C is ignored and says so. Ctrl+\ quits at once, but the ports may stay acquired.

### Where things are on the backend host

Profiles for the Control page's Traffic tab are paths on the backend host, browsed with **Browse…**. `.py` profiles run
as code there when inspected or assigned.

Everything the GUI writes goes under the data directory:

- `settings.toml` (preferences and saved connections);
- `port_labels.toml` (port names);
- `test_setups/`;
- the Studio library in `profiles/` (or the **Profile library directory** setting);
- your packet templates in `templates/` (or **Templates directory**);
- saved captures in `captures/`;
- files uploaded from a browser in `uploads/` (kept an hour).

The Settings page's **About** section shows the directory in use, and **Back up and restore** moves the settings,
saved connections, port names and test setups between backends as one JSON file.

## Monitor and wall displays

`trex-web-gui --monitor-only [--monitor HOST[:PORT]]` serves the Monitor alone: the top bar offers only
**Monitor** and **Settings** (the theme), and the backend answers 409 to every request that changes state and to the
capture, Studio, daemon, profile and file-browser routes. `--monitor` connects it to a TRex PUB socket at start-up. A
`--monitor-only` backend is still bound to `127.0.0.1`, so a wall screen on another machine reaches it through the same
SSH tunnel ([Access: single user, localhost only](#access-single-user-localhost-only)). The Monitor opens no RPC
connection and changes nothing on the server, so it also works beside a stateful (`-f`) run, which has no command port.

## Daemon exposure

The **TRex daemon…** dialog starts and stops TRex through the TRex daemon (`trex_daemon_server`, port 8090). That daemon
is not part of trex-web-gui, and it has no authentication: it runs as root and binds `0.0.0.0` by default
(`--trex-host`, trex-core's `scripts/automation/trex_control_plane/server/trex_server.py:740-741`). Its
`push_file` and `prepare_astf_profile` (`trex_server.py:113-135`) let anyone who can reach port 8090 write a file into
the daemon's files directory and have a Python profile loaded, which is code run as root, whatever trex-web-gui does.

trex-web-gui's own checks are not a boundary against that. The rule on `trex_args` (at most 20 tokens of letters, digits
and `_ . , : = / + -`, with the flags that change what TRex reads refused: `--cfg`, `-i`, `--astf`, `-f`, `-d`, `-m`,
`-l`, `--rx-check`, `--client_cfg`, also abbreviated) is defence in depth on trex-web-gui's own route. Flags that take a
path, such as `--rpc-logfile`, still pass it, and port 8090 accepts such arguments from anyone directly. Restrict port
8090 before you use daemon control outside a trusted test network: firewall it to the GUI host, or start the daemon with
`--trex-host 127.0.0.1` and reach it through an SSH tunnel. The project's own test VM is already safe: QEMU forwards its
daemon port on `127.0.0.1` only (`vm/launch.sh`).

## Documentation

- [`docs/howto-first-stream.md`](docs/howto-first-stream.md): a walk-through that sends a first stream and watches it
  arrive.
- [`docs/gui-guide.md`](docs/gui-guide.md): the user's guide to every page, dialog, menu and shortcut.
- [`docs/gui-inventory/README.md`](docs/gui-inventory/README.md): every screen, with the screenshots in
  `docs/gui-inventory/screens/`.
- [`docs/api.md`](docs/api.md): the REST and WebSocket reference, every HTTP route with its parameters, request body
  and response codes, and the WebSocket's message types. trex-web-gui serves no OpenAPI page itself (the token
  boundary), so the file is generated by `make api-docs`.
- [Release notes](https://github.com/CRinATL/trex-web-gui/releases): what changed in each release.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): building from a checkout, the checks, the conventions, the repository layout.
- [`SECURITY.md`](SECURITY.md): the security model and how to report a problem.

## Development

The backend is Python (FastAPI) and drives a vendored, patched copy of the TRex Python client. The frontend is React +
TypeScript + Vite.

To build it from a checkout:

- [`uv`](https://docs.astral.sh/uv/), and Node.js with npm at a version `frontend/package.json` accepts
  ([`CONTRIBUTING.md`](CONTRIBUTING.md)). Only building needs Node.js: the release wheel already holds the built
  UI, so installing and running a release needs neither Node.js nor npm.
- `npm --prefix frontend install`, then `make build` builds the UI that `trex-web-gui` serves.
- Then run it from the checkout, with the [options](#command-line-options) above:

  ```
  uv run --project backend trex-web-gui [options]
  ```

  One more option is for development only: `--dev` serves the API only, and the UI comes from the Vite dev server on
  `:5173` (see `make dev`).

How to set up a checkout, run the GUI from source, and run the checks, and a map of the repository:
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Help and contact

- Questions, bugs and suggestions: [GitHub issues](https://github.com/CRinATL/trex-web-gui/issues). trex-web-gui has one
  maintainer, who answers on a best-effort basis.
- Questions about TRex itself: upstream, at [trex-core](https://github.com/cisco-system-traffic-generator/trex-core).
- A security problem: privately, as [`SECURITY.md`](SECURITY.md) says, not in a public issue.

Licence: GPL-2.0-or-later ([`LICENSE`](LICENSE), [`NOTICE`](NOTICE)).
