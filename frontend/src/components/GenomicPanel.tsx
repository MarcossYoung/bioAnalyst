import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts'
import type { AnalystResult, AnalystSetStats } from '../lib/types'

interface GenomicPanelProps {
  analyst: AnalystResult
}

const ASSESS_COLOR: Record<string, string> = {
  supports: 'var(--verdict-strong)',
  contradicts: 'var(--verdict-falsified)',
  neutral: 'var(--verdict-moderate)',
  inconclusive: 'var(--verdict-moderate)',
}

function patternValue(s: string): number {
  if (s === 'yes' || s === 'supports') return 1
  if (s === 'no' || s === 'contradicts') return -1
  return 0
}

function barColor(v: number): string {
  return v > 0 ? 'var(--verdict-strong)' : v < 0 ? 'var(--verdict-falsified)' : '#9ca3af'
}

const SectionLabel = ({ children }: { children: React.ReactNode }) => (
  <div style={{
    fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
    textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px',
  }}>
    {children}
  </div>
)

const Chip = ({ children, scheme = 'blue' }: { children: React.ReactNode; scheme?: 'blue' | 'cyan' }) => {
  const c = scheme === 'cyan'
    ? { background: '#ecfeff', border: '1px solid #a5f3fc', color: '#155e75' }
    : { background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af' }
  return (
    <span style={{
      ...c, fontSize: '11px', fontFamily: 'ui-monospace, Consolas, monospace',
      padding: '1px 5px', borderRadius: '3px',
    }}>
      {children}
    </span>
  )
}

function SetStats({ stats }: { stats: AnalystSetStats }) {
  const num = (n: number | null | undefined, d = 1) => (typeof n === 'number' ? n.toFixed(d) : '—')
  return (
    <div style={{
      fontSize: '11px', color: 'var(--text-muted)',
      fontFamily: 'ui-monospace, Consolas, monospace',
      display: 'flex', flexDirection: 'column', gap: '1px',
    }}>
      <span>{stats.valid_gene_count} gene(s) · {num(stats.mean_ortholog_count)} orthologs avg</span>
      <span>{num(stats.mean_paralog_count)} paralogs · {num(stats.mean_duplication_count)} dups avg</span>
      <span>dN/dS: {stats.dnds_n > 0 && typeof stats.dnds_mean === 'number' ? stats.dnds_mean.toFixed(3) : 'n/a'}</span>
    </div>
  )
}

export function GenomicPanel({ analyst }: GenomicPanelProps) {
  const interp = analyst.interpretation
  const assessment = interp?.overall_genomic_assessment ?? 'inconclusive'
  const color = ASSESS_COLOR[assessment] ?? 'var(--verdict-moderate)'
  const jaccard = analyst.cross_set?.jaccard_index ?? interp?.regulatory_overlap?.jaccard_index ?? null
  const sharedTfs = analyst.cross_set?.shared_tfs ?? interp?.regulatory_overlap?.shared_tf_motifs ?? []

  const patternData = (interp?.patterns_observed ?? [])
    .filter((p): p is NonNullable<typeof p> & { pattern: string } =>
      !!p && typeof p.pattern === 'string' && p.pattern.length > 0,
    )
    .map((p, i) => ({
      key: i,
      name: p.pattern.length > 64 ? p.pattern.slice(0, 64) + '…' : p.pattern,
      fullName: p.pattern,
      annotation: p.supports_hypothesis ?? 'neutral',
      evidence: p.evidence ?? '',
      value: patternValue(p.supports_hypothesis ?? 'neutral'),
    }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Assessment */}
      <div>
        <SectionLabel>Genomic assessment</SectionLabel>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '16px', flexWrap: 'wrap' }}>
          <span style={{
            fontWeight: 700, fontSize: '15px', color,
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>
            {assessment}
          </span>
          {jaccard !== null && (
            <span style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'ui-monospace, Consolas, monospace' }}>
              cross-set Jaccard: {jaccard.toFixed(3)}
            </span>
          )}
        </div>
        {interp?.assessment_justification && (
          <p className="prose" style={{ margin: '8px 0 0', fontSize: '14px' }}>{interp.assessment_justification}</p>
        )}
      </div>

      {/* Patterns observed — chart + evidence */}
      {patternData.length > 0 && (
        <div>
          <SectionLabel>Patterns observed</SectionLabel>
          <ResponsiveContainer width="100%" height={Math.max(80, patternData.length * 30)}>
            <BarChart data={patternData} layout="vertical" margin={{ left: 0, right: 24, top: 2, bottom: 2 }}>
              <XAxis type="number" domain={[-1.2, 1.2]} hide />
              <YAxis type="category" dataKey="name" width={280} tick={{ fontSize: 10, fill: '#374151', fontFamily: 'system-ui' }} />
              <ReferenceLine x={0} stroke="#d1d5db" />
              <Tooltip
                formatter={(_v: unknown, _n: unknown, item: { payload?: { annotation?: string; evidence?: string } }) => [
                  item.payload?.evidence ?? '',
                  `supports hypothesis: ${item.payload?.annotation ?? '?'}`,
                ]}
                contentStyle={{ fontSize: 12, border: '1px solid #d1d5db', borderRadius: 4 }}
              />
              <Bar dataKey="value" radius={2} maxBarSize={14}>
                {patternData.map((e) => <Cell key={e.key} fill={barColor(e.value)} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <ul style={{ margin: '8px 0 0', paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {patternData.map((p) => (
              <li key={p.key} style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                <span style={{ color: barColor(p.value), fontWeight: 600 }}>[{p.annotation}]</span>{' '}
                {p.fullName} — {p.evidence}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Gene sets + stats */}
      {((analyst.set_a?.length ?? 0) > 0 || (analyst.set_b?.length ?? 0) > 0) && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
          {(analyst.set_a?.length ?? 0) > 0 && (
            <div>
              <SectionLabel>Set A — synaptic genes</SectionLabel>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '6px' }}>
                {(analyst.set_a ?? []).map((g) => <Chip key={g}>{g}</Chip>)}
              </div>
              {analyst.set_a_stats && <SetStats stats={analyst.set_a_stats} />}
            </div>
          )}
          {(analyst.set_b?.length ?? 0) > 0 && (
            <div>
              <SectionLabel>Set B — BBB genes</SectionLabel>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '6px' }}>
                {(analyst.set_b ?? []).map((g) => <Chip key={g} scheme="cyan">{g}</Chip>)}
              </div>
              {analyst.set_b_stats && <SetStats stats={analyst.set_b_stats} />}
            </div>
          )}
        </div>
      )}

      {/* Outlier genes */}
      {(interp?.outlier_genes?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Outlier genes</SectionLabel>
          <ul style={{ margin: 0, paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {interp.outlier_genes.map((o) => (
              <li key={o.gene} style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                <span style={{ fontFamily: 'ui-monospace, Consolas, monospace', color: 'var(--text-heading)', fontWeight: 600 }}>{o.gene}</span>
                {' '}— {o.why_notable}. <em>{o.implication}</em>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Shared TF motifs */}
      {sharedTfs.length > 0 && (
        <div>
          <SectionLabel>Shared TF motifs ({sharedTfs.length})</SectionLabel>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {sharedTfs.map((tf) => <Chip key={tf}>{tf}</Chip>)}
          </div>
          {interp?.regulatory_overlap?.interpretation && (
            <p style={{ margin: '8px 0 0', fontSize: '12px', color: 'var(--text-muted)' }}>
              {interp.regulatory_overlap.interpretation}
            </p>
          )}
        </div>
      )}

      {/* Limitations */}
      {(interp?.limitations?.length ?? 0) > 0 && (
        <div>
          <SectionLabel>Limitations</SectionLabel>
          <ul style={{ margin: 0, paddingLeft: '16px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
            {interp.limitations.map((l, i) => (
              <li key={i} style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{l}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
