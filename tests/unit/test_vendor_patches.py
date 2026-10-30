import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from support import PIN_FILE, ROOT, VENDOR, pinned_sha, run_vendor_python, warnings_in

BuildVendor = Callable[[list[str]], Path]

IMPORT_ALL = "import trex.stl.api, trex.scapy_server.scapy_service\n"


def test_pin_records_sha_and_all_patches() -> None:
    pin = json.loads((VENDOR / "TREX_PIN").read_text())
    assert pin == json.loads(PIN_FILE.read_text())                  # copy in _vendor matches the record
    assert f"--sha {pinned_sha()}" in (ROOT / "Makefile").read_text()  # and the record matches the Makefile
    assert [p[:2] for p in pin["patches"]] == [f"{n:02d}" for n in range(1, 14)]


def test_vendor_is_reproducible(build_vendor: BuildVendor) -> None:  # canon:TSK-5
    again = build_vendor([])
    assert (again / "MANIFEST.sha256").read_text() == (VENDOR / "MANIFEST.sha256").read_text()


# 01: TREX_SYSTEM_LIBS lets the venv supply zmq and yaml
CODE_01 = IMPORT_ALL + "import zmq, yaml, zmq.asyncio; print('external_libs' in zmq.__file__, " \
                       "'external_libs' in yaml.__file__)"

def test_01_venv_libs(build_vendor: BuildVendor) -> None:
    assert run_vendor_python(VENDOR, CODE_01).stdout.strip() == "False False"
    neg = run_vendor_python(build_vendor(["01"]), CODE_01)
    assert "Unable to find required external library: 'yaml'" in neg.stderr   # the trim (P6)


def test_trimmed_libs_are_gone() -> None:
    """The bundled pyzmq-ctypes and pyyaml-3.11 are not vendored, and no .so remains (P6)."""
    assert not (VENDOR / "external_libs" / "pyzmq-ctypes").exists()
    assert not (VENDOR / "external_libs" / "pyyaml-3.11").exists()
    assert sorted([*VENDOR.rglob("*.so"), *VENDOR.rglob("*.so.*")]) == []


def test_import_needs_system_libs_after_the_trim() -> None:
    """Negative control of the trim: without TREX_SYSTEM_LIBS the client cannot find yaml (canon:VER-2)."""
    r = run_vendor_python(VENDOR, IMPORT_ALL, system_libs=False)
    assert "Unable to find required external library: 'yaml'" in r.stderr


# 02: load_yaml works with PyYAML 6
CODE_02 = IMPORT_ALL + """
import os, tempfile, yaml
from trex.stl.api import *
p = STLProfile([STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()), mode=STLTXCont(pps=5))])
f = tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False); yaml.dump(p.to_json(), f); f.close()
print(STLProfile.load_yaml(f.name).to_json() == p.to_json())
os.unlink(f.name)
"""

def test_02_safe_load(build_vendor: BuildVendor) -> None:
    assert run_vendor_python(VENDOR, CODE_02).stdout.strip() == "True"
    neg = run_vendor_python(build_vendor(["02"]), CODE_02)
    assert "Loader" in neg.stderr          # PyYAML 6: load() missing 1 required positional argument: 'Loader'


# 03: scapy.themes imports on Python >= 3.13 (no cgi module)

def _py313() -> str:
    """The path of uv's Python 3.13, for the patch-03 control run; skips the test when uv has none."""
    r = subprocess.run(["uv", "python", "find", "3.13"], check=False, capture_output=True, text=True)
    if r.returncode:
        pytest.skip("uv Python 3.13 not installed; cannot run patch-03 control")
    return r.stdout.strip()

def test_03_cgi_shim(build_vendor: BuildVendor) -> None:
    code = "import trex\nimport scapy.themes; print('ok')"     # the 3.13 venv-free run
    py = _py313()
    assert run_vendor_python(VENDOR, code, python=py).stdout.strip() == "ok"   # zmq/yaml are not needed here
    neg = run_vendor_python(build_vendor(["03"]), code, python=py)
    assert "No module named 'cgi'" in neg.stderr


# 04: no SyntaxWarning in the vendored trex package

def test_04_no_syntax_warnings(build_vendor: BuildVendor) -> None:
    assert warnings_in(VENDOR / "trex") == 0
    assert warnings_in(build_vendor(["04"]) / "trex") == 8


