"""backend/trex_web_gui/stfyaml.py: the STF profile rules. Offline, no I/O beyond tmp_path."""
import re
from pathlib import Path

import pytest
from trex_web_gui import stfyaml

SFR2 = """- duration : 10.0
  generator :
          distribution : "seq"
          clients_start : "16.0.0.1"
          clients_end   : "16.0.0.255"
          servers_start : "48.0.0.1"
          servers_end   : "48.0.0.255"
          clients_per_gb : 201
          min_clients    : 101
          dual_port_mask : "1.0.0.0"
          tcp_aging      : 1
          udp_aging      : 1
  cap_info :
     - name: cap2/Oracle.pcap
       cps : 1.0
       ipg : 10
       rtt : 10
       w   : 4
     - name: cap2/smtp.pcap
       cps : 1.2
       ipg : 10000
       rtt : 10000
       w   : 4
     - name: cap2/dns.pcap
       cps : 1.0
       ipg : 10000
       rtt : 10000
       w   : 4
"""
OLD = ["cap2/Oracle.pcap", "cap2/smtp.pcap", "cap2/dns.pcap"]


def cap(n: int) -> str:
    """A profile with n valid templates."""
    items = "".join(f"     - {{name: a{i}.pcap, cps: 1, ipg: 1, rtt: 1, w: 1}}\n" for i in range(n))
    return f"- duration: 10\n  cap_info:\n{items}"


def test_parse_accepts_the_stock_shape() -> None:
    """The pinned cap2/sfr2.yaml gives duration 10.0 and its three names."""
    entry, errors, warnings = stfyaml.parse(SFR2)
    assert errors == [] and warnings == []
    assert entry is not None and entry["duration"] == 10.0
    assert stfyaml.templates(entry) == OLD


@pytest.mark.parametrize("text", [
    "duration: 10\n",
    "[]\n",
    "- cap_info: [{name: a, cps: 1, ipg: 1, rtt: 1, w: 1}]\n",
    "- duration: 10\n",
    "- duration: 10\n  cap_info: []\n",
    '- duration: 10\n  cap_info: [{name: \'a"b\', cps: 1, ipg: 1, rtt: 1, w: 1}]\n',
    "- duration: 10\n  cap_info: [{name: a, cps: 1, ipg: 1, rtt: 1}]\n",
    cap(65),
    "- duration: true\n  cap_info: [{name: a, cps: 1, ipg: 1, rtt: 1, w: 1}]\n",
    "- [1, 2]\n",
    "a: [unclosed\n",
])
def test_parse_refuses_bad_shapes(text: str) -> None:
    """Each bad shape gives at least one error."""
    assert stfyaml.parse(text)[1]


def test_parse_is_safe(tmp_path: Path) -> None:
    """A Python tag is an error and runs nothing."""
    marker = tmp_path / "x"
    _entry, errors, _w = stfyaml.parse(f'!!python/object/apply:os.system ["touch {marker}"]\n')
    assert errors
    assert not marker.exists()


def test_parse_warns_on_extra_entries() -> None:
    """A second list element is a warning, not an error."""
    _entry, errors, warnings = stfyaml.parse(cap(1) + cap(1))
    assert errors == []
    assert warnings == ["TRex reads only the first entry"]


def test_parse_refuses_oversize() -> None:
    """Text over 65536 bytes is refused."""
    text = SFR2 + "#" * (stfyaml.STF_YAML_MAX_BYTES + 1 - len(SFR2))
    assert len(text) == 65537
    assert stfyaml.parse(text)[1]


def test_rewrite_replaces_every_name() -> None:
    """The output re-parses with exactly the new names; a name missing from the map is a ValueError."""
    new = {o: f"/tmp/trex_files/tg-0123456789ab-{i}.pcap" for i, o in enumerate(OLD)}
    entry, errors, _w = stfyaml.parse(stfyaml.rewrite(SFR2, new))
    assert errors == [] and entry is not None
    assert stfyaml.templates(entry) == list(new.values())
    with pytest.raises(ValueError):
        stfyaml.rewrite(SFR2, {OLD[0]: "x.pcap"})


