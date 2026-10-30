from trex_web_gui.trexapi.errors import (
    NOT_RUNNING_HINT,
    ServerUnreachable,
    TrexApiError,
    UnsupportedServerVersion,
    mode_refusal,
    translate,
)


class FakeTRexError(Exception):
    pass


def test_too_new_names_server_version() -> None:
    e = translate(FakeTRexError(
        "*** Version mismatch: your client version is too new to be supported by the server\n"
        "(API type 'STL':  - server: '5.0', client: '5.1')"))
    assert isinstance(e, UnsupportedServerVersion)
    assert e.server_version == "5.0"
    assert "5.0" in e.message and "5.1" in e.message and "v3.08" in e.message


def test_older_client_names_server_version() -> None:
    e = translate(FakeTRexError("RPC version mismatch: a newer client version is required\n"
                                "(API type 'STL':  - server: '6.0', client: '5.1')"))
    assert isinstance(e, UnsupportedServerVersion) and e.server_version == "6.0"


def test_no_server() -> None:
    e = translate(FakeTRexError("*** Failed to get server response from tcp://127.0.0.1:14531"))
    assert isinstance(e, ServerUnreachable)
    assert "127.0.0.1:14531" in e.message
    # the likeliest cause, said (RC1-19): TRex is not running, and trex-web-gui does not start it
    said = "Cannot reach the TRex server: Failed to get server response from tcp://127.0.0.1:14531."
    assert e.message == f"{said} {NOT_RUNNING_HINT}"
    assert NOT_RUNNING_HINT == ("Is TRex running on that host, with its RPC on that port? "
                                "trex-web-gui does not start it.")


def test_bad_host_name() -> None:  # trex_req_resp_client.py:422
    e = translate(FakeTRexError("ZMQ Error: Bad server or port name: Invalid argument"))
    assert isinstance(e, ServerUnreachable)
    assert NOT_RUNNING_HINT not in e.message     # a bad name, not a silent server


def test_not_connected_to_server_is_unreachable() -> None:   # trex_req_resp_client.py:36/180/389
    e = translate(FakeTRexError("\x1b[1m*** Not connected to server\x1b[0m"))
    assert isinstance(e, ServerUnreachable)
    assert "Not connected to server" in e.message


def test_other_errors_keep_text_without_ansi() -> None:
    e = translate(FakeTRexError("\x1b[1m*** Method get_system_info not registered\x1b[0m"))
    assert type(e) is TrexApiError
    assert e.message == "Method get_system_info not registered"


# recorded in tests/fixtures/p2/negatives.json
def test_api_handler_mismatch_is_a_server_restart() -> None:
    import json

    from support import FIXTURES
    from trex_web_gui.trexapi.errors import ServerRestarted
    rec = json.loads((FIXTURES / "p2" / "negatives.json").read_text())["api_h_mismatch"]
    text = rec["resp"]["error"]["specific_err"]
    e = translate(FakeTRexError(f"*** {text}"))
    assert isinstance(e, ServerRestarted) and "restarted" in e.message


def test_failed_to_send_message_translates_to_unreachable() -> None:
    # trex_req_resp_client.py:323 and :338 both begin with this text
    e = translate(FakeTRexError("*** [RPC] - Failed to send message to server"))
    assert isinstance(e, ServerUnreachable) and "Failed to send message to server" in e.message


def test_a_server_in_another_mode_is_refused_with_the_stl_check_text() -> None:
    """ASTF-1: the client's own mode-mismatch text becomes the STL check's message."""
    e = translate(FakeTRexError("RPC configuration mismatch - server RPC configuration: 'ASTF', "
                                "client RPC configuration: 'STL'"))
    assert type(e) is TrexApiError
    assert e.message == mode_refusal("ASTF")
    assert e.message == "The server runs in ASTF mode; trex-web-gui Control needs STL mode."
