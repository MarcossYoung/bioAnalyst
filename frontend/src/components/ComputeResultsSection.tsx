import { useState } from 'react'
import type { ComputeTest, ComputeResult } from '../lib/types'

const RERCONVERGE_EFFECT_GUIDE = 'Secondary RERconverge association: absolute rate-trait correlation minus matched-control mean absolute correlation; association-only, not causal.'

const EFFECT_SIZE_GUIDES: Record<string, string> = {
  omega_foreground: 'ω > 1: positive selection · ω < 1: purifying/neutral · ω = 1: neutral evolution',
  cohens_d: 'Small ≥ 0.2 · Medium ≥ 0.5 · Large ≥ 0.8 (Cohen 1988)',
  cliffs_delta: 'Small ≥ 0.147 · Medium ≥ 0.33 · Large ≥ 0.474 (Romano 2006)',
  spearmans_rho: 'Weak < 0.3 · Moderate 0.3–0.7 · Strong > 0.7',
}

EFFECT_SIZE_GUIDES.abs_rer_trait_r_minus_control_mean_abs_r = RERCONVERGE_EFFECT_GUIDE

const SECTION_LABEL: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px',
}

function pFmt(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  if (v < 0.001) return v.toExponential(2)
  return v.toFixed(4)
}

function SigBadge({ sig }: { sig: boolean | null | undefined }) {
  if (sig === null || sig === undefined) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  return (
    <span style={{
      fontSize: '10px', fontWeight: 700, letterSpacing: '0.04em',
      padding: '1px 5px', borderRadius: '3px',
      background: sig ? '#f0fdf4' : '#f9fafb',
      border: `1px solid ${sig ? '#bbf7d0' : '#e5e7eb'}`,
      color: sig ? '#166534' : 'var(--text-muted)',
    }}>
      {sig ? 'sig' : 'ns'}
    </span>
  )
}

function formatCorrection(c: unknown): string {
  if (typeof c === 'string') return c
  if (c && typeof c === 'object') {
    const obj = c as Record<string, unknown>
    const method = obj.adjust_method ?? obj.method ?? 'correction'
    const n = obj.n_tests !== undefined ? `, n=${obj.n_tests}` : ''
    const alpha = obj.alpha !== undefined ? `, alpha=${obj.alpha}` : ''
    return `${method}${n}${alpha}`
  }
  return String(c)
}

function TestRow({ t }: { t: ComputeTest }) {
  const sig = t.significant_adjusted ?? t.significant
  const unavailable = t.available === false
  const statusCounts = t.details?.paml_status_counts as Record<string, number> | undefined
  const statusText = statusCounts
    ? Object.entries(statusCounts).map(([status, count]) => `${status}: ${count}`).join(', ')
    : null
  return (
    <tr style={{ borderBottom: '1px solid var(--border-light)', verticalAlign: 'middle' }}>
      <td style={{ padding: '6px 8px 6px 0', fontSize: '12px', fontFamily: 'ui-monospace, Consolas, monospace', color: 'var(--text-body)' }}>
        {t.test}
      </td>
      <td style={{ padding: '6px 8px', fontSize: '12px', fontFamily: 'ui-monospace, Consolas, monospace', textAlign: 'right' }}>
        {unavailable
          ? <span title={statusText ?? t.closest_alternative} style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>unavailable</span>
          : t.error ? <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>error</span> : pFmt(t.p_value)}
      </td>
      <td style={{ padding: '6px 8px', textAlign: 'center' }}>
        {!t.error && !unavailable && <SigBadge sig={sig} />}
      </td>
      <td style={{ padding: '6px 8px', fontSize: '12px', fontFamily: 'ui-monospace, Consolas, monospace', textAlign: 'right', color: 'var(--text-muted)' }}>
        {t.effect_size !== null && t.effect_size !== undefined
          ? (
            <span title={t.effect_size_name ? (EFFECT_SIZE_GUIDES[t.effect_size_name] ?? t.effect_size_label ?? undefined) : (t.effect_size_label ?? undefined)}>
              {t.effect_size.toFixed(3)}{t.effect_size_label ? ` (${t.effect_size_label})` : ''}
            </span>
          )
          : '—'}
      </td>
      <td style={{ padding: '6px 0', fontSize: '12px', fontFamily: 'ui-monospace, Consolas, monospace', color: 'var(--text-muted)', textAlign: 'right' }}>
        {t.ci_lower !== null && t.ci_lower !== undefined && t.ci_upper !== null && t.ci_upper !== undefined
          ? `[${t.ci_lower.toFixed(3)}, ${t.ci_upper.toFixed(3)}]`
          : '—'}
      </td>
    </tr>
  )
}

interface ComputeResultsSectionProps {
  result: ComputeResult | null
  progressiveTests?: ComputeTest[]
  correctionsApplied?: Array<string | Record<string, unknown>>
}

export function ComputeResultsSection({ result, progressiveTests = [], correctionsApplied = [] }: ComputeResultsSectionProps) {
  const [hideNonSig, setHideNonSig] = useState(false)

  const tests: ComputeTest[] = result?.tests ?? progressiveTests
  const corrections = result?.corrections_applied ?? correctionsApplied
  const correctionLabels = corrections.map(formatCorrection)

  const visible = hideNonSig
    ? tests.filter((t) => (t.significant_adjusted ?? t.significant) === true)
    : tests

  if (tests.length === 0) {
    return <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Computing…</p>
  }

  return (
    <div style={{
      background: 'var(--compute-bg)', border: '1px solid #c7d7fb',
      borderRadius: '6px', padding: '16px 20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
        <div style={{ ...SECTION_LABEL, marginBottom: 0 }}>Test results</div>
        <span style={{
          fontSize: '10px', fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase',
          padding: '1px 7px', borderRadius: '3px',
          background: 'var(--compute-badge)', color: '#fff', border: 'none',
        }}>
          computed
        </span>
        {correctionLabels.length > 0 && (
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: 'auto' }}>
            correction: {correctionLabels.join(', ')}
          </span>
        )}
        <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer', marginLeft: correctionLabels.length > 0 ? '0' : 'auto' }}>
          <input
            type="checkbox"
            checked={hideNonSig}
            onChange={(e) => setHideNonSig(e.target.checked)}
            style={{ cursor: 'pointer' }}
          />
          Hide non-significant
        </label>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '2px solid var(--border)' }}>
            <th style={{ ...SECTION_LABEL, textAlign: 'left', paddingBottom: '6px', marginBottom: 0 }}>Test</th>
            <th style={{ ...SECTION_LABEL, textAlign: 'right', paddingBottom: '6px', marginBottom: 0 }}>p-value</th>
            <th style={{ ...SECTION_LABEL, textAlign: 'center', paddingBottom: '6px', marginBottom: 0 }}>Sig</th>
            <th style={{ ...SECTION_LABEL, textAlign: 'right', paddingBottom: '6px', marginBottom: 0 }}>Effect size</th>
            <th style={{ ...SECTION_LABEL, textAlign: 'right', paddingBottom: '6px', marginBottom: 0 }}>95% CI</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((t, i) => <TestRow key={`${t.test}-${i}`} t={t} />)}
        </tbody>
      </table>

      {hideNonSig && visible.length === 0 && (
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '8px' }}>
          No significant results after correction.
        </p>
      )}
    </div>
  )
}
