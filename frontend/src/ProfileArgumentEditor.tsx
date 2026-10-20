import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CircleHelp,
  Info,
  LoaderCircle,
  Minus,
  Plus,
  RefreshCw,
  Search as SearchIcon,
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react'
import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ArgumentDefinition, EffectDirection, EffectiveArgumentCatalog, ProfilePreview, RawOrigin, RuntimeOptionInfo } from './api'
import {
  type ClientIssue,
  type DraftValue,
  type EditorEntry,
  emptyDraft,
  groupByCategory,
  isSet,
  nextEntryKey,
  searchArguments,
  simpleDefinitions,
} from './argumentCatalog'

const DIRECTION_LABEL: Record<EffectDirection, string> = {
  increase: 'Increases',
  decrease: 'Decreases',
  tradeoff: 'Trade-off',
  neutral: 'Neutral',
}

const ORIGIN_LABEL: Record<RawOrigin, string> = {
  runtime: 'Runtime option',
  custom: 'Custom',
  'catalog-mismatch': 'Catalog mismatch',
  deprecated: 'Deprecated',
}

function DirectionIcon({ direction }: { direction: EffectDirection }) {
  const Icon = direction === 'increase' ? ArrowUp : direction === 'decrease' ? ArrowDown : direction === 'tradeoff' ? ArrowUpDown : Minus
  return <span className={`effect-direction ${direction}`} title={DIRECTION_LABEL[direction]}><Icon aria-hidden="true" size={13} /><span className="sr-only">{DIRECTION_LABEL[direction]}</span></span>
}

export function EffectPills({ catalog, tags }: { catalog: EffectiveArgumentCatalog; tags: string[] }) {
  if (!tags.length) return null
  return (
    <ul aria-label="Effects" className="effect-pills">
      {tags.map((tag) => {
        const info = catalog.effectTags[tag]
        return <li className="effect-pill" data-color={info?.color ?? 'gray'} key={tag} title={info?.description}>{info?.label ?? tag}</li>
      })}
    </ul>
  )
}

function defaultText(definition: ArgumentDefinition) {
  const runtimeDefault = definition.runtime?.default
  if (runtimeDefault) return runtimeDefault
  if (definition.value.default !== undefined) return String(definition.value.default)
  return undefined
}

function statusNote(definition: ArgumentDefinition) {
  const runtime = definition.runtime
  if (!runtime) return undefined
  if (runtime.status === 'not-advertised') return 'Not advertised by this runtime; it will still be passed with a warning.'
  if (runtime.status === 'alias') return `This runtime advertises it as ${runtime.advertisedAs}; LlamaWebUI passes ${definition.flag}.`
  return undefined
}

