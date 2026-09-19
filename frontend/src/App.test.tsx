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
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function renderApp({
  runtimeList = [],
  tokenList = [],
  serverStatus = responses['/api/server/status'],
}: {
  runtimeList?: unknown[]
  tokenList?: unknown[]
  serverStatus?: unknown
} = {}) {
  const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const path = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.pathname
        : new URL(input.url).pathname
    const payload = init?.method === 'DELETE'
      ? { ...tokenList[0] as object, enabled: false }
      : init?.method === 'POST'
      ? path === '/api/runtimes'
        ? { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
        : path === '/api/tokens'
          ? { id: 'token-1', name: 'OpenCode', token: 'lwui_once_only', last_four: 'only', expiry_note: 'Rotate monthly', enabled: true, created_at: '2026-09-19T12:00:00' }
        : { id: 'profile-1', alias: 'qwen-local', runtime_id: 'runtime-1', model_path: 'E:\\models\\qwen.gguf', configuration: {}, enabled: true }
      : path === '/api/runtimes'
        ? runtimeList
        : path === '/api/tokens'
          ? tokenList
          : path === '/api/server/status'
            ? serverStatus
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
  return fetchMock
}

describe('App', () => {
  it('renders live operational state and navigates to server details', async () => {
    renderApp()

    expect(await screen.findByText('http://127.0.0.1:1234')).toBeInTheDocument()
    expect(screen.getByText('No model profiles yet')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Server' }))

    expect(screen.getByRole('heading', { name: 'Process' })).toBeInTheDocument()
    expect(screen.getByText('Waiting for router output…')).toBeInTheDocument()
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
})