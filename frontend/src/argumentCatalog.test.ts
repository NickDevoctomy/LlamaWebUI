import { describe, expect, it } from 'vitest'
import {
  type DraftValue,
  type EditorEntry,
  catalogIndex,
  clientIssues,
  draftError,
  draftFromStored,
  emptyDraft,
  entriesFromStored,
  groupByCategory,
  rawEntryError,
  searchArguments,
  simpleDefinitions,
  storedArguments,
  unadvertisedOnRuntime,
  valueFromDraft,
} from './argumentCatalog'
import { catalogFixture } from './test/catalogFixture'

const catalog = catalogFixture()
const { byId } = catalogIndex(catalog)
const def = (id: string) => byId.get(id)!
const entry = (id: string, draft: DraftValue): EditorEntry => ({ key: `k-${id}`, kind: 'catalog', id, draft })
const raw = (flag: string, value = '', flagOnly = false, origin: 'custom' | 'runtime' = 'custom'): EditorEntry => ({ key: `raw-${flag}`, kind: 'raw', flag, value, flagOnly, origin })

describe('argument value conversion', () => {
  it('converts drafts to stored values for each schema type', () => {
    expect(valueFromDraft(def('ctx-size'), '4096')).toBe(4096)
    expect(valueFromDraft(def('n-gpu-layers'), 'auto')).toBe('auto')
    expect(valueFromDraft(def('warmup'), false)).toBe(false)
    expect(valueFromDraft(def('flash-attn'), 'on')).toBe('on')
    expect(valueFromDraft(def('override-tensor'), ['a=CPU', '', 'b=CPU'])).toEqual(['a=CPU', 'b=CPU'])
    expect(valueFromDraft(def('tensor-split'), '3, 1.5')).toEqual([3, 1.5])
    expect(valueFromDraft(def('chat-template-kwargs'), '{"a":1}')).toBe('{"a":1}')
  })

  it('reports the same messages the server uses for invalid drafts', () => {
    expect(draftError(def('ctx-size'), 'many')).toBe('--ctx-size must be a whole number')
    expect(draftError(def('ctx-size'), '-5')).toBe('--ctx-size must be at least 0')
    expect(draftError(def('n-gpu-layers'), 'most')).toBe('--n-gpu-layers must be a whole number or one of: auto, all')
    expect(draftError(def('flash-attn'), 'maybe')).toBe('--flash-attn must be one of: on, off, auto')
    expect(draftError(def('chat-template-kwargs'), '{')).toBe('--chat-template-kwargs must be valid JSON')
    expect(draftError(def('override-tensor'), ['nope'])).toBe('--override-tensor has an invalid format: nope')
    expect(draftError(def('tensor-split'), '3,x')).toBe('--tensor-split must contain only numbers')
    expect(draftError(def('ssl-key-file'), 'C:\\a\nb')).toBe('--ssl-key-file must not contain newlines or control characters')
    expect(draftError(def('ctx-size'), '')).toBeUndefined()
    expect(() => valueFromDraft(def('warmup'), 'yes')).toThrow('true or false')
    expect(() => valueFromDraft(def('tensor-split'), '')).toThrow('at least one value')
    expect(() => valueFromDraft(def('ctx-size'), '   ')).toThrow('requires a value')
    expect(() => valueFromDraft(def('ssl-key-file'), '')).toThrow('requires a value')
  })

  it('round-trips stored arguments through editor entries in order', () => {
    const stored = [
      { id: 'ctx-size', value: 8192 },
      { id: 'warmup', value: false },
      { id: 'override-tensor', value: ['a=CPU'] },
      { id: 'tensor-split', value: [3, 1] },
      { id: 'from-a-newer-catalog', value: 2 },
      { flag: '--brand-new', value: '2', origin: 'runtime' },
      { flag: '--legacy', value: true, origin: 'unexpected' },
      'junk',
    ]

    const entries = entriesFromStored(catalog, stored)

    expect(storedArguments(catalog, entries)).toEqual([
      { id: 'ctx-size', value: 8192 },
      { id: 'warmup', value: false },
      { id: 'override-tensor', value: ['a=CPU'] },
      { id: 'tensor-split', value: [3, 1] },
      { flag: '--from-a-newer-catalog', value: '2', origin: 'catalog-mismatch' },
      { flag: '--brand-new', value: '2', origin: 'runtime' },
      { flag: '--legacy', value: true, origin: 'custom' },
    ])
    expect(entriesFromStored(catalog, 'not a list')).toEqual([])
    expect(draftFromStored(def('ctx-size'), [1, 2])).toBe('1 2')
    expect(emptyDraft(def('override-tensor'))).toEqual([''])
    expect(emptyDraft(def('ctx-size'))).toBeUndefined()
  })

  it('omits unset catalog arguments and sends invalid drafts for server reporting', () => {
    expect(storedArguments(catalog, [entry('ctx-size', ''), entry('threads', 'x'), entry('missing', '1')])).toEqual([
      { id: 'threads', value: 'x' },
    ])
  })
})

