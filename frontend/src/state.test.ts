import { describe, expect, it } from 'vitest'
import { initialState, reducer } from './state'
import type { CaptureMsg, CaptureSaveMsg, SetupsMsg } from './api'
import testSetupsFixture from './__fixtures__/testSetups.json'
import captureMsg from './__fixtures__/captureMsg.json'
import captureSave from './__fixtures__/captureSave.json'
import daemonStf from './__fixtures__/daemonMsgStf.json'
import type { DaemonMsg } from './api'
import { wireNeighbours, wirePing } from './testWire'

describe('reducer', () => {
  it('caps the log at 2000 and rpc at 500', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_polling: false } })
    expect(s.session.state).toBe('connected')
    s = { ...s, log: [] }   // the Connected mark is not under test
    for (let i = 0; i < 2010; i++) s = reducer(s, { kind: 'msg', msg: { type: 'event', name: `e${i}`, args: [], message: null, ts: i, level: 'info' } })
    expect(s.log).toHaveLength(2000)
    expect(s.log[1999].message).toBe('e2009')
    for (let i = 0; i < 600; i++) s = reducer(s, { kind: 'msg', msg: { type: 'rpc', direction: 'request', body: { id: i }, ts: i, thread: 't' } })
    expect(s.rpc).toHaveLength(500); expect((s.rpc[0].body as { id: number }).id).toBe(100)
  })
  it('marks backend offline and keeps the last session', () => {
    const s = reducer({ ...initialState, backendOnline: true }, { kind: 'socket', open: false })
    expect(s.backendOnline).toBe(false)
  })
  it('hello sets the server hint', () => {
    const s = reducer(initialState, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: 'lab1', server_ports: null } })
    expect(s.serverHint).toBe('lab1')
  })
  it('hello sets the server ports', () => {
    const ports = { sync_port: 4601, async_port: 4600 }
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: 'lab1', server_ports: ports } })
    expect(s.serverPorts).toEqual(ports)
    s = reducer(s, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: 'lab1', server_ports: null } })
    expect(s.serverPorts).toBeNull()
  })
})

describe('reducer (P3c capture)', () => {
  it('stores the capture snapshot and the newest save message per recorder, and an ended session clears both', () => {
    const cap = captureMsg.monitor_and_recorder as CaptureMsg
    const [progress, done] = captureSave.messages.all as CaptureSaveMsg[]
    let s = reducer(initialState, { kind: 'msg', msg: cap })
    s = reducer(s, { kind: 'msg', msg: progress })
    expect(s.captureSave[1].file).toBeNull()
    s = reducer(s, { kind: 'msg', msg: done })
    expect(s.capture).toBe(cap)
    expect(s.captureSave[1].file?.pkt_count).toBe(120)
    s = reducer({ ...s, session: { ...s.session, state: 'connected' } }, { kind: 'msg', msg: { ...s.session, state: 'disconnected' } })
    expect(s.capture.captures).toEqual([])
    expect(s.captureSave).toEqual({})
  })
})

const connected = { type: 'session' as const, state: 'connected' as const, params: null, info: null, error: null, rpc_console: false, include_polling: false }

