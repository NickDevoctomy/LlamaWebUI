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
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { api, type LibraryModel, type Profile, type ProfileCreate, type Runtime } from './api'

function optionalNumber(value: string) {
  return value === '' ? undefined : Number(value)
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
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [backend, setBackend] = useState('cpu')
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

  function submit(event: FormEvent) {
    event.preventDefault()
    registration.mutate()
  }

  return (
    <>
      <section className="data-panel">
        <div className="panel-heading">
          <div><h2>Installed runtimes</h2><p>Local llama.cpp executables available to this control plane.</p></div>
          <button className="button primary compact" onClick={() => setOpen(true)} type="button"><Plus size={15} /> Register runtime</button>
        </div>
        {runtimes.length ? <div className="record-list">{runtimes.map((runtime) => (
          <article className="record-row" key={runtime.id}>
            <span className="record-icon"><Cpu size={18} /></span>
            <div className="record-copy"><strong>{runtime.name}</strong><span>{runtime.executable_path}</span></div>
            <div className="record-meta"><small>Build</small><span>{runtime.build ?? 'Unknown'}</span></div>
            <div className="record-meta"><small>Backend</small><span>{runtime.backend?.toUpperCase() ?? 'AUTO'}</span></div>
            <span className={`state-pill ${runtime.usable ? 'ready' : 'error'}`}>{runtime.usable ? 'Ready' : 'Probe failed'}</span>
          </article>
        ))}</div> : <div className="empty"><Cpu size={28} /><strong>No runtime registered</strong><span>Point Llama Control at an existing llama-server executable to begin.</span><button className="button primary" onClick={() => setOpen(true)} type="button"><Plus size={15} /> Register runtime</button></div>}
      </section>
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
  const [repairing, setRepairing] = useState<Profile>()
  const queryClient = useQueryClient()
  const runtime = runtimes.find((item) => item.id === runtimeId)
  const supports = (option: string) => runtime?.options.includes(option) ?? false
  const aliasValid = /^[a-z0-9][a-z0-9._-]{0,63}$/.test(alias)
  const availableLibrary = selectedLibraryModel && !library.some((item) => item.download_id === selectedLibraryModel.download_id)
    ? [selectedLibraryModel, ...library]
    : library

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
      setAlias('')
      setModelPath('')
      setSelectedLibraryModel(undefined)
    },
  })
  const deletion = useMutation({
    mutationFn: (profileId: string) => api.deleteProfile(profileId),
    onSuccess: async () => {
      setDeleting(undefined)
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

  function submit(event: FormEvent) {
    event.preventDefault()
    creation.mutate()
  }

  return (
    <>
      <section className="data-panel">
        <div className="panel-heading">
          <div><h2>Model profiles</h2><p>Named models and launch settings published to the router.</p></div>
          <button className="button primary compact" disabled={!runtimes.length} onClick={() => setOpen(true)} type="button"><Plus size={15} /> Create profile</button>
        </div>
        {profiles.length ? <div className="record-list">{profiles.map((profile) => (
          <article className="record-row" key={profile.id}>
            <span className="record-icon"><FileCog size={18} /></span>
            <div className="record-copy"><strong>{profile.alias}</strong><span>{profile.model_path}</span></div>
            <div className="record-meta wide"><small>Runtime</small><span>{runtimes.find((item) => item.id === profile.runtime_id)?.name ?? 'Missing runtime'}</span></div>
            {profile.validation_state === 'broken' ? <button className="state-pill error" disabled={running || !profile.source_download} onClick={() => setRepairing(profile)} title={profile.source_download ? 'Repair missing model' : 'Model file is missing'} type="button">Broken</button> : <span className={`state-pill ${profile.enabled ? 'ready' : ''}`}>{profile.enabled ? 'Enabled' : 'Disabled'}</span>}
            <button aria-label={`Delete profile ${profile.alias}`} className="icon-button small danger-icon" disabled={running} onClick={() => setDeleting(profile)} title="Delete profile" type="button"><Trash2 size={16} /></button>
          </article>
        ))}</div> : <div className="empty"><FileCog size={28} /><strong>No profiles configured</strong><span>{runtimes.length ? 'Create a profile for a local GGUF model.' : 'Register a runtime before creating a model profile.'}</span>{runtimes.length > 0 && <button className="button primary" onClick={() => setOpen(true)} type="button"><Plus size={15} /> Create profile</button>}</div>}
      </section>
      {open && <Dialog title="Create model profile" description="Basic identity and capability-aware llama.cpp launch settings." onClose={() => setOpen(false)}>
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
          <footer className="dialog-actions"><button className="button secondary" onClick={() => setOpen(false)} type="button">Cancel</button><button className="button primary" disabled={creation.isPending || !aliasValid || !runtimeId || !modelPath.trim()} type="submit">{creation.isPending ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />} Create profile</button></footer>
        </form>
      </Dialog>}
      {deleting && <Dialog title="Delete model profile?" description="This removes only the profile. Downloaded model files are not affected." onClose={() => setDeleting(undefined)}><div className="confirm-body"><Trash2 size={24} /><p>Profile <strong>{deleting.alias}</strong> will be permanently removed.</p>{deletion.error && <div className="form-error"><AlertCircle size={15} /> {deletion.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep profile</button><button className="button danger" disabled={deletion.isPending} onClick={() => deletion.mutate(deleting.id)} type="button">{deletion.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete profile</button></footer></Dialog>}
      {repairing?.source_download && <Dialog title="Re-download missing model?" description="The exact pinned model artifact will be downloaded again and repair this profile." onClose={() => setRepairing(undefined)}><div className="confirm-body"><ShieldAlert size={24} /><p><strong>{repairing.source_download.repo_id}</strong> · <span className="mono">{repairing.source_download.group_key}</span> is missing for profile <strong>{repairing.alias}</strong>. Re-download revision <span className="mono">{repairing.source_download.revision.slice(0, 9)}</span>?</p>{redownload.error && <div className="form-error"><AlertCircle size={15} /> {redownload.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setRepairing(undefined)} type="button">Not now</button><button className="button primary" disabled={redownload.isPending} onClick={() => redownload.mutate(repairing.source_download!.id)} type="button">{redownload.isPending ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />} Re-download</button></footer></Dialog>}
    </>
  )
}