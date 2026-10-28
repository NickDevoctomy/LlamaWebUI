import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ArrowDown, ArrowUp, Bell, BookOpen, Bot, Check, ChevronRight, ClipboardCheck, Eraser, FileBarChart, LoaderCircle, MessagesSquare, Pencil, Play, Plus, RotateCcw, Send, ShieldQuestion, Square, Trash2, Wrench, X } from 'lucide-react'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { api, type AssistantApproval, type AssistantConversation, type AssistantConversationDetail, type AssistantMessage, type AssistantStatus, type Brief, type BriefTarget } from './api'
import { LazyMarkdown } from './LazyMarkdown'
import { priceSummary } from './openRouterPricing'
import { ReportsPanel, ResearchPanel } from './LibraryPanel'
import { Dialog } from './SetupPanels'

const TARGET_LABELS: Record<BriefTarget, string> = {
  decode: 'Decode speed',
  prefill: 'Prefill speed',
  load: 'Load time',
  memory: 'Memory use',
}
const DEFAULT_PRIORITY: BriefTarget[] = ['decode', 'prefill', 'load', 'memory']
const STATE_LABELS: Record<AssistantConversation['state'], string> = {
  idle: 'Ready',
  running: 'Working',
  awaiting_approval: 'Needs approval',
  paused: 'Paused',
  interrupted: 'Interrupted',
  failed: 'Failed',
}

export function formatCost(value: number) {
  return `$${value > 0 && value < 0.01 ? value.toFixed(4) : value.toFixed(2)}`
}

/** The brief's targets as short phrases, e.g. "decode ≥ 30 tok/s". */
export function briefTargets(brief: Brief) {
  return [
    brief.min_decode_tps != null && `decode ≥ ${brief.min_decode_tps} tok/s`,
    brief.min_prefill_tps != null && `prefill ≥ ${brief.min_prefill_tps} tok/s`,
    brief.max_load_seconds != null && `load ≤ ${brief.max_load_seconds} s`,
    brief.max_ram_gb != null && `RAM ≤ ${brief.max_ram_gb} GB`,
    brief.max_vram_gb != null && `VRAM ≤ ${brief.max_vram_gb} GB`,
    brief.min_context_tokens != null && `context ≥ ${brief.min_context_tokens.toLocaleString()} tokens`,
    (brief.parallel_slots ?? 1) > 1 && `${brief.parallel_slots} parallel slots`,
  ].filter((item): item is string => Boolean(item))
}

function numberOrNull(value: string) {
  const parsed = Number(value)
  return value.trim() && Number.isFinite(parsed) ? parsed : null
}

