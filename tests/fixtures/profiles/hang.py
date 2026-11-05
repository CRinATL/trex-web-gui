import time

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: sleeps an hour instead of returning."""
        time.sleep(3600)

    def get_profile(self, tunables, **kwargs):
        """The EMU entry point (emu_load): runs get_streams, then returns an empty EMU profile."""
        self.get_streams(tunables, **kwargs)
        from trex.emu.trex_emu_profile import EMUProfile
        return EMUProfile()


def register():
    """The loader's entry point: the profile object."""
    return P()
