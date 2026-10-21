import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { catalogFixture } from './test/catalogFixture'
import { APP_VERSION } from './version'

const responses: Record<string, unknown> = {
  '/api/auth/me': { username: 'admin', default_credentials: false, description: null, role: 'Administrator', privileges: ['auth.read', 'auth.write', 'server.read', 'library.read', 'profiles.read', 'downloads.read', 'tokens.read', 'runtimes.read', 'huggingface.read', 'integrations.read', 'diagnostics.read', 'settings.read'] },
  '/api/auth/users': [{ id: 'admin-1', username: 'admin', description: null, role_id: 'administrator', role: 'Administrator', default_credentials: false }],
  '/api/auth/roles': [{ id: 'administrator', name: 'Administrator', description: 'Full control', protected: true, privileges: ['auth.read', 'auth.write'], user_count: 1 }],
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
  repositoryReadme = '# Qwen model card\n\nUseful model details.',
  currentUser = responses['/api/auth/me'],
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
  repositoryReadme?: string | null
  currentUser?: unknown
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
            : path === '/api/auth/me'
              ? currentUser
            : path === '/api/server/status'
            ? serverStatus
            : path === '/api/huggingface/models'
              ? [{ repo_id: 'owner/model-GGUF', downloads: 1200, likes: 42, last_modified: '2026-09-19T00:00:00Z', gated: false, private: false, tags: ['gguf', 'qwen'] }]
                : path === '/api/huggingface/repositories/owner/model-GGUF'
                  ? { repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), readme: repositoryReadme, groups: repositoryGroups }
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
  client.setQueryData(['auth-user'], currentUser)
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
  return Object.assign(fetchMock, { client })
}

