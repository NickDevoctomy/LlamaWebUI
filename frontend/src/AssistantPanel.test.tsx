import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type AssistantConversationDetail, type AssistantStatus } from './api'
import { AssistantPanel, briefTargets, formatCost } from './AssistantPanel'

const status: AssistantStatus = {
  available: true,
  openrouter_key_configured: true,
  name: 'Lama',
  model: { id: 'test/cheap', name: 'Cheap Model', description: '', context_length: 1000, pricing: { prompt: '0.000001', completion: '0.000002' }, selected_at: '2026-10-26T00:00:00Z' },
}

const conversation: AssistantConversationDetail = {
  id: 'c1',
  title: 'Get Model-GGUF working',
  created_by: 'admin',
  model_id: 'test/cheap',
  brief: { subject: 'model', model: { repo_id: 'owner/Model-GGUF', quantization: 'Q4_K_M' }, min_decode_tps: 30, parallel_slots: 1, benchmark_id: 'tetris', priority: ['decode', 'prefill', 'load', 'memory'] },
  state: 'idle',
  error: null,
  cost_usd: 0.0123,
  spend_limit_usd: 2,
  prompt_tokens: 100,
  completion_tokens: 20,
  created_at: '2026-10-26T00:00:00',
  updated_at: '2026-10-26T00:00:00',
  progress: null,
  approvals: [],
  messages: [
    { id: 'm1', seq: 1, role: 'user', content: 'What hardware do I have?', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: null, cost_usd: null, created_at: '' },
    { id: 'm2', seq: 2, role: 'assistant', content: '', reasoning: '', tool_calls: [{ id: 't1', function: { name: 'get_hardware', arguments: '{}' } }], tool_call_id: null, tool_name: null, summary: null, cost_usd: 0.001, created_at: '' },
    { id: 'm3', seq: 3, role: 'tool', content: '{"cpu":"Test CPU"}', reasoning: '', tool_calls: null, tool_call_id: 't1', tool_name: 'get_hardware', summary: { summary: 'Test CPU', ok: true }, cost_usd: null, created_at: '' },
    { id: 'm4', seq: 4, role: 'assistant', content: 'You have a **Test CPU**.', reasoning: 'Look it up.', tool_calls: null, tool_call_id: null, tool_name: null, summary: null, cost_usd: 0.002, created_at: '' },
    { id: 'm5', seq: 5, role: 'event', content: 'admin updated the brief.', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: { kind: 'brief' }, cost_usd: null, created_at: '' },
  ],
}

function renderPanel(canWrite = true, canDeleteRuns = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><AssistantPanel canDeleteRuns={canDeleteRuns} canWrite={canWrite} status={status} /></QueryClientProvider>)
}