describe('reducer (P2)', () => {
  it('stores ports snapshots', () => {
    const s = reducer(initialState, { kind: 'msg', msg: { type: 'ports', ports: [{ id: 0 } as never] } })
    expect(s.ports).toHaveLength(1)
  })
  it('turns events and logs into typed log entries', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'event', name: 'port acquired', args: [], message: 'Port 0 was taken by you', ts: 1, level: 'info' } })
    s = reducer(s, { kind: 'msg', msg: { type: 'log', level: 'error', message: 'Start: boom', ts: 2 } })
    expect(s.log.map((e) => e.kind)).toEqual(['SERVER_EVENT', 'ERROR'])
    expect(s.log[0].message).toBe('Port 0 was taken by you')
  })
  it('profile events become log entries: info as SERVER_EVENT, profile error as WARNING', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'event', name: 'profile finished tx', args: [0, 'bench'], message: 'Profile 0.bench job done', ts: 1, level: 'info' } })
    s = reducer(s, { kind: 'msg', msg: { type: 'event', name: 'profile error', args: [0, 'bench'], message: 'Profile 0.bench job failed', ts: 2, level: 'warning' } })
    expect(s.log.map((e) => e.kind)).toEqual(['SERVER_EVENT', 'WARNING'])
    expect(s.log.map((e) => e.message)).toEqual(['Profile 0.bench job done', 'Profile 0.bench job failed'])
  })
  it('records the latest port error event with a fresh sequence number', () => {
    const ev = { type: 'event' as const, name: 'port error', args: [1], message: 'Port 1 error', ts: 1, level: 'warning' }
    let s = reducer(initialState, { kind: 'msg', msg: ev })
    expect(s.portError).toEqual({ port: 1, seq: 1 })
    s = reducer(s, { kind: 'msg', msg: ev })
    expect(s.portError).toEqual({ port: 1, seq: 2 })
    expect(s.log.map((e) => e.kind)).toEqual(['WARNING', 'WARNING'])
  })
  it('keeps the port error seq increasing across a disconnect', () => {
    const ev = { type: 'event' as const, name: 'port error', args: [1], message: 'Port 1 error', ts: 1, level: 'warning' }
    let s = reducer(initialState, { kind: 'msg', msg: ev })
    s = reducer(s, { kind: 'msg', msg: connected })
    s = reducer(s, { kind: 'msg', msg: { ...connected, state: 'disconnected' } })
    expect(s.portError).toBeNull()
    s = reducer(s, { kind: 'msg', msg: ev })
    expect(s.portError).toEqual({ port: 1, seq: 2 })
  })
  it('keeps the log on disconnect and appends a Disconnected mark; ports are cleared', () => {
    let s = reducer(initialState, { kind: 'msg', msg: connected })
    s = reducer(s, { kind: 'msg', msg: { type: 'log', level: 'info', message: 'x', ts: 1 } })
    s = reducer(s, { kind: 'msg', msg: { type: 'ports', ports: [{ id: 0 } as never] } })
    s = reducer(s, { kind: 'msg', msg: { ...connected, state: 'disconnected' } })
    expect(s.log.map((e) => [e.kind, e.message])).toEqual([['MARK', 'Connected to TRex'], ['INFO', 'x'], ['MARK', 'Disconnected']])
    expect(s.ports).toEqual([])
  })
  it('keeps the log when the session is lost', () => {
    let s = reducer(initialState, { kind: 'msg', msg: connected })
    s = reducer(s, { kind: 'msg', msg: { type: 'log', level: 'info', message: 'x', ts: 1 } })
    s = reducer(s, { kind: 'msg', msg: { ...connected, state: 'lost', error: 'gone' } })
    expect(s.log.filter((e) => e.kind !== 'MARK')).toHaveLength(1)
    expect(s.log[s.log.length - 1].message).toBe('Connection lost: gone')
  })
  it('keeps the log across a lost session and a new connect', () => {
    const snap = (state: 'connected' | 'lost', seq: string, error: string | null = null) => ({ ...connected, state, connect_seq: seq, error, params: { host: 'h', sync_port: 4501 } as never })
    let s = reducer(initialState, { kind: 'msg', msg: snap('connected', 'c:1') })
    for (const m of ['a', 'b', 'c']) s = reducer(s, { kind: 'msg', msg: { type: 'log', level: 'info', message: m, ts: 1 } })
    s = reducer(s, { kind: 'msg', msg: snap('lost', 'c:1', 'x') })
    s = reducer(s, { kind: 'msg', msg: snap('connected', 'c:2') })
    expect(s.log.map((e) => e.message)).toEqual(['Connected to h:4501', 'a', 'b', 'c', 'Connection lost: x', 'Connected to h:4501'])
    expect(s.log.filter((e) => e.kind === 'MARK')).toHaveLength(3)
  })
  it('a lost session without an error says unknown cause', () => {
    let s = reducer(initialState, { kind: 'msg', msg: connected })
    s = reducer(s, { kind: 'msg', msg: { ...connected, state: 'lost', error: null } })
    expect(s.log[s.log.length - 1].message).toBe('Connection lost: unknown cause')
  })
  it('logUnseen counts errors and warnings until seen', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'log', level: 'error', message: 'e', ts: 1 } })
    s = reducer(s, { kind: 'msg', msg: { type: 'log', level: 'warning', message: 'w', ts: 1 } })
    s = reducer(s, { kind: 'msg', msg: { type: 'log', level: 'info', message: 'i', ts: 1 } })
    s = reducer(s, { kind: 'msg', msg: { type: 'event', name: 'n', args: [], message: 'ev', ts: 1, level: 'warning' } })
    expect(s.logUnseen).toBe(3)
    s = reducer(s, { kind: 'logSeen' })
    expect(s.logUnseen).toBe(0)
    expect(s.log).toHaveLength(4)
  })
  it('clear empties the log and the count', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'log', level: 'error', message: 'e', ts: 1 } })
    s = reducer(s, { kind: 'logClear' })
    expect(s.log).toEqual([]); expect(s.logUnseen).toBe(0)
  })
})

