import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { StatsMsg } from './api'
import { statsStore } from './stats'
import { useStatsSelector } from './useStats'

const msg = (ts: number, cpu: number | null, pps: number | null): StatsMsg => ({
  type: 'stats', ts, interval_s: 1, global: { cpu_util: cpu, tx_pps: pps }, ports: {}, total: {},
})

beforeEach(() => { statsStore.onSession('connected'); statsStore.onSession('connecting') })

describe('useStatsSelector', () => {
  it('does not re-render when an unrelated key changes', () => {
    let renders = 0
    function Cpu() { renders++; const v = useStatsSelector((s) => s.latest?.global.cpu_util ?? null); return <span data-testid="v">{String(v)}</span> }
    render(<Cpu />)
    act(() => statsStore.push(msg(1, 5, 100)))
    expect(screen.getByTestId('v')).toHaveTextContent('5')
    const before = renders
    act(() => statsStore.push(msg(2, 5, 999)))
    expect(renders).toBe(before)
    act(() => statsStore.push(msg(3, 6, 999)))
    expect(renders).toBe(before + 1)
    expect(screen.getByTestId('v')).toHaveTextContent('6')
  })
})
