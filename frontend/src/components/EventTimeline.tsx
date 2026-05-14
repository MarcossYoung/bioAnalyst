import type { WsEvent } from '../lib/types'
import { formatTime } from '../lib/utils'

interface EventTimelineProps {
  events: WsEvent[]
}

function eventLabel(ev: WsEvent): string {
  const p = ev.payload as Record<string, unknown>
  switch (ev.type) {
    case 'run_started':           return 'run started'
    case 'stage_started':         return String(p.label ?? p.stage)
    case 'stage_completed':       return `${p.stage} — done`
    case 'hypothesis_extracted':  return `domain: ${p.domain}`
    case 'confirmation_required': return 'awaiting confirmation'
    case 'confirmation_received': return `confirmed (${p.action})`
    case 'claims_formalized':     return `${p.claim_count} claim(s) identified`
    case 'queries_expanded':      return `[${p.claim_id}] queries expanded`
    case 'papers_retrieved':      return `[${p.claim_id}] ${p.paper_count} papers`
    case 'paper_classified':      return `  ${p.classification} — ${String(p.paper_title ?? '').slice(0, 48)}`
    case 'synthesis_ready':       return `[${p.claim_id}] ${p.evidence_strength}`
    case 'analyst_started':       return `analyst: ${p.gene_count} gene(s)`
    case 'analyst_gene_fetched':  return `  ${p.gene} — ${p.status}`
    case 'analyst_ready':         return `genomic: ${p.overall_genomic_assessment}`
    case 'analyst_skipped':       return `analyst skipped (${p.reason})`
    case 'gene_sets_expanded':    return `gene sets: ${p.total_expanded} expanded, ${p.total_controls} controls`
    case 'methodologist_plan_complete': return `methodologist: ${p.test_count} tests (${p.correction})`
    case 'compute_start':         return `computing ${p.test_count} statistical tests…`
    case 'compute_test_complete': {
      const pv = typeof p.p_value === 'number' ? p.p_value.toFixed(4) : '?'
      return `  ${p.test}: p=${pv} (${p.significant ? 'sig' : 'ns'})`
    }
    case 'compute_all_complete':  return `compute done: ${p.test_count} tests`
    case 'compute_robustness_start': return `robustness: ${p.n_perturbations} perturbations`
    case 'compute_robustness_complete': return `robustness: ${p.stability} (${Math.round(Number(p.agreement_fraction) * 100)}%)`
    case 'interpreter_start':     return `interpreter reading results…`
    case 'interpreter_complete':  return `interpreter: ${p.overall_assessment}`
    case 'verdict_ready':         return `verdict: ${p.verdict}`
    case 'token_update':          return `cost: $${Number(p.cost_estimate).toFixed(4)}`
    case 'run_completed':         return 'run completed'
    case 'run_failed':            return `error: ${p.error}`
    case 'run_aborted':           return 'aborted'
    default:                      return ev.type
  }
}

function rowColor(type: string): string {
  if (type === 'run_failed')    return '#fca5a5'
  if (type === 'run_aborted')   return '#94a3b8'
  if (type === 'run_completed') return '#86efac'
  if (type === 'verdict_ready') return '#fcd34d'
  if (type.includes('analyst')) return '#67e8f9'
  if (type === 'paper_classified' || type === 'analyst_gene_fetched' || type === 'compute_test_complete') return '#475569'
  if (type.startsWith('compute_') || type === 'gene_sets_expanded' || type === 'methodologist_plan_complete') return '#a5b4fc'
  if (type.startsWith('interpreter')) return '#67e8f9'
  return '#cbd5e1'
}

export function EventTimeline({ events }: EventTimelineProps) {
  return (
    <div className="log-mono" style={{ color: '#cbd5e1' }}>
      {events.map((ev) => (
        <div
          key={ev.seq}
          style={{
            display: 'grid',
            gridTemplateColumns: '64px 1fr',
            gap: '6px',
            padding: '1px 0',
            color: rowColor(ev.type),
          }}
        >
          <span style={{ color: '#475569', userSelect: 'none' }}>
            {formatTime(ev.ts)}
          </span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {eventLabel(ev)}
          </span>
        </div>
      ))}
    </div>
  )
}