export function HelpButton({ catalog, definition }: { catalog: EffectiveArgumentCatalog; definition: ArgumentDefinition }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  function close() {
    setOpen(false)
    buttonRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    panelRef.current?.querySelector<HTMLElement>('button')?.focus()
    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node
      if (!panelRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'Escape') return
    // Close only the help panel, not the surrounding profile dialog.
    event.preventDefault()
    event.stopPropagation()
    close()
  }

  const schema = definition.value
  const fallback = defaultText(definition)
  const runtimeSyntax = definition.runtime?.syntax
  return (
    <span className="help-anchor">
      <button aria-controls={open ? id : undefined} aria-expanded={open} aria-label={`Help for ${definition.label}`} className="help-button" onClick={() => setOpen((value) => !value)} ref={buttonRef} type="button"><CircleHelp aria-hidden="true" size={15} /></button>
      {open && <>
        <div aria-hidden="true" className="help-sheet-backdrop" onMouseDown={close} />
        <div aria-labelledby={`${id}-title`} aria-modal="false" className="help-popover" id={id} onKeyDown={onKeyDown} ref={panelRef} role="dialog">
          <header>
            <div><strong id={`${id}-title`}>{definition.label}</strong><code>{definition.help.syntax ?? definition.flag}</code></div>
            <button aria-label="Close help" className="icon-button small" onClick={close} type="button"><X size={15} /></button>
          </header>
          <p>{definition.help.summary}</p>
          {definition.help.details && <p>{definition.help.details}</p>}
          <dl className="help-facts">
            <div><dt>Default</dt><dd>{fallback ?? 'Set by llama-server'}</dd></div>
            {(schema.minimum !== undefined || schema.maximum !== undefined) && <div><dt>Range</dt><dd>{schema.minimum ?? '−∞'} to {schema.maximum ?? '∞'}{schema.unit ? ` ${schema.unit}` : ''}</dd></div>}
            {schema.unit && schema.minimum === undefined && schema.maximum === undefined && <div><dt>Unit</dt><dd>{schema.unit}</dd></div>}
            {schema.options.length > 0 && <div><dt>Values</dt><dd>{schema.options.map((option) => option.value).join(', ')}</dd></div>}
            {schema.specialValues.length > 0 && <div><dt>Also accepts</dt><dd>{schema.specialValues.join(', ')}</dd></div>}
            {(definition.aliases.length > 0 || schema.negation) && <div><dt>Aliases</dt><dd>{[...definition.aliases, schema.negation, ...schema.negationAliases].filter(Boolean).join(', ')}</dd></div>}
            {runtimeSyntax && runtimeSyntax !== definition.help.syntax && <div><dt>This runtime</dt><dd><code>{runtimeSyntax}</code></dd></div>}
          </dl>
          {definition.help.examples.length > 0 && <div className="help-section"><small>Examples</small><ul>{definition.help.examples.map((example) => <li key={example}><code>{example}</code></li>)}</ul></div>}
          {definition.help.effects.length > 0 && <div className="help-section"><small>Effects</small><ul className="help-effects">{definition.help.effects.map((effect) => <li key={effect.text}><DirectionIcon direction={effect.direction} /><span>{effect.text}</span><EffectPills catalog={catalog} tags={effect.tags} /></li>)}</ul></div>}
          {definition.safety && <div className={`help-callout ${definition.safety.level}`}><ShieldAlert aria-hidden="true" size={15} /><span>{definition.safety.warning}{definition.safety.requiresConfirmation ? ' Saving requires an explicit acknowledgement.' : ''}</span></div>}
          {definition.deprecated && <div className="help-callout warning"><AlertCircle aria-hidden="true" size={15} /><span>{definition.deprecated.message}{definition.deprecated.replacement ? ` Use --${definition.deprecated.replacement} instead.` : ''}</span></div>}
          {definition.controlReason && <div className="help-callout info"><Info aria-hidden="true" size={15} /><span>{definition.controlReason}</span></div>}
          {statusNote(definition) && <div className="help-callout info"><Info aria-hidden="true" size={15} /><span>{statusNote(definition)}</span></div>}
          {definition.help.notes.map((note) => <p className="help-note" key={note}>{note}</p>)}
          {definition.runtime?.help && <div className="help-section"><small>Runtime help</small><p className="help-runtime">{definition.runtime.help}</p></div>}
        </div>
      </>}
    </span>
  )
}

function hintFor(definition: ArgumentDefinition) {
  const schema = definition.value
  const parts: string[] = []
  if (schema.type === 'list' && schema.separator) parts.push(schema.separator === ',' ? 'Comma-separated' : `Separated by "${schema.separator}"`)
  if (schema.type === 'list' && schema.repeat) parts.push('One value per row')
  if (schema.specialValues.length) parts.push(`Number or ${schema.specialValues.join(', ')}`)
  if (schema.unit) parts.push(schema.unit)
  if (schema.type === 'path' || schema.itemType === 'path') parts.push(schema.pathKind === 'directory' ? 'Absolute directory path' : 'Absolute file path')
  if (schema.type === 'json') parts.push('JSON')
  if (schema.arity > 1) parts.push(`${schema.arity} values separated by a space`)
  return parts.join(' · ')
}

