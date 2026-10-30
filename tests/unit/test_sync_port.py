from types import SimpleNamespace

import pytest
from trex_web_gui.trexapi import ServerUnreachable, TrexApiError
from trex_web_gui.trexapi.ports import sync_port


class _Rc:
    """The vendored client's RC: bad when it carries an error."""

    def __init__(self, err=None):
        """`err` None is a good RC."""
        self._err = err

    def bad(self):
        """True when the RC carries an error."""
        return self._err is not None

    def err(self):
        """The error text, as RC.err() returns it."""
        return self._err


def _client(rc, status):
    """A client with one port 3 whose sync() returns `rc` and whose server status is `status`."""
    p = SimpleNamespace(status=status, profile_state_list={"_": "STREAMS", "bench": "TX"},
                        profile_stream_list={"_": [1], "bench": [2]}, streams={1: "a", 2: "b"},
                        sync=lambda: rc)
    return SimpleNamespace(ports={3: p}), p


def test_sync_port_drops_idle_and_translates_a_bad_rc():
    c, p = _client(_Rc("boom"), {})
    with pytest.raises(TrexApiError):
        sync_port(c, 3)
    c, p = _client(_Rc(), {"state_profile": {"bench": "TX"}})
    sync_port(c, 3)
    assert p.profile_state_list == {"bench": "TX"} and p.streams == {2: "b"}


def test_sync_port_loss_class_text_is_unreachable():
    c, _ = _client(_Rc("Failed to get server response"), {})
    with pytest.raises(ServerUnreachable):
        sync_port(c, 3)
