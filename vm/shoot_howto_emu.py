"""Shoot the EMU HOWTO's screens (docs/howto/emu-screens/*.png): docs/howto-emu.md, step by step, against the
real TRex guest running TRex with `--emu`.

It is vm/shoot_howto.py for the EMU page: the same backend child, headless Chrome, 1400x900 Light-theme shots
at device scale 2, scratch handling and `run()`/`main()` from vm/shoot_inventory.py (read that module's
docstring). The guest must be up and in EMU mode, frontend/dist built, ports 8766 and 9344 free:

    vm/demo.sh up && vm/mode.sh emu
    systemd-run --user --scope -q -p MemoryMax=4G -p MemorySwapMax=0 -- \\
        backend/.venv/bin/python -I -B vm/shoot_howto_emu.py <scratch dir> [--out DIR] [--until STEP]
    vm/mode.sh stateless && vm/demo.sh down

`--out` defaults to docs/howto/emu-screens (its own directory: a complete run replaces every PNG of --out).
The backend runs on this machine and EMU's port is forwarded from the guest to 127.0.0.1 (4510 on jammy; for
another guest export the ports first, eval "$(TREX_VM_GUEST=<guest> vm/guests.sh)", and the script reads
TREX_VM_EMU_PORT), so the page connects to 127.0.0.1:<that port>. The script stages TRex's own `simple_emu.py`
(from $TREX_SRC/scripts/emu/, default the Makefile's clone) into <scratch>/trex/v3.08/emu/ and sets
`preferences.workspace_dir` to <scratch>/trex/v3.08 through the API, so the Browse… dialog opens there with
`emu/` one click away and no shot lists $HOME. The script starts from an empty EMU server (it removes what an
earlier run left) and leaves it empty. The shots show the scratch path (Browse…, the profile input, the load
box), so give a short one such as /var/tmp/shoot-<date>/howto-emu-scratch. STEPS lists the steps in order.
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
PROFILE = "simple_emu.py"
EMU_PORT = os.environ.get("TREX_VM_EMU_PORT", "4510")
NO_NAMESPACES = "The server has no namespaces."


def stage_trex_tree(scratch):
    """Copy TRex's stock `simple_emu.py` into <scratch>/trex/v3.08/emu/; return <scratch>/trex/v3.08."""
    root = scratch / "trex" / "v3.08"
    (root / "emu").mkdir(parents=True)
    shutil.copy(TREX_SRC / "scripts" / "emu" / PROFILE, root / "emu" / PROFILE)
    return root


async def step_page(c):
    """01: log in, open the EMU page and fill in the host and port; point Browse… at the staged TRex tree."""
    b = c.b
    await b.goto(f"/api/auth/login?token={c.be.token}")
    await b.wait_for("EMU", "a")
    await b.js("localStorage.setItem('trex-web-gui.theme', 'light')")
    c.api = Api(c.be.token)
    cur = c.api.get("/api/settings")["settings"]
    cur["preferences"]["workspace_dir"] = str(c.scratch / "trex" / "v3.08")
    check_status(c.api.h.put("/api/settings", json=cur), "PUT /api/settings")
    await b.goto("/emu")
    await b.fill("Host", "127.0.0.1", ".emu-page")
    await b.fill("Port", EMU_PORT, ".emu-page")
    await b.shot("01-emu-page")


async def step_connect(c):
    """02: Connect; the status line reads Connected and the server holds no namespaces (an earlier run's
    leftovers are removed first, through the page's own Remove all…)."""
    b = c.b
    await b.click("Connect", "button", ".emu-page")
    await b.wait_text("Connected", 40)
    await b.wait("document.body.innerText.includes('Clients of') || document.body.innerText.includes("
                 f"{inv.js(NO_NAMESPACES)})", 40, "the namespaces listing")
    if not await b.js(f"document.body.innerText.includes({inv.js(NO_NAMESPACES)})"):
        await b.click("Remove all…", "button", ".emu-page")
        await b.wait_for("Remove everything", "h2", "[role=dialog]")
        await b.wait_text("On the server now", 20)    # the second box shows only after the dialog's own read
        # Namespaces this app did not add need the dialog's second box ticked before Remove is allowed.
        await b.js("document.querySelectorAll('[role=dialog] input[type=checkbox]')"
                   ".forEach((x) => x.click())")
        await b.click("Remove", "button", "[role=dialog]")
        await b.wait_text(NO_NAMESPACES, 60)
    await asyncio.sleep(1)
    await b.shot("02-emu-connected")


