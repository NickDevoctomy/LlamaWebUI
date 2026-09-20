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
  profileList = [],
  serverStatus = responses['/api/server/status'],
}: {
  runtimeList?: unknown[]
  tokenList?: unknown[]
  downloadList?: unknown[] | (() => unknown[])
  libraryList?: unknown[]
  profileList?: unknown[]
  serverStatus?: unknown
} = {}) {
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const rawPath = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url
    const path = new URL(rawPath, 'http://localhost').pathname
    const payload = init?.method === 'DELETE'
      ? { ...tokenList[0] as object, enabled: false }
      : init?.method === 'POST'
      ? path === '/api/runtimes'
        ? { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
        : path === '/api/tokens'
          ? { id: 'token-1', name: 'OpenCode', token: 'lwui_once_only', last_four: 'only', expiry_note: 'Rotate monthly', enabled: true, created_at: '2026-09-19T12:00:00' }
        : path === '/api/downloads'
          ? { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [{ path: 'model-Q4_K_M.gguf', size: 4_200_000_000 }], destination: 'E:\\models\\owner--model-GGUF', total_bytes: 4_200_000_000, completed_bytes: 0, state: 'queued', error: null }
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
          : path === '/api/server/status'
            ? serverStatus
            : path === '/api/huggingface/models'
              ? [{ repo_id: 'owner/model-GGUF', downloads: 1200, likes: 42, last_modified: '2026-09-19T00:00:00Z', gated: false, private: false, tags: ['gguf', 'qwen'] }]
              : path === '/api/huggingface/repositories/owner/model-GGUF'
                ? { repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), groups: [{ key: 'model-Q4_K_M', quantization: 'Q4_K_M', total_size: 4_200_000_000, complete: true, files: [{ path: 'model-Q4_K_M.gguf', size: 4_200_000_000 }] }] }
            : path === '/api/integrations/opencode'
              ? { provider: { 'llama-web-ui': { options: { apiKey: '{env:LLAMA_WEB_UI_API_KEY}' }, models: { 'qwen-local': { name: 'qwen-local' } } } } }
              : path === '/api/server/models'
                ? []
                : responses[path]
    return Promise.resolve(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
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
    expect(screen.getByText('No downloaded models')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Server' }))

    expect(screen.getByRole('heading', { name: 'Process' })).toBeInTheDocument()
    expect(screen.getByText('Waiting for router output…')).toBeInTheDocument()
  })

  it('renders the operational dashboard by default', async () => {
    renderApp()

    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByText('Operational overview')).toBeInTheDocument()
    expect(screen.getByText('Current work queues.')).toBeInTheDocument()
  })

  it('refreshes the downloaded model library from the Models page', async () => {
    const fetchMock = renderApp()
    await screen.findByText('No downloaded models')
    fetchMock.mockClear()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/library', expect.anything())
    })
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

    fireEvent.click(await screen.findByRole('button', { name: 'Configure' }))

    expect(await screen.findByRole('heading', { name: 'Create model profile' })).toBeInTheDocument()
    expect(screen.getByLabelText(/API model alias/i)).toHaveValue('qwen-test')
    expect(screen.getByLabelText(/Primary GGUF file/i)).toHaveValue(model.primary_path)
  })

  it('deletes a downloaded model without deleting its profile', async () => {
    const model = { download_id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', primary_path: 'E:\\models\\model.gguf', file_count: 1, total_bytes: 1000 }
    const profile = { id: 'profile-1', alias: 'qwen-test', runtime_id: 'runtime-1', model_path: model.primary_path, configuration: {}, enabled: true, validation_state: 'available', source_download: { id: model.download_id, repo_id: model.repo_id, revision: model.revision, group_key: model.group_key, file_count: 1, total_bytes: 1000 } }
    const fetchMock = renderApp({ libraryList: [model], profileList: [profile] })

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