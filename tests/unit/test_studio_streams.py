"""The stream schema, the cross-stream rules and the edit diff (P4a Task 2)."""
import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from support import FIXTURES, ROOT
from trex_web_gui.studio.streams import (
    StreamError,
    StreamsDiff,
    apply_diff,
    check_profile,
    free_name,
    parse_streams,
)

GOLDEN = ROOT / "tests/golden/stl"


def recorded() -> dict[str, Any]:
    """Stream 0 of the recorded two_streams.json."""
    return json.loads((FIXTURES / "profiles/two_streams.json").read_text())[0]


def mk(name: Any = None, nxt: Any = None, burst: bool = False, **over: Any) -> dict[str, Any]:
    """A valid stream; `name`, `nxt` (a burst mode) and `over` are set when given."""
    s = recorded()
    if name is not None:
        s["name"] = name
    if burst:
        s["mode"] = {"type": "single_burst", "total_pkts": 5, "rate": {"type": "pps", "value": 10.0}}
    if nxt is not None:
        s["next"] = nxt
    s.update(over)
    return s


def diff(**kw: Any) -> StreamsDiff:
    """A StreamsDiff with a fixed rev."""
    return StreamsDiff(rev="r", **kw)


def b64(n: int) -> str:
    """Base64 of n zero bytes."""
    import base64
    return base64.b64encode(bytes(n)).decode()


def test_a_recorded_stream_parses_unchanged() -> None:
    """A recorded stream comes back equal and distinct."""
    s = recorded()
    out = parse_streams([s])
    assert out == [s] and out[0] is not s and out[0]["mode"] is not s["mode"]


def with_(*path: Any, value: Any) -> dict[str, Any]:
    """The recorded stream with the value at `path` replaced (a missing last key is added)."""
    s = recorded()
    cur = s
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value
    return s


def burst(**mode: Any) -> dict[str, Any]:
    """The recorded stream with a mode of the given keys."""
    return with_("mode", value={"rate": {"type": "pps", "value": 1.0}, **mode})


LIMIT_ROWS: list[tuple[str, dict[str, Any]]] = [
    ("packet", with_("packet", "binary", value=b64(13))),
    ("packet", with_("packet", "binary", value=b64(9239))),
    ("flags", with_("flags", value=16)),
    ("flags", with_("flags", value=6)),
    ("action_count", with_("action_count", value=2**32)),
    ("core_id", with_("core_id", value=-2)),
    ("core_id", with_("core_id", value=256)),
    ("mode.rate", with_("mode", "rate", value={"type": "percentage", "value": 0})),
    ("mode.rate", with_("mode", "rate", value={"type": "percentage", "value": 100.5})),
    ("total_pkts", burst(type="single_burst", total_pkts=0)),
    ("vm.cache", with_("vm", "cache", value=0)),
    ("vm.cache", with_("vm", "cache", value=65536)),
    ("bogus", with_("bogus", value=1)),
    ("name", with_("name", value="")),
    ("name", with_("name", value=0)),
    ("flags", with_("flags", value="3")),
    ("enabled", with_("enabled", value=1)),
    ("name", with_("name", value=True)),
    ("name", with_("name", value=1.0)),
    ("core_id", with_("core_id", value=True)),
    ("ibg", burst(type="multi_burst", pkts_per_burst=1, ibg=-1.0, count=1)),
    ("mode", burst(type="single_burst")),
    ("random_seed", with_("random_seed", value=2**32)),
    ("isg", with_("isg", value=-1.0)),
]


@pytest.mark.parametrize(("field", "stream"), LIMIT_ROWS)
def test_limits_refused(field: str, stream: dict[str, Any]) -> None:
    """Each limit row is refused with an error naming its field and the stream index."""
    with pytest.raises(StreamError) as e:
        parse_streams([recorded(), stream])
    assert field in e.value.field and e.value.index == 1
    assert str(e.value).startswith("stream 1 ")


def test_limit_edges_pass() -> None:
    """The edge values just inside each limit pass."""
    rows = [with_("packet", "binary", value=b64(14)), with_("packet", "binary", value=b64(9238)),
            with_("flags", value=15 & ~6 | 4), with_("action_count", value=2**32 - 1),
            with_("core_id", value=255), with_("random_seed", value=2**32 - 1),
            with_("mode", "rate", value={"type": "percentage", "value": 100}),
            with_("vm", "cache", value=65535), with_("name", value=7), with_("name", value="x" * 80),
            burst(type="multi_burst", pkts_per_burst=1, ibg=0, count=1), with_("isg", value=5)]
    assert parse_streams(rows) == rows


