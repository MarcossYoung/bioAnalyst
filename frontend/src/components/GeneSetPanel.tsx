import type { GeneSetExpansion } from '../lib/types'
import { ProvenanceAffordance } from './ProvenanceAffordance'

const SECTION_LABEL: React.CSSProperties = {
  fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
  textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '8px',
}

function Chip({ label }: { label: string }) {
  return (
    <span style={{
      fontSize: '11px', fontFamily: 'ui-monospace, Consolas, monospace',
      background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af',
      padding: '1px 6px', borderRadius: '3px',
    }}>
      {label}
    </span>
  )
}

interface GeneSetPanelProps {
  expansion: GeneSetExpansion
}

export function GeneSetPanel({ expansion }: GeneSetPanelProps) {
  return (
    <div style={{
      background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: '6px', padding: '16px 20px',
      display: 'flex', flexDirection: 'column', gap: '14px',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
        <div style={{ ...SECTION_LABEL, marginBottom: 0 }}>Gene-set expansion</div>
        <ProvenanceAffordance provenance={expansion.provenance} />
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '16px' }}>
          {expansion.syngo_release && (
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'ui-monospace, Consolas, monospace' }}>
              SynGO {expansion.syngo_release}
            </span>
          )}
          {expansion.bbb_version && (
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'ui-monospace, Consolas, monospace' }}>
              BBB {expansion.bbb_version}
            </span>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
        <div>
          <div style={SECTION_LABEL}>
            Expanded canonical sets ({expansion.total_expanded} genes)
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {expansion.expanded_sets.length > 0
              ? expansion.expanded_sets.map((s) => <Chip key={s} label={s} />)
              : <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>none</span>
            }
          </div>
        </div>

        <div>
          <div style={SECTION_LABEL}>
            Control sets ({expansion.total_controls} genes)
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {expansion.control_sets.length > 0
              ? expansion.control_sets.map((s) => <Chip key={s} label={s} />)
              : <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>none</span>
            }
          </div>
        </div>
      </div>

      <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
        Started with {expansion.starter_count} entity{expansion.starter_count !== 1 ? 'ies' : 'y'} from the hypothesis.
      </div>
    </div>
  )
}
