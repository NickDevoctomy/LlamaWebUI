import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ArrowLeft, ArrowLeftRight, ArrowRight, GitCompareArrows, LoaderCircle } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type ArgumentCatalog, type BenchmarkResult, type Profile, type ProfileCreate, type Runtime, type StoredArgument } from './api'
import { formatRate, formatSeconds, phaseSpeeds, reasoningLabel } from './benchmarkFormat'

type Side = string | null

interface CompareRow {
  key: string
  group: string
  label: string
  flag?: string
  /** One profile's value can be copied onto the other; false for version and benchmark figures. */
  takeable?: boolean
  left: Side
  right: Side
  /** Shortened values to show, when they differ from the compared ones. */
  leftDisplay?: Side
  rightDisplay?: Side
  /** Full values, shown on hover where the cell is shortened. */
  leftTitle?: string
  rightTitle?: string
}

const GENERAL = 'Profile'
const REFERENCE = 'recommended:'

/** The compare-side id of a profile's recommended settings at one version. */
export const recommendedId = (profile: Profile, version: number) => `${REFERENCE}${profile.id}:${version}`

const isReference = (profile: Profile) => profile.id.startsWith(REFERENCE)

/**
 * A profile's recommended settings as read-only sides to compare against: the version it was
 * added from and, when newer, the bundled one. Anything a recommendation does not set is
 * taken from the profile, so only real setting changes show.
 */
export function recommendedReferences(profile: Profile): Profile[] {
  const recommendation = profile.recommendation
  if (!recommendation) return []
  const reference = (version: number, args: StoredArgument[], extra: Partial<Profile> = {}): Profile => ({
    ...profile,
    ...extra,
    id: recommendedId(profile, version),
    alias: `${profile.alias} · recommended v${version}`,
    version,
    configuration: { ...profile.configuration, arguments: args },
    hash: null,
    recommendation: null,
    benchmark_results: {},
  })
  const { baseline, latest } = recommendation
  const latestReference = latest ? reference(latest.version, latest.arguments, { exclusive: latest.exclusive, description: latest.description }) : undefined
  // The bundled version also has the description and exclusive setting, so it wins a tie.
  const baselineReference = baseline && baseline.version !== latest?.version ? reference(baseline.version, baseline.arguments) : undefined
  return [baselineReference, latestReference].filter((item): item is Profile => item !== undefined)
}

function storedArguments(profile: Profile): StoredArgument[] {
  return Array.isArray(profile.configuration.arguments) ? profile.configuration.arguments as StoredArgument[] : []
}

function formatValue(value: StoredArgument['value']): string {
  if (value === true) return 'on'
  if (value === false) return 'off'
  if (typeof value === 'number') return value.toLocaleString()
  return Array.isArray(value) ? value.join(', ') : String(value)
}

function fileName(path: string) {
  return path.split(/[\\/]/).at(-1) || path
}

function generalRows(left: Profile, right: Profile, runtimes: Runtime[]): CompareRow[] {
  const runtime = (profile: Profile) => runtimes.find((item) => item.id === profile.runtime_id)?.name ?? 'Missing runtime'
  const version = (profile: Profile) => profile.recommendation || isReference(profile) ? `v${profile.version ?? 1} (recommended)` : `v${profile.version ?? 1}`
  const rows: Omit<CompareRow, 'group'>[] = [
    { key: 'runtime', label: 'Runtime', takeable: true, left: runtime(left), right: runtime(right) },
    { key: 'model', label: 'Model file', takeable: true, left: left.model_path || null, right: right.model_path || null, leftDisplay: left.model_path ? fileName(left.model_path) : null, rightDisplay: right.model_path ? fileName(right.model_path) : null, leftTitle: left.model_path, rightTitle: right.model_path },
    { key: 'version', label: 'Version', left: version(left), right: version(right) },
    { key: 'exclusive', label: 'Exclusive', takeable: true, left: left.exclusive ? 'yes' : 'no', right: right.exclusive ? 'yes' : 'no' },
    { key: 'enabled', label: 'Enabled', takeable: true, left: left.enabled ? 'yes' : 'no', right: right.enabled ? 'yes' : 'no' },
    { key: 'description', label: 'Description', takeable: true, left: left.description ?? null, right: right.description ?? null },
  ]
  return rows.map((row) => ({ ...row, group: GENERAL }))
}

