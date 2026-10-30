import { afterEach, describe, expect, it, vi } from 'vitest'
import { acquire, ApiError, connect, getAbout } from './api'

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
