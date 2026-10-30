from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: raises ValueError("boom-from-profile") while the streams are being built."""
        raise ValueError("boom-from-profile")


def register():
    """The loader's entry point: the profile object."""
    return P()
