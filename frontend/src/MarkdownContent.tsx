import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export function stripYamlFrontmatter(markdown: string): string {
  const normalized = markdown.replace(/^\uFEFF/, '')
  const lines = normalized.split(/\r?\n/)
  if (lines[0]?.trim() !== '---') return markdown

  const closingLine = lines.findIndex((line, index) => index > 0 && /^(?:---|\.\.\.)\s*$/.test(line.trim()))
  if (closingLine < 0) return markdown
  return lines.slice(closingLine + 1).join('\n').trimStart()
}

export function MarkdownContent({ content }: { content: string }) {
  return (
    <ReactMarkdown
      rehypePlugins={[rehypeRaw, rehypeSanitize]}
      remarkPlugins={[remarkGfm]}
    >
      {stripYamlFrontmatter(content)}
    </ReactMarkdown>
  )
}