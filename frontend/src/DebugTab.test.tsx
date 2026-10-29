import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type ProfilerStatus } from './api'
import { SettingsPanel } from './SettingsPanel'

const config = { enabled: false, nsys_path: '', trace: 'cuda,nvtx,cublas', cpu_sampling: true, sampling_hz: 250, capture_on_start: true, graph_nodes: false }
const idle: ProfilerStatus = {
  config,
  nsys: { path: 'C:\\Program Files\\NVIDIA Corporation\\Nsight Systems 2025.6.3\\target-windows-x64\\nsys.exe', version: '2025.6.3.541' },
  elevated: false,
  router_profiled: false,
  capture: null,
  saving: null,
  last_error: null,
  captures: [],
}
const on: ProfilerStatus = { ...idle, config: { ...config, enabled: true } }
const report = { name: '20261029-093015-b11815-cuda-debugsym.nsys-rep', path: 'E:\\data\\debug\\captures\\20261029-093015-b11815-cuda-debugsym.nsys-rep', size: 1_139_411, created_at: '2026-10-29T09:31:00Z' }

function renderDebug(canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><SettingsPanel canManage={false} canViewDiagnostics canManageDiagnostics={canManage} canViewSettings={false} /></QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('DebugTab', () => {
  it('keeps everything but the master switch and reports out of the way while profiling is off', async () => {
    vi.spyOn(api, 'profiler').mockResolvedValue(idle)
    const update = vi.spyOn(api, 'updateProfiler').mockResolvedValue(on)
    renderDebug()

    expect(await screen.findByText(/The router runs normally/)).toBeInTheDocument()
    expect(screen.getByText('Off', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText(/No reports yet/)).toBeInTheDocument()
    expect(screen.queryByText('2025.6.3.541')).not.toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'CPU call stacks' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start capture' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('switch', { name: 'Profiling' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith({ ...config, enabled: true }))
    expect(await screen.findByText('On from next start', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText('2025.6.3.541', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText(/record the GPU timeline only/)).toBeInTheDocument()
    expect(screen.getByText('Router not profiled', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start capture' })).toBeDisabled()
    expect(screen.getByText(/Start the router, or restart it/)).toBeInTheDocument()
  })

  it('keeps the capture controls while a router started with profiling is still running', async () => {
    const recording = { ...idle, router_profiled: true, capture: { name: report.name, started_at: new Date().toISOString() } }
    vi.spyOn(api, 'profiler').mockResolvedValue(recording)
    renderDebug()

    expect(await screen.findByText('Off from next start', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop and save' })).toBeEnabled()
    expect(screen.queryByRole('switch', { name: 'CPU call stacks' })).not.toBeInTheDocument()
  })

  it('saves the nsys path and trace together', async () => {
    vi.spyOn(api, 'profiler').mockResolvedValue(on)
    const update = vi.spyOn(api, 'updateProfiler').mockResolvedValue(on)
    renderDebug()

    fireEvent.change(await screen.findByLabelText(/^Trace/), { target: { value: ' cuda,nvtx ' } })
    fireEvent.change(screen.getByLabelText(/^nsys.exe path/), { target: { value: 'D:\\Tools\\nsys.exe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith({ ...config, enabled: true, nsys_path: 'D:\\Tools\\nsys.exe', trace: 'cuda,nvtx' }))
  })

  it('starts and stops a capture and lists the report', async () => {
    const ready = { ...idle, config: { ...config, enabled: true, capture_on_start: false }, router_profiled: true }
    const recording = { ...ready, capture: { name: report.name, started_at: new Date().toISOString() } }
    vi.spyOn(api, 'profiler').mockResolvedValue(ready)
    const start = vi.spyOn(api, 'startCapture').mockResolvedValue(recording)
    const stop = vi.spyOn(api, 'stopCapture').mockResolvedValue({ ...ready, captures: [report] })
    renderDebug()

    fireEvent.click(await screen.findByRole('button', { name: 'Start capture' }))
    await waitFor(() => expect(start).toHaveBeenCalled())
    expect(await screen.findByText(/^Recording · 0:0\d$/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Stop and save' }))
    await waitFor(() => expect(stop).toHaveBeenCalled())
    expect(await screen.findByText(report.name, { selector: '.record-title' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute('href', api.captureUrl(report.name))
  })

  it('shows a profiler error and keeps controls read-only without diagnostics.write', async () => {
    vi.spyOn(api, 'profiler').mockResolvedValue({ ...idle, captures: [report], last_error: 'nsys could not write the report: stop failed' })
    renderDebug(false)

    // With profiling off the capture row is hidden, so the error shows at the master switch.
    expect(await screen.findByText(/could not write the report/)).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Profiling' })).toBeDisabled()
    expect(screen.getByRole('button', { name: `Delete ${report.name}` })).toBeDisabled()
    expect(screen.getByRole('link', { name: 'Download' })).toBeInTheDocument()
  })

  it('shows a report being saved and keeps the capture buttons out of the way', async () => {
    const startedAt = new Date(Date.now() - 75_000).toISOString()
    vi.spyOn(api, 'profiler').mockResolvedValue({ ...on, router_profiled: true, saving: { name: report.name, started_at: startedAt } })
    renderDebug()

    expect(await screen.findByText(/^Saving report · 1:1\d$/)).toBeInTheDocument()
    expect(screen.getByText(/Leave it to finish/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Start capture' })).not.toBeInTheDocument()
  })

  it('saves the CPU sampling rate and refuses one nsys would reject', async () => {
    vi.spyOn(api, 'profiler').mockResolvedValue(on)
    const update = vi.spyOn(api, 'updateProfiler').mockResolvedValue(on)
    renderDebug()

    const rate = await screen.findByLabelText(/^CPU samples per second/)
    expect(rate).toHaveValue(250)
    fireEvent.change(rate, { target: { value: '20000' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(rate, { target: { value: '500' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith({ ...config, enabled: true, sampling_hz: 500 }))
  })
})
