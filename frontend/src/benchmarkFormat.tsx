import type { BenchmarkResult } from './api'

export function formatSeconds(value?: number | null) {
  if (value == null) return '—'
  return value >= 100 ? `${Math.round(value)} s` : `${value.toFixed(1)} s`
}

export function formatRate(value?: number | null) {
  if (value == null) return '—'
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} tok/s`
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
  return <span title={`Benchmark run on profile v${result.profile_version}, ${formatWhen(result.completed_at)}`}>
    {result.benchmark_title}{result.reasoning_level ? ` (reasoning ${reasoningLabel(result.reasoning_level).toLowerCase()})` : ''}: {formatRate(result.decode_tokens_per_second)} decode{result.decode_peak_tokens_per_second != null ? ` (peak ${formatRate(result.decode_peak_tokens_per_second)})` : ''} · {formatRate(result.prompt_tokens_per_second)} prefill · load {formatSeconds(result.load_seconds)}{result.outcome ? ` · ${result.outcome === 'completed' ? 'task complete' : 'unfinished'}` : ''}
  </span>
}
