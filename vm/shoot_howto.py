"""Shoot the HOWTO's screens (docs/howto/screens/*.png): docs/howto-first-stream.md, step by step, against the
real TRex guest.

It is vm/shoot_inventory.py with its own steps: the same backend child, headless Chrome, 1400x900 Light-theme
shots at device scale 2, scratch handling and `run()`/`main()`, and the same usage (read that module's
docstring: the guest up with `vm/demo.sh up`, frontend/dist built, ports 8766 and 9344 free, run under
`systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 --`, finish with `vm/demo.sh down`):

    backend/.venv/bin/python -I -B vm/shoot_howto.py <scratch dir> [--out DIR] [--until STEP]

`--out` defaults to docs/howto/screens. The backend here runs on this machine, so the script stages TRex's own
`udp_1pkt_simple.py` (from $TREX_SRC/scripts/stl/, default the Makefile's clone) into
<scratch>/trex/v3.08/stl/ and sets `preferences.workspace_dir` to <scratch>/trex/v3.08 through the API, so the
Browse… dialog opens there with `stl/` one click away and no shot lists $HOME. The guest's ports 0 and 1 are
looped back to each other, so the Rx numbers are real. STEPS lists the steps in order.
"""
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot_inventory as inv
from shoot_inventory import Api, check_status

TREX_SRC = Path(os.environ.get("TREX_SRC") or os.path.expanduser("~/Projects/trex-rework/trex-core"))
STOCK = "udp_1pkt_simple.py"
STUDIO_NAME = "My first stream"
DEST_PORT = "5000"


def stage_trex_tree(scratch):
    """Copy TRex's stock `udp_1pkt_simple.py` into <scratch>/trex/v3.08/stl/; return <scratch>/trex/v3.08."""
    root = scratch / "trex" / "v3.08"
    (root / "stl").mkdir(parents=True)
    shutil.copy(TREX_SRC / "scripts" / "stl" / STOCK, root / "stl" / STOCK)
    return root


async def tx_chip(b):
    """Wait until the selected port's State chip reads TX, and print the chips."""
    await b.wait_text("State TX")
    chips = "document.querySelector('.chips').innerText.replace(/\\s+/g, ' ')"
    print("chips:", await b.js(chips), flush=True)


async def rates(c):
    """Wait until the Dashboard's ports table shows about 1 kpps (Tx on port 0, Rx on port 1); print it."""
    b = c.b
    await b.click("Dashboard", "a")
    await b.wait("[...document.querySelectorAll('table')].some((t) => S.vis(t)"
                 " && t.innerText.includes('Tx pps') && /\\b1(\\.0+)?\\s?k/.test(t.innerText))",
                 40, "port 0 Tx pps and port 1 Rx pps near 1k")
    await asyncio.sleep(3)
    print("ports table:", await b.js(
        "[...document.querySelectorAll('table')].filter((t) => S.vis(t) && t.innerText.includes('Tx pps'))"
        ".map((t) => t.innerText.replace(/\\s+/g, ' ')).join(' | ')"), flush=True)


async def step_connect(c):
    """01: log in, then the Connect dialog; connect to 127.0.0.1 and point Browse… at the staged TRex tree."""
    b = c.b
    await b.goto(f"/api/auth/login?token={c.be.token}")
    await b.wait_for("Control", "a")
    await b.js("localStorage.setItem('trex-web-gui.theme', 'light')")
    await b.goto("/control")
    await b.click("Connect…", "button")
    await b.fill("Host", "127.0.0.1", "[role=dialog]")
    await b.shot("01-connect-dialog")
    await b.click("Connect", "button", "[role=dialog]")
    await b.wait_for("Disconnect", "button")
    await b.wait_gone("Connect to TRex", "[role=dialog]")
    c.api = Api(c.be.token)
    cur = c.api.get("/api/settings")["settings"]
    cur["preferences"]["workspace_dir"] = str(c.scratch / "trex" / "v3.08")
    check_status(c.api.h.put("/api/settings", json=cur), "PUT /api/settings")


async def step_acquire(c):
    """02: start from clean, released ports (an earlier run may have left traffic), then Acquire all."""
    b, api = c.b, c.api
    api.post("/api/ports/acquire", {"ports": [0, 1], "force": True})
    api.post("/api/ports/stop", {"ports": [0, 1]})
    api.post("/api/ports/reset", {"ports": [0, 1]})
    api.post("/api/ports/release", {"ports": [0, 1]})
    await b.wait_for("~Port 0, IDLE, not owned", "[role=treeitem]")
    await b.click("Acquire all", "button", "[role=toolbar]")
    await b.wait_for("~Port 0, IDLE, owned by you", "[role=treeitem]")
    await b.wait_for("~Port 1, IDLE, owned by you", "[role=treeitem]")
    await b.shot("02-acquire-all")


async def step_config(c):
    """03-04: port 1's Config tab (its Source MAC), then port 0's Config tab in L3 with its resolved
    Destination MAC."""
    b = c.b
    await b.click("~Port 1", "[role=treeitem]")
    await b.click("Config", "[role=tab]")
    await b.wait_text("Source MAC")
    await b.shot("03-port1-config-source-mac")
    await b.click("~Port 0", "[role=treeitem]")
    await b.click("Config", "[role=tab]")
    await b.wait_text("Destination MAC")
    print("port 0 config:", await b.js(
        "[...document.querySelectorAll('.config-tab .form-row')]"
        ".map((r) => r.innerText.replace(/\\s+/g, ' ')).join(' | ')"), flush=True)
    await b.shot("04-port0-config-destination-mac")


