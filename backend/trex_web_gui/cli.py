"""`trex-web-gui serve` (A2.12). Loopback only: there is deliberately no --listen (Revision 2)."""
import argparse
import contextlib
import copy
import getpass
import ipaddress
import logging
import os
import re
import signal
import socket
from pathlib import Path
from types import FrameType
from typing import Any, NamedTuple

from trex_web_gui import __version__
from trex_web_gui.install import resolve_static
from trex_web_gui.monitor.host import check_monitor_host
from trex_web_gui.validation import check_host

LOOPBACK = "127.0.0.1"
DEV_PORT = 5173      # the Vite dev server; pinned in frontend/vite.config.ts (server.port, strictPort)


def _port(text: str) -> int:
    """argparse type for a TCP port: one to five ASCII digits with a value from 1 to 65535, else
    ArgumentTypeError (no sign, space or underscore, which `int()` would accept)."""
    if not re.fullmatch(r"[0-9]{1,5}", text):
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}")
    n = int(text)
    if not 1 <= n <= 65535:
        raise argparse.ArgumentTypeError(f"{n} is outside 1-65535")
    return n


class ServerArg(NamedTuple):
    """A parsed --server: the host to prefill, and the (sync, async) ports when given."""
    host: str
    ports: tuple[int, int] | None


def parse_server(text: str) -> ServerArg:
    """argparse type of --server: `host`, `host:S`, `host:S:A`, or `[v6]`, `[v6]:S`, `[v6]:S:A`.
    A value with `::` or more than two colons is a host with no ports only if it parses as an IPv6 address,
    else it is an error. An omitted A is S - 1. Raises ArgumentTypeError on a missing `]`, an empty or invalid
    host (validation.check_host), a port outside 1-65535, or whitespace anywhere in the text."""
    if any(c.isspace() for c in text):
        raise argparse.ArgumentTypeError(f"whitespace in {text!r}")
    if text.startswith("["):
        host, close, rest = text[1:].partition("]")
        if not close:
            raise argparse.ArgumentTypeError(f"missing ']' in {text!r}")
        if rest and not rest.startswith(":"):
            raise argparse.ArgumentTypeError(f"unexpected text after ']' in {text!r}")
        parts = rest[1:].split(":") if rest else []
    elif "::" in text or text.count(":") > 2:
        try:
            ipaddress.IPv6Address(text)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{text!r} is neither host[:SYNC[:ASYNC]] nor an IPv6 literal") from None
        host, parts = text, []
    else:
        host, *parts = text.split(":")
    if not host:
        raise argparse.ArgumentTypeError(f"empty host in {text!r}")
    try:
        host = check_host(host)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{text!r}: {e}") from None
    if len(parts) > 2:
        raise argparse.ArgumentTypeError(f"too many ports in {text!r}")
    if not parts:
        return ServerArg(host, None)
    sync = _port(parts[0])
    if len(parts) == 2:
        return ServerArg(host, (sync, _port(parts[1])))
    if sync < 2:
        raise argparse.ArgumentTypeError(f"{sync} leaves no event port (default: command port minus 1)")
    return ServerArg(host, (sync, sync - 1))


DAEMON_PORT = 8090        # the TRex daemon's default port (trex_daemon_server)
MONITOR_PORT = 4500      # the TRex async (PUB) port the Monitor subscribes to by default


def parse_monitor(text: str) -> tuple[str, int]:
    """argparse type of --monitor: `host`, `host:PORT`, `[v6]` or `[v6]:PORT`; a bare IPv6 literal (`::1`) is
    a host. A bare host means port 4500. Raises ArgumentTypeError on a missing `]`, an empty or invalid host
    (validation.check_host, then monitor.host.check_monitor_host), a port outside 1-65535, or whitespace."""
    if any(c.isspace() for c in text):
        raise argparse.ArgumentTypeError(f"whitespace in {text!r}")
    port_text = ""
    if text.startswith("["):
        host, close, rest = text[1:].partition("]")
        if not close:
            raise argparse.ArgumentTypeError(f"missing ']' in {text!r}")
        if rest and not rest.startswith(":"):
            raise argparse.ArgumentTypeError(f"unexpected text after ']' in {text!r}")
        port_text = rest[1:]
        if rest and not port_text:
            raise argparse.ArgumentTypeError(f"empty port in {text!r}")
    elif text.count(":") > 1:
        host = text
    else:
        host, _, port_text = text.partition(":")
        if ":" in text and not port_text:
            raise argparse.ArgumentTypeError(f"empty port in {text!r}")
    try:
        host = check_monitor_host(check_host(host))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{text!r}: {e}") from None
    return host, _port(port_text) if port_text else MONITOR_PORT