# 05: a subscriber crash does not kill the process
CODE_05 = IMPORT_ALL + """
from trex.stl.api import STLClient
c = STLClient(verbose_level='none')
try:
    raise ValueError('seq gap')
except ValueError as e:
    c.ctx.event_handler.on_event('subscriber crashed', e)
print('alive')
"""

def test_05_no_sigterm(build_vendor: BuildVendor) -> None:
    r = run_vendor_python(VENDOR, CODE_05)
    assert (r.returncode, r.stdout.strip()) == (0, "alive")
    neg = run_vendor_python(build_vendor(["05"]), CODE_05)
    assert neg.returncode == -15                               # SIGTERM, as upstream does


# 05 (second site): mark_for_disconnect while an API call has SIGINT-on-loss enabled
CODE_05_SIGINT = IMPORT_ALL + """
from trex.stl.api import STLClient
c = STLClient(verbose_level='none')
c.conn.sigint_on_conn_lost_enable()          # what client_api does on entry to every call
c.conn.mark_for_disconnect('lost')
import time; time.sleep(0.2)                 # give a pending signal time to land
print('alive')
"""

def test_05_no_sigint(build_vendor: BuildVendor) -> None:
    r = run_vendor_python(VENDOR, CODE_05_SIGINT)
    assert (r.returncode, r.stdout.strip()) == (0, "alive")
    neg = run_vendor_python(build_vendor(["05"]), CODE_05_SIGINT)
    assert "KeyboardInterrupt" in neg.stderr                   # SIGINT, as upstream does


# 06: latency_stats dispatch reaches its own handler
CODE_06 = IMPORT_ALL + """
from trex.common.trex_subscriber import TRexSubscriber
print(hasattr(TRexSubscriber, 'handle_latency_stats_update'))
"""

def test_06_latency_handler(build_vendor: BuildVendor) -> None:
    assert run_vendor_python(VENDOR, CODE_06).stdout.strip() == "True"
    assert run_vendor_python(build_vendor(["06"]), CODE_06).stdout.strip() == "False"


# 07: an AssertionError in the field engine comes back as an error string
CODE_07 = IMPORT_ALL + """
from trex.scapy_server.scapy_service import Scapy_service
s = Scapy_service(); h = s.get_version_handler('1', '02')
def boom(*a): raise AssertionError('bad fe')
s._generate_vm_instructions = boom
r = s.build_pkt_ex(h, [{'id': 'Ether'}, {'id': 'IP'}], {'field_engine': {}})
print(r['field_engine']['error'])
"""

def test_07_assert_message(build_vendor: BuildVendor) -> None:
    assert run_vendor_python(VENDOR, CODE_07).stdout.strip().endswith("bad fe")
    neg = run_vendor_python(build_vendor(["07"]), CODE_07)
    assert "'AssertionError' object has no attribute 'message'" in neg.stderr


# 08: set_port_attr's link/LED/flow-control arguments reach the wire
CODE_08 = IMPORT_ALL + """
from trex.stl.trex_stl_port import STLPort
from trex.common.trex_types import RC_OK
p = object.__new__(STLPort)
p.port_id, p.handler, sent = 0, 'h', []
p.is_acquired = lambda: True
p.transmit = lambda method, params: sent.append(params) or RC_OK()
p.sync = lambda: RC_OK()
p.set_attr(link_up=False, led_on=True, flow_ctrl=2)
print(sorted(sent[0]['attr']))
"""

def test_08_set_attr_kwargs(build_vendor: BuildVendor) -> None:
    # reset(restart=True) restores the link through this set_attr (trex_stl_client.py:387). A full reset
    # cannot run against the fake: trex_stl_client.py:369-371 raises when the recorded port does not
    # support link changes.
    assert (run_vendor_python(VENDOR, CODE_08).stdout.strip()
            == "['flow_ctrl_mode', 'led_status', 'link_status']")
    assert run_vendor_python(build_vendor(["08"]), CODE_08).stdout.strip() == "[]"
    header = (ROOT / "backend" / "vendor" / "patches" / "08-set-attr-kwarg-names.patch").read_text().split(
        "diff -ruN")[0]
    header = " ".join(header.split())
    assert ("39a62671c" in header and "unfixed upstream as of the v3.08 pin" in header
            and "reset(restart=True)" in header)


# 09: an ICMP destination-unreachable record is built instead of raising AttributeError
CODE_09 = IMPORT_ALL + """
from types import SimpleNamespace as NS
from trex.common.services.trex_service_icmp import ServiceICMP
try:
    r = ServiceICMP.PINGRecord({'ICMP': NS(type=3), 'IP': NS(src='1.1.1.99')}, 0.0, 0.1)
    print(r.state, r)
except AttributeError as e:
    print('AttributeError', e)
"""

