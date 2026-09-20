import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  Box,
  ChevronDown,
  CircleGauge,
  Command,
  Cpu,
  Database,
  Download,
  FolderCog,
  KeyRound,
  Library,
  LoaderCircle,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Settings,
  ShieldCheck,
  Square,
  TerminalSquare,
  Trash2,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { AccessPanel } from './AccessPanel'
import { api, type DownloadJob, type LibraryModel, type Profile, type RouterModel, type Runtime } from './api'
import { DiscoverPanel, DownloadsPanel } from './DiscoveryPanels'
import { Dialog, ProfilePanel, RuntimePanel } from './SetupPanels'

const navigation = [
  ['Dashboard', CircleGauge],
  ['Models', Library],
  ['Discover', Search],
  ['Downloads', Download],
  ['Server', Server],
  ['Profiles', FolderCog],
  ['Access', KeyRound],
  ['Runtimes', Cpu],
  ['Settings', Settings],
] as const

function stateLabel(value?: string) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : 'Unknown'
}

function modelState(profile: Profile, models: RouterModel[]) {
  const live = models.find((model) => model.id === profile.alias)
  return live?.status.value ?? (profile.enabled ? 'available' : 'disabled')
}

function modelSize(profile: Profile) {
  const size = profile.configuration.model_size
  return typeof size === 'number' ? `${(size / 1024 ** 3).toFixed(1)} GB` : 'Local'
}

