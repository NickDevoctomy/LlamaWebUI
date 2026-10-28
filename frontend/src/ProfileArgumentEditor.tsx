import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
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
import { createContext, type KeyboardEvent, useContext, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ArgumentDefinition, EffectDirection, EffectiveArgumentCatalog, LibraryModel, ProfilePreview, RawOrigin, RuntimeOptionInfo } from './api'
import { MemoryBreakdown, memorySummary } from './MemoryEstimate'
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

export function EffectPills({ catalog, tags }: { catalog: Pick<EffectiveArgumentCatalog, 'effectTags'>; tags: string[] }) {
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
  if (schema.type === 'list') parts.push('One value per row')
  if (schema.type === 'list' && schema.repeat) parts.push(`Each sent as its own ${definition.flag}`)
  if (schema.type === 'list' && !schema.repeat) parts.push(`Sent as one ${definition.flag}, joined with "${schema.separator ?? ','}"`)
  if (schema.specialValues.length) parts.push(`Number or ${schema.specialValues.join(', ')}`)
  if (schema.unit) parts.push(schema.unit)
  if (schema.fileType === 'gguf') parts.push('Choose a downloaded model, or type an absolute .gguf path')
  else if (schema.type === 'path' || schema.itemType === 'path') parts.push(schema.pathKind === 'directory' ? 'Absolute directory path' : 'Absolute file path')
  if (schema.type === 'json') parts.push('JSON')
  if (schema.arity > 1) parts.push(`${schema.arity} values separated by a space`)
  return parts.join(' · ')
}

/** Downloaded models offered for arguments whose file must be a GGUF model. */
const DownloadedModels = createContext<LibraryModel[]>([])

function samePath(left: string, right: string) {
  return left.replace(/\//g, '\\').toLowerCase() === right.replace(/\//g, '\\').toLowerCase()
}

function ModelPathInput({ label, inputId, value, placeholder, inputProps, onChange }: {
  label: string
  inputId: string
  value: string
  placeholder: string
  inputProps: Record<string, unknown>
  onChange: (value: string) => void
}) {
  const models = useContext(DownloadedModels)
  const chosen = models.find((model) => samePath(model.primary_path, value))
  return (
    <div className="model-path-input">
      <select aria-label={`${label}: downloaded model`} onChange={(event) => { if (event.target.value) onChange(event.target.value) }} value={chosen?.primary_path ?? ''}>
        <option value="">{models.length ? 'Choose a downloaded model…' : 'No downloaded models yet'}</option>
        {models.map((model) => <option key={model.download_id} value={model.primary_path}>{model.repo_id} · {model.group_key}</option>)}
      </select>
      <input {...inputProps} id={inputId} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} spellCheck={false} value={value} />
    </div>
  )
}

/** Common sizes, in binary thousands: 32K is 32,768. */
const SIZE_PRESETS = [4, 8, 16, 24, 32, 48, 64, 96, 128, 192, 256].map((size) => ({ label: `${size}K`, value: size * 1024 }))

/** Sizes counted in tokens or MiB get presets; counts such as threads or layers do not. */
function sizePresets(definition: ArgumentDefinition) {
  const schema = definition.value
  if (schema.type !== 'integer' || (schema.unit !== 'tokens' && schema.unit !== 'MiB')) return []
  return SIZE_PRESETS.filter(({ value }) => (schema.minimum === undefined || value >= schema.minimum) && (schema.maximum === undefined || value <= schema.maximum))
}

