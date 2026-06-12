import { useState, useEffect } from 'react'
import type { WsEvent } from '../lib/types'
import { formatTime } from '../lib/utils'

// ── Stage definitions ────────────────────────────────────────────────────────

const STAGE_KEY: Record<string, string> = {
  run_started: 'meta', stage_started: 'meta', stage_completed: 'meta',
  token_update: 'meta', run_completed: 'meta', run_failed: 'meta', run_aborted: 'meta',
  hypothesis_extracted: 'formalizer', confirmation_required: 'formalizer',
  confirmation_received: 'formalizer', formalizer_detected_completed_analysis: 'formalizer',
  claims_formalized: 'formalizer',
  queries_expanded: 'librarian', papers_retrieved: 'librarian',
  paper_classified: 'librarian', synthesis_ready: 'librarian',
  classifier_degraded: 'librarian',
  analyst_started: 'analyst', analyst_comparability_screen: 'analyst',
  analyst_gene_fetched: 'analyst',
  analyst_symbol_resolved: 'analyst', analyst_phylo_loaded: 'analyst',
  analyst_gnomad_fetched: 'analyst', analyst_paml_complete: 'analyst',
  analyst_rdnds_complete: 'analyst',
  'paml.gene_started': 'analyst', 'paml.gene_complete': 'analyst',
  'paml.gene_timeout': 'analyst', 'rdnds.gene_started': 'analyst',
  'rdnds.gene_complete': 'analyst', 'ensembl.batch_progress': 'analyst',
  analyst_ready: 'analyst', analyst_skipped: 'analyst',
  analyst_reproducibility_check_start: 'analyst', analyst_reproducibility_check_complete: 'analyst',
  gene_sets_expanded: 'methodologist', methodologist_plan_complete: 'methodologist',
  compute_start: 'compute', compute_test_complete: 'compute', compute_all_complete: 'compute',
  compute_robustness_start: 'robustness', compute_robustness_complete: 'robustness',
  interpreter_start: 'interpreter', interpreter_complete: 'interpreter',
  skeptic_critique_mode_active: 'skeptic', verdict_ready: 'skeptic',
}

const STAGE_ORDER = [
  'meta', 'formalizer', 'librarian', 'analyst',
  'methodologist', 'compute', 'robustness', 'interpreter', 'skeptic',
]

const STAGE_LABEL: Record<string, string> = {
  meta: 'Run', formalizer: 'Formalizer', librarian: 'Librarian',
  analyst: 'Analyst', methodologist: 'Methodologist', compute: 'Compute',
  robustness: 'Robustness', interpreter: 'Interpreter', skeptic: 'Skeptic',
}

const STAGE_COLOR: Record<string, string> = {
  meta: '#cbd5e1', formalizer: '#cbd5e1', librarian: '#cbd5e1',
  analyst: '#67e8f9', methodologist: '#a5b4fc', compute: '#a5b4fc',
  robustness: '#a5b4fc', interpreter: '#67e8f9', skeptic: '#fcd34d',
}

const STAGE_TERMINALS: Record<string, string[]> = {
  formalizer: ['claims_formalized'],
  librarian: ['synthesis_ready'],
  analyst: ['analyst_ready', 'analyst_skipped'],
  methodologist: ['methodologist_plan_complete'],
  compute: ['compute_all_complete'],
  robustness: ['compute_robustness_complete'],
  interpreter: ['interpreter_complete'],
  skeptic: ['verdict_ready'],
  meta: ['run_completed', 'run_failed', 'run_aborted'],
}

function isDone(stage: string, eventTypes: Set<string>): boolean {
  return (STAGE_TERMINALS[stage] ?? []).some(t => eventTypes.has(t))
}

