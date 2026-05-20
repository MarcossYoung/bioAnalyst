import { useState } from 'react'
import { CLASS_STYLE, STRENGTH_COLOR, CLASSIFICATION_OPTIONS } from '../lib/utils'
import { createFlag } from '../lib/api'
import type { ClaimEvidence, NormalizedPaper, PaperClassification } from '../lib/types'

export interface FlagContext {
  hypothesis: string
  domain: string
  entities: string[]
}

interface EvidencePanelProps {
  evidence: Record<string, ClaimEvidence>
  claimText?: Record<string, string>
  flagContext?: FlagContext
}

interface DisplayPaper {
  title: string
  authors?: string[]
  year?: number | null
  venue?: string | null
  classification: string
  quote?: string
  reasoning?: string
  abstract?: string
  doi?: string | null
}

function authorString(authors?: string[]): string {
  if (!authors || authors.length === 0) return ''
  const cleaned = authors.filter(Boolean)
  if (cleaned.length === 0) return ''
  if (cleaned.length === 1) return cleaned[0]
  if (cleaned.length === 2) return `${cleaned[0]} & ${cleaned[1]}`
  return `${cleaned[0]} et al.`
}

function badgeStyleFromCss(css: string): React.CSSProperties {
  return Object.fromEntries(
    css.split(';').filter(Boolean).map((s) => {
      const [k, v] = s.split(':').map((x) => x.trim())
      const camel = k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
      return [camel, v]
    }),
  ) as React.CSSProperties
}

function mergePapers(claim: ClaimEvidence): DisplayPaper[] {
  const byId = new Map<string, NormalizedPaper>()
  for (const p of claim.retrieved_papers ?? []) {
    byId.set(`${p.source}:${p.id}`, p)
  }
  return (claim.classifications ?? []).map((c: PaperClassification) => {
    const rp = byId.get(c.paper_id)
    return {
      title: c.paper_title || rp?.title || '(untitled)',
      authors: rp?.authors,
      year: c.year ?? rp?.year ?? null,
      venue: c.venue ?? rp?.venue ?? null,
      classification: c.classification || 'tangential',
      quote: c.justification_quote,
      reasoning: c.reasoning,
      abstract: rp?.abstract,
      doi: rp?.doi ?? null,
    }
  })
}

function FlagPopover({ paper, ctx, onDone }: { paper: DisplayPaper; ctx: FlagContext; onDone: () => void }) {
  const [correct, setCorrect] = useState<string>(CLASSIFICATION_OPTIONS.find((o) => o !== paper.classification) ?? 'tangential')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setBusy(true); setErr(null)
    try {
      await createFlag({
        hypothesis_summary: ctx.hypothesis,
        domain: ctx.domain,
        entities: ctx.entities,
        paper_title: paper.title,
        paper_abstract_excerpt: paper.quote || (paper.abstract ? paper.abstract.slice(0, 300) : ''),
        agent_classification: paper.classification,
        agent_justification: paper.reasoning ?? '',
        user_classification: correct,
        user_reason: reason,
      })
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save flag')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      marginTop: '6px', padding: '8px 10px', border: '1px solid var(--border)', borderRadius: '4px',
      background: 'var(--bg)', display: 'flex', flexDirection: 'column', gap: '6px', maxWidth: '420px',
    }}>
      <div style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
        Agent said <strong>{paper.classification}</strong>. Correct classification:
      </div>
      <select value={correct} onChange={(e) => setCorrect(e.target.value)} style={{ fontSize: '12px', padding: '3px 6px' }}>
        {CLASSIFICATION_OPTIONS.filter((o) => o !== paper.classification).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="why (optional)"
        rows={2}
        style={{ fontSize: '12px', padding: '4px 6px', border: '1px solid var(--border)', borderRadius: '3px', resize: 'vertical' }}
      />
      {err && <div style={{ fontSize: '11px', color: '#7f1d1d' }}>{err}</div>}
      <div style={{ display: 'flex', gap: '8px' }}>
        <button onClick={submit} disabled={busy} style={{
          fontSize: '11px', padding: '3px 10px', borderRadius: '3px', border: 'none',
          background: 'var(--oxford)', color: '#fff', cursor: busy ? 'default' : 'pointer',
        }}>{busy ? 'saving…' : 'save flag'}</button>
        <button onClick={onDone} style={{ fontSize: '11px', padding: '3px 10px', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)' }}>cancel</button>
      </div>
    </div>
  )
}

