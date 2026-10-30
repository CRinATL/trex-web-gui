import sys

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """One continuous Ether/IP/UDP stream, after writing 5,000,000 "x" to sys.stdout."""
        sys.stdout.write("x" * 5_000_000)
        return [STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()), mode=STLTXCont())]


def register():
    """The loader's entry point: the profile object."""
    return P()
