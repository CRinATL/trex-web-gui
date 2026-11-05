from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: allocates 2 GiB in one piece, which the worker's address-space limit refuses
        when it is set below that (a negative control beside hang.py)."""
        hog = bytearray(2 * 1024**3)
        return [] if hog else []

    def get_profile(self, tunables, **kwargs):
        """The EMU entry point (emu_load): runs get_streams, then returns an empty EMU profile."""
        self.get_streams(tunables, **kwargs)
        from trex.emu.trex_emu_profile import EMUProfile
        return EMUProfile()


def register():
    """The loader's entry point: the profile object."""
    return P()
