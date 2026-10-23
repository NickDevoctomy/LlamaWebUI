import { Activity, ArrowUpRight, Check, ChevronLeft, Copy, Cpu, Download, FolderCog, KeyRound, Library, MemoryStick, TerminalSquare } from 'lucide-react'
import { Fragment, useEffect, useRef, useState } from 'react'
import type { AccessToken, DownloadJob, LibraryModel, ModelContext, ModelMemory, Profile, RouterModel, Runtime, ServerStatus, StoredArgument } from './api'

const HISTORY_LENGTH = 60

export interface StatusSample {
  cpu: number | null
  ram: number | null
  gpu: number | null
  vram: number | null
  decode: number | null
  rollingPeak: number | null
}

function percent(used: number | undefined, total: number | undefined) {
  return used != null && total ? Math.round(used / total * 100) : null
}

function numeric(value: unknown) {
  const parsed = typeof value === 'string' ? Number(value) : value
  return typeof parsed === 'number' && Number.isFinite(parsed) ? parsed : null
}

/** Keeps a rolling window of status samples so the dashboard can draw trends. */
export function useStatusHistory(status: ServerStatus | undefined, updatedAt: number) {
  const [history, setHistory] = useState<StatusSample[]>([])
  const lastUpdate = useRef(0)
  useEffect(() => {
    if (!status || updatedAt === lastUpdate.current) return
    lastUpdate.current = updatedAt
    const system = status.system
    const sample: StatusSample = {
      cpu: system?.cpu_percent ?? null,
      ram: percent(system?.ram_used_bytes, system?.ram_total_bytes),
      gpu: system?.gpu?.utilization_percent ?? null,
      vram: percent(system?.gpu?.memory_used_bytes, system?.gpu?.memory_total_bytes),
      decode: numeric(status.timing?.decode_tokens_per_second),
      rollingPeak: numeric(status.timing?.decode_tokens_per_second_rolling_peak),
    }
    setHistory((current) => [...current.slice(1 - HISTORY_LENGTH), sample])
  }, [status, updatedAt])
  return history
}

