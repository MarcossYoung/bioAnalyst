import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listFlags } from '../lib/api'
import { CLASS_STYLE } from '../lib/utils'
import type { Flag } from '../lib/types'

function badgeStyle(css: string | undefined): React.CSSProperties {
  if (!css) return {}
  return Object.fromEntries(
    css.split(';').filter(Boolean).map((s) => {
      const [k, v] = s.split(':').map((x) => x.trim())
      return [k.replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]
    }),
  ) as React.CSSProperties
}

const chip: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
  padding: '1px 6px', borderRadius: '3px', border: '1px solid', whiteSpace: 'nowrap',
}

export function FlagsPage() {
  const navigate = useNavigate()
  const [flags, setFlags] = useState<Flag[]>([])
  const [loading, setLoading] = useState(true)
  const [domain, setDomain] = useState('')
  const [correction, setCorrection] = useState('')
  const [q, setQ] = useState('')

  useEffect(() => {
    listFlags().then(setFlags).finally(() => setLoading(false))
  }, [])

  const domains = useMemo(() => Array.from(new Set(flags.map((f) => f.domain).filter(Boolean))) as string[], [flags])
  const corrections = useMemo(
    () => Array.from(new Set(flags.map((f) => `${f.agent_classification}->${f.user_classification}`))),
    [flags],
  )

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return flags.filter((f) => {
      if (domain && (f.domain ?? '') !== domain) return false
      if (correction && `${f.agent_classification}->${f.user_classification}` !== correction) return false
      if (ql && !(`${f.paper_title} ${f.paper_abstract_excerpt} ${f.hypothesis_summary}`.toLowerCase().includes(ql))) return false
      return true
    })
  }, [flags, domain, correction, q])

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>
      <header style={{
        background: '#0f172a', borderBottom: '1px solid #1e293b', padding: '14px 32px',
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px' }}>
          <button onClick={() => navigate('/')} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#475569', fontSize: '16px', lineHeight: 1, padding: 0, marginRight: '4px' }}>←</button>
          <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '13px', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#f1f5f9' }}>Nullifier</span>
          <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: '#475569', letterSpacing: '0.04em' }}>flag library</span>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <a href="/api/flags/export" style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: '#94a3b8', textDecoration: 'none', border: '1px solid #1e293b', borderRadius: '3px', padding: '4px 12px' }}>export JSON</a>
          <button onClick={() => navigate('/history')} style={{ background: 'none', border: '1px solid #1e293b', borderRadius: '3px', cursor: 'pointer', fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: '#475569', padding: '4px 12px' }}>history</button>
        </div>
      </header>

      <main style={{ flex: 1, maxWidth: '880px', width: '100%', margin: '0 auto', padding: '24px' }}>
        <div style={{ display: 'flex', gap: '10px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="search title / abstract / hypothesis" style={{ fontSize: '13px', padding: '5px 8px', border: '1px solid var(--border)', borderRadius: '4px', flex: 1, minWidth: '200px' }} />
          <select value={domain} onChange={(e) => setDomain(e.target.value)} style={{ fontSize: '13px', padding: '5px 8px' }}>
            <option value="">all domains</option>
            {domains.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <select value={correction} onChange={(e) => setCorrection(e.target.value)} style={{ fontSize: '13px', padding: '5px 8px' }}>
            <option value="">all corrections</option>
            {corrections.map((c) => <option key={c} value={c}>{c.replace('->', ' → ')}</option>)}
          </select>
          <span style={{ fontSize: '11px', color: 'var(--text-xs)', fontFamily: 'ui-monospace, Consolas, monospace' }}>{filtered.length} / {flags.length}</span>
        </div>

        {loading ? (
          <p style={{ color: 'var(--text-muted)' }}>Loading…</p>
        ) : flags.length === 0 ? (
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '24px' }}>
            <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-muted)' }}>
              No corrections recorded yet. Flag a misclassification from a run's Evidence panel or the per-run
              review page (<code>/runs/&lt;id&gt;/review</code>), or via <code>python -m nullifier.cli review &lt;report.json&gt;</code>.
              Corrections are injected as few-shot examples into future Librarian runs and stored in <code>~/.nullifier/flags.db</code>.
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {filtered.map((f) => (
              <div key={f.id} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '12px 16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '6px' }}>
                  <span style={{ ...chip, ...badgeStyle(CLASS_STYLE[f.agent_classification]) }}>{f.agent_classification}</span>
                  <span style={{ color: 'var(--text-muted)' }}>→</span>
                  <span style={{ ...chip, ...badgeStyle(CLASS_STYLE[f.user_classification]) }}>{f.user_classification}</span>
                  {f.domain && <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'ui-monospace, Consolas, monospace' }}>{f.domain}</span>}
                  <div style={{ flex: 1 }} />
                  <span style={{ fontSize: '11px', color: 'var(--text-xs)', fontFamily: 'ui-monospace, Consolas, monospace' }}>{f.created_at?.slice(0, 19).replace('T', ' ')}</span>
                </div>
                <p className="prose" style={{ margin: '0 0 4px', fontSize: '14px', color: 'var(--text-heading)' }}><em>{f.paper_title}</em></p>
                {f.paper_abstract_excerpt && <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>"{f.paper_abstract_excerpt}"</p>}
                {f.user_reason && <p style={{ margin: '0 0 4px', fontSize: '12px', color: 'var(--text-body)' }}><span style={{ fontWeight: 600 }}>Reason: </span>{f.user_reason}</p>}
                <p style={{ margin: 0, fontSize: '11px', color: 'var(--text-xs)' }}>Hypothesis: {f.hypothesis_summary}</p>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
