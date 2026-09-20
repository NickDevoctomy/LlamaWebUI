import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Download, FileArchive, FileCog, Heart, LoaderCircle, Pause, Play, Search, X } from 'lucide-react'
import { FormEvent, useState } from 'react'
import { api, type DownloadJob, type LibraryModel, type ModelSearchResult } from './api'

function formatBytes(bytes: number) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`
}

function formatCount(value: number) {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

export function DiscoverPanel({ onQueued }: { onQueued: () => void }) {
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('downloads')
  const [selected, setSelected] = useState<ModelSearchResult | null>(null)
  const search = useQuery({
    queryKey: ['huggingface-search', query, sort],
    queryFn: () => api.searchModels(query, sort),
    enabled: Boolean(query),
  })
  const manifest = useQuery({
    queryKey: ['huggingface-repository', selected?.repo_id],
    queryFn: () => api.repository(selected!.repo_id),
    enabled: Boolean(selected),
  })
  const create = useMutation({
    mutationFn: ({ groupKey, revision }: { groupKey: string; revision: string }) =>
      api.createDownload(selected!.repo_id, groupKey, revision),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['downloads'] })
      onQueued()
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const nextQuery = input.trim()
    if (!nextQuery) return
    setSelected(null)
    setQuery(nextQuery)
  }

  return (
    <div className="discover-layout">
      <section className="data-panel discover-results">
        <div className="panel-heading discover-heading">
          <div><h2>Hugging Face catalog</h2><p>Search public GGUF repositories without leaving the control plane.</p></div>
        </div>
        <form className="catalog-search" onSubmit={submit}>
          <label><span className="sr-only">Search models</span><Search size={16} /><input onChange={(event) => setInput(event.target.value)} placeholder="Search models, authors, or architectures" value={input} /></label>
          <select aria-label="Sort search results" onChange={(event) => setSort(event.target.value)} value={sort}>
            <option value="downloads">Most downloaded</option>
            <option value="likes">Most liked</option>
            <option value="last_modified">Recently updated</option>
            <option value="trending_score">Trending</option>
          </select>
          <button className="button primary" disabled={!input.trim() || search.isFetching} type="submit">{search.isFetching ? <LoaderCircle className="spin" size={15} /> : <Search size={15} />} Search</button>
        </form>
        {search.error && <div className="form-error"><AlertCircle size={15} /> {search.error.message}</div>}
        {!query ? <div className="empty catalog-empty"><Search size={28} /><strong>Find a GGUF model</strong><span>Results stay idle until you search. No model is downloaded automatically.</span></div>
          : search.isLoading ? <div className="empty catalog-empty"><LoaderCircle className="spin" size={28} /><strong>Searching Hugging Face</strong></div>
            : search.data?.length ? <div className="catalog-list">{search.data.map((result) => (
              <button className={selected?.repo_id === result.repo_id ? 'catalog-row selected' : 'catalog-row'} key={result.repo_id} onClick={() => setSelected(result)} type="button">
                <span className="record-icon"><FileArchive size={17} /></span>
                <span className="catalog-copy"><strong>{result.repo_id}</strong><small>{result.tags.slice(0, 4).join(' · ') || 'GGUF repository'}</small></span>
                <span className="catalog-stat"><Download size={13} /> {formatCount(result.downloads)}</span>
                <span className="catalog-stat"><Heart size={13} /> {formatCount(result.likes)}</span>
              </button>
            ))}</div> : <div className="empty catalog-empty"><Search size={28} /><strong>No GGUF repositories found</strong><span>Try a broader model or author name.</span></div>}
      </section>

      <section className="data-panel repository-panel">
        <div className="panel-heading"><div><h2>Quantizations</h2><p>{selected ? selected.repo_id : 'Select a repository to inspect its files.'}</p></div></div>
        {!selected ? <div className="empty catalog-empty"><FileArchive size={28} /><strong>No repository selected</strong><span>Choose a search result to inspect complete GGUF groups and exact sizes.</span></div>
          : manifest.isLoading ? <div className="empty catalog-empty"><LoaderCircle className="spin" size={28} /><strong>Reading repository manifest</strong></div>
            : manifest.error ? <div className="integration-empty error-state"><AlertCircle size={25} /><strong>Manifest unavailable</strong><span>{manifest.error.message}</span></div>
              : <div className="quant-list">{manifest.data?.groups.map((group) => (
                <article className="quant-row" key={group.key}>
                  <div><strong>{group.quantization}</strong><span>{group.files.length} {group.files.length === 1 ? 'file' : 'files'} · {formatBytes(group.total_size)}</span></div>
                  <span className={`state-pill ${group.complete ? 'ready' : 'error'}`}>{group.complete ? 'Complete' : 'Incomplete'}</span>
                  <button className="button secondary compact" disabled={!group.complete || create.isPending} onClick={() => create.mutate({ groupKey: group.key, revision: manifest.data!.revision })} type="button"><Download size={14} /> Download</button>
                </article>
              ))}{!manifest.data?.groups.length && <div className="empty catalog-empty"><FileArchive size={28} /><strong>No GGUF groups found</strong></div>}</div>}
        {create.error && <div className="form-error"><AlertCircle size={15} /> {create.error.message}</div>}
      </section>
    </div>
  )
}

export function DownloadsPanel({ jobs, library, onCreateProfile }: {
  jobs: DownloadJob[]
  library: LibraryModel[]
  onCreateProfile: (model: LibraryModel) => void
}) {
  const queryClient = useQueryClient()
  const action = useMutation({
    mutationFn: ({ job, operation }: { job: DownloadJob; operation: 'pause' | 'resume' | 'cancel' }) =>
      operation === 'pause' ? api.pauseDownload(job.id) : operation === 'resume' ? api.resumeDownload(job.id) : api.cancelDownload(job.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['downloads'] }),
  })

  return (
    <section className="data-panel">
      <div className="panel-heading"><div><h2>Download jobs</h2><p>Revision-pinned transfers publish only after every shard is validated.</p></div><span className="profile-tag">{jobs.filter((job) => ['queued', 'downloading'].includes(job.state)).length} active</span></div>
      {action.error && <div className="form-error"><AlertCircle size={15} /> {action.error.message}</div>}
      {jobs.length ? <div className="download-list">{jobs.map((job) => {
        const percent = job.total_bytes ? Math.min(100, Math.round(job.completed_bytes / job.total_bytes * 100)) : 0
        const pending = action.isPending && action.variables?.job.id === job.id
        const model = library.find((item) => item.download_id === job.id)
        return <article className="download-row" key={job.id}>
          <span className="record-icon"><Download size={17} /></span>
          <div className="download-copy"><strong>{job.repo_id}</strong><span>{job.group_key} · {job.revision.slice(0, 9)}</span>{job.error && <small>{job.error}</small>}</div>
          <div className="download-progress"><div><span style={{ width: `${percent}%` }} /></div><small>{formatBytes(job.completed_bytes)} / {formatBytes(job.total_bytes)} · {percent}%</small></div>
          <span className={`state-pill ${job.state}`}>{job.state}</span>
          <div className="download-actions">
            {model && <button aria-label={`Create profile for ${job.repo_id}`} className="button secondary compact" onClick={() => onCreateProfile(model)} type="button"><FileCog size={14} /> Configure</button>}
            {job.state === 'downloading' && <button aria-label={`Pause ${job.repo_id}`} className="icon-button small" disabled={pending} onClick={() => action.mutate({ job, operation: 'pause' })} title="Pause download" type="button"><Pause size={15} /></button>}
            {['paused', 'failed'].includes(job.state) && <button aria-label={`Resume ${job.repo_id}`} className="icon-button small" disabled={pending} onClick={() => action.mutate({ job, operation: 'resume' })} title="Resume download" type="button"><Play size={15} /></button>}
            {['queued', 'downloading', 'paused'].includes(job.state) && <button aria-label={`Cancel ${job.repo_id}`} className="icon-button small danger-icon" disabled={pending} onClick={() => action.mutate({ job, operation: 'cancel' })} title="Cancel download" type="button"><X size={16} /></button>}
          </div>
        </article>
      })}</div> : <div className="empty"><Download size={28} /><strong>No download jobs</strong><span>Search Hugging Face in Discover, inspect a quantization, then start a revision-pinned download.</span></div>}
      <div className="panel-footer"><span>{jobs.length} jobs</span><span>Validated publication</span></div>
    </section>
  )
}