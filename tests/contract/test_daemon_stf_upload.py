"""DaemonSession.stf_upload / stf_upload_preview (spec §13.7, Task 15) against tests/fake/fake_daemon.py: a
local profile and its pcaps pushed as content-addressed tg- files, pcaps first, nothing re-pushed."""
import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from helpers import Recorder, wait_for
from trex_web_gui import stfyaml
from trex_web_gui.daemon.client import DaemonClient, DaemonFault, StfOptions
from trex_web_gui.daemon.session import DaemonRefused, DaemonSession

FILES = "/tmp/trex_files"
MAGIC = bytes.fromhex("d4c3b2a1")
STF_FLAGS = ("--stf-scale", "0.05")
Body = Callable[[DaemonSession, Recorder], Awaitable[None]]


def drive(body: Body) -> None:
    """Run `body(session, recorder)` under asyncio.run and close the session in that loop, raised or not."""
    async def wrapped() -> None:
        """The body, then close."""
        rec = Recorder()
        s = DaemonSession(rec, lambda h, p: None)
        try:
            await body(s, rec)
        finally:
            await s.close()
    asyncio.run(wrapped())


def pushes(fake: Any, at_least: int = 0, timeout_s: float = 5.0) -> list[str]:
    """The names the fake's log shows push_file receiving, in order, once at least `at_least` are there: a
    thread collects the fake's output, so its last line can land after the client has the reply."""
    prefix = "[fake-daemon] push "
    deadline = time.monotonic() + timeout_s
    while True:
        names = [ln[len(prefix):] for ln in list(fake.lines) if ln.startswith(prefix)]
        if len(names) >= at_least or time.monotonic() >= deadline:
            return names
        time.sleep(0.02)


def make_profile(root: Path, pcaps: dict[str, bytes], yaml_name: str = "p.yaml") -> Path:
    """Write `pcaps` (template name -> content after the magic) under `root` and a profile naming them."""
    items = ""
    for name, body in pcaps.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(MAGIC + body)
        items += f"    - {{name: {name}, cps: 1, ipg: 10, rtt: 10, w: 4}}\n"
    y = root / yaml_name
    gen = "{distribution: seq, clients_start: 16.0.0.1}"
    y.write_text(f"- duration: 10\n  generator: {gen}\n  cap_info:\n{items}")
    return y


ANY_PLAN = "tg-000000000000-x.yaml"      # an expected name for calls refused before it is compared


def target_of(s: DaemonSession) -> str:
    """The connected target as the dialog names it, host:port ('' when not connected)."""
    t = s.snapshot()["target"]
    return f"{t['host']}:{t['port']}" if t else ""


async def upload(s: DaemonSession, y: Path) -> dict[str, Any]:
    """Preview the profile, then upload it expecting exactly what the preview named (the dialog's flow)."""
    prev = await s.stf_upload_preview(str(y))
    return await s.stf_upload(str(y), True, prev["yaml_remote"], target_of(s))


async def refused(coro: Awaitable[Any], code: str) -> DaemonRefused:
    """Await `coro`, which must raise DaemonRefused with `code`; return the exception."""
    with pytest.raises(DaemonRefused) as e:
        await coro
    assert e.value.code == code, e.value.message
    return e.value


