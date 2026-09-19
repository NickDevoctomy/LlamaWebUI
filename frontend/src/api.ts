export type RouterState =
  | 'stopped'
  | 'starting'
  | 'ready'
  | 'degraded'
  | 'stopping'
  | 'crashed'

export interface ServerStatus {
  state: RouterState
  pid: number | null
  last_exit_code: number | null
  endpoint: string
  logs: string[]
}

export interface Runtime {
  id: string
  name: string
  executable_path: string
  build: string | null
  backend: string | null
  devices: string[]
  options: string[]
  usable: boolean
}

export interface Profile {
  id: string
  alias: string
  runtime_id: string
  model_path: string
  configuration: Record<string, unknown>
  enabled: boolean
}

export interface RouterModel {
  id: string
  path: string | null
  status: { value?: string }
  metadata: Record<string, unknown>
}

export interface AccessToken {
  id: string
  name: string
  last_four: string
  expiry_note: string | null
  enabled: boolean
  created_at: string
}

export interface RuntimeRegistration {
  name: string
  executable_path: string
  backend?: string
}

export interface ProfileCreate {
  alias: string
  runtime_id: string
  model_path: string
  enabled: boolean
  no_reasoning_preserve: boolean
  n_gpu_layers?: number
  ctx_size?: number
  flash_attn?: string
  cache_type_k?: string
  cache_type_v?: string
  threads?: number
  batch_size?: number
  ubatch_size?: number
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null
    const detail = typeof payload?.detail === 'string' ? payload.detail : response.statusText
    throw new Error(detail || 'Request failed')
  }
  return response.json() as Promise<T>
}

export const api = {
  serverStatus: () => request<ServerStatus>('/api/server/status'),
  runtimes: () => request<Runtime[]>('/api/runtimes'),
  profiles: () => request<Profile[]>('/api/profiles'),
  tokens: () => request<AccessToken[]>('/api/tokens'),
  models: () => request<RouterModel[]>('/api/server/models'),
  registerRuntime: (runtime: RuntimeRegistration) =>
    request<Runtime>('/api/runtimes', {
      method: 'POST',
      body: JSON.stringify(runtime),
    }),
  createProfile: (profile: ProfileCreate) =>
    request<Profile>('/api/profiles', {
      method: 'POST',
      body: JSON.stringify(profile),
    }),
  start: (runtimeId: string) =>
    request<ServerStatus>('/api/server/start', {
      method: 'POST',
      body: JSON.stringify({ runtime_id: runtimeId }),
    }),
  stop: () => request<ServerStatus>('/api/server/stop', { method: 'POST' }),
  restart: () => request<ServerStatus>('/api/server/restart', { method: 'POST' }),
  loadModel: (model: string) =>
    request<{ success: boolean }>('/api/server/models/load', {
      method: 'POST',
      body: JSON.stringify({ model }),
    }),
  unloadModel: (model: string) =>
    request<{ success: boolean }>('/api/server/models/unload', {
      method: 'POST',
      body: JSON.stringify({ model }),
    }),
}