/** The New optimisation form: what to work on and the targets to reach. */
/** The brief form: for a new conversation (optionally filled from an earlier one), or to edit one. */
function BriefDialog({ initial, initialMessage = '', creating, title, onClose, onSave, saving, error }: {
  initial?: Brief
  /** The first message to send once a new conversation is created. */
  initialMessage?: string
  creating: boolean
  title: string
  onClose: () => void
  onSave: (brief: Brief, title: string, message: string) => void
  saving: boolean
  error?: string
}) {
  const [subject, setSubject] = useState<Brief['subject']>(initial?.subject ?? 'model')
  const [repoId, setRepoId] = useState(initial?.model?.repo_id ?? '')
  const [quantization, setQuantization] = useState(initial?.model?.quantization ?? '')
  const [profileId, setProfileId] = useState(initial?.profile_id ?? '')
  const [fields, setFields] = useState<Record<string, string>>(() => Object.fromEntries(
    (['max_load_seconds', 'min_prefill_tps', 'min_decode_tps', 'max_ram_gb', 'max_vram_gb', 'min_context_tokens'] as const)
      .map((key) => [key, initial?.[key] != null ? String(initial[key]) : '']),
  ))
  const [slots, setSlots] = useState(String(initial?.parallel_slots ?? 1))
  const [benchmarkId, setBenchmarkId] = useState(initial?.benchmark_id ?? '')
  // Undefined until chosen, so a new brief takes the level of the profile's last result.
  const [reasoning, setReasoning] = useState<string | undefined>(initial ? initial.reasoning_level ?? '' : undefined)
  const [priority, setPriority] = useState<BriefTarget[]>(initial?.priority ?? DEFAULT_PRIORITY)
  const [autonomous, setAutonomous] = useState(initial?.autonomous ?? true)
  const [conversationTitle, setConversationTitle] = useState('')
  const [firstMessage, setFirstMessage] = useState(initialMessage)
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.profiles })
  const library = useQuery({ queryKey: ['library'], queryFn: api.library })
  const benchmarks = useQuery({ queryKey: ['benchmarks'], queryFn: api.benchmarks })
  const search = useQuery({
    queryKey: ['huggingface-search', repoId.trim(), 'downloads'],
    queryFn: () => api.searchModels(repoId.trim()),
    enabled: subject === 'model' && repoId.trim().length >= 3 && !repoId.includes('/'),
  })
  const suggestions = useMemo(() => [...new Set([
    ...(library.data ?? []).map((model) => model.repo_id),
    ...(search.data ?? []).map((result) => result.repo_id),
  ])], [library.data, search.data])
  const field = (key: string) => ({ value: fields[key], onChange: (event: React.ChangeEvent<HTMLInputElement>) => setFields({ ...fields, [key]: event.target.value }) })
  const move = (index: number, by: number) => {
    const next = [...priority]
    ;[next[index], next[index + by]] = [next[index + by], next[index]]
    setPriority(next)
  }
  const valid = subject === 'model' ? repoId.trim().length >= 3 : Boolean(profileId)
  // Runs only compare at the same level, so start from the one the profile was last measured at.
  const measuredLevel = subject === 'profile'
    ? profiles.data?.find((profile) => profile.id === profileId)?.benchmark_results?.[benchmarkId || benchmarks.data?.default_benchmark_id || '']?.reasoning_level ?? undefined
    : undefined
  const level = reasoning ?? measuredLevel ?? ''
  const submit = () => onSave({
    subject,
    model: subject === 'model' ? { repo_id: repoId.trim(), quantization: quantization.trim() || null } : null,
    profile_id: subject === 'profile' ? profileId : null,
    max_load_seconds: numberOrNull(fields.max_load_seconds),
    min_prefill_tps: numberOrNull(fields.min_prefill_tps),
    min_decode_tps: numberOrNull(fields.min_decode_tps),
    max_ram_gb: numberOrNull(fields.max_ram_gb),
    max_vram_gb: numberOrNull(fields.max_vram_gb),
    min_context_tokens: numberOrNull(fields.min_context_tokens),
    parallel_slots: numberOrNull(slots) ?? 1,
    benchmark_id: benchmarkId || null,
    reasoning_level: level || null,
    priority,
    autonomous,
  }, conversationTitle.trim(), firstMessage.trim())
  return <Dialog description="What to work on, and the targets to reach. Targets are optional; leave one empty to let the assistant decide." onClose={onClose} title={title} wide>
    <form className="form-body brief-form" onSubmit={(event) => { event.preventDefault(); if (valid) submit() }}>
      <fieldset className="brief-section">
        <legend>Subject</legend>
        <div aria-label="Subject" className="segmented brief-subject" role="tablist">
          <button aria-selected={subject === 'model'} onClick={() => setSubject('model')} role="tab" type="button">Get a model working</button>
          <button aria-selected={subject === 'profile'} onClick={() => setSubject('profile')} role="tab" type="button">Optimise a profile</button>
        </div>
        {subject === 'model'
          ? <div className="brief-grid">
            <label className="form-field brief-wide"><span>Hugging Face repository</span><input autoFocus list="brief-models" onChange={(event) => setRepoId(event.target.value)} placeholder="owner/Model-GGUF, or a name to search" value={repoId} /><small>Pick a downloaded model, or type a name to search Hugging Face.</small></label>
            <datalist id="brief-models">{suggestions.map((item) => <option key={item} value={item} />)}</datalist>
            <label className="form-field"><span>Quantization <small>(optional)</small></span><input onChange={(event) => setQuantization(event.target.value)} placeholder="e.g. Q4_K_M" value={quantization} /></label>
          </div>
          : <label className="form-field"><span>Profile</span><select onChange={(event) => setProfileId(event.target.value)} value={profileId}><option value="">Choose a profile…</option>{(profiles.data ?? []).map((profile) => <option key={profile.id} value={profile.id}>{profile.alias}</option>)}</select></label>}
      </fieldset>
      <fieldset className="brief-section">
        <legend>Targets</legend>
        <div className="brief-grid">
          <label className="form-field"><span>Max load time (s)</span><input inputMode="decimal" min="0" step="any" type="number" {...field('max_load_seconds')} /></label>
          <label className="form-field"><span>Min prefill (tok/s, average)</span><input inputMode="decimal" min="0" step="any" type="number" {...field('min_prefill_tps')} /></label>
          <label className="form-field"><span>Min decode (tok/s, average)</span><input inputMode="decimal" min="0" step="any" type="number" {...field('min_decode_tps')} /></label>
          <label className="form-field"><span>Max RAM (GB)</span><input inputMode="decimal" min="0" step="any" type="number" {...field('max_ram_gb')} /></label>
          <label className="form-field"><span>Max VRAM (GB)</span><input inputMode="decimal" min="0" step="any" type="number" {...field('max_vram_gb')} /></label>
        </div>
      </fieldset>
      <fieldset className="brief-section">
        <legend>Context</legend>
        <div className="brief-grid">
          <label className="form-field"><span>Min context (tokens)</span><input inputMode="numeric" min="256" step="1" type="number" {...field('min_context_tokens')} /><small>Never shrunk below this to meet another target.</small></label>
          <label className="form-field"><span>Parallel slots</span><input inputMode="numeric" min="1" onChange={(event) => setSlots(event.target.value)} step="1" type="number" value={slots} /><small>Requests the profile must serve at once.</small></label>
        </div>
      </fieldset>
      <fieldset className="brief-section">
        <legend>Workload and priority</legend>
        <div className="brief-grid">
          <label className="form-field"><span>Benchmark</span><select onChange={(event) => setBenchmarkId(event.target.value)} value={benchmarkId}><option value="">Default{benchmarks.data?.default_benchmark_id ? ` (${benchmarks.data.benchmarks.find((item) => item.id === benchmarks.data?.default_benchmark_id)?.title ?? benchmarks.data.default_benchmark_id})` : ''}</option>{(benchmarks.data?.benchmarks ?? []).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><small>Judges every change.</small></label>
          <label className="form-field"><span>Reasoning</span><select onChange={(event) => setReasoning(event.target.value)} value={level}><option value="">Model default</option>{(benchmarks.data?.reasoning_levels ?? []).map((item) => <option key={item} value={item}>{item}</option>)}</select><small>{reasoning === undefined && measuredLevel ? 'As the profile was last measured.' : 'Every run uses it.'}</small></label>
        </div>
        <div className="form-field"><span>When targets conflict, favour</span>
          <ol className="priority-list">{priority.map((target, index) => <li key={target}>
            <span className="priority-rank">{index + 1}</span><span>{TARGET_LABELS[target]}</span>
            <button aria-label={`Move ${TARGET_LABELS[target]} up`} className="icon-button small" disabled={index === 0} onClick={() => move(index, -1)} type="button"><ArrowUp size={14} /></button>
            <button aria-label={`Move ${TARGET_LABELS[target]} down`} className="icon-button small" disabled={index === priority.length - 1} onClick={() => move(index, 1)} type="button"><ArrowDown size={14} /></button>
          </li>)}</ol>
        </div>
      </fieldset>
      <fieldset className="brief-section">
        <legend>How to work</legend>
        <label className="check-row brief-check"><input checked={autonomous} onChange={(event) => setAutonomous(event.target.checked)} type="checkbox" /><span><strong>Work without asking questions</strong><small>Decides for itself and carries on until the targets are met or nothing is left worth trying, then writes a report. Actions that need approval still wait for you.</small></span></label>
      </fieldset>
      {creating && <>
        <label className="form-field"><span>Title <small>(optional)</small></span><input onChange={(event) => setConversationTitle(event.target.value)} placeholder="Named from the subject if left empty" value={conversationTitle} /></label>
        <label className="form-field"><span>First message <small>(optional)</small></span><textarea onChange={(event) => setFirstMessage(event.target.value)} placeholder="Sent as soon as the conversation starts, e.g. “Get this working and tune it to the targets.”" rows={3} value={firstMessage} /></label>
      </>}
      {error && <div className="form-error" role="alert"><AlertCircle size={15} /> {error}</div>}
      <footer className="dialog-actions"><button className="button secondary" onClick={onClose} type="button">Cancel</button><button className="button primary" disabled={!valid || saving} type="submit">{saving ? <LoaderCircle className="spin" size={15} /> : creating ? <Plus size={15} /> : <Pencil size={15} />} {creating ? 'Start' : 'Save brief'}</button></footer>
    </form>
  </Dialog>
}

