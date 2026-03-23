import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from 'react'
import './App.css'
import { apiRequest, createChatSocket, deleteConversationHistory, uploadFile } from './lib/api'
import type { EugeneApplet, EugeneHealth, EugeneSchedule, EugeneUsage } from './lib/api'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  text: string
}

type ConversationRecord = {
  id: string
  title: string
  updatedAt: string
}

type DraftAttachment = {
  id: string
  filename: string
  path: string
  tokenEstimate: number
}

const STORAGE_KEYS = {
  apiKey: 'eugene.apiKey',
  conversations: 'eugene.conversations',
}

function makeConversationRecord(id: string): ConversationRecord {
  return {
    id,
    title: 'New conversation',
    updatedAt: new Date().toISOString(),
  }
}

function readStoredConversations(): ConversationRecord[] {
  const raw = localStorage.getItem(STORAGE_KEYS.conversations)
  if (!raw) {
    return []
  }
  try {
    const parsed = JSON.parse(raw) as ConversationRecord[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function summarizeTitle(text: string): string {
  const cleaned = text.trim().replace(/\s+/g, ' ')
  return cleaned.length > 40 ? `${cleaned.slice(0, 40)}…` : cleaned || 'New conversation'
}

function formatTimestamp(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function totalTokens(usage: EugeneUsage[]): number {
  return usage.reduce((sum, row) => sum + row.prompt_tokens + row.completion_tokens, 0)
}

function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil(text.length / 4))
}

function isImageFile(file: File): boolean {
  return file.type.startsWith('image/')
}

function isTextMedium(file: File): boolean {
  if (file.type.startsWith('text/')) {
    return true
  }
  return /\.(pdf|md|txt|csv|json|ya?ml|toml|xml|html|css|js|ts|tsx|jsx|py|rs|go|java|c|cpp|h|hpp|sh|sql|log)$/i.test(file.name)
}

function icon(name: 'plus' | 'upload' | 'send' | 'refresh' | 'spark' | 'clock' | 'chip' | 'trash') {
  const paths = {
    plus: 'M12 5v14M5 12h14',
    upload: 'M12 16V6m0 0-4 4m4-4 4 4M5 19h14',
    send: 'm4 12 15-7-4 14-2-6-9-1Z',
    refresh: 'M20 11a8 8 0 1 0 2 5m-2-5h-5m5 0V6',
    spark: 'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z',
    clock: 'M12 7v5l3 3',
    chip: 'M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3M8 8h8v8H8Z',
    trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  } as const
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="ui-icon">
      <path d={paths[name]} />
    </svg>
  )
}

export default function App() {
  const initialConversations = readStoredConversations()
  const seededConversations = initialConversations.length > 0 ? initialConversations : [makeConversationRecord(crypto.randomUUID())]
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(STORAGE_KEYS.apiKey) ?? '')
  const [conversations, setConversations] = useState<ConversationRecord[]>(seededConversations)
  const [activeSessionId, setActiveSessionId] = useState(seededConversations[0].id)
  const [prompt, setPrompt] = useState('')
  const [attachments, setAttachments] = useState<DraftAttachment[]>([])
  const [messages, setMessages] = useState<Record<string, ChatMessage[]>>({})
  const [status, setStatus] = useState('Disconnected')
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [health, setHealth] = useState<EugeneHealth | null>(null)
  const [applets, setApplets] = useState<EugeneApplet[]>([])
  const [schedules, setSchedules] = useState<EugeneSchedule[]>([])
  const [usage, setUsage] = useState<EugeneUsage[]>([])
  const socketRef = useRef<WebSocket | null>(null)
  const activeSessionRef = useRef(activeSessionId)
  const logRef = useRef<HTMLDivElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const currentMessages = messages[activeSessionId] ?? []
  const connected = status === 'Connected'
  const enabledAppletCount = applets.filter((item) => item.enabled).length

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.apiKey, apiKey)
  }, [apiKey])

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.conversations, JSON.stringify(conversations))
  }, [conversations])

  useEffect(() => {
    activeSessionRef.current = activeSessionId
  }, [activeSessionId])

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [activeSessionId, currentMessages])

  useEffect(() => {
    void loadHistory(activeSessionId)
  }, [activeSessionId, apiKey])

  useEffect(() => {
    if (!apiKey.trim()) {
      return
    }
    void refreshPanels()
    const timer = window.setInterval(() => {
      void refreshPanels()
    }, 15000)
    return () => window.clearInterval(timer)
  }, [apiKey])

  useEffect(() => {
    disconnectSocket()
    if (apiKey.trim()) {
      connectSocket(activeSessionId)
    }
    return () => {
      disconnectSocket()
    }
  }, [activeSessionId, apiKey])

  function updateConversation(sessionId: string, update: Partial<ConversationRecord>) {
    setConversations((current) => {
      const found = current.some((item) => item.id === sessionId)
      const next = found ? current.map((item) => (item.id === sessionId ? { ...item, ...update } : item)) : [...current, { ...makeConversationRecord(sessionId), ...update }]
      return [...next].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    })
  }

  function addMessage(sessionId: string, message: ChatMessage) {
    setMessages((current) => ({
      ...current,
      [sessionId]: [...(current[sessionId] ?? []), message],
    }))
  }

  async function loadHistory(sessionId: string) {
    if (!apiKey.trim()) {
      return
    }
    try {
      const history = await apiRequest<Array<{ role: string; content: string }>>(`/api/history/${sessionId}`, apiKey)
      setMessages((current) => ({
        ...current,
        [sessionId]: history
          .filter((item) => item.role === 'user' || item.role === 'assistant')
          .map((item, index) => ({
            id: `${sessionId}-${item.role}-${index}`,
            role: item.role as 'user' | 'assistant',
            text: item.content,
          })),
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load conversation history.')
    }
  }

  async function refreshPanels() {
    if (!apiKey.trim()) {
      return
    }
    try {
      setRefreshing(true)
      setError('')
      const [healthPayload, appletsPayload, schedulesPayload, usagePayload] = await Promise.all([
        apiRequest<EugeneHealth>('/api/health', apiKey),
        apiRequest<EugeneApplet[]>('/api/applets', apiKey),
        apiRequest<EugeneSchedule[]>('/api/schedules', apiKey),
        apiRequest<EugeneUsage[]>('/api/token-usage', apiKey),
      ])
      setHealth(healthPayload)
      setApplets(Array.isArray(appletsPayload) ? appletsPayload : [])
      setSchedules(Array.isArray(schedulesPayload) ? schedulesPayload : [])
      setUsage(Array.isArray(usagePayload) ? usagePayload.slice(0, 12) : [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh Eugene state.')
    } finally {
      setRefreshing(false)
    }
  }

  function connectSocket(sessionId: string) {
    if (!apiKey.trim()) {
      setError('Enter your Eugene API key first.')
      return
    }
    if (socketRef.current && socketRef.current.readyState <= WebSocket.OPEN && activeSessionRef.current === sessionId) {
      return
    }
    disconnectSocket()
    const socket = createChatSocket(sessionId, apiKey)
    socketRef.current = socket
    setStatus('Connecting…')
    socket.onopen = () => {
      setStatus('Connected')
      setError('')
    }
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as { text?: string }
      if (!payload.text) {
        return
      }
      addMessage(sessionId, {
        id: `${sessionId}-assistant-${crypto.randomUUID()}`,
        role: 'assistant',
        text: payload.text,
      })
      updateConversation(sessionId, {
        updatedAt: new Date().toISOString(),
      })
      setSending(false)
    }
    socket.onclose = () => {
      setStatus('Disconnected')
      setSending(false)
    }
    socket.onerror = () => {
      setStatus('Connection error')
      setError('WebSocket connection failed.')
      setSending(false)
    }
  }

  function disconnectSocket() {
    if (socketRef.current) {
      socketRef.current.onclose = null
      socketRef.current.close()
      socketRef.current = null
    }
    setStatus('Disconnected')
  }

  function createConversation() {
    const sessionId = crypto.randomUUID()
    updateConversation(sessionId, makeConversationRecord(sessionId))
    setActiveSessionId(sessionId)
    setMessages((current) => ({ ...current, [sessionId]: [] }))
    setPrompt('')
    setAttachments([])
    setError('')
  }

  async function deleteConversation(sessionId: string) {
    if (!apiKey.trim()) {
      return
    }
    const remaining = conversations.filter((item) => item.id !== sessionId)
    const nextSessionId = remaining[0]?.id ?? crypto.randomUUID()
    try {
      await deleteConversationHistory(sessionId, apiKey)
      setConversations(remaining.length > 0 ? remaining : [makeConversationRecord(nextSessionId)])
      setMessages((current) => {
        const next = { ...current }
        delete next[sessionId]
        if (remaining.length === 0) {
          next[nextSessionId] = []
        }
        return next
      })
      if (activeSessionId === sessionId) {
        setActiveSessionId(nextSessionId)
      }
      setAttachments([])
      setPrompt('')
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete conversation.')
    }
  }

  function selectConversation(sessionId: string) {
    setActiveSessionId(sessionId)
    setPrompt('')
    setAttachments([])
    setError('')
  }

  async function handleFileSelection(event: ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(event.target.files ?? [])
    event.target.value = ''
    if (!apiKey.trim()) {
      setError('Enter your Eugene API key before uploading files.')
      return
    }
    if (selectedFiles.length === 0) {
      return
    }
    setUploading(true)
    setError('')
    try {
      for (const file of selectedFiles) {
        if (isImageFile(file)) {
          setError(`Image uploads are not supported yet. "${file.name}" was skipped because Eugene currently accepts text mediums only.`)
          continue
        }
        if (!isTextMedium(file)) {
          setError(`"${file.name}" was skipped. Eugene currently accepts text-oriented uploads only.`)
          continue
        }
        const uploaded = await uploadFile(file, apiKey)
        const tokenEstimate = estimateTokens(await file.text())
        setAttachments((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            filename: uploaded.filename,
            path: uploaded.path,
            tokenEstimate,
          },
        ])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  function removeAttachment(attachmentId: string) {
    setAttachments((current) => current.filter((item) => item.id !== attachmentId))
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submitDraft()
    }
  }

  async function submitDraft() {
    if (sending || uploading) {
      return
    }
    const text = prompt.trim()
    if (!text && attachments.length === 0) {
      return
    }
    connectSocket(activeSessionId)
    const socket = socketRef.current
    if (!socket) {
      return
    }
    const payload = {
      text: text || 'Please process the attached files.',
      attachments: attachments.map((item) => item.path),
    }
    addMessage(activeSessionId, {
      id: `${activeSessionId}-user-${crypto.randomUUID()}`,
      role: 'user',
      text:
        text ||
        `Uploaded ${attachments.length} attachment${attachments.length === 1 ? '' : 's'}: ${attachments.map((item) => item.filename).join(', ')}`,
    })
    updateConversation(activeSessionId, {
      title: currentMessages.length === 0 ? summarizeTitle(text || attachments[0]?.filename || 'New conversation') : conversations.find((item) => item.id === activeSessionId)?.title ?? summarizeTitle(text),
      updatedAt: new Date().toISOString(),
    })
    setPrompt('')
    setAttachments([])
    setSending(true)
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload))
      return
    }
    socket.addEventListener(
      'open',
      () => {
        socket.send(JSON.stringify(payload))
      },
      { once: true },
    )
    socket.addEventListener(
      'error',
      () => {
        setSending(false)
      },
      { once: true },
    )
  }

  function submitMessage(event: FormEvent) {
    event.preventDefault()
    void submitDraft()
  }

  return (
    <main className="layout-shell">
      <a className="skip-link" href="#composer-input">
        Skip to composer
      </a>

      {!apiKey.trim() ? (
        <div className="api-key-overlay" role="dialog" aria-modal="true" aria-labelledby="api-key-title">
          <div className="api-key-card">
            <p className="eyebrow">Setup</p>
            <h2 id="api-key-title">Enter your Eugene API key</h2>
            <p className="api-key-copy">Paste the static API key from `eugene.toml` to unlock chat, history, schedules, and applet state.</p>
            <label className="field api-key-field">
              Eugene API Key
              <input
                name="api_key_modal"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Paste the API key from eugene.toml"
                autoFocus
              />
            </label>
          </div>
        </div>
      ) : null}

      <aside className="sidebar">
        <div className="sidebar-top">
          <div>
            <p className="sidebar-kicker">Eugene</p>
            <h1>Conversations</h1>
          </div>
          <button type="button" className="sidebar-plus" onClick={createConversation} aria-label="New conversation" title="New conversation">
            {icon('plus')}
          </button>
        </div>

        <label className="field">
          Eugene API Key
          <input
            name="api_key"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder="Paste the API key from eugene.toml"
          />
        </label>

        <nav className="conversation-list" aria-label="Conversation list">
          {conversations.map((item) => (
            <div key={item.id} className={`conversation-item ${item.id === activeSessionId ? 'active' : ''}`}>
              <button type="button" className="conversation-main" onClick={() => selectConversation(item.id)}>
                <span className="conversation-title">{item.title}</span>
                <span className="conversation-meta">{formatTimestamp(item.updatedAt)}</span>
              </button>
              <button
                type="button"
                className="conversation-delete"
                onClick={() => void deleteConversation(item.id)}
                aria-label={`Delete ${item.title}`}
                title="Delete conversation"
              >
                {icon('trash')}
              </button>
            </div>
          ))}
        </nav>
      </aside>

      <section className="chat-pane">
        <header className="chat-topbar">
          <div>
            <p className="eyebrow">Single-user assistant</p>
            <h2>{conversations.find((item) => item.id === activeSessionId)?.title ?? 'Conversation'}</h2>
          </div>
          <div className="status-chip">
            <span className={`status-dot ${connected ? 'online' : ''}`} />
            <span>{connected ? 'Live' : status}</span>
          </div>
        </header>

        <div ref={logRef} className="chat-stream" aria-live="polite">
          {currentMessages.length === 0 ? (
            <div className="empty-state">
              <h3>Start a conversation</h3>
              <p>Ask Eugene something, attach a file path if needed, and the reply will stay in this session.</p>
            </div>
          ) : null}

          {currentMessages.map((item) => (
            <article key={item.id} className={`message-row ${item.role}`}>
              <div className="message-avatar">{item.role === 'assistant' ? 'E' : 'Y'}</div>
              <div className="message-card">
                <p className="message-role">{item.role === 'assistant' ? 'Eugene' : 'You'}</p>
                <p className="message-text">{item.text}</p>
              </div>
            </article>
          ))}

          {sending ? (
            <article className="message-row assistant pending">
              <div className="message-avatar">E</div>
              <div className="message-card">
                <p className="message-role">Eugene</p>
                <p className="message-text">Thinking…</p>
              </div>
            </article>
          ) : null}
        </div>

        <form className="composer" onSubmit={submitMessage}>
          {attachments.length > 0 ? (
            <div className="attachment-bar" aria-label="Draft attachments">
              {attachments.map((item) => (
                <div key={item.id} className="attachment-chip">
                  {icon('chip')}
                  <div className="attachment-copy">
                    <strong>{item.filename}</strong>
                    <span>{item.tokenEstimate} tok</span>
                  </div>
                  <button type="button" className="icon-button subtle" onClick={() => removeAttachment(item.id)} aria-label={`Remove ${item.filename}`}>
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : null}

          <div className="composer-actions">
            <div className="composer-input-row">
              <input ref={fileInputRef} type="file" multiple hidden onChange={handleFileSelection} />
              <button
                type="button"
                className="icon-button"
                onClick={() => fileInputRef.current?.click()}
                disabled={!apiKey.trim() || sending || uploading}
                aria-label="Upload file"
                title="Upload text file"
              >
                {icon('upload')}
              </button>
              <label className="composer-field" htmlFor="composer-input">
                <span className="sr-only">Message Eugene</span>
                <textarea
                  id="composer-input"
                  name="message"
                  autoComplete="off"
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  onKeyDown={onComposerKeyDown}
                  placeholder="Message Eugene"
                />
              </label>
            </div>
            <button type="submit" disabled={sending || uploading || !apiKey.trim()} className="send-button">
              {icon('send')}
              <span>{sending ? 'Sending…' : 'Send'}</span>
            </button>
          </div>
        </form>
      </section>

      <aside className="inspector">
        <div className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Runtime</p>
              <h3>Health</h3>
            </div>
            <button type="button" className="ghost" onClick={() => void refreshPanels()} disabled={refreshing || !apiKey.trim()}>
              {icon('refresh')}
              <span className="sr-only">{refreshing ? 'Refreshing' : 'Refresh'}</span>
            </button>
          </div>
          {error ? <p className="panel-error">{error}</p> : null}
          <dl className="stat-list">
            <div>
              <dt>Provider</dt>
              <dd>{health?.provider?.message ?? 'Waiting for API key'}</dd>
            </div>
            <div>
              <dt>Connected</dt>
              <dd>{connected ? 'Web client live' : 'Disconnected'}</dd>
            </div>
            <div>
              <dt>Channels</dt>
              <dd>{health ? Object.keys(health.channels).length : 0}</dd>
            </div>
            <div>
              <dt>Applets</dt>
              <dd>{enabledAppletCount}</dd>
            </div>
          </dl>
        </div>

        <div className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Usage</p>
              <h3>Token activity</h3>
            </div>
          </div>
          <div className="usage-summary">
            <div className="usage-pill">
              <span>Total rows</span>
              <strong>{usage.length}</strong>
            </div>
            <div className="usage-pill">
              <span>{icon('spark')} Total</span>
              <strong>{totalTokens(usage)}</strong>
            </div>
          </div>
          <div className="compact-list">
            {usage.length === 0 ? <p className="empty-copy">No token logs yet.</p> : null}
            {usage.map((row, index) => (
              <article key={`${row.timestamp}-${index}`} className="compact-card">
                <div className="compact-head">
                  <strong>{row.model}</strong>
                  <span>{formatTimestamp(row.timestamp)}</span>
                </div>
                <p>
                  in {row.prompt_tokens} / out {row.completion_tokens}
                </p>
              </article>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Automation</p>
              <h3>Schedules</h3>
            </div>
          </div>
          <div className="compact-list">
            {schedules.length === 0 ? <p className="empty-copy">No scheduled tasks.</p> : null}
            {schedules.map((item) => (
              <article key={item.id} className="compact-card">
                <div className="compact-head">
                  <strong>{item.name}</strong>
                  <span>{icon('clock')}{item.trigger_type}</span>
                </div>
                <p>{item.trigger_value}</p>
              </article>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Capability</p>
              <h3>Applets</h3>
            </div>
          </div>
          <div className="compact-list">
            {applets.length === 0 ? <p className="empty-copy">No applet data loaded.</p> : null}
            {applets.map((item) => (
              <article key={item.name} className="compact-card">
                <div className="compact-head">
                  <strong>{item.name}</strong>
                  <span className={`status-tag ${item.enabled ? 'enabled' : 'disabled'}`}>{item.status}</span>
                </div>
                <p>{item.description}</p>
              </article>
            ))}
          </div>
        </div>
      </aside>
    </main>
  )
}
