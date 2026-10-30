import type { DaemonMsg, MonitorMsg } from './api'

const FRESH_S = 5   // an STF name counts only when it arrived this close to the message's own ts (fable M7)

// True for a TRex command line that is a stateful run: the whitespace tokens hold -f and not -i. Same rule as the backend's _is_stf.
export function isStfCmd(cmd: string): boolean {
  const toks = cmd.split(/\s+/)
  return toks.includes('-f') && !toks.includes('-i')
}

export interface StfSources { daemon: DaemonMsg | null; monitor: MonitorMsg | null; lastStfRun: { host: string; endsAt: number } | null; now: number }

// Whether a stateful (-f) run is on `host`, which has no command port (spec 13.5). Any one of three signs is enough: the daemon
// lists a -f TRex process there, the Monitor is connected there and saw an STF-only name in the last 5 s, or this GUI
// started a run there that has not reached its end time.
export function stfRunOn(host: string, { daemon, monitor, lastStfRun, now }: StfSources): boolean {
  if (!host) return false
  if (daemon?.target?.host === host && daemon.trex_cmds.some((c) => isStfCmd(c.cmd))) return true
  if (monitor?.target?.host === host && monitor.state === 'connected' && monitor.ts !== null) {
    const fresh = (ts: number | undefined) => ts !== undefined && monitor.ts !== null && monitor.ts - ts <= FRESH_S
    if (fresh(monitor.known_ts['template_info']) || fresh(monitor.known_ts['trex-latecny']) || fresh(monitor.other['tx-gen']?.ts)) return true
  }
  return lastStfRun !== null && lastStfRun.host === host && now < lastStfRun.endsAt
}

// The sentence for a host with a stateful run (ConnectDialog and the Control page show it with an Open Monitor link).
export const stfRunText = (host: string): string => `${host} is running a stateful (-f) run. It has no command port; watch it on the Monitor.`
