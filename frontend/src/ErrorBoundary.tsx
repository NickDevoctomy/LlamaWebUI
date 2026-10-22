import { AlertCircle, RotateCcw } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Keeps a rendering error in one page from blanking the whole app, and shows what broke. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: Error | null; resetKey?: string }> {
  state: { error: Error | null; resetKey?: string } = { error: null, resetKey: this.props.resetKey }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  static getDerivedStateFromProps(props: { resetKey?: string }, state: { error: Error | null; resetKey?: string }) {
    // Navigating to another page clears the error.
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page failed to render', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return <section className="data-panel page-error" role="alert">
      <AlertCircle size={22} />
      <div>
        <strong>This page failed to render</strong>
        <p>Other pages still work. The error below says what went wrong:</p>
        <pre>{this.state.error.message || String(this.state.error)}</pre>
        <button className="button secondary compact" onClick={() => this.setState({ error: null })} type="button"><RotateCcw size={14} /> Try again</button>
      </div>
    </section>
  }
}
