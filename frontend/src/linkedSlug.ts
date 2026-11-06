import { tunablesToArgs } from './traffic'
import type { TrafficForm } from './trafficStore'

// The slug a request may send for this form: the pick's, while the form still is that pick (same path, same arguments).
// Before the specs are known the arguments are libraryArgs themselves, as TrafficTab's tunablesFor has it.
export function linkedSlug(form: TrafficForm): string | undefined {
  if (!form.librarySlug || form.libraryPy !== form.path) return undefined
  const args = form.specs.length === 0 ? form.libraryArgs : tunablesToArgs(form.specs, form.values, form.extra)
  const same = args.length === form.libraryArgs.length && args.every((a, i) => a === form.libraryArgs[i])
  return same ? form.librarySlug : undefined
}
