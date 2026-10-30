"""Worker operations and studio conversions (P4a Task 4). Pcaps are written with struct, so the test process
never imports Scapy."""
import asyncio
import base64
import json
import resource
import struct
from pathlib import Path
from typing import Any

import pytest
import yaml
from support import FIXTURES
from trex_web_gui import trexapi
from trex_web_gui.studio import convert
from trex_web_gui.studio.streams import parse_streams

P = FIXTURES / "profiles"
GIB = 1024**3
MIB = 1024 * 1024


def run(coro):
    """Runs the coroutine to completion on a fresh event loop and returns its result."""
    return asyncio.run(coro)


def udp_frame(src: int = 0x10000001, dst: int = 0x30000001, size: int = 60) -> bytes:
    """An Ethernet/IPv4/UDP frame of `size` bytes (checksums left zero), src and dst as integers."""
    payload = b"x" * (size - 42)
    ip = struct.pack("!BBHHHBBHII", 0x45, 0, 20 + 8 + len(payload), 1, 0, 64, 17, 0, src, dst)
    udp = struct.pack("!HHHH", 1025, 12, 8 + len(payload), 0)
    return b"\x00\x11\x22\x33\x44\x55" + b"\x00\x66\x77\x88\x99\xaa" + b"\x08\x00" + ip + udp + payload


def arp_frame() -> bytes:
    """An Ethernet/ARP request frame (no IPv4 header), padded to 60 bytes."""
    arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, b"\x00\x66\x77\x88\x99\xaa", b"\x10\x00\x00\x01",
                      b"\x00" * 6, b"\x30\x00\x00\x01")
    frame = b"\xff" * 6 + b"\x00\x66\x77\x88\x99\xaa" + b"\x08\x06" + arp
    return frame + b"\x00" * (60 - len(frame))


