import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, BookOpen, Check, ExternalLink, FileBarChart, LoaderCircle, Search as SearchIcon, Trash2, Upload, type LucideIcon } from 'lucide-react'
import { useDeferredValue, useState, type ReactNode } from 'react'
import { api, type LibraryKind, type ReportDocument, type ReportSummary, type ResearchDocument, type ResearchSummary } from './api'
import { LazyMarkdown } from './LazyMarkdown'
import { Dialog } from './SetupPanels'

type Base = { slug: string; title: string; summary: string; tags: string[]; updated: string; origin: 'shipped' | 'local'; replaces_shipped: boolean; author: { model?: string | null } | null }

/** What makes the research and report libraries different; everything else is shared. */
interface LibraryConfig<S extends Base, D extends S & { body: string }> {
  kind: LibraryKind
  title: string
  singular: string
  plural: string
  icon: LucideIcon
  empty: string
  /** The line under each title in the list. */
  listDetail: (item: S) => string
  facts: (document: D) => [string, string][]
  /** Shown between the facts and the body, e.g. a report's results. */
  before?: (document: D) => ReactNode
  /** Shown after the body, e.g. a research document's sources. */
  after?: (document: D) => ReactNode
  charts?: boolean
}

function originLabel(document: Base) {
  if (document.origin === 'shipped') return 'Shipped'
  return document.replaces_shipped ? 'Local, replaces shipped' : 'Local'
}

const fact = (label: string, value: string | null | undefined): [string, string][] => value ? [[label, value]] : []

