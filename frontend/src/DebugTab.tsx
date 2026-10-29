import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertCircle, CirclePlay, Cpu, Network, Download, FileText, Info, LoaderCircle, type LucideIcon, Play, Power, Save, ShieldCheck, Square, Timer, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type ProfilerConfig, type ProfilerStatus } from './api'
import { formatBytes } from './Dashboard'

const DIAGNOSTICS_WRITE_HINT = 'Changing debug settings needs the diagnostics.write privilege'

type Toggle = 'capture_on_start' | 'cpu_sampling' | 'graph_nodes'

const TOGGLES: { key: Toggle; icon: LucideIcon; label: string; description: string }[] = [
  { key: 'capture_on_start', icon: CirclePlay, label: 'Capture from start', description: 'Records from the moment the router is ready and saves the report when it stops, so a benchmark run gives one report. Off: start and stop captures here.' },
  { key: 'graph_nodes', icon: Network, label: 'Kernels inside CUDA graphs', description: "Shows each kernel inside llama.cpp's CUDA graphs, not one block per graph. Slows the router down, so leave it off when timing matters." },
  { key: 'cpu_sampling', icon: Cpu, label: 'CPU call stacks', description: "Samples llama.cpp's threads with their call stacks, beside the GPU timeline. Needs the backend to run as administrator." },
]

