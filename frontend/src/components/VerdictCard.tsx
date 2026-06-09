import type { VerdictData } from '../lib/types'
import { VERDICT_STYLE, scoreColor } from '../lib/utils'

interface VerdictCardProps {
  verdict: VerdictData
  mode?: 'hypothesis' | 'analysis'
}

const SECTION_LABEL: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px',
}

const CALIBRATION_NOTE: React.CSSProperties = {
  fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic', marginTop: '8px',
}

const CORE_SCORE_ROWS: [keyof VerdictData['scores'], string][] = [
  ['overall_falsifiability_score', 'Overall falsifiability'],
  ['statistical_robustness',       'Statistical robustness'],
  ['literature_consensus',         'Literature consensus'],
  ['mechanistic_plausibility',     'Mechanistic plausibility'],
  ['counter_explanation_risk',     'Survives alternatives'],
  ['novelty_adjusted_confidence',  'Novelty-adjusted confidence'],
  ['genomic_evidence_alignment',   'Genomic alignment'],
]

const CRITIQUE_SCORE_ROWS: [keyof VerdictData['scores'], string][] = [
  ['methods_critique_score',        'Methods rigor'],
  ['statistical_critique_score',    'Statistical rigor'],
  ['reproducibility_score',         'Reproducibility'],
  ['interpretation_critique_score', 'Interpretation calibration'],
]

export function VerdictCard({ verdict, mode = 'hypothesis' }: VerdictCardProps) {
  const style = VERDICT_STYLE[verdict.verdict] ?? VERDICT_STYLE['WEAK']
  const alts = verdict.top_alternative_explanations ?? []
  const scoreRows = mode === 'analysis' ? CRITIQUE_SCORE_ROWS : CORE_SCORE_ROWS
  const hasScores = scoreRows.some(([key]) => verdict.scores?.[key] !== undefined)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {mode === 'hypothesis' && (
        <div>
          <div style={{ ...SECTION_LABEL, marginBottom: '6px' }}>Verdict</div>
          <div style={{
            display: 'inline-block', padding: '4px 12px', border: `1px solid ${style.border}`,
            background: style.bg, color: style.color, borderRadius: '4px', fontWeight: 700,
            fontSize: '15px', letterSpacing: '0.04em',
          }}>
            {verdict.verdict}
          </div>
          {verdict.verdict_justification && (
            <p className="prose" style={{ margin: '10px 0 0', fontSize: '14px' }}>{verdict.verdict_justification}</p>
          )}
        </div>
      )}

      {hasScores && (
        <div>
          <div style={SECTION_LABEL}>Scores</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontVariantNumeric: 'tabular-nums' }}>
            <tbody>
              {scoreRows.map(([key, label]) => {
                const val = verdict.scores?.[key]
                if (val === undefined) return null
                const isNA = val === null
                return (
                  <tr key={key} style={{ borderBottom: '1px solid var(--border-light)' }}>
                    <td style={{ padding: '5px 0', color: 'var(--text-body)', fontSize: '13px' }}>{label}</td>
                    <td style={{
                      padding: '5px 0', textAlign: 'right', fontFamily: 'ui-monospace, Consolas, monospace',
                      fontSize: '13px', color: isNA ? 'var(--text-muted)' : scoreColor(Number(val)), fontWeight: 600,
                    }}>
                      {isNA ? '—' : `${Number(val).toFixed(1)} / 10`}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p style={CALIBRATION_NOTE}>Heuristic score, not a calibrated probability.</p>
        </div>
      )}

      {mode === 'hypothesis' && alts.length > 0 && (
        <div>
          <div style={SECTION_LABEL}>Top alternative explanations</div>
          <ol style={{ margin: 0, paddingLeft: '18px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {alts.map((a, i) => (
              <li key={i} className="prose" style={{ fontSize: '13px' }}>
                <span style={{ fontWeight: 600 }}>{a.explanation}</span>
                {a.plausibility && (
                  <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', fontSize: '11px', color: 'var(--text-muted)' }}> ({a.plausibility})</span>
                )}
                {a.why && <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{a.why}</div>}
                {a.how_to_rule_out && <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontStyle: 'italic' }}>Rule out: {a.how_to_rule_out}</div>}
              </li>
            ))}
          </ol>
        </div>
      )}

      {mode === 'hypothesis' && verdict.decisive_experiment && (
        <div>
          <div style={SECTION_LABEL}>Decisive experiment</div>
          <p className="prose" style={{ margin: 0, fontSize: '13px' }}>{verdict.decisive_experiment}</p>
        </div>
      )}

      {mode === 'hypothesis' && verdict.librarian_sanity_check && (
        <div>
          <div style={SECTION_LABEL}>Skeptic's sanity-check of the Librarian</div>
          <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>{verdict.librarian_sanity_check}</p>
        </div>
      )}
    </div>
  )
}
