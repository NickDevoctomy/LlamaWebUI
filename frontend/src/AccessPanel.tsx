import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  Clipboard,
  Code2,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  Plus,
  ShieldAlert,
  Trash2,
} from 'lucide-react'
import { type FormEvent, useState } from 'react'
import { api, type AccessToken, type CreatedAccessToken } from './api'
import { Dialog, Field } from './SetupPanels'

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1800)
  }

  return (
    <button className="button secondary compact" onClick={copy} type="button">
      {copied ? <Check size={14} /> : <Clipboard size={14} />}
      <span aria-live="polite">{copied ? 'Copied' : label}</span>
    </button>
  )
}

export function AccessPanel({ tokens, running }: {
  tokens: AccessToken[]
  running: boolean
}) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [expiryNote, setExpiryNote] = useState('')
  const [created, setCreated] = useState<CreatedAccessToken | null>(null)
  const [revoking, setRevoking] = useState<AccessToken | null>(null)
  const queryClient = useQueryClient()
  const opencode = useQuery({
    queryKey: ['opencode'],
    queryFn: api.opencodeConfig,
    enabled: running,
    retry: false,
  })
  const createToken = useMutation({
    mutationFn: () => api.createToken(name, expiryNote),
    onSuccess: async (token) => {
      setCreated(token)
      await queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const revokeToken = useMutation({
    mutationFn: (tokenId: string) => api.revokeToken(tokenId),
    onSuccess: async () => {
      setRevoking(null)
      await queryClient.invalidateQueries({ queryKey: ['tokens'] })
    },
  })
  const config = opencode.data ? JSON.stringify(opencode.data, null, 2) : ''

  function closeCreation() {
    setCreating(false)
    setCreated(null)
    setName('')
    setExpiryNote('')
    createToken.reset()
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    createToken.mutate()
  }

  return (
    <div className="access-grid">
      <section className="data-panel access-tokens">
        <div className="panel-heading">
          <div><h2>API access keys</h2><p>Bearer keys accepted directly by the native llama.cpp server.</p></div>
          <button className="button primary compact" disabled={running} onClick={() => setCreating(true)} type="button"><Plus size={15} /> Create key</button>
        </div>
        {running && <div className="inline-notice"><LockKeyhole size={15} /><span>Stop the router before changing keys. Native llama.cpp does not hot-reload its key file.</span></div>}
        {tokens.length ? <div className="token-list">{tokens.map((token) => (
          <article className="token-row" key={token.id}>
            <span className="record-icon"><KeyRound size={18} /></span>
            <div className="record-copy"><strong>{token.name}</strong><span>•••• •••• •••• {token.last_four}</span></div>
            <div className="record-meta wide"><small>Created</small><span>{formatDate(token.created_at)}</span></div>
            <div className="record-meta wide"><small>Expiry note</small><span>{token.expiry_note ?? 'None'}</span></div>
            <span className={`state-pill ${token.enabled ? 'ready' : ''}`}>{token.enabled ? 'Active' : 'Revoked'}</span>
            <button aria-label={`Revoke ${token.name}`} className="icon-button small danger-icon" disabled={!token.enabled || running} onClick={() => setRevoking(token)} title={`Revoke ${token.name}`} type="button"><Trash2 size={16} /></button>
          </article>
        ))}</div> : <div className="empty access-empty"><KeyRound size={28} /><strong>No access keys</strong><span>Create a key before exposing the inference endpoint to clients.</span><button className="button primary" disabled={running} onClick={() => setCreating(true)} type="button"><Plus size={15} /> Create key</button></div>}
      </section>

      <section className="data-panel integration-panel">
        <div className="panel-heading">
          <div><h2>OpenCode</h2><p>Generated from model IDs reported by the live router.</p></div>
          <Code2 size={20} />
        </div>
        {!running ? <div className="integration-empty"><Code2 size={25} /><strong>Start the router to generate configuration</strong><span>The model IDs must come from the live native endpoint.</span></div> : opencode.isPending ? <div className="integration-empty"><LoaderCircle className="spin" size={25} /><strong>Reading live models</strong></div> : opencode.error ? <div className="integration-empty error-state"><AlertCircle size={25} /><strong>Configuration unavailable</strong><span>{opencode.error.message}</span></div> : <div className="code-wrap"><div className="code-toolbar"><span>opencode.jsonc</span><CopyButton label="Copy config" text={config} /></div><pre>{config}</pre></div>}
      </section>

      {creating && <Dialog title={created ? 'Key created' : 'Create access key'} description={created ? 'Copy this key now. It cannot be displayed again.' : 'Keys are passed to llama.cpp through its restricted native key file.'} onClose={closeCreation}>
        {created ? <div className="secret-reveal"><div className="security-warning"><ShieldAlert size={19} /><div><strong>Shown once</strong><span>Closing this dialog permanently removes the plaintext key from the control UI.</span></div></div><div className="secret-value"><code>{created.token}</code><CopyButton label="Copy key" text={created.token} /></div><dl><div><dt>Name</dt><dd>{created.name}</dd></div><div><dt>Ends in</dt><dd>{created.last_four}</dd></div></dl><footer className="dialog-actions"><button className="button primary" onClick={closeCreation} type="button">I have saved the key</button></footer></div> : <form onSubmit={submit}><div className="form-body"><Field label="Key name" hint="Use a client or device name so this key is recognizable later."><input autoFocus maxLength={200} onChange={(event) => setName(event.target.value)} placeholder="OpenCode workstation" required value={name} /></Field><Field label="Expiry note" hint="Informational only; llama.cpp does not enforce per-key expiry."><input maxLength={500} onChange={(event) => setExpiryNote(event.target.value)} placeholder="Rotate after project delivery" value={expiryNote} /></Field>{createToken.error && <div className="form-error"><AlertCircle size={15} /> {createToken.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={closeCreation} type="button">Cancel</button><button className="button primary" disabled={createToken.isPending || !name.trim()} type="submit">{createToken.isPending ? <LoaderCircle className="spin" size={15} /> : <KeyRound size={15} />} Create key</button></footer></form>}
      </Dialog>}

      {revoking && <Dialog title="Revoke access key?" description="This permanently removes the key from llama.cpp's key file." onClose={() => setRevoking(null)}><div className="confirm-body"><ShieldAlert size={24} /><p><strong>{revoking.name}</strong> ending in <span className="mono">{revoking.last_four}</span> will stop authenticating after the next router start.</p>{revokeToken.error && <div className="form-error"><AlertCircle size={15} /> {revokeToken.error.message}</div>}</div><footer className="dialog-actions"><button className="button secondary" onClick={() => setRevoking(null)} type="button">Keep key</button><button className="button danger" disabled={revokeToken.isPending} onClick={() => revokeToken.mutate(revoking.id)} type="button">{revokeToken.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />} Revoke key</button></footer></Dialog>}
    </div>
  )
}