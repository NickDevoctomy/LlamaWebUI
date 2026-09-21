import { useMutation, useQueryClient } from '@tanstack/react-query'
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
import { type ChangeEvent, type FormEvent, useEffect, useState } from 'react'
import { api, type LibraryModel, type Profile, type ProfileCreate, type Runtime, type RuntimeRelease } from './api'

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

export function Dialog({ title, description, onClose, children }: {
  title: string
  description: string
  onClose: () => void
  children: React.ReactNode
}) {
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section aria-describedby="dialog-description" aria-labelledby="dialog-title" aria-modal="true" className="dialog" role="dialog">
        <header className="dialog-header">
          <div><p className="eyebrow">Configuration</p><h2 id="dialog-title">{title}</h2><p id="dialog-description">{description}</p></div>
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
            <div className="record-meta"><small>Build</small><span>{runtime.build ?? 'Unknown'}</span></div>
            <div className="record-meta"><small>Backend</small><span>{runtime.backend?.toUpperCase() ?? 'AUTO'}</span></div>
            <div className="row-actions"><span className={`state-pill ${runtime.usable ? 'ready' : 'error'}`}>{runtime.usable ? 'Ready' : 'Probe failed'}</span><button aria-label={`Remove runtime ${runtime.name}`} className="icon-button small danger-icon" onClick={() => setDeletingRuntime(runtime)} title="Remove runtime" type="button"><Trash2 size={16} /></button></div>
          </article>
        ))}</div> : <div className="empty"><Cpu size={28} /><strong>No runtime registered</strong><span>Point Llama Control at an existing llama-server executable to begin.</span><button className="button primary" onClick={() => setOpen(true)} type="button"><Plus size={15} /> Register runtime</button></div>}
      </section>
      {deletingRuntime && <Dialog title="Remove runtime?" description="This unregisters the runtime only. Installed files are not deleted." onClose={() => setDeletingRuntime(undefined)}><div className="confirm-body"><Trash2 size={24} /><p><strong>{deletingRuntime.name}</strong> will be removed from the runtime registry. Profiles or an active router may prevent removal.</p>{removal.error && <div className="form-error"><AlertCircle size={15} /> {removal.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeletingRuntime(undefined)} type="button">Keep runtime</button><button className="button danger" disabled={removal.isPending} onClick={() => removal.mutate(deletingRuntime.id)} type="button">{removal.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Remove runtime</button></footer></Dialog>}
      {installOpen && <Dialog title="Install official llama.cpp build" description="Choose a published asset. The download is staged, verified, probed, and promoted only after validation." onClose={() => setInstallOpen(false)}>
        <form onSubmit={(event) => { event.preventDefault(); installation.mutate() }}>
          <div className="form-body">
            <div className="form-grid"><Field label="Release tag" hint="Use a stable tag such as v0.4.1 or a build tag such as b11060."><input autoFocus value={releaseTag} onChange={(event) => setReleaseTag(event.target.value)} placeholder="v0.4.1" required /></Field><Field label="Backend"><select value={backend} onChange={(event) => { setBackend(event.target.value); setRelease(undefined); setAssetName('') }}><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="vulkan">Vulkan</option><option value="metal">Metal</option><option value="sycl">SYCL</option></select></Field></div>
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
            <Field label="Backend"><select onChange={(event) => setBackend(event.target.value)} value={backend}><option value="cpu">CPU</option><option value="cuda">CUDA</option><option value="vulkan">Vulkan</option><option value="metal">Metal</option><option value="sycl">SYCL</option></select></Field>
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

export function ProfilePanel({ profiles, runtimes, library, initialModel, onInitialModelConsumed, running }: {
  profiles: Profile[]
  runtimes: Runtime[]
  library: LibraryModel[]
  initialModel?: LibraryModel
  onInitialModelConsumed: () => void
  running: boolean
}) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'basic' | 'advanced'>('basic')
  const [alias, setAlias] = useState('')
  const [modelPath, setModelPath] = useState('')
  const [selectedLibraryModel, setSelectedLibraryModel] = useState<LibraryModel>()
  const [runtimeId, setRuntimeId] = useState(runtimes.find((item) => item.usable)?.id ?? '')
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
  const [validation, setValidation] = useState<{ alias: string; valid: boolean; errors: string[]; preset: string }>()
  const [resetting, setResetting] = useState<Profile>()
  const [importError, setImportError] = useState('')
  const [command, setCommand] = useState<{ alias: string; value: string }>()
  const queryClient = useQueryClient()
  const runtime = runtimes.find((item) => item.id === runtimeId)
  const supports = (option: string) => runtime?.options.includes(option) ?? false
  const aliasValid = /^[a-z0-9][a-z0-9._-]{0,63}$/.test(alias)
  const availableLibrary = selectedLibraryModel && !library.some((item) => item.download_id === selectedLibraryModel.download_id)
    ? [selectedLibraryModel, ...library]
    : library

  function resetEditor() {
    setEditing(undefined)
    setTab('basic')
    setAlias('')
    setModelPath('')
    setSelectedLibraryModel(undefined)
    setRuntimeId(runtimes.find((item) => item.usable)?.id ?? '')
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

  const creation = useMutation({
    mutationFn: () => {
      const profile: ProfileCreate = {
        alias,
        runtime_id: runtimeId,
        model_path: modelPath,
        enabled: true,
        no_reasoning_preserve: !preserveReasoning,
        ctx_size: supports('ctx-size') ? optionalNumber(contextSize) : undefined,
        n_gpu_layers: supports('n-gpu-layers') ? optionalNumber(gpuLayers) : undefined,
        threads: supports('threads') ? optionalNumber(threads) : undefined,
        batch_size: supports('batch-size') ? optionalNumber(batchSize) : undefined,
        flash_attn: supports('flash-attn') && flashAttention ? flashAttention : undefined,
      }
      return api.createProfile(profile)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
      setOpen(false)
      resetEditor()
    },
  })
  const update = useMutation({
    mutationFn: () => api.updateProfile(editing!.id, {
      ...(editing!.configuration as Partial<ProfileCreate>),
      alias,
      runtime_id: runtimeId,
      model_path: modelPath,
      enabled: editing!.enabled,
      no_reasoning_preserve: !preserveReasoning,
      ctx_size: supports('ctx-size') ? optionalNumber(contextSize) : undefined,
      n_gpu_layers: supports('n-gpu-layers') ? optionalNumber(gpuLayers) : undefined,
      threads: supports('threads') ? optionalNumber(threads) : undefined,
      batch_size: supports('batch-size') ? optionalNumber(batchSize) : undefined,
      flash_attn: supports('flash-attn') && flashAttention ? flashAttention : undefined,
    }),
    onSuccess: async () => {
      setEditing(undefined)
      setOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })

  function editProfile(profile: Profile) {
    setEditing(profile)
    setAlias(profile.alias)
    setModelPath(profile.model_path)
    setRuntimeId(profile.runtime_id)
    const configuration = profile.configuration
    setContextSize(configuration.ctx_size == null ? '' : String(configuration.ctx_size))
    setGpuLayers(configuration.n_gpu_layers == null ? '' : String(configuration.n_gpu_layers))
    setThreads(configuration.threads == null ? '' : String(configuration.threads))
    setBatchSize(configuration.batch_size == null ? '' : String(configuration.batch_size))
    setFlashAttention(typeof configuration.flash_attn === 'string' ? configuration.flash_attn : '')
    setPreserveReasoning(configuration.no_reasoning_preserve !== true)
    setSelectedLibraryModel(library.find((item) => item.primary_path === profile.model_path))
    setTab('basic')
    setOpen(true)
  }
  async function exportProfile(profile: Profile) {
    const blob = await api.exportProfile(profile.id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${profile.alias}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }
  async function showCommand(profile: Profile) {
    setCommand({ alias: profile.alias, value: await api.profileCommand(profile.id) })
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

  return (
    <>
      <section className="data-panel">
        <div className="panel-heading">
          <div><h2>Model profiles</h2><p>Named models and launch settings published to the router.</p></div>
          <div className="row-actions"><label className="button secondary compact" htmlFor="profile-import"><Upload size={15} /> Import profile</label><input accept="application/json,.json" id="profile-import" onChange={importProfile} style={{ display: 'none' }} type="file" /><button className="button primary compact" disabled={!runtimes.length} onClick={openCreate} type="button"><Plus size={15} /> Create profile</button></div>
        </div>
        {importError && <div className="form-error"><AlertCircle size={15} /> {importError}</div>}
        {profiles.length ? <div className="record-list">{profiles.map((profile) => (
          <article className="record-row" key={profile.id}>
            <span className="record-icon"><FileCog size={18} /></span>
            <div className="record-copy"><button className="record-link" onClick={() => editProfile(profile)} type="button"><strong>{profile.alias}</strong></button><span>{profile.model_path}</span></div>
            <div className="record-meta wide"><small>Runtime</small><span>{runtimes.find((item) => item.id === profile.runtime_id)?.name ?? 'Missing runtime'}</span></div>
            {profile.validation_state === 'broken' ? <button className="state-pill error" disabled={running || !profile.source_download} onClick={() => setRepairing(profile)} title={profile.source_download ? 'Repair missing model' : 'Model file is missing'} type="button">Broken</button> : <span className={`state-pill ${profile.enabled ? 'ready' : ''}`}>{profile.enabled ? 'Enabled' : 'Disabled'}</span>}
            <div className="row-actions"><button className="button row-button" disabled={running || validate.isPending} onClick={() => validate.mutate(profile)} type="button">Validate</button><button className="button row-button" disabled={running} onClick={() => editProfile(profile)} type="button">Edit</button><button className="button row-button" disabled={running} onClick={() => setResetting(profile)} type="button">Reset</button><button className="button row-button" disabled={running} onClick={() => void exportProfile(profile)} type="button"><Download size={13} /> Export</button><button className="button row-button" disabled={running} onClick={() => void showCommand(profile)} type="button"><TerminalSquare size={13} /> Command</button><button className="button row-button" disabled={running} onClick={() => { setCloning(profile); setCloneAlias(`${profile.alias}-copy`) }} type="button"><Plus size={13} /> Clone</button><button aria-label={`Delete profile ${profile.alias}`} className="icon-button small danger-icon" disabled={running} onClick={() => setDeleting(profile)} title="Delete profile" type="button"><Trash2 size={16} /></button></div>
          </article>
        ))}</div> : <div className="empty"><FileCog size={28} /><strong>No profiles configured</strong><span>{runtimes.length ? 'Create a profile for a local GGUF model.' : 'Register a runtime before creating a model profile.'}</span>{runtimes.length > 0 && <button className="button primary" onClick={openCreate} type="button"><Plus size={15} /> Create profile</button>}</div>}
      </section>
      {open && <Dialog title={editing ? 'Edit model profile' : 'Create model profile'} description="Basic identity and capability-aware llama.cpp launch settings." onClose={() => { setOpen(false); resetEditor() }}>
        <form onSubmit={submit}>
          <div className="segmented" role="tablist" aria-label="Profile settings"><button aria-selected={tab === 'basic'} onClick={() => setTab('basic')} role="tab" type="button">Basic</button><button aria-selected={tab === 'advanced'} onClick={() => setTab('advanced')} role="tab" type="button">Advanced</button></div>
          <div className="form-body">
            {tab === 'basic' ? <>
              <div className="form-grid">
                <Field label="API model alias" hint="Lowercase letters, numbers, dots, underscores, or hyphens."><input autoFocus aria-invalid={Boolean(alias) && !aliasValid} maxLength={64} onChange={(event) => setAlias(event.target.value.toLowerCase().replace(/\s+/g, '-'))} placeholder="qwen3.8-flash" required value={alias} /></Field>
                <Field label="Runtime"><select onChange={(event) => setRuntimeId(event.target.value)} required value={runtimeId}><option value="">Select runtime</option>{runtimes.filter((item) => item.usable).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.build ?? 'unknown'}</option>)}</select></Field>
              </div>
              <Field label="Primary GGUF file" hint="For sharded models, select the first 00001-of-000NN file."><input onChange={(event) => setModelPath(event.target.value)} placeholder="E:\\models\\model-00001-of-00003.gguf" required value={modelPath} /></Field>
              {availableLibrary.length > 0 && <Field label="Downloaded model" hint="Only completed downloads whose files and sizes still validate are listed."><select onChange={(event) => {
                const model = availableLibrary.find((item) => item.download_id === event.target.value)
                if (!model) return
                setModelPath(model.primary_path)
                setSelectedLibraryModel(model)
                if (!alias) setAlias(suggestedAlias(model))
              }} value={selectedLibraryModel?.download_id ?? ''}><option value="">Select a validated download</option>{availableLibrary.map((model) => <option disabled={profiles.some((profile) => profile.model_path === model.primary_path)} key={model.download_id} value={model.download_id}>{model.repo_id} · {model.group_key}</option>)}</select></Field>}
              <label className="toggle-row"><span><strong>Preserve reasoning</strong><small>Keep reasoning content when the model supports it.</small></span><input checked={preserveReasoning} onChange={(event) => setPreserveReasoning(event.target.checked)} type="checkbox" /></label>
            </> : <>
              <div className="capability-note"><Gauge size={16} /><span>Fields unavailable in <strong>{runtime?.name ?? 'the selected runtime'}</strong> are disabled.</span></div>
              <div className="form-grid advanced-grid">
                <Field label="Context size"><input disabled={!supports('ctx-size')} min="1" onChange={(event) => setContextSize(event.target.value)} placeholder="262144" type="number" value={contextSize} /></Field>
                <Field label="GPU layers"><input disabled={!supports('n-gpu-layers')} min="0" onChange={(event) => setGpuLayers(event.target.value)} placeholder="Auto" type="number" value={gpuLayers} /></Field>
                <Field label="CPU threads"><input disabled={!supports('threads')} min="1" onChange={(event) => setThreads(event.target.value)} placeholder="Auto" type="number" value={threads} /></Field>
                <Field label="Batch size"><input disabled={!supports('batch-size')} min="1" onChange={(event) => setBatchSize(event.target.value)} placeholder="Auto" type="number" value={batchSize} /></Field>
                <Field label="Flash attention"><select disabled={!supports('flash-attn')} onChange={(event) => setFlashAttention(event.target.value)} value={flashAttention}><option value="">Runtime default</option><option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option></select></Field>
              </div>
            </>}
            {creation.error && <div className="form-error"><AlertCircle size={15} /> {creation.error.message}</div>}
          </div>
          <footer className="dialog-actions"><button className="button secondary" onClick={() => { setOpen(false); resetEditor() }} type="button">Cancel</button><button className="button primary" disabled={creation.isPending || update.isPending || !aliasValid || !runtimeId || !modelPath.trim()} type="submit">{creation.isPending || update.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />} {editing ? 'Save profile' : 'Create profile'}</button></footer>
        </form>
      </Dialog>}
      {deleting && <Dialog title="Delete model profile?" description="This removes only the profile. Downloaded model files are not affected." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p>Profile <strong>{deleting.alias}</strong> will be permanently removed.</p>{deletion.error && <div className="form-error"><AlertCircle size={15} /> {deletion.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep profile</button><button className="button danger" disabled={deletion.isPending} onClick={() => deletion.mutate(deleting.id)} type="button">{deletion.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete profile</button></footer></Dialog>}
      {cloning && <Dialog title="Clone model profile" description="Create a disabled copy that can be edited before enabling." onClose={() => setCloning(undefined)}><form onSubmit={(event) => { event.preventDefault(); clone.mutate() }}><div className="form-body"><Field label="New API model alias"><input autoFocus value={cloneAlias} onChange={(event) => setCloneAlias(event.target.value.toLowerCase().replace(/\s+/g, '-'))} required /></Field>{clone.error && <div className="form-error"><AlertCircle size={15} /> {clone.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setCloning(undefined)} type="button">Cancel</button><button className="button primary" disabled={clone.isPending || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(cloneAlias)} type="submit">{clone.isPending ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />} Clone profile</button></footer></form></Dialog>}
      {validation && <Dialog title={`Validate ${validation.alias}`} description="No files or settings are changed by validation." onClose={() => setValidation(undefined)}><div className="confirm-body">{validation.valid ? <><Check size={24} /><p>This profile is valid for its selected runtime and model files.</p></> : <><AlertCircle size={24} /><div><p>Validation found {validation.errors.length} issue(s):</p><ul>{validation.errors.map((error) => <li key={error}>{error}</li>)}</ul></div></>}{validation.valid && <details><summary>Generated preset</summary><pre className="mono">{validation.preset}</pre></details>}</div><footer className="dialog-actions"><button className="button primary" onClick={() => setValidation(undefined)} type="button">Close</button></footer></Dialog>}
      {command && <Dialog title={`Command for ${command.alias}`} description="Readable export only; this command is never executed by the control plane." onClose={() => setCommand(undefined)}><div className="confirm-body"><TerminalSquare size={24} /><pre className="mono">{command.value}</pre></div><footer className="dialog-actions"><button className="button primary" onClick={() => setCommand(undefined)} type="button">Close</button></footer></Dialog>}
      {resetting && <Dialog title="Reset profile settings?" description="This removes advanced and typed launch overrides, retaining the alias and model path." onClose={() => setResetting(undefined)}><div className="confirm-body"><AlertCircle size={24} /><p>Reset <strong>{resetting.alias}</strong> to the selected runtime defaults?</p>{reset.error && <div className="form-error"><AlertCircle size={15} /> {reset.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setResetting(undefined)} type="button">Cancel</button><button className="button danger" disabled={reset.isPending} onClick={() => reset.mutate()} type="button">{reset.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />} Reset settings</button></footer></Dialog>}
      {repairing?.source_download && <Dialog title="Re-download missing model?" description="The exact pinned model artifact will be downloaded again and repair this profile." onClose={() => setRepairing(undefined)}><div className="confirm-body"><ShieldAlert size={24} /><p><strong>{repairing.source_download.repo_id}</strong> · <span className="mono">{repairing.source_download.group_key}</span> is missing for profile <strong>{repairing.alias}</strong>. Re-download revision <span className="mono">{repairing.source_download.revision.slice(0, 9)}</span>?</p>{redownload.error && <div className="form-error"><AlertCircle size={15} /> {redownload.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setRepairing(undefined)} type="button">Not now</button><button className="button primary" disabled={redownload.isPending} onClick={() => redownload.mutate(repairing.source_download!.id)} type="button">{redownload.isPending ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />} Re-download</button></footer></Dialog>}
    </>
  )
}