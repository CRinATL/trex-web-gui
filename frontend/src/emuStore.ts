import { useSyncExternalStore } from 'react'
import type { EmuMsg } from './api'
import { clearBaselines } from './emuBaseline'

export const EMU_HOSTS_KEY = 'trex-web-gui.emuHosts'
export const EMU_DEFAULT_PORT = 4510
const HISTORY_MAX = 10

export interface EmuHost { host: string; port: number }
// The datalist text of an entry; an IPv6 host is bracketed so the port is readable.
export const hostLabel = (e: EmuHost) => `${e.host.includes(':') ? `[${e.host}]` : e.host}:${e.port}`

// The targets this browser connected to before, newest first. Anything else in storage is dropped; unreadable storage is no history.
export function readEmuHosts(): EmuHost[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(EMU_HOSTS_KEY) ?? '[]')
    if (!Array.isArray(v)) return []
    return v.filter((x): x is EmuHost => typeof x === 'object' && x !== null && typeof (x as EmuHost).host === 'string' && Number.isInteger((x as EmuHost).port))
      .map((x) => ({ host: x.host, port: x.port })).slice(0, HISTORY_MAX)
  } catch { return [] }
}
// Puts the entry first (once each), stores the list and returns it; a browser that refuses storage keeps no history.
export function rememberEmuHost(entry: EmuHost): EmuHost[] {
  const next = [entry, ...readEmuHosts().filter((h) => hostLabel(h) !== hostLabel(entry))].slice(0, HISTORY_MAX)
  try { localStorage.setItem(EMU_HOSTS_KEY, JSON.stringify(next)) } catch { /* storage unavailable: no history */ }
  return next
}

// The newest `emu` message (null until the first). It lives outside the reducer, like monitorStore; getSnapshot hands back the
// same object until the next message.
class EmuStore {
  private latest: EmuMsg | null = null
  epoch = 0       // counts the connections established anew and the generation changes since the page loaded
  private listeners = new Set<() => void>()

  // A connection established anew (connected after a known state other than connected) or a changed generation (a load or removal
  // changed what the server holds) drops the counter baselines; a first message after a page load is not either, so a reload keeps them.
  push = (m: EmuMsg) => {
    if (this.latest && ((this.latest.state !== 'connected' && m.state === 'connected') || m.generation !== this.latest.generation)) {
      clearBaselines(); this.epoch++
    }
    this.latest = m
    this.listeners.forEach((fn) => fn())
  }
  get = (): EmuMsg | null => this.latest
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const emuStore = new EmuStore()

// The selector must return a primitive or a reference that stays the same until the message changes (a field of the message qualifies).
export function useEmu<T>(sel: (m: EmuMsg | null) => T): T {
  return useSyncExternalStore(emuStore.subscribe, () => sel(emuStore.get()))
}
