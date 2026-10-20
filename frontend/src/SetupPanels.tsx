import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  Cpu,
  Download,
  FileCog,
  Gauge,
  LoaderCircle,
  Plus,
  Search as SearchIcon,
  ShieldAlert,
  TerminalSquare,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { type ChangeEvent, type FormEvent, useEffect, useId, useMemo, useRef, useState } from 'react'
import { api, type LibraryModel, type Profile, type ProfileCreate, type ProfileValidation, type Runtime, type RuntimeRelease, type StoredArgument } from './api'
import {
  type EditorEntry,
  catalogIndex,
  clientIssues,
  entriesFromStored,
  isSet,
  storedArguments,
  unadvertisedOnRuntime,
} from './argumentCatalog'
import { LaunchPreview, ProfileArgumentEditor } from './ProfileArgumentEditor'

function optionalNumber(value: string) {
  return value === '' ? undefined : Number(value)
}

function isRuntimeArchive(name: string) {
  return /\.(?:zip|tar\.gz|tgz|tar\.xz|tar\.bz2)$/i.test(name)
}

function suggestedRuntimeAsset(name: string, backend: string) {
  const normalized = name.toLowerCase()
  if (backend === 'cpu') {
    return !/(cuda|vulkan|metal|sycl|rocm)/.test(normalized)
  }
  return normalized.includes(backend.toLowerCase())
}

