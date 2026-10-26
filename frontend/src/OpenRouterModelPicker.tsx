import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Bot, LoaderCircle, RefreshCw, Search as SearchIcon, X } from 'lucide-react'
import { useDeferredValue, useId, useState } from 'react'
import { api } from './api'
import { contextSummary, priceLines, priceSummary } from './openRouterPricing'

const SETTINGS_WRITE_HINT = 'Changing settings needs the settings.write privilege'

function formatDate(value: string) {
  return new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Search OpenRouter's models and keep one, with its pricing, for the assistant. */
export function OpenRouterModelPicker({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const listId = useId()
  const [input, setInput] = useState('')
  const query = useDeferredValue(input.trim())
  const selected = useQuery({ queryKey: ['openrouter-model'], queryFn: api.openRouterModel })
  const results = useQuery({
    queryKey: ['openrouter-models', query],
    queryFn: () => api.openRouterModels(query),
    enabled: query.length > 0,
    placeholderData: keepPreviousData,
  })
  const settle = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['openrouter-model'] }),
    queryClient.invalidateQueries({ queryKey: ['assistant-status'] }),
  ])
  const select = useMutation({ mutationFn: api.selectOpenRouterModel, onSuccess: () => setInput(''), onSettled: settle })
  const clear = useMutation({ mutationFn: api.clearOpenRouterModel, onSettled: settle })
  const model = selected.data?.model
  const busy = select.isPending || clear.isPending
  const error = select.error?.message ?? clear.error?.message
  const hint = canManage ? undefined : SETTINGS_WRITE_HINT
  return <>
    <div className="settings-section"><h3>Model</h3><p>The model the assistant uses. Its pricing is saved with it, as OpenRouter listed it when you chose it.</p></div>
    {selected.isError
      ? <div className="settings-body"><div className="capability-note error" role="alert"><AlertCircle size={16} /><span>{selected.error.message}</span></div></div>
      : !selected.data
        ? <div className="settings-body"><div className="capability-note" role="status"><LoaderCircle className="spin" size={16} /><span>Loading the selected model…</span></div></div>
        : <div className="record-list"><div className="record-row">
          <span className="record-icon"><Bot size={17} /></span>
          {model
            ? <>
              <div className="record-copy">
                <strong className="record-title">{model.name} <span className="profile-tag recommended-tag">Selected</span></strong>
                <span className="record-description"><code>{model.id}</code>{contextSummary(model) && ` · ${contextSummary(model)}`}</span>
              </div>
              <span className="row-actions">
                <button className="button secondary compact" disabled={!canManage || busy} onClick={() => select.mutate(model.id)} title={hint ?? 'Fetch the current price from OpenRouter'} type="button">{select.isPending ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} Refresh price</button>
                <button className="button secondary compact" disabled={!canManage || busy} onClick={() => clear.mutate()} title={hint} type="button">{clear.isPending ? <LoaderCircle className="spin" size={14} /> : <X size={14} />} Clear</button>
              </span>
              <div className="record-details price-details">
                <dl className="price-list">{priceLines(model.pricing).map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
                <small>Prices as of {formatDate(model.selected_at)}</small>
              </div>
            </>
            : <div className="record-copy">
              <strong className="record-title">No model selected <span className="profile-tag">Not set</span></strong>
              <span className="record-description">Search below and select one. The Assistant page appears once a model is selected and an API key is saved.</span>
            </div>}
        </div></div>}
    <div className="settings-body model-search" role="search">
      <label className="search-box"><SearchIcon aria-hidden="true" size={15} /><span className="sr-only">Search OpenRouter models</span><input aria-controls={listId} aria-label="Search OpenRouter models" onChange={(event) => setInput(event.target.value)} placeholder="Search by name or id, e.g. claude, gpt, llama" type="search" value={input} /></label>
      {query && <ul aria-label="OpenRouter models" className="search-results" id={listId}>
        {results.isError && <li className="search-empty">{results.error.message}</li>}
        {!results.data && results.isFetching && <li className="search-empty">Searching OpenRouter…</li>}
        {results.data?.models.map((result) => {
          const current = result.id === model?.id
          return <li key={result.id}>
            <div className="search-result-copy">
              <strong>{result.name}</strong>
              <code>{result.id}</code>
              <span>{[contextSummary(result), priceSummary(result.pricing)].filter(Boolean).join(' · ')}</span>
            </div>
            <span />
            <button aria-label={`Select ${result.name}`} className="button secondary compact" disabled={!canManage || busy || current} onClick={() => select.mutate(result.id)} title={hint} type="button">{select.isPending && select.variables === result.id ? <LoaderCircle className="spin" size={13} /> : null}{current ? 'Selected' : 'Select'}</button>
          </li>
        })}
        {results.data && !results.data.models.length && <li className="search-empty">No OpenRouter models match “{query}”.</li>}
        {results.data && results.data.total > results.data.models.length && <li className="search-empty">Showing {results.data.models.length} of {results.data.total} matches. Keep typing to narrow them down.</li>}
      </ul>}
      {error && <div className="form-error" role="alert"><AlertCircle size={15} /> {error}</div>}
    </div>
  </>
}
