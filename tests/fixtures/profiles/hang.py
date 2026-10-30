import time

from trex_stl_lib.api import *


class P:
    def get_streams(self, tunables, **kwargs):
        """Defines no traffic: sleeps an hour instead of returning."""
        time.sleep(3600)


def register():
    """The loader's entry point: the profile object."""
    return P()
