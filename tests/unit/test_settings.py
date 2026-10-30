import re
from pathlib import Path

import pytest
from trex_web_gui.store.settings import (
    Preferences,
    SavedConnection,
    Settings,
    SettingsError,
    SettingsStore,
    default_data_dir,
    parse_settings,
    validate,
)


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    st = SettingsStore(tmp_path)
    s = st.load()
    assert s.connections == [] and s.preferences == Preferences() and st.load_error is None


def test_round_trip(tmp_path: Path) -> None:
    st = SettingsStore(tmp_path)
    s = Settings(Preferences(templates_dir="/t"), [SavedConnection("lab", "10.0.0.5", timeout_s=5.0)])
    st.save(s)
    assert SettingsStore(tmp_path).load() == s


def test_corrupt_file_is_reported_not_overwritten(tmp_path: Path) -> None:  # Review Focus 2
    (tmp_path / "settings.toml").write_text("[[connections]\nname = ")
    st = SettingsStore(tmp_path)
    assert st.load() == Settings(Preferences(), [])
    assert st.load_error and "settings.toml" in st.load_error
    assert (tmp_path / "settings.toml").read_text() == "[[connections]\nname = "


def test_save_after_corrupt_backs_up(tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_text("garbage = = 1")
    st = SettingsStore(tmp_path)
    st.load()
    st.save(Settings(Preferences(), []))
    backups = list(tmp_path.glob("settings.toml.bad-*"))
    assert len(backups) == 1
    assert re.fullmatch(r"settings\.toml\.bad-\d{4}-\d\d-\d\d-\d{6}Z", backups[0].name)   # canon:OUT-1
    assert backups[0].read_text() == "garbage = = 1"


def test_wrong_types_in_valid_toml_are_reported(tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_text('[[connections]]\nname = "a"\nhost = "h"\nsync_port = "x"\n')
    st = SettingsStore(tmp_path)
    assert st.load().connections == [] and st.load_error


@pytest.mark.parametrize("conn, msg", [
    (SavedConnection(" ", "h"), "name"),
    (SavedConnection("a", ""), "host"),
    (SavedConnection("a", "h", sync_port=0), "port"),
    (SavedConnection("a", "h", timeout_s=0.1), "timeout"),
])
def test_validation(tmp_path: Path, conn: SavedConnection, msg: str) -> None:
    with pytest.raises(SettingsError, match=msg):
        SettingsStore(tmp_path).save(Settings(Preferences(), [conn]))


def test_duplicate_names_rejected(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="duplicate"):
        SettingsStore(tmp_path).save(Settings(Preferences(), [SavedConnection("Lab", "a"),
                                                              SavedConnection("lab", "b")]))


def test_save_is_atomic(tmp_path: Path) -> None:
    st = SettingsStore(tmp_path)
    st.save(Settings(Preferences(), []))
    assert [p.name for p in tmp_path.iterdir()] == ["settings.toml"]   # no temp files left


def test_failed_write_keeps_old_file_and_leaves_no_temp(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    st = SettingsStore(tmp_path)
    st.save(Settings(Preferences(templates_dir="/old"), []))
    before = st.path.read_bytes()

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("trex_web_gui.store.settings.tomli_w.dump", boom)
    with pytest.raises(OSError):
        st.save(Settings(Preferences(templates_dir="/new"), []))
    assert st.path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["settings.toml"]


def test_non_utf8_file_is_reported_not_a_crash(tmp_path: Path) -> None:
    raw = '[preferences]\ntemplates_dir = "/home/ren\u00e9"\n'.encode("latin-1")
    (tmp_path / "settings.toml").write_bytes(raw)
    st = SettingsStore(tmp_path)
    assert st.load() == Settings(Preferences(), [])
    assert st.load_error and "settings.toml" in st.load_error
    assert (tmp_path / "settings.toml").read_bytes() == raw


def test_relative_trex_cfg_path_in_the_file_is_a_load_error(tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_text('[preferences]\ntrex_cfg_path = "etc/trex_cfg.yaml"\n')
    st = SettingsStore(tmp_path)
    assert st.load() == Settings(Preferences(), [])
    assert st.load_error and "absolute" in st.load_error


def test_unreadable_file_is_reported_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "settings.toml").mkdir()               # exists, but read_text raises IsADirectoryError
    st = SettingsStore(tmp_path)
    assert st.load() == Settings(Preferences(), [])
    assert st.load_error and "settings.toml" in st.load_error


def test_unreadable_data_dir_falls_back_to_defaults(tmp_path: Path) -> None:
    d = tmp_path / "locked"
    d.mkdir()
    (d / "settings.toml").write_text("[preferences]\n")
    d.chmod(0)
    try:
        st = SettingsStore(d)
        s = st.load()
        assert s == Settings() and st.load_error and "Permission denied" in st.load_error
    finally:
        d.chmod(0o700)


def test_release_on_exit_round_trips_and_type_checks(tmp_path: Path) -> None:
    st = SettingsStore(tmp_path)
    s = Settings()
    s.preferences.release_on_exit = False
    st.save(s)
    assert st.load().preferences.release_on_exit is False
    with pytest.raises(SettingsError):
        parse_settings({"preferences": {"release_on_exit": "no"}})


def test_library_dir_round_trips_and_accepts_a_tilde_path(tmp_path: Path) -> None:
    st = SettingsStore(tmp_path)
    for value in ("/srv/profiles", "~/profiles", ""):
        s = Settings()
        s.preferences.library_dir = value
        st.save(s)
        assert st.load().preferences.library_dir == value and st.load_error is None


def test_library_dir_must_be_absolute_without_nul_in_parse_and_validate() -> None:
    for bad in ("relative/dir", "/a\x00b"):
        with pytest.raises(SettingsError, match="library_dir"):
            parse_settings({"preferences": {"library_dir": bad}})
        s = Settings()
        s.preferences.library_dir = bad
        with pytest.raises(SettingsError, match="library_dir"):
            validate(s)
    with pytest.raises(SettingsError):
        parse_settings({"preferences": {"library_dir": 5}})
    assert parse_settings({"preferences": {"library_dir": ""}}).preferences.library_dir == ""


def test_pcap_size_cap_round_trips_and_refuses_out_of_range_and_wrong_types(tmp_path: Path) -> None:
    st = SettingsStore(tmp_path)
    assert Settings().preferences.pcap_size_cap_mib == 64
    for value in (1, 64, 1024):
        s = Settings()
        s.preferences.pcap_size_cap_mib = value
        st.save(s)
        assert st.load().preferences.pcap_size_cap_mib == value and st.load_error is None
    for bad in (0, 1025):
        with pytest.raises(SettingsError, match="pcap_size_cap_mib"):
            parse_settings({"preferences": {"pcap_size_cap_mib": bad}})
        s = Settings()
        s.preferences.pcap_size_cap_mib = bad
        with pytest.raises(SettingsError, match="pcap_size_cap_mib"):
            validate(s)
    for wrong in ("64", 64.0, True):
        with pytest.raises(SettingsError):
            parse_settings({"preferences": {"pcap_size_cap_mib": wrong}})


def test_templates_dir_must_be_absolute_after_tilde_in_parse_and_validate(tmp_path: Path) -> None:
    for bad in ("relative/x", "/a\x00b"):
        with pytest.raises(SettingsError, match="templates_dir"):
            parse_settings({"preferences": {"templates_dir": bad}})
        s = Settings()
        s.preferences.templates_dir = bad
        with pytest.raises(SettingsError, match="templates_dir"):
            validate(s)
    st = SettingsStore(tmp_path)
    for value in ("~/t", "/srv/templates", ""):
        s = Settings()
        s.preferences.templates_dir = value
        st.save(s)
        assert st.load().preferences.templates_dir == value and st.load_error is None


def test_default_data_dir_is_named_for_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / "trex-web-gui"
