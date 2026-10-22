import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { type EditorEntry, clientIssues } from './argumentCatalog'
import { LaunchPreview, ProfileArgumentEditor } from './ProfileArgumentEditor'
import { Dialog } from './SetupPanels'
import { catalogFixture } from './test/catalogFixture'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Harness({ initial = [], initialAcknowledged = [], runtimeSwitchFlags = [], onState }: {
  initial?: EditorEntry[]
  initialAcknowledged?: string[]
  runtimeSwitchFlags?: string[]
  onState?: (entries: EditorEntry[], acknowledged: string[]) => void
}) {
  const catalog = catalogFixture()
  const [entries, setEntries] = useState<EditorEntry[]>(initial)
  const [acknowledged, setAcknowledged] = useState<string[]>(initialAcknowledged)
  onState?.(entries, acknowledged)
  return (
    <ProfileArgumentEditor
      acknowledged={acknowledged}
      catalog={catalog}
      entries={entries}
      issues={clientIssues(catalog, entries, acknowledged)}
      onAcknowledgedChange={setAcknowledged}
      onEntriesChange={setEntries}
      runtimeName="Local CPU"
      runtimeSwitchFlags={runtimeSwitchFlags}
    />
  )
}

describe('ProfileArgumentEditor', () => {
  it('renders Simple mode by category and stores only fields with values', () => {
    let state: EditorEntry[] = []
    render(<Harness onState={(entries) => { state = entries }} />)

    const simple = screen.getByRole('region', { name: 'Common settings' })
    expect(within(simple).getAllByRole('group').map((group) => group.querySelector('legend')?.textContent)).toEqual(['Process and CPU', 'Context and KV cache'])
    const context = screen.getByLabelText('Context size')
    expect(context).toHaveAttribute('placeholder', 'Default: 4096')
    expect(screen.getByText('tokens')).toBeInTheDocument()

    fireEvent.change(context, { target: { value: '32768' } })
    expect(state).toMatchObject([{ kind: 'catalog', id: 'ctx-size', draft: '32768' }])
    fireEvent.change(context, { target: { value: 'lots' } })
    expect(context).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('--ctx-size must be a whole number')).toBeInTheDocument()
    fireEvent.change(context, { target: { value: '' } })
    expect(state).toEqual([])

    fireEvent.change(screen.getByLabelText('Flash attention'), { target: { value: 'on' } })
    expect(state).toMatchObject([{ id: 'flash-attn', draft: 'on' }])
    fireEvent.change(screen.getByLabelText('Flash attention'), { target: { value: '' } })
    expect(state).toEqual([])
  })

  it('opens keyboard-accessible help with defaults, examples, and directional effects', () => {
    render(<Harness />)
    const helpButton = screen.getByRole('button', { name: 'Help for Context size' })

    fireEvent.click(helpButton)
    const help = screen.getByRole('dialog', { name: 'Context size' })
    expect(helpButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(help).getByRole('button', { name: 'Close help' })).toHaveFocus()
    expect(within(help).getByText('Larger contexts use more memory.')).toBeInTheDocument()
    expect(within(help).getByText('4096')).toBeInTheDocument()
    expect(within(help).getByText('32768')).toBeInTheDocument()
    expect(within(help).getByText('Increases')).toBeInTheDocument()
    expect(within(help).getByText('-c, --ctx-size N')).toBeInTheDocument()
    expect(within(help).getByText('size of the prompt context')).toBeInTheDocument()

    fireEvent.keyDown(help, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Context size' })).not.toBeInTheDocument()
    expect(helpButton).toHaveFocus()

    fireEvent.click(helpButton)
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('dialog', { name: 'Context size' })).not.toBeInTheDocument()
    fireEvent.click(helpButton)
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Context size' }), { key: 'Tab' })
    fireEvent.click(screen.getByRole('button', { name: 'Close help' }))
    expect(screen.queryByRole('dialog', { name: 'Context size' })).not.toBeInTheDocument()
  })

  it('closes only the help panel when Escape is pressed inside the profile dialog', () => {
    const onClose = vi.fn()
    render(<Dialog description="Profile" onClose={onClose} title="Edit"><Harness /></Dialog>)

    fireEvent.click(screen.getByRole('button', { name: 'Help for Context size' }))
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Context size' }), { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Edit' })).toBeInTheDocument()
  })

  it('searches advanced arguments, pins added settings, and removes them', () => {
    let state: EditorEntry[] = []
    render(<Harness onState={(entries) => { state = entries }} />)
    const search = screen.getByRole('searchbox', { name: 'Search arguments' })

    fireEvent.change(search, { target: { value: 'warmup' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add Startup warmup' }))
    const warmup = screen.getByLabelText('Startup warmup')
    expect(warmup).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Add Startup warmup' })).toBeDisabled()
    fireEvent.change(warmup, { target: { value: 'false' } })
    expect(state).toMatchObject([{ id: 'warmup', draft: false }])
    fireEvent.change(warmup, { target: { value: '' } })
    expect(screen.getByLabelText('Startup warmup')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Remove Startup warmup' }))
    expect(screen.queryByLabelText('Startup warmup')).not.toBeInTheDocument()
    expect(state).toEqual([])

    fireEvent.change(search, { target: { value: 'port' } })
    expect(screen.getByRole('button', { name: 'Add Listen port' })).toBeDisabled()
    expect(screen.getByText('Set by LlamaWebUI.')).toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'removed' } })
    expect(screen.getByText('Removed')).toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'nothing-matches' } })
    expect(screen.getByText(/No arguments match/)).toBeInTheDocument()
  })

  it('removes a common setting back to the runtime default', () => {
    let state: EditorEntry[] = []
    render(<Harness initial={[
      { key: 'ctx', kind: 'catalog', id: 'ctx-size', draft: '4096' },
      { key: 'fa', kind: 'catalog', id: 'flash-attn', draft: 'on' },
    ]} onState={(entries) => { state = entries }} />)

    expect(screen.queryByRole('button', { name: 'Remove CPU threads' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Context size' }))

    expect(state).toEqual([expect.objectContaining({ id: 'flash-attn', draft: 'on' })])
    expect(screen.getByLabelText('Context size')).toHaveValue('')
    expect(screen.queryByRole('button', { name: 'Remove Context size' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Flash attention' }))
    expect(state).toEqual([])
  })

  it('edits repeated lists, JSON, alias notes, and deprecation hints', () => {
    let state: EditorEntry[] = []
    render(<Harness initial={[
      { key: 'ot', kind: 'catalog', id: 'override-tensor', draft: ['a=CPU'] },
      { key: 'json', kind: 'catalog', id: 'chat-template-kwargs', draft: '{}' },
      { key: 'draft', kind: 'catalog', id: 'spec-draft-n-max', draft: '4' },
      { key: 'defrag', kind: 'catalog', id: 'defrag-thold', draft: '0.1' },
    ]} onState={(entries) => { state = entries }} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add value' }))
    fireEvent.change(screen.getByLabelText('Tensor buffer overrides value 2'), { target: { value: 'b=CPU' } })
    expect(state[0]).toMatchObject({ draft: ['a=CPU', 'b=CPU'] })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Tensor buffer overrides value 1' }))
    expect(state[0]).toMatchObject({ draft: ['b=CPU'] })
    fireEvent.change(screen.getByLabelText('Chat template parameters'), { target: { value: '{' } })
    expect(screen.getByText('--chat-template-kwargs must be valid JSON')).toBeInTheDocument()
    expect(screen.getByText(/advertises it as --draft-max/)).toBeInTheDocument()
    expect(screen.getByText('Deprecated in current builds.')).toBeInTheDocument()
  })

  it('requires an explicit acknowledgement for security-sensitive arguments', () => {
    let acknowledgedState: string[] = []
    render(<Harness onState={(_, acknowledged) => { acknowledgedState = acknowledged }} />)

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search arguments' }), { target: { value: 'agent' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add Agent tools' }))
    fireEvent.change(screen.getByLabelText('Agent tools'), { target: { value: 'true' } })

    expect(screen.getByText('Enables powerful local tools.')).toBeInTheDocument()
    expect(screen.getByText(/Not advertised by this runtime/)).toBeInTheDocument()
    const checkbox = screen.getByRole('checkbox', { name: 'I understand the risk of --agent' })
    fireEvent.click(checkbox)
    expect(acknowledgedState).toEqual(['agent'])
    fireEvent.click(checkbox)
    expect(acknowledgedState).toEqual([])
  })

  it('adds uncatalogued runtime options and validates custom parameters', () => {
    let state: EditorEntry[] = []
    render(<Harness onState={(entries) => { state = entries }} runtimeSwitchFlags={['--warmup']} />)
    expect(screen.getByRole('status')).toHaveTextContent('Local CPU does not advertise --warmup')

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search arguments' }), { target: { value: 'brand' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add --brand-new' }))
    const runtimeSection = screen.getByRole('region', { name: 'Uncatalogued runtime options' })
    expect(within(runtimeSection).getByLabelText('Flag')).toHaveAttribute('readonly')
    fireEvent.change(within(runtimeSection).getByLabelText('Value'), { target: { value: '3' } })
    expect(state).toMatchObject([{ kind: 'raw', flag: '--brand-new', value: '3', flagOnly: false, origin: 'runtime' }])

    fireEvent.click(screen.getByRole('button', { name: 'Add custom parameter' }))
    const customSection = screen.getByRole('region', { name: 'Custom parameters' })
    fireEvent.change(within(customSection).getByLabelText('Flag'), { target: { value: '--future' } })
    fireEvent.change(within(customSection).getByLabelText('Value'), { target: { value: 'a ; rm' } })
    expect(within(customSection).getByText(/contains shell syntax/)).toBeInTheDocument()
    fireEvent.click(within(customSection).getByLabelText('Flag only'))
    expect(within(customSection).queryByText(/contains shell syntax/)).not.toBeInTheDocument()
    fireEvent.click(within(customSection).getByRole('button', { name: 'Remove --future' }))
    fireEvent.click(within(runtimeSection).getByRole('button', { name: 'Remove --brand-new' }))
    expect(state).toEqual([])
  })

  it('shows catalog compatibility callouts', () => {
    const catalog = catalogFixture({ issues: [{ level: 'warning', code: 'help-unparsed', message: 'Help could not be parsed.' }] })
    render(<ProfileArgumentEditor acknowledged={[]} catalog={catalog} entries={[]} issues={[]} onAcknowledgedChange={() => undefined} onEntriesChange={() => undefined} runtimeName="X" runtimeSwitchFlags={[]} />)
    expect(screen.getByText('Help could not be parsed.')).toBeInTheDocument()
  })
})

describe('LaunchPreview', () => {
  it('lists blockers, warnings, and both argument vectors', () => {
    const onRefresh = vi.fn()
    render(<LaunchPreview
      clientIssues={[{ level: 'error', message: 'client blocker', target: 'x' }, { level: 'warning', message: 'client warning', target: 'y' }]}
      onRefresh={onRefresh}
      pending={false}
      preview={{ valid: false, errors: ['server blocker'], warnings: ['server warning'], issues: [], arguments: ['llama-server', '--model', 'm.gguf'], command: '', preset: 'version = 1', router_arguments: ['llama-server', '--models-preset', 'p.ini'] }}
    />)

    expect(screen.getByText('2 launch blockers')).toBeInTheDocument()
    expect(screen.getByText('server blocker')).toBeInTheDocument()
    expect(screen.getByText('2 warnings')).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Model argument vector' })).getAllByRole('listitem')).toHaveLength(3)
    expect(within(screen.getByRole('list', { name: 'Router argument vector' })).getByText('--models-preset')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalled()
  })

  it('reports a clear preflight and request errors', () => {
    render(<LaunchPreview clientIssues={[]} error="preview failed" onRefresh={() => undefined} pending />)
    expect(screen.getByText('No launch blockers')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('preview failed')
  })
})