// ── Event label / color (unchanged logic) ────────────────────────────────────

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
    case 'classifier_degraded':   return `[${p.claim_id}] classifier degraded (${p.dropped}/${p.retrieved} dropped)`
    case 'analyst_comparability_screen': return `screened ${p.total} -> ${p.kept} comparable (${p.dropped} dropped)`
    case 'analyst_started':       return `analyst: ${p.gene_count} gene(s)`
    case 'analyst_gene_fetched':  return `  ${p.gene} — ${p.status}`
    case 'ensembl.batch_progress': return `Ensembl batch: ${p.fetched} / ${p.total}`
    case 'paml.gene_started':     return `PAML: ${p.gene} (${p.foreground})`
    case 'paml.gene_complete':    return `PAML: ${p.gene} ω=${typeof p.omega_foreground === 'number' ? p.omega_foreground.toFixed(3) : 'n/a'}`
    case 'paml.gene_timeout':     return `PAML timeout: ${p.gene}`
    case 'rdnds.gene_started':    return `R dN/dS: ${p.gene}`
    case 'rdnds.gene_complete':   return `R dN/dS: ${p.gene} (${p.species_count} species)`
    case 'analyst_rdnds_complete': return `R dN/dS: ${p.genes_with_dnds}/${p.total} genes, ${p.orthologs_attached} orthologs`
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
  if (type.includes('analyst') || type.startsWith('paml.') || type.startsWith('rdnds.') || type === 'ensembl.batch_progress') return '#67e8f9'
  if (type === 'classifier_degraded') return '#fca5a5'
  if (type === 'paper_classified' || type === 'analyst_gene_fetched' || type === 'compute_test_complete') return '#475569'
  if (type.startsWith('compute_') || type === 'gene_sets_expanded' || type === 'methodologist_plan_complete') return '#a5b4fc'
  if (type.startsWith('interpreter')) return '#67e8f9'
  return '#cbd5e1'
}

// ── Component ────────────────────────────────────────────────────────────────

interface EventTimelineProps {
  events: WsEvent[]
}

export function EventTimeline({ events }: EventTimelineProps) {
  // Group events by stage
  const groups = new Map<string, WsEvent[]>()
  for (const ev of events) {
    const stage = STAGE_KEY[ev.type] ?? 'meta'
    if (!groups.has(stage)) groups.set(stage, [])
    groups.get(stage)!.push(ev)
  }

  const seenTypes = new Set(events.map(e => e.type))

  // Collapsed state: auto-collapse completed stages
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    const init = new Set<string>()
    for (const stage of STAGE_ORDER) {
      if (isDone(stage, seenTypes)) init.add(stage)
    }
    return init
  })

  useEffect(() => {
    setCollapsed(prev => {
      const next = new Set(prev)
      for (const stage of STAGE_ORDER) {
        if (!next.has(stage) && isDone(stage, seenTypes)) next.add(stage)
      }
      return next
    })
  }, [events])  // eslint-disable-line react-hooks/exhaustive-deps

  function toggle(stage: string) {
    setCollapsed(prev => {
      const next = new Set(prev)
      next.has(stage) ? next.delete(stage) : next.add(stage)
      return next
    })
  }

  const activeStages = STAGE_ORDER.filter(s => groups.has(s))

  return (
    <div className="log-mono" style={{ color: '#cbd5e1' }}>
      {activeStages.map(stage => {
        const stageEvents = groups.get(stage)!
        const isCollapsed = collapsed.has(stage)
        const done = isDone(stage, seenTypes)
        const accent = STAGE_COLOR[stage]

        return (
          <div key={stage} style={{ marginBottom: '2px' }}>
            {/* Section header */}
            <button
              onClick={() => toggle(stage)}
              style={{
                display: 'grid',
                gridTemplateColumns: '14px 1fr auto',
                gap: '4px',
                width: '100%',
                background: 'none',
                border: 'none',
                padding: '3px 0',
                cursor: 'pointer',
                textAlign: 'left',
                opacity: isCollapsed && done ? 0.55 : 1,
              }}
            >
              <span style={{ color: 'var(--oxford, #1e3a5f)', fontSize: '10px', lineHeight: '14px' }}>
                {isCollapsed ? '▶' : '▼'}
              </span>
              <span style={{ color: accent, fontSize: '11px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {STAGE_LABEL[stage]}
              </span>
              <span style={{ color: '#475569', fontSize: '10px', userSelect: 'none', whiteSpace: 'nowrap' }}>
                {stageEvents.length}
              </span>
            </button>

            {/* Events */}
            {!isCollapsed && stageEvents.map(ev => (
              <div
                key={ev.seq}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '64px 1fr',
                  gap: '6px',
                  padding: '1px 0 1px 14px',
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
      })}
    </div>
  )
}
