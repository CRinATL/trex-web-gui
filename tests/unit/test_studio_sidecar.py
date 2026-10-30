"""The profile sidecar (P4a Task 3)."""
import copy
import re

import pytest
from trex_web_gui.store.test_setups import _now
from trex_web_gui.studio.sidecar import SIDECAR_VERSION, SidecarError, Source, new_sidecar, parse_sidecar

SOURCE: Source = {"kind": "py", "path": "~/x/../p.py", "args": ["--a", "1"],
                  "tunables": {"values": {"a": 1}, "extra": {}}, "ports_differ": False,
                  "imported": "2026-10-23T10:00:00.000Z"}


def test_new_sidecar_round_trips_and_normalises_the_source_path() -> None:
    sc = new_sidecar("Lab A", SOURCE, 3)
    assert sc["version"] == SIDECAR_VERSION == 1
    assert sc["name"] == "Lab A" and sc["stream_count"] == 3 and sc["streams"] == [{}, {}, {}]
    assert sc["created"] == sc["updated"] and sc["pcap_wizard"] is None
    assert sc["source"] is not None
    assert sc["source"]["path"].startswith("/") and ".." not in sc["source"]["path"]
    assert parse_sidecar(copy.deepcopy(sc)) == sc
    assert new_sidecar("B", None, 0)["source"] is None


def test_unknown_major_version_is_refused_naming_it() -> None:
    sc = new_sidecar("A", None, 0)
    sc["version"] = 2
    with pytest.raises(SidecarError, match="2"):
        parse_sidecar(sc)


@pytest.mark.parametrize("bad", [None, [], {"version": 1}, {"version": True}])
def test_non_sidecars_are_refused(bad: object) -> None:
    with pytest.raises(SidecarError):
        parse_sidecar(bad)


def test_a_bad_source_or_list_is_refused() -> None:
    for key, value in (("source", {"kind": "exe"}), ("streams", [1]), ("stream_count", "3"), ("name", 5)):
        sc = new_sidecar("A", None, 1)
        sc[key] = value  # type: ignore[literal-required]
        with pytest.raises(SidecarError):
            parse_sidecar(sc)


def test_timestamps_match_the_setups_format() -> None:
    sc = new_sidecar("A", None, 0)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", sc["created"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", _now())
