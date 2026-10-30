"""User packet templates (P4b Task 4): the name rule, the store, the preference, and the Save-as-template
round trip through the engine. R9: IGMP-JOIN is still unbuildable."""
import os
from pathlib import Path
from typing import Any

import pytest
from test_packet_engine import with_engine
from trex_web_gui.store.settings import Preferences
from trex_web_gui.studio.packetdoc import empty_doc, model_of
from trex_web_gui.studio.templates import (
    TEMPLATE_NAME_MAX,
    TemplateExists,
    TemplateNameError,
    TemplateNotFound,
    TemplateStore,
    TemplateTooLarge,
    check_name,
    templates_dir,
)
from trex_web_gui.trexapi.packet import PacketError


def doc_with_payload(code: str = "3232323232") -> Any:
    """Ether/IP/UDP/Raw whose Raw load is the hex code pattern `code`."""
    doc = empty_doc()
    doc["packet"] = [{"id": "Ether", "fields": []}, {"id": "IP", "fields": []}, {"id": "UDP", "fields": []},
                     {"id": "Raw", "fields": [{"id": "load", "value": {
                         "vtype": "BYTES", "generate": "template_code", "template_code": code}}]}]
    return doc


def test_user_template_nested_name_round_trips(tmp_path: Path) -> None:
    store = TemplateStore(lambda: tmp_path / "t")
    assert store.list() == []
    doc = empty_doc()
    store.write("a/b/c", doc, overwrite=False)
    store.write("top", doc, overwrite=False)
    assert (tmp_path / "t" / "a" / "b" / "c.trp").is_file()
    assert store.read("a/b/c") == doc
    assert store.list() == ["a/b/c", "top"]
    assert [p.name for p in (tmp_path / "t").rglob(".*")] == []           # no temporary file left behind
    store.delete("a/b/c")
    with pytest.raises(TemplateNotFound):
        store.read("a/b/c")
    with pytest.raises(TemplateNotFound):
        store.delete("a/b/c")
    assert store.list() == ["top"]


@pytest.mark.parametrize("name", ["../x", "a/../../b", "/abs", "a//b", "/".join(["s"] * 9), "a\\b", "a\x00b",
                                  "", ".", "..", "a/./b", "x" * (TEMPLATE_NAME_MAX + 1), "a b", "é"])
def test_user_template_traversal_refused_before_io(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*a: object, **k: object) -> None:
        raise AssertionError("check_name did I/O")
    monkeypatch.setattr(os, "stat", fail)
    monkeypatch.setattr(os, "lstat", fail)
    monkeypatch.setattr(os.path, "realpath", fail)
    with pytest.raises(TemplateNameError):
        check_name(name)


def test_user_template_names_that_are_allowed() -> None:
    assert check_name("12/34/56/78") == ["12", "34", "56", "78"]
    assert check_name("/".join(["s"] * 8)) == ["s"] * 8
    assert check_name("v1.2_final-x") == ["v1.2_final-x"]


def test_user_template_symlink_out_refused(tmp_path: Path) -> None:
    root, outside = tmp_path / "t", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside)
    (root / "file.trp").symlink_to(outside / "elsewhere.trp")
    store = TemplateStore(lambda: root)
    for name in ("link/x", "file"):
        with pytest.raises(TemplateNameError):
            store.write(name, empty_doc(), overwrite=True)
        with pytest.raises(TemplateNameError):
            store.read(name)
    assert os.listdir(outside) == []
    assert store.list() == []


def test_overwrite_needs_the_flag(tmp_path: Path) -> None:
    store = TemplateStore(lambda: tmp_path)
    first, second = empty_doc(), empty_doc()
    second["metadata"]["caption"] = "second"
    store.write("x", first, overwrite=False)
    with pytest.raises(TemplateExists):
        store.write("x", second, overwrite=False)
    assert store.read("x") == first
    store.write("x", second, overwrite=True)
    assert store.read("x")["metadata"]["caption"] == "second"


def test_write_refuses_a_file_over_the_cap_by_its_stored_size(tmp_path: Path) -> None:
    store = TemplateStore(lambda: tmp_path)
    doc = empty_doc()
    doc["metadata"]["caption"] = "é" * (2 * 1024 * 1024 + 1)          # 2 bytes each as UTF-8: just over 4 MiB
    with pytest.raises(TemplateTooLarge):
        store.write("big", doc, overwrite=True)
    assert list(tmp_path.iterdir()) == []
    doc["metadata"]["caption"] = "é" * (2 * 1024 * 1024 - 1000)       # stored as UTF-8, not \\u escapes
    store.write("fits", doc, overwrite=True)
    assert store.read("fits")["metadata"]["caption"] == doc["metadata"]["caption"]


def test_templates_dir_preference_per_call(tmp_path: Path) -> None:
    prefs = Preferences()
    store = TemplateStore(lambda: templates_dir(prefs, tmp_path / "data"))
    store.write("x", empty_doc(), overwrite=False)
    assert (tmp_path / "data" / "templates" / "x.trp").is_file()
    prefs.templates_dir = str(tmp_path / "elsewhere")
    assert store.list() == []                                                # the new directory, per call
    store.write("y", empty_doc(), overwrite=False)
    assert (tmp_path / "elsewhere" / "y.trp").is_file()
    assert templates_dir(Preferences(templates_dir="~/t"), tmp_path) == Path("~/t").expanduser()


def test_should_save_template_nested(tmp_path: Path) -> None:
    store = TemplateStore(lambda: tmp_path)
    store.write("12/34/56/78", doc_with_payload(), overwrite=False)
    doc = store.read("12/34/56/78")
    assert (tmp_path / "12" / "34" / "56" / "78.trp").is_file()

    async def body(e: Any) -> None:
        packet, fe = model_of(doc)
        built = await e.build(packet, fe)
        assert built["error"] is None, built["error"]
        raw = next(layer for layer in built["data"] if layer["id"] == "Raw")
        assert next(f for f in raw["fields"] if f["id"] == "load")["hvalue"] == "22222"
    with_engine(body)


def test_igmp_join_server_template_is_still_unbuildable() -> None:
    """R9: the template listing marks IGMP-JOIN unbuildable because the service cannot build it. If this
    fails, remove IGMP-JOIN from UNBUILDABLE_SERVER (api/packet.py) and snapshot it in tests/golden/trp."""
    async def body(e: Any) -> None:
        assert "IGMP-JOIN" in [t["id"] for t in await e.templates()]
        doc = await e.template("IGMP-JOIN")
        packet, fe = model_of(doc)
        try:
            built = await e.build(packet, fe)
        except PacketError:
            return
        assert built["error"], "IGMP-JOIN builds now"
    with_engine(body)
