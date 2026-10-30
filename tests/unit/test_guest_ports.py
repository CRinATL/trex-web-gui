"""Every Python reader takes the guest's ports from vm/guestenv.py; none spells a guest port literally."""
import ast
from pathlib import Path

import pytest
from support import ROOT, guestenv

GUEST_INTS = {2222, 4500, 4501, 4510, 8090, 8765}
LOOPBACK_PORTS = ("127.0.0.1:4500", "127.0.0.1:4501")
SCANNED = sorted([*(ROOT / "tests" / "real").glob("*.py"),
                  *(p for p in (ROOT / "vm").glob("*.py") if p.name != "guestenv.py")])


def _docstrings(tree: ast.AST) -> set[int]:
    """ids of the docstring Constant nodes (first statement of a module, class or function)."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def test_no_literal_guest_port() -> None:
    """No guest port as an int constant, no ':4500'/':4501' string piece, outside docstrings."""
    bad = []
    for path in SCANNED:
        tree = ast.parse(path.read_text())
        skip = _docstrings(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Constant) or id(n) in skip:
                continue
            v = n.value
            is_port = isinstance(v, int) and not isinstance(v, bool) and v in GUEST_INTS
            is_piece = isinstance(v, str) and (v in (":4500", ":4501") or any(x in v for x in LOOPBACK_PORTS))
            if is_port or is_piece:
                bad.append(f"{path.relative_to(ROOT)}:{n.lineno} {v!r}")
    assert not bad, "literal guest ports:\n" + "\n".join(bad)


def test_every_stlclient_names_its_ports() -> None:
    """Each STLClient(...) call passes sync_port= and async_port=."""
    bad = []
    for path in SCANNED:
        for n in ast.walk(ast.parse(path.read_text())):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                kws = {k.arg for k in n.keywords}
                if name == "STLClient" and not {"sync_port", "async_port"} <= kws:
                    bad.append(f"{path.relative_to(ROOT)}:{n.lineno}")
    assert not bad, "STLClient without ports:\n" + "\n".join(bad)


def test_guestenv_defaults_to_jammy() -> None:
    """No guest, or jammy, with empty variables: jammy's values."""
    assert guestenv.ports({}) == (4501, 4500)
    assert guestenv.ports({"TREX_VM_GUEST": ""}) == (4501, 4500)
    assert guestenv.ports({"TREX_VM_GUEST": "jammy", "TREX_REAL_SYNC_PORT": ""}) == (4501, 4500)
    assert guestenv.vm_dir({}) == Path("/var/tmp/trex-vm")
    assert guestenv.hostname({}) == "trex-vm"


def test_guestenv_reads_the_exported_ports() -> None:
    """The exported variables win, as ints."""
    env = {"TREX_VM_GUEST": "resolute", "TREX_REAL_SYNC_PORT": "4601", "TREX_REAL_ASYNC_PORT": "4600"}
    assert guestenv.ports(env) == (4601, 4600)


def test_guestenv_refuses_a_guest_without_ports() -> None:
    """A non-jammy guest without its variables is refused with the eval hint."""
    hint = 'eval "$(TREX_VM_GUEST=resolute vm/guests.sh)"'
    one = {"TREX_VM_GUEST": "resolute", "TREX_REAL_SYNC_PORT": "4601"}
    for call in (lambda: guestenv.ports({"TREX_VM_GUEST": "resolute"}), lambda: guestenv.ports(one),
                 lambda: guestenv.vm_dir({"TREX_VM_GUEST": "resolute"}),
                 lambda: guestenv.hostname({"TREX_VM_GUEST": "resolute"})):
        with pytest.raises(guestenv.GuestEnvError) as e:
            call()
        assert hint in str(e.value)


def test_guestenv_refuses_one_port_variable_for_jammy() -> None:
    """Jammy with only one of the two port variables is refused, with the eval hint."""
    for one in ({"TREX_REAL_SYNC_PORT": "4601"}, {"TREX_REAL_ASYNC_PORT": "4600"}):
        with pytest.raises(guestenv.GuestEnvError, match=r"vm/guests\.sh"):
            guestenv.ports({"TREX_VM_GUEST": "jammy", **one})


def test_guestenv_refuses_a_non_numeric_port() -> None:
    """A port that is not a number is a GuestEnvError, not a bare ValueError."""
    with pytest.raises(guestenv.GuestEnvError, match="port numbers"):
        guestenv.ports({"TREX_REAL_SYNC_PORT": "abc", "TREX_REAL_ASYNC_PORT": "4600"})


def test_guestenv_refuses_an_environment_prepared_for_another_guest() -> None:
    """TREX_VM_CODENAME differing from the selected guest (jammy when empty) is refused by every reader."""
    ports = {"TREX_REAL_SYNC_PORT": "4501", "TREX_REAL_ASYNC_PORT": "4500",
             "TREX_VM_DIR": "/x", "TREX_VM_HOSTNAME": "h"}
    for env, prepared, selected in (
            ({"TREX_VM_CODENAME": "jammy", "TREX_VM_GUEST": "resolute"}, "jammy", "resolute"),
            ({"TREX_VM_CODENAME": "resolute", "TREX_VM_GUEST": ""}, "resolute", "jammy")):
        for call in (guestenv.ports, guestenv.vm_dir, guestenv.hostname):
            with pytest.raises(guestenv.GuestEnvError) as e:
                call({**ports, **env})
            assert prepared in str(e.value) and selected in str(e.value)
            assert "unset TREX_VM_CODENAME TREX_VM_DIR TREX_VM_DL TREX_VM_SHARED" in str(e.value)
    for ok in ({"TREX_VM_CODENAME": "resolute", "TREX_VM_GUEST": "resolute"}, {"TREX_VM_CODENAME": "jammy"}):
        assert guestenv.ports({**ports, **ok}) == (4501, 4500)
        assert guestenv.vm_dir({**ports, **ok}) == Path("/x")
        assert guestenv.hostname({**ports, **ok}) == "h"
