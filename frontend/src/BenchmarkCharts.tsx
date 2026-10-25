import { useRef, useState, type PointerEvent } from 'react'
import type { BenchmarkResource } from './api'
import { formatSeconds } from './benchmarkFormat'
import { formatBytes } from './Dashboard'

type Series = {
  key: string
  label: string
  values: (number | null)[]
  /** How a value reads in the legend and tooltip; index is the sample it came from. */
  format: (value: number, index: number) => string
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

/** The last reading a series has, and where it was taken. */
function latest(values: (number | null)[]) {
  for (let index = values.length - 1; index >= 0; index--) {
    const value = values[index]
    if (value != null) return { value, index }
  }
  return undefined
}

/**
 * Values over a run's real time, on one shared axis from zero. Hovering shows the
 * readings nearest the pointer; a chart of more than one series names each in a legend.
 */
function TimeChart({ title, times, series, max, formatAxis, summary }: { title: string; times: number[]; series: Series[]; max: number; formatAxis: (value: number) => string; summary: string }) {
  const plot = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number>()
  const end = Math.max(times.at(-1) ?? 0, 1)
  const x = (t: number) => t / end * 100
  const y = (value: number) => 100 - Math.min(value / max, 1) * 100
  const single = series.length === 1
  const point = (event: PointerEvent) => {
    const box = plot.current?.getBoundingClientRect()
    if (!box?.width) return
    const t = (event.clientX - box.left) / box.width * end
    let nearest = 0
    times.forEach((time, index) => { if (Math.abs(time - t) < Math.abs(times[nearest] - t)) nearest = index })
    setHover(nearest)
  }
  const at = hover != null && hover < times.length ? hover : undefined
  return <figure aria-label={`${title}. ${summary}`} className="time-chart" data-single={single || undefined}>
    <figcaption className="time-chart-head">
      <span>{title}</span>
      {!single && <ul className="time-chart-legend">
        {series.map((item) => {
          const last = latest(item.values)
          return <li data-series={item.key} key={item.key}><i aria-hidden="true" />{item.label}{last && <span>{item.format(last.value, last.index)}</span>}</li>
        })}
      </ul>}
    </figcaption>
    <div aria-hidden="true" className="time-chart-body">
      <div className="time-chart-axis">
        <span>{formatAxis(max)}</span>
        <span>{formatAxis(max / 2)}</span>
        <span>{formatAxis(0)}</span>
      </div>
      <div className="time-chart-plot" onPointerLeave={() => setHover(undefined)} onPointerMove={point} ref={plot}>
        <svg preserveAspectRatio="none" viewBox="0 0 100 100">
          {[0, 50, 100].map((level) => <line className="time-chart-grid" key={level} vectorEffect="non-scaling-stroke" x1="0" x2="100" y1={level} y2={level} />)}
          {series.map((item) => segments(times, item.values).map((run, index) => {
            const line = run.map(([t, value]) => `${x(t).toFixed(2)},${y(value).toFixed(2)}`).join(' ')
            return <g data-series={item.key} key={`${item.key}-${index}`}>
              {single && run.length > 1 && <polygon className="time-chart-area" points={`${x(run[0][0]).toFixed(2)},100 ${line} ${x(run[run.length - 1][0]).toFixed(2)},100`} />}
              <polyline className="time-chart-line" points={run.length > 1 ? line : `${line} ${line}`} vectorEffect="non-scaling-stroke" />
            </g>
          }))}
        </svg>
        {at != null && <>
          <span className="time-chart-cursor" style={{ left: `${x(times[at])}%` }} />
          {series.map((item) => item.values[at] != null && <span className="time-chart-dot" data-series={item.key} key={item.key} style={{ left: `${x(times[at])}%`, top: `${y(item.values[at]!)}%` }} />)}
          <div className="time-chart-tip" data-flip={x(times[at]) > 55 || undefined} style={{ left: `${x(times[at])}%` }}>
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

/** The model process's RAM and VRAM over the run, on one byte axis. */
export function MemoryChart({ resources }: { resources: BenchmarkResource[] }) {
  const measured = resources.filter((item) => item.ram_bytes != null || item.vram_bytes != null)
  if (measured.length < 2) return null
  const vram = measured.map((item) => item.vram_bytes ?? null)
  const ram = measured.map((item) => item.ram_bytes ?? null)
  const highest = Math.max(...vram.map((value) => value ?? 0), ...ram.map((value) => value ?? 0))
  const peak = (values: (number | null)[]) => Math.max(...values.map((value) => value ?? 0))
  const series: Series[] = [
    { key: 'vram', label: 'VRAM', values: vram, format: (value: number) => formatBytes(value) },
    { key: 'ram', label: 'RAM', values: ram, format: (value: number) => formatBytes(value) },
  ].filter((item) => item.values.some((value) => value != null))
  return <TimeChart
    formatAxis={(value) => value ? formatBytes(value) : '0'}
    max={byteCeiling(highest)}
    series={series}
    summary={series.map((item) => `${item.label} peaked at ${formatBytes(peak(item.values))}`).join('; ') + '.'}
    times={measured.map((item) => item.t)}
    title={series.length === 1 ? `Model ${series[0].label} over the run` : 'Model memory over the run'}
  />
}
