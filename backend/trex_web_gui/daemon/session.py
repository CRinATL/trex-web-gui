"""The daemon session: one connection to a TRex daemon, its 2 s status poll, and the safety rules around
every call that changes state (D1-D3, D12, D13).

Everything here runs on the event loop except the daemon calls, which run one at a time on the executor
`daemon-rpc` (never TrexSession's executors), each under `asyncio.wait_for(deadline + 1)`. A state-changing
operation (connect, push, start, stop) holds the single `_op` slot; a second one raises `busy`. A hung read
therefore cannot block the Control session. Only the status poll counts its reads (`_reads`); a state-changing
call waits up to BUSY_WAIT_S for those, then answers `busy`, so it cannot queue behind a hung poll."""
import asyncio
import dataclasses
import functools
import getpass
import logging
import re
import shlex
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from .. import stfyaml
from ..sessions.trex_session import Publish
from ..trexapi import port_owners
from ..trexapi.errors import TrexApiError
from .client import CALL_TIMEOUT_S, PUSH_TIMEOUT_S, DaemonClient, DaemonFault, DaemonUnreachable, StfOptions
from .config import ConfigError, defaults, metadata_version, parse_yaml, render_yaml, validate

log = logging.getLogger(__name__)

POLL_S = 2.0                    # status poll while connected (A10.4)
STOP_WAIT_S = 10.0              # how long Stop waits for is_running to turn false
STOP_POLL_S = 0.2
BUSY_WAIT_S = 1.0               # how long a state-changing call lets in-flight reads finish before `busy`
OWNERS_DEADLINE_S = 10.0        # the whole port-owner read; each of its RPCs has its own 2 s
START_TIMEOUT_MAX_S = 300.0
MAX_TREX_ARGS = 20
MAX_C = 64

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.,:=/+-]+$")
# a token (or the part of it before `=`) that would change what root TRex runs: the typed fields, the
# stateless/ASTF mode switches and the files TRex would read
_FORBIDDEN_TOKENS = frozenset({"--cfg", "-i", "--astf", "-f", "-d", "-m", "-l", "--rx-check", "--client_cfg",
                              "--client-cfg"})
_FORBIDDEN_LONG = tuple(t for t in _FORBIDDEN_TOKENS if t.startswith("--"))


def _is_forbidden(token: str) -> bool:
    """True for a forbidden flag, with or without `=value`, and for any unique-prefix abbreviation of a
    forbidden long flag. TRex's SimpleOpt resolves a partial `--` option to its only match unless
    SO_O_EXACT is set (trex-core-history src/common/arg/SimpleOpt.h:922-925; the table and the parser
    are at src/main_dpdk.cpp:285-286,546), so `--cf` or `--client_` reaches `--cfg` / `--client_cfg`."""
    flag = token.split("=", 1)[0]
    if flag in _FORBIDDEN_TOKENS:
        return True
    return flag.startswith("--") and len(flag) > 2 and any(f.startswith(flag) for f in _FORBIDDEN_LONG)
_CFG_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]*$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}\.ya?ml$")
_STATE_NAMES = {1: "Idle", 2: "Starting", 3: "Running"}
_STF_NO_PORTS = "a stateful (-f) run has no command port, so its users cannot be seen"
_FORCE_HINT = "Force stop can proceed if you acknowledge that the owners are unknown."


class DaemonRefused(TrexApiError):
    """A refusal made by trex-web-gui before any state-changing daemon call. `code` is one of
    confirm_required, not_connected, busy, already_running, foreign_trex, reserved, in_use, owners_unknown,
    not_ours, invalid_config, invalid_profile and plan_changed."""
    def __init__(self, code: str, message: str) -> None:
        """Keep the short reason as .code beside the message."""
        super().__init__(message)
        self.code = code


def _is_stf(cmd: str) -> bool:
    """True when the TRex command line has `-f` and not `-i`: a stateful run, which has no command port."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    return "-f" in toks and "-i" not in toks


def _stf_profile_of(cmd: str) -> str | None:
    """The token after `-f` in a TRex command line, or None."""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return None
    i = toks.index("-f") if "-f" in toks else -1
    return toks[i + 1] if 0 <= i < len(toks) - 1 else None


def _state_name(raw: Any) -> str:
    """The daemon's running state as a name: 1/2/3 are Idle/Starting/Running, a string stays as it is."""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return _STATE_NAMES.get(raw, str(raw))
    return str(raw)


