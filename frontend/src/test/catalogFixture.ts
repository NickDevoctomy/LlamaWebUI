import type { ArgumentDefinition, ArgumentValueSchema, EffectiveArgumentCatalog } from '../api'

type DefinitionInput = Partial<Omit<ArgumentDefinition, 'value' | 'help'>> & {
  id: string
  value?: Partial<ArgumentValueSchema>
  help?: Partial<ArgumentDefinition['help']>
}

export function definition(input: DefinitionInput): ArgumentDefinition {
  const { value, help, ...rest } = input
  return {
    flag: `--${input.id}`,
    aliases: [],
    label: input.id,
    category: 'context-kv',
    isAdvanced: true,
    control: 'editable',
    effectTags: [],
    conflicts: [],
    requires: [],
    runtime: { status: 'advertised', advertisedAs: null, syntax: null, help: null, default: null },
    ...rest,
    value: { type: 'string', options: [], repeat: false, specialValues: [], negationAliases: [], arity: 1, ...value },
    help: { summary: `${input.label ?? input.id} summary`, examples: [], notes: [], effects: [], ...help },
  }
}

export function catalogFixture(overrides: Partial<EffectiveArgumentCatalog> = {}): EffectiveArgumentCatalog {
  return {
    catalogId: 'llama-server',
    catalogVersion: '0.4.1',
    source: { url: 'https://example.invalid/readme', retrieved: '2026-09-21' },
    categories: [
      { id: 'process-cpu', label: 'Process and CPU', sortOrder: 10 },
      { id: 'context-kv', label: 'Context and KV cache', sortOrder: 20 },
      { id: 'security-network', label: 'Security and network', sortOrder: 80 },
    ],
    effectTags: {
      vram: { label: 'VRAM', color: 'violet', description: 'Device memory' },
      ram: { label: 'RAM', color: 'blue' },
      security: { label: 'Security', color: 'pink' },
    },
    runtime: { id: 'runtime-1', name: 'Local CPU', helpParsed: true, counts: { advertised: 1, alias: 0, 'not-advertised': 0, unknown: 0 } },
    uncatalogued: [{ flag: '--brand-new', aliases: ['-bn'], syntax: '-bn, --brand-new N', help: 'a brand new runtime option', default: null }],
    issues: [],
    arguments: [
      definition({
        id: 'ctx-size', label: 'Context size', aliases: ['-c'], isAdvanced: false, commonRank: 10, effectTags: ['vram', 'ram'],
        value: { type: 'integer', minimum: 0, unit: 'tokens' },
        help: {
          summary: 'Prompt context size.', details: 'Larger contexts use more memory.', syntax: '--ctx-size N', examples: ['32768'],
          effects: [{ text: 'Increases KV-cache memory.', direction: 'increase', tags: ['vram'] }],
        },
        runtime: { status: 'advertised', advertisedAs: null, syntax: '-c, --ctx-size N', help: 'size of the prompt context', default: '4096' },
      }),
      definition({ id: 'threads', label: 'CPU threads', category: 'process-cpu', isAdvanced: false, commonRank: 40, value: { type: 'integer', minimum: -1 } }),
      definition({ id: 'warmup', label: 'Startup warmup', value: { type: 'boolean', negation: '--no-warmup' } }),
      definition({ id: 'flash-attn', label: 'Flash attention', isAdvanced: false, commonRank: 30, value: { type: 'enum', options: [{ value: 'on' }, { value: 'off' }, { value: 'auto' }] } }),
      definition({ id: 'n-gpu-layers', label: 'GPU layers', value: { type: 'integer', minimum: 0, specialValues: ['auto', 'all'] } }),
      definition({ id: 'override-tensor', label: 'Tensor buffer overrides', value: { type: 'list', repeat: true, itemType: 'string', pattern: '^.+=.+$' } }),
      definition({ id: 'tensor-split', label: 'Tensor split proportions', value: { type: 'list', separator: ',', itemType: 'number' } }),
      definition({ id: 'chat-template-kwargs', label: 'Chat template parameters', value: { type: 'json' } }),
      definition({ id: 'cpu-moe', label: 'All MoE weights on CPU', value: { type: 'boolean' }, conflicts: ['n-cpu-moe'] }),
      definition({ id: 'n-cpu-moe', label: 'MoE layers on CPU', value: { type: 'integer', minimum: 0 }, conflicts: ['cpu-moe'] }),
      definition({ id: 'ssl-key-file', label: 'TLS private key', category: 'security-network', value: { type: 'path', pathKind: 'file' }, requires: ['ssl-cert-file'] }),
      definition({ id: 'ssl-cert-file', label: 'TLS certificate', category: 'security-network', value: { type: 'path', pathKind: 'file' }, requires: ['ssl-key-file'] }),
      definition({
        id: 'agent', label: 'Agent tools', category: 'security-network', effectTags: ['security'], value: { type: 'boolean', negation: '--no-agent' },
        safety: { level: 'danger', warning: 'Enables powerful local tools.', requiresConfirmation: true, sensitive: false },
        runtime: { status: 'not-advertised', advertisedAs: null, syntax: null, help: null, default: null },
      }),
      definition({ id: 'hf-token', label: 'Hugging Face token', category: 'security-network', safety: { level: 'danger', warning: 'Writes a token.', requiresConfirmation: true, sensitive: true } }),
      definition({ id: 'port', label: 'Listen port', category: 'security-network', control: 'managed', controlReason: 'Set by LlamaWebUI.', value: { type: 'integer' } }),
      definition({ id: 'defrag-thold', label: 'KV defragmentation threshold', value: { type: 'number' }, deprecated: { message: 'Deprecated in current builds.' } }),
      definition({ id: 'draft', label: 'Draft tokens (removed)', control: 'removed', value: { type: 'integer' }, deprecated: { message: 'Removed.', replacement: 'spec-draft-n-max' } }),
      definition({ id: 'spec-draft-n-max', label: 'Maximum draft tokens', aliases: ['--draft-max'], value: { type: 'integer' }, runtime: { status: 'alias', advertisedAs: '--draft-max', syntax: null, help: null, default: null } }),
    ],
    ...overrides,
  }
}