def test_latency_with_a_core_id_is_refused() -> None:
    """A latency or tpg rule beside a core id is refused naming core_id; core_id -1 passes."""
    fs = {"enabled": True, "rule_type": "latency", "stream_id": 1, "vxlan": False, "ieee_1588": False}
    for rule in ("latency", "tpg"):
        with pytest.raises(StreamError, match="core_id"):
            parse_streams([with_("flow_stats", value={**fs, "rule_type": rule}) | {"core_id": 0}])
    parse_streams([with_("flow_stats", value=fs)])


CRAFTED = [("name", mk("x'); __import__('os').system('id'); ('")),
           ("name", mk("a\nb")),
           ("name", mk("é")),
           ("vm.instructions[0].name", with_("vm", "instructions",
                                             value=[{"type": "write_flow_var", "name": "a'b"}])),
           ("vm.instructions[0].op", with_("vm", "instructions",
                                    value=[{"type": "flow_var", "op": "inc\n"}])),
           ("vm.split_by_var", with_("vm", "split_by_var", value="a'b")),
           ("vm.instructions[0].l4_type", with_("vm", "instructions",
                                                value=[{"type": "fix_checksum_hw", "l4_type": "x; y"}]))]


@pytest.mark.parametrize(("field", "stream"), CRAFTED)
def test_crafted_name_and_vm_text_refused(field: str, stream: dict[str, Any]) -> None:
    """Text that could break out of a quoted .py is refused naming its field."""
    with pytest.raises(StreamError) as e:
        parse_streams([stream])
    assert e.value.field == field


def test_vm_values_numbers_lists_and_safe_text_pass() -> None:
    """Numbers, bools, null, number lists and safe words pass in instructions; a nested object does not."""
    ok = [{"type": "flow_var", "name": "a.b-c_1", "value_list": [1, 2.5], "next_var": None, "split": True}]
    parse_streams([with_("vm", "instructions", value=ok), with_("vm", "split_by_var", value="")])
    with pytest.raises(StreamError, match="instructions"):
        parse_streams([with_("vm", "instructions", value=[{"type": "x", "v": {"a": 1}}])])


def test_names_unique_by_value_and_type() -> None:
    """`1` and `"1"` may coexist; two `"a"` may not."""
    check_profile([mk(1), mk("1")])
    with pytest.raises(StreamError, match="name"):
        check_profile([mk("a"), mk("b"), mk("a")])


def test_next_must_resolve_and_not_on_continuous() -> None:
    """A dangling next and a next on a continuous stream are refused; a chain by int name resolves."""
    check_profile([mk("a", "b", burst=True), mk("b")])
    check_profile([mk(1, 2, burst=True), mk(2, 1, burst=True)])
    with pytest.raises(StreamError, match="names no stream"):
        check_profile([mk("a", "zz", burst=True)])
    with pytest.raises(StreamError, match="names no stream"):
        check_profile([mk("a", "1", burst=True), mk(1)])
    with pytest.raises(StreamError, match="continuous"):
        check_profile([mk("a", "b"), mk("b")])


def test_patch_merges_only_its_fields() -> None:
    """An enabled-only patch leaves the stored name even when the caller saw an older one."""
    streams = [mk("new"), mk("other")]
    r = apply_diff(streams, [{}, {}], diff(changed={"0": {"enabled": False}}))
    assert r.streams[0]["enabled"] is False and r.streams[0]["name"] == "new"
    assert streams[0]["enabled"] is True


def test_patch_null_deletes_an_optional_key() -> None:
    """null removes random_seed and next; a nested object is replaced whole."""
    streams = [mk("a", "b", burst=True, random_seed=5), mk("b")]
    r = apply_diff(streams, [], diff(changed={"0": {"random_seed": None, "next": None,
                                                    "vm": {"instructions": []}}}))
    assert "random_seed" not in r.streams[0] and "next" not in r.streams[0]
    assert r.cleared == []
    with pytest.raises(StreamError, match="stream 0"):
        apply_diff(streams, [], diff(changed={"0": {"isg": None}}))


def test_rename_rewrites_next_everywhere_including_int_names() -> None:
    """Streams 1 -> 2 -> 1 and a rename of 1 to "first": both next values follow."""
    streams = [mk(1, 2, burst=True), mk(2, 1, burst=True)]
    r = apply_diff(streams, [], diff(changed={"0": {"name": "first"}}))
    assert [s["name"] for s in r.streams] == ["first", 2]
    assert r.streams[0]["next"] == 2 and r.streams[1]["next"] == "first"
    swap = apply_diff([mk("a", "b", burst=True), mk("b", "a", burst=True)], [],
                      diff(changed={"0": {"name": "b"}, "1": {"name": "a"}}))
    assert [(s["name"], s["next"]) for s in swap.streams] == [("b", "a"), ("a", "b")]


