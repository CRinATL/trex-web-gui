"""vm/record_p7.py without the guest: a harness that loads the recorder with a stub RPC client, so the
branches a recording run takes only when the server misbehaves (E3's crash and re-setup) run on every gate."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def record_p7(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """vm/record_p7.py loaded as a fresh module: the jammy defaults (no guest selected), `tmp_path` as its
    out_dir argument, and sys.path restored afterwards (the module prepends backend/ and vm/ at import)."""
    for name in ("TREX_VM_GUEST", "TREX_VM_EMU_PORT", "TREX_VM_CODENAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["record_p7.py", str(tmp_path)])
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("record_p7_under_test", ROOT / "vm" / "record_p7.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StubRpc:
    """A RawRpc stand-in: answers every call with an empty result, raises the recorder's ServerUnreachable for
    the (method, params) pairs in `hang`, and an error reply for those in `refuse`."""

    def __init__(self, mod: ModuleType, name: str, hang: list[tuple[str, dict]] = (),
                 refuse: list[tuple[str, dict]] = ()) -> None:
        """A client called `name`; `hang` and `refuse` are consumed one match each."""
        self.mod, self.name = mod, name
        self.hang, self.refuse = list(hang), list(refuse)
        self.calls: list[tuple[str, Any]] = []
        self.closed = False
        self._last: dict = {}

    def call(self, method: str, params: dict | None = None) -> dict:
        """One call: hang, refuse or answer, in that order of checks."""
        self.calls.append((method, params))
        req = {"method": method, "params": params}
        if (method, params) in self.hang:
            self.hang.remove((method, params))
            raise self.mod.ServerUnreachable(f"{method}: no reply within 10.0 s")
        if (method, params) in self.refuse:
            self.refuse.remove((method, params))
            self._last = {"req": req, "resp": {"error": {"code": -32000, "message": "refused"}}}
            raise self.mod.RawRpcError(-32000, "refused")
        self._last = {"req": req, "resp": {"result": {"data": []}}}
        return {"data": []}

    def last(self) -> dict:
        """The last request and reply, as Rpc.last() returns the last logged row."""
        return self._last

    def close(self) -> None:
        """Marks the client closed."""
        self.closed = True


def _step(mod: ModuleType, name: str) -> tuple[str, dict]:
    """(method, params) of the E3 step called `name`."""
    return next((m, p) for n, m, p, _ in mod.e3_steps() if n == name)


@pytest.fixture
def rig(record_p7: ModuleType, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """record_e3's collaborators replaced: sh and panic_block record, clear_server does nothing, fresh() hands
    out the clients queued in rig["next"] (a fresh StubRpc each when the queue is empty)."""
    rig: dict[str, Any] = {"sh": [], "opened": [], "next": []}
    monkeypatch.setattr(record_p7, "sh", lambda *cmd: rig["sh"].append(cmd))
    monkeypatch.setattr(record_p7, "panic_block", lambda: "panic: test")
    monkeypatch.setattr(record_p7, "clear_server", lambda rpc: None)

    def fresh(name: str, handshake: bool = True) -> StubRpc:
        """The next queued client, or a new well-behaved one."""
        rpc = rig["next"].pop(0) if rig["next"] else StubRpc(record_p7, name)
        rig["opened"].append((name, rpc))
        return rpc

    monkeypatch.setattr(record_p7, "fresh", fresh)
    return rig


def test_e3_runs_every_step_on_one_client_when_the_server_answers(record_p7: ModuleType,
                                                                  rig: dict[str, Any]) -> None:
    """No crash: all 29 steps run in order on the caller's client, nothing is restarted or re-set-up."""
    first = StubRpc(record_p7, "a")
    assert record_p7.record_e3(first) is first
    e3 = record_p7.summary["e3"]
    assert [s["name"] for s in e3["steps"]] == [n for n, *_ in record_p7.e3_steps()]
    assert len(e3["steps"]) == 29 and e3["resetup"] == [] and rig["sh"] == [] and not first.closed


