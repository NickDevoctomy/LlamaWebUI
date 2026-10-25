import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, BadgeCheck, CircleArrowUp, Cpu, Download, FolderCog, LoaderCircle, MemoryStick, MonitorCog, Plus } from 'lucide-react'
import { useState } from 'react'
import { api, type ArgumentCatalog, type DownloadJob, type Profile, type Recommendation, type Runtime, type StoredArgument } from './api'
import { groupByCategory } from './argumentCatalog'
import { formatBytes } from './Dashboard'
import { EffectPills } from './ProfileArgumentEditor'
import { Field } from './SetupPanels'

const fitLabels = {
  fits: 'Enough memory',
  tight: 'Memory is tight',
  insufficient: 'Not enough memory',
} as const

const backendNames: Record<string, string> = { rocm: 'ROCm', cuda: 'CUDA', cpu: 'CPU', sycl: 'SYCL' }

function backendList(backends: string[]) {
  const names = backends.map((backend) => backendNames[backend] ?? backend.charAt(0).toUpperCase() + backend.slice(1))
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} or ${names.at(-1)}` : names[0]
}

function argumentName(argument: StoredArgument) {
  return 'id' in argument ? argument.id : argument.flag.replace(/^-+/, '')
}

function argumentValue(argument: StoredArgument) {
  if (typeof argument.value === 'number') return argument.value.toLocaleString()
  return Array.isArray(argument.value) ? argument.value.join(', ') : String(argument.value)
}

/** The launch settings grouped by catalog category, each with its colour-coded effects. */
function LaunchSettings({ catalog, settings }: { catalog?: ArgumentCatalog; settings: StoredArgument[] }) {
  if (!catalog) {
    return <ul aria-label="Launch settings" className="recommendation-arguments">
      {settings.map((argument) => <li key={argumentName(argument)}><code>{argumentName(argument)} {argumentValue(argument)}</code></li>)}
    </ul>
  }
  const definitions = new Map(catalog.arguments.map((definition) => [definition.id, definition]))
  const groups = groupByCategory(catalog, settings, (argument) => ('id' in argument ? definitions.get(argument.id)?.category : undefined) ?? 'custom')
  return <div aria-label="Launch settings" className="recommendation-settings" role="list">
    {groups.map((group) => <section aria-label={group.label} className="setting-group" key={group.id} role="listitem">
      <h3>{group.id === 'custom' ? 'Custom' : group.label}</h3>
      <ul>
        {group.items.map((argument) => {
          const definition = 'id' in argument ? definitions.get(argument.id) : undefined
          return <li className="setting" key={argumentName(argument)}>
            <span className="setting-name" title={definition?.help.summary}>{definition?.label ?? argumentName(argument)}</span>
            <code className="setting-value" title={definition?.flag}>{argumentValue(argument)}</code>
            {definition && <EffectPills catalog={catalog} tags={definition.effectTags} />}
          </li>
        })}
      </ul>
    </section>)}
  </div>
}

type DownloadProgress = { state: string; completed_bytes: number; total_bytes: number }

function DownloadBar({ download, label }: { download: DownloadProgress; label?: string }) {
  const percent = download.total_bytes ? Math.min(100, Math.round(download.completed_bytes / download.total_bytes * 100)) : 0
  return <div className="recommendation-progress">
    <div><span style={{ width: `${percent}%` }} /></div>
    <small>{label && `${label} · `}{download.state === 'paused' ? 'Paused' : download.state === 'queued' ? 'Queued' : 'Downloading'} · {formatBytes(download.completed_bytes)} of {formatBytes(download.total_bytes)} · {percent}%</small>
  </div>
}

function ProfileState({ profile, onOpenProfiles }: { profile?: Profile; onOpenProfiles: () => void }) {
  if (profile?.validation_state === 'pending' && profile.download) {
    return <div className="recommendation-status">
      <DownloadBar download={profile.download} />
      <p>The profile <strong className="mono">{profile.alias}</strong> switches on by itself when the download finishes.</p>
    </div>
  }
  return <div className="recommendation-status">
    <p className="recommendation-added">
      <BadgeCheck size={16} />
      {profile?.enabled
        ? <span>Added as <strong className="mono">{profile.alias}</strong> and enabled. Restart the router to serve it.</span>
        : <span>Added as <strong className="mono">{profile?.alias ?? 'a profile'}</strong>{profile?.validation_state === 'broken' ? '. Its model files are missing; repair it from Profiles.' : '.'}</span>}
    </p>
    <button className="button secondary compact" onClick={onOpenProfiles} type="button"><FolderCog size={14} /> Open profiles</button>
  </div>
}

function RecommendationCard({ recommendation, variants, onSelectVariant, catalog, runtimes, profiles, downloads, canApply, running, onOpenProfiles, onOpenRuntimes }: {
  recommendation: Recommendation
  variants: Recommendation[]
  onSelectVariant: (id: string) => void
  catalog?: ArgumentCatalog
  runtimes: Runtime[]
  profiles: Profile[]
  downloads: DownloadJob[]
  canApply: boolean
  running: boolean
  onOpenProfiles: () => void
  onOpenRuntimes: () => void
}) {
  const queryClient = useQueryClient()
  // Tested backends are a preference, not a requirement: offer every usable runtime, tested ones first.
  const tested = new Set(recommendation.compatible_runtime_ids)
  const usable = runtimes
    .filter((runtime) => runtime.usable)
    .sort((left, right) => Number(tested.has(right.id)) - Number(tested.has(left.id)))
  const [runtimeId, setRuntimeId] = useState('')
  const selectedRuntime = runtimeId || usable[0]?.id || ''
  const selected = usable.find((runtime) => runtime.id === selectedRuntime)
  const untested = selected !== undefined && !tested.has(selected.id)
  const [aliasText, setAliasText] = useState<string>()
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['recommendations'] }),
    queryClient.invalidateQueries({ queryKey: ['profiles'] }),
    queryClient.invalidateQueries({ queryKey: ['downloads'] }),
  ])
  // The recommended alias is taken by a profile with other settings: name the new copy.
  const aliasTaken = recommendation.suggested_alias !== recommendation.profile.alias
  const alias = aliasText ?? recommendation.suggested_alias
  const aliasValid = /^[a-z0-9][a-z0-9._-]{0,63}$/.test(alias)
  const apply = useMutation({
    mutationFn: (addProfile: boolean) => api.applyRecommendation(recommendation.id, selectedRuntime, { alias: aliasTaken && addProfile ? alias : undefined, addProfile }),
    onSuccess: () => { setAliasText(undefined); return refresh() },
  })
  const upgrade = useMutation({ mutationFn: (profile: Profile) => api.upgradeProfile(profile.id), onSuccess: refresh })
  const { assessment } = recommendation
  const matchedId = recommendation.matches[selectedRuntime]?.[0]
  const matched = profiles.find((profile) => profile.id === matchedId)
  const upgradable = profiles.filter((profile) => recommendation.profile_ids.includes(profile.id) && profile.recommendation?.upgradable)
  // Live progress from the polled downloads list, falling back to the state at page load.
  const download = downloads.find((job) => job.id === recommendation.download?.id) ?? recommendation.download
  const draft = downloads.find((job) => job.id === recommendation.draft_download?.id) ?? recommendation.draft_download ?? null
  const hasDraft = Boolean(recommendation.model.draft_group)
  // Every file is on disk or on its way, so adding the profile downloads nothing new.
  const haveFiles = download !== null && (!hasDraft || draft !== null)
  const active = (job: DownloadProgress | null): job is DownloadProgress => job !== null && job.state !== 'completed'
  const error = apply.error ?? upgrade.error

  return <article className="data-panel recommendation-card" data-fit={assessment.fit} data-match={assessment.hardware_match}>
    <header className="recommendation-head">
      <div>
        <h2>{recommendation.title}</h2>
        <p className="mono">{recommendation.model.repo_id} · {recommendation.model.quantization}{recommendation.model.draft_group && <> · draft {recommendation.model.draft_group}</>}</p>
      </div>
      <div className="recommendation-badges">
        {assessment.hardware_match ? <span className="state-pill ready">Tuned on hardware like yours</span> : <span className="state-pill">Tuned for different hardware</span>}
        <span className={`state-pill ${assessment.fit === 'fits' ? 'ready' : assessment.fit === 'tight' ? 'warning' : 'error'}`}>{fitLabels[assessment.fit]}</span>
        {recommendation.profile.exclusive && <span className="state-pill" title="Uses the machine fully, so no other model can be loaded beside it">Exclusive</span>}
      </div>
    </header>
    {variants.length > 1 && <div aria-label="Variant" className="segmented recommendation-variants" role="tablist">
      {variants.map((variant) => <button aria-selected={variant.id === recommendation.id} key={variant.id} onClick={() => onSelectVariant(variant.id)} role="tab" title={variant.profile_ids.length ? `${variant.title} · added as a profile` : variant.title} type="button">
        {variant.label ?? variant.title}{variant.profile_ids.length > 0 && <BadgeCheck aria-label="Added" size={13} />}
      </button>)}
    </div>}
    <p className="recommendation-summary">{recommendation.summary}</p>
    <dl className="recommendation-facts">
      <div><dt>Tested on</dt><dd>{recommendation.tested_on.label}{recommendation.tested_on.notes && <small>{recommendation.tested_on.notes}</small>}</dd></div>
      <div><dt>Needs</dt><dd>{recommendation.requirements.vram_gb ? `${recommendation.requirements.memory_gb - recommendation.requirements.vram_gb} GB memory + ${recommendation.requirements.vram_gb} GB VRAM` : `${recommendation.requirements.memory_gb} GB memory`}<small>This machine has {assessment.available_memory_gb} GB. Tested with a {backendList(recommendation.requirements.backends)} runtime.</small></dd></div>
      <div><dt>Profile alias</dt><dd className="mono">{recommendation.profile.alias}<small>Version {recommendation.version}</small></dd></div>
    </dl>
    <LaunchSettings catalog={catalog} settings={recommendation.profile.arguments} />
    <footer className="recommendation-actions">
      {matchedId ? <ProfileState onOpenProfiles={onOpenProfiles} profile={matched} />
        : !usable.length ? <div className="recommendation-status">
          <p className="recommendation-note"><AlertCircle size={15} /> Register a llama-server build to use this profile. It was tested with {backendList(recommendation.requirements.backends)}.</p>
          <button className="button secondary compact" onClick={onOpenRuntimes} type="button"><Cpu size={14} /> Open runtimes</button>
        </div>
          : <div className="recommendation-apply">
            {assessment.fit === 'insufficient' && <p className="recommendation-note"><AlertCircle size={15} /> This machine has less memory than the profile was tuned for. It may fail to load or need a smaller context.</p>}
            {untested && <p className="recommendation-note"><AlertCircle size={15} /> {selected.backend?.toLowerCase() === 'cpu'
              ? `${selected.name} is a CPU build, so GPU offload settings have no effect and it will run much slower than tested.`
              : `Tuned with ${backendList(recommendation.requirements.backends)}. The settings carry over to ${selected.backend ? backendList([selected.backend.toLowerCase()]) : 'this runtime'}, but speed and memory use will differ from the tested machine.`}</p>}
            {usable.length > 1 && <label className="form-field"><span>Runtime</span><select onChange={(event) => setRuntimeId(event.target.value)} value={selectedRuntime}>{usable.map((runtime) => <option key={runtime.id} value={runtime.id}>{runtime.name} · {runtime.build ?? 'unknown build'}{tested.has(runtime.id) ? ' · tested backend' : ''}</option>)}</select></label>}
            {usable.length === 1 && <p className="recommendation-runtime">Runs on <strong>{usable[0].name}</strong>{usable[0].build && <span className="mono"> · {usable[0].build}</span>}</p>}
            {active(download) && <DownloadBar download={download} label={hasDraft ? 'Model' : undefined} />}
            {active(draft) && <DownloadBar download={draft} label="Draft model" />}
            {upgradable.map((profile) => <p className="recommendation-note info" key={profile.id}><CircleArrowUp size={15} /> <span><strong className="mono">{profile.alias}</strong> is unchanged from version {profile.version ?? 1}. Upgrade it to version {recommendation.version}, or add version {recommendation.version} as another profile.</span></p>)}
            {aliasTaken && <Field className="recommendation-alias" hint={`${recommendation.profile.alias} is already used by a profile with different settings.`} label="Profile alias">
              <input aria-invalid={!aliasValid} onChange={(event) => setAliasText(event.target.value.toLowerCase().replace(/\s+/g, '-'))} value={alias} />
            </Field>}
            <div className="recommendation-buttons">
              {upgradable.map((profile) => <button className="button secondary" disabled={!canApply || running || upgrade.isPending} key={profile.id} onClick={() => upgrade.mutate(profile)} title={running ? 'Stop the router to upgrade a profile' : `Replace the settings of ${profile.alias} with version ${recommendation.version}. Its alias, model location and enabled state are kept.`} type="button">
                {upgrade.isPending && upgrade.variables?.id === profile.id ? <LoaderCircle className="spin" size={15} /> : <CircleArrowUp size={15} />} Upgrade {profile.alias}
              </button>)}
              {!haveFiles && <button className="button secondary" disabled={!canApply || apply.isPending || !selectedRuntime} onClick={() => apply.mutate(false)} title={canApply ? 'Download the model without adding a profile' : 'Requires profile and download write access'} type="button">
                {apply.isPending && apply.variables === false ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />} Download model
              </button>}
              <button className="button primary" disabled={!canApply || apply.isPending || !selectedRuntime || (aliasTaken && !aliasValid)} onClick={() => apply.mutate(true)} title={canApply ? undefined : 'Requires profile and download write access'} type="button">
                {apply.isPending && apply.variables ? <LoaderCircle className="spin" size={15} /> : haveFiles ? <Plus size={15} /> : <Download size={15} />} {haveFiles ? 'Add profile' : 'Download model and add profile'}
              </button>
            </div>
          </div>}
      {error && <div className="form-error" role="alert"><AlertCircle size={15} /> {error.message}</div>}
    </footer>
  </article>
}

/** The variant to show first: one tuned for this machine with enough memory, then the base. */
function preferredVariant(variants: Recommendation[]) {
  return variants.find((item) => item.assessment.hardware_match && item.assessment.fit === 'fits')
    ?? variants.find((item) => item.assessment.hardware_match)
    ?? variants.find((item) => item.assessment.fit === 'fits')
    ?? variants[0]
}

/** One recommendation with its variants, showing the picked one. */
function RecommendationFamily({ variants, ...props }: Omit<Parameters<typeof RecommendationCard>[0], 'recommendation' | 'onSelectVariant'>) {
  const [selectedId, setSelectedId] = useState<string>()
  const recommendation = variants.find((item) => item.id === selectedId) ?? preferredVariant(variants)
  return <RecommendationCard {...props} key={recommendation.id} onSelectVariant={setSelectedId} recommendation={recommendation} variants={variants} />
}

/** Recommendations grouped by family in bundled order, the base first. */
function families(recommendations: Recommendation[]) {
  const grouped = new Map<string, Recommendation[]>()
  for (const item of recommendations) {
    const family = item.family ?? item.id
    grouped.set(family, [...(grouped.get(family) ?? []), item])
  }
  return [...grouped.entries()]
}

export function RecommendationsPanel({ runtimes, profiles, downloads, canApply, running, onOpenProfiles, onOpenRuntimes }: {
  runtimes: Runtime[]
  profiles: Profile[]
  downloads: DownloadJob[]
  canApply: boolean
  running: boolean
  onOpenProfiles: () => void
  onOpenRuntimes: () => void
}) {
  const list = useQuery({ queryKey: ['recommendations'], queryFn: api.recommendations, refetchInterval: false })
  const catalog = useQuery({ queryKey: ['argument-catalog', 'base'], queryFn: api.argumentCatalog, refetchInterval: false, staleTime: Infinity })
  const host = list.data?.host

  return <div className="recommendations">
    <section className="data-panel host-panel" aria-label="This machine">
      <div className="host-item"><span className="shortcut-icon"><Cpu size={17} /></span><span><small>Processor</small><strong>{host ? host.cpu ?? 'Unknown' : '…'}</strong></span></div>
      <div className="host-item"><span className="shortcut-icon"><MemoryStick size={17} /></span><span><small>Memory</small><strong>{host ? formatBytes(host.memory_bytes) : '…'}{host?.discrete_vram_bytes ? ` + ${formatBytes(host.discrete_vram_bytes)} VRAM` : ''}</strong></span></div>
      <div className="host-item"><span className="shortcut-icon"><MonitorCog size={17} /></span><span><small>Graphics</small><strong>{host ? host.gpus.join(', ') || 'None detected' : '…'}</strong></span></div>
    </section>
    {list.error ? <div className="form-error" role="alert"><AlertCircle size={15} /> {list.error.message}</div>
      : list.isPending ? <div className="empty"><LoaderCircle className="spin" size={26} /><strong>Checking this machine</strong></div>
        : list.data.recommendations.length ? families(list.data.recommendations)
          .sort(([, left], [, right]) => Number(right.some((item) => item.assessment.hardware_match)) - Number(left.some((item) => item.assessment.hardware_match)))
          .map(([family, variants]) => <RecommendationFamily
          canApply={canApply}
          catalog={catalog.data}
          downloads={downloads}
          key={family}
          onOpenProfiles={onOpenProfiles}
          onOpenRuntimes={onOpenRuntimes}
          profiles={profiles}
          running={running}
          runtimes={runtimes}
          variants={variants}
        />)
          : <div className="data-panel empty"><BadgeCheck size={26} /><strong>No recommended profiles</strong><span>This build does not include any recommended profiles.</span></div>}
  </div>
}
