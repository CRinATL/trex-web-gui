import { describe, expect, it } from 'vitest'
import type { DaemonMsg, MonitorMsg } from './api'
import idle from './__fixtures__/daemonMsg.json'
import running from './__fixtures__/daemonMsgStf.json'
import stl from './__fixtures__/monitorStl.json'
import stateful from './__fixtures__/monitorStateful.json'
import { isStfCmd, stfRunOn } from './stfDetect'

const daemonIdle = idle as unknown as DaemonMsg
const daemonStf = running as unknown as DaemonMsg
const monStl = stl as unknown as MonitorMsg
const monStf = stateful as unknown as MonitorMsg
const none = { daemon: null, monitor: null, lastStfRun: null, now: 1000 }

describe('stfDetect', () => {
  it('isStfCmd reads -f without -i', () => {
    expect(isStfCmd(daemonStf.trex_cmds[0].cmd)).toBe(true)
    expect(isStfCmd(daemonIdle.trex_cmds[0].cmd)).toBe(false)   // ./_t-rex-64 -i --iom 0 …
    expect(isStfCmd('./_t-rex-64 -f x.yaml -i')).toBe(false)
    expect(isStfCmd('./_t-rex-64 --cfg /f/x.yaml')).toBe(false)   // -f only counts as a whole token
  })
  it('detects from daemon trex_cmds', () => {
    expect(stfRunOn('127.0.0.1', { ...none, daemon: daemonStf })).toBe(true)
    expect(stfRunOn('127.0.0.1', { ...none, daemon: daemonIdle })).toBe(false)
  })
  it('detects from monitor STF names', () => {
    // monitorStateful.json is connected to 127.0.0.1 and carries a fresh template_info
    expect(stfRunOn(monStf.target!.host, { ...none, monitor: monStf })).toBe(true)
    const byName = (name: string): MonitorMsg => ({ ...monStl, known_ts: { [name]: monStl.ts! } })
    expect(stfRunOn('127.0.0.1', { ...none, monitor: byName('trex-latecny') })).toBe(true)
    const txGen: MonitorMsg = { ...monStl, other: { 'tx-gen': { ts: monStl.ts!, data: {} } } }
    expect(stfRunOn('127.0.0.1', { ...none, monitor: txGen })).toBe(true)
    expect(stfRunOn('127.0.0.1', { ...none, monitor: monStl })).toBe(false)
  })
  it('detects from the remembered run until it ends', () => {
    const lastStfRun = { host: 'h', endsAt: 5000 }
    expect(stfRunOn('h', { ...none, lastStfRun, now: 4999 })).toBe(true)
    expect(stfRunOn('h', { ...none, lastStfRun, now: 5000 })).toBe(false)
  })
  it('old STF slots on a connected STL monitor do not detect', () => {
    const old: MonitorMsg = { ...monStl, state: 'connected', known_ts: { 'trex-global': monStl.ts!, template_info: monStl.ts! - 10 } }
    expect(stfRunOn('127.0.0.1', { ...none, monitor: old })).toBe(false)
    const edge: MonitorMsg = { ...old, known_ts: { template_info: monStl.ts! - 5 } }
    expect(stfRunOn('127.0.0.1', { ...none, monitor: edge })).toBe(true)
    expect(stfRunOn('127.0.0.1', { ...none, monitor: { ...monStf, state: 'stale' } })).toBe(false)
  })
  it('another host is not detected', () => {
    expect(stfRunOn('10.9.9.9', { daemon: daemonStf, monitor: monStf, lastStfRun: { host: 'h', endsAt: 5000 }, now: 1000 })).toBe(false)
    expect(stfRunOn('', { daemon: daemonStf, monitor: null, lastStfRun: null, now: 0 })).toBe(false)
  })
})
