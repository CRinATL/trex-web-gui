"""docs/howto-first-stream.md: its shots exist and are all used, it states the loop before step 1 and both
ways port 1 accepts the frames, and the README and the GUI guide link it."""
import re

from support import ROOT

HOWTO = ROOT / "docs" / "howto-first-stream.md"
SCREENS = ROOT / "docs" / "howto" / "screens"


def howto_text() -> str:
    """The HOWTO's text (a missing file fails the test that reads it)."""
    assert HOWTO.is_file(), f"{HOWTO} does not exist"
    return HOWTO.read_text()


def test_howto_images_exist_and_are_used() -> None:
    """Every image the HOWTO links exists under docs/howto/screens/, and every PNG there is linked."""
    linked = set(re.findall(r"!\[[^\]]*\]\(howto/screens/([^)\s]+)\)", howto_text()))
    assert linked, "the HOWTO links no image under howto/screens/"
    on_disk = {p.name for p in SCREENS.glob("*.png")}
    assert linked - on_disk == set(), "linked but missing"
    assert on_disk - linked == set(), "in docs/howto/screens/ but not linked"


def test_howto_states_the_loop() -> None:
    """A line starting `> **This example's setup:**` names ports 0 and 1 cabled to each other and comes before
    step 1; both ways port 1 accepts the frames are named."""
    lines = howto_text().splitlines()
    setup = [i for i, line in enumerate(lines) if line.startswith("> **This example's setup:**")]
    assert len(setup) == 1, "expected exactly one setup box"
    first_step = next(i for i, line in enumerate(lines) if re.match(r"^1\. ", line))
    assert setup[0] < first_step
    box = []
    for line in lines[setup[0]:]:
        if not line.startswith(">"):
            break
        box.append(line)
    text = " ".join(box)
    assert re.search(r"ports? 0 and (port )?1", text) and "cabled to each other" in text
    body = "\n".join(lines)
    assert "Destination MAC" in body and "Promiscuous" in body


def test_howto_is_linked() -> None:
    """README.md and docs/gui-guide.md link the HOWTO (the guide by its relative path)."""
    assert "docs/howto-first-stream.md" in (ROOT / "README.md").read_text()
    assert "howto-first-stream.md" in (ROOT / "docs" / "gui-guide.md").read_text()
