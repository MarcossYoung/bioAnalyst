import { useState } from 'react'

interface ProvenanceAffordanceProps {
  provenance: Record<string, unknown> | null | undefined
}

export function ProvenanceAffordance({ provenance }: ProvenanceAffordanceProps) {
  const [open, setOpen] = useState(false)
  if (!provenance) return null

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Show provenance"
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-muted)', fontSize: '12px', padding: '0 2px',
          lineHeight: 1, userSelect: 'none',
        }}
      >
        ⓘ
      </button>
      {open && (
        <div style={{
          position: 'absolute', zIndex: 10, marginTop: '4px',
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: '4px', padding: '10px 12px', maxWidth: '420px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
        }}>
          <pre style={{
            margin: 0, fontSize: '11px', color: 'var(--text-body)',
            fontFamily: 'ui-monospace, Consolas, monospace',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {JSON.stringify(provenance, null, 2)}
          </pre>
        </div>
      )}
    </span>
  )
}