/** Every argument either profile sets, keyed by catalog id or, for raw flags, the flag. */
function argumentRows(left: Profile, right: Profile, catalog?: ArgumentCatalog): CompareRow[] {
  const definitions = new Map(catalog?.arguments.map((definition) => [definition.id, definition]))
  const categories = new Map(catalog?.categories.map((category) => [category.id, category]))
  const values = (profile: Profile) => new Map(storedArguments(profile).map((argument) => ['id' in argument ? `id:${argument.id}` : `flag:${argument.flag}`, argument] as const))
  const leftValues = values(left)
  const rightValues = values(right)
  const keys = [...new Set([...leftValues.keys(), ...rightValues.keys()])]
  const order = (row: CompareRow & { sortOrder: number }) => row.sortOrder
  return keys.map((key) => {
    const argument = leftValues.get(key) ?? rightValues.get(key)!
    const definition = 'id' in argument ? definitions.get(argument.id) : undefined
    const category = definition ? categories.get(definition.category) : undefined
    const leftArgument = leftValues.get(key)
    const rightArgument = rightValues.get(key)
    // Files are compared by full path but shown by name; hovering shows the path.
    const isPath = definition?.value.type === 'path'
    const shown = (value: string | null) => value !== null && isPath ? fileName(value) : value
    const leftValue = leftArgument ? formatValue(leftArgument.value) : null
    const rightValue = rightArgument ? formatValue(rightArgument.value) : null
    return {
      key,
      group: category?.label ?? 'Other arguments',
      sortOrder: category?.sortOrder ?? Number.MAX_SAFE_INTEGER,
      label: definition?.label ?? ('id' in argument ? argument.id : argument.flag),
      // A raw flag is already its own label.
      flag: definition?.flag,
      takeable: true,
      left: leftValue,
      right: rightValue,
      leftDisplay: shown(leftValue),
      rightDisplay: shown(rightValue),
      leftTitle: leftValue ?? undefined,
      rightTitle: rightValue ?? undefined,
    }
  }).sort((a, b) => order(a) - order(b) || a.label.localeCompare(b.label))
}

/** Each benchmark's stored results, so two profiles' speeds line up side by side. */
function benchmarkRows(left: Profile, right: Profile): CompareRow[] {
  const leftResults = left.benchmark_results ?? {}
  const rightResults = right.benchmark_results ?? {}
  const ids = [...new Set([...Object.keys(leftResults), ...Object.keys(rightResults)])].sort()
  const figures: [string, string, (result: BenchmarkResult) => string][] = [
    ['decode-low', 'Decode 1% low', (result) => formatRate(result.decode_low_tokens_per_second)],
    ['decode', 'Decode average', (result) => formatRate(result.decode_tokens_per_second)],
    ['decode-high', 'Decode 1% high', (result) => formatRate(phaseSpeeds(result, 'decode').high)],
    ['prefill-low', 'Prefill 1% low', (result) => formatRate(result.prompt_low_tokens_per_second)],
    ['prefill', 'Prefill average', (result) => formatRate(result.prompt_tokens_per_second)],
    ['prefill-high', 'Prefill 1% high', (result) => formatRate(phaseSpeeds(result, 'prefill').high)],
    ['load', 'Model load', (result) => formatSeconds(result.load_seconds)],
    ['first', 'First token', (result) => formatSeconds(result.first_token_seconds)],
    ['reasoning', 'Reasoning', (result) => reasoningLabel(result.reasoning_level)],
  ]
  return ids.flatMap((id) => {
    const title = (leftResults[id] ?? rightResults[id]).benchmark_title
    return figures.map(([key, label, format]) => ({
      key: `benchmark:${id}:${key}`,
      group: `Benchmark · ${title}`,
      label,
      left: leftResults[id] ? format(leftResults[id]) : null,
      right: rightResults[id] ? format(rightResults[id]) : null,
    }))
  })
}