function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`
}

function App() {
  const [section, setSection] = useState('Dashboard')
  const [selectedRuntime, setSelectedRuntime] = useState('')
  const [profileSeed, setProfileSeed] = useState<LibraryModel>()
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: ['server'],
    queryFn: api.serverStatus,
    refetchInterval: (query) => query.state.data?.state === 'ready' ? 2000 : false,
  })
  const runtimes = useQuery({ queryKey: ['runtimes'], queryFn: api.runtimes })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.profiles })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: api.tokens })
  const downloads = useQuery({
    queryKey: ['downloads'],
    queryFn: api.downloads,
    refetchInterval: (query) => query.state.data?.some((job) => ['queued', 'downloading'].includes(job.state)) ? 2000 : false,
  })
  const library = useQuery({ queryKey: ['library'], queryFn: api.library, refetchInterval: 5000 })
  const running = status.data?.state === 'ready' || status.data?.state === 'degraded'
  const models = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    enabled: running,
  })
  const previousDownloadStates = useRef<Map<string, string> | undefined>(undefined)

  useEffect(() => {
    if (!downloads.data) return
    const currentStates = new Map(downloads.data.map((job) => [job.id, job.state]))
    const previousStates = previousDownloadStates.current
    previousDownloadStates.current = currentStates
    if (!previousStates) return
    const completed = downloads.data.some((job) =>
      job.state === 'completed'
      && previousStates.has(job.id)
      && previousStates.get(job.id) !== 'completed')
    if (completed) {
      void queryClient.invalidateQueries({ queryKey: ['library'] })
    }
  }, [downloads.data, queryClient])

  const refresh = () => queryClient.invalidateQueries()
  const refreshModels = () => queryClient.invalidateQueries({ queryKey: ['library'] })
  const lifecycle = useMutation({
    mutationFn: async (action: 'start' | 'stop' | 'restart') => {
      if (action === 'start') {
        const runtimeId = selectedRuntime || runtimes.data?.find((item) => item.usable)?.id
        if (!runtimeId) throw new Error('Register a usable runtime before starting the server.')
        return api.start(runtimeId)
      }
      return action === 'stop' ? api.stop() : api.restart(action === 'restart' ? selectedRuntime || undefined : undefined)
    },
    onSuccess: refresh,
  })
  const modelAction = useMutation({
    mutationFn: ({ id, loaded }: { id: string; loaded: boolean }) =>
      loaded ? api.unloadModel(id) : api.loadModel(id),
    onSuccess: refresh,
  })
  const rollback = useMutation({
    mutationFn: api.rollback,
    onSuccess: refresh,
  })

  const runtime = runtimes.data?.find((item) => item.id === selectedRuntime)
    ?? runtimes.data?.find((item) => item.usable)
  const loadedCount = models.data?.filter((model) => model.status.value === 'loaded').length ?? 0
  const error = lifecycle.error ?? rollback.error ?? modelAction.error ?? status.error

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand" aria-label="Llama Web UI">
          <div className="brand-mark"><Command size={17} strokeWidth={2.4} /></div>
          <div><strong>Llama</strong><span>CONTROL</span></div>
        </div>
        <nav aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          {navigation.map(([label, Icon]) => (
            <button
              aria-label={label}
              className={section === label ? 'nav-item active' : 'nav-item'}
              key={label}
              onClick={() => setSection(label)}
              type="button"
            >
              <Icon size={17} />
              <span>{label}</span>
              {label === 'Downloads' && <span className="nav-count">{downloads.data?.filter((job) => ['queued', 'downloading'].includes(job.state)).length ?? 0}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="local-badge"><ShieldCheck size={15} /> Local control plane</div>
          <span>v0.1 alpha</span>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div className="mobile-brand">LLAMA CONTROL</div>
          <div className="status-cluster">
            <span className={`state-dot ${status.data?.state ?? 'offline'}`} />
            <div className="status-primary">
              <small>Router</small>
              <strong>{stateLabel(status.data?.state)}</strong>
            </div>
            <div className="status-stat"><small>Endpoint</small><span>{status.data?.endpoint ?? 'Unavailable'}</span></div>
            <div className="status-stat"><small>Runtime</small><span>{runtime?.name ?? 'Not selected'}</span></div>
            <div className="status-stat"><small>Models</small><span>{loadedCount} loaded</span></div>
          </div>
          <button className="icon-button" onClick={refresh} title="Refresh data" type="button">
            <RefreshCw size={17} />
          </button>
        </header>

        <div className="workspace">
          <section className="page-heading">
            <div>
              <p className="eyebrow">Local model router</p>
              <h1>{section}</h1>
            </div>
            <div className="server-actions">
              {running ? (
                <>
                  <button className="button secondary" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate('restart')} type="button">
                    <RotateCcw size={16} /> Restart
                  </button>
                  <button className="button danger" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate('stop')} type="button">
                    <Square size={14} fill="currentColor" /> Stop
                  </button>
                </>
              ) : (
                <button className="button primary" disabled={lifecycle.isPending || !runtime} onClick={() => lifecycle.mutate('start')} type="button">
                  <Play size={15} fill="currentColor" /> Start router
                </button>
              )}
            </div>
          </section>

          {error && <div className="error-banner">{error.message}</div>}

          <section className="metrics" aria-label="System overview">
            <div className="metric">
              <span className="metric-icon"><CircleGauge size={18} /></span>
              <div><small>Router health</small><strong>{running ? 'Operational' : stateLabel(status.data?.state)}</strong></div>
              <span className={running ? 'trend good' : 'trend'}>{running ? 'Healthy' : 'Idle'}</span>
            </div>
            <div className="metric">
              <span className="metric-icon"><Box size={18} /></span>
              <div><small>Model profiles</small><strong>{profiles.data?.length ?? '—'}</strong></div>
              <span className="trend">{profiles.data?.filter((item) => item.enabled).length ?? 0} enabled</span>
            </div>
            <div className="metric">
              <span className="metric-icon"><Cpu size={18} /></span>
              <div><small>Runtime build</small><strong>{runtime?.build ?? 'None'}</strong></div>
              <span className="trend">{runtime?.backend?.toUpperCase() ?? 'Unconfigured'}</span>
            </div>
            <div className="metric">
              <span className="metric-icon"><KeyRound size={18} /></span>
              <div><small>Access keys</small><strong>{tokens.data?.filter((token) => token.enabled).length ?? '—'}</strong></div>
              <span className="trend">Active</span>
            </div>
          </section>

          {section === 'Dashboard' ? (
            <DashboardPanel status={status.data} runtimes={runtimes.data ?? []} profiles={profiles.data ?? []} models={models.data ?? []} downloads={downloads.data ?? []} />
          ) : section === 'Models' ? (
            <ModelsPanel
              models={library.data ?? []}
              profiles={profiles.data ?? []}
              onConfigure={(model) => { setProfileSeed(model); setSection('Profiles') }}
              onDiscover={() => setSection('Discover')}
              onRefresh={() => { void refreshModels() }}
              running={running}
            />
          ) : section === 'Server' ? (
            <ServerPanel status={status.data} runtimes={runtimes.data ?? []} profiles={profiles.data ?? []} selectedRuntime={runtime?.id ?? ''} onRuntime={setSelectedRuntime} onRestart={() => lifecycle.mutate('restart')} onRollback={() => rollback.mutate()} restarting={lifecycle.isPending || rollback.isPending} />
          ) : section === 'Runtimes' ? (
            <RuntimePanel runtimes={runtimes.data ?? []} />
          ) : section === 'Profiles' ? (
            <ProfilePanel initialModel={profileSeed} library={library.data ?? []} onInitialModelConsumed={() => setProfileSeed(undefined)} profiles={profiles.data ?? []} running={running} runtimes={runtimes.data ?? []} />
          ) : section === 'Access' ? (
            <AccessPanel running={running} tokens={tokens.data ?? []} />
          ) : section === 'Discover' ? (
            <DiscoverPanel jobs={downloads.data ?? []} library={library.data ?? []} onQueued={() => setSection('Downloads')} />
          ) : section === 'Downloads' ? (
            <DownloadsPanel jobs={downloads.data ?? []} library={library.data ?? []} onCreateProfile={(model) => { setProfileSeed(model); setSection('Profiles') }} />
          ) : (
            <CollectionPanel section={section} runtimes={runtimes.data ?? []} profiles={profiles.data ?? []} tokens={tokens.data ?? []} />
          )}
        </div>
      </main>
    </div>
  )
}

function DashboardPanel({ status, runtimes, profiles, models, downloads }: {
  status?: Awaited<ReturnType<typeof api.serverStatus>>
  runtimes: Runtime[]
  profiles: Profile[]
  models: RouterModel[]
  downloads: DownloadJob[]
}) {
  const loaded = models.filter((model) => model.status.value === 'loaded').length
  const activeDownloads = downloads.filter((job) => ['queued', 'downloading'].includes(job.state)).length
  return <section className="dashboard-grid">
    <section className="data-panel dashboard-hero"><div className="panel-heading"><div><p className="eyebrow">Operational overview</p><h2>Dashboard</h2><p>Local control-plane health and active work.</p></div><CircleGauge size={24} /></div><div className="dashboard-stats"><div><small>Router</small><strong>{stateLabel(status?.state)}</strong></div><div><small>Runtime</small><strong>{runtimes.find((runtime) => runtime.usable)?.name ?? 'None'}</strong></div><div><small>Loaded models</small><strong>{loaded}</strong></div><div><small>Profiles</small><strong>{profiles.length}</strong></div></div></section>
    <section className="data-panel"><div className="panel-heading"><div><h2>Telemetry</h2><p>Native timing samples.</p></div></div><dl className="dashboard-detail"><div><dt>Prompt processing</dt><dd>{status?.timing?.prompt_tokens_per_second ? `${status.timing.prompt_tokens_per_second} t/s` : 'Unavailable'}</dd></div><div><dt>Decode</dt><dd>{status?.timing?.decode_tokens_per_second ? `${status.timing.decode_tokens_per_second} t/s` : 'Unavailable'}</dd></div><div><dt>Peak decode</dt><dd>{status?.timing?.decode_tokens_per_second_peak ? `${status.timing.decode_tokens_per_second_peak} t/s` : 'Unavailable'}</dd></div><div><dt>Active task</dt><dd>{status?.timing?.task_id ?? 'None'}</dd></div><div><dt>Context tokens</dt><dd>{status?.timing?.context_tokens ?? 'Unavailable'}</dd></div></dl></section>
    <section className="data-panel"><div className="panel-heading"><div><h2>System telemetry</h2><p>Host resources · live snapshot</p></div><Cpu size={20} /></div><div className="gauge-grid"><GaugeCard label="CPU" value={status?.system?.cpu_percent != null ? status.system.cpu_percent : null} suffix="%" /><GaugeCard label="RAM" value={status?.system ? Math.round(status.system.ram_used_bytes / status.system.ram_total_bytes * 100) : null} suffix="%" detail={status?.system ? `${formatBytes(status.system.ram_used_bytes)} / ${formatBytes(status.system.ram_total_bytes)}` : undefined} /><GaugeCard label="Disk" value={status?.system ? Math.round((1 - status.system.disk_free_bytes / status.system.disk_total_bytes) * 100) : null} suffix="%" detail={status?.system ? `${formatBytes(status.system.disk_free_bytes)} free` : undefined} /><GaugeCard label="GPU" value={status?.system?.gpu?.utilization_percent ?? null} suffix="%" detail={status?.system?.gpu_supported ? undefined : 'Unsupported'} /><GaugeCard label="VRAM usage" value={status?.system?.gpu ? Math.round(status.system.gpu.memory_used_bytes / status.system.gpu.memory_total_bytes * 100) : null} suffix="%" detail={status?.system?.gpu ? `${formatBytes(status.system.gpu.memory_used_bytes)} / ${formatBytes(status.system.gpu.memory_total_bytes)}` : 'Unsupported'} /></div><div className="telemetry-foot"><span>Network received</span><strong>{status?.system ? formatBytes(status.system.network_received_bytes) : 'Unavailable'}</strong><span>Disk read</span><strong>{status?.system?.disk_read_bytes != null ? formatBytes(status.system.disk_read_bytes) : 'Unavailable'}</strong><span>Active downloads</span><strong>{activeDownloads}</strong></div></section>
  </section>
}

function GaugeCard({ label, value, suffix, detail }: { label: string; value: number | null; suffix: string; detail?: string }) {
  const percentage = value == null ? 0 : Math.min(100, Math.max(0, value))
  return <div className="gauge-card"><div className="gauge-ring" style={{ '--gauge-value': `${percentage * 3.6}deg` } as React.CSSProperties}><div><strong>{value == null ? '—' : `${value}${suffix}`}</strong><small>{label}</small></div></div>{detail && <span>{detail}</span>}</div>
}

function ModelsPanel({ models, profiles, onConfigure, onDiscover, onRefresh, running }: {
  models: LibraryModel[]
  profiles: Profile[]
  onConfigure: (model: LibraryModel) => void
  onDiscover: () => void
  onRefresh: () => void
  running: boolean
}) {
  const [deleting, setDeleting] = useState<LibraryModel>()
  const [reconciling, setReconciling] = useState(false)
  const [reconcileResult, setReconcileResult] = useState<string>()
  const [discovered, setDiscovered] = useState<{ primary_path: string; files: string[]; total_bytes: number }[]>([])
  const queryClient = useQueryClient()
  const removal = useMutation({
    mutationFn: (downloadId: string) => api.deleteLibraryModel(downloadId),
    onSuccess: async () => {
      setDeleting(undefined)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['library'] }),
        queryClient.invalidateQueries({ queryKey: ['profiles'] }),
      ])
    },
  })
  async function reconcile() {
    setReconciling(true)
    try {
      const result = await api.reconcileLibrary()
      setReconcileResult(`${result.valid_models} valid model(s), ${result.invalid_jobs} invalid job(s), ${result.stray_gguf_files} stray GGUF file(s).`)
      await queryClient.invalidateQueries({ queryKey: ['library'] })
    } finally {
      setReconciling(false)
    }
  }
  async function discover() {
    const result = await api.discoverLibrary()
    setDiscovered(result)
  }
  return (
    <><section className="data-panel">
      <div className="panel-heading">
        <div><h2>Downloaded models</h2><p>Validated GGUF models in the application-managed library.</p></div>
        <div className="panel-heading-actions">
          <button className="button secondary compact" disabled={reconciling} onClick={() => void reconcile()} type="button"><RefreshCw size={14} /> {reconciling ? 'Reconciling…' : 'Reconcile library'}</button><button className="button secondary compact" onClick={() => void discover()} type="button"><Search size={15} /> Discover local files</button><button className="button secondary compact" onClick={onRefresh} type="button"><RefreshCw size={14} /> Refresh models</button>
          <button className="button secondary compact" onClick={onDiscover} type="button"><Search size={15} /> Discover models</button>
        </div>
      </div>
      {reconcileResult && <div className="panel-footer"><span>{reconcileResult}</span></div>}
      {discovered.length > 0 && <div className="panel-footer"><span>Found {discovered.length} complete external model set(s).</span>{discovered.map((model) => <span className="mono" key={model.primary_path}>{model.primary_path}</span>)}</div>}
      <div className="table-wrap">
        <table className="models-table">
          <thead><tr><th>Model</th><th>Group</th><th>Size</th><th>Files</th><th>Revision</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {models.map((model) => {
              const configured = profiles.some((profile) => profile.model_path === model.primary_path)
              const name = model.repo_id.split('/').at(-1) ?? model.repo_id
              return (
                <tr key={model.download_id}>
                  <td><div className="model-name"><span className="model-glyph">{name.slice(0, 2).toUpperCase()}</span><div><strong>{model.repo_id}</strong><span>{model.primary_path}</span></div></div></td>
                  <td className="mono">{model.group_key}</td>
                  <td>{formatBytes(model.total_bytes)}</td>
                  <td>{model.file_count} {model.file_count === 1 ? 'file' : 'files'}</td>
                  <td className="mono" title={model.revision}>{model.revision.slice(0, 9)}</td>
                  <td><div className="row-actions">{configured ? <span className="profile-tag">Configured</span> : <button className="button row-button" onClick={() => onConfigure(model)} type="button"><FolderCog size={13} /> Configure</button>}<button aria-label={`Delete ${model.repo_id} ${model.group_key}`} className="icon-button small danger-icon" disabled={running} onClick={() => setDeleting(model)} title="Delete downloaded model" type="button"><Trash2 size={15} /></button></div></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!models.length && <div className="empty"><Library size={28} /><strong>No downloaded models</strong><span>Use Discover to download a complete GGUF model.</span></div>}
      </div>
      <div className="panel-footer"><span>{models.length} downloaded models</span><span><ShieldCheck size={14} /> Validated files only</span></div>
    </section>{deleting && <Dialog title="Delete downloaded model?" description="The downloaded files will be removed, but associated profiles will be preserved as broken." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p><strong>{deleting.repo_id}</strong> · <span className="mono">{deleting.group_key}</span> at revision <span className="mono">{deleting.revision.slice(0, 9)}</span> will be deleted. Re-downloading this exact artifact will repair its profiles.</p>{removal.error && <div className="form-error"><AlertCircle size={15} /> {removal.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep model</button><button className="button danger" disabled={removal.isPending} onClick={() => removal.mutate(deleting.download_id)} type="button">{removal.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete model</button></footer></Dialog>}</>
  )
}

function ServerPanel({ status, runtimes, profiles, selectedRuntime, onRuntime, onRestart, onRollback, restarting }: {
  status?: Awaited<ReturnType<typeof api.serverStatus>>
  runtimes: Runtime[]
  profiles: Profile[]
  selectedRuntime: string
  onRuntime: (id: string) => void
  onRestart: () => void
  onRollback: () => void
  restarting: boolean
}) {
  return (
    <div className="server-grid">
      <section className="data-panel server-detail"><div className="panel-heading"><div><h2>Process</h2><p>Managed native llama.cpp server.</p></div><TerminalSquare size={20} /></div><dl><div><dt>State</dt><dd><span className={`state-pill ${status?.state}`}>{status?.state ?? 'unknown'}</span></dd></div><div><dt>Process ID</dt><dd>{status?.pid ?? '—'}</dd></div><div><dt>Endpoint</dt><dd className="mono">{status?.endpoint ?? '—'}</dd></div><div><dt>Last exit</dt><dd>{status?.last_exit_code ?? '—'}</dd></div><div><dt>Prompt throughput</dt><dd>{status?.timing?.prompt_tokens_per_second ? `${status.timing.prompt_tokens_per_second} t/s` : '—'}</dd></div><div><dt>Decode throughput</dt><dd>{status?.timing?.decode_tokens_per_second ? `${status.timing.decode_tokens_per_second} t/s` : '—'}</dd></div><div><dt>Active task</dt><dd>{status?.timing?.task_id ?? '—'}</dd></div><div><dt>Task elapsed</dt><dd>{status?.timing?.task_elapsed_seconds ? `${status.timing.task_elapsed_seconds}s` : '—'}</dd></div></dl></section>
      <section className="data-panel server-detail"><div className="panel-heading"><div><h2>Runtime</h2><p>Binary used for the next start or restart.</p></div><Cpu size={20} /></div><label className="select-label">Selected runtime<select value={selectedRuntime} onChange={(event) => onRuntime(event.target.value)}><option value="">Select runtime</option>{runtimes.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.name} · {runtime.build ?? 'unknown build'}</option>)}</select><ChevronDown size={16} /></label><div className="profile-summary"><small>Enabled profiles</small>{profiles.filter((profile) => profile.enabled && profile.runtime_id === selectedRuntime).map((profile) => <div key={profile.id}><strong>{profile.alias}</strong><span>{profile.model_path}</span></div>)}{!profiles.some((profile) => profile.enabled && profile.runtime_id === selectedRuntime) && <span>None configured for this runtime</span>}</div><div className="panel-footer"><span>Runtime changes are explicit</span><div className="row-actions"><button className="button secondary compact" disabled={!selectedRuntime || status?.state !== 'ready' || restarting} onClick={onRestart} type="button">{restarting ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />} Apply & restart</button><button className="button secondary compact" disabled={status?.state !== 'ready' || restarting} onClick={onRollback} type="button">Rollback</button></div></div><details className="launch-details"><summary>Effective launch arguments</summary><pre>{status?.arguments?.join(' ') || 'Unavailable'}</pre></details></section>
      <section className="data-panel log-panel"><div className="panel-heading"><div><h2>Recent output</h2><p>Newest output first · bounded in-memory tail</p></div></div><pre>{status?.logs.length ? [...status.logs].reverse().join('\n') : 'Waiting for router output…'}</pre></section>
    </div>
  )
}

function CollectionPanel({ section, runtimes, profiles, tokens }: {
  section: string
  runtimes: Runtime[]
  profiles: Profile[]
  tokens: Awaited<ReturnType<typeof api.tokens>>
}) {
  const items = section === 'Runtimes'
    ? runtimes.map((item) => ({ title: item.name, meta: `${item.backend ?? 'unknown'} · ${item.build ?? 'unprobed'}`, state: item.usable ? 'Ready' : 'Unavailable' }))
    : section === 'Profiles'
      ? profiles.map((item) => ({ title: item.alias, meta: item.model_path, state: item.enabled ? 'Enabled' : 'Disabled' }))
      : section === 'Access'
        ? tokens.map((item) => ({ title: item.name, meta: `Key ending ${item.last_four}`, state: item.enabled ? 'Active' : 'Revoked' }))
        : []
  return <section className="data-panel"><div className="panel-heading"><div><h2>{section}</h2><p>Local control-plane records and configuration.</p></div></div>{items.length ? <div className="collection">{items.map((item) => <div className="collection-row" key={`${item.title}-${item.meta}`}><div><strong>{item.title}</strong><span>{item.meta}</span></div><span className="profile-tag">{item.state}</span></div>)}</div> : <div className="empty"><Database size={28} /><strong>No {section.toLowerCase()} to show</strong><span>This workspace will populate as items are configured.</span></div>}</section>
}

export default App