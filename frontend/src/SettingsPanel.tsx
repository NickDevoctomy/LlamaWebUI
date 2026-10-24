import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Download, FileCheck2, LoaderCircle, Settings, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api, type LinterStatus } from './api'
import { formatBytes } from './Dashboard'

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

/** ESLint, downloaded on request, gives benchmark agents the check_file tool. */
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
      <strong className="record-title">ESLint {eslint.state === 'installed' && <span className="profile-tag">Installed</span>}</strong>
      <span className="record-description">Lints JavaScript, and the scripts inside HTML pages, for the <code>check_file</code> benchmark tool. Runs on its own copy of Node.js, kept in the data folder.</span>
      {versions && <span title={eslint.state === 'installed' ? 'Installed versions' : 'Versions an install fetches'}>{eslint.state === 'installed' ? '' : 'Fetches '}Node.js {versions.node} · eslint {versions.eslint} · eslint-plugin-html {versions['eslint-plugin-html']}{eslint.size ? ` · ${formatBytes(eslint.size)}` : ''}</span>}
    </div>
    <span className="row-actions">
      {eslint.state === 'installed'
        ? <button className="button secondary compact" disabled={!canManage || busy} onClick={() => remove.mutate()} type="button">{remove.isPending ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />} Remove</button>
        : <button className="button primary compact" disabled={!canManage || busy} onClick={() => install.mutate()} title={canManage ? undefined : 'Changing settings needs the settings.write privilege'} type="button">{busy ? <LoaderCircle className="spin" size={14} /> : <Download size={14} />} {eslint.state === 'failed' ? 'Try again' : 'Download'}</button>}
    </span>
    {eslint.progress && <InstallProgress progress={eslint.progress} />}
    {error && eslint.state !== 'installing' && <div className="form-error record-details" role="alert"><AlertCircle size={15} /> {error}</div>}
  </div>
}

export function SettingsPanel({ canManage }: { canManage: boolean }) {
  const [tab, setTab] = useState<'benchmark'>('benchmark')
  return <section className="data-panel settings-panel">
    <div className="panel-heading"><div><h2>Settings</h2><p>Control-plane preferences and the tools features rely on.</p></div><Settings size={20} /></div>
    <div aria-label="Settings" className="segmented settings-tabs" role="tablist">
      <button aria-selected={tab === 'benchmark'} onClick={() => setTab('benchmark')} role="tab" type="button">Benchmark</button>
    </div>
    {tab === 'benchmark' && <div role="tabpanel" aria-label="Benchmark">
      <div className="settings-section"><h3>Tools</h3><p>Extra tools benchmark agents can be given. Each one is offered in a run&apos;s tool list once it is installed.</p></div>
      <div className="record-list"><EslintRow canManage={canManage} /></div>
    </div>}
  </section>
}
