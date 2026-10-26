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
  memory?: ModelMemory[]
  /** Tokens held in each model slot's context. */
  context?: ModelContext[]
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

/** Memory a model process holds as measured by the OS; a draft shares its main model's process. */
export interface ModelMemory {
  model: string
  pid: number
  /** Dedicated GPU memory; null when the platform does not report it per process. */
  vram_bytes: number | null
  /** System memory shared with the GPU (pinned host buffers); Windows only. */
  gpu_shared_bytes: number | null
  /** Private resident memory, which includes any shared GPU memory. */
  ram_bytes: number
  /** Resident pages of memory-mapped model files, read from disk. */
  mapped_bytes: number
}

export interface ModelContext {
  model: string | null
  /** Set when slots share one unified KV cache of this many tokens. */
  shared_capacity: number | null
  slots: { id: number; used: number; total: number | null }[]
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
  /** Nothing else may be loaded in the router beside this profile's model. */
  exclusive?: boolean
  description?: string | null
  version?: number
  /** SHA-256 of the llama-server command, alias excluded; equal hashes launch identically. */
  hash?: string | null
  /** Set when the profile was added from a bundled recommendation. */
  recommendation?: ProfileRecommendation | null
  /** Latest completed result per benchmark id; kept when the runs are deleted. */
  benchmark_results?: Record<string, BenchmarkResult>
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

export interface Benchmark {
  id: string
  title: string
  prompt: string
  /** Tasks a run with complete_task starts with, from the prompt file's frontmatter. */
  tasks: string[]
  /** Set for a TypeScript benchmark, which gets the TypeScript tools; null otherwise. */
  typescript: { strict: boolean } | null
}

/** A task on the agent's task list; deleting one only marks it deleted. */
export interface BenchmarkTask {
  id: number
  title: string
  status: 'open' | 'done' | 'deleted'
  /** Whether the benchmark prompt or the agent added it. */
  source: 'prompt' | 'agent'
}

export interface SystemPrompt {
  id: string
  title: string
  text: string
}

export interface BenchmarkCatalog {
  benchmarks: Benchmark[]
  /** Bundled system prompts a run can be sent with, ahead of the benchmark prompt. */
  system_prompts: SystemPrompt[]
  default_system_prompt_id: string | null
  /** The benchmark picked until another is chosen; null falls back to the first. */
  default_benchmark_id: string | null
  /** 'off', or effort levels the chat template is asked for. */
  reasoning_levels: string[]
  /** Null: no limit, so a run may use the profile's whole context. */
  default_max_tokens: number | null
  /** The file tools a run can be given, in the order they are offered. */
  tools: BenchmarkTool[]
  active_run_id: string | null
}

export interface BenchmarkTool {
  name: string
  summary: string
  /** False until what the tool needs is installed (check_file needs ESLint). */
  available: boolean
}

export type SecretName = 'huggingface-token' | 'openrouter-api-key'

/** A credential kept in the system credential store. The value itself never reaches the browser. */
export interface SecretStatus {
  name: SecretName
  configured: boolean
  last_four: string | null
}

export interface SecuritySettings {
  /** Secrets calls are accepted from other machines, not only from the server itself. */
  allow_remote_secrets: boolean
  /** This browser is on the server itself. */
  local_request: boolean
}

export interface LinterStatus {
  state: 'missing' | 'installing' | 'installed' | 'failed'
  /** Installed versions of Node.js and each package. */
  versions: Record<string, string> | null
  installed_at: string | null
  size: number | null
  progress: { stage: 'downloading' | 'verifying' | 'extracting' | 'packages'; received?: number; total?: number | null } | null
  error: string | null
  /** What an install fetches. */
  pinned: Record<string, string>
  /** Whether the install carries TypeScript and esbuild, for the TypeScript tools. */
  typescript: boolean
  /** Installed from before the TypeScript tools; installing again adds them. */
  outdated: boolean
}

/** Timings in seconds and speeds in tokens per second; any may be missing for a run that stopped early. */
export interface BenchmarkStats {
  server_start_seconds?: number
  load_seconds?: number
  first_token_seconds?: number
  prompt_tokens?: number
  prompt_seconds?: number
  prompt_tokens_per_second?: number
  generated_tokens?: number
  decode_seconds?: number
  /** Average over the whole generation, as llama.cpp reports it. */
  decode_tokens_per_second?: number
  /** Fastest 3-second window, comparable to the dashboard's peak. */
  decode_peak_tokens_per_second?: number
  /** Speed over the last 3 seconds of generation. */
  decode_final_tokens_per_second?: number
  generation_seconds?: number
  total_seconds?: number
  /** stop, length, context (the conversation outgrew it), tool_round_limit or finish_job (task_complete on older runs). */
  finish_reason?: string
  /** With file tools: completed only when the agent called finish_job. */
  outcome?: 'completed' | 'unfinished'
  /** Requests sent: one, plus one more after each round of tool calls. */
  rounds?: number
  tool_calls?: number
  files_written?: number
  /** The most tokens the model's context held during the run, and the context's size. */
  context_peak?: number
  context_size?: number
}

/** The latest completed result of a benchmark, written onto the profile. */
export interface BenchmarkResult extends BenchmarkStats {
  run_id: string
  benchmark_title: string
  profile_version: number
  completed_at: string
  reasoning_level?: string | null
}

export type BenchmarkState = 'running' | 'completed' | 'failed' | 'cancelled'
export type BenchmarkStage = 'starting' | 'loading' | 'prefill' | 'decoding' | 'stopping' | 'done'

export interface BenchmarkRun {
  id: string
  profile_id: string
  profile_alias: string
  profile_version: number
  benchmark_id: string
  benchmark_title: string
  /** Null: no limit beyond the profile's context size. */
  max_tokens: number | null
  /** Whether the model could read and write files in the run's own folder. */
  tools: boolean
  /** The tools it was given; null for runs from before they could be chosen, which had them all. */
  tool_names?: string[] | null
  system_prompt_id: string | null
  /** 'off', an effort level, or null for the model's own default. */
  reasoning_level: string | null
  /** For a run with the TypeScript tools, whether check_typescript was strict; null otherwise. */
  typescript_strict?: boolean | null
  /** Whether each tool call kept its arguments and whole result. */
  verbose_log?: boolean
  state: BenchmarkState
  stage: BenchmarkStage
  error: string | null
  stats: BenchmarkStats
  started_at: string | null
  finished_at: string | null
  /** Current speeds while the run is in progress. */
  live?: {
    prefill?: { tokens: number | null; rate: number | null } | null
    decode?: { tokens: number | null; rate: number | null } | null
    /** The tool call being written: its name and bytes of arguments so far. */
    tool_call?: { name: string; bytes: number } | null
    /** Tokens in the model's context right now, and its size. */
    context?: { used: number; size: number } | null
    /** Seconds since the prompt was sent, on the same clock as the run's timeline. */
    elapsed?: number | null
  }
}

/**
 * One step of generation, in seconds from when the prompt was sent; end is null while it runs.
 * Thinking and answer entries point into the run's reasoning and output text by position.
 */
export type TimelineEntry = { round: number; start: number; end: number | null } & (
  | { kind: 'reading'; prompt_tokens?: number }
  | { kind: 'thinking' | 'answer'; tokens?: number; from: number; to?: number }
  | { kind: 'tool_call'; tokens?: number; names?: string[]; bytes?: number }
  | { kind: 'tool'; name: string; call: number; path?: string | null; ok?: boolean }
)

/** Context and model memory in use at one moment of a run; a reading not available is left out. */
export interface BenchmarkResource {
  /** Seconds since the prompt was sent. */
  t: number
  context_used?: number
  context_size?: number
  /** The model process's RAM, including the model file pages it holds. */
  ram_bytes?: number
  vram_bytes?: number
  /** Dedicated VRAM in use on every GPU, by any process. */
  vram_total_bytes?: number
  /** System memory in use across the machine. */
  ram_total_bytes?: number
}

export interface BenchmarkRunDetail extends BenchmarkRun {
  /** What generation spent its time on, step by step. */
  timeline: TimelineEntry[]
  prompt: string
  /** The system prompt's text as it was sent, or null for none. */
  system_prompt: string | null
  /** Speed over the recent window (span seconds), sampled about every half second. */
  samples: { t: number; phase: 'prefill' | 'decode'; tokens: number | null; rate: number; span?: number }[]
  /** Context and memory use every few seconds while generating; absent from older runs. */
  resources?: BenchmarkResource[]
  output: string
  reasoning: string
  /** Each file tool call, in order; results are cut short unless the run kept a verbose log, which also keeps the arguments as sent. */
  tool_calls: { round: number; name: string; path: string | null; ok: boolean; result: string; arguments?: string }[]
  /** The agent's task list from the planning tools, in the order tasks were added. */
  tasks: BenchmarkTask[]
  /** Files the model wrote into the run's folder. */
  artifacts: { path: string; size: number }[]
}

/** How a profile added from a recommendation compares with the bundled version. */
export interface ProfileRecommendation {
  id: string
  title: string | null
  latest_version: number | null
  /** Its settings changed since it was added, so it cannot be upgraded. */
  modified: boolean
  upgradable: boolean
}

export interface Recommendation {
  id: string
  version: number
  title: string
  summary: string
  /** The base recommendation's id, shared by all its variants. */
  family?: string
  /** The short name a variant is picked by, such as "16 GB VRAM". */
  label?: string | null
  /** draft_group names a draft model, such as an MTP head, downloaded with the model; group pins the exact model group. */
  model: { repo_id: string; quantization: string; revision: string | null; draft_group?: string | null; group?: string | null }
  tested_on: { label: string; memory_gb: number; unified_memory: boolean; backend: string; notes: string | null }
  /** memory_gb is system memory plus any discrete VRAM; vram_gb, when set, is the GPU share. */
  requirements: { memory_gb: number; vram_gb?: number | null; backends: string[] }
  profile: { alias: string; description?: string | null; exclusive?: boolean; arguments: StoredArgument[] }
  assessment: { fit: 'fits' | 'tight' | 'insufficient'; hardware_match: boolean; available_memory_gb: number }
  compatible_runtime_ids: string[]
  /** A local download of the recommended model, if there is one. */
  download: { id: string; state: string; completed_bytes: number; total_bytes: number } | null
  /** A local download of its draft model, if it has one and it was found. */
  draft_download?: { id: string; state: string; completed_bytes: number; total_bytes: number } | null
  /** Per runtime id, the profiles that launch exactly what this recommendation would. */
  matches: Record<string, string[]>
  /** Profiles added from this recommendation, under any alias. */
  profile_ids: string[]
  /** The recommended alias, or the first free alias-n when it is taken. */
  suggested_alias: string
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
  /** The file must be a GGUF model, so the editor can offer downloaded models. */
  fileType?: 'gguf'
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
  exclusive?: boolean
  description?: string | null
  /** Typed by hand; left out, the server numbers the profile itself. */
  version?: number
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
  applyRecommendation: (id: string, runtimeId: string, options: { alias?: string; addProfile?: boolean } = {}) =>
    request<{ profile: Profile | null; download: DownloadJob; ready: boolean }>(`/api/profiles/recommendations/${id}/apply`, {
      method: 'POST',
      body: JSON.stringify({
        runtime_id: runtimeId,
        ...(options.alias ? { alias: options.alias } : {}),
        ...(options.addProfile === false ? { add_profile: false } : {}),
      }),
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
  benchmarks: () => request<BenchmarkCatalog>('/api/benchmarks'),
  benchmarkRuns: () => request<BenchmarkRun[]>('/api/benchmarks/runs'),
  benchmarkRun: (runId: string) => request<BenchmarkRunDetail>(`/api/benchmarks/runs/${runId}`),
  benchmarkArtifact: (runId: string, path: string) =>
    request<{ path: string; content: string }>(`/api/benchmarks/runs/${runId}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`),
  startBenchmark: (body: { profile_id: string; benchmark_id: string; max_tokens: number | null; tools: boolean; tool_names: string[] | null; system_prompt_id: string | null; reasoning_level: string | null; verbose_log: boolean }) =>
    request<BenchmarkRunDetail>('/api/benchmarks/runs', { method: 'POST', body: JSON.stringify(body) }),
  cancelBenchmark: (runId: string) =>
    request<BenchmarkRun>(`/api/benchmarks/runs/${runId}/cancel`, { method: 'POST' }),
  benchmarkTools: () => request<{ eslint: LinterStatus }>('/api/settings/benchmark-tools'),
  installEslint: () => request<{ eslint: LinterStatus }>('/api/settings/benchmark-tools/eslint', { method: 'POST' }),
  removeEslint: () => request<{ eslint: LinterStatus }>('/api/settings/benchmark-tools/eslint', { method: 'DELETE' }),
  securitySettings: () => request<SecuritySettings>('/api/settings/security'),
  updateSecuritySettings: (allowRemoteSecrets: boolean) => request<SecuritySettings>('/api/settings/security', { method: 'PUT', body: JSON.stringify({ allow_remote_secrets: allowRemoteSecrets }) }),
  secrets: () => request<{ store: string; secrets: SecretStatus[] }>('/api/secrets'),
  saveSecret: (name: SecretName, value: string) => request<SecretStatus>(`/api/secrets/${name}`, { method: 'PUT', body: JSON.stringify({ value }) }),
  removeSecret: (name: SecretName) => request<SecretStatus>(`/api/secrets/${name}`, { method: 'DELETE' }),
  deleteBenchmarkRun: (runId: string) =>
    request<void>(`/api/benchmarks/runs/${runId}`, { method: 'DELETE' }),
  upgradeProfile: (profileId: string) =>
    request<Profile>(`/api/profiles/${profileId}/upgrade`, { method: 'POST' }),
  setProfileEnabled: (profileId: string, enabled: boolean) =>
    request<Profile>(`/api/profiles/${profileId}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled }) }),
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