import os
import time

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: forks a child that sleeps 30 s holding every inherited fd, writes its pid to
        child.pid beside this file, then sleeps an hour."""
        pid = os.fork()
        # child keeps every inherited fd, result pipe included
        if pid == 0:
            time.sleep(30)
            os._exit(0)
        with open(os.path.join(os.path.dirname(__file__), "child.pid"), "w") as f:
            f.write(str(pid))
        time.sleep(3600)

    def get_profile(self, tunables, **kwargs):
        """The EMU entry point (emu_load): runs get_streams, then returns an empty EMU profile."""
        self.get_streams(tunables, **kwargs)
        from trex.emu.trex_emu_profile import EMUProfile
        return EMUProfile()


def register():
    """The loader's entry point: the profile object."""
    return P()
