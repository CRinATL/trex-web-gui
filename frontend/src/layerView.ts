import type { PortView } from './api'

export interface LayerView {
  mode: 'L2' | 'L3'
  srcMac: string
  // L2: the configured destination. L3: the ARP answer once resolved (trex_port.py:722-726 shows ether.dst then), null while unresolved.
  dstMac: string | null
  srcIpv4: string | null
  dstIpv4: string | null
  arp: 'resolved' | 'unresolved' | null
  vlan: number[]
  ipv6: 'off' | 'auto' | string
}

// What the port's layer_cfg means, as TRex's own port status reads it (backend/trex_web_gui/_vendor/trex/common/trex_port.py:705-739).
export function layerView(p: PortView): LayerView {
  const { ether, ipv4, ipv6 } = p.layer_cfg
  const configured = ether?.state === 'configured'
  const common = { srcMac: ether?.src ?? '', vlan: p.vlan, ipv6: ipv6?.enabled ? (ipv6.src || 'auto') : 'off' }
  if (!ipv4 || ipv4.state === 'none') {
    return { ...common, mode: 'L2', dstMac: configured ? ether.dst : null, srcIpv4: null, dstIpv4: null, arp: null }
  }
  return { ...common, mode: 'L3', dstMac: ipv4.state === 'resolved' ? (ether?.dst ?? null) : null, srcIpv4: ipv4.src ?? null, dstIpv4: ipv4.dst ?? null, arp: ipv4.state }
}
