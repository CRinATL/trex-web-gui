"""The live Wireshark pipe (P3c, user open question 2): the monitor's packets streamed into a Wireshark window
on the backend host, through a fifo in a private temp dir. Linux only. The launch needs the binary
(`shutil.which(wireshark_path)`) and a display (`DISPLAY` or `WAYLAND_DISPLAY`); `preflight` says which is
missing. The path is the saved preference, never a request's.

Threads: the loop thread calls `start`, `write`, `stop`, `kill` and reads the properties; one writer thread
per pipe owns the fifo fd (only it opens and closes it) and the temp dir's removal. The writer opens the fifo
with O_WRONLY | O_NONBLOCK, retried every 50 ms until Wireshark opens the read end (ENXIO before that) or the
open deadline passes, then sets the fd blocking, writes the pcap header and then the queued frames. The loop
learns of an end the writer found on its own (the window closed: EPIPE; Wireshark exited; the open deadline)
only through `loop.call_soon_threadsafe(on_change)`: the hub is loop-thread only. Python ignores SIGPIPE, so a
write to a closed fifo raises BrokenPipeError rather than killing the process.

`stop()` closes the fifo after the queued frames and leaves the window open; `kill()` ends the process group
(Wireshark runs in its own session), which also unblocks a write blocked on a full fifo with EPIPE. A pipe
is used once: a new launch is a new WiresharkPipe. Pipes still alive at interpreter exit are killed
(`atexit`), for an exit that skips the session's close()."""
import asyncio
import atexit
import contextlib
import errno
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from trex_web_gui.store import pcap

QUEUE_MAX = 10_000              # frames waiting for the writer; more are dropped and counted
OPEN_RETRY_S = 0.05
IDLE_CHECK_S = 0.25             # with nothing to write, the writer looks whether Wireshark has exited
JOIN_S = 2.0                    # stop() and kill() wait this long for the writer thread
KILL_WAIT_S = 2.0               # SIGTERM, then SIGKILL after this long
CLOSED = "Wireshark closed the pipe"
FIFO_NAME = "capture.pcap"
NO_DISPLAY = "no display on the backend host (DISPLAY/WAYLAND_DISPLAY unset)"

_STOP = object()

_live: "weakref.WeakSet[WiresharkPipe]" = weakref.WeakSet()


def preflight(path: str, env: Mapping[str, str] = os.environ) -> tuple[bool, str | None]:
    """(True, None) when `path` resolves to an executable and a display is set, else (False, the reason)."""
    if shutil.which(path) is None:
        if os.sep not in path:                  # a bare name is looked up on PATH: there is no "at" to name
            return False, f"'{path}' is not installed on the backend host (not on its PATH)"
        return False, f"not found at {path}"
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return False, NO_DISPLAY
    return True, None