def _config_errors(errors: list[ConfigError]) -> list[dict[str, Any]]:
    """ConfigErrors as plain dicts for JSON."""
    return [dataclasses.asdict(e) for e in errors]


def _check_cfg_and_options(cfg_path: str, options: dict[str, int]) -> None:
    """ValueError unless cfg_path is absolute with no `..` and characters in [A-Za-z0-9_./-], and options is
    at most {c: 1..64} (D9)."""
    if not isinstance(cfg_path, str) or not _CFG_PATH_RE.fullmatch(cfg_path) or ".." in cfg_path.split("/"):
        raise ValueError("cfg_path must be absolute, with no `..`, using only A-Z a-z 0-9 _ . / -")
    if not isinstance(options, dict) or set(options) - {"c"}:
        raise ValueError("options may only hold c")
    if "c" in options:
        c = options["c"]
        if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= MAX_C:
            raise ValueError(f"c must be an int in 1..{MAX_C}")


def _check_trex_args(trex_args: str) -> list[str]:
    """The tokens of `trex_args` (shlex.split), or ValueError: at most 20, each matching
    ^[A-Za-z0-9_.,:=/+-]+$, none a forbidden flag (also as `flag=value`) or its long-flag prefix."""
    toks = shlex.split(trex_args)
    if len(toks) > MAX_TREX_ARGS:
        raise ValueError(f"trex_args may hold at most {MAX_TREX_ARGS} tokens")
    for t in toks:
        if not _TOKEN_RE.fullmatch(t):
            raise ValueError(f"trex_args token {t!r} has characters outside A-Z a-z 0-9 _ . , : = / + -")
        if _is_forbidden(t):
            raise ValueError(f"trex_args token {t!r} is not allowed here")
    return toks


def _check_stf(stf: StfOptions, trex_args: str) -> None:
    """ValueError for an out-of-bounds d, m, l or rx_check, or for any trex_args (D13)."""
    stfyaml.check_stf_bounds(stf.d, stf.m, stf.l, stf.rx_check)
    if trex_args.strip():
        raise ValueError("trex_args must be empty for a stateful run")


