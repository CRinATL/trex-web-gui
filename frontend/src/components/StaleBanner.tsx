// performance.now() as a wall-clock HH:MM:SS, for display only (staleness itself is judged on the monotonic clock, useStale.ts).
const clock = (at: number) => new Date(performance.timeOrigin + at).toTimeString().slice(0, 8)

// A tab whose read has stopped arriving. `what` names the read ("Stream stats"), so a reader can tell which tab is old;
// `since` is the newest message's arrival (performance.now()), null when none has arrived.
export function StaleBanner({ what, since = null }: { what: string; since?: number | null }) {
  return <p role="status" aria-label={`${what} are stale`} className="banner stale-banner">{what} are not updating{since !== null && ` (last update ${clock(since)})`}</p>
}
