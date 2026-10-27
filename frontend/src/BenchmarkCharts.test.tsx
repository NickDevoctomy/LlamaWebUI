import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ContextChart, MemoryChart, SpeedChart } from './BenchmarkCharts'
import type { BenchmarkResource, BenchmarkRunDetail } from './api'

const GIB = 1024 ** 3

const resources: BenchmarkResource[] = [
  { t: 0, context_used: 1024, context_size: 8192, ram_bytes: 2 * GIB, vram_bytes: 10 * GIB },
  { t: 2, context_used: 4096, context_size: 8192, vram_bytes: 11 * GIB },
  { t: 4, context_used: 2048, context_size: 8192, ram_bytes: 3 * GIB, vram_bytes: 12 * GIB },
]

function hover(chart: HTMLElement, clientX: number) {
  const plot = chart.querySelector('.time-chart-plot') as HTMLElement
  plot.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 96, right: 400, bottom: 96, x: 0, y: 0, toJSON: () => ({}) })
  fireEvent.pointerMove(plot, { clientX })
}

describe('benchmark resource charts', () => {
  it('draws prefill and decode speed on one log axis, with each 1% high across it', () => {
    const run = {
      samples: [
        { t: 0.5, phase: 'prefill', tokens: 900, rate: 1800 },
        { t: 2, phase: 'decode', tokens: 100, rate: 110 },
        { t: 3, phase: 'decode', tokens: 230, rate: 130 },
        { t: 4, phase: 'prefill', tokens: 1300, rate: 2400 },
        { t: 5, phase: 'decode', tokens: 360, rate: 120 },
      ],
      stats: { prompt_tokens_per_second: 2000, prompt_high_tokens_per_second: 2600, decode_tokens_per_second: 118, decode_high_tokens_per_second: 135 },
    } as unknown as BenchmarkRunDetail
    render(<SpeedChart run={run} />)

    const chart = screen.getByRole('figure', { name: 'Speed over the run, tok/s (log scale). Decode averaged 118 tok/s; Prefill averaged 2,000 tok/s.' })
    // Powers of ten from under the slowest reading to over the fastest.
    expect([...chart.querySelectorAll('.time-chart-axis span')].map((item) => item.textContent)).toEqual(['10,000', '1,000', '100'])
    const legend = chart.querySelector('.time-chart-legend') as HTMLElement
    expect([...legend.querySelectorAll('li')].map((item) => item.textContent)).toEqual(['Decode120 tok/s', 'Prefill2,400 tok/s', 'Decode 1% high135 tok/s', 'Prefill 1% high2,600 tok/s'])
    expect(chart.querySelectorAll('.time-chart-reference')).toHaveLength(2)
    // Prefill between decode readings breaks the decode line into its rounds.
    expect(chart.querySelectorAll('g[data-series="decode"]')).toHaveLength(2)
    hover(chart, 240)
    const tip = chart.querySelector('.time-chart-tip') as HTMLElement
    expect(tip).toHaveTextContent('At 3.0 s')
    expect(tip).toHaveTextContent('Decode130 tok/s')
    expect(tip).toHaveTextContent('Prefill—')
  })

  it('draws no speed chart without two samples', () => {
    const { container } = render(<SpeedChart run={{ samples: [{ t: 1, phase: 'decode', tokens: 1, rate: 20 }], stats: {} } as unknown as BenchmarkRunDetail} />)
    expect(container.querySelector('.time-chart')).toBeNull()
  })

  it('shows context use as a share of the context, with readings on hover', () => {
    render(<ContextChart resources={resources} />)

    const chart = screen.getByRole('figure', { name: 'Context used over the run. Peaked at 50% and ended at 25%.' })
    expect(within(chart).getByText('100%')).toBeInTheDocument()
    // One series: the title names it, so there is no legend.
    expect(chart.querySelector('.time-chart-legend')).toBeNull()
    hover(chart, 200)
    expect(within(chart).getByText('At 2.0 s')).toBeInTheDocument()
    expect(within(chart).getByText('50% (4,096 / 8,192 tokens)')).toBeInTheDocument()
  })

  it('draws RAM and VRAM on one byte axis, leaving a gap where a reading is missing', () => {
    render(<MemoryChart resources={resources} />)

    const chart = screen.getByRole('figure', { name: /^Model memory over the run\. VRAM peaked at .+; RAM peaked at .+\.$/ })
    const legend = chart.querySelector('.time-chart-legend') as HTMLElement
    expect(within(legend).getByText('VRAM')).toBeInTheDocument()
    expect(within(legend).getByText('RAM')).toBeInTheDocument()
    // The missing RAM reading splits its line in two.
    expect(chart.querySelectorAll('g[data-series="ram"]')).toHaveLength(2)
    expect(chart.querySelectorAll('g[data-series="vram"]')).toHaveLength(1)
    hover(chart, 210)
    const tip = chart.querySelector('.time-chart-tip') as HTMLElement
    expect(within(tip).getByText('—')).toBeInTheDocument()
  })

  it('adds what the whole machine was using alongside the model', () => {
    const withTotals = resources.map((item, index) => ({ ...item, vram_total_bytes: (14 + index) * GIB, ram_total_bytes: 30 * GIB }))
    render(<MemoryChart resources={withTotals} />)

    const chart = screen.getByRole('figure', { name: /^Memory over the run\. VRAM peaked at .+; VRAM Total peaked at 16\.0 GB; RAM peaked at .+; RAM Total peaked at 30\.0 GB\.$/ })
    const legend = chart.querySelector('.time-chart-legend') as HTMLElement
    expect([...legend.querySelectorAll('li')].map((item) => item.dataset.series)).toEqual(['vram', 'vram-total', 'ram', 'ram-total'])
    expect(within(legend).getByText('VRAM Total')).toBeInTheDocument()
    expect(within(legend).getByText('RAM Total')).toBeInTheDocument()
  })

  it('names the one series it has, and draws nothing without two readings', () => {
    const cpuOnly = resources.map(({ t, ram_bytes }) => ({ t, ram_bytes: ram_bytes ?? GIB }))
    const { container, rerender } = render(<MemoryChart resources={cpuOnly} />)
    expect(screen.getByRole('figure', { name: /^Model RAM over the run\./ })).toBeInTheDocument()

    rerender(<><MemoryChart resources={resources.slice(0, 1)} /><ContextChart resources={[{ t: 0 }, { t: 2 }]} /></>)
    expect(container).toBeEmptyDOMElement()
  })
})
