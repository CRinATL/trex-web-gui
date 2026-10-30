"""The P5 recording (tests/fixtures/p5, vm/record_p5.py): PUB frames of the snapshot TRex (idle, traffic), the
one stateful attempt (a recording, or the documented samples when the attempt failed),
and the daemon exchanges.
Offline: every test reads the files only."""
import base64
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

P5 = Path(__file__).resolve().parent.parent / "fixtures" / "p5"
MAGIC = 0xABE85CEA
METADATA_TYPES = {"NUMBER", "FLOAT", "STRING", "BOOLEAN", "LIST", "OBJECT", "IP", "MAC"}


def body_of(frame_b64: str) -> bytes:
    """The JSON bytes of one recorded frame: unzipped when it carries TRex's zip header, else as is."""
    raw = base64.b64decode(frame_b64)
    if len(raw) >= 8 and struct.unpack(">I", raw[:4])[0] == MAGIC:
        return zlib.decompress(raw[8:])
    return raw


def frames(name: str) -> list[dict[str, Any]]:
    """The JSON lines of one frame file, each with its decoded `msg` (a dict, or {} for a trigger)."""
    rows = [json.loads(line) for line in (P5 / name).read_text().splitlines() if line.strip()]
    for r in rows:
        r["msg"] = json.loads(body_of(r["frame"]))
    return rows


def summary() -> dict[str, Any]:
    """summary.json."""
    return json.loads((P5 / "summary.json").read_text())


def stateful_rows() -> list[dict[str, Any]]:
    """The stateful frames: the recording, or the samples file when `stateful_source` says so."""
    recorded = summary()["stateful_source"] == "recording"
    return frames("pub-stateful.jsonl" if recorded else "pub-stateful-samples.jsonl")


def names(rows: list[dict[str, Any]]) -> set[str]:
    """The distinct `name` values of the object frames."""
    return {r["msg"]["name"] for r in rows if r["msg"].get("name")}


def daemon_rows() -> list[dict[str, Any]]:
    """daemon.jsonl."""
    return [json.loads(line) for line in (P5 / "daemon.jsonl").read_text().splitlines() if line.strip()]


def calls(method: str) -> list[dict[str, Any]]:
    """The logged calls of one method, in order."""
    return [r for r in daemon_rows() if r["method"] == method]


def test_stl_frames_hold_trex_global_and_triggers() -> None:
    rows = frames("pub-stl.jsonl")
    assert {r["phase"] for r in rows} == {"idle", "traffic"}
    assert "trex-global" in names(rows)
    assert any(r["msg"] == {} for r in rows), "the {} triggers are kept"
    assert all(isinstance(r["t"], (int, float)) for r in rows)


def test_stl_traffic_raises_tx_pps() -> None:
    rows = frames("pub-stl.jsonl")
    top = max(r["msg"]["data"]["m_tx_pps"] for r in rows
              if r["phase"] == "traffic" and r["msg"].get("name") == "trex-global")
    assert top >= 9000


def test_stateful_names() -> None:
    got = names(stateful_rows())
    assert {"trex-global", "template_info", "trex-latecny", "trex-latecny-v2"} <= got
    if summary()["stateful_source"] == "recording":
        assert {"tx-gen", "rx-check"} <= got


def test_template_names_come_from_sfr2_yaml() -> None:
    """trex_stf.cpp:147-165; published only for more than one template (bp_sim.h:2962), hence sfr2.yaml."""
    assert summary()["template_names"] == ["cap2/Oracle.pcap", "cap2/smtp.pcap", "cap2/dns.pcap"]


def test_latency_histogram_keys_follow_the_decade_scheme() -> None:
    allowed = {d * 10 ** k for k in range(1, 12) for d in range(1, 10)}
    seen = 0
    for r in stateful_rows():
        if r["msg"].get("name") != "trex-latecny-v2":
            continue
        for port, entry in r["msg"]["data"].items():
            if port.startswith("port-"):
                for bucket in entry["hist"]["histogram"]:
                    assert bucket["key"] in allowed, bucket
                    seen += 1
    assert seen > 0


def test_template_tx_comes_from_trex_global() -> None:
    latest = [r["msg"] for r in stateful_rows() if r["msg"].get("name") == "trex-global"][-1]
    template = latest["data"]["template"]
    assert isinstance(template, list)
    assert len(template) >= len(summary()["template_names"])


def test_metadata_types_are_the_ones_the_editor_knows() -> None:
    meta = calls("get_trex_config_metadata")[0]["body"]["result"]
    found: set[str] = set()

    def walk(node: Any) -> None:
        """Collect every `type` string in the metadata tree."""
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                found.add(node["type"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(meta)
    assert found == METADATA_TYPES


def test_fault_shapes() -> None:
    f = summary()["faults"]
    assert f["get_file"]["error"]["code"] == -33
    assert f["stop_wrong_seq_idle"]["result"] is False
    assert f["stop_wrong_seq_running"]["error"]["code"] == -33
    assert f["start_twice"]["error"]["code"] == -13
    assert f["start_bad_cfg"]["error"]["code"] == -11
    assert isinstance(f["unknown_method"]["error"]["code"], int)
    assert "foreign_start" in f


def test_foreign_trex_is_listed() -> None:
    cmds = calls("get_trex_cmds")[0]["body"]["result"]
    assert len(cmds) == 1 and len(cmds[0]) == 2
    pid, cmd = cmds[0]
    assert isinstance(pid, str) and pid.isdigit()
    assert "_t-rex-64" in cmd


def test_started_trex_answers_rpc() -> None:
    assert summary()["started_version"] == "v3.08"


@pytest.mark.parametrize("method",
                         ["get_trex_config", "get_trex_log", "get_trex_version", "get_trex_daemon_log"])
def test_base64_payloads_decode(method: str) -> None:
    result = calls(method)[0]["body"]["result"]
    assert base64.b64decode(result).decode()


def test_stateful_file_starts_with_the_stateful_trex() -> None:
    """The first stateful frame is not left over from the STL TRex that was transmitting 10 kpps."""
    first = stateful_rows()[0]
    assert first["t"] == 0
    msg = first["msg"]
    if msg.get("name") == "trex-global":
        assert msg["data"]["m_tx_pps"] < 1000
