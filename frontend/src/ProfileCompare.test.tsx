import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type ArgumentCatalog, type Profile, type Runtime } from './api'
import { ProfileCompare } from './ProfileCompare'

const catalog = {
  catalogId: 'test',
  catalogVersion: '1',
  source: { url: '', retrieved: '' },
  categories: [{ id: 'context', label: 'Context and KV cache', sortOrder: 1 }, { id: 'cpu', label: 'Process and CPU', sortOrder: 2 }],
  effectTags: {},
  arguments: [
    { id: 'ctx-size', flag: '--ctx-size', label: 'Context size', category: 'context', value: { type: 'integer' } },
    { id: 'threads', flag: '--threads', label: 'CPU threads', category: 'cpu', value: { type: 'integer' } },
    { id: 'flash-attn', flag: '--flash-attn', label: 'Flash attention', category: 'context', value: { type: 'enum' } },
  ],
} as unknown as ArgumentCatalog

const runtimes = [{ id: 'runtime-1', name: 'latest cuda' }] as Runtime[]

function profile(id: string, overrides: Partial<Profile>): Profile {
  return {
    id,
    alias: id,
    runtime_id: 'runtime-1',
    model_path: 'E:\\models\\qwen-00001-of-00003.gguf',
    configuration: { arguments: [] },
    enabled: true,
    exclusive: true,
    version: 1,
    validation_state: 'available',
    ...overrides,
  } as Profile
}

const profiles = [
  profile('qwen-mtp', { configuration: { arguments: [{ id: 'ctx-size', value: 196608 }, { id: 'threads', value: 24 }, { id: 'flash-attn', value: 'on' }, { flag: '--spec-type', value: 'draft-mtp', origin: 'custom' }] }, version: 3 }),
  profile('qwen-nomtp', { configuration: { arguments: [{ id: 'ctx-size', value: 131072 }, { id: 'threads', value: 24 }, { id: 'flash-attn', value: 'on' }] } }),
  profile('qwen-copy', { configuration: { arguments: [{ id: 'ctx-size', value: 196608 }, { id: 'threads', value: 24 }, { id: 'flash-attn', value: 'on' }, { flag: '--spec-type', value: 'draft-mtp', origin: 'custom' }] }, version: 3 }),
]