beforeEach(() => {
  vi.spyOn(api, 'profiles').mockResolvedValue([])
  vi.spyOn(api, 'library').mockResolvedValue([])
  vi.spyOn(api, 'benchmarks').mockResolvedValue({ benchmarks: [{ id: 'tetris', title: 'Tetris', prompt: '', tasks: [], typescript: null }], system_prompts: [], default_system_prompt_id: null, default_benchmark_id: 'tetris', reasoning_levels: ['off', 'low'], default_max_tokens: null, tools: [], active_run_id: null })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('assistant helpers', () => {
  it('formats costs and brief targets', () => {
    expect(formatCost(0)).toBe('$0.00')
    expect(formatCost(0.0042)).toBe('$0.0042')
    expect(formatCost(1.5)).toBe('$1.50')
    expect(briefTargets({ subject: 'profile', min_prefill_tps: 500, max_load_seconds: 20, max_ram_gb: 48, max_vram_gb: 12, min_context_tokens: 65536, parallel_slots: 2 })).toEqual([
      'prefill ≥ 500 tok/s', 'load ≤ 20 s', 'RAM ≤ 48 GB', 'VRAM ≤ 12 GB', 'context ≥ 65,536 tokens', '2 parallel slots',
    ])
  })
})

describe('AssistantPanel', () => {
  it('switches between the chat and the research library', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([])
    vi.spyOn(api, 'libraryDocuments').mockResolvedValue({ documents: [], publish_problem: null } as never)
    renderPanel()

    expect(await screen.findByText('No conversations yet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Research' }))
    expect(await screen.findByText('No documents yet')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Research' })).toHaveAttribute('aria-selected', 'true')
  })

  it('starts with a brief when there are no conversations', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([])
    const create = vi.spyOn(api, 'createAssistantConversation').mockResolvedValue({ ...conversation, messages: [] })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'New optimisation' }))
    const dialog = screen.getByRole('dialog', { name: 'New optimisation' })
    const start = within(dialog).getByRole('button', { name: 'Start' })
    expect(start).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/Hugging Face repository/), { target: { value: 'owner/Model-GGUF' } })
    fireEvent.change(within(dialog).getByLabelText('Min decode (tok/s, average)'), { target: { value: '30' } })
    fireEvent.change(within(dialog).getByLabelText(/Min context/), { target: { value: '65536' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Move Prefill speed up' }))
    fireEvent.click(start)

    await waitFor(() => expect(create).toHaveBeenCalled())
    const [brief, title] = create.mock.calls[0]
    expect(title).toBe('')
    expect(brief).toMatchObject({ subject: 'model', model: { repo_id: 'owner/Model-GGUF', quantization: null }, profile_id: null, min_decode_tps: 30, min_context_tokens: 65536, max_ram_gb: null, parallel_slots: 1, system_prompt_extra: null, priority: ['prefill', 'decode', 'load', 'memory'] })
  })

  it('starts a profile brief at the reasoning level the profile was last benchmarked at', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([])
    vi.spyOn(api, 'profiles').mockResolvedValue([
      { id: 'p1', alias: 'qwen', benchmark_results: { tetris: { reasoning_level: 'low' } } },
      { id: 'p2', alias: 'fresh' },
    ] as never)
    const create = vi.spyOn(api, 'createAssistantConversation').mockResolvedValue({ ...conversation, messages: [] })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'New optimisation' }))
    const dialog = screen.getByRole('dialog', { name: 'New optimisation' })
    fireEvent.click(within(dialog).getByRole('tab', { name: 'Optimise a profile' }))
    await within(dialog).findByRole('option', { name: 'qwen' })
    fireEvent.change(within(dialog).getByLabelText('Profile'), { target: { value: 'p1' } })
    expect(within(dialog).getByLabelText(/^Reasoning/)).toHaveValue('low')
    expect(within(dialog).getByText('As the profile was last measured.')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Profile'), { target: { value: 'p2' } })
    expect(within(dialog).getByLabelText(/^Reasoning/)).toHaveValue('')
    fireEvent.change(within(dialog).getByLabelText('Profile'), { target: { value: 'p1' } })
    fireEvent.change(within(dialog).getByLabelText(/^Added to system prompt/), { target: { value: '  Reasoning strength: high\n' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start' }))
    await waitFor(() => expect(create.mock.calls[0]?.[0]).toMatchObject({ profile_id: 'p1', reasoning_level: 'low', system_prompt_extra: 'Reasoning strength: high' }))
  })

  it('deletes the benchmark runs a conversation started', async () => {
    const withRuns = { ...conversation, benchmark_runs: 3 }
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([withRuns])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(withRuns)
    const cleanup = vi.spyOn(api, 'deleteConversationRuns').mockResolvedValue({ deleted: ['a', 'b'], skipped: [{ run_id: 'c', reason: 'running' }] })
    renderPanel(true, true)

    fireEvent.click(await screen.findByRole('button', { name: 'Delete 3 runs' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete 3 benchmark runs?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete runs' }))
    await waitFor(() => expect(cleanup).toHaveBeenCalledWith('c1'))
    expect(await screen.findByRole('status')).toHaveTextContent('Deleted 2 benchmark runs; 1 still running was kept.')
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(/Deleted 2 benchmark runs/)).not.toBeInTheDocument()
  })

  it('offers no run clean-up without permission or runs', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([conversation])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue({ ...conversation, benchmark_runs: 2 })
    renderPanel(true, false)
    expect(await screen.findByRole('heading', { name: conversation.title })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete 2 runs/ })).not.toBeInTheDocument()
  })

  it('shows the conversation, its tool calls and cost, and sends a message', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([conversation])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(conversation)
    const send = vi.spyOn(api, 'sendAssistantMessage').mockResolvedValue({ ...conversation, state: 'running' })
    renderPanel()

    expect(await screen.findByText('Test CPU', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText('Spent $0.01 of $2.00')).toBeInTheDocument()
    expect(screen.getByText(/Cheap Model · \$1\.00 in · \$2\.00 out per 1M tokens/)).toBeInTheDocument()
    expect(screen.getByText('owner/Model-GGUF · Q4_K_M')).toBeInTheDocument()
    expect(screen.getByText('decode ≥ 30 tok/s')).toBeInTheDocument()
    expect(screen.getByText('get_hardware')).toBeInTheDocument()
    expect(screen.getByText('admin updated the brief.')).toBeInTheDocument()
    expect(screen.getAllByText('Lama').length).toBeGreaterThan(0)

    const box = screen.getByPlaceholderText('Message Lama…')
    fireEvent.change(box, { target: { value: 'Now tune it' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(send).toHaveBeenCalledWith('c1', 'Now tune it'))
  })

  it('streams a running turn and stops it', async () => {
    const running: AssistantConversationDetail = { ...conversation, state: 'running', progress: { round: 2, content: 'Checking the **profile**', reasoning: '', tool_call: null, running_tool: 'get_profile' } }
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([running])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(running)
    const stop = vi.spyOn(api, 'stopAssistant').mockResolvedValue(conversation)
    renderPanel()

    expect(await screen.findByText('profile', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText('get_profile', { selector: 'code' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Message Lama…')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit brief' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(stop).toHaveBeenCalledWith('c1'))
  })

  it('offers to continue a paused conversation and shows a failure', async () => {
    const paused = { ...conversation, state: 'paused' as const }
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([paused])
    const detail = vi.spyOn(api, 'assistantConversation').mockResolvedValue(paused)
    const resume = vi.spyOn(api, 'continueAssistant').mockResolvedValue({ ...conversation, state: 'running' })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(resume).toHaveBeenCalledWith('c1'))

    cleanup()
    detail.mockResolvedValue({ ...conversation, state: 'failed', error: 'OpenRouter: Insufficient credits (402)' })
    const retry = vi.spyOn(api, 'retryAssistant').mockResolvedValue({ ...conversation, state: 'running' })
    renderPanel()
    expect(await screen.findByText('The last reply failed; the reason is shown above.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(retry).toHaveBeenCalledWith('c1'))
  })

  it('runs the brief again in a new conversation with the same first message', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([conversation])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(conversation)
    const create = vi.spyOn(api, 'createAssistantConversation').mockResolvedValue({ ...conversation, id: 'c2', messages: [] })
    const send = vi.spyOn(api, 'sendAssistantMessage').mockResolvedValue({ ...conversation, id: 'c2', state: 'running' })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Run again' }))
    const dialog = screen.getByRole('dialog', { name: 'Run again' })
    expect(within(dialog).getByLabelText(/Hugging Face repository/)).toHaveValue('owner/Model-GGUF')
    expect(within(dialog).getByLabelText('Min decode (tok/s, average)')).toHaveValue(30)
    expect(within(dialog).getByLabelText(/First message/)).toHaveValue('What hardware do I have?')
    const autonomous = within(dialog).getByRole('checkbox', { name: /Work without asking questions/ })
    expect(autonomous).toBeChecked()
    fireEvent.click(autonomous)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start' }))

    await waitFor(() => expect(send).toHaveBeenCalledWith('c2', 'What hardware do I have?'))
    expect(create.mock.calls[0]?.[0]).toMatchObject({ subject: 'model', model: { repo_id: 'owner/Model-GGUF', quantization: 'Q4_K_M' }, min_decode_tps: 30, autonomous: false })
  })

  it('shows that a failed request is being retried', async () => {
    const retrying: AssistantConversationDetail = { ...conversation, state: 'running', progress: { round: 1, content: '', reasoning: '', tool_call: null, retrying: { attempt: 1, error: 'OpenRouter: Inference engine abort.' } } }
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([retrying])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(retrying)
    renderPanel()

    expect(await screen.findByText('OpenRouter: Inference engine abort. Trying again (2)…')).toBeInTheDocument()
  })

  it('edits the brief of an idle conversation', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([conversation])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(conversation)
    const update = vi.spyOn(api, 'updateAssistantBrief').mockResolvedValue(conversation)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Edit brief' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit brief' })
    expect(within(dialog).getByLabelText(/Hugging Face repository/)).toHaveValue('owner/Model-GGUF')
    expect(within(dialog).queryByLabelText(/Title/)).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Max RAM (GB)'), { target: { value: '48' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save brief' }))
    await waitFor(() => expect(update.mock.calls[0]?.[1]).toMatchObject({ max_ram_gb: 48, min_decode_tps: 30 }))
  })

  it('is read-only without assistant.write', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([conversation])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(conversation)
    renderPanel(false)

    expect(await screen.findByPlaceholderText('Message Lama…')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'New' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit brief' })).toBeDisabled()
  })
})

