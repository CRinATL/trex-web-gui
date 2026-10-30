import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { CAP_REASON, PGID_PALETTE } from '../pgidPalette'
import { pgidStore } from '../pgidStats'
import { wirePgid } from '../testPgid'
import { PgidSelector } from './PgidSelector'

const reset = () => { pgidStore.reset(); pgidStore.track('streams', []); pgidStore.track('latency', []) }
beforeEach(reset)
afterEach(reset)

const ids = (n: number) => Array.from({ length: n }, (_, i) => i + 1)
function Harness({ list }: { list: number[] }) {
  return (
    <PgidSelector tab="streams" ids={list}>
      {(s) => (
        <ul>
          {list.map((id) => (
            <li key={id}>
              <input type="checkbox" aria-label={`pg ${id}`} checked={s.isSelected(id)} disabled={!s.isSelected(id) && s.atCap}
                title={!s.isSelected(id) && s.atCap ? CAP_REASON : undefined} onChange={() => s.toggle(id)} />
              <span data-testid={`c${id}`}>{s.selected.find((p) => p.id === id)?.color ?? ''}</span>
            </li>
          ))}
        </ul>
      )}
    </PgidSelector>
  )
}
const checked = () => screen.getAllByRole('checkbox').filter((c) => (c as HTMLInputElement).checked).map((c) => c.getAttribute('aria-label'))

describe('PgidSelector', () => {
  it('selects the first 4 on first sight and tracks them in the store', () => {
    const m = wirePgid('tx')
    m.flow['5'].tx_pps.total = 1
    render(<Harness list={ids(10)} />)
    expect(checked()).toEqual(['pg 1', 'pg 2', 'pg 3', 'pg 4'])
    // tracked: a push now records history for pg_id 1..4 only
    const msg = wirePgid('tx')
    const tpl = msg.flow['5']
    msg.flow = Object.fromEntries(ids(10).map((i) => [String(i), JSON.parse(JSON.stringify(tpl))]))
    msg.ver_id = Object.fromEntries(ids(10).map((i) => [String(i), 0]))
    msg.active.flow_stats = ids(10)
    act(() => pgidStore.push(msg))
    expect(pgidStore.series(4, 'tx_pps', 60)).toHaveLength(1)
    expect(pgidStore.series(5, 'tx_pps', 60)).toHaveLength(0)
  })
  it('does not select again when more ids appear later', () => {
    const { rerender } = render(<Harness list={ids(2)} />)
    rerender(<Harness list={ids(6)} />)
    expect(checked()).toEqual(['pg 1', 'pg 2'])
  })
  it('caps at 8: the 9th checkbox is disabled with a reason', async () => {
    render(<Harness list={ids(10)} />)
    for (const i of [5, 6, 7, 8]) await userEvent.click(screen.getByRole('checkbox', { name: `pg ${i}` }))
    expect(checked()).toHaveLength(8)
    const ninth = screen.getByRole('checkbox', { name: 'pg 9' })
    expect(ninth).toBeDisabled()
    expect(ninth).toHaveAttribute('title', CAP_REASON)
    await userEvent.click(screen.getByRole('checkbox', { name: 'pg 1' }))
    expect(screen.getByRole('checkbox', { name: 'pg 9' })).toBeEnabled()
  })
  it('keeps a pg_id colour while it stays selected: a deselect repaints no other', async () => {
    render(<Harness list={ids(6)} />)
    const before = [1, 2, 3, 4].map((i) => screen.getByTestId(`c${i}`).textContent)
    await userEvent.click(screen.getByRole('checkbox', { name: 'pg 2' }))
    expect(screen.getByTestId('c1').textContent).toBe(before[0])
    expect(screen.getByTestId('c3').textContent).toBe(before[2])
    expect(screen.getByTestId('c4').textContent).toBe(before[3])
    await userEvent.click(screen.getByRole('checkbox', { name: 'pg 5' }))
    expect(screen.getByTestId('c5').textContent).toBe(before[1])   // takes the freed colour
    expect(PGID_PALETTE).toHaveLength(8)
  })
  it('drops a pg_id that is no longer in ids', () => {
    const { rerender } = render(<Harness list={ids(4)} />)
    rerender(<Harness list={[1, 3, 4]} />)
    expect(checked()).toEqual(['pg 1', 'pg 3', 'pg 4'])
  })
  it('a deselected seeded id stays deselected across later pushes', async () => {
    const m = wirePgid('tx')
    m.flow = { '1': m.flow['5'], '2': m.flow['5'], '3': m.flow['5'], '4': m.flow['5'] }
    m.active.flow_stats = [1, 2, 3, 4]
    const { rerender } = render(<Harness list={ids(4)} />)
    await userEvent.click(screen.getByRole('checkbox', { name: 'pg 2' }))
    rerender(<Harness list={ids(4)} />)
    rerender(<Harness list={[...ids(4)]} />)
    expect(checked()).toEqual(['pg 1', 'pg 3', 'pg 4'])
  })
  it('seeds the first 4 afresh after the ids go empty and come back', () => {
    const { rerender } = render(<Harness list={ids(6)} />)
    rerender(<Harness list={[]} />)
    rerender(<Harness list={[11, 12, 13, 14, 15, 16]} />)
    expect(checked()).toEqual(['pg 11', 'pg 12', 'pg 13', 'pg 14'])
  })
  it('frees the colour of an id that left', async () => {
    const { rerender } = render(<Harness list={ids(4)} />)
    const c4 = screen.getByTestId('c4').textContent
    rerender(<Harness list={[1, 2, 3, 5]} />)
    expect(checked()).toEqual(['pg 1', 'pg 2', 'pg 3'])
    rerender(<Harness list={ids(5)} />)
    expect(checked()).toEqual(['pg 1', 'pg 2', 'pg 3'])   // 4 came back unpicked
    // 4 left, so its colour is free: the next id picked takes it.
    await userEvent.click(screen.getByRole('checkbox', { name: 'pg 5' }))
    expect(screen.getByTestId('c5').textContent).toBe(c4)
  })
})
