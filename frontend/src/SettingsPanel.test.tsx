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

  it('saves, replaces and removes a credential without showing it again', async () => {
    const empty = { store: 'Windows Credential Manager', secrets: [
      { name: 'huggingface-token' as const, configured: false, last_four: null },
      { name: 'openrouter-api-key' as const, configured: false, last_four: null },
    ] }
    const status = vi.spyOn(api, 'secrets').mockResolvedValue(empty)
    const save = vi.spyOn(api, 'saveSecret').mockResolvedValue({ ...empty.secrets[1], configured: true, last_four: 'wxyz' })
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><SettingsPanel canManage canViewSecrets canManageSecrets /></QueryClientProvider>)

    fireEvent.click(screen.getByRole('tab', { name: 'OpenRouter' }))
    expect(await screen.findByText('Not saved', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText(/kept in Windows Credential Manager, never in the database/)).toBeInTheDocument()
    const field = screen.getByLabelText('API key')
    expect(field).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()

    status.mockResolvedValue({ ...empty, secrets: [empty.secrets[0], { ...empty.secrets[1], configured: true, last_four: 'wxyz' }] })
    fireEvent.change(field, { target: { value: 'sk-or-v1-abcdwxyz' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('openrouter-api-key', 'sk-or-v1-abcdwxyz'))
    expect(await screen.findByText('Saved', { selector: '.profile-tag' })).toBeInTheDocument()
    expect(screen.getByText('wxyz')).toBeInTheDocument()
    expect(screen.getByLabelText('Replace API key')).toHaveValue('')

    const remove = vi.spyOn(api, 'removeSecret').mockResolvedValue(empty.secrets[1])
    status.mockResolvedValue(empty)
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('openrouter-api-key'))
    expect(await screen.findByText('Not saved', { selector: '.profile-tag' })).toBeInTheDocument()
  })

  it('needs secrets.write to change a credential', async () => {
    vi.spyOn(api, 'secrets').mockResolvedValue({ store: 'Windows Credential Manager', secrets: [
      { name: 'huggingface-token', configured: true, last_four: 'abcd' },
      { name: 'openrouter-api-key', configured: false, last_four: null },
    ] })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><SettingsPanel canManage={false} canViewSettings={false} canViewSecrets /></QueryClientProvider>)

    expect(screen.queryByRole('tab', { name: 'Benchmark' })).not.toBeInTheDocument()
    expect(await screen.findByLabelText('Replace access token')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove' })).toHaveAttribute('title', 'Changing credentials needs the secrets.write privilege')
  })

  it('shows when the credential store is unavailable', async () => {
    vi.spyOn(api, 'secrets').mockRejectedValue(new Error('the system credential store is unavailable (NoKeyringError)'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><SettingsPanel canManage canViewSettings={false} canViewSecrets canManageSecrets /></QueryClientProvider>)

    expect(await screen.findByRole('alert')).toHaveTextContent('NoKeyringError')
  })

  it('turns remote secrets access on from the server and off again', async () => {
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    const status = vi.spyOn(api, 'securitySettings').mockResolvedValue({ allow_remote_secrets: false, local_request: true })
    const update = vi.spyOn(api, 'updateSecuritySettings').mockResolvedValue({ allow_remote_secrets: true, local_request: true })
    renderPanel()

    fireEvent.click(screen.getByRole('tab', { name: 'Security' }))
    const toggle = await screen.findByRole('switch', { name: 'Allow secrets access from remote' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    status.mockResolvedValue({ allow_remote_secrets: true, local_request: true })
    fireEvent.click(toggle)
    await waitFor(() => expect(update.mock.calls[0]?.[0]).toBe(true))
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'))
    fireEvent.click(toggle)
    await waitFor(() => expect(update.mock.calls[1]?.[0]).toBe(false))
  })

  it('cannot turn remote secrets access on from another machine', async () => {
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    vi.spyOn(api, 'securitySettings').mockResolvedValue({ allow_remote_secrets: false, local_request: false })
    renderPanel()

    fireEvent.click(screen.getByRole('tab', { name: 'Security' }))
    const toggle = await screen.findByRole('switch', { name: 'Allow secrets access from remote' })
    expect(toggle).toBeDisabled()
    expect(toggle).toHaveAttribute('title', 'Turn this on from a browser on the server itself')
    expect(screen.getByText(/connected from another machine/)).toBeInTheDocument()
  })

  it('shows OpenRouter for its model, but not Hugging Face, without secrets.read', () => {
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    renderPanel()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Benchmark', 'Security', 'OpenRouter', 'Assistant'])
  })

  it('needs the settings write privilege to change anything', async () => {
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    renderPanel(false)
    const download = await screen.findByRole('button', { name: 'Download' })
    expect(download).toBeDisabled()
    expect(download).toHaveAttribute('title', 'Changing settings needs the settings.write privilege')
  })
})

describe('SettingsPanel assistant approvals', () => {
  it('switches each approval category on its own', async () => {
    const saved = { name: 'Lama', spend_limit_usd: 2, max_rounds_per_turn: 40, approvals: { downloads: true, runtimes: false, profiles: true, benchmarks: true }, auto_publish: false, search_model: null }
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    vi.spyOn(api, 'assistantSettings').mockResolvedValue(saved)
    const update = vi.spyOn(api, 'updateAssistantSettings').mockResolvedValue({ ...saved, approvals: { ...saved.approvals, runtimes: true } })
    renderPanel()

    fireEvent.click(screen.getByRole('tab', { name: 'Assistant' }))
    const runtimes = await screen.findByRole('switch', { name: 'Ask before runtime installs' })
    expect(runtimes).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('switch', { name: 'Ask before model downloads' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(runtimes)
    await waitFor(() => expect(update.mock.calls[0]?.[0]).toEqual({ ...saved, approvals: { ...saved.approvals, runtimes: true } }))
    await waitFor(() => expect(runtimes).toHaveAttribute('aria-checked', 'true'))

    update.mockResolvedValue({ ...saved, approvals: { ...saved.approvals, runtimes: true }, auto_publish: true })
    const publish = screen.getByRole('switch', { name: 'Auto-publish research and reports' })
    expect(publish).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(publish)
    await waitFor(() => expect(update.mock.calls[1]?.[0]).toMatchObject({ auto_publish: true }))
    await waitFor(() => expect(publish).toHaveAttribute('aria-checked', 'true'))
  })

  it('saves a model of its own for web searches, suggesting OpenRouter models', async () => {
    const saved = { name: 'Lama', spend_limit_usd: 2, max_rounds_per_turn: 40, approvals: { downloads: true, runtimes: false, profiles: true, benchmarks: true }, auto_publish: false, search_model: 'old/model' }
    vi.spyOn(api, 'benchmarkTools').mockResolvedValue({ eslint: missing })
    vi.spyOn(api, 'assistantSettings').mockResolvedValue(saved)
    const models = vi.spyOn(api, 'openRouterModels').mockResolvedValue({ models: [{ id: 'perplexity/sonar', name: 'Sonar' }] as never, total: 1 })
    const update = vi.spyOn(api, 'updateAssistantSettings').mockImplementation(async (next) => next)
    renderPanel()

    fireEvent.click(screen.getByRole('tab', { name: 'Assistant' }))
    const field = await screen.findByLabelText(/^Web search model/)
    expect(field).toHaveValue('old/model')
    fireEvent.change(field, { target: { value: 'sonar' } })
    await waitFor(() => expect(models).toHaveBeenCalledWith('sonar'))
    expect(await screen.findByRole('option', { hidden: true, name: 'Sonar' })).toHaveAttribute('value', 'perplexity/sonar')
    fireEvent.change(field, { target: { value: ' ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(update.mock.calls[0]?.[0]).toMatchObject({ search_model: null }))
  })
})
