"""Golden snapshots of the server's packet templates as the packet engine builds them (P4b Task 3).

Each template under the vendored scapy_server/templates, except IGMP-JOIN, is loaded and built on a fresh
PacketEngine; its `binary` and `data` must equal tests/golden/trp/<id with / as __>.json. IGMP-JOIN has no
snapshot: the service cannot build it (two Scapy classes are named IGMP, and its fixture carries an IP
Router Alert option). WRITE_GOLDEN=1 rewrites the snapshots. The suite is marked golden and runs only under
`make check-golden`.

Host note (R12). The snapshots hold the display text (hvalue) of enum fields named by the host's
/etc/ethertypes and /etc/protocols (Ether type "IPv4", IP proto "udp"); the snapshots were recorded on a
host whose tables match a macOS-like one. A host with an ebtables-era /etc/ethertypes reads 0x9000 as
"LOOP". The numbers (`value`) are the same everywhere and are what a saved file holds; no table is patched
and nothing is masked here."""
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from support import ROOT
from trex_web_gui.studio.packetdoc import model_of
from trex_web_gui.trexapi.packet import PacketEngine

pytestmark = pytest.mark.golden

TEMPLATES = ROOT / "backend/trex_web_gui/_vendor/trex/scapy_server/templates"
HERE = Path(__file__).parent / "trp"
WRITE = os.environ.get("WRITE_GOLDEN") == "1"
CLOCK_FIELDS = {"ts_ori", "ts_rx", "ts_tx"}      # an ICMP layer's timestamps default to the build time
UNBUILDABLE = {"IGMP-JOIN": "two Scapy classes named IGMP; the fixture also has an IP Router Alert option"}


def template_ids() -> list[str]:
    """The id of every .trp under TEMPLATES (its path without the suffix), sorted; [] when absent."""
    if not TEMPLATES.is_dir():
        return []
    return sorted(p.relative_to(TEMPLATES).with_suffix("").as_posix() for p in TEMPLATES.rglob("*.trp"))


def snapshot_path(template_id: str) -> Path:
    """The snapshot file of a template id, with / written as __."""
    return HERE / (template_id.replace("/", "__") + ".json")


def mask_clock(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`data` with the clock-derived ICMP timestamp fields' values replaced by "<clock>" (they differ per
    build and are not in the built bytes of an echo)."""
    def mask(f: dict[str, Any]) -> dict[str, Any]:
        """One field, masked when it is a clock field."""
        return {**f, "hvalue": "<clock>", "value": "<clock>"} if f["id"] in CLOCK_FIELDS else f
    return [{**layer, "fields": [mask(f) for f in layer["fields"]]} if layer["id"] == "ICMP" else layer
            for layer in data]


def build_template(template_id: str) -> dict[str, Any]:
    """`{binary, data}` of the template built on a fresh engine (opus M9)."""
    async def go() -> dict[str, Any]:
        """Start an engine, load and build the template, close the engine."""
        engine = PacketEngine()
        try:
            doc = await engine.template(template_id)
            packet, fe = model_of(doc)
            built = await engine.build(packet, fe)
            assert built["error"] is None, built["error"]
            return {"binary": built["binary"], "data": mask_clock(built["data"])}
        finally:
            await engine.close()
    return asyncio.run(go())


@pytest.mark.parametrize("template_id", [t for t in template_ids() if t not in UNBUILDABLE])
def test_template_matches_snapshot(template_id: str) -> None:
    """The template's built binary and data equal its snapshot (WRITE_GOLDEN=1 rewrites it)."""
    snap = snapshot_path(template_id)
    got = json.dumps(build_template(template_id), sort_keys=True, indent=1)
    if WRITE:
        HERE.mkdir(exist_ok=True)
        snap.write_text(got + "\n")
        return
    assert snap.exists(), f"no snapshot for {template_id}; run WRITE_GOLDEN=1 make check-golden"
    assert got + "\n" == snap.read_text()


def test_the_listing_and_the_snapshots_agree() -> None:
    """Every buildable template listed by a fresh engine has a snapshot file, and no snapshot is orphaned."""
    async def listed() -> list[str]:
        """The ids a fresh engine lists."""
        engine = PacketEngine()
        try:
            return [t["id"] for t in await engine.templates()]
        finally:
            await engine.close()
    ids = asyncio.run(listed())
    assert sorted(ids) == template_ids()
    want = {snapshot_path(t).name for t in ids if t not in UNBUILDABLE}
    have = {p.name for p in HERE.glob("*.json")} if HERE.is_dir() else set()
    assert have == want or WRITE