def test_remove_clears_dangling_next_and_reports_it() -> None:
    """Removing S1 from S0 -> S1 -> S2 leaves S0 without next and reports it."""
    streams = [mk("S0", "S1", burst=True), mk("S1", "S2", burst=True), mk("S2")]
    r = apply_diff(streams, [], diff(removed=[1]))
    assert [s["name"] for s in r.streams] == ["S0", "S2"]
    assert "next" not in r.streams[0] and r.cleared == ["S0"]


def test_a_next_the_caller_wrote_to_a_missing_name_is_refused() -> None:
    """Ruling Task 2 M3 (revised again): a `next` set by a patch that names no stream in the result is refused
    at its new index; a removal in the same diff does not make it a clear."""
    streams = [mk("a", burst=True), mk("b", burst=True)]
    with pytest.raises(StreamError, match="names no stream") as e:
        apply_diff(streams, [], diff(changed={"1": {"next": "zz"}}))
    assert (e.value.index, e.value.field) == (1, "next")
    with pytest.raises(StreamError) as e:
        apply_diff(streams, [], diff(changed={"1": {"next": "a"}}, removed=[0]))
    assert (e.value.index, e.value.field) == (0, "next")           # b is stream 0 once a is gone
    ok = apply_diff(streams, [], diff(changed={"1": {"next": "a"}}, added=[mk("c", "b", burst=True)]))
    assert ok.streams[1]["next"] == "a" and ok.streams[2]["next"] == "b" and ok.cleared == []


def test_an_added_stream_with_a_dangling_next_is_cleared_and_reported() -> None:
    """Ruling Task 2 M3 (revised again): a pasted stream's `next` that names nothing is cleared and reported,
    not refused: the user copied that `next`, they did not write it."""
    streams = [mk("a", burst=True), mk("b", burst=True)]
    r = apply_diff(streams, [], diff(added=[mk("c", "zz", burst=True)], order=["+0", 0, 1]))
    assert [s["name"] for s in r.streams] == ["c", "a", "b"]
    assert "next" not in r.streams[0] and r.cleared == ["c"]
    parse_streams(r.streams)


def test_deleting_a_name_clears_the_next_that_named_it() -> None:
    """a -> b with b's name deleted: a loses its next (no null is written) and is reported."""
    r = apply_diff([mk("a", "b", burst=True), mk("b")], [], diff(changed={"1": {"name": None}}))
    assert "next" not in r.streams[0] and r.cleared == ["a"]
    assert "name" not in r.streams[1]
    parse_streams(r.streams)


def test_order_with_added_tokens_and_sidecar_follows() -> None:
    """order=[2, "+0", 0] with removed=[1]: streams and the sidecar are permuted alike; added gets {}."""
    streams = [mk("a"), mk("b"), mk("c")]
    side = [{"i": 0}, {"i": 1}, {"i": 2}]
    r = apply_diff(streams, side, diff(removed=[1], added=[mk("n")], order=[2, "+0", 0]))
    assert [s["name"] for s in r.streams] == ["c", "n", "a"]
    assert r.sidecar_streams == [{"i": 2}, {}, {"i": 0}]
    kept = apply_diff(streams, side, diff(removed=[0], added=[mk("n")]))
    assert [s["name"] for s in kept.streams] == ["b", "c", "n"]


def test_short_sidecar_list_is_padded() -> None:
    """A two-entry sidecar list for three streams is padded; a long one is trimmed."""
    streams = [mk("a"), mk("b"), mk("c")]
    padded = apply_diff(streams, [{"i": 0}, {"i": 1}], diff(order=[2, 1, 0]))
    assert padded.sidecar_streams == [{}, {"i": 1}, {"i": 0}]
    assert apply_diff(streams, [{}] * 5 + [{"x": 1}], diff()).sidecar_streams == [{}, {}, {}]


def editor_entry(tag: str = "x", pad: int = 0) -> dict[str, Any]:
    """A valid sidecar entry whose caption is `tag` plus `pad` filler characters."""
    doc = {"fileType": "trex-packet-editor", "version": "1.0.0", "metadata": {"caption": tag + "z" * pad},
           "packet": [{"id": "Ether", "fields": []}], "fePrarameters": {}, "feInstructions": []}
    return {"editor": {"doc": doc, "binary": b64(14), "vm": {"instructions": []}}}


