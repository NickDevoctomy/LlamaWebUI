import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { MemoryEstimate } from './api'
import { MemoryBreakdown, MemoryEstimateToggle, memorySummary } from './MemoryEstimate'

const GIB = 1024 ** 3
const MIB = 1024 ** 2

const estimate: MemoryEstimate = {
  gpu_bytes: 17.5 * GIB,
  cpu_bytes: 680 * MIB,
  gpu_capacity_bytes: 24 * GIB,
  context_size: 131072,
  slots: 1,
  parts: [
    { id: 'weights', label: 'Model weights', gpu_bytes: 14.7 * GIB, cpu_bytes: 680 * MIB, detail: 'All 65 layers on the GPU.', approximate: false },
    { id: 'kv', label: 'KV cache', gpu_bytes: 2.25 * GIB, cpu_bytes: 0, detail: '131,072 tokens.', approximate: false },
    { id: 'compute', label: 'Compute buffers', gpu_bytes: 0.55 * GIB, cpu_bytes: 0, detail: 'Scratch space.', approximate: true },
  ],
  growth: [{ id: 'prompt-cache', label: 'Prompt cache', gpu_bytes: 0, cpu_bytes: 8 * GIB, detail: 'Up to this much RAM.', approximate: false }],
  notes: ['--fit is on.'],
}

function Toggle({ value }: { value: MemoryEstimate }) {
  const [open, setOpen] = useState(false)
  return <>
    <MemoryEstimateToggle controls="memory" estimate={value} expanded={open} onToggle={() => setOpen(!open)} />
    {open && <MemoryBreakdown estimate={value} id="memory" />}
  </>
}

afterEach(() => {
  cleanup()
})

describe('memory estimate', () => {
  it('summarises the GPU and RAM sides, leaving out an empty one', () => {
    expect(memorySummary(estimate)).toBe('17.5 GB GPU · 680 MB RAM')
    expect(memorySummary({ ...estimate, cpu_bytes: 0 })).toBe('17.5 GB GPU')
    expect(memorySummary({ ...estimate, gpu_bytes: 0, cpu_bytes: 2 * GIB })).toBe('2.0 GB RAM')
  })

  it('lists the parts on hover and expands the breakdown on click', () => {
    render(<Toggle value={estimate} />)
    const toggle = screen.getByRole('button', { name: 'Estimated memory: 17.5 GB GPU · 680 MB RAM' })

    expect(toggle).toHaveTextContent('≈ 17.5 GB GPU · 680 MB RAM')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle.title).toContain('Model weights: 14.7 GB GPU, 680 MB RAM')
    expect(toggle.title).toContain('Compute buffers: 563 MB GPU (approx.)')
    fireEvent.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const table = screen.getAllByRole('table')[0]
    const kv = within(table).getByRole('rowheader', { name: /KV cache/ }).closest('tr')!
    expect(within(kv).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['2.3 GB', '—'])
    expect(within(table).getByRole('rowheader', { name: /Total at load/ }).closest('tr')).toHaveTextContent('17.5 GB680 MB')
    expect(screen.getByText(/GPU/, { selector: '.memory-capacity span' })).toHaveTextContent('GPU 17.5 GB of 24.0 GB')
    expect(screen.getByRole('table', { name: /Can grow while it runs/ })).toHaveTextContent('Prompt cache')
    expect(screen.getByText('--fit is on.')).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('flags an estimate larger than the GPU', () => {
    render(<Toggle value={{ ...estimate, gpu_bytes: 30 * GIB }} />)
    const toggle = screen.getByRole('button', { name: /Estimated memory/ })

    expect(toggle).toHaveClass('over')
    fireEvent.click(toggle)
    expect(screen.getByText(/more than the GPU has/)).toBeInTheDocument()
  })

  it('leaves out the GPU meter for a CPU runtime', () => {
    render(<MemoryBreakdown estimate={{ ...estimate, gpu_bytes: 0, gpu_capacity_bytes: null, growth: [], notes: [] }} />)

    expect(document.querySelector('.memory-capacity')).toBeNull()
    expect(screen.getAllByRole('table')).toHaveLength(1)
    expect(document.querySelector('.memory-notes')).toBeNull()
  })
})
