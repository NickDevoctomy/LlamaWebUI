import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
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
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Settings,
  ShieldCheck,
  Square,
  TerminalSquare,
} from 'lucide-react'
import { useState } from 'react'
import { AccessPanel } from './AccessPanel'
import { api, type Profile, type RouterModel, type Runtime } from './api'
import { DiscoverPanel, DownloadsPanel } from './DiscoveryPanels'
import { ProfilePanel, RuntimePanel } from './SetupPanels'

const navigation = [
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

function App() {
  const [section, setSection] = useState('Models')
  const [selectedRuntime, setSelectedRuntime] = useState('')
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['server'], queryFn: api.serverStatus })
  const runtimes = useQuery({ queryKey: ['runtimes'], queryFn: api.runtimes })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.profiles })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: api.tokens })
  const downloads = useQuery({
    queryKey: ['downloads'],
    queryFn: api.downloads,
    refetchInterval: (query) => query.state.data?.some((job) => ['queued', 'downloading'].includes(job.state)) ? 2000 : false,
  })
  const running = status.data?.state === 'ready' || status.data?.state === 'degraded'
  const models = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    enabled: running,
  })

  const refresh = () => queryClient.invalidateQueries()
  const lifecycle = useMutation({
    mutationFn: async (action: 'start' | 'stop' | 'restart') => {
      if (action === 'start') {
        const runtimeId = selectedRuntime || runtimes.data?.find((item) => item.usable)?.id
        if (!runtimeId) throw new Error('Register a usable runtime before starting the server.')
        return api.start(runtimeId)
      }
      return action === 'stop' ? api.stop() : api.restart()
    },
    onSuccess: refresh,
  })
  const modelAction = useMutation({
    mutationFn: ({ id, loaded }: { id: string; loaded: boolean }) =>
      loaded ? api.unloadModel(id) : api.loadModel(id),
    onSuccess: refresh,
  })

  const runtime = runtimes.data?.find((item) => item.id === selectedRuntime)
    ?? runtimes.data?.find((item) => item.usable)
  const loadedCount = models.data?.filter((model) => model.status.value === 'loaded').length ?? 0
  const error = lifecycle.error ?? modelAction.error ?? status.error

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

          {section === 'Models' ? (
            <ModelsPanel
              models={models.data ?? []}
              profiles={profiles.data ?? []}
              running={running}
              pendingModel={modelAction.variables?.id}
              onAction={(id, loaded) => modelAction.mutate({ id, loaded })}
              onAddModel={() => setSection('Profiles')}
            />
          ) : section === 'Server' ? (
            <ServerPanel status={status.data} runtimes={runtimes.data ?? []} selectedRuntime={runtime?.id ?? ''} onRuntime={setSelectedRuntime} />
          ) : section === 'Runtimes' ? (
            <RuntimePanel runtimes={runtimes.data ?? []} />
          ) : section === 'Profiles' ? (
            <ProfilePanel profiles={profiles.data ?? []} runtimes={runtimes.data ?? []} />
          ) : section === 'Access' ? (
            <AccessPanel running={running} tokens={tokens.data ?? []} />
          ) : section === 'Discover' ? (
            <DiscoverPanel onQueued={() => setSection('Downloads')} />
          ) : section === 'Downloads' ? (
            <DownloadsPanel jobs={downloads.data ?? []} />
          ) : (
            <CollectionPanel section={section} runtimes={runtimes.data ?? []} profiles={profiles.data ?? []} tokens={tokens.data ?? []} />
          )}
        </div>
      </main>
    </div>
  )
}

function ModelsPanel({ models, profiles, running, pendingModel, onAction, onAddModel }: {
  models: RouterModel[]
  profiles: Profile[]
  running: boolean
  pendingModel?: string
  onAction: (id: string, loaded: boolean) => void
  onAddModel: () => void
}) {
  return (
    <section className="data-panel">
      <div className="panel-heading">
        <div><h2>Model inventory</h2><p>Profiles available to the managed llama.cpp router.</p></div>
        <button className="button secondary compact" onClick={onAddModel} type="button"><Database size={15} /> Add model</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Model</th><th>State</th><th>Storage</th><th>Profile</th><th>Runtime</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {profiles.map((profile) => {
              const state = modelState(profile, models)
              const loaded = state === 'loaded'
              return (
                <tr key={profile.id}>
                  <td><div className="model-name"><span className="model-glyph">{profile.alias.slice(0, 2).toUpperCase()}</span><div><strong>{profile.alias}</strong><span>{profile.model_path}</span></div></div></td>
                  <td><span className={`state-pill ${state}`}>{state}</span></td>
                  <td>{modelSize(profile)}</td>
                  <td><span className="profile-tag">Default</span></td>
                  <td className="muted">llama.cpp</td>
                  <td><div className="row-actions"><button className="button row-button" disabled={!running || pendingModel === profile.alias || !profile.enabled} onClick={() => onAction(profile.alias, loaded)} type="button">{loaded ? <><Square size={12} fill="currentColor" /> Unload</> : <><Play size={13} fill="currentColor" /> Load</>}</button></div></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!profiles.length && <div className="empty"><Library size={28} /><strong>No model profiles yet</strong><span>Create a profile to make a local GGUF model available to the router.</span></div>}
      </div>
      <div className="panel-footer"><span>{profiles.length} profiles</span><span><Activity size={14} /> Live state {running ? 'connected' : 'paused'}</span></div>
    </section>
  )
}

function ServerPanel({ status, runtimes, selectedRuntime, onRuntime }: {
  status?: Awaited<ReturnType<typeof api.serverStatus>>
  runtimes: Runtime[]
  selectedRuntime: string
  onRuntime: (id: string) => void
}) {
  return (
    <div className="server-grid">
      <section className="data-panel server-detail"><div className="panel-heading"><div><h2>Process</h2><p>Managed native llama.cpp server.</p></div><TerminalSquare size={20} /></div><dl><div><dt>State</dt><dd><span className={`state-pill ${status?.state}`}>{status?.state ?? 'unknown'}</span></dd></div><div><dt>Process ID</dt><dd>{status?.pid ?? '—'}</dd></div><div><dt>Endpoint</dt><dd className="mono">{status?.endpoint ?? '—'}</dd></div><div><dt>Last exit</dt><dd>{status?.last_exit_code ?? '—'}</dd></div></dl></section>
      <section className="data-panel server-detail"><div className="panel-heading"><div><h2>Runtime</h2><p>Binary used for the next start.</p></div><Cpu size={20} /></div><label className="select-label">Selected runtime<select value={selectedRuntime} onChange={(event) => onRuntime(event.target.value)}><option value="">Select runtime</option>{runtimes.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.name} · {runtime.build ?? 'unknown build'}</option>)}</select><ChevronDown size={16} /></label></section>
      <section className="data-panel log-panel"><div className="panel-heading"><div><h2>Recent output</h2><p>Bounded in-memory process log.</p></div></div><pre>{status?.logs.length ? status.logs.join('\n') : 'Waiting for router output…'}</pre></section>
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