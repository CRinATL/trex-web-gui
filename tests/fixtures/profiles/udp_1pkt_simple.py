import argparse

from trex_stl_lib.api import *


class STLS1:

    def create_stream (self):
        """One continuous UDP stream 16.0.0.1:1025 -> 48.0.0.1:12 with a 10-byte payload, at the default
        rate."""
        return STLStream(
            packet =
                    STLPktBuilder(
                        pkt = Ether()/IP(src="16.0.0.1",dst="48.0.0.1")/
                                UDP(dport=12,sport=1025)/(10*'x')
                    ),
             mode = STLTXCont())

    def get_streams (self, tunables, **kwargs):
        """That one stream; accepts no tunables."""
        parser = argparse.ArgumentParser(description=f'Argparser for {os.path.basename(__file__)}',
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.parse_args(tunables)
        # create 1 stream
        return [ self.create_stream() ]


# dynamic load - used for trex console or simulator
def register():
    return STLS1()