/** A conversation is busy while a turn runs, including while it waits for an approval. */
export function isBusy(state: AssistantConversation['state']) {
  return state === 'running' || state === 'awaiting_approval'
}

function detailValue(value: unknown) {
  if (value == null) return '—'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** What the assistant wants to do, waiting for the operator to approve or reject it. */
function ApprovalCard({ approval, canDecide, onDecide, deciding }: {
  approval: AssistantApproval
  canDecide: boolean
  onDecide: (approve: boolean) => void
  deciding: boolean
}) {
  const { changes, ...rest } = approval.details
  const entries = Object.entries(rest).filter(([, value]) => value !== null && value !== undefined && !(Array.isArray(value) && !value.length))
  return <li className="assistant-approval" role="group" aria-label={`Approval needed: ${approval.title}`}>
    <div className="assistant-approval-head"><ShieldQuestion size={16} /><strong>{approval.title}</strong><span className="profile-tag warning-tag">{approval.category}</span></div>
    {changes?.length ? <table className="approval-changes"><thead><tr><th>Argument</th><th>Now</th><th>After</th></tr></thead><tbody>
      {changes.map((change) => <tr key={change.argument}><td><code>{change.argument}</code></td><td>{detailValue(change.before)}</td><td>{detailValue(change.after)}</td></tr>)}
    </tbody></table> : null}
    {entries.length ? <dl className="approval-details">{entries.map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{detailValue(value)}</dd></div>)}</dl> : null}
    <div className="assistant-approval-actions">
      <button className="button primary compact" disabled={!canDecide || deciding} onClick={() => onDecide(true)} type="button"><Check size={14} /> Approve</button>
      <button className="button secondary compact" disabled={!canDecide || deciding} onClick={() => onDecide(false)} type="button"><X size={14} /> Reject</button>
      {!canDecide && <small>Deciding needs the assistant.write privilege and the action's own.</small>}
    </div>
  </li>
}

const DECISION_LABELS: Partial<Record<AssistantApproval['state'], string>> = {
  approved: 'Approved',
  // A rejected call already reads Rejected; an expired one was never decided.
  expired: 'Not decided',
}

function ToolRow({ call, result, running, approval }: {
  call: { id: string; function: { name: string; arguments: string } }
  result?: AssistantMessage
  running: boolean
  approval?: AssistantApproval
}) {
  const pretty = (text: string) => {
    try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text }
  }
  const ok = result?.summary?.ok
  return <li className="assistant-tool" data-ok={ok === false ? 'false' : undefined}>
    <details>
      <summary>
        <ChevronRight aria-hidden="true" className="timeline-chevron" size={13} />
        <Wrench aria-hidden="true" size={13} />
        <code>{call.function.name}</code>
        <span>{result ? result.summary?.summary || (ok === false ? 'Failed' : 'Done') : running ? 'Running…' : 'Not run'}</span>
        {approval && DECISION_LABELS[approval.state] && <span className={`profile-tag decision-tag ${approval.state}`} title={approval.decided_by ? `By ${approval.decided_by}` : undefined}>{DECISION_LABELS[approval.state]}</span>}
        {!result && running && <LoaderCircle className="spin" size={13} />}
      </summary>
      <div className="assistant-tool-body">
        <small>Arguments</small><pre>{pretty(call.function.arguments || '{}')}</pre>
        {result && <><small>Result</small><pre>{pretty(result.content)}</pre></>}
      </div>
    </details>
  </li>
}

