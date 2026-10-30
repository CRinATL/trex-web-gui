"""Make the vendored `trex` package importable. Only trexapi may import `trex` (plan §4)."""
import json
import os
import sys
import threading
from pathlib import Path

# The override exists for negative-control tests only (a vendor tree built with --skip-patch).
VENDOR = Path(os.environ.get("TREX_WEB_GUI_VENDOR_OVERRIDE")
              or Path(__file__).resolve().parent.parent / "_vendor")
_lock = threading.Lock()
_loaded = False


def load() -> None:
    """Set TREX_EXT_LIBS and TREX_SYSTEM_LIBS in os.environ, put the vendored tree on sys.path and import
    trex.stl.api. Later calls do nothing once an import succeeded; a failed one is retried."""
    global _loaded
    with _lock:
        if _loaded:
            return
        os.environ["TREX_EXT_LIBS"] = str(VENDOR / "external_libs")
        os.environ["TREX_SYSTEM_LIBS"] = "zmq,yaml"   # vendor patch 01; spike 1c
        sys.path.insert(0, str(VENDOR))
        import trex.stl.api  # noqa: F401  (runs trex/__init__.__load)
        _loaded = True


def pin() -> dict[str, object]:
    """The vendored TREX_PIN file parsed as JSON; OSError or ValueError when it is missing or bad."""
    data: dict[str, object] = json.loads((VENDOR / "TREX_PIN").read_text())
    return data
