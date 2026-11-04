"""Whether the TRex server process runs on this host, and the loss messages built on that answer.

A read-only scan of /proc: no process is started and nothing is written. It is meaningful only when TRex
runs on the backend's own host, which the session judges by the server's reported hostname (as hostinfo
does). The interactive server is `_t-rex-64` started with `-i`: the `t-rex-64` wrapper script runs
`./_t-rex-64` with the arguments it was given, so that binary, not the script, is the server while it lives.
"""
from pathlib import Path

SERVER_BINARY = "_t-rex-64"
STOPPED = f"TRex server stopped: the TRex server process ({SERVER_BINARY}) on this host has exited."


def _argv(cmdline: Path) -> list[str]:
    """One process's argv from its cmdline file; [] for a kernel thread or a process that exited meanwhile."""
    try:
        raw = cmdline.read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def server_running(proc_root: Path = Path("/proc")) -> bool | None:
    """True while some process's argv[0] is the `_t-rex-64` binary (any path) and `-i` is among its arguments;
    False when none is; None when `proc_root` cannot be listed, so the answer is unknown."""
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for d in entries:
        if not d.name.isdigit():
            continue
        argv = _argv(d / "cmdline")
        if argv and argv[0].rsplit("/", 1)[-1] == SERVER_BINARY and "-i" in argv[1:]:
            return True
    return False


def silence_message(host: str, port: int, timeout_s: float, running: bool | None) -> str:
    """The cause shown when the server has gone silent ("subscriber timeout"), by what the process check
    found: False, it exited; True, it is still running; None, it could not be checked (another host, or no
    /proc). The silence is the subscriber's nominal 4 * timeout_s + 0.5 s (client.loss_bound), in whole
    seconds."""
    if running is False:
        return STOPPED
    head = f"No response from the TRex server at {host}:{port} for {int(4 * timeout_s + 0.5)} s."
    if running:
        return f"{head} The TRex server process is still running; it may be hung or overloaded."
    return f"{head} The TRex server process may have stopped, or the network may be down."
