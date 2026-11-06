# Security

## Reporting a vulnerability

Report it privately through GitHub: the repository's **Security** tab, **Report a vulnerability**. Do not open a
public issue. trex-web-gui has one maintainer, who answers on a best-effort basis. Only the latest release is supported,
and there is no bug bounty.

A problem in TRex itself, its daemon or Scapy belongs upstream
([Upstream, not trex-web-gui](#upstream-not-trex-web-gui)).

## What trex-web-gui is built for

One user, on the machine that runs the backend. The backend serves only on `127.0.0.1`
(`backend/trex_web_gui/cli.py:22,253`), with no TLS and deliberately no option to listen elsewhere
(`backend/trex_web_gui/cli.py:1`). A browser on another machine reaches it through an SSH tunnel, which supplies the
encryption and the authentication of the remote end.

## What protects it

- **A per-launch access token.** Each launch of `trex-web-gui` makes a token and prints a login URL; opening it sets a
  session cookie. Without the cookie, every `/api` route except `/api/auth/login`, `/api/auth/status` and
  `/api/auth/logout` answers 401, and the WebSocket is refused (`backend/trex_web_gui/api/auth.py:29`,
  `backend/trex_web_gui/api/auth.py:100-108`).
  - A token from `TREX_WEB_GUI_TOKEN` must be 16 or more characters from `A-Za-z0-9_-`, or the backend exits with
    status 2 (`backend/trex_web_gui/api/auth.py:38-52`).
  - The variable is read once and removed from the environment, so no child process (the profile worker,
    Wireshark) inherits it (`backend/trex_web_gui/cli.py:238`).
  - The uvicorn access log rewrites the login URL's `token` parameter to `<redacted>`
    (`backend/trex_web_gui/api/auth.py:111-114`, `backend/trex_web_gui/cli.py:248`).
- **Host and Origin checks.** Only the host names `127.0.0.1` and `localhost` are served, against DNS rebinding
  (`backend/trex_web_gui/api/app.py:326`), and a browser request whose `Origin` is not a loopback origin on the serving
  port is refused, against cross-site requests and WebSocket hijacking (`backend/trex_web_gui/api/origin.py:25`,
  `backend/trex_web_gui/api/origin.py:46`).

## By design, not vulnerabilities

- **Profiles and packet expressions run as code on the backend host**, as the user who runs trex-web-gui. A `.py`
  traffic profile is a Python program, and a packet field's expression value is Python that Scapy evaluates. Both
  run in a worker process, never in the backend: the worker has an address-space limit set before it reads
  anything (`backend/trex_web_gui/trexapi/profiles.py:347`) and its own process group, which is killed on every exit
  path (`backend/trex_web_gui/trexapi/profiles.py:252`, `backend/trex_web_gui/trexapi/profiles.py:259-263`;
  `backend/trex_web_gui/trexapi/packet_worker.py:1-5`). That contains mistakes, not an attacker: a child that calls
  `setsid()` leaves the group and survives the kill (`backend/trex_web_gui/trexapi/profiles.py:260-261`). Load only
  profiles you trust, and do not run trex-web-gui as root.
- **Anyone who can read the login URL can drive the GUI.** The token is in the start-up banner, and in the journal
  when trex-web-gui runs as a service, readable by members of `systemd-journal` and `adm`. Any process running as the
  same user can read it, from the terminal, the journal or, when `TREX_WEB_GUI_TOKEN` supplied the token, the
  kernel's copy of the start-up environment (`/proc/<pid>/environ` of the backend, and of the `uv` process that
  launched it), which the variable's removal does not clear.
  Logging out (`POST /api/auth/logout`) deletes that browser's cookie and, when the request carried a valid cookie,
  closes every open WebSocket of the backend, every tab's (`backend/trex_web_gui/api/app.py:409-419`). The token
  stays valid until the backend exits, so restart it to revoke a login URL someone else has seen.
- **The Wireshark path is a program the backend runs**, as the user who runs trex-web-gui
  (`backend/trex_web_gui/sessions/wireshark.py:133`). Whoever can change the settings chooses it: anyone with the
  token, or a settings bundle applied with Preferences set to replace, whose preview names the change
  (`backend/trex_web_gui/store/bundle.py:186`). That is no more than loading a profile already allows.

## Known open items

- **No size cap on most JSON request bodies.** The routes that read their body by hand (the Studio library, the
  packet editor, test setups and the settings bundle) refuse one over their limit with 413
  (`backend/trex_web_gui/api/bodies.py:32`); file uploads are capped at 64 MiB
  (`backend/trex_web_gui/api/studio.py:47`).
  The routes whose body is a validated model have no cap of their own. Only an authenticated user can send one.
- **The cookie is shared across ports.** A browser sends a cookie to every server on the same host name, whatever
  the port, so another local web server reached under the host name the GUI was opened with can read the token
  (`backend/trex_web_gui/api/auth.py:8-11`). Open other local web apps under the other name: `localhost` when the GUI is
  at `127.0.0.1`, and the reverse. The `Origin` check is what stops a page on another loopback port from using the
  cookie.

## Upstream, not trex-web-gui

- **The TRex daemon** (`trex_daemon_server`), which the **TRex daemon…** dialog uses, has no authentication, runs
  as root and binds `0.0.0.0` by default (`--trex-host`, trex-core's
  `scripts/automation/trex_control_plane/server/trex_server.py:740-741`).
  - Its `push_file` and `prepare_astf_profile` (`trex_server.py:113-135`) let anyone who can reach its port (8090)
    write a file into the daemon's files directory and have a Python profile loaded, which is code run as root,
    whatever trex-web-gui does.
  - trex-web-gui's rule on `trex_args` is defence in depth on its own route, not a boundary: at most 20 tokens of
    letters, digits and `_ . , : = / + -`, with the flags that change what TRex reads refused (`--cfg`, `-i`,
    `--astf`, `-f`, `-d`, `-m`, `-l`, `--rx-check`, `--client_cfg`, `--client-cfg`, and a long flag also when
    abbreviated; `backend/trex_web_gui/daemon/session.py:37-56`, `backend/trex_web_gui/daemon/session.py:118-129`).
    Flags that take a path, such as `--rpc-logfile`, still pass it, and the daemon's port accepts such arguments
    from anyone directly.
  - Restrict that port before using daemon control: the README's [Daemon exposure](README.md#daemon-exposure)
    section says how.
- **The EMU server** (`trex-emu`, RPC port 4510), which the **EMU** page uses, has no authentication, so anyone who
  can reach its port can read and change its namespaces and clients, and a profile load replaces them all. The
  shipped server (v3.08) crashes when a namespace with the `ipfix` or `tdl` plugin at namespace level is removed, or
  when a client with `mdns` is added to a namespace without it (measured with empty plugin configs; `ipfix` on a client loaded and removed cleanly). trex-web-gui
  refuses to load such a profile and asks for an acknowledgement before removing such a namespace it did not add.
  It also asks for a confirmation (and a second acknowledgement for namespaces it did not add) before a load, which
  guards a mistake and not another client. Restrict the port: the README's [EMU exposure](README.md#emu-exposure)
  section says how.
- **The vendored TRex client and Scapy** are third-party code, carried with a small set of patches. What each patch
  changes, and its upstream status, is in [`backend/vendor/patches.md`](backend/vendor/patches.md).
