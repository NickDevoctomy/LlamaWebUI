import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type LibraryKind, type ReportDocument, type ResearchDocument, type ResearchSummary } from './api'
import { change, ReportsPanel, ResearchPanel } from './LibraryPanel'
import { niceCeiling, parseChart, ReportChart } from './ReportChart'

const local: ResearchSummary = {
  slug: 'ubatch-4090', title: 'Micro-batch on a 4090', summary: '512 beat 256 for prefill.', tags: ['batching'], researched: '2026-10-26', updated: '2026-10-26', confidence: 'medium', origin: 'local', replaces_shipped: false,
  sources: [{ title: 'llama.cpp README', url: 'https://github.com/ggml-org/llama.cpp', accessed: '2026-10-26' }, { title: 'Benchmark run abc' }],
  applies_to: { models: ['Qwen3.8-27B'], hardware: ['RTX 4090'], runtime: 'b11815' },
  author: { model: 'qwen/qwen3.8', conversation: 'c1' },
}
const shipped: ResearchSummary = { ...local, slug: 'flash-attention', title: 'Flash attention', origin: 'shipped', updated: '2026-03-01' }
const document: ResearchDocument = { ...local, body: '## Results\n\n512 wins.' }

const CHART = { type: 'line', title: 'Decode per change', categories: ['baseline', 'n-max 4', 'n-max 16'], series: [{ name: 'decode', values: [112.2, 120.6, null] }, { name: 'prefill', values: [1851, 2271, 2100] }], y_label: 'tok/s', targets: [{ label: 'target', value: 115 }] }
const report: ReportDocument = {
  slug: 'qwen-tuning', title: 'Tuning Qwen3.8 27B', summary: 'Decode up 7%.', tags: [], updated: '2026-10-26', created: '2026-10-26', origin: 'local', replaces_shipped: false, author: null,
  subject: { profile: 'qwen3.8-27b' }, hardware: ['RTX 4090'], runtime: 'b11815', benchmark: 'Tetris', outcome: 'improved',
  results: [{ metric: 'decode', before: 112.2, after: 120.6, unit: 'tok/s' }, { metric: 'load', before: null, after: 6 }],
  body: `## Changes\n\n\`\`\`chart\n${JSON.stringify(CHART)}\n\`\`\`\n\n\`\`\`chart\nnot json\n\`\`\``,
}

function renderPanel(panel: 'research' | 'reports' = 'research', canWrite = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Panel = panel === 'research' ? ResearchPanel : ReportsPanel
  return render(<QueryClientProvider client={client}><Panel canWrite={canWrite} /></QueryClientProvider>)
}

