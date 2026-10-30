import { useEffect, useReducer, useRef, useState, type ReactNode } from 'react'
import uPlot from 'uplot'
import { useTheme } from '../theme'
import 'uplot/dist/uPlot.min.css'

const FALLBACK_WIDTH = 600 // jsdom has no ResizeObserver and no layout
const HEIGHT = 220
// Light-theme values from index.css, used where the variable is empty (jsdom).
const FALLBACK: Record<string, string> = { '--fg': '#1d1d22', '--border': '#d9d9e0', '--chart-grid': '#d9d9e0', '--accent': '#3b5bdb' }

const two = (n: number) => String(n).padStart(2, '0')
function clockOf(x: number | undefined): string | null {
  if (x === undefined) return null
  const d = new Date(x * 1000)
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}
// An x tick, on one line (RC1-38): `:15` within a minute, `3:46pm` at a whole minute. uPlot's default adds a second
// line with the date and time, which collides with its neighbour when a minute starts near the left edge; the date is
// left out (the hover clock gives the time).
function tickOf(x: number): string {
  const d = new Date(Math.round(x) * 1000)
  if (d.getSeconds() !== 0) return `:${two(d.getSeconds())}`
  const h = d.getHours()
  return `${h % 12 === 0 ? 12 : h % 12}:${two(d.getMinutes())}${h < 12 ? 'am' : 'pm'}`
}

export type ChartSeries = { label: string; color: string }   // color: a CSS colour or `var(--name)`

type Props = {
  title: string
  subscribe: (fn: () => void) => () => void
  data: (windowS: number) => uPlot.AlignedData
  series: ChartSeries[]
  format: (v: number | null) => string
  windowS: number
  latestTs: () => number | undefined
  controls?: ReactNode
  caption?: ReactNode   // shown in the figcaption instead of `title`; aria-label stays `title`
  legend: 'line' | 'chips'   // 'line': "latest <value>" under the chart; 'chips': a ✓/✗ toggle per series with its value
  yFrom0?: boolean   // y runs from 0 to the largest shown value; toggling redraws without rebuilding the chart
  dataKey?: string   // what `data` reads (e.g. the metric): a new value redraws at once, without rebuilding the chart
}

