"""Profile worker (P2 Task 3; plan §4.3: .py runs in a subprocess with a timeout)."""
import asyncio
import os
import time
from pathlib import Path

import pytest
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.trexapi import profiles

P = FIXTURES / "profiles"


def run(coro):
    """Runs the coroutine to completion on a fresh event loop and returns its result."""
    return asyncio.run(coro)


def test_a_worker_that_exits_without_a_result_is_named_by_its_label(tmp_path):
    (tmp_path / "exits.py").write_text("import os\nos._exit(3)\n")
    want = r"^exits\.py: the loader exited without a result \(exit code 3\)"
    with pytest.raises(trexapi.ProfileError, match=want):
        run(trexapi.load_profile(str(tmp_path / "exits.py"), [0], []))


def test_round_trip_keeps_the_stream_count_line_past_the_cap():
    before = [{"isg": i} for i in range(profiles.ROUND_TRIP_DIFFS + 5)]
    after = [{"isg": i + 0.5} for i in range(profiles.ROUND_TRIP_DIFFS + 4)]
    out = profiles._differences(before, after)
    assert len(out) == profiles.ROUND_TRIP_DIFFS + 1
    assert out[-1] == f"streams: {len(before)} -> {len(after)}"
    assert out[0] == "stream 0 isg: 0 -> 0.5"


def test_py_tunables_and_summary():
    lp = run(trexapi.load_profile(str(P / "udp_for_benchmarks.py"), [0, 1], ["--packet_len", "128"]))
    assert lp.kind == "py"
    assert [(t["dest"], t["kind"], t["default"]) for t in lp.tunables] == [("packet_len", "int", 64),
                                                                            ("stream_count", "int", 1)]
    assert sorted(lp.streams_by_port) == [0, 1]
    assert lp.summary[0]["length"] == 124                  # measured 2026-10-16: 128 - 4 (FCS)
    assert lp.summary[0]["mode"] == "continuous" and "UDP" in lp.summary[0]["packet"]


def test_py_without_tunables():
    lp = run(trexapi.load_profile(str(P / "udp_1pkt_simple.py"), [0], []))
    assert lp.tunables == [] and len(lp.summary) == 1


def test_legacy_gui_yaml():
    lp = run(trexapi.load_profile(str(P / "gui_example.yaml"), [0], []))
    assert lp.kind == "yaml" and lp.summary[0]["rate"] == "4000 pps"


def test_json_profile():
    path = P / "two_streams.json"
    lp = run(trexapi.load_profile(str(path), [0], []))
    assert lp.kind == "json" and len(lp.summary) == 2


def test_streams_from_json_roundtrip():
    lp = run(trexapi.load_profile(str(P / "udp_for_benchmarks.py"), [0], []))
    streams = trexapi.streams_from_json(lp.streams_by_port[0])
    assert [s.to_json() for s in streams] == lp.streams_by_port[0]


def test_hanging_profile_times_out():
    t = time.monotonic()
    with pytest.raises(trexapi.ProfileError, match=r"did not finish within 2 s"):
        run(trexapi.load_profile(str(P / "hang.py"), [0], [], timeout_s=2))
    assert time.monotonic() - t < 4


def test_raising_profile_reports_traceback():
    with pytest.raises(trexapi.ProfileError, match="boom-from-profile"):
        run(trexapi.load_profile(str(P / "boom.py"), [0], []))


def test_noisy_profile_output_is_capped():
    lp = run(trexapi.load_profile(str(P / "noisy.py"), [0], []))
    assert len(lp.summary) == 1                      # stdout noise neither breaks parsing nor floods memory


def test_summary_is_capped_but_counts_every_stream():
    lp = run(trexapi.load_profile(str(P / "udp_for_benchmarks.py"), [0], ["--stream_count", "600"]))
    assert lp.stream_count == 600 and len(lp.streams_by_port[0]) == 600
    assert len(lp.summary) == 500                       # SUMMARY_CAP: Scapy dissection is 0.45 ms/stream


def test_bad_tunable_value_is_reported():
    with pytest.raises(trexapi.ProfileError, match="packet_len"):
        run(trexapi.load_profile(str(P / "udp_for_benchmarks.py"), [0], ["--packet_len", "big"]))


def test_help_tunable_is_a_profile_error_not_a_crash():
    # argparse --help exits 0; load_py turns that SystemExit into None (trex_stl_streams.py:1179-1181)
    with pytest.raises(trexapi.ProfileError, match="the profile asked for --help"):
        run(trexapi.load_profile(str(P / "udp_for_benchmarks.py"), [0], ["--help"]))


def test_unsupported_suffix_and_missing_file():
    with pytest.raises(trexapi.ProfileError, match="pcap"):
        run(trexapi.load_profile("/x/y.pcap", [0], []))
    with pytest.raises(trexapi.ProfileError, match="does not exist"):
        run(trexapi.load_profile(str(P / "nope.py"), [0], []))


def test_tilde_path_loads(tmp_path, monkeypatch):
    (tmp_path / "x.json").write_text((P / "two_streams.json").read_text())
    monkeypatch.setenv("HOME", str(tmp_path))
    lp = run(trexapi.load_profile("~/x.json", [0], []))
    assert lp.kind == "json" and len(lp.summary) == 2


def _pid_alive(pid: int) -> bool:
    """True when the pid exists and is not a zombie (signal 0; another user's pid raises PermissionError)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"


def test_normal_finish_kills_leftover_children(tmp_path):
    prof = tmp_path / "spawns_child.py"                   # copied so the pid file lands in tmp_path
    prof.write_text((P / "spawns_child.py").read_text())
    t = time.monotonic()
    lp = run(trexapi.load_profile(str(prof), [0], [], timeout_s=2))  # the child outlives the profile's return
    assert time.monotonic() - t < 4 and len(lp.summary) == 1
    pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 2
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid)                            # killed with the group after a normal finish too


def test_profile_writing_to_fd1_cannot_corrupt_result():
    lp = run(trexapi.load_profile(str(P / "writes_fd1.py"), [0], []))
    assert len(lp.summary) == 1


def test_profile_output_appears_in_error():
    with pytest.raises(trexapi.ProfileError, match="Profile output") as e:
        run(trexapi.load_profile(str(P / "prints_then_fails.py"), [0], []))
    assert "diagnostic-from-profile" in str(e.value)


def test_over_cap_result_is_rejected_without_hanging(monkeypatch):
    # Measured 2026-10-16: with a 1 MB or 5 MB cap the hang does not reproduce (the reader drains to EOF by
    # luck); at 32 MiB the old unbounded proc.wait() hangs every time, so the cap stays at the production
    # size. Pinned here so the test keeps reproducing the hang even if the production MAX_RESULT changes.
    monkeypatch.setattr(trexapi.profiles, "MAX_RESULT", 32 * 1024 * 1024)
    t = time.monotonic()
    with pytest.raises(trexapi.ProfileError, match="more than"):
        run(trexapi.load_profile(str(P / "floods_result_fd.py"), [0], [], timeout_s=3))
    assert time.monotonic() - t < 6


def test_timeout_kills_a_forked_child_holding_the_pipe(tmp_path):
    prof = tmp_path / "forks_then_hangs.py"
    prof.write_text((P / "forks_then_hangs.py").read_text())
    t = time.monotonic()
    with pytest.raises(trexapi.ProfileError, match="did not finish within 2 s"):
        run(trexapi.load_profile(str(prof), [0], [], timeout_s=2))
    assert time.monotonic() - t < 4
    pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 2
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_alive(pid)