class DaemonSession:
    """At most one daemon connection. All methods run on the loop; the daemon calls go to `daemon-rpc`."""

    def __init__(self, publish: Publish, session_owned: Callable[[str, int], set[int] | None]) -> None:
        """Disconnected, with no run. `session_owned(host, sync_port)` is the ids of the ports this backend's
        TrexSession owns when it is connected to that server, else None."""
        self._publish, self._session_owned = publish, session_owned
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daemon-rpc")
        self._client: DaemonClient | None = None
        self._target: dict[str, Any] | None = None
        self._state = "disconnected"
        self._gen = 0
        self._poll_task: asyncio.Task[None] | None = None
        self._op = False                    # a state-changing operation is in flight
        self._reads = 0                     # status reads in flight
        self._metadata: list[dict[str, Any]] = []
        self._version = ""
        self._status: dict[str, Any] | None = None
        self._files_path: str | None = None
        self._last_error: str | None = None
        self._cmds: list[tuple[int, str]] = []
        self._reserved = False
        self._pushed: list[str] = []
        self._running_seq: int | None = None
        self._run: dict[str, Any] | None = None
        self._last_target: tuple[str, int] | None = None     # the run record belongs to this target
        self._stop_sent = False
        self._last_msg: dict[str, Any] | None = None

    # -- publishing ---------------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """The `daemon` hub message for the current state."""
        return {"type": "daemon", "state": self._state,
                "target": dict(self._target) if self._target else None,
                "version": self._version or None, "status": dict(self._status) if self._status else None,
                "running_seq": self._running_seq, "files_path": self._files_path,
                "last_error": self._last_error,
                "trex_cmds": [{"pid": p, "cmd": c} for p, c in self._cmds], "reserved": self._reserved,
                "pushed": list(self._pushed), "busy": self._op, "run": dict(self._run) if self._run else None}

    def _flush(self) -> None:
        """Publish the snapshot when it differs from the last one published (coalescing)."""
        msg = self.snapshot()
        if msg != self._last_msg:
            self._last_msg = msg
            self._publish(msg)

    def _log_warning(self, message: str) -> None:
        """Log `message` and publish it as a `log` hub message at level warning (as TrexSession does)."""
        log.warning(message)
        self._publish({"type": "log", "level": "warning", "message": message, "ts": time.time()})

    # -- plumbing -----------------------------------------------------------------------------------
    def _need_client(self) -> DaemonClient:
        """The connected client, or DaemonRefused not_connected."""
        if self._client is None:
            raise DaemonRefused("not_connected", "Not connected to a TRex daemon.")
        return self._client

    async def _call(self, fn: Callable[..., Any], *args: Any, deadline: float = CALL_TIMEOUT_S) -> Any:
        """Run one blocking daemon call on `daemon-rpc` under wait_for(deadline + 1); the client's own
        deadline fires first, so the wait_for is the backstop. DaemonUnreachable when it fires."""
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._executor, functools.partial(fn, *args))
        try:
            return await asyncio.wait_for(fut, deadline + 1)
        except TimeoutError:
            raise DaemonUnreachable(f"the daemon did not answer within {deadline + 1:.0f} s") from None

    async def _begin_op(self) -> None:
        """Take the state-changing slot. `busy` when another operation holds it, or when a read is still in
        flight after BUSY_WAIT_S (a healthy poll finishes in milliseconds; a hung one is the reason)."""
        if self._op:
            raise DaemonRefused("busy", "Another daemon operation is in progress.")
        end = time.monotonic() + BUSY_WAIT_S
        while self._reads and time.monotonic() < end:
            await asyncio.sleep(0.02)
        if self._op or self._reads:
            raise DaemonRefused("busy", "The daemon is busy with another call.")
        self._op = True
        self._flush()

    def _end_op(self) -> None:
        """Release the state-changing slot."""
        self._op = False
        self._flush()

    # -- connection ---------------------------------------------------------------------------------
    async def connect(self, host: str, port: int) -> dict[str, Any]:
        """Drop any connection, read the daemon's identity, config, metadata, devices, files dir and status,
        and start the 2 s poll. DaemonUnreachable / DaemonFault when the daemon cannot be read (the session
        is then disconnected). DaemonRefused not_connected when disconnect() ran while the reads were out: the
        session stays disconnected, with no client and no poll. A different target than the last one clears
        the run record."""
        await self._begin_op()
        try:
            await self._drop()
            gen = self._gen
            client = DaemonClient(host, port)
            version = await self._call(client.version)
            config_yaml = await self._call(client.config_text)
            metadata = await self._call(client.metadata)
            devices = await self._call(client.devices)
            files_path = await self._call(client.files_path)
            status = await self._call(client.running_status)
            reserved = await self._call(client.is_reserved)
            cmds = await self._call(client.trex_cmds)
            if gen != self._gen:            # disconnect() does not take the slot, and ran while we read
                raise DaemonRefused("not_connected", "Disconnected while connecting.")
        except BaseException:
            self._end_op()
            raise
        self._op = False
        if self._last_target != (host, port):
            self._run, self._running_seq, self._pushed = None, None, []
        self._last_target = (host, port)
        self._client, self._metadata, self._files_path = client, metadata, files_path
        self._version = version.strip()
        self._target = {"host": host, "port": port}
        self._status = {"state": _state_name(status.get("state")), "verbose": str(status.get("verbose", ""))}
        self._reserved, self._cmds, self._last_error = bool(reserved), cmds, None
        self._apply_idle()
        self._state = "connected"
        self._flush()
        self._poll_task = asyncio.create_task(self._poll_loop(self._gen))
        return {"version": self._version, "status": dict(self._status), "metadata": metadata,
                "metadata_version": metadata_version(metadata), "config_yaml": config_yaml,
                "devices": [*devices, {"id": "dummy"}], "files_path": files_path}

    async def _drop(self) -> None:
        """Forget the connection: stop the poll and clear what belongs to it. The run record stays."""
        self._gen += 1
        task, self._poll_task = self._poll_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._client, self._target, self._state = None, None, "disconnected"
        self._status, self._files_path, self._last_error = None, None, None
        self._cmds, self._reserved, self._version, self._metadata = [], False, "", []

    async def disconnect(self) -> None:
        """Stop the poll and publish `disconnected`. The run record survives (the dialog disconnects on
        close)."""
        await self._drop()
        self._flush()

    async def close(self) -> None:
        """disconnect(), then shut the executor down off the loop (a daemon call in flight ends by its own
        deadline)."""
        await self.disconnect()
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)

    # -- polling ------------------------------------------------------------------------------------
    async def _poll_loop(self, gen: int) -> None:
        """Poll every POLL_S while this connection lasts; a tick is skipped while any call is in flight."""
        while gen == self._gen:
            await asyncio.sleep(POLL_S)
            if gen != self._gen:
                return
            if self._op or self._reads:
                continue
            await self._poll(False)

    async def status(self) -> dict[str, Any]:
        """One poll now (is_running, get_running_status, is_reserved, get_trex_cmds); the `daemon` message
        it publishes is returned. DaemonUnreachable / DaemonFault when a read fails; the state is then
        `error`. While a state-changing operation runs, the current message is returned unchanged."""
        self._need_client()
        if self._op:
            return self.snapshot()
        await self._poll(True)
        return self.snapshot()

    async def _poll(self, raise_errors: bool) -> None:
        """Read the four status calls, fold them into the state and publish. A failure sets state `error` and
        last_error (and is raised when `raise_errors`); the next good poll sets `connected` again."""
        client, gen = self._client, self._gen
        if client is None:
            return
        self._reads += 1
        try:
            running = bool(await self._call(client.is_running))
            status = await self._call(client.running_status)
            reserved = await self._call(client.is_reserved)
            cmds = await self._call(client.trex_cmds)
        except TrexApiError as exc:
            if gen == self._gen:
                self._state, self._last_error = "error", str(exc)
                self._flush()
            if raise_errors:
                raise
            return
        finally:
            self._reads -= 1
        if gen != self._gen:
            return                          # disconnected or reconnected while the calls were out
        self._status = {"state": _state_name(status.get("state")), "verbose": str(status.get("verbose", ""))}
        self._reserved, self._cmds = bool(reserved), cmds
        self._state, self._last_error = "connected", None
        self._apply_idle(running)
        self._flush()

    def _apply_idle(self, running: bool | None = None) -> None:
        """An Idle status ends a `running` run (`stopped` when this session sent a Stop, else `ended`; the
        outcome is the first line of the status's verbose) and clears running_seq."""
        if self._status is None or self._status["state"] != "Idle" or running:
            return
        self._running_seq = None
        run = self._run
        if run is not None and run["state"] == "running":
            run["state"] = "stopped" if self._stop_sent else "ended"
            run["outcome"] = (self._status["verbose"].splitlines() or [""])[0]

    # -- config -------------------------------------------------------------------------------------
    def preview(self, values: dict[str, Any] | None = None, text: str | None = None) -> dict[str, Any]:
        """{yaml, values, errors} for form `values` (rendered to YAML) or for YAML `text` (parsed; `yaml` is
        the text as given). Neither: the metadata's defaults. Errors are checked against the connected
        daemon's metadata (none when disconnected). Makes no daemon call."""
        if values is not None and text is not None:
            raise ValueError("give values or text, not both")
        if text is not None:
            parsed, errors = parse_yaml(text)
            if parsed is None:
                return {"yaml": text, "values": None, "errors": _config_errors(errors)}
            errors = validate(parsed, self._metadata)
            return {"yaml": text, "values": parsed, "errors": _config_errors(errors)}
        vals = defaults(self._metadata) if values is None else values
        return {"yaml": render_yaml(vals), "values": vals,
                "errors": _config_errors(validate(vals, self._metadata))}

    async def push(self, yaml_text: str, filename: str | None, confirm: bool) -> dict[str, str]:
        """Write the config to the daemon's files dir as `filename` (default trex-web-gui-<user>.yaml);
        returns {path}. confirm_required without `confirm`; ValueError for a bad name (it must match
        ^[A-Za-z0-9_.-]{1,64}\\.ya?ml$ and not start with `tg-`); invalid_config when parse or validate
        fails, with no call made."""
        if confirm is not True:
            raise DaemonRefused("confirm_required", "Pushing a config needs confirm.")
        client = self._need_client()
        if filename is None:
            filename = "trex-web-gui-" + re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())[:40] + ".yaml"
        if not _FILENAME_RE.fullmatch(filename) or filename.startswith("tg-"):
            raise ValueError("filename must match ^[A-Za-z0-9_.-]{1,64}\\.ya?ml$ and not start with tg-")
        values, errors = parse_yaml(yaml_text)
        if values is not None:
            errors = validate(values, self._metadata)
        if errors:
            first = errors[0]
            where = f"{first.path}: " if first.path else ""
            raise DaemonRefused("invalid_config", f"The config is invalid ({len(errors)} problem(s)): "
                                f"{where}{first.message}")
        await self._begin_op()
        try:
            await self._call(client.push_file, filename, yaml_text, PUSH_TIMEOUT_S, deadline=PUSH_TIMEOUT_S)
            path = f"{self._files_path}/{filename}"
            if path not in self._pushed:
                self._pushed.append(path)
            return {"path": path}
        finally:
            self._end_op()

    # -- start --------------------------------------------------------------------------------------
    def command_preview(self, cfg_path: str, options: dict[str, int], trex_args: str,
                        stf: StfOptions | None = None) -> str:
        """The command the daemon will run, in `generate_run_cmd`'s format (trex_server.py:498-559) with its
        default nice of -19: `nice -n -19 ./t-rex-64 --iom 0 --cfg <cfg> [-c N] -i [<args>] --no-key`, or with
        `stf` the -f form. The daemon's own string has a double space after `--iom 0`; this one collapses
        runs of spaces. ValueError when an input breaks the rules."""
        _check_cfg_and_options(cfg_path, options)
        toks = _check_trex_args(trex_args)
        parts = ["nice -n -19 ./t-rex-64 --iom 0 --cfg", cfg_path]
        c = f" -c {options['c']}" if "c" in options else ""
        if stf is None:
            return " ".join(parts) + c + " -i" + "".join(" " + t for t in toks) + " --no-key"
        _check_stf(stf, trex_args)
        parts += ["-f", stf.profile, "-d", str(stf.d), "-m", str(float(stf.m))]
        if stf.l > 0:
            parts += ["-l", str(stf.l)]
        if stf.rx_check > 0:
            parts += ["--rx-check", str(stf.rx_check)]
        return " ".join(parts) + c + " --no-key"

    async def stf_profiles(self, source: Literal["stock", "uploaded"]) -> dict[str, Any]:
        """{source, files}: the stock profiles (`cap2/x.yaml`, `avl/x.yaml` matching STOCK_PROFILE_RE) or
        the uploaded `<files_path>/tg-*.yaml`. A dir the daemon refuses lists nothing."""
        client = self._need_client()
        files: list[str] = []
        if source == "stock":
            for d in ("cap2", "avl"):
                try:
                    _dirs, names = await self._call(client.files_list, d)
                except DaemonFault:
                    continue
                files += [f"{d}/{n}" for n in names if n.endswith(".yaml")
                          and re.fullmatch(stfyaml.STOCK_PROFILE_RE, f"{d}/{n}")]
        elif source == "uploaded":
            try:
                _dirs, names = await self._call(client.files_list, self._files_path)
            except DaemonFault:
                names = []
            files = [f"{self._files_path}/{n}" for n in names if re.fullmatch(stfyaml.TG_YAML_RE, n)]
        else:
            raise ValueError("source must be stock or uploaded")
        return {"source": source, "files": sorted(files)}

    async def stf_profile(self, path: str) -> dict[str, Any]:
        """{path, duration, templates, errors, warnings} for one profile. `invalid_profile` when the path is
        neither a stock path nor an uploaded one. A file that cannot be read, is over 64 KiB or fails
        stfyaml.parse is an `errors` entry, not an exception. An uploaded profile's templates must each be
        `<files_path>/tg-*.pcap` that the daemon lists (ruling R16)."""
        client = self._need_client()
        return await self._stf_profile(client, path)

    async def _stf_profile(self, client: DaemonClient, path: str) -> dict[str, Any]:
        """stf_profile's body, on a given client (start uses it inside its own operation)."""
        files_path = self._files_path or ""
        stock = stfyaml.stock_profile_ok(path)
        if not stock and not stfyaml.uploaded_profile_ok(path, files_path):
            raise DaemonRefused("invalid_profile", f"{path!r} is not a stock profile (cap2/x.yaml, "
                                "avl/x.yaml) or an uploaded tg-*.yaml in the daemon's files dir.")
        out: dict[str, Any] = {"path": path, "duration": None, "templates": [], "errors": [], "warnings": []}
        try:
            data = await self._call(client.get_file, path)
        except DaemonFault as exc:
            out["errors"] = [f"cannot read {path}: {exc.message}"]
            return out
        if len(data) > stfyaml.STF_YAML_MAX_BYTES:
            out["errors"] = [f"{path} is larger than {stfyaml.STF_YAML_MAX_BYTES} bytes"]
            return out
        entry, errors, warnings = stfyaml.parse(data.decode("utf-8", errors="replace"))
        out["errors"], out["warnings"] = list(errors), list(warnings)
        if entry is None or errors:
            return out
        names = stfyaml.templates(entry)
        out["templates"], out["duration"] = names, entry.get("duration")
        if not stock:
            await self._check_uploaded_templates(client, names, files_path, out["errors"])
        return out

    async def _check_uploaded_templates(self, client: DaemonClient, names: list[str], files_path: str,
                                        errors: list[str]) -> None:
        """Append one error per template that is not `<files_path>/<tg-*.pcap>` listed by the daemon."""
        try:
            _dirs, listed = await self._call(client.files_list, files_path)
        except DaemonFault:
            listed = []
        prefix = files_path.rstrip("/") + "/"
        for name in names:
            tail = name[len(prefix):] if name.startswith(prefix) else ""
            if not re.fullmatch(stfyaml.TG_PCAP_RE, tail) or tail not in listed:
                errors.append(f"template {name!r} must be {prefix}tg-<12 hex>-<name>.pcap, "
                              "uploaded to the daemon")

    async def start(self, cfg_path: str, options: dict[str, int], trex_args: str, timeout_s: float,
                    confirm: bool, stf: StfOptions | None = None) -> dict[str, Any]:
        """Start TRex. Refusals, in order, all before `start_trex`: confirm_required, already_running,
        reserved, foreign_trex, then (with `stf`) the bounds and empty trex_args (ValueError) and
        invalid_profile. Returns {seq, command_preview, run}. A daemon fault (a bad config, say) propagates
        as DaemonFault and leaves the run record alone."""
        if confirm is not True:
            raise DaemonRefused("confirm_required", "Starting TRex needs confirm.")
        client = self._need_client()
        _check_cfg_and_options(cfg_path, options)
        _check_trex_args(trex_args)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
                or not 0 < timeout_s <= START_TIMEOUT_MAX_S:
            raise ValueError(f"timeout_s must be a number in 0..{START_TIMEOUT_MAX_S:.0f}")
        await self._begin_op()
        try:
            if await self._call(client.is_running):
                raise DaemonRefused("already_running", "TRex is already running on this daemon.")
            if await self._call(client.is_reserved):
                raise DaemonRefused("reserved", "The daemon is reserved.")
            cmds = await self._call(client.trex_cmds)
            if cmds:
                listing = "; ".join(f"pid {pid}: {cmd}" for pid, cmd in cmds)
                raise DaemonRefused("foreign_trex", f"A TRex this daemon did not start is running: {listing}")
            if stf is not None:
                _check_stf(stf, trex_args)
                prof = await self._stf_profile(client, stf.profile)
                if prof["errors"]:
                    raise DaemonRefused("invalid_profile", f"{stf.profile}: {prof['errors'][0]}")
            preview = self.command_preview(cfg_path, options, trex_args, stf)
            user = "trex-web-gui:" + getpass.getuser()
            seq = await self._call(client.start_trex, cfg_path, options, user, timeout_s, trex_args, stf,
                                   deadline=timeout_s + 5)
            self._stop_sent = False
            self._running_seq = seq
            self._run = {"mode": "stl" if stf is None else "stf", "seq": seq,
                         "profile": None if stf is None else stf.profile,
                         "duration_s": None if stf is None else stf.d, "started_at": time.time(),
                         "state": "running", "outcome": None}
            return {"seq": seq, "command_preview": preview, "run": dict(self._run)}
        finally:
            self._end_op()

    # -- stop ---------------------------------------------------------------------------------------
    def _is_our_stf_run(self, cmds: list[tuple[int, str]]) -> bool:
        """D12: our running_seq, run.mode stf, and exactly one TRex command, an STF one whose -f value is
        run.profile."""
        run = self._run
        return (self._running_seq is not None and run is not None and run["mode"] == "stf"
                and len(cmds) == 1 and _is_stf(cmds[0][1]) and _stf_profile_of(cmds[0][1]) == run["profile"])

    async def _check_owners(self, host: str, rpc_port: int, force: bool, ack: bool) -> str | None:
        """Read every port's owner through the TRex command port. DaemonRefused in_use for an owner outside
        this backend's own session; owners_unknown when the read fails, except for Force stop with the ack,
        which gets the cause back instead (None when the read worked)."""
        try:
            owners = await self._call(port_owners, host, rpc_port, 2.0, deadline=OWNERS_DEADLINE_S)
            owned = self._session_owned(host, rpc_port) or set()
        except Exception as exc:
            cause = str(exc) or type(exc).__name__
            if force and ack:
                return cause
            msg = (f"Cannot read who owns the ports at {host}:{rpc_port} ({cause}). "
                   "Stop needs the TRex command port to see who owns its ports.")
            raise DaemonRefused("owners_unknown", msg + (" " + _FORCE_HINT if force else "")) from exc
        for pid, owner in sorted(owners.items()):
            if owner and pid not in owned:
                raise DaemonRefused("in_use", f"port {pid}: {owner}")
        return None

    async def stop(self, rpc_port: int, confirm: bool, force: bool = False,
                   owners_unknown_ack: bool = False) -> dict[str, Any]:
        """Stop (or Force stop) TRex; returns {running, stop_s}. Refusals before any state-changing call:
        confirm_required; for a stateful run that is not ours, not_ours (Stop) or owners_unknown (Force stop,
        lifted by `owners_unknown_ack`); in_use when another user owns a port (never lifted);
        owners_unknown when the owners cannot be read (lifted only for Force stop with the ack); not_ours
        when no run of ours is held and `force` is not set. Our own STF run is stopped without a port read
        (D12)."""
        if confirm is not True:
            raise DaemonRefused("confirm_required", "Stopping TRex needs confirm.")
        client = self._need_client()
        host = client.host
        await self._begin_op()
        try:
            cmds = await self._call(client.trex_cmds)
            ours = self._is_our_stf_run(cmds)
            cause: str | None = None
            kill_only = False
            if not ours and any(_is_stf(cmd) for _pid, cmd in cmds):
                if not force:
                    raise DaemonRefused("not_ours", "A stateful (-f) TRex run is going that this backend did "
                                        "not start; Stop never stops a run it did not start.")
                if not owners_unknown_ack:
                    raise DaemonRefused("owners_unknown", f"Cannot tell who runs it: {_STF_NO_PORTS}. "
                                        + _FORCE_HINT)
                cause, kill_only = _STF_NO_PORTS, True
            elif not ours:
                cause = await self._check_owners(host, rpc_port, force, owners_unknown_ack)
            seq = self._running_seq
            if seq is not None and not kill_only:
                await self._call(client.stop_trex, seq)
            elif force:
                if cause is not None:
                    self._log_warning(f"Force stop on {host}:{rpc_port} with owners_unknown_ack: {cause}")
                await self._call(client.force_trex_kill)
            else:
                raise DaemonRefused("not_ours", "This backend holds no run to stop; Stop never stops a run "
                                    "it did not start. Force stop can.")
            self._stop_sent = True
            return await self._wait_stopped(client)
        finally:
            self._end_op()

    async def _wait_stopped(self, client: DaemonClient) -> dict[str, Any]:
        """Poll is_running until false, for up to STOP_WAIT_S; then fold in one status read so a stopped run
        is recorded at once. A TRex still running is not an error: {running: true, stop_s: None}."""
        t0 = time.monotonic()
        stop_s: float | None = None
        while True:
            if not await self._call(client.is_running):
                stop_s = round(time.monotonic() - t0, 3)
                break
            if time.monotonic() - t0 >= STOP_WAIT_S:
                break
            await asyncio.sleep(STOP_POLL_S)
        try:
            await self._poll(False)
        except TrexApiError:        # _poll(False) records its own failures
            pass
        return {"running": stop_s is None, "stop_s": stop_s}

    # -- upload -------------------------------------------------------------------------------------
    async def _plan_upload(self, path: str) -> stfyaml.UploadPlan:
        """The upload plan for the local profile `path`, read off the loop. DaemonRefused invalid_profile
        when stfyaml.plan_upload refuses it."""
        files_path = self._files_path or ""
        try:
            return await asyncio.to_thread(stfyaml.plan_upload, Path(path), files_path)
        except ValueError as exc:
            raise DaemonRefused("invalid_profile", str(exc)) from exc

    async def _listed(self, client: DaemonClient) -> set[str]:
        """The names get_files_list reports in the daemon's files dir."""
        _dirs, names = await self._call(client.files_list, self._files_path)
        return set(names)

    async def stf_upload_preview(self, path: str) -> dict[str, Any]:
        """What uploading the local profile `path` would write: {profile, files: [{template, local, remote,
        bytes, exists}], total_bytes, yaml, yaml_remote, yaml_bytes, yaml_exists, warnings}. `exists` comes
        from the daemon's listing. not_connected without a connection; invalid_profile when the plan is
        refused. Makes no write."""
        client = self._need_client()
        plan = await self._plan_upload(path)
        listed = await self._listed(client)
        return {"profile": f"{self._files_path}/{plan.yaml_remote}",
                "files": [{"template": f.template, "local": str(f.local), "remote": f.remote, "bytes": f.size,
                           "exists": f.remote in listed} for f in plan.pcaps],
                "total_bytes": plan.total_bytes, "yaml": plan.yaml_text, "yaml_remote": plan.yaml_remote,
                "yaml_bytes": len(plan.yaml_text.encode("utf-8")), "yaml_exists": plan.yaml_remote in listed,
                "warnings": list(plan.warnings)}

    async def stf_upload(self, path: str, confirm: bool, expected_yaml_remote: str,
                         expected_target: str) -> dict[str, Any]:
        """Push the local profile `path` and its pcaps into the daemon's files dir; returns {profile, pushed,
        skipped} (remote names). Refusals before any push_file: confirm_required, busy, invalid_profile and
        plan_changed (the re-planned profile's name is not `expected_yaml_remote`, the preview's: its hash
        covers every pcap's name, so the files differ from what the user confirmed; or the connected daemon
        is not `expected_target`, the host:port the confirm named). The plan and the
        listing are read again here. Pcaps go first, then the profile; a name already
        listed is skipped. A push_file that answers false is DaemonFault -33, and so is a name still
        missing from the listing afterwards."""
        if confirm is not True:
            raise DaemonRefused("confirm_required", "Uploading needs confirm.")
        client = self._need_client()
        connected = f"{self._target['host']}:{self._target['port']}" if self._target else ""
        if connected != expected_target:
            raise DaemonRefused("plan_changed", f"The confirm named {expected_target or 'no daemon'}, but "
                                f"the connected daemon is {connected}; open the upload again.")
        await self._begin_op()
        try:
            plan = await self._plan_upload(path)
            if plan.yaml_remote != expected_yaml_remote:
                raise DaemonRefused("plan_changed", f"The files changed since the preview "
                                    f"({plan.yaml_remote} is not {expected_yaml_remote}); preview again.")
            listed = await self._listed(client)
            remotes = [f.remote for f in plan.pcaps]
            for name, pattern in [(r, stfyaml.TG_PCAP_RE) for r in remotes] \
                    + [(plan.yaml_remote, stfyaml.TG_YAML_RE)]:
                if not re.fullmatch(pattern, name):
                    raise AssertionError(f"upload would write {name!r}, which is not a tg- name")
            pushed: list[str] = []
            skipped: list[str] = []
            for f in plan.pcaps:
                if f.remote in listed:
                    skipped.append(f.remote)
                    continue
                try:
                    data = await asyncio.to_thread(stfyaml.read_regular, f.local, stfyaml.PCAP_MAX_BYTES,
                                                   f"template {f.template!r} file")
                except ValueError as exc:
                    raise DaemonRefused("invalid_profile", str(exc)) from exc
                if stfyaml.tg_name(data, f.local.stem, "pcap") != f.remote:
                    raise DaemonRefused("invalid_profile", f"{f.local} changed while it was being uploaded.")
                await self._push_one(client, f.remote, data)
                pushed.append(f.remote)
            if plan.yaml_remote in listed:
                skipped.append(plan.yaml_remote)
            else:
                await self._push_one(client, plan.yaml_remote, plan.yaml_text.encode("utf-8"))
                pushed.append(plan.yaml_remote)
            now = await self._listed(client)
            for name in [*remotes, plan.yaml_remote]:
                if name not in now:
                    raise DaemonFault(-33, f"{name} is not in the daemon's file list after its push")
            return {"profile": f"{self._files_path}/{plan.yaml_remote}", "pushed": pushed, "skipped": skipped}
        finally:
            self._end_op()

    async def _push_one(self, client: DaemonClient, remote: str, data: bytes) -> None:
        """push_file one tg- file; a false answer is DaemonFault -33."""
        if not await self._call(client.push_file, remote, data, PUSH_TIMEOUT_S, deadline=PUSH_TIMEOUT_S):
            raise DaemonFault(-33, f"push_file returned false for {remote}")

    async def log(self, which: Literal["trex", "daemon"]) -> str:
        """The tail of TRex's log (`trex`) or the daemon's own (`daemon`), at most LOG_TAIL_BYTES."""
        client = self._need_client()
        if which == "trex":
            text: str = await self._call(client.trex_log)
        elif which == "daemon":
            text = await self._call(client.daemon_log)
        else:
            raise ValueError("which must be trex or daemon")
        return text