def test_an_unanswered_e3_step_restarts_emu_and_re_runs_the_setup(record_p7: ModuleType,
                                                                  rig: dict[str, Any]) -> None:
    """A step the server never answers is kept as data (unanswered, with the guest's panic text); the old
    client is closed, EMU stopped and started, a new client opened, the two setup steps re-run on it under
    `after`, and the remaining steps run on the new client, which record_e3 returns."""
    crash = "e3_ctx_client_add_duplicate_in_request"
    first = StubRpc(record_p7, "a", hang=[_step(record_p7, crash)])
    second = StubRpc(record_p7, "e3")
    rig["next"].append(second)
    assert record_p7.record_e3(first) is second
    e3 = record_p7.summary["e3"]
    by_name = {s["name"]: s for s in e3["steps"]}
    assert len(e3["steps"]) == 29
    method = _step(record_p7, crash)[0]
    assert by_name[crash]["outcome"] == {"unanswered": f"{method}: no reply within 10.0 s",
                                         "guest": "panic: test"}
    assert first.closed and rig["sh"] == [("vm/emu.sh", "stop"), ("vm/emu.sh", "start")]
    assert [n for n, _ in rig["opened"]] == ["e3"]
    setup = record_p7.e3_steps()[:2]
    assert [(r["after"], r["name"], r["method"], r["params"]) for r in e3["resetup"]] == [
        (crash, n, m, p) for n, m, p, _ in setup]
    assert all(r["outcome"] == {"result": {"data": []}} for r in e3["resetup"])
    # the new client got the re-setup first, then every step after the crash
    after = [(m, p) for n, m, p, _ in record_p7.e3_steps()]
    after = after[after.index(_step(record_p7, crash)) + 1:]
    assert second.calls == [(m, p) for _, m, p, _ in setup] + after


def test_two_unanswered_steps_restart_twice(record_p7: ModuleType, rig: dict[str, Any]) -> None:
    """A second crash after a re-setup restarts again from the client then in use: two re-setups, each under
    its own `after`, and the third client is returned."""
    one, two = "e3_ctx_add_duplicate_in_request", "e3_ctx_remove_with_clients"  # params no earlier step sends
    first = StubRpc(record_p7, "a", hang=[_step(record_p7, one)])
    second = StubRpc(record_p7, "e3", hang=[_step(record_p7, two)])
    third = StubRpc(record_p7, "e3")
    rig["next"] += [second, third]
    assert record_p7.record_e3(first) is third
    assert first.closed and second.closed and not third.closed
    assert [r["after"] for r in record_p7.summary["e3"]["resetup"]] == [one, one, two, two]
    assert rig["sh"] == [("vm/emu.sh", "stop"), ("vm/emu.sh", "start")] * 2


def test_an_error_reply_is_data_and_kept_in_negatives(record_p7: ModuleType, rig: dict[str, Any]) -> None:
    """An error reply to a step that may fail is its outcome and a negatives entry; nothing is restarted."""
    name = "e3_ctx_client_remove_unknown_mac"
    first = StubRpc(record_p7, "a", refuse=[_step(record_p7, name)])
    assert record_p7.record_e3(first) is first
    by_name = {s["name"]: s for s in record_p7.summary["e3"]["steps"]}
    assert by_name[name]["outcome"] == {"error": {"code": -32000, "text": "refused"}}
    method, params = _step(record_p7, name)
    assert record_p7.negatives[name]["req"] == {"method": method, "params": params}
    assert rig["sh"] == []


def test_an_error_reply_to_a_setup_step_aborts(record_p7: ModuleType, rig: dict[str, Any]) -> None:
    """A setup step (must) that gets an error reply ends the recording with RuntimeError."""
    first = StubRpc(record_p7, "a", refuse=[_step(record_p7, "e3_setup_client_add")])
    with pytest.raises(RuntimeError, match="ctx_client_add failed"):
        record_p7.record_e3(first)