async def step_browse(c):
    """03-04: Browse… (opens in the workspace), the emu/ directory, then the profile picked."""
    b = c.b
    await b.click("Browse…", "button", ".emu-profile")
    await b.wait_for("~emu/", "button", "[role=dialog]")
    await b.click("emu/", "button", "[role=dialog]")
    await b.wait_for(PROFILE, "button", "[role=dialog]")
    await b.shot("03-browse-emu")
    await b.click(PROFILE, "button", "[role=dialog]")
    await b.wait_gone("Pick an EMU profile", "[role=dialog]")


async def step_inspect(c):
    """04: Inspect: the tunables, and the namespace and client counts, plugins and vports."""
    b = c.b
    await b.click("Inspect", "button", ".emu-profile")
    await b.wait_text("client(s); plugins:", 40)
    print("inspected:", await b.js("document.querySelector('.emu-profile').innerText.replace(/\\s+/g, ' ')"),
          flush=True)
    await b.shot("04-profile-inspected")


async def step_load(c):
    """05-06: Load… asks first; Load; the namespace appears and the page says what this app loaded."""
    b = c.b
    await b.click("Load…", "button", ".emu-profile")
    await b.wait_for("Load the profile", "h2", "[role=dialog]")
    await b.shot("05-load-confirm")
    await b.click("Load", "button", "[role=dialog]")
    await b.wait_gone("Load the profile", "h2", "[role=dialog]")
    await b.wait_text("Loaded from this app", 120)
    await b.wait_for("~Clients of", "button", ".emu-page", 40)
    await asyncio.sleep(2)
    await b.shot("06-loaded-namespaces")


async def step_clients(c):
    """07: Clients of the namespace: the paged clients table."""
    b = c.b
    await b.click("~Clients of", "button", ".emu-namespaces")
    await b.wait_for("~Select client", "button", timeout=40)
    await asyncio.sleep(2)
    await b.tall_shot("07-clients", 1500)


async def step_counters(c):
    """08: the counters at Namespace scope (zeros hidden), the Counters section scrolled into view. (A client
    of this profile has every counter at zero, so the Client scope shows no table and is not shot.)"""
    b = c.b
    await b.click("Namespace", "button", "[aria-label='Counter scope']")
    await b.wait_text("Counters: port 0", 40)
    await b.wait_text("pktTxArpQuery", 40)
    await asyncio.sleep(3)
    counters = "document.querySelector('.emu-counters').innerText.replace(/\\s+/g, ' ')"
    print("ns counters:", await b.js(counters), flush=True)
    await b.js("document.querySelector('.emu-counters').scrollIntoView({block: 'start'})")
    await b.shot("08-namespace-counters")


async def step_remove(c):
    """09-10: Remove all… asks first; Remove; the server holds no namespaces; Disconnect."""
    b = c.b
    await b.click("Remove all…", "button", ".emu-profile")
    await b.wait_for("Remove everything", "h2", "[role=dialog]")
    await b.shot("09-remove-confirm")
    await b.click("Remove", "button", "[role=dialog]")
    await b.wait_gone("Remove everything", "h2", "[role=dialog]")
    await b.wait_text(NO_NAMESPACES, 120)
    await b.wait_gone("~Clients of port 0", "h2", ".emu-page", 20)
    await asyncio.sleep(1)
    await b.shot("10-removed")
    await b.click("Disconnect", "button", ".emu-page")
    # The status bar reads Disconnected throughout (the Control session's); read the EMU status line.
    await b.wait("document.querySelector('.emu-page').innerText.includes('Disconnected')", 20,
                 "EMU disconnected")


STEPS = [("page", step_page), ("connect", step_connect), ("browse", step_browse), ("inspect", step_inspect),
         ("load", step_load), ("clients", step_clients), ("counters", step_counters), ("remove", step_remove)]


if __name__ == "__main__":
    sys.exit(inv.main(steps=STEPS, stage=stage_trex_tree, out=inv.REPO / "docs/howto/emu-screens",
                      doc=__doc__))
