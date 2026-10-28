import type { BenchmarkResult, BenchmarkStats } from './api'

export function formatSeconds(value?: number | null) {
  if (value == null) return '—'
  return value >= 100 ? `${Math.round(value)} s` : `${value.toFixed(1)} s`
}

/** A speed without its unit, for figures already headed tok/s. */
export function formatRateValue(value?: number | null) {
  if (value == null) return '—'
  return value >= 100 ? Math.round(value).toLocaleString() : value.toFixed(1)
}

export function formatRate(value?: number | null) {
  if (value == null) return '—'
  return `${formatRateValue(value)} tok/s`
}

/** A phase's slowest 1%, its average over the whole run, and its fastest 1%, in that order. */
export const SPEED_FIGURES = [
  { key: 'low', label: '1% low', title: 'Average speed over the slowest 1% of the time' },
  { key: 'average', label: 'Average', title: 'Average over the whole run' },
  { key: 'high', label: '1% high', title: 'Average speed over the fastest 1% of the time' },
] as const

export type SpeedFigure = (typeof SPEED_FIGURES)[number]['key']

/**
 * Prefill or decode speeds of a run or result, keyed like SPEED_FIGURES. Older runs kept a
 * peak instead of a 1% high: decode's, over 3-second windows, stands in for it, but
 * prefill's came from single batches a cache hit could inflate a thousandfold, so it doesn't.
 */
export function phaseSpeeds(stats: BenchmarkStats, phase: 'prefill' | 'decode'): Record<SpeedFigure, number | undefined> {
  return phase === 'prefill'
    ? { low: stats.prompt_low_tokens_per_second, average: stats.prompt_tokens_per_second, high: stats.prompt_high_tokens_per_second }
    : { low: stats.decode_low_tokens_per_second, average: stats.decode_tokens_per_second, high: stats.decode_high_tokens_per_second ?? stats.decode_peak_tokens_per_second }
}

const REASONING_LABELS: Record<string, string> = {
  off: 'Off',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'Extra high',
  max: 'Max',
}

/** A reasoning level as shown to people; null is whatever the model's template defaults to. */
export function reasoningLabel(level?: string | null) {
  return level ? REASONING_LABELS[level] ?? level : 'Model default'
}

export function formatWhen(value: string | null) {
  if (!value) return '—'
  // Stored in UTC without an offset.
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/** The headline figures of a benchmark, as written onto a profile. */
export function BenchmarkSummary({ result }: { result: BenchmarkResult }) {
  const range = [
    result.decode_low_tokens_per_second != null && `1% low ${formatRate(result.decode_low_tokens_per_second)}`,
    phaseSpeeds(result, 'decode').high != null && `1% high ${formatRate(phaseSpeeds(result, 'decode').high)}`,
  ].filter(Boolean).join(', ')
  return <span title={`Benchmark run on profile v${result.profile_version}, ${formatWhen(result.completed_at)}`}>
    {result.benchmark_title}{result.reasoning_level ? ` (reasoning ${reasoningLabel(result.reasoning_level).toLowerCase()})` : ''}: {formatRate(result.decode_tokens_per_second)} decode{range ? ` (${range})` : ''} · {formatRate(result.prompt_tokens_per_second)} prefill · load {formatSeconds(result.load_seconds)}{result.outcome ? ` · ${result.outcome === 'completed' ? 'task complete' : 'unfinished'}` : ''}
  </span>
}
