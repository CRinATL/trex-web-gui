// Eight chart colours for the selected pg_ids, as the theme tokens --series-1..8 (Okabe-Ito, adjusted per theme
// in index.css so each clears its contrast bar). Colour is never the only signal: every series also carries its pg_id
// in the legend text.
export const PGID_PALETTE = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)'] as const

// First palette colour not in `used`; undefined when all eight are taken.
export const freeColor = (used: Iterable<string>): string | undefined => {
  const taken = new Set(used)
  return PGID_PALETTE.find((c) => !taken.has(c))
}

export const AUTO_SELECT = 4                     // picked on first sight
export const SELECT_MAX = PGID_PALETTE.length    // one colour each
export const CAP_REASON = `At most ${SELECT_MAX} streams can be charted; untick one first`

// The id of the paragraph that explains why a checkbox is disabled; one per tab, so two mounted tabs never share an id.
export const capReasonId = (tab: 'streams' | 'latency') => `pgid-cap-reason-${tab}`
