import { useRef, useState, type PointerEvent } from 'react'

/** A chart from a report: one value (or null) per category for each series. */
export interface ReportChartSpec {
  type: 'line' | 'bar'
  title: string
  categories: string[]
  series: { name: string; values: (number | null)[] }[]
  y_label: string
  x_label?: string | null
  targets?: { label: string; value: number }[]
}

/** The spec if the JSON is a chart the renderer can draw, otherwise undefined. */
export function parseChart(text: string): ReportChartSpec | undefined {
  try {
    const value = JSON.parse(text) as Partial<ReportChartSpec>
    const valid = (value.type === 'line' || value.type === 'bar')
      && typeof value.title === 'string'
      && Array.isArray(value.categories) && value.categories.length > 0
      && Array.isArray(value.series) && value.series.length > 0
      && value.series.every((series) => Array.isArray(series.values) && series.values.length === value.categories!.length)
    return valid ? value as ReportChartSpec : undefined
  } catch {
    return undefined
  }
}

/** A round top for the axis: 1, 2, 2.5 or 5 times a power of ten, above the highest value. */
export function niceCeiling(highest: number) {
  if (!(highest > 0)) return 1
  const target = highest * 1.08
  const power = 10 ** Math.floor(Math.log10(target))
  return ([1, 2, 2.5, 5, 10].find((factor) => factor * power >= target) ?? 10) * power
}

const format = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: Math.abs(value) >= 100 ? 1 : 2 })

/**
 * A line or bar chart over named categories (for example the change each benchmark
 * measured), from zero on one axis, with the brief's targets as dashed lines. Hovering
 * a category shows every series' value; the data table below gives the same numbers.
 */
export function ReportChart({ chart }: { chart: ReportChartSpec }) {
  const plot = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number>()
  const { categories, series } = chart
  const targets = chart.targets ?? []
  const values = series.flatMap((item) => item.values.filter((value): value is number => value != null))
  const max = niceCeiling(Math.max(...values, ...targets.map((target) => target.value), 0))
  const count = categories.length
  // Each category owns an equal slot; lines and markers sit at its centre.
  const x = (index: number) => (index + 0.5) / count * 100
  const y = (value: number) => 100 - Math.max(0, Math.min(value / max, 1)) * 100
  const single = series.length === 1
  const point = (event: PointerEvent) => {
    const box = plot.current?.getBoundingClientRect()
    if (!box?.width) return
    setHover(Math.min(count - 1, Math.max(0, Math.floor((event.clientX - box.left) / box.width * count))))
  }
  const flip = hover != null && x(hover) > 60
  const summary = series.map((item) => {
    const known = item.values.filter((value): value is number => value != null)
    return known.length ? `${item.name} from ${format(known[0])} to ${format(known.at(-1)!)} ${chart.y_label}` : `${item.name}: no values`
  }).join('; ')
  return <figure aria-label={`${chart.title}. ${summary}.`} className="report-chart">
    <figcaption className="time-chart-head">
      <span className="report-chart-title">{chart.title}</span>
      {!single && <ul className="time-chart-legend">
        {series.map((item, index) => <li data-slot={index + 1} key={item.name}><i aria-hidden="true" />{item.name}</li>)}
      </ul>}
    </figcaption>
    <div aria-hidden="true" className="report-chart-body">
      <div className="report-chart-axis">
        <span>{format(max)}</span>
        <span>{format(max / 2)}</span>
        <span>0</span>
      </div>
      <div className="report-chart-plot" onPointerLeave={() => setHover(undefined)} onPointerMove={point} ref={plot}>
        <svg preserveAspectRatio="none" viewBox="0 0 100 100">
          {[0, 50, 100].map((level) => <line className="time-chart-grid" key={level} vectorEffect="non-scaling-stroke" x1="0" x2="100" y1={level} y2={level} />)}
          {targets.map((target) => <line className="report-chart-target" key={target.label} vectorEffect="non-scaling-stroke" x1="0" x2="100" y1={y(target.value)} y2={y(target.value)} />)}
          {chart.type === 'line' && series.map((item, index) => {
            const points = item.values.map((value, at) => value == null ? null : `${x(at).toFixed(2)},${y(value).toFixed(2)}`)
            // A null leaves a gap rather than joining across it.
            const runs = points.reduce<string[][]>((all, value) => {
              if (value == null) all.push([])
              else all[all.length - 1].push(value)
              return all
            }, [[]]).filter((run) => run.length > 1)
            return runs.map((run, runIndex) => <polyline className="report-chart-line" data-slot={index + 1} key={`${item.name}-${runIndex}`} points={run.join(' ')} vectorEffect="non-scaling-stroke" />)
          })}
        </svg>
        {chart.type === 'line' && series.map((item, index) => item.values.map((value, at) => value != null && <span className="report-chart-marker" data-active={at === hover || undefined} data-slot={index + 1} key={`${item.name}-${at}`} style={{ left: `${x(at)}%`, top: `${y(value)}%` }} />))}
        {chart.type === 'bar' && categories.map((category, at) => <div className="report-chart-group" key={category + at} style={{ left: `${at / count * 100}%`, width: `${100 / count}%` }}>
          {series.map((item, index) => {
            const value = item.values[at]
            return <span className="report-chart-bar" data-active={at === hover || undefined} data-slot={index + 1} key={item.name} style={{ height: value == null ? 0 : `${100 - y(value)}%` }} />
          })}
        </div>)}
        {targets.map((target) => <span className="report-chart-target-label" key={target.label} style={{ top: `${y(target.value)}%` }}>{target.label} {format(target.value)}</span>)}
        {hover != null && <>
          {chart.type === 'line' && <span className="time-chart-cursor" style={{ left: `${x(hover)}%` }} />}
          <div className="time-chart-tip" data-flip={flip || undefined} style={{ left: `${x(hover)}%` }}>
            <strong>{categories[hover]}</strong>
            {series.map((item, index) => <span data-slot={index + 1} key={item.name}><i />{item.name}<b>{item.values[hover] != null ? `${format(item.values[hover]!)} ${chart.y_label}` : '—'}</b></span>)}
          </div>
        </>}
      </div>
      <div className="report-chart-categories" style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}>
        {categories.map((category, at) => <span key={category + at} title={category}>{category}</span>)}
      </div>
      {chart.x_label && <span className="report-chart-x-label">{chart.x_label}</span>}
    </div>
    <details className="report-chart-table">
      <summary>Data table</summary>
      <table>
        <thead><tr><th scope="col">{chart.x_label ?? 'Step'}</th>{series.map((item) => <th key={item.name} scope="col">{item.name} ({chart.y_label})</th>)}</tr></thead>
        <tbody>{categories.map((category, at) => <tr key={category + at}><th scope="row">{category}</th>{series.map((item) => <td key={item.name}>{item.values[at] != null ? format(item.values[at]!) : '—'}</td>)}</tr>)}</tbody>
      </table>
    </details>
  </figure>
}
