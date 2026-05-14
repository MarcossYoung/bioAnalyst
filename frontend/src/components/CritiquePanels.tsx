import type { VerdictData, AnalystResult, Critique } from '../lib/types'
import { SEVERITY_STYLE } from '../lib/utils'

const SECTION_LABEL: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '10px',
}

function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY_STYLE[severity] ?? { color: 'var(--text-muted)', bg: 'var(--surface)', border: 'var(--border)' }
  return (
    <span style={{
      fontSize: '10px', fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase',
      padding: '1px 7px', borderRadius: '3px', border: `1px solid ${s.border}`, background: s.bg, color: s.color,
    }}>
      {severity} severity
    </span>
  )
}

export function CritiquePanel({ title, critique }: { title: string; critique: Critique }) {
  return (
    <div style={{ borderLeft: '3px solid var(--verdict-problematic-border)', paddingLeft: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '8px', flexWrap: 'wrap' }}>
        <span style={{ ...SECTION_LABEL, marginBottom: 0, color: 'var(--text-heading)' }}>{title}</span>
        <SeverityBadge severity={critique.severity} />
      </div>
      {(critique.issues?.length ?? 0) > 0 && (
        <ul style={{ margin: '0 0 6px', paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {critique.issues.map((iss, i) => (
            <li key={i} className="prose" style={{ fontSize: '13px', color: 'var(--text-body)' }}>{iss}</li>
          ))}
        </ul>
      )}
      {critique.notes && <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>{critique.notes}</p>}
    </div>
  )
}

function ReproducibilityTable({ analyst, critique }: { analyst?: AnalystResult | null; critique?: Critique }) {
  const items = analyst?.interpretation?.reproducibility_check ?? []
  const notVerifiable = analyst?.reproducibility?.not_verifiable_here ?? []
  if (items.length === 0 && !critique && notVerifiable.length === 0) return null
  return (
    <div style={{ borderLeft: '3px solid var(--verdict-problematic-border)', paddingLeft: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '8px', flexWrap: 'wrap' }}>
        <span style={{ ...SECTION_LABEL, marginBottom: 0, color: 'var(--text-heading)' }}>Reproducibility check</span>
        {critique && <SeverityBadge severity={critique.severity} />}
      </div>

      {items.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px', marginBottom: '8px' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-muted)' }}>
              <th style={{ padding: '4px 8px 4px 0', fontWeight: 600 }}>Reported</th>
              <th style={{ padding: '4px 8px', fontWeight: 600 }}>Ensembl value</th>
              <th style={{ padding: '4px 8px', fontWeight: 600 }}>Checkable here?</th>
              <th style={{ padding: '4px 0', fontWeight: 600 }}>Note</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border-light)', verticalAlign: 'top' }}>
                <td style={{ padding: '5px 8px 5px 0' }}>{it.reported}</td>
                <td style={{ padding: '5px 8px', fontFamily: 'ui-monospace, Consolas, monospace' }}>{it.ensembl_value}</td>
                <td style={{ padding: '5px 8px', color: it.verifiable ? 'var(--verdict-strong)' : 'var(--text-muted)', fontWeight: 600 }}>
                  {it.verifiable ? 'yes' : 'no'}
                </td>
                <td style={{ padding: '5px 0', color: 'var(--text-muted)' }}>{it.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {notVerifiable.length > 0 && (
        <p style={{ margin: '0 0 6px', fontSize: '12px', color: 'var(--text-muted)' }}>
          <span style={{ fontWeight: 600 }}>Not verifiable from Ensembl here:</span> {notVerifiable.join('; ')}
        </p>
      )}

      {critique && (critique.issues?.length ?? 0) > 0 && (
        <ul style={{ margin: '0 0 6px', paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {critique.issues.map((iss, i) => <li key={i} className="prose" style={{ fontSize: '13px' }}>{iss}</li>)}
        </ul>
      )}
      {critique?.notes && <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-muted)' }}>{critique.notes}</p>}
    </div>
  )
}

const RULE: React.CSSProperties = { borderTop: '1px solid var(--border)', margin: '36px 0' }

/** Renders the four conditional critique panels (only when their data is present).
 *  Pass inline=true when embedding inside the two-verdict layout (suppresses the RULE divider). */
export function CritiqueSections({ verdict, analyst, inline }: { verdict: VerdictData; analyst?: AnalystResult | null; inline?: boolean }) {
  const hasRepro = !!verdict.reproducibility_check
    || (analyst?.interpretation?.reproducibility_check?.length ?? 0) > 0
    || (analyst?.reproducibility?.not_verifiable_here?.length ?? 0) > 0
  const any = verdict.methods_critique || verdict.statistical_critique || verdict.interpretation_critique || hasRepro
  if (!any) return null
  return (
    <>
      {!inline && <div style={RULE} />}
      {!inline && <div style={SECTION_LABEL}>Completed-analysis critique</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
        {verdict.methods_critique && <CritiquePanel title="Methods critique" critique={verdict.methods_critique} />}
        {verdict.statistical_critique && <CritiquePanel title="Statistical rigor" critique={verdict.statistical_critique} />}
        {hasRepro && <ReproducibilityTable analyst={analyst} critique={verdict.reproducibility_check} />}
        {verdict.interpretation_critique && <CritiquePanel title="Interpretation critique" critique={verdict.interpretation_critique} />}
      </div>
    </>
  )
}