function Messages({ detail, name, canDecide, onDecide, deciding }: {
  detail: AssistantConversationDetail
  name: string
  canDecide: boolean
  onDecide: (id: string, approve: boolean) => void
  deciding: boolean
}) {
  const results = new Map(detail.messages.filter((message) => message.role === 'tool').map((message) => [message.tool_call_id, message]))
  const approvals = new Map((detail.approvals ?? []).map((approval) => [approval.tool_call_id, approval]))
  const running = isBusy(detail.state)
  const progress = detail.progress
  return <ol className="assistant-messages" aria-label="Conversation">
    {detail.messages.map((message) => {
      if (message.role === 'tool') return null
      if (message.role === 'user') return <li className="assistant-message user" key={message.id}><div className="assistant-bubble">{message.content}</div></li>
      if (message.role === 'event') {
        const kind = message.summary?.kind
        if (kind === 'notification') return <li className={`assistant-event notification${message.summary?.ok === false ? ' failed' : ''}`} key={message.id}><Bell size={13} /> {message.content}</li>
        if (kind === 'check') return <li className="assistant-event notification check" key={message.id} title={`An automatic reminder sent to ${name}`}><ClipboardCheck size={13} /> <span><strong>Reminder to {name}:</strong> {message.content}</span></li>
        return <li className={`assistant-event${kind === 'error' ? ' error' : ''}`} key={message.id}>{message.content}</li>
      }
      return <Fragment key={message.id}>
        {(message.content || message.reasoning) && <li className="assistant-message reply">
          <span className="assistant-author"><Bot size={14} /> {name}</span>
          {message.reasoning && <details className="assistant-reasoning"><summary>Thinking</summary><p>{message.reasoning}</p></details>}
          {message.content && <LazyMarkdown content={message.content} />}
        </li>}
        {message.tool_calls?.length ? <li className="assistant-tools"><ol>{message.tool_calls.map((call) => {
          const approval = approvals.get(call.id)
          return approval?.state === 'pending' && !results.has(call.id)
            ? <ApprovalCard approval={approval} canDecide={canDecide} deciding={deciding} key={call.id} onDecide={(approve) => onDecide(approval.id, approve)} />
            : <ToolRow approval={approval} call={call} key={call.id} result={results.get(call.id)} running={running} />
        })}</ol></li> : null}
      </Fragment>
    })}
    {detail.state === 'running' && progress && <li className="assistant-message reply live" aria-live="polite">
      <span className="assistant-author"><Bot size={14} /> {name}</span>
      {progress.reasoning && !progress.content && <p className="assistant-status-line"><LoaderCircle className="spin" size={13} /> Thinking…</p>}
      {progress.content && <LazyMarkdown content={progress.content} />}
      {progress.tool_call && <p className="assistant-status-line"><LoaderCircle className="spin" size={13} /> Preparing <code>{progress.tool_call.name || 'a tool call'}</code>…</p>}
      {progress.running_tool && <p className="assistant-status-line"><LoaderCircle className="spin" size={13} /> Running <code>{progress.running_tool}</code>…</p>}
      {progress.retrying && <p className="assistant-status-line warning"><LoaderCircle className="spin" size={13} /> {progress.retrying.error.replace(/\.$/, '')}. Trying again ({progress.retrying.attempt + 1})…</p>}
      {!progress.retrying && !progress.content && !progress.reasoning && !progress.tool_call && !progress.running_tool && <p className="assistant-status-line"><LoaderCircle className="spin" size={13} /> Waiting for {name}…</p>}
    </li>}
  </ol>
}

