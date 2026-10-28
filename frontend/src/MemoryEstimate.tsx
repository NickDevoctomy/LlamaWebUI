import { ChevronDown, MemoryStick } from 'lucide-react'
import type { MemoryEstimate, MemoryPart } from './api'
import { formatBytes } from './Dashboard'

/** "17.5 GB GPU · 680 MB RAM", leaving out a side that holds nothing. */
export function memorySummary(estimate: MemoryEstimate) {
  const sides = [
    estimate.gpu_bytes > 0 && `${formatBytes(estimate.gpu_bytes)} GPU`,
    (estimate.cpu_bytes > 0 || estimate.gpu_bytes === 0) && `${formatBytes(estimate.cpu_bytes)} RAM`,
  ].filter(Boolean)
  return sides.join(' · ')
}

function overCapacity(estimate: MemoryEstimate) {
  return estimate.gpu_capacity_bytes != null && estimate.gpu_bytes > estimate.gpu_capacity_bytes
}

function partLine(part: MemoryPart) {
  const sides = [part.gpu_bytes > 0 && `${formatBytes(part.gpu_bytes)} GPU`, part.cpu_bytes > 0 && `${formatBytes(part.cpu_bytes)} RAM`].filter(Boolean)
  return `${part.label}: ${sides.length ? sides.join(', ') : '0 B'}${part.approximate ? ' (approx.)' : ''}`
}

/** The row-level summary: hover for the parts, click to expand the full breakdown. */
export function MemoryEstimateToggle({ estimate, expanded, onToggle, controls }: {
  estimate: MemoryEstimate
  expanded: boolean
  onToggle: () => void
  controls: string
}) {
  const summary = memorySummary(estimate)
  const title = ['Estimated memory once loaded', ...estimate.parts.map(partLine), '', expanded ? 'Click to hide the breakdown' : 'Click for the full breakdown'].join('\n')
  return <button aria-controls={controls} aria-expanded={expanded} aria-label={`Estimated memory: ${summary}`} className={`memory-estimate-toggle${overCapacity(estimate) ? ' over' : ''}`} onClick={onToggle} title={title} type="button">
    <MemoryStick aria-hidden="true" size={13} /><span className="memory-estimate-value">≈ {summary}</span><ChevronDown aria-hidden="true" className="memory-estimate-chevron" size={13} />
  </button>
}

function Amount({ bytes }: { bytes: number }) {
  return bytes > 0 ? <>{formatBytes(bytes)}</> : <span className="memory-estimate-none">—</span>
}

/** A part's figures, then its explanation across the full width so it never squeezes. */
function PartRows({ part }: { part: MemoryPart }) {
  return <>
    <tr className="memory-row">
      <th scope="row">{part.label}{part.approximate && <span className="memory-approx" title="A rule of thumb rather than computed from the model"> ≈</span>}</th>
      <td><Amount bytes={part.gpu_bytes} /></td>
      <td><Amount bytes={part.cpu_bytes} /></td>
    </tr>
    <tr className="memory-detail"><td colSpan={3}>{part.detail}</td></tr>
  </>
}

/** Every part of an estimate, with where it sits, what grows later and any caveats. */
export function MemoryBreakdown({ estimate, id }: { estimate: MemoryEstimate, id?: string }) {
  const capacity = estimate.gpu_capacity_bytes
  const used = capacity ? Math.min(100, Math.round(estimate.gpu_bytes / capacity * 100)) : 0
  return <div className="memory-breakdown" id={id}>
    {capacity != null && estimate.gpu_bytes > 0 && <div className={`memory-capacity${overCapacity(estimate) ? ' over' : ''}`}>
      <span>GPU <strong>{formatBytes(estimate.gpu_bytes)}</strong> of {formatBytes(capacity)}{overCapacity(estimate) ? ' — more than the GPU has' : ''}</span>
      <span aria-hidden="true" className="memory-capacity-bar"><span style={{ width: `${used}%` }} /></span>
    </div>}
    <table className="memory-table">
      <thead><tr><th scope="col">Part</th><th scope="col">GPU</th><th scope="col">RAM</th></tr></thead>
      <tbody>{estimate.parts.map((part) => <PartRows key={part.id} part={part} />)}</tbody>
      <tfoot><tr><th scope="row">Total at load</th><td><Amount bytes={estimate.gpu_bytes} /></td><td><Amount bytes={estimate.cpu_bytes} /></td></tr></tfoot>
    </table>
    {estimate.growth.length > 0 && <table className="memory-table memory-growth">
      <caption>Can grow while it runs (not in the total)</caption>
      <tbody>{estimate.growth.map((part) => <PartRows key={part.id} part={part} />)}</tbody>
    </table>}
    {estimate.notes.length > 0 && <ul className="memory-notes">{estimate.notes.map((note) => <li key={note}>{note}</li>)}</ul>}
  </div>
}