describe('App', () => {
  it('hides navigation sections without the matching read privilege', async () => {
    renderApp({
      currentUser: {
        username: 'reader',
        default_credentials: false,
        description: null,
        role: 'User',
        privileges: ['profiles.read'],
      },
    })

    expect(await screen.findByRole('button', { name: 'Profiles' })).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Downloads' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Runtimes' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Access' })).not.toBeInTheDocument()
    })
  })

  it('allows a basic User session to load its own identity', async () => {
    renderApp({
      currentUser: {
        username: 'reader',
        default_credentials: false,
        description: null,
        role: 'User',
        privileges: ['profiles.read'],
      },
    })

    expect(await screen.findByText('reader')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Profiles' })).toBeInTheDocument()
  })

  it('opens Account from the bottom-left account button and keeps Settings restricted', async () => {
    renderApp({
      currentUser: {
        username: 'reader',
        default_credentials: false,
        description: null,
        role: 'User',
        privileges: ['server.read'],
      },
    })

    expect(await screen.findByRole('button', { name: 'Account' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Account' }))
    expect(screen.getByRole('heading', { name: 'Account', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change password' })).toBeInTheDocument()
  })

  it('clears password fields after a successful password change', async () => {
    renderApp({
      onRequest: (path, init) => {
        if (path === '/api/auth/password' && init?.method === 'POST') return { changed: true }
        return undefined
      },
    })

    fireEvent.click(await screen.findByRole('button', { name: 'Account' }))
    expect(await screen.findByRole('heading', { name: 'Account', level: 2 })).toBeInTheDocument()
    const current = await screen.findByLabelText('Current password')
    const next = await screen.findByLabelText(/New password/)
    const confirmation = await screen.findByLabelText('Confirm new password')
    fireEvent.change(current, { target: { value: 'admin' } })
    fireEvent.change(next, { target: { value: 'new-password' } })
    fireEvent.change(confirmation, { target: { value: 'new-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))

    await waitFor(() => {
      expect(screen.getByText('Password changed. Other sessions have been signed out.')).toBeInTheDocument()
      expect(current).toHaveValue('')
      expect(next).toHaveValue('')
      expect(confirmation).toHaveValue('')
    })
    expect(screen.queryByText('Passwords do not match.')).not.toBeInTheDocument()
  })

  it('renders Settings as an empty full-width section for users with settings access', async () => {
    renderApp()
    fireEvent.click(await screen.findByRole('button', { name: 'Settings' }))
    expect(screen.getByRole('heading', { name: 'Settings', level: 2 })).toBeInTheDocument()
    expect(screen.getByText('Application settings will be available here in a future release.')).toBeInTheDocument()
  })

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
    expect(screen.getByText(`v${APP_VERSION}`)).toBeInTheDocument()
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

  it('fills the profile editor from a pasted command and relocates its model', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const localPath = 'E:\\models\\unsloth\\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf'
    const foreignPath = 'C:\\Users\\someone\\.lmstudio\\models\\unsloth\\Qwen3.8-Flash-Next-GGUF\\Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf'
    const library = [{ download_id: 'download-1', repo_id: 'unsloth/Qwen3.8-Flash-Next-GGUF', revision: 'a'.repeat(40), group_key: 'Qwen3.8-Flash-Next-UD-IQ4_XS', primary_path: localPath, file_count: 3, total_bytes: 3 }]
    const command = `llama-server.exe ^\n  -m ${foreignPath} ^\n  --ctx-size 262144 --port 8080`
    const fetchMock = renderApp({
      runtimeList: [runtime],
      libraryList: library,
      onRequest: (path) => {
        if (path === '/api/runtimes/runtime-1/argument-catalog') return catalogFixture()
        if (path === '/api/profiles/parse-command') {
          return {
            model: { status: 'relocated', requested_path: foreignPath, path: localPath, download_id: 'download-1', repo_id: 'unsloth/Qwen3.8-Flash-Next-GGUF', search: 'Qwen3.8-Flash-Next' },
            arguments: [{ id: 'ctx-size', value: 262144 }],
            ignored_arguments: ['--port 8080'],
          }
        }
        return undefined
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    const openImport = await screen.findByRole('button', { name: 'Import command' })
    await waitFor(() => expect(openImport).toBeEnabled())
    fireEvent.click(openImport)
    const dialog = await screen.findByRole('dialog', { name: 'Create model profile' })
    fireEvent.change(within(dialog).getByLabelText(/llama-server command/), { target: { value: command } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply command' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profiles/parse-command', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ command, runtime_id: 'runtime-1' }),
    })))
    expect(await within(dialog).findByRole('status')).toHaveTextContent(`Model found locally at ${localPath}; the command referenced ${foreignPath}. 1 argument applied`)
    expect(within(dialog).getByRole('status')).toHaveTextContent('Skipped options managed by LlamaWebUI: --port 8080.')
    expect(within(dialog).getByLabelText(/Primary GGUF file/i)).toHaveValue(localPath)
    expect(within(dialog).getByLabelText(/API model alias/i)).toHaveValue('qwen3.8-flash-next')
    expect(within(dialog).queryByLabelText(/llama-server command/)).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('tab', { name: /Arguments/ }))
    expect(await within(dialog).findByLabelText('Context size')).toHaveValue('262144')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create profile' }))

    await waitFor(() => {
      const create = fetchMock.mock.calls.find(([path, init]) => path === '/api/profiles' && init?.method === 'POST')
      expect(JSON.parse(String(create?.[1]?.body))).toEqual({ alias: 'qwen3.8-flash-next', runtime_id: 'runtime-1', model_path: localPath, enabled: true, arguments: [{ id: 'ctx-size', value: 262144 }], acknowledged: [] })
    })
  })

  it('warns when a pasted command names a missing model and opens its repository on Discover', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const foreignPath = 'C:\\Users\\someone\\.lmstudio\\models\\owner\\model-GGUF\\model-Q4_K_M.gguf'
    const fetchMock = renderApp({
      runtimeList: [runtime],
      onRequest: (path) => {
        if (path === '/api/runtimes/runtime-1/argument-catalog') return catalogFixture()
        if (path === '/api/profiles/parse-command') {
          return { model: { status: 'missing', requested_path: foreignPath, path: foreignPath, download_id: null, repo_id: 'owner/model-GGUF', search: 'model' }, arguments: [], ignored_arguments: [] }
        }
        return undefined
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    const createButton = (await screen.findAllByRole('button', { name: 'Create profile' }))[0]
    await waitFor(() => expect(createButton).toBeEnabled())
    fireEvent.click(createButton)
    const dialog = await screen.findByRole('dialog', { name: 'Create model profile' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Paste llama-server command' }))
    fireEvent.change(within(dialog).getByLabelText(/llama-server command/), { target: { value: `llama-server -m ${foreignPath}` } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply command' }))

    const warning = await within(dialog).findByRole('alert')
    expect(warning).toHaveTextContent(`The command's model was not found locally: ${foreignPath}.`)
    expect(within(dialog).getByLabelText(/Primary GGUF file/i)).toHaveValue(foreignPath)
    fireEvent.change(within(dialog).getByLabelText(/Primary GGUF file/i), { target: { value: 'E:\\models\\other.gguf' } })
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText(/Primary GGUF file/i), { target: { value: foreignPath } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Find owner/model-GGUF on Discover' }))

    expect(await screen.findByPlaceholderText('Search models, authors, or architectures')).toHaveValue('owner/model-GGUF')
    expect(screen.queryByRole('dialog', { name: 'Create model profile' })).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith('/api/huggingface/repositories/owner/model-GGUF'))).toBe(true))
  })

  it('searches Discover by model name when a missing model has no known repository', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const fetchMock = renderApp({
      runtimeList: [runtime],
      onRequest: (path) => {
        if (path === '/api/runtimes/runtime-1/argument-catalog') return catalogFixture()
        if (path === '/api/profiles/parse-command') {
          return { model: { status: 'missing', requested_path: '/srv/Mistral-7B.BF16.gguf', path: '/srv/Mistral-7B.BF16.gguf', download_id: null, repo_id: null, search: 'Mistral-7B' }, arguments: [], ignored_arguments: [] }
        }
        return undefined
      },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    const openImport = await screen.findByRole('button', { name: 'Import command' })
    await waitFor(() => expect(openImport).toBeEnabled())
    fireEvent.click(openImport)
    const dialog = await screen.findByRole('dialog', { name: 'Create model profile' })
    fireEvent.change(within(dialog).getByLabelText(/llama-server command/), { target: { value: 'llama-server -m /srv/Mistral-7B.BF16.gguf' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply command' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('not found locally: /srv/Mistral-7B.BF16.gguf')
    expect(within(dialog).getByLabelText(/API model alias/i)).toHaveValue('mistral-7b')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Search on Discover' }))

    expect(await screen.findByPlaceholderText('Search models, authors, or architectures')).toHaveValue('Mistral-7B')
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path).startsWith('/api/huggingface/models?') && String(path).includes('Mistral-7B'))).toBe(true))
  })

  it('exports a saved profile as readable command text', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const command = '"C:\\llama tools\\llama-server.exe" --model "C:\\models\\shard 00001-of-00003.gguf" --ctx-size 4096'
    const profile = { id: 'profile-imported', alias: 'qwen-roundtrip', runtime_id: 'runtime-1', model_path: 'C:\\models\\shard 00001-of-00003.gguf', configuration: { schema_version: 2, arguments: [{ id: 'ctx-size', value: 4096 }], acknowledged: [] }, enabled: false }
    renderApp({ runtimeList: [runtime], profileList: [profile], commandResponse: command })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Command' }))

    expect(await screen.findByRole('heading', { name: 'Command for qwen-roundtrip' })).toBeInTheDocument()
    expect(screen.getByRole('dialog').querySelector('pre')?.textContent).toBe(command)
  })

  it('shows profile command failures instead of failing silently', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset'], usable: true }
    const profile = { id: 'profile-1', alias: 'qwen-local', runtime_id: 'runtime-1', model_path: 'C:\\models\\qwen.gguf', configuration: {}, enabled: true }
    const fetchMock = renderApp({ runtimeList: [runtime], profileList: [profile], onRequest: (path) => {
      if (path === '/api/profiles/profile-1/command') return undefined
      return undefined
    } })
    fetchMock.mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url, 'http://localhost').pathname
      if (path === '/api/profiles/profile-1/command') return Promise.resolve(new Response(JSON.stringify({ detail: 'command unavailable' }), { status: 502, headers: { 'Content-Type': 'application/json' } }))
      return Promise.resolve(new Response(JSON.stringify(responses[path] ?? []), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Command' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('command unavailable')
  })

  it('closes dialogs with Escape and restores focus', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'C:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset'], usable: true }
    renderApp({ runtimeList: [runtime] })
    fireEvent.click(screen.getByRole('button', { name: 'Runtimes' }))
    const register = screen.getAllByRole('button', { name: 'Register runtime' })[0]
    register.focus()
    fireEvent.mouseDown(register)
    fireEvent.click(register)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(document.activeElement).toBe(register)
  })

  it('uses an accessible confirmation dialog for user deletion', async () => {
    const user = { id: 'operator-1', username: 'operator', description: null, role_id: 'user', role: 'User', default_credentials: false }
    const fetchMock = renderApp({
      currentUser: { ...(responses['/api/auth/me'] as object), privileges: ['auth.read', 'auth.write'] },
      onRequest: (path) => path === '/api/auth/users' ? [user] : undefined,
    })
    fireEvent.click(screen.getByRole('button', { name: 'Users' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete operator' }))
    expect(await screen.findByRole('heading', { name: 'Delete user?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Keep user' }))
    expect(screen.queryByRole('heading', { name: 'Delete user?' })).not.toBeInTheDocument()
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

  it('opens a downloaded model repository in Discover to inspect available quants', async () => {
    const model = { download_id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', primary_path: 'E:\\models\\model.gguf', file_count: 1, total_bytes: 1000 }
    const profile = { id: 'profile-1', alias: 'qwen-test', runtime_id: 'runtime-1', model_path: model.primary_path, configuration: {}, enabled: true }
    const fetchMock = renderApp({ libraryList: [model], profileList: [profile] })
    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    fireEvent.click(await screen.findByRole('button', { name: `View ${model.repo_id} on Discover` }))

    expect(await screen.findByRole('heading', { name: 'Hugging Face catalog' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Quantizations' })).toBeInTheDocument()
    expect(await screen.findByText('Q4_K_M')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/api/huggingface/repositories/${model.repo_id}`,
      expect.anything(),
    ))
  })

  it('deletes a downloaded model without deleting its profile', async () => {
    const model = { download_id: 'download-1', repo_id: 'owner/Qwen-Test-GGUF', revision: 'a'.repeat(40), group_key: 'Q4/model-Q4', primary_path: 'E:\\models\\model.gguf', file_count: 1, total_bytes: 1000 }
    const profile = { id: 'profile-1', alias: 'qwen-test', runtime_id: 'runtime-1', model_path: model.primary_path, configuration: {}, enabled: true, validation_state: 'available', source_download: { id: model.download_id, repo_id: model.repo_id, revision: model.revision, group_key: model.group_key, file_count: 1, total_bytes: 1000 } }
    const fetchMock = renderApp({ libraryList: [model], profileList: [profile] })

    fireEvent.click(screen.getByRole('button', { name: 'Models' }))
    fireEvent.click(await screen.findByRole('button', { name: `Delete ${model.repo_id} ${model.group_key}` }))
    expect(screen.getByRole('heading', { name: 'Delete downloaded model?' })).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete model' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/library/download-1', expect.objectContaining({ method: 'DELETE' })))
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
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads/download-1/redownload', expect.objectContaining({ method: 'POST' })))

    fireEvent.click(screen.getByRole('button', { name: 'Delete profile qwen-test' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete profile' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profiles/profile-1', expect.objectContaining({ method: 'DELETE' })))
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

  it('creates a profile from catalog arguments and previews the exact launch vector', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const preview = { valid: true, errors: [], warnings: ['runtime does not advertise --fit'], issues: [], arguments: ['E:\\llama-server.exe', '--model', 'E:\\models\\qwen.gguf', '--ctx-size', '32768'], command: '', preset: '[qwen-local]', router_arguments: ['E:\\llama-server.exe', '--models-preset', 'llama-models.ini'] }
    const fetchMock = renderApp({ runtimeList: [runtime], onRequest: (path) => {
      if (path === '/api/runtimes/runtime-1/argument-catalog') return catalogFixture()
      if (path === '/api/profiles/preview') return preview
      return undefined
    } })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    const createButton = screen.getAllByRole('button', { name: 'Create profile' })[0]
    await waitFor(() => expect(createButton).toBeEnabled())
    fireEvent.click(createButton)
    fireEvent.change(screen.getByLabelText(/API model alias/i), { target: { value: 'Qwen Local' } })
    fireEvent.change(screen.getByLabelText(/Primary GGUF file/i), { target: { value: 'E:\\models\\qwen.gguf' } })
    fireEvent.click(screen.getByRole('tab', { name: 'Arguments' }))
    const context = await screen.findByLabelText('Context size')
    fireEvent.change(context, { target: { value: 'lots' } })
    const dialog = screen.getByRole('dialog', { name: 'Create model profile' })
    expect(within(dialog).getByRole('button', { name: 'Create profile' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: /1 blocker/ })).toBeInTheDocument()
    fireEvent.change(context, { target: { value: '32768' } })
    fireEvent.click(screen.getByRole('tab', { name: 'Launch preview' }))

    expect(await screen.findByText('No launch blockers')).toBeInTheDocument()
    expect(await screen.findByText('runtime does not advertise --fit')).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([path, init]) => path === '/api/profiles/preview' && init?.method === 'POST')
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ arguments: [{ id: 'ctx-size', value: 32768 }] })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create profile' }))

    await waitFor(() => {
      const create = fetchMock.mock.calls.find(([path, init]) => path === '/api/profiles' && init?.method === 'POST')
      expect(JSON.parse(String(create?.[1]?.body))).toEqual({ alias: 'qwen-local', runtime_id: 'runtime-1', model_path: 'E:\\models\\qwen.gguf', enabled: true, arguments: [{ id: 'ctx-size', value: 32768 }], acknowledged: [] })
    })
  })

  it('keeps the legacy editor as a fallback when the catalog cannot load', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const fetchMock = renderApp({ runtimeList: [runtime] })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    const createButton = screen.getAllByRole('button', { name: 'Create profile' })[0]
    await waitFor(() => expect(createButton).toBeEnabled())
    fireEvent.click(createButton)
    fireEvent.change(screen.getByLabelText(/API model alias/i), { target: { value: 'qwen-local' } })
    fireEvent.change(screen.getByLabelText(/Primary GGUF file/i), { target: { value: 'E:\\models\\qwen.gguf' } })
    fireEvent.click(screen.getByRole('tab', { name: 'Arguments' }))
    expect(await screen.findByText(/argument catalog could not be loaded/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Context size'), { target: { value: '32768' } })
    expect(screen.getByLabelText('GPU layers')).toBeDisabled()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create profile' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([path, init]) => path === '/api/profiles' && init?.method === 'POST')
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ alias: 'qwen-local', runtime_id: 'runtime-1', ctx_size: 32768, arguments: [] })
      expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('n_gpu_layers')
    })
  })

  it('edits a stored profile in order without losing runtime or custom parameters', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset', 'ctx-size'], usable: true }
    const other = { ...runtime, id: 'runtime-2', name: 'Old build', options: ['model', 'models-preset'] }
    const stored = [{ id: 'warmup', value: false }, { id: 'ctx-size', value: 8192 }, { flag: '--brand-new', value: '2', origin: 'runtime' }, { flag: '--future', value: true, origin: 'catalog-mismatch' }]
    const profile = { id: 'profile-1', alias: 'qwen-local', runtime_id: 'runtime-1', model_path: 'E:\\models\\qwen.gguf', configuration: { schema_version: 2, arguments: stored, acknowledged: [] }, enabled: false, validation_state: 'available', source_download: null }
    const fetchMock = renderApp({ runtimeList: [runtime, other], profileList: [profile], onRequest: (path) => {
      if (path.endsWith('/argument-catalog')) return catalogFixture()
      return undefined
    } })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Runtime'), { target: { value: 'runtime-2' } })
    fireEvent.click(screen.getByRole('tab', { name: /Arguments/ }))

    expect(await screen.findByLabelText('Context size')).toHaveValue('8192')
    expect(screen.getByText('Old build').closest('.capability-note')).toHaveTextContent('Old build does not advertise --no-warmup, --ctx-size, --brand-new, --future')
    expect(within(screen.getByRole('region', { name: 'Custom parameters' })).getByDisplayValue('--future')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Context size'), { target: { value: '16384' } })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save profile' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([path, init]) => path === '/api/profiles/profile-1' && init?.method === 'PUT')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        alias: 'qwen-local',
        runtime_id: 'runtime-2',
        model_path: 'E:\\models\\qwen.gguf',
        enabled: false,
        arguments: [{ id: 'warmup', value: false }, { id: 'ctx-size', value: 16384 }, { flag: '--brand-new', value: '2', origin: 'runtime' }, { flag: '--future', value: true, origin: 'catalog-mismatch' }],
        acknowledged: [],
      })
    })
  })

  it('shows validation warnings separately from blocking errors', async () => {
    const runtime = { id: 'runtime-1', name: 'Local CPU', executable_path: 'E:\\llama-server.exe', build: 'b11053', backend: 'cpu', devices: [], options: ['model', 'models-preset'], usable: true }
    const profile = { id: 'profile-1', alias: 'qwen-local', runtime_id: 'runtime-1', model_path: 'E:\\models\\qwen.gguf', configuration: {}, enabled: true, validation_state: 'available', source_download: null }
    renderApp({ runtimeList: [runtime], profileList: [profile], onRequest: (path) => (
      path === '/api/profiles/profile-1/validate'
        ? { valid: true, errors: [], warnings: ['runtime does not advertise --fit'], issues: [], preset: 'version = 1', arguments: [] }
        : undefined
    ) })
    fireEvent.click(screen.getByRole('button', { name: 'Profiles' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Validate' }))

    expect(await screen.findByText('This profile is valid for its selected runtime and model files.')).toBeInTheDocument()
    expect(screen.getByText('1 warning(s) that do not block launch:')).toBeInTheDocument()
    expect(screen.getByText('runtime does not advertise --fit')).toBeInTheDocument()
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

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/tokens/token-1', expect.objectContaining({ method: 'DELETE' })))
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

  it('renders the selected repository README as formatted Markdown below quantizations', async () => {
    renderApp({ repositoryReadme: '---\nlanguage:\n  - en\nlicense: apache-2.0\ntags:\n  - text-generation\n---\n\n<div><p><strong>See <a href="https://example.com/collection">our collection</a> for model versions.</strong></p></div>\n\n# Qwen details\n\nA **formatted** model description.\n\n| Field | Value |\n| --- | --- |\n| Context | 32K |' })
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))

    const title = await screen.findByRole('heading', { name: 'Qwen details', level: 1 })
    expect(title.closest('.model-card-panel')).toBeInTheDocument()
    expect(screen.getByText('formatted')).toBeInTheDocument()
    expect(screen.getByText('formatted').tagName).toBe('STRONG')
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'our collection' })).toHaveAttribute('href', 'https://example.com/collection')
    expect(screen.queryByText('language:')).not.toBeInTheDocument()
    expect(screen.queryByText('apache-2.0')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Quantizations' })).toBeInTheDocument()
  })

  it('sanitizes executable raw HTML in repository READMEs', async () => {
    renderApp({ repositoryReadme: '<img src="x" onerror="alert(1)"><script>alert(1)</script>\n\n# Safe card' })
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))

    expect(await screen.findByRole('heading', { name: 'Safe card' })).toBeInTheDocument()
    expect(document.querySelector('.model-card-content img')).not.toHaveAttribute('onerror')
    expect(document.querySelector('.model-card-content script')).not.toBeInTheDocument()
  })

  it('shows a clear empty state when a selected repository has no README', async () => {
    renderApp({ repositoryReadme: null })
    fireEvent.click(screen.getByRole('button', { name: 'Discover' }))
    fireEvent.change(screen.getByLabelText('Search models'), { target: { value: 'qwen' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByRole('button', { name: /owner\/model-GGUF/ }))

    expect(await screen.findByText('No README available')).toBeInTheDocument()
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

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads/download-1/resume', expect.objectContaining({ method: 'POST' })))
  })

  it('clears completed and cancelled download jobs', async () => {
    const job = { id: 'download-1', repo_id: 'owner/model-GGUF', revision: 'a'.repeat(40), group_key: 'model-Q4_K_M', files: [], destination: 'E:\\models', total_bytes: 1000, completed_bytes: 400, state: 'cancelled', error: null }
    const fetchMock = renderApp({ downloadList: [job] })
    fireEvent.click(screen.getByRole('button', { name: 'Downloads' }))
    const clearButton = await screen.findByRole('button', { name: 'Clear finished' })
    await waitFor(() => expect(clearButton).toBeEnabled())
    fireEvent.click(clearButton)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/downloads/terminal', expect.objectContaining({ method: 'DELETE' })))
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