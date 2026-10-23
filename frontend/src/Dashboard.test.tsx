import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Profile, RouterModel, ServerStatus } from './api'
import { DashboardPanel, draftModel, Sparkline } from './Dashboard'

const GIB = 1024 ** 3

const profile = {
  id: 'profile-1',
  alias: 'qwen',
  runtime_id: 'runtime-1',
  model_path: 'E:\\models\\qwen.gguf',
  configuration: { arguments: [{ id: 'ctx-size', value: 8192 }, { id: 'spec-draft-model', value: 'E:\\models\\mtp-qwen.gguf' }] },
  enabled: true,
  validation_state: 'available',
  source_download: null,
} as unknown as Profile

const status = {
  state: 'ready', pid: 42, last_exit_code: null, endpoint: 'http://127.0.0.1:1234', logs: [], arguments: [],
  timing: { decode_tokens_per_second: 50, decode_tokens_per_second_rolling_peak: 38.4, decode_tokens_per_second_session_peak: 61 },
  memory: [
    { model: 'qwen', pid: 11, vram_bytes: 21 * GIB, gpu_shared_bytes: 5 * GIB, ram_bytes: 12 * GIB, mapped_bytes: 3 * GIB },
    { model: 'embed', pid: 12, vram_bytes: null, gpu_shared_bytes: null, ram_bytes: 1 * GIB, mapped_bytes: 0 },
  ],
  context: [{ model: 'qwen', shared_capacity: null, slots: [{ id: 0, used: 1000, total: 4096 }, { id: 1, used: 24, total: 4096 }] }],
  system: { cpu_percent: 5, ram_used_bytes: GIB, ram_total_bytes: 8 * GIB, network_received_bytes: 0, network_sent_bytes: 0, disk_free_bytes: 1, disk_total_bytes: 2, disk_read_bytes: null, disk_write_bytes: null, gpu: null, gpu_supported: false },
} as ServerStatus

const models: RouterModel[] = [{ id: 'qwen', path: null, status: { value: 'loaded' }, metadata: {} }]

function renderDashboard(overrides: Partial<ServerStatus> = {}, running = true) {
  return render(<DashboardPanel downloads={[]} history={[]} library={[]} models={models} profiles={[profile]} running={running} runtimes={[]} status={{ ...status, ...overrides }} tokens={[]} />)
}

afterEach(() => {
  cleanup()
})

describe('dashboard telemetry', () => {
  it('finds draft models in catalog and raw arguments', () => {
    expect(draftModel(profile)).toBe('mtp-qwen.gguf')
    expect(draftModel({ ...profile, configuration: { arguments: [{ flag: '-md', value: '/models/draft.gguf', origin: 'raw' }] } })).toBe('draft.gguf')
    expect(draftModel({ ...profile, configuration: {} })).toBeNull()
  })

  it('counts the draft as a loaded model and lists it under its profile', () => {
    renderDashboard()

    expect(screen.getByText('Serving 2 loaded models (1 draft) from 1 enabled profile.')).toBeInTheDocument()
    const roster = screen.getByRole('heading', { name: 'Models' }).closest('section')!
    expect(within(roster).getByText('mtp-qwen.gguf')).toBeInTheDocument()
    expect(within(roster).getByText('Draft model for qwen')).toBeInTheDocument()
    expect(within(roster).getByText('1 of 1 loaded in the router')).toBeInTheDocument()
  })

  it('shows the rolling peak with the session peak in brackets', () => {
    renderDashboard()

    expect(screen.getByText('38.4')).toBeInTheDocument()
    expect(screen.getByText('(61.0)')).toHaveClass('session-peak')
  })

  it('splits measured model memory into VRAM, RAM and mapped files', () => {
    renderDashboard()

    expect(screen.getByRole('img', { name: 'VRAM 21.0 GB, RAM 13.0 GB, Mapped from disk 3.0 GB' })).toBeInTheDocument()
    expect(screen.getByText('37.0 GB')).toBeInTheDocument()
    const panel = within(screen.getByRole('heading', { name: 'Model memory' }).closest('section')!)
    const legend = panel.getByText('Shared with GPU').closest('li')!
    expect(legend).toHaveTextContent('5.0 GB')
    expect(legend).toHaveAttribute('title', 'RAM · Shared with GPU: 5.0 GB\nqwen: 5.0 GB')
    expect(panel.getByText('Process').closest('li')).toHaveTextContent('8.0 GB')
  })

  it('reports no live telemetry while the router is stopped', () => {
    renderDashboard({ state: 'stopped' }, false)

    expect(screen.getByText('No model memory reported')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show context breakdown' })).not.toBeInTheDocument()
  })

  it('shows total context and toggles a per-slot breakdown', () => {
    renderDashboard()

    fireEvent.click(screen.getByRole('button', { name: 'Show context breakdown' }))
    expect(screen.getByText('Slot 0')).toBeInTheDocument()
    expect(screen.getByText('Slot 1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Total' }))
    expect(screen.queryByText('Slot 0')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show context breakdown' })).toHaveTextContent('1,024/ 8,192 tokens')
  })

  it('uses a unified cache capacity once for every slot', () => {
    renderDashboard({ context: [{ model: 'qwen', shared_capacity: 8192, slots: [{ id: 0, used: 10, total: 8192 }, { id: 1, used: 20, total: 8192 }] }] })

    expect(screen.getByRole('button', { name: 'Show context breakdown' })).toHaveTextContent('30/ 8,192 tokens')
  })

  it('draws the rolling peak over the decode trend with the session peak on top', () => {
    const { container } = render(<Sparkline ceiling={40} overlay={[null, 10, 20]} values={[5, 30, 25]} />)
    const svg = container.querySelector('svg')!
    expect([...svg.children].map((child) => child.getAttribute('class'))).toEqual(['spark-area', 'spark-line', 'spark-overlay', 'spark-ceiling'])
    expect(svg.querySelector('.spark-ceiling')).toHaveAttribute('y1', '2.00')
  })
})