def test_sidecar_entry_replaced_and_follows_order() -> None:
    """A `sidecar` entry replaces the entry at its old index; entries then move with their streams."""
    streams = [mk("a"), mk("b"), mk("c")]
    res = apply_diff(streams, [{}, {}, {}], diff(sidecar={"1": editor_entry("one")}, order=[2, 1, 0]))
    assert res.sidecar_streams == [{}, editor_entry("one"), {}]
    res2 = apply_diff(streams, [editor_entry("zero"), {}, {}], diff(sidecar={"0": {}}, removed=[2]))
    assert res2.sidecar_streams == [{}, {}]
    with pytest.raises(StreamError, match="also removed"):
        apply_diff(streams, [{}] * 3, diff(sidecar={"2": {}}, removed=[2]))


def test_added_sidecar_parallel_to_added() -> None:
    """added_sidecar[k] is the entry of added[k], also after the reorder."""
    streams = [mk("a")]
    res = apply_diff(streams, [{}], diff(added=[mk("p"), mk("q")], added_sidecar=[editor_entry("p"), {}],
                                         order=["+1", 0, "+0"]))
    assert res.sidecar_streams == [{}, {}, editor_entry("p")]
    assert apply_diff(streams, [{}], diff(added=[mk("p")])).sidecar_streams == [{}, {}]


def test_bad_editor_doc_refused() -> None:
    """An entry whose doc does not parse, or with the wrong shape, is a StreamError naming the field."""
    bad = editor_entry()
    bad["editor"]["doc"]["packet"] = "no"
    with pytest.raises(StreamError) as e:
        apply_diff([mk("a")], [{}], diff(sidecar={"0": bad}))
    assert e.value.index == 0 and e.value.field == "sidecar"
    for entry in ({"other": 1}, {"editor": {"doc": {}}}, {"editor": []}):
        with pytest.raises(StreamError, match="sidecar"):
            apply_diff([mk("a")], [{}], diff(sidecar={"0": entry}))
    with pytest.raises(StreamError, match=r"added_sidecar\[0\]"):
        apply_diff([], [], diff(added=[mk("p")], added_sidecar=[bad]))


def test_added_sidecar_length_mismatch_refused() -> None:
    """A different number of entries than added streams is refused."""
    with pytest.raises(StreamError) as e:
        apply_diff([], [], diff(added=[mk("p")], added_sidecar=[{}, {}]))
    assert e.value.field == "added_sidecar" and e.value.index is None


def test_sidecar_entry_over_cap_refused() -> None:
    """An entry of more than 256 KiB of compact JSON is refused; one just under is kept."""
    from trex_web_gui.studio.streams import SIDECAR_ENTRY_MAX
    base = len(json.dumps(editor_entry(), separators=(",", ":")).encode())
    ok = editor_entry(pad=SIDECAR_ENTRY_MAX - base)
    assert len(json.dumps(ok, separators=(",", ":")).encode()) == SIDECAR_ENTRY_MAX
    apply_diff([mk("a")], [{}], diff(sidecar={"0": ok}))
    with pytest.raises(StreamError, match="over 256 KiB") as e:
        apply_diff([mk("a")], [{}], diff(sidecar={"0": editor_entry(pad=SIDECAR_ENTRY_MAX - base + 1)}))
    assert e.value.index == 0 and e.value.field == "sidecar"


def test_added_collisions_are_renamed_and_their_chain_follows() -> None:
    """Pasting a -> b into a profile holding a gives a_2 -> b, and no new name takes an added one."""
    r = apply_diff([mk("a")], [{}], diff(added=[mk("a", "b", burst=True), mk("b")]))
    assert [(s["name"], s.get("next")) for s in r.streams] == [("a", None), ("a_2", "b"), ("b", None)]
    assert r.renamed == {"a": "a_2"}
    both = apply_diff([mk("a"), mk("b")], [], diff(added=[mk("a", "b", burst=True), mk("b", "a", burst=True),
                                                         mk("a_2")]))
    assert [s["name"] for s in both.streams] == ["a", "b", "a_3", "b_2", "a_2"]
    assert both.streams[2]["next"] == "b_2" and both.streams[3]["next"] == "a_3"
    assert both.renamed == {"a": "a_3", "b": "b_2"}


