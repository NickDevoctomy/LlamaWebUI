import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Benchmark, type BenchmarkRun, type BenchmarkRunDetail, type BenchmarkTool, type Profile, type RouterState } from './api'
import { BenchmarksPanel } from './BenchmarksPanel'

const profiles = [
  { id: 'p1', alias: 'qwen-24gb', enabled: true, version: 2 },
  { id: 'p2', alias: 'qwen-off', enabled: false, version: 1 },
] as Profile[]

const finished: BenchmarkRun = {
  id: 'run-1',
  profile_id: 'p1',
  profile_alias: 'qwen-24gb',
  profile_version: 2,
  benchmark_id: 'tetris',
  benchmark_title: 'Tetris',
  max_tokens: 4096,
  tools: false,
  system_prompt_id: null,
  reasoning_level: null,
  state: 'completed',
  stage: 'done',
  error: null,
  stats: { server_start_seconds: 2.1, load_seconds: 41.3, prompt_tokens: 30, prompt_tokens_per_second: 412.5, prompt_low_tokens_per_second: 380.2, prompt_high_tokens_per_second: 451, generated_tokens: 3001, decode_tokens_per_second: 21.37, decode_low_tokens_per_second: 12.4, decode_high_tokens_per_second: 33.8, decode_final_tokens_per_second: 18.2, total_seconds: 190.2, finish_reason: 'stop' },
  started_at: '2026-10-23T11:00:00',
  finished_at: '2026-10-23T11:03:10',
}

const running: BenchmarkRun = {
  ...finished,
  id: 'run-2',
  state: 'running',
  stage: 'decoding',
  stats: { server_start_seconds: 2.0, load_seconds: 40.1, first_token_seconds: 3.2 },
  live: { prefill: { tokens: 30, rate: 400, average: 380 }, decode: { tokens: 120, rate: 19.5, average: 24.1 } },
  finished_at: null,
}

// check_file waits for ESLint, which these tests have not installed.
const TOOLS = ['write_file', 'edit_file', 'read_file', 'delete_file', 'list_files', 'check_file', 'finish_job'].map((name) => ({ name, summary: `What ${name} does.`, available: name !== 'check_file' }))
const ALL_TOOLS = TOOLS.filter((tool) => tool.available).map((tool) => tool.name)

const TETRIS: Benchmark = { id: 'tetris', title: 'Tetris', prompt: 'make tetris', tasks: [], typescript: null }

function mockApi(runs: BenchmarkRun[], { benchmarks = [TETRIS], tools = TOOLS, defaultBenchmarkId = null }: { benchmarks?: Benchmark[]; tools?: BenchmarkTool[]; defaultBenchmarkId?: string | null } = {}) {
  vi.spyOn(api, 'benchmarks').mockResolvedValue({ benchmarks, system_prompts: [{ id: 'coding-agent', title: 'Coding agent', text: 'You are an AI agent running in a coding agent harness.' }], default_system_prompt_id: 'coding-agent', default_benchmark_id: defaultBenchmarkId, reasoning_levels: ['off', 'low', 'medium', 'high', 'xhigh'], default_max_tokens: null, tools, active_run_id: null })
  vi.spyOn(api, 'benchmarkRuns').mockResolvedValue(runs)
}