function SideValue({ value, title }: { value: Side; title?: string }) {
  return value === null ? <span className="compare-missing">Not set</span> : <span title={title ?? value}>{value}</span>
}

const argumentKey = (argument: StoredArgument) => 'id' in argument ? `id:${argument.id}` : `flag:${argument.flag}`

/**
 * The save that gives `target` the value `source` has for one row: a field copied across,
 * or an argument added, replaced in place, or removed when `source` does not set it.
 */
function takeValue(target: Profile, source: Profile, key: string): ProfileCreate {
  const acknowledged = target.configuration.acknowledged
  const body: ProfileCreate = {
    alias: target.alias,
    runtime_id: target.runtime_id,
    model_path: target.model_path,
    enabled: target.enabled,
    exclusive: Boolean(target.exclusive),
    description: target.description ?? null,
    arguments: storedArguments(target),
    acknowledged: Array.isArray(acknowledged) ? acknowledged as string[] : [],
  }
  if (key === 'runtime') return { ...body, runtime_id: source.runtime_id }
  if (key === 'model') return { ...body, model_path: source.model_path }
  if (key === 'exclusive') return { ...body, exclusive: Boolean(source.exclusive) }
  if (key === 'enabled') return { ...body, enabled: source.enabled }
  if (key === 'description') return { ...body, description: source.description ?? null }
  const taken = storedArguments(source).find((argument) => argumentKey(argument) === key)
  const current = body.arguments ?? []
  const index = current.findIndex((argument) => argumentKey(argument) === key)
  const updated = !taken ? current.filter((_, position) => position !== index)
    : index < 0 ? [...current, taken]
      : current.map((argument, position) => position === index ? taken : argument)
  return { ...body, arguments: updated }
}

/** A request to show two sides, such as a profile beside its recommended settings. */
export interface CompareFocus {
  leftId: string
  rightId: string
  /** Changes on every request, so asking for the same pair again still scrolls to it. */
  nonce: number
}