export function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`
}

function formatRate(value: number | null) {
  if (value == null) return '—'
  return value >= 100 ? Math.round(value).toString() : value.toFixed(1)
}

function stateLabel(value?: string) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : 'Unknown'
}

function plotted(values: (number | null)[]) {
  return values.map((value, index) => [index, value] as const).filter((point): point is readonly [number, number] => point[1] != null)
}

/**
 * Trend line with an optional second series drawn over it and an optional
 * horizontal ceiling, painted last so it stays visible where the two meet.
 */
export function Sparkline({ values, max, overlay, ceiling }: { values: (number | null)[]; max?: number; overlay?: (number | null)[]; ceiling?: number | null }) {
  const points = plotted(values)
  const overlayPoints = overlay ? plotted(overlay) : []
  if (points.length < 2 && overlayPoints.length < 2) return <div aria-hidden="true" className="sparkline empty-spark" />
  const top = max ?? Math.max(...points.map(([, value]) => value), ...overlayPoints.map(([, value]) => value), ceiling ?? 0, 1)
  const span = Math.max(values.length - 1, (overlay?.length ?? 0) - 1, 1)
  const x = (index: number) => index / span * 100
  const y = (value: number) => 30 - Math.min(value / top, 1) * 28
  const path = (series: (readonly [number, number])[]) => series.map(([index, value]) => `${x(index).toFixed(2)},${y(value).toFixed(2)}`).join(' ')
  const line = path(points)
  const last = points.at(-1)
  return (
    <div aria-hidden="true" className="sparkline">
      <svg preserveAspectRatio="none" viewBox="0 0 100 32">
        {points.length >= 2 && <>
          <polygon className="spark-area" points={`${x(points[0][0]).toFixed(2)},32 ${line} ${x(points[points.length - 1][0]).toFixed(2)},32`} />
          <polyline className="spark-line" points={line} vectorEffect="non-scaling-stroke" />
        </>}
        {overlayPoints.length >= 2 && <polyline className="spark-overlay" points={path(overlayPoints)} vectorEffect="non-scaling-stroke" />}
        {ceiling != null && ceiling > 0 && <line className="spark-ceiling" vectorEffect="non-scaling-stroke" x1="0" x2="100" y1={y(ceiling).toFixed(2)} y2={y(ceiling).toFixed(2)} />}
      </svg>
      {points.length >= 2 && last && <span className="spark-end" style={{ left: `${x(last[0])}%`, top: `${y(last[1]) / 32 * 100}%` }} />}
    </div>
  )
}

const DRAFT_FLAGS = new Set(['--spec-draft-model', '--model-draft', '-md'])

/** File name of the speculative-decoding draft model a profile loads alongside its main model. */
export function draftModel(profile: Profile) {
  const stored = Array.isArray(profile.configuration.arguments) ? profile.configuration.arguments as StoredArgument[] : []
  for (const argument of stored) {
    const value = 'id' in argument
      ? argument.id === 'spec-draft-model' ? argument.value : null
      : DRAFT_FLAGS.has(argument.flag) ? argument.value : null
    if (typeof value === 'string' && value.trim()) return value.trim().split(/[\\/]/).at(-1) ?? null
  }
  return null
}

type MemoryLocation = 'vram' | 'ram' | 'mapped'

const MEMORY_LOCATIONS: { id: MemoryLocation; label: string }[] = [
  { id: 'vram', label: 'VRAM' },
  { id: 'ram', label: 'RAM' },
  { id: 'mapped', label: 'Mapped from disk' },
]
const MEMORY_KINDS: { location: MemoryLocation; kind: string; label: string; bytes: (entry: ModelMemory) => number }[] = [
  { location: 'vram', kind: 'dedicated', label: 'Dedicated', bytes: (entry) => entry.vram_bytes ?? 0 },
  { location: 'ram', kind: 'pinned', label: 'Shared with GPU', bytes: (entry) => Math.min(entry.gpu_shared_bytes ?? 0, entry.ram_bytes) },
  { location: 'ram', kind: 'private', label: 'Process', bytes: (entry) => entry.ram_bytes - Math.min(entry.gpu_shared_bytes ?? 0, entry.ram_bytes) },
  { location: 'mapped', kind: 'files', label: 'Model files', bytes: (entry) => entry.mapped_bytes },
]

function MemoryPanel({ memory }: { memory: ModelMemory[] }) {
  const segments = MEMORY_KINDS.map(({ location, kind, label, bytes }) => {
    const parts = memory.map((entry) => ({ model: entry.model, bytes: bytes(entry) })).filter((part) => part.bytes > 0)
    return { location: MEMORY_LOCATIONS.find((item) => item.id === location)!, kind: { id: kind, label }, parts, bytes: parts.reduce((total, part) => total + part.bytes, 0) }
  }).filter((segment) => segment.bytes > 0)
  const total = segments.reduce((sum, segment) => sum + segment.bytes, 0)
  const groups = MEMORY_LOCATIONS
    .map((location) => ({ location, segments: segments.filter((segment) => segment.location.id === location.id) }))
    .filter((group) => group.segments.length)
    .map((group) => ({ ...group, bytes: group.segments.reduce((sum, segment) => sum + segment.bytes, 0) }))
  const describe = (segment: (typeof segments)[number]) => [
    `${segment.location.label} · ${segment.kind.label}: ${formatBytes(segment.bytes)}`,
    ...segment.parts.map((part) => `${part.model}: ${formatBytes(part.bytes)}`),
  ].join('\n')
  return (
    <section className="data-panel memory-panel">
      <div className="panel-heading"><div><h2>Model memory</h2><p>Measured from each model process. A draft model shares its main model&apos;s process.</p></div>{total ? <strong className="memory-total">{formatBytes(total)}</strong> : <MemoryStick size={18} />}</div>
      {total ? <div className="memory-body">
        <div aria-label={groups.map((group) => `${group.location.label} ${formatBytes(group.bytes)}`).join(', ')} className="memory-bar" role="img">
          {segments.map((segment) => <span data-kind={segment.kind.id} data-location={segment.location.id} key={`${segment.location.id}-${segment.kind.id}`} style={{ flexGrow: segment.bytes }} title={describe(segment)} />)}
        </div>
        <div className="memory-legend">
          {groups.map(({ location, segments: items, bytes }) => <div className="memory-group" key={location.id}>
            <strong>{location.label}<span>{formatBytes(bytes)}</span></strong>
            <ul>{items.map((segment) => <li key={segment.kind.id} title={describe(segment)}><i data-kind={segment.kind.id} data-location={location.id} />{segment.kind.label}<span>{formatBytes(segment.bytes)}</span></li>)}</ul>
          </div>)}
        </div>
      </div> : <div className="empty compact-empty"><MemoryStick size={24} /><strong>No model memory reported</strong><span>The split appears once a model has loaded.</span></div>}
    </section>
  )
}

function ContextUsage({ entries }: { entries: ModelContext[] }) {
  const [expanded, setExpanded] = useState(false)
  const several = entries.length > 1
  const rows = entries.flatMap((entry) => entry.slots.map((slot) => ({
    key: `${entry.model ?? ''}-${slot.id}`,
    label: several ? `${entry.model ?? 'router'} · slot ${slot.id}` : `Slot ${slot.id}`,
    used: slot.used,
    total: entry.shared_capacity ?? slot.total,
  })))
  const used = rows.reduce((sum, row) => sum + row.used, 0)
  const total = entries.reduce((sum, entry) => sum + (entry.shared_capacity ?? entry.slots.reduce((slots, slot) => slots + (slot.total ?? 0), 0)), 0) || null
  const fill = (value: number, capacity: number | null) => capacity ? Math.min(100, value / capacity * 100) : 0
  const amount = (value: number, capacity: number | null) => <>{value.toLocaleString()}<small>/ {capacity ? capacity.toLocaleString() : '—'} tokens</small></>
  if (!rows.length) return <div className="context-usage"><dt>Context</dt><dd><span className="context-amount">—<small>tokens</small></span></dd></div>
  if (expanded) return (
    <div className="context-usage expanded">
      <dt>Context<button className="button secondary compact" onClick={() => setExpanded(false)} type="button"><ChevronLeft size={13} /> Total</button></dt>
      <dd>
        <ul className="context-slots">
          {rows.map((row) => <li key={row.key}>
            <span className="context-slot-label">{row.label}</span>
            <span className="context-slot-value">{amount(row.used, row.total)}</span>
            <span className="context-bar"><span style={{ width: `${fill(row.used, row.total)}%` }} /></span>
          </li>)}
        </ul>
      </dd>
    </div>
  )
  return (
    <div className="context-usage">
      <dt>Context</dt>
      <dd>
        <button aria-label="Show context breakdown" className="context-toggle" onClick={() => setExpanded(true)} title="Show per-slot breakdown" type="button">
          <span className="context-amount">{amount(used, total)}</span>
          <span className="context-bar"><span style={{ width: `${fill(used, total)}%` }} /></span>
        </button>
      </dd>
    </div>
  )
}

function CopyValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      aria-label={copied ? 'Endpoint copied' : 'Copy endpoint'}
      className="icon-button small"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true)
          window.setTimeout(() => setCopied(false), 1500)
        }).catch(() => undefined)
      }}
      title="Copy endpoint"
      type="button"
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  )
}

function Meter({ label, value, detail, trend }: { label: string; value: number | null; detail?: string; trend?: (number | null)[] }) {
  const level = value == null ? 'idle' : value >= 90 ? 'critical' : value >= 75 ? 'warn' : 'normal'
  return (
    <div className="meter" data-level={level}>
      <div className="meter-head">
        <span className="meter-label">{label}</span>
        <strong className="meter-value">{value == null ? '—' : `${Math.round(value)}%`}</strong>
      </div>
      <div className="meter-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}>
        <span style={{ width: `${Math.min(100, Math.max(0, value ?? 0))}%` }} />
      </div>
      <div className="meter-foot">
        <span>{detail ?? (value == null ? 'Unavailable' : ' ')}</span>
        {trend && <Sparkline max={100} values={trend} />}
      </div>
    </div>
  )
}

export function DashboardPanel({ status, history, runtime, runtimes, profiles, models, downloads, library, tokens, running, onNavigate }: {
  status?: ServerStatus
  history: StatusSample[]
  runtime?: Runtime
  runtimes: Runtime[]
  profiles: Profile[]
  models: RouterModel[]
  downloads: DownloadJob[]
  library: LibraryModel[]
  tokens: AccessToken[]
  running: boolean
  onNavigate?: (section: string) => void
}) {
  const state = status?.state ?? 'unknown'
  const system = status?.system
  const timing = status?.timing ?? {}
  const decode = numeric(timing.decode_tokens_per_second)
  const prompt = numeric(timing.prompt_tokens_per_second)
  const rollingPeak = numeric(timing.decode_tokens_per_second_rolling_peak)
  const sessionPeak = numeric(timing.decode_tokens_per_second_session_peak)
  const liveStatus = (alias: string) => models.find((model) => model.id === alias)?.status.value
  const activeModels = models.filter((model) => ['loaded', 'loading'].includes(model.status.value ?? ''))
  const loadedDrafts = profiles.filter((profile) => draftModel(profile) && liveStatus(profile.alias) === 'loaded').length
  const loaded = models.filter((model) => model.status.value === 'loaded').length + loadedDrafts
  // Only models the router still holds; a stopped router reports nothing live.
  const resident = (name: string | null) => name == null || ['loaded', 'loading'].includes(liveStatus(name) ?? '')
  const memory = running ? status?.memory ?? [] : []
  const context = running ? (status?.context ?? []).filter((entry) => resident(entry.model)) : []
  const activeDownloads = downloads.filter((job) => ['queued', 'downloading', 'paused'].includes(job.state))
  const enabledProfiles = profiles.filter((profile) => profile.enabled).length
  const libraryBytes = library.reduce((total, model) => total + model.total_bytes, 0)
  const recentLogs = status?.logs.slice(-6) ?? []
  const series = (key: keyof StatusSample) => history.map((sample) => sample[key])
  const heroSummary = running
    ? `Serving ${loaded} loaded ${loaded === 1 ? 'model' : 'models'}${loadedDrafts ? ` (${loadedDrafts} ${loadedDrafts === 1 ? 'draft' : 'drafts'})` : ''} from ${enabledProfiles} enabled ${enabledProfiles === 1 ? 'profile' : 'profiles'}.`
    : state === 'crashed'
      ? 'The router exited unexpectedly. Check the recent output below, then start it again.'
      : runtime
        ? `Ready to start on ${runtime.name} with ${enabledProfiles} enabled ${enabledProfiles === 1 ? 'profile' : 'profiles'}.`
        : 'Register a runtime to start serving models.'

  const shortcuts = [
    { section: 'Profiles', icon: FolderCog, label: 'Profiles', value: profiles.length, detail: `${enabledProfiles} enabled` },
    { section: 'Models', icon: Library, label: 'Library', value: library.length, detail: library.length ? `${formatBytes(libraryBytes)} on disk` : 'No models yet' },
    { section: 'Runtimes', icon: Cpu, label: 'Runtimes', value: runtimes.length, detail: `${runtimes.filter((item) => item.usable).length} ready` },
    { section: 'Access', icon: KeyRound, label: 'Access keys', value: tokens.filter((token) => token.enabled).length, detail: 'Active keys' },
  ]

  return (
    <div className="dashboard">
      <section className="data-panel router-hero" data-state={state}>
        <div className="hero-main">
          <p className="eyebrow">Operational overview</p>
          <div className="hero-state">
            <span className={`state-dot large ${state}`} />
            <h2>Router {stateLabel(status?.state).toLowerCase()}</h2>
          </div>
          <p className="hero-summary">{heroSummary}</p>
          {running && <ul aria-label="Active models" className="hero-models">
            {activeModels.length ? activeModels.map((model) => {
              const loading = model.status.value === 'loading'
              return <li data-state={loading ? 'loading' : 'loaded'} key={model.id}>
                <span className={`state-dot ${loading ? 'loading' : 'ready'}`} />
                <strong>{model.id}</strong>
                <span>{loading ? 'Loading…' : 'Loaded'}</span>
              </li>
            }) : <li className="hero-models-empty">No model loaded. The first request for a model loads it.</li>}
          </ul>}
          <dl className="hero-facts">
            <div><dt>Endpoint</dt><dd><span className="mono">{status?.endpoint ?? 'Unavailable'}</span>{status?.endpoint && <CopyValue value={status.endpoint} />}</dd></div>
            <div><dt>Runtime</dt><dd>{runtime ? <>{runtime.name}<span className="hero-fact-note">{[runtime.build, runtime.backend?.toUpperCase()].filter(Boolean).join(' · ')}</span></> : 'None selected'}</dd></div>
            <div><dt>Process</dt><dd className="mono">{status?.pid ? `PID ${status.pid}` : status?.last_exit_code != null ? `Exited ${status.last_exit_code}` : '—'}</dd></div>
          </dl>
        </div>
        <div className="hero-throughput">
          <div className="throughput-primary">
            <span className="throughput-label">Decode</span>
            <strong>{formatRate(decode)}<small>tok/s</small></strong>
            <Sparkline ceiling={sessionPeak} overlay={series('rollingPeak')} values={series('decode')} />
          </div>
          <dl className="throughput-grid">
            <div><dt>Prompt</dt><dd>{formatRate(prompt)}<small>tok/s</small></dd></div>
            <div title="Average over the last 10 seconds, with the peak since the router started in brackets"><dt>Peak decode</dt><dd>{formatRate(rollingPeak)}{sessionPeak != null && <span className="session-peak">({formatRate(sessionPeak)})</span>}<small>tok/s</small></dd></div>
            <div><dt>Loaded</dt><dd>{loaded}<small>{loaded === 1 ? 'model' : 'models'}{loadedDrafts ? ` · ${loadedDrafts} ${loadedDrafts === 1 ? 'draft' : 'drafts'}` : ''}</small></dd></div>
            <ContextUsage entries={context} />
          </dl>
        </div>
      </section>

      <MemoryPanel memory={memory} />

      <div className="shortcut-grid">
        {shortcuts.map(({ section, icon: Icon, label, value, detail }) => {
          const content = <>
            <span className="shortcut-icon"><Icon size={17} /></span>
            <span className="shortcut-copy"><small>{label}</small><strong>{value}</strong><span>{detail}</span></span>
            {onNavigate && <ArrowUpRight className="shortcut-arrow" size={16} />}
          </>
          return onNavigate
            ? <button aria-label={`Open ${label}`} className="shortcut" key={section} onClick={() => onNavigate(section)} type="button">{content}</button>
            : <div className="shortcut" key={section}>{content}</div>
        })}
      </div>

      <section className="data-panel resources-panel">
        <div className="panel-heading"><div><h2>Host resources</h2><p>Live snapshot of this machine, with recent trend.</p></div><Activity size={18} /></div>
        <div className="meter-grid">
          <Meter label="CPU" trend={series('cpu')} value={system?.cpu_percent ?? null} />
          <Meter detail={system ? `${formatBytes(system.ram_used_bytes)} of ${formatBytes(system.ram_total_bytes)}` : undefined} label="Memory" trend={series('ram')} value={percent(system?.ram_used_bytes, system?.ram_total_bytes)} />
          <Meter detail={system?.gpu ? undefined : system?.gpu_supported === false ? 'No supported GPU' : undefined} label="GPU" trend={system?.gpu ? series('gpu') : undefined} value={system?.gpu?.utilization_percent ?? null} />
          <Meter detail={system?.gpu ? `${formatBytes(system.gpu.memory_used_bytes)} of ${formatBytes(system.gpu.memory_total_bytes)}` : system?.gpu_supported === false ? 'No supported GPU' : undefined} label="VRAM" trend={system?.gpu ? series('vram') : undefined} value={percent(system?.gpu?.memory_used_bytes, system?.gpu?.memory_total_bytes)} />
          <Meter detail={system ? `${formatBytes(system.disk_free_bytes)} free` : undefined} label="Model disk" value={system ? Math.round((1 - system.disk_free_bytes / system.disk_total_bytes) * 100) : null} />
        </div>
        <dl className="io-strip">
          <div><dt>Network in</dt><dd>{system ? formatBytes(system.network_received_bytes) : '—'}</dd></div>
          <div><dt>Network out</dt><dd>{system ? formatBytes(system.network_sent_bytes) : '—'}</dd></div>
          <div><dt>Disk read</dt><dd>{system?.disk_read_bytes != null ? formatBytes(system.disk_read_bytes) : '—'}</dd></div>
          <div><dt>Disk write</dt><dd>{system?.disk_write_bytes != null ? formatBytes(system.disk_write_bytes) : '—'}</dd></div>
        </dl>
      </section>

      <section className="data-panel roster-panel">
        <div className="panel-heading"><div><h2>Models</h2><p>{running ? `${loaded - loadedDrafts} of ${models.length || profiles.length} loaded in the router` : 'Profiles published when the router starts'}</p></div>{onNavigate && <button className="button secondary compact" onClick={() => onNavigate('Profiles')} type="button">Manage</button>}</div>
        {profiles.length ? <ul className="roster">
          {profiles.slice(0, 8).map((profile) => {
            const live = liveStatus(profile.alias)
            const value = profile.validation_state === 'broken' ? 'broken' : profile.validation_state === 'pending' ? 'downloading' : live ?? (profile.enabled ? 'available' : 'disabled')
            const size = profile.configuration.model_size
            const draft = draftModel(profile)
            return <Fragment key={profile.id}>
              <li>
                <span className={`roster-dot ${value}`} />
                <span className="roster-copy"><strong>{profile.alias}</strong><span>{typeof size === 'number' ? formatBytes(size) : profile.model_path.split(/[\\/]/).at(-1)}</span></span>
                <span className={`state-pill ${value === 'broken' ? 'error' : value}`}>{value}</span>
              </li>
              {draft && <li className="roster-draft">
                <span className={`roster-dot ${value}`} />
                <span className="roster-copy"><strong>{draft}</strong><span>Draft model for {profile.alias}</span></span>
                <span className={`state-pill ${value === 'broken' ? 'error' : value}`}>{value}</span>
              </li>}
            </Fragment>
          })}
        </ul> : <div className="empty compact-empty"><FolderCog size={24} /><strong>No model profiles yet</strong><span>Create a profile to publish a model to the router.</span></div>}
        {profiles.length > 8 && <div className="panel-footer"><span>{profiles.length - 8} more profiles</span></div>}
      </section>

      <section className="data-panel activity-panel">
        <div className="panel-heading"><div><h2>Activity</h2><p>Downloads in progress and the latest router output.</p></div>{onNavigate && <button className="button secondary compact" onClick={() => onNavigate('Server')} type="button"><TerminalSquare size={14} /> Full output</button>}</div>
        {activeDownloads.length > 0 && <ul className="activity-downloads">
          {activeDownloads.slice(0, 4).map((job) => {
            const value = job.total_bytes ? Math.min(100, Math.round(job.completed_bytes / job.total_bytes * 100)) : 0
            return <li key={job.id}>
              <Download size={15} />
              <span className="activity-copy"><strong>{job.repo_id}</strong><span>{job.group_key} · {formatBytes(job.completed_bytes)} of {formatBytes(job.total_bytes)}</span></span>
              <span className="activity-progress"><span style={{ width: `${value}%` }} /></span>
              <span className="activity-percent">{value}%</span>
            </li>
          })}
        </ul>}
        <pre className="log-tail">{recentLogs.length ? recentLogs.join('\n') : running ? 'Waiting for router output…' : 'The router is not running. Output appears here once it starts.'}</pre>
      </section>
    </div>
  )
}
