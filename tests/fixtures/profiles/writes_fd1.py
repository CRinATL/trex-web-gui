import os
import sys

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """One continuous Ether/IP/UDP stream, after writing "junk-line" to sys.__stdout__ and raw bytes
        (no newline) to fd 1."""
        print("junk-line", file=sys.__stdout__)
        sys.__stdout__.flush()
        os.write(1, b"raw-junk-no-newline")
        return [STLStream(packet=STLPktBuilder(pkt=Ether()/IP()/UDP()), mode=STLTXCont())]


def register():
    """The loader's entry point: the profile object."""
    return P()
