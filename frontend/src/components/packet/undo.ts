// A bounded snapshot history for the packet editor (spec §6.2): the states before each change, the states undone.
export const HISTORY_CAP = 200

export class History<T> {
  private past: T[] = []
  private future: T[] = []
  private readonly cap: number

  constructor(cap: number = HISTORY_CAP) { this.cap = cap }

  get canUndo(): boolean { return this.past.length > 0 }
  get canRedo(): boolean { return this.future.length > 0 }

  // Record `before`, the state a change replaced. A new change ends the redo line; the oldest state goes when the cap is passed.
  push(before: T): void {
    this.past.push(before)
    if (this.past.length > this.cap) this.past.shift()
    this.future = []
  }

  // Step back from `current`: the state to show, or undefined with nothing to undo. `current` becomes redoable.
  undo(current: T): T | undefined {
    const prev = this.past.pop()
    if (prev === undefined) return undefined
    this.future.push(current)
    return prev
  }

  // Step forward from `current`: the state to show, or undefined with nothing to redo. `current` becomes undoable again.
  redo(current: T): T | undefined {
    const next = this.future.pop()
    if (next === undefined) return undefined
    this.past.push(current)
    return next
  }

  // Take back the newest push without making it redoable (a change that was refused). False when it is not the top.
  drop(before: T): boolean {
    if (this.past[this.past.length - 1] !== before) return false
    this.past.pop()
    return true
  }

  clear(): void { this.past = []; this.future = [] }
}
