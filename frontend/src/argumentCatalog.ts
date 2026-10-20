import type {
  ArgumentCatalog,
  ArgumentDefinition,
  EffectiveArgumentCatalog,
  RawOrigin,
  RuntimeOptionInfo,
  StoredArgument,
  StoredArgumentValue,
} from './api'

/** Form state for one catalog argument: text for scalars, rows for repeated lists. */
export type DraftValue = string | boolean | string[] | undefined

export type EditorEntry =
  | { key: string; kind: 'catalog'; id: string; draft: DraftValue }
  | { key: string; kind: 'raw'; flag: string; value: string; flagOnly: boolean; origin: RawOrigin }

export interface ClientIssue {
  level: 'error' | 'warning'
  message: string
  /** Catalog id or entry key the issue belongs to. */
  target: string
}

const RAW_FLAG = /^--[a-z0-9][a-z0-9-]*$/
const CONTROL_CHARACTERS = /[\x00-\x08\x0a-\x1f\x7f]/
// Mirrors the server: command substitution and whitespace-delimited chaining or redirection.
const SHELL_SYNTAX = /`|\$[({]|(?:^|\s)(?:\d?>>?|<|&&|\|\||[;&|])(?=\s|$)|[;&|]\s/
const INTEGER = /^[+-]?\d+$/
const NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/

let keySequence = 0
export function nextEntryKey(prefix = 'entry') {
  keySequence += 1
  return `${prefix}-${keySequence}`
}

export function catalogIndex(catalog: ArgumentCatalog) {
  const byId = new Map(catalog.arguments.map((definition) => [definition.id, definition]))
  const byFlag = new Map<string, ArgumentDefinition>()
  for (const definition of catalog.arguments) {
    for (const flag of [definition.flag, ...definition.aliases, definition.value.negation, ...definition.value.negationAliases]) {
      if (flag) byFlag.set(flag, definition)
    }
  }
  return { byId, byFlag }
}

export function isSet(draft: DraftValue) {
  if (draft === undefined) return false
  if (typeof draft === 'boolean') return true
  if (Array.isArray(draft)) return draft.some((item) => item.trim() !== '')
  return draft.trim() !== ''
}

export function draftFromStored(definition: ArgumentDefinition, value: StoredArgumentValue): DraftValue {
  if (definition.value.type === 'boolean') return value === true || value === 'true'
  if (definition.value.type === 'list') {
    const items = Array.isArray(value) ? value.map(String) : [String(value)]
    return definition.value.repeat ? items : items.join(definition.value.separator ?? ',')
  }
  return Array.isArray(value) ? value.join(' ') : String(value)
}

export function emptyDraft(definition: ArgumentDefinition): DraftValue {
  if (definition.value.type === 'list' && definition.value.repeat) return ['']
  return undefined
}

function hasControlCharacters(text: string) {
  return CONTROL_CHARACTERS.test(text)
}

function parseNumber(definition: ArgumentDefinition, text: string, integer: boolean): number | string {
  const schema = definition.value
  const trimmed = text.trim()
  if (schema.specialValues.includes(trimmed)) return trimmed
  const kind = integer ? 'a whole number' : 'a number'
  const special = schema.specialValues.length ? ` or one of: ${schema.specialValues.join(', ')}` : ''
  if (!(integer ? INTEGER : NUMBER).test(trimmed)) throw new Error(`${definition.flag} must be ${kind}${special}`)
  const value = Number(trimmed)
  if (!Number.isFinite(value)) throw new Error(`${definition.flag} must be a finite number`)
  if (schema.minimum !== undefined && value < schema.minimum) throw new Error(`${definition.flag} must be at least ${schema.minimum}`)
  if (schema.maximum !== undefined && value > schema.maximum) throw new Error(`${definition.flag} must be at most ${schema.maximum}`)
  return value
}

function checkPattern(definition: ArgumentDefinition, text: string) {
  if (definition.value.pattern && !new RegExp(definition.value.pattern).test(text)) {
    throw new Error(`${definition.flag} has an invalid format: ${text}`)
  }
}

function parseText(definition: ArgumentDefinition, text: string) {
  if (!text) throw new Error(`${definition.flag} requires a value`)
  if (hasControlCharacters(text)) throw new Error(`${definition.flag} must not contain newlines or control characters`)
  return text
}

/** Convert a draft to the stored value, throwing the same messages the server would. */
export function valueFromDraft(definition: ArgumentDefinition, draft: DraftValue): StoredArgumentValue {
  const schema = definition.value
  if (schema.type === 'boolean') {
    if (typeof draft !== 'boolean') throw new Error(`${definition.flag} must be true or false`)
    return draft
  }
  if (schema.type === 'list') {
    const items = (Array.isArray(draft) ? draft : String(draft ?? '').split(schema.separator ?? ','))
      .map((item) => (schema.repeat ? item : item.trim()))
      .filter((item) => item.trim() !== '')
    if (!items.length) throw new Error(`${definition.flag} requires at least one value`)
    return items.map((item) => {
      if (schema.itemType === 'integer' || schema.itemType === 'number') {
        const trimmed = item.trim()
        if (!(schema.itemType === 'integer' ? INTEGER : NUMBER).test(trimmed)) throw new Error(`${definition.flag} must contain only numbers`)
        return Number(trimmed)
      }
      parseText(definition, item)
      checkPattern(definition, item)
      return item
    })
  }
  const text = typeof draft === 'string' ? draft : ''
  if (schema.type === 'integer' || schema.type === 'number') {
    if (!text.trim()) throw new Error(`${definition.flag} requires a value`)
    return parseNumber(definition, text, schema.type === 'integer')
  }
  parseText(definition, text)
  if (schema.type === 'enum' && !schema.options.some((option) => option.value === text)) {
    throw new Error(`${definition.flag} must be one of: ${schema.options.map((option) => option.value).join(', ')}`)
  }
  if (schema.type === 'json') {
    try {
      JSON.parse(text)
    } catch {
      throw new Error(`${definition.flag} must be valid JSON`)
    }
  }
  checkPattern(definition, text)
  return text
}

export function draftError(definition: ArgumentDefinition, draft: DraftValue): string | undefined {
  if (!isSet(draft)) return undefined
  try {
    valueFromDraft(definition, draft)
    return undefined
  } catch (error) {
    return error instanceof Error ? error.message : String(error)
  }
}

export function rawEntryError(entry: Extract<EditorEntry, { kind: 'raw' }>): string | undefined {
  if (!RAW_FLAG.test(entry.flag)) return `unsafe option name: ${entry.flag || '(empty)'} (use a long option such as --example-option)`
  if (entry.flagOnly) return undefined
  if (!entry.value) return `${entry.flag} needs a value, or mark it as a flag without a value`
  if (hasControlCharacters(entry.value)) return `option ${entry.flag} contains a newline`
  if (SHELL_SYNTAX.test(entry.value)) return `option ${entry.flag} contains shell syntax, which is never interpreted and is blocked`
  return undefined
}

export function entriesFromStored(catalog: ArgumentCatalog, stored: unknown): EditorEntry[] {
  if (!Array.isArray(stored)) return []
  const { byId } = catalogIndex(catalog)
  const entries: EditorEntry[] = []
  for (const item of stored as Record<string, unknown>[]) {
    if (!item || typeof item !== 'object') continue
    if (typeof item.id === 'string') {
      const definition = byId.get(item.id)
      if (definition) {
        entries.push({ key: nextEntryKey(item.id), kind: 'catalog', id: item.id, draft: draftFromStored(definition, item.value as StoredArgumentValue) })
        continue
      }
      entries.push({ key: nextEntryKey('raw'), kind: 'raw', flag: `--${item.id}`, value: String(item.value), flagOnly: item.value === true, origin: 'catalog-mismatch' })
      continue
    }
    if (typeof item.flag === 'string') {
      const origin = (['runtime', 'custom', 'catalog-mismatch', 'deprecated'] as const).find((value) => value === item.origin) ?? 'custom'
      entries.push({ key: nextEntryKey('raw'), kind: 'raw', flag: item.flag, value: item.value === true ? '' : String(item.value ?? ''), flagOnly: item.value === true, origin })
    }
  }
  return entries
}

/** Serialize entries in their current order, skipping catalog arguments left unset. */
export function storedArguments(catalog: ArgumentCatalog, entries: EditorEntry[]): StoredArgument[] {
  const { byId } = catalogIndex(catalog)
  const result: StoredArgument[] = []
  for (const entry of entries) {
    if (entry.kind === 'raw') {
      result.push({ flag: entry.flag.trim(), value: entry.flagOnly ? true : entry.value, origin: entry.origin })
      continue
    }
    const definition = byId.get(entry.id)
    if (!definition || !isSet(entry.draft)) continue
    try {
      result.push({ id: entry.id, value: valueFromDraft(definition, entry.draft) })
    } catch {
      // Invalid drafts are reported as blockers; send the text so the server reports it too.
      result.push({ id: entry.id, value: Array.isArray(entry.draft) ? entry.draft : String(entry.draft) })
    }
  }
  return result
}

/** The flag an entry will emit, used for duplicate and runtime checks. */
function entryIdentity(entry: EditorEntry, byFlag: Map<string, ArgumentDefinition>) {
  if (entry.kind === 'catalog') return entry.id
  return byFlag.get(entry.flag)?.id ?? entry.flag
}

export function emittedFlag(definition: ArgumentDefinition, draft: DraftValue) {
  if (definition.value.type === 'boolean' && draft === false) return definition.value.negation
  return definition.flag
}

/** Client-side pre-flight mirroring the server's blockers and warnings for arguments. */
export function clientIssues(
  catalog: ArgumentCatalog,
  entries: EditorEntry[],
  acknowledged: string[],
  runtimeOptions?: string[],
): ClientIssue[] {
  const { byId, byFlag } = catalogIndex(catalog)
  const issues: ClientIssue[] = []
  const seen = new Map<string, string>()
  const present = new Map<string, ArgumentDefinition>()
  const advertised = runtimeOptions && runtimeOptions.length ? new Set(runtimeOptions) : undefined
  for (const entry of entries) {
    const definition = entry.kind === 'catalog' ? byId.get(entry.id) : byFlag.get(entry.flag)
    if (entry.kind === 'catalog' && !isSet(entry.draft)) continue
    const identity = entryIdentity(entry, byFlag)
    const label = entry.kind === 'catalog' ? definition?.flag ?? entry.id : entry.flag
    const target = entry.kind === 'catalog' ? entry.id : entry.key
    if (seen.has(identity)) issues.push({ level: 'error', message: `${label} is set more than once (also as ${seen.get(identity)})`, target })
    else seen.set(identity, label)
    if (entry.kind === 'raw') {
      const error = rawEntryError(entry)
      if (error) issues.push({ level: 'error', message: error, target })
    } else if (definition) {
      const error = draftError(definition, entry.draft)
      if (error) issues.push({ level: 'error', message: error, target })
    }
    if (definition) {
      present.set(definition.id, definition)
      if (definition.control === 'managed' || definition.control === 'informational') {
        issues.push({ level: 'error', message: `${label} cannot be set in a profile: ${definition.controlReason ?? 'it is controlled by LlamaWebUI'}`, target })
      } else if (definition.safety?.requiresConfirmation && !acknowledged.includes(definition.id)) {
        issues.push({ level: 'error', message: `${label} requires a security acknowledgement: ${definition.safety.warning}`, target })
      }
    }
    if (advertised) {
      const flag = entry.kind === 'raw' ? entry.flag : definition ? emittedFlag(definition, entry.draft) : undefined
      if (flag && !advertised.has(flag.replace(/^--/, ''))) {
        issues.push({ level: 'warning', message: `runtime does not advertise ${flag}; it will still be passed to llama-server`, target })
      }
    }
  }
  const reported = new Set<string>()
  for (const definition of present.values()) {
    for (const other of definition.conflicts) {
      const pair = [definition.id, other].sort().join('|')
      if (present.has(other) && !reported.has(pair)) {
        reported.add(pair)
        issues.push({ level: 'warning', message: `${definition.flag} conflicts with ${present.get(other)?.flag}; set only one`, target: definition.id })
      }
    }
    if (definition.requires.length && !definition.requires.some((id) => present.has(id))) {
      const needed = definition.requires.map((id) => byId.get(id)?.flag ?? `--${id}`).join(' or ')
      issues.push({ level: 'warning', message: `${definition.flag} has no effect without ${needed}`, target: definition.id })
    }
  }
  return issues
}

export interface SearchResult {
  kind: 'catalog' | 'runtime'
  key: string
  label: string
  flag: string
  summary: string
  definition?: ArgumentDefinition
  option?: RuntimeOptionInfo
}

/** Case-insensitive search over names, flags and descriptions; every term must match. */
function matchScore(terms: string[], names: string[], text: string) {
  const lowered = names.map((name) => name.toLowerCase())
  const joined = lowered.join(' ')
  let score = 0
  for (const term of terms) {
    // An exact flag, alias, or id match (for example "-c") outranks partial matches.
    if (lowered.includes(term)) score += 10
    else if (joined.includes(term)) score += 2
    else if (text.includes(term)) score += 1
    else return undefined
  }
  return score
}

export function searchArguments(catalog: EffectiveArgumentCatalog | ArgumentCatalog, query: string, limit = 12): SearchResult[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return []
  const results: { score: number; result: SearchResult }[] = []
  for (const definition of catalog.arguments) {
    const names = [definition.label, definition.id, definition.flag, ...definition.aliases]
    const text = [definition.help.summary, definition.help.details ?? '', definition.runtime?.help ?? ''].join(' ').toLowerCase()
    const match = matchScore(terms, names, text)
    if (match === undefined) continue
    const score = match + (definition.control === 'editable' ? 1 : 0)
    results.push({ score, result: { kind: 'catalog', key: definition.id, label: definition.label, flag: definition.flag, summary: definition.help.summary, definition } })
  }
  for (const option of 'uncatalogued' in catalog ? catalog.uncatalogued : []) {
    const score = matchScore(terms, [option.flag, ...option.aliases], option.help.toLowerCase())
    if (score === undefined) continue
    results.push({ score, result: { kind: 'runtime', key: option.flag, label: option.flag, flag: option.syntax || option.flag, summary: option.help || 'Advertised by this runtime; not in the catalog.', option } })
  }
  return results
    .sort((left, right) => right.score - left.score || left.result.label.localeCompare(right.result.label))
    .slice(0, limit)
    .map((item) => item.result)
}

export function simpleDefinitions(catalog: ArgumentCatalog) {
  return catalog.arguments
    .filter((definition) => !definition.isAdvanced && definition.control === 'editable')
    .sort((left, right) => (left.commonRank ?? 0) - (right.commonRank ?? 0))
}

export function groupByCategory<T>(catalog: ArgumentCatalog, items: T[], categoryOf: (item: T) => string) {
  const order = new Map(catalog.categories.map((category) => [category.id, category]))
  const groups = new Map<string, T[]>()
  for (const item of items) {
    const category = categoryOf(item)
    groups.set(category, [...(groups.get(category) ?? []), item])
  }
  return [...groups.entries()]
    .sort(([left], [right]) => (order.get(left)?.sortOrder ?? 999) - (order.get(right)?.sortOrder ?? 999))
    .map(([id, members]) => ({ id, label: order.get(id)?.label ?? id, items: members }))
}

/** Arguments a profile sets that the newly selected runtime does not advertise. */
export function unadvertisedOnRuntime(catalog: EffectiveArgumentCatalog, entries: EditorEntry[], runtimeOptions: string[]) {
  if (!catalog.runtime.helpParsed) return []
  const advertised = new Set(runtimeOptions)
  const { byId } = catalogIndex(catalog)
  const flags: string[] = []
  for (const entry of entries) {
    if (entry.kind === 'raw') {
      if (!advertised.has(entry.flag.replace(/^--/, ''))) flags.push(entry.flag)
      continue
    }
    const definition = byId.get(entry.id)
    if (!definition || !isSet(entry.draft)) continue
    const flag = emittedFlag(definition, entry.draft)
    if (flag && !advertised.has(flag.replace(/^--/, ''))) flags.push(flag)
  }
  return flags
}