function Reader<S extends Base, D extends S & { body: string }>({ config, document, canPublish, canDelete, publishing, onPublish, onDelete }: {
  config: LibraryConfig<S, D>
  document: D
  canPublish: boolean
  canDelete: boolean
  publishing: boolean
  onPublish: () => void
  onDelete: () => void
}) {
  const local = document.origin === 'local'
  return <>
    <header className="assistant-header">
      <div>
        <h2>{document.title}</h2>
        <p>
          <span className={`state-pill${local ? '' : ' ready'}`}>{originLabel(document)}</span>
          {document.tags.length > 0 && <span>{document.tags.join(' · ')}</span>}
        </p>
      </div>
      {local && <div className="assistant-header-actions">
        {canPublish && <button className="button secondary compact" disabled={publishing} onClick={onPublish} title={`Move this ${config.singular} into the repository`} type="button">{publishing ? <LoaderCircle className="spin" size={14} /> : <Upload size={14} />} Publish</button>}
        <button aria-label={`Delete ${document.title}`} className="icon-button small danger-icon" disabled={!canDelete} onClick={onDelete} title={`Delete ${config.singular}`} type="button"><Trash2 size={14} /></button>
      </div>}
    </header>
    <div className="assistant-scroll research-reader">
      <p className="research-summary">{document.summary}</p>
      <dl className="research-facts">
        {[...config.facts(document), ...fact('Written by', document.author?.model)].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      {config.before?.(document)}
      <LazyMarkdown charts={config.charts} content={document.body} />
      {config.after?.(document)}
    </div>
  </>
}

function LibraryPanel<S extends Base, D extends S & { body: string }>({ config, canWrite }: { config: LibraryConfig<S, D>; canWrite: boolean }) {
  const { kind, singular, plural } = config
  const queryClient = useQueryClient()
  const [input, setInput] = useState('')
  const query = useDeferredValue(input.trim())
  const list = useQuery({ queryKey: [kind, query], queryFn: () => api.libraryDocuments<S>(kind, query), placeholderData: keepPreviousData })
  const documents = list.data?.documents ?? []
  const [selected, setSelected] = useState<string>()
  const current = documents.some((item) => item.slug === selected) ? selected : documents[0]?.slug
  const document = useQuery({ queryKey: [`${kind}-document`, current], queryFn: () => api.libraryDocument<D>(kind, current!), enabled: Boolean(current) })
  const [chosen, setChosen] = useState<string[]>([])
  const [deleting, setDeleting] = useState<Base>()
  const [published, setPublished] = useState<{ count: number; folder: string }>()
  const problem = list.data?.publish_problem ?? null
  const canPublish = canWrite && !problem
  const local = documents.filter((item) => item.origin === 'local')
  const refresh = () => queryClient.invalidateQueries({ queryKey: [kind] }).then(() => queryClient.invalidateQueries({ queryKey: [`${kind}-document`] }))
  const publish = useMutation({
    mutationFn: (slugs: string[]) => api.publishLibrary(kind, slugs),
    onSuccess: async (result) => { setChosen([]); setPublished({ count: result.published.length, folder: result.folder }); await refresh() },
  })
  const remove = useMutation({
    mutationFn: (slug: string) => api.deleteLibraryDocument(kind, slug),
    onSuccess: async () => { setDeleting(undefined); await refresh() },
  })
  const toggle = (slug: string, on: boolean) => setChosen((items) => on ? [...items, slug] : items.filter((item) => item !== slug))
  const Icon = config.icon
  const count = (n: number) => `${n} ${n === 1 ? singular : plural}`

  return <section className="data-panel assistant-panel research-panel">
    <aside className="assistant-sidebar" aria-label={config.title}>
      <div className="assistant-sidebar-head">
        <strong>{config.title}</strong>
        <span className="research-count">{count(documents.length)}</span>
      </div>
      <label className="search-box research-search"><SearchIcon aria-hidden="true" size={15} /><span className="sr-only">Search {config.title.toLowerCase()}</span><input aria-label={`Search ${config.title.toLowerCase()}`} onChange={(event) => setInput(event.target.value)} placeholder="Search titles, tags and text" type="search" value={input} /></label>
      <label className="assistant-picker"><span className="sr-only">{singular}</span>
        <select onChange={(event) => setSelected(event.target.value)} value={current ?? ''}>
          {!documents.length && <option value="">No {plural}</option>}
          {documents.map((item) => <option key={item.slug} value={item.slug}>{item.title}</option>)}
        </select>
      </label>
      <ul className="assistant-list research-list">
        {documents.map((item) => <li key={item.slug}>
          {canPublish && item.origin === 'local' && <input aria-label={`Select ${item.title} for publishing`} checked={chosen.includes(item.slug)} onChange={(event) => toggle(item.slug, event.target.checked)} type="checkbox" />}
          <button aria-current={item.slug === current ? 'true' : undefined} onClick={() => setSelected(item.slug)} type="button">
            <strong>{item.title}</strong>
            <span>{config.listDetail(item)}</span>
          </button>
        </li>)}
      </ul>
      {local.length > 0 && <div className="research-publish">
        {canPublish
          ? <button className="button primary compact" disabled={!chosen.length || publish.isPending} onClick={() => publish.mutate(chosen)} type="button">{publish.isPending ? <LoaderCircle className="spin" size={14} /> : <Upload size={14} />} Publish {chosen.length ? chosen.length : ''} to repository</button>
          : <p>{canWrite ? `Publishing is unavailable: ${problem}.` : 'Publishing needs the assistant.write and settings.write privileges.'}</p>}
      </div>}
    </aside>
    <div className="assistant-main">
      {published && <div className="research-notice" role="status"><Check size={15} /><span>Published {count(published.count)} to <code>{published.folder}</code>. Review and commit them there.</span></div>}
      {publish.error && <div className="form-error assistant-notice" role="alert"><AlertCircle size={15} /> {publish.error.message}</div>}
      {list.isError
        ? <div className="empty"><AlertCircle size={24} /><strong>{config.title} could not be loaded</strong><span>{list.error.message}</span></div>
        : list.isLoading
          ? <div className="empty"><LoaderCircle className="spin" size={22} /><span>Loading {plural}…</span></div>
          : !documents.length
            ? <div className="empty"><Icon size={28} />{query
              ? <><strong>No matches</strong><span>No {singular} mentions “{query}”.</span></>
              : <><strong>No {plural} yet</strong><span>{config.empty}</span></>}</div>
            : !document.data
              ? <div className="empty"><LoaderCircle className="spin" size={22} /><span>Loading the {singular}…</span></div>
              : <Reader canDelete={canWrite} canPublish={canPublish} config={config} document={document.data} onDelete={() => setDeleting(document.data)} onPublish={() => publish.mutate([document.data!.slug])} publishing={publish.isPending} />}
    </div>
    {deleting && <Dialog description={`The ${singular} is removed from this machine. Shipped ${plural} are not affected.`} onClose={() => setDeleting(undefined)} title={`Delete ${singular}?`}>
      <div className="confirm-body"><Trash2 size={24} /><p><strong>{deleting.title}</strong>{deleting.replaces_shipped ? `, which replaces a shipped ${singular}. The shipped version will be used again.` : '.'}</p>{remove.error && <div className="form-error"><AlertCircle size={15} /> {remove.error.message}</div>}</div>
      <footer className="dialog-actions"><button className="button secondary" onClick={() => setDeleting(undefined)} type="button">Keep {singular}</button><button className="button danger" disabled={remove.isPending} onClick={() => remove.mutate(deleting.slug)} type="button">{remove.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Delete {singular}</button></footer>
    </Dialog>}
  </section>
}

const originDetail = (item: Base) => item.replaces_shipped ? 'Replaces shipped' : originLabel(item)

const RESEARCH: LibraryConfig<ResearchSummary, ResearchDocument> = {
  kind: 'research',
  title: 'Research',
  singular: 'document',
  plural: 'documents',
  icon: BookOpen,
  empty: 'The assistant saves what it learns here as it works: how arguments behave on this hardware, what models need, and llama.cpp changes.',
  listDetail: (item) => `${originDetail(item)} · ${item.updated}`,
  facts: (document) => [
    ['Researched', document.researched],
    ['Updated', document.updated],
    ['Confidence', document.confidence],
    ...fact('Models', document.applies_to.models.join(', ')),
    ...fact('Hardware', document.applies_to.hardware.join(', ')),
    ...fact('Runtime', document.applies_to.runtime),
  ],
  after: (document) => <section className="research-sources" aria-label="Sources">
    <h3>Sources</h3>
    <ol>
      {document.sources.map((source, index) => <li key={`${source.title}-${index}`}>
        {source.url ? <a href={source.url} rel="noreferrer" target="_blank">{source.title} <ExternalLink aria-hidden="true" size={12} /></a> : <span>{source.title}</span>}
        {source.accessed && <small>Read {source.accessed}</small>}
      </li>)}
    </ol>
  </section>,
}

export const OUTCOME_LABELS: Record<ReportSummary['outcome'], string> = {
  improved: 'Improved',
  unchanged: 'Unchanged',
  worse: 'Worse',
  failed: 'Failed',
  incomplete: 'Incomplete',
}

const number = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 2 })