function renderPanel(routerState: RouterState = 'stopped', canRun = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><BenchmarksPanel canRun={canRun} profiles={profiles} routerState={routerState} /></QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('BenchmarksPanel', () => {
  it('starts a benchmark for an enabled profile from a stopped router', async () => {
    mockApi([finished])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const run = await screen.findByRole('button', { name: 'Run benchmark' })
    await waitFor(() => expect(run).toBeEnabled())
    expect(screen.getByRole('option', { name: 'qwen-off (disabled)' })).toBeDisabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Max tokens' }), { target: { value: '2048' } })
    fireEvent.click(run)

    await waitFor(() => expect(start).toHaveBeenCalledWith({ profile_id: 'p1', benchmark_id: 'tetris', max_tokens: 2048, tools: true, tool_names: ALL_TOOLS, system_prompt_id: 'coding-agent', system_prompt_extra: null, reasoning_level: null, verbose_log: false, compact_context: true }))
  })

  it('picks the catalog\'s default benchmark until another is chosen', async () => {
    const plain: Benchmark = { ...TETRIS, id: 'tetris-no-details', title: 'Tetris no details' }
    mockApi([], { benchmarks: [TETRIS, plain], defaultBenchmarkId: 'tetris-no-details' })
    renderPanel()

    const picker = await screen.findByRole('combobox', { name: 'Benchmark' })
    await waitFor(() => expect(picker).toHaveValue('tetris-no-details'))
    fireEvent.change(picker, { target: { value: 'tetris' } })
    expect(picker).toHaveValue('tetris')
  })

  it('keeps a verbose log when asked, and shows each call\'s arguments as sent', async () => {
    const body = 'function a() {\n  return 1\n}'
    const sent = JSON.stringify({ path: 'src/render.ts', old_string: body, new_string: 'b' })
    const verbose: BenchmarkRun = { ...finished, tools: true, tool_names: ALL_TOOLS, verbose_log: true }
    mockApi([verbose])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue({ ...verbose, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [{ round: 1, name: 'edit_file', path: 'src/render.ts', ok: false, result: 'Error: Could not find old_string', arguments: sent }, { round: 2, name: 'write_file', path: 'a', ok: false, result: 'Error: the tool arguments were not a JSON object.', arguments: '{"path": "a' }], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const run = await screen.findByRole('button', { name: 'Run benchmark' })
    await waitFor(() => expect(run).toBeEnabled())
    fireEvent.click(screen.getByRole('checkbox', { name: 'Verbose log' }))
    fireEvent.click(run)
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ verbose_log: true })))

    // Without tools there is nothing to log, so the option goes.
    fireEvent.click(screen.getByRole('checkbox', { name: 'Tools' }))
    expect(screen.queryByRole('checkbox', { name: 'Verbose log' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry run Tetris on qwen-24gb' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ verbose_log: true })))

    fireEvent.click(screen.getByRole('button', { name: 'View run Tetris on qwen-24gb' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect(await within(dialog).findByText('Verbose')).toBeInTheDocument()
    expect(within(dialog).getByText('old_string')).toBeInTheDocument()
    expect(within(dialog).getByText((_, element) => element?.tagName === 'PRE' && element.textContent === body)).toBeInTheDocument()
    expect(within(dialog).getByText('Not a JSON object, as sent:')).toBeInTheDocument()
  })

  it('shows how much of the context each run filled, and opens a run from its title', async () => {
    const full: BenchmarkRun = { ...finished, stats: { ...finished.stats, context_peak: 64900, context_size: 65536 } }
    const live: BenchmarkRun = { ...running, live: { ...running.live, context: { used: 16384, size: 65536 } } }
    const older: BenchmarkRun = { ...finished, id: 'run-old', benchmark_title: 'Snake' }
    mockApi([live, full, older])
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue({ ...full, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const percent = await screen.findByText('99%')
    expect(percent).toHaveAttribute('title', '64,900 of 65,536 tokens at most')
    expect(percent).toHaveAttribute('data-full', 'true')
    expect(screen.getByText('25%')).toHaveAttribute('title', '16,384 of 65,536 tokens now')
    expect(screen.queryByRole('button', { name: /^View$/ })).not.toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: 'View run Tetris on qwen-24gb' })[1])
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect(await within(dialog).findByText('64,900 / 65,536 (99%)')).toBeInTheDocument()
  })

  it('retries a run with its settings on the profile as it is now', async () => {
    const chosen: BenchmarkRun = { ...finished, profile_version: 1, tools: true, tool_names: ['write_file', 'finish_job'], max_tokens: null, system_prompt_id: 'coding-agent', system_prompt_extra: 'Reasoning strength: high', reasoning_level: 'high' }
    const legacy: BenchmarkRun = { ...finished, id: 'run-legacy', benchmark_title: 'Snake', tools: true }
    const gone: BenchmarkRun = { ...finished, id: 'run-gone', benchmark_title: 'Pong', profile_id: 'deleted', profile_alias: 'old-profile' }
    const off: BenchmarkRun = { ...finished, id: 'run-off', benchmark_title: 'Off', profile_id: 'p2', profile_alias: 'qwen-off' }
    mockApi([chosen, legacy, gone, off])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const retry = await screen.findByRole('button', { name: 'Retry run Tetris on qwen-24gb' })
    await waitFor(() => expect(retry).toBeEnabled())
    expect(retry).toHaveAttribute('title', 'Run again with the same settings on the profile as it is now (v2, this run used v1)')
    fireEvent.click(retry)
    await waitFor(() => expect(start).toHaveBeenLastCalledWith({ profile_id: 'p1', benchmark_id: 'tetris', max_tokens: null, tools: true, tool_names: ['write_file', 'finish_job'], system_prompt_id: 'coding-agent', system_prompt_extra: 'Reasoning strength: high', reasoning_level: 'high', verbose_log: false, compact_context: false }))

    // A run from before tools could be chosen had them all.
    fireEvent.click(screen.getByRole('button', { name: 'Retry run Snake on qwen-24gb' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ tools: true, tool_names: null, max_tokens: 4096 })))
    expect(screen.getByRole('button', { name: 'Retry run Pong on old-profile' })).toHaveAttribute('title', 'This profile has been deleted.')
    expect(screen.getByRole('button', { name: 'Retry run Pong on old-profile' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Retry run Off on qwen-off' })).toHaveAttribute('title', 'Enable the profile to run it again.')
  })

  it('waits for the router to stop before retrying', async () => {
    mockApi([finished])
    renderPanel('ready')
    const retry = await screen.findByRole('button', { name: 'Retry run Tetris on qwen-24gb' })
    expect(retry).toBeDisabled()
    expect(retry.getAttribute('title')).toMatch(/^Stop the router first/)
  })

  it('turns every file tool on or off at once, or chooses them one by one', async () => {
    mockApi([])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const all = await screen.findByRole('checkbox', { name: 'Tools' })
    expect(all).toBeChecked()
    const expand = screen.getByRole('button', { name: 'Choose tools' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('checkbox', { name: /delete_file/ })).not.toBeInTheDocument()
    fireEvent.click(expand)
    expect(expand).toHaveAttribute('aria-expanded', 'true')
    // Grouped under headers, each holding its own tools.
    expect(within(screen.getByRole('region', { name: 'Files' })).getByRole('checkbox', { name: /delete_file/ })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Source' })).getByRole('checkbox', { name: /check_file/ })).toBeInTheDocument()
    const lint = screen.getByRole('checkbox', { name: /check_file/ })
    expect(lint).toBeDisabled()
    expect(lint).not.toBeChecked()
    expect(screen.getByText(/Needs ESLint: download it in Settings, under Benchmark\./)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox', { name: /delete_file/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /edit_file/ }))
    expect(all).not.toBeChecked()
    expect((all as HTMLInputElement).indeterminate).toBe(true)
    expect(screen.getByText(/^4 of 6 tools: write_file, read_file, list_files, finish_job\./)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ tools: true, tool_names: ['write_file', 'read_file', 'list_files', 'finish_job'] })))

    // Ticking the partly chosen box turns them all on; clicking again turns them all off.
    fireEvent.click(all)
    expect(all).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /delete_file/ })).toBeChecked()
    fireEvent.click(all)
    expect(screen.getAllByRole('checkbox', { name: /_/ }).every((box) => !(box as HTMLInputElement).checked)).toBe(true)
    fireEvent.click(all)
    expect(lint).not.toBeChecked()
    fireEvent.click(all)
    expect(screen.getByText('Off: the model can only answer in text.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ tools: false, tool_names: [] })))
  })

  it('offers the TypeScript tools only on a TypeScript benchmark', async () => {
    const typed = ['check_typescript', 'bundle_typescript'].map((name) => ({ name, summary: `What ${name} does.`, available: true }))
    const tools = [...TOOLS.slice(0, 6), ...typed, TOOLS[6]]
    const neon: Benchmark = { ...TETRIS, id: 'neon', title: 'Neon', typescript: { strict: false } }
    mockApi([], { benchmarks: [TETRIS, neon], tools })
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Choose tools' }))
    const bundle = screen.getByRole('checkbox', { name: /bundle_typescript/ })
    expect(bundle).toBeDisabled()
    expect(screen.getAllByText(/Only for TypeScript benchmarks\./)).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ benchmark_id: 'tetris', tool_names: ALL_TOOLS })))

    fireEvent.change(screen.getByRole('combobox', { name: 'Benchmark' }), { target: { value: 'neon' } })
    expect(bundle).toBeEnabled()
    expect(bundle).toBeChecked()
    expect(screen.queryByText(/Only for TypeScript benchmarks\./)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ benchmark_id: 'neon', tool_names: [...ALL_TOOLS.slice(0, 5), 'check_typescript', 'bundle_typescript', 'finish_job'] })))
  })

  it('offers run_tests only on a TypeScript benchmark with tests, and shows how they went', async () => {
    const typed = ['check_typescript', 'bundle_typescript', 'run_tests'].map((name) => ({ name, summary: `What ${name} does.`, available: true }))
    const tools = [...TOOLS.slice(0, 6), ...typed, TOOLS[6]]
    const neon: Benchmark = { ...TETRIS, id: 'neon', title: 'Neon', typescript: { strict: false, tests: false } }
    const tested: Benchmark = { ...TETRIS, id: 'tested', title: 'Tested', typescript: { strict: false, tests: true } }
    const run: BenchmarkRun = { ...finished, tools: true, tool_names: ['write_file', 'run_tests', 'finish_job'], stats: { ...finished.stats, test_runs: 3, tests_passed: 12, tests_failed: 1 } }
    const never: BenchmarkRun = { ...run, id: 'run-7', benchmark_title: 'Snake', stats: finished.stats }
    mockApi([run, never], { benchmarks: [neon, tested], tools })
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    vi.spyOn(api, 'benchmarkRun').mockImplementation(async (id) => ({ ...(id === run.id ? run : never), prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] }))
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'Choose tools' }))
    const tests = screen.getByRole('checkbox', { name: /run_tests/ })
    expect(tests).toBeDisabled()
    expect(screen.getByText(/Only for TypeScript benchmarks with tests turned on./)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: 'Benchmark' }), { target: { value: 'tested' } })
    expect(tests).toBeEnabled()
    expect(tests).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ benchmark_id: 'tested', tool_names: expect.arrayContaining(['run_tests']) })))

    fireEvent.click(screen.getByRole('button', { name: /^View run Tetris/ }))
    let dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect((await within(dialog).findByText('Tests')).nextSibling).toHaveTextContent('12 passed, 1 failed (last of 3 runs)')
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0])
    fireEvent.click(screen.getByRole('button', { name: /^View run Snake/ }))
    dialog = await screen.findByRole('dialog', { name: 'Snake benchmark' })
    expect((await within(dialog).findByText('Tests')).nextSibling).toHaveTextContent('Never run')
  })

  it('warns when a TypeScript benchmark would run without its tools', async () => {
    const typed = ['check_typescript', 'bundle_typescript'].map((name) => ({ name, summary: `What ${name} does.`, available: false }))
    mockApi([], { benchmarks: [{ ...TETRIS, typescript: { strict: true } }], tools: [...TOOLS, ...typed] })
    renderPanel()

    expect(await screen.findByText(/Tetris is a TypeScript benchmark, but the TypeScript tools are not installed/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Choose tools' }))
    expect(screen.getAllByText(/Needs the TypeScript tools: download or update ESLint in Settings/)).toHaveLength(2)
  })

  it('sends the coding agent system prompt by default, or none when chosen', async () => {
    mockApi([])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const choice = await screen.findByRole('combobox', { name: 'System prompt' })
    await waitFor(() => expect(choice).toHaveValue('coding-agent'))
    expect(screen.getByText('System prompt and prompt')).toBeInTheDocument()
    expect(screen.getByLabelText('System prompt text')).toHaveTextContent('You are an AI agent')

    fireEvent.change(choice, { target: { value: '' } })
    expect(screen.queryByLabelText('System prompt text')).not.toBeInTheDocument()
    // Max tokens is left blank, so the run may use the profile's whole context.
    expect(screen.getByRole('spinbutton', { name: 'Max tokens' })).toHaveAttribute('placeholder', 'Full context')
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ system_prompt_id: null, max_tokens: null })))
  })

  it('adds text after the system prompt, trimmed, and shows it with the prompt', async () => {
    mockApi([])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const extra = await screen.findByRole('textbox', { name: 'Added to system prompt' })
    expect(screen.queryByLabelText('Added system prompt text')).not.toBeInTheDocument()
    fireEvent.change(extra, { target: { value: '  Reasoning strength: xhigh\n' } })
    expect(screen.getByLabelText('Added system prompt text')).toHaveTextContent('Reasoning strength: xhigh')
    // With no system prompt chosen, the added text is the whole system prompt.
    fireEvent.change(screen.getByRole('combobox', { name: 'System prompt' }), { target: { value: '' } })
    expect(screen.getByText('System prompt and prompt')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ system_prompt_id: null, system_prompt_extra: 'Reasoning strength: xhigh' })))
  })

  it('leaves the added text out when it is blank and shows it on runs that had it', async () => {
    mockApi([{ ...finished, system_prompt_id: 'coding-agent', system_prompt_extra: 'Reasoning strength: high' }])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    expect(await screen.findByText(/coding-agent · \+ Reasoning strength: high/)).toBeInTheDocument()
    fireEvent.change(await screen.findByRole('textbox', { name: 'Added to system prompt' }), { target: { value: '  \n ' } })
    const run = screen.getByRole('button', { name: 'Run benchmark' })
    await waitFor(() => expect(run).toBeEnabled())
    fireEvent.click(run)
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ system_prompt_extra: null })))
  })

  it('shows the size of a tool call while it is being written', async () => {
    mockApi([running])
    const detail: BenchmarkRunDetail = {
      ...running,
      tools: true,
      live: { ...running.live, elapsed: 700, tool_call: { name: 'write_file', bytes: 12700 } },
      prompt: 'make tetris',
      system_prompt: null,
      samples: [],
      output: '',
      reasoning: '',
      tool_calls: [],
      tasks: [],
      artifacts: [],
      timeline: [
        { kind: 'reading', round: 1, start: 0, end: 5, prompt_tokens: 529 },
        { kind: 'tool_call', round: 1, start: 100, end: null },
      ],
    }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    renderPanel()

    const toggle = await screen.findByRole('button', { name: /^Generate/ })
    await waitFor(() => expect(toggle).toHaveTextContent('Generate(Writing tool call (write_file, 12.4 KB)… 10m 0s)'))
    fireEvent.click(toggle)
    const steps = screen.getByRole('list', { name: 'Generation steps' })
    expect(within(steps).getAllByRole('listitem')[1]).toHaveTextContent('Writing tool call: write_file…12.4 KB10m 0s')
  })

  it('says whether a tool-using agent finished its task, and why generation ended', async () => {
    const unfinished: BenchmarkRun = { ...finished, id: 'run-3', tools: true, max_tokens: null, stats: { ...finished.stats, finish_reason: 'length', outcome: 'unfinished' } }
    const done: BenchmarkRun = { ...finished, id: 'run-4', benchmark_title: 'Snake', tools: true, stats: { ...finished.stats, finish_reason: 'tool_round_limit', round_limit: 80, outcome: 'completed' } }
    // A tool run from before outcomes were recorded.
    const older: BenchmarkRun = { ...finished, id: 'run-5', tools: true }
    mockApi([unfinished, done, finished, older])
    const detail: BenchmarkRunDetail = { ...unfinished, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [{ kind: 'tool_call', round: 1, start: 1, end: 9, tokens: 900, names: ['write_file'], bytes: 1300 }] }
    vi.spyOn(api, 'benchmarkRun').mockImplementation(async (id) => id === done.id ? { ...detail, ...done } : detail)
    renderPanel()

    const rows = await screen.findAllByRole('button', { name: /^View run/ })
    expect(rows).toHaveLength(4)
    expect(within(rows[0].closest('li')!).getByText('Unfinished')).toBeInTheDocument()
    expect(within(rows[1].closest('li')!).getByText('Job finished')).toBeInTheDocument()
    // Neither a run without tools nor one from before outcomes were kept claims either.
    expect(within(rows[2].closest('li')!).queryByText(/Unfinished|Job finished/)).not.toBeInTheDocument()
    expect(within(rows[3].closest('li')!).queryByText(/Unfinished|Job finished/)).not.toBeInTheDocument()

    fireEvent.click(rows[0])
    let dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    // With no token limit, running out means the context was full.
    expect(await within(dialog).findByText('Ran out of context')).toBeInTheDocument()
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0])
    fireEvent.click(rows[1])
    const limited = await screen.findByRole('dialog', { name: 'Snake benchmark' })
    expect(await within(limited).findByText('Hit the tool round limit (80)')).toBeInTheDocument()
    fireEvent.click(within(limited).getAllByRole('button', { name: 'Close' })[0])
    fireEvent.click(rows[0])
    dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect(within(dialog).getByText('Unfinished')).toBeInTheDocument()
    expect(within(dialog).getByText('Tokens / limit').nextSibling).toHaveTextContent('3,001 / context')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Generate' }))
    expect(within(dialog).getByRole('list', { name: 'Generation steps' })).toHaveTextContent('1.3 KB · 900 tokens')
  })

  it('asks for a reasoning level, or leaves it to the model', async () => {
    mockApi([{ ...finished, reasoning_level: 'low' }])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const reasoning = await screen.findByRole('combobox', { name: 'Reasoning' })
    expect(reasoning).toHaveValue('')
    await waitFor(() => expect(within(reasoning).getAllByRole('option').map((option) => option.textContent)).toEqual(['Model default', 'Off', 'Low', 'Medium', 'High', 'Extra high']))
    expect(await screen.findByText(/· reasoning low/)).toBeInTheDocument()

    fireEvent.change(reasoning, { target: { value: 'low' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ reasoning_level: 'low' })))
  })

  it('waits for the router to stop, and needs permission to run', async () => {
    mockApi([])
    const { unmount } = renderPanel('ready')
    expect(await screen.findByText(/Stop the router first/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run benchmark' })).toBeDisabled()
    expect(await screen.findByText('No benchmark runs yet')).toBeInTheDocument()
    unmount()

    renderPanel('stopped', false)
    expect(await screen.findByText(/needs permission to start and stop the server/)).toBeInTheDocument()
  })

  it('follows a running benchmark step by step and can cancel it', async () => {
    mockApi([running, finished])
    const cancel = vi.spyOn(api, 'cancelBenchmark').mockResolvedValue({ ...running, state: 'cancelled' })
    renderPanel()

    expect(await screen.findByText('Tetris on qwen-24gb')).toBeInTheDocument()
    expect(screen.getByText(/A benchmark is already running/)).toBeInTheDocument()
    const steps = screen.getAllByRole('list')[0]
    expect(within(steps).getByText('Load model').closest('li')).toHaveAttribute('data-state', 'done')
    expect(within(steps).getByText('40.1 s')).toBeInTheDocument()
    expect(within(steps).getByText('Generate').closest('li')).toHaveAttribute('data-state', 'active')
    expect(within(steps).getByText('19.5 tok/s · 120 tokens')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('run-2'))
  })

  it('lists the files a run wrote and opens one in a read-only editor', async () => {
    const withTools: BenchmarkRun = { ...finished, tools: true, tool_names: ['write_file', 'read_file'], typescript_strict: true, stats: { ...finished.stats, rounds: 3, tool_calls: 2, files_written: 1 } }
    mockApi([withTools])
    const detail: BenchmarkRunDetail = {
      ...withTools,
      prompt: 'make tetris',
      system_prompt: null,
      samples: [],
      output: 'Saved it.',
      reasoning: '',
      tool_calls: [
        { round: 1, name: 'write_file', path: 'tetris/index.html', ok: true, result: 'Wrote 25 bytes to tetris/index.html' },
        { round: 1, name: 'write_file', path: 'C:/test.py', ok: false, result: "Error: 'C:/test.py' is outside your workspace." },
        { round: 2, name: 'finish_job', path: null, ok: false, result: 'Error: final_message is required.' },
        { round: 3, name: 'finish_job', path: null, ok: true, result: 'Job marked finished. Built Tetris in tetris/index.html.\nI had every tool I needed.' },
      ],
      tasks: [
        { id: 1, title: 'Draw the board', status: 'done', source: 'prompt' },
        { id: 2, title: 'Add the pieces', status: 'open', source: 'prompt' },
        { id: 3, title: 'Add a bassline', status: 'deleted', source: 'agent' },
      ],
      artifacts: [{ path: 'tetris/index.html', size: 25 }, { path: 'tetris/game.js', size: 30 }],
      timeline: [],
    }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    const page = '<script>alert(1)</script>\n<script src="game.js"></script><script src="https://cdn.example/x.js"></script>\n<p>hi</p>'
    const read = vi.spyOn(api, 'benchmarkArtifact').mockImplementation(async (_run, path) => ({ path, content: path.endsWith('game.js') ? 'startGame("</script>")' : page }))
    renderPanel()

    expect(await screen.findByText(/· 1 file/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'View run Tetris on qwen-24gb' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect(await within(dialog).findByText('2 in 3 requests')).toBeInTheDocument()
    expect(within(dialog).getByText('write_file, read_file')).toBeInTheDocument()
    expect(within(dialog).getByText('Strict checks')).toBeInTheDocument()
    expect(within(dialog).getByText('Tool calls (4, 2 refused)')).toBeInTheDocument()
    // The accepted final message sits under the artifacts, without the tool's own preamble.
    const completion = within(dialog).getByRole('heading', { name: 'Completion message' }).closest('section')!
    expect(completion.querySelector('pre')).toHaveTextContent(/^Built Tetris in tetris\/index\.html\. I had every tool I needed\.$/)
    expect(completion.previousElementSibling).toHaveTextContent(/^Artifacts/)
    // The task list shows what was ticked off, and keeps what the agent deleted.
    const tasks = within(dialog).getByRole('heading', { name: /^Tasks/ }).closest('section')!
    expect(within(tasks).getByText('1 of 2 done · 1 deleted')).toBeInTheDocument()
    expect([...tasks.querySelectorAll('li')].map((item) => [item.dataset.status, item.textContent])).toEqual([
      ['done', 'Done: Draw the board'],
      ['open', 'Open: Add the pieces'],
      ['deleted', 'Deleted: Add a basslineAdded by the agent'],
    ])
    expect(tasks.nextElementSibling).toBe(completion.previousElementSibling)
    expect(within(dialog).getByText('C:/test.py').closest('li')).toHaveAttribute('data-ok', 'false')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Open tetris/index.html' }))
    const viewer = await screen.findByRole('dialog', { name: 'tetris/index.html' })
    const editor = await within(viewer).findByRole('textbox', { name: 'Contents of tetris/index.html' })
    // Opens on the source, as text in a read-only editor.
    expect(editor).toHaveValue(page)
    expect(editor).toHaveAttribute('readonly')
    expect(within(viewer).getByText('3 lines · read-only')).toBeInTheDocument()
    expect(read).toHaveBeenCalledWith('run-1', 'tetris/index.html')

    // The preview runs the page in a sandbox with an opaque origin, cut off from this app.
    fireEvent.click(within(viewer).getByRole('tab', { name: 'Preview' }))
    // The run's own game.js is embedded, since the preview has no folder to load it from.
    const preview = await within(viewer).findByTitle('Preview of tetris/index.html')
    expect(preview).toHaveAttribute('sandbox', 'allow-scripts')
    const srcdoc = preview.getAttribute('srcdoc')
    expect(srcdoc).toContain('<script>alert(1)</script>')
    expect(srcdoc).toContain(`<script src="data:text/javascript;charset=utf-8,${encodeURIComponent('startGame("</script>")')}"></script>`)
    // Storage throws without an origin, so the page gets an in-memory stand-in first.
    expect(srcdoc).toMatch(/^<html><head><script>for \(const name of \['localStorage'/)
    expect(srcdoc).toContain('<script src="https://cdn.example/x.js"></script>')
    expect(srcdoc).toContain('<p>hi</p>')
    expect(read).toHaveBeenCalledWith('run-1', 'tetris/game.js')
    // Previewing gives the page the whole window, with full screen a click away.
    expect(viewer).toHaveClass('fill')
    expect(within(viewer).getByRole('button', { name: 'Full screen' })).toBeEnabled()
    expect(within(viewer).queryByRole('textbox')).not.toBeInTheDocument()
    fireEvent.click(within(viewer).getByRole('tab', { name: 'Source' }))
    expect(within(viewer).getByRole('textbox', { name: 'Contents of tetris/index.html' })).toBeInTheDocument()
    expect(viewer).not.toHaveClass('fill')

    // Escape closes only the editor on top, not the run underneath.
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'tetris/index.html' })).not.toBeInTheDocument())
    expect(screen.getByRole('dialog', { name: 'Tetris benchmark' })).toBeInTheDocument()
  })

  it('expands the generate step into thinking, answers and timed tool calls', async () => {
    const withTools: BenchmarkRun = { ...finished, tools: true }
    mockApi([withTools])
    const detail: BenchmarkRunDetail = {
      ...withTools,
      prompt: 'make tetris',
      system_prompt: null,
      samples: [],
      output: 'Saved tetris/index.html.',
      reasoning: 'Plan the board, then the pieces.',
      tool_calls: [
        { round: 1, name: 'write_file', path: 'tetris/index.html', ok: true, result: 'Wrote 25 bytes to tetris/index.html' },
        { round: 1, name: 'write_file', path: 'C:/test.py', ok: false, result: "Error: 'C:/test.py' is outside your workspace." },
      ],
      tasks: [],
      artifacts: [{ path: 'tetris/index.html', size: 25 }],
      timeline: [
        { kind: 'reading', round: 1, start: 0, end: 4.2, prompt_tokens: 529 },
        { kind: 'thinking', round: 1, start: 4.2, end: 130.5, tokens: 2400, from: 0, to: 32 },
        { kind: 'tool_call', round: 1, start: 130.5, end: 190, tokens: 900, names: ['write_file', 'write_file'] },
        { kind: 'tool', round: 1, start: 190, end: 190.004, name: 'write_file', call: 0, path: 'tetris/index.html', ok: true },
        { kind: 'tool', round: 1, start: 190.004, end: 190.006, name: 'write_file', call: 1, path: 'C:/test.py', ok: false },
        { kind: 'reading', round: 2, start: 190.006, end: 191.5, prompt_tokens: 120 },
        { kind: 'answer', round: 2, start: 191.5, end: 193, tokens: 12, from: 0, to: 24 },
      ],
    }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    renderPanel()

    fireEvent.click(await screen.findByRole('button', { name: 'View run Tetris on qwen-24gb' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    const toggle = await within(dialog).findByRole('button', { name: 'Generate' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)

    const steps = within(dialog).getByRole('list', { name: 'Generation steps' })
    // Each round gets a heading row where it starts.
    expect(within(steps).getAllByRole('listitem').map((item) => item.dataset.kind ?? item.textContent)).toEqual(['Round 1', 'reading', 'thinking', 'tool_call', 'tool', 'tool', 'Round 2', 'reading', 'answer'])
    const items = within(steps).getAllByRole('listitem').filter((item) => item.dataset.kind)
    expect(items[0]).toHaveTextContent('Reading prompt529 tokens4.2 s')
    expect(items[1]).toHaveTextContent('Thinking32 characters · 2,400 tokens2m 6s')
    expect(within(items[1]).getByText('Plan the board, then the pieces.')).toBeInTheDocument()
    expect(items[2]).toHaveTextContent('Writing tool calls: write_file, write_file900 tokens59.5 s')
    expect(items[3]).toHaveTextContent('4 ms')
    expect(items[4]).toHaveAttribute('data-ok', 'false')
    expect(items[4]).toHaveTextContent('refused')
    expect(within(items[4]).getByText(/outside your workspace/)).toBeInTheDocument()
    expect(items[5]).toHaveTextContent('Reading tool results120 tokens1.5 s')
    expect(within(items[6]).getByText('Saved tetris/index.html.')).toBeInTheDocument()
  })

  it('shows the average so far, not the current speed, as a running run\'s average', async () => {
    mockApi([running])
    renderPanel()

    const row = (await screen.findByText(/Tetris · qwen-24gb/)).closest('li')!
    const figures = (label: string) => [...within(row).getByText(label).closest('div')!.querySelectorAll('dd > span')].map((item) => item.textContent)
    expect(figures('Prefill')).toEqual(['1% low—', 'Average380', '1% high—'])
    expect(figures('Decode')).toEqual(['1% low—', 'Average24.1', '1% high—'])
  })

  it('shows the decode peak of an older run as its 1% high, but not its prefill peak, and counts cached prompt tokens', async () => {
    const { prompt_high_tokens_per_second: _prefill, decode_high_tokens_per_second: _decode, ...speeds } = finished.stats
    const older: BenchmarkRun = { ...finished, stats: { ...speeds, prompt_peak_tokens_per_second: 1641538, decode_peak_tokens_per_second: 33.8, prompt_cached_tokens: 9000 } }
    mockApi([older])
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue({ ...older, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [{ kind: 'reading', round: 1, start: 0, end: 1, prompt_tokens: 30, cached_tokens: 9000 }] })
    renderPanel()

    const row = (await screen.findByText(/Tetris · qwen-24gb/)).closest('li')!
    const figures = (label: string) => [...within(row).getByText(label).closest('div')!.querySelectorAll('dd > span')].map((item) => item.textContent)
    expect(figures('Prefill')).toEqual(['1% low380', 'Average413', '1% high—'])
    expect(figures('Decode')).toEqual(['1% low12.4', 'Average21.4', '1% high33.8'])
    fireEvent.click(within(row).getByRole('button', { name: 'View run Tetris on qwen-24gb' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect((await within(dialog).findByText('Prompt', { selector: 'dt' })).nextSibling).toHaveTextContent('30 tokens + 9,000 cached')
    expect(within(dialog).getByText('Cache hit rate').nextSibling).toHaveTextContent('99% of 9,030 tokens')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Generate' }))
    expect(within(dialog).getByRole('list', { name: 'Generation steps' })).toHaveTextContent('30 tokens · 9,000 cached')
  })

  it('compacts the context by default, and shows each compaction', async () => {
    const compacted: BenchmarkRun = { ...finished, tools: true, tool_names: ALL_TOOLS, max_tokens: null, compact_context: true, stats: { ...finished.stats, compactions: 2, compactions_pruned: 1, compactions_summarised: 1 } }
    const off: BenchmarkRun = { ...finished, id: 'run-6', tools: true, tool_names: ALL_TOOLS, compact_context: false }
    mockApi([compacted, off])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue({
      ...compacted, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [],
      timeline: [
        { kind: 'compacting', round: 5, start: 100, end: 100.1, tokens_before: 14000, tokens_after: 6500, mode: 'pruned' },
        { kind: 'compacting', round: 9, start: 200, end: 260, tokens_before: 15000, tokens_after: 2100, mode: 'summarised', tokens: 812, summary: 'Board done; scoring left.' },
      ],
    })
    renderPanel()

    const rows = await screen.findAllByRole('button', { name: /^View run/ })
    expect(rows[0].closest('li')).toHaveTextContent('2 compactions')
    expect(rows[1].closest('li')).not.toHaveTextContent('compaction')

    fireEvent.click(screen.getByRole('checkbox', { name: 'Compact context' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compact context' }))
    fireEvent.click(screen.getByRole('button', { name: /Run benchmark/ }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(expect.objectContaining({ compact_context: true })))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Compact context' }))
    fireEvent.click(screen.getByRole('button', { name: /Run benchmark/ }))
    await waitFor(() => expect(start).toHaveBeenLastCalledWith(expect.objectContaining({ compact_context: false })))

    fireEvent.click(rows[0])
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect((await within(dialog).findByText('Compactions')).nextSibling).toHaveTextContent('2 (1 pruned, 1 summarised)')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Generate' }))
    const steps = within(dialog).getByRole('list', { name: 'Generation steps' })
    expect(steps).toHaveTextContent('Compacted context: pruned old tool output14,000 → 6,500 tokens')
    expect(steps).toHaveTextContent('Compacted context: summarised15,000 → 2,100 tokens · summary, 812 tokens1m 0s')
    fireEvent.click(within(steps).getByText('Compacted context: summarised'))
    expect(within(steps).getByText('Board done; scoring left.')).toBeVisible()
  })

  it('shows a summary being written while the context is compacted', async () => {
    mockApi([running])
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue({
      ...running, tools: true, compact_context: true, live: { ...running.live, elapsed: 130, compaction: { tokens: 412 } },
      prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [],
      timeline: [{ kind: 'compacting', round: 4, start: 100, end: null, tokens_before: 15000 }],
    })
    renderPanel()

    const toggle = await screen.findByRole('button', { name: /^Generate/ })
    await waitFor(() => expect(toggle).toHaveTextContent('Generate(Compacting context (summary, 412 tokens)… 30.0 s)'))
  })

  it('counts the total time of a running benchmark up until it finishes', async () => {
    mockApi([{ ...running, live: { ...running.live, total: 75.4 } }])
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], tasks: [], artifacts: [], timeline: [] })
    renderPanel()

    const row = (await screen.findByRole('button', { name: /^View run Tetris/ })).closest('li')!
    expect(within(row).getByText('Total').nextSibling).toHaveTextContent('75.4 s')
  })

  it('says what a running benchmark is doing next to Generate on the page', async () => {
    mockApi([running])
    const detail: BenchmarkRunDetail = {
      ...running,
      live: { ...running.live, elapsed: 195.4 },
      prompt: 'make tetris',
      system_prompt: null,
      samples: [],
      output: '',
      reasoning: 'Plan the board.',
      tool_calls: [],
      tasks: [],
      artifacts: [],
      timeline: [
        { kind: 'reading', round: 1, start: 0, end: 5.2, prompt_tokens: 529 },
        { kind: 'thinking', round: 1, start: 5.2, end: null, from: 0 },
      ],
    }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    renderPanel()

    const toggle = await screen.findByRole('button', { name: /^Generate/ })
    await waitFor(() => expect(toggle).toHaveTextContent('Generate(Thinking… 3m 10s)'))
    fireEvent.click(toggle)
    // Expanded, the activity moves into the list, where the open step counts up live.
    expect(toggle).toHaveTextContent(/^Generate$/)
    const steps = screen.getByRole('list', { name: 'Generation steps' })
    expect(within(steps).getAllByRole('listitem')[1]).toHaveTextContent('3m 10s')
  })

  it('shows a finished run with its output, and deletes it after confirming', async () => {
    mockApi([finished])
    const detail: BenchmarkRunDetail = { ...finished, prompt: 'make tetris', system_prompt: null, samples: [{ t: 3, phase: 'decode', tokens: 10, rate: 20 }, { t: 4, phase: 'decode', tokens: 30, rate: 21 }], output: '<html>tetris</html>', reasoning: 'Plan the grid', tool_calls: [], tasks: [], artifacts: [], timeline: [] }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    const remove = vi.spyOn(api, 'deleteBenchmarkRun').mockResolvedValue(undefined)
    renderPanel()

    const row = (await screen.findByText(/Tetris · qwen-24gb/)).closest('li')!
    expect(within(row).getByText('190 s')).toBeInTheDocument()
    expect(within(row).getByText('41.3 s')).toBeInTheDocument()
    // Each phase stacks its 1% low, average and 1% high under one tok/s heading.
    const figures = (label: string) => [...within(row).getByText(label).closest('div')!.querySelectorAll('dd > span')].map((item) => item.textContent)
    expect(figures('Prefill')).toEqual(['1% low380', 'Average413', '1% high451'])
    expect(figures('Decode')).toEqual(['1% low12.4', 'Average21.4', '1% high33.8'])
    expect(within(row).getByText('3,001')).toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: 'View run Tetris on qwen-24gb' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect(await within(dialog).findByText('<html>tetris</html>')).toBeInTheDocument()
    expect(within(dialog).getByText('Reasoning (13 characters)')).toBeInTheDocument()
    expect(within(dialog).getByText('Finished: stop')).toBeInTheDocument()
    const speeds = within(dialog).getByRole('table', { name: 'Speed, tokens per second' })
    expect(within(speeds).getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['1% low', 'Average', '1% high'])
    expect(within(speeds).getByRole('row', { name: /Prefill/ })).toHaveTextContent('Prefill380413451')
    expect(within(speeds).getByRole('row', { name: /Decode/ })).toHaveTextContent('Decode12.421.433.8')
    expect(within(dialog).getByText('Decode at end (3 s)').nextSibling).toHaveTextContent('18.2 tok/s')
    // A run that never reported the cache has no hit rate to show.
    expect(within(dialog).queryByText('Cache hit rate')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' }).at(-1)!)

    fireEvent.click(within(row).getByRole('button', { name: 'Delete run Tetris on qwen-24gb' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete run' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('run-1'))
  })

  it('selects finished runs and deletes them together', async () => {
    const older = { ...finished, id: 'run-3', started_at: '2026-10-22T09:00:00' }
    mockApi([running, finished, older])
    const remove = vi.spyOn(api, 'deleteBenchmarkRuns').mockResolvedValue({ deleted: ['run-1', 'run-3'], skipped: [] })
    renderPanel()

    const deleteButton = await screen.findByRole('button', { name: 'Delete' })
    expect(deleteButton).toBeDisabled()
    // A running run cannot be chosen.
    expect(screen.getAllByRole('checkbox', { name: /^Select run/ })[0]).toBeDisabled()
    fireEvent.click(screen.getAllByRole('checkbox', { name: /^Select run/ })[1])
    expect(screen.getByRole('checkbox', { name: 'Select every finished run' })).toHaveProperty('indeterminate', true)
    fireEvent.click(screen.getByRole('button', { name: 'Delete 1' }))
    expect(await screen.findByRole('dialog', { name: 'Delete 1 benchmark run?' })).toHaveTextContent('1 of 2 finished runs will be deleted.')
    fireEvent.click(screen.getByRole('button', { name: 'Keep runs' }))

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every finished run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete 2' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete 2 benchmark runs?' })
    expect(dialog).toHaveTextContent('Every finished run will be deleted.')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete runs' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(['run-1', 'run-3']))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every finished run' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select every finished run' }))
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
  })

  it('offers no selection without permission to run benchmarks', async () => {
    mockApi([finished])
    renderPanel('stopped', false)
    expect(await screen.findByText(/Tetris · qwen-24gb/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /^Select/ })).not.toBeInTheDocument()
  })
})
