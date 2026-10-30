// A controllable window.matchMedia for tests (jsdom has none). setupTests installs it before each
// test and resets it after; tests import setPrefersDark from here, never from setupTests.
type Listener = (e: { matches: boolean }) => void

let dark = false
const listeners = new Set<Listener>()

const matchMedia = (query: string) => ({
  get matches() { return query.includes('prefers-color-scheme: dark') ? dark : false },
  media: query,
  addEventListener: (type: string, fn: Listener) => { if (type === 'change') listeners.add(fn) },
  removeEventListener: (type: string, fn: Listener) => { if (type === 'change') listeners.delete(fn) },
})

export function installMatchMedia() {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: matchMedia })
}

// Flip the OS preference and tell every registered `change` listener.
export function setPrefersDark(value: boolean) {
  dark = value
  for (const fn of [...listeners]) fn({ matches: dark })
}

export function resetMatchMedia() {
  dark = false
  listeners.clear()
}
