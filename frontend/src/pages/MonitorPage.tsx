import { MonitorConnect } from '../components/monitor/MonitorConnect'
import { MonitorGlobal } from '../components/monitor/MonitorGlobal'
import { MonitorInspector } from '../components/monitor/MonitorInspector'
import { MonitorLatency } from '../components/monitor/MonitorLatency'
import { MonitorLatencyV2 } from '../components/monitor/MonitorLatencyV2'
import { MonitorPanel } from '../components/monitor/MonitorPanel'
import { MonitorPorts } from '../components/monitor/MonitorPorts'
import { MonitorRxCheck } from '../components/monitor/MonitorRxCheck'
import { MonitorStatus } from '../components/monitor/MonitorStatus'
import { MonitorTemplates } from '../components/monitor/MonitorTemplates'
import { StaleBanner } from '../components/StaleBanner'
import { portRows } from '../monitorLayouts'
import { useMonitor } from '../monitorStore'
import { Info } from '../ui/Info'

// The Monitor page: a passive subscriber to a TRex publisher (no RPC). A panel shows once the message that feeds it has arrived.
export function MonitorPage() {
  const global = useMonitor((m) => m?.global ?? null)
  const templates = useMonitor((m) => m?.templates ?? null)
  const latency = useMonitor((m) => m?.latency ?? null)
  const rxCheck = useMonitor((m) => m?.rx_check ?? null)
  const latencyV2 = useMonitor((m) => m?.latency_v2 ?? null)
  const inspectable = useMonitor((m) => m !== null && (Object.keys(m.known_ts).length > 0 || Object.keys(m.other).length > 0))
  const stale = useMonitor((m) => m?.state === 'stale')
  return (
    <section className="monitor-page">
      <div className="page-head">
        <h1>Monitor</h1>
        <Info label="the Monitor">The Monitor listens to the statistics TRex publishes on its async port (default 4500). It opens no RPC connection and changes nothing on the server, so it works beside any other client.</Info>
      </div>
      <MonitorConnect />
      <MonitorStatus />
      {stale && <StaleBanner what="Monitor data" />}
      <div className={`monitor-grid${stale ? ' is-stale' : ''}`}>
        <MonitorPanel id="global" title="Global" present={global !== null}><MonitorGlobal /></MonitorPanel>
        <MonitorPanel id="ports" title="Ports" present={portRows(global).length > 0}><MonitorPorts /></MonitorPanel>
        <MonitorPanel id="templates" title="Templates" present={templates !== null && templates.length > 0}><MonitorTemplates /></MonitorPanel>
        <MonitorPanel id="latency" title="Latency" present={latency !== null}><MonitorLatency /></MonitorPanel>
        <MonitorPanel id="rx-check" title="Rx-check" present={rxCheck !== null}><MonitorRxCheck /></MonitorPanel>
        <MonitorPanel id="latency-v2" title="Latency v2" present={latencyV2 !== null}><MonitorLatencyV2 /></MonitorPanel>
        <MonitorPanel id="inspector" title="Inspector" present={inspectable}><MonitorInspector /></MonitorPanel>
      </div>
    </section>
  )
}
