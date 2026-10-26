import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type OpenRouterModel } from './api'
import { OpenRouterModelPicker } from './OpenRouterModelPicker'
import { contextSummary, priceLines, priceSummary } from './openRouterPricing'

const sonnet: OpenRouterModel = {
  id: 'anthropic/claude-sonnet-5.5',
  name: 'Anthropic: Claude Sonnet 5.5',
  description: '',
  context_length: 1_000_000,
  pricing: { prompt: '0.000003', completion: '0.000015', input_cache_read: '0.0000003', web_search: '0.01' },
}
const free: OpenRouterModel = { id: 'qwen/qwen3.8-flash:free', name: 'Qwen: Qwen3.8 Flash (free)', description: '', context_length: 262_144, pricing: { prompt: '0', completion: '0' } }

function renderPicker(canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><OpenRouterModelPicker canManage={canManage} /></QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('openRouterPricing', () => {
  it('shows per-token prices per million tokens and request prices as they are', () => {
    expect(priceLines(sonnet.pricing)).toEqual([
      { label: 'Input', value: '$3.00 / 1M tokens' },
      { label: 'Output', value: '$15.00 / 1M tokens' },
      { label: 'Cache read', value: '$0.30 / 1M tokens' },
      { label: 'Web search', value: '$0.01 / search' },
    ])
    expect(priceLines({ prompt: '0.0000000025', completion: '-1' })).toEqual([
      { label: 'Input', value: '$0.0025 / 1M tokens' },
      { label: 'Output', value: 'Varies' },
    ])
    expect(priceSummary(sonnet.pricing)).toBe('$3.00 in · $15.00 out per 1M tokens')
    expect(priceSummary(free.pricing)).toBe('Free')
    expect(priceSummary({ prompt: '-1', completion: '-1' })).toBe('Price varies by request')
    expect(priceSummary({})).toBe('Pricing unknown')
    expect(contextSummary(sonnet)).toBe('1M context')
    expect(contextSummary({ context_length: null })).toBeNull()
  })
})

describe('OpenRouterModelPicker', () => {
  it('searches, selects a model and shows the pricing saved with it', async () => {
    const selected = vi.spyOn(api, 'openRouterModel').mockResolvedValue({ model: null })
    const search = vi.spyOn(api, 'openRouterModels').mockResolvedValue({ models: [sonnet, free], total: 2 })
    const select = vi.spyOn(api, 'selectOpenRouterModel').mockResolvedValue({ model: { ...sonnet, selected_at: '2026-10-26T09:00:00+00:00' } })
    renderPicker()

    expect(await screen.findByText('No model selected')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search OpenRouter models' }), { target: { value: 'claude' } })
    expect(await screen.findByText('1M context · $3.00 in · $15.00 out per 1M tokens')).toBeInTheDocument()
    expect(search).toHaveBeenCalledWith('claude')
    expect(screen.getByText('262K context · Free')).toBeInTheDocument()

    selected.mockResolvedValue({ model: { ...sonnet, selected_at: '2026-10-26T09:00:00+00:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Select Anthropic: Claude Sonnet 5.5' }))
    await waitFor(() => expect(select.mock.calls[0]?.[0]).toBe('anthropic/claude-sonnet-5.5'))
    expect(await screen.findByText('Selected', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText('$15.00 / 1M tokens')).toBeInTheDocument()
    expect(screen.getByText(/^Prices as of /)).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: 'Search OpenRouter models' })).toHaveValue('')
  })

  it('refreshes the saved price and clears the model', async () => {
    vi.spyOn(api, 'openRouterModel').mockResolvedValue({ model: { ...sonnet, selected_at: '2026-10-01T09:00:00+00:00' } })
    const select = vi.spyOn(api, 'selectOpenRouterModel').mockResolvedValue({ model: { ...sonnet, selected_at: '2026-10-26T09:00:00+00:00' } })
    const clear = vi.spyOn(api, 'clearOpenRouterModel').mockResolvedValue({ model: null })
    renderPicker()

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh price' }))
    await waitFor(() => expect(select.mock.calls[0]?.[0]).toBe('anthropic/claude-sonnet-5.5'))
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() => expect(clear).toHaveBeenCalled())
  })

  it('lets anyone search but needs settings.write to choose', async () => {
    vi.spyOn(api, 'openRouterModel').mockResolvedValue({ model: null })
    vi.spyOn(api, 'openRouterModels').mockResolvedValue({ models: [sonnet], total: 60 })
    renderPicker(false)

    fireEvent.change(await screen.findByRole('searchbox', { name: 'Search OpenRouter models' }), { target: { value: 'a' } })
    const choose = await screen.findByRole('button', { name: 'Select Anthropic: Claude Sonnet 5.5' })
    expect(choose).toBeDisabled()
    expect(choose).toHaveAttribute('title', 'Changing settings needs the settings.write privilege')
    expect(screen.getByText('Showing 1 of 60 matches. Keep typing to narrow them down.')).toBeInTheDocument()
  })

  it('says when OpenRouter cannot be reached', async () => {
    vi.spyOn(api, 'openRouterModel').mockResolvedValue({ model: null })
    vi.spyOn(api, 'openRouterModels').mockRejectedValue(new Error("OpenRouter's model list is unavailable"))
    renderPicker()

    fireEvent.change(await screen.findByRole('searchbox', { name: 'Search OpenRouter models' }), { target: { value: 'gpt' } })
    expect(await screen.findByText("OpenRouter's model list is unavailable")).toBeInTheDocument()
  })
})