/** Pick two profiles, or a profile and its recommended settings, see where they differ, and copy values across. */
export function ProfileCompare({ profiles, runtimes, running = false, focus }: { profiles: Profile[]; runtimes: Runtime[]; running?: boolean; focus?: CompareFocus }) {
  const [leftId, setLeftId] = useState(focus?.leftId ?? profiles[0]?.id ?? '')
  const [rightId, setRightId] = useState(focus?.rightId ?? profiles[1]?.id ?? '')
  const [differencesOnly, setDifferencesOnly] = useState(true)
  const [focused, setFocused] = useState(focus?.nonce)
  const section = useRef<HTMLElement>(null)
  if (focus && focus.nonce !== focused) {
    setFocused(focus.nonce)
    setLeftId(focus.leftId)
    setRightId(focus.rightId)
    setDifferencesOnly(true)
  }
  useEffect(() => {
    if (focus) section.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [focus])
  const catalog = useQuery({ queryKey: ['argument-catalog', 'base'], queryFn: api.argumentCatalog, refetchInterval: false, staleTime: Infinity })
  const queryClient = useQueryClient()
  const take = useMutation({
    mutationFn: ({ target, source, key }: { target: Profile; source: Profile; key: string }) => api.updateProfile(target.id, takeValue(target, source, key)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['profiles'] }),
  })
  const references = useMemo(() => profiles.flatMap(recommendedReferences), [profiles])
  const sides = [...profiles, ...references]
  const left = sides.find((profile) => profile.id === leftId) ?? sides[0]
  const right = sides.find((profile) => profile.id === rightId) ?? sides.find((profile) => profile.id !== left?.id)

  const rows = useMemo(() => left && right
    ? [
        ...generalRows(left, right, runtimes),
        ...argumentRows(left, right, catalog.data),
        // Recommended settings have no benchmark results of their own.
        ...(isReference(left) || isReference(right) ? [] : benchmarkRows(left, right)),
      ]
    : [], [left, right, runtimes, catalog.data])
  if (sides.length < 2 || !left || !right) return null

  const differing = rows.filter((row) => row.left !== row.right)
  // A button on one side takes the other side's value; it is offered only where they differ.
  const takeButton = (row: CompareRow, target: Profile, source: Profile, Icon: typeof ArrowLeft) => {
    if (!row.takeable || row.left === row.right || target.id === source.id || isReference(target)) return null
    const pending = take.isPending && take.variables?.key === row.key && take.variables.target.id === target.id
    return <button
      aria-label={`Take ${row.label} from ${source.alias}`}
      className="icon-button small compare-take"
      disabled={running || take.isPending}
      onClick={() => take.mutate({ target, source, key: row.key })}
      title={running ? 'Stop the router to change a profile' : `Take value from ${source.alias}`}
      type="button"
    >{pending ? <LoaderCircle className="spin" size={14} /> : <Icon size={14} />}</button>
  }
  const shown = differencesOnly ? differing : rows
  const groups = [...new Set(shown.map((row) => row.group))]
  const select = (label: string, value: string, onChange: (id: string) => void) => (
    <label className="compare-pick">
      <span>{label}</span>
      <select aria-label={`${label} profile`} onChange={(event) => onChange(event.target.value)} value={value}>
        {references.length > 0 ? <>
          <optgroup label="Profiles">{profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.alias}</option>)}</optgroup>
          <optgroup label="Recommended settings">{references.map((profile) => <option key={profile.id} value={profile.id}>{profile.alias}</option>)}</optgroup>
        </> : profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.alias}</option>)}
      </select>
    </label>
  )

  return (
    <section aria-labelledby="compare-heading" className="data-panel profile-compare" ref={section}>
      <div className="panel-heading">
        <div><h2 id="compare-heading">Compare profiles</h2><p>Pick two profiles, or a profile and its recommended settings, to see where they differ.</p></div>
        <GitCompareArrows size={18} />
      </div>
      <div className="compare-controls">
        {select('Left', left.id, setLeftId)}
        <button aria-label="Swap sides" className="icon-button" onClick={() => { setLeftId(right.id); setRightId(left.id) }} title="Swap sides" type="button"><ArrowLeftRight size={16} /></button>
        {select('Right', right.id, setRightId)}
        <label className="compare-toggle"><input checked={differencesOnly} onChange={(event) => setDifferencesOnly(event.target.checked)} type="checkbox" /> Only differences</label>
      </div>
      <p className="compare-summary" role="status">
        {left.id === right.id ? 'The same profile is selected on both sides.' : differing.length ? `${differing.length} ${differing.length === 1 ? 'difference' : 'differences'} across ${rows.length} settings.` : 'These profiles have identical settings.'}
      </p>
      {take.error && <div className="form-error compare-error" role="alert"><AlertCircle size={15} /> {take.error.message}</div>}
      {shown.length > 0 && <div className="compare-table" role="table" aria-label={`${left.alias} compared with ${right.alias}`}>
        <div className="compare-row compare-head" role="row">
          <span role="columnheader">Setting</span>
          <span role="columnheader">{left.alias}</span>
          <span role="columnheader">{right.alias}</span>
        </div>
        {groups.map((group) => <div className="compare-group" key={group} role="rowgroup">
          <div className="compare-group-label" role="row"><span role="rowheader">{group}</span></div>
          {shown.filter((row) => row.group === group).map((row) => {
            const state = row.left === row.right ? 'same' : row.left === null ? 'right-only' : row.right === null ? 'left-only' : 'changed'
            return <div className="compare-row" data-state={state} key={row.key} role="row">
              <span className="compare-setting" role="rowheader">{row.label}{row.flag && <small>{row.flag}</small>}</span>
              <span className="compare-value" data-side="left" role="cell">{takeButton(row, left, right, ArrowLeft)}<SideValue title={row.leftTitle} value={row.leftDisplay !== undefined ? row.leftDisplay : row.left} /></span>
              <span className="compare-value" data-side="right" role="cell">{takeButton(row, right, left, ArrowRight)}<SideValue title={row.rightTitle} value={row.rightDisplay !== undefined ? row.rightDisplay : row.right} /></span>
            </div>
          })}
        </div>)}
      </div>}
    </section>
  )
}
