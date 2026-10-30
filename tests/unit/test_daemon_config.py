"""The trex_cfg.yaml model (backend/trex_web_gui/daemon/config.py), against the recorded daemon metadata."""
import json
from pathlib import Path
from typing import Any

import yaml
from trex_web_gui.daemon import config

ROOT = Path(__file__).resolve().parent.parent.parent
GUEST = ROOT / "vm" / "guest" / "trex_cfg.yaml"


def metadata() -> list[dict[str, Any]]:
    """The recorded get_trex_config_metadata result."""
    for line in (ROOT / "tests/fixtures/p5/daemon.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["method"] == "get_trex_config_metadata":
            return row["body"]["result"]  # type: ignore[no-any-return]
    raise AssertionError("no metadata recorded")


def good() -> dict[str, Any]:
    """A valid two-port document."""
    return {"port_limit": 2, "version": 2, "interfaces": ["veth0", "veth1"],
            "port_info": [{"ip": "1.1.1.1", "default_gw": "2.2.2.2"},
                          {"ip": "2.2.2.2", "dest_mac": "00:11:22:33:44:55"}]}


def test_defaults_from_metadata() -> None:
    """Fields with a default start there, and a field with none is absent."""
    d = config.defaults(metadata())
    assert d["version"] == 2
    assert d["zmq_pub_port"] == 4500
    assert "interfaces" not in d and "port_limit" not in d


def test_defaults_leave_optional_sections_and_c_to_trex() -> None:
    """No optional OBJECT is synthesized and `c` is unset (F1, E0 on the guest 2026-10-26): a `platform` built
    from master_thread_id's default alone is refused (`key not found: rx_thread_id`), and `c: 4` asks for
    2 + 4 x pairs CPUs (`EAL: core 4 unavailable` on the 4-vCPU guest). TRex's own defaults then apply."""
    d = config.defaults(metadata())
    assert "platform" not in d and "memory" not in d and "tw" not in d
    assert "c" not in d


def test_defaults_keep_a_mandatory_objects_attributes() -> None:
    """A mandatory OBJECT still nests its attributes' defaults; only optional ones are left out."""
    x = {"id": "x", "type": "NUMBER", "default": 1}
    y = {"id": "y", "type": "NUMBER", "default": 2}
    meta = [{"id": "s", "type": "OBJECT", "mandatory": True, "attributes": [x]},
            {"id": "o", "type": "OBJECT", "mandatory": False, "attributes": [y]}]
    assert config.defaults(meta) == {"s": {"x": 1}}


def test_guest_config_round_trips() -> None:
    """The guest's trex_cfg.yaml through parse, validate and render gives the same document."""
    text = GUEST.read_text()
    values, errs = config.parse_yaml(text)
    assert errs == [] and values is not None
    assert config.validate(values, metadata()) == []
    out = config.render_yaml(values)
    assert yaml.safe_load(out) == yaml.safe_load(text)
    assert config.render_yaml(config.parse_yaml(out)[0] or {}) == out  # type: ignore[arg-type]


def test_validate_reports_paths() -> None:
    """Each problem names its dotted path: missing, wrong type, bad IP and MAC, mandatory_if_not_set."""
    assert config.validate(good(), metadata()) == []
    bad = good()
    del bad["port_limit"]
    bad["port_info"] = [{"ip": "1.1.1.1"}, {"ip": "x", "default_gw": "nope"}, {"dest_mac": "zz"}]
    bad["version"] = True
    bad["c"] = 1.5
    bad["memory"] = {"mbuf_64": "lots"}
    bad["tw"] = {"bucket_time_usec": 5}  # an int is a valid FLOAT
    paths = {e.path for e in config.validate(bad, metadata())}
    assert {"port_limit", "port_info.0.default_gw", "port_info.1.default_gw", "port_info.2.dest_mac",
            "version", "c", "memory.mbuf_64"} <= paths
    assert "tw.bucket_time_usec" not in paths
    short = good()
    short["interfaces"] = ["veth0"]
    assert [e.path for e in config.validate(short, metadata())] == ["interfaces"]


def test_parse_reports_the_line() -> None:
    """A YAML syntax error carries its 1-based line."""
    values, errs = config.parse_yaml("- port_limit: 2\n  version: [2\n  low_end: true\n")
    assert values is None and len(errs) == 1
    assert errs[0].line is not None and errs[0].line >= 2


def test_parse_refuses_two_documents_and_oversize() -> None:
    """Two documents, a non-list, two mappings and an over-size text are all refused."""
    assert config.parse_yaml("- a: 1\n---\n- b: 2\n")[0] is None
    assert config.parse_yaml("a: 1\n")[0] is None
    assert config.parse_yaml("- a: 1\n- b: 2\n")[0] is None
    big = "- a: " + "x" * config.MAX_YAML_BYTES + "\n"
    values, errs = config.parse_yaml(big)
    assert values is None and "larger" in errs[0].message


def test_unknown_keys_are_kept() -> None:
    """A key the metadata does not describe is no error and survives the round trip, in place."""
    doc = good()
    doc["future_option"] = {"x": [1, 2]}
    assert config.validate(doc, metadata()) == []
    back, _ = config.parse_yaml(config.render_yaml(doc))
    assert back == doc and list(back) == list(doc)  # type: ignore[arg-type]


def test_metadata_version_is_stable_md5() -> None:
    """The version is a 32-hex md5 independent of key order, and changes with the metadata."""
    md = metadata()
    v = config.metadata_version(md)
    assert len(v) == 32 and v == config.metadata_version([dict(reversed(list(m.items()))) for m in md])
    assert v != config.metadata_version(md[:-1])
