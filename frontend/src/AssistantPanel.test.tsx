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
  messages: [
    { id: 'm1', seq: 1, role: 'user', content: 'What hardware do I have?', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: null, cost_usd: null, created_at: '' },
    { id: 'm2', seq: 2, role: 'assistant', content: '', reasoning: '', tool_calls: [{ id: 't1', function: { name: 'get_hardware', arguments: '{}' } }], tool_call_id: null, tool_name: null, summary: null, cost_usd: 0.001, created_at: '' },
    { id: 'm3', seq: 3, role: 'tool', content: '{"cpu":"Test CPU"}', reasoning: '', tool_calls: null, tool_call_id: 't1', tool_name: 'get_hardware', summary: { summary: 'Test CPU', ok: true }, cost_usd: null, created_at: '' },
    { id: 'm4', seq: 4, role: 'assistant', content: 'You have a **Test CPU**.', reasoning: 'Look it up.', tool_calls: null, tool_call_id: null, tool_name: null, summary: null, cost_usd: 0.002, created_at: '' },
    { id: 'm5', seq: 5, role: 'event', content: 'admin updated the brief.', reasoning: '', tool_calls: null, tool_call_id: null, tool_name: null, summary: { kind: 'brief' }, cost_usd: null, created_at: '' },
  ],
}

function renderPanel(canWrite = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><AssistantPanel canWrite={canWrite} status={status} /></QueryClientProvider>)
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
    expect(brief).toMatchObject({ subject: 'model', model: { repo_id: 'owner/Model-GGUF', quantization: null }, profile_id: null, min_decode_tps: 30, min_context_tokens: 65536, max_ram_gb: null, parallel_slots: 1, priority: ['prefill', 'decode', 'load', 'memory'] })
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
    renderPanel()
    expect(await screen.findByRole('alert')).toHaveTextContent('Insufficient credits')
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
