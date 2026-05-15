import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getRun, createFlag } from '../lib/api'
import { CLASS_STYLE, CLASSIFICATION_OPTIONS } from '../lib/utils'
import type { ClaimEvidence, PaperClassification, AtomicClaim, NormalizedPaper } from '../lib/types'

interface ReviewItem {
  claimId: string
  claimStatement: string
  cls: PaperClassification
  abstract?: string
}

function badgeStyleFromCss(css: string): React.CSSProperties {
  return Object.fromEntries(
    css.split(';').filter(Boolean).map((s) => {
      const [k, v] = s.split(':').map((x) => x.trim())
      return [k.replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]
    }),
  ) as React.CSSProperties
}

export function ReviewPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()

  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [run, setRun] = useState<Record<string, unknown> | null>(null)
  const [idx, setIdx] = useState(0)
  const [formOpen, setFormOpen] = useState(false)
  const [correct, setCorrect] = useState<string>('tangential')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [flagged, setFlagged] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!runId) return
    getRun(runId).then(setRun).catch((e) => setErr(e instanceof Error ? e.message : 'load failed')).finally(() => setLoading(false))
  }, [runId])

  const formalized = (run?.formalized ?? null) as Record<string, unknown> | null
  const items = useMemo<ReviewItem[]>(() => {
    if (!run) return []
    const evidence = (run.evidence ?? {}) as { claim_evidence?: Record<string, ClaimEvidence> }
    const claims = (formalized?.atomic_claims as AtomicClaim[]) ?? []
    const stmtById = Object.fromEntries(claims.map((c) => [c.id, c.statement]))
    const out: ReviewItem[] = []
    for (const [cid, ce] of Object.entries(evidence.claim_evidence ?? {})) {
      const abstractById = new Map<string, string | undefined>()
      for (const p of (ce.retrieved_papers ?? []) as NormalizedPaper[]) abstractById.set(`${p.source}:${p.id}`, p.abstract)
      for (const cls of ce.classifications ?? []) {
        out.push({ claimId: cid, claimStatement: stmtById[cid] ?? cid, cls, abstract: abstractById.get(cls.paper_id) })
      }
    }
    return out
  }, [run, formalized])

  const current = items[idx]
  const itemKey = current ? `${current.claimId}#${current.cls.paper_id}` : ''

  const flagCtx = useMemo(() => ({
    hypothesis: String(formalized?.core_hypothesis ?? ''),
    domain: String(formalized?.domain ?? 'unknown'),
    entities: [...((formalized?.key_entities as string[]) ?? []), ...((formalized?.starter_entities as string[]) ?? [])],
  }), [formalized])

  function next() {
    setFormOpen(false)
    setIdx((i) => Math.min(i + 1, items.length - 1))
  }

  function prev() {
    setFormOpen(false)
    setIdx((i) => Math.max(i - 1, 0))
  }

  function openForm() {
    if (!current) return
    setCorrect(CLASSIFICATION_OPTIONS.find((o) => o !== current.cls.classification) ?? 'tangential')
    setReason('')
    setFormOpen(true)
  }

  async function save() {
    if (!current) return
    setBusy(true)
    try {
      await createFlag({
        hypothesis_summary: flagCtx.hypothesis,
        domain: flagCtx.domain,
        entities: flagCtx.entities,
        paper_title: current.cls.paper_title,
        paper_abstract_excerpt: current.cls.justification_quote || (current.abstract ? current.abstract.slice(0, 300) : ''),
        agent_classification: current.cls.classification,
        agent_justification: current.cls.reasoning ?? '',
        user_classification: correct,
        user_reason: reason,
      })
      setFlagged((s) => new Set(s).add(itemKey))
      setFormOpen(false)
      next()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'save failed')
    } finally {
      setBusy(false)
    }
  }

  const classCss = current ? (CLASS_STYLE[current.cls.classification] ?? CLASS_STYLE['tangential']) : ''

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>
      <header style={{
        background: '#0f172a', borderBottom: '1px solid #1e293b', padding: '10px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <button onClick={() => navigate(`/runs/${runId}`)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#475569', fontSize: '16px', padding: 0 }}>←</button>
          <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: '#64748b', letterSpacing: '0.06em', textTransform: 'uppercase' }}>Review</span>
          <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '12px', color: '#94a3b8' }}>run/{runId}</span>
        </div>
        <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: '#64748b' }}>
          {items.length ? `${idx + 1} / ${items.length}` : ''} · {flagged.size} flagged
        </span>
      </header>

      <main style={{ flex: 1, maxWidth: '720px', width: '100%', margin: '0 auto', padding: '28px 24px' }}>
        {loading && <p style={{ color: 'var(--text-muted)' }}>Loading…</p>}
        {err && <p style={{ color: '#7f1d1d' }}>{err}</p>}
        {!loading && !err && items.length === 0 && <p style={{ color: 'var(--text-muted)' }}>No classifications to review for this run.</p>}

        {current && (
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', padding: '20px 24px' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '4px' }}>
              Claim {current.claimId}
            </div>
            <p className="prose" style={{ margin: '0 0 16px', fontSize: '14px', color: 'var(--text-heading)' }}>{current.claimStatement}</p>

            <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap', marginBottom: '8px' }}>
              <span style={{
                fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
                padding: '1px 6px', borderRadius: '3px', border: '1px solid', ...badgeStyleFromCss(classCss),
              }}>
                {current.cls.classification}
              </span>
              {flagged.has(itemKey) && <span style={{ fontSize: '11px', color: 'var(--verdict-strong)' }}>⚑ flagged ✓</span>}
            </div>
            <p className="prose" style={{ margin: '0 0 8px', fontSize: '15px', color: 'var(--text-heading)' }}><em>{current.cls.paper_title}</em></p>
            {current.cls.justification_quote && (
              <blockquote className="prose" style={{ margin: '6px 0', paddingLeft: '12px', borderLeft: '2px solid var(--border)', fontStyle: 'italic', fontSize: '13px', color: 'var(--text-body)' }}>
                "{current.cls.justification_quote}"
              </blockquote>
            )}
            {current.cls.reasoning && <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>{current.cls.reasoning}</p>}
            {current.abstract && <p className="prose" style={{ margin: '12px 0 0', fontSize: '13px', color: 'var(--text-body)' }}>{current.abstract}</p>}

            {formOpen && (
              <div style={{ marginTop: '16px', padding: '12px', border: '1px solid var(--border)', borderRadius: '6px', background: 'var(--bg)', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>Correct classification:</div>
                <select value={correct} onChange={(e) => setCorrect(e.target.value)} style={{ fontSize: '13px', padding: '4px 6px', maxWidth: '220px' }}>
                  {CLASSIFICATION_OPTIONS.filter((o) => o !== current.cls.classification).map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
                <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} placeholder="why (optional)" style={{ fontSize: '13px', padding: '6px', border: '1px solid var(--border)', borderRadius: '3px', resize: 'vertical' }} />
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button onClick={save} disabled={busy} style={{ fontSize: '12px', padding: '5px 14px', borderRadius: '4px', border: 'none', background: 'var(--oxford)', color: '#fff', cursor: busy ? 'default' : 'pointer' }}>{busy ? 'saving…' : 'save flag & next'}</button>
                  <button onClick={() => setFormOpen(false)} style={{ fontSize: '12px', padding: '5px 12px', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}>cancel</button>
                </div>
              </div>
            )}

            <div style={{ display: 'flex', gap: '8px', marginTop: '20px', borderTop: '1px solid var(--border-light)', paddingTop: '14px' }}>
              <button onClick={prev} disabled={idx === 0} style={navBtn(idx === 0)}>← prev</button>
              {!formOpen && <button onClick={openForm} style={navBtn(false)}>⚑ flag</button>}
              <button onClick={next} disabled={idx >= items.length - 1} style={navBtn(idx >= items.length - 1)}>keep & next →</button>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

function navBtn(disabled: boolean): React.CSSProperties {
  return {
    fontSize: '12px', padding: '5px 12px', borderRadius: '4px', border: '1px solid var(--border)',
    background: 'var(--surface)', color: disabled ? 'var(--text-xs)' : 'var(--text-body)',
    cursor: disabled ? 'default' : 'pointer', fontFamily: 'ui-monospace, Consolas, monospace',
  }
}
