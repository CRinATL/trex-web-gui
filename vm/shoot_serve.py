"""`trex-web-gui serve` for the screenshot scripts, under the neutral user name `demo`.

The TRex client names its session after the login of the process's effective uid (the vendored
`trex.utils.common.get_current_user`, read once when the client module is imported), and the Control tab
shows it as the ports' Owner. The shots are published, so they must not carry the shooter's login: this
wrapper makes the lookup answer `demo` for this process's own uid, and sets USER and LOGNAME for
`getpass.getuser()`, then runs the installed CLI unchanged. vm/shoot_inventory.py and vm/shoot_howto.py
start the backend through it:

    backend/.venv/bin/python -I -B vm/shoot_serve.py serve --port … --data-dir … --server …
"""
import os
import pwd
import sys

NAME = "demo"
_getpwuid = pwd.getpwuid


def getpwuid(uid):
    """The real entry, with `demo` as the name when it is this process's own uid."""
    p = _getpwuid(uid)
    return pwd.struct_passwd((NAME, *tuple(p)[1:])) if uid == os.geteuid() else p


pwd.getpwuid = getpwuid
os.environ.update(USER=NAME, LOGNAME=NAME)

from trex_web_gui.cli import entry  # noqa: E402  (after the patch, so the client module reads `demo`)

sys.exit(entry())
