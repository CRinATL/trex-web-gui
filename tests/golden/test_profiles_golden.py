"""Golden snapshots of every stock stl profile's streams as the worker returns them (P4a Task 1).

Each file under SRC/scripts/stl is either listed in stl/SKIPPED.md (with a reason) or loads through
trexapi.load_profile and matches stl/<file>.json. WRITE_GOLDEN=1 rewrites the snapshots and SKIPPED.md.
The suite is marked golden and runs only under `make check-golden`."""
import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from support import SRC
from trex_web_gui import trexapi

pytestmark = pytest.mark.golden

STL = SRC / "scripts/stl"
HERE = Path(__file__).parent / "stl"
SKIPPED = HERE / "SKIPPED.md"
FULL_MAX = 256 * 1024               # a snapshot above this many bytes is stored as a digest plus stream 0
WRITE = os.environ.get("WRITE_GOLDEN") == "1"
NOT_A_PROFILE = {"tpg_tags_conf.json": "TPG tag configuration, not a profile (spec section 8)",
                 "tpg_tags_conf.py": "TPG tag configuration, not a profile (spec section 8)"}


def profiles() -> list[Path]:
    """Every .py, .yaml and .json directly under SRC/scripts/stl, sorted; [] when the clone is missing."""
    if not STL.is_dir():
        return []
    return sorted(p for p in STL.iterdir() if p.is_file() and p.suffix in (".py", ".yaml", ".json"))


def canonical(streams: list[dict[str, Any]]) -> str:
    """The canonical pretty JSON of a stream list."""
    return json.dumps(streams, sort_keys=True, indent=1)


def snapshot(streams: list[dict[str, Any]]) -> str:
    """The snapshot text: the canonical JSON, or for a large profile its sha256, count and stream 0."""
    text = canonical(streams)
    if len(text.encode()) <= FULL_MAX:
        return text
    compact = json.dumps(streams, sort_keys=True, separators=(",", ":"))
    return json.dumps({"sha256": hashlib.sha256(compact.encode()).hexdigest(), "stream_count": len(streams),
                       "first": streams[0]}, sort_keys=True, indent=1)


def load(path: Path) -> list[dict[str, Any]]:
    """The streams of port 0 from a worker load; ProfileError when the profile does not load."""
    lp = asyncio.run(trexapi.load_profile(str(path), [0], []))
    return lp.streams_by_port[0]


def first_difference(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> str:
    """Describe the first stream index at which two loads differ."""
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return f"stream {i}"
    return f"stream {min(len(a), len(b))}: counts {len(a)} and {len(b)}"


def read_skipped() -> dict[str, str]:
    """The `| file | reason |` rows of SKIPPED.md."""
    if not SKIPPED.exists():
        return {}
    rows = re.findall(r"^\| (\S+) \| (.+?) \|$", SKIPPED.read_text(), re.M)
    return {f: r for f, r in rows if f not in ("file", "---")}


def write_skipped(rows: dict[str, str]) -> None:
    """Write SKIPPED.md as one table."""
    lines = ["# Stock profiles with no snapshot", "", "| file | reason |", "| --- | --- |"]
    lines += [f"| {f} | {r} |" for f, r in sorted(rows.items())]
    SKIPPED.write_text("\n".join(lines) + "\n")


def listing_problem(name: str, reason: str | None, loads: bool) -> str | None:
    """Why a profile's SKIPPED.md listing (reason; None if unlisted) is wrong given whether it loads."""
    if not loads:
        return None if reason is not None else f"{name} does not load and is not listed in SKIPPED.md"
    if reason is not None and not reason.startswith("nondeterministic"):
        return f"{name} now loads: remove {name} from SKIPPED.md"
    return None


@pytest.fixture(scope="module")
def skipped_rows() -> Iterator[dict[str, str]]:
    """The rows being collected by a WRITE_GOLDEN run (written at module teardown), else the file's rows."""
    rows: dict[str, str] = {} if WRITE else read_skipped()
    yield rows
    if WRITE:
        write_skipped(rows)


@pytest.mark.parametrize("path", profiles(), ids=lambda p: p.name)
def test_profile_matches_snapshot(path: Path, skipped_rows: dict[str, str]) -> None:
    """The profile is listed in SKIPPED.md, or its port-0 streams equal its snapshot."""
    snap = HERE / (path.name + ".json")
    if WRITE:
        snap.unlink(missing_ok=True)
        if path.name in NOT_A_PROFILE:
            skipped_rows[path.name] = NOT_A_PROFILE[path.name]
            return
        try:
            first = load(path)
            second = load(path)
        except trexapi.ProfileError as e:
            skipped_rows[path.name] = (str(e).splitlines() or [""])[0].replace("|", "/")
            return
        if first != second:
            skipped_rows[path.name] = f"nondeterministic ({first_difference(first, second)})"
            return
        snap.write_text(snapshot(first))
        return
    reason = skipped_rows.get(path.name)
    try:
        streams = load(path)
    except trexapi.ProfileError:
        streams = None
    problem = listing_problem(path.name, reason, streams is not None)
    assert problem is None, problem
    if streams is None or reason is not None:      # listed and unloadable, or listed as nondeterministic
        return
    assert snap.exists(), f"no snapshot for {path.name}; run WRITE_GOLDEN=1 make check-golden"
    assert snapshot(streams) == snap.read_text()


def test_listing_problem_decisions() -> None:
    """A loadable nondeterministic row passes; any other loadable row or unlisted failure is a problem."""
    assert listing_problem("a.py", "nondeterministic (stream 0)", True) is None
    assert listing_problem("a.py", "nondeterministic (stream 0)", False) is None
    assert listing_problem("a.py", None, True) is None
    assert listing_problem("a.py", "some error", False) is None
    assert "now loads: remove a.py" in (listing_problem("a.py", "some error", True) or "")
    assert "not listed" in (listing_problem("a.py", None, False) or "")
