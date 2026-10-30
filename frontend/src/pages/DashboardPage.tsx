import { useSyncExternalStore, type ReactNode } from 'react'
import { DEFAULT_CHARTS, INTERVALS, LAYOUTS } from '../chartSeries'
import { GlobalTiles } from '../components/GlobalTiles'
import { MbufTable } from '../components/MbufTable'
import { PortsTable, ServiceModeBanner } from '../components/PortsTable'
import { StaleBanner } from '../components/StaleBanner'
import { CpuChart, CpuTable } from '../components/CpuTable'
import { LatencyTab } from '../components/LatencyTable'
import { StreamsTab } from '../components/StreamsTable'
import { YAxisToggle } from '../components/YAxisToggle'
import { StatsChart } from '../components/StatsChart'
import { dashboardPrefs, type DashTabId } from '../dashboardPrefs'
import { usePortDisplayLabelOf } from '../portLabels'
import { useAppState } from '../state'
import { CLEAR_STATS_HINT, clearAll } from '../clearStats'
import { Tabs, tabPanel } from '../ui/Tabs'
import { pgidSource, statsSource, useStale, utilSource, type StaleSource } from '../useStale'

type TabId = DashTabId
// what: the stale banner's subject. Each tab judges its own read.
const TABS: { id: TabId; label: string; source: StaleSource; what: string }[] = [
  { id: 'overview', label: 'Overview', source: statsSource, what: 'Statistics' },
  { id: 'streams', label: 'Streams', source: pgidSource, what: 'Stream stats' },
  { id: 'latency', label: 'Latency', source: pgidSource, what: 'Latency stats' },
  { id: 'utilization', label: 'Utilization', source: utilSource, what: 'Utilization stats' },
]

// All panels stay mounted, so the Overview keeps its charts and settings while another tab is shown.
function TabPanel({ tab, selected, children }: { tab: (typeof TABS)[number]; selected: TabId; children?: ReactNode }) {
  const stale = useStale(tab.source)
  return (
    <div {...tabPanel('dash', tab.id, selected)} className={stale ? 'is-stale' : undefined}>
      {stale && <StaleBanner what={tab.what} since={tab.source.receivedAt()} />}
      {children}
    </div>
  )
}

// The tables are not rendered while another tab is shown; the chart stays mounted, so its history is not redrawn.
function UtilizationTab({ visible }: { visible: boolean }) {
  return (
    <div className="overview-grid">
      <div>
        {visible && <section aria-label="CPU"><CpuTable /></section>}
        {visible && <section aria-label="Mbufs"><MbufTable /></section>}
      </div>
      <div><CpuChart /></div>
    </div>
  )
}

export function DashboardPage() {
  const { ports, session } = useAppState()
  // Tab, interval and layout live in dashboardPrefs: they survive leaving the page and reset on a new connect_seq.
  const { tab, windowS, layout } = useSyncExternalStore(dashboardPrefs.subscribe, dashboardPrefs.get)
  const setTab = (t: TabId) => dashboardPrefs.set({ tab: t })
  const setWindowS = (n: number) => dashboardPrefs.set({ windowS: n })
  const setLayout = (n: number) => dashboardPrefs.set({ layout: n })
  const labelOf = usePortDisplayLabelOf(ports)

  return (
    <section>
      <h1>Dashboard</h1>
      {session.state === 'lost' && <p role="status" aria-label="Connection lost" className="banner alert"><strong>Connection lost:</strong> values are the last received</p>}
      <ServiceModeBanner ports={ports} canAct={session.state === 'connected' && !session.params?.read_only} />
      <Tabs label="Dashboard" idPrefix="dash" tabs={TABS} selected={tab} onSelect={setTab}
        end={<button type="button" disabled={session.state !== 'connected'} title={CLEAR_STATS_HINT} onClick={clearAll}>Clear stats</button>} />
      <TabPanel tab={TABS[0]} selected={tab}>
        <section aria-label="Global" className="overview-tiles"><GlobalTiles ports={ports} /></section>
        <div className="overview-grid">
          <section aria-label="Ports"><PortsTable ports={ports} labelOf={labelOf} /></section>
          <section aria-label="Charts">
            <label>
              Interval
              <select aria-label="Interval" value={windowS} onChange={(e) => setWindowS(Number(e.target.value))}>
                {INTERVALS.map((n) => <option key={n} value={n}>{n} s</option>)}
              </select>
            </label>
            <label>
              Layout
              <select aria-label="Layout" value={layout} onChange={(e) => setLayout(Number(e.target.value))}>
                {LAYOUTS.map((n) => <option key={n} value={n}>{n} {n === 1 ? 'chart' : 'charts'}</option>)}
              </select>
            </label>
            <YAxisToggle />
            <div className="chart-grid" style={{ display: 'grid', gridTemplateColumns: `repeat(${layout === 1 ? 1 : 2}, minmax(0, 1fr))`, gap: '0.5rem' }}>
              {DEFAULT_CHARTS.slice(0, layout).map((id, i) => (
                <StatsChart key={i} initialSeriesId={id} windowS={windowS} label={`Chart ${i + 1} series`} />
              ))}
            </div>
          </section>
        </div>
      </TabPanel>
      <TabPanel tab={TABS[1]} selected={tab}>
        <StreamsTab ports={ports} labelOf={labelOf} visible={tab === 'streams'} />
      </TabPanel>
      <TabPanel tab={TABS[2]} selected={tab}>
        <LatencyTab visible={tab === 'latency'} />
      </TabPanel>
      <TabPanel tab={TABS[3]} selected={tab}>
        <UtilizationTab visible={tab === 'utilization'} />
      </TabPanel>
    </section>
  )
}