function ArgumentInput({ definition, draft, inputId, describedBy, invalid, onChange }: {
  definition: ArgumentDefinition
  draft: DraftValue
  inputId: string
  describedBy?: string
  invalid: boolean
  onChange: (draft: DraftValue) => void
}) {
  const schema = definition.value
  const fallback = defaultText(definition)
  const placeholder = fallback ? `Default: ${fallback}` : 'Runtime default'
  const common = { 'aria-describedby': describedBy, 'aria-invalid': invalid || undefined, id: inputId }
  if (schema.type === 'boolean') {
    const value = draft === true ? 'true' : draft === false ? 'false' : ''
    return (
      <select {...common} onChange={(event) => onChange(event.target.value === '' ? undefined : event.target.value === 'true')} value={value}>
        <option value="">{fallback ? `Runtime default (${fallback})` : 'Runtime default'}</option>
        <option value="true">Enabled ({definition.flag})</option>
        {schema.negation && <option value="false">Disabled ({schema.negation})</option>}
      </select>
    )
  }
  if (schema.type === 'enum') {
    return (
      <select {...common} onChange={(event) => onChange(event.target.value || undefined)} value={typeof draft === 'string' ? draft : ''}>
        <option value="">{fallback ? `Runtime default (${fallback})` : 'Runtime default'}</option>
        {schema.options.map((option) => <option key={option.value} value={option.value}>{option.label ?? option.value}</option>)}
      </select>
    )
  }
  if (schema.type === 'json') {
    return <textarea {...common} className="mono" onChange={(event) => onChange(event.target.value)} placeholder={placeholder} rows={3} spellCheck={false} value={typeof draft === 'string' ? draft : ''} />
  }
  if (schema.type === 'list' && schema.repeat) {
    const rows = Array.isArray(draft) && draft.length ? draft : ['']
    return (
      <div className="list-input">
        {rows.map((row, index) => (
          <div className="list-input-row" key={index}>
            <input {...common} aria-label={index === 0 ? undefined : `${definition.label} value ${index + 1}`} id={index === 0 ? inputId : `${inputId}-${index}`} onChange={(event) => onChange(rows.map((item, position) => (position === index ? event.target.value : item)))} placeholder={index === 0 ? placeholder : 'Another value'} spellCheck={false} value={row} />
            {rows.length > 1 && <button aria-label={`Remove ${definition.label} value ${index + 1}`} className="icon-button small" onClick={() => onChange(rows.filter((_, position) => position !== index))} type="button"><X size={14} /></button>}
          </div>
        ))}
        <button className="button secondary compact list-add" onClick={() => onChange([...rows, ''])} type="button"><Plus size={13} /> Add value</button>
      </div>
    )
  }
  const numeric = schema.type === 'integer' || schema.type === 'number'
  return (
    <input
      {...common}
      autoComplete="off"
      inputMode={numeric && !schema.specialValues.length ? (schema.type === 'integer' ? 'numeric' : 'decimal') : undefined}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      type={definition.safety?.sensitive ? 'password' : 'text'}
      value={typeof draft === 'string' ? draft : ''}
    />
  )
}

