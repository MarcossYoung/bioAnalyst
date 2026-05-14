import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { cancelRun, connectRunWs } from '../lib/api'
import type {
  WsEvent, VerdictData, AnalystResult, ClaimEvidence, AtomicClaim, ConfirmPayload, SectionEdit,
  GeneSetExpansion, ComputeTest, ComputeResult, RobustnessResult,
} from '../lib/types'
import { EventTimeline } from '../components/EventTimeline'
import { ConfirmModal } from '../components/ConfirmModal'
import { VerdictSection } from '../components/VerdictSection'
import { EvidencePanel } from '../components/EvidencePanel'
import { GenomicPanel } from '../components/GenomicPanel'
import { GeneSetPanel } from '../components/GeneSetPanel'
import { ComputeResultsSection } from '../components/ComputeResultsSection'
import { RobustnessPanel } from '../components/RobustnessPanel'

const SECTION_LABEL: React.CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: 'var(--text-muted)',
  marginBottom: '10px',
}

const RULE: React.CSSProperties = {
  borderTop: '1px solid var(--border)',
  margin: '36px 0',
}

export function RunPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()

  const [events, setEvents] = useState<WsEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [done, setDone] = useState(false)
  const [confirmPayload, setConfirmPayload] = useState<ConfirmPayload | null>(null)
  const [verdict, setVerdict] = useState<VerdictData | null>(null)
  const [evidence, setEvidence] = useState<Record<string, unknown>>({})
  const [formalized, setFormalized] = useState<Record<string, unknown> | null>(null)
  const [analyst, setAnalyst] = useState<AnalystResult | null>(null)
  const [tokenCost, setTokenCost] = useState<number | null>(null)
  // v6 progressive state
  const [geneSetExpansion, setGeneSetExpansion] = useState<GeneSetExpansion | null>(null)
  const [computeTests, setComputeTests] = useState<ComputeTest[]>([])
  const [computeResult, setComputeResult] = useState<ComputeResult | null>(null)
  const [robustness, setRobustness] = useState<RobustnessResult | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const logBottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!runId) return
    const ws = connectRunWs(
      runId,
      (ev) => {
        setEvents((prev) => {
          if (prev.some((e) => e.seq === ev.seq)) return prev
          return [...prev, ev]
        })
        handleEvent(ev)
      },
      () => {
        setConnected(false)
        setDone(true)
      },
    )
    ws.onopen = () => setConnected(true)
    wsRef.current = ws
    return () => ws.close()
  }, [runId])

  useEffect(() => {
    logBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  function handleEvent(ev: WsEvent) {
    if (ev.type === 'confirmation_required') {
      setConfirmPayload(ev.payload as unknown as ConfirmPayload)
    }
    if (ev.type === 'run_completed') {
      const p = ev.payload as Record<string, unknown>
      setVerdict(p.verdict as VerdictData)
      setEvidence((p.evidence as Record<string, unknown>) ?? {})
      setFormalized(p.formalized as Record<string, unknown>)
      const a = (p.analyst as AnalystResult) ?? null
      setAnalyst(a)
      // restore v6 state from DB replay
      if (a?.expansion) setGeneSetExpansion(a.expansion as GeneSetExpansion)
      if (a?.compute_results) {
        setComputeResult(a.compute_results as ComputeResult)
        setComputeTests((a.compute_results as ComputeResult).tests ?? [])
      }
      if (a?.robustness) setRobustness(a.robustness as RobustnessResult)
      setDone(true)
    }
    if (ev.type === 'run_failed' || ev.type === 'run_aborted') {
      setDone(true)
    }
    if (ev.type === 'token_update') {
      setTokenCost(Number((ev.payload as { cost_estimate: number }).cost_estimate))
    }
    if (ev.type === 'verdict_ready') {
      const p = ev.payload as { verdict: VerdictData['verdict']; scores: VerdictData['scores'] }
      setVerdict((prev) => prev ?? ({ verdict: p.verdict, scores: p.scores } as VerdictData))
    }
    // v6 progressive events
    if (ev.type === 'gene_sets_expanded') {
      setGeneSetExpansion(ev.payload as unknown as GeneSetExpansion)
    }
    if (ev.type === 'compute_test_complete') {
      setComputeTests((prev) => [...prev, ev.payload as unknown as ComputeTest])
    }
    if (ev.type === 'compute_robustness_complete') {
      setRobustness(ev.payload as unknown as RobustnessResult)
    }
  }

  function sendWs(msg: Record<string, unknown>) {
    wsRef.current?.send(JSON.stringify(msg))
  }

  function handleSubmitConfirm(edits: Record<string, SectionEdit>) {
    sendWs({ type: 'confirm_sections', edits })
    setConfirmPayload(null)
  }
  function handleAbort() {
    sendWs({ type: 'abort_run' })
    setConfirmPayload(null)
  }

  async function handleCancel() {
    if (!runId) return
    await cancelRun(runId)
  }

  const claimEvidence = (evidence.claim_evidence ?? {}) as Record<string, ClaimEvidence>
  const hasEvidence = Object.keys(claimEvidence).length > 0
  const claimText: Record<string, string> = Object.fromEntries(
    (((formalized?.atomic_claims as AtomicClaim[]) ?? []).map((c) => [c.id, c.statement])),
  )
  const flagsApplied = Number((evidence.flags_applied as number) ?? 0)
  const fmt = (formalized ?? {}) as Record<string, unknown>
  const flagContext = done && formalized
    ? {
        hypothesis: String(fmt.core_hypothesis ?? ''),
        domain: String(fmt.domain ?? 'unknown'),
        entities: [...((fmt.key_entities as string[]) ?? []), ...((fmt.starter_entities as string[]) ?? [])],
      }
    : undefined

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg)' }}>

      {/* Header */}
      <header style={{
        background: '#0f172a',
        borderBottom: '1px solid #1e293b',
        padding: '9px 20px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <button
            onClick={() => navigate('/')}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#475569', fontSize: '16px', lineHeight: 1, padding: 0 }}
          >
            ←
          </button>
          <span style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '11px',
            color: '#64748b',
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
          }}>
            Nullifier
          </span>
          <span style={{
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: '12px',
            color: '#94a3b8',
          }}>
            run/{runId}
          </span>
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '5px',
            fontSize: '11px',
            fontFamily: 'ui-monospace, Consolas, monospace',
            color: connected ? '#4ade80' : '#475569',
          }}>
            <span style={{
              width: '6px', height: '6px', borderRadius: '50%',
              background: connected ? '#4ade80' : '#475569',
              display: 'inline-block',
              flexShrink: 0,
            }} />
            {connected ? 'live' : 'closed'}
          </span>
          {tokenCost !== null && (
            <span style={{
              fontFamily: 'ui-monospace, Consolas, monospace',
              fontSize: '11px',
              color: '#64748b',
            }}>
              ~${tokenCost.toFixed(4)}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {done && hasEvidence && (
            <button
              onClick={() => navigate(`/runs/${runId}/review`)}
              style={{
                background: 'none', border: '1px solid #374151', borderRadius: '3px', cursor: 'pointer',
                fontSize: '11px', color: '#cbd5e1', padding: '3px 10px',
                fontFamily: 'ui-monospace, Consolas, monospace', letterSpacing: '0.04em',
              }}
            >
              review classifications
            </button>
          )}
          {!done && (
            <button
              onClick={handleCancel}
              style={{
                background: 'none', border: '1px solid #374151', borderRadius: '3px', cursor: 'pointer',
                fontSize: '11px', color: '#ef4444', padding: '3px 10px',
                fontFamily: 'ui-monospace, Consolas, monospace', letterSpacing: '0.04em',
              }}
            >
              cancel
            </button>
          )}
        </div>
      </header>

      {/* Two-pane layout */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Left — event log sidebar */}
        <aside style={{
          width: '220px',
          flexShrink: 0,
          borderRight: '1px solid #1e293b',
          background: '#0f172a',
          overflowY: 'auto',
          padding: '12px 10px',
        }}>
          <div style={{
            fontSize: '9px',
            fontWeight: 600,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: '#334155',
            marginBottom: '10px',
          }}>
            Event stream
          </div>
          <EventTimeline events={events} />
          <div ref={logBottomRef} />
        </aside>

        {/* Right — scrolling document */}
        <main style={{ flex: 1, overflowY: 'auto', padding: '36px 48px', maxWidth: '820px' }}>

          {/* Hypothesis */}
          {formalized && (
            <div>
              <div style={SECTION_LABEL}>Core hypothesis</div>
              <div style={{
                borderLeft: '3px solid var(--oxford)',
                paddingLeft: '16px',
              }}>
                <p className="prose" style={{ margin: '0 0 12px', color: 'var(--text-heading)', fontSize: '16px' }}>
                  {String((formalized as Record<string, unknown>).core_hypothesis ?? '')}
                </p>
                {((formalized as Record<string, unknown>).key_entities as string[] ?? []).length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                    {((formalized as Record<string, unknown>).key_entities as string[]).map((e) => (
                      <span key={e} style={{
                        fontSize: '11px',
                        fontFamily: 'ui-monospace, Consolas, monospace',
                        background: '#eff6ff',
                        border: '1px solid #bfdbfe',
                        color: '#1e40af',
                        padding: '1px 6px',
                        borderRadius: '3px',
                      }}>
                        {e}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Running indicator */}
          {!hasEvidence && !done && (
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '80px 0',
              gap: '14px',
              color: 'var(--text-muted)',
            }}>
              <svg
                className="animate-spin"
                style={{ width: '24px', height: '24px', color: 'var(--oxford)' }}
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle style={{ opacity: 0.25 }} cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path style={{ opacity: 0.75 }} fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              <span style={{ fontSize: '13px' }}>Pipeline running — watch the event stream</span>
            </div>
          )}

          {/* Evidence */}
          {hasEvidence && (
            <>
              <div style={RULE} />
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px' }}>
                <div style={{ ...SECTION_LABEL, marginBottom: 0 }}>Evidence by claim</div>
                {flagsApplied > 0 && (
                  <span style={{
                    fontSize: '11px', color: 'var(--verdict-novel)', background: 'var(--verdict-novel-bg)',
                    border: '1px solid var(--verdict-novel-border)', padding: '1px 7px', borderRadius: '3px',
                  }}>
                    {flagsApplied} past correction{flagsApplied === 1 ? '' : 's'} applied to this run
                  </span>
                )}
              </div>
              <EvidencePanel evidence={claimEvidence} claimText={claimText} flagContext={flagContext} />
            </>
          )}

          {/* Genomic */}
          {analyst && !analyst.skipped && (
            <>
              <div style={RULE} />
              <div style={SECTION_LABEL}>Genomic analysis</div>
              <GenomicPanel analyst={analyst} />
            </>
          )}
          {analyst && analyst.skipped && (
            <>
              <div style={RULE} />
              <div style={SECTION_LABEL}>Genomic analysis</div>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                Skipped{analyst.reason ? ` — ${analyst.reason}` : ''}.
              </p>
            </>
          )}

          {/* v6: Gene-set expansion */}
          {geneSetExpansion && (
            <>
              <div style={RULE} />
              <div style={SECTION_LABEL}>Gene-set expansion</div>
              <GeneSetPanel expansion={geneSetExpansion} />
            </>
          )}

          {/* v6: Statistical analysis (compute layer) */}
          {(computeTests.length > 0 || computeResult) && (
            <>
              <div style={RULE} />
              <div style={SECTION_LABEL}>Statistical analysis</div>
              <ComputeResultsSection result={computeResult} progressiveTests={computeTests} />
            </>
          )}

          {/* v6: Robustness */}
          {robustness && (
            <>
              <div style={RULE} />
              <RobustnessPanel robustness={robustness} />
            </>
          )}

          {/* Verdict */}
          {verdict && (
            <>
              <div style={RULE} />
              <VerdictSection verdict={verdict} analyst={analyst} />
            </>
          )}

        </main>
      </div>

      {confirmPayload && (
        <ConfirmModal
          payload={confirmPayload}
          onSubmit={handleSubmitConfirm}
          onAbort={handleAbort}
        />
      )}
    </div>
  )
}
