import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Download, FileCheck2, Info, KeyRound, LoaderCircle, Save, Settings, ShieldCheck, Trash2, Upload } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { api, type ApprovalCategory, type LinterStatus, type SecretName } from './api'
import { DebugTab } from './DebugTab'
import { OpenRouterModelPicker } from './OpenRouterModelPicker'
import { formatBytes } from './Dashboard'

const SETTINGS_WRITE_HINT = 'Changing settings needs the settings.write privilege'

const STAGES: Record<NonNullable<LinterStatus['progress']>['stage'], string> = {
  downloading: 'Downloading Node.js',
  verifying: 'Checking the download',
  extracting: 'Unpacking Node.js',
  packages: 'Installing ESLint',
}

function InstallProgress({ progress }: { progress: NonNullable<LinterStatus['progress']> }) {
  const { received, total } = progress
  const percent = progress.stage === 'downloading' && total ? Math.min(100, Math.round((received ?? 0) / total * 100)) : null
  return <div className="recommendation-progress" role="status">
    <div><span className={percent == null ? 'indeterminate' : undefined} style={{ width: `${percent ?? 100}%` }} /></div>
    <small>{STAGES[progress.stage]}{percent != null ? ` · ${formatBytes(received ?? 0)} of ${formatBytes(total ?? 0)} · ${percent}%` : '…'}</small>
  </div>
}