export function ArgumentField({ catalog, definition, draft, issues, acknowledged, onChange, onAcknowledge, onRemove }: {
  catalog: EffectiveArgumentCatalog
  definition: ArgumentDefinition
  draft: DraftValue
  issues: ClientIssue[]
  acknowledged: boolean
  onChange: (draft: DraftValue) => void
  onAcknowledge: (value: boolean) => void
  onRemove?: () => void
}) {
  const inputId = `argument-${definition.id}`
  const hintId = `${inputId}-hint`
  const errorId = `${inputId}-error`
  const errors = issues.filter((issue) => issue.level === 'error' && !issue.message.includes('security acknowledgement'))
  const warnings = issues.filter((issue) => issue.level === 'warning')
  const hint = hintFor(definition)
  const note = statusNote(definition)
  const describedBy = [hint && hintId, errors.length && errorId].filter(Boolean).join(' ') || undefined
  const set = isSet(draft)
  return (
    <div className={`argument-field${set ? ' is-set' : ''}`} data-argument={definition.id}>
      <div className="argument-label-row">
        <label htmlFor={inputId}>{definition.label}</label>
        <HelpButton catalog={catalog} definition={definition} />
        {onRemove && <button aria-label={`Remove ${definition.label}`} className="icon-button small argument-remove" onClick={onRemove} title="Remove setting" type="button"><Trash2 size={14} /></button>}
      </div>
      <code className="argument-flag">{definition.flag}</code>
      <ArgumentInput definition={definition} describedBy={describedBy} draft={draft} inputId={inputId} invalid={errors.length > 0} onChange={onChange} />
      {hint && <small className="argument-hint" id={hintId}>{hint}</small>}
      <EffectPills catalog={catalog} tags={definition.effectTags} />
      {note && set && <small className="argument-note"><Info aria-hidden="true" size={12} /> {note}</small>}
      {definition.deprecated && <small className="argument-note warning"><AlertCircle aria-hidden="true" size={12} /> {definition.deprecated.message}</small>}
      {set && definition.safety && definition.safety.level !== 'info' && (
        <div className={`safety-callout ${definition.safety.level}`}>
          <ShieldAlert aria-hidden="true" size={15} />
          <div>
            <span>{definition.safety.warning}</span>
            {definition.safety.requiresConfirmation && <label className="check-row"><input checked={acknowledged} onChange={(event) => onAcknowledge(event.target.checked)} type="checkbox" /> I understand the risk of {definition.flag}</label>}
          </div>
        </div>
      )}
      {errors.length > 0 && <small className="argument-error" id={errorId}>{errors.map((issue) => issue.message).join(' ')}</small>}
      {warnings.length > 0 && set && <small className="argument-note warning">{warnings.map((issue) => issue.message).join(' ')}</small>}
    </div>
  )
}

function RawParameterRow({ entry, issues, onChange, onRemove, flagEditable }: {
  entry: Extract<EditorEntry, { kind: 'raw' }>
  issues: ClientIssue[]
  onChange: (entry: Extract<EditorEntry, { kind: 'raw' }>) => void
  onRemove: () => void
  flagEditable: boolean
}) {
  const errors = issues.filter((issue) => issue.level === 'error')
  const warnings = issues.filter((issue) => issue.level === 'warning')
  const errorId = `${entry.key}-error`
  return (
    <div className="raw-row" data-origin={entry.origin}>
      <label className="raw-flag"><span>Flag</span><input aria-describedby={errors.length ? errorId : undefined} aria-invalid={errors.length > 0 || undefined} onChange={(event) => onChange({ ...entry, flag: event.target.value.trim() })} placeholder="--option-name" readOnly={!flagEditable} spellCheck={false} value={entry.flag} /></label>
      <label className="raw-value"><span>Value</span><input disabled={entry.flagOnly} onChange={(event) => onChange({ ...entry, value: event.target.value })} placeholder={entry.flagOnly ? 'No value' : 'Value'} spellCheck={false} value={entry.flagOnly ? '' : entry.value} /></label>
      <label className="check-row raw-toggle"><input checked={entry.flagOnly} onChange={(event) => onChange({ ...entry, flagOnly: event.target.checked })} type="checkbox" /> Flag only</label>
      <span className="profile-tag">{ORIGIN_LABEL[entry.origin]}</span>
      <button aria-label={`Remove ${entry.flag || 'custom parameter'}`} className="icon-button small danger-icon" onClick={onRemove} type="button"><Trash2 size={15} /></button>
      {errors.length > 0 && <small className="argument-error" id={errorId}>{errors.map((issue) => issue.message).join(' ')}</small>}
      {warnings.length > 0 && <small className="argument-note warning">{warnings.map((issue) => issue.message).join(' ')}</small>}
    </div>
  )
}