function PaperEntry({ paper, flagContext }: { paper: DisplayPaper; flagContext?: FlagContext }) {
  const [showAbstract, setShowAbstract] = useState(false)
  const [flagging, setFlagging] = useState(false)
  const [flagged, setFlagged] = useState(false)
  const classCss = CLASS_STYLE[paper.classification] ?? CLASS_STYLE['tangential']

  return (
    <div style={{ borderLeft: '3px solid var(--border)', paddingLeft: '12px', marginBottom: '14px' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap', marginBottom: '4px' }}>
        <span style={{
          fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em',
          textTransform: 'uppercase', padding: '1px 6px', borderRadius: '3px',
          border: '1px solid', whiteSpace: 'nowrap', ...badgeStyleFromCss(classCss),
        }}>
          {paper.classification}
        </span>
        <span className="prose" style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-heading)', flex: 1 }}>
          {authorString(paper.authors)}{paper.year ? ` (${paper.year})` : ''}
          {authorString(paper.authors) || paper.year ? '. ' : ''}
          <em>{paper.title}</em>
          {paper.venue ? <>. <span style={{ color: 'var(--text-muted)' }}>{paper.venue}</span></> : ''}
          {'.'}
        </span>
        {flagContext && !flagged && (
          <button
            title="flag this classification as wrong"
            onClick={() => setFlagging((v) => !v)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '12px', color: flagging ? 'var(--oxford)' : 'var(--text-xs)', padding: 0 }}
          >
            ⚑ flag
          </button>
        )}
        {flagged && <span style={{ fontSize: '11px', color: 'var(--verdict-strong)' }}>⚑ flagged ✓</span>}
      </div>

      {flagging && flagContext && (
        <FlagPopover paper={paper} ctx={flagContext} onDone={() => { setFlagging(false); setFlagged(true) }} />
      )}

      {paper.quote && (
        <blockquote className="prose" style={{
          margin: '6px 0 4px', paddingLeft: '10px', borderLeft: '2px solid #d1d5db',
          color: 'var(--text-body)', fontStyle: 'italic', fontSize: '13px', lineHeight: '1.55',
        }}>
          "{paper.quote}"
        </blockquote>
      )}

      {paper.reasoning && (
        <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>{paper.reasoning}</p>
      )}

      {paper.abstract && (
        <div>
          <button
            onClick={() => setShowAbstract(!showAbstract)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', fontSize: '11px',
              color: 'var(--oxford)', padding: '2px 0', textDecoration: 'underline',
            }}
          >
            {showAbstract ? 'Hide abstract' : 'Show abstract'}
          </button>
          {showAbstract && (
            <p className="prose" style={{ margin: '6px 0 0', fontSize: '13px', color: 'var(--text-body)' }}>
              {paper.abstract}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export function EvidencePanel({ evidence, claimText, flagContext }: EvidencePanelProps) {
  const claims = Object.values(evidence)
  if (claims.length === 0) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
      {claims.map((claim) => {
        const papers = mergePapers(claim)
        const stmt = claimText?.[claim.claim_id]
        const novelty = claim.novelty_flag && claim.novelty_flag !== 'normal' ? claim.novelty_flag : null
        return (
          <div key={claim.claim_id}>
            {/* Claim header */}
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '10px',
              paddingBottom: '8px', borderBottom: '1px solid var(--border-light)', flexWrap: 'wrap',
            }}>
              <span style={{
                fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', fontWeight: 700,
                color: 'var(--oxford)', background: '#eff6ff', border: '1px solid #bfdbfe',
                padding: '1px 6px', borderRadius: '3px',
              }}>
                {claim.claim_id}
              </span>
              {claim.evidence_strength && (
                <span style={{
                  fontSize: '12px', fontWeight: 600,
                  color: STRENGTH_COLOR[claim.evidence_strength] ?? 'var(--text-muted)',
                }}>
                  {claim.evidence_strength} evidence
                </span>
              )}
              {novelty && (
                <span style={{
                  fontSize: '11px', color: 'var(--verdict-novel)', background: 'var(--verdict-novel-bg)',
                  border: '1px solid var(--verdict-novel-border)', padding: '1px 6px', borderRadius: '3px',
                }}>
                  {novelty}
                </span>
              )}
              {papers.length > 0 && (
                <span style={{ fontSize: '11px', color: 'var(--text-xs)', fontFamily: 'ui-monospace, Consolas, monospace' }}>
                  {papers.length} paper(s) classified
                </span>
              )}
            </div>

            {stmt && (
              <p className="prose" style={{ margin: '0 0 10px', color: 'var(--text-heading)', fontSize: '14px' }}>{stmt}</p>
            )}

            {claim.synthesis && (
              <p className="prose" style={{ margin: '0 0 10px', color: 'var(--text-body)', fontSize: '14px' }}>{claim.synthesis}</p>
            )}

            {claim.literature_gap && (
              <p style={{ margin: '0 0 10px', fontSize: '12px', color: 'var(--text-muted)' }}>
                <span style={{ fontWeight: 600 }}>Gap: </span>{claim.literature_gap}
              </p>
            )}

            {claim.confounders_identified && (
              <div style={{ marginBottom: '10px' }}>
                <div style={{
                  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
                  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '4px',
                }}>
                  Confounders
                </div>
                <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>
                  {claim.confounders_identified}
                </p>
              </div>
            )}

            {papers.length > 0 && (
              <div style={{ marginTop: '6px' }}>
                {papers.map((p, i) => <PaperEntry key={i} paper={p} flagContext={flagContext} />)}
              </div>
            )}
            {papers.length === 0 && (claim.retrieved_papers?.length ?? 0) > 0 && (
              <p style={{ fontSize: '11px', color: 'var(--text-xs)', fontFamily: 'ui-monospace, Consolas, monospace' }}>
                {claim.retrieved_papers.length} paper(s) retrieved, none classified
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}
