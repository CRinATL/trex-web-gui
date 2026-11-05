import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EmuMsg } from './api'
import { emuStore } from './emuStore'
import emuMsg from './__fixtures__/emuMsg.json'
import { useEmuPoll } from './useEmuPoll'

const connected = emuMsg.connected as EmuMsg
const disconnected = emuMsg.disconnected as EmuMsg

let docState: 'visible' | 'hidden' = 'visible'
const setDoc = (s: 'visible' | 'hidden') => { docState = s; act(() => { document.dispatchEvent(new Event('visibilitychange')) }) }
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

function Probe({ read, ms, dep }: { read: () => Promise<number>; ms: number; dep: number }) {
  const { data, error } = useEmuPoll(read, ms, [dep])
  return <p>{`data=${data} error=${error?.message ?? 'none'}`}</p>
}

beforeEach(() => {
  vi.useFakeTimers()
  docState = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => docState })
  act(() => emuStore.push(connected))
})
afterEach(() => { vi.useRealTimers() })

describe('useEmuPoll', () => {
  it('reads nothing until the state is connected, then reads at once and every interval', async () => {
    act(() => emuStore.push(disconnected))
    const read = vi.fn().mockResolvedValue(7)
    render(<Probe read={read} ms={5000} dep={0} />)
    await tick(20000)
    expect(read).not.toHaveBeenCalled()
    act(() => emuStore.push(connected))
    await tick(0)
    expect(read).toHaveBeenCalledTimes(1)
    expect(screen.getByText('data=7 error=none')).toBeInTheDocument()
    await tick(4999)
    expect(read).toHaveBeenCalledTimes(1)
    await tick(1)
    expect(read).toHaveBeenCalledTimes(2)
  })
  it('stops when the page is hidden or the state leaves connected, and resumes after', async () => {
    const read = vi.fn().mockResolvedValue(1)
    render(<Probe read={read} ms={1000} dep={0} />)
    await tick(0)
    setDoc('hidden')
    const n = read.mock.calls.length
    await tick(10000)
    expect(read).toHaveBeenCalledTimes(n)
    setDoc('visible')
    await tick(0)
    expect(read).toHaveBeenCalledTimes(n + 1)
    act(() => emuStore.push({ ...connected, state: 'lost' }))
    await tick(10000)
    expect(read).toHaveBeenCalledTimes(n + 1)
  })
  it('reads again at once when a dependency changes, and drops a reply from the old one', async () => {
    let first: (v: number) => void = () => undefined
    const read = vi.fn().mockImplementationOnce(() => new Promise<number>((r) => { first = r })).mockResolvedValue(2)
    const { rerender } = render(<Probe read={read} ms={5000} dep={0} />)
    await tick(0)
    rerender(<Probe read={read} ms={5000} dep={1} />)
    await tick(0)
    expect(read).toHaveBeenCalledTimes(2)
    await act(async () => { first(99); await Promise.resolve() })
    expect(screen.getByText('data=2 error=none')).toBeInTheDocument()
  })
  it('drops the old data at once when the dependency changes, and shows none if the new read fails', async () => {
    const read = vi.fn().mockResolvedValueOnce(5).mockRejectedValue(new Error('boom'))
    const { rerender } = render(<Probe read={read} ms={5000} dep={0} />)
    await tick(0)
    expect(screen.getByText('data=5 error=none')).toBeInTheDocument()
    rerender(<Probe read={read} ms={5000} dep={1} />)
    expect(screen.getByText('data=null error=none')).toBeInTheDocument()
    await tick(0)
    expect(screen.getByText('data=null error=boom')).toBeInTheDocument()
  })
  it('clears the data when the state leaves connected, but keeps it while the page is hidden', async () => {
    render(<Probe read={vi.fn().mockResolvedValue(4)} ms={1000} dep={0} />)
    await tick(0)
    setDoc('hidden')
    expect(screen.getByText('data=4 error=none')).toBeInTheDocument()
    setDoc('visible')
    act(() => emuStore.push({ ...connected, state: 'lost' }))
    expect(screen.getByText('data=null error=none')).toBeInTheDocument()
  })
  it('keeps the last data when a read fails and reports the error', async () => {
    const read = vi.fn().mockResolvedValueOnce(3).mockRejectedValue(new Error('boom'))
    render(<Probe read={read} ms={1000} dep={0} />)
    await tick(0)
    await tick(1000)
    expect(screen.getByText('data=3 error=boom')).toBeInTheDocument()
  })
})
