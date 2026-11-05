"""The profile worker's `emu_load` op (P7b Task 10): an EMU .py profile run in the contained worker."""
import asyncio
import json
import time
from pathlib import Path

import pytest
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.trexapi import profiles

P = FIXTURES / "profiles"
EMU = P / "emu_two_ns.py"


def run(coro):
    """Runs the coroutine to completion on a fresh event loop and returns its result."""
    return asyncio.run(coro)


def load(path, tunables=(), **kw):
    """load_emu_profile(path, tunables) run to completion."""
    return run(profiles.load_emu_profile(str(path), list(tunables), **kw))


def wire_of(profile):
    """The requests Task 11 sends for a loaded profile's data, in the order of the vendored
    EMUClient._start_profile: the default namespace plugins, every namespace, then for each namespace its
    default client plugins and its clients."""
    out = [{"method": "ctx_set_def_plugins", "params": {"def_plugs": profile["def_ns_plugs"]}},
           {"method": "ctx_add", "params": {"tunnels": [ns["fields"] for ns in profile["namespaces"]]}}]
    for ns in profile["namespaces"]:
        out.append({"method": "ctx_client_set_def_plugins",
                    "params": {"def_plugs": ns["def_c_plugs"], "tun": ns["key"]}})
        out.append({"method": "ctx_client_add", "params": {"clients": ns["clients"], "tun": ns["key"]}})
    return out


def test_fixture_loads_with_defaults_and_reports_counts():
    lp = load(EMU)
    assert (lp.namespaces, lp.clients) == (2, 6)
    assert lp.plugins == ["arp", "icmp"] and lp.vports == [0, 1]
    assert [t["dest"] for t in lp.tunables] == ["clients", "vports"]
    assert [t["default"] for t in lp.tunables] == [3, 2]


def test_tunables_change_the_profile():
    lp = load(EMU, ["--clients", "5", "--vports", "3"])
    assert (lp.namespaces, lp.clients, lp.vports) == (3, 15, [0, 1, 2])


def test_worker_output_equals_the_recorded_wire_data():
    """Review M12: what the worker returns for emu_two_ns.py at its defaults is the recorded wire form."""
    want = json.loads((FIXTURES / "p7" / "profile_wire.json").read_text())["requests"]
    assert wire_of(load(EMU).profile) == want


def test_stl_load_is_unchanged_beside_emu_load():
    lp = run(trexapi.load_profile(str(P / "udp_for_benchmarks.py"), [0], []))
    assert lp.stream_count >= 1 and any(t["dest"] == "packet_len" for t in lp.tunables)


@pytest.mark.parametrize("tunables", [None, "x", [1], {"a": 1}])
def test_a_forged_result_without_proper_tunables_is_a_profile_error(monkeypatch, tunables):
    """A profile can write a result line to the result fd; load_emu_profile checks `tunables` too."""
    async def forged(*a, **kw):
        """A worker result that is ok but whose tunables are wrong (or missing for None)."""
        res = {"ok": True, "profile": {"def_ns_plugs": {}, "namespaces": []}}
        if tunables is not None:
            res["tunables"] = tunables
        return res
    monkeypatch.setattr(profiles, "_run_worker", forged)
    with pytest.raises(trexapi.ProfileError, match="tunables are not a list of objects"):
        load(EMU)


def test_help_tunable_is_refused():
    want = "the profile asked for --help; remove it from the tunables"
    with pytest.raises(trexapi.ProfileError, match=want):
        load(EMU, ["--help"])


def test_bad_tunable_is_reported():
    with pytest.raises(trexapi.ProfileError, match=r"bad tunables: .*--clients"):
        load(EMU, ["--clients", "many"])


def test_only_py_and_existing_files_load(tmp_path):
    with pytest.raises(trexapi.ProfileError, match="only .py EMU profiles"):
        load(P / "two_streams.json")
    with pytest.raises(trexapi.ProfileError, match="does not exist"):
        load(tmp_path / "nope.py")


def test_hanging_profile_times_out():
    t = time.monotonic()
    with pytest.raises(trexapi.ProfileError, match=r"did not finish within 2 s"):
        load(P / "hang.py", timeout_s=2)
    assert time.monotonic() - t < 4


def test_raising_profile_reports_traceback():
    with pytest.raises(trexapi.ProfileError, match="boom-from-profile"):
        load(P / "boom.py")


def test_memory_hog_is_stopped_by_the_address_space_limit(monkeypatch):
    monkeypatch.setattr(profiles, "WORKER_AS_LIMIT", 1024**3)
    with pytest.raises(trexapi.ProfileError, match="ran out of memory"):
        load(P / "allocates.py")


def test_over_cap_result_is_rejected_without_hanging(monkeypatch):
    monkeypatch.setattr(profiles, "MAX_RESULT", 32 * 1024 * 1024)
    t = time.monotonic()
    with pytest.raises(trexapi.ProfileError, match="more than"):
        load(P / "floods_result_fd.py", timeout_s=3)
    assert time.monotonic() - t < 6


def _gone(pid: int) -> bool:
    """True once the pid is gone or a zombie, waiting up to 2 s."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                return True
        except FileNotFoundError:
            return True
        time.sleep(0.05)
    return False


def test_normal_finish_kills_leftover_children(tmp_path):
    prof = tmp_path / "spawns_child.py"                   # copied so the pid file lands in tmp_path
    prof.write_text((P / "spawns_child.py").read_text())
    lp = load(prof, timeout_s=3)
    assert (lp.namespaces, lp.clients) == (0, 0)
    assert _gone(int((tmp_path / "child.pid").read_text()))


def test_timeout_kills_a_forked_child_holding_the_pipe(tmp_path):
    prof = tmp_path / "forks_then_hangs.py"
    prof.write_text((P / "forks_then_hangs.py").read_text())
    with pytest.raises(trexapi.ProfileError, match="did not finish within 2 s"):
        load(prof, timeout_s=2)
    assert _gone(int((tmp_path / "child.pid").read_text()))


def test_ten_thousand_clients_load_within_the_timeout():
    """10000 clients (5000 in each of two namespaces, 10000 in total) load well inside EMU_LOAD_TIMEOUT_S
    (the measured time is recorded beside the constant in profiles.py)."""
    t = time.monotonic()
    lp = load(EMU, ["--clients", "5000", "--vports", "2"], timeout_s=profiles.EMU_LOAD_TIMEOUT_S)
    took = time.monotonic() - t
    assert lp.clients == 10000
    assert took < profiles.EMU_LOAD_TIMEOUT_S / 2, f"{took:.1f} s"
