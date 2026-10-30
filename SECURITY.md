# Security

## Reporting a vulnerability

Report it privately through GitHub: the repository's **Security** tab, **Report a vulnerability**. Do not open a
public issue. trex-web-gui has one maintainer, who answers on a best-effort basis. Only the latest release is supported,
and there is no bug bounty.

A problem in TRex itself, its daemon or Scapy belongs upstream ([Upstream, not trex-web-gui](#upstream-not-trex-web-gui)).

## What trex-web-gui is built for

One user, on the machine that runs the backend. The backend serves only on `127.0.0.1`
(`backend/trex_web_gui/cli.py:21,250`), with no TLS and deliberately no option to listen elsewhere
(`backend/trex_web_gui/cli.py:1`). A browser on another machine reaches it through an SSH tunnel, which supplies the
encryption and the authentication of the remote end.

## What protects it

- **A per-launch access token.** Each `trex-web-gui serve` makes a token and prints a login URL; opening it sets a
  session cookie. Without the cookie, every `/api` route except `/api/auth/login`, `/api/auth/status` and
  `/api/auth/logout` answers 401, and the WebSocket is refused (`backend/trex_web_gui/api/auth.py:29`,
  `backend/trex_web_gui/api/auth.py:99-107`).
- **Host and Origin checks.** Only the host names `127.0.0.1` and `localhost` are served, against DNS rebinding
  (`backend/trex_web_gui/api/app.py:301`), and a browser request whose `Origin` is not a loopback origin on the serving
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
  when trex-web-gui runs as a service. Any process running as the same user can read it, from the terminal, the journal
  or, when `TREX_WEB_GUI_TOKEN` supplied the token, the backend's start-up environment (`/proc/<pid>/environ`).

## Known open items

- **No size cap on most JSON request bodies.** The routes that read their body by hand (the Studio library, the
  packet editor, test setups and the settings bundle) refuse one over their limit with 413
  (`backend/trex_web_gui/api/bodies.py:22`); file uploads are capped at 64 MiB (`backend/trex_web_gui/api/studio.py:46`).
  The routes whose body is a validated model have no cap of their own. Only an authenticated user can send one.
- **The cookie is shared across ports.** A browser sends a cookie to every server on the same host name, whatever
  the port, so another local web server reached under the host name the GUI was opened with can read the token
  (`backend/trex_web_gui/api/auth.py:8-11`). Open other local web apps under the other name: `localhost` when the GUI is
  at `127.0.0.1`, and the reverse.

## Upstream, not trex-web-gui

- **The TRex daemon** (`trex_daemon_server`), which the **TRex daemon…** dialog uses, has no authentication, runs
  as root and listens on all interfaces by default. Anyone who can reach its port can have code run as root,
  whatever trex-web-gui does. Restrict that port before using daemon control: the README's
  [Daemon exposure](README.md#daemon-exposure) section says how.
- **The vendored TRex client and Scapy** are third-party code, carried with a small set of patches. What each patch
  changes, and its upstream status, is in [`backend/vendor/patches.md`](backend/vendor/patches.md).