/** ESLint, downloaded on request, gives benchmark agents check_file and, with TypeScript and esbuild, the TypeScript tools. */
function EslintRow({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const tools = useQuery({
    queryKey: ['benchmark-tools'],
    queryFn: api.benchmarkTools,
    refetchInterval: (query) => query.state.data?.eslint.state === 'installing' ? 1000 : false,
  })
  const settle = async () => {
    await Promise.all([queryClient.invalidateQueries({ queryKey: ['benchmark-tools'] }), queryClient.invalidateQueries({ queryKey: ['benchmarks'] })])
  }
  const install = useMutation({ mutationFn: api.installEslint, onSettled: settle })
  const remove = useMutation({ mutationFn: api.removeEslint, onSettled: settle })
  const eslint = tools.data?.eslint
  const versions = eslint?.versions ?? eslint?.pinned
  const busy = install.isPending || remove.isPending || eslint?.state === 'installing'
  const error = install.error?.message ?? remove.error?.message ?? eslint?.error
  if (!eslint) return <div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading tools…</span></div>
  return <div className="record-row">
    <span className="record-icon"><FileCheck2 size={17} /></span>
    <div className="record-copy">
      <strong className="record-title">ESLint and TypeScript {eslint.state === 'installed' && <span className="profile-tag">{eslint.outdated ? 'Update available' : 'Installed'}</span>}</strong>
      <span className="record-description">Lints JavaScript, and the scripts inside HTML pages, for the <code>check_file</code> benchmark tool. TypeScript and esbuild give TypeScript benchmarks <code>check_typescript</code> and <code>bundle_typescript</code>. Runs on its own copy of Node.js, kept in the data folder.</span>
      {versions && <span title={eslint.state === 'installed' ? 'Installed versions' : 'Versions an install fetches'}>{eslint.state === 'installed' ? '' : 'Fetches '}Node.js {versions.node} · eslint {versions.eslint} · eslint-plugin-html {versions['eslint-plugin-html']}{versions.typescript ? ` · typescript ${versions.typescript} · esbuild ${versions.esbuild}` : ''}{eslint.size ? ` · ${formatBytes(eslint.size)}` : ''}</span>}
      {eslint.outdated && <span>Installed before the TypeScript tools. Update to add TypeScript {eslint.pinned.typescript} and esbuild {eslint.pinned.esbuild}; it downloads everything again.</span>}
    </div>
    <span className="row-actions">
      {eslint.outdated && <button className="button primary compact" disabled={!canManage || busy} onClick={() => install.mutate()} title={canManage ? undefined : SETTINGS_WRITE_HINT} type="button">{install.isPending ? <LoaderCircle className="spin" size={14} /> : <Download size={14} />} Update</button>}
      {eslint.state === 'installed'
        ? <button className="button secondary compact" disabled={!canManage || busy} onClick={() => remove.mutate()} type="button">{remove.isPending ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />} Remove</button>
        : <button className="button primary compact" disabled={!canManage || busy} onClick={() => install.mutate()} title={canManage ? undefined : SETTINGS_WRITE_HINT} type="button">{busy ? <LoaderCircle className="spin" size={14} /> : <Download size={14} />} {eslint.state === 'failed' ? 'Try again' : 'Download'}</button>}
    </span>
    {eslint.progress && <InstallProgress progress={eslint.progress} />}
    {error && eslint.state !== 'installing' && <div className="form-error record-details" role="alert"><AlertCircle size={15} /> {error}</div>}
  </div>
}

const SECRETS: Record<SecretName, { tab: string; title: string; label: string; noun: string; placeholder: string; intro: ReactNode }> = {
  'huggingface-token': {
    tab: 'Hugging Face',
    title: 'Hugging Face token',
    label: 'Access token',
    noun: 'access token',
    placeholder: 'hf_…',
    intro: <>Lets downloads reach gated and private repositories. Create a read token at <a href="https://huggingface.co/settings/tokens" rel="noreferrer" target="_blank">huggingface.co/settings/tokens</a>.</>,
  },
  'openrouter-api-key': {
    tab: 'OpenRouter',
    title: 'OpenRouter API key',
    label: 'API key',
    noun: 'API key',
    placeholder: 'sk-or-…',
    intro: <>Authenticates requests to models hosted on OpenRouter. Create a key at <a href="https://openrouter.ai/settings/keys" rel="noreferrer" target="_blank">openrouter.ai/settings/keys</a>.</>,
  },
}

const SECRETS_WRITE_HINT = 'Changing credentials needs the secrets.write privilege'

/** One credential: whether it is saved, and a field to save or replace it. The saved value is never sent back. */
function SecretTab({ name, canManage }: { name: SecretName; canManage: boolean }) {
  const queryClient = useQueryClient()
  const { title, label, noun, placeholder, intro } = SECRETS[name]
  const [value, setValue] = useState('')
  const secrets = useQuery({ queryKey: ['secrets'], queryFn: api.secrets })
  const settle = () => Promise.all([queryClient.invalidateQueries({ queryKey: ['secrets'] }), queryClient.invalidateQueries({ queryKey: ['assistant-status'] })])
  const save = useMutation({ mutationFn: () => api.saveSecret(name, value), onSuccess: () => setValue(''), onSettled: settle })
  const remove = useMutation({ mutationFn: () => api.removeSecret(name), onSettled: settle })
  const secret = secrets.data?.secrets.find((item) => item.name === name)
  const store = secrets.data?.store
  const error = save.error?.message ?? remove.error?.message
  return <>
    <div className="settings-section"><h3>{title}</h3><p>{intro}</p></div>
    {secrets.isError
      ? <div className="settings-body"><div className="capability-note error" role="alert"><AlertCircle size={16} /><span>{secrets.error.message}</span></div></div>
      : !secret
        ? <div className="settings-body"><div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Checking the credential store…</span></div></div>
        : <>
          <div className="record-list"><div className="record-row">
            <span className="record-icon"><KeyRound size={17} /></span>
            <div className="record-copy">
              <strong className="record-title">{label} <span className={secret.configured ? 'profile-tag recommended-tag' : 'profile-tag'}>{secret.configured ? 'Saved' : 'Not saved'}</span></strong>
              <span className="record-description">{secret.configured
                ? <>Kept in {store}, ending in <code>{secret.last_four}</code>.</>
                : `Nothing saved yet. A saved ${noun} is kept in ${store}, never in the database.`}</span>
            </div>
            {secret.configured && <span className="row-actions">
              <button className="button secondary compact" disabled={!canManage || remove.isPending} onClick={() => remove.mutate()} title={canManage ? undefined : SECRETS_WRITE_HINT} type="button">{remove.isPending ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />} Remove</button>
            </span>}
          </div></div>
          <form className="secret-form" onSubmit={(event) => { event.preventDefault(); save.mutate() }}>
            <label className="form-field">
              <span>{secret.configured ? `Replace ${noun}` : label}</span>
              <input autoComplete="off" disabled={!canManage} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} spellCheck={false} title={canManage ? undefined : SECRETS_WRITE_HINT} type="password" value={value} />
            </label>
            <button className="button primary" disabled={!canManage || !value.trim() || save.isPending} title={canManage ? undefined : SECRETS_WRITE_HINT} type="submit">{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />} Save</button>
            {error && <div className="form-error" role="alert"><AlertCircle size={15} /> {error}</div>}
          </form>
        </>}
  </>
}

