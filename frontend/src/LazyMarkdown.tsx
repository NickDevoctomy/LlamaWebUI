import { lazy, Suspense } from 'react'

const MarkdownContent = lazy(() => import('./MarkdownContent').then(({ MarkdownContent }) => ({ default: MarkdownContent })))

/** Markdown from the lazily loaded renderer, showing the plain text until it loads. */
export function LazyMarkdown({ content, className = 'assistant-markdown', charts = false }: { content: string; className?: string; charts?: boolean }) {
  return <div className={className}><Suspense fallback={<p>{content}</p>}><MarkdownContent charts={charts} content={content} /></Suspense></div>
}
