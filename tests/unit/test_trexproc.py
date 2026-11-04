"""The TRex server process check and the loss messages built on it (trexproc.py)."""
from pathlib import Path

from trex_web_gui.trexproc import STOPPED, server_running, silence_message


def _proc(root: Path, pid: int, *argv: str) -> None:
    """A fake /proc/<pid>/cmdline holding `argv`, NUL-separated and NUL-terminated as the kernel writes it."""
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))


def test_finds_the_interactive_server_by_its_binary_and_flag(tmp_path: Path) -> None:
    _proc(tmp_path, 10, "bash")
    _proc(tmp_path, 11, "./_t-rex-64", "-i", "--mlx5-so", "-c", "4")
    assert server_running(tmp_path) is True


def test_an_absolute_path_counts(tmp_path: Path) -> None:
    _proc(tmp_path, 11, "/opt/trex/v3.08/_t-rex-64", "--cfg", "/etc/trex_cfg.yaml", "-i")
    assert server_running(tmp_path) is True


def test_no_server_process_is_false(tmp_path: Path) -> None:
    _proc(tmp_path, 10, "bash")
    _proc(tmp_path, 12, "./t-rex-64", "-i")                 # the wrapper script alone is not the server
    _proc(tmp_path, 13, "./_t-rex-64", "-f", "cap2/dns.yaml")   # a batch run, not the interactive server
    _proc(tmp_path, 14, "grep", "_t-rex-64 -i")              # someone searching for it
    (tmp_path / "self").mkdir()                              # non-pid entries are skipped
    (tmp_path / "15").mkdir()                                # a process that exited mid-scan: no cmdline
    _proc(tmp_path, 16)                                      # a kernel thread: empty cmdline
    assert server_running(tmp_path) is False


def test_an_unreadable_proc_is_unknown(tmp_path: Path) -> None:
    assert server_running(tmp_path / "missing") is None


def test_silence_messages_name_the_server_and_what_the_check_found() -> None:
    assert STOPPED == "TRex server stopped: the TRex server process (_t-rex-64) on this host has exited."
    assert silence_message("127.0.0.1", 4501, 3.0, False) == STOPPED
    assert silence_message("127.0.0.1", 4501, 3.0, True) == (
        "No response from the TRex server at 127.0.0.1:4501 for 12 s. The TRex server process is still "
        "running; it may be hung or overloaded.")
    assert silence_message("trex-2", 4501, 3.0, None) == (
        "No response from the TRex server at trex-2:4501 for 12 s. The TRex server process may have "
        "stopped, or the network may be down.")
    assert "for 4 s." in silence_message("h", 1, 1.0, None)   # 4 * timeout_s, rounded down
