import { describe, expect, it } from 'vitest'
import { wirePort } from './testWire'

describe('wirePort: the shapes the backend really publishes', () => {
  it('a foreign client running only the named profile `bench`: TX, no default-profile entry', () => {
    const p = wirePort('foreign_bench_tx')
    expect(p.state).toBe('TX')
    expect(p.profiles).toEqual({ bench: 'TX' })
    expect(p.profiles).not.toHaveProperty('_')
  })
  it('an idle owned port has no profiles at all (the client never records IDLE)', () => {
    expect(wirePort('idle_owned').profiles).toEqual({})
  })
  it('overrides win and the fixture is not mutated', () => {
    expect(wirePort('tx_owned', { owned: false }).owned).toBe(false)
    expect(wirePort('tx_owned').owned).toBe(true)
  })
})