def test_upload_needs_confirm_and_makes_no_push(fake_daemon: Any, tmp_path: Path) -> None:
    """Without confirm: 409 confirm_required and no push_file. The preview, a refused plan and a busy
    session make no write either."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Each call that must not write."""
        await refused(s.stf_upload(str(y), True, ANY_PLAN, target_of(s)), "not_connected")
        await refused(s.stf_upload_preview(str(y)), "not_connected")
        await s.connect(d.host, d.port)
        await refused(s.stf_upload(str(y), False, ANY_PLAN, target_of(s)), "confirm_required")
        await refused(s.stf_upload(str(y), None, ANY_PLAN, target_of(s)), "confirm_required")  # type: ignore[arg-type]
        prev = await s.stf_upload_preview(str(y))
        assert [f["exists"] for f in prev["files"]] == [False] and prev["yaml_exists"] is False
        gone = str(tmp_path / "nope.yaml")
        await refused(s.stf_upload(gone, True, ANY_PLAN, target_of(s)), "invalid_profile")
        await refused(s.stf_upload_preview("relative.yaml"), "invalid_profile")
        s._op = True
        await refused(s.stf_upload(str(y), True, ANY_PLAN, target_of(s)), "busy")
        s._op = False
        assert pushes(d) == [] and "push_file" not in d.methods()
        # a pcapng and an oversize pcap are refused with confirm given, before any push_file
        ng = make_profile(tmp_path / "ng", {"x.pcap": b"zz"})
        (tmp_path / "ng" / "x.pcap").write_bytes(bytes.fromhex("0a0d0d0a") + b"zz")
        big = make_profile(tmp_path / "big", {"x.pcap": b"\0" * stfyaml.PCAP_MAX_BYTES})
        for bad, what in ((ng, "pcapng"), (big, "larger than")):
            e = await refused(s.stf_upload(str(bad), True, ANY_PLAN, target_of(s)), "invalid_profile")
            assert what in e.message
        assert pushes(d) == [] and "push_file" not in d.methods()
    drive(body)


def test_upload_preview_describes_the_plan(fake_daemon: Any, tmp_path: Path) -> None:
    """The preview's rows, totals, rewritten YAML and profile path; `exists` turns true after an upload."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa", "sub/b.pcap": b"bbbb"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Preview, upload, preview."""
        await s.connect(d.host, d.port)
        prev = await s.stf_upload_preview(str(y))
        assert [f["template"] for f in prev["files"]] == ["a.pcap", "sub/b.pcap"]
        assert [f["bytes"] for f in prev["files"]] == [7, 8]
        assert [f["local"] for f in prev["files"]] == [str(tmp_path / "a.pcap"), str(tmp_path / "sub/b.pcap")]
        assert prev["profile"] == f"{FILES}/{prev['yaml_remote']}"
        assert prev["total_bytes"] == 15 + prev["yaml_bytes"] == 15 + len(prev["yaml"].encode())
        assert all(f"{FILES}/{f['remote']}" in prev["yaml"] for f in prev["files"])
        assert prev["warnings"] == []
        out = await upload(s, y)
        again = await s.stf_upload_preview(str(y))
        assert out["profile"] == prev["profile"]
        assert all(f["exists"] for f in again["files"]) and again["yaml_exists"] is True
    drive(body)


def test_upload_pushes_pcaps_then_yaml(fake_daemon: Any, tmp_path: Path) -> None:
    """The fake's log shows every pcap before the YAML, and each stored file equals the local one."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa", "b.pcap": b"bbbb", "c.pcap": b"cc"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Upload, then read the files back through the daemon."""
        await s.connect(d.host, d.port)
        out = await upload(s, y)
        names = pushes(d, len(out["pushed"]))
        assert names == out["pushed"] and out["skipped"] == []
        assert [bool(re.fullmatch(stfyaml.TG_PCAP_RE, n)) for n in names] == [True, True, True, False]
        assert re.fullmatch(stfyaml.TG_YAML_RE, names[-1]) and out["profile"] == f"{FILES}/{names[-1]}"
        client = DaemonClient(d.host, d.port)
        for local, remote in zip(("a.pcap", "b.pcap", "c.pcap"), names[:3], strict=True):
            stored_pcap = await asyncio.to_thread(client.get_file, f"{FILES}/{remote}")
            assert stored_pcap == (tmp_path / local).read_bytes()
        stored = (await asyncio.to_thread(client.get_file, out["profile"])).decode()
        _entry, errors, _w = stfyaml.parse(stored)
        assert errors == [] and all(f"{FILES}/{n}" in stored for n in names[:-1])
        prof = await s.stf_profile(out["profile"])
        assert prof["errors"] == [] and prof["templates"] == [f"{FILES}/{n}" for n in names[:-1]]
        assert not any(m.get("level") == "warning" for m in rec.msgs)
    drive(body)


