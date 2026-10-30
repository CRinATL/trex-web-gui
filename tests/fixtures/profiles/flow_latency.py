import argparse

from trex_stl_lib.api import *


class STLS1:
    """Two streams on one UDP packet: pg_id 5 flow stats at 1000 pps,
    pg_id 7 flow latency stats at 100 pps."""

    def get_streams(self, tunables, **kwargs):
        """The two streams of the class docstring (pg_id 5 flow stats, pg_id 7 latency stats) on a 40-byte
        UDP payload packet 16.0.0.1 -> 48.0.0.1; accepts no tunables."""
        parser = argparse.ArgumentParser(description=f'Argparser for {os.path.basename(__file__)}',
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.parse_args(tunables)
        pkt = STLPktBuilder(pkt=Ether() / IP(src="16.0.0.1", dst="48.0.0.1") / UDP(dport=12, sport=1025)
                            / (40 * 'x'))
        return [STLStream(packet=pkt, mode=STLTXCont(pps=1000), flow_stats=STLFlowStats(pg_id=5)),
                STLStream(packet=pkt, mode=STLTXCont(pps=100), flow_stats=STLFlowLatencyStats(pg_id=7))]


# dynamic load - used for trex console or simulator
def register():
    return STLS1()
