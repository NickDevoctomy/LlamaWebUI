import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  BadgeCheck,
  Box,
  ChevronDown,
  CircleGauge,
  Command,
  Cpu,
  Database,
  Download,
  FileSearch,
  FolderCog,
  KeyRound,
  Library,
  LoaderCircle,
  LogOut,
  Menu,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Settings,
  ShieldCheck,
  ShieldAlert,
  Square,
  TerminalSquare,
  Trash2,
  UserRound,
  Shield,
  Pencil,
  X,
} from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import { AccessPanel } from './AccessPanel'
import { api, type LibraryModel, type LogicalModel, type Profile, type RouterModel, type Runtime } from './api'
import { DashboardPanel, formatBytes, useStatusHistory } from './Dashboard'
import { DiscoverPanel, DownloadsPanel } from './DiscoveryPanels'
import { ErrorBoundary } from './ErrorBoundary'
import { RecommendationsPanel } from './Recommendations'
import { Dialog, ProfilePanel, RuntimePanel } from './SetupPanels'
import { ThemeSwitch } from './theme'
import { APP_VERSION } from './version'

const navigation = [
  ['Dashboard', CircleGauge, 'server.read', 'Operate'],
  ['Server', Server, 'server.read', 'Operate'],
  ['Models', Library, 'library.read', 'Models'],
  ['Recommended', BadgeCheck, 'profiles.read', 'Models'],
  ['Discover', Search, 'huggingface.read', 'Models'],
  ['Downloads', Download, 'downloads.read', 'Models'],
  ['Profiles', FolderCog, 'profiles.read', 'Models'],
  ['Runtimes', Cpu, 'runtimes.read', 'Models'],
  ['Access', KeyRound, 'tokens.read', 'Administration'],
  ['Users', UserRound, 'auth.read', 'Administration'],
  ['Roles', Shield, 'auth.read', 'Administration'],
  ['Settings', Settings, 'settings.read', 'Administration'],
] as const

const sectionSummaries: Record<string, string> = {
  Dashboard: 'Router health, throughput and host resources at a glance.',
  Server: 'Process state, runtime selection and live router output.',
  Models: 'GGUF files in the managed library and the records reconciled from them.',
  Recommended: 'Profiles tuned on known hardware. Add one and its model downloads for you.',
  Discover: 'Search Hugging Face and pick a quantization to download.',
  Downloads: 'Transfers in progress, paused, failed and finished.',
  Profiles: 'Model aliases and the llama-server settings each one launches with.',
  Runtimes: 'llama-server builds this control plane can launch.',
  Access: 'API keys for clients and generated client configuration.',
  Users: 'Who can sign in to this control plane.',
  Roles: 'Privilege sets assigned to accounts.',
  Settings: 'Control-plane preferences.',
  Account: 'Your sign-in details.',
}

function sectionGroup(section: string) {
  return navigation.find(([label]) => label === section)?.[3] ?? 'Account'
}

function stateLabel(value?: string) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : 'Unknown'
}

function App() {
  const [section, setSection] = useState('Dashboard')
  const [discoveryRepository, setDiscoveryRepository] = useState<string>()
  const [selectedRuntime, setSelectedRuntime] = useState('')
  const [profileSeed, setProfileSeed] = useState<LibraryModel>()
  const queryClient = useQueryClient()
  const auth = useQuery({
    queryKey: ['auth-user'],
    queryFn: api.currentUser,
    retry: false,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  })
  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ['auth-user'], exact: true })
    },
  })
  if (auth.isError) {
    const authError = auth.error as Error & { status?: number }
    if (authError.status === 401) return <LoginScreen onLoggedIn={(user) => queryClient.setQueryData(['auth-user'], user)} />
    return <div className="auth-state"><AlertCircle size={20} /> Unable to check the control-plane session: {auth.error.message}</div>
  }
  if (auth.isPending || !auth.data) return <div className="auth-state">Checking control-plane session…</div>
  return <AuthenticatedApp user={auth.data} authReady onLogout={() => logout.mutate()} loggingOut={logout.isPending} />
}

