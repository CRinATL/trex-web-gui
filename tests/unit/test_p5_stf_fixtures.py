"""The P5 STF recording (tests/fixtures/p5-stf, vm/record_p5_stf.py): a daemon-started `-f` run, the file
listings and the upload gate's measurements. Offline: every test reads the files only. The gate's verdict is
the controller's ruling (plan Task 14 Step 7), so no test asserts that it passed."""
import json
import re
from pathlib import Path
from typing import Any

P5_STF = Path(__file__).resolve().parent.parent / "fixtures" / "p5-stf"
IDLE, RUNNING = 1, 3


def summary() -> dict[str, Any]:
    """summary.json."""
    return json.loads((P5_STF / "summary.json").read_text())


def daemon_lines() -> list[dict[str, Any]]:
    """The JSON lines of daemon.jsonl."""
    return [json.loads(ln) for ln in (P5_STF / "daemon.jsonl").read_text().splitlines() if ln.strip()]


def test_stock_listing_holds_sfr2() -> None:
    """get_files_list("cap2") is [dirs, files] with sfr2.yaml among the files."""
    body = summary()["listings"]["cap2"]
    assert "sfr2.yaml" in body["result"][1]


def test_avl_listing_is_a_list_or_a_fault() -> None:
    """avl is recorded as whichever it is; Task 9's stock listing follows it."""
    body = summary()["listings"]["avl"]
    if "result" in body:
        assert isinstance(body["result"], list) and len(body["result"]) == 2
    else:
        assert isinstance(body["error"]["code"], int)


def test_refused_path_is_a_fault() -> None:
    """/etc is outside the daemon's two roots: -33."""
    assert summary()["listings"]["etc"]["error"]["code"] == -33


def test_stock_run_reached_running_then_idle() -> None:
    """The timeline has Running and then Idle, the run lasted 20..60 s, and TRex finished."""
    s = summary()
    states = [t["state"] for t in s["timeline"]]
    assert RUNNING in states
    assert IDLE in states[states.index(RUNNING):]
    assert 20 <= s["run_s"] <= 60
    assert s["outcomes"]["finished"] == "TRex finished."


def test_stf_cmd_is_stf() -> None:
    """The _t-rex-64 command line has -f and no -i."""
    tokens = summary()["stf_cmd"][1].split()
    assert "-f" in tokens and "-i" not in tokens


def test_full_command_has_the_stf_form() -> None:
    """trex_server.py:528-553 builds exactly this command for the recorded start_trex."""
    full = re.sub(r" +", " ", summary()["full_command_stf"]).strip()
    assert full == ("nice -n -19 ./t-rex-64 --iom 0 --cfg /etc/trex_cfg.yaml -f cap2/sfr2.yaml -d 20 -m 1.0 "
                    "-l 1000 --rx-check 100 -c 1 --no-key")


def test_fixture_carries_no_pcap_bytes() -> None:
    """No daemon.jsonl line is over 128 KiB; every pcap push is elided or small, the 4 MiB one elided."""
    text = (P5_STF / "daemon.jsonl").read_text()
    assert all(len(ln) <= 128 * 1024 for ln in text.splitlines())
    pushes = [r for r in daemon_lines()
              if r["method"] == "push_file" and r["params"]["filename"].endswith(".pcap")]
    assert pushes
    for r in pushes:   # the elision rule is "over 64 KiB": a smaller pcap (dns.pcap, 199 bytes) stays whole
        data = r["params"]["bin_data"]
        assert (isinstance(data, dict) and data["elided"] is True) or len(data) <= 65536
    sizes = [d.get("bytes", 0) for d in (r["params"]["bin_data"] for r in pushes) if isinstance(d, dict)]
    assert max(sizes) >= 4 * 1024 * 1024


def test_stopped_run() -> None:
    """stop_trex on a Running STF run answered true and the daemon was Idle within 10 s."""
    s = summary()
    assert s["stop_result"]["result"] is True
    assert s["stop_s"] <= 10


def test_start_without_d_is_an_error() -> None:
    """A start with no d is refused with a JSON-RPC error."""
    assert isinstance(summary()["start_stf_no_d"]["error"]["code"], int)


def test_gate_is_recorded() -> None:
    """Every gate key is present and typed. No pass assertion: the verdict is the controller's."""
    g = summary()["gate"]
    assert isinstance(g["g1_round_trip"], bool)
    for d in g["g1_detail"].values():
        assert isinstance(d["pushed"], bool) and isinstance(d["sha_ok"], bool)
    assert g["g2_push_4m_s"] is None or isinstance(g["g2_push_4m_s"], float)
    assert isinstance(g["g3_listed"], bool)
    g4 = g["g4"]
    assert set(g4) == {"seq", "templates", "outcome", "run_s"}
    assert g4["seq"] is None or isinstance(g4["seq"], int)
    assert isinstance(g4["templates"], list)
    assert "g5_relative" in g
