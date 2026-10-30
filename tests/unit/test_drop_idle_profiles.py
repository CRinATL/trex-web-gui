from types import SimpleNamespace

from trex_web_gui.trexapi.ports import _drop_idle_profiles


def _port(status):
    """A port stand-in with this status and cached profiles "_" (STREAMS, 1) and "bench" (TX, 2)."""
    return SimpleNamespace(status=status, profile_state_list={"_": "STREAMS", "bench": "TX"},
                           profile_stream_list={"_": [1], "bench": [2]}, streams={1: "a", 2: "b"})


def test_a_status_without_state_profile_drops_nothing():
    p = _port({})
    _drop_idle_profiles(p)
    assert p.profile_state_list == {"_": "STREAMS", "bench": "TX"} and p.streams == {1: "a", 2: "b"}


def test_a_profile_the_server_omits_or_reports_idle_is_dropped_with_its_streams():
    p = _port({"state_profile": {"_": "IDLE"}})
    _drop_idle_profiles(p)
    assert p.profile_state_list == {} and p.streams == {} and p.profile_stream_list == {}
