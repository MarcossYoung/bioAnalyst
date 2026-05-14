import type { RunSummary, WsEvent, Flag } from './types'

const BASE = '/api'

export interface CreateFlagBody {
  hypothesis_summary: string
  domain?: string
  entities?: string[]
  paper_title: string
  paper_abstract_excerpt: string
  agent_classification: string
  agent_justification?: string
  user_classification: string
  user_reason?: string
}

export async function listFlags(params: { domain?: string; correction?: string; q?: string } = {}): Promise<Flag[]> {
  const qs = new URLSearchParams()
  if (params.domain) qs.set('domain', params.domain)
  if (params.correction) qs.set('correction', params.correction)
  if (params.q) qs.set('q', params.q)
  const res = await fetch(`${BASE}/flags${qs.toString() ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error('Failed to load flags')
  return res.json()
}

export async function createFlag(body: CreateFlagBody): Promise<void> {
  const res = await fetch(`${BASE}/flags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail ?? res.statusText)
  }
}

export async function createRun(rawInput: string, maxPapers: number): Promise<{ run_id: string }> {
  const res = await fetch(`${BASE}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_input: rawInput, max_papers: maxPapers }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail ?? res.statusText)
  }
  return res.json()
}

export async function getRun(runId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/runs/${runId}`)
  if (!res.ok) throw new Error(`Run ${runId} not found`)
  return res.json()
}

export async function listRuns(): Promise<RunSummary[]> {
  const res = await fetch(`${BASE}/runs`)
  if (!res.ok) throw new Error('Failed to load runs')
  return res.json()
}

export async function cancelRun(runId: string): Promise<void> {
  await fetch(`${BASE}/runs/${runId}`, { method: 'DELETE' })
}

export async function getHealth(): Promise<{ local_llm: { ok: boolean; message: string } }> {
  const res = await fetch(`${BASE}/health`)
  return res.json()
}

export function connectRunWs(
  runId: string,
  onEvent: (e: WsEvent) => void,
  onClose: () => void,
): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/runs/${runId}`)
  ws.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data))
    } catch {
      // ignore malformed frames
    }
  }
  ws.onclose = onClose
  return ws
}
