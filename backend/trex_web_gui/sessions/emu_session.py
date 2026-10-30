"""EMU (P7, v1.1) interface stub so later work needs no refactor (plan §0 item 5)."""
from typing import Protocol

from trex_web_gui.trexapi import TrexApiError


class EmuSession(Protocol):
    # connects to the EMU server at host:port (4510 by default)
    async def connect(self, host: str, port: int = 4510) -> None: ...
    # ends the EMU connection
    async def disconnect(self) -> None: ...


class NotAvailableEmuSession:
    async def connect(self, host: str, port: int = 4510) -> None:
        """Always raises TrexApiError: EMU arrives in v1.1 (P7)."""
        raise TrexApiError("EMU arrives in v1.1 (P7)")

    async def disconnect(self) -> None:
        """Nothing to end: returns at once."""
        return None
