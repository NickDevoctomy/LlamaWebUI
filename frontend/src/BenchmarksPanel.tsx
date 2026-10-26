import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, ChevronDown, ChevronRight, Code, Copy, Download, Eye, FileText, LoaderCircle, Maximize2, Play, RotateCcw, Square, SquareCheck, SquareX, Timer, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type BenchmarkRun, type BenchmarkRunDetail, type BenchmarkStage, type BenchmarkTask, type BenchmarkTool, type Profile, type RouterState, type TimelineEntry } from './api'
import { formatRate, formatSeconds, formatWhen, reasoningLabel } from './benchmarkFormat'
import { ContextChart, MemoryChart } from './BenchmarkCharts'
import { Sparkline } from './Dashboard'
import { Dialog } from './SetupPanels'

const STEPS: { stage: BenchmarkStage; label: string }[] = [
  { stage: 'starting', label: 'Start server' },
  { stage: 'loading', label: 'Load model' },
  { stage: 'prefill', label: 'Process prompt' },
  { stage: 'decoding', label: 'Generate' },
  { stage: 'stopping', label: 'Stop server' },
]
const ORDER: BenchmarkStage[] = ['starting', 'loading', 'prefill', 'decoding', 'stopping', 'done']

function stepState(run: BenchmarkRun, step: BenchmarkStage) {
  const reached = ORDER.indexOf(run.stage)
  const index = ORDER.indexOf(step)
  if (run.state === 'running') return index < reached ? 'done' : index === reached ? 'active' : 'pending'
  if (step === 'stopping') return 'done'
  if (run.state === 'completed') return 'done'
  return index < reached ? 'done' : index === reached ? 'failed' : 'pending'
}

function stepFigure(run: BenchmarkRun, step: BenchmarkStage) {
  const { stats, live } = run
  if (step === 'starting') return formatSeconds(stats.server_start_seconds)
  if (step === 'loading') return formatSeconds(stats.load_seconds)
  if (step === 'prefill') {
    const rate = stats.prompt_tokens_per_second ?? live?.prefill?.rate
    const tokens = stats.prompt_tokens ?? live?.prefill?.tokens
    return rate != null ? `${formatRate(rate)}${tokens != null ? ` · ${tokens.toLocaleString()} tokens` : ''}` : '—'
  }
  if (step === 'decoding') {
    const rate = stats.decode_tokens_per_second ?? live?.decode?.rate
    const tokens = stats.generated_tokens ?? live?.decode?.tokens
    return rate != null ? `${formatRate(rate)}${tokens != null ? ` · ${tokens.toLocaleString()} tokens` : ''}` : '—'
  }
  return ''
}