function ArgumentSearch({ catalog, entries, onAddCatalog, onAddRuntime }: {
  catalog: EffectiveArgumentCatalog
  entries: EditorEntry[]
  onAddCatalog: (definition: ArgumentDefinition) => void
  onAddRuntime: (option: RuntimeOptionInfo) => void
}) {
  const [query, setQuery] = useState('')
  const results = useMemo(() => searchArguments(catalog, query), [catalog, query])
  const added = new Set(entries.map((entry) => (entry.kind === 'catalog' ? entry.id : entry.flag)))
  const listId = useId()
  return (
    <div className="argument-search" role="search">
      <label className="search-box"><SearchIcon aria-hidden="true" size={15} /><span className="sr-only">Search arguments</span><input aria-controls={listId} aria-label="Search arguments" onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, flag, or description" type="search" value={query} /></label>
      {query.trim() && (
        <ul aria-label="Search results" className="search-results" id={listId}>
          {results.map((result) => {
            const definition = result.definition
            const isAdded = added.has(result.kind === 'catalog' ? result.key : result.option?.flag ?? '')
            const disabled = Boolean(definition && definition.control !== 'editable')
            const badge = definition
              ? definition.control === 'removed' ? 'Removed' : definition.control !== 'editable' ? 'Managed' : definition.runtime?.status === 'not-advertised' ? 'Not advertised' : !definition.isAdvanced ? 'Common' : undefined
              : 'Uncatalogued'
            return (
              <li key={`${result.kind}:${result.key}`}>
                <div className="search-result-copy">
                  <strong>{result.label}</strong>
                  <code>{result.flag}</code>
                  <span>{disabled ? definition?.controlReason ?? definition?.deprecated?.message : result.summary}</span>
                </div>
                {badge && <span className="profile-tag">{badge}</span>}
                <button aria-label={`Add ${result.label}`} className="button secondary compact" disabled={disabled || isAdded} onClick={() => {
                  if (result.definition) onAddCatalog(result.definition)
                  else if (result.option) onAddRuntime(result.option)
                }} type="button">{isAdded ? 'Added' : <><Plus size={13} /> Add</>}</button>
              </li>
            )
          })}
          {!results.length && <li className="search-empty">No arguments match “{query.trim()}”. Add it under Custom parameters if your runtime supports it.</li>}
        </ul>
      )}
    </div>
  )
}

