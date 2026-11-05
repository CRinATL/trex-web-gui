import argparse

from trex.emu.api import *


class Prof:
    """Two namespaces (vport 0 and 1, no VLAN) of --clients clients each, with the arp and icmp plugins."""

    def get_profile(self, tunables, **kwargs):
        """The profile for the tunables: --vports namespaces on vports 0.., --clients clients in each."""
        parser = argparse.ArgumentParser(description='Two namespaces of arp and icmp clients',
                                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument('--clients', type=int, default=3, help='Clients in each namespace')
        parser.add_argument('--vports', type=int, default=2, help='Namespaces, on vports 0 up')
        args = parser.parse_args(tunables)
        plugs = {'icmp': {}, 'arp': {'enable': True}}
        spaces = []
        for v in range(args.vports):
            ns = EMUNamespaceObj(ns_key=EMUNamespaceKey(vport=v), def_c_plugs=plugs)
            for j in range(args.clients):
                # MAC 00:00:00:70:<v>:<j+1>, IPv4 1.1.<v+1>.<j+3>; a count past 255 carries up
                mac = [0, 0, 0, 0x70 + (j + 1) // 256, v, (j + 1) % 256]
                ip = list((0x01010103 + (v << 8) + j).to_bytes(4, 'big'))
                dg = list((0x01010101 + (v << 8)).to_bytes(4, 'big'))
                ns.add_clients(EMUClientObj(mac=mac, ipv4=ip, ipv4_dg=dg))
            spaces.append(ns)
        return EMUProfile(ns=spaces, def_ns_plugs=plugs)


def register():
    """The loader's entry point: the profile object."""
    return Prof()