/** The change from before to after, as a signed percentage, when both are known. */
export function change(before: number | null | undefined, after: number | null | undefined) {
  if (before == null || after == null || before === 0) return '—'
  const percent = (after - before) / Math.abs(before) * 100
  return `${percent > 0 ? '+' : ''}${percent.toFixed(1)}%`
}

const REPORTS: LibraryConfig<ReportSummary, ReportDocument> = {
  kind: 'reports',
  title: 'Reports',
  singular: 'report',
  plural: 'reports',
  icon: FileBarChart,
  empty: 'When the assistant finishes a piece of work it writes a report here: what it tried, what it measured and the settings it chose.',
  listDetail: (item) => `${OUTCOME_LABELS[item.outcome]} · ${originDetail(item)} · ${item.created}`,
  facts: (document) => [
    ['Outcome', OUTCOME_LABELS[document.outcome]],
    ['Created', document.created],
    ...fact('Profile', document.subject.profile),
    ...fact('Model', document.subject.model),
    ...fact('Hardware', document.hardware.join(', ')),
    ...fact('Runtime', document.runtime),
    ...fact('Benchmark', document.benchmark),
  ],
  before: (document) => document.results.length > 0 && <table className="report-results" aria-label="Results">
    <thead><tr><th scope="col">Result</th><th scope="col">Before</th><th scope="col">After</th><th scope="col">Change</th></tr></thead>
    <tbody>{document.results.map((result) => <tr key={result.metric}>
      <th scope="row">{result.metric}{result.unit ? <small> {result.unit}</small> : null}</th>
      <td>{number(result.before)}</td>
      <td>{number(result.after)}</td>
      <td>{change(result.before, result.after)}</td>
    </tr>)}</tbody>
  </table>,
  charts: true,
}

/** The research library: documents shipped with LlamaWebUI and those the assistant wrote here. */
export function ResearchPanel({ canWrite }: { canWrite: boolean }) {
  return <LibraryPanel canWrite={canWrite} config={RESEARCH} />
}

/** Reports of earlier optimisation work, with their charts. */
export function ReportsPanel({ canWrite }: { canWrite: boolean }) {
  return <LibraryPanel canWrite={canWrite} config={REPORTS} />
}