class WiresharkPipe:
    def __init__(self, on_change: Callable[[], None], loop: asyncio.AbstractEventLoop) -> None:
        """A pipe not started yet: no process, fifo or writer. `on_change` runs on `loop` when the writer ends
        on its own with an error."""
        self._on_change = on_change
        self._loop = loop
        self._proc: subprocess.Popen[bytes] | None = None
        self._dir: Path | None = None
        self._fifo: Path | None = None
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[object] = queue.Queue()    # bounded by write(): the sentinel always fits
        self._stopping = threading.Event()                 # stop() or kill() was asked: an end is no error
        self._ended = threading.Event()                    # the writer has finished
        self._drain = False                                # stop() found frames queued: open, deliver them
        self._error: str | None = None
        self._dropped = 0

    # -- properties (loop thread) ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        """The pipe is open or opening: not stopped, the writer not finished, the process alive (polling reaps
        an exited child)."""
        p = self._proc
        if p is None or self._stopping.is_set() or self._ended.is_set():
            return False
        return p.poll() is None

    @property
    def process_alive(self) -> bool:
        """Wireshark still runs, whether or not the pipe does (a stopped pipe leaves the window). Reaps."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        """Wireshark's pid once started, else None."""
        return None if self._proc is None else self._proc.pid

    @property
    def error(self) -> str | None:
        """Why the writer ended on its own; None while it runs, and when stop() or kill() ended it."""
        return self._error

    @property
    def dropped(self) -> int:
        """Frames write() dropped because the queue was full."""
        return self._dropped

    @property
    def fifo(self) -> Path | None:
        """The fifo's path once started, else None; kept after the writer removed the file."""
        return self._fifo

    # -- commands (loop thread) -----------------------------------------------------------------
    def start(self, path: str, open_deadline_s: float = 10.0) -> None:
        """Make the fifo, launch `path -k -i <fifo>` in its own session and start the writer. An OSError from
        the launch propagates, with the temp dir removed."""
        if self._proc is not None:
            raise RuntimeError("a WiresharkPipe is started once")
        d = Path(tempfile.mkdtemp(prefix="trex-web-gui-ws-"))
        fifo = d / FIFO_NAME
        try:
            os.mkfifo(fifo, 0o600)
            self._proc = subprocess.Popen([path, "-k", "-i", str(fifo)], start_new_session=True,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        except BaseException:
            _remove_dir(d)
            raise
        self._dir, self._fifo = d, fifo
        _live.add(self)
        self._thread = threading.Thread(target=self._writer, args=(fifo, open_deadline_s),
                                        name="wireshark-writer", daemon=True)
        self._thread.start()

    def write(self, ts_rel: float, raw: bytes, wirelen: int) -> None:
        """Queue one frame, never blocking; a full queue drops it and counts. Ignored once the pipe ended."""
        if self._proc is None or self._stopping.is_set() or self._ended.is_set():
            return
        if self._queue.qsize() >= QUEUE_MAX:
            self._dropped += 1
            return
        self._queue.put_nowait((ts_rel, raw, wirelen))

    def stop(self) -> None:
        """Close the fifo once the queued frames are out (a pipe still opening keeps trying, until its
        deadline, only when frames are queued); the window stays. Waits up to JOIN_S for the writer."""
        self._drain = not self._queue.empty()
        self._stopping.set()
        self._queue.put_nowait(_STOP)
        if self._thread is not None:
            self._thread.join(JOIN_S)

    def kill(self) -> None:
        """End Wireshark's process group and the writer (a blocked write fails with EPIPE once the reader is
        gone). Safe to call more than once and on a stopped or ended pipe."""
        self._stopping.set()
        self._queue.put_nowait(_STOP)
        self._terminate()
        if self._thread is not None:
            self._thread.join(JOIN_S)
        _live.discard(self)

    # -- internals --------------------------------------------------------------------------------
    def _terminate(self) -> None:
        """SIGTERM Wireshark's process group, then SIGKILL after KILL_WAIT_S; nothing when the process already
        exited and was reaped. Called by kill() and by the writer at the open deadline."""
        p = self._proc
        if p is None or p.poll() is not None:            # exited and reaped: its pid may belong to another
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(p.pid, sig)
            try:
                p.wait(KILL_WAIT_S)
                return
            except subprocess.TimeoutExpired:
                continue

    def _writer(self, fifo: Path, open_deadline_s: float) -> None:
        """The writer thread: the only code that opens or closes the fifo fd. On the way out it closes the fd,
        removes the temporary directory, records the error (none when stop() asked for the end), sets `_ended`
        and, on an error, notifies `on_change`."""
        fd: int | None = None
        error: str | None = None
        try:
            fd, error = self._open(fifo, open_deadline_s)
            if fd is not None:
                os.set_blocking(fd, True)
                pcap.write_all(fd, pcap.HEADER)
                error = self._pump(fd)
        except BrokenPipeError:
            error = CLOSED
        except OSError as e:
            error = f"Writing to Wireshark failed: {e}"
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            if self._dir is not None:
                _remove_dir(self._dir)
            if self._stopping.is_set():
                error = None                             # asked for: not an error
            self._error = error
            self._ended.set()
            if error is not None:
                self._notify()

    def _open(self, fifo: Path, open_deadline_s: float) -> tuple[int | None, str | None]:
        """Writer thread: open the fifo's write end, retried every OPEN_RETRY_S while there is no reader;
        returns (fd, None), (None, the reason) when Wireshark exited first or the deadline passed (which
        terminates it), or (None, None) when stopped with no frames to deliver. Any other OSError raises."""
        end = time.monotonic() + open_deadline_s
        assert self._proc is not None
        while True:
            if self._stopping.is_set() and not self._drain:
                return None, None
            try:
                return os.open(fifo, os.O_WRONLY | os.O_NONBLOCK), None
            except OSError as e:
                if e.errno != errno.ENXIO:               # ENXIO: no reader yet
                    raise
            if self._proc.poll() is not None:
                return None, "Wireshark exited before it opened the pipe"
            if time.monotonic() >= end:
                self._terminate()
                return None, f"Wireshark did not open the pipe within {open_deadline_s:g} s"
            time.sleep(OPEN_RETRY_S)

    def _pump(self, fd: int) -> str | None:
        """Writer thread: write each queued frame as a pcap record until the `_STOP` sentinel, or return
        CLOSED when Wireshark exited while the queue was idle. A failed write raises (BrokenPipeError once
        the window closed)."""
        assert self._proc is not None
        while True:
            try:
                item = self._queue.get(timeout=IDLE_CHECK_S)
            except queue.Empty:
                if self._proc.poll() is not None:        # the window closed while nothing was being written
                    return CLOSED
                continue
            if item is _STOP:
                return None
            ts_rel, raw, wirelen = cast(tuple[float, bytes, int], item)
            pcap.write_all(fd, pcap.pack_record(max(0.0, ts_rel), raw, wirelen))

    def _notify(self) -> None:
        """Writer thread: schedule `on_change` on the loop; nothing when the loop is closed."""
        with contextlib.suppress(RuntimeError):         # the loop closed meanwhile: nobody to tell
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._on_change)


def _remove_dir(d: Path) -> None:
    """Remove the fifo, then the temp dir; a missing fifo, or a dir that cannot be removed, is ignored."""
    with contextlib.suppress(FileNotFoundError):
        (d / FIFO_NAME).unlink()
    with contextlib.suppress(OSError):
        d.rmdir()


@atexit.register
def _kill_live() -> None:
    """At interpreter exit: kill every pipe still alive, ignoring any failure."""
    for p in list(_live):
        with contextlib.suppress(Exception):
            p.kill()
