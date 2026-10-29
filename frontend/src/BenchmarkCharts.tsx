import { useRef, useState, type PointerEvent } from 'react'
import type { BenchmarkResource, BenchmarkRunDetail } from './api'
import { formatRate, formatSeconds, phaseSpeeds } from './benchmarkFormat'
import { formatBytes } from './Dashboard'

type Series = {
  key: string
  label: string
  values: (number | null)[]
  /** How a value reads in the tooltip; index is the sample it came from. */
  format: (value: number, index: number) => string
}

/** A level drawn across the whole run, such as a 1% high, named in the legend. */
type Reference = { key: string; label: string; value: number }

/** Where a value sits on the axis, from 0 at the bottom to 1 at the top. */
type Scale = { position: (value: number) => number; ticks: number[] }

function linearScale(max: number): Scale {
  return { position: (value) => Math.min(Math.max(value / max, 0), 1), ticks: [max, max / 2, 0] }
}

/** Powers of ten from just under the smallest value to just over the largest, evenly spaced. */
function logScale(smallest: number, largest: number): Scale {
  const low = Math.floor(Math.log10(Math.max(smallest, 1e-3)))
  const high = Math.max(Math.ceil(Math.log10(Math.max(largest, 1e-3))), low + 1)
  const ticks = Array.from({ length: high - low + 1 }, (_, index) => 10 ** (high - index))
  return { position: (value) => value > 0 ? Math.min(Math.max((Math.log10(value) - low) / (high - low), 0), 1) : 0, ticks }
}

/** Runs of consecutive readings, so a missed one leaves a gap rather than a false line. */
function segments(times: number[], values: (number | null)[]) {
  const runs: [number, number][][] = []
  let current: [number, number][] = []
  values.forEach((value, index) => {
    if (value == null) {
      if (current.length) runs.push(current)
      current = []
    } else current.push([times[index], value])
  })
  if (current.length) runs.push(current)
  return runs
}

/**
 * Values over a run's real time, on one shared axis from zero. Hovering shows the
 * readings nearest the pointer; a chart of more than one series names each in a legend,
 * without values: the run's figures are shown above the chart.
 */
function TimeChart({ title, times, series, max, scale: given, references = [], formatAxis, summary }: { title: string; times: number[]; series: Series[]; max?: number; scale?: Scale; references?: Reference[]; formatAxis: (value: number) => string; summary: string }) {
  const scale = given ?? linearScale(max ?? 1)
  const plot = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number>()
  const end = Math.max(times.at(-1) ?? 0, 1)
  const x = (t: number) => t / end * 100
  const y = (value: number) => 100 - scale.position(value) * 100
  const single = series.length === 1 && !references.length
  const grid = scale.ticks.map((_, index) => index / Math.max(scale.ticks.length - 1, 1) * 100)
  const point = (event: PointerEvent) => {
    const box = plot.current?.getBoundingClientRect()
    if (!box?.width) return
    const t = (event.clientX - box.left) / box.width * end
    let nearest = 0
    times.forEach((time, index) => { if (Math.abs(time - t) < Math.abs(times[nearest] - t)) nearest = index })
    setHover(nearest)
  }
  const at = hover != null && hover < times.length ? hover : undefined
  // The tip opens right of the cursor unless it would run past the plot's edge.
  const width = plot.current?.clientWidth
  const flip = at != null && (width ? x(times[at]) / 100 * width + 200 > width : x(times[at]) > 55)
  return <figure aria-label={`${title}. ${summary}`} className="time-chart" data-single={single || undefined}>
    <figcaption className="time-chart-head">
      <span>{title}</span>
      {!single && <ul className="time-chart-legend">
        {series.map((item) => <li data-series={item.key} key={item.key}><i aria-hidden="true" />{item.label}</li>)}
        {references.map((item) => <li data-reference data-series={item.key} key={`${item.key}-reference`}><i aria-hidden="true" />{item.label}</li>)}
      </ul>}
    </figcaption>
    <div aria-hidden="true" className="time-chart-body">
      <div className="time-chart-axis">
        {scale.ticks.map((tick) => <span key={tick}>{formatAxis(tick)}</span>)}
      </div>
      <div className="time-chart-plot" onPointerLeave={() => setHover(undefined)} onPointerMove={point} ref={plot}>
        <svg preserveAspectRatio="none" viewBox="0 0 100 100">
          {grid.map((level) => <line className="time-chart-grid" key={level} vectorEffect="non-scaling-stroke" x1="0" x2="100" y1={level} y2={level} />)}
          {references.map((item) => <line className="time-chart-reference" data-series={item.key} key={`${item.key}-reference`} vectorEffect="non-scaling-stroke" x1="0" x2="100" y1={y(item.value)} y2={y(item.value)} />)}
          {series.map((item) => segments(times, item.values).map((run, index) => {
            const line = run.map(([t, value]) => `${x(t).toFixed(2)},${y(value).toFixed(2)}`).join(' ')
            return <g data-series={item.key} key={`${item.key}-${index}`}>
              {single && run.length > 1 && <polygon className="time-chart-area" points={`${x(run[0][0]).toFixed(2)},100 ${line} ${x(run[run.length - 1][0]).toFixed(2)},100`} />}
              <polyline className="time-chart-line" data-point={run.length === 1 || undefined} points={run.length > 1 ? line : `${line} ${line}`} vectorEffect="non-scaling-stroke" />
            </g>
          }))}
        </svg>
        {at != null && <>
          <span className="time-chart-cursor" style={{ left: `${x(times[at])}%` }} />
          {series.map((item) => item.values[at] != null && <span className="time-chart-dot" data-series={item.key} key={item.key} style={{ left: `${x(times[at])}%`, top: `${y(item.values[at]!)}%` }} />)}
          <div className="time-chart-tip" data-flip={flip || undefined} style={{ left: `${x(times[at])}%` }}>
            <strong>At {formatSeconds(times[at])}</strong>
            {series.map((item) => <span data-series={item.key} key={item.key}><i />{item.label}<b>{item.values[at] != null ? item.format(item.values[at]!, at) : '—'}</b></span>)}
          </div>
        </>}
      </div>
      <div className="time-chart-time"><span>0 s</span><span>{formatSeconds(times.at(-1) ?? 0)}</span></div>
    </div>
  </figure>
}

