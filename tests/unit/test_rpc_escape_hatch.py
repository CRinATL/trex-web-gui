import re
from pathlib import Path

from trex_web_gui.trexapi.rpc_escape_hatch import RAW_METHODS

PKG = Path(__file__).resolve().parent.parent.parent / "backend" / "trex_web_gui"
SEND = re.compile(r'_transmit\("(\w+)", params=\{"command": "(\w+)"')


def test_every_raw_transmit_is_listed() -> None:
    hatch = PKG / "trexapi" / "rpc_escape_hatch.py"
    found: list[Path] = []
    sent: list[tuple[str, str]] = []
    for f in sorted(PKG.rglob("*.py")):
        if "_vendor" in f.relative_to(PKG).parts:
            continue
        for line in f.read_text().splitlines():
            if "_transmit(" in line:
                found.append(f)
                m = SEND.search(line)
                assert m, f"unparseable raw send in {f}: {line.strip()}"
                sent.append((m.group(1), m.group(2)))
    assert found and set(found) == {hatch}, found
    assert tuple(sent) == RAW_METHODS
