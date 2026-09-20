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

export interface CreatedAccessToken extends AccessToken {
  token: string
}

export interface ModelSearchResult {
  repo_id: string
  downloads: number
  likes: number
  last_modified: string | null
  gated: boolean
  private: boolean
  tags: string[]
}

export interface GgufGroup {
  key: string
  quantization: string
  total_size: number
  complete: boolean
  files: { path: string; size: number }[]
}

export interface RepositoryManifest {
  repo_id: string
  revision: string
  groups: GgufGroup[]
}

export type DownloadState = 'queued' | 'downloading' | 'paused' | 'completed' | 'failed' | 'cancelled'

export interface DownloadJob {
  id: string
  repo_id: string
  revision: string
  group_key: string
  files: { path: string; size: number }[]
  destination: string
  total_bytes: number
  completed_bytes: number
  state: DownloadState
  error: string | null
}

export interface LibraryModel {
  download_id: string
  repo_id: string
  revision: string
  group_key: string
  primary_path: string
  file_count: number
  total_bytes: number
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
  downloads: () => request<DownloadJob[]>('/api/downloads'),
  library: () => request<LibraryModel[]>('/api/library'),
  models: () => request<RouterModel[]>('/api/server/models'),
  searchModels: (query: string, sort = 'downloads') => {
    const parameters = new URLSearchParams({ q: query, sort })
    return request<ModelSearchResult[]>(`/api/huggingface/models?${parameters}`)
  },
  repository: (repoId: string) =>
    request<RepositoryManifest>(`/api/huggingface/repositories/${repoId}`),
  createDownload: (repoId: string, groupKey: string, revision: string) =>
    request<DownloadJob>('/api/downloads', {
      method: 'POST',
      body: JSON.stringify({ repo_id: repoId, group_key: groupKey, revision }),
    }),
  pauseDownload: (jobId: string) =>
    request<DownloadJob>(`/api/downloads/${jobId}/pause`, { method: 'POST' }),
  resumeDownload: (jobId: string) =>
    request<DownloadJob>(`/api/downloads/${jobId}/resume`, { method: 'POST' }),
  cancelDownload: (jobId: string) =>
    request<DownloadJob>(`/api/downloads/${jobId}/cancel`, { method: 'POST' }),
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
  createToken: (name: string, expiryNote?: string) =>
    request<CreatedAccessToken>('/api/tokens', {
      method: 'POST',
      body: JSON.stringify({ name, expiry_note: expiryNote || null }),
    }),
  revokeToken: (tokenId: string) =>
    request<AccessToken>(`/api/tokens/${tokenId}`, { method: 'DELETE' }),
  opencodeConfig: () => request<Record<string, unknown>>('/api/integrations/opencode'),
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