function elapsed(since: string, now: number) {
  const seconds = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

function CaptureRow({ status, canManage }: { status: ProfilerStatus; canManage: boolean }) {
  const queryClient = useQueryClient()
  const update = (next: ProfilerStatus) => queryClient.setQueryData(['profiler'], next)
  const start = useMutation({ mutationFn: api.startCapture, onSuccess: update })
  const stop = useMutation({ mutationFn: api.stopCapture, onSuccess: update })
  const capture = status.capture
  const saving = status.saving
  const now = useNow(Boolean(capture ?? saving))
  const hint = canManage ? undefined : DIAGNOSTICS_WRITE_HINT
  const error = start.error?.message ?? stop.error?.message
  const description = saving
    ? <>Nsight Systems is writing <code>{saving.name}</code>. With CPU call stacks this can take minutes, and if the router is stopping it waits for the report. Leave it to finish.</>
    : capture
    ? <>Recording to <code>{capture.name}</code>. Stopping writes the report, which can take a while with CPU call stacks.</>
    : status.router_profiled
      ? 'The router is running under Nsight Systems. Start a capture, run what you want to measure, then stop it.'
      : status.config.enabled
        ? 'Start the router, or restart it if it is running, to profile it.'
        : 'Profiling is off; this capture is the last one for this router run.'
  return <div className="record-row">
    <span className="record-icon"><Timer size={17} /></span>
    <div className="record-copy">
      <strong className="record-title">Capture {saving
        ? <span className="profile-tag warning-tag">Saving report · {elapsed(saving.started_at, now)}</span>
        : capture
        ? <span className="profile-tag recommended-tag">Recording · {elapsed(capture.started_at, now)}</span>
        : <span className="profile-tag">{status.router_profiled ? 'Ready' : 'Router not profiled'}</span>}</strong>
      <span className="record-description">{description}</span>
    </div>
    <span className="row-actions">
      {saving
        ? <button className="button secondary compact" disabled type="button"><LoaderCircle className="spin" size={14} /> Saving…</button>
        : capture
        ? <button className="button primary compact" disabled={!canManage || stop.isPending} onClick={() => stop.mutate()} title={hint} type="button">{stop.isPending ? <LoaderCircle className="spin" size={14} /> : <Square size={14} />} {stop.isPending ? 'Saving…' : 'Stop and save'}</button>
        : <button className="button secondary compact" disabled={!canManage || !status.router_profiled || start.isPending} onClick={() => start.mutate()} title={hint} type="button">{start.isPending ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />} Start capture</button>}
    </span>
    {(error ?? status.last_error) && <div className="form-error record-details" role="alert"><AlertCircle size={15} /> {error ?? status.last_error}</div>}
  </div>
}

function ReportList({ status, canManage }: { status: ProfilerStatus; canManage: boolean }) {
  const queryClient = useQueryClient()
  const remove = useMutation({ mutationFn: api.deleteCapture, onSuccess: (next) => queryClient.setQueryData(['profiler'], next) })
  if (!status.captures.length) return <div className="settings-body"><div className="capability-note" role="status"><Info size={16} /><span>No reports yet. They are saved in the data folder under <code>debug\captures</code>.</span></div></div>
  return <>
    <div className="record-list">{status.captures.map((capture) => <div className="record-row" key={capture.name}>
      <span className="record-icon"><FileText size={17} /></span>
      <div className="record-copy">
        <strong className="record-title">{capture.name}</strong>
        <span title={capture.path}>{formatBytes(capture.size)} · {new Date(capture.created_at).toLocaleString()}</span>
      </div>
      <span className="row-actions">
        <a className="button secondary compact" download={capture.name} href={api.captureUrl(capture.name)}><Download size={14} /> Download</a>
        <button aria-label={`Delete ${capture.name}`} className="button secondary compact" disabled={!canManage || remove.isPending || status.capture?.name === capture.name} onClick={() => remove.mutate(capture.name)} title={canManage ? 'Delete' : DIAGNOSTICS_WRITE_HINT} type="button"><Trash2 size={14} /></button>
      </span>
    </div>)}</div>
    {remove.error && <div className="settings-body"><div className="form-error" role="alert"><AlertCircle size={15} /> {remove.error.message}</div></div>}
  </>
}

/** Nsight Systems profiling of the router: settings, the current capture and saved reports. */
export function DebugTab({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const profiler = useQuery({ queryKey: ['profiler'], queryFn: api.profiler, refetchInterval: (query) => query.state.data?.saving ? 1000 : 3000 })
  const [draft, setDraft] = useState<{ nsys_path: string; trace: string; sampling_hz: string }>()
  const status = profiler.data
  const saved = status?.config
  const save = useMutation({
    mutationFn: (config: ProfilerConfig) => api.updateProfiler(config),
    onSuccess: (next) => { queryClient.setQueryData(['profiler'], next); setDraft(undefined) },
  })
  if (profiler.isError) return <div className="settings-body"><div className="capability-note error" role="alert"><AlertCircle size={16} /><span>{profiler.error.message}</span></div></div>
  if (!status || !saved) return <div className="settings-body"><div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading debug settings…</span></div></div>
  const values = draft ?? { nsys_path: saved.nsys_path, trace: saved.trace, sampling_hz: String(saved.sampling_hz) }
  const samplingHz = Number(values.sampling_hz)
  const samplingValid = Number.isInteger(samplingHz) && samplingHz >= 100 && samplingHz <= 8000
  const hint = canManage ? undefined : DIAGNOSTICS_WRITE_HINT
  const found = status.nsys.path !== null
  const enabled = saved.enabled
  // Off hides the setup, but a router still running under the profiler keeps its capture controls.
  const showCapture = enabled || status.router_profiled || Boolean(status.capture ?? status.saving)
  const state = !enabled
    ? status.router_profiled ? 'Off from next start' : 'Off'
    : status.router_profiled ? 'Profiling the router' : 'On from next start'
  return <>
    <div className="settings-section"><h3>Nsight Systems profiler</h3><p>Records a timeline of the router: llama.cpp&apos;s threads and every CUDA kernel and memory copy it runs. Open the reports in Nsight Systems.</p></div>
    <div className="record-list">
      <div className="record-row">
        <span className="record-icon"><Power size={17} /></span>
        <div className="record-copy">
          <strong className="record-title">Profiling <span className={enabled ? 'profile-tag recommended-tag' : 'profile-tag'}>{state}</span></strong>
          <span className="record-description">{enabled
            ? 'The router starts under Nsight Systems, which follows it into each model process. Switching it on or off takes effect the next time the router starts.'
            : 'The router runs normally. Switch this on, then start or restart the router, to profile it.'}</span>
        </div>
        <span className="row-actions">
          <button aria-checked={enabled} aria-label="Profiling" className="profile-switch" disabled={!canManage || save.isPending} onClick={() => save.mutate({ ...saved, enabled: !enabled })} role="switch" title={hint} type="button">
            <span aria-hidden="true" className="switch-track"><span /></span>{enabled ? 'On' : 'Off'}
          </button>
        </span>
        {save.error && !enabled && <div className="form-error record-details" role="alert"><AlertCircle size={15} /> {save.error.message}</div>}
        {status.last_error && !showCapture && <div className="form-error record-details" role="alert"><AlertCircle size={15} /> {status.last_error}</div>}
      </div>
    </div>
    {enabled && <>
    <div className="settings-section"><h3>Setup</h3><p>What each profiled router run records. Changes take effect the next time the router starts.</p></div>
    <div className="record-list">
      <div className="record-row">
        <span className="record-icon"><Activity size={17} /></span>
        <div className="record-copy">
          <strong className="record-title">Nsight Systems <span className={found ? 'profile-tag recommended-tag' : 'profile-tag warning-tag'}>{found ? status.nsys.version ?? 'Found' : 'Not found'}</span></strong>
          {found
            ? <span title={status.nsys.path ?? undefined}>{status.nsys.path}</span>
            : <span className="record-description">Install it from <a href="https://developer.nvidia.com/nsight-systems" rel="noreferrer" target="_blank">developer.nvidia.com/nsight-systems</a>, or set the path to nsys.exe below.</span>}
        </div>
      </div>
      <div className="record-row">
        <span className="record-icon"><ShieldCheck size={17} /></span>
        <div className="record-copy">
          <strong className="record-title">Administrator rights <span className={status.elevated ? 'profile-tag recommended-tag' : 'profile-tag'}>{status.elevated ? 'Yes' : 'No'}</span></strong>
          <span className="record-description">{status.elevated
            ? 'Captures can include CPU call stacks.'
            : 'The backend is not running as administrator, so captures record the GPU timeline only. Start it from an elevated prompt for CPU call stacks.'}</span>
        </div>
      </div>
      {TOGGLES.map(({ key, icon: Icon, label, description }) => <div className="record-row" key={key}>
        <span className="record-icon"><Icon size={17} /></span>
        <div className="record-copy"><strong className="record-title">{label}</strong><span className="record-description">{description}</span></div>
        <span className="row-actions">
          <button aria-checked={saved[key]} aria-label={label} className="profile-switch" disabled={!canManage || save.isPending} onClick={() => save.mutate({ ...saved, [key]: !saved[key] })} role="switch" title={hint} type="button">
            <span aria-hidden="true" className="switch-track"><span /></span>{saved[key] ? 'On' : 'Off'}
          </button>
        </span>
      </div>)}
    </div>
    <form className="secret-form assistant-settings-form" onSubmit={(event) => { event.preventDefault(); save.mutate({ ...saved, nsys_path: values.nsys_path.trim(), trace: values.trace.trim(), sampling_hz: samplingHz }) }}>
      <label className="form-field"><span>nsys.exe path</span><input autoComplete="off" disabled={!canManage} onChange={(event) => setDraft({ ...values, nsys_path: event.target.value })} placeholder="Found automatically" spellCheck={false} title={hint} value={values.nsys_path} /><small>Leave empty to use the newest install found.</small></label>
      <label className="form-field"><span>Trace</span><input autoComplete="off" disabled={!canManage} onChange={(event) => setDraft({ ...values, trace: event.target.value })} spellCheck={false} title={hint} value={values.trace} /><small>What nsys records, for example <code>cuda,nvtx,cublas</code>.</small></label>
      <label className="form-field"><span>CPU samples per second</span><input disabled={!canManage} inputMode="numeric" max="8000" min="100" onChange={(event) => setDraft({ ...values, sampling_hz: event.target.value })} step="50" title={hint} type="number" value={values.sampling_hz} /><small>100–8,000. Lower saves faster; 250 suits a whole benchmark.</small></label>
      <button className="button primary" disabled={!canManage || !draft || !values.trace.trim() || !samplingValid || save.isPending} title={hint} type="submit">{save.isPending ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />} Save</button>
      {save.error && <div className="form-error" role="alert"><AlertCircle size={15} /> {save.error.message}</div>}
    </form>
    </>}
    {showCapture && <>
    <div className="settings-section"><h3>Capture</h3><p>What is being recorded now.</p></div>
    <div className="record-list"><CaptureRow canManage={canManage} status={status} /></div>
    </>}
    <div className="settings-section"><h3>Reports</h3><p>Saved captures, newest first. Each one opens in Nsight Systems.</p></div>
    <ReportList canManage={canManage} status={status} />
  </>
}
