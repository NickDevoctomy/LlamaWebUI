import type { OpenRouterModel } from './api'

/** Prices OpenRouter quotes per token, shown per million tokens. */
const TOKEN_PRICES: [key: string, label: string][] = [
  ['prompt', 'Input'],
  ['completion', 'Output'],
  ['input_cache_read', 'Cache read'],
  ['input_cache_write', 'Cache write'],
  ['internal_reasoning', 'Reasoning'],
]
/** Prices OpenRouter quotes per request or per search. */
const REQUEST_PRICES: [key: string, label: string, unit: string][] = [
  ['request', 'Request', 'request'],
  ['web_search', 'Web search', 'search'],
]

function dollars(value: number) {
  if (value >= 0.01) return `$${value.toFixed(2)}`
  return `$${Number(value.toPrecision(2))}`
}

/** One price per line item, for example { label: 'Input', value: '$3.00 / 1M tokens' }. */
export function priceLines(pricing: Record<string, string>) {
  const lines: { label: string; value: string }[] = []
  for (const [key, label] of TOKEN_PRICES) {
    const price = Number(pricing[key])
    if (pricing[key] === undefined || Number.isNaN(price)) continue
    lines.push({ label, value: price < 0 ? 'Varies' : price === 0 ? 'Free' : `${dollars(price * 1_000_000)} / 1M tokens` })
  }
  for (const [key, label, unit] of REQUEST_PRICES) {
    const price = Number(pricing[key])
    if (!price || Number.isNaN(price)) continue
    lines.push({ label, value: price < 0 ? 'Varies' : `${dollars(price)} / ${unit}` })
  }
  return lines
}

/** A short input and output price, for search results. */
export function priceSummary(pricing: Record<string, string>) {
  const input = Number(pricing.prompt)
  const output = Number(pricing.completion)
  if (Number.isNaN(input) || Number.isNaN(output)) return 'Pricing unknown'
  if (input < 0 || output < 0) return 'Price varies by request'
  if (input === 0 && output === 0) return 'Free'
  return `${dollars(input * 1_000_000)} in · ${dollars(output * 1_000_000)} out per 1M tokens`
}

export function contextSummary(model: Pick<OpenRouterModel, 'context_length'>) {
  if (!model.context_length) return null
  return `${new Intl.NumberFormat('en', { notation: 'compact' }).format(model.context_length)} context`
}
