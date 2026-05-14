import type { RobustnessResult } from '../lib/types'

const SECTION_LABEL: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px',
}

function stabilityStyle(s: string): { color: string; bg: string; border: string } {
  if (s === 'stable')    return { color: 'var(--verdict-strong)',   bg: 'var(--verdict-strong-bg)',   border: 'var(--verdict-strong-border)' }
  if (s === 'sensitive') return { color: 'var(--verdict-moderate)', bg: 'var(--verdict-moderate-bg)', border: 'var(--verdict-moderate-border)' }
  if (s === 'fragile')   return { color: 'var(--verdict-falsified)',bg: 'var(--verdict-falsified-bg)',border: 'var(--verdict-falsified-border)' }
  return { color: 'var(--text-muted)', bg: 'var(--surface)', border: 'var(--border)' }
}

function Chip({ label }: { label: string }) {
  return (
    <span style={{
      fontSize: '11px', fontFamily: 'ui-monospace, Consolas, monospace',
      background: '#fff7ed', border: '1px solid #fed7aa', color: '#9a3412',
      padding: '1px 6px', borderRadius: '3px',
    }}>
      {label}
    </span>
  )
}

interface RobustnessPanelProps {
  robustness: RobustnessResult
}

export function RobustnessPanel({ robustness }: RobustnessPanelProps) {
  if (robustness.applicable === false) {
    return (
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '14px 20px' }}>
        <div style={SECTION_LABEL}>Verdict robustness</div>
        <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-muted)' }}>
          Not applicable{robustness.reason ? ` — ${robustness.reason}` : ''}.
        </p>
      </div>
    )
  }

  const st = stabilityStyle(robustness.stability)
  const pct = Math.round(robustness.agreement_fraction * 100)

  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: '6px', padding: '16px 20px',
      display: 'flex', flexDirection: 'column', gap: '12px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        <div style={{ ...SECTION_LABEL, marginBottom: 0 }}>Verdict stability</div>
        <span style={{
          fontSize: '13px', fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
          padding: '3px 12px', borderRadius: '4px',
          background: st.bg, border: `1px solid ${st.border}`, color: st.color,
        }}>
          {robustness.stability}
        </span>
        <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
          {pct}% of leave-one-out perturbations agree
        </span>
      </div>

      {robustness.most_influential_genes.length > 0 && (
        <div>
          <div style={SECTION_LABEL}>Most influential genes</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {robustness.most_influential_genes.map((g) => <Chip key={g} label={g} />)}
          </div>
          <p style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
            Removing these genes individually causes the largest shift in the verdict.
          </p>
        </div>
      )}
    </div>
  )
}