/** A span of time as people say it: 4 ms, 12.3 s, 3m 10s. */
function formatSpan(seconds: number) {
  if (seconds < 1) return `${Math.max(Math.round(seconds * 1000), 1)} ms`
  if (seconds < 60) return formatSeconds(seconds)
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${Math.floor(seconds - minutes * 60)}s`
}

/** How long a step took, or has taken so far while it is still going; empty if unknown. */
function formatDuration(entry: TimelineEntry, elapsed?: number | null) {
  if (entry.end != null) return formatSpan(entry.end - entry.start)
  return elapsed != null ? formatSpan(Math.max(elapsed - entry.start, 0)) : ''
}

/** What a running benchmark is doing right now, such as "Thinking 3m 10s". */
function currentActivity(run: BenchmarkRunDetail) {
  const entry = run.state === 'running' ? run.timeline?.at(-1) : undefined
  if (!entry || entry.end != null) return undefined
  const writing = entry.kind === 'tool_call' ? liveToolCall(run) : ''
  const doing = `${activityName(entry)}${writing ? ` (${writing})` : ''}`
  const soFar = formatDuration(entry, run.live?.elapsed)
  return soFar ? `${doing}… ${soFar}` : `${doing}…`
}

/** The tool call being streamed, such as "write_file, 12.4 KB", so a long write visibly advances. */
function liveToolCall(run: BenchmarkRunDetail) {
  const call = run.live?.tool_call
  if (!call) return ''
  return [call.name, formatSize(call.bytes)].filter(Boolean).join(', ')
}

/** Why generation ended, in words; the token limit and the context are told apart. */
function finishLabel(run: BenchmarkRun) {
  switch (run.stats.finish_reason) {
    case undefined: return undefined
    case 'length': return run.max_tokens != null ? 'Hit the token limit' : 'Ran out of context'
    case 'context': return 'Ran out of context'
    case 'tool_round_limit': return 'Hit the tool round limit'
    case 'finish_job': return 'Agent called finish_job'
    case 'task_complete': return 'Agent called task_complete'
    default: return `Finished: ${run.stats.finish_reason}`
  }
}

/** Whether a tool-using agent said it finished the task; runs from before outcomes were kept show none. */
function OutcomeTag({ run }: { run: BenchmarkRun }) {
  if (!run.stats.outcome || run.state === 'running' || run.state === 'cancelled') return null
  const completed = run.stats.outcome === 'completed'
  return <span className={`state-pill ${completed ? 'ready' : 'warning'}`} title={completed ? 'The agent called finish_job.' : 'The agent never finished the job: it stopped, ran out of tokens or context, or failed.'}>{completed ? 'Job finished' : 'Unfinished'}</span>
}

function activityName(entry: TimelineEntry) {
  switch (entry.kind) {
    case 'reading': return entry.round > 1 ? 'Reading tool results' : 'Reading prompt'
    case 'thinking': return 'Thinking'
    case 'answer': return 'Writing answer'
    case 'tool_call': return 'Writing tool call'
    case 'tool': return `Running ${entry.name}`
  }
}

function tokenCount(tokens?: number) {
  return tokens ? ` · ${tokens.toLocaleString()} ${tokens === 1 ? 'token' : 'tokens'}` : ''
}

/** What generation spent its time on: reading, thinking, answering and each tool call. */
function GenerationTimeline({ run }: { run: BenchmarkRunDetail }) {
  const rounds = Math.max(1, ...run.timeline.map((entry) => entry.round))
  return <ol aria-label="Generation steps" className="generation-timeline">
    {run.timeline.flatMap((entry, index) => {
      // With several rounds, each one gets a heading row where it starts.
      const starts = rounds > 1 && (index === 0 || run.timeline[index - 1].round !== entry.round)
      const heading = starts ? [<li className="timeline-round" key={`round-${entry.round}`}>Round {entry.round}</li>] : []
      return [...heading, timelineRow(run, entry, index)]
    })}
  </ol>
}

function timelineRow(run: BenchmarkRunDetail, entry: TimelineEntry, index: number) {
  const expandable = entry.kind !== 'reading' && entry.kind !== 'tool_call'
  const head = (label: React.ReactNode, detail = '') => <>
    <span className="timeline-label">{expandable ? <ChevronRight aria-hidden="true" className="timeline-chevron" size={13} /> : <span className="timeline-chevron" />}<span className="timeline-body"><span className="timeline-name">{label}{entry.end == null ? '…' : ''}</span><small>{detail}</small></span></span>
    <span className="timeline-time">{formatDuration(entry, run.live?.elapsed)}</span>
  </>
  if (entry.kind === 'thinking' || entry.kind === 'answer') {
    const source = entry.kind === 'thinking' ? run.reasoning : run.output
    const text = source.slice(entry.from, entry.to ?? source.length).trim()
    return <li data-kind={entry.kind} key={index}>
      <details>
        <summary>{head(entry.kind === 'thinking' ? 'Thinking' : 'Answer', `${text.length.toLocaleString()} characters${tokenCount(entry.tokens)}`)}</summary>
        <pre>{text || '(nothing yet)'}</pre>
      </details>
    </li>
  }
  if (entry.kind === 'tool') {
    const call = run.tool_calls[entry.call]
    return <li data-kind="tool" data-ok={entry.ok ?? call?.ok} key={index}>
      <details>
        <summary>{head(<>Ran <code>{entry.name}</code>{entry.path ? <code>{entry.path}</code> : null}</>, entry.ok === false ? 'refused' : entry.end == null ? '' : 'done')}</summary>
        <pre>{call?.result ?? 'Running…'}</pre>
      </details>
    </li>
  }
  if (entry.kind === 'tool_call') {
    // While it streams the call has no names yet; the live figures stand in for them.
    const live = entry.end == null ? run.live?.tool_call : undefined
    const names = entry.names?.join(', ') ?? live?.name
    const bytes = entry.bytes ?? live?.bytes
    const detail = [bytes != null ? formatSize(bytes) : '', tokenCount(entry.tokens).replace(/^ · /, '')].filter(Boolean).join(' · ')
    return <li data-kind="tool_call" key={index}><div className="timeline-row">{head(`Writing tool ${entry.names && entry.names.length > 1 ? 'calls' : 'call'}${names ? `: ${names}` : ''}`, detail)}</div></li>
  }
  const read = entry.kind === 'reading' ? entry.prompt_tokens : undefined
  return <li data-kind="reading" key={index}><div className="timeline-row">{head(activityName(entry), read ? `${read.toLocaleString()} tokens` : '')}</div></li>
}

/** The run's steps; with the run's detail, Generate opens into what it spent its time on. */
function RunSteps({ run, detail }: { run: BenchmarkRun; detail?: BenchmarkRunDetail }) {
  const [open, setOpen] = useState(false)
  const activity = detail ? currentActivity(detail) : undefined
  return <ol className="benchmark-steps">
    {STEPS.map(({ stage, label }) => {
      const state = stepState(run, stage)
      const expandable = stage === 'decoding' && Boolean(detail?.timeline?.length)
      return <li data-state={state} key={stage}>
        <span className="benchmark-step-icon" aria-hidden="true">{state === 'done' ? <Check size={13} /> : state === 'active' ? <LoaderCircle className="spin" size={13} /> : state === 'failed' ? <X size={13} /> : null}</span>
        {expandable
          ? <button aria-expanded={open} className="benchmark-step-label benchmark-step-toggle" onClick={() => setOpen(!open)} type="button">{label}{!open && activity && <span className="benchmark-step-activity">({activity})</span>}<ChevronDown aria-hidden="true" size={14} /></button>
          : <span className="benchmark-step-label">{label}</span>}
        <span className="benchmark-step-figure">{stepFigure(run, stage)}</span>
        {expandable && open && detail && <div className="benchmark-step-detail"><GenerationTimeline run={detail} /></div>}
      </li>
    })}
  </ol>
}

function StatePill({ run }: { run: BenchmarkRun }) {
  const tone = run.state === 'completed' ? 'ready' : run.state === 'failed' ? 'error' : run.state === 'running' ? 'loading' : ''
  return <span className={`state-pill ${tone}`}>{run.state}</span>
}

function RunDetail({ runId, onClose }: { runId: string; onClose: () => void }) {
  const run = useQuery({
    queryKey: ['benchmark-run', runId],
    queryFn: () => api.benchmarkRun(runId),
    refetchInterval: (query) => query.state.data?.state === 'running' ? 1000 : false,
  })
  const data = run.data
  const [artifact, setArtifact] = useState<string>()
  return <>
    <Dialog description={data ? `${data.profile_alias} v${data.profile_version} · ${formatWhen(data.started_at)}` : 'Loading the run…'} onClose={onClose} title={data ? `${data.benchmark_title} benchmark` : 'Benchmark run'} wide>
      <div className="form-body benchmark-detail">
        {!data ? <div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading the run…</span></div> : <RunDetailBody onOpenArtifact={setArtifact} run={data} />}
      </div>
      <footer className="dialog-actions"><button className="button secondary" onClick={onClose} type="button">Close</button></footer>
    </Dialog>
    {artifact && <ArtifactViewer files={data?.artifacts.map((file) => file.path) ?? []} onClose={() => setArtifact(undefined)} path={artifact} runId={runId} />}
  </>
}

function formatSize(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Whether a reference in a page is a relative path rather than a URL with a scheme or host. */
function isRelative(ref: string) {
  return !/^([a-z][a-z\d+.-]*:|\/\/|#)/i.test(ref)
}

/**
 * Storage throws in a frame with an opaque origin, and generated games often keep a high
 * score there, so the preview gets an in-memory stand-in that lasts until it reloads.
 */
const PREVIEW_STORAGE = `for (const name of ['localStorage', 'sessionStorage']) {
  try { window[name] } catch {
    const data = new Map()
    const store = {
      get length() { return data.size },
      key: (index) => [...data.keys()][index] ?? null,
      getItem: (key) => data.has(String(key)) ? data.get(String(key)) : null,
      setItem: (key, value) => { data.set(String(key), String(value)) },
      removeItem: (key) => { data.delete(String(key)) },
      clear: () => { data.clear() },
    }
    Object.defineProperty(window, name, { value: store, configurable: true })
  }
}`

/**
 * The page ready to preview. A preview has no folder of its own, so scripts and stylesheets it
 * links from the run's files are embedded as data URLs, which keeps attributes such as defer
 * and type="module" working as written. References to anything else are left alone.
 */
async function previewPage(html: string, path: string, files: string[], read: (path: string) => Promise<string>) {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const storage = doc.createElement('script')
  storage.textContent = PREVIEW_STORAGE
  doc.head.prepend(storage)
  const base = new URL(path, 'https://run.invalid/')
  const local = (ref: string | null) => {
    if (!ref || !isRelative(ref)) return undefined
    const url = new URL(ref, base)
    const file = decodeURIComponent(url.pathname.slice(1))
    return url.origin === base.origin && files.includes(file) ? file : undefined
  }
  const embed = async (element: Element, attribute: string, type: string) => {
    const file = local(element.getAttribute(attribute))
    if (file) element.setAttribute(attribute, `data:${type};charset=utf-8,${encodeURIComponent(await read(file))}`)
  }
  for (const script of Array.from(doc.querySelectorAll('script[src]'))) await embed(script, 'src', 'text/javascript')
  for (const link of Array.from(doc.querySelectorAll('link[rel~="stylesheet" i][href]'))) await embed(link, 'href', 'text/css')
  return `${doc.doctype ? '<!DOCTYPE html>\n' : ''}${doc.documentElement.outerHTML}`
}

/**
 * A file the model wrote, shown as read-only text. HTML files can also be previewed in a
 * sandboxed frame: scripts run, but with an opaque origin, so the page cannot reach this app.
 */
function ArtifactViewer({ runId, path, files, onClose }: { runId: string; path: string; files: string[]; onClose: () => void }) {
  const file = useQuery({ queryKey: ['benchmark-artifact', runId, path], queryFn: () => api.benchmarkArtifact(runId, path) })
  const [wrap, setWrap] = useState(false)
  const [copied, setCopied] = useState(false)
  const [tab, setTab] = useState<'source' | 'preview'>('source')
  const [reloads, setReloads] = useState(0)
  const frame = useRef<HTMLIFrameElement>(null)
  const html = /\.html?$/i.test(path)
  const previewing = html && tab === 'preview'
  const content = file.data?.content ?? ''
  const page = useQuery({
    queryKey: ['benchmark-artifact-preview', runId, path, content],
    queryFn: () => previewPage(content, path, files, async (other) => (await api.benchmarkArtifact(runId, other)).content),
    enabled: previewing && Boolean(file.data),
  })
  const lines = content ? content.split('\n').length : 0
  function download() {
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain' }))
    const link = document.createElement('a')
    link.href = url
    link.download = path.split('/').at(-1) || 'artifact.txt'
    link.click()
    URL.revokeObjectURL(url)
  }
  return <Dialog description={file.data ? `${lines.toLocaleString()} lines · read-only` : 'Loading the file…'} fill={previewing} onClose={onClose} title={path} wide>
    <div className="form-body artifact-viewer">
      {file.error ? <div className="form-error" role="alert"><AlertCircle size={15} /> {file.error.message}</div>
        : !file.data ? <div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading the file…</span></div>
          : <>
            <div className="artifact-toolbar">
              {html && <div aria-label="View" className="segmented artifact-tabs" role="tablist">
                <button aria-selected={tab === 'source'} onClick={() => setTab('source')} role="tab" type="button"><Code size={14} /> Source</button>
                <button aria-selected={tab === 'preview'} onClick={() => setTab('preview')} role="tab" type="button"><Eye size={14} /> Preview</button>
              </div>}
              {previewing
                ? <span className="artifact-preview-actions">
                  <button className="button secondary compact" onClick={() => setReloads(reloads + 1)} type="button"><RotateCcw size={14} /> Reload</button>
                  <button className="button secondary compact" disabled={page.data == null} onClick={() => { void frame.current?.requestFullscreen?.().catch(() => undefined) }} type="button"><Maximize2 size={14} /> Full screen</button>
                </span>
                : <label className="compare-toggle"><input checked={wrap} onChange={(event) => setWrap(event.target.checked)} type="checkbox" /> Wrap lines</label>}
              <span>
                <button className="button secondary compact" onClick={() => { void navigator.clipboard?.writeText(content).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500) }).catch(() => undefined) }} type="button">{copied ? <Check size={14} /> : <Copy size={14} />} {copied ? 'Copied' : 'Copy'}</button>
                <button className="button secondary compact" onClick={download} type="button"><Download size={14} /> Download</button>
              </span>
            </div>
            {previewing
              ? page.data != null
                ? <iframe className="artifact-preview" key={reloads} ref={frame} referrerPolicy="no-referrer" sandbox="allow-scripts" srcDoc={page.data} title={`Preview of ${path}`} />
                : page.error ? <div className="form-error" role="alert"><AlertCircle size={15} /> {page.error.message}</div>
                  : <div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading the page…</span></div>
              : <textarea aria-label={`Contents of ${path}`} className="artifact-text" data-wrap={wrap} readOnly spellCheck={false} value={content} wrap={wrap ? 'soft' : 'off'} />}
          </>}
    </div>
    <footer className="dialog-actions"><button className="button secondary" onClick={onClose} type="button">Close</button></footer>
  </Dialog>
}

/** The share of the context a run filled at most; while running, what it holds now. */
function contextUse(run: BenchmarkRun) {
  const live = run.state === 'running' ? run.live?.context : undefined
  const used = live?.used ?? run.stats.context_peak
  const size = live?.size ?? run.stats.context_size
  return used != null && size ? { used, size, percent: Math.round(used / size * 100), live: Boolean(live) } : undefined
}

function ContextFigure({ run }: { run: BenchmarkRun }) {
  const use = contextUse(run)
  if (!use) return <dd>—</dd>
  return <dd data-full={use.percent >= 95 || undefined} title={`${use.used.toLocaleString()} of ${use.size.toLocaleString()} tokens${use.live ? ' now' : ' at most'}`}>{use.percent}%</dd>
}

/** The same benchmark and settings as an earlier run; the profile is whatever it is now. */
function retryRequest(run: BenchmarkRun) {
  // Runs from before tools could be chosen had them all, which null asks for.
  return { profile_id: run.profile_id, benchmark_id: run.benchmark_id, max_tokens: run.max_tokens, tools: run.tools, tool_names: run.tools ? run.tool_names ?? null : [], system_prompt_id: run.system_prompt_id, reasoning_level: run.reasoning_level, verbose_log: Boolean(run.verbose_log) }
}

function RetryButton({ run, profile, blocker, pending, onRetry }: { run: BenchmarkRun; profile?: Profile; blocker?: string; pending: boolean; onRetry: () => void }) {
  const reason = blocker ?? (!profile ? 'This profile has been deleted.' : !profile.enabled ? 'Enable the profile to run it again.' : undefined)
  const version = profile && profile.version !== run.profile_version ? `v${profile.version}, this run used v${run.profile_version}` : `v${run.profile_version}`
  return <button aria-label={`Retry run ${run.benchmark_title} on ${run.profile_alias}`} className="icon-button small" disabled={Boolean(reason) || pending || run.state === 'running'} onClick={onRetry} title={reason ?? `Run again with the same settings on the profile as it is now (${version})`} type="button"><RotateCcw size={15} /></button>
}

// Offered only on a TypeScript benchmark, once an install that carries them is in place.
const TYPESCRIPT_TOOLS = ['check_typescript', 'bundle_typescript']

/** Why a tool can't be chosen: the benchmark isn't TypeScript, or what it needs isn't installed. */
type PickerTool = BenchmarkTool & { reason?: string }

function toolsFor(tools: BenchmarkTool[], typescript: boolean): PickerTool[] {
  return tools.map((tool) => {
    if (!TYPESCRIPT_TOOLS.includes(tool.name)) return tool.available ? tool : { ...tool, reason: 'Needs ESLint: download it in Settings, under Benchmark.' }
    if (!typescript) return { ...tool, available: false, reason: 'Only for TypeScript benchmarks.' }
    return tool.available ? tool : { ...tool, reason: 'Needs the TypeScript tools: download or update ESLint in Settings, under Benchmark.' }
  })
}

// How the tool list is grouped when expanded; a tool not named here goes under Other.
const TOOL_GROUPS: { title: string; names: string[] }[] = [
  { title: 'Files', names: ['write_file', 'edit_file', 'read_file', 'delete_file', 'list_files'] },
  { title: 'Tasks', names: ['list_tasks', 'create_task', 'complete_task', 'delete_task'] },
  { title: 'Source', names: ['check_file', 'check_typescript', 'bundle_typescript'] },
  { title: 'Job', names: ['finish_job'] },
]

function groupTools(tools: PickerTool[]) {
  const known = TOOL_GROUPS.flatMap((group) => group.names)
  return [...TOOL_GROUPS.map((group) => ({ title: group.title, tools: tools.filter((tool) => group.names.includes(tool.name)) })),
    { title: 'Other', tools: tools.filter((tool) => !known.includes(tool.name)) }].filter((group) => group.tools.length)
}

/** Tools for a run: all on or off at once, or chosen one by one, by group, when expanded. */
function ToolPicker({ tools, chosen, onChange }: { tools: PickerTool[]; chosen: string[]; onChange: (names: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const all = useRef<HTMLInputElement>(null)
  // Tools whose requirements are not installed can't be chosen, so "all" means all usable.
  const usable = tools.filter((tool) => tool.available).map((tool) => tool.name)
  const some = chosen.length > 0 && chosen.length < usable.length
  useEffect(() => { if (all.current) all.current.indeterminate = some }, [some])
  const toggle = (name: string, on: boolean) => onChange(usable.filter((item) => item === name ? on : chosen.includes(item)))
  const summary = !chosen.length ? 'Off: the model can only answer in text.'
    : some ? `${chosen.length} of ${usable.length} tools: ${chosen.join(', ')}. Files stay inside this run's own folder.`
      : `All ${usable.length} tools. The model works on files only inside this run's own folder, and they appear as artifacts on the run.`
  return <div className="benchmark-tools">
    <div className="benchmark-tools-head">
      <label className="benchmark-option"><input aria-label="Tools" checked={chosen.length > 0 && !some} onChange={(event) => onChange(event.target.checked ? usable : [])} ref={all} type="checkbox" /><span><strong>Tools</strong><small>{summary}</small></span></label>
      <button aria-controls="benchmark-tool-list" aria-expanded={open} className="button secondary compact" onClick={() => setOpen(!open)} type="button">Choose tools<ChevronDown aria-hidden="true" className="benchmark-tools-chevron" size={14} /></button>
    </div>
    {open && <div className="benchmark-tool-groups" id="benchmark-tool-list">
      {groupTools(tools).map((group) => <section aria-labelledby={`benchmark-tools-${group.title}`} className="benchmark-tool-group" key={group.title}>
        <h4 id={`benchmark-tools-${group.title}`}>{group.title}</h4>
        <ul className="benchmark-tool-list">
          {group.tools.map((tool) => <li key={tool.name}><label className="benchmark-option" data-disabled={!tool.available || undefined}><input checked={tool.available && chosen.includes(tool.name)} disabled={!tool.available} onChange={(event) => toggle(tool.name, event.target.checked)} type="checkbox" /><span><code>{tool.name}</code><small>{tool.summary}{tool.reason && ` ${tool.reason}`}</small></span></label></li>)}
        </ul>
      </section>)}
    </div>}
  </div>
}