def test_upload_skips_names_already_listed(fake_daemon: Any, tmp_path: Path) -> None:
    """A second upload pushes nothing and reports every name as skipped; a changed pcap pushes only itself
    and the YAML."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa", "b.pcap": b"bbbb"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Upload twice, change one pcap, upload again."""
        await s.connect(d.host, d.port)
        first = await upload(s, y)
        n = len(pushes(d, 3))
        second = await upload(s, y)
        assert len(pushes(d)) == n == 3
        assert second["pushed"] == [] and second["skipped"] == first["pushed"]
        (tmp_path / "b.pcap").write_bytes(MAGIC + b"changed")
        third = await upload(s, y)
        assert len(third["pushed"]) == 2 and len(third["skipped"]) == 1
        assert len(pushes(d, 5)) == 5 and third["profile"] != first["profile"]
    drive(body)


@pytest.mark.parametrize("pcap_name", ["../x.pcap", "a/b.pcap", ".hidden.pcap", "s" * 190 + ".pcap",
                                       "UPPER_and-dash.1.pcap"])
@pytest.mark.parametrize("yaml_name", ["p.yaml", "my profile (1).yaml", "t" * 120 + ".yaml"])
def test_upload_never_pushes_a_non_tg_name(fake_daemon: Any, tmp_path: Path, pcap_name: str,
                                           yaml_name: str) -> None:
    """Whatever the template and profile file names, every push_file name matches TG_PCAP_RE or TG_YAML_RE."""
    d = fake_daemon()
    root = tmp_path / "root" / "deep"
    y = make_profile(root, {pcap_name: b"zz"}, yaml_name)

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Upload and check the fake's log."""
        await s.connect(d.host, d.port)
        out = await upload(s, y)
        names = pushes(d, 2)
        assert len(names) == 2 and names == out["pushed"]
        assert re.fullmatch(stfyaml.TG_PCAP_RE, names[0]) and re.fullmatch(stfyaml.TG_YAML_RE, names[1])
    drive(body)