function SecurityTab({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const security = useQuery({ queryKey: ['security-settings'], queryFn: api.securitySettings })
  const update = useMutation({
    mutationFn: api.updateSecuritySettings,
    onSettled: () => Promise.all([queryClient.invalidateQueries({ queryKey: ['security-settings'] }), queryClient.invalidateQueries({ queryKey: ['secrets'] })]),
  })
  const settings = security.data
  if (security.isError) return <div className="settings-body"><div className="capability-note error" role="alert"><AlertCircle size={16} /><span>{security.error.message}</span></div></div>
  if (!settings) return <div className="settings-body"><div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading security settings…</span></div></div>
  const allowed = settings.allow_remote_secrets
  // Turning remote access on is only accepted from the server itself; turning it off works anywhere.
  const blockedHere = !allowed && !settings.local_request
  const hint = !canManage ? SETTINGS_WRITE_HINT : blockedHere ? 'Turn this on from a browser on the server itself' : undefined
  return <>
    <div className="settings-section"><h3>Secrets</h3><p>Provider credentials are kept in the system credential store and are never sent back to the browser.</p></div>
    <div className="record-list"><div className="record-row">
      <span className="record-icon"><ShieldCheck size={17} /></span>
      <div className="record-copy">
        <strong className="record-title">Allow secrets access from remote</strong>
        <span className="record-description">When off, credentials can only be viewed, saved or removed from a browser on the server itself.</span>
      </div>
      <span className="row-actions">
        <button aria-checked={allowed} aria-label="Allow secrets access from remote" className="profile-switch" disabled={!canManage || blockedHere || update.isPending} onClick={() => update.mutate(!allowed)} role="switch" title={hint} type="button">
          <span aria-hidden="true" className="switch-track"><span /></span>{allowed ? 'Allowed' : 'Off'}
        </button>
      </span>
      {blockedHere && <div className="capability-note"><Info size={16} /><span>You are connected from another machine, so this can only be turned on from the server itself.</span></div>}
      {update.error && <div className="form-error record-details" role="alert"><AlertCircle size={15} /> {update.error.message}</div>}
    </div></div>
  </>
}

/** The assistant's name, and the limits on what one conversation may spend and do. */
const APPROVAL_CATEGORIES: { key: ApprovalCategory; label: string; description: string }[] = [
  { key: 'downloads', label: 'Model downloads', description: 'Downloading a model from Hugging Face into the library.' },
  { key: 'profiles', label: 'Profile changes', description: 'Creating, changing, copying or deleting a profile.' },
  { key: 'benchmarks', label: 'Benchmarks and router restarts', description: 'Running a benchmark, or starting or stopping the router, which unloads any models in use.' },
  { key: 'runtimes', label: 'Runtime installs', description: 'Downloading and registering a llama.cpp build.' },
]

function AssistantTab({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['assistant-settings'], queryFn: api.assistantSettings })
  const [draft, setDraft] = useState<{ name: string; spend: string; rounds: string; search: string }>()
  const saved = settings.data
  const values = draft ?? (saved ? { name: saved.name, spend: String(saved.spend_limit_usd), rounds: String(saved.max_rounds_per_turn), search: saved.search_model ?? '' } : undefined)
  // Suggestions for the search model, from OpenRouter's list as it is typed.
  const searchQuery = (values?.search ?? '').trim()
  const searchModels = useQuery({
    queryKey: ['openrouter-models', searchQuery],
    queryFn: () => api.openRouterModels(searchQuery),
    enabled: canManage && searchQuery.length >= 2,
  })
  const save = useMutation({
    mutationFn: () => api.updateAssistantSettings({ ...saved!, name: values!.name, spend_limit_usd: Number(values!.spend), max_rounds_per_turn: Number(values!.rounds), search_model: values!.search.trim() || null }),
    onSuccess: async (next) => {
      queryClient.setQueryData(['assistant-settings'], next)
      setDraft(undefined)
      await queryClient.invalidateQueries({ queryKey: ['assistant-status'] })
    },
  })
  // Each switch saves at once, keeping the other saved settings as they are.
  const toggle = useMutation({
    mutationFn: (category: ApprovalCategory) => api.updateAssistantSettings({ ...saved!, approvals: { ...saved!.approvals, [category]: !saved!.approvals[category] } }),
    onSuccess: (next) => queryClient.setQueryData(['assistant-settings'], next),
  })
  const publishing = useMutation({
    mutationFn: () => api.updateAssistantSettings({ ...saved!, auto_publish: !saved!.auto_publish }),
    onSuccess: (next) => queryClient.setQueryData(['assistant-settings'], next),
  })
  if (settings.isError) return <div className="settings-body"><div className="capability-note error" role="alert"><AlertCircle size={16} /><span>{settings.error.message}</span></div></div>
  if (!values) return <div className="settings-body"><div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading assistant settings…</span></div></div>
  const change = (key: 'name' | 'spend' | 'rounds' | 'search') => (event: React.ChangeEvent<HTMLInputElement>) => setDraft({ ...values, [key]: event.target.value })
  const hint = canManage ? undefined : SETTINGS_WRITE_HINT
  return <>
    <div className="settings-section"><h3>Assistant</h3><p>What the assistant is called, and how far one conversation may go before it stops to check with you.</p></div>
    <form className="secret-form assistant-settings-form" onSubmit={(event) => { event.preventDefault(); save.mutate() }}>
      <label className="form-field"><span>Name</span><input disabled={!canManage} maxLength={40} onChange={change('name')} title={hint} value={values.name} /><small>Used in its replies and its instructions.</small></label>
      <label className="form-field"><span>Spending limit (USD)</span><input disabled={!canManage} inputMode="decimal" min="0.01" onChange={change('spend')} step="0.01" title={hint} type="number" value={values.spend} /><small>A conversation pauses at this cost; continuing adds the same again.</small></label>
      <label className="form-field"><span>Model calls per message</span><input disabled={!canManage} inputMode="numeric" min="1" onChange={change('rounds')} step="1" title={hint} type="number" value={values.rounds} /><small>Tool calls included, before it stops and waits.</small></label>
      <label className="form-field"><span>Web search model</span><input autoComplete="off" disabled={!canManage} list="search-models" onChange={change('search')} placeholder="Same as the assistant" spellCheck={false} title={hint} value={values.search} /><small>An OpenRouter model that answers web searches. One built for search returns answers with sources.</small></label>
      <datalist id="search-models">{(searchModels.data?.models ?? []).map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</datalist>
      <button className="button primary" disabled={!canManage || !draft || !values.name.trim() || save.isPending} title={hint} type="submit">{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />} Save</button>
      {save.error && <div className="form-error" role="alert"><AlertCircle size={15} /> {save.error.message}</div>}
    </form>
    <div className="settings-section"><h3>Approvals</h3><p>Actions that wait for you to approve them in the conversation before the assistant takes them.</p></div>
    <div className="record-list">{APPROVAL_CATEGORIES.map(({ key, label, description }) => {
      const required = saved?.approvals[key] ?? true
      return <div className="record-row" key={key}>
        <span className="record-icon"><ShieldCheck size={17} /></span>
        <div className="record-copy"><strong className="record-title">{label}</strong><span className="record-description">{description}</span></div>
        <span className="row-actions">
          <button aria-checked={required} aria-label={`Ask before ${label.toLowerCase()}`} className="profile-switch" disabled={!canManage || toggle.isPending} onClick={() => toggle.mutate(key)} role="switch" title={hint} type="button">
            <span aria-hidden="true" className="switch-track"><span /></span>{required ? 'Ask first' : 'Allowed'}
          </button>
        </span>
      </div>
    })}</div>
    {toggle.error && <div className="settings-body"><div className="form-error" role="alert"><AlertCircle size={15} /> {toggle.error.message}</div></div>}
    <div className="settings-section"><h3>Research and reports</h3><p>What the assistant learns and the reports it writes can ship with LlamaWebUI, so later installs start with them.</p></div>
    <div className="record-list">
      <div className="record-row">
        <span className="record-icon"><Upload size={17} /></span>
        <div className="record-copy"><strong className="record-title">Auto-publish</strong><span className="record-description">Write new research and reports straight into the repository, ready to review and commit, instead of publishing them by hand. Works only when LlamaWebUI runs from a source checkout, and can only be switched on from this machine.</span></div>
        <span className="row-actions">
          <button aria-checked={Boolean(saved?.auto_publish)} aria-label="Auto-publish research and reports" className="profile-switch" disabled={!canManage || publishing.isPending} onClick={() => publishing.mutate()} role="switch" title={hint} type="button">
            <span aria-hidden="true" className="switch-track"><span /></span>{saved?.auto_publish ? 'On' : 'Off'}
          </button>
        </span>
      </div>
    </div>
    {publishing.error && <div className="settings-body"><div className="form-error" role="alert"><AlertCircle size={15} /> {publishing.error.message}</div></div>}
  </>
}

type SettingsTab = 'benchmark' | 'security' | 'assistant' | 'debug' | SecretName

export function SettingsPanel({ canManage, canViewSettings = true, canViewSecrets = false, canManageSecrets = false, canViewDiagnostics = false, canManageDiagnostics = false }: {
  canManage: boolean
  canViewSettings?: boolean
  canViewSecrets?: boolean
  canManageSecrets?: boolean
  canViewDiagnostics?: boolean
  canManageDiagnostics?: boolean
}) {
  const tabs: { key: SettingsTab; label: string }[] = [
    ...(canViewSettings ? [{ key: 'benchmark' as const, label: 'Benchmark' }, { key: 'security' as const, label: 'Security' }] : []),
    ...(canViewSecrets ? [{ key: 'huggingface-token' as const, label: SECRETS['huggingface-token'].tab }] : []),
    // The OpenRouter tab holds the API key (secrets) and the assistant's model (settings).
    ...(canViewSecrets || canViewSettings ? [{ key: 'openrouter-api-key' as const, label: SECRETS['openrouter-api-key'].tab }] : []),
    ...(canViewSettings ? [{ key: 'assistant' as const, label: 'Assistant' }] : []),
    ...(canViewDiagnostics ? [{ key: 'debug' as const, label: 'Debug' }] : []),
  ]
  const [selected, setTab] = useState<SettingsTab>()
  const tab = selected ?? tabs[0]?.key
  return <section className="data-panel settings-panel">
    <div className="panel-heading"><div><h2>Settings</h2><p>Control-plane preferences, provider credentials, the tools features rely on and debugging.</p></div><Settings size={20} /></div>
    <div aria-label="Settings" className="segmented settings-tabs" role="tablist">
      {tabs.map(({ key, label }) => <button aria-selected={tab === key} key={key} onClick={() => setTab(key)} role="tab" type="button">{label}</button>)}
    </div>
    {tab === 'security' && <div role="tabpanel" aria-label="Security"><SecurityTab canManage={canManage} /></div>}
    {tab === 'assistant' && <div role="tabpanel" aria-label="Assistant"><AssistantTab canManage={canManage} /></div>}
    {tab === 'huggingface-token' && <div role="tabpanel" aria-label="Hugging Face"><SecretTab key={tab} canManage={canManageSecrets} name={tab} /></div>}
    {tab === 'openrouter-api-key' && <div role="tabpanel" aria-label="OpenRouter">
      {canViewSecrets && <SecretTab key={tab} canManage={canManageSecrets} name={tab} />}
      {canViewSettings && <OpenRouterModelPicker canManage={canManage} />}
    </div>}
    {tab === 'debug' && <div role="tabpanel" aria-label="Debug"><DebugTab canManage={canManageDiagnostics} /></div>}
    {tab === 'benchmark' && <div role="tabpanel" aria-label="Benchmark">
      <div className="settings-section"><h3>Tools</h3><p>Extra tools benchmark agents can be given. Each one is offered in a run&apos;s tool list once it is installed.</p></div>
      <div className="record-list"><EslintRow canManage={canManage} /></div>
    </div>}
  </section>
}
