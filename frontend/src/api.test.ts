import { afterEach, describe, expect, it, vi } from 'vitest'
import { acquire, ApiError, connect, getAbout } from './api'
import * as api from './api'

function stubFetch(status: number, body: unknown, statusText = 'Err') {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: status < 400, status, statusText,
    json: async () => { if (body === undefined) throw new Error('no json'); return body },
  })))
}

afterEach(() => { vi.unstubAllGlobals() })

describe('api error mapping', () => {
  it('maps the {error, message} shape to ApiError', async () => {
    stubFetch(409, { error: 'busy', message: 'Session is already connected' })
    const e = await connect({ host: 'h', sync_port: 1, async_port: 2, timeout_s: 3 }).catch((x) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 409, code: 'busy', message: 'Session is already connected' })
  })
  it('joins FastAPI validation details', async () => {
    stubFetch(422, { detail: [{ msg: 'bad a' }, { msg: 'bad b' }] })
    const e = await connect({ host: '', sync_port: 1, async_port: 2, timeout_s: 3 }).catch((x) => x)
    expect(e).toMatchObject({ status: 422, code: 'http_error', message: 'bad a; bad b' })
  })
  it('no HTTP reply at all names the request and what to check, not the browser\'s bare "Load failed" (RC1-27)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Load failed') }))
    const e = await acquire({ ports: [0] }).catch((x) => x)
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({ status: 0, code: 'no_reply' })
    expect(e.message).toBe('No reply from trex-web-gui to POST /api/ports/acquire (the browser says "Load failed"). Is trex-web-gui serve still running, and the SSH tunnel up?')
  })
  it('an error that is not the browser\'s network failure passes through', async () => {
    const boom = new RangeError('x')
    vi.stubGlobal('fetch', vi.fn(async () => { throw boom }))
    expect(await getAbout().catch((x) => x)).toBe(boom)
  })
  it('falls back to statusText when the body is not JSON', async () => {
    stubFetch(502, undefined, 'Bad Gateway')
    const e = await getAbout().catch((x) => x)
    expect(e).toMatchObject({ status: 502, code: 'http_error', message: 'Bad Gateway' })
  })
  it('a 401 dispatches trex-web-gui:unauthenticated', async () => {
    stubFetch(401, { error: 'unauthenticated', message: 'Open the URL printed by trex-web-gui serve.' })
    const seen = vi.fn()
    window.addEventListener('trex-web-gui:unauthenticated', seen)
    try {
      const e = await getAbout().catch((x) => x)
      expect(e).toMatchObject({ status: 401, code: 'unauthenticated' })
      expect(seen).toHaveBeenCalledTimes(1)
    } finally { window.removeEventListener('trex-web-gui:unauthenticated', seen) }
  })
  it('returns the parsed body on success', async () => {
    stubFetch(200, { app_version: '1' })
    expect(await getAbout()).toEqual({ app_version: '1' })
  })
})

describe('EMU calls', () => {
  const ok = () => vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => ({}) }))
  it('macText formats six ints as colon-hex', () => {
    expect(api.macText([0, 0, 0, 1, 0, 255])).toBe('00:00:00:01:00:ff')
  })
  it('the counters call sends the scope, the namespace key and the MAC as colon-hex', async () => {
    const f = ok()
    vi.stubGlobal('fetch', f)
    try {
      await api.emuCounters('client', { ns: { vport: 1, tci: [10, 20] }, mac: [170, 187, 204, 221, 238, 255], plugin: 'arp' })
      await api.emuCounters('ctx', { meta: true })
      await api.emuClients({ vport: 0 }, 100, 50)
    } finally { vi.unstubAllGlobals() }
    expect(f.mock.calls.map((c) => (c as unknown[])[0])).toEqual([
      '/api/emu/counters?scope=client&plugin=arp&vport=1&tci=10,20&tpid=0,0&mac=aa:bb:cc:dd:ee:ff',
      '/api/emu/counters?scope=ctx&meta=true',
      '/api/emu/clients?vport=0&tci=0,0&tpid=0,0&offset=100&limit=50',
    ])
  })
})
