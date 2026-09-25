import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const responses: Record<string, unknown> = {
  '/api/server/status': {
    state: 'stopped',
    pid: null,
    last_exit_code: null,
    endpoint: 'http://127.0.0.1:1234',
    logs: [],
  },
  '/api/runtimes': [],
  '/api/profiles': [],
  '/api/tokens': [],
  '/api/downloads': [],
  '/api/library': [],
  '/api/library/logical': [],
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function renderApp({
  runtimeList = [],
  tokenList = [],
  downloadList = [],
  libraryList = [],
  logicalLibraryList = [],
  profileList = [],
  onRequest,
  commandResponse = '',
  serverStatus = responses['/api/server/status'],
  repositoryGroups = [{ key: 'model-Q4_K_M', quantization: 'Q4_K_M', total_size: 4_200_000_000, complete: true, files: [{ path: 'model-Q4_K_M.gguf', size: 4_200_000_000 }] }],
}: {
  runtimeList?: unknown[]
  tokenList?: unknown[]
  downloadList?: unknown[] | (() => unknown[])
  libraryList?: unknown[]
  logicalLibraryList?: unknown[]
  profileList?: unknown[]
  onRequest?: (path: string, init?: RequestInit) => unknown
  commandResponse?: string
  serverStatus?: unknown
  repositoryGroups?: unknown[]
} = {}) {
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const rawPath = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url
    const path = new URL(rawPath, 'http://localhost').pathname
      const customPayload = onRequest?.(path, init)
      const payload = customPayload !== undefined ? customPayload : (init?.method === 'DELETE'
      ? { ...tokenList[0] as object, enabled: false }
      : init?.method === 'POST'
      ? path === '/api/runtimes'
        ? { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
        : path === '/api/tokens'
          ? { id: 'token-1', name: 'OpenCode', token: 'lwui_once_only', last_four: 'only', expiry_note: 'Rotate monthly', enabled: true, created_at: '2026-09-19T12:00:00' }
        : path === '/api/downloads'
          ? { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [{ path: 'model-Q4_K_M.gguf', size: 4_200_000_000 }], destination: 'E:\\models\\owner--model-GGUF', total_bytes: 4_200_000_000, completed_bytes: 0, state: 'queued', error: null }
        : path === '/api/library/reconcile'
          ? { managed_jobs: 0, valid_models: 0, invalid_jobs: 0, stray_gguf_files: 0, logical_models: logicalLibraryList.length, missing_logical_models: 1, linked_logical_models: 1 }
        : path.endsWith('/resume')
          ? { ...(typeof downloadList === 'function' ? downloadList()[0] : downloadList[0]) as object, state: 'queued' }
        : { id: 'profile-1', alias: 'qwen-local', runtime_id: 'runtime-1', model_path: 'E:\\models\\qwen.gguf', configuration: {}, enabled: true }
      : path === '/api/runtimes'
        ? runtimeList
        : path === '/api/tokens'
          ? tokenList
          : path === '/api/profiles'
            ? profileList
          : path === '/api/downloads'
            ? typeof downloadList === 'function' ? downloadList() : downloadList
            : path === '/api/library'
              ? libraryList
            : path === '/api/library/logical'
              ? logicalLibraryList
          : path === '/api/server/status'
            ? serverStatus
            : path === '/api/huggingface/models'
              ? [{ repo_id: 'owner/model-GGUF', downloads: 1200, likes: 42, last_modified: '2026-09-19T00:00:00Z', gated: false, private: false, tags: ['gguf', 'qwen'] }]
                : path === '/api/huggingface/repositories/owner/model-GGUF'
                  ? { repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), groups: repositoryGroups }
            : path === '/api/integrations/opencode'
              ? { provider: { 'llama-web-ui': { options: { apiKey: '{env:LLAMA_WEB_UI_API_KEY}' }, models: { 'qwen-local': { name: 'qwen-local' } } } } }
              : path === '/api/server/models'
                ? []
                : responses[path])
    return Promise.resolve(new Response(
      path.endsWith('/command') && init?.method !== 'POST' ? commandResponse : JSON.stringify(payload),
      {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      },
    ))
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return Object.assign(fetchMock, { client })
}

describe('App', () => {
  it('renders live operational state and navigates to server details', async () => {
    renderApp()

    expect(await screen.findByText('http://127.0.0.1:1234')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Server' }))

    expect(screen.getByRole('heading', { name: 'Process' })).toBeInTheDocument()
    expect(screen.getByText('Waiting for router output…')).toBeInTheDocument()
  })

  it('renders the operational dashboard by default', async () => {
    renderApp()

    expect(await screen.findByRole('heading', { name: 'Dashboard', level: 1 })).toBeInTheDocument()
    expect(screen.getByText('Operational overview')).toBeInTheDocument()
    expect(screen.getByText('Local control-plane health and active work.')).toBeInTheDocument()
  })

  it('refreshes the downloaded model library from the Models page', async () => {
    const fetchMock = renderApp()
    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    await screen.findByText('No downloaded models')
    fetchMock.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/library', expect.anything())
    })
  })

  it('shows logical model validity, profile links, and removable missing records', async () => {
    const logicalModels = [
      { id: 'valid-1', primary_path: 'E:\\models\\valid.gguf', files: ['valid.gguf'], metadata: { 'general.name': 'Valid model' }, validation_state: 'valid', profile_ids: ['profile-1'] },
      { id: 'missing-1', primary_path: 'E:\\models\\missing.gguf', files: ['missing.gguf'], metadata: {}, validation_state: 'missing', profile_ids: [] },
      { id: 'linked-missing', primary_path: 'E:\\models\\linked.gguf', files: ['linked.gguf'], metadata: {}, validation_state: 'missing', profile_ids: ['profile-2'] },
    ]
    renderApp({ logicalLibraryList: logicalModels })

    fireEvent.click(screen.getByRole('button', { name: 'Models' }))

    expect(await screen.findByText('Valid model')).toBeInTheDocument()
    expect(screen.getByText('Valid')).toBeInTheDocument()
    expect(screen.getAllByText('Missing')).toHaveLength(2)
    expect(screen.getByText('E:\\models\\valid.gguf').closest('tr')).toHaveTextContent('1')
    const removableRow = screen.getByText('E:\\models\\missing.gguf').closest('tr')
    expect(within(removableRow!).getByRole('button', { name: 'Remove record' })).toBeEnabled()
    const linkedRow = screen.getByText('E:\\models\\linked.gguf').closest('tr')
    expect(linkedRow?.querySelectorAll('td')[3]).toHaveTextContent('1')
    expect(within(linkedRow!).getByRole('button', { name: 'Remove record' })).toBeDisabled()
  })

  it('includes missing and linked logical-model totals after reconciliation', async () => {
    renderApp({ logicalLibraryList: [{ id: 'missing-1', primary_path: 'missing.gguf', files: [], metadata: {}, validation_state: 'missing', profile_ids: [] }] })
    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Reconcile library' }))

    expect(await screen.findByText(/1 missing logical model\(s\), 1 linked logical model\(s\)/)).toBeInTheDocument()
  })

  it('imports a command disabled and exports its readable command text', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const command = '"C:\\llama tools\\llama-server.exe" --model "C:\\models\\shard 00001-of-00003.gguf" --ctx-size 4096'
    const profiles: unknown[] = []
    const fetchMock = renderApp({
      runtimeList: [runtime],
      profileList: profiles,
      commandResponse: command,
      onRequest: (path, init) => {
        if (path === '/api/profiles/import-command' && init?.method === 'POST') {
          const request = JSON.parse(String(init.body)) as { alias: string; runtime_id: string; enabled: boolean }
          const profile = { id: 'profile-imported', alias: request.alias, runtime_id: request.runtime_id, model_path: 'C:\\models\\shard 00001-of-00003.gguf', configuration: { ctx_size: 4096 }, enabled: request.enabled }
          profiles.push(profile)
          return profile
        }
        if (path === '/api/profiles') return profiles
        return undefined
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    await screen.findByRole('heading', { name: 'Model profiles' })
    const openImport = screen.getByRole('button', { name: 'Import command' })
    await waitFor(() => expect(openImport).toBeEnabled())
    fireEvent.click(openImport)
    await screen.findByLabelText('llama-server command')
    fireEvent.change(screen.getByLabelText('Profile alias'), { target: { value: 'qwen-roundtrip' } })
    fireEvent.change(screen.getByLabelText('llama-server command'), { target: { value: command } })
    const importButton = screen.getAllByRole('button', { name: 'Import command' })[1]
    await waitFor(() => expect(importButton).toBeEnabled())
    fireEvent.click(importButton)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profiles/import-command', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ command, alias: 'qwen-roundtrip', runtime_id: 'runtime-1', enabled: false }),
    })))
    expect(await screen.findByText('Disabled')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Command' }))
    expect(await screen.findByRole('heading', { name: 'Command for qwen-roundtrip' })).toBeInTheDocument()
    expect(screen.getByRole('dialog').querySelector('pre')?.textContent).toBe(command)
  })

  it('refreshes library and live models when a download completes', async () => {
    const downloading = { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [], destination: 'E:\\models', total_bytes: 1000, completed_bytes: 400, state: 'downloading', error: null }
    let downloadList = [downloading]
    const fetchMock = renderApp({
      downloadList: () => downloadList,
      serverStatus: { ...responses['/api/server/status'] as object, state: 'ready' },
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads', expect.anything()))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/server/models', expect.anything()))
    fetchMock.mockClear()
    downloadList = [{ ...downloading, completed_bytes: 1000, state: 'completed' }]

    await fetchMock.client.invalidateQueries({ queryKey: ['downloads'] })

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/library', expect.anything())
    })
  })

  it('lists validated downloads on Models instead of profiles', async () => {
    const model = { download_id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', primary_path: 'E:\\models\\model.gguf', file_count: 2, total_bytes: 4_200_000_000 }
    const profile = { id: 'profile-1', alias: 'profile-only', runtime_id: 'runtime-1', model_path: 'E:\\other.gguf', configuration: {}, enabled: true }
    renderApp({ libraryList: [model], profileList: [profile] })

    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    expect(await screen.findByText('owner/Qwen-Test-GGUF')).toBeInTheDocument()
    expect(screen.getByText('Q4/model-Q4')).toBeInTheDocument()
    expect(screen.getByText('3.9 GB')).toBeInTheDocument()
    expect(screen.getByText('2 files')).toBeInTheDocument()
    expect(screen.getByText('aaaaaaaaa')).toBeInTheDocument()
    expect(screen.queryByText('profile-only')).not.toBeInTheDocument()
  })

  it('configures a downloaded model from Models', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CUDA', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cuda', devices: ['CUDA0'], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const model = { download_id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', primary_path: 'E:\\models\\model.gguf', file_count: 1, total_bytes: 1000 }
    renderApp({ libraryList: [model], runtimeList: [runtime] })

    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Configure' }))

    expect(await screen.findByRole('heading', { name: 'Create model profile' })).toBeInTheDocument()
    expect(screen.getByLabelText(/API model alias/i)).toHaveValue('qwen-test')
    expect(screen.getByLabelText(/Primary GGUF file/i)).toHaveValue(model.primary_path)
  })

  it('deletes a downloaded model without deleting its profile', async () => {
    const model = { download_id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', primary_path: 'E:\\models\\model.gguf', file_count: 1, total_bytes: 1000 }
    const profile = { id: 'profile-1', alias: 'qwen-test', runtime_id: 'runtime-1', model_path: model.primary_path, configuration: {}, enabled: true, validation_state: 'available', source_download: { id: model.download_id, repo_id: model.repo_id, revision: model.revision, group_key: model.group_key, file_count: 1, total_bytes: 1000 } }
    const fetchMock = renderApp({ libraryList: [model], profileList: [profile] })

    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    fireEvent.click(await screen.findByRole('button', { name: `Delete ${model.repo_id} ${model.group_key}` }))
    expect(screen.getByRole('heading', { name: 'Delete downloaded model?' })).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete model' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/library/download-1', { method: 'DELETE' }))
    expect(fetchMock).not.toHaveBeenCalledWith('/api/profiles/profile-1', expect.anything())
  })

  it('repairs and deletes a broken profile', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CUDA', executable_path: 'E:\\llama-server.exe', build: 'b11060', backend: 'cuda', devices: [], options: ['model'], usable: true }
    const source = { id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', file_count: 1, total_bytes: 1000 }
    const profile = { id: 'profile-1', alias: 'qwen-test', runtime_id: 'runtime-1', model_path: 'E:\\models\\missing.gguf', configuration: {}, enabled: true, validation_state: 'broken', source_download: source }
    const fetchMock = renderApp({ profileList: [profile], runtimeList: [runtime] })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))

    fireEvent.click(await screen.findByRole('button', { name: 'Broken' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Re-download' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads/download-1/redownload', { method: 'POST' }))

    fireEvent.click(screen.getByRole('button', { name: 'Delete profile qwen-test' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete profile' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profiles/profile-1', { method: 'DELETE' }))
  })

  it('registers and probes a local runtime', async () => {
    const fetchMock = renderApp()
    fireEvent.click(screen.getByRole('button', { name: 'Runtimes' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Register runtime' })[0])
    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Local CPU' } })
    fireEvent.change(screen.getByLabelText(/llama-server executable/i), { target: { value: 'E:\\llama-server.exe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Probe & register' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/runtimes', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ name: 'Local CPU', executable_path: 'E:\\llama-server.exe', backend: 'cpu' }),
    })))
  })

  it('creates a profile with only supported advanced options', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const fetchMock = renderApp({ runtimeList: [runtime] })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    const createButton = screen.getAllByRole('button', { name: 'Create profile' })[0]
    await waitFor(() => expect(createButton).toBeEnabled())
    fireEvent.click(createButton)
    fireEvent.change(screen.getByLabelText(/API model alias/i), { target: { value: 'Qwen Local' } })
    fireEvent.change(screen.getByLabelText(/Primary GGUF file/i), { target: { value: 'E:\\models\\qwen.gguf' } })
    fireEvent.click(screen.getByRole('tab', { name: 'Advanced' }))
    fireEvent.change(screen.getByLabelText('Context size'), { target: { value: '32768' } })
    expect(screen.getByLabelText('GPU layers')).toBeDisabled()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create profile' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([path, init]) => path === '/api/profiles' && init?.method === 'POST')
      expect(call).toBeDefined()
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ alias: 'qwen-local', runtime_id: 'runtime-1', ctx_size: 32768 })
      expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('n_gpu_layers')
    })
  })

  it('shows a created key once and removes plaintext when closed', async () => {
    const fetchMock = renderApp()
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Access' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Create key' })[0])
    fireEvent.change(screen.getByLabelText(/Key name/i), { target: { value: 'OpenCode' } })
    fireEvent.change(screen.getByLabelText(/Expiry note/i), { target: { value: 'Rotate monthly' } })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create key' }))

    expect(await screen.findByText('lwui_once_only')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/tokens', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ name: 'OpenCode', expiry_note: 'Rotate monthly' }),
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Copy key' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('lwui_once_only'))
    fireEvent.click(screen.getByRole('button', { name: 'I have saved the key' }))
    expect(screen.queryByText('lwui_once_only')).not.toBeInTheDocument()
  })

  it('requires confirmation before revoking a key', async () => {
    const token = { id: 'token-1', name: 'CI workstation', last_four: '7xQp', expiry_note: null, enabled: true, created_at: '2026-09-19T12:00:00' }
    const fetchMock = renderApp({ tokenList: [token] })
    fireEvent.click(screen.getByRole('button', { name: 'Access' }))
    const revoke = await screen.findByRole('button', { name: 'Revoke CI workstation' })
    fireEvent.click(revoke)
    expect(screen.getByRole('heading', { name: 'Revoke access key?' })).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Revoke key' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/tokens/token-1', { method: 'DELETE' }))
  })

  it('shows live OpenCode configuration without embedding a token', async () => {
    renderApp({ serverStatus: { ...responses['/api/server/status'] as object, state: 'ready' } })
    fireEvent.click(screen.getByRole('button', { name: 'Access' }))

    expect(await screen.findByText('opencode.jsonc')).toBeInTheDocument()
    expect(screen.getByText(/LLAMA_WEB_UI_API_KEY/)).toBeInTheDocument()
    expect(screen.queryByText(/lwui_/)).not.toBeInTheDocument()
  })

  it('searches repositories and queues an inspected GGUF group', async () => {
    const fetchMock = renderApp()
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))
    expect(await screen.findByText('Q4_K_M')).toBeInTheDocument()
    expect(screen.getByText('All shards available')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Download' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ repo_id: 'owner/model-GGUF', group_key: 'model-Q4_K_M', revision: 'a'.repeat(40) }),
    })))
    expect(await screen.findByRole('heading', { name: 'Download jobs' })).toBeInTheDocument()
  })

  it('shows the group key when quantization detection has no match', async () => {
    renderApp({ repositoryGroups: [{ key: 'model-custom-format', quantization: null, total_size: 42, complete: true, files: [{ path: 'model-custom-format.gguf', size: 42 }] }] })
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))

    expect(await screen.findByText('model-custom-format')).toBeInTheDocument()
  })

  it('marks a validated matching quantization as downloaded', async () => {
    const model = { download_id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', primary_path: 'E:\\models\\model.gguf', file_count: 1, total_bytes: 4_200_000_000 }
    renderApp({ libraryList: [model] })
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))

    const downloaded = await screen.findByRole('button', { name: 'Downloaded' })
    expect(downloaded).toBeDisabled()
    expect(screen.getByText('All shards available')).toBeInTheDocument()
  })

  it('shows an active matching quantization without offering a duplicate download', async () => {
    const job = { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [], destination: 'E:\\models', total_bytes: 1000, completed_bytes: 400, state: 'downloading', error: null }
    renderApp({ downloadList: [job] })
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))

    expect(await screen.findByRole('button', { name: 'Downloading' })).toBeDisabled()
  })

  it('resumes a paused durable download', async () => {
    const job = { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [], destination: 'E:\\models', total_bytes: 1000, completed_bytes: 400, state: 'paused', error: null }
    const fetchMock = renderApp({ downloadList: [job] })
    fireEvent.click(screen.getByRole('button', { name: 'Downloads' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Resume owner/model-GGUF' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads/download-1/resume', { method: 'POST' }))
  })

  it('clears completed and cancelled download jobs', async () => {
    const job = { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [], destination: 'E:\\models', total_bytes: 1000, completed_bytes: 400, state: 'cancelled', error: null }
    const fetchMock = renderApp({ downloadList: [job] })
    fireEvent.click(screen.getByRole('button', { name: 'Downloads' }))
    const clearButton = await screen.findByRole('button', { name: 'Clear finished' })
    await waitFor(() => expect(clearButton).toBeEnabled())
    fireEvent.click(clearButton)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads/terminal', { method: 'DELETE' }))
  })

  it('prefills a profile from a validated completed download', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CUDA', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cuda', devices: ['CUDA0'], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const job = { id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', files: [], destination: 'E:\\models', total_bytes: 1000, completed_bytes: 1000, state: 'completed', error: null }
    const model = { download_id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', primary_path: 'E:\\models\\model-00001-of-00002.gguf', file_count: 2, total_bytes: 1000 }
    renderApp({ downloadList: [job], libraryList: [model], runtimeList: [runtime] })
    fireEvent.click(screen.getByRole('button', { name: 'Downloads' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Create profile for owner/Qwen-Test-GGUF' }))

    expect(await screen.findByRole('heading', { name: 'Create model profile' })).toBeInTheDocument()
    expect(screen.getByLabelText(/API model alias/i)).toHaveValue('qwen-test')
    expect(screen.getByLabelText(/Primary GGUF file/i)).toHaveValue(model.primary_path)
    expect(await screen.findByLabelText(/Downloaded model/i)).toHaveValue('download-1')
    expect(screen.getByLabelText('Runtime')).toHaveValue('runtime-1')
  })
})