function formatAssetSize(size: number) {
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KiB`
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MiB`
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GiB`
}

export function Field({ label, hint, children }: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="form-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}

export function Dialog({ title, description, onClose, children, wide = false }: {
  title: string
  description: string
  onClose: () => void
  children: React.ReactNode
  wide?: boolean
}) {
  const dialogId = useId()
  const titleId = `${dialogId}-title`
  const descriptionId = `${dialogId}-description`
  const dialogRef = useRef<HTMLElement>(null)
  const previousFocus = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  )
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const focusable = dialog.querySelector<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )
    focusable?.focus()

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const elements = Array.from(dialog?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ) ?? [])
      if (!elements.length) return
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus.current?.focus()
    }
  }, [])

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section ref={dialogRef} aria-describedby={descriptionId} aria-labelledby={titleId} aria-modal="true" className={wide ? 'dialog wide' : 'dialog'} role="dialog">
        <header className="dialog-header">
          <div><p className="eyebrow">Configuration</p><h2 id={titleId}>{title}</h2><p id={descriptionId}>{description}</p></div>
          <button aria-label="Close" className="icon-button" onClick={onClose} type="button"><X size={18} /></button>
        </header>
        {children}
      </section>
    </div>
  )
}

export function RuntimePanel({ runtimes }: { runtimes: Runtime[] }) {
  const [open, setOpen] = useState(false)
  const [installOpen, setInstallOpen] = useState(false)
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [backend, setBackend] = useState('cpu')
  const [releaseTag, setReleaseTag] = useState('latest')
  const [release, setRelease] = useState<RuntimeRelease>()
  const [assetName, setAssetName] = useState('')
  const [deletingRuntime, setDeletingRuntime] = useState<Runtime>()
  const [expandedRuntime, setExpandedRuntime] = useState<string>()
  const queryClient = useQueryClient()
  const registration = useMutation({
    mutationFn: () => api.registerRuntime({ name, executable_path: path, backend }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['runtimes'] })
      setOpen(false)
      setName('')
      setPath('')
    },
  })
  const discovery = useMutation({
    mutationFn: () => api.runtimeRelease(releaseTag.trim()),
    onSuccess: (value) => {
      setRelease(value)
      setAssetName(value.assets.find((asset) => suggestedRuntimeAsset(asset.name, backend))?.name ?? '')
    },
  })
  const installation = useMutation({
    mutationFn: () => api.installRuntime(releaseTag.trim(), assetName, backend),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['runtimes'] })
      setInstallOpen(false)
      setRelease(undefined)
      setAssetName('')
    },
  })
  const removal = useMutation({
    mutationFn: (runtimeId: string) => api.removeRuntime(runtimeId),
    onSuccess: async () => {
      setDeletingRuntime(undefined)
      await queryClient.invalidateQueries({ queryKey: ['runtimes'] })
    },
  })
  const reprobe = useMutation({
    mutationFn: (runtimeId: string) => api.reprobeRuntime(runtimeId),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['runtimes'] }) },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    registration.mutate()
  }

  return (
    <>
      <section className="data-panel">
        <div className="panel-heading">
          <div><h2>Installed runtimes</h2><p>Local llama.cpp executables available to this control plane.</p></div>
          <div className="panel-heading-actions"><button className="button secondary compact" onClick={() => setInstallOpen(true)} type="button"><Download size={15} /> Install official build</button><button className="button primary compact" onClick={() => setOpen(true)} type="button"><Plus size={15} /> Register runtime</button></div>
        </div>
        {runtimes.length ? <div className="record-list">{runtimes.map((runtime) => (
          <article className="record-row" key={runtime.id}>
            <span className="record-icon"><Cpu size={18} /></span>
            <div className="record-copy"><strong>{runtime.name}</strong><span>{runtime.executable_path}</span></div>
            <div className="record-meta"><small>Build</small><span>{runtime.build ?? 'Unknown'}{runtime.commit ? ` · ${runtime.commit.slice(0, 8)}` : ''}</span></div>
            <div className="record-meta"><small>Backend</small><span>{runtime.backend?.toUpperCase() ?? 'AUTO'}</span></div>
            <div className="row-actions"><span className={`state-pill ${runtime.usable ? 'ready' : 'error'}`}>{runtime.usable ? 'Ready' : 'Probe failed'}</span><button className="button secondary compact" disabled={reprobe.isPending} onClick={() => reprobe.mutate(runtime.id)} type="button">{reprobe.isPending ? <LoaderCircle className="spin" size={14} /> : 'Re-probe'}</button><button aria-expanded={expandedRuntime === runtime.id} aria-label={`Show diagnostics for ${runtime.name}`} className="button secondary compact" onClick={() => setExpandedRuntime(expandedRuntime === runtime.id ? undefined : runtime.id)} type="button">Details</button><button aria-label={`Remove runtime ${runtime.name}`} className="icon-button small danger-icon" onClick={() => setDeletingRuntime(runtime)} title="Remove runtime" type="button"><Trash2 size={16} /></button></div>
            {expandedRuntime === runtime.id && <div className="record-details"><span>Devices: {runtime.device_status === 'available' ? runtime.devices.join(', ') : runtime.device_status}</span><span>Options: {runtime.options.length}</span><span>Router: {runtime.router_compatible ? 'compatible' : 'incompatible'}</span>{runtime.diagnostics.length > 0 && <ul>{runtime.diagnostics.map((diagnostic) => <li key={diagnostic}>{diagnostic}</li>)}</ul>}{reprobe.error && <div className="form-error"><AlertCircle size={15} /> {reprobe.error.message}</div>}</div>}
          </article>
        ))}</div> : <div className="empty"><Cpu size={28} /><strong>No runtime registered</strong><span>Point Llama Control at an existing llama-server executable to begin.</span><button className="button primary" onClick={() => setOpen(true)} type="button"><Plus size={15} /> Register runtime</button></div>}
      </section>
      {deletingRuntime && <Dialog title="Remove runtime?" description="This unregisters the runtime only. Installed files are not deleted." onClose={() => setDeletingRuntime(undefined)}><div className="confirm-body"><Trash2 size={24} /><p><strong>{deletingRuntime.name}</strong> will be removed from the runtime registry. Profiles or an active router may prevent removal.</p>{removal.error && <div className="form-error"><AlertCircle size={15} /> {removal.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeletingRuntime(undefined)} type="button">Keep runtime</button><button className="button danger" disabled={removal.isPending} onClick={() => removal.mutate(deletingRuntime.id)} type="button">{removal.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Remove runtime</button></footer></Dialog>}
      {installOpen && <Dialog title="Install official llama.cpp build" description="Choose a published asset. The download is staged, verified, probed, and promoted only after validation." onClose={() => setInstallOpen(false)}>
        <form onSubmit={(event) => { event.preventDefault(); installation.mutate() }}>
          <div className="form-body">
            <div className="form-grid"><Field label="Release tag" hint="Use latest, a stable tag such as v0.4.1, or a build tag such as b11060."><input autoFocus value={releaseTag} onChange={(event) => setReleaseTag(event.target.value)} placeholder="latest" required /></Field><Field label="Backend"><select value={backend} onChange={(event) => { setBackend(event.target.value); setRelease(undefined); setAssetName('') }}><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="vulkan">Vulkan</option><option value="rocm">ROCm</option><option value="metal">Metal</option><option value="sycl">SYCL</option></select></Field></div>
            <button className="button secondary" disabled={discovery.isPending || !releaseTag.trim()} onClick={() => discovery.mutate()} type="button">{discovery.isPending ? <LoaderCircle className="spin" size={15} /> : <SearchIcon size={15} />} Discover published assets</button>
            {release && <Field label={`Published assets${release.stable_tag ? ` · ${release.stable_tag} → ${release.tag}` : ''}`} hint="CUDA companion libraries are included automatically when published with the selected runtime."><select value={assetName} onChange={(event) => setAssetName(event.target.value)} required><option value="">Select an asset</option>{release.assets.filter((asset) => isRuntimeArchive(asset.name)).map((asset) => { const group = release.groups.find((item) => item.primary === asset.name); return <option key={asset.name} value={asset.name}>{asset.name} · {formatAssetSize(asset.size)}{group?.companions.length ? ` · +${group.companions.length} companion` : ''}{asset.digest ? ' · SHA-256' : ''}</option> })}</select></Field>}
            {release && !release.assets.some((asset) => isRuntimeArchive(asset.name)) && <div className="form-error"><AlertCircle size={15} /> No compatible runtime archive was published for this release.</div>}
            {(discovery.error || installation.error) && <div className="form-error"><AlertCircle size={15} /> {(discovery.error || installation.error)?.message}</div>}
          </div>
          <footer className="dialog-actions"><button className="button secondary" onClick={() => setInstallOpen(false)} type="button">Cancel</button><button className="button primary" disabled={installation.isPending || !assetName} type="submit">{installation.isPending ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />} Download & install</button></footer>
        </form>
      </Dialog>}
      {open && <Dialog title="Register runtime" description="The executable is probed immediately for its build, devices, and supported options." onClose={() => setOpen(false)}>
        <form onSubmit={submit}>
          <div className="form-body">
            <Field label="Display name"><input autoFocus maxLength={200} onChange={(event) => setName(event.target.value)} placeholder="Local CPU" required value={name} /></Field>
            <Field label="llama-server executable" hint="Use the full path to llama-server.exe on Windows."><input onChange={(event) => setPath(event.target.value)} placeholder="E:\\llama.cpp\\llama-server.exe" required value={path} /></Field>
            <Field label="Backend"><select onChange={(event) => setBackend(event.target.value)} value={backend}><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="vulkan">Vulkan</option><option value="rocm">ROCm</option><option value="metal">Metal</option><option value="sycl">SYCL</option></select></Field>
            {registration.error && <div className="form-error"><AlertCircle size={15} /> {registration.error.message}</div>}
          </div>
          <footer className="dialog-actions"><button className="button secondary" onClick={() => setOpen(false)} type="button">Cancel</button><button className="button primary" disabled={registration.isPending || !name.trim() || !path.trim()} type="submit">{registration.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />} Probe & register</button></footer>
        </form>
      </Dialog>}
    </>
  )
}

function suggestedAlias(model: LibraryModel) {
  const repository = model.repo_id.split('/').at(-1)?.replace(/-gguf$/i, '') ?? 'model'
  return repository.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-|-$/g, '').slice(0, 64)
}

/** Catalog ids the legacy fallback form edits directly. */
const LEGACY_FORM_IDS = new Set(['ctx-size', 'n-gpu-layers', 'threads', 'batch-size', 'flash-attn', 'reasoning-preserve'])

function storedList(configuration: Record<string, unknown>): StoredArgument[] {
  return Array.isArray(configuration.arguments) ? configuration.arguments as StoredArgument[] : []
}

function storedValue(stored: StoredArgument[], id: string) {
  const item = stored.find((argument) => 'id' in argument && argument.id === id)
  return item?.value
}

type ProfileTab = 'basic' | 'arguments' | 'preview'

export function ProfilePanel({ profiles, runtimes, library, initialModel, onInitialModelConsumed, running }: {
  profiles: Profile[]
  runtimes: Runtime[]
  library: LibraryModel[]
  initialModel?: LibraryModel
  onInitialModelConsumed: () => void
  running: boolean
}) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<ProfileTab>('basic')
  const [alias, setAlias] = useState('')
  const [modelPath, setModelPath] = useState('')
  const [selectedLibraryModel, setSelectedLibraryModel] = useState<LibraryModel>()
  const [runtimeId, setRuntimeId] = useState(runtimes.find((item) => item.usable)?.id ?? '')
  const [entries, setEntries] = useState<EditorEntry[]>([])
  const [acknowledged, setAcknowledged] = useState<string[]>([])
  const [storedArgs, setStoredArgs] = useState<StoredArgument[]>([])
  const [pendingStored, setPendingStored] = useState<StoredArgument[]>()
  const [loadedRuntimeId, setLoadedRuntimeId] = useState<string>()
  // Legacy fallback form, shown only when the argument catalog cannot be loaded.
  const [contextSize, setContextSize] = useState('')
  const [gpuLayers, setGpuLayers] = useState('')
  const [threads, setThreads] = useState('')
  const [batchSize, setBatchSize] = useState('')
  const [flashAttention, setFlashAttention] = useState('')
  const [preserveReasoning, setPreserveReasoning] = useState(true)
  const [deleting, setDeleting] = useState<Profile>()
  const [cloning, setCloning] = useState<Profile>()
  const [cloneAlias, setCloneAlias] = useState('')
  const [editing, setEditing] = useState<Profile>()
  const [repairing, setRepairing] = useState<Profile>()
  const [validation, setValidation] = useState<ProfileValidation & { alias: string; preset: string }>()
  const [resetting, setResetting] = useState<Profile>()
  const [importError, setImportError] = useState('')
  const [importNotice, setImportNotice] = useState('')
  const [commandImportOpen, setCommandImportOpen] = useState(false)
  const [commandImport, setCommandImport] = useState('')
  const [commandImportAlias, setCommandImportAlias] = useState('')
  const [command, setCommand] = useState<{ alias: string; value: string }>()
  const [actionError, setActionError] = useState('')
  const [actionPending, setActionPending] = useState<string>()
  const queryClient = useQueryClient()
  const runtime = runtimes.find((item) => item.id === runtimeId)
  const supports = (option: string) => runtime?.options.includes(option) ?? false
  const aliasValid = /^[a-z0-9][a-z0-9._-]{0,63}$/.test(alias)
  const commandImportAliasValid = /^[a-z0-9][a-z0-9._-]{0,63}$/.test(commandImportAlias)
  const availableLibrary = selectedLibraryModel && !library.some((item) => item.download_id === selectedLibraryModel.download_id)
    ? [selectedLibraryModel, ...library]
    : library
  const catalogQuery = useQuery({
    queryKey: ['argument-catalog', runtimeId],
    queryFn: () => api.runtimeArgumentCatalog(runtimeId),
    enabled: open && Boolean(runtimeId),
    staleTime: 60_000,
    refetchInterval: false,
    retry: false,
  })
  const catalog = catalogQuery.data
  const fallback = catalogQuery.isError
  const issues = useMemo(
    () => (catalog ? clientIssues(catalog, entries, acknowledged, runtime?.options) : []),
    [catalog, entries, acknowledged, runtime?.options],
  )
  const blockers = issues.filter((issue) => issue.level === 'error')
  const runtimeSwitchFlags = useMemo(
    () => (catalog && editing && loadedRuntimeId && loadedRuntimeId !== runtimeId ? unadvertisedOnRuntime(catalog, entries, runtime?.options ?? []) : []),
    [catalog, editing, loadedRuntimeId, runtimeId, entries, runtime?.options],
  )

  useEffect(() => {
    if (!catalog || pendingStored === undefined) return
    setEntries(entriesFromStored(catalog, pendingStored))
    setPendingStored(undefined)
  }, [catalog, pendingStored])

  function resetEditor() {
    setEditing(undefined)
    setTab('basic')
    setAlias('')
    setModelPath('')
    setSelectedLibraryModel(undefined)
    setRuntimeId(runtimes.find((item) => item.usable)?.id ?? '')
    setEntries([])
    setAcknowledged([])
    setStoredArgs([])
    setPendingStored(undefined)
    setLoadedRuntimeId(undefined)
    setContextSize('')
    setGpuLayers('')
    setThreads('')
    setBatchSize('')
    setFlashAttention('')
    setPreserveReasoning(true)
  }

  function openCreate() {
    resetEditor()
    setOpen(true)
  }

  function closeEditor() {
    setOpen(false)
    resetEditor()
  }

  useEffect(() => {
    if (!runtimeId) {
      setRuntimeId(runtimes.find((item) => item.usable)?.id ?? '')
    }
  }, [runtimeId, runtimes])

  useEffect(() => {
    if (!initialModel) return
    setModelPath(initialModel.primary_path)
    setAlias(suggestedAlias(initialModel))
    setSelectedLibraryModel(initialModel)
    setOpen(true)
    onInitialModelConsumed()
  }, [initialModel, onInitialModelConsumed])

  function presentIds(): Set<string> {
    if (!catalog) return new Set(acknowledged)
    const { byFlag } = catalogIndex(catalog)
    return new Set(entries.flatMap((entry) => {
      if (entry.kind === 'catalog') return isSet(entry.draft) ? [entry.id] : []
      const definition = byFlag.get(entry.flag)
      return definition ? [definition.id] : []
    }))
  }

  function profilePayload(): ProfileCreate {
    const base = { alias, runtime_id: runtimeId, model_path: modelPath, enabled: editing ? editing.enabled : true }
    if (catalog && !fallback) {
      const present = presentIds()
      return { ...base, arguments: storedArguments(catalog, entries), acknowledged: acknowledged.filter((id) => present.has(id)) }
    }
    return {
      ...base,
      // Keep every stored argument the fallback form does not edit, so nothing is lost.
      arguments: storedArgs.filter((argument) => !('id' in argument && LEGACY_FORM_IDS.has(argument.id))),
      acknowledged,
      no_reasoning_preserve: !preserveReasoning,
      ctx_size: supports('ctx-size') ? optionalNumber(contextSize) : undefined,
      n_gpu_layers: supports('n-gpu-layers') ? optionalNumber(gpuLayers) : undefined,
      threads: supports('threads') ? optionalNumber(threads) : undefined,
      batch_size: supports('batch-size') ? optionalNumber(batchSize) : undefined,
      flash_attn: supports('flash-attn') && flashAttention ? flashAttention : undefined,
    }
  }

  const payload = profilePayload()
  const payloadKey = JSON.stringify(payload)
  const preview = useMutation({ mutationFn: (value: ProfileCreate) => api.previewProfile(value) })
  const runPreview = preview.mutate
  useEffect(() => {
    if (!open || tab !== 'preview' || !runtimeId) return
    const timer = window.setTimeout(() => runPreview(JSON.parse(payloadKey) as ProfileCreate), 300)
    return () => window.clearTimeout(timer)
  }, [open, tab, runtimeId, payloadKey, runPreview])

  const creation = useMutation({
    mutationFn: () => api.createProfile(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
      closeEditor()
    },
  })
  const update = useMutation({
    mutationFn: () => api.updateProfile(editing!.id, payload),
    onSuccess: async () => {
      closeEditor()
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })

  function editProfile(profile: Profile) {
    resetEditor()
    setEditing(profile)
    setAlias(profile.alias)
    setModelPath(profile.model_path)
    setRuntimeId(profile.runtime_id)
    setLoadedRuntimeId(profile.runtime_id)
    const configuration = profile.configuration
    const stored = storedList(configuration)
    setStoredArgs(stored)
    setPendingStored(stored)
    setAcknowledged(Array.isArray(configuration.acknowledged) ? configuration.acknowledged.filter((item): item is string => typeof item === 'string') : [])
    const text = (id: string) => {
      const value = storedValue(stored, id)
      return value === undefined ? '' : String(value)
    }
    setContextSize(text('ctx-size'))
    setGpuLayers(text('n-gpu-layers'))
    setThreads(text('threads'))
    setBatchSize(text('batch-size'))
    setFlashAttention(text('flash-attn'))
    setPreserveReasoning(storedValue(stored, 'reasoning-preserve') !== false)
    setSelectedLibraryModel(library.find((item) => item.primary_path === profile.model_path))
    setOpen(true)
  }
  async function exportProfile(profile: Profile) {
    setActionError('')
    setActionPending(`export:${profile.id}`)
    try {
      const blob = await api.exportProfile(profile.id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${profile.alias}.json`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Profile export failed.')
    } finally {
      setActionPending(undefined)
    }
  }
  async function showCommand(profile: Profile) {
    setActionError('')
    setActionPending(`command:${profile.id}`)
    try {
      setCommand({ alias: profile.alias, value: await api.profileCommand(profile.id) })
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Command generation failed.')
    } finally {
      setActionPending(undefined)
    }
  }
  const deletion = useMutation({
    mutationFn: (profileId: string) => api.deleteProfile(profileId),
    onSuccess: async () => {
      setDeleting(undefined)
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
  const clone = useMutation({
    mutationFn: () => api.cloneProfile(cloning!.id, cloneAlias),
    onSuccess: async () => {
      setCloning(undefined)
      setCloneAlias('')
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
  const redownload = useMutation({
    mutationFn: (downloadId: string) => api.redownload(downloadId),
    onSuccess: async () => {
      setRepairing(undefined)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['profiles'] }),
        queryClient.invalidateQueries({ queryKey: ['downloads'] }),
      ])
    },
  })
  const validate = useMutation({
    mutationFn: (profile: Profile) => api.validateProfile(profile.id),
    onSuccess: (result, profile) => setValidation({ alias: profile.alias, ...result }),
  })
  const reset = useMutation({
    mutationFn: () => api.resetProfile(resetting!.id),
    onSuccess: async () => {
      setResetting(undefined)
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
  const importing = useMutation({
    mutationFn: (document: Record<string, unknown>) => api.importProfile(document),
    onSuccess: async () => {
      setImportError('')
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (error: Error) => setImportError(error.message),
  })
  const commandImportMutation = useMutation({
    mutationFn: () => api.importProfileCommand(commandImport, commandImportAlias, runtimeId),
    onSuccess: async (profile) => {
      setCommandImportOpen(false)
      setCommandImport('')
      setCommandImportAlias('')
      setImportError('')
      const ignored = profile.ignored_arguments ?? []
      const warnings = profile.warnings ?? []
      setImportNotice([
        `Imported ${profile.alias} as a disabled profile.`,
        ignored.length ? `Skipped options managed by LlamaWebUI: ${ignored.join(', ')}.` : '',
        warnings.length ? `Warnings: ${warnings.join(' ')}` : '',
      ].filter(Boolean).join(' '))
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (error: Error) => setImportError(error.message),
  })

  async function importProfile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const document = JSON.parse(await file.text()) as Record<string, unknown>
      importing.mutate(document)
    } catch {
      setImportError('The selected file is not valid JSON.')
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (editing) update.mutate()
    else creation.mutate()
  }

  const saveError = creation.error ?? update.error
  const catalogBlocked = Boolean(catalog) && !fallback && blockers.length > 0

  return (
    <>
      <section className="data-panel">
        <div className="panel-heading">
          <div><h2>Model profiles</h2><p>Named models and launch settings published to the router.</p></div>
          <div className="row-actions"><label className="button secondary compact" htmlFor="profile-import"><Upload size={15} /> Import profile</label><input accept="application/json,.json" id="profile-import" onChange={importProfile} style={{ display: 'none' }} type="file" /><button className="button secondary compact" disabled={!runtimes.length} onClick={() => setCommandImportOpen(true)} type="button"><TerminalSquare size={15} /> Import command</button><button className="button primary compact" disabled={!runtimes.length} onClick={openCreate} type="button"><Plus size={15} /> Create profile</button></div>
        </div>
        {(importError || actionError) && <div className="form-error" role="alert"><AlertCircle size={15} /> {importError || actionError}</div>}
        {importNotice && <div className="inline-notice" role="status"><Check size={15} /> <span>{importNotice}</span><button aria-label="Dismiss import notice" className="icon-button small" onClick={() => setImportNotice('')} type="button"><X size={14} /></button></div>}
        {profiles.length ? <div className="record-list">{profiles.map((profile) => (
          <article className="record-row" key={profile.id}>
            <span className="record-icon"><FileCog size={18} /></span>
            <div className="record-copy"><button className="record-link" onClick={() => editProfile(profile)} type="button"><strong>{profile.alias}</strong></button><span>{profile.model_path}</span></div>
            <div className="record-meta wide"><small>Runtime</small><span>{runtimes.find((item) => item.id === profile.runtime_id)?.name ?? 'Missing runtime'}</span></div>
            {profile.validation_state === 'broken' ? <button className="state-pill error" disabled={running || !profile.source_download} onClick={() => setRepairing(profile)} title={profile.source_download ? 'Repair missing model' : 'Model file is missing'} type="button">Broken</button> : <span className={`state-pill ${profile.validation_state === 'unverified' ? 'warning' : profile.enabled ? 'ready' : ''}`} title={profile.validation_state === 'unverified' ? 'File size verified; checksum unavailable for this legacy download' : undefined}>{profile.validation_state === 'unverified' ? 'Unverified' : profile.enabled ? 'Enabled' : 'Disabled'}</span>}
            <div className="row-actions"><button className="button row-button" disabled={running || validate.isPending} onClick={() => validate.mutate(profile)} type="button">Validate</button><button className="button row-button" disabled={running} onClick={() => editProfile(profile)} type="button">Edit</button><button className="button row-button" disabled={running} onClick={() => setResetting(profile)} type="button">Reset</button><button className="button row-button" disabled={running || actionPending !== undefined} onClick={() => void exportProfile(profile)} type="button">{actionPending === `export:${profile.id}` ? <LoaderCircle className="spin" size={13} /> : <Download size={13} />} Export</button><button className="button row-button" disabled={running || actionPending !== undefined} onClick={() => void showCommand(profile)} type="button">{actionPending === `command:${profile.id}` ? <LoaderCircle className="spin" size={13} /> : <TerminalSquare size={13} />} Command</button><button className="button row-button" disabled={running} onClick={() => { setCloning(profile); setCloneAlias(`${profile.alias}-copy`) }} type="button"><Plus size={13} /> Clone</button><button aria-label={`Delete profile ${profile.alias}`} className="icon-button small danger-icon" disabled={running} onClick={() => setDeleting(profile)} title="Delete profile" type="button"><Trash2 size={16} /></button></div>
          </article>
        ))}</div> : <div className="empty"><FileCog size={28} /><strong>No profiles configured</strong><span>{runtimes.length ? 'Create a profile for a local GGUF model.' : 'Register a runtime before creating a model profile.'}</span>{runtimes.length > 0 && <button className="button primary" onClick={openCreate} type="button"><Plus size={15} /> Create profile</button>}</div>}
      </section>
      {open && <Dialog title={editing ? 'Edit model profile' : 'Create model profile'} description="Identity, catalog-driven llama.cpp arguments, and a launch preview." onClose={closeEditor} wide>
        <form onSubmit={submit}>
          <div className="segmented" role="tablist" aria-label="Profile settings">
            <button aria-selected={tab === 'basic'} onClick={() => setTab('basic')} role="tab" type="button">Basic</button>
            <button aria-selected={tab === 'arguments'} onClick={() => setTab('arguments')} role="tab" type="button">Arguments{entries.length ? ` (${entries.length})` : ''}</button>
            <button aria-selected={tab === 'preview'} onClick={() => setTab('preview')} role="tab" type="button">Launch preview</button>
          </div>
          <div className="form-body">
            {tab === 'basic' && <>
              <div className="form-grid">
                <Field label="API model alias" hint="Lowercase letters, numbers, dots, underscores, or hyphens."><input autoFocus aria-invalid={Boolean(alias) && !aliasValid} maxLength={64} onChange={(event) => setAlias(event.target.value.toLowerCase().replace(/\s+/g, '-'))} placeholder="qwen3.8-flash" required value={alias} /></Field>
                <Field label="Runtime" hint={editing && loadedRuntimeId && runtimeId !== loadedRuntimeId ? 'Switching runtimes keeps every setting; unsupported ones are flagged on the Arguments tab.' : undefined}><select onChange={(event) => setRuntimeId(event.target.value)} required value={runtimeId}><option value="">Select runtime</option>{runtimes.filter((item) => item.usable).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.build ?? 'unknown'}</option>)}</select></Field>
              </div>
              <Field label="Primary GGUF file" hint="For sharded models, select the first 00001-of-000NN file."><input onChange={(event) => setModelPath(event.target.value)} placeholder="E:\\models\\model-00001-of-00003.gguf" required value={modelPath} /></Field>
              {availableLibrary.length > 0 && <Field label="Downloaded model" hint="Only completed downloads whose files and sizes still validate are listed."><select onChange={(event) => {
                const model = availableLibrary.find((item) => item.download_id === event.target.value)
                if (!model) return
                setModelPath(model.primary_path)
                setSelectedLibraryModel(model)
                if (!alias) setAlias(suggestedAlias(model))
              }} value={selectedLibraryModel?.download_id ?? ''}><option value="">Select a validated download</option>{availableLibrary.map((model) => <option disabled={profiles.some((profile) => profile.model_path === model.primary_path)} key={model.download_id} value={model.download_id}>{model.repo_id} · {model.group_key}</option>)}</select></Field>}
            </>}
            {tab === 'arguments' && (!runtimeId ? <div className="capability-note"><Gauge size={16} /><span>Select a runtime on the Basic tab to load its arguments.</span></div>
              : fallback ? <>
                <div className="capability-note warning" role="status"><AlertCircle size={16} /><span>The argument catalog could not be loaded ({catalogQuery.error?.message}). Showing the basic editor; other saved arguments are kept unchanged.</span></div>
                <div className="form-grid advanced-grid">
                  <Field label="Context size"><input disabled={!supports('ctx-size')} min="0" onChange={(event) => setContextSize(event.target.value)} placeholder="262144" type="number" value={contextSize} /></Field>
                  <Field label="GPU layers"><input disabled={!supports('n-gpu-layers')} min="0" onChange={(event) => setGpuLayers(event.target.value)} placeholder="Auto" type="number" value={gpuLayers} /></Field>
                  <Field label="CPU threads"><input disabled={!supports('threads')} min="-1" onChange={(event) => setThreads(event.target.value)} placeholder="Auto" type="number" value={threads} /></Field>
                  <Field label="Batch size"><input disabled={!supports('batch-size')} min="1" onChange={(event) => setBatchSize(event.target.value)} placeholder="Auto" type="number" value={batchSize} /></Field>
                  <Field label="Flash attention"><select disabled={!supports('flash-attn')} onChange={(event) => setFlashAttention(event.target.value)} value={flashAttention}><option value="">Runtime default</option><option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option></select></Field>
                </div>
                <label className="toggle-row"><span><strong>Preserve reasoning</strong><small>Keep reasoning content when the model supports it.</small></span><input checked={preserveReasoning} onChange={(event) => setPreserveReasoning(event.target.checked)} type="checkbox" /></label>
              </>
              : !catalog || pendingStored !== undefined ? <div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading the argument catalog for {runtime?.name ?? 'the runtime'}…</span></div>
              : <ProfileArgumentEditor acknowledged={acknowledged} catalog={catalog} entries={entries} issues={issues} onAcknowledgedChange={setAcknowledged} onEntriesChange={setEntries} runtimeName={runtime?.name ?? 'This runtime'} runtimeSwitchFlags={runtimeSwitchFlags} />)}
            {tab === 'preview' && (runtimeId
              ? <LaunchPreview clientIssues={issues} error={preview.error?.message} onRefresh={() => preview.mutate(payload)} pending={preview.isPending} preview={preview.data} />
              : <div className="capability-note"><Gauge size={16} /><span>Select a runtime to preview the launch arguments.</span></div>)}
            {saveError && <div className="form-error" role="alert"><AlertCircle size={15} /> {saveError.message}</div>}
          </div>
          <footer className="dialog-actions">
            {catalogBlocked && <button className="blocker-link" onClick={() => setTab('preview')} type="button"><AlertCircle size={14} /> {blockers.length} blocker{blockers.length === 1 ? '' : 's'}</button>}
            <button className="button secondary" onClick={closeEditor} type="button">Cancel</button>
            <button className="button primary" disabled={creation.isPending || update.isPending || !aliasValid || !runtimeId || !modelPath.trim() || catalogBlocked} type="submit">{creation.isPending || update.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />} {editing ? 'Save profile' : 'Create profile'}</button>
          </footer>
        </form>
      </Dialog>}
      {deleting && <Dialog title="Delete model profile?" description="This removes only the profile. Downloaded model files are not affected." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p>Profile <strong>{deleting.alias}</strong> will be permanently removed.</p>{deletion.error && <div className="form-error"><AlertCircle size={15} /> {deletion.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep profile</button><button className="button danger" disabled={deletion.isPending} onClick={() => deletion.mutate(deleting.id)} type="button">{deletion.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete profile</button></footer></Dialog>}
      {cloning && <Dialog title="Clone model profile" description="Create a disabled copy that can be edited before enabling." onClose={() => setCloning(undefined)}><form onSubmit={(event) => { event.preventDefault(); clone.mutate() }}><div className="form-body"><Field label="New API model alias"><input autoFocus value={cloneAlias} onChange={(event) => setCloneAlias(event.target.value.toLowerCase().replace(/\s+/g, '-'))} required /></Field>{clone.error && <div className="form-error"><AlertCircle size={15} /> {clone.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setCloning(undefined)} type="button">Cancel</button><button className="button primary" disabled={clone.isPending || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(cloneAlias)} type="submit">{clone.isPending ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />} Clone profile</button></footer></form></Dialog>}
      {validation && <Dialog title={`Validate ${validation.alias}`} description="No files or settings are changed by validation." onClose={() => setValidation(undefined)}><div className="confirm-body">{validation.valid ? <><Check size={24} /><p>This profile is valid for its selected runtime and model files.</p></> : <><AlertCircle size={24} /><div><p>Validation found {validation.errors.length} blocking issue(s):</p><ul>{validation.errors.map((error) => <li key={error}>{error}</li>)}</ul></div></>}{validation.warnings.length > 0 && <div className="validation-warnings"><p>{validation.warnings.length} warning(s) that do not block launch:</p><ul>{validation.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}{validation.valid && <details><summary>Generated preset</summary><pre className="mono">{validation.preset}</pre></details>}</div><footer className="dialog-actions"><button className="button primary" onClick={() => setValidation(undefined)} type="button">Close</button></footer></Dialog>}
      {command && <Dialog title={`Command for ${command.alias}`} description="Readable export only; this command is never executed by the control plane. Sensitive values are masked." onClose={() => setCommand(undefined)}><div className="confirm-body"><TerminalSquare size={24} /><pre className="mono">{command.value}</pre></div><footer className="dialog-actions"><button className="button primary" onClick={() => setCommand(undefined)} type="button">Close</button></footer></Dialog>}
      {commandImportOpen && <Dialog title="Import command" description="Tokenize a llama-server command without executing it. Catalogued flags become typed settings; others are kept as custom parameters." onClose={() => setCommandImportOpen(false)}><div className="form-body"><Field label="Profile alias"><input autoFocus value={commandImportAlias} onChange={(event) => setCommandImportAlias(event.target.value.toLowerCase().replace(/\s+/g, '-'))} /></Field><Field label="Runtime"><select value={runtimeId} onChange={(event) => setRuntimeId(event.target.value)}><option value="">Select runtime</option>{runtimes.filter((item) => item.usable).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field><Field label="llama-server command"><textarea rows={7} value={commandImport} onChange={(event) => setCommandImport(event.target.value)} /></Field>{commandImportMutation.error && <div className="form-error"><AlertCircle size={15} /> {commandImportMutation.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setCommandImportOpen(false)} type="button">Cancel</button><button className="button primary" disabled={commandImportMutation.isPending || !commandImportAliasValid || !runtimeId || !/(^|\s)(-m|--model)(\s|=)/.test(commandImport)} onClick={() => commandImportMutation.mutate()} type="button">Import command</button></footer></Dialog>}
      {resetting && <Dialog title="Reset profile settings?" description="This removes every launch argument and custom parameter, retaining the alias and model path." onClose={() => setResetting(undefined)}><div className="confirm-body"><AlertCircle size={24} /><p>Reset <strong>{resetting.alias}</strong> to the selected runtime defaults?</p>{reset.error && <div className="form-error"><AlertCircle size={15} /> {reset.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setResetting(undefined)} type="button">Cancel</button><button className="button danger" disabled={reset.isPending} onClick={() => reset.mutate()} type="button">{reset.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />} Reset settings</button></footer></Dialog>}
      {repairing?.source_download && <Dialog title="Re-download missing model?" description="The exact pinned model artifact will be downloaded again and repair this profile." onClose={() => setRepairing(undefined)}><div className="confirm-body"><ShieldAlert size={24} /><p><strong>{repairing.source_download.repo_id}</strong> · <span className="mono">{repairing.source_download.group_key}</span> is missing for profile <strong>{repairing.alias}</strong>. Re-download revision <span className="mono">{repairing.source_download.revision.slice(0, 9)}</span>?</p>{redownload.error && <div className="form-error"><AlertCircle size={15} /> {redownload.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setRepairing(undefined)} type="button">Not now</button><button className="button primary" disabled={redownload.isPending} onClick={() => redownload.mutate(repairing.source_download!.id)} type="button">{redownload.isPending ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />} Re-download</button></footer></Dialog>}
    </>
  )
}
