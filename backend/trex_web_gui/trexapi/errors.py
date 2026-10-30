"""TRexError → typed errors. Matching is on the server's own wording, recorded in
tests/fixtures/negatives.json (spike 5); if TRex rewords, those fixtures and these tests change together."""
import re

SUPPORTED = "STL API 5.1 (TRex v3.08)"
SUPPORTED_VERSION = "v3.08"      # get_version's string, as recorded in tests/fixtures/rpc.jsonl
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_SERVER_MODE = re.compile(r"server RPC configuration: '([^']+)'")
_SERVER_VER = re.compile(r"server: '([^']+)'")


class TrexApiError(Exception):
    def __init__(self, message: str) -> None:
        """Keep the message as .message as well as the exception text."""
        super().__init__(message)
        self.message = message


class ServerUnreachable(TrexApiError):
    pass


class UnsupportedServerVersion(TrexApiError):
    def __init__(self, server_version: str, message: str | None = None) -> None:
        """The default message names the server's API version and the one supported; keeps server_version."""
        super().__init__(message or f"The server speaks STL API {server_version}; "
                         f"trex-web-gui supports only {SUPPORTED}.")
        self.server_version = server_version


class CommandRefused(TrexApiError):
    """Refused by trex-web-gui itself before any RPC (or by an ownership check on the client cache)."""
    def __init__(self, code: str, message: str) -> None:
        """code is the short reason for the refusal (e.g. "not_owned"); the message is for people."""
        super().__init__(message)
        self.code = code


class ServerRestarted(TrexApiError):
    pass


# A server that does not answer is most often one that is not running (its SSH session ended, say): RC1-19.
NOT_RUNNING_HINT = "Is TRex running on that host, with its RPC on that port? trex-web-gui does not start it."


def _clean(text: str) -> str:
    """text on one line, without ANSI colour codes, leading "*" markers or blank lines."""
    lines = [ln.strip().lstrip("*").strip() for ln in _ANSI.sub("", text).splitlines()]
    return " ".join(ln for ln in lines if ln)


def mode_refusal(mode: str) -> str:
    """The message for a server not in STL mode; the connect's STL check and `translate` both use it."""
    return f"The server runs in {mode} mode; trex-web-gui Control needs STL mode."


def translate(exc: BaseException) -> TrexApiError:
    """A TrexApiError unchanged; anything else classified by the server's wording: UnsupportedServerVersion,
    ServerRestarted, ServerUnreachable, the mode refusal (the server's "RPC configuration mismatch", built in
    trex-core's src/rpc-server/trex_rpc_cmd_api.h:101), or a plain TrexApiError carrying the cleaned text."""
    if isinstance(exc, TrexApiError):
        return exc
    text = _clean(str(exc))
    m = _SERVER_VER.search(text)
    if m and "ersion mismatch" in text:
        return UnsupportedServerVersion(m.group(1))
    # server text at src/rpc-server/trex_rpc_cmd.cpp:121; recorded in Task 1 as api_h_mismatch
    if "API handler provided mismatch" in text:
        return ServerRestarted("The TRex server restarted (its API handler changed); connect again.")
    # the server's mode check (trex-core src/rpc-server/trex_rpc_cmd_api.h:101): a server not in STL mode
    m = _SERVER_MODE.search(text)
    if m and "RPC configuration mismatch" in text:
        return TrexApiError(mode_refusal(m.group(1)))
    # trex_req_resp_client.py:36/180/389: the client gave up reconnecting to a server that stopped answering;
    # :323 and :338 ("Failed to send message to server[ general exception]"): the send failed
    if "Bad server or port name" in text:
        return ServerUnreachable(f"Cannot reach the TRex server: {text}")
    if "Failed to get server response" in text or "Not connected to server" in text \
            or "Failed to send message to server" in text:
        return ServerUnreachable(f"Cannot reach the TRex server: {text}. {NOT_RUNNING_HINT}")
    return TrexApiError(text)