// A uPlot line chart over a store. The chart is built once and setData runs on each store notification; it is rebuilt
// only when the series identity (labels and colours) or the resolved colour scheme changes, because uPlot fixes both at
// construction. x is pinned to [latestTs - windowS, latestTs]; points carry their own ts, so the spacing between them is
// never assumed.
export function TimeChart({ title, subscribe, data, series, format, windowS, latestTs, controls, caption, dataKey, legend, yFrom0 = false }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const plot = useRef<uPlot | null>(null)
  const live = useRef({ data, format, latestTs, windowS, subscribe, yFrom0 })
  const { scheme } = useTheme()   // uPlot paints resolved colours, so a scheme change must rebuild it
  const identity = scheme + '\u0002' + series.map((s) => `${s.label}\u0000${s.color}`).join('\u0001')
  const seriesRef = useRef(series)
  const [, repaint] = useReducer((n: number) => n + 1, 0)   // the legend reads the store, so each push repaints it
  const [hover, setHover] = useState<{ id: string; idx: number | null }>({ id: identity, idx: null })
  const [hiddenState, setHiddenState] = useState<{ id: string; set: ReadonlySet<number> }>({ id: identity, set: new Set() })
  const hovered = hover.id === identity ? hover.idx : null
  const hidden = hiddenState.id === identity ? hiddenState.set : new Set<number>()   // a rebuilt chart shows every series

  // Declared before the effects below, so they read this render's values.
  useEffect(() => { live.current = { data, format, latestTs, windowS, subscribe, yFrom0 }; seriesRef.current = series })

  useEffect(() => {
    const el = host.current
    if (!el) return
    const width = () => el.clientWidth || FALLBACK_WIDTH
    // uPlot draws on canvas, so CSS variables never reach it: resolve them once here.
    const css = getComputedStyle(el)
    const v = (name: string) => css.getPropertyValue(name).trim() || FALLBACK[name] || ''
    const resolve = (c: string) => { const m = /^var\((--[\w-]+)\)$/.exec(c); return m ? v(m[1]) || c : c }
    const axis = { stroke: v('--fg'), grid: { stroke: v('--chart-grid') }, ticks: { stroke: v('--border') } }
    const u = new uPlot(
      {
        width: width(),
        height: HEIGHT,
        scales: {
          x: {
            range: (_u, min, max) => {
              const last = live.current.latestTs()
              return last === undefined ? [min, max] : [last - live.current.windowS, last]
            },
          },
          y: {
            range: (_u, min, max) => {
              if (min == null || max == null) return (live.current.yFrom0 ? [0, 1] : [min, max]) as uPlot.Range.MinMax
              if (!live.current.yFrom0) return uPlot.rangeNum(min, max, 0.1, true)   // uPlot's own default
              return max > 0 ? [0, uPlot.rangeNum(0, max, 0.1, true)[1]] : [0, 1]
            },
          },
        },
        hooks: { setCursor: [(self: uPlot) => setHover({ id: identity, idx: self.cursor.idx ?? null })] },
        legend: { show: false },
        axes: [{ ...axis, values: (_u, splits) => splits.map(tickOf) }, { ...axis, size: 80, values: (_u, splits) => splits.map((x) => live.current.format(x)) }],
        series: [{}, ...seriesRef.current.map((s) => ({ label: s.label, stroke: resolve(s.color), width: 2 }))],
      },
      live.current.data(live.current.windowS),
      el,
    )
    plot.current = u
    const unsub = live.current.subscribe(() => { u.setData(live.current.data(live.current.windowS)); repaint() })
    const ro = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(() => u.setSize({ width: width(), height: HEIGHT }))
    ro?.observe(el)
    return () => {
      unsub()
      ro?.disconnect()
      plot.current = null
      u.destroy()
    }
  }, [identity])

  useEffect(() => { plot.current?.setData(live.current.data(windowS)) }, [windowS, identity, dataKey, yFrom0])

  const toggle = (i: number) => {
    const next = new Set(hidden)
    const show = next.has(i)
    if (show) next.delete(i); else next.add(i)
    setHiddenState({ id: identity, set: next })
    plot.current?.setSeries(i + 1, { show })
  }
  const cols = data(windowS)
  const at = (i: number): number | null => {
    const col = cols[i + 1] as ArrayLike<number | null> | undefined
    if (!col) return null
    if (hovered !== null) return col[hovered] ?? null
    for (let k = col.length - 1; k >= 0; k--) if (col[k] != null) return col[k]
    return null
  }
  const clock = hovered === null ? null : clockOf(cols[0][hovered])
  const lineValue = at(0)
  const shown = series.length - hidden.size

  return (
    <figure className="stats-chart" aria-label={title}>
      <figcaption>{caption ?? title}</figcaption>
      {controls}
      <div ref={host} />
      {legend === 'line' && (clock !== null || lineValue !== null) && (
        <p className="chart-legend">{clock !== null ? `${clock} · ${lineValue === null ? 'no value' : format(lineValue)}` : `latest ${format(lineValue)}`}</p>
      )}
      {legend === 'chips' && (
        <div className="chart-legend">
          {clock !== null && <span className="legend-time">{clock}</span>}
          {series.map((s, i) => {
            const on = !hidden.has(i)
            const last = on && shown === 1
            const v = at(i)
            return (
              <button key={i} type="button" className="legend-chip" aria-pressed={on} disabled={last}
                title={last ? 'The last shown series cannot be hidden' : undefined} onClick={() => toggle(i)}>
                <span aria-hidden="true">{on ? '✓' : '✗'}</span>
                <span className="legend-swatch" style={{ background: s.color }} aria-hidden="true" />
                {s.label}{v !== null && <> <span className="legend-value">{format(v)}</span></>}
              </button>
            )
          })}
        </div>
      )}
    </figure>
  )
}
