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
  validation_state: 'available' | 'unverified' | 'pending' | 'broken'
  /** Switches on by itself when its model download completes. */
  auto_enable?: boolean
  download?: { id: string; state: string; completed_bytes: number; total_bytes: number } | null
  source_download: {
    id: string
    repo_id: string
    revision: string
    group_key: string
    file_count: number
    total_bytes: number
  } | null
}

export interface HostHardware {
  cpu: string | null
  memory_bytes: number
  gpus: string[]
  discrete_vram_bytes: number
}

export interface Recommendation {
  id: string
  title: string
  summary: string
  model: { repo_id: string; quantization: string; revision: string | null }
  tested_on: { label: string; memory_gb: number; unified_memory: boolean; backend: string; notes: string | null }
  /** memory_gb is system memory plus any discrete VRAM; vram_gb, when set, is the GPU share. */
  requirements: { memory_gb: number; vram_gb?: number | null; backends: string[] }
  profile: { alias: string; arguments: StoredArgument[] }
  assessment: { fit: 'fits' | 'tight' | 'insufficient'; hardware_match: boolean; available_memory_gb: number }
  compatible_runtime_ids: string[]
  profile_id: string | null
}

export interface RecommendationList {
  host: HostHardware
  recommendations: Recommendation[]
}

export interface RouterModel {
  id: string
  path: string | null
  status: { value?: string; failed?: boolean; exit_code?: number }
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

export type ArgumentValueType = 'boolean' | 'integer' | 'number' | 'string' | 'path' | 'enum' | 'list' | 'json'
export type ArgumentControl = 'editable' | 'managed' | 'informational' | 'removed'
export type EffectDirection = 'increase' | 'decrease' | 'tradeoff' | 'neutral'
export type RuntimeArgumentStatus = 'advertised' | 'alias' | 'not-advertised' | 'unknown'
export type RawOrigin = 'runtime' | 'custom' | 'catalog-mismatch' | 'deprecated'

export interface ArgumentValueSchema {
  type: ArgumentValueType
  default?: string | number | boolean
  minimum?: number
  maximum?: number
  unit?: string
  options: { value: string; label?: string }[]
  separator?: string
  repeat: boolean
  itemType?: 'string' | 'integer' | 'number' | 'path'
  pathKind?: 'file' | 'directory' | 'any'
  pattern?: string
  specialValues: string[]
  negation?: string
  negationAliases: string[]
  arity: number
}

export interface ArgumentEffect {
  text: string
  direction: EffectDirection
  tags: string[]
}

export interface ArgumentDefinition {
  id: string
  flag: string
  aliases: string[]
  label: string
  category: string
  isAdvanced: boolean
  commonRank?: number
  control: ArgumentControl
  controlReason?: string
  value: ArgumentValueSchema
  help: { summary: string; details?: string; syntax?: string; examples: string[]; notes: string[]; effects: ArgumentEffect[] }
  effectTags: string[]
  safety?: { level: 'info' | 'warning' | 'danger'; warning: string; requiresConfirmation: boolean; sensitive: boolean }
  deprecated?: { message: string; replacement?: string }
  conflicts: string[]
  requires: string[]
  runtime?: { status: RuntimeArgumentStatus; advertisedAs: string | null; syntax: string | null; help: string | null; default: string | null }
}

export interface RuntimeOptionInfo {
  flag: string
  aliases: string[]
  syntax: string
  help: string
  default: string | null
}

export interface ArgumentCategory {
  id: string
  label: string
  sortOrder: number
  description?: string
}

export interface CatalogIssue {
  level: 'info' | 'warning' | 'error'
  code: string
  message: string
}

export interface ArgumentCatalog {
  catalogId: string
  catalogVersion: string
  source: { url: string; retrieved: string; reference?: string }
  categories: ArgumentCategory[]
  effectTags: Record<string, { label: string; color: string; description?: string }>
  arguments: ArgumentDefinition[]
}

export interface EffectiveArgumentCatalog extends ArgumentCatalog {
  runtime: { id?: string; name?: string; build?: string | null; helpParsed: boolean; flagsSha256?: string | null; counts: Record<RuntimeArgumentStatus, number> }
  uncatalogued: RuntimeOptionInfo[]
  issues: CatalogIssue[]
}

export type StoredArgumentValue = string | number | boolean | (string | number)[]
export type StoredArgument =
  | { id: string; value: StoredArgumentValue }
  | { flag: string; value: string | true; origin: RawOrigin }

export interface CommandModelLocation {
  /** found: the pasted path exists; relocated: matched to a local copy; missing: not on disk. */
  status: 'found' | 'relocated' | 'missing'
  requested_path: string | null
  path: string | null
  download_id: string | null
  repo_id: string | null
  search: string
}

export interface ParsedProfileCommand {
  model: CommandModelLocation
  arguments: StoredArgument[]
  ignored_arguments: string[]
}

export interface ProfileIssue {
  level: 'error' | 'warning'
  code: string
  message: string
  argument: string | null
}

export interface ProfileValidation {
  valid: boolean
  errors: string[]
  warnings: string[]
  issues: ProfileIssue[]
}

export interface ProfilePreview extends ProfileValidation {
  arguments: string[]
  command: string
  preset: string
  router_arguments: string[]
}

export interface ProfileCreate {
  alias: string
  runtime_id: string
  model_path: string
  enabled: boolean
  arguments?: StoredArgument[]
  acknowledged?: string[]
  /** Legacy flat fields, used only by the fallback editor when the catalog cannot load. */
  no_reasoning_preserve?: boolean
  n_gpu_layers?: number
  ctx_size?: number
  flash_attn?: string
  cache_type_k?: string
  cache_type_v?: string
  threads?: number
  batch_size?: number
  ubatch_size?: number
}

function errorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => typeof item === 'string' ? item : typeof item === 'object' && item && 'msg' in item ? String((item as { msg: unknown }).msg) : '')
      .filter(Boolean)
      .join('; ')
  }
  return ''
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
    const error = new Error(errorDetail(payload?.detail) || response.statusText || 'Request failed') as Error & { status?: number }
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
  deleteUser: (id: string) => request<void>(`/api/auth/users/${id}`, { method: 'DELETE' }),
  serverStatus: () => request<ServerStatus>('/api/server/status'),
  runtimes: () => request<Runtime[]>('/api/runtimes'),
  reprobeRuntime: (runtimeId: string) => request<Runtime>(`/api/runtimes/${runtimeId}/probe`, { method: 'POST' }),
  profiles: () => request<Profile[]>('/api/profiles'),
  recommendations: () => request<RecommendationList>('/api/profiles/recommendations'),
  applyRecommendation: (id: string, runtimeId: string) =>
    request<{ profile: Profile; download: DownloadJob; ready: boolean }>(`/api/profiles/recommendations/${id}/apply`, {
      method: 'POST',
      body: JSON.stringify({ runtime_id: runtimeId }),
    }),
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
    request<ProfileValidation & { preset: string; arguments: string[] }>(`/api/profiles/${profileId}/validate`, { method: 'POST' }),
  previewProfile: (profile: ProfileCreate) =>
    request<ProfilePreview>('/api/profiles/preview', { method: 'POST', body: JSON.stringify(profile) }),
  argumentCatalog: () => request<ArgumentCatalog>('/api/argument-catalog'),
  runtimeArgumentCatalog: (runtimeId: string) =>
    request<EffectiveArgumentCatalog>(`/api/runtimes/${encodeURIComponent(runtimeId)}/argument-catalog`),
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
  parseProfileCommand: (command: string, runtimeId: string) =>
    request<ParsedProfileCommand>('/api/profiles/parse-command', {
      method: 'POST',
      body: JSON.stringify({ command, runtime_id: runtimeId }),
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