/** A round top for a byte axis: 1, 2 or 5 times a power of ten, in GiB, above the highest reading. */
function byteCeiling(highest: number) {
  const gib = 1024 ** 3
  const target = Math.max(highest / gib, 0.5) * 1.08
  const power = 10 ** Math.floor(Math.log10(target))
  const step = [1, 2, 2.5, 5, 10].find((factor) => factor * power >= target) ?? 10
  return step * power * gib
}

/** How full the model's context was over the run, as a share of its size. */
export function ContextChart({ resources }: { resources: BenchmarkResource[] }) {
  const measured = resources.filter((item) => item.context_used != null && item.context_size)
  if (measured.length < 2) return null
  const values = measured.map((item) => item.context_used! / item.context_size! * 100)
  const peak = Math.max(...values)
  return <TimeChart
    formatAxis={(value) => `${Math.round(value)}%`}
    max={100}
    series={[{ key: 'context', label: 'Context', values, format: (value, index) => `${Math.round(value)}% (${measured[index].context_used!.toLocaleString()} / ${measured[index].context_size!.toLocaleString()} tokens)` }]}
    summary={`Peaked at ${Math.round(peak)}% and ended at ${Math.round(values.at(-1)!)}%.`}
    times={measured.map((item) => item.t)}
    title="Context used over the run"
  />
}

const memorySeries = [
  { key: 'vram', label: 'VRAM', read: (item: BenchmarkResource) => item.vram_bytes },
  { key: 'vram-total', label: 'VRAM Total', read: (item: BenchmarkResource) => item.vram_total_bytes },
  { key: 'ram', label: 'RAM', read: (item: BenchmarkResource) => item.ram_bytes },
  { key: 'ram-total', label: 'RAM Total', read: (item: BenchmarkResource) => item.ram_total_bytes },
]

/**
 * The model process's RAM and VRAM over the run, on one byte axis, with what the
 * whole machine was using alongside for reference.
 */
export function MemoryChart({ resources }: { resources: BenchmarkResource[] }) {
  const measured = resources.filter((item) => memorySeries.some(({ read }) => read(item) != null))
  if (measured.length < 2) return null
  const peak = (values: (number | null)[]) => Math.max(...values.map((value) => value ?? 0))
  const series: Series[] = memorySeries
    .map(({ key, label, read }) => ({ key, label, values: measured.map((item) => read(item) ?? null), format: (value: number) => formatBytes(value) }))
    .filter((item) => item.values.some((value) => value != null))
  const totals = series.some((item) => item.key.endsWith('-total'))
  return <TimeChart
    formatAxis={(value) => value ? formatBytes(value) : '0'}
    max={byteCeiling(Math.max(...series.map((item) => peak(item.values))))}
    series={series}
    summary={series.map((item) => `${item.label} peaked at ${formatBytes(peak(item.values))}`).join('; ') + '.'}
    times={measured.map((item) => item.t)}
    title={totals ? 'Memory over the run' : series.length === 1 ? `Model ${series[0].label} over the run` : 'Model memory over the run'}
  />
}

/**
 * Prefill and decode speed over the run on one log axis, as prefill runs tens of times
 * faster than decode, with each phase's 1% high drawn across for reference. Samples come
 * as each phase happens, so a line breaks where the other phase or a tool ran.
 */
export function SpeedChart({ run }: { run: BenchmarkRunDetail }) {
  if (run.samples.length < 2) return null
  const times = [...new Set(run.samples.map((sample) => sample.t))].sort((a, b) => a - b)
  const phases = [
    { key: 'decode', label: 'Decode', phase: 'decode' as const },
    { key: 'prefill', label: 'Prefill', phase: 'prefill' as const },
  ]
  const series: Series[] = phases
    .map(({ key, label, phase }) => {
      const byTime = new Map(run.samples.filter((sample) => sample.phase === phase).map((sample) => [sample.t, sample.rate]))
      return { key, label, values: times.map((t) => byTime.get(t) ?? null), format: (value: number) => formatRate(value) }
    })
    .filter((item) => item.values.some((value) => value != null))
  const references: Reference[] = phases.flatMap(({ key, label, phase }) => {
    const high = phaseSpeeds(run.stats, phase).high
    return high != null && series.some((item) => item.key === key) ? [{ key, label: `${label} 1% high`, value: high }] : []
  })
  const values = [...series.flatMap((item) => item.values), ...references.map((item) => item.value)].filter((value): value is number => value != null && value > 0)
  if (!values.length) return null
  return <TimeChart
    formatAxis={(value) => value >= 1 ? Math.round(value).toLocaleString() : String(value)}
    references={references}
    scale={logScale(Math.min(...values), Math.max(...values))}
    series={series}
    summary={series.map((item) => `${item.label} averaged ${formatRate(phaseSpeeds(run.stats, item.key as 'decode' | 'prefill').average)}`).join('; ') + '.'}
    times={times}
    title="Speed over the run, tok/s (log scale)"
  />
}