describe('reducer (P2.5 reconnect)', () => {
  const sess = (state: 'connecting' | 'lost' | 'disconnected' | 'connected') => ({ msg: { ...connected, state }, kind: 'msg' as const })
  const dirty = {
    ...initialState,
    session: { ...connected, state: 'lost' as const },
    ports: [{ id: 0 } as never],
    log: [{ kind: 'ERROR' as const, ts: 1, message: 'x' }],
    portError: { port: 0, seq: 1 },
    rpc: [{ type: 'rpc' as const } as never],
  }
  it('reducer clears ports, portError and rpc, keeps the log, when a lost session starts connecting', () => {
    const s = reducer(dirty, sess('connecting'))
    expect(s.ports).toEqual([])
    expect(s.log[0]).toEqual({ kind: 'ERROR', ts: 1, message: 'x' })
    expect(s.portError).toBeNull()
    expect(s.rpc).toEqual([])
  })
  it('reducer keeps them while connected -> lost', () => {
    const s = reducer({ ...dirty, session: connected }, sess('lost'))
    expect(s.ports).toHaveLength(1)
    expect(s.log.filter((e) => e.kind !== 'MARK')).toHaveLength(1)
    expect(s.portError).not.toBeNull()
    expect(s.rpc).toHaveLength(1)
  })
  it('reducer keeps rpc when a connect fails', () => {
    const s = reducer({ ...dirty, session: { ...connected, state: 'connecting' as const } }, sess('disconnected'))
    expect(s.rpc).toHaveLength(1)
    expect(s.ports).toEqual([])
  })
})