function BriefSummary({ brief, profiles, onEdit, canEdit }: { brief: Brief; profiles: Map<string, string>; onEdit: () => void; canEdit: boolean }) {
  const targets = briefTargets(brief)
  const subject = brief.subject === 'model'
    ? `${brief.model?.repo_id}${brief.model?.quantization ? ` · ${brief.model.quantization}` : ''}`
    : profiles.get(brief.profile_id ?? '') ?? 'a profile'
  return <div className="assistant-brief">
    <div>
      <small>{brief.subject === 'model' ? 'Get working' : 'Optimise'}</small>
      <strong>{subject}</strong>
      <span className="brief-chips">{targets.length ? targets.map((target) => <span className="profile-tag" key={target}>{target}</span>) : <span className="brief-none">No targets set</span>}<span className="brief-mode">{brief.autonomous === false ? 'Asks before big decisions' : 'Works without asking'}</span></span>
    </div>
    <button className="button secondary compact" disabled={!canEdit} onClick={onEdit} title={canEdit ? undefined : 'Stop the assistant before changing the brief'} type="button"><Pencil size={14} /> Edit brief</button>
  </div>
}

export function AssistantPanel({ status, canWrite, canDeleteRuns = false }: { status: AssistantStatus; canWrite: boolean; canDeleteRuns?: boolean }) {
  const [tab, setTab] = useState<'chat' | 'research' | 'reports'>('chat')
  return <div className="assistant-page">
    <div aria-label="Assistant" className="segmented assistant-tabs" role="tablist">
      <button aria-selected={tab === 'chat'} onClick={() => setTab('chat')} role="tab" type="button"><MessagesSquare size={14} /> Chat</button>
      <button aria-selected={tab === 'research'} onClick={() => setTab('research')} role="tab" type="button"><BookOpen size={14} /> Research</button>
      <button aria-selected={tab === 'reports'} onClick={() => setTab('reports')} role="tab" type="button"><FileBarChart size={14} /> Reports</button>
    </div>
    {tab === 'chat' && <ChatPanel canDeleteRuns={canDeleteRuns} canWrite={canWrite} status={status} />}
    {tab === 'research' && <ResearchPanel canWrite={canWrite} />}
    {tab === 'reports' && <ReportsPanel canWrite={canWrite} />}
  </div>
}