def build_parser() -> argparse.ArgumentParser:
    """The `trex-web-gui` parser: a required `serve` subcommand with --port (default 8765), --data-dir,
    --server, --monitor, --monitor-only, --daemon-port and --dev."""
    p = argparse.ArgumentParser(prog="trex-web-gui")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the backend on 127.0.0.1")
    s.add_argument("--port", type=_port, default=8765)
    s.add_argument("--data-dir", type=Path, default=None)
    s.add_argument("--server", type=parse_server, default=None, metavar="HOST[:SYNC[:ASYNC]]",
                   help="TRex host, and optionally its command and event ports, to prefill in the dialog "
                        "(an omitted ASYNC is SYNC-1; an IPv6 host with ports is written [::1]:4601:4600)")
    s.add_argument("--monitor", type=parse_monitor, default=None, metavar="HOST[:PORT]",
                   help="connect the Monitor to this TRex PUB socket at startup (default port 4500; an IPv6 "
                        "host with a port is written [::1]:4500). Independent of --monitor-only; --server "
                        "does not apply to it")
    s.add_argument("--monitor-only", action="store_true",
                   help="serve the Monitor only: every route that changes state answers 409")
    s.add_argument("--daemon-port", type=_port, default=DAEMON_PORT, metavar="PORT",
                   help="the TRex daemon port the Daemon dialog prefills, on the --server host "
                        "(default 8090)")
    s.add_argument("--dev", action="store_true", help="API only; run the frontend dev server for the UI")
    return p


# Written once, at the first ignored SIGINT. The close can last up to the session's loss bound
# (11.6 s measured at the default timeout_s = 3, app.py create_app), which the handler cannot see,
# so no figure is given. Ctrl+\ (SIGQUIT) keeps its default action: Python installs no handler for
# it (user decision 2026-10-17, P3a final gate).
IGNORED_SIGINT_MESSAGE = (b"Ctrl+C ignored: closing the TRex session, which can take several seconds "
                          b"against an unresponsive server. Ctrl+\\ quits at once, but the ports may "
                          b"stay acquired.\n")


def _install_shutdown_sigint_handler() -> None:
    """While uvicorn serves, its own SIGINT handler is installed and this one is not called. When uvicorn
    stops serving it restores this one and raises SIGINT again. The first call raises KeyboardInterrupt,
    as Python's default handler does. asyncio.run then cancels the tasks that are left, including the
    lifespan task, whose cleanup closes the TRex session. Every later SIGINT is ignored.
    With the default handler, a SIGINT during that cleanup raised KeyboardInterrupt in the event loop.
    The loop stopped with the lifespan task pending, so the session close never finished, and the
    task's collection was logged as a GeneratorExit traceback (P3a Task 11). `uv run` forwards each
    Ctrl+C to the server again about 0.2 s later, so two presses reach the server as four SIGINTs.
    A non-default handler also keeps asyncio.run from installing its own: it does so only over the
    default, and it puts the default back before that final cancel.
    The handler only sets flags and writes one line with os.write, the first time a SIGINT is ignored.
    Logging from a signal handler can raise "reentrant call" on a buffered stderr, which would stop the
    loop again. main() restores the previous handler once uvicorn.run has returned."""
    interrupted = False
    told = False

    def on_sigint(signum: int, frame: FrameType | None) -> None:
        """The first SIGINT raises KeyboardInterrupt; later ones are ignored, the first of them writing one
        line to stderr with os.write (safe in a handler)."""
        nonlocal interrupted, told
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        if not told:
            told = True
            with contextlib.suppress(OSError):      # an unwritable stderr must not raise in the loop
                os.write(2, IGNORED_SIGINT_MESSAGE)

    signal.signal(signal.SIGINT, on_sigint)