def test_upload_then_start_uses_the_absolute_profile(fake_daemon: Any, tmp_path: Path) -> None:
    """Start with the uploaded profile: start_trex's -f is <files_path>/tg-...yaml, and the run ends."""
    d = fake_daemon(*STF_FLAGS)
    y = make_profile(tmp_path, {"a.pcap": b"aaa"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Upload, Start, wait for the end."""
        await s.connect(d.host, d.port)
        up = await upload(s, y)
        out = await s.start("/etc/trex_cfg.yaml", {}, "", 20, True, StfOptions(up["profile"], 20, 1.0, 0, 0))
        assert re.search(rf" -f {re.escape(FILES)}/tg-[0-9a-f]{{12}}-p\.yaml ", out["command_preview"])
        assert out["run"]["profile"] == up["profile"]
        def ended() -> bool:
            """True once the last daemon message says the run ended."""
            return [m for m in rec.msgs if m["type"] == "daemon"][-1]["run"]["state"] == "ended"
        await wait_for(ended, 4)
    drive(body)


def test_push_false_is_a_fault(fake_daemon: Any, tmp_path: Path) -> None:
    """push_file answering false is DaemonFault -33 naming the remote, and stops the upload at the first
    push (no YAML)."""
    d = fake_daemon("--push-false")
    y = make_profile(tmp_path, {"a.pcap": b"aaa", "b.pcap": b"bbb"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """The upload raises; the slot is free again."""
        await s.connect(d.host, d.port)
        with pytest.raises(DaemonFault) as e:
            await upload(s, y)
        names = pushes(d, 1)
        assert e.value.code == -33 and names[0] in e.value.message and len(names) == 1
        assert s.snapshot()["busy"] is False
    drive(body)


def test_upload_refuses_a_different_daemon_than_the_confirm_named(fake_daemon: Any, tmp_path: Path) -> None:
    """The confirm named a daemon host:port; an upload while another target is connected is plan_changed and
    the fake logs no push_file."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """A wrong host, a wrong port, then the right target."""
        await s.connect(d.host, d.port)
        prev = await s.stf_upload_preview(str(y))
        for wrong in (f"10.9.9.9:{d.port}", f"{d.host}:{d.port + 1}", ""):
            e = await refused(s.stf_upload(str(y), True, prev["yaml_remote"], wrong), "plan_changed")
            assert f"{d.host}:{d.port}" in e.message
        assert pushes(d) == [] and "push_file" not in d.methods()
        assert len((await upload(s, y))["pushed"]) == 2
    drive(body)


def test_upload_refuses_a_plan_that_changed_since_the_preview(fake_daemon: Any, tmp_path: Path) -> None:
    """Preview, change one pcap on disk, upload expecting the old profile name: plan_changed, and the fake
    logs no push_file."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa", "b.pcap": b"bbb"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """The stale expectation is refused; a fresh preview is accepted."""
        await s.connect(d.host, d.port)
        old = (await s.stf_upload_preview(str(y)))["yaml_remote"]
        (tmp_path / "b.pcap").write_bytes(MAGIC + b"changed")
        e = await refused(s.stf_upload(str(y), True, old, target_of(s)), "plan_changed")
        assert old in e.message
        assert pushes(d) == [] and "push_file" not in d.methods()
        assert len((await upload(s, y))["pushed"]) == 3
    drive(body)


def swap_after_plan(monkeypatch: pytest.MonkeyPatch, change: Callable[[], None]) -> None:
    """Make stfyaml.plan_upload (as the session calls it) run `change()` right after it has planned."""
    real = stfyaml.plan_upload

    def planned(yaml_path: Path, files_path: str) -> stfyaml.UploadPlan:
        """The real plan, then the change."""
        plan = real(yaml_path, files_path)
        change()
        return plan
    monkeypatch.setattr(stfyaml, "plan_upload", planned)


@pytest.mark.parametrize("how", ["bytes", "grown"])
def test_upload_refuses_a_pcap_that_changes_after_the_plan(fake_daemon: Any, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    """A pcap whose bytes change between the plan and its own push is refused (its name no longer matches
    its content); one grown past PCAP_MAX_BYTES is refused by the bounded re-read. Nothing is pushed."""
    d = fake_daemon()
    y = make_profile(tmp_path, {"a.pcap": b"aaa", "b.pcap": b"bbb"})
    victim = tmp_path / "a.pcap"

    def change() -> None:
        """Rewrite or grow the first pcap."""
        victim.write_bytes(MAGIC + (b"x" if how == "bytes" else b"\0" * (stfyaml.PCAP_MAX_BYTES + 1)))

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Upload with the file swapped after the plan."""
        await s.connect(d.host, d.port)
        prev = await s.stf_upload_preview(str(y))
        swap_after_plan(monkeypatch, change)
        e = await refused(s.stf_upload(str(y), True, prev["yaml_remote"], target_of(s)), "invalid_profile")
        assert ("changed while" if how == "bytes" else "larger than") in e.message
        assert pushes(d) == [] and "push_file" not in d.methods()
        assert s.snapshot()["busy"] is False
    drive(body)


def test_a_pushed_name_missing_from_the_listing_is_a_fault(fake_daemon: Any, tmp_path: Path) -> None:
    """When get_files_list does not show what was pushed, the upload is DaemonFault -33 naming the file."""
    d = fake_daemon("--list-hides-pushed")
    y = make_profile(tmp_path, {"a.pcap": b"aaa"})

    async def body(s: DaemonSession, rec: Recorder) -> None:
        """Upload; the pushes happened, the verification fails."""
        await s.connect(d.host, d.port)
        with pytest.raises(DaemonFault) as e:
            await upload(s, y)
        assert e.value.code == -33 and "not in the daemon's file list" in e.value.message
        assert len(pushes(d, 2)) == 2 and e.value.message.startswith(pushes(d)[0])
    drive(body)