// The tool an agent finishes with; older runs called it task_complete.
const FINISH_TOOLS = ['finish_job', 'task_complete']

/** What the agent said it finished on: the message from its accepted finish_job call. */
function completionMessage(run: BenchmarkRunDetail) {
  const done = run.tool_calls.filter((call) => FINISH_TOOLS.includes(call.name) && call.ok).at(-1)
  return done?.result.replace(/^(Job marked finished|Task marked complete)\.\s*/, '').trim() || undefined
}

/** A verbose log's tool arguments, one field at a time, with the text exactly as the model sent it. */
function ToolArguments({ sent }: { sent: string }) {
  let fields: [string, string][] | undefined
  try {
    const parsed: unknown = JSON.parse(sent)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) fields = Object.entries(parsed).map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value, null, 2)])
  } catch { /* shown as sent below */ }
  return <details className="tool-call-arguments">
    <summary>Arguments ({sent.length.toLocaleString()} characters)</summary>
    {fields ? <>
      <dl>{fields.map(([key, value]) => <div key={key}><dt>{key}</dt><dd><pre>{value}</pre></dd></div>)}</dl>
      <details><summary>As sent</summary><pre>{sent}</pre></details>
    </> : <><p>Not a JSON object, as sent:</p><pre>{sent}</pre></>}
  </details>
}

