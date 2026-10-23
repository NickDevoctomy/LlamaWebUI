import { useQuery } from '@tanstack/react-query'
import { ArrowLeftRight, GitCompareArrows } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api, type ArgumentCatalog, type Profile, type Runtime, type StoredArgument } from './api'

type Side = string | null

interface CompareRow {
  key: string
  group: string
  label: string
  flag?: string
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
  const version = (profile: Profile) => profile.recommendation ? `v${profile.version ?? 1} (recommended)` : `v${profile.version ?? 1}`
  const rows: Omit<CompareRow, 'group'>[] = [
    { key: 'runtime', label: 'Runtime', left: runtime(left), right: runtime(right) },
    { key: 'model', label: 'Model file', left: left.model_path || null, right: right.model_path || null, leftDisplay: left.model_path ? fileName(left.model_path) : null, rightDisplay: right.model_path ? fileName(right.model_path) : null, leftTitle: left.model_path, rightTitle: right.model_path },
    { key: 'version', label: 'Version', left: version(left), right: version(right) },
    { key: 'exclusive', label: 'Exclusive', left: left.exclusive ? 'yes' : 'no', right: right.exclusive ? 'yes' : 'no' },
    { key: 'enabled', label: 'Enabled', left: left.enabled ? 'yes' : 'no', right: right.enabled ? 'yes' : 'no' },
    { key: 'description', label: 'Description', left: left.description ?? null, right: right.description ?? null },
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
      left: leftValue,
      right: rightValue,
      leftDisplay: shown(leftValue),
      rightDisplay: shown(rightValue),
      leftTitle: leftValue ?? undefined,
      rightTitle: rightValue ?? undefined,
    }
  }).sort((a, b) => order(a) - order(b) || a.label.localeCompare(b.label))
}

function SideValue({ value, title }: { value: Side; title?: string }) {
  return value === null ? <span className="compare-missing">Not set</span> : <span title={title ?? value}>{value}</span>
}

/** Pick two profiles and see where their settings differ. */
export function ProfileCompare({ profiles, runtimes }: { profiles: Profile[]; runtimes: Runtime[] }) {
  const [leftId, setLeftId] = useState(profiles[0]?.id ?? '')
  const [rightId, setRightId] = useState(profiles[1]?.id ?? '')
  const [differencesOnly, setDifferencesOnly] = useState(true)
  const catalog = useQuery({ queryKey: ['argument-catalog', 'base'], queryFn: api.argumentCatalog, refetchInterval: false, staleTime: Infinity })
  const left = profiles.find((profile) => profile.id === leftId) ?? profiles[0]
  const right = profiles.find((profile) => profile.id === rightId) ?? profiles.find((profile) => profile.id !== left?.id)

  const rows = useMemo(() => left && right
    ? [...generalRows(left, right, runtimes), ...argumentRows(left, right, catalog.data)]
    : [], [left, right, runtimes, catalog.data])
  if (profiles.length < 2 || !left || !right) return null

  const differing = rows.filter((row) => row.left !== row.right)
  const shown = differencesOnly ? differing : rows
  const groups = [...new Set(shown.map((row) => row.group))]
  const select = (label: string, value: string, onChange: (id: string) => void) => (
    <label className="compare-pick">
      <span>{label}</span>
      <select aria-label={`${label} profile`} onChange={(event) => onChange(event.target.value)} value={value}>
        {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.alias}</option>)}
      </select>
    </label>
  )

  return (
    <section aria-labelledby="compare-heading" className="data-panel profile-compare">
      <div className="panel-heading">
        <div><h2 id="compare-heading">Compare profiles</h2><p>Pick two profiles to see where their settings differ.</p></div>
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
              <span className="compare-value" data-side="left" role="cell"><SideValue title={row.leftTitle} value={row.leftDisplay !== undefined ? row.leftDisplay : row.left} /></span>
              <span className="compare-value" data-side="right" role="cell"><SideValue title={row.rightTitle} value={row.rightDisplay !== undefined ? row.rightDisplay : row.right} /></span>
            </div>
          })}
        </div>)}
      </div>}
    </section>
  )
}