def test_tg_name_is_content_addressed() -> None:
    """Equal bytes give equal names, one changed byte another; stems are made safe and bounded."""
    a = stfyaml.tg_name(b"abc", "x", "pcap")
    assert a == stfyaml.tg_name(b"abc", "x", "pcap")
    assert a != stfyaml.tg_name(b"abd", "x", "pcap")
    assert re.fullmatch(stfyaml.TG_PCAP_RE, stfyaml.tg_name(b"abc", "a b/c", "pcap"))
    assert "a_b_c" in stfyaml.tg_name(b"abc", "a b/c", "pcap")
    long = stfyaml.tg_name(b"abc", "s" * 100, "yaml")
    assert re.fullmatch(stfyaml.TG_YAML_RE, long)
    assert long.endswith("-" + "s" * 40 + ".yaml")
    empty = stfyaml.tg_name(b"abc", "", "pcap")
    assert empty.endswith("-_.pcap") and re.fullmatch(stfyaml.TG_PCAP_RE, empty)


@pytest.mark.parametrize("files_path", ["", "tmp/trex_files"])
def test_uploaded_profile_rejects_empty_or_relative_files_path(files_path: str) -> None:
    """An empty files_path would make `/tg-*.yaml` at the root look uploaded."""
    assert stfyaml.uploaded_profile_ok("/tg-0123456789ab-x.yaml", files_path) is False


@pytest.mark.parametrize("path,ok", [
    ("cap2/sfr2.yaml", True), ("avl/x.yaml", True),
    ("cap2/../x.yaml", False), ("/etc/x.yaml", False), ("stl/x.yaml", False), ("cap2/x.yml", False),
    ("/tmp/trex_files/tg-0123456789ab-x.yaml", False),
])
def test_stock_profile_path_rules(path: str, ok: bool) -> None:
    """Only cap2/ and avl/ yaml files are stock profiles."""
    assert stfyaml.stock_profile_ok(path) is ok


@pytest.mark.parametrize("args,field", [
    ((10, 1.0, 0, 0), None), ((3600, 1000.0, 100000, 65535), None),
    ((9, 1.0, 0, 0), "d"), ((3601, 1.0, 0, 0), "d"), ((10.5, 1.0, 0, 0), "d"),
    ((10, 0, 0, 0), "m"), ((10, 1000.5, 0, 0), "m"),
    ((10, 1.0, -1, 0), "l"), ((10, 1.0, 100001, 0), "l"),
    ((10, 1.0, 0, -1), "rx_check"), ((10, 1.0, 0, 65536), "rx_check"),
])
def test_check_stf_bounds(args: tuple, field: str | None) -> None:
    """The edges pass; one past each raises a ValueError naming the field."""
    if field is None:
        stfyaml.check_stf_bounds(*args)
        return
    with pytest.raises(ValueError, match=rf"^{field} "):
        stfyaml.check_stf_bounds(*args)


@pytest.mark.parametrize("path,ok", [
    ("/tmp/trex_files/tg-0123456789ab-x.yaml", True),
    ("/tmp/trex_files/x.yaml", False),
    ("/tmp/trex_files/tg-0123456789ab-x.yaml/..", False),
    ("/tmp/trex_files/sub/tg-0123456789ab-x.yaml", False),
    ("/etc/tg-0123456789ab-x.yaml", False),
    ("/tmp/trex_files/tg-0123456789ab-x.pcap", False),
    ("tg-0123456789ab-x.yaml", False),
])
def test_uploaded_profile_path_rules(path: str, ok: bool) -> None:
    """Only a tg-*.yaml directly inside files_path is an uploaded profile."""
    assert stfyaml.uploaded_profile_ok(path, "/tmp/trex_files") is ok


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
@pytest.mark.parametrize("key", ["cps", "ipg", "rtt", "w"])
def test_parse_refuses_non_finite_numbers(key: str, value: str) -> None:
    """A template number of NaN or infinity is an error, never accepted as 'a number' (ruling R16)."""
    text = re.sub(rf"(\n\s+{key}\s*:\s*)\S+", rf"\g<1>{value}", SFR2, count=1)
    _entry, errors, _warnings = stfyaml.parse(text)
    assert errors == [f"cap_info[0].{key} must be a finite number"]