const TASK_ICONS = { open: Square, done: SquareCheck, deleted: SquareX }

/** The agent's task list, ticked off as it went; deleted tasks stay, struck through. */
function TaskList({ run }: { run: BenchmarkRunDetail }) {
  const tasks = run.tasks ?? []
  if (!tasks.length) return null
  const kept = tasks.filter((task) => task.status !== 'deleted')
  const done = kept.filter((task) => task.status === 'done').length
  const deleted = tasks.length - kept.length
  return <section className="benchmark-text">
    <h3>Tasks <span className="benchmark-task-count">{done} of {kept.length} done{deleted ? ` · ${deleted} deleted` : ''}</span></h3>
    <ul className="benchmark-task-list">
      {tasks.map((task: BenchmarkTask) => {
        const Icon = TASK_ICONS[task.status]
        return <li data-status={task.status} key={task.id}>
          <Icon aria-hidden="true" size={16} />
          <span className="sr-only">{task.status === 'done' ? 'Done: ' : task.status === 'deleted' ? 'Deleted: ' : 'Open: '}</span>
          <span className="benchmark-task-title">{task.title}</span>
          {task.source === 'agent' && <small>Added by the agent</small>}
        </li>
      })}
    </ul>
  </section>
}

function RunDetailBody({ run, onOpenArtifact }: { run: BenchmarkRunDetail; onOpenArtifact: (path: string) => void }) {
  const offered = useQuery({ queryKey: ['benchmarks'], queryFn: api.benchmarks }).data?.tools
  const { stats } = run
  const decode = run.samples.filter((sample) => sample.phase === 'decode').map((sample) => sample.rate)
  const prefill = run.samples.filter((sample) => sample.phase === 'prefill').map((sample) => sample.rate)
  const context = contextUse(run)
  const facts: [string, string][] = [
    ['Server start', formatSeconds(stats.server_start_seconds)],
    ['Model load', formatSeconds(stats.load_seconds)],
    ['First token', formatSeconds(stats.first_token_seconds)],
    ['Prompt', stats.prompt_tokens != null ? `${stats.prompt_tokens.toLocaleString()} tokens` : '—'],
    ['Prefill', formatRate(stats.prompt_tokens_per_second)],
    ['Tokens / limit', stats.generated_tokens != null ? `${stats.generated_tokens.toLocaleString()} / ${run.max_tokens?.toLocaleString() ?? 'context'}` : '—'],
    ['Decode average', formatRate(stats.decode_tokens_per_second)],
    ['Decode peak (3 s)', formatRate(stats.decode_peak_tokens_per_second)],
    ['Decode at end (3 s)', formatRate(stats.decode_final_tokens_per_second)],
    [context?.live ? 'Context (now)' : 'Context (most used)', context ? `${context.used.toLocaleString()} / ${context.size.toLocaleString()} (${context.percent}%)` : '—'],
    ['Reasoning', reasoningLabel(run.reasoning_level)],
    ...(run.verbose_log ? [['Log', 'Verbose'] as [string, string]] : []),
    ...(run.typescript_strict != null ? [['TypeScript', run.typescript_strict ? 'Strict checks' : 'Non-strict checks'] as [string, string]] : []),
    ['Total', formatSeconds(stats.total_seconds)],
    ...(run.tools && run.tool_names ? [['Tools', offered && run.tool_names.length === offered.length ? 'All' : run.tool_names.join(', ')] as [string, string]] : []),
    ...(run.tools ? [['Tool calls', `${stats.tool_calls ?? run.tool_calls.length} in ${stats.rounds ?? 1} ${stats.rounds === 1 ? 'request' : 'requests'}`] as [string, string]] : []),
  ]
  const refused = run.tool_calls.filter((call) => !call.ok).length
  const completion = completionMessage(run)
  return <>
    {run.error && run.state !== 'cancelled' && <div className="form-error" role="alert"><AlertCircle size={15} /> {run.error}</div>}
    <div className="benchmark-detail-head"><StatePill run={run} /><OutcomeTag run={run} />{finishLabel(run) && <span className="profile-tag" title="Why generation ended">{finishLabel(run)}</span>}</div>
    <RunSteps detail={run} run={run} />
    <dl className="benchmark-facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {decode.length > 1 && <div className="benchmark-chart"><span>Decode speed over the run (3 s windows)</span><Sparkline ceiling={stats.decode_peak_tokens_per_second} values={decode} /></div>}
    {prefill.length > 1 && <div className="benchmark-chart"><span>Prefill speed</span><Sparkline values={prefill} /></div>}
    <ContextChart resources={run.resources ?? []} />
    <MemoryChart resources={run.resources ?? []} />
    <TaskList run={run} />
    {run.tools && <section className="benchmark-text">
      <h3>Artifacts</h3>
      {run.artifacts.length ? <ul className="artifact-list">
        {run.artifacts.map((artifact) => <li key={artifact.path}>
          <button aria-label={`Open ${artifact.path}`} className="artifact-link" onClick={() => onOpenArtifact(artifact.path)} type="button"><FileText size={15} /><span>{artifact.path}</span><small>{formatSize(artifact.size)}</small></button>
        </li>)}
      </ul> : <p className="benchmark-empty">{run.state === 'running' ? 'No files written yet.' : 'The model did not write any files.'}</p>}
    </section>}
    {completion && <section className="benchmark-text"><h3>Completion message</h3><pre>{completion}</pre></section>}
    {run.tool_calls.length > 0 && <details className="benchmark-text">
      <summary>Tool calls ({run.tool_calls.length}{refused ? `, ${refused} refused` : ''})</summary>
      <ol className="tool-call-list">
        {run.tool_calls.map((call, index) => <li data-ok={call.ok} key={index}>
          <span className="tool-call-name">{call.name}{call.path ? <code>{call.path}</code> : null}</span>
          <span className="tool-call-result">{call.result}</span>
          {call.arguments != null && <ToolArguments sent={call.arguments} />}
        </li>)}
      </ol>
    </details>}
    <section className="benchmark-text"><h3>Output</h3><pre>{run.output || (run.state === 'running' ? 'Waiting for output…' : 'No output.')}</pre></section>
    {run.reasoning && <details className="benchmark-text"><summary>Reasoning ({run.reasoning.length.toLocaleString()} characters)</summary><pre>{run.reasoning}</pre></details>}
    {run.system_prompt && <details className="benchmark-text"><summary>System prompt{run.system_prompt_id ? ` (${run.system_prompt_id})` : ''}</summary><pre>{run.system_prompt}</pre></details>}
    <details className="benchmark-text"><summary>Prompt</summary><pre>{run.prompt}</pre></details>
  </>
}

