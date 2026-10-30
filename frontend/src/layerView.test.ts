import { describe, expect, it } from 'vitest'
import { layerView } from './layerView'
import { wireLayerPort } from './testWire'

// Every view is a PortView the backend published (tests/contract/test_wire_fixtures.py); 52:52:18:89:91:ea is port 1's hw_mac.
const PEER = '52:52:18:89:91:ea'
const SRC = '8e:4e:97:11:bf:a8'

describe('layerView mirrors trex_port.py:705-739', () => {
  it('test_layerView_l2: IPv4 state none is Ethernet mode with the configured destination', () => {
    expect(layerView(wireLayerPort('l2'))).toEqual({
      mode: 'L2', srcMac: SRC, dstMac: PEER, srcIpv4: null, dstIpv4: null, arp: null, vlan: [], ipv6: 'off' })
  })
  it('test_layerView_l3_resolved: the MAC is the ARP answer', () => {
    expect(layerView(wireLayerPort('l3_resolved'))).toEqual({
      mode: 'L3', srcMac: SRC, dstMac: PEER, srcIpv4: '1.1.1.1', dstIpv4: '2.2.2.2', arp: 'resolved', vlan: [], ipv6: 'off' })
  })
  it('test_layerView_l3_unresolved: the Ethernet destination is not valid, so no MAC is shown', () => {
    // the server keeps the old ether.dst but marks it unconfigured; showing it would present a stale MAC as the ARP result
    expect(layerView(wireLayerPort('l3_unresolved'))).toEqual({
      mode: 'L3', srcMac: SRC, dstMac: null, srcIpv4: '1.1.1.1', dstIpv4: '1.1.1.99', arp: 'unresolved', vlan: [], ipv6: 'off' })
  })
  it('test_layerView_vlan_two_tags: outer tag first', () => {
    expect(layerView(wireLayerPort('vlan_two_tags')).vlan).toEqual([10, 20])
  })
  it('test_layerView_ipv6_auto: enabled with no source reads auto', () => {
    expect(layerView(wireLayerPort('ipv6_auto')).ipv6).toBe('auto')
  })
  it('test_layerView_ipv6_address: enabled with a source reads that address', () => {
    expect(layerView(wireLayerPort('ipv6_address')).ipv6).toBe('fe80::5052:18ff:fe89:91ea')
  })
  it('a port with no layer_cfg yet reads as an unconfigured Ethernet port', () => {
    expect(layerView(wireLayerPort('l2', { layer_cfg: {} }))).toEqual({
      mode: 'L2', srcMac: '', dstMac: null, srcIpv4: null, dstIpv4: null, arp: null, vlan: [], ipv6: 'off' })
  })
})