def banner_lines(version: str, pin_sha: str | None, port: int, shown: int, token: str, who: str) -> list[str]:
    """The three start-up lines: version, STL API, client pin and serving URL (`client pin unreadable` when
    `pin_sha` is None); the loopback login URL; the ssh tunnel command with the `localhost` login URL.
    `port` is the backend's, `shown` the port a browser uses (the Vite port under --dev)."""
    from trex_web_gui.api.auth import login_url
    from trex_web_gui.trexapi.errors import SUPPORTED

    pin = f"client pin {pin_sha}" if pin_sha else "client pin unreadable"
    return [
        f"trex-web-gui {version} for {SUPPORTED}, {pin}, serving on http://{LOOPBACK}:{port}",
        f"Open: {login_url(LOOPBACK, shown, token)}",
        f"From another machine: ssh -N -L {shown}:{LOOPBACK}:{shown} {who}, "
        f"then open {login_url('localhost', shown, token)}",
    ]


def _pin_sha() -> str | None:
    """The vendored client's upstream SHA, or None when the pin file is missing or unreadable."""
    from trex_web_gui import trexapi
    try:
        sha = trexapi.pin()["upstream_sha"]
    except (OSError, ValueError, KeyError):
        return None
    return sha if isinstance(sha, str) else None


LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def log_config() -> dict[str, Any]:
    """uvicorn's logging config with each line led by its local date and time, and the trex_web_gui loggers
    (session events, refusals, the exit release) on uvicorn's console handler at INFO, in its format; they
    otherwise reached the console through logging's last-resort handler, warnings only and with no time."""
    from uvicorn.config import LOGGING_CONFIG
    cfg = copy.deepcopy(LOGGING_CONFIG)
    for f in cfg["formatters"].values():
        f["fmt"] = "%(asctime)s " + f["fmt"]
        f["datefmt"] = LOG_DATEFMT
    cfg["loggers"]["trex_web_gui"] = {"handlers": ["default"], "level": "INFO", "propagate": False}
    return cfg


def main(argv: list[str] | None = None) -> int:
    """Runs `serve`: builds the app with a token from TREX_WEB_GUI_TOKEN (popped) or a new one, prints the
    login URL and an ssh tunnel hint, and serves on 127.0.0.1 until uvicorn returns. Adds the login-token
    log filter, swaps the SIGINT handler for the serve duration and restores it afterwards; returns 0. Exits
    with status 2 for an invalid TREX_WEB_GUI_TOKEN and for bad arguments."""
    import uvicorn

    from trex_web_gui.api.app import create_app
    from trex_web_gui.api.auth import RedactLoginToken, token_from_env_or_new
    from trex_web_gui.store.settings import default_data_dir

    a = build_parser().parse_args(argv)
    token = token_from_env_or_new()        # pops TREX_WEB_GUI_TOKEN: not inherited by any child process
    app = create_app(a.data_dir or default_data_dir(), a.server.host if a.server else None,
                     None if a.dev else resolve_static(),
                     server_ports=a.server.ports if a.server else None,
                     origin_ports=frozenset({a.port} | ({DEV_PORT} if a.dev else set())), token=token,
                     monitor_only=a.monitor_only, monitor_target=a.monitor, daemon_port=a.daemon_port)
    shown = DEV_PORT if a.dev else a.port      # --dev: the Vite proxy forwards /api, and cookies ignore ports
    who = f"{getpass.getuser()}@{socket.gethostname()}"
    for line in banner_lines(__version__, _pin_sha(), a.port, shown, token, who):
        print(line, flush=True)
    logging.getLogger("uvicorn.access").addFilter(RedactLoginToken())
    previous = signal.getsignal(signal.SIGINT)
    _install_shutdown_sigint_handler()
    try:
        # lifespan shutdown closes the session
        uvicorn.run(app, host=LOOPBACK, port=a.port, log_level="info", log_config=log_config())
    finally:
        if previous is not None:            # None: a handler not set from Python, which cannot be restored
            signal.signal(signal.SIGINT, previous)
    return 0


def entry() -> int:
    """The `trex-web-gui` console script: main(), then SIGINT ignored for the rest of the exit. Once main() is
    done the process only exits, and a Ctrl+C that lands while Python runs its exit handlers
    (logging.shutdown) printed a KeyboardInterrupt traceback; `uv run` forwards each Ctrl+C again about
    0.2 s later, so under load one can arrive after uvicorn has returned. main() itself restores the handler
    it found, for callers that go on running."""
    try:
        return main()
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