export function ProfileArgumentEditor({ catalog, entries, onEntriesChange, acknowledged, onAcknowledgedChange, issues, runtimeName, runtimeSwitchFlags }: {
  catalog: EffectiveArgumentCatalog
  entries: EditorEntry[]
  onEntriesChange: (entries: EditorEntry[]) => void
  acknowledged: string[]
  onAcknowledgedChange: (ids: string[]) => void
  issues: ClientIssue[]
  runtimeName: string
  runtimeSwitchFlags: string[]
}) {
  const [focusTarget, setFocusTarget] = useState<string>()
  const byId = useMemo(() => new Map(catalog.arguments.map((definition) => [definition.id, definition])), [catalog])
  const simple = useMemo(() => simpleDefinitions(catalog), [catalog])
  const simpleIds = new Set(simple.map((definition) => definition.id))

  useEffect(() => {
    if (!focusTarget) return
    document.getElementById(focusTarget)?.focus()
    setFocusTarget(undefined)
  }, [focusTarget, entries])

  function draftFor(id: string) {
    const entry = entries.find((item) => item.kind === 'catalog' && item.id === id)
    return entry?.kind === 'catalog' ? entry.draft : undefined
  }

  function setDraft(id: string, draft: DraftValue) {
    const index = entries.findIndex((item) => item.kind === 'catalog' && item.id === id)
    if (index === -1) {
      if (isSet(draft)) onEntriesChange([...entries, { key: nextEntryKey(id), kind: 'catalog', id, draft }])
      return
    }
    // Common settings disappear from the stored list when cleared; pinned advanced ones stay visible.
    if (!isSet(draft) && simpleIds.has(id)) {
      onEntriesChange(entries.filter((_, position) => position !== index))
      return
    }
    onEntriesChange(entries.map((item, position) => (position === index && item.kind === 'catalog' ? { ...item, draft } : item)))
  }

  function acknowledge(id: string, value: boolean) {
    onAcknowledgedChange(value ? [...new Set([...acknowledged, id])] : acknowledged.filter((item) => item !== id))
  }

  function removeEntry(key: string) {
    onEntriesChange(entries.filter((item) => item.key !== key))
  }

  function updateRaw(next: Extract<EditorEntry, { kind: 'raw' }>) {
    onEntriesChange(entries.map((item) => (item.key === next.key ? next : item)))
  }

  function addCatalog(definition: ArgumentDefinition) {
    if (!entries.some((item) => item.kind === 'catalog' && item.id === definition.id)) {
      onEntriesChange([...entries, { key: nextEntryKey(definition.id), kind: 'catalog', id: definition.id, draft: emptyDraft(definition) }])
    }
    setFocusTarget(`argument-${definition.id}`)
  }

  function addRuntime(option: RuntimeOptionInfo) {
    const takesValue = option.syntax.trim().split(/\s+/).some((part, index, parts) => index > 0 && !parts[index - 1].endsWith(',') && !part.startsWith('-'))
    const key = nextEntryKey('raw')
    onEntriesChange([...entries, { key, kind: 'raw', flag: option.flag, value: '', flagOnly: !takesValue, origin: 'runtime' }])
  }

  function addCustom() {
    onEntriesChange([...entries, { key: nextEntryKey('raw'), kind: 'raw', flag: '', value: '', flagOnly: false, origin: 'custom' }])
  }

  const issuesFor = (target: string) => issues.filter((issue) => issue.target === target)
  const pinned = entries.filter((entry): entry is Extract<EditorEntry, { kind: 'catalog' }> => entry.kind === 'catalog' && !simpleIds.has(entry.id) && byId.has(entry.id))
  const runtimeEntries = entries.filter((entry): entry is Extract<EditorEntry, { kind: 'raw' }> => entry.kind === 'raw' && entry.origin === 'runtime')
  const customEntries = entries.filter((entry): entry is Extract<EditorEntry, { kind: 'raw' }> => entry.kind === 'raw' && entry.origin !== 'runtime')
  const callouts = catalog.issues.filter((issue) => issue.code !== 'not-advertised')

  return (
    <div className="argument-editor">
      {callouts.map((issue) => <div className={`capability-note ${issue.level}`} key={issue.code}><Info aria-hidden="true" size={16} /><span>{issue.message}</span></div>)}
      {runtimeSwitchFlags.length > 0 && <div className="capability-note warning" role="status"><AlertCircle aria-hidden="true" size={16} /><span><strong>{runtimeName}</strong> does not advertise {runtimeSwitchFlags.join(', ')}. These settings are kept and still passed to llama-server.</span></div>}
      <section aria-labelledby="simple-settings-heading" className="argument-section">
        <header><h3 id="simple-settings-heading">Common settings</h3><p>Leave a field empty to use the runtime default.</p></header>
        {groupByCategory(catalog, simple, (definition) => definition.category).map((group) => (
          <fieldset className="argument-group" key={group.id}>
            <legend>{group.label}</legend>
            <div className="argument-grid">
              {group.items.map((definition) => <ArgumentField acknowledged={acknowledged.includes(definition.id)} catalog={catalog} definition={definition} draft={draftFor(definition.id)} issues={issuesFor(definition.id)} key={definition.id} onAcknowledge={(value) => acknowledge(definition.id, value)} onChange={(draft) => setDraft(definition.id, draft)} />)}
            </div>
          </fieldset>
        ))}
      </section>
      <section aria-labelledby="advanced-settings-heading" className="argument-section">
        <header><h3 id="advanced-settings-heading">Advanced settings</h3><p>Search {catalog.arguments.length} catalogued arguments{catalog.uncatalogued.length ? ` and ${catalog.uncatalogued.length} uncatalogued runtime options` : ''}. Settings with a value stay pinned here.</p></header>
        <ArgumentSearch catalog={catalog} entries={entries} onAddCatalog={addCatalog} onAddRuntime={addRuntime} />
        {groupByCategory(catalog, pinned, (entry) => byId.get(entry.id)?.category ?? '').map((group) => (
          <fieldset className="argument-group" key={group.id}>
            <legend>{group.label}</legend>
            <div className="argument-grid">
              {group.items.map((entry) => {
                const definition = byId.get(entry.id)!
                return <ArgumentField acknowledged={acknowledged.includes(definition.id)} catalog={catalog} definition={definition} draft={entry.draft} issues={issuesFor(definition.id)} key={entry.key} onAcknowledge={(value) => acknowledge(definition.id, value)} onChange={(draft) => setDraft(definition.id, draft)} onRemove={() => removeEntry(entry.key)} />
              })}
            </div>
          </fieldset>
        ))}
      </section>
      {runtimeEntries.length > 0 && (
        <section aria-labelledby="runtime-options-heading" className="argument-section">
          <header><h3 id="runtime-options-heading">Uncatalogued runtime options</h3><p>Advertised by this runtime's help but not described by catalog {catalog.catalogVersion}. Values are passed through unchanged.</p></header>
          {runtimeEntries.map((entry) => <RawParameterRow entry={entry} flagEditable={false} issues={issuesFor(entry.key)} key={entry.key} onChange={updateRaw} onRemove={() => removeEntry(entry.key)} />)}
        </section>
      )}
      <section aria-labelledby="custom-parameters-heading" className="argument-section">
        <header><h3 id="custom-parameters-heading">Custom parameters</h3><p>Passthrough flags for options the catalog does not cover. Shell syntax and newlines are blocked; llama-server checks the meaning.</p></header>
        {customEntries.map((entry) => <RawParameterRow entry={entry} flagEditable issues={issuesFor(entry.key)} key={entry.key} onChange={updateRaw} onRemove={() => removeEntry(entry.key)} />)}
        <button className="button secondary compact" onClick={addCustom} type="button"><Plus size={14} /> Add custom parameter</button>
      </section>
    </div>
  )
}

