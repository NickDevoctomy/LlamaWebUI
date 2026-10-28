import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { parseChart, ReportChart } from './ReportChart'

export function stripYamlFrontmatter(markdown: string): string {
  const normalized = markdown.replace(/^﻿/, '')
  const lines = normalized.split(/\r?\n/)
  if (lines[0]?.trim() !== '---') return markdown

  const closingLine = lines.findIndex((line, index) => index > 0 && /^(?:---|\.\.\.)\s*$/.test(line.trim()))
  if (closingLine < 0) return markdown
  return lines.slice(closingLine + 1).join('\n').trimStart()
}

type HastNode = { tagName?: string; value?: string; properties?: { className?: unknown }; children?: HastNode[] }

/** The JSON of a ```chart block, or undefined for any other block. */
function chartSource(node: HastNode | undefined) {
  const code = node?.children?.[0]
  const classes = code?.tagName === 'code' ? code.properties?.className : undefined
  if (!Array.isArray(classes) || !classes.includes('language-chart')) return undefined
  return (code?.children ?? []).map((child) => child.value ?? '').join('')
}

const chartComponents: Components = {
  pre({ node, children, ...props }) {
    const source = chartSource(node as HastNode | undefined)
    const chart = source == null ? undefined : parseChart(source)
    return chart ? <ReportChart chart={chart} /> : <pre {...props}>{children}</pre>
  },
}

/** Sanitised markdown; with ``charts``, ```chart blocks of JSON are drawn as report charts. */
export function MarkdownContent({ content, charts = false }: { content: string; charts?: boolean }) {
  return (
    <ReactMarkdown
      components={charts ? chartComponents : undefined}
      rehypePlugins={[rehypeRaw, rehypeSanitize]}
      remarkPlugins={[remarkGfm]}
    >
      {stripYamlFrontmatter(content)}
    </ReactMarkdown>
  )
}