describe('AssistantPanel approvals and notifications', () => {
  const waiting: AssistantConversationDetail = {
    ...conversation,
    state: 'awaiting_approval',
    messages: [
      ...conversation.messages,
      { id: 'm6', seq: 6, role: 'assistant', content: 'Raising the context.', reasoning: '', tool_calls: [{ id: 't2', function: { name: 'update_profile', arguments: '{"profile":"qwen"}' } }], tool_call_id: null, tool_name: null, summary: null, cost_usd: 0.001, created_at: '' },
      { id: 'm7', seq: 7, role: 'event', content: 'Download d1 (owner/Model-GGUF, Q4) completed: 4 GiB.', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: { kind: 'notification', ok: true }, cost_usd: null, created_at: '' },
      { id: 'm8', seq: 8, role: 'event', content: 'Benchmark run r1 failed: boom.', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: { kind: 'notification', ok: false }, cost_usd: null, created_at: '' },
      { id: 'm9', seq: 9, role: 'event', content: 'You changed qwen after its last benchmark.', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: { kind: 'check' }, cost_usd: null, created_at: '' },
    ],
    approvals: [
      { id: 'a0', tool_call_id: 't1', tool_name: 'get_hardware', category: 'profiles', title: 'Old', details: {}, state: 'approved', decided_by: 'admin', created_at: '', decided_at: '' },
      { id: 'a1', tool_call_id: 't2', tool_name: 'update_profile', category: 'profiles', title: 'Change profile qwen', details: { changes: [{ argument: 'ctx-size', before: 8192, after: 16384 }], enabled: true }, state: 'pending', decided_by: null, created_at: '', decided_at: null },
    ],
  }

  it('asks for approval with the change, and sends the decision', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([waiting])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(waiting)
    const decide = vi.spyOn(api, 'decideAssistantApproval').mockResolvedValue({ ...waiting, state: 'running' })
    renderPanel()

    const card = await screen.findByRole('group', { name: 'Approval needed: Change profile qwen' })
    expect(within(card).getByText('ctx-size')).toBeInTheDocument()
    expect(within(card).getByText('16384')).toBeInTheDocument()
    expect(within(card).getByText('true')).toBeInTheDocument()
    expect(screen.getAllByText('Needs approval').length).toBeGreaterThan(0)
    expect(screen.getByText('Approved', { selector: '.decision-tag' })).toHaveAttribute('title', 'By admin')
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
    expect(screen.getByText(/Download d1/)).toHaveClass('notification')
    expect(screen.getByText(/Benchmark run r1 failed/)).toHaveClass('failed')
    expect(screen.getByText('Reminder to Lama:').closest('li')).toHaveTextContent('Reminder to Lama: You changed qwen after its last benchmark.')

    fireEvent.click(within(card).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(decide).toHaveBeenCalledWith('a1', true))
    fireEvent.click(within(card).getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(decide).toHaveBeenCalledWith('a1', false))
  })

  it('cannot decide without assistant.write', async () => {
    vi.spyOn(api, 'assistantConversations').mockResolvedValue([waiting])
    vi.spyOn(api, 'assistantConversation').mockResolvedValue(waiting)
    renderPanel(false)

    const card = await screen.findByRole('group', { name: 'Approval needed: Change profile qwen' })
    expect(within(card).getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(within(card).getByText(/needs the assistant.write privilege/)).toBeInTheDocument()
  })
})
