import os
import subprocess

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """One continuous Ether/IP/UDP stream, after starting a `sleep 30` child in the worker's process group
        and writing its pid to child.pid beside this file."""
        child = subprocess.Popen(["sleep", "30"])        # same process group; close_fds drops the result pipe
        with open(os.path.join(os.path.dirname(__file__), "child.pid"), "w") as f:
            f.write(str(child.pid))
        return [STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()), mode=STLTXCont())]


def register():
    """The loader's entry point: the profile object."""
    return P()
