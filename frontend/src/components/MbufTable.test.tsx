import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { wireUtil, type WireUtil } from '../testPgid'
import { utilStore } from '../utilStats'
import { MbufTable } from './MbufTable'

beforeEach(() => utilStore.reset())
afterEach(() => utilStore.reset())

const table = () => screen.getByRole('table', { name: 'Mbuf utilization' })
const heads = () => within(table()).getAllByRole('columnheader').map((h) => h.textContent)
const row = (name: string) => within(within(table()).getByRole('row', { name: new RegExp(`^${name}`) })).getAllByRole('cell').map((c) => c.textContent)

// Expected values: `to_table_mbuf` of the vendored client, fed the get_utilization result recorded at t=6.235 in
// tests/fixtures/p3a/rpc.jsonl (the sample utilMsg.json's tx was taken from). It prints
//            |   64b   |  128b   |  256b   |  512b   |  1024b  |  2048b  |  4096b  |   9kb   | RAM(MB)
//   Total:   |   81900 |   40950 |   16380 |   16380 |   16380 |   13023 |    1024 |    2048 |      89
//   Socket 0:|       0 |       2 |       0 |       0 |       0 |       0 |       0 |       0 |       0
//   Percent: |      0% |      0% |      0% |      0% |      0% |      0% |      0% |      0% |
describe('MbufTable', () => {
  it('shows the recorded tx sample as to_table_mbuf prints it', () => {
    utilStore.push(wireUtil('tx'))
    render(<MbufTable />)
    expect(heads()).toEqual(['', '64b', '128b', '256b', '512b', '1024b', '2048b', '4096b', '9kb', 'RAM (MB)'])
    expect(row('Total')).toEqual(['81,900', '40,950', '16,380', '16,380', '16,380', '13,023', '1,024', '2,048', '89'])
    expect(row('Socket 0 used')).toEqual(['0', '2', '0', '0', '0', '0', '0', '0', '0'])
    expect(row('Socket 0 percent')).toEqual(['0%', '0%', '0%', '0%', '0%', '0%', '0%', '0%', ''])
  })
  it('sorts the sizes by bytes, taking 9kb as 9000, whatever order the server sent them in', () => {
    const m = wireUtil('tx')
    const s = m.mbuf['cpu-socket-0']
    m.mbuf['cpu-socket-0'] = { '9kb': s['9kb'], '64b': s['64b'], '2048b': s['2048b'], '4096b': s['4096b'], '1024b': s['1024b'], '128b': s['128b'], '512b': s['512b'], '256b': s['256b'] }
    utilStore.push(m)
    render(<MbufTable />)
    expect(heads().slice(1, -1)).toEqual(['64b', '128b', '256b', '512b', '1024b', '2048b', '4096b', '9kb'])
  })
  // to_table_mbuf on the recorded sample with a second socket whose 128b free is 30000 and 9kb free is 1000:
  //   Total RAM 178 (the totals times 2 sockets); Socket 1 used 0 10950 0 0 0 0 0 1048, RAM 10; Percent 0% 26% ... 51%
  it('adds a Used and a Percent row per socket, and multiplies the Total RAM by the socket count', () => {
    const m: WireUtil = wireUtil('tx')
    const s1 = JSON.parse(JSON.stringify(m.mbuf['cpu-socket-0']))
    s1['128b'].free = 30000
    s1['9kb'].free = 1000
    m.mbuf['cpu-socket-1'] = s1
    utilStore.push(m)
    render(<MbufTable />)
    expect(row('Total').at(-1)).toBe('178')
    expect(row('Socket 0 used').at(-1)).toBe('0')
    expect(row('Socket 1 used')).toEqual(['0', '10,950', '0', '0', '0', '0', '0', '1,048', '10'])
    expect(row('Socket 1 percent')).toEqual(['0%', '26%', '0%', '0%', '0%', '0%', '0%', '51%', ''])
  })
  it('shows a dash, never NaN, for a null count or a zero total', () => {
    const m = wireUtil('tx')
    m.mbuf['cpu-socket-0']['64b'] = { free: null, total: null }
    m.mbuf['cpu-socket-0']['9kb'] = { free: 0, total: 0 }
    utilStore.push(m)
    render(<MbufTable />)
    expect(row('Total')[0]).toBe('–')
    expect(row('Total').at(-1)).toBe('–')
    expect(row('Socket 0 used')[0]).toBe('–')
    expect(row('Socket 0 percent').slice(0, 1).concat(row('Socket 0 percent').slice(7, 8))).toEqual(['–', '–'])
    expect(table().textContent).not.toMatch(/NaN|Infinity/)
  })
  it('says so when there is no sample yet, and follows the store', () => {
    render(<MbufTable />)
    expect(screen.getByText('No utilization data yet.')).toBeInTheDocument()
    act(() => utilStore.push(wireUtil('tx')))
    expect(row('Total')[0]).toBe('81,900')
  })
})
