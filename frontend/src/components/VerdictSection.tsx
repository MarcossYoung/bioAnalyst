import type { VerdictData, AnalystResult } from '../lib/types'
import { VERDICT_STYLE } from '../lib/utils'
import { VerdictCard } from './VerdictCard'
import { CritiqueSections } from './CritiquePanels'

const CARD_LABEL: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '10px',
}

interface VerdictSectionProps {
  verdict: VerdictData
  analyst?: AnalystResult | null
}

function hasCritique(v: VerdictData): boolean {
  return !!(v.methods_critique || v.statistical_critique || v.interpretation_critique || v.reproducibility_check)
}

/** When the Skeptic overrode to RESULTS-PROBLEMATIC, derive the underlying hypothesis
 *  verdict from overall_falsifiability_score so the left card is still meaningful. */
function deriveHypothesisVerdict(v: VerdictData): VerdictData {
  if (v.verdict !== 'RESULTS-PROBLEMATIC') return v
  const score = v.scores?.overall_falsifiability_score ?? 5
  const derived: VerdictData['verdict'] =
    score >= 7 ? 'STRONG' :
    score >= 5 ? 'MODERATE' :
    score >= 3 ? 'WEAK' : 'FALSIFIED'
  return { ...v, verdict: derived }
}

/** Analysis card — critique scores + critique panels, with a distinct left border. */
function AnalysisCard({ verdict, analyst }: { verdict: VerdictData; analyst?: AnalystResult | null }) {
  const probStyle = VERDICT_STYLE['RESULTS-PROBLEMATIC'] ?? VERDICT_STYLE['WEAK']
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div>
        <div style={{ ...CARD_LABEL, marginBottom: '6px' }}>Completed-analysis critique</div>
        {verdict.verdict === 'RESULTS-PROBLEMATIC' && (
          <div style={{
            display: 'inline-block', padding: '4px 12px',
            border: `1px solid ${probStyle.border}`, background: probStyle.bg, color: probStyle.color,
            borderRadius: '4px', fontWeight: 700, fontSize: '13px', letterSpacing: '0.04em',
            marginBottom: '8px',
          }}>
            RESULTS-PROBLEMATIC
          </div>
        )}
      </div>
      <VerdictCard verdict={verdict} mode="analysis" />
      <CritiqueSections verdict={verdict} analyst={analyst} inline />
    </div>
  )
}

export function VerdictSection({ verdict, analyst }: VerdictSectionProps) {
  const twoVerdict = hasCritique(verdict)

  if (!twoVerdict) {
    return (
      <>
        <VerdictCard verdict={verdict} />
        <CritiqueSections verdict={verdict} analyst={analyst} />
      </>
    )
  }

  const hypothesisVerdict = deriveHypothesisVerdict(verdict)

  return (
    <div style={{ display: 'flex', gap: '24px', alignItems: 'flex-start' }}>
      <div style={{ flex: 1 }}>
        <div style={CARD_LABEL}>Hypothesis verdict</div>
        <VerdictCard verdict={hypothesisVerdict} mode="hypothesis" />
      </div>
      <div style={{
        flex: 1,
        borderLeft: '3px solid var(--verdict-problematic-border)',
        paddingLeft: '20px',
      }}>
        <AnalysisCard verdict={verdict} analyst={analyst} />
      </div>
    </div>
  )
}
