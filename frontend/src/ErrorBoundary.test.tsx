import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Broken({ fail }: { fail: boolean }) {
  if (fail) throw new Error('models.map is not a function')
  return <p>Page content</p>
}

describe('ErrorBoundary', () => {
  it('shows the render error in place of the page and recovers on navigation', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { rerender } = render(<ErrorBoundary resetKey="Server"><Broken fail /></ErrorBoundary>)

    expect(screen.getByRole('alert')).toHaveTextContent('This page failed to render')
    expect(screen.getByText('models.map is not a function')).toBeInTheDocument()

    rerender(<ErrorBoundary resetKey="Dashboard"><Broken fail={false} /></ErrorBoundary>)
    expect(screen.getByText('Page content')).toBeInTheDocument()
  })

  it('retries rendering when asked', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    let fail = true
    function Flaky() {
      if (fail) throw new Error('temporary')
      return <p>Recovered</p>
    }
    render(<ErrorBoundary resetKey="Server"><Flaky /></ErrorBoundary>)
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByText('Recovered')).toBeInTheDocument()
  })
})