async def step_stock(c):
    """05-08: port 0's Traffic tab: Browse… (opens in the workspace), the stl/ directory, the inspected stock
    profile at 1 kpps, assigned."""
    b = c.b
    await b.click("Traffic", "[role=tab]")
    await b.click("Browse…", "button")
    await b.wait_for("~stl/", "button", "[role=dialog]")
    await b.shot("05-browse-workspace")
    await b.click("stl/", "button", "[role=dialog]")
    await b.wait_for(STOCK, "button", "[role=dialog]")
    await b.shot("06-browse-stl")
    await b.click(STOCK, "button", "[role=dialog]")
    await b.wait_gone("Browse", "[role=dialog]")
    await b.click("Inspect", "button", "[role=tabpanel]")
    await b.wait(inv.SOME_TABLE, 20, "streams table")
    await b.fill("Multiplier", "1kpps")
    await b.click("Assign", "button", "[role=tabpanel]")
    await asyncio.sleep(1.5)
    await b.shot("07-stock-inspected-assigned")


async def step_stock_run(c):
    """08-09: Start; the State chip reads TX; the Dashboard shows port 0 Tx pps and port 1 Rx pps; Stop."""
    b = c.b
    await b.click("Start", "button", "[role=tabpanel]")
    await b.click("Control", "[role=tab]")
    await tx_chip(b)
    await b.shot("08-stock-tx-chip")
    await rates(c)
    await b.shot("09-stock-dashboard")
    await b.click("Control", "a")
    await b.click("~Port 0", "[role=treeitem]")
    await b.click("Stop", "button", "[role=toolbar]")
    await b.click("Control", "[role=tab]")
    await b.wait_text("State STREAMS")


async def step_studio(c):
    """10-12: Studio: New… profile, Add stream, the Packet tab, Edit packet (the embedded editor), the UDP
    destination port changed and saved."""
    b = c.b
    await b.click("Studio", "a")
    await b.click("New…", "button")
    await b.fill("Name", STUDIO_NAME, "[role=group]")
    await b.click("Create", "button", "[role=group]")
    await b.wait_for(f"~{STUDIO_NAME}", "[role=option]")
    await b.click("Add stream", "button")
    await b.wait(inv.STREAM_ROWS + ".length > 0", 20, "stream table rows")
    await b.shot("10-studio-new-profile")
    await b.click("Packet", "[role=tab]")
    await b.click("Edit packet", "button")
    await b.wait_for("Save", "button")
    await b.wait_text("Destination")
    await b.click("Expand all", "button")
    await b.wait_text("Checksum")
    await b.shot("11-studio-edit-packet")
    await b.fill("dport", DEST_PORT)
    await b.key("Tab")
    await b.wait_for("Reset dport to default", "button")
    await b.shot("12-studio-dport-changed")
    await b.click("Save", "button")
    await b.wait_for("Edit packet", "button")
    await b.click("Properties", "[role=tab]")
    await b.fill("Rate", "1000")
    await b.shot("13-studio-properties-rate")
    await b.click("Save", "button", ".stream-detail")
    await asyncio.sleep(1)


async def step_assign(c):
    """14-15: Use in Control…, port 0, Open Traffic tab: it holds the inspected profile; Assign, Start"""
    b = c.b
    await b.click("Use in Control…", "button")
    await b.wait_for("Use in Control", "[role=dialog]")
    await b.shot("14-studio-assign-to-port")
    await b.click("Open Traffic tab", "button", "[role=dialog]")
    await b.wait_gone("Use in Control", "[role=dialog]")
    await b.wait(inv.SOME_TABLE, 20, "streams table")
    await b.fill("Multiplier", "1")    # a factor: the stream's own 1000 pps, not Part 1's 1 kpps still there
    await b.click("Assign", "button", "[role=tabpanel]")
    await asyncio.sleep(1.5)
    await b.shot("15-studio-inspected-assigned")
    await b.click("Start", "button", "[role=tabpanel]")
    await b.click("Control", "[role=tab]")
    await tx_chip(b)
    await b.shot("16-studio-tx-chip")
    await rates(c)
    await b.shot("17-studio-dashboard")
    await b.click("Control", "a")
    await b.click("~Port 0", "[role=treeitem]")
    await b.click("Stop", "button", "[role=toolbar]")
    await b.click("Release all", "button", "[role=toolbar]")
    await b.wait(f"!{inv.FOOTER}.includes('you own 0')", 20, "ports released")


STEPS = [("connect", step_connect), ("acquire", step_acquire), ("config", step_config),
         ("stock", step_stock), ("stock_run", step_stock_run), ("studio", step_studio),
         ("assign", step_assign)]


if __name__ == "__main__":
    sys.exit(inv.main(steps=STEPS, stage=stage_trex_tree, out=inv.REPO / "docs/howto/screens", doc=__doc__))