export function LaunchPreview({ preview, pending, error, clientIssues, onRefresh }: {
  preview?: ProfilePreview
  pending: boolean
  error?: string
  clientIssues: ClientIssue[]
  onRefresh: () => void
}) {
  const clientErrors = clientIssues.filter((issue) => issue.level === 'error').map((issue) => issue.message)
  const blockers = [...new Set([...clientErrors, ...(preview?.errors ?? [])])]
  const warnings = [...new Set([...clientIssues.filter((issue) => issue.level === 'warning').map((issue) => issue.message), ...(preview?.warnings ?? [])])]
  return (
    <div className="launch-preview">
      <div className="launch-preview-head">
        <div><h3>Launch preview</h3><p>The exact arguments LlamaWebUI will pass. Nothing is executed.</p></div>
        <button className="button secondary compact" disabled={pending} onClick={onRefresh} type="button">{pending ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />} Refresh</button>
      </div>
      {error && <div className="form-error" role="alert"><AlertCircle size={15} /> {error}</div>}
      <div aria-live="polite" className={`preflight ${blockers.length ? 'blocked' : 'clear'}`}>
        {blockers.length ? <><strong>{blockers.length} launch blocker{blockers.length === 1 ? '' : 's'}</strong><ul>{blockers.map((message) => <li key={message}>{message}</li>)}</ul></> : <strong>No launch blockers</strong>}
      </div>
      {warnings.length > 0 && <div className="preflight warnings"><strong>{warnings.length} warning{warnings.length === 1 ? '' : 's'}</strong><ul>{warnings.map((message) => <li key={message}>{message}</li>)}</ul></div>}
      {preview && preview.arguments.length > 0 && <>
        <h4>Model arguments (preset equivalent)</h4>
        <ol aria-label="Model argument vector" className="argv">{preview.arguments.map((argument, index) => <li key={`${index}:${argument}`}><code>{argument}</code></li>)}</ol>
        <h4>Router launch</h4>
        <ol aria-label="Router argument vector" className="argv">{preview.router_arguments.map((argument, index) => <li key={`${index}:${argument}`}><code>{argument}</code></li>)}</ol>
        <details className="launch-details"><summary>Generated preset section</summary><pre>{preview.preset}</pre></details>
      </>}
    </div>
  )
}