function LoginScreen({ onLoggedIn }: { onLoggedIn: (user: Awaited<ReturnType<typeof api.currentUser>>) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({ mutationFn: () => api.login(username, password), onSuccess: onLoggedIn })
  return <main className="auth-shell">
    <section className="auth-panel">
      <div className="brand auth-brand"><div className="brand-mark"><Command size={17} strokeWidth={2.4} /></div><div><strong>Llama</strong><span>CONTROL</span></div></div>
      <p className="eyebrow">Local control plane</p>
      <h1>Sign in</h1>
      <p className="auth-copy">Authenticate to manage runtimes, model profiles, downloads, and access keys.</p>
      <form className="form-body auth-form" onSubmit={(event) => { event.preventDefault(); login.mutate() }}>
        <label className="form-field"><span>Username</span><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
        <label className="form-field"><span>Password</span><input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        {login.error && <div className="form-error"><AlertCircle size={15} /> {login.error.message}</div>}
        <button className="button primary" disabled={login.isPending || !username || !password} type="submit">{login.isPending ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />} Sign in</button>
      </form>
    </section>
  </main>
}

function AuthenticatedApp({ user, authReady, onLogout, loggingOut }: { user: Awaited<ReturnType<typeof api.currentUser>>; authReady: boolean; onLogout: () => void; loggingOut: boolean }) {
  const [section, setSection] = useState(user.default_credentials ? 'Account' : 'Dashboard')
  const [discoveryRepository, setDiscoveryRepository] = useState<string>()
  const [discoverySearch, setDiscoverySearch] = useState<string>()
  const [selectedRuntime, setSelectedRuntime] = useState('')
  const [profileSeed, setProfileSeed] = useState<LibraryModel>()
  const queryClient = useQueryClient()
  const canRead = (privilege: string) => user.privileges?.includes(privilege) ?? false
  const visibleNavigation = user.default_credentials
    ? []
    : navigation.filter(([, , privilege]) => canRead(privilege))
  const hasVisibleSection = section === 'Account' || visibleNavigation.some(([label]) => label === section)
  useEffect(() => {
    if (!authReady) return
    if (user.default_credentials) {
      setSection('Account')
    } else if (!hasVisibleSection) {
      setSection(visibleNavigation[0]?.[0] ?? '')
    }
  }, [authReady, hasVisibleSection, user.default_credentials, visibleNavigation])
  const status = useQuery({
    queryKey: ['server'],
    queryFn: api.serverStatus,
    enabled: authReady && canRead('server.read'),
    refetchInterval: (query) => query.state.data?.state === 'ready' ? 2000 : 5000,
  })
  const history = useStatusHistory(status.data, status.dataUpdatedAt)
  const [navOpen, setNavOpen] = useState(false)
  useEffect(() => {
    if (!navOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setNavOpen(false) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [navOpen])
  const navigate = (label: string) => {
    setSection(label)
    setNavOpen(false)
    if (label === 'Discover') { setDiscoveryRepository(undefined); setDiscoverySearch(undefined) }
  }
  const runtimes = useQuery({ queryKey: ['runtimes'], queryFn: api.runtimes, enabled: authReady && canRead('runtimes.read') })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.profiles, enabled: authReady && canRead('profiles.read') })
  const tokens = useQuery({ queryKey: ['tokens'], queryFn: api.tokens, enabled: authReady && canRead('tokens.read') })
  const downloads = useQuery({
    queryKey: ['downloads'],
    queryFn: api.downloads,
    enabled: authReady && canRead('downloads.read'),
    refetchInterval: (query) => query.state.data?.some((job) => ['queued', 'downloading'].includes(job.state)) ? 2000 : false,
  })
  const library = useQuery({ queryKey: ['library'], queryFn: api.library, enabled: authReady && canRead('library.read'), refetchInterval: 5000 })
  const logicalLibrary = useQuery({ queryKey: ['logical-library'], queryFn: api.logicalLibrary, enabled: authReady && canRead('library.read'), refetchInterval: 5000 })
  const running = status.data?.state === 'ready' || status.data?.state === 'degraded'
  const models = useQuery({
    queryKey: ['models'],
    queryFn: api.models,
    enabled: authReady && running && canRead('server.read'),
    // Poll quickly while a model loads so its state change shows promptly.
    refetchInterval: (query) => query.state.data?.some((model) => model.status.value === 'loading') ? 1000 : 5000,
  })
  const users = useQuery({ queryKey: ['users'], queryFn: api.users, enabled: authReady && canRead('auth.read') })
  const roles = useQuery({ queryKey: ['roles'], queryFn: api.roles, enabled: authReady && canRead('auth.read') })
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
    onSettled: () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['models'] }),
      queryClient.invalidateQueries({ queryKey: ['server'] }),
    ]),
  })
  const rollback = useMutation({
    mutationFn: api.rollback,
    onSuccess: refresh,
  })

  const runtime = runtimes.data?.find((item) => item.id === selectedRuntime)
    ?? runtimes.data?.find((item) => item.usable)
  const loadedCount = models.data?.filter((model) => model.status.value === 'loaded').length ?? 0
  // The router reports ready while it is still loading a model, so surface that separately.
  const loadingModel = running ? models.data?.find((model) => model.status.value === 'loading') : undefined
  const error = lifecycle.error ?? rollback.error ?? status.error

  const activeDownloadCount = downloads.data?.filter((job) => ['queued', 'downloading'].includes(job.state)).length ?? 0

  return (
    <div className="app-shell" data-nav-open={navOpen || undefined}>
      <aside className="sidebar" id="primary-sidebar">
        <div className="sidebar-top">
          <div className="brand" aria-label="Llama Web UI">
            <div className="brand-mark"><Command size={17} strokeWidth={2.4} /></div>
            <div><strong>Llama</strong><span>CONTROL</span></div>
          </div>
          <button aria-label="Close navigation" className="icon-button small nav-close" onClick={() => setNavOpen(false)} type="button"><X size={18} /></button>
        </div>
        <nav aria-label="Primary navigation">
          {visibleNavigation.map(([label, Icon, , group], index) => (
            <Fragment key={label}>
              {group !== visibleNavigation[index - 1]?.[3] && <p className="nav-label">{group}</p>}
              <button
                aria-label={label}
                aria-current={section === label ? 'page' : undefined}
                className={section === label ? 'nav-item active' : 'nav-item'}
                onClick={() => navigate(label)}
                type="button"
              >
                <Icon size={17} />
                <span>{label}</span>
                {label === 'Downloads' && activeDownloadCount > 0 && <span className="nav-count">{activeDownloadCount}</span>}
              </button>
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="sidebar-user">
            <button aria-label="Account" className="icon-button account-button" onClick={() => navigate('Account')} title="Account" type="button"><UserRound size={15} /></button>
            <div><strong>{user.username}</strong>{user.default_credentials && <span className="default-warning">Default password active</span>}</div>
            <button aria-label="Log out" className="icon-button small sidebar-logout" disabled={loggingOut} onClick={onLogout} title="Log out" type="button"><LogOut size={15} /></button>
          </div>
          <div className="local-badge"><ShieldCheck size={14} /> Local control plane <span>v{APP_VERSION}</span></div>
        </div>
      </aside>
      <button aria-hidden="true" className="nav-scrim" onClick={() => setNavOpen(false)} tabIndex={-1} type="button" />

      <main>
        <header className="topbar">
          <button aria-controls="primary-sidebar" aria-expanded={navOpen} aria-label="Open navigation" className="icon-button nav-toggle" onClick={() => setNavOpen(true)} type="button"><Menu size={18} /></button>
          <div className="status-cluster">
            <div className="status-primary" data-state={loadingModel ? 'loading' : status.data?.state ?? 'offline'} title={loadingModel ? `Router ${status.data?.state}, loading ${loadingModel.id}` : undefined}>
              <span className={`state-dot ${loadingModel ? 'loading' : status.data?.state ?? 'offline'}`} />
              <small>Router</small>
              <strong>{loadingModel ? 'Loading model' : stateLabel(status.data?.state)}</strong>
            </div>
            <div className="status-stat"><small>Endpoint</small><span className="mono">{status.data?.endpoint ?? 'Unavailable'}</span></div>
            <div className="status-stat"><small>Runtime</small><span>{runtime?.name ?? 'Not selected'}</span></div>
            <div className="status-stat"><small>Models</small><span>{loadingModel ? `Loading ${loadingModel.id}` : `${loadedCount} loaded`}</span></div>
          </div>
          <div className="topbar-actions">
            <ThemeSwitch />
            <button aria-label="Refresh data" className="icon-button" onClick={refresh} title="Refresh data" type="button">
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        <div className="workspace">
          <section className="page-heading">
            <div>
              <p className="eyebrow">{sectionGroup(section)}</p>
              <h1>{section}</h1>
              {sectionSummaries[section] && <p className="page-summary">{sectionSummaries[section]}</p>}
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

          <ErrorBoundary resetKey={section}>
          {section === 'Dashboard' ? (
            <DashboardPanel
              downloads={downloads.data ?? []}
              history={history}
              library={library.data ?? []}
              models={models.data ?? []}
              onNavigate={(label) => { if (visibleNavigation.some(([item]) => item === label)) navigate(label) }}
              profiles={profiles.data ?? []}
              running={running}
              runtime={runtime}
              runtimes={runtimes.data ?? []}
              status={status.data}
              tokens={tokens.data ?? []}
            />
          ) : section === 'Models' ? (
            <ModelsPanel
              models={library.data ?? []}
              logicalModels={logicalLibrary.data ?? []}
              profiles={profiles.data ?? []}
              onConfigure={(model) => { setProfileSeed(model); setSection('Profiles') }}
              onDiscover={() => { setDiscoveryRepository(undefined); setDiscoverySearch(undefined); setSection('Discover') }}
              onDiscoverRepository={(repoId) => { setDiscoveryRepository(repoId); setDiscoverySearch(undefined); setSection('Discover') }}
              onRefresh={() => { void refreshModels() }}
              running={running}
            />
          ) : section === 'Server' ? (
            <ServerPanel
              canManageModels={canRead('server.write')}
              modelAction={{
                error: modelAction.error,
                pendingId: modelAction.isPending ? modelAction.variables?.id : undefined,
                run: (id, loaded) => modelAction.mutate({ id, loaded }),
              }}
              models={models.data ?? []}
              onRestart={() => lifecycle.mutate('restart')}
              onRollback={() => rollback.mutate()}
              onRuntime={setSelectedRuntime}
              profiles={profiles.data ?? []}
              restarting={lifecycle.isPending || rollback.isPending}
              running={running}
              runtimes={runtimes.data ?? []}
              selectedRuntime={runtime?.id ?? ''}
              status={status.data}
            />
          ) : section === 'Runtimes' ? (
            <RuntimePanel runtimes={runtimes.data ?? []} />
          ) : section === 'Profiles' ? (
            <ProfilePanel initialModel={profileSeed} library={library.data ?? []} onDiscover={({ repoId, query }) => { setDiscoveryRepository(repoId ?? undefined); setDiscoverySearch(repoId ? undefined : query); setSection('Discover') }} onInitialModelConsumed={() => setProfileSeed(undefined)} profiles={profiles.data ?? []} running={running} runtimes={runtimes.data ?? []} />
          ) : section === 'Access' ? (
            <AccessPanel running={running} tokens={tokens.data ?? []} />
          ) : section === 'Recommended' ? (
            <RecommendationsPanel
              canApply={canRead('profiles.write') && canRead('downloads.write')}
              onOpenProfiles={() => navigate('Profiles')}
              onOpenRuntimes={() => navigate('Runtimes')}
              profiles={profiles.data ?? []}
              runtimes={runtimes.data ?? []}
            />
          ) : section === 'Discover' ? (
            <DiscoverPanel jobs={downloads.data ?? []} library={library.data ?? []} initialQuery={discoverySearch} initialRepoId={discoveryRepository} onQueued={() => setSection('Downloads')} />
          ) : section === 'Downloads' ? (
            <DownloadsPanel jobs={downloads.data ?? []} library={library.data ?? []} onCreateProfile={(model) => { setProfileSeed(model); setSection('Profiles') }} />
          ) : section === 'Account' ? (
            <AccountPanel user={user} />
          ) : section === 'Settings' ? (
            <SettingsPanel />
          ) : section === 'Users' ? (
            <UsersPanel users={users.data ?? []} roles={roles.data ?? []} canManage={user.privileges?.includes('auth.write') ?? true} />
          ) : section === 'Roles' ? (
            <RolesPanel roles={roles.data ?? []} canManage={user.privileges?.includes('auth.write') ?? true} />
          ) : (
            <CollectionPanel section={section} runtimes={runtimes.data ?? []} profiles={profiles.data ?? []} tokens={tokens.data ?? []} />
          )}
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}

function ModelsPanel({ models, logicalModels, profiles, onConfigure, onDiscover, onDiscoverRepository, onRefresh, running }: {
  models: LibraryModel[]
  logicalModels: LogicalModel[]
  profiles: Profile[]
  onConfigure: (model: LibraryModel) => void
  onDiscover: () => void
  onDiscoverRepository: (repoId: string) => void
  onRefresh: () => void
  running: boolean
}) {
  const [deleting, setDeleting] = useState<LibraryModel>()
  const [reconciling, setReconciling] = useState(false)
  const [reconcileResult, setReconcileResult] = useState<string>()
  const [removingLogical, setRemovingLogical] = useState<LogicalModel>()
  const [discovered, setDiscovered] = useState<{ primary_path: string; files: string[]; total_bytes: number; model_name: string }[]>([])
  const [importing, setImporting] = useState<{ primary_path: string; model_name: string }>()
  const [importAlias, setImportAlias] = useState('')
  const importExternal = useMutation({
    mutationFn: () => api.importExternalModel(importing!.primary_path, profiles[0]?.runtime_id ?? '', importAlias),
    onSuccess: async () => {
      setImporting(undefined)
      setImportAlias('')
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
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
  const logicalRemoval = useMutation({
    mutationFn: (id: string) => api.deleteLogicalModel(id),
    onSuccess: async () => {
      setRemovingLogical(undefined)
      await queryClient.invalidateQueries({ queryKey: ['logical-library'] })
    },
  })
  async function reconcile() {
    setReconciling(true)
    try {
      const result = await api.reconcileLibrary()
      setReconcileResult(`${result.valid_models} valid model(s), ${result.invalid_jobs} invalid job(s), ${result.stray_gguf_files} stray GGUF file(s), ${result.logical_models} logical record(s), ${result.missing_logical_models} missing logical model(s), ${result.linked_logical_models} linked logical model(s).`)
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['library'] }), queryClient.invalidateQueries({ queryKey: ['logical-library'] })])
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
      {discovered.length > 0 && <div className="panel-footer"><span>Found {discovered.length} complete external model set(s).</span>{discovered.map((model) => <span className="mono" key={model.primary_path}>{model.primary_path} <button className="button row-button" onClick={() => { setImporting(model); setImportAlias(model.model_name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-')) }} type="button">Import as profile</button></span>)}</div>}
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
                  <td><div className="row-actions">{configured ? <span className="profile-tag">Configured</span> : <button className="button row-button" onClick={() => onConfigure(model)} type="button"><FolderCog size={13} /> Configure</button>}<button aria-label={`View ${model.repo_id} on Discover`} className="icon-button small" onClick={() => onDiscoverRepository(model.repo_id)} title="View repository quants on Discover" type="button"><FileSearch size={15} /></button><button aria-label={`Delete ${model.repo_id} ${model.group_key}`} className="icon-button small danger-icon" disabled={running} onClick={() => setDeleting(model)} title="Delete downloaded model" type="button"><Trash2 size={15} /></button></div></td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!models.length && <div className="empty"><Library size={28} /><strong>No downloaded models</strong><span>Use Discover to download a complete GGUF model.</span></div>}
      </div>
      <div className="panel-footer"><span>{models.length} downloaded models</span><span><ShieldCheck size={14} /> Validated files only</span></div>
    </section><section className="data-panel">
      <div className="panel-heading"><div><h2>Logical library</h2><p>Durable records reconciled from local GGUF files and profile links.</p></div></div>
      <div className="table-wrap"><table className="models-table"><thead><tr><th>Model path</th><th>State</th><th>Files</th><th>Profiles</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{logicalModels.map((model) => <tr key={model.id}><td><div className="model-name"><span className="model-glyph">GG</span><div><strong>{String(model.metadata['general.name'] ?? 'Local model')}</strong><span>{model.primary_path}</span></div></div></td><td><span className={`state-pill ${model.validation_state}`}>{stateLabel(model.validation_state)}</span></td><td>{model.files.length}</td><td>{model.profile_ids.length}</td><td>{model.validation_state === 'missing' && <button className="button row-button" disabled={model.profile_ids.length > 0 || logicalRemoval.isPending} onClick={() => setRemovingLogical(model)} type="button"><Trash2 size={13} /> Remove record</button>}</td></tr>)}</tbody></table>{!logicalModels.length && <div className="empty"><Library size={28} /><strong>No logical models</strong><span>Reconcile the library to create durable records.</span></div>}</div>
      <div className="panel-footer"><span>{logicalModels.length} logical record(s)</span><span>Missing records linked to profiles must be repaired first.</span></div>
    </section>{removingLogical && <Dialog title="Remove missing record?" description="This removes only the durable record; no files will be changed." onClose={() => setRemovingLogical(undefined)}><div className="confirm-body"><Trash2 size={24} /><p><strong>{removingLogical.primary_path}</strong> is missing and has no linked profiles.</p>{logicalRemoval.error && <div className="form-error"><AlertCircle size={15} /> {logicalRemoval.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setRemovingLogical(undefined)} type="button">Keep record</button><button className="button danger" disabled={logicalRemoval.isPending} onClick={() => logicalRemoval.mutate(removingLogical.id)} type="button">Remove record</button></footer></Dialog>}{importing && <Dialog title="Import external model" description="Create a disabled profile for this complete model set." onClose={() => setImporting(undefined)}><div className="form-body"><label className="form-field"><span>Profile alias</span><input autoFocus value={importAlias} onChange={(event) => setImportAlias(event.target.value.toLowerCase().replace(/\s+/g, '-'))} required /></label>{importExternal.error && <div className="form-error"><AlertCircle size={15} /> {importExternal.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setImporting(undefined)} type="button">Cancel</button><button className="button primary" disabled={importExternal.isPending || !profiles[0]?.runtime_id || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(importAlias)} onClick={() => importExternal.mutate()} type="button">{importExternal.isPending ? <LoaderCircle className="spin" size={15} /> : <FolderCog size={15} />} Import profile</button></footer></Dialog>}{deleting && <Dialog title="Delete downloaded model?" description="The downloaded files will be removed, but associated profiles will be preserved as broken." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p><strong>{deleting.repo_id}</strong> · <span className="mono">{deleting.group_key}</span> at revision <span className="mono">{deleting.revision.slice(0, 9)}</span> will be deleted. Re-downloading this exact artifact will repair its profiles.</p>{removal.error && <div className="form-error"><AlertCircle size={15} /> {removal.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep model</button><button className="button danger" disabled={removal.isPending} onClick={() => removal.mutate(deleting.download_id)} type="button">{removal.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete model</button></footer></Dialog>}</>
  )
}

interface ModelActionControl {
  error: Error | null
  pendingId?: string
  run: (id: string, loaded: boolean) => void
}

function routerModelState(model: RouterModel) {
  const value = model.status.value ?? 'unknown'
  const failed = model.status.failed === true || (value === 'unloaded' && typeof model.status.exit_code === 'number' && model.status.exit_code !== 0)
  return { value: failed ? 'failed' : value, exitCode: typeof model.status.exit_code === 'number' ? model.status.exit_code : undefined }
}

function RouterModelsPanel({ models, profiles, running, canManage, action }: {
  models: RouterModel[]
  profiles: Profile[]
  running: boolean
  canManage: boolean
  action: ModelActionControl
}) {
  const unpublished = profiles.filter((profile) => !profile.enabled).length
  const exclusive = new Set(profiles.filter((profile) => profile.exclusive).map((profile) => profile.alias))
  const occupied = models.filter((model) => ['loaded', 'loading'].includes(model.status.value ?? '')).map((model) => model.id)
  /** Mirrors the server's rule: an exclusive model never shares the router. */
  const blockedReason = (id: string) => {
    const others = occupied.filter((other) => other !== id)
    if (!others.length) return undefined
    if (exclusive.has(id)) return `Exclusive: unload ${others.join(', ')} first`
    const blocker = others.find((other) => exclusive.has(other))
    return blocker ? `${blocker} is exclusive: unload it first` : undefined
  }
  return <section className="data-panel router-models">
    <div className="panel-heading"><div><h2>Router models</h2><p>Load a profile to check it starts, or unload it to free memory, without sending a request.</p></div><Box size={20} /></div>
    {action.error && <div className="form-error" role="alert"><AlertCircle size={15} /> {action.error.message}</div>}
    {!running ? <div className="empty compact-empty"><Box size={24} /><strong>The router is not running</strong><span>Start the router to load or unload its {profiles.filter((profile) => profile.enabled).length} enabled profiles.</span></div>
      : models.length ? <ul className="router-model-list">
        {models.map((model) => {
          const { value, exitCode } = routerModelState(model)
          const profile = profiles.find((item) => item.alias === model.id)
          const path = model.path ?? profile?.model_path
          const pending = action.pendingId === model.id
          const loaded = value === 'loaded'
          const busy = pending || value === 'loading'
          const blocked = loaded ? undefined : blockedReason(model.id)
          return <li key={model.id}>
            <span className={`roster-dot ${value === 'failed' ? 'error' : value}`} />
            <span className="roster-copy"><strong>{model.id}{exclusive.has(model.id) && <span className="profile-tag" title="Nothing else can be loaded beside this model">Exclusive</span>}</strong>{blocked && !busy ? <span className="roster-note">{blocked}</span> : <span title={path}>{path?.split(/[\\/]/).at(-1) ?? 'Model path not reported'}</span>}</span>
            <span className={`state-pill ${value === 'failed' ? 'error' : value}`}>{value === 'failed' && exitCode !== undefined ? `Failed · exit ${exitCode}` : value}</span>
            <button
              aria-label={`${loaded ? 'Unload' : 'Load'} ${model.id}`}
              className={loaded ? 'button secondary compact' : 'button primary compact'}
              disabled={!canManage || busy || blocked !== undefined}
              onClick={() => action.run(model.id, loaded)}
              title={canManage ? blocked : 'Requires server write access'}
              type="button"
            >
              {busy ? <LoaderCircle className="spin" size={14} /> : loaded ? <Square size={12} fill="currentColor" /> : <Play size={13} fill="currentColor" />}
              {value === 'loading' ? 'Loading…' : pending ? (loaded ? 'Unloading…' : 'Loading…') : loaded ? 'Unload' : 'Load'}
            </button>
          </li>
        })}
      </ul> : <div className="empty compact-empty"><Box size={24} /><strong>No models published</strong><span>Enable a profile, then restart the router to publish it.</span></div>}
    {running && <div className="panel-footer"><span>{models.filter((model) => model.status.value === 'loaded').length} of {models.length} loaded</span><span>{unpublished ? `${unpublished} disabled ${unpublished === 1 ? 'profile is' : 'profiles are'} not published` : 'Load failures appear in the output below'}</span></div>}
  </section>
}

function ServerPanel({ status, runtimes, profiles, models, running, canManageModels, modelAction, selectedRuntime, onRuntime, onRestart, onRollback, restarting }: {
  status?: Awaited<ReturnType<typeof api.serverStatus>>
  runtimes: Runtime[]
  profiles: Profile[]
  models: RouterModel[]
  running: boolean
  canManageModels: boolean
  modelAction: ModelActionControl
  selectedRuntime: string
  onRuntime: (id: string) => void
  onRestart: () => void
  onRollback: () => void
  restarting: boolean
}) {
  return (
    <div className="server-grid">
      <RouterModelsPanel action={modelAction} canManage={canManageModels} models={models} profiles={profiles} running={running} />
      <section className="data-panel server-detail"><div className="panel-heading"><div><h2>Process</h2><p>Managed native llama.cpp server.</p></div><TerminalSquare size={20} /></div><dl><div><dt>State</dt><dd><span className={`state-pill ${status?.state}`}>{status?.state ?? 'unknown'}</span></dd></div><div><dt>Process ID</dt><dd>{status?.pid ?? '—'}</dd></div><div><dt>Endpoint</dt><dd className="mono">{status?.endpoint ?? '—'}</dd></div><div><dt>Last exit</dt><dd>{status?.last_exit_code ?? '—'}</dd></div><div><dt>Prompt throughput</dt><dd>{status?.timing?.prompt_tokens_per_second ? `${status.timing.prompt_tokens_per_second} t/s` : '—'}</dd></div><div><dt>Decode throughput</dt><dd>{status?.timing?.decode_tokens_per_second ? `${status.timing.decode_tokens_per_second} t/s` : '—'}</dd></div><div><dt>Active task</dt><dd>{status?.timing?.task_id ?? '—'}</dd></div><div><dt>Task elapsed</dt><dd>{status?.timing?.task_elapsed_seconds ? `${status.timing.task_elapsed_seconds}s` : '—'}</dd></div></dl></section>
      <section className="data-panel server-detail"><div className="panel-heading"><div><h2>Runtime</h2><p>Binary used for the next start or restart.</p></div><Cpu size={20} /></div><label className="select-label">Selected runtime<select value={selectedRuntime} onChange={(event) => onRuntime(event.target.value)}><option value="">Select runtime</option>{runtimes.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.name} · {runtime.build ?? 'unknown build'}</option>)}</select><ChevronDown size={16} /></label><div className="profile-summary"><small>Enabled profiles</small>{profiles.filter((profile) => profile.enabled && profile.runtime_id === selectedRuntime).map((profile) => <div key={profile.id}><strong>{profile.alias}</strong><span>{profile.model_path}</span></div>)}{!profiles.some((profile) => profile.enabled && profile.runtime_id === selectedRuntime) && <span>None configured for this runtime</span>}</div><div className="panel-footer"><span>Runtime changes are explicit</span><div className="row-actions"><button className="button secondary compact" disabled={!selectedRuntime || status?.state !== 'ready' || restarting} onClick={onRestart} type="button">{restarting ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />} Apply & restart</button><button className="button secondary compact" disabled={status?.state !== 'ready' || restarting} onClick={onRollback} type="button">Rollback</button></div></div><details className="launch-details"><summary>Effective launch arguments</summary><pre>{status?.arguments?.join(' ') || 'Unavailable'}</pre></details></section>
      <section className="data-panel log-panel"><div className="panel-heading"><div><h2>Recent output</h2><p>Chronological output · bounded in-memory tail</p></div></div><pre>{status?.logs.length ? status.logs.join('\n') : 'Waiting for router output…'}</pre></section>
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

function SettingsPanel() {
  return <section className="data-panel settings-panel">
    <div className="panel-heading"><div><h2>Settings</h2><p>Application settings will be available here in a future release.</p></div><Settings size={20} /></div>
  </section>
}

function AccountPanel({ user }: { user: Awaited<ReturnType<typeof api.currentUser>> }) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [successMessage, setSuccessMessage] = useState('')
  const queryClient = useQueryClient()
  const changePassword = useMutation({
    mutationFn: () => api.changePassword(currentPassword, newPassword),
    onSuccess: () => {
      setCurrentPassword('')
      setNewPassword('')
      setConfirmation('')
      setSuccessMessage('Password changed. Other sessions have been signed out.')
      void queryClient.invalidateQueries({ queryKey: ['auth-user'] })
    },
  })
  const valid = currentPassword.length > 0 && newPassword.length >= 8 && newPassword === confirmation
  return <section className="settings-grid">
    <section className="data-panel account-panel">
      <div className="panel-heading"><div><h2>Account</h2><p>View your account and change your password.</p></div><UserRound size={20} /></div>
      <div className="account-summary"><span className="metric-icon"><UserRound size={18} /></span><div><small>Signed in as</small><strong>{user.username}</strong></div>{user.default_credentials && <span className="profile-tag warning-tag">Default password active</span>}</div>
      {user.default_credentials && <div className="inline-notice"><ShieldAlert size={15} /> Change the default password before exposing the control plane beyond this local machine.</div>}
      <form className="form-body" onSubmit={(event) => { event.preventDefault(); changePassword.mutate() }}>
        <label className="form-field" htmlFor="current-password"><span>Current password</span><input id="current-password" autoComplete="current-password" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
        <label className="form-field" htmlFor="new-password"><span>New password</span><input id="new-password" autoComplete="new-password" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /><small>Use at least 8 characters. Existing passwords are never displayed.</small></label>
        <label className="form-field" htmlFor="confirm-new-password"><span>Confirm new password</span><input id="confirm-new-password" autoComplete="new-password" type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} aria-invalid={confirmation.length > 0 && confirmation !== newPassword} /></label>
        {changePassword.error && <div className="form-error"><AlertCircle size={15} /> {changePassword.error.message}</div>}
        {confirmation && <div className={confirmation === newPassword ? 'form-success' : 'form-error'}>{confirmation === newPassword ? 'Passwords match.' : 'Passwords do not match.'}</div>}
        {successMessage && <div className="form-success">{successMessage}</div>}
        <button className="button primary" disabled={!valid || changePassword.isPending} type="submit">{changePassword.isPending ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />} Change password</button>
      </form>
    </section>
  </section>
}

function UsersPanel({ users, roles, canManage }: { users: Awaited<ReturnType<typeof api.users>>; roles: Awaited<ReturnType<typeof api.roles>>; canManage: boolean }) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Awaited<ReturnType<typeof api.users>>[number]>()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [description, setDescription] = useState('')
  const [roleId, setRoleId] = useState('')
  const [deleting, setDeleting] = useState<Awaited<ReturnType<typeof api.users>>[number]>()
  const queryClient = useQueryClient()
  const createUser = useMutation({
    mutationFn: () => api.createUser(username, password, description, roleId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['users'] })
      setOpen(false)
      setUsername('')
      setPassword('')
      setDescription('')
      setRoleId('')
    },
  })
  const updateUser = useMutation({
    mutationFn: () => api.updateUser(editing!.id, description, roleId),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['users'] }); setEditing(undefined) },
  })
  const deleteUser = useMutation({
    mutationFn: (id: string) => api.deleteUser(id),
    onSuccess: async () => {
      setDeleting(undefined)
      await queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })
  const openCreate = () => { setRoleId(roles.find((role) => role.name === 'User')?.id ?? roles.find((role) => role.name === 'Administrator')?.id ?? roles[0]?.id ?? ''); setOpen(true) }
  const openEdit = (user: typeof editing) => { setEditing(user); setDescription(user?.description ?? ''); setRoleId(user?.role_id ?? '') }
  return <section className="data-panel">
    <div className="panel-heading"><div><h2>Users</h2><p>Control-plane accounts and assigned roles.</p></div><button className="button primary compact" disabled={!canManage} onClick={openCreate} type="button"><UserRound size={14} /> Add user</button></div>
    <div className="record-list">{users.map((managedUser) => <div className="record-row" key={managedUser.id}><span className="record-icon"><UserRound size={17} /></span><div className="record-copy"><strong>{managedUser.username}</strong><span>{managedUser.description || 'No description'}</span></div><span className="profile-tag">{managedUser.role}</span><button className="icon-button small" disabled={!canManage} aria-label={`Edit ${managedUser.username}`} onClick={() => openEdit(managedUser)} type="button"><Pencil size={14} /></button><button className="icon-button small danger-icon" disabled={!canManage || managedUser.username === 'admin' || deleteUser.isPending} aria-label={`Delete ${managedUser.username}`} title={managedUser.username === 'admin' ? 'The default admin account cannot be deleted' : 'Delete user'} onClick={() => setDeleting(managedUser)} type="button"><Trash2 size={14} /></button></div>)}</div>
    {!users.length && <div className="empty"><UserRound size={28} /><strong>No users found</strong><span>The account list is unavailable or empty.</span></div>}
    <div className="panel-footer"><span>{users.length} account(s)</span><span>Descriptions and role assignments are editable.</span></div>
    {deleteUser.error && <div className="form-error"><AlertCircle size={15} /> {deleteUser.error.message}</div>}
    {open && <Dialog title="Add user" description="Create a control-plane account with a selected role." onClose={() => setOpen(false)}><form className="form-body" onSubmit={(event) => { event.preventDefault(); createUser.mutate() }}><label className="form-field"><span>Username</span><input autoFocus autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label><label className="form-field"><span>Temporary password</span><input autoComplete="new-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><label className="form-field"><span>Description <small>(optional)</small></span><input value={description} onChange={(event) => setDescription(event.target.value)} /></label><label className="form-field"><span>Role</span><select value={roleId} onChange={(event) => setRoleId(event.target.value)}>{roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>{createUser.error && <div className="form-error"><AlertCircle size={15} /> {createUser.error.message}</div>}<footer className="dialog-actions"><button className="button secondary" onClick={() => setOpen(false)} type="button">Cancel</button><button className="button primary" disabled={createUser.isPending || !roleId || username.trim().length < 1 || password.length < 8} type="submit">{createUser.isPending ? <LoaderCircle className="spin" size={15} /> : <UserRound size={15} />} Add user</button></footer></form></Dialog>}
    {editing && <Dialog title={`Edit ${editing.username}`} description="Update the account description and role." onClose={() => setEditing(undefined)}><form className="form-body" onSubmit={(event) => { event.preventDefault(); updateUser.mutate() }}><label className="form-field"><span>Description <small>(optional)</small></span><input autoFocus value={description} onChange={(event) => setDescription(event.target.value)} /></label><label className="form-field"><span>Role</span><select value={roleId} onChange={(event) => setRoleId(event.target.value)}>{roles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>{updateUser.error && <div className="form-error"><AlertCircle size={15} /> {updateUser.error.message}</div>}<footer className="dialog-actions"><button className="button secondary" onClick={() => setEditing(undefined)} type="button">Cancel</button><button className="button primary" disabled={updateUser.isPending || !roleId} type="submit">{updateUser.isPending ? <LoaderCircle className="spin" size={15} /> : <Pencil size={15} />} Save user</button></footer></form></Dialog>}
    {deleting && <Dialog title="Delete user?" description="This permanently removes the control-plane account and its active sessions." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p>User <strong>{deleting.username}</strong> will be permanently removed.</p>{deleteUser.error && <div className="form-error" role="alert"><AlertCircle size={15} /> {deleteUser.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep user</button><button className="button danger" disabled={deleteUser.isPending} onClick={() => deleteUser.mutate(deleting.id)} type="button">{deleteUser.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete user</button></footer></Dialog>}
  </section>
}

function RolesPanel({ roles, canManage }: { roles: Awaited<ReturnType<typeof api.roles>>; canManage: boolean }) {
  const [editing, setEditing] = useState<Awaited<ReturnType<typeof api.roles>>[number]>()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [privileges, setPrivileges] = useState<string[]>([])
  const [deleting, setDeleting] = useState<Awaited<ReturnType<typeof api.roles>>[number]>()
  const queryClient = useQueryClient()
  const save = useMutation({ mutationFn: () => editing ? api.updateRole(editing.id, name, description, privileges) : api.createRole(name, description, privileges), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['roles'] }); setOpen(false); setEditing(undefined) } })
  const remove = useMutation({ mutationFn: (id: string) => api.deleteRole(id), onSuccess: async () => { setDeleting(undefined); await queryClient.invalidateQueries({ queryKey: ['roles'] }) } })
  const allPrivileges = ['auth.read', 'auth.write', 'server.read', 'server.write', 'tokens.read', 'tokens.write', 'runtimes.read', 'runtimes.write', 'profiles.read', 'profiles.write', 'huggingface.read', 'huggingface.write', 'downloads.read', 'downloads.write', 'library.read', 'library.write', 'diagnostics.read', 'diagnostics.write', 'integrations.read', 'integrations.write', 'settings.read', 'settings.write']
  const begin = (role?: typeof editing) => { setEditing(role); setName(role?.name ?? ''); setDescription(role?.description ?? ''); setPrivileges(role?.privileges ?? []); setOpen(true) }
  return <section className="data-panel">
    <div className="panel-heading"><div><h2>Roles</h2><p>Assign separate read and write privileges to control-plane accounts.</p></div><button className="button primary compact" disabled={!canManage} onClick={() => begin()} type="button"><Shield size={14} /> Add role</button></div>
    <div className="record-list">{roles.map((role) => <div className="record-row" key={role.id}><span className="record-icon"><Shield size={17} /></span><div className="record-copy"><strong>{role.name}</strong><span>{role.description || 'No description'} · {role.user_count} user(s)</span></div><span className="profile-tag">{role.protected ? 'Protected' : `${role.privileges.length} privileges`}</span>{!role.protected && <><button className="icon-button small" disabled={!canManage} aria-label={`Edit ${role.name}`} onClick={() => begin(role)} type="button"><Pencil size={14} /></button><button className="icon-button small danger-icon" disabled={!canManage || remove.isPending} aria-label={`Delete ${role.name}`} onClick={() => setDeleting(role)} type="button"><Trash2 size={14} /></button></>}</div>)}</div>
    {!roles.length && <div className="empty"><Shield size={28} /><strong>No roles found</strong><span>The protected Administrator role should be present.</span></div>}
    {open && <Dialog title={editing ? `Edit ${editing.name}` : 'Add role'} description="Choose the control-plane read and write privileges for this role." onClose={() => setOpen(false)}><form className="form-body" onSubmit={(event) => { event.preventDefault(); save.mutate() }}><label className="form-field"><span>Name</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label><label className="form-field"><span>Description <small>(optional)</small></span><input value={description} onChange={(event) => setDescription(event.target.value)} /></label><div className="privilege-grid">{allPrivileges.map((privilege) => <label className="check-row" key={privilege}><input type="checkbox" checked={privileges.includes(privilege)} onChange={(event) => setPrivileges(event.target.checked ? [...privileges, privilege] : privileges.filter((item) => item !== privilege))} /><span>{privilege}</span></label>)}</div>{save.error && <div className="form-error"><AlertCircle size={15} /> {save.error.message}</div>}<footer className="dialog-actions"><button className="button secondary" onClick={() => setOpen(false)} type="button">Cancel</button><button className="button primary" disabled={save.isPending || !name.trim()} type="submit">{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Shield size={15} />} Save role</button></footer></form></Dialog>}
    {deleting && <Dialog title="Delete role?" description="This permanently removes the role. Users must not be assigned to it." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p>Role <strong>{deleting.name}</strong> will be permanently removed.</p>{remove.error && <div className="form-error" role="alert"><AlertCircle size={15} /> {remove.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep role</button><button className="button danger" disabled={remove.isPending} onClick={() => remove.mutate(deleting.id)} type="button">{remove.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete role</button></footer></Dialog>}
  </section>
}

export default App