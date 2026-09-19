import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const responses: Record<string, unknown> = {
  '/api/server/status': {
    state: 'stopped',
    pid: null,
    last_exit_code: null,
    endpoint: 'http://127.0.0.1:1234',
    logs: [],
  },
  '/api/runtimes': [],
  '/api/profiles': [],
  '/api/tokens': [],
}

afterEach(() => vi.restoreAllMocks())

describe('App', () => {
  it('renders live operational state and navigates to server details', async () => {
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : new URL(input.url).pathname
      return Promise.resolve(new Response(JSON.stringify(responses[path]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>)

    expect(await screen.findByText('http://127.0.0.1:1234')).toBeInTheDocument()
    expect(screen.getByText('No model profiles yet')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Server' }))

    expect(screen.getByRole('heading', { name: 'Process' })).toBeInTheDocument()
    expect(screen.getByText('Waiting for router output…')).toBeInTheDocument()
  })
})