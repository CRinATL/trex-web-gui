from pathlib import Path

import pytest
from trex_web_gui.store.port_labels import LabelError, PortLabelStore

SRV = "10.0.0.5:4501"
MAC = "aa:bb:cc:00:11:22"


def test_missing_file_is_empty(tmp_path: Path) -> None:
    st = PortLabelStore(tmp_path)
    assert st.load() == {} and st.load_error is None


def test_round_trip(tmp_path: Path) -> None:
    st = PortLabelStore(tmp_path)
    got = st.set(SRV, MAC, "uplink-A", "to the DUT")
    assert got == {SRV: {MAC: {"name": "uplink-A", "description": "to the DUT"}}}
    assert PortLabelStore(tmp_path).load() == got
    assert st.set(SRV, "#1", "b", "") [SRV]["#1"] == {"name": "b", "description": ""}


def test_trim_and_length_limits(tmp_path: Path) -> None:
    st = PortLabelStore(tmp_path)
    got = st.set(SRV, MAC, "  x  ", "  d  ")
    assert got[SRV][MAC] == {"name": "x", "description": "d"}
    assert st.set(SRV, MAC, "n" * 40, "d" * 500)[SRV][MAC]["name"] == "n" * 40
    with pytest.raises(LabelError) as e:
        st.set(SRV, MAC, "n" * 41, "")
    assert e.value.field == "name"
    with pytest.raises(LabelError) as e:
        st.set(SRV, MAC, "n", "d" * 501)
    assert e.value.field == "description"
    assert st.set(SRV, MAC, " " + "n" * 40 + " ", "")[SRV][MAC]["name"] == "n" * 40     # trimmed first


@pytest.mark.parametrize("name, desc", [("a\x00b", ""), ("a\nb", ""), ("a", "x\x07"), ("a", "t\tb"),
                                        ("\n", ""), ("a", "a\u2028b"), ("a\u202eb", ""), ("a", "\u2066b"),
                                        ("\u2069", "")])
def test_control_character_is_rejected(tmp_path: Path, name: str, desc: str) -> None:
    with pytest.raises(LabelError):
        PortLabelStore(tmp_path).set(SRV, MAC, name, desc)
    assert not (tmp_path / "port_labels.toml").exists()


@pytest.mark.parametrize("key", ["", "AA:BB:CC:00:11:22", "aa:bb:cc:00:11", "aa-bb-cc-00-11-22", "#", "#1234",
                                 "#-1", "#\u0663", "port0", "#1\n", " #1"])
def test_bad_key_is_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(LabelError) as e:
        PortLabelStore(tmp_path).set(SRV, key, "n", "")
    assert e.value.field == "key"


@pytest.mark.parametrize("server", ["", "x" * 301, "h:1\n", "h:\x001"])
def test_bad_server_is_rejected(tmp_path: Path, server: str) -> None:
    with pytest.raises(LabelError) as e:
        PortLabelStore(tmp_path).set(server, MAC, "n", "")
    assert e.value.field == "server"


def test_longest_server_is_accepted(tmp_path: Path) -> None:
    srv = "h" * 295 + ":4501"
    assert len(srv) == 300
    assert PortLabelStore(tmp_path).set(srv, "#0", "n", "") [srv]["#0"]["name"] == "n"


def test_empty_name_and_description_delete_the_entry(tmp_path: Path) -> None:
    st = PortLabelStore(tmp_path)
    st.set(SRV, MAC, "a", "")
    st.set(SRV, "#1", "b", "")
    assert st.set(SRV, MAC, " ", "") == {SRV: {"#1": {"name": "b", "description": ""}}}
    assert st.set(SRV, "#1", "", "  ") == {}
    assert PortLabelStore(tmp_path).load() == {}
    assert st.set(SRV, MAC, "", "") == {}               # deleting what is absent is fine


def test_description_alone_keeps_the_entry(tmp_path: Path) -> None:
    assert PortLabelStore(tmp_path).set(SRV, MAC, "", "only a note")[SRV][MAC] == \
        {"name": "", "description": "only a note"}


def test_servers_with_the_same_port_numbers_stay_separate(tmp_path: Path) -> None:
    st = PortLabelStore(tmp_path)
    st.set("localhost:4501", "#0", "one", "")
    got = st.set("127.0.0.1:4501", "#0", "two", "")
    assert got["localhost:4501"]["#0"]["name"] == "one" and got["127.0.0.1:4501"]["#0"]["name"] == "two"
    assert st.set("localhost:4501", "#0", "", "")["127.0.0.1:4501"]["#0"]["name"] == "two"


def test_corrupt_file_is_reported_and_kept_until_the_next_save(tmp_path: Path) -> None:
    f = tmp_path / "port_labels.toml"
    f.write_text("[[x\nbad = ")
    st = PortLabelStore(tmp_path)
    assert st.load() == {} and st.load_error and "port_labels.toml" in st.load_error
    assert f.read_text() == "[[x\nbad = "
    got = st.set(SRV, MAC, "n", "")
    assert got == {SRV: {MAC: {"name": "n", "description": ""}}} and st.load_error is None
    assert [p.name for p in tmp_path.glob("port_labels.toml.bad-*")] == [p.name for p in tmp_path.iterdir()
                                                                         if ".bad-" in p.name] != []
    assert PortLabelStore(tmp_path).load() == got


@pytest.mark.parametrize("text", [
    'a = 1\n',                                        # server is not a table
    '["h:1"]\nx = 1\n',                               # key is not a table
    '["h:1"."aa:bb"]\nname = "n"\n',                  # bad key
    '["h:1"."#0"]\nname = 5\n',                       # wrong type
    '["h:1"."#0"]\nname = "' + "n" * 41 + '"\n',      # over-long
])
def test_invalid_content_is_a_load_error(tmp_path: Path, text: str) -> None:
    (tmp_path / "port_labels.toml").write_text(text)
    st = PortLabelStore(tmp_path)
    assert st.load() == {} and st.load_error


def test_non_utf8_file_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / "port_labels.toml").write_bytes(b"\xff\xfe")
    st = PortLabelStore(tmp_path)
    assert st.load() == {} and st.load_error


def test_zero_width_joiner_is_allowed(tmp_path: Path) -> None:
    assert PortLabelStore(tmp_path).set(SRV, MAC, "a‍b", "")[SRV][MAC]["name"] == "a‍b"


def test_unreadable_data_dir_is_a_load_error(tmp_path: Path) -> None:
    d = tmp_path / "locked"
    d.mkdir()
    (d / "port_labels.toml").write_text('["h:1"."aa:bb"]\nname = "n"\n')
    d.chmod(0)
    try:
        st = PortLabelStore(d)
        assert st.load() == {} and st.load_error and "Permission denied" in st.load_error
    finally:
        d.chmod(0o700)