def test_free_name_keeps_types_apart() -> None:
    """free_name takes the smallest free k >= 2, and a bool or int never stands for a text."""
    assert free_name("a", {(str, "a")}) == "a_2"
    assert free_name("a", {(str, "a_2"), (str, "a_3")}) == "a_4"
    assert free_name(1, {(int, 1), (bool, True)}) == "1_2"
    assert free_name("x", {(int, 2)}) == "x_2"


def test_bad_order_is_refused_and_input_untouched() -> None:
    """Out-of-range, repeated and non-permutation indices are refused naming the field; inputs stay equal."""
    streams = [mk("a"), mk("b")]
    side: list[dict[str, Any]] = [{"i": 0}]
    before = copy.deepcopy((streams, side))
    for d in (diff(order=[0, 0]), diff(order=[0]), diff(order=[0, 1, 2]), diff(order=[0, "+0"]),
              diff(order=[0, "x"]), diff(removed=[1], order=[0, 1]), diff(removed=[5]), diff(removed=[0, 0]),
              diff(changed={"9": {}}), diff(changed={"-1": {}}), diff(changed={"x": {}}),
              diff(changed={"1": {}, "01": {}}), diff(changed={"00": {}}),
              diff(removed=[0], changed={"0": {"enabled": False}})):
        with pytest.raises(StreamError) as e:
            apply_diff(streams, side, d)
        assert e.value.field in ("order", "removed", "changed")
    assert (streams, side) == before


def test_a_patch_that_breaks_a_rule_is_refused_with_its_index() -> None:
    """A merged stream failing the schema, a duplicate name or an added stream's bad field is refused."""
    streams = [mk("a"), mk("b")]
    with pytest.raises(StreamError) as e:
        apply_diff(streams, [], diff(changed={"1": {"flags": 99}}))
    assert e.value.index == 1 and e.value.field == "flags"
    with pytest.raises(StreamError, match="name"):
        apply_diff(streams, [], diff(changed={"1": {"name": "a"}}))
    with pytest.raises(StreamError, match=r"added\[0\]\.flags"):
        apply_diff(streams, [], diff(added=[mk("c", flags=99)]))


def test_streams_max() -> None:
    """25 001 added streams are refused naming the count, before any is validated."""
    with pytest.raises(StreamError, match="25001 streams"):
        apply_diff([], [], diff(added=[{}] * 25_001))
    with pytest.raises(StreamError, match="25001 streams"):
        parse_streams([{}] * 25_001)


def golden_snapshots() -> list[Path]:
    """The golden snapshot files."""
    return sorted(GOLDEN.glob("*.json")) if GOLDEN.is_dir() else []


def test_every_golden_profile_parses() -> None:
    """Every full golden snapshot passes parse_streams and check_profile (fable M6)."""
    files = golden_snapshots()
    if not files:
        pytest.skip("no golden snapshots under tests/golden/stl")
    checked = 0
    for f in files:
        data = json.loads(f.read_text())
        whole = isinstance(data, list)
        if not whole:  # a digest plus stream 0 only
            data = [data["first"]]
        try:
            parse_streams(data)
            if whole:
                check_profile(data)
        except StreamError as e:
            pytest.fail(f"{f.name}: {e}")
        checked += 1
    assert checked == len(files)


def test_studio_imports_no_trex_or_scapy() -> None:
    """studio/ and api/packet.py never import trex or scapy (only the packet worker does)."""
    pkg = ROOT / "backend/trex_web_gui"
    for f in [*(pkg / "studio").glob("*.py"), pkg / "api/packet.py"]:
        assert not re.search(r"^\s*(import|from)\s+(trex\b|scapy)", f.read_text(), re.M), f


def test_entry_size_is_checked_before_the_document_is_parsed() -> None:
    """An oversized entry whose document would not parse is refused for its size, not walked."""
    from trex_web_gui.studio.streams import SIDECAR_ENTRY_MAX
    big = editor_entry(pad=SIDECAR_ENTRY_MAX)
    big["editor"]["doc"]["packet"] = "no"
    with pytest.raises(StreamError, match="over 256 KiB"):
        apply_diff([mk("a")], [{}], diff(sidecar={"0": big}))


def test_lone_surrogate_in_an_entry_is_a_stream_error() -> None:
    """A string no UTF-8 can hold is refused, naming the field, rather than escaping as UnicodeEncodeError."""
    bad = editor_entry("\ud800")
    with pytest.raises(StreamError, match="not valid UTF-8") as e:
        apply_diff([mk("a")], [{}], diff(sidecar={"0": bad}))
    assert e.value.index == 0 and e.value.field == "sidecar"
