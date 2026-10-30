import { describe, expect, it } from 'vitest'
import { seriesById } from './chartSeries'
import type { Point } from './stats'

const p: Point = { ts: 1, tx_pps: 10, rx_pps: 20, tx_bps: 111, rx_bps: 222, tx_bps_L1: 333, rx_bps_L1: 444 }

describe('SERIES', () => {
  it('Tx bps L2 reads the tx field', () => {
    expect(seriesById('tx_bps_l2').value(p)).toBe(111)
    expect(seriesById('rx_bps_l2').value(p)).toBe(222)
  })
  it('Rx bps L1 reads rx_bps_L1', () => {
    expect(seriesById('rx_bps_l1').value(p)).toBe(444)
    expect(seriesById('tx_bps_l1').value(p)).toBe(333)
  })
  it('unusable readings become null', () => {
    expect(seriesById('tx_pps').value({ ts: 1, tx_pps: null })).toBeNull()
    expect(seriesById('tx_pps').value({ ts: 1, tx_pps: -1 })).toBeNull()
    expect(seriesById('tx_pps').value({ ts: 1 })).toBeNull()
  })
})