# -- rewrite hardening (ruling R16) ---------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "- duration: 10\n  cap_info:\n    - 5\n",
    "- duration: 10\n  cap_info: [[1]]\n",
    "- duration: 10\n  cap_info: x\n",
    "- duration: 10\n  cap_info:\n    - &n {name: a.pcap, cps: 1, ipg: 1, rtt: 1, w: 1}\n    - *n\n",
    "a: [unclosed\n",
    SFR2 + "#" * stfyaml.STF_YAML_MAX_BYTES,
], ids=["scalar-entry", "list-entry", "cap-not-list", "alias", "bad-yaml", "oversize"])
def test_rewrite_refuses_what_parse_would_not_pass(text: str) -> None:
    """A non-mapping entry, an alias, broken YAML and text over the size cap are ValueErrors, not a
    traceback and not a rewrite."""
    with pytest.raises(ValueError):
        stfyaml.rewrite(text, {"a.pcap": "/x/tg-0123456789ab-a.pcap"})


# -- plan_upload (spec §13.7) -----------------------------------------------------------------------------

PCAP_HEAD = bytes.fromhex("d4c3b2a1")
FILES = "/tmp/trex_files"


def pcap(path: Path, body: bytes = b"", magic: bytes = PCAP_HEAD) -> Path:
    """Write a file that starts with `magic` and carries `body`; creates parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic + body)
    return path


def profile(path: Path, names: list[str], extra: str = "") -> Path:
    """Write a one-entry profile naming `names`."""
    items = "".join(f"    - {{name: {n}, cps: 1, ipg: 1, rtt: 1, w: 1}}\n" for n in names)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{extra}- duration: 10\n  cap_info:\n{items}")
    return path


def test_plan_upload_resolves_names_against_the_yaml_dir_then_its_parent(tmp_path: Path) -> None:
    """A relative name is tried in the YAML's dir first, then one level up; an absolute one is used as is."""
    pcap(tmp_path / "prof" / "here.pcap", b"here")
    pcap(tmp_path / "up.pcap", b"up")
    pcap(tmp_path / "prof" / "dup.pcap", b"inner")
    pcap(tmp_path / "dup.pcap", b"outer")
    far = pcap(tmp_path / "far" / "abs.pcap", b"abs")
    y = profile(tmp_path / "prof" / "p.yaml", ["here.pcap", "up.pcap", "dup.pcap", str(far)])
    plan = stfyaml.plan_upload(y, FILES)
    assert {f.template: f.local.resolve() for f in plan.pcaps} == {
        "here.pcap": (tmp_path / "prof" / "here.pcap").resolve(),
        "up.pcap": (tmp_path / "up.pcap").resolve(),
        "dup.pcap": (tmp_path / "prof" / "dup.pcap").resolve(),
        str(far): far.resolve()}
    assert [f.size for f in plan.pcaps] == [8, 6, 9, 7]


def test_plan_upload_refuses_pcapng_and_oversize(tmp_path: Path) -> None:
    """pcapng (named as such), a pcap one byte over PCAP_MAX_BYTES and a file that is no pcap are refused."""
    for kind, magic, body, match in [
        ("ng", bytes.fromhex("0a0d0d0a"), b"x", r"pcapng.*editcap -F pcap"),
        ("big", PCAP_HEAD, b"\0" * (stfyaml.PCAP_MAX_BYTES - 3), r"larger than"),
        ("text", b"hell", b"o", r"not a pcap"),
        ("short", b"ab", b"", r"not a pcap"),
    ]:
        pcap(tmp_path / kind / "x.pcap", body, magic)
        y = profile(tmp_path / kind / "p.yaml", ["x.pcap"])
        with pytest.raises(ValueError, match=match):
            stfyaml.plan_upload(y, FILES)
    pcap(tmp_path / "ok" / "x.pcap", b"\0" * (stfyaml.PCAP_MAX_BYTES - 4))
    assert stfyaml.plan_upload(profile(tmp_path / "ok" / "p.yaml", ["x.pcap"]), FILES).pcaps[0].size \
        == stfyaml.PCAP_MAX_BYTES


@pytest.mark.parametrize("magic", ["a1b2c3d4", "d4c3b2a1", "a1b23c4d", "4d3cb2a1"])
def test_plan_upload_accepts_the_four_classic_magics(tmp_path: Path, magic: str) -> None:
    """Each magic TRex's pcap reader takes is accepted."""
    pcap(tmp_path / "x.pcap", b"", bytes.fromhex(magic))
    assert len(stfyaml.plan_upload(profile(tmp_path / "p.yaml", ["x.pcap"]), FILES).pcaps) == 1


