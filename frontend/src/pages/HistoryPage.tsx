import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listRuns, getRun, createRun } from '../lib/api'
import type { RunSummary } from '../lib/types'
import { formatDate, formatDuration, STATUS_STYLE, VERDICT_STYLE } from '../lib/utils'

export function HistoryPage() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [rerunning, setRerunning] = useState<string | null>(null)

  useEffect(() => {
    listRuns().then(setRuns).finally(() => setLoading(false))
  }, [])

  async function rerun(id: string) {
    setRerunning(id)
    try {
      const run = await getRun(id)
      const { run_id } = await createRun(String(run.raw_input ?? ''), Number(run.max_papers ?? 12))
      navigate(`/runs/${run_id}`)
    } catch {
      setRerunning(null)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>

      {/* Header */}
      <header style={{
        background: '#0f172a',
        borderBottom: '1px solid #1e293b',
        padding: '14px 32px',
        display: 'flex',
        alignItems: 'baseline',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px' }}>
          <button
            onClick={() => navigate('/')}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: '#475569',
              fontSize: '16px',
              lineHeight: 1,
              padding: 0,
              marginRight: '4px',
            }}
          >
            ←
          </button>
          <span style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '13px',
            fontWeight: 700,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: '#f1f5f9',
          }}>
            Nullifier
          </span>
          <span style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '11px',
            color: '#475569',
            letterSpacing: '0.04em',
          }}>
            run history
          </span>
        </div>
        <button
          onClick={() => navigate('/')}
          style={{
            background: 'none',
            border: '1px solid #1e293b',
            borderRadius: '3px',
            cursor: 'pointer',
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '11px',
            color: '#475569',
            padding: '4px 12px',
            letterSpacing: '0.04em',
          }}
          onMouseOver={(e) => (e.currentTarget.style.color = '#94a3b8')}
          onMouseOut={(e) => (e.currentTarget.style.color = '#475569')}
        >
          new run
        </button>
      </header>

      <main style={{ flex: 1, maxWidth: '760px', width: '100%', margin: '0 auto', padding: '32px 24px' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: '80px 0', color: 'var(--text-muted)', fontSize: '14px' }}>
            Loading…
          </div>
        ) : runs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '80px 0' }}>
            <p style={{ color: 'var(--text-muted)', marginBottom: '12px' }}>No runs yet.</p>
            <button
              onClick={() => navigate('/')}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--oxford)',
                fontSize: '14px',
                textDecoration: 'underline',
              }}
            >
              Start your first analysis →
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {runs.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                onClick={() => navigate(`/runs/${run.id}`)}
                onRerun={() => rerun(run.id)}
                rerunning={rerunning === run.id}
              />
            ))}
          </div>
        )}
      </main>
    </div>
  )
}

function statusBadgeStyle(status: string): React.CSSProperties {
  const s = STATUS_STYLE[status]
  if (!s) return {}
  const parts = Object.fromEntries(
    s.split(';').filter(Boolean).map(seg => {
      const [k, v] = seg.split(':').map(x => x.trim())
      const camel = k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
      return [camel, v]
    })
  )
  return {
    ...parts,
    fontSize: '10px',
    fontWeight: 600,
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    padding: '1px 6px',
    borderRadius: '3px',
    border: '1px solid',
    display: 'inline-block',
    whiteSpace: 'nowrap',
  } as React.CSSProperties
}

function verdictBadgeStyle(verdict: string): React.CSSProperties {
  const v = VERDICT_STYLE[verdict]
  if (!v) return {}
  return {
    color: v.color,
    background: v.bg,
    borderColor: v.border,
    fontSize: '10px',
    fontWeight: 700,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    padding: '1px 6px',
    borderRadius: '3px',
    border: '1px solid',
    display: 'inline-block',
    whiteSpace: 'nowrap',
  }
}

function RunRow({ run, onClick, onRerun, rerunning }: {
  run: RunSummary
  onClick: () => void
  onRerun: () => void
  rerunning: boolean
}) {
  return (
    <div
      style={{
        width: '100%',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: '5px',
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '16px',
        transition: 'border-color 0.1s',
      }}
      onMouseOver={(e) => (e.currentTarget.style.borderColor = 'var(--oxford)')}
      onMouseOut={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
    >
      <button
        onClick={onClick}
        style={{ minWidth: 0, flex: 1, textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '5px', flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '12px', color: 'var(--text-muted)' }}>#{run.id}</span>
          <span style={statusBadgeStyle(run.status)}>{run.status}</span>
          {run.verdict && <span style={verdictBadgeStyle(run.verdict)}>{run.verdict}</span>}
          {run.mode === 'v5' && (
            <span style={{
              fontSize: '10px', fontWeight: 700, letterSpacing: '0.06em', padding: '1px 6px',
              borderRadius: '3px', border: '1px solid var(--verdict-problematic-border)',
              background: 'var(--verdict-problematic-bg)', color: 'var(--verdict-problematic)',
            }} title="run included a completed-analysis critique">v5 · critique</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: '12px', fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'ui-monospace, Consolas, monospace', flexWrap: 'wrap' }}>
          <span>{formatDate(run.created_at)}</span>
          <span>·</span>
          <span>{formatDuration(run.created_at, run.completed_at)}</span>
          <span>·</span>
          <span>{run.max_papers} papers/claim</span>
        </div>
      </button>
      <button
        onClick={onRerun}
        disabled={rerunning}
        title="start a fresh run on the same input (picks up any new flags)"
        style={{
          flexShrink: 0, fontSize: '11px', padding: '4px 10px', borderRadius: '4px',
          border: '1px solid var(--border)', background: 'var(--surface)', cursor: rerunning ? 'default' : 'pointer',
          color: rerunning ? 'var(--text-xs)' : 'var(--text-body)', fontFamily: 'ui-monospace, Consolas, monospace',
        }}
      >
        {rerunning ? 'starting…' : 're-run with new flags'}
      </button>
    </div>
  )
}