function mockLibrary(documents: unknown[], detail: unknown, problem: string | null = null) {
  const list = vi.spyOn(api, 'libraryDocuments').mockResolvedValue({ documents, publish_problem: problem } as never)
  vi.spyOn(api, 'libraryDocument').mockResolvedValue(detail as never)
  return list
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('ResearchPanel', () => {
  it('reads a document with its facts and sources, and publishes the selected ones', async () => {
    const list = mockLibrary([local, shipped], document)
    const publish = vi.spyOn(api, 'publishLibrary').mockResolvedValue({ published: ['ubatch-4090'], folder: 'E:/repo/research' })
    renderPanel()

    expect(await screen.findByRole('heading', { name: 'Micro-batch on a 4090' })).toBeInTheDocument()
    expect(screen.getByText('2 documents')).toBeInTheDocument()
    expect(screen.getByText('Written by').nextSibling).toHaveTextContent('qwen/qwen3.8')
    expect(screen.getByRole('link', { name: /llama.cpp README/ })).toHaveAttribute('href', 'https://github.com/ggml-org/llama.cpp')
    expect(within(screen.getByRole('region', { name: 'Sources' })).getByText('Benchmark run abc')).toBeInTheDocument()
    expect(screen.queryByLabelText('Select Flash attention for publishing')).not.toBeInTheDocument()

    const publishButton = screen.getByRole('button', { name: /to repository/ })
    expect(publishButton).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Select Micro-batch on a 4090 for publishing'))
    fireEvent.click(publishButton)
    await waitFor(() => expect(publish).toHaveBeenCalledWith('research', ['ubatch-4090']))
    expect(await screen.findByRole('status')).toHaveTextContent('Published 1 document to E:/repo/research')

    fireEvent.change(screen.getByLabelText('Search research'), { target: { value: 'cuda' } })
    await waitFor(() => expect(list).toHaveBeenLastCalledWith('research', 'cuda'))
  })

  it('explains why publishing is unavailable and deletes after confirming', async () => {
    mockLibrary([local], document, 'publishing needs LlamaWebUI to run from a source checkout')
    const remove = vi.spyOn(api, 'deleteLibraryDocument').mockResolvedValue(undefined)
    renderPanel()

    expect(await screen.findByText('Publishing is unavailable: publishing needs LlamaWebUI to run from a source checkout.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Micro-batch on a 4090' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete document' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('research', 'ubatch-4090'))
  })

  it('shows the empty library and empty searches', async () => {
    mockLibrary([], undefined)
    renderPanel('research', false)
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search research'), { target: { value: 'mtp' } })
    expect(await screen.findByText('No document mentions “mtp”.')).toBeInTheDocument()
  })
})

describe('ReportsPanel', () => {
  it('shows a report with its results and charts', async () => {
    const list = mockLibrary([report], report)
    renderPanel('reports')

    expect(await screen.findByRole('heading', { name: 'Tuning Qwen3.8 27B' })).toBeInTheDocument()
    expect(list).toHaveBeenCalledWith('reports', '')
    expect(screen.getByText('Improved · Local · 2026-10-26')).toBeInTheDocument()
    const results = screen.getByRole('table', { name: 'Results' })
    expect(within(results).getByText('+7.5%')).toBeInTheDocument()
    const chart = await screen.findByRole('figure', { name: /Decode per change/ })
    expect(chart).toHaveAccessibleName('Decode per change. decode from 112.2 to 120.6 tok/s; prefill from 1,851 to 2,100 tok/s.')
    expect(within(chart).getByText('target 115')).toBeInTheDocument()
    // A block that is not a valid chart stays as code.
    expect(screen.getByText('not json')).toBeInTheDocument()
  })

  it('shows the empty reports state', async () => {
    mockLibrary([], undefined)
    renderPanel('reports')
    expect(await screen.findByText('No reports yet')).toBeInTheDocument()
  })
})

describe('ReportChart', () => {
  it('parses specs, picks round axis tops and formats changes', () => {
    expect(parseChart(JSON.stringify(CHART))?.title).toBe('Decode per change')
    expect(parseChart('{"type":"pie"}')).toBeUndefined()
    expect(parseChart(JSON.stringify({ ...CHART, series: [{ name: 'x', values: [1] }] }))).toBeUndefined()
    expect(parseChart('nope')).toBeUndefined()
    expect(niceCeiling(0)).toBe(1)
    expect(niceCeiling(120.6)).toBe(200)
    expect(niceCeiling(2271)).toBe(2500)
    expect(change(100, 90)).toBe('-10.0%')
    expect(change(null, 90)).toBe('—')
  })

  it('shows every value for the hovered category, as bars or lines', () => {
    const kinds: LibraryKind[] = []
    expect(kinds).toEqual([])
    const { container, rerender } = render(<ReportChart chart={{ ...CHART, type: 'bar' } as never} />)
    const plot = container.querySelector('.report-chart-plot') as HTMLElement
    plot.getBoundingClientRect = () => ({ left: 0, width: 300, top: 0, height: 180, right: 300, bottom: 180, x: 0, y: 0, toJSON: () => ({}) })
    fireEvent.pointerMove(plot, { clientX: 150 })
    const tip = container.querySelector('.time-chart-tip') as HTMLElement
    expect(tip).toHaveTextContent('n-max 4')
    expect(tip).toHaveTextContent('120.6 tok/s')
    expect(container.querySelectorAll('.report-chart-bar[data-active]')).toHaveLength(2)
    fireEvent.pointerMove(plot, { clientX: 290 })
    expect(container.querySelector('.time-chart-tip')).toHaveTextContent('—')
    fireEvent.pointerLeave(plot)
    expect(container.querySelector('.time-chart-tip')).toBeNull()
    rerender(<ReportChart chart={{ ...CHART, series: [CHART.series[0]], x_label: 'Change' } as never} />)
    expect(screen.queryByRole('list')).toBeNull()
    expect(screen.getAllByText('Change').length).toBeGreaterThan(0)
  })
})