function PresetMenu({ label, presets, value, onPick }: {
  label: string
  presets: { label: string; value: number }[]
  value: string
  onPick: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  function close() {
    setOpen(false)
    buttonRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    const items = menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]')
    const current = [...(items ?? [])].find((item) => item.getAttribute('aria-checked') === 'true')
    ;(current ?? items?.[0])?.focus()
    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const items = [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])]
    const index = items.indexOf(document.activeElement as HTMLElement)
    const move = ({ ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 } as Record<string, number>)[event.key]
    if (move) {
      event.preventDefault()
      items[(index + move + items.length) % items.length]?.focus()
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      items[event.key === 'Home' ? 0 : items.length - 1]?.focus()
    } else if (event.key === 'Escape') {
      // Close only the menu, not the surrounding profile dialog.
      event.preventDefault()
      event.stopPropagation()
      close()
    } else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <>
      <button aria-controls={open ? id : undefined} aria-expanded={open} aria-haspopup="menu" aria-label={`Common values for ${label}`} className="icon-button preset-button" onClick={() => setOpen((shown) => !shown)} ref={buttonRef} title="Common values" type="button"><ChevronDown aria-hidden="true" size={15} /></button>
      {open && <div aria-label={`Common values for ${label}`} className="preset-menu" id={id} onKeyDown={onKeyDown} ref={menuRef} role="menu">
        {presets.map((preset) => (
          <button aria-checked={value.trim() === String(preset.value)} key={preset.value} onClick={() => { onPick(String(preset.value)); close() }} role="menuitemradio" tabIndex={-1} type="button">
            <strong>{preset.label}</strong>{' '}<span>{preset.value.toLocaleString()}</span>
          </button>
        ))}
      </div>}
    </>
  )
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
  const modelFile = schema.fileType === 'gguf'
  if (schema.type === 'path' && modelFile) {
    return <ModelPathInput inputId={inputId} inputProps={common} label={definition.label} onChange={onChange} placeholder="Or type an absolute path" value={typeof draft === 'string' ? draft : ''} />
  }
  if (schema.type === 'list') {
    const rows = Array.isArray(draft) && draft.length ? draft : typeof draft === 'string' && draft ? [draft] : ['']
    return (
      <div className="list-input">
        {rows.map((row, index) => (
          <div className="list-input-row" key={index}>
            {modelFile
              ? <ModelPathInput inputId={index === 0 ? inputId : `${inputId}-${index}`} inputProps={{ ...common, 'aria-label': index === 0 ? undefined : `${definition.label} value ${index + 1}` }} label={index === 0 ? definition.label : `${definition.label} value ${index + 1}`} onChange={(next) => onChange(rows.map((item, position) => (position === index ? next : item)))} placeholder="Or type an absolute path" value={row} />
              : <input {...common} aria-label={index === 0 ? undefined : `${definition.label} value ${index + 1}`} id={index === 0 ? inputId : `${inputId}-${index}`} onChange={(event) => onChange(rows.map((item, position) => (position === index ? event.target.value : item)))} placeholder={index === 0 ? placeholder : 'Another value'} spellCheck={false} value={row} />}
            {rows.length > 1 && <button aria-label={`Remove ${definition.label} value ${index + 1}`} className="icon-button small" onClick={() => onChange(rows.filter((_, position) => position !== index))} type="button"><X size={14} /></button>}
          </div>
        ))}
        <button className="button secondary compact list-add" onClick={() => onChange([...rows, ''])} type="button"><Plus size={13} /> Add value</button>
      </div>
    )
  }
  const numeric = schema.type === 'integer' || schema.type === 'number'
  const value = typeof draft === 'string' ? draft : ''
  const input = (
    <input
      {...common}
      autoComplete="off"
      inputMode={numeric && !schema.specialValues.length ? (schema.type === 'integer' ? 'numeric' : 'decimal') : undefined}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      type={definition.safety?.sensitive ? 'password' : 'text'}
      value={value}
    />
  )
  const presets = sizePresets(definition)
  if (!presets.length) return input
  return <div className="preset-input">{input}<PresetMenu label={definition.label} onPick={onChange} presets={presets} value={value} /></div>
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

export function ProfileArgumentEditor({ catalog, entries, onEntriesChange, acknowledged, onAcknowledgedChange, issues, runtimeName, runtimeSwitchFlags, downloadedModels = [] }: {
  catalog: EffectiveArgumentCatalog
  downloadedModels?: LibraryModel[]
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
    <DownloadedModels.Provider value={downloadedModels}>
    <div className="argument-editor">
      {callouts.map((issue) => <div className={`capability-note ${issue.level}`} key={issue.code}><Info aria-hidden="true" size={16} /><span>{issue.message}</span></div>)}
      {runtimeSwitchFlags.length > 0 && <div className="capability-note warning" role="status"><AlertCircle aria-hidden="true" size={16} /><span><strong>{runtimeName}</strong> does not advertise {runtimeSwitchFlags.join(', ')}. These settings are kept and still passed to llama-server.</span></div>}
      <section aria-labelledby="simple-settings-heading" className="argument-section">
        <header><h3 id="simple-settings-heading">Common settings</h3><p>Leave a field empty to use the runtime default.</p></header>
        {groupByCategory(catalog, simple, (definition) => definition.category).map((group) => (
          <fieldset className="argument-group" key={group.id}>
            <legend>{group.label}</legend>
            <div className="argument-grid">
              {group.items.map((definition) => {
                const draft = draftFor(definition.id)
                // Removing a common setting returns it to the runtime default; the field itself stays.
                return <ArgumentField acknowledged={acknowledged.includes(definition.id)} catalog={catalog} definition={definition} draft={draft} issues={issuesFor(definition.id)} key={definition.id} onAcknowledge={(value) => acknowledge(definition.id, value)} onChange={(next) => setDraft(definition.id, next)} onRemove={isSet(draft) ? () => setDraft(definition.id, undefined) : undefined} />
              })}
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
    </DownloadedModels.Provider>
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
      {preview?.memory_estimate && <section aria-label="Estimated memory" className="launch-memory">
        <h4>Estimated memory <span>{memorySummary(preview.memory_estimate)}</span></h4>
        <MemoryBreakdown estimate={preview.memory_estimate} />
      </section>}
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
