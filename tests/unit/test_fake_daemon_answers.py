"""What tests/fake/fake_daemon.py answers where the real daemon's source, not a recording, is the authority.

The real daemon is trex_server.py (trex-core, the vendor pin): its `start` sets the verbose status
'TRex is starting (data is not available yet)', and its `push_file` writes `os.path.basename(filename)` under
the files dir and answers False when the open fails (daemon-register-pr-b-16)."""

import base64
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fake.fake_daemon import STARTING, Daemon, build_parser


@pytest.fixture
def make() -> Iterator[Callable[..., Daemon]]:
    """A factory for fake daemon states built from the real parser's defaults plus `flags`; every one it built
    is closed at teardown, so its temp dir is removed even when the test fails first."""
    built: list[Daemon] = []

    def build(*flags: str) -> Daemon:
        """One Daemon, remembered for the teardown."""
        d = Daemon(build_parser().parse_args(["0", *flags]))
        built.append(d)
        return d

    yield build
    for d in built:
        d.close()


def push(d: Daemon, name: str) -> Any:
    """The fake's answer to push_file of `name` with one byte of content."""
    return d.dispatch("push_file", {"filename": name, "bin_data": base64.b64encode(b"x").decode()})


def test_status_while_starting_carries_the_servers_text(make: Callable[..., Daemon]) -> None:
    """While Starting, get_running_status says what trex_server.py start sets, not a made-up text."""
    d = make()
    d.run_seq = 1
    d.starting_until = time.monotonic() + 60
    assert d.status() == {"state": STARTING, "verbose": "TRex is starting (data is not available yet)"}


def test_push_file_keeps_only_the_basename(make: Callable[..., Daemon]) -> None:
    """A name with a directory part is stored under its basename and answers True (os.path.basename)."""
    d = make()
    assert push(d, "a/b.yaml") == ("result", True)
    assert d.pushed() == ["b.yaml"]


@pytest.mark.parametrize("name", ["", ".", "..", "a/"])
def test_push_file_names_with_no_file_part_answer_false_and_write_nothing(
        make: Callable[..., Daemon], name: str) -> None:
    """A name whose basename is a directory (or empty) cannot be opened: False, and nothing is stored."""
    d = make()
    assert push(d, name) == ("result", False)
    assert d.pushed() == []


def test_push_file_plain_name_still_answers_true(make: Callable[..., Daemon]) -> None:
    """Control: an ordinary name is stored as it is."""
    d = make()
    assert push(d, "c.yaml") == ("result", True)
    assert d.pushed() == ["c.yaml"]


def test_close_removes_the_temp_dir_and_is_idempotent(make: Callable[..., Daemon]) -> None:
    """Daemon.close removes the directory push_file wrote into, and a second call raises nothing."""
    d = make()
    assert push(d, "c.yaml") == ("result", True)
    tmp = Path(d.tmp.name)
    assert tmp.exists()
    d.close()
    d.close()
    assert not tmp.exists()
