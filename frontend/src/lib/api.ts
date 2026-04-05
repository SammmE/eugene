export type EugeneHealth = {
  ok: boolean
  provider: { ok: boolean; message: string }
  channels: Record<string, { connected: boolean; enabled: boolean; details: string }>
}

export type EugeneApplet = {
  name: string
  description: string
  enabled: boolean
  status: string
}

export type EugeneSchedule = {
  id: string
  name: string
  trigger_type: string
  trigger_value: string
}

export type EugeneTrigger = {
  id: string
  name: string
  source_applet: string
  signal_name: string
  enabled: boolean
  last_fired_at?: string | null
}

export type EugeneUsage = {
  timestamp: string
  model: string
  prompt_tokens: number
  completion_tokens: number
}

export type UploadedAttachment = {
  path: string
  filename: string
}

type RequestOptions = {
  method?: string
  body?: BodyInit | null
  headers?: HeadersInit
}

const httpBase = (import.meta.env.VITE_EUGENE_HTTP_BASE as string | undefined) ?? ''
const wsBase =
  (import.meta.env.VITE_EUGENE_WS_BASE as string | undefined) ??
  (httpBase
    ? httpBase.replace(/^http/, 'ws')
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`)

function endpoint(path: string): string {
  return `${httpBase}${path}`
}

export async function apiRequest<T>(path: string, apiKey: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers ?? {})
  headers.set('x-api-key', apiKey)
  const response = await fetch(endpoint(path), { ...options, headers })
  const contentType = response.headers.get('content-type') ?? ''
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = typeof payload === 'string' ? payload : (payload?.detail ?? JSON.stringify(payload))
    throw new Error(`${response.status} ${detail}`)
  }
  return payload as T
}

export async function uploadFile(file: File, apiKey: string): Promise<UploadedAttachment> {
  const formData = new FormData()
  formData.append('file', file)
  return apiRequest<UploadedAttachment>('/api/upload', apiKey, {
    method: 'POST',
    body: formData,
  })
}

export async function deleteConversationHistory(sessionId: string, apiKey: string): Promise<{ deleted: boolean; session_id: string }> {
  return apiRequest<{ deleted: boolean; session_id: string }>(`/api/history/${sessionId}`, apiKey, {
    method: 'DELETE',
  })
}

export function createChatSocket(sessionId: string, apiKey: string): WebSocket {
  const encoded = encodeURIComponent(apiKey)
  return new WebSocket(`${wsBase}/ws/${sessionId}?api_key=${encoded}`)
}
