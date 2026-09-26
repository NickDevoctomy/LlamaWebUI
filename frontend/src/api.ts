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
  timing: Record<string, number | string | null>
  system: {
    cpu_percent: number
    ram_used_bytes: number
    ram_total_bytes: number
    network_received_bytes: number
    network_sent_bytes: number
    disk_free_bytes: number
    disk_total_bytes: number
    disk_read_bytes: number | null
    disk_write_bytes: number | null
    gpu: { utilization_percent: number; memory_used_bytes: number; memory_total_bytes: number } | null
    gpu_supported: boolean
  }
  arguments: string[]
}

export interface Runtime {
  id: string
  name: string
  executable_path: string
  build: string | null
  commit: string | null
  backend: string | null
  devices: string[]
  options: string[]
  usable: boolean
  probe_error: string | null
  probe_errors: string[]
  device_status: 'available' | 'none' | 'unavailable'
  router_compatible: boolean
  missing_router_options: string[]
  diagnostics: string[]
  help_sha256: string
}

export interface Profile {
  id: string
  alias: string
  runtime_id: string
  model_path: string
  configuration: Record<string, unknown>
  enabled: boolean
  validation_state: 'available' | 'broken'
  source_download: {
    id: string
    repo_id: string
    revision: string
    group_key: string
    file_count: number
    total_bytes: number
  } | null
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

export interface AuthUser {
  username: string
  default_credentials: boolean
  description: string | null
  role: string
  privileges: string[]
}

export interface ManagedUser {
  id: string
  username: string
  default_credentials: boolean
  description: string | null
  role_id: string
  role: string
}

export interface ManagedRole {
  id: string
  name: string
  description: string | null
  protected: boolean
  privileges: string[]
  user_count: number
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
  quantization: string | null
  total_size: number
  complete: boolean
  files: { path: string; size: number }[]
}

export interface RepositoryManifest {
  repo_id: string
  revision: string
  readme: string | null
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

export interface DiscoveredModel {
  primary_path: string
  files: string[]
  total_bytes: number
  model_name: string
  metadata: Record<string, string | number>
}

export interface LogicalModel {
  id: string
  primary_path: string
  files: string[]
  metadata: Record<string, unknown>
  validation_state: string
  profile_ids: string[]
}

export interface RuntimeRegistration {
  name: string
  executable_path: string
  backend?: string
}

export interface RuntimeReleaseAsset {
  name: string
  url: string
  size: number
  digest: string | null
}

export interface RuntimeRelease {
  tag: string
  stable_tag: string | null
  assets: RuntimeReleaseAsset[]
  groups: { key: string; primary: string; companions: string[] }[]
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
  const method = init?.method?.toUpperCase() ?? 'GET'
  const stateChanging = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(stateChanging ? { 'X-Requested-With': 'LlamaWebUI' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null
    const detail = typeof payload?.detail === 'string' ? payload.detail : response.statusText
    const error = new Error(detail || 'Request failed') as Error & { status?: number }
    error.status = response.status
    throw error
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  currentUser: () => request<AuthUser>('/api/auth/me'),
  login: (username: string, password: string) => request<AuthUser>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  logout: () => request<{ logged_out: boolean }>('/api/auth/logout', { method: 'POST' }),
  changePassword: (currentPassword: string, newPassword: string) => request<{ changed: boolean }>('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  }),
  users: () => request<ManagedUser[]>('/api/auth/users'),
  roles: () => request<ManagedRole[]>('/api/auth/roles'),
  createRole: (name: string, description: string, privileges: string[]) => request<ManagedRole>('/api/auth/roles', {
    method: 'POST', body: JSON.stringify({ name, description: description || null, privileges }),
  }),
  updateRole: (id: string, name: string, description: string, privileges: string[]) => request<ManagedRole>(`/api/auth/roles/${id}`, {
    method: 'PUT', body: JSON.stringify({ name, description: description || null, privileges }),
  }),
  deleteRole: (id: string) => request<void>(`/api/auth/roles/${id}`, { method: 'DELETE' }),
  createUser: (username: string, password: string, description: string, roleId: string) => request<ManagedUser>('/api/auth/users', {
    method: 'POST',
    body: JSON.stringify({ username, password, description: description || null, role_id: roleId || null }),
  }),
  updateUser: (id: string, description: string, roleId: string) => request<ManagedUser>(`/api/auth/users/${id}`, {
    method: 'PUT', body: JSON.stringify({ description: description || null, role_id: roleId }),
  }),
  serverStatus: () => request<ServerStatus>('/api/server/status'),
  runtimes: () => request<Runtime[]>('/api/runtimes'),
  reprobeRuntime: (runtimeId: string) => request<Runtime>(`/api/runtimes/${runtimeId}/probe`, { method: 'POST' }),
  profiles: () => request<Profile[]>('/api/profiles'),
  tokens: () => request<AccessToken[]>('/api/tokens'),
  downloads: () => request<DownloadJob[]>('/api/downloads'),
  library: () => request<LibraryModel[]>('/api/library'),
  logicalLibrary: () => request<LogicalModel[]>('/api/library/logical'),
  reconcileLibrary: () => request<{ managed_jobs: number; valid_models: number; invalid_jobs: number; stray_gguf_files: number; logical_models: number; missing_logical_models: number; linked_logical_models: number }>('/api/library/reconcile', { method: 'POST' }),
  discoverLibrary: () => request<DiscoveredModel[]>('/api/library/discover'),
  importExternalModel: (primaryPath: string, runtimeId: string, alias: string) =>
    request<Profile>('/api/library/import', { method: 'POST', body: JSON.stringify({ primary_path: primaryPath, runtime_id: runtimeId, alias }) }),
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
  clearTerminalDownloads: () =>
    request<{ cleared: number }>('/api/downloads/terminal', { method: 'DELETE' }),
  deleteLibraryModel: (downloadId: string) =>
    request<DownloadJob>(`/api/library/${downloadId}`, { method: 'DELETE' }),
  deleteLogicalModel: (logicalModelId: string) =>
    request<void>(`/api/library/logical/${logicalModelId}`, { method: 'DELETE' }),
  redownload: (downloadId: string) =>
    request<DownloadJob>(`/api/downloads/${downloadId}/redownload`, { method: 'POST' }),
  registerRuntime: (runtime: RuntimeRegistration) =>
    request<Runtime>('/api/runtimes', {
      method: 'POST',
      body: JSON.stringify(runtime),
    }),
  removeRuntime: (runtimeId: string) =>
    request<void>(`/api/runtimes/${runtimeId}`, { method: 'DELETE' }),
  runtimeRelease: (tag: string) =>
    request<RuntimeRelease>(`/api/runtimes/releases/${encodeURIComponent(tag)}`),
  installRuntime: (tag: string, assetName: string, backend?: string) =>
    request<Runtime>('/api/runtimes/install', {
      method: 'POST',
      body: JSON.stringify({ tag, asset_name: assetName, backend: backend || null }),
    }),
  createProfile: (profile: ProfileCreate) =>
    request<Profile>('/api/profiles', {
      method: 'POST',
      body: JSON.stringify(profile),
    }),
  updateProfile: (profileId: string, profile: ProfileCreate) =>
    request<Profile>(`/api/profiles/${profileId}`, { method: 'PUT', body: JSON.stringify(profile) }),
  validateProfile: (profileId: string) =>
    request<{ valid: boolean; errors: string[]; preset: string }>(`/api/profiles/${profileId}/validate`, { method: 'POST' }),
  resetProfile: (profileId: string) =>
    request<Profile>(`/api/profiles/${profileId}/reset`, { method: 'POST' }),
  cloneProfile: (profileId: string, alias: string) =>
    request<Profile>(`/api/profiles/${profileId}/clone`, { method: 'POST', body: JSON.stringify({ alias }) }),
  exportProfile: (profileId: string) => fetch(`/api/profiles/${profileId}/export`).then((response) => {
    if (!response.ok) throw new Error(response.statusText)
    return response.blob()
  }),
  profileCommand: async (profileId: string) => {
    const response = await fetch(`/api/profiles/${profileId}/command`)
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null
      throw new Error(typeof payload?.detail === 'string' ? payload.detail : response.statusText)
    }
    return response.text()
  },
  importProfile: (document: Record<string, unknown>, alias?: string) =>
    request<Profile>('/api/profiles/import', {
      method: 'POST',
      body: JSON.stringify({ document, alias: alias || null }),
    }),
  importProfileCommand: (command: string, alias: string, runtimeId: string) =>
    request<Profile>('/api/profiles/import-command', {
      method: 'POST',
      body: JSON.stringify({ command, alias, runtime_id: runtimeId, enabled: false }),
    }),
  deleteProfile: (profileId: string) =>
    request<void>(`/api/profiles/${profileId}`, { method: 'DELETE' }),
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
  restart: (runtimeId?: string) => request<ServerStatus>('/api/server/restart', {
    method: 'POST',
    body: JSON.stringify({ runtime_id: runtimeId || null }),
  }),
  rollback: () => request<ServerStatus>('/api/server/rollback', { method: 'POST' }),
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