function ChatPanel({ status, canWrite, canDeleteRuns }: { status: AssistantStatus; canWrite: boolean; canDeleteRuns: boolean }) {
  const queryClient = useQueryClient()
  const conversations = useQuery({
    queryKey: ['assistant-conversations'],
    queryFn: api.assistantConversations,
    refetchInterval: (query) => query.state.data?.some((item) => isBusy(item.state)) ? 2000 : false,
  })
  const [selected, setSelected] = useState<string>()
  const current = selected ?? conversations.data?.[0]?.id
  const detail = useQuery({
    queryKey: ['assistant-conversation', current],
    queryFn: () => api.assistantConversation(current!),
    enabled: Boolean(current),
    refetchInterval: (query) => query.state.data && isBusy(query.state.data.state) ? 1000 : false,
  })
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: api.profiles })
  const profileNames = useMemo(() => new Map((profiles.data ?? []).map((profile) => [profile.id, profile.alias])), [profiles.data])
  const [dialog, setDialog] = useState<'new' | 'edit' | 'rerun' | 'cleanup'>()
  // What the last clean-up did, shown until another conversation is opened.
  const [cleaned, setCleaned] = useState<{ conversation: string; deleted: number; skipped: number }>()
  const [draft, setDraft] = useState('')
  const settle = (conversation?: AssistantConversationDetail) => {
    if (conversation) queryClient.setQueryData(['assistant-conversation', conversation.id], conversation)
    return queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] })
  }
  const create = useMutation({
    mutationFn: async ({ brief, title, message }: { brief: Brief; title: string; message: string }) => {
      const created = await api.createAssistantConversation(brief, title)
      return message ? api.sendAssistantMessage(created.id, message) : created
    },
    onSuccess: async (conversation) => { setSelected(conversation.id); setDialog(undefined); await settle(conversation) },
  })
  const editBrief = useMutation({
    mutationFn: (brief: Brief) => api.updateAssistantBrief(current!, brief),
    onSuccess: async (conversation) => { setDialog(undefined); await settle(conversation) },
  })
  const send = useMutation({
    mutationFn: (content: string) => api.sendAssistantMessage(current!, content),
    onSuccess: async (conversation) => { setDraft(''); await settle(conversation) },
  })
  const stop = useMutation({ mutationFn: () => api.stopAssistant(current!), onSuccess: settle })
  const resume = useMutation({ mutationFn: () => api.continueAssistant(current!), onSuccess: settle })
  const retry = useMutation({ mutationFn: () => api.retryAssistant(current!), onSuccess: settle })
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) => api.decideAssistantApproval(id, approve),
    onSuccess: settle,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAssistantConversation(id),
    onSuccess: async () => { setSelected(undefined); await queryClient.invalidateQueries({ queryKey: ['assistant-conversations'] }) },
  })
  const cleanup = useMutation({
    mutationFn: (id: string) => api.deleteConversationRuns(id),
    onSuccess: async (result, id) => {
      setDialog(undefined)
      setCleaned({ conversation: id, deleted: result.deleted.length, skipped: result.skipped.length })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['assistant-conversation', id] }),
        queryClient.invalidateQueries({ queryKey: ['benchmark-runs'] }),
      ])
    },
  })
  const conversation = detail.data
  const running = conversation ? isBusy(conversation.state) : false
  const runCount = conversation?.benchmark_runs ?? 0
  const end = useRef<HTMLDivElement>(null)
  const lastSeen = `${conversation?.messages.length}:${conversation?.progress?.content.length ?? 0}`
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }) }, [lastSeen])
  const error = send.error?.message ?? stop.error?.message ?? resume.error?.message ?? retry.error?.message ?? remove.error?.message ?? decide.error?.message
  const firstMessage = conversation?.messages.find((message) => message.role === 'user')?.content
  const submit = () => { if (draft.trim() && !send.isPending) send.mutate(draft.trim()) }

  return <section className="data-panel assistant-panel">
    <aside className="assistant-sidebar" aria-label="Conversations">
      <div className="assistant-sidebar-head">
        <strong>Conversations</strong>
        <button className="button primary compact" disabled={!canWrite} onClick={() => setDialog('new')} title={canWrite ? undefined : 'Starting a conversation needs the assistant.write privilege'} type="button"><Plus size={14} /> New</button>
      </div>
      <label className="assistant-picker"><span className="sr-only">Conversation</span>
        <select onChange={(event) => setSelected(event.target.value)} value={current ?? ''}>
          {!conversations.data?.length && <option value="">No conversations</option>}
          {conversations.data?.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </label>
      <ul className="assistant-list">
        {conversations.data?.map((item) => <li key={item.id}>
          <button aria-current={item.id === current ? 'true' : undefined} onClick={() => setSelected(item.id)} type="button">
            <strong>{item.title}</strong>
            <span>{STATE_LABELS[item.state]} · {formatCost(item.cost_usd)}</span>
          </button>
        </li>)}
      </ul>
    </aside>
    <div className="assistant-main">
      {!conversations.isLoading && !conversations.data?.length
        ? <div className="empty"><Bot size={28} /><strong>No conversations yet</strong><span>Start one with a brief: the model or profile to work on, and the targets to reach.</span>
          <button className="button primary" disabled={!canWrite} onClick={() => setDialog('new')} type="button"><Plus size={15} /> New optimisation</button></div>
        : !conversation
          ? <div className="empty"><LoaderCircle className="spin" size={22} /><span>Loading the conversation…</span></div>
          : <>
            <header className="assistant-header">
              <div>
                <h2>{conversation.title}</h2>
                <p>
                  <span className={`state-pill ${conversation.state === 'running' ? 'loading' : conversation.state === 'failed' ? 'failed' : conversation.state === 'idle' ? 'ready' : 'warning'}`}>{STATE_LABELS[conversation.state]}</span>
                  <span>{status.model?.name ?? conversation.model_id ?? 'No model'}{status.model ? ` · ${priceSummary(status.model.pricing)}` : ''}</span>
                  <span>Spent {formatCost(conversation.cost_usd)} of {formatCost(conversation.spend_limit_usd)}</span>
                </p>
              </div>
              <div className="assistant-header-actions">
                {canDeleteRuns && runCount > 0 && <button className="button secondary compact" disabled={running} onClick={() => setDialog('cleanup')} title={running ? 'Stop the assistant first' : 'Delete the benchmark runs this conversation started'} type="button"><Eraser size={14} /> Delete {runCount} {runCount === 1 ? 'run' : 'runs'}</button>}
                <button className="button secondary compact" disabled={!canWrite} onClick={() => setDialog('rerun')} title="Start a new conversation with this brief and first message" type="button"><RotateCcw size={14} /> Run again</button>
                <button aria-label={`Delete ${conversation.title}`} className="icon-button small danger-icon" disabled={!canWrite || running || remove.isPending} onClick={() => remove.mutate(conversation.id)} title="Delete conversation" type="button"><Trash2 size={14} /></button>
              </div>
            </header>
            <BriefSummary brief={conversation.brief} canEdit={canWrite && !running} onEdit={() => setDialog('edit')} profiles={profileNames} />
            <div className="assistant-scroll">
              {conversation.messages.length || running
                ? <Messages canDecide={canWrite} deciding={decide.isPending} detail={conversation} name={status.name} onDecide={(id, approve) => decide.mutate({ id, approve })} />
                : <p className="assistant-hint">Tell {status.name} what to do first, for example “Check this machine and suggest where to start.”</p>}
              <div ref={end} />
            </div>
            {conversation.state === 'paused' && <div className="inline-notice assistant-notice"><AlertCircle size={15} /><span>This conversation reached its spending limit. Continuing raises the limit by the amount set in Settings.</span><button className="button primary compact" disabled={!canWrite || resume.isPending} onClick={() => resume.mutate()} type="button"><Play size={14} /> Continue</button></div>}
            {(conversation.state === 'failed' || conversation.state === 'interrupted') && <div className="inline-notice assistant-notice"><AlertCircle size={15} /><span>{conversation.state === 'failed' ? 'The last reply failed; the reason is shown above.' : 'This conversation stopped before it finished, for example because the app restarted.'}</span><button className="button primary compact" disabled={!canWrite || retry.isPending} onClick={() => retry.mutate()} type="button">{retry.isPending ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />} Try again</button></div>}
            {cleaned?.conversation === conversation.id && <div className="research-notice cleanup-notice" role="status"><Check size={15} /><span>Deleted {cleaned.deleted} benchmark {cleaned.deleted === 1 ? 'run' : 'runs'}{cleaned.skipped ? `; ${cleaned.skipped} still running ${cleaned.skipped === 1 ? 'was' : 'were'} kept` : ''}.</span><button aria-label="Dismiss" className="icon-button small" onClick={() => setCleaned(undefined)} type="button"><X size={14} /></button></div>}
            {error && <div className="form-error assistant-notice" role="alert"><AlertCircle size={15} /> {error}</div>}
            <form className="assistant-composer" onSubmit={(event) => { event.preventDefault(); submit() }}>
              <label className="sr-only" htmlFor="assistant-draft">Message</label>
              <textarea disabled={!canWrite || running || conversation.state === 'paused'} id="assistant-draft" onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }} placeholder={`Message ${status.name}…`} rows={2} value={draft} />
              {running
                ? <button className="button danger" disabled={stop.isPending} onClick={() => stop.mutate()} type="button"><Square fill="currentColor" size={13} /> Stop</button>
                : <button className="button primary" disabled={!canWrite || !draft.trim() || send.isPending || conversation.state === 'paused'} type="submit">{send.isPending ? <LoaderCircle className="spin" size={15} /> : <Send size={15} />} Send</button>}
            </form>
          </>}
    </div>
    {dialog === 'new' && <BriefDialog creating error={create.error?.message} onClose={() => setDialog(undefined)} onSave={(brief, title, message) => create.mutate({ brief, title, message })} saving={create.isPending} title="New optimisation" />}
    {dialog === 'rerun' && conversation && <BriefDialog creating error={create.error?.message} initial={conversation.brief} initialMessage={firstMessage} onClose={() => setDialog(undefined)} onSave={(brief, title, message) => create.mutate({ brief, title, message })} saving={create.isPending} title="Run again" />}
    {dialog === 'cleanup' && conversation && <Dialog description="Their timings and output are removed. The latest result already written to each profile is kept, and so are reports and research." onClose={() => setDialog(undefined)} title={`Delete ${runCount} benchmark ${runCount === 1 ? 'run' : 'runs'}?`}>
      <div className="confirm-body"><Eraser size={24} /><p>Every benchmark run <strong>{conversation.title}</strong> started will be deleted. A run still going is kept.</p>{cleanup.error && <div className="form-error"><AlertCircle size={15} /> {cleanup.error.message}</div>}</div>
      <footer className="dialog-actions"><button className="button secondary" onClick={() => setDialog(undefined)} type="button">Keep runs</button><button className="button danger" disabled={cleanup.isPending} onClick={() => cleanup.mutate(conversation.id)} type="button">{cleanup.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete runs</button></footer>
    </Dialog>}
    {dialog === 'edit' && conversation && <BriefDialog creating={false} error={editBrief.error?.message} initial={conversation.brief} onClose={() => setDialog(undefined)} onSave={(brief) => editBrief.mutate(brief)} saving={editBrief.isPending} title="Edit brief" />}
  </section>
}