def write_pcap(path: Path, frames: list[bytes], gap_usec: int = 1000) -> Path:
    """A classic libpcap file (Ethernet link type) holding `frames`, `gap_usec` apart."""
    out = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    for i, f in enumerate(frames):
        t = 1_700_000_000 * 1_000_000 + i * gap_usec
        out.append(struct.pack("<IIII", t // 1_000_000, t % 1_000_000, len(f), len(f)) + f)
    path.write_bytes(b"".join(out))
    return path


def opts(**kw: Any) -> trexapi.PcapOptions:
    """PcapOptions with the defaults (timestamps, speedup 1, one loop, no prefix, no rewrite) and `kw`."""
    base: dict[str, Any] = {"ipg_usec": None, "speedup": 1.0, "loop_count": 1, "name_prefix": "",
                            "rewrite_src": None, "rewrite_dst": None}
    base.update(kw)
    return base  # type: ignore[return-value]


def test_legacy_yaml_imports():
    imp = run(convert.import_file(str(P / "gui_example.yaml"), "yaml", [], None))
    assert not imp.ports_differ and len(imp.streams) == 1
    s = imp.streams[0]
    assert s["name"] == "l1" and "next" not in s                # the {name, stream: {...}} form, no next
    assert s["flow_stats"] == {"enabled": True, "rule_type": "latency", "stream_id": 12, "vxlan": False,
                               "ieee_1588": False}                  # from_json's defaults filled in
    assert s["vm"]["instructions"] == []
    assert s["mode"]["rate"] == {"type": "pps", "value": 4000.0}


def test_py_with_tunables_imports_port_0():
    imp = run(convert.import_file(str(P / "udp_for_benchmarks.py"), "py", ["--packet_len", "128"], None))
    assert len(imp.streams) == 1 and imp.ports_differ is False
    assert len(base64.b64decode(imp.streams[0]["packet"]["binary"])) == 124


def test_yaml_export_round_trips(tmp_path):
    imp = run(convert.import_file(str(P / "udp_for_benchmarks.py"), "py", ["--stream_count", "3"], None))
    path = tmp_path / "p.yaml"
    path.write_text(convert.to_yaml(imp.streams))
    back = run(trexapi.load_profile(str(path), [0], []))
    assert back.streams_by_port[0] == imp.streams


def test_json_export_is_indented_json():
    imp = run(convert.import_file(str(P / "two_streams.json"), "json", [], None))
    text = convert.to_json_text(imp.streams)
    assert json.loads(text) == imp.streams and text.startswith("[\n  {")


@pytest.mark.parametrize("name", ["udp_for_benchmarks.py", "flow_latency.py"])
def test_python_export_reimports_equal(tmp_path, name):
    imp = run(convert.import_file(str(P / name), "py", [], None))
    code = run(trexapi.to_python(imp.streams))
    (tmp_path / "p.py").write_text(code)
    back = run(trexapi.load_profile(str(tmp_path / "p.py"), [0], []))
    assert back.streams_by_port[0] == imp.streams


def test_to_python_refuses_unsafe_text_before_the_worker(monkeypatch):
    stream = json.loads((P / "two_streams.json").read_text())[0]
    stream["name"] = 'a"b'

    async def no_worker(*a: Any, **k: Any) -> Any:
        """Fails the test: no subprocess may start."""
        raise AssertionError("a worker was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_worker)
    with pytest.raises(trexapi.ProfileError, match="stream 0 name"):
        run(trexapi.to_python([stream]))


CODE = "__import__('os').getpid()"


@pytest.mark.parametrize("path", ["action_count", "isg", "core_id", "random_seed", "flags", "stream_id",
                                  "enabled", "self_start", "start_paused", "vm.cache", "flow_stats.stream_id",
                                  "mode.rate.value"])
def test_to_python_refuses_code_in_an_unquoted_scalar_before_the_worker(monkeypatch, path):
    """`dump_to_code` writes these without quotes, so a string there is code in the .py (final review C1)."""
    stream = json.loads((P / "two_streams.json").read_text())[0]
    stream["flow_stats"] = {"enabled": True, "rule_type": "stats", "stream_id": 7}
    *parents, key = path.split(".")
    at = stream
    for p in parents:
        at = at[p]
    at[key] = CODE

    async def no_worker(*a: Any, **k: Any) -> Any:
        """Fails the test: no subprocess may start."""
        raise AssertionError("a worker was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_worker)
    with pytest.raises(trexapi.ProfileError, match=f"stream 0 {path}"):
        run(trexapi.to_python([stream]))


def test_pcap_isg_from_timestamps(tmp_path):
    path = write_pcap(tmp_path / "two.pcap", [udp_frame(), udp_frame()], gap_usec=1000)
    streams = run(trexapi.import_pcap(str(path), opts()))
    assert [s["isg"] for s in streams] == [0.0, 1000.0]


def test_pcap_fixed_ipg(tmp_path):
    path = write_pcap(tmp_path / "two.pcap", [udp_frame(), udp_frame()], gap_usec=1000)
    streams = run(trexapi.import_pcap(str(path), opts(ipg_usec=10.0)))
    assert streams[1]["isg"] == 10.0


def test_pcap_chain_and_loop(tmp_path):
    path = write_pcap(tmp_path / "two.pcap", [udp_frame(), udp_frame()])
    streams = run(trexapi.import_pcap(str(path), opts(loop_count=5)))
    assert [s["name"] for s in streams] == [1, 2] and [s["next"] for s in streams] == [2, 1]
    assert streams[-1]["action_count"] == 5 and streams[0]["action_count"] == 0


def test_pcap_rewrite_builds_the_vm(tmp_path):
    path = write_pcap(tmp_path / "two.pcap", [udp_frame(), udp_frame()])
    o = opts(rewrite_src={"start": "16.0.0.1", "count": 10}, rewrite_dst={"start": "48.0.0.1", "count": 1})
    streams = run(trexapi.import_pcap(str(path), o))
    ins = streams[0]["vm"]["instructions"]
    # The VM compiler puts every flow variable before the other instructions
    # (trex_stl_packet_builder_scapy.py:2006 "order_flow_vars"), so the two writes follow both variables.
    assert [i["type"] for i in ins] == ["flow_var", "flow_var", "write_flow_var", "write_flow_var",
                                        "fix_checksum_ipv4"]
    assert ins[0]["name"] == "ip_src" and (ins[0]["min_value"], ins[0]["max_value"]) == (268435457, 268435466)
    assert ins[1]["name"] == "ip_dst" and ins[1]["min_value"] == ins[1]["max_value"] == 805306369
    assert [(i["name"], i["pkt_offset"]) for i in ins[2:4]] == [("ip_src", 26), ("ip_dst", 30)]
    assert ins[4]["pkt_offset"] == 14
    assert streams[1]["vm"]["instructions"] == ins                     # every packet gets the same rewrite


def test_rewrite_over_a_non_ipv4_packet_names_it(tmp_path):
    path = write_pcap(tmp_path / "mixed.pcap", [udp_frame(), arp_frame()])
    with pytest.raises(trexapi.ProfileError, match="packet 2 has no IPv4 header"):
        run(trexapi.import_pcap(str(path), opts(rewrite_src={"start": "16.0.0.1", "count": 2})))
    assert len(run(trexapi.import_pcap(str(path), opts()))) == 2        # without a rewrite it imports


def test_pcap_prefix_renames_and_chains(tmp_path):
    path = write_pcap(tmp_path / "three.pcap", [udp_frame()] * 3)
    streams = run(trexapi.import_pcap(str(path), opts(name_prefix="cap ")))
    assert [s["name"] for s in streams] == ["cap 1", "cap 2", "cap 3"]
    assert [s["next"] for s in streams] == ["cap 2", "cap 3", "cap 1"]
    parse_streams(streams)


def test_pcap_over_streams_max_is_refused_before_building(tmp_path, monkeypatch):
    monkeypatch.setattr(trexapi.profiles, "STREAMS_MAX", 3)
    path = write_pcap(tmp_path / "four.pcap", [udp_frame()] * 4)
    with pytest.raises(trexapi.ProfileError, match="more than 3 packets"):
        run(trexapi.import_pcap(str(path), opts()))
    path3 = write_pcap(tmp_path / "three.pcap", [udp_frame()] * 3)
    assert len(run(trexapi.import_pcap(str(path3), opts()))) == 3


@pytest.mark.parametrize(("field", "kw"), [
    ("speedup", {"speedup": 0}),
    ("loop_count", {"loop_count": 65536}),
    ("rewrite_src", {"rewrite_src": {"start": "256.0.0.1", "count": 1}}),
    ("rewrite_dst", {"rewrite_dst": {"start": "255.255.255.255", "count": 2}}),
    ("name_prefix", {"name_prefix": 'a"'}),
    ("ipg_usec", {"ipg_usec": 0.0001}),
    ("ipg_usec", {"ipg_usec": 0.0015, "speedup": 2.0}),      # load_pcap's rule: ipg >= 0.001 * speedup
])
def test_pcap_options_refused(field, kw):
    with pytest.raises(ValueError, match=field):
        trexapi.check_pcap_options(opts(**kw))


def test_pcap_options_accepted():
    trexapi.check_pcap_options(opts())
    trexapi.check_pcap_options(opts(ipg_usec=0.001, loop_count=0, name_prefix="x" * 40,
                                    rewrite_src={"start": "255.255.255.255", "count": 1}))
    trexapi.check_pcap_options(opts(ipg_usec=0.002, speedup=2.0))


@pytest.fixture(scope="module")
def big_streams(tmp_path_factory) -> list[dict[str, Any]]:
    """A pcap import of 3000 frames of 9000 bytes: about 37 MiB of streams JSON."""
    path = write_pcap(tmp_path_factory.mktemp("big") / "big.pcap", [udp_frame(size=9000)] * 3000)
    return run(trexapi.import_pcap(str(path), opts()))


def test_pcap_result_between_32_and_96_mib_is_accepted(big_streams):
    size = len(json.dumps(big_streams))
    assert len(big_streams) == 3000 and 32 * MIB < size < 96 * MIB


def test_a_large_pcap_import_round_trips_and_exports(big_streams, tmp_path):
    assert run(trexapi.round_trip(big_streams)) == trexapi.RoundTrip(True, [])
    code = run(trexapi.to_python(big_streams))
    assert code.count("streams.append(stream)") == 3000


def test_load_profile_max_result_admits_a_large_library_profile(big_streams, tmp_path):
    path = tmp_path / "big.json"
    path.write_text(json.dumps(big_streams))
    with pytest.raises(trexapi.ProfileError, match="more than 32 MiB"):          # the default is unchanged
        run(trexapi.load_profile(str(path), [0], []))
    lp = run(trexapi.load_profile(str(path), [0], [], max_result=trexapi.MAX_RESULT_PCAP))
    assert lp.streams_by_port[0] == big_streams


FLOODS_40_MIB = """import os
import stat

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        \"\"\"floods_result_fd.py's pattern, bounded: 40 MiB to the first FIFO among fds 3..63.\"\"\"
        for fd in range(3, 64):
            if stat.S_ISFIFO(os.fstat(fd).st_mode):
                for _ in range(640):
                    os.write(fd, b"x" * 65536)
                return []
        return []


def register():
    \"\"\"The loader's entry point: the profile object.\"\"\"
    return P()
"""


def test_load_result_over_32_mib_still_refused(tmp_path):
    prof = tmp_path / "floods_40_mib.py"         # 40 MiB: inside the pcap cap, over the load cap
    prof.write_text(FLOODS_40_MIB)
    with pytest.raises(trexapi.ProfileError, match="more than 32 MiB"):
        run(trexapi.load_profile(str(prof), [0], [], timeout_s=5))


def test_allocating_profile_hits_the_memory_limit(monkeypatch):
    before = resource.getrlimit(resource.RLIMIT_AS)
    monkeypatch.setattr(trexapi.profiles, "WORKER_AS_LIMIT", 1 * GIB)
    with pytest.raises(trexapi.ProfileError, match=r"ran out of memory \(limit 1 GiB\)"):
        run(trexapi.load_profile(str(P / "allocates.py"), [0], []))
    lp = run(trexapi.load_profile(str(P / "udp_1pkt_simple.py"), [0], []))    # the baseline fits the limit
    assert lp.stream_count == 1
    assert resource.getrlimit(resource.RLIMIT_AS) == before                   # the test process is untouched


CATCHES_MEMORY = """from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        \"\"\"Catches the MemoryError of a 2 GiB allocation and raises its own error instead.\"\"\"
        try:
            bytearray(2 * 1024**3)
        except MemoryError:
            raise RuntimeError("own-error-after-memory")
        return []


def register():
    \"\"\"The loader's entry point: the profile object.\"\"\"
    return P()
"""


def test_an_error_raised_while_handling_memory_is_not_out_of_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(trexapi.profiles, "WORKER_AS_LIMIT", 1 * GIB)
    prof = tmp_path / "catches_memory.py"
    prof.write_text(CATCHES_MEMORY)
    with pytest.raises(trexapi.ProfileError, match="own-error-after-memory") as e:
        run(trexapi.load_profile(str(prof), [0], []))
    assert "out of memory" not in str(e.value)


def test_round_trip_reports_a_normalised_key():
    streams = json.loads((P / "two_streams.json").read_text())
    assert run(trexapi.round_trip(streams)) == trexapi.RoundTrip(True, [])
    del streams[1]["start_paused"]
    rt = run(trexapi.round_trip(streams))
    assert rt.equal is False and rt.differences == ["stream 1 start_paused: absent -> false"]


def test_template_is_60_bytes_and_parses():
    t = run(trexapi.template_stream())
    assert len(base64.b64decode(t["packet"]["binary"])) == 60 and "name" not in t
    assert t["mode"]["type"] == "continuous" and t["mode"]["rate"] == {"type": "pps", "value": 1}
    parse_streams([t])
    t["name"] = "changed"
    assert "name" not in run(trexapi.template_stream())                       # the cache hands out copies


def test_garbage_named_pcap_is_a_profile_error(tmp_path):
    path = tmp_path / "x.pcap"
    path.write_bytes(b"not a capture file at all" * 10)
    with pytest.raises(trexapi.ProfileError):
        run(convert.import_file(str(path), "pcap", [], opts()))


def test_kind_of_and_upload_suffixes():
    assert convert.kind_of("/a/B.YML") == "yaml" and convert.kind_of("x.cap") == "pcap"
    assert convert.UPLOAD_SUFFIXES == {".yaml", ".yml", ".json", ".pcap", ".cap"}
    with pytest.raises(convert.ConvertError):
        convert.kind_of("x.exe")


def test_to_yaml_is_the_stl_sim_form():
    streams = json.loads((P / "two_streams.json").read_text())
    assert yaml.safe_load(convert.to_yaml(streams)) == streams
    assert convert.to_yaml(streams).startswith("- ")