def test_plan_upload_refuses_too_many_or_too_big(tmp_path: Path) -> None:
    """65 pcaps, and 33 pcaps of 1 MiB (33 MiB in all), are refused; 31 of them pass."""
    names = [f"n{i}.pcap" for i in range(65)]
    for n in names:
        pcap(tmp_path / "many" / n, n.encode())
    with pytest.raises(ValueError, match=r"cap_info must be a list of 1\.\.64 templates"):
        stfyaml.plan_upload(profile(tmp_path / "many" / "p.yaml", names), FILES)
    mib = b"\0" * (1024 * 1024 - 4)      # with the 4-byte magic, 1 MiB
    names = [f"m{i}.pcap" for i in range(33)]
    for n in names:
        pcap(tmp_path / "big" / n, mib)
    with pytest.raises(ValueError, match=r"and its pcaps are \d+ bytes; the most is"):
        stfyaml.plan_upload(profile(tmp_path / "big" / "p.yaml", names), FILES)
    ok = names[:31]
    plan = stfyaml.plan_upload(profile(tmp_path / "big" / "q.yaml", ok), FILES)
    assert plan.total_bytes <= stfyaml.UPLOAD_MAX_BYTES


def test_plan_upload_names_are_content_addressed_and_absolute(tmp_path: Path) -> None:
    """Every rewritten name is <files_path>/tg-...pcap; the plan is the same twice; equal content under two
    names is one file; the profile is named tg-...yaml from its rewritten text."""
    pcap(tmp_path / "a.pcap", b"one")
    pcap(tmp_path / "b.pcap", b"two")
    y = profile(tmp_path / "my prof.yaml", ["a.pcap", "b.pcap", "a.pcap"], extra="# note\n")
    plan, again = stfyaml.plan_upload(y, FILES), stfyaml.plan_upload(y, FILES)
    assert plan == again
    entry, errors, _w = stfyaml.parse(plan.yaml_text)
    assert errors == [] and entry is not None
    names = stfyaml.templates(entry)
    assert len(names) == 3 and names[0] == names[2]
    for n, f in zip(names, [plan.pcaps[0], plan.pcaps[1], plan.pcaps[0]], strict=True):
        assert n == f"{FILES}/{f.remote}"
        assert re.fullmatch(stfyaml.TG_PCAP_RE, f.remote)
    assert [f.template for f in plan.pcaps] == ["a.pcap", "b.pcap"]
    assert re.fullmatch(stfyaml.TG_YAML_RE, plan.yaml_remote) and "my_prof" in plan.yaml_remote
    assert plan.yaml_remote == stfyaml.tg_name(plan.yaml_text.encode(), "my prof", "yaml")
    assert plan.total_bytes == 7 + 7 + len(plan.yaml_text.encode())
    assert plan.warnings == ["comments are not kept"]
    assert stfyaml.plan_upload(profile(tmp_path / "q.yaml", ["a.pcap"]), FILES + "/").pcaps[0].remote \
        == plan.pcaps[0].remote


def test_plan_upload_refuses_a_missing_pcap(tmp_path: Path) -> None:
    """The message names the template and where it looked."""
    y = profile(tmp_path / "p.yaml", ["gone.pcap"])
    with pytest.raises(ValueError, match=r"template 'gone.pcap'.*no such file"):
        stfyaml.plan_upload(y, FILES)


def test_plan_upload_refuses_a_bad_profile(tmp_path: Path) -> None:
    """A relative path, a missing file, a directory, an oversize profile, a non-profile and a relative
    files_path are ValueErrors."""
    pcap(tmp_path / "x.pcap")
    good = profile(tmp_path / "p.yaml", ["x.pcap"])
    with pytest.raises(ValueError, match="absolute"):
        stfyaml.plan_upload(Path("p.yaml"), FILES)
    with pytest.raises(ValueError, match="cannot be read"):
        stfyaml.plan_upload(tmp_path / "nope.yaml", FILES)
    with pytest.raises(ValueError, match="not a regular file"):
        stfyaml.plan_upload(tmp_path, FILES)
    big = tmp_path / "big.yaml"
    big.write_text(good.read_text() + "#" * stfyaml.STF_YAML_MAX_BYTES)
    with pytest.raises(ValueError, match="larger than"):
        stfyaml.plan_upload(big, FILES)
    bad = tmp_path / "bad.yaml"
    bad.write_text("duration: 10\n")
    with pytest.raises(ValueError, match="bad.yaml"):
        stfyaml.plan_upload(bad, FILES)
    with pytest.raises(ValueError, match="absolute"):
        stfyaml.plan_upload(good, "tmp/trex_files")