describe('client pre-flight', () => {
  it('blocks unsafe raw parameters', () => {
    expect(rawEntryError({ key: 'a', kind: 'raw', flag: '-x', value: '', flagOnly: true, origin: 'custom' })).toContain('unsafe option name')
    expect(rawEntryError({ key: 'a', kind: 'raw', flag: '', value: '', flagOnly: true, origin: 'custom' })).toContain('(empty)')
    expect(rawEntryError({ key: 'a', kind: 'raw', flag: '--x', value: '', flagOnly: false, origin: 'custom' })).toContain('needs a value')
    expect(rawEntryError({ key: 'a', kind: 'raw', flag: '--x', value: 'a\nb', flagOnly: false, origin: 'custom' })).toContain('newline')
    for (const value of ['$(id)', 'a ; b', 'x > y', '`id`']) {
      expect(rawEntryError({ key: 'a', kind: 'raw', flag: '--x', value, flagOnly: false, origin: 'custom' })).toContain('shell syntax')
    }
    expect(rawEntryError({ key: 'a', kind: 'raw', flag: '--x', value: 'top_k;top_p', flagOnly: false, origin: 'custom' })).toBeUndefined()
  })

  it('reports duplicates, managed flags, acknowledgements, relationships and runtime gaps', () => {
    const entries = [
      entry('ctx-size', '1'),
      raw('--ctx-size', '2'),
      raw('--port', '80'),
      entry('agent', true),
      entry('cpu-moe', true),
      entry('n-cpu-moe', '2'),
      entry('ssl-key-file', 'C:\\key.pem'),
      entry('warmup', false),
      entry('threads', ''),
      entry('ctx-size', 'x'),
      raw('-bad', '', true),
    ]

    const issues = clientIssues(catalog, entries, [], ['ctx-size', 'agent'])
    const messages = issues.map((issue) => `${issue.level}: ${issue.message}`)

    expect(messages).toContain('error: --ctx-size is set more than once (also as --ctx-size)')
    expect(messages).toContain('error: --port cannot be set in a profile: Set by LlamaWebUI.')
    expect(messages).toContain('error: --agent requires a security acknowledgement: Enables powerful local tools.')
    expect(messages).toContain('warning: --cpu-moe conflicts with --n-cpu-moe; set only one')
    expect(messages).toContain('warning: --ssl-key-file has no effect without --ssl-cert-file')
    expect(messages).toContain('warning: runtime does not advertise --no-warmup; it will still be passed to llama-server')
    expect(messages).toContain('error: --ctx-size must be a whole number')
    expect(messages.some((message) => message.includes('unsafe option name: -bad'))).toBe(true)
    expect(clientIssues(catalog, [entry('agent', true)], ['agent']).filter((issue) => issue.level === 'error')).toEqual([])
    expect(clientIssues(catalog, [entry('warmup', false)], [], [])).toEqual([])
  })

  it('lists flags a switched-to runtime does not advertise', () => {
    const entries = [entry('ctx-size', '1'), entry('warmup', true), raw('--brand-new', '1'), entry('threads', '')]
    expect(unadvertisedOnRuntime(catalog, entries, ['ctx-size'])).toEqual(['--warmup', '--brand-new'])
    expect(unadvertisedOnRuntime(catalogFixture({ runtime: { ...catalog.runtime, helpParsed: false } }), entries, [])).toEqual([])
  })
})

describe('catalog navigation', () => {
  it('searches names, flags and descriptions including uncatalogued runtime options', () => {
    expect(searchArguments(catalog, '')).toEqual([])
    expect(searchArguments(catalog, 'context')[0].key).toBe('ctx-size')
    expect(searchArguments(catalog, '-c')[0].key).toBe('ctx-size')
    expect(searchArguments(catalog, 'memory larger').map((result) => result.key)).toEqual(['ctx-size'])
    const runtime = searchArguments(catalog, 'brand new')
    expect(runtime[0]).toMatchObject({ kind: 'runtime', key: '--brand-new' })
    expect(searchArguments(catalog, 'zzz-nothing')).toEqual([])
    const { uncatalogued: _ignored, ...plain } = catalog
    expect(searchArguments(plain, 'brand new')).toEqual([])
  })

  it('orders Simple mode by rank and groups by category order', () => {
    expect(simpleDefinitions(catalog).map((item) => item.id)).toEqual(['ctx-size', 'flash-attn', 'threads'])
    const groups = groupByCategory(catalog, ['ctx-size', 'threads', 'agent', 'mystery'], (id) => byId.get(id)?.category ?? 'unknown')
    expect(groups.map((group) => group.label)).toEqual(['Process and CPU', 'Context and KV cache', 'Security and network', 'unknown'])
  })
})
