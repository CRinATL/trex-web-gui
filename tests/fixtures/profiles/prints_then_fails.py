from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: prints "diagnostic-from-profile", then raises
        ValueError("fails-after-print")."""
        print("diagnostic-from-profile")
        raise ValueError("fails-after-print")


def register():
    """The loader's entry point: the profile object."""
    return P()