/** Run a bundled prompt against one profile from a stopped router, and keep the measurements. */
export function BenchmarksPanel({ profiles, routerState, canRun }: { profiles: Profile[]; routerState?: RouterState; canRun: boolean }) {
  const queryClient = useQueryClient()
  const catalog = useQuery({ queryKey: ['benchmarks'], queryFn: api.benchmarks, refetchInterval: (query) => query.state.data?.active_run_id ? 1000 : false })
  const runs = useQuery({
    queryKey: ['benchmark-runs'],
    queryFn: api.benchmarkRuns,
    refetchInterval: (query) => query.state.data?.some((run) => run.state === 'running') ? 1000 : false,
  })
  const [profileId, setProfileId] = useState('')
  const [benchmarkId, setBenchmarkId] = useState('')
  const [maxTokens, setMaxTokens] = useState('')
  // Undefined until chosen, so every tool the catalog offers is on.
  const [toolNames, setToolNames] = useState<string[]>()
  // Undefined until chosen, so the catalog's default applies; '' means no system prompt.
  const [systemPromptId, setSystemPromptId] = useState<string>()
  // '' sends no level, so the model's chat template picks its own default.
  const [reasoningLevel, setReasoningLevel] = useState('')
  const [verboseLog, setVerboseLog] = useState(false)
  const [viewing, setViewing] = useState<string>()
  const [deleting, setDeleting] = useState<BenchmarkRun>()
  // Runs ticked for deleting together, and whether that is being confirmed.
  const [chosen, setChosen] = useState<string[]>([])
  const [deletingChosen, setDeletingChosen] = useState(false)
  const runPanel = useRef<HTMLElement>(null)
  const enabled = profiles.filter((profile) => profile.enabled)
  const profile = profiles.find((item) => item.id === profileId) ?? enabled[0]
  const benchmarks = catalog.data?.benchmarks ?? []
  const benchmark = benchmarks.find((item) => item.id === (benchmarkId || catalog.data?.default_benchmark_id)) ?? benchmarks[0]
  const systemPrompts = catalog.data?.system_prompts ?? []
  const allTools = toolsFor(catalog.data?.tools ?? [], Boolean(benchmark?.typescript))
  // A TypeScript benchmark without its tools would run as a plain one, so say so.
  const typescriptMissing = Boolean(benchmark?.typescript) && allTools.some((tool) => TYPESCRIPT_TOOLS.includes(tool.name) && !tool.available)
  const usableTools = allTools.filter((tool) => tool.available).map((tool) => tool.name)
  // A tool chosen earlier drops out if what it needs is removed in the meantime.
  const chosenTools = (toolNames ?? usableTools).filter((name) => usableTools.includes(name))
  const chosenSystemPrompt = systemPromptId ?? catalog.data?.default_system_prompt_id ?? ''
  const systemPrompt = systemPrompts.find((item) => item.id === chosenSystemPrompt)
  // Blank means no limit: generation may use the profile's whole context.
  const tokens = maxTokens ? Number(maxTokens) : catalog.data?.default_max_tokens ?? null
  const active = runs.data?.find((run) => run.state === 'running')
  // The running benchmark's full detail, so its steps can show what generation is doing.
  const activeDetail = useQuery({
    queryKey: ['benchmark-run', active?.id],
    queryFn: () => api.benchmarkRun(active!.id),
    enabled: Boolean(active),
    refetchInterval: 1000,
  })
  const routerStopped = routerState === 'stopped' || routerState === 'crashed'

  // When a run finishes its result lands on the profile and the router stops: refresh both.
  const activeId = active?.id
  const [watched, setWatched] = useState<string>()
  useEffect(() => {
    if (activeId) setWatched(activeId)
    else if (watched) {
      setWatched(undefined)
      void queryClient.invalidateQueries({ queryKey: ['profiles'] })
      void queryClient.invalidateQueries({ queryKey: ['server'] })
    }
  }, [activeId, queryClient, watched])

  const start = useMutation({
    mutationFn: (body: Parameters<typeof api.startBenchmark>[0]) => api.startBenchmark(body),
    onSettled: async () => {
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['benchmark-runs'] }), queryClient.invalidateQueries({ queryKey: ['benchmarks'] })])
    },
  })
  const cancel = useMutation({
    mutationFn: (runId: string) => api.cancelBenchmark(runId),
    onSettled: async () => { await queryClient.invalidateQueries({ queryKey: ['benchmark-runs'] }) },
  })
  const removal = useMutation({
    mutationFn: (runId: string) => api.deleteBenchmarkRun(runId),
    onSuccess: async () => {
      setDeleting(undefined)
      await queryClient.invalidateQueries({ queryKey: ['benchmark-runs'] })
    },
  })
  const bulkRemoval = useMutation({
    mutationFn: (runIds: string[]) => api.deleteBenchmarkRuns(runIds),
    onSuccess: async (result) => {
      setDeletingChosen(false)
      setChosen((current) => current.filter((id) => !result.deleted.includes(id)))
      await queryClient.invalidateQueries({ queryKey: ['benchmark-runs'] })
    },
  })
  // A running run cannot be deleted, so it cannot be ticked either.
  const deletable = (runs.data ?? []).filter((run) => run.state !== 'running').map((run) => run.id)
  const picked = chosen.filter((id) => deletable.includes(id))
  const allPicked = deletable.length > 0 && picked.length === deletable.length
  const toggle = (runId: string, on: boolean) => setChosen((current) => on ? [...current, runId] : current.filter((id) => id !== runId))

  const blocker = !canRun ? 'Running a benchmark needs permission to start and stop the server.'
    : active ? 'A benchmark is already running.'
      : !routerStopped ? 'Stop the router first. A benchmark starts it on its own, so nothing else shares the machine.'
        : !enabled.length ? 'Enable a profile to benchmark it.'
          : !benchmarks.length ? 'No benchmark prompts are installed.'
            : undefined
  const tokensValid = tokens == null || (Number.isInteger(tokens) && tokens >= 16 && tokens <= 1048576)

  return <>
    <section className="data-panel benchmark-run-panel" ref={runPanel}>
      <div className="panel-heading"><div><h2>Run a benchmark</h2><p>Starts the server, loads one profile, sends the prompt and measures it, then stops the server again.</p></div><Timer size={18} /></div>
      <form className="benchmark-form" onSubmit={(event) => { event.preventDefault(); start.mutate({ profile_id: profile!.id, benchmark_id: benchmark!.id, max_tokens: tokens, tools: chosenTools.length > 0, tool_names: chosenTools, system_prompt_id: systemPrompt?.id ?? null, reasoning_level: reasoningLevel || null, verbose_log: chosenTools.length > 0 && verboseLog }) }}>
        <label className="compare-pick"><span>Profile</span><select aria-label="Profile" onChange={(event) => setProfileId(event.target.value)} value={profile?.id ?? ''}>
          {profiles.map((item) => <option disabled={!item.enabled} key={item.id} value={item.id}>{item.alias}{item.enabled ? '' : ' (disabled)'}</option>)}
        </select></label>
        <label className="compare-pick"><span>Benchmark</span><select aria-label="Benchmark" onChange={(event) => setBenchmarkId(event.target.value)} value={benchmark?.id ?? ''}>
          {benchmarks.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select></label>
        <label className="compare-pick benchmark-tokens"><span>Max tokens</span><input aria-label="Max tokens" max={1048576} min={16} onChange={(event) => setMaxTokens(event.target.value)} placeholder={catalog.data?.default_max_tokens != null ? String(catalog.data.default_max_tokens) : 'Full context'} title="Leave blank to let the run use the profile's whole context" type="number" value={maxTokens} /></label>
        <button className="button primary" disabled={Boolean(blocker) || !tokensValid || start.isPending || !profile || !benchmark} type="submit">{start.isPending ? <LoaderCircle className="spin" size={15} /> : <Play size={14} fill="currentColor" />} Run benchmark</button>
      </form>
      {allTools.length > 0 && <ToolPicker chosen={chosenTools} onChange={setToolNames} tools={allTools} />}
      {chosenTools.length > 0 && <label className="benchmark-option"><input aria-label="Verbose log" checked={verboseLog} onChange={(event) => setVerboseLog(event.target.checked)} type="checkbox" /><span><strong>Verbose log</strong><small>Keeps the arguments of every tool call and its whole result, so you can see why a call failed. Runs take more space.</small></span></label>}
      {typescriptMissing && <p className="benchmark-note" role="status"><AlertCircle size={15} /> {benchmark?.title} is a TypeScript benchmark, but the TypeScript tools are not installed, so it would run without them. Download or update ESLint in Settings, under Benchmark, to add them.</p>}
      <div className="benchmark-choices">
        {systemPrompts.length > 0 && <label className="compare-pick"><span>System prompt</span><select aria-label="System prompt" onChange={(event) => setSystemPromptId(event.target.value)} value={chosenSystemPrompt}>
          <option value="">None: the prompt on its own</option>
          {systemPrompts.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select></label>}
        <label className="compare-pick"><span>Reasoning</span><select aria-label="Reasoning" onChange={(event) => setReasoningLevel(event.target.value)} value={reasoningLevel}>
          <option value="">Model default</option>
          {(catalog.data?.reasoning_levels ?? []).map((level) => <option key={level} value={level}>{reasoningLabel(level)}</option>)}
        </select></label>
      </div>
      <p className="benchmark-hint">Reasoning levels are passed to the model&apos;s chat template, which decides what each one means. Model default is whatever the template picks when nothing is sent, which can be its highest level. A level the template does not support fails the run with its message.</p>
      {benchmark && <details className="benchmark-prompt"><summary>{systemPrompt ? 'System prompt and prompt' : 'Prompt'}</summary>{systemPrompt && <pre aria-label="System prompt text">{systemPrompt.text}</pre>}<pre>{benchmark.prompt}</pre></details>}
      {(blocker || start.error) && <p className={start.error ? 'form-error' : 'benchmark-note'} role={start.error ? 'alert' : 'status'}><AlertCircle size={15} /> {start.error?.message ?? blocker}</p>}
      {active && <div className="benchmark-active">
        <div className="benchmark-active-head">
          <strong>{active.benchmark_title} on {active.profile_alias}</strong>
          <span>
            <button className="button secondary compact" onClick={() => setViewing(active.id)} type="button"><Eye size={14} /> Watch output</button>
            <button className="button danger compact" disabled={cancel.isPending || !canRun} onClick={() => cancel.mutate(active.id)} type="button"><Square size={12} fill="currentColor" /> Cancel</button>
          </span>
        </div>
        <RunSteps detail={activeDetail.data?.id === active.id ? activeDetail.data : undefined} run={activeDetail.data?.id === active.id ? activeDetail.data : active} />
      </div>}
    </section>

    <section className="data-panel">
      <div className="panel-heading"><div><h2>Runs</h2><p>Every run keeps its timings and output until you delete it. The latest result of each benchmark also stays on its profile.</p></div>
        {canRun && deletable.length > 0 && <div className="panel-heading-actions">
          <label className="benchmark-select-all"><input aria-label="Select every finished run" checked={allPicked} onChange={(event) => setChosen(event.target.checked ? deletable : [])} ref={(input) => { if (input) input.indeterminate = picked.length > 0 && !allPicked }} type="checkbox" /> Select all</label>
          <button className="button danger compact" disabled={!picked.length} onClick={() => setDeletingChosen(true)} type="button"><Trash2 size={14} /> Delete{picked.length ? ` ${picked.length}` : ''}</button>
        </div>}
      </div>
      {runs.data?.length ? <ul className="benchmark-runs">
        {runs.data.map((run) => <li data-selectable={canRun || undefined} key={run.id}>
          {canRun && <input aria-label={`Select run ${run.benchmark_title} on ${run.profile_alias}, ${formatWhen(run.started_at)}`} checked={picked.includes(run.id)} className="benchmark-run-check" disabled={run.state === 'running'} onChange={(event) => toggle(run.id, event.target.checked)} type="checkbox" />}
          <span className="benchmark-run-copy">
            <strong><button aria-label={`View run ${run.benchmark_title} on ${run.profile_alias}`} className="benchmark-run-title" onClick={() => setViewing(run.id)} type="button">{run.benchmark_title} · {run.profile_alias}</button> <span className="profile-tag">v{run.profile_version}</span> <OutcomeTag run={run} /></strong>
            <span>{formatWhen(run.started_at)}{run.system_prompt_id ? ` · ${run.system_prompt_id}` : ''}{run.reasoning_level ? ` · reasoning ${reasoningLabel(run.reasoning_level).toLowerCase()}` : ''}{run.stats.files_written ? ` · ${run.stats.files_written} ${run.stats.files_written === 1 ? 'file' : 'files'}` : ''}{run.error && run.state === 'failed' ? ` · ${run.error}` : ''}</span>
          </span>
          <dl className="benchmark-run-figures">
            <div><dt>Total</dt><dd>{formatSeconds(run.stats.total_seconds)}</dd></div>
            <div><dt>Load</dt><dd>{formatSeconds(run.stats.load_seconds)}</dd></div>
            <div><dt>Prefill</dt><dd>{formatRate(run.stats.prompt_tokens_per_second ?? run.live?.prefill?.rate)}</dd></div>
            <div><dt>Decode avg</dt><dd>{formatRate(run.stats.decode_tokens_per_second ?? run.live?.decode?.rate)}</dd></div>
            <div><dt>Peak</dt><dd>{formatRate(run.stats.decode_peak_tokens_per_second)}</dd></div>
            <div><dt>Tokens</dt><dd>{run.stats.generated_tokens?.toLocaleString() ?? run.live?.decode?.tokens?.toLocaleString() ?? '—'}</dd></div>
            <div><dt>Context</dt><ContextFigure run={run} /></div>
          </dl>
          <span className="row-actions">
            <StatePill run={run} />
            <RetryButton blocker={blocker} onRetry={() => {
              start.mutate(retryRequest(run))
              // Progress and any error show in the run panel, above the list.
              runPanel.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
            }} pending={start.isPending} profile={profiles.find((item) => item.id === run.profile_id)} run={run} />
            <button aria-label={`Delete run ${run.benchmark_title} on ${run.profile_alias}`} className="icon-button small danger-icon" disabled={run.state === 'running' || !canRun} onClick={() => setDeleting(run)} title="Delete run" type="button"><Trash2 size={15} /></button>
          </span>
        </li>)}
      </ul> : <div className="empty compact-empty"><Timer size={24} /><strong>No benchmark runs yet</strong><span>Runs appear here with their timings and output.</span></div>}
    </section>

    {viewing && <RunDetail onClose={() => setViewing(undefined)} runId={viewing} />}
    {deletingChosen && <Dialog description="Their timings and output are removed. The latest result already written to each profile is kept." onClose={() => setDeletingChosen(false)} title={`Delete ${picked.length} benchmark ${picked.length === 1 ? 'run' : 'runs'}?`}>
      <div className="confirm-body"><Trash2 size={24} /><p>{picked.length === deletable.length ? 'Every finished run' : `${picked.length} of ${deletable.length} finished runs`} will be deleted.</p>{bulkRemoval.error && <div className="form-error"><AlertCircle size={15} /> {bulkRemoval.error.message}</div>}</div>
      <footer className="dialog-actions"><button className="button secondary" onClick={() => setDeletingChosen(false)} type="button">Keep runs</button><button className="button danger" disabled={bulkRemoval.isPending || !picked.length} onClick={() => bulkRemoval.mutate(picked)} type="button">{bulkRemoval.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete {picked.length === 1 ? 'run' : 'runs'}</button></footer>
    </Dialog>}
    {deleting && <Dialog description="The run's timings and output are removed. The latest result already written to the profile is kept." onClose={() => setDeleting(undefined)} title="Delete benchmark run?">
      <div className="confirm-body"><Trash2 size={24} /><p><strong>{deleting.benchmark_title}</strong> on <strong>{deleting.profile_alias}</strong>, {formatWhen(deleting.started_at)}.</p>{removal.error && <div className="form-error"><AlertCircle size={15} /> {removal.error.message}</div>}</div>
      <footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep run</button><button className="button danger" disabled={removal.isPending} onClick={() => removal.mutate(deleting.id)} type="button">{removal.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete run</button></footer>
    </Dialog>}
  </>
}