def test_09_icmp_dest_unreachable_name(build_vendor: BuildVendor) -> None:
    assert (run_vendor_python(VENDOR, CODE_09).stdout.strip()
            == "3 Reply from 1.1.1.99: Destination host unreachable")
    neg = run_vendor_python(build_vendor(["09"]), CODE_09)
    assert "AttributeError" in neg.stdout and "ICMP_TYPE_DEST_UNREACBLE" in neg.stdout


# 10: the client imports without the bundled trex-openssl (its OpenSSL 1.1 .so files are GPL-incompatible)
CODE_10 = IMPORT_ALL + "print('ok')"
CODE_10_WLC = IMPORT_ALL + """
try:
    import trex.stl.trex_stl_wlc
except ImportError as e:
    print('ImportError', e)
"""

def test_10_drop_trex_openssl(build_vendor: BuildVendor) -> None:
    assert not (VENDOR / "external_libs" / "trex-openssl").exists()
    assert run_vendor_python(VENDOR, CODE_10).stdout.strip() == "ok"
    wlc = run_vendor_python(VENDOR, CODE_10_WLC).stdout.strip()   # the only users: WLC/AP modules
    assert wlc.startswith("ImportError") and "trex_openssl" in wlc
    neg = run_vendor_python(build_vendor(["10"]), CODE_10)
    assert "Unable to find required external library: 'trex_openssl'" in neg.stderr


# 11: an EnumField reports type ENUM (the parenthesis in `len(values_dict) > 0`)
CODE_11 = IMPORT_ALL + """
import json
from scapy.all import ARP, Dot1Q
from trex.scapy_server.scapy_service import Scapy_service
s = Scapy_service()
d = s.get_definitions(s.get_version_handler('1', '02'), ['Dot1Q', 'ARP'])
print(json.dumps({p['id']: {f['id']: f.get('type') for f in p['fields']} for p in d['protocols']}))
"""

def test_11_enum_type(build_vendor: BuildVendor) -> None:
    got = json.loads(run_vendor_python(VENDOR, CODE_11).stdout.strip().splitlines()[-1])
    assert got["Dot1Q"]["type"] == "ENUM" and got["ARP"]["op"] == "ENUM"
    neg = json.loads(run_vendor_python(build_vendor(["11"]), CODE_11).stdout.strip().splitlines()[-1])
    assert neg["Dot1Q"]["type"] is None and neg["ARP"]["op"] is None            # canon:VER-2


# 12: STLProfile.from_json keeps a flow var's init_value (and a dec sweep with none still starts at max_value)
CODE_12 = IMPORT_ALL + """
import json
from trex.stl.api import *
def stream(**kw):
    vm = STLVM()
    vm.var(name='v', size=2, **kw)
    vm.write(fv_name='v', pkt_offset='UDP.sport')
    return STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP(), vm=vm), mode=STLTXCont(pps=5))
def init(p):
    return [i['init_value'] for i in p.to_json()[0]['vm']['instructions'] if i['type'] == 'flow_var'][0]
p = STLProfile([stream(op='inc', min_value=1, max_value=100, init_value=50),
                stream(op='dec', min_value=1, max_value=100)])
back = STLProfile.from_json(p.to_json())
print(json.dumps([init(STLProfile([s])) for s in p.get_streams()]),
      json.dumps([init(STLProfile([s])) for s in back.get_streams()]))
"""

def test_12_flow_var_init_value(build_vendor: BuildVendor) -> None:
    last = lambda path: run_vendor_python(path, CODE_12).stdout.strip().splitlines()[-1]   # noqa: E731
    # An explicit init survives; the dec default is max_value. canon:VER-2: unpatched, init is min_value.
    assert last(VENDOR) == "[50, 100] [50, 100]"
    assert last(build_vendor(["12"])) == "[50, 100] [1, 100]"


# 13: no return or break inside finally, which Python 3.14 compiles with a SyntaxWarning (PEP 765)

def test_13_py314_finally_flow(build_vendor: BuildVendor) -> None:
    if sys.version_info < (3, 14):
        pytest.skip("the SyntaxWarning exists from Python 3.14; test_vendor_tree.py covers 3.12 and 3.13")
    assert warnings_in(build_vendor(["13"]) / "trex") == 3     # the three sites the patch restructures
