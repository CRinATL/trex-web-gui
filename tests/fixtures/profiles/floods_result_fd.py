import os
import stat

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: floods each FIFO among fds 3..63 in turn (the worker's saved result
        pipe) with 64 KiB writes of "x" until a write fails (then it tries the next fd) or blocks on a full
        pipe; returns [] when none is left."""
        for fd in range(3, 64):                          # the worker saved the result pipe on one of these
            try:
                if stat.S_ISFIFO(os.fstat(fd).st_mode):
                    while True:
                        os.write(fd, b"x" * 65536)
            except OSError:
                continue
        return []

    def get_profile(self, tunables, **kwargs):
        """The EMU entry point (emu_load): runs get_streams, then returns an empty EMU profile."""
        self.get_streams(tunables, **kwargs)
        from trex.emu.trex_emu_profile import EMUProfile
        return EMUProfile()


def register():
    """The loader's entry point: the profile object."""
    return P()