describe('ping log entries and neighbours', () => {
  it('a PING entry appears in the event log, once per echo', () => {
    const s = reducer(initialState, { kind: 'msg', msg: { type: 'log', level: 'ping', message: 'Port 0: Reply', ts: 7 } })
    expect(s.log).toEqual([{ kind: 'PING', ts: 7, message: 'Port 0: Reply' }])
  })
  it('a streamed ping line adds no log entry (the backend log message is the source)', () => {
    const s = reducer(initialState, { kind: 'msg', msg: wirePing('reply', { run: 1 }) })
    expect(s.pings[0]).toHaveLength(1)
    expect(s.log).toEqual([])
  })
  it('neighbours are kept per port, cleared by clear, and dropped by a new connect_seq', () => {
    const list = wireNeighbours().neighbours
    let s = reducer(initialState, { kind: 'neighbours', port: 0, list })
    s = reducer(s, { kind: 'neighbours', port: 1, list: [] })
    expect(s.neighbours).toEqual({ 0: list, 1: [] })
    s = reducer(s, { kind: 'neighbours', port: 0, list: null })
    expect(s.neighbours).toEqual({ 1: [] })
    const next = reducer(s, { kind: 'msg', msg: { type: 'session', state: 'connecting', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: 'b' } })
    expect(next.neighbours).toEqual({})
  })

  it('a busy message with no ports clears a previously non-empty busy map', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'busy', ports: { 0: 'ping' } } })
    expect(s.busy).toEqual({ 0: 'ping' })
    s = reducer(s, { kind: 'msg', msg: { type: 'busy', ports: {} } })
    expect(s.busy).toEqual({})
  })
  it('test_setups fills the setups and survives a new connect', () => {
    const setup = { name: 'A', slug: 'a', created: '', updated: '', server: null, ports: [], dashboard: null }
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'test_setups', setups: [setup], load_errors: [{ file: 'x.json', error: 'bad' }] } })
    expect(s.setups).toEqual([setup])
    expect(s.setupLoadErrors).toEqual([{ file: 'x.json', error: 'bad' }])
    s = reducer(s, { kind: 'msg', msg: { type: 'session', state: 'connecting', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: 'n:2' } })
    expect(s.setups).toEqual([setup])
  })
})

describe('reducer (P3d test_setups wire fixture)', () => {
  it('feeds the recorded test_setups message into setups and setupLoadErrors', () => {
    const msg = testSetupsFixture.two_setups as unknown as SetupsMsg
    const s = reducer(initialState, { kind: 'msg', msg })
    expect(s.setups.map((x) => x.slug)).toEqual(['lab-a', 'lab-b'])
    expect(s.setups[0].ports[0].layer?.mode).toBe('L2')
    expect(s.setupLoadErrors).toEqual([])
  })
  it('hello carries monitor_only and the monitor target, and a new connect keeps both', () => {
    const target = { host: 'lab2', port: 4500 }
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: null, server_ports: null, monitor_only: true, monitor_target: target } })
    expect(s.monitorOnly).toBe(true)
    expect(s.monitorTarget).toEqual(target)
    s = reducer(s, { kind: 'msg', msg: { type: 'session', state: 'connecting', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: 'x:9' } })
    expect(s.monitorOnly).toBe(true)
    s = reducer(s, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: null, server_ports: null } })   // an older backend sends neither
    expect(s.monitorOnly).toBe(false)
    expect(s.monitorTarget).toBeNull()
  })
  it('hello sets the daemon hint, and a daemon message is kept', () => {
    let s = reducer(initialState, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: 'lab1', server_ports: null, daemon_hint: { host: 'lab1', port: 8090 } } })
    expect(s.daemonHint).toEqual({ host: 'lab1', port: 8090 })
    s = reducer(s, { kind: 'msg', msg: daemonStf as unknown as DaemonMsg })
    expect(s.daemon?.run?.mode).toBe('stf')
    s = reducer(s, { kind: 'msg', msg: { type: 'hello', app_version: '0', server_hint: 'lab1', server_ports: null } })
    expect(s.daemonHint).toBeNull()
  })
  it('the daemon data and the remembered stateful run survive a new Control connect', () => {
    let s = reducer(initialState, { kind: 'msg', msg: daemonStf as unknown as DaemonMsg })
    s = reducer(s, { kind: 'stfRun', run: { host: 'lab1', endsAt: 5 } })
    s = reducer(s, { kind: 'msg', msg: { type: 'session', state: 'connecting', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: 'x:1' } })
    expect(s.daemon?.target?.host).toBe('127.0.0.1')
    expect(s.lastStfRun).toEqual({ host: 'lab1', endsAt: 5 })
    expect(reducer(s, { kind: 'stfRun', run: null }).lastStfRun).toBeNull()
  })
})
