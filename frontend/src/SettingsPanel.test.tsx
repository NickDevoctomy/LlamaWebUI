import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type LinterStatus } from './api'
import { SettingsPanel } from './SettingsPanel'

const pinned = { node: '24.22.1', eslint: '10.13.0', '@eslint/js': '10.0.1', 'eslint-plugin-html': '8.2.1', globals: '17.13.0', typescript: '7.0.2', esbuild: '0.28.2' }
const missing: LinterStatus = { state: 'missing', versions: null, installed_at: null, size: null, progress: null, error: null, pinned, typescript: false, outdated: false }
const installed: LinterStatus = { ...missing, state: 'installed', versions: pinned, installed_at: '2026-10-24T15:00:00', size: 92_000_000, typescript: true }

function renderPanel(canManage = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><SettingsPanel canManage={canManage} /></QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('SettingsPanel', () => {
  it('downloads ESLint, showing its progress, then offers to remove it', async () => {
    const status = vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    const install = vi.spyOn(api, 'installEslint').mockResolvedValue({ eslint: { ...missing, state: 'installing', progress: { stage: 'downloading', received: 0, total: null } } })
    renderPanel()

    expect(await screen.findByText(/Fetches Node.js 24.22.1 · eslint 10.13.0 · eslint-plugin-html 8.2.1/)).toBeInTheDocument()
    status.mockResolvedValue({ eslint: { ...missing, state: 'installing', progress: { stage: 'downloading', received: 15_000_000, total: 30_000_000 } } })
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(install).toHaveBeenCalled())
    expect(await screen.findByText(/Downloading Node.js · .* · 50%/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()

    status.mockResolvedValue({ eslint: { ...missing, state: 'installing', progress: { stage: 'packages' } } })
    expect(await screen.findByText('Installing ESLint…', {}, { timeout: 3000 })).toBeInTheDocument()

    status.mockResolvedValue({ eslint: installed })
    const remove = vi.spyOn(api, 'removeEslint').mockResolvedValue({ eslint: missing })
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }, { timeout: 3000 }))
    expect(screen.getByText('Installed', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText(/^Node.js 24.22.1 · eslint 10.13.0 · eslint-plugin-html 8.2.1 · typescript 7.0.2 · esbuild 0.28.2 · [0-9.]+ MB$/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Update' })).not.toBeInTheDocument()
    await waitFor(() => expect(remove).toHaveBeenCalled())
  })

  it('offers an install from before the TypeScript tools an update', async () => {
    const { typescript: _typescript, esbuild: _esbuild, ...older } = pinned
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: { ...installed, versions: older, typescript: false, outdated: true } })
    const install = vi.spyOn(api, 'installEslint').mockResolvedValue({ eslint: { ...installed, state: 'installing', progress: { stage: 'downloading', received: 0, total: null } } })
    renderPanel()

    expect(await screen.findByText('Update available', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText(/^Node.js 24.22.1 · eslint 10.13.0 · eslint-plugin-html 8.2.1 · [0-9.]+ MB$/)).toBeInTheDocument()
    expect(screen.getByText(/Update to add TypeScript 7.0.2 and esbuild 0.28.2/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(install).toHaveBeenCalled())
  })

  it('shows why an install failed and lets it be tried again', async () => {
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: { ...missing, state: 'failed', error: 'download failed: ConnectError' } })
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent('download failed: ConnectError')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled()
  })

  it('needs the settings write privilege to change anything', async () => {
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    renderPanel(false)
    const download = await screen.findByRole('button', { name: 'Download' })
    expect(download).toBeDisabled()
    expect(download).toHaveAttribute('title', 'Changing settings needs the settings.write privilege')
  })
})
