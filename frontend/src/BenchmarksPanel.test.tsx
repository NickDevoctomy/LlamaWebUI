import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type BenchmarkRun, type BenchmarkRunDetail, type Profile, type RouterState } from './api'
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
  stats: { server_start_seconds: 2.1, load_seconds: 41.3, prompt_tokens: 30, prompt_tokens_per_second: 412.5, generated_tokens: 3001, decode_tokens_per_second: 21.37, decode_peak_tokens_per_second: 33.8, decode_final_tokens_per_second: 18.2, total_seconds: 190.2, finish_reason: 'stop' },
  started_at: '2026-10-23T11:00:00',
  finished_at: '2026-10-23T11:03:10',
}

const running: BenchmarkRun = {
  ...finished,
  id: 'run-2',
  state: 'running',
  stage: 'decoding',
  stats: { server_start_seconds: 2.0, load_seconds: 40.1, first_token_seconds: 3.2 },
  live: { prefill: { tokens: 30, rate: 400 }, decode: { tokens: 120, rate: 19.5 } },
  finished_at: null,
}

function mockApi(runs: BenchmarkRun[]) {
  vi.spyOn(api, 'benchmarks').mockResolvedValue({ benchmarks: [{ id: 'tetris', title: 'Tetris', prompt: 'make tetris' }], system_prompts: [{ id: 'coding-agent', title: 'Coding agent', text: 'You are an AI agent running in a coding agent harness.' }], default_system_prompt_id: 'coding-agent', reasoning_levels: ['off', 'low', 'medium', 'high', 'xhigh'], default_max_tokens: null, active_run_id: null })
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
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], artifacts: [], timeline: [] })
    renderPanel()

    const run = await screen.findByRole('button', { name: 'Run benchmark' })
    await waitFor(() => expect(run).toBeEnabled())
    expect(screen.getByRole('option', { name: 'qwen-off (disabled)' })).toBeDisabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Max tokens' }), { target: { value: '2048' } })
    fireEvent.click(run)

    await waitFor(() => expect(start).toHaveBeenCalledWith({ profile_id: 'p1', benchmark_id: 'tetris', max_tokens: 2048, tools: true, system_prompt_id: 'coding-agent', reasoning_level: null }))
  })

  it('sends the coding agent system prompt by default, or none when chosen', async () => {
    mockApi([])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], artifacts: [], timeline: [] })
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
    const done: BenchmarkRun = { ...finished, id: 'run-4', benchmark_title: 'Snake', tools: true, stats: { ...finished.stats, finish_reason: 'task_complete', outcome: 'completed' } }
    // A tool run from before outcomes were recorded.
    const older: BenchmarkRun = { ...finished, id: 'run-5', tools: true }
    mockApi([unfinished, done, finished, older])
    const detail: BenchmarkRunDetail = { ...unfinished, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], artifacts: [], timeline: [{ kind: 'tool_call', round: 1, start: 1, end: 9, tokens: 900, names: ['write_file'], bytes: 1300 }] }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    renderPanel()

    const rows = await screen.findAllByRole('button', { name: /^View run/ })
    expect(rows).toHaveLength(4)
    expect(within(rows[0].closest('li')!).getByText('Unfinished')).toBeInTheDocument()
    expect(within(rows[1].closest('li')!).getByText('Task complete')).toBeInTheDocument()
    // Neither a run without tools nor one from before outcomes were kept claims either.
    expect(within(rows[2].closest('li')!).queryByText(/Unfinished|Task complete/)).not.toBeInTheDocument()
    expect(within(rows[3].closest('li')!).queryByText(/Unfinished|Task complete/)).not.toBeInTheDocument()

    fireEvent.click(rows[0])
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    // With no token limit, running out means the context was full.
    expect(await within(dialog).findByText('Ran out of context')).toBeInTheDocument()
    expect(within(dialog).getByText('Unfinished')).toBeInTheDocument()
    expect(within(dialog).getByText('Tokens / limit').nextSibling).toHaveTextContent('3,001 / context')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Generate' }))
    expect(within(dialog).getByRole('list', { name: 'Generation steps' })).toHaveTextContent('1.3 KB · 900 tokens')
  })

  it('asks for a reasoning level, or leaves it to the model', async () => {
    mockApi([{ ...finished, reasoning_level: 'low' }])
    const start = vi.spyOn(api, 'startBenchmark').mockResolvedValue({ ...running, prompt: 'make tetris', system_prompt: null, samples: [], output: '', reasoning: '', tool_calls: [], artifacts: [], timeline: [] })
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
    const withTools: BenchmarkRun = { ...finished, tools: true, stats: { ...finished.stats, rounds: 3, tool_calls: 2, files_written: 1 } }
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
    expect(within(dialog).getByText('Tool calls (2, 1 refused)')).toBeInTheDocument()
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
    const detail: BenchmarkRunDetail = { ...finished, prompt: 'make tetris', system_prompt: null, samples: [{ t: 3, phase: 'decode', tokens: 10, rate: 20 }, { t: 4, phase: 'decode', tokens: 30, rate: 21 }], output: '<html>tetris</html>', reasoning: 'Plan the grid', tool_calls: [], artifacts: [], timeline: [] }
    vi.spyOn(api, 'benchmarkRun').mockResolvedValue(detail)
    const remove = vi.spyOn(api, 'deleteBenchmarkRun').mockResolvedValue(undefined)
    renderPanel()

    const row = (await screen.findByText(/Tetris · qwen-24gb/)).closest('li')!
    expect(within(row).getByText('190 s')).toBeInTheDocument()
    expect(within(row).getByText('41.3 s')).toBeInTheDocument()
    expect(within(row).getByText('413 tok/s')).toBeInTheDocument()
    expect(within(row).getByText('21.4 tok/s')).toBeInTheDocument()
    expect(within(row).getByText('33.8 tok/s')).toBeInTheDocument()
    expect(within(row).getByText('3,001')).toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: 'View run Tetris on qwen-24gb' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tetris benchmark' })
    expect(await within(dialog).findByText('<html>tetris</html>')).toBeInTheDocument()
    expect(within(dialog).getByText('Reasoning (13 characters)')).toBeInTheDocument()
    expect(within(dialog).getByText('Finished: stop')).toBeInTheDocument()
    expect(within(dialog).getByText('Decode peak (3 s)').nextSibling).toHaveTextContent('33.8 tok/s')
    expect(within(dialog).getByText('Decode at end (3 s)').nextSibling).toHaveTextContent('18.2 tok/s')
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' }).at(-1)!)

    fireEvent.click(within(row).getByRole('button', { name: 'Delete run Tetris on qwen-24gb' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete run' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('run-1'))
  })
})