function renderCompare(items = profiles, running = false) {
  vi.spyOn(api, 'argumentCatalog').mockResolvedValue(catalog)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ProfileCompare profiles={items} running={running} runtimes={runtimes} /></QueryClientProvider>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ProfileCompare', () => {
  it('shows only the settings that differ, labelled from the catalog', async () => {
    renderCompare()

    const table = await screen.findByRole('table', { name: 'qwen-mtp compared with qwen-nomtp' })
    const context = await within(table).findByRole('row', { name: /Context size/ })
    expect(context).toHaveAttribute('data-state', 'changed')
    expect(context).toHaveTextContent('196,608')
    expect(context).toHaveTextContent('131,072')
    expect(within(table).getByRole('row', { name: /--spec-type/ })).toHaveAttribute('data-state', 'left-only')
    expect(within(table).getByRole('row', { name: /Version/ })).toHaveTextContent('v3v1')
    expect(within(table).queryByRole('row', { name: /CPU threads/ })).not.toBeInTheDocument()
    expect(within(table).queryByRole('row', { name: /Model file/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('3 differences across')
  })

  it('lists every setting when asked, and swaps sides', async () => {
    renderCompare()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Only differences' }))

    expect(await screen.findByRole('row', { name: /CPU threads/ })).toHaveAttribute('data-state', 'same')
    fireEvent.click(screen.getByRole('button', { name: 'Swap sides' }))
    expect(screen.getByRole('table', { name: 'qwen-nomtp compared with qwen-mtp' })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /--spec-type/ })).toHaveAttribute('data-state', 'right-only')
  })

  it('reports identical profiles and the same profile on both sides', async () => {
    renderCompare()
    fireEvent.change(screen.getByRole('combobox', { name: 'Right profile' }), { target: { value: 'qwen-copy' } })
    expect(screen.getByRole('status')).toHaveTextContent('These profiles have identical settings.')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: 'Right profile' }), { target: { value: 'qwen-mtp' } })
    expect(screen.getByRole('status')).toHaveTextContent('The same profile is selected on both sides.')
  })

  it('compares files by full path but shows their names', async () => {
    renderCompare([
      profile('here', { model_path: 'E:\\models\\a\\qwen.gguf' }),
      profile('there', { model_path: 'D:\\backup\\qwen.gguf' }),
    ])

    const row = await screen.findByRole('row', { name: /Model file/ })
    expect(row).toHaveAttribute('data-state', 'changed')
    expect(within(row).getAllByText('qwen.gguf').map((cell) => cell.getAttribute('title'))).toEqual(['E:\\models\\a\\qwen.gguf', 'D:\\backup\\qwen.gguf'])
  })

  it('lines up the benchmark results stored on each profile', async () => {
    const result = { run_id: 'r', benchmark_title: 'Tetris', profile_version: 1, completed_at: '2026-10-23T11:00:00', decode_tokens_per_second: 21.37, prompt_tokens_per_second: 412.5, load_seconds: 41.3 }
    renderCompare([
      profile('fast', { benchmark_results: { tetris: result } }),
      profile('slow', { benchmark_results: { tetris: { ...result, decode_tokens_per_second: 15.02 } } }),
    ])

    const row = await screen.findByRole('row', { name: /^Decode average/ })
    expect(row).toHaveTextContent('21.4 tok/s15.0 tok/s')
    expect(screen.getByText('Benchmark · Tetris')).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /Model load/ })).not.toBeInTheDocument()
  })

  it('takes a differing value from the other side and saves that profile', async () => {
    const update = vi.spyOn(api, 'updateProfile').mockImplementation(async (id) => profiles.find((item) => item.id === id)!)
    renderCompare()

    const context = await screen.findByRole('row', { name: /Context size/ })
    const takeRight = within(context).getByRole('button', { name: 'Take Context size from qwen-nomtp' })
    expect(takeRight).toHaveAttribute('title', 'Take value from qwen-nomtp')
    expect(within(context).getByRole('button', { name: 'Take Context size from qwen-mtp' })).toHaveAttribute('title', 'Take value from qwen-mtp')
    fireEvent.click(takeRight)
    // The changed argument is replaced where it stands; everything else is kept.
    await vi.waitFor(() => expect(update).toHaveBeenLastCalledWith('qwen-mtp', expect.objectContaining({
      alias: 'qwen-mtp',
      runtime_id: 'runtime-1',
      enabled: true,
      exclusive: true,
      arguments: [{ id: 'ctx-size', value: 131072 }, { id: 'threads', value: 24 }, { id: 'flash-attn', value: 'on' }, { flag: '--spec-type', value: 'draft-mtp', origin: 'custom' }],
    })))

    const spec = screen.getByRole('row', { name: /--spec-type/ })
    // Taking "not set" removes the argument; taking a value the other side lacks adds it.
    fireEvent.click(within(spec).getByRole('button', { name: 'Take --spec-type from qwen-nomtp' }))
    await vi.waitFor(() => expect(update).toHaveBeenLastCalledWith('qwen-mtp', expect.objectContaining({
      arguments: [{ id: 'ctx-size', value: 196608 }, { id: 'threads', value: 24 }, { id: 'flash-attn', value: 'on' }],
    })))
    fireEvent.click(within(spec).getByRole('button', { name: 'Take --spec-type from qwen-mtp' }))
    await vi.waitFor(() => expect(update).toHaveBeenLastCalledWith('qwen-nomtp', expect.objectContaining({
      arguments: [{ id: 'ctx-size', value: 131072 }, { id: 'threads', value: 24 }, { id: 'flash-attn', value: 'on' }, { flag: '--spec-type', value: 'draft-mtp', origin: 'custom' }],
    })))
  })

  it('takes profile fields too, but never versions or benchmark figures', async () => {
    const update = vi.spyOn(api, 'updateProfile').mockImplementation(async (id) => profiles.find((item) => item.id === id)!)
    const result = { run_id: 'r', benchmark_title: 'Tetris', profile_version: 1, completed_at: '2026-10-23T11:00:00', decode_tokens_per_second: 21.37 }
    renderCompare([
      profile('a', { description: 'Fast', exclusive: false, version: 2, benchmark_results: { tetris: result } }),
      profile('b', { description: null, exclusive: true, version: 5, benchmark_results: { tetris: { ...result, decode_tokens_per_second: 15 } } }),
    ])

    const exclusive = await screen.findByRole('row', { name: /Exclusive/ })
    fireEvent.click(within(exclusive).getByRole('button', { name: 'Take Exclusive from b' }))
    await vi.waitFor(() => expect(update).toHaveBeenLastCalledWith('a', expect.objectContaining({ exclusive: true, description: 'Fast' })))
    fireEvent.click(within(screen.getByRole('row', { name: /Description/ })).getByRole('button', { name: 'Take Description from b' }))
    await vi.waitFor(() => expect(update).toHaveBeenLastCalledWith('a', expect.objectContaining({ description: null })))
    expect(within(screen.getByRole('row', { name: /Version/ })).queryByRole('button')).not.toBeInTheDocument()
    expect(within(screen.getByRole('row', { name: /^Decode average/ })).queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers nothing on matching rows, and waits for the router to stop', async () => {
    renderCompare(profiles, true)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Only differences' }))

    expect(within(await screen.findByRole('row', { name: /CPU threads/ })).queryByRole('button')).not.toBeInTheDocument()
    const take = within(screen.getByRole('row', { name: /Context size/ })).getByRole('button', { name: 'Take Context size from qwen-nomtp' })
    expect(take).toBeDisabled()
    expect(take).toHaveAttribute('title', 'Stop the router to change a profile')
  })

  it('stays hidden until there are two profiles to compare', () => {
    const { container } = renderCompare(profiles.slice(0, 1))
    expect(container).toBeEmptyDOMElement()
